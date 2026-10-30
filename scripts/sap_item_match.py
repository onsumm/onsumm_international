"""
SAP Item Code Mapping — matcher (plain Postgres, in-app cosine).

Maps a bill line item's "Description of Goods" (product_name) to the best SAP
item_code using OpenAI embeddings stored in `sap_item_embeddings` (float8[]).
HSN code constrains/boosts candidates; a confidence band decides auto-accept vs.
review vs. no-match (see docs/PRD_SAP_ITEM_CODE_MAPPING.md §7).

Modes:
    # self-test the trained catalog with ad-hoc descriptions
    python3 scripts/sap_item_match.py --test "C.I Pulley 16 inch 2 Groove" --hsn 8483

    # backfill unmapped bill line items for a tenant
    python3 scripts/sap_item_match.py --backfill --tenant d0e00000-0000-0000-0000-000000000004
"""

import argparse
import math
import os
import sys

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

DB_URL = os.getenv("DB_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBED_MODEL = "text-embedding-3-small"

AUTO_ACCEPT = 0.86
REVIEW_FLOOR = 0.60
TOP_K = 5


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


def hsn_match(q_hsn, c_hsn):
    if not q_hsn or not c_hsn:
        return False
    a, b = q_hsn.strip(), c_hsn.strip()
    n = min(len(a), len(b), 4)
    return n >= 2 and a[:n] == b[:n]


def load_catalog(cur, tenant):
    cur.execute(
        """
        SELECT c.item_code, c.material_desc, c.hsn_code, e.embedding
        FROM sap_item_catalog c
        JOIN sap_item_embeddings e ON e.catalog_id = c.catalog_id
        WHERE c.tenant_id = %s AND c.is_active AND e.model = %s
        """,
        (tenant, EMBED_MODEL),
    )
    out = []
    for code, desc, hsn, emb in cur.fetchall():
        out.append((code, desc, hsn, _norm([float(x) for x in emb])))
    return out


def embed_query(client, text):
    resp = client.embeddings.create(model=EMBED_MODEL, input=[text])
    return _norm(list(resp.data[0].embedding))


def match(qvec, q_hsn, catalog):
    """Return (ranked_candidates, decision). Candidates: dicts w/ blended score."""
    scored = []
    for code, desc, hsn, vec in catalog:
        cos = cosine(qvec, vec)
        hm = hsn_match(q_hsn, hsn)
        blended = 0.85 * cos + 0.15 * (1.0 if hm else 0.0)
        scored.append({"item_code": code, "material_desc": desc, "hsn_code": hsn,
                       "cosine": round(cos, 4), "hsn_match": hm,
                       "score": round(blended, 4)})
    scored.sort(key=lambda r: r["score"], reverse=True)
    top = scored[:TOP_K]
    best = top[0]
    if best["score"] >= AUTO_ACCEPT and best["hsn_match"]:
        decision = "AUTO"
    elif best["score"] >= REVIEW_FLOOR:
        decision = "REVIEW"
    else:
        decision = "NONE"
    return top, decision


def backfill(cur, conn, tenant, client, catalog):
    cur.execute(
        """
        SELECT line_item_id, product_name, hsn_code
        FROM stg_bill_line_items
        WHERE tenant_id = %s AND sap_item_code IS NULL AND product_name IS NOT NULL
        """,
        (tenant,),
    )
    rows = cur.fetchall()
    print(f"  unmapped line items: {len(rows)}")
    auto = review = none = 0
    for lid, name, hsn in rows:
        qvec = embed_query(client, f"{name} | HSN {hsn}" if hsn else name)
        top, decision = match(qvec, hsn, catalog)
        best = top[0]
        if decision == "AUTO":
            cur.execute(
                """UPDATE stg_bill_line_items
                   SET sap_item_code=%s, sap_material_desc=%s, sap_map_score=%s, sap_map_status='AUTO'
                   WHERE line_item_id=%s""",
                (best["item_code"], best["material_desc"], best["score"], lid),
            )
            auto += 1
        elif decision == "REVIEW":
            cur.execute(
                """UPDATE stg_bill_line_items
                   SET sap_item_code=%s, sap_material_desc=%s, sap_map_score=%s, sap_map_status='REVIEW'
                   WHERE line_item_id=%s""",
                (best["item_code"], best["material_desc"], best["score"], lid),
            )
            cur.execute(
                """INSERT INTO mapping_review_queue
                       (tenant_id, line_item_id, line_item_kind, query_text, candidates)
                   VALUES (%s, %s, 'bill', %s, %s)""",
                (tenant, lid, name, Json(top)),
            )
            review += 1
        else:
            cur.execute(
                "UPDATE stg_bill_line_items SET sap_map_status='NONE' WHERE line_item_id=%s",
                (lid,),
            )
            none += 1
    conn.commit()
    print(f"  auto={auto} review={review} none={none}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="d0e00000-0000-0000-0000-000000000004")
    ap.add_argument("--test", help="ad-hoc description to map")
    ap.add_argument("--hsn", default=None)
    ap.add_argument("--backfill", action="store_true")
    args = ap.parse_args()

    if not DB_URL or not OPENAI_API_KEY:
        sys.exit("DB_URL and OPENAI_API_KEY must be set")

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    conn = psycopg2.connect(dsn=DB_URL)
    cur = conn.cursor()
    catalog = load_catalog(cur, args.tenant)
    print(f"catalog loaded: {len(catalog)} embedded items")

    if args.test:
        qvec = embed_query(client, f"{args.test} | HSN {args.hsn}" if args.hsn else args.test)
        top, decision = match(qvec, args.hsn, catalog)
        print(f"\nQuery: {args.test!r}  HSN={args.hsn}  -> decision={decision}")
        for i, c in enumerate(top, 1):
            print(f"  {i}. [{c['item_code']}] {c['material_desc']}  "
                  f"hsn={c['hsn_code']} cos={c['cosine']} hsn_match={c['hsn_match']} score={c['score']}")

    if args.backfill:
        print("Backfilling bill line items ...")
        backfill(cur, conn, args.tenant, client, catalog)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
