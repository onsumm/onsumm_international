"""Schema migrations executed at application startup.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import psycopg2
from .config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from .routers.email_connection import router as email_router, ensure_user_email_connections_table
from .routers.sso import ensure_sso_tables
from .routers.agent import router as agent_router, ensure_agent_tables, start_agent_poller
from .domain.fact_graph import init_fact_graph_schema
from .security.licensing_schema import DDL as LICENSING_DDL, sync_catalog_sql
from .security.services import SERVICES, sort_order as service_sort_order

from .core.dbconn import _safe_connect

async def on_startup_user_email_connections():
    ensure_user_email_connections_table()
    ensure_sso_tables()

def _migrate_licensing():
    """Create the licensing / platform-admin tables and sync the service catalog.

    The catalog sync must happen on boot rather than only in the migration script:
    tenant_service.service_key foreign-keys service_catalog, so a service added in
    services.py would otherwise be rejected the first time an admin toggled it on
    whichever database this process is pointed at.

    Wrapped in _safe_connect() like the other startup migrations so a DB outage
    delays licensing rather than blocking the server from starting.
    """
    import logging

    conn = _safe_connect()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            for label, sql in LICENSING_DDL:
                try:
                    cur.execute(sql)
                except Exception as exc:
                    logging.warning("[licensing] DDL %s failed: %s", label, exc)
                    conn.rollback()
                    continue
                conn.commit()

            upsert, retire = sync_catalog_sql()
            try:
                for svc in SERVICES:
                    cur.execute(upsert, (
                        svc.key, svc.display_name, svc.description, svc.category,
                        svc.is_core, svc.default_enabled, svc.metered_unit,
                        service_sort_order(svc.key),
                    ))
                cur.execute(retire, ([s.key for s in SERVICES],))
                conn.commit()
            except Exception as exc:
                logging.warning("[licensing] catalog sync failed: %s", exc)
                conn.rollback()
    finally:
        conn.close()

def _migrate_bank_columns():
    """Add bank verification columns to dim_suppliers if not present."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE dim_suppliers
                    ADD COLUMN IF NOT EXISTS bank_account  text,
                    ADD COLUMN IF NOT EXISTS bank_ifsc     text,
                    ADD COLUMN IF NOT EXISTS bank_verified boolean NOT NULL DEFAULT false,
                    ADD COLUMN IF NOT EXISTS bank_name     text
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_file_data_column():
    """Store PDF bytes in DB so files survive Render redeploys."""
    conn = _safe_connect() 
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE raw_invoice_uploads
                    ADD COLUMN IF NOT EXISTS file_data bytea
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_tally_agent_columns():
    """Add agent pairing/auth columns to int_connections."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE int_connections
                    ADD COLUMN IF NOT EXISTS agent_api_key_hash     text,
                    ADD COLUMN IF NOT EXISTS agent_pairing_token    text,
                    ADD COLUMN IF NOT EXISTS agent_pairing_expires_at timestamptz,
                    ADD COLUMN IF NOT EXISTS agent_version          text
            """)
            # Index for fast api_key_hash lookups on every ingest request
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_int_connections_agent_api_key_hash
                ON int_connections (agent_api_key_hash)
                WHERE agent_api_key_hash IS NOT NULL
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_bill_attachments():
    """Create bill_attachments table if not present."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bill_attachments (
                    id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                    invoice_id    uuid        NOT NULL,
                    tenant_id     uuid        NOT NULL,
                    file_path     text        NOT NULL,
                    file_name     text        NOT NULL,
                    file_type     text,
                    file_size     bigint,
                    uploaded_by   text,
                    uploaded_at   timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bill_attachments_invoice ON bill_attachments (invoice_id)")
            # A user's delete only marks the row; the file stays in S3.
            cur.execute("ALTER TABLE bill_attachments ADD COLUMN IF NOT EXISTS deleted_at timestamptz")
            cur.execute("ALTER TABLE bill_attachments ADD COLUMN IF NOT EXISTS deleted_by text")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_tally_push():
    """Add tally_push_pending flag to fact_invoices."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE fact_invoices
                    ADD COLUMN IF NOT EXISTS tally_push_pending boolean NOT NULL DEFAULT false
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_supplier_gstns():
    """Create supplier_gstns table for vendors with multiple GSTNs."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS supplier_gstns (
                    id          uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
                    tenant_id   uuid NOT NULL,
                    supplier_id uuid NOT NULL,
                    gstn        char(15) NOT NULL,
                    label       text,
                    created_at  timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (tenant_id, gstn)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sgstns_supplier ON supplier_gstns (supplier_id)")
            # Backfill existing dim_suppliers.gstn into supplier_gstns
            cur.execute("""
                INSERT INTO supplier_gstns (tenant_id, supplier_id, gstn)
                SELECT tenant_id, supplier_id, gstn
                FROM dim_suppliers
                WHERE gstn IS NOT NULL
                ON CONFLICT (tenant_id, gstn) DO NOTHING
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_vendor_legal_name():
    """A vendor has two names: `name` is the brand / trading name the business
    uses, `legal_name` the registered one (what the GST portal returns). They are
    the same until the portal says otherwise. Filled by
    scripts/backfill_vendor_legal_name.py; NULL reads as "same as name"."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE dim_suppliers ADD COLUMN IF NOT EXISTS legal_name text")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)


def _migrate_vendor_pan():
    """Add pan column to dim_suppliers and create vendor_outreach_log if not present."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE dim_suppliers ADD COLUMN IF NOT EXISTS pan text")
            # Set true when a user is offered a PAN-duplicate merge and chooses to
            # KEEP the duplicate — drives the "Deduplicate" affordance in the vendor list.
            cur.execute("ALTER TABLE dim_suppliers ADD COLUMN IF NOT EXISTS pan_dup_deferred boolean NOT NULL DEFAULT false")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vendor_outreach_log (
                    id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                    supplier_id   uuid        NOT NULL,
                    tenant_id     uuid        NOT NULL,
                    requested_by  text,
                    requested_at  timestamptz NOT NULL DEFAULT now(),
                    email_to      text,
                    subject       text,
                    body          text,
                    status        text        NOT NULL DEFAULT 'sent',
                    PRIMARY KEY (id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_vol_supplier ON vendor_outreach_log (supplier_id)")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_dim_charges():
    """Create dim_charges table if not present."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dim_charges (
                    charge_id          uuid        NOT NULL DEFAULT gen_random_uuid(),
                    tenant_id          uuid        NOT NULL,
                    name               text        NOT NULL,
                    hsn_sac            text,
                    default_ledger_id  uuid,
                    default_tax_rate   numeric(5,2),
                    is_active          boolean     NOT NULL DEFAULT true,
                    created_at         timestamptz NOT NULL DEFAULT now(),
                    updated_at         timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (charge_id),
                    UNIQUE (tenant_id, name)
                )
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_line_item_ledger():
    """Add account_id + ledger_mapping_log table for ledger auto-suggestion."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE stg_bill_line_items
                ADD COLUMN IF NOT EXISTS account_id uuid
                    REFERENCES dim_ledger_accounts(account_id) ON DELETE SET NULL
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ledger_mapping_log (
                    id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                    tenant_id     uuid        NOT NULL,
                    supplier_id   uuid,
                    hsn_code      text,
                    description   text,
                    account_id    uuid        NOT NULL,
                    source        text        NOT NULL DEFAULT 'user',
                    created_at    timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lml_tenant_supplier ON ledger_mapping_log (tenant_id, supplier_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_lml_tenant_hsn ON ledger_mapping_log (tenant_id, hsn_code)")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_service_month():
    """Add service_month (the billing/service period the invoice covers) to
    stg_bills and stg_bill_line_items — captured at OCR, used by Nova reconciliation."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE stg_bills ADD COLUMN IF NOT EXISTS service_month text")
            cur.execute("ALTER TABLE stg_bill_line_items ADD COLUMN IF NOT EXISTS service_month text")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_gst_breakdown():
    """Keep the CGST/SGST/IGST split the OCR already reads.

    `taxes` arrives from extraction with each component and its rate, the
    review screen shows all of them as editable fields — and then ingest adds
    them together into stg_bills.tax_amount and drops the parts. From that
    moment the only place the split still exists is raw_ocr_results' payload
    blob, which nothing reads and which an edit to the bill never updates.

    So a GST return, an ITC claim or a simple "is this intra-state?" question
    has to be answered off the PDF, even though the platform read the answer
    at upload and threw it away. These columns are where it lands instead.
    """
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            for col in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount",
                        "cgst_rate", "sgst_rate", "igst_rate", "taxable_amount"):
                cur.execute(
                    f"ALTER TABLE stg_bills ADD COLUMN IF NOT EXISTS {col} NUMERIC(18,2)"
                )
            # The state whose GST applies. Printed on the invoice, and the
            # thing that decides whether CGST+SGST or IGST is correct — so a
            # split cannot really be checked without it.
            cur.execute(
                "ALTER TABLE stg_bills ADD COLUMN IF NOT EXISTS place_of_supply text"
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_coa_rules():
    """Create coa_rules table and seed default rules if table is empty."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS coa_rules (
                    id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                    tenant_id     uuid        NOT NULL,
                    exp_head      text        NOT NULL,
                    vendor_name   text,
                    category      text,
                    priority      int         NOT NULL DEFAULT 0,
                    created_at    timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_coa_rules_tenant ON coa_rules (tenant_id)")
            # Seed defaults only if empty for this tenant
            cur.execute("SELECT COUNT(*) FROM coa_rules WHERE tenant_id = %s", (TENANT_ID,))
            count = cur.fetchone()[0]
            if count == 0:
                defaults = [
                    ('Rent',               'UTILITY'),
                    ('Software / SaaS',    'IT'),
                    ('Food & Beverages',   'PANTRY'),
                    ('Utilities',          'UTILITY'),
                    ('Travel & Expenses',  'ADMINISTRATION'),
                    ('Reimbursement',      'ADMINISTRATION'),
                ]
                for cat, exp_head in defaults:
                    cur.execute(
                        "INSERT INTO coa_rules (tenant_id, exp_head, category, priority) VALUES (%s, %s, %s, 0)",
                        (TENANT_ID, exp_head, cat)
                    )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_fx_schema():
    """
    Bring fx_bill_payments and fact_invoices up to date with the FX feature schema.

    fx_bill_payments was originally created with:
      - invoice_id FK → fact_bills (wrong: frontend uses fact_invoices)
      - missing currency_code, bank_name, notes columns

    fact_invoices needs FX settlement columns (fx_actual_inr, fx_effective_rate,
    fx_realized_cost) that were only added to fact_bills in the initial migration.
    """
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            # Drop the incorrect FK constraint (references fact_bills, should be fact_invoices)
            cur.execute("""
                ALTER TABLE fx_bill_payments
                    DROP CONSTRAINT IF EXISTS fx_bill_payments_invoice_id_fkey
            """)
            # Add missing columns to fx_bill_payments
            cur.execute("""
                ALTER TABLE fx_bill_payments
                    ADD COLUMN IF NOT EXISTS currency_code char(3),
                    ADD COLUMN IF NOT EXISTS bank_name     text,
                    ADD COLUMN IF NOT EXISTS notes         text
            """)
            # Add FX settlement columns to fact_invoices (mirroring fact_bills)
            cur.execute("""
                ALTER TABLE fact_invoices
                    ADD COLUMN IF NOT EXISTS fx_actual_inr     numeric(18,2),
                    ADD COLUMN IF NOT EXISTS fx_effective_rate numeric(12,6),
                    ADD COLUMN IF NOT EXISTS fx_realized_cost  numeric(18,2)
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_fx_convert_existing():
    """Fix existing non-INR bills in fact_invoices where total_amount was never converted to INR.
    Detects unconverted bills by fx_rate_id IS NULL + currency_code not INR."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE fact_invoices fi
                SET total_amount       = fi.original_amount * dr.inr_per_unit,
                    outstanding_amount = GREATEST(fi.original_amount * dr.inr_per_unit - COALESCE(fi.amount_paid, 0), 0),
                    subtotal           = CASE WHEN fi.subtotal IS NOT NULL AND fi.subtotal > 0
                                              THEN fi.subtotal * dr.inr_per_unit
                                              ELSE fi.subtotal END,
                    tax_amount         = CASE WHEN fi.tax_amount IS NOT NULL AND fi.tax_amount > 0
                                              THEN fi.tax_amount * dr.inr_per_unit
                                              ELSE fi.tax_amount END,
                    fx_rate_id         = dr.rate_id
                FROM (
                    SELECT DISTINCT ON (currency_code) currency_code, inr_per_unit, rate_id
                    FROM dim_fx_rates
                    ORDER BY currency_code, rate_date DESC
                ) dr
                WHERE fi.currency_code = dr.currency_code
                  AND fi.currency_code IS NOT NULL
                  AND fi.currency_code != 'INR'
                  AND fi.original_amount IS NOT NULL
                  AND fi.original_amount > 0
                  AND fi.fx_rate_id IS NULL
            """)
            fixed = cur.rowcount
            if fixed:
                import logging
                logging.getLogger(__name__).info("[fx-migrate] converted %d existing non-INR bills to INR", fixed)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _migrate_perf_indexes():
    """Add indexes on hot query paths to speed up bills, vendors, and approval lookups."""
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_fi_tenant_status        ON fact_invoices (tenant_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_fi_tenant_supplier       ON fact_invoices (tenant_id, supplier_id)",
                "CREATE INDEX IF NOT EXISTS idx_fi_tenant_created        ON fact_invoices (tenant_id, created_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_fi_invoice_number        ON fact_invoices (tenant_id, invoice_number)",
                "CREATE INDEX IF NOT EXISTS idx_stg_bills_tenant_status  ON stg_bills (tenant_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_stg_bills_supplier       ON stg_bills (tenant_id, supplier_id)",
                "CREATE INDEX IF NOT EXISTS idx_dim_suppliers_tenant      ON dim_suppliers (tenant_id, is_active)",
                "CREATE INDEX IF NOT EXISTS idx_dim_suppliers_gstn        ON dim_suppliers (tenant_id, gstn) WHERE gstn IS NOT NULL",
                "CREATE INDEX IF NOT EXISTS idx_sba_supplier_primary      ON supplier_bank_accounts (supplier_id, is_primary)",
                "CREATE INDEX IF NOT EXISTS idx_sgstns_gstn               ON supplier_gstns (tenant_id, gstn)",
            ]
            for idx in indexes:
                cur.execute(idx)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _ensure_refresh_tokens_table():
    conn = _safe_connect()
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
                    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    token_hash  text UNIQUE NOT NULL,
                    username    text NOT NULL,
                    company_id  uuid NOT NULL,
                    issued_at   timestamptz NOT NULL DEFAULT now(),
                    expires_at  timestamptz NOT NULL,
                    revoked     boolean NOT NULL DEFAULT false,
                    revoked_at  timestamptz,
                    replaced_by text
                );
                CREATE INDEX IF NOT EXISTS idx_refresh_username ON auth_refresh_tokens(username);
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

def _ensure_approval_tables():
    import psycopg2, traceback
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # Add new approval statuses to the enum (safe to run multiple times)
            for val in ("PENDING_APPROVER", "PENDING_ACCOUNTANT", "PENDING_FC", "NEEDS_REVISION"):
                try:
                    cur.execute(f"ALTER TYPE invoice_status ADD VALUE IF NOT EXISTS '{val}'")
                    conn.commit()
                except Exception:
                    conn.rollback()

            # Add category column to bills tables
            for table in ("stg_bills", "fact_invoices"):
                try:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS category VARCHAR(100)")
                    conn.commit()
                except Exception:
                    conn.rollback()

            # Add department column to users if not exists
            try:
                cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS department VARCHAR(100)")
                conn.commit()
            except Exception:
                conn.rollback()

            # Add department to fact_invoices
            try:
                cur.execute("ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS department VARCHAR(100)")
                conn.commit()
            except Exception:
                conn.rollback()

            # Add uploader columns if they don't exist
            for col, typ in [("uploaded_by_name", "VARCHAR(100)"), ("uploaded_by_role", "VARCHAR(50)")]:
                try:
                    cur.execute(f"ALTER TABLE raw_invoice_uploads ADD COLUMN IF NOT EXISTS {col} {typ}")
                    conn.commit()
                except Exception:
                    conn.rollback()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS bill_approval_chain (
                    id          BIGSERIAL PRIMARY KEY,
                    invoice_id  UUID NOT NULL,
                    tenant_id   UUID NOT NULL,
                    step        SMALLINT NOT NULL,
                    actor_role  VARCHAR(50),
                    actor_name  VARCHAR(100),
                    action      VARCHAR(20) NOT NULL,
                    comment     TEXT,
                    created_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tenant_chain_config (
                    id          BIGSERIAL PRIMARY KEY,
                    tenant_id   UUID NOT NULL,
                    department  VARCHAR(100),
                    step_order  SMALLINT NOT NULL,
                    label       VARCHAR(100) NOT NULL,
                    role        VARCHAR(50) NOT NULL,
                    locked      BOOLEAN DEFAULT false,
                    is_snapshot BOOLEAN DEFAULT false,
                    updated_at  TIMESTAMPTZ DEFAULT now()
                )
            """)
            cur.execute("ALTER TABLE tenant_chain_config ADD COLUMN IF NOT EXISTS is_snapshot BOOLEAN DEFAULT false")
            cur.execute("ALTER TABLE tenant_chain_config ADD COLUMN IF NOT EXISTS assignees JSONB DEFAULT NULL")
        conn.commit()
        put_conn(conn)
        print("[startup] bill_approval_chain and tenant_chain_config tables ready")
    except Exception:
        print("[startup] ERROR creating approval tables:")
        traceback.print_exc()

async def on_startup():
    _ensure_approval_tables()

def _ensure_supplier_bank_accounts_table():
    import psycopg2, traceback
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplier_bank_accounts (
                id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                tenant_id     uuid        NOT NULL,
                supplier_id   uuid        NOT NULL,
                account_number text       NOT NULL,
                ifsc          text,
                bank_name     text,
                is_primary    boolean     NOT NULL DEFAULT false,
                verified      boolean     NOT NULL DEFAULT false,
                name_at_bank  text,
                created_at    timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (id)
            );
            CREATE INDEX IF NOT EXISTS idx_sba_tenant_supplier ON supplier_bank_accounts (tenant_id, supplier_id);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] supplier_bank_accounts table ready")
    except Exception:
        print("[startup] ERROR creating supplier_bank_accounts table:")
        traceback.print_exc()

async def on_startup_bank_accounts():
    _ensure_supplier_bank_accounts_table()

def _ensure_supplier_contacts_table():
    import traceback
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS supplier_contacts (
                id            uuid        NOT NULL DEFAULT gen_random_uuid(),
                tenant_id     uuid        NOT NULL,
                supplier_id   uuid        NOT NULL,
                name          text        NOT NULL,
                role          text,
                email         text,
                phone         text,
                is_primary    boolean     NOT NULL DEFAULT false,
                created_at    timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (id)
            );
            CREATE INDEX IF NOT EXISTS idx_sc_tenant_supplier ON supplier_contacts (tenant_id, supplier_id);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] supplier_contacts table ready")
    except Exception:
        print("[startup] ERROR creating supplier_contacts table:")
        traceback.print_exc()

async def on_startup_supplier_contacts():
    _ensure_supplier_contacts_table()
    # Agent tables ALTER supplier_contacts, so they must run *after* it exists.
    ensure_agent_tables()
    start_agent_poller()
    # Fact knowledge graph — depends on dim_suppliers / supplier_contacts /
    # fact_invoices already existing, so create it last.
    init_fact_graph_schema()

def _migrate_custom_categories():
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dim_categories (
                    id         uuid        NOT NULL DEFAULT gen_random_uuid(),
                    tenant_id  uuid        NOT NULL,
                    name       text        NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    PRIMARY KEY (id),
                    UNIQUE (tenant_id, name)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_dim_categories_tenant ON dim_categories (tenant_id)")
            cur.execute("ALTER TABLE dim_categories ADD COLUMN IF NOT EXISTS exp_head text")
            cur.execute("ALTER TABLE dim_categories ADD COLUMN IF NOT EXISTS is_builtin boolean NOT NULL DEFAULT false")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

async def _ensure_vendor_documents_table():
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendor_documents (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                supplier_id   UUID NOT NULL,
                tenant_id     UUID NOT NULL,
                brand_id      UUID,
                file_name     VARCHAR(255) NOT NULL,
                file_path     TEXT NOT NULL,
                file_size     BIGINT,
                uploaded_by   VARCHAR(100),
                uploaded_at   TIMESTAMPTZ DEFAULT now()
            )
        """)
        # Documents stay with the brand they were uploaded under, so migrating a
        # vendor to another brand leaves them behind (mirrors statements).
        cur.execute("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS brand_id UUID")
        # What a document is. Every upload used to be read as a contract, so
        # existing rows default to 'contract'; 'other' carries a type the team
        # names (doc_label, e.g. "Rate cards") and is stored but never read for
        # values. Bills are not stored here: the card lists them from the bills.
        cur.execute("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS doc_type VARCHAR(20) NOT NULL DEFAULT 'contract'")
        cur.execute("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS doc_label VARCHAR(80)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_vdoc_tenant_label ON vendor_documents (tenant_id, doc_label) WHERE doc_label IS NOT NULL")
        # A user's delete only marks the row; the file stays in S3.
        cur.execute("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE vendor_documents ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(100)")
        # Backfill existing rows from each vendor's current brand so nothing
        # disappears from view; shared vendors (brand_id NULL) stay NULL = visible everywhere.
        cur.execute("""
            UPDATE vendor_documents d
               SET brand_id = s.brand_id
              FROM dim_suppliers s
             WHERE d.supplier_id = s.supplier_id
               AND d.tenant_id   = s.tenant_id
               AND d.brand_id IS NULL
               AND s.brand_id IS NOT NULL
        """)
    conn.commit()
    put_conn(conn)

async def _ensure_vendor_logos_table():
    """Vendor logos, looked up from the vendor's website (routers/vendor_logos.py).
    status: found · none (nothing found; tried again after 30 days) · hidden (removed by hand)."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vendor_logos (
                supplier_id UUID PRIMARY KEY,
                tenant_id   UUID NOT NULL,
                domain      TEXT,
                source      VARCHAR(10),
                status      VARCHAR(10) NOT NULL,
                mime        VARCHAR(40),
                image       BYTEA,
                fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_vlogo_tenant ON vendor_logos (tenant_id)")
    conn.commit()
    put_conn(conn)

def _migrate_supplier_gstins():
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS supplier_gstins (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    supplier_id UUID NOT NULL REFERENCES dim_suppliers(supplier_id) ON DELETE CASCADE,
                    tenant_id   UUID NOT NULL,
                    gstin       TEXT NOT NULL,
                    pan         TEXT,
                    is_primary  BOOLEAN NOT NULL DEFAULT false,
                    status      TEXT NOT NULL DEFAULT 'UNVERIFIED',
                    trade_name  TEXT,
                    legal_name  TEXT,
                    gstin_status TEXT,
                    error_message TEXT,
                    verified_at  TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (supplier_id, gstin)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_supplier_gstins_supplier ON supplier_gstins(supplier_id)")
        conn.commit()
    finally:
        conn.close()

async def _migrate_brands():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS brands (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id   UUID NOT NULL,
                    name        VARCHAR(255) NOT NULL,
                    logo_path   TEXT,
                    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_brands_tenant ON brands (tenant_id)")

            # Add brand_id to fact_invoices and dim_suppliers (nullable for back-compat)
            cur.execute("ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS brand_id UUID")
            cur.execute("ALTER TABLE dim_suppliers ADD COLUMN IF NOT EXISTS brand_id UUID")

            # Give every tenant with invoices a default brand, named after *that*
            # tenant. This used to insert the literal 'Brightloans Private Limited'
            # for every tenant in the database — one customer's legal name printed
            # on everyone else's invoices.
            cur.execute("""
                INSERT INTO brands (tenant_id, name, is_default)
                SELECT DISTINCT fi.tenant_id,
                       COALESCE(t.legal_name, t.company_name, 'Default'), TRUE
                FROM fact_invoices fi
                LEFT JOIN dim_tenants t ON t.tenant_id = fi.tenant_id
                WHERE fi.tenant_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM brands b WHERE b.tenant_id = fi.tenant_id)
                ON CONFLICT DO NOTHING
            """)

            # Backfill brand_id on existing rows using the default brand per tenant
            cur.execute("""
                UPDATE fact_invoices fi
                SET brand_id = b.id
                FROM brands b
                WHERE b.tenant_id = fi.tenant_id
                  AND b.is_default = TRUE
                  AND fi.brand_id IS NULL
            """)
            cur.execute("""
                UPDATE dim_suppliers ds
                SET brand_id = b.id
                FROM brands b
                WHERE b.tenant_id = ds.tenant_id
                  AND b.is_default = TRUE
                  AND ds.brand_id IS NULL
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

async def _migrate_cost_centers():
    """Cost centers: an OPTIONAL, admin-configurable, brand-scoped tag on bills.
    Nothing is seeded — the feature does not exist for a tenant until an admin
    creates cost centers in Settings. Mirrors _migrate_brands (table + nullable
    column on fact_invoices), minus the default-row seeding."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cost_centers (
                    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id   UUID NOT NULL,
                    brand_id    UUID NOT NULL,
                    name        VARCHAR(255) NOT NULL,
                    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cost_centers_tenant_brand ON cost_centers (tenant_id, brand_id)")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_centers_tenant_brand_name ON cost_centers (tenant_id, brand_id, name)")
            # Nullable tag on bills — bills stay untagged until a cost center is chosen.
            cur.execute("ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS cost_center_id UUID")
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)

async def _migrate_soft_status():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS soft_status VARCHAR(20) NOT NULL DEFAULT 'active'"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_fi_soft_status ON fact_invoices (tenant_id, soft_status)"
            )
            # Holds the staging link of a bill moved to the hold/deleted collection.
            # While archived, the live stg_bill_id is set NULL so the partial unique
            # index (tenant_id, stg_bill_id) frees up and the same bill can be
            # re-uploaded; restore re-links it if the slot is still free.
            cur.execute(
                "ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS archived_stg_bill_id uuid"
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        import logging as _lg; _lg.warning("[startup] soft_status migration failed: %s", exc)
    finally:
        put_conn(conn)


def _migrate_line_item_percent_rate() -> None:
    """Let a line express its Rate as a percentage of a base.

    Indian invoices routinely price a line as a percentage rather than a unit
    price — commission on sales, a platform fee, brokerage, interest on an
    overdue amount. The rest of the pipeline assumes line_total = quantity x
    unit_price (ocr_engine reconciles that triple and ingest recomputes from it),
    so a percentage line needs to be marked as such or its total gets recomputed
    to nonsense.

    When the flag is set: quantity carries the BASE amount, unit_price carries the
    PERCENT, and line_total = quantity x unit_price / 100. Default false, so every
    existing row keeps the qty x rate meaning it already had.
    """
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                ALTER TABLE stg_bill_line_items
                ADD COLUMN IF NOT EXISTS rate_is_percent boolean NOT NULL DEFAULT false
            """)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        put_conn(conn)


def _migrate_vendor_activity_log() -> None:
    """vendor_activity_log: who changed what on a vendor, for the Activity tab.

    Covers the vendor actions that wrote no audit of their own — master-data
    edits, bank accounts, contacts, GSTINs, document re-filing — written by
    core/vendor_activity_log.py inside each action's transaction. title and
    changes are display-ready (account numbers already masked); meta carries
    the entity id so the timeline can drop the current-state copy of a row the
    log already describes.
    """
    conn = _safe_connect()
    if conn is None: return
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vendor_activity_log (
                    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id   uuid        NOT NULL,
                    supplier_id uuid        NOT NULL,
                    actor       text,
                    actor_role  text,
                    action      text        NOT NULL,
                    title       text        NOT NULL,
                    changes     jsonb,
                    meta        jsonb,
                    created_at  timestamptz NOT NULL DEFAULT now()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_vendor_activity_log_supplier
                    ON vendor_activity_log (tenant_id, supplier_id, created_at DESC)
            """)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        import logging as _lg; _lg.warning("[migrate] vendor_activity_log failed: %s", exc)
    finally:
        conn.close()  # _safe_connect() opens a direct connection, not a pooled one


# ── Manual migration plan ────────────────────────────────────────────────────
# Order matters: some later steps assume tables an earlier one made. This tuple
# is deliberately *not* registered with FastAPI. Run it explicitly with:
#   python3 scripts/run_api_migrations.py
# before deploying or restarting the API.
#
MIGRATION_STEPS = (
    on_startup_user_email_connections,
    _migrate_licensing,
    _migrate_bank_columns,
    _migrate_file_data_column,
    _migrate_tally_agent_columns,
    _migrate_bill_attachments,
    _migrate_tally_push,
    _migrate_supplier_gstns,
    _migrate_vendor_pan,
    _migrate_vendor_legal_name,
    _migrate_dim_charges,
    _migrate_line_item_ledger,
    _migrate_line_item_percent_rate,
    _migrate_service_month,
    _migrate_gst_breakdown,
    _migrate_coa_rules,
    _migrate_fx_schema,
    _migrate_fx_convert_existing,
    _migrate_perf_indexes,
    _ensure_refresh_tokens_table,
    on_startup,
    on_startup_bank_accounts,
    on_startup_supplier_contacts,
    _migrate_custom_categories,
    _ensure_vendor_documents_table,
    _ensure_vendor_logos_table,
    _migrate_supplier_gstins,
    _migrate_brands,
    _migrate_cost_centers,
    _migrate_soft_status,
    _migrate_vendor_activity_log,
)
