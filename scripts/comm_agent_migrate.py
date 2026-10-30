"""
Communication + Compliance agent runtime — DB migration.
Implements docs/HLD_LLD_COMM_COMPLIANCE_REVIEWHUB.md §5.1.2, §5.1.3, §5.1.1 Phase 0+1.

Everything here is additive: new tables (`comm_thread_state`, `comm_extractions`),
new columns on the existing `poc_email_messages` table, and two `agent_registry`
seed rows. Nothing already-shipped is touched or dropped.

`agent_registry` (see scripts/agent_runtime_migrate.py) has no `tenant_id`
column — it is a global catalogue keyed by `name`, not a per-tenant table. The
HLD draft assumed a per-tenant config row; the real table disagrees, so the
compliance policy lives as one global default row (`agent_registry.config`)
that every tenant reads unless/until a per-tenant override table is built —
same fallback-to-sane-defaults shape `VendorComplianceAdapter` already uses
when no row exists at all.

Reads DB_URL from config.py (.env). Safe to re-run.

To invoke (mirrors scripts/agent_runtime_migrate.py exactly):

    python3.12 scripts/comm_agent_migrate.py

Do not wire this into a startup hook — like review_hub_migrate.py and
agent_runtime_migrate.py, it is a deliberate, reviewed, manually-run step.
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


DDL: list[tuple[str, str]] = [
    # ── Conversation state (§5.1.2) ─────────────────────────────────────────────
    # Additive to poc_email_messages/vendor_outreach_log: those two already carry
    # every individual message, but neither is a first-class "where does this
    # thread stand right now" row. last_direction flips status between
    # awaiting_vendor/awaiting_internal at the two write sites (agent.py's inbound
    # poller, email_connection.py's outbound send) — see comm_extraction_worker.py
    # and the send_contact_email patch for the two flips.
    ("Create comm_thread_state", """
        CREATE TABLE IF NOT EXISTS comm_thread_state (
            tenant_id     uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            thread_id     text NOT NULL,
            supplier_id   uuid,
            contact_id    uuid,
            purpose       text NOT NULL DEFAULT 'general'
                          CHECK (purpose IN ('bill_followup','contract_request',
                                              'verification_request','general')),
            status        text NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open','awaiting_vendor','awaiting_internal','resolved')),
            linked_subject_type text,     -- 'bill' | 'vendor' | 'contract'
            linked_subject_id   uuid,
            last_direction  text,         -- 'inbound' | 'outbound'
            last_message_at timestamptz,
            sla_due_at      timestamptz,  -- e.g. "follow up if no reply in 3 days"
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, thread_id)
        );
    """),
    ("Create comm_thread_state open index", """
        CREATE INDEX IF NOT EXISTS idx_comm_thread_state_open
            ON comm_thread_state (tenant_id, status) WHERE status <> 'resolved';
    """),

    # ── Claims store (§5.1.3) ────────────────────────────────────────────────────
    # Per-claim lifecycle lives inside claims[].status (PENDING -> APPLIED /
    # DISCARDED / CONFLICTED -> RESOLVED) rather than a child table, so a
    # partially-applied extraction (three claims, one conflict) needs no join.
    ("Create comm_extractions", """
        CREATE TABLE IF NOT EXISTS comm_extractions (
            extraction_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            source_type     text NOT NULL CHECK (source_type IN ('email','contract')),
            source_id       uuid NOT NULL,
            supplier_id     uuid,
            contact_id      uuid,
            thread_id       text,
            model           text,
            prompt_version  text,
            agent_run_id    uuid,
            claims          jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
    """),
    ("Create comm_extractions supplier index", """
        CREATE INDEX IF NOT EXISTS idx_comm_extractions_supplier
            ON comm_extractions (tenant_id, supplier_id, created_at DESC);
    """),

    # ── poc_email_messages: claim-extraction lifecycle (§5.1.1) ─────────────────
    # PENDING is the default so every historical row backfills as "not yet
    # extracted" without a separate UPDATE — CommExtractionWorker's
    # skip_backfill=True means those rows are deliberately never drained on
    # first boot (see that worker's module docstring), but they do not need a
    # different default to express that; the worker just never claims them.
    ("Add poc_email_messages claims_status", """
        ALTER TABLE poc_email_messages
            ADD COLUMN IF NOT EXISTS claims_status text NOT NULL DEFAULT 'PENDING'
                CHECK (claims_status IN ('PENDING','RUNNING','DONE','SKIPPED','FAILED'));
    """),
    ("Add poc_email_messages claims_error", """
        ALTER TABLE poc_email_messages
            ADD COLUMN IF NOT EXISTS claims_error text;
    """),
    ("Create poc_email_messages pending-claims index", """
        CREATE INDEX IF NOT EXISTS idx_pem_claims_pending
            ON poc_email_messages (tenant_id, created_at)
            WHERE claims_status = 'PENDING' AND direction = 'inbound';
    """),

    # ── vendor_documents: claim-extraction lifecycle (§5.1.4, Phase 3) ──────────
    # Same PENDING/RUNNING/DONE/SKIPPED/FAILED vocabulary as poc_email_messages
    # above — CommExtractionWorker's contract path claims rows here the same
    # way its email path claims poc_email_messages rows. vendor_documents
    # predates this feature (backend_ocr/app/migrations.py's
    # _ensure_vendor_documents_table) and never needed a processing lifecycle
    # before now, so both columns are new.
    ("Add vendor_documents claims_status", """
        ALTER TABLE vendor_documents
            ADD COLUMN IF NOT EXISTS claims_status text NOT NULL DEFAULT 'PENDING'
                CHECK (claims_status IN ('PENDING','RUNNING','DONE','SKIPPED','FAILED'));
    """),
    ("Add vendor_documents claims_error", """
        ALTER TABLE vendor_documents
            ADD COLUMN IF NOT EXISTS claims_error text;
    """),
    ("Create vendor_documents pending-claims index", """
        CREATE INDEX IF NOT EXISTS idx_vdoc_claims_pending
            ON vendor_documents (tenant_id, uploaded_at)
            WHERE claims_status = 'PENDING';
    """),

    # ── agent_registry seeds ─────────────────────────────────────────────────────
    ("Seed the communication agent", """
        INSERT INTO agent_registry (name, display_name, description, autonomy, enabled, config)
        VALUES (
            'communication',
            'Communication Agent',
            'Extracts typed, field-addressable claims (bank/GSTIN/PAN changes) out '
            'of inbound vendor email, so master-data changes can be diffed against '
            'dim_suppliers instead of living as free text in the fact graph.',
            1, true,
            -- stall_days: comm_stall.py's default if this row is absent is the
            -- same number, so a tenant with no row and a tenant with this
            -- untouched default behave identically.
            '{"claim_model": "gpt-4o-mini", "stall_days": 3}'::jsonb
        )
        ON CONFLICT (name) DO NOTHING;
    """),
    ("Seed the compliance agent", """
        INSERT INTO agent_registry (name, display_name, description, autonomy, enabled, config)
        VALUES (
            'compliance',
            'Compliance Agent',
            'Deterministic adapters that score vendor completeness (GSTIN/PAN/bank) '
            'and reconcile communication-extracted claims against the vendor master.',
            0, true,
            '{"requires_gstin": true, "requires_pan": true, "requires_bank": true, '
            '"requires_tds_section": false, "grace_days_new_vendor": 7}'::jsonb
        )
        ON CONFLICT (name) DO NOTHING;
    """),
]


def _connect():
    """Connect the way the app does, not the way RDS alone expects.

    config.DB_SSL_CERT is the RDS global bundle, which correctly verifies the
    production instance and cannot verify the self-hosted dev server — whose
    certificate is not signed by it. The app's own pool sidesteps this by putting
    only `sslmode` in the DSN, so a migration that insists on the bundle simply
    cannot be run against dev. Try verified first, then fall back to an encrypted
    but unverified connection, and say which one happened.
    """
    try:
        return psycopg2.connect(
            config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT
        )
    except psycopg2.OperationalError as exc:
        if "certificate verify failed" not in str(exc):
            raise
        print("  [note] server certificate is not in the RDS bundle — connecting "
              "encrypted but unverified (expected on the self-hosted dev box)")
        return psycopg2.connect(config.DATABASE_URL, sslmode="require")


def run() -> None:
    print("Communication + Compliance agent migration\n")
    conn = _connect()
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
        cur.execute(
            "INSERT INTO schema_migrations (label) VALUES (%s) ON CONFLICT DO NOTHING",
            ("comm_agent_v1",),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"  [warn] could not record in schema_migrations: {exc}")

    cur.close()
    conn.close()
    print(f"\nDone — {passed} passed, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    run()
