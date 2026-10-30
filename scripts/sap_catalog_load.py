"""
SAP Item Code Mapping — catalog loader.

Reads the SAP Purchase Register CSV, deduplicates to ONE canonical row per
`item_code` (most frequent material description + HSN code win), and upserts
into `sap_item_catalog` scoped to a single tenant. Optionally embeds each row
with OpenAI `text-embedding-3-small` and stores the vector as a plain Postgres
`double precision[]` (no pgvector).

Usage:
    python3 scripts/sap_catalog_load.py \
        --csv Purchase_Reg_Feb_Mar_2026.csv \
        --tenant d0e00000-0000-0000-0000-000000000004
    # add --no-embed to load the catalog without calling OpenAI
"""

import argparse
import hashlib
import os
import sys
from collections import Counter, defaultdict

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_URL = os.getenv("DB_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
EMBED_BATCH = 256

COL_ITEM = "Item Code"
COL_DESC = "Material Description"
COL_HSN = "HSN Code"

# Vendor (SAP supplier) columns on the purchase register — used to build the
# item_code → supplier_code map that the OCR review surfaces as the bill's
# "Vendor SAP Code". Invoice Supplier is the party that actually billed the line.
COL_SUP_CODE = "Invoice Supplier Code"
COL_SUP_NAME = "Invoice Supplier Name"
COL_VENDOR_GST = "Vendor GST No."


def _clean(v: str) -> str:
    return (v or "").strip()


def _clean_code(v: str) -> str:
    """Normalize a SAP item code: trim, then drop zero-padding so exports that
    pad to 18 digits ('000000004540000001') align with unpadded codes
    ('4540000001') already in the catalog."""
    c = _clean(v)
    return c.lstrip("0") or c


def _most_common(values) -> str:
    """Most frequent non-empty value; '' if none."""
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else ""


def build_catalog(csv_path: str):
    """Return list of dicts: ONE row per distinct (item_code, material_desc).

    Vendor invoices match alternate surface forms (e.g. "SS Stub End 40NB",
    "SS-304 STUBEND SCH-10 SIZE-40NB"), so we keep every distinct description per
    item code instead of collapsing to the most frequent one. HSN for a pair is
    the most frequent HSN seen on that exact (code, desc)."""
    import csv

    hsns = defaultdict(list)   # (code, desc) -> [hsn, ...]
    with open(csv_path, encoding="latin-1", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = _clean_code(row.get(COL_ITEM))
            desc = _clean(row.get(COL_DESC))
            if not code or not desc:
                continue
            hsns[(code, desc)].append(_clean(row.get(COL_HSN)))

    catalog = []
    for (code, desc), hsn_list in hsns.items():
        hsn = _most_common(hsn_list) or None
        # The matcher reads material_desc directly (no embeddings); embedding_text
        # / text_hash are retained only to satisfy the NOT NULL schema columns.
        emb_text = desc
        catalog.append({
            "item_code": code,
            "material_desc": desc,
            "hsn_code": hsn,
            "embedding_text": emb_text,
            "text_hash": hashlib.sha256(f"{code}|{desc}".encode()).hexdigest(),
        })
    return catalog


def build_vendor_map(csv_path: str):
    """Return list of dicts: one per (item_code, vendor_gstn, supplier_code) with
    an occurrence count. This is what lets the OCR review derive a single
    "Vendor SAP Code" for a bill from its matched item codes (narrowed by the
    bill's vendor GSTIN when known)."""
    import csv

    counts = Counter()          # (code, gstn, sup_code) -> freq
    names = {}                  # sup_code -> most recent supplier name
    with open(csv_path, encoding="latin-1", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = _clean_code(row.get(COL_ITEM))
            sup_code = _clean(row.get(COL_SUP_CODE))
            if not code or not sup_code:
                continue
            gstn = _clean(row.get(COL_VENDOR_GST)).upper()
            counts[(code, gstn, sup_code)] += 1
            name = _clean(row.get(COL_SUP_NAME))
            if name:
                names[sup_code] = name

    return [
        {"item_code": code, "vendor_gstn": gstn, "supplier_code": sup_code,
         "supplier_name": names.get(sup_code, ""), "freq": freq}
        for (code, gstn, sup_code), freq in counts.items()
    ]


def upsert_vendor_map(cur, tenant: str, vmap: list):
    rows = [
        (tenant, r["item_code"], r["vendor_gstn"], r["supplier_code"],
         r["supplier_name"], r["freq"])
        for r in vmap
    ]
    execute_values(
        cur,
        """
        INSERT INTO sap_item_vendors
            (tenant_id, item_code, vendor_gstn, supplier_code, supplier_name, freq)
        VALUES %s
        ON CONFLICT (tenant_id, item_code, vendor_gstn, supplier_code) DO UPDATE SET
            supplier_name = EXCLUDED.supplier_name,
            freq          = EXCLUDED.freq,
            updated_at    = now()
        """,
        rows,
        page_size=500,
    )


def upsert_catalog(cur, tenant: str, catalog: list):
    rows = [
        (tenant, c["item_code"], c["material_desc"], c["hsn_code"],
         c["embedding_text"], c["text_hash"])
        for c in catalog
    ]
    execute_values(
        cur,
        """
        INSERT INTO sap_item_catalog
            (tenant_id, item_code, material_desc, hsn_code, embedding_text, text_hash)
        VALUES %s
        ON CONFLICT (tenant_id, item_code, material_desc) DO UPDATE SET
            hsn_code       = EXCLUDED.hsn_code,
            embedding_text = EXCLUDED.embedding_text,
            text_hash      = EXCLUDED.text_hash,
            is_active      = true,
            updated_at     = now()
        """,
        rows,
        page_size=500,
    )


def embed_pending(cur, tenant: str):
    """Embed catalog rows whose text_hash has no current embedding."""
    from openai import OpenAI

    if not OPENAI_API_KEY:
        print("  [skip] OPENAI_API_KEY not set — catalog loaded without embeddings")
        return 0

    cur.execute(
        """
        SELECT c.catalog_id, c.embedding_text
        FROM sap_item_catalog c
        LEFT JOIN sap_item_embeddings e ON e.catalog_id = c.catalog_id AND e.model = %s
        WHERE c.tenant_id = %s AND c.is_active AND e.catalog_id IS NULL
        """,
        (EMBED_MODEL, tenant),
    )
    pending = cur.fetchall()
    if not pending:
        print("  [embed] nothing pending — all rows already embedded")
        return 0

    client = OpenAI(api_key=OPENAI_API_KEY)
    done = 0
    for i in range(0, len(pending), EMBED_BATCH):
        chunk = pending[i:i + EMBED_BATCH]
        resp = client.embeddings.create(
            model=EMBED_MODEL,
            input=[t for _, t in chunk],
        )
        rows = [
            (cid, tenant, EMBED_MODEL, EMBED_DIM, list(d.embedding))
            for (cid, _), d in zip(chunk, resp.data)
        ]
        execute_values(
            cur,
            """
            INSERT INTO sap_item_embeddings (catalog_id, tenant_id, model, dim, embedding)
            VALUES %s
            ON CONFLICT (catalog_id) DO UPDATE SET
                model = EXCLUDED.model, dim = EXCLUDED.dim,
                embedding = EXCLUDED.embedding, created_at = now()
            """,
            rows,
            page_size=200,
        )
        done += len(chunk)
        print(f"  [embed] {done}/{len(pending)}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="Purchase_Reg_Feb_Mar_2026.csv")
    ap.add_argument("--tenant", default="d0e00000-0000-0000-0000-000000000004")
    ap.add_argument("--embed", action="store_true",
                    help="(optional) also generate OpenAI embeddings; NOT used by the "
                         "GPT-4o-mini matcher, which reads the catalog text directly")
    args = ap.parse_args()

    if not DB_URL:
        sys.exit("DB_URL not set in environment / .env")

    csv_path = args.csv
    if not os.path.isabs(csv_path):
        csv_path = os.path.join(os.path.dirname(__file__), "..", csv_path)

    print(f"Building catalog from {csv_path} ...")
    catalog = build_catalog(csv_path)
    n_codes = len({c["item_code"] for c in catalog})
    print(f"  distinct (item_code, desc) pairs: {len(catalog)} across {n_codes} item codes")

    conn = psycopg2.connect(dsn=DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    upsert_catalog(cur, args.tenant, catalog)
    conn.commit()
    cur.execute("SELECT count(*) FROM sap_item_catalog WHERE tenant_id = %s", (args.tenant,))
    print(f"  catalog rows in DB for tenant: {cur.fetchone()[0]}")

    print("Building item → vendor (SAP supplier code) map ...")
    vmap = build_vendor_map(csv_path)
    print(f"  distinct (item_code, gstn, supplier_code) triples: {len(vmap)}")
    if vmap:
        upsert_vendor_map(cur, args.tenant, vmap)
        conn.commit()
        cur.execute("SELECT count(*) FROM sap_item_vendors WHERE tenant_id = %s", (args.tenant,))
        print(f"  vendor-map rows in DB for tenant: {cur.fetchone()[0]}")

    if args.embed:
        print("Embedding (optional; not used by the GPT-4o-mini matcher) ...")
        cur.execute("DELETE FROM sap_item_embeddings WHERE tenant_id = %s", (args.tenant,))
        conn.commit()
        embed_pending(cur, args.tenant)
        conn.commit()
        cur.execute("SELECT count(*) FROM sap_item_embeddings WHERE tenant_id = %s", (args.tenant,))
        print(f"  embedding rows in DB for tenant: {cur.fetchone()[0]}")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
