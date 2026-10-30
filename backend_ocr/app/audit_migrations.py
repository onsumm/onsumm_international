"""Schema for the hash-chained audit log and its on-chain anchors.

Why a trigger and not Python
────────────────────────────
`wf_audit_log` is written from many places — the disbursement router's
`_audit()`, `BaseWorker.log_audit` in the pipelines, and a long tail of inline
INSERTs. Hashing in Python would only cover the writers someone remembered to
change. A BEFORE INSERT trigger covers every writer, today's and tomorrow's,
with no code change anywhere else.

Per row, inside the inserting transaction:

    seq       = previous seq for the tenant + 1
    prev_hash = previous row_hash for the tenant, or 32 zero bytes
    canonical = wf_audit_canonical(...)            -- one jsonb object, as text
    row_hash  = sha256(prev_hash || utf8(canonical))

`pg_advisory_xact_lock` on the tenant keeps `seq` gap-free when two
transactions write audit rows for the same tenant at once.

The trigger must never stop the business write it rides on. Any failure inside
it is caught, logged as a WARNING, and the row is stored unhashed (`seq`,
`prev_hash`, `row_hash` NULL) — it is then simply outside the chain, which
`verify_chain` reports, rather than a payment that failed because of an audit
detail.

`wf_audit_canonical()` is the single definition of the hashed text. The proof
bundle and `verify_chain` call it too, so "what was hashed" and "what we show
the auditor" cannot drift apart.

Run by scripts/run_api_migrations.py like every other step; each step is
idempotent.
"""
from __future__ import annotations

import traceback

from .config import get_conn, put_conn


def _run(label: str, sql: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print(f"[migration] {label} ready")
    except Exception:
        conn.rollback()
        print(f"[migration] ERROR in {label}:")
        traceback.print_exc()
        raise
    finally:
        put_conn(conn)


def _ensure_pgcrypto() -> None:
    _run("pgcrypto", "CREATE EXTENSION IF NOT EXISTS pgcrypto;")


def _migrate_audit_hash_columns() -> None:
    _run("wf_audit_log hash columns", """
        ALTER TABLE wf_audit_log ADD COLUMN IF NOT EXISTS seq       bigint;
        ALTER TABLE wf_audit_log ADD COLUMN IF NOT EXISTS prev_hash bytea;
        ALTER TABLE wf_audit_log ADD COLUMN IF NOT EXISTS row_hash  bytea;
        ALTER TABLE wf_audit_log ADD COLUMN IF NOT EXISTS anchor_id bigint;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_audit_tenant_seq
            ON wf_audit_log (tenant_id, seq) WHERE seq IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_wf_audit_unanchored
            ON wf_audit_log (tenant_id, seq) WHERE anchor_id IS NULL AND row_hash IS NOT NULL;
    """)


# The canonical text. Field order inside jsonb is normalised by Postgres
# (shorter keys first, then bytewise), so the same row always yields the same
# text. created_at is rendered explicitly in UTC with microseconds.
_CANONICAL_FN = r"""
CREATE OR REPLACE FUNCTION wf_audit_canonical(
    p_tenant_id uuid, p_seq bigint, p_table_name text, p_entity_id uuid, p_action text,
    p_user_id uuid, p_correlation_id uuid, p_old jsonb, p_new jsonb, p_created_at timestamptz
) RETURNS text
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT jsonb_build_object(
        'tenant_id',        p_tenant_id::text,
        'seq',              p_seq,
        'table_name',       p_table_name,
        'entity_id',        p_entity_id::text,
        'action',           p_action,
        'user_id',          p_user_id::text,
        'correlation_id',   p_correlation_id::text,
        'old_values_jsonb', p_old,
        'new_values_jsonb', p_new,
        'created_at',       to_char(p_created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
    )::text
$$;

CREATE OR REPLACE FUNCTION wf_audit_canonical_text(p_log_id uuid) RETURNS text
LANGUAGE sql STABLE
SET search_path = public, pg_temp
AS $$
    SELECT wf_audit_canonical(tenant_id, seq, table_name, entity_id, action::text,
                              user_id, correlation_id, old_values_jsonb, new_values_jsonb, created_at)
      FROM wf_audit_log WHERE log_id = p_log_id
$$;
"""

_TRIGGER_FN = r"""
CREATE OR REPLACE FUNCTION wf_audit_hash_chain() RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    v_seq  bigint;
    v_prev bytea;
BEGIN
    BEGIN
        NEW.created_at := COALESCE(NEW.created_at, now());
        PERFORM pg_advisory_xact_lock(hashtextextended('wf_audit_log:' || NEW.tenant_id::text, 0));
        SELECT seq, row_hash INTO v_seq, v_prev
          FROM wf_audit_log
         WHERE tenant_id = NEW.tenant_id AND seq IS NOT NULL
         ORDER BY seq DESC
         LIMIT 1;
        NEW.seq       := COALESCE(v_seq, 0) + 1;
        NEW.prev_hash := COALESCE(v_prev, decode(repeat('00', 32), 'hex'));
        NEW.row_hash  := digest(
            NEW.prev_hash || convert_to(
                wf_audit_canonical(NEW.tenant_id, NEW.seq, NEW.table_name, NEW.entity_id, NEW.action::text,
                                   NEW.user_id, NEW.correlation_id, NEW.old_values_jsonb,
                                   NEW.new_values_jsonb, NEW.created_at),
                'UTF8'),
            'sha256');
        NEW.anchor_id := NULL;
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'wf_audit_log hash chain skipped for tenant %: %', NEW.tenant_id, SQLERRM;
        NEW.seq := NULL; NEW.prev_hash := NULL; NEW.row_hash := NULL;
    END;
    RETURN NEW;
END
$$;
"""

# Hashes every tenant whose rows are not yet chained, oldest first. Only
# tenants with no hashed row at all are backfilled: once a tenant's chain has
# started, an unhashed row is a trigger failure to report, not to paper over.
_BACKFILL_FN = r"""
CREATE OR REPLACE FUNCTION wf_audit_backfill_chain() RETURNS integer
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    t      record;
    r      record;
    v_seq  bigint;
    v_prev bytea;
    v_hash bytea;
    n      integer := 0;
BEGIN
    FOR t IN
        SELECT tenant_id FROM wf_audit_log
         GROUP BY tenant_id
        HAVING count(seq) = 0
    LOOP
        v_seq  := 0;
        v_prev := decode(repeat('00', 32), 'hex');
        FOR r IN
            SELECT log_id, tenant_id, table_name, entity_id, action::text AS action, user_id,
                   correlation_id, old_values_jsonb, new_values_jsonb, created_at
              FROM wf_audit_log
             WHERE tenant_id = t.tenant_id
             ORDER BY created_at, log_id
        LOOP
            v_seq  := v_seq + 1;
            v_hash := digest(v_prev || convert_to(
                          wf_audit_canonical(r.tenant_id, v_seq, r.table_name, r.entity_id, r.action,
                                             r.user_id, r.correlation_id, r.old_values_jsonb,
                                             r.new_values_jsonb, r.created_at), 'UTF8'), 'sha256');
            UPDATE wf_audit_log SET seq = v_seq, prev_hash = v_prev, row_hash = v_hash
             WHERE log_id = r.log_id;
            v_prev := v_hash;
            n := n + 1;
        END LOOP;
    END LOOP;
    RETURN n;
END
$$;
"""


def _migrate_audit_chain_functions() -> None:
    _run("wf_audit_log canonical + chain functions", _CANONICAL_FN + _TRIGGER_FN + _BACKFILL_FN)


def _migrate_audit_chain_backfill_and_trigger() -> None:
    """Backfill and install the trigger under one table lock, so no insert can
    land between "last backfilled row" and "trigger active" and fork the chain."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("LOCK TABLE wf_audit_log IN SHARE ROW EXCLUSIVE MODE")
        cur.execute("SELECT wf_audit_backfill_chain()")
        hashed = cur.fetchone()[0]
        cur.execute("""
            DROP TRIGGER IF EXISTS trg_wf_audit_hash_chain ON wf_audit_log;
            CREATE TRIGGER trg_wf_audit_hash_chain
                BEFORE INSERT ON wf_audit_log
                FOR EACH ROW EXECUTE FUNCTION wf_audit_hash_chain();
        """)
        conn.commit()
        print(f"[migration] wf_audit_log chain backfilled ({hashed} rows) and trigger installed")
    except Exception:
        conn.rollback()
        print("[migration] ERROR in wf_audit_log backfill/trigger:")
        traceback.print_exc()
        raise
    finally:
        put_conn(conn)


def _migrate_audit_anchor_tables() -> None:
    _run("audit anchor tables", """
        CREATE TABLE IF NOT EXISTS tenant_audit_salts (
            tenant_id  uuid        PRIMARY KEY,
            salt       bytea       NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS audit_anchors (
            id           bigserial   PRIMARY KEY,
            merkle_root  bytea       NOT NULL,
            from_seq     bigint,
            to_seq       bigint,
            tenant_count int         NOT NULL DEFAULT 0,
            chain        text,
            contract     text,
            tx_hash      text,
            status       text        NOT NULL DEFAULT 'BUILT',
            anchored_at  timestamptz NOT NULL DEFAULT now()
        );
        ALTER TABLE audit_anchors ADD COLUMN IF NOT EXISTS mode     text;
        ALTER TABLE audit_anchors ADD COLUMN IF NOT EXISTS chain_id bigint;
        ALTER TABLE audit_anchors ADD COLUMN IF NOT EXISTS error    text;
        ALTER TABLE audit_anchors ADD COLUMN IF NOT EXISTS contract text;

        CREATE TABLE IF NOT EXISTS audit_anchor_tenant_roots (
            anchor_id   bigint  NOT NULL REFERENCES audit_anchors(id) ON DELETE CASCADE,
            tenant_id   uuid    NOT NULL,
            tenant_root bytea   NOT NULL,
            first_seq   bigint  NOT NULL,
            last_seq    bigint  NOT NULL,
            leaf_count  int     NOT NULL,
            PRIMARY KEY (anchor_id, tenant_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wf_audit_anchor ON wf_audit_log (anchor_id) WHERE anchor_id IS NOT NULL;
    """)


AUDIT_MIGRATION_STEPS = (
    _ensure_pgcrypto,
    _migrate_audit_hash_columns,
    _migrate_audit_chain_functions,
    _migrate_audit_chain_backfill_and_trigger,
    _migrate_audit_anchor_tables,
)
