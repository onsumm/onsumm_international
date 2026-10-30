"""
FX Multi-Currency DB Migration
Implements schema from docs/FX_MULTI_CURRENCY_SPEC.md — Phase 1 (Core) + Phase 2 tables.
Safe to re-run: uses CREATE TABLE IF NOT EXISTS and ADD COLUMN IF NOT EXISTS.
"""

import os

import psycopg2
import sys

DB_CONFIG = dict(
    host=os.getenv("FX_DB_HOST", "database-1.ctoc6isoe550.ap-south-1.rds.amazonaws.com"),
    port=int(os.getenv("FX_DB_PORT", "5432")),
    dbname=os.getenv("FX_DB_NAME", "postgres"),
    user=os.getenv("FX_DB_USER", "postgres"),
    password=os.environ["FX_DB_PASSWORD"],
    sslmode="verify-full",
    sslrootcert=os.getenv("FX_DB_SSLROOTCERT", "global-bundle.pem"),
)

MIGRATIONS = [
    # ── PHASE 1 ─────────────────────────────────────────────────────────────

    ("Create dim_currencies", """
        CREATE TABLE IF NOT EXISTS dim_currencies (
            currency_code  char(3)   PRIMARY KEY,
            name           text      NOT NULL,
            symbol         text      NOT NULL,
            decimal_places smallint  NOT NULL DEFAULT 2,
            is_active      boolean   NOT NULL DEFAULT true
        )
    """),

    ("Seed dim_currencies", """
        INSERT INTO dim_currencies (currency_code, name, symbol, decimal_places, is_active) VALUES
            ('INR', 'Indian Rupee',   '₹', 2, true),
            ('USD', 'US Dollar',      '$',      2, true),
            ('EUR', 'Euro',           '€', 2, true),
            ('GBP', 'British Pound',  '£', 2, true),
            ('JPY', 'Japanese Yen',   '¥', 0, true)
        ON CONFLICT (currency_code) DO NOTHING
    """),

    ("Create dim_fx_rates", """
        CREATE TABLE IF NOT EXISTS dim_fx_rates (
            rate_id         uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            rate_date       date          NOT NULL,
            currency_code   char(3)       NOT NULL REFERENCES dim_currencies(currency_code),
            inr_per_unit    numeric(12,6) NOT NULL,
            source          text          NOT NULL,
            is_business_day boolean       NOT NULL DEFAULT true,
            fetched_at      timestamptz   NOT NULL DEFAULT now(),
            UNIQUE (rate_date, currency_code)
        )
    """),

    ("Index dim_fx_rates: rate_date DESC", """
        CREATE INDEX IF NOT EXISTS idx_fx_rates_date
            ON dim_fx_rates (rate_date DESC)
    """),

    ("Index dim_fx_rates: currency + date DESC", """
        CREATE INDEX IF NOT EXISTS idx_fx_rates_currency_date
            ON dim_fx_rates (currency_code, rate_date DESC)
    """),

    ("Create fx_bill_payments", """
        CREATE TABLE IF NOT EXISTS fx_bill_payments (
            payment_id      uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            invoice_id      uuid          NOT NULL REFERENCES fact_bills(bill_id),
            tenant_id       uuid          NOT NULL REFERENCES dim_tenants(tenant_id),
            payment_date    date          NOT NULL,
            foreign_amount  numeric(18,2) NOT NULL CHECK (foreign_amount > 0),
            actual_inr      numeric(18,2) NOT NULL CHECK (actual_inr > 0),
            effective_rate  numeric(12,6) NOT NULL,
            rbi_rate        numeric(12,6),
            bank_spread     numeric(8,6),
            bank_spread_pct numeric(5,2),
            reference_no    text,
            created_at      timestamptz   NOT NULL DEFAULT now()
        )
    """),

    ("Index fx_bill_payments: invoice_id", """
        CREATE INDEX IF NOT EXISTS idx_fx_payments_bill
            ON fx_bill_payments (invoice_id)
    """),

    ("Index fx_bill_payments: tenant + date DESC", """
        CREATE INDEX IF NOT EXISTS idx_fx_payments_tenant
            ON fx_bill_payments (tenant_id, payment_date DESC)
    """),

    # ── ALTER stg_bills ──────────────────────────────────────────────────────

    ("stg_bills: add original_amount", """
        ALTER TABLE stg_bills
            ADD COLUMN IF NOT EXISTS original_amount numeric(18,2)
    """),

    ("stg_bills: add fx_rate_id", """
        ALTER TABLE stg_bills
            ADD COLUMN IF NOT EXISTS fx_rate_id uuid REFERENCES dim_fx_rates(rate_id)
    """),

    # ── ALTER fact_bills ─────────────────────────────────────────────────────

    ("fact_bills: add original_amount", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS original_amount numeric(18,2)
    """),

    ("fact_bills: add fx_rate_id", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_rate_id uuid REFERENCES dim_fx_rates(rate_id)
    """),

    ("fact_bills: add inr_amount_current", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS inr_amount_current numeric(18,2)
    """),

    ("fact_bills: add fx_gain_loss", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_gain_loss numeric(18,2)
    """),

    ("fact_bills: add fx_actual_inr", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_actual_inr numeric(18,2)
    """),

    ("fact_bills: add fx_effective_rate", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_effective_rate numeric(12,6)
    """),

    ("fact_bills: add fx_realized_cost", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_realized_cost numeric(18,2)
    """),

    # ── ALTER stg_invoices ───────────────────────────────────────────────────

    ("stg_invoices: add original_amount", """
        ALTER TABLE stg_invoices
            ADD COLUMN IF NOT EXISTS original_amount numeric(18,2)
    """),

    ("stg_invoices: add fx_rate_id", """
        ALTER TABLE stg_invoices
            ADD COLUMN IF NOT EXISTS fx_rate_id uuid REFERENCES dim_fx_rates(rate_id)
    """),

    # ── ALTER fact_invoices ──────────────────────────────────────────────────

    ("fact_invoices: add original_amount", """
        ALTER TABLE fact_invoices
            ADD COLUMN IF NOT EXISTS original_amount numeric(18,2)
    """),

    ("fact_invoices: add fx_rate_id", """
        ALTER TABLE fact_invoices
            ADD COLUMN IF NOT EXISTS fx_rate_id uuid REFERENCES dim_fx_rates(rate_id)
    """),

    ("fact_invoices: add inr_amount_current", """
        ALTER TABLE fact_invoices
            ADD COLUMN IF NOT EXISTS inr_amount_current numeric(18,2)
    """),

    ("fact_invoices: add fx_gain_loss", """
        ALTER TABLE fact_invoices
            ADD COLUMN IF NOT EXISTS fx_gain_loss numeric(18,2)
    """),

    # ── PHASE 2 ─────────────────────────────────────────────────────────────

    ("Create fx_rate_alerts", """
        CREATE TABLE IF NOT EXISTS fx_rate_alerts (
            alert_id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id             uuid          NOT NULL REFERENCES dim_tenants(tenant_id),
            alert_type            text          NOT NULL,
            currency_code         char(3)       NOT NULL DEFAULT 'USD',
            rate_date             date          NOT NULL,
            rate_inr              numeric(12,6) NOT NULL,
            threshold_inr         numeric(12,6),
            direction             text,
            recommendation        text          NOT NULL,
            affected_bill_ids     uuid[],
            affected_invoice_ids  uuid[],
            exposure_foreign      numeric(18,2),
            exposure_inr          numeric(18,2),
            status                text          NOT NULL DEFAULT 'ACTIVE',
            created_at            timestamptz   NOT NULL DEFAULT now(),
            dismissed_at          timestamptz,
            dismissed_by          text,
            UNIQUE (tenant_id, alert_type, currency_code, rate_date)
        )
    """),

    ("Index fx_rate_alerts: tenant + status + date", """
        CREATE INDEX IF NOT EXISTS idx_fx_alerts_active
            ON fx_rate_alerts (tenant_id, status, created_at DESC)
    """),

    ("Create fx_agent_notes", """
        CREATE TABLE IF NOT EXISTS fx_agent_notes (
            note_id              uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            uuid          NOT NULL REFERENCES dim_tenants(tenant_id),
            target_type          text          NOT NULL,
            target_id            uuid,
            currency_code        char(3)       NOT NULL,
            severity             text          NOT NULL,
            note_text            text          NOT NULL,
            confidence           numeric(3,2),
            context_snapshot     jsonb,
            rate_at_generation   numeric(12,6),
            is_current           boolean       NOT NULL DEFAULT true,
            created_at           timestamptz   NOT NULL DEFAULT now()
        )
    """),

    ("Index fx_agent_notes: target (partial: is_current)", """
        CREATE INDEX IF NOT EXISTS idx_fx_notes_target
            ON fx_agent_notes (target_type, target_id, is_current)
            WHERE is_current = true
    """),

    ("Index fx_agent_notes: tenant + type + date", """
        CREATE INDEX IF NOT EXISTS idx_fx_notes_tenant
            ON fx_agent_notes (tenant_id, target_type, created_at DESC)
    """),

    # ── Phase 2 columns on fact_bills ────────────────────────────────────────

    ("fact_bills: add fx_agent_note", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_agent_note text
    """),

    ("fact_bills: add fx_agent_note_at", """
        ALTER TABLE fact_bills
            ADD COLUMN IF NOT EXISTS fx_agent_note_at timestamptz
    """),
]


def run():
    print("Connecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    passed = 0
    failed = 0

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
