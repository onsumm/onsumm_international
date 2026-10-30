"""
Nova Vendor Billing Reconciliation — DB Migration v2
Implements docs/PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md §16.0's Phase-1 hardening
corrections on top of the §8 schema created by billing_reconciliation_migrate.py.

Each ALTER TYPE ... ADD VALUE runs in its own step/commit — per §16.0's migration
sequencing note, this cannot share a transaction with anything that uses the new value.

Safe to re-run: ADD COLUMN IF NOT EXISTS, DO-block duplicate_object/duplicate_table guards.
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

# ── Step 1: new enum values, each its own migration step (§16.0 migration sequencing) ──
ENUM_STEPS = [
    ("Add billing_discrepancy_type.NOVA_SELF_INCONSISTENT", """
        DO $$ BEGIN
            ALTER TYPE billing_discrepancy_type ADD VALUE 'NOVA_SELF_INCONSISTENT';
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),
    ("Add billing_discrepancy_type.RATE_CHANGED_MOM", """
        DO $$ BEGIN
            ALTER TYPE billing_discrepancy_type ADD VALUE 'RATE_CHANGED_MOM';
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),
    ("Create enum bill_verification_flag", """
        DO $$ BEGIN
            CREATE TYPE bill_verification_flag AS ENUM ('GREEN', 'YELLOW', 'RED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),
]

# ── Step 2: schema changes that can share a transaction (don't touch the new enum values yet) ──
DDL_STEPS = [
    ("Add billing_vendor_accounts.supplier_id (D6/O2 auto-registration join target)", """
        ALTER TABLE billing_vendor_accounts
            ADD COLUMN IF NOT EXISTS supplier_id uuid REFERENCES dim_suppliers(supplier_id)
    """),
    ("Add billing_vendor_module_alias.billing_basis (§16.2#1)", """
        ALTER TABLE billing_vendor_module_alias
            ADD COLUMN IF NOT EXISTS billing_basis text NOT NULL DEFAULT 'BILLABLE_OPERATIONS'
    """),
    # O3-4: one row per (vendor, pattern), not one row per (vendor, module) — Signzy PAN
    # legitimately has 2 pattern rows. Drop the module-grain uniqueness...
    ("Drop billing_vendor_module_alias (vendor_name, nova_module) uniqueness (O3-4)", """
        ALTER TABLE billing_vendor_module_alias
            DROP CONSTRAINT IF EXISTS billing_vendor_module_alias_vendor_name_nova_module_key
    """),
    # ...replace with pattern-grain uniqueness (multiple NULL invoice_line_pattern rows per
    # vendor/module are fine — standard SQL treats each NULL as distinct for UNIQUE).
    ("Add billing_vendor_module_alias (vendor_name, nova_module, invoice_line_pattern) uniqueness", """
        DO $$ BEGIN
            ALTER TABLE billing_vendor_module_alias
                ADD CONSTRAINT billing_vendor_module_alias_pattern_key
                UNIQUE (vendor_name, nova_module, invoice_line_pattern);
        EXCEPTION WHEN duplicate_table THEN NULL; END $$;
    """),
    ("Add idempotency uniqueness on billing_reconciliation_runs (O3-1)", """
        DO $$ BEGIN
            ALTER TABLE billing_reconciliation_runs
                ADD CONSTRAINT billing_reconciliation_runs_tenant_vendor_month_key
                UNIQUE (tenant_id, vendor_name, billing_month);
        EXCEPTION WHEN duplicate_table THEN NULL; END $$;
    """),
]

# ── Step 3: flag columns — depend on bill_verification_flag existing (run after Step 1 commits) ──
FLAG_STEPS = [
    ("Add verification flag columns to billing_vendor_invoices (§16.13)", """
        ALTER TABLE billing_vendor_invoices
            ADD COLUMN IF NOT EXISTS verification_flag   bill_verification_flag NOT NULL DEFAULT 'YELLOW',
            ADD COLUMN IF NOT EXISTS verification_detail text,
            ADD COLUMN IF NOT EXISTS verified_at         timestamptz
    """),
    ("Mirror verification flag columns onto fact_bills (§16.13 — nullable, not every bill is a Nova vendor)", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS verification_flag   bill_verification_flag,
            ADD COLUMN IF NOT EXISTS verification_detail text,
            ADD COLUMN IF NOT EXISTS verified_at         timestamptz
    """),
    # main.py:377 documents fact_bills as the wrong table — the live BillsPage.tsx
    # bill-detail view actually reads from fact_invoices (GET /api/bills/{id} selects
    # `FROM fact_invoices fi`). Mirror there too so the flag is actually visible.
    ("Mirror verification flag columns onto fact_invoices (the table the UI actually reads)", """
        ALTER TABLE fact_invoices
            ADD COLUMN IF NOT EXISTS verification_flag   bill_verification_flag,
            ADD COLUMN IF NOT EXISTS verification_detail text,
            ADD COLUMN IF NOT EXISTS verified_at         timestamptz
    """),
    ("Add billing_vendor_invoices.invoice_id FK to fact_invoices (correct link target)", """
        ALTER TABLE billing_vendor_invoices
            ADD COLUMN IF NOT EXISTS invoice_id uuid REFERENCES fact_invoices(invoice_id)
    """),
]

# ── Step 4: re-seed Signzy's multi-pattern alias rows as one row per pattern (O3-4) ──
_SIGNZY_SPLIT_PATTERNS = {
    "PAN": ["Pan extensive premium%", "Pan basic%"],
    "eKYC": ["eKYC%", "Digilocker%"],
    "Face Match": ["Face Match%", "Facematch%"],
    "Image Liveliness": ["Image Liveliness%", "Liveliness%"],
}
_SIGNZY_SLUG_BY_MODULE = {
    "PAN": "pan", "eKYC": "eKYC", "Face Match": "facematch", "Image Liveliness": "image-liveliness",
}


def resplit_signzy_alias_rows(cur) -> None:
    for module, patterns in _SIGNZY_SPLIT_PATTERNS.items():
        # Remove the old single combined-pattern row for this module, if present.
        cur.execute(
            "DELETE FROM billing_vendor_module_alias WHERE vendor_name = 'Signzy' AND nova_module = %s AND invoice_line_pattern LIKE %s",
            (module, "%,%"),
        )
        slug = _SIGNZY_SLUG_BY_MODULE[module]
        for pattern in patterns:
            cur.execute(
                """
                INSERT INTO billing_vendor_module_alias (vendor_name, invoice_line_pattern, nova_module, nova_usage_slug)
                VALUES ('Signzy', %s, %s, %s)
                ON CONFLICT (vendor_name, nova_module, invoice_line_pattern) DO NOTHING
                """,
                (pattern, module, slug),
            )


def run() -> None:
    conn = psycopg2.connect(config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT)
    conn.autocommit = False
    cur = conn.cursor()
    passed = failed = 0

    for label, sql in ENUM_STEPS:
        try:
            cur.execute(sql.strip())
            conn.commit()
            print(f"  [OK] {label}")
            passed += 1
        except Exception as exc:
            conn.rollback()
            print(f"  [FAIL] {label}: {exc}")
            failed += 1

    for label, sql in DDL_STEPS:
        try:
            cur.execute(sql.strip())
            conn.commit()
            print(f"  [OK] {label}")
            passed += 1
        except Exception as exc:
            conn.rollback()
            print(f"  [FAIL] {label}: {exc}")
            failed += 1

    for label, sql in FLAG_STEPS:
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
        resplit_signzy_alias_rows(cur)
        conn.commit()
        print("  [OK] Re-split Signzy multi-pattern alias rows (O3-4)")
        passed += 1
    except Exception as exc:
        conn.rollback()
        print(f"  [FAIL] Re-split Signzy alias rows: {exc}")
        failed += 1

    cur.close()
    conn.close()
    print(f"\nDone — {passed} passed, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
