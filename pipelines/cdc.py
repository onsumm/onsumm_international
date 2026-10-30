"""
PostgreSQL LISTEN/NOTIFY CDC trigger bootstrap.

Creates a generic trigger function `_pipeline_notify()` and per-table AFTER
triggers on every pipeline source table.  Workers that declare `notify_channel`
will wake immediately when new rows land instead of waiting for the next poll.

Called once at startup (after pool init, before workers start).  Fully
idempotent — safe to re-run on every restart.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# (source_table, trigger_event, notify_channel)
# One row per trigger needed.  Tables watched by multiple workers get one
# trigger per channel so each worker receives its own notification.
_TRIGGERS: list[tuple[str, str, str]] = [
    # Raw ingest
    ("raw_api_payloads",    "INSERT",           "ch_api_sync"),
    ("raw_webhook_events",  "INSERT",           "ch_webhook"),
    ("raw_invoice_uploads", "INSERT",           "ch_upload_ocr"),

    # Staging → fact promotion
    ("stg_invoices",        "INSERT",           "ch_staging_promotion"),
    ("stg_bills",           "INSERT",           "ch_staging_promotion"),
    ("stg_payments",        "INSERT",           "ch_staging_promotion"),

    # TDS — fires on insert (new bill) AND update (supplier category assigned)
    ("stg_bills",           "INSERT OR UPDATE", "ch_tds_processing"),

    # Post-promotion enrichment
    ("fact_invoices",       "INSERT",           "ch_category_enrichment"),
    ("fact_invoices",       "INSERT",           "ch_deduplication"),

    # Supplier / customer verification — INSERT only (UPDATE handled by reset triggers below)
    ("dim_suppliers",           "INSERT",           "ch_gst_verification"),
    ("dim_end_customers",       "INSERT",           "ch_gst_verification"),
    ("dim_suppliers",           "INSERT",           "ch_bank_verification"),
    ("dim_end_customers",       "INSERT",           "ch_bank_verification"),
    # Multiple bank accounts per supplier — each new/updated row triggers verification
    ("supplier_bank_accounts",  "INSERT",           "ch_bank_verification"),
    # ── Review Hub ────────────────────────────────────────────────────────────
    # These wake the adapter worker. The triggers are FOR EACH STATEMENT with an
    # empty payload, so a notify means "something here changed", never "review
    # this row" — which is why the adapters are bounded reconcile loops.
    ("fact_invoices",                        "INSERT OR UPDATE", "ch_review_adapters"),
    ("stg_bills",                            "INSERT OR UPDATE", "ch_review_adapters"),
    ("dim_suppliers",                        "INSERT OR UPDATE", "ch_review_adapters"),
    ("supplier_gstins",                      "INSERT OR UPDATE", "ch_review_adapters"),
    ("ai_anomalies",                         "INSERT OR UPDATE", "ch_review_adapters"),
    ("billing_reconciliation_discrepancies", "INSERT OR UPDATE", "ch_review_adapters"),
    ("payment_transactions",                 "INSERT OR UPDATE", "ch_review_adapters"),
    ("fact_disbursements",                   "INSERT OR UPDATE", "ch_review_adapters"),
    ("fx_rate_alerts",                       "INSERT",           "ch_review_adapters"),
    ("raw_invoice_uploads",                  "INSERT OR UPDATE", "ch_review_adapters"),
    ("mapping_review_queue",                 "INSERT OR UPDATE", "ch_review_adapters"),
    ("rh_review_requests",                   "INSERT",           "ch_review_agent"),

    # ── Communication Agent ──────────────────────────────────────────────────────
    ("poc_email_messages",                   "INSERT",           "ch_comm_extract"),
    # Contract PDFs (agent.py's new contract branch, or a manual upload via
    # vendor_documents.py) — same channel as the email path, since
    # CommExtractionWorker's one process_batch() pass already claims from both
    # tables (see that module's docstring for why it is one worker, not two).
    ("vendor_documents",                     "INSERT",           "ch_comm_extract"),

]

# ── Auto-reset + re-trigger SQL ───────────────────────────────────────────────
# When a key input column (GSTIN, bank account, IFSC) is updated on an existing
# row the verification status must be reset so the worker re-processes the row.
# These BEFORE UPDATE triggers run before the row is written, allowing them to
# modify NEW in-place (reset status) and fire NOTIFY so the worker wakes up.

_RESET_TRIGGERS_SQL = """
-- ── GST verification reset ────────────────────────────────────────────────────
-- dim_suppliers: gstn changed → status back to UNVERIFIED
CREATE OR REPLACE FUNCTION _gst_reset_suppliers()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.gstn IS DISTINCT FROM OLD.gstn THEN
        NEW.status := 'UNVERIFIED';
        PERFORM pg_notify('ch_gst_verification', '');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_gst_reset_suppliers ON dim_suppliers;
CREATE TRIGGER tg_gst_reset_suppliers
BEFORE UPDATE OF gstn ON dim_suppliers
FOR EACH ROW EXECUTE FUNCTION _gst_reset_suppliers();

-- dim_end_customers: gstin changed → kyc_status back to PENDING
CREATE OR REPLACE FUNCTION _gst_reset_customers()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.gstin IS DISTINCT FROM OLD.gstin THEN
        NEW.kyc_status := 'PENDING';
        PERFORM pg_notify('ch_gst_verification', '');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_gst_reset_customers ON dim_end_customers;
CREATE TRIGGER tg_gst_reset_customers
BEFORE UPDATE OF gstin ON dim_end_customers
FOR EACH ROW EXECUTE FUNCTION _gst_reset_customers();

-- ── Bank verification reset ───────────────────────────────────────────────────
-- dim_suppliers: bank_account or bank_ifsc changed → bank_verified back to false
CREATE OR REPLACE FUNCTION _bank_reset_suppliers()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.bank_account IS DISTINCT FROM OLD.bank_account
       OR NEW.bank_ifsc IS DISTINCT FROM OLD.bank_ifsc THEN
        NEW.bank_verified := false;
        PERFORM pg_notify('ch_bank_verification', '');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_bank_reset_suppliers ON dim_suppliers;
CREATE TRIGGER tg_bank_reset_suppliers
BEFORE UPDATE OF bank_account, bank_ifsc ON dim_suppliers
FOR EACH ROW EXECUTE FUNCTION _bank_reset_suppliers();

-- dim_end_customers: bank_account_no or bank_ifsc changed → bank_verified back to false
CREATE OR REPLACE FUNCTION _bank_reset_customers()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.bank_account_no IS DISTINCT FROM OLD.bank_account_no
       OR NEW.bank_ifsc IS DISTINCT FROM OLD.bank_ifsc THEN
        NEW.bank_verified := false;
        PERFORM pg_notify('ch_bank_verification', '');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_bank_reset_customers ON dim_end_customers;
CREATE TRIGGER tg_bank_reset_customers
BEFORE UPDATE OF bank_account_no, bank_ifsc ON dim_end_customers
FOR EACH ROW EXECUTE FUNCTION _bank_reset_customers();

-- supplier_bank_accounts: account_number or ifsc changed → re-verify that account row
CREATE OR REPLACE FUNCTION _bank_reset_sba()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.account_number IS DISTINCT FROM OLD.account_number
       OR NEW.ifsc IS DISTINCT FROM OLD.ifsc THEN
        NEW.verified := false;
        PERFORM pg_notify('ch_bank_verification', '');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tg_bank_reset_sba ON supplier_bank_accounts;
CREATE TRIGGER tg_bank_reset_sba
BEFORE UPDATE OF account_number, ifsc ON supplier_bank_accounts
FOR EACH ROW EXECUTE FUNCTION _bank_reset_sba();
"""


async def create_notify_triggers(pool: asyncpg.Pool) -> None:
    """Idempotently install all NOTIFY triggers.  Safe to call on every startup."""
    async with pool.acquire() as conn:
        # Shared trigger function — channel name passed as first TG_ARGV argument
        await conn.execute("""
            CREATE OR REPLACE FUNCTION _pipeline_notify()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM pg_notify(TG_ARGV[0], '');
                RETURN NEW;
            END;
            $$;
        """)

        for table, event, channel in _TRIGGERS:
            trigger_name = f"tg_{channel}_{table}"
            # DROP + CREATE so the event clause is always up-to-date
            await conn.execute(f"""
                DROP TRIGGER IF EXISTS {trigger_name} ON {table};
                CREATE TRIGGER {trigger_name}
                AFTER {event} ON {table}
                FOR EACH STATEMENT
                EXECUTE FUNCTION _pipeline_notify('{channel}');
            """)
            logger.debug("[cdc] trigger %s  %s → %s", trigger_name, table, channel)

        # Install auto-reset triggers for verification re-triggering
        await conn.execute(_RESET_TRIGGERS_SQL)
        logger.debug("[cdc] verification reset triggers installed")

    logger.info("[cdc] %d NOTIFY triggers installed", len(_TRIGGERS))
