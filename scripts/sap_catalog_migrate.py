"""
SAP Item Code Mapping — DB migration (plain Postgres, no pgvector).

Implements the schema from docs/PRD_SAP_ITEM_CODE_MAPPING.md.
Training catalog keeps only UNIQUE (item_code, material_desc, hsn_code) triples.
Embeddings are stored as a plain `double precision[]` column — similarity is
computed in the application layer, so no vector extension is required.

Safe to re-run: CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS.

Usage:
    python3 scripts/sap_catalog_migrate.py
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Full libpq URI (carries sslmode + sslrootcert) — do NOT strip query params.
DB_URL = os.getenv("DB_URL")

MIGRATIONS = [
    # ── Training catalog: one row per UNIQUE (item_code, material_desc, hsn) ──
    ("Create sap_item_catalog", """
        CREATE TABLE IF NOT EXISTS sap_item_catalog (
            catalog_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            item_code      text        NOT NULL,
            material_desc  text        NOT NULL,
            hsn_code       text,
            embedding_text text        NOT NULL,
            text_hash      text        NOT NULL,
            is_active      boolean     NOT NULL DEFAULT true,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, item_code, material_desc)
        )
    """),
    # Keep ALL distinct (item_code, material_desc) pairs, not one row per code —
    # vendor invoices match alternate surface forms the dedup used to discard.
    # Migrate an existing table from UNIQUE(tenant_id,item_code) → triple key.
    ("Relax sap_item_catalog unique key to (tenant, item_code, material_desc)", """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint
                       WHERE conname = 'sap_item_catalog_tenant_id_item_code_key') THEN
                ALTER TABLE sap_item_catalog
                    DROP CONSTRAINT sap_item_catalog_tenant_id_item_code_key;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint
                           WHERE conname = 'sap_item_catalog_tenant_code_desc_key') THEN
                ALTER TABLE sap_item_catalog
                    ADD CONSTRAINT sap_item_catalog_tenant_code_desc_key
                    UNIQUE (tenant_id, item_code, material_desc);
            END IF;
        END $$;
    """),
    ("Index sap_item_catalog: tenant", """
        CREATE INDEX IF NOT EXISTS idx_sap_catalog_tenant
            ON sap_item_catalog (tenant_id)
    """),
    ("Index sap_item_catalog: tenant + hsn", """
        CREATE INDEX IF NOT EXISTS idx_sap_catalog_hsn
            ON sap_item_catalog (tenant_id, hsn_code)
    """),
    ("Index sap_item_catalog: tenant + item_code", """
        CREATE INDEX IF NOT EXISTS idx_sap_catalog_item
            ON sap_item_catalog (tenant_id, item_code)
    """),

    # ── Embeddings stored as plain float8[] (no pgvector) ────────────────────
    ("Create sap_item_embeddings", """
        CREATE TABLE IF NOT EXISTS sap_item_embeddings (
            catalog_id uuid              PRIMARY KEY
                                          REFERENCES sap_item_catalog(catalog_id) ON DELETE CASCADE,
            tenant_id  uuid              NOT NULL REFERENCES dim_tenants(tenant_id),
            model      text              NOT NULL,
            dim        integer           NOT NULL,
            embedding  double precision[] NOT NULL,
            created_at timestamptz       NOT NULL DEFAULT now()
        )
    """),
    ("Index sap_item_embeddings: tenant", """
        CREATE INDEX IF NOT EXISTS idx_sap_emb_tenant
            ON sap_item_embeddings (tenant_id)
    """),

    # ── Learned synonyms from operator confirmations ─────────────────────────
    ("Create sap_item_aliases", """
        CREATE TABLE IF NOT EXISTS sap_item_aliases (
            alias_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id  uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            alias_text text        NOT NULL,
            item_code  text        NOT NULL,
            source     text        NOT NULL DEFAULT 'operator',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, alias_text)
        )
    """),

    # ── Review queue for low/medium-confidence matches ───────────────────────
    ("Create mapping_review_queue", """
        CREATE TABLE IF NOT EXISTS mapping_review_queue (
            review_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            line_item_id     uuid        NOT NULL,
            line_item_kind   text        NOT NULL,
            query_text       text        NOT NULL,
            candidates       jsonb       NOT NULL,
            status           text        NOT NULL DEFAULT 'PENDING',
            chosen_item_code text,
            created_at       timestamptz NOT NULL DEFAULT now(),
            resolved_at      timestamptz
        )
    """),
    ("Index mapping_review_queue: tenant + status", """
        CREATE INDEX IF NOT EXISTS idx_mapping_review_status
            ON mapping_review_queue (tenant_id, status, created_at DESC)
    """),

    # ── Write-back columns on the bill line item (shown in bill preview) ──────
    ("stg_bill_line_items: add sap_item_code", """
        ALTER TABLE stg_bill_line_items
            ADD COLUMN IF NOT EXISTS sap_item_code text
    """),
    ("stg_bill_line_items: add sap_material_desc", """
        ALTER TABLE stg_bill_line_items
            ADD COLUMN IF NOT EXISTS sap_material_desc text
    """),
    ("stg_bill_line_items: add sap_map_score", """
        ALTER TABLE stg_bill_line_items
            ADD COLUMN IF NOT EXISTS sap_map_score numeric(5,4)
    """),
    ("stg_bill_line_items: add sap_map_status", """
        ALTER TABLE stg_bill_line_items
            ADD COLUMN IF NOT EXISTS sap_map_status text
    """),

    # ── Item → vendor (SAP supplier code) map, loaded from the purchase register ─
    # One row per (item_code, vendor GSTN, supplier_code) with an occurrence count.
    # vendor_gstn is stored as '' (never NULL) so the UNIQUE key dedups cleanly —
    # Postgres treats NULLs as distinct and would let duplicate rows through.
    ("Create sap_item_vendors", """
        CREATE TABLE IF NOT EXISTS sap_item_vendors (
            id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            item_code     text        NOT NULL,
            vendor_gstn   text        NOT NULL DEFAULT '',
            supplier_code text        NOT NULL,
            supplier_name text,
            freq          integer     NOT NULL DEFAULT 1,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, item_code, vendor_gstn, supplier_code)
        )
    """),
    ("Index sap_item_vendors: tenant + item_code", """
        CREATE INDEX IF NOT EXISTS idx_sap_itemvend_item
            ON sap_item_vendors (tenant_id, item_code)
    """),
    ("Index sap_item_vendors: tenant + gstn", """
        CREATE INDEX IF NOT EXISTS idx_sap_itemvend_gstn
            ON sap_item_vendors (tenant_id, vendor_gstn)
    """),
    # Vendor SAP (supplier) code derived at OCR-review time, persisted on the bill.
    ("stg_bills: add vendor_sap_code", """
        ALTER TABLE stg_bills
            ADD COLUMN IF NOT EXISTS vendor_sap_code text
    """),
]


def run():
    if not DB_URL:
        sys.exit("DB_URL not set in environment / .env")

    print("Connecting to database...")
    conn = psycopg2.connect(dsn=DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    passed = failed = 0
    for label, sql in MIGRATIONS:
        try:
            cur.execute(sql.strip())
            conn.commit()
            print(f"  [OK] {label}")
            passed += 1
        except Exception as exc:
            conn.rollback()
            print(f"  [FAIL] {label}: {exc}")
            failed += 1

    cur.close()
    conn.close()
    print(f"\nDone — {passed} passed, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
