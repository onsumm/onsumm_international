"""
Nova Vendor Billing Reconciliation — DB Migration
Implements schema from docs/PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md §8.
Safe to re-run: enums/tables use CREATE ... IF NOT EXISTS / duplicate_object guards,
seed inserts use ON CONFLICT DO NOTHING.

Reads DB_URL from config.py (.env) — no hardcoded credentials.
"""
import json
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

MAPPING_PATH = Path(__file__).resolve().parent.parent / "lendix" / "nova_vendor_mapping.json"

# d0e00000-...-003 = PRODUCTION (real users, e.g. Vikram Singh / admin2, are on this tenant —
#                     NEVER seed test/dev billing_vendor_accounts rows here).
# d0e00000-...-002 = DEV/TEST tenant for the Nova billing reconciliation feature. All
#                     billing_vendor_accounts / invoice / reconciliation test data lives here
#                     until the feature has a real OCR->supplier_id auto-registration path (D6)
#                     and is deliberately promoted to prod. See docs/CHANGE_RELEASE_NOVA_BILLING_DEV_TENANT.md.
BRIGHT_LOANS_TENANT_ID = "d0e00000-0000-0000-0000-000000000002"

# Curated alias rows for vendors/modules we have a real invoice for (Signzy).
# nova_module must match nova_vendor_mapping.json's canonical names exactly.
_SIGNZY_CURATED = {
    "PAN":                   ("Pan extensive premium%,Pan basic%", "pan"),
    "Company Name to GST":   ("Company Name To GST",               "company-name-to-gst"),
    "eKYC":                  ("eKYC%,Digilocker%",                 "eKYC"),
    "eSign":                 ("eSign%",                            "eSign"),
    "Email Verification":    ("Email Verification%",               "email-verification"),
    "Face Match":            ("Face Match%,Facematch%",            "facematch"),
    "Image Liveliness":      ("Image Liveliness%,Liveliness%",     "image-liveliness"),
    "Penny Drop":            ("Penny Drop%",                       "penny-drop"),
    "Bank Aggregator":       ("Bank Aggregator%",                  "bank-aggregator"),
}

DDL = [
    ("Create enum billing_discrepancy_type", """
        DO $$ BEGIN
            CREATE TYPE billing_discrepancy_type AS ENUM (
                'QTY_MISMATCH', 'RATE_MISMATCH', 'COST_MISMATCH',
                'MISSING_IN_NOVA', 'MISSING_IN_INVOICE',
                'UNMAPPED_LINE_ITEM', 'ENTITY_UNRESOLVED', 'NOVA_SNAPSHOT_UNAVAILABLE'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),

    ("Create enum billing_discrepancy_status", """
        DO $$ BEGIN
            CREATE TYPE billing_discrepancy_status AS ENUM (
                'OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'DISPUTED_WITH_VENDOR', 'IGNORED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),

    ("Create enum billing_reconciliation_status", """
        DO $$ BEGIN
            CREATE TYPE billing_reconciliation_status AS ENUM (
                'PENDING', 'RUNNING', 'COMPLETED', 'COMPLETED_WITH_DISCREPANCIES', 'FAILED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),

    ("Add wf_entity_type value BILLING_DISCREPANCY", """
        DO $$ BEGIN
            ALTER TYPE wf_entity_type ADD VALUE 'BILLING_DISCREPANCY';
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),

    ("Create billing_vendor_accounts", """
        CREATE TABLE IF NOT EXISTS billing_vendor_accounts (
            vendor_account_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            vendor_name        text        NOT NULL,
            legal_entity_name  text        NOT NULL,
            gstin              char(15),
            nova_sub_account_id text,
            nova_api_key_ref   text        NOT NULL,
            is_active          boolean     NOT NULL DEFAULT true,
            created_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, vendor_name)
        )
    """),

    ("Create billing_vendor_module_alias", """
        CREATE TABLE IF NOT EXISTS billing_vendor_module_alias (
            alias_id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_name          text        NOT NULL,
            invoice_line_pattern text,
            nova_module          text        NOT NULL,
            nova_usage_slug      text,
            created_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (vendor_name, nova_module)
        )
    """),

    ("Create billing_vendor_invoices", """
        CREATE TABLE IF NOT EXISTS billing_vendor_invoices (
            vendor_invoice_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            vendor_account_id  uuid        NOT NULL REFERENCES billing_vendor_accounts(vendor_account_id),
            upload_id          uuid        REFERENCES raw_invoice_uploads(upload_id),
            bill_id            uuid        REFERENCES fact_bills(bill_id),
            invoice_number     text        NOT NULL,
            invoice_date       date        NOT NULL,
            billing_period     text        NOT NULL,
            subtotal           numeric     NOT NULL,
            tax_amount         numeric     NOT NULL DEFAULT 0,
            total_amount       numeric     NOT NULL,
            created_at         timestamptz NOT NULL DEFAULT now(),
            UNIQUE (vendor_account_id, invoice_number)
        )
    """),

    ("Create billing_vendor_invoice_lines", """
        CREATE TABLE IF NOT EXISTS billing_vendor_invoice_lines (
            line_id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            vendor_invoice_id  uuid        NOT NULL REFERENCES billing_vendor_invoices(vendor_invoice_id) ON DELETE CASCADE,
            line_number        smallint    NOT NULL,
            description        text        NOT NULL,
            hsn_sac            text,
            quantity           numeric     NOT NULL,
            rate               numeric     NOT NULL,
            tax_pct            numeric     NOT NULL DEFAULT 0,
            amount             numeric     NOT NULL,
            nova_module        text
        )
    """),

    ("Create billing_nova_usage_snapshots", """
        CREATE TABLE IF NOT EXISTS billing_nova_usage_snapshots (
            snapshot_id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id               uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            vendor_name             text        NOT NULL,
            billing_month           text        NOT NULL,
            total_billable_operations integer   NOT NULL,
            total_cost_rupees       numeric     NOT NULL,
            raw_response_jsonb      jsonb       NOT NULL,
            nova_reference_id       text,
            fetched_at              timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, vendor_name, billing_month)
        )
    """),

    ("Create billing_nova_usage_lines", """
        CREATE TABLE IF NOT EXISTS billing_nova_usage_lines (
            usage_line_id       uuid    PRIMARY KEY DEFAULT gen_random_uuid(),
            snapshot_id         uuid    NOT NULL REFERENCES billing_nova_usage_snapshots(snapshot_id) ON DELETE CASCADE,
            nova_usage_slug     text    NOT NULL,
            api_call_count      integer NOT NULL,
            billable_operations integer NOT NULL,
            cost_per_operation  numeric NOT NULL,
            total_cost_rupees   numeric NOT NULL,
            UNIQUE (snapshot_id, nova_usage_slug)
        )
    """),

    ("Create billing_reconciliation_runs", """
        CREATE TABLE IF NOT EXISTS billing_reconciliation_runs (
            run_id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            vendor_name           text        NOT NULL,
            billing_month         text        NOT NULL,
            vendor_invoice_id     uuid        REFERENCES billing_vendor_invoices(vendor_invoice_id),
            snapshot_id           uuid        REFERENCES billing_nova_usage_snapshots(snapshot_id),
            status                billing_reconciliation_status NOT NULL DEFAULT 'PENDING',
            total_invoice_amount  numeric,
            total_nova_cost       numeric,
            variance_amount       numeric,
            started_at            timestamptz NOT NULL DEFAULT now(),
            completed_at          timestamptz
        )
    """),

    ("Create billing_reconciliation_discrepancies", """
        CREATE TABLE IF NOT EXISTS billing_reconciliation_discrepancies (
            discrepancy_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id             uuid        NOT NULL REFERENCES billing_reconciliation_runs(run_id) ON DELETE CASCADE,
            tenant_id          uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            nova_module        text,
            discrepancy_type   billing_discrepancy_type NOT NULL,
            severity           severity_level NOT NULL,
            invoice_value      jsonb,
            nova_value         jsonb,
            variance_pct       numeric,
            variance_amount    numeric,
            status             billing_discrepancy_status NOT NULL DEFAULT 'OPEN',
            resolution_note    text,
            resolved_by        uuid REFERENCES org_users(user_id),
            resolved_at        timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now()
        )
    """),

    ("Index billing_reconciliation_discrepancies (tenant_id, status)", """
        CREATE INDEX IF NOT EXISTS idx_billing_disc_tenant_status
            ON billing_reconciliation_discrepancies (tenant_id, status)
    """),
    ("Index billing_reconciliation_discrepancies (tenant_id, severity)", """
        CREATE INDEX IF NOT EXISTS idx_billing_disc_tenant_severity
            ON billing_reconciliation_discrepancies (tenant_id, severity)
    """),
    ("Index billing_reconciliation_discrepancies (run_id)", """
        CREATE INDEX IF NOT EXISTS idx_billing_disc_run
            ON billing_reconciliation_discrepancies (run_id)
    """),

    ("Create billing_bill_reconciliation (persisted per-bill panel result)", """
        CREATE TABLE IF NOT EXISTS billing_bill_reconciliation (
            invoice_id   uuid        PRIMARY KEY REFERENCES fact_invoices(invoice_id) ON DELETE CASCADE,
            tenant_id    uuid        NOT NULL,
            result       jsonb       NOT NULL,
            computed_at  timestamptz NOT NULL DEFAULT now()
        )
    """),
]


def seed_alias_table(cur) -> tuple[int, int]:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    inserted, skipped = 0, 0
    for vendor, modules in mapping["vendors"].items():
        for module in modules:
            pattern, slug = (None, None)
            if vendor == "Signzy" and module in _SIGNZY_CURATED:
                pattern, slug = _SIGNZY_CURATED[module]
            cur.execute(
                """
                INSERT INTO billing_vendor_module_alias
                    (vendor_name, invoice_line_pattern, nova_module, nova_usage_slug)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (vendor_name, nova_module) DO UPDATE SET
                    invoice_line_pattern = COALESCE(billing_vendor_module_alias.invoice_line_pattern, EXCLUDED.invoice_line_pattern),
                    nova_usage_slug      = COALESCE(billing_vendor_module_alias.nova_usage_slug, EXCLUDED.nova_usage_slug)
                """,
                (vendor, pattern, module, slug),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
    return inserted, skipped


def seed_vendor_accounts(cur) -> int:
    """One billing_vendor_accounts row per vendor Bright Loans routes through.
    Confirmed live (2026-07-13): Nova's /billing/monthly key is TENANT-scoped, not
    vendor-scoped — the same key returns valid data for every vendor name tried
    (Signzy, Digitap, Wocom, Smartping, Scienaptic, Girnar, CRIF, ICICI Bank), so
    one nova_api_key_ref covers all vendors for this tenant. legal_entity_name/gstin
    default to the confirmed Signzy invoice entity until each vendor's own invoice
    is observed and may need correcting per vendor.
    """
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    n = 0
    for vendor in mapping["vendors"]:
        cur.execute(
            """
            INSERT INTO billing_vendor_accounts
                (tenant_id, vendor_name, legal_entity_name, gstin, nova_api_key_ref, is_active)
            VALUES (%s, %s, %s, %s, %s, true)
            ON CONFLICT (tenant_id, vendor_name) DO NOTHING
            """,
            (
                BRIGHT_LOANS_TENANT_ID,
                vendor,
                "AVINASH CAPITAL MARKETS PRIVATE LIMITED",
                "07AACCA2775M1ZF",
                "NOVA_API_KEY__BRIGHTLOANS",
            ),
        )
        if cur.rowcount:
            n += 1
    return n


def run() -> None:
    print(f"Connecting to {config.DB_HOST}/{config.DB_NAME} ...")
    conn = psycopg2.connect(config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT)
    conn.autocommit = False
    cur = conn.cursor()

    passed = failed = 0
    for label, sql in DDL:
        try:
            cur.execute(sql.strip())
            conn.commit()
            print(f"  [OK] {label}")
            passed += 1
        except Exception as exc:
            conn.rollback()
            print(f"  [FAIL] {label}: {exc}")
            failed += 1

    try:
        ins, skip = seed_alias_table(cur)
        conn.commit()
        print(f"  [OK] Seed billing_vendor_module_alias — {ins} inserted, {skip} already present")
        passed += 1
    except Exception as exc:
        conn.rollback()
        print(f"  [FAIL] Seed billing_vendor_module_alias: {exc}")
        failed += 1

    try:
        n = seed_vendor_accounts(cur)
        conn.commit()
        print(f"  [OK] Seed billing_vendor_accounts — {n} vendor rows for Bright Loans")
        passed += 1
    except Exception as exc:
        conn.rollback()
        print(f"  [FAIL] Seed billing_vendor_accounts: {exc}")
        failed += 1

    cur.close()
    conn.close()
    print(f"\nDone — {passed} passed, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
