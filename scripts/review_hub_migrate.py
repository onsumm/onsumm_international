"""
Review Hub — DB migration.

Creates the `rh_*` family: one canonical findings store that every issue in the
platform lands in, whether it came from a deterministic adapter or from the
review agent. See docs/ plan: "Review Hub — a per-user command centre".

Why this is a script and not a step in backend_ocr/app/migrations.py
───────────────────────────────────────────────────────────────────
migrations.py runs 28 fire-and-forget DDL functions as FastAPI startup handlers,
each swallowing exceptions. That is fine for `ADD COLUMN IF NOT EXISTS`; it is
wrong for a seven-table subsystem, because a half-created schema would be
silently accepted and the hub would boot with sequential scans on its hottest
table and nobody would know. This script fails loudly and is run deliberately
before deploy. `migrations.py` imports DDL from here for dev self-heal, so the
SQL has exactly one source and cannot drift.

Design notes worth keeping in view while reading the DDL
────────────────────────────────────────────────────────
* Every new categorical column is `text` + CHECK, not a Postgres enum. Enums
  cannot be extended inside a transaction (hence the DO $$ EXCEPTION dance in
  billing_reconciliation_migrate.py), and `finding_type` is deliberately
  open-ended — the agent mints new ones at runtime. `severity_level` is the one
  enum reused, because it already exists and four levels is the whole range.
* `resolved_at IS NULL` is the single liveness predicate. Every hot index is
  partial on it, so the interactive working set stays small even when the
  archive is millions of rows.
* The unique index is partial too, which is what makes re-detection after a
  human resolution insert a NEW row (a genuine regression with its own
  first_detected_at) rather than resurrecting the old one.

Reads DB_URL from config.py (.env) — no hardcoded credentials.
Safe to re-run.
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


DDL: list[tuple[str, str]] = [

    # ── 1. rh_findings — the canonical row ────────────────────────────────────
    ("Create rh_findings", """
        CREATE TABLE IF NOT EXISTS rh_findings (
            finding_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id          uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,

            -- Dedup identity. ALWAYS computed by us as
            -- "{finding_type}:{subject_type}:{subject_id}[:qualifier]".
            -- Never emitted verbatim by a model: three paraphrases of one problem
            -- would become three findings and occurrence_count would never move.
            finding_key        text NOT NULL CHECK (length(finding_key) BETWEEN 3 AND 240),

            -- finding_type is free-text and unbounded: that is the point.
            -- family is a closed 8-value spine so lanes and badges stay fixed.
            finding_type       text NOT NULL CHECK (finding_type ~ '^[a-z][a-z0-9_]{2,79}$'),
            family             text NOT NULL CHECK (family IN (
                                   'approval','payment','vendor_identity','tax',
                                   'reconciliation','data_quality','duplication','fx')),
            type_id            uuid,

            subject_type       text NOT NULL CHECK (subject_type IN (
                                   'bill','invoice','vendor','payment','disbursement',
                                   'upload','line_item','gstin','bank_account',
                                   'recon_run','agent_run','tenant')),
            subject_id         uuid,

            severity           severity_level NOT NULL,
            confidence         numeric(4,3) NOT NULL DEFAULT 0.500
                                   CHECK (confidence BETWEEN 0 AND 1),
            verification       text NOT NULL DEFAULT 'UNVERIFIED' CHECK (verification IN (
                                   'VERIFIED','UNVERIFIABLE','UNVERIFIED','REFUTED')),
            money_at_risk      numeric(18,2),
            money_currency     char(3) NOT NULL DEFAULT 'INR',
            money_at_risk_inr  numeric(18,2),
            due_at             timestamptz,
            rank_score         numeric(12,6) NOT NULL DEFAULT 0,

            title              text NOT NULL,
            detail             text,
            evidence_jsonb     jsonb NOT NULL DEFAULT '{}'::jsonb,
            actions_jsonb      jsonb NOT NULL DEFAULT '[]'::jsonb,
            deep_link          text NOT NULL,

            audience_roles       text[] NOT NULL DEFAULT '{}',
            audience_users       text[] NOT NULL DEFAULT '{}',
            audience_departments text[] NOT NULL DEFAULT '{}',
            required_capability  text NOT NULL DEFAULT 'view' CHECK (required_capability IN (
                                     'view','edit','approve','verify','pay','admin')),

            status             text NOT NULL DEFAULT 'OPEN' CHECK (status IN (
                                   'OPEN','ACKNOWLEDGED','SNOOZED',
                                   'RESOLVED','DISMISSED','AUTO_RESOLVED')),
            status_reason      text,
            status_by          text,
            status_at          timestamptz,
            snoozed_until      timestamptz,
            resolved_at        timestamptz,

            -- Which service licence this finding originated from. The hub is core
            -- and default-on, but a tenant without an FX licence must not be shown
            -- FX findings, so the list handler filters on this.
            source_service     text NOT NULL DEFAULT 'bills',
            detector           text NOT NULL,
            detector_version   text NOT NULL DEFAULT 'v1',
            agent_run_id       uuid,
            model              text,
            cost_usd           numeric(12,6),

            condition_hash     text NOT NULL,
            occurrence_count   integer NOT NULL DEFAULT 1,
            first_detected_at  timestamptz NOT NULL DEFAULT now(),
            last_seen_at       timestamptz NOT NULL DEFAULT now(),
            preempted_event_at timestamptz,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now()
        );
    """),

    ("Create rh_findings live-key unique index", """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rh_findings_live_key
            ON rh_findings (tenant_id, finding_key) WHERE resolved_at IS NULL;
    """),

    ("Create rh_findings stream index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_stream
            ON rh_findings (tenant_id, rank_score DESC, finding_id DESC)
            WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings lane index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_lane
            ON rh_findings (tenant_id, family, rank_score DESC, finding_id DESC)
            WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings subject index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_subject
            ON rh_findings (tenant_id, subject_type, subject_id)
            WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings counts index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_counts
            ON rh_findings (tenant_id, family, severity)
            WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings audience indexes", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_aud_users
            ON rh_findings USING gin (audience_users) WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings audience role index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_aud_roles
            ON rh_findings USING gin (audience_roles) WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings sweep index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_sweep
            ON rh_findings (tenant_id, detector, last_seen_at)
            WHERE resolved_at IS NULL;
    """),
    ("Create rh_findings type-history index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_type_hist
            ON rh_findings (tenant_id, finding_type, created_at DESC);
    """),
    ("Create rh_findings archive index", """
        CREATE INDEX IF NOT EXISTS idx_rh_findings_archive
            ON rh_findings (resolved_at) WHERE resolved_at IS NOT NULL;
    """),

    # ── 2. rh_finding_entities — a finding can span several records ───────────
    ("Create rh_finding_entities", """
        CREATE TABLE IF NOT EXISTS rh_finding_entities (
            finding_id   uuid NOT NULL REFERENCES rh_findings(finding_id) ON DELETE CASCADE,
            tenant_id    uuid NOT NULL,
            entity_type  text NOT NULL,
            entity_id    uuid NOT NULL,
            entity_role  text NOT NULL DEFAULT 'related'
                         CHECK (entity_role IN ('subject','related','counterparty','evidence')),
            label        text,
            PRIMARY KEY (finding_id, entity_type, entity_id)
        );
    """),
    ("Create rh_finding_entities entity index", """
        CREATE INDEX IF NOT EXISTS idx_rh_fe_entity
            ON rh_finding_entities (tenant_id, entity_type, entity_id);
    """),

    # ── 3. rh_finding_events — timeline, and the precision label store ────────
    ("Create rh_finding_events", """
        CREATE TABLE IF NOT EXISTS rh_finding_events (
            event_id      bigserial PRIMARY KEY,
            finding_id    uuid NOT NULL REFERENCES rh_findings(finding_id) ON DELETE CASCADE,
            tenant_id     uuid NOT NULL,
            event_type    text NOT NULL CHECK (event_type IN (
                              'DETECTED','REDETECTED','VIEWED','ACKNOWLEDGED','SNOOZED',
                              'OPEN','RESOLVED','DISMISSED','AUTO_RESOLVED',
                              'ACTION_EXECUTED','COMMENT','RERANKED','REFUTED','FEEDBACK')),
            actor         text,
            reason        text,
            payload_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
    """),
    # `_write_event` records the status verbatim as the event type, so every
    # status the API accepts has to be listed above. OPEN was not: it has been
    # a legal status since the endpoint was written (it is the un-snooze), and
    # taking it wrote an event row that failed this CHECK — a 500 on the single
    # endpoint, and on the bulk one a failed statement that aborted the whole
    # transaction and took the rest of the batch with it.
    #
    # Separate step rather than an edit to the CREATE above, because CREATE
    # TABLE IF NOT EXISTS is a no-op against a table that already exists, which
    # every deployed environment has. Dropping first keeps it re-runnable.
    ("Widen rh_finding_events event_type to include OPEN", """
        ALTER TABLE rh_finding_events
            DROP CONSTRAINT IF EXISTS rh_finding_events_event_type_check;
        ALTER TABLE rh_finding_events
            ADD CONSTRAINT rh_finding_events_event_type_check CHECK (event_type IN (
                'DETECTED','REDETECTED','VIEWED','ACKNOWLEDGED','SNOOZED','OPEN',
                'RESOLVED','DISMISSED','AUTO_RESOLVED','ACTION_EXECUTED',
                'COMMENT','RERANKED','REFUTED','FEEDBACK'));
    """),
    # sweep_autoresolve closes what a detector stopped asserting, and it is
    # keyed on the detector — so a detector that is *removed* never sweeps, and
    # everything it ever raised sits open forever. vendor_field_drift was
    # folded into bill_coding, which proposes the same corrections in one place
    # instead of one finding per field.
    #
    # Guarded on status_by IS NULL, like the sweep itself: a finding a human
    # has already ruled on keeps their decision and their name on it.
    ("Retire adapter:vendor_field_drift findings", """
        UPDATE rh_findings
           SET status        = 'AUTO_RESOLVED',
               resolved_at   = now(),
               status_at     = now(),
               status_reason = 'detector replaced by adapter:bill_coding',
               updated_at    = now()
         WHERE detector    = 'adapter:vendor_field_drift'
           AND resolved_at IS NULL
           AND status_by   IS NULL;
    """),
    ("Create rh_finding_events finding index", """
        CREATE INDEX IF NOT EXISTS idx_rh_fev_finding
            ON rh_finding_events (finding_id, created_at DESC);
    """),
    ("Create rh_finding_events label index", """
        CREATE INDEX IF NOT EXISTS idx_rh_fev_label
            ON rh_finding_events (tenant_id, event_type, created_at DESC)
            WHERE event_type IN ('RESOLVED','DISMISSED');
    """),

    # ── 4. rh_finding_types — the emergent catalogue ──────────────────────────
    # A cluster IS a type. One table, not two. `centroid` lets a model-authored
    # claim ANN-match an existing type instead of minting a near-duplicate;
    # `verifier_spec` stores the tool plan that confirmed the type the first
    # time, so later subjects are re-checked by replaying it with no LLM call.
    ("Create rh_finding_types", """
        CREATE TABLE IF NOT EXISTS rh_finding_types (
            type_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id        uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            type_slug        text NOT NULL,
            display_name     text NOT NULL,
            description      text,
            family           text NOT NULL,
            origin           text NOT NULL CHECK (origin IN ('adapter','agent','promoted','manual')),
            default_severity severity_level NOT NULL DEFAULT 'MEDIUM',
            verifier_spec    jsonb NOT NULL DEFAULT '{}'::jsonb,
            promoted_detector text,
            status           text NOT NULL DEFAULT 'ACTIVE'
                             CHECK (status IN ('ACTIVE','SHADOW','SUPPRESSED','PROMOTED','RETIRED')),
            occurrences      integer NOT NULL DEFAULT 0,
            confirmed_count  integer NOT NULL DEFAULT 0,
            dismissed_count  integer NOT NULL DEFAULT 0,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, type_slug)
        );
    """),

    # pgvector is optional at this layer: without it the hub still runs
    # adapter-only, it just cannot canonicalise agent-authored types.
    ("Add rh_finding_types.centroid (needs pgvector)", """
        ALTER TABLE rh_finding_types ADD COLUMN IF NOT EXISTS centroid vector(1536);
    """),
    ("Create rh_finding_types centroid HNSW index", """
        CREATE INDEX IF NOT EXISTS idx_rh_types_centroid
            ON rh_finding_types USING hnsw (centroid vector_cosine_ops);
    """),

    ("Link rh_findings.type_id to rh_finding_types", """
        DO $$ BEGIN
            ALTER TABLE rh_findings
                ADD CONSTRAINT fk_rh_findings_type
                FOREIGN KEY (type_id) REFERENCES rh_finding_types(type_id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """),

    # ── 5. rh_suppressions — "don't tell me again" ────────────────────────────
    ("Create rh_suppressions", """
        CREATE TABLE IF NOT EXISTS rh_suppressions (
            suppression_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            scope          text NOT NULL CHECK (scope IN
                               ('exact_key','type_on_subject','type_tenant_wide')),
            finding_key    text,
            finding_type   text,
            subject_type   text,
            subject_id     uuid,
            until          timestamptz,
            reason         text NOT NULL,
            created_by     text NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        );
    """),
    ("Create rh_suppressions lookup index", """
        CREATE INDEX IF NOT EXISTS idx_rh_supp_lookup
            ON rh_suppressions (tenant_id, finding_type, subject_id);
    """),

    # ── 6. rh_review_requests — the agent's durable queue ─────────────────────
    # CDC triggers fire FOR EACH STATEMENT with an empty payload, so a NOTIFY
    # means "something in this table changed", never "review this row". The
    # agent therefore needs its own queue rather than reading the notification.
    ("Create rh_review_requests", """
        CREATE TABLE IF NOT EXISTS rh_review_requests (
            request_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            subject_type    text NOT NULL,
            subject_id      uuid,
            trigger_type    text NOT NULL CHECK (trigger_type IN ('user','event','schedule','sweep')),
            trigger_ref     text,
            tier            text NOT NULL DEFAULT 'standard'
                            CHECK (tier IN ('cheap','standard','deep')),
            idempotency_key text NOT NULL,
            status          text NOT NULL DEFAULT 'PENDING'
                            CHECK (status IN ('PENDING','RUNNING','DONE','SKIPPED','FAILED')),
            agent_run_id    uuid,
            error           text,
            requested_by    text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            started_at      timestamptz,
            finished_at     timestamptz
        );
    """),
    ("Create rh_review_requests idempotency index", """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rh_req_idem
            ON rh_review_requests (tenant_id, idempotency_key)
            WHERE status IN ('PENDING','RUNNING','DONE');
    """),
    ("Create rh_review_requests pending index", """
        CREATE INDEX IF NOT EXISTS idx_rh_req_pending
            ON rh_review_requests (tenant_id, created_at) WHERE status = 'PENDING';
    """),

    # ── 7. rh_finding_rejects — the hallucination ledger ──────────────────────
    # Everything the verifier REFUTED. Never shown to a user; this is the
    # per-model, per-prompt-version hallucination rate, which is the metric that
    # says whether open-ended detection is working at all.
    ("Create rh_finding_rejects", """
        CREATE TABLE IF NOT EXISTS rh_finding_rejects (
            reject_id     bigserial PRIMARY KEY,
            tenant_id     uuid NOT NULL,
            agent_run_id  uuid,
            subject_type  text,
            subject_id    uuid,
            proposed_type text,
            claim_jsonb   jsonb NOT NULL DEFAULT '{}'::jsonb,
            reject_reason text NOT NULL,
            model         text,
            prompt_version text,
            created_at    timestamptz NOT NULL DEFAULT now()
        );
    """),
    ("Create rh_finding_rejects tenant index", """
        CREATE INDEX IF NOT EXISTS idx_rh_rejects_tenant
            ON rh_finding_rejects (tenant_id, created_at DESC);
    """),

    # ── 8. rh_undo_tokens — server-backed undo for fast triage ────────────────
    ("Create rh_undo_tokens", """
        CREATE TABLE IF NOT EXISTS rh_undo_tokens (
            token        text PRIMARY KEY,
            tenant_id    uuid NOT NULL,
            finding_id   uuid NOT NULL REFERENCES rh_findings(finding_id) ON DELETE CASCADE,
            prev_state   jsonb NOT NULL,
            created_by   text NOT NULL,
            expires_at   timestamptz NOT NULL,
            used_at      timestamptz
        );
    """),
    ("Create rh_undo_tokens expiry index", """
        CREATE INDEX IF NOT EXISTS idx_rh_undo_expiry
            ON rh_undo_tokens (expires_at) WHERE used_at IS NULL;
    """),
    # Data: vendor_compliance used to read only supplier_bank_accounts, so a
    # vendor whose account sits in the older dim_suppliers fields (the vendor
    # card shows both) was flagged "no bank account" / "no verified bank account";
    # and a snoozed finding never auto-closed once an account was added. Close
    # every such finding whose account now exists (or is verified), snoozed
    # included. Idempotent; the fixed adapter will not raise
    # them again.
    ("Close false bank findings (account on vendor record)", """
        WITH false_hits AS (
            SELECT f.finding_id, f.tenant_id
              FROM rh_findings f
              JOIN rh_finding_entities e
                ON e.finding_id = f.finding_id AND e.entity_type = 'vendor'
              JOIN dim_suppliers s
                ON s.supplier_id = e.entity_id AND s.tenant_id = f.tenant_id
             WHERE f.resolved_at IS NULL
               AND (
                     -- "no bank account": there is one, in either place.
                     (f.finding_type = 'vendor_bank_missing'
                      AND (NULLIF(TRIM(s.bank_account), '') IS NOT NULL
                           OR EXISTS (SELECT 1 FROM supplier_bank_accounts b
                                       WHERE b.supplier_id = s.supplier_id
                                         AND b.tenant_id = s.tenant_id)))
                     -- "no verified bank account": one of them is verified.
                  OR (f.finding_type = 'vendor_bank_unverified'
                      AND f.detector = 'adapter:vendor_compliance'
                      AND ((NULLIF(TRIM(s.bank_account), '') IS NOT NULL
                            AND COALESCE(s.bank_verified, false))
                           OR EXISTS (SELECT 1 FROM supplier_bank_accounts b
                                       WHERE b.supplier_id = s.supplier_id
                                         AND b.tenant_id = s.tenant_id
                                         AND b.verified)))
               )
        ), closed AS (
            UPDATE rh_findings f
               SET status = 'AUTO_RESOLVED', resolved_at = now(), status_at = now(),
                   snoozed_until = NULL, updated_at = now(),
                   status_reason = 'condition no longer present'
              FROM false_hits h
             WHERE f.finding_id = h.finding_id
            RETURNING f.finding_id, f.tenant_id
        )
        INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
        SELECT finding_id, tenant_id, 'AUTO_RESOLVED', 'system:vendor_compliance',
               'bank account is on the vendor record', '{}'::jsonb
          FROM closed
    """),
    # Data: vendor_identity reported a vendor with no bank account at all as
    # "never verified" / "could not be verified" (the legacy bank_verified flag
    # defaults to false). Close those; vendor_compliance raises the real
    # "no bank account on file". Idempotent.
    ("Close bank-verify findings for vendors with no account", """
        WITH false_hits AS (
            SELECT f.finding_id, f.tenant_id
              FROM rh_findings f
              JOIN rh_finding_entities e
                ON e.finding_id = f.finding_id AND e.entity_type = 'vendor'
              JOIN dim_suppliers s
                ON s.supplier_id = e.entity_id AND s.tenant_id = f.tenant_id
             WHERE f.resolved_at IS NULL
               AND f.detector = 'adapter:vendor_identity'
               AND f.finding_type IN ('vendor_bank_unverified', 'vendor_bank_unverifiable')
               AND NULLIF(TRIM(s.bank_account), '') IS NULL
               AND NOT EXISTS (SELECT 1 FROM supplier_bank_accounts b
                                WHERE b.supplier_id = s.supplier_id AND b.tenant_id = s.tenant_id)
        ), closed AS (
            UPDATE rh_findings f
               SET status = 'AUTO_RESOLVED', resolved_at = now(), status_at = now(),
                   snoozed_until = NULL, updated_at = now(),
                   status_reason = 'condition no longer present'
              FROM false_hits h
             WHERE f.finding_id = h.finding_id
            RETURNING f.finding_id, f.tenant_id
        )
        INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
        SELECT finding_id, tenant_id, 'AUTO_RESOLVED', 'system:vendor_identity',
               'vendor has no bank account to verify', '{}'::jsonb
          FROM closed
    """),
    # Data: contract_bill_variance flagged a usage-billed line whose quantity
    # differed from the contract even when the rate was exactly the contracted
    # one. Only the rate is compared now; close the findings whose rates agree
    # (within the adapter's 2% tolerance). Idempotent.
    ("Close contract variance findings where the rate matches", """
        WITH same_rate AS (
            SELECT f.finding_id, f.tenant_id
              FROM rh_findings f
             WHERE f.resolved_at IS NULL
               AND f.finding_type = 'contract_bill_variance'
               AND f.evidence_jsonb -> 'rows' -> 0 ->> 0 = 'Rate'
               AND (f.evidence_jsonb -> 'rows' -> 0 ->> 1) ~ '^[0-9.]+$'
               AND (f.evidence_jsonb -> 'rows' -> 0 ->> 2) ~ '^[0-9.]+$'
               AND (f.evidence_jsonb -> 'rows' -> 0 ->> 1)::numeric > 0
               AND abs((f.evidence_jsonb -> 'rows' -> 0 ->> 2)::numeric
                       - (f.evidence_jsonb -> 'rows' -> 0 ->> 1)::numeric)
                   / (f.evidence_jsonb -> 'rows' -> 0 ->> 1)::numeric <= 0.02
        ), closed AS (
            UPDATE rh_findings f
               SET status = 'AUTO_RESOLVED', resolved_at = now(), status_at = now(),
                   snoozed_until = NULL, updated_at = now(),
                   status_reason = 'condition no longer present'
              FROM same_rate r
             WHERE f.finding_id = r.finding_id
            RETURNING f.finding_id, f.tenant_id
        )
        INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
        SELECT finding_id, tenant_id, 'AUTO_RESOLVED', 'system:contract_bill_variance',
               'billed at the contracted rate; quantity varies with usage', '{}'::jsonb
          FROM closed
    """),
    # Every change made from the Review Hub — who, when, which finding, and the
    # before → after of the vendor or bill (backend_ocr/app/review/audit.py).
    ("rh_audit_log", """
        CREATE TABLE IF NOT EXISTS rh_audit_log (
            audit_id      bigserial PRIMARY KEY,
            tenant_id     uuid,
            actor         text,
            actor_role    text,
            finding_id    uuid,
            surface       text,
            method        text NOT NULL,
            path          text NOT NULL,
            entity_type   text,
            entity_id     text,
            request_body  jsonb NOT NULL DEFAULT '{}'::jsonb,
            changes       jsonb NOT NULL DEFAULT '{}'::jsonb,
            status_code   int,
            request_id    text,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
    """),
    ("rh_audit_log indexes", """
        CREATE INDEX IF NOT EXISTS rh_audit_log_tenant_time ON rh_audit_log (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS rh_audit_log_finding ON rh_audit_log (finding_id, created_at DESC)
    """),
    # Data: close findings about orphan vendors — a retired (inactive) vendor
    # record, or a vendor with no bills for bank / GSTIN / PAN findings. Same
    # rule the queue applies (NOT_ORPHAN_VENDOR_SQL). Idempotent.
    ("Close findings on orphan vendors", """
        WITH orphan AS (
            SELECT f.finding_id, f.tenant_id
              FROM rh_findings f
              JOIN dim_suppliers ov
                ON ov.supplier_id = f.subject_id AND ov.tenant_id = f.tenant_id
             WHERE f.resolved_at IS NULL
               AND f.subject_type = 'vendor'
               AND (NOT ov.is_active
                    OR (f.family = 'vendor_identity'
                        AND NOT EXISTS (
                            SELECT 1 FROM fact_invoices ofi
                             WHERE ofi.supplier_id = ov.supplier_id
                               AND ofi.tenant_id   = ov.tenant_id
                               AND COALESCE(ofi.soft_status, 'active') = 'active')))
        ), closed AS (
            UPDATE rh_findings f
               SET status = 'AUTO_RESOLVED', resolved_at = now(), status_at = now(),
                   snoozed_until = NULL, updated_at = now(),
                   status_reason = 'condition no longer present'
              FROM orphan o
             WHERE f.finding_id = o.finding_id
            RETURNING f.finding_id, f.tenant_id
        )
        INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
        SELECT finding_id, tenant_id, 'AUTO_RESOLVED', 'system:review_hub',
               'vendor is retired or has no bills', '{}'::jsonb
          FROM closed
    """),
    # Data: close AI-anomaly findings about bills that no longer exist (deleted
    # or purged since). Same rule as NOT_DEAD_ANOMALY_BILL_SQL. Idempotent.
    ("Close anomaly findings on bills that no longer exist", """
        WITH dead AS (
            SELECT f.finding_id, f.tenant_id
              FROM rh_findings f
             WHERE f.resolved_at IS NULL
               AND f.detector = 'adapter:ai_anomalies'
               AND f.subject_type = 'bill'
               AND NOT EXISTS (SELECT 1 FROM fact_invoices fi
                                WHERE fi.invoice_id = f.subject_id AND fi.tenant_id = f.tenant_id)
               AND NOT EXISTS (SELECT 1 FROM stg_bills sb
                                WHERE sb.stg_bill_id = f.subject_id AND sb.tenant_id = f.tenant_id)
        ), closed AS (
            UPDATE rh_findings f
               SET status = 'AUTO_RESOLVED', resolved_at = now(), status_at = now(),
                   snoozed_until = NULL, updated_at = now(),
                   status_reason = 'condition no longer present'
              FROM dead d
             WHERE f.finding_id = d.finding_id
            RETURNING f.finding_id, f.tenant_id
        )
        INSERT INTO rh_finding_events (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
        SELECT finding_id, tenant_id, 'AUTO_RESOLVED', 'system:ai_anomalies',
               'the bill no longer exists', '{}'::jsonb
          FROM closed
    """),
    # Data, not schema: bring already-stored approval findings to the fixed P2 that
    # store.FAMILY_SEVERITY now writes. Idempotent; agent-raised findings may never
    # be re-asserted, so the next adapter pass alone would not reach them.
    ("Approval findings at P2", """
        UPDATE rh_findings
           SET severity = 'MEDIUM', updated_at = now()
         WHERE family = 'approval'
           AND resolved_at IS NULL
           AND severity <> 'MEDIUM'
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
    print("Review Hub migration\n")
    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor()

    passed = failed = 0
    soft_fail_labels = ("pgvector", "HNSW")
    for label, sql in DDL:
        try:
            cur.execute(sql.strip())
            conn.commit()
            print(f"  [OK] {label}")
            passed += 1
        except Exception as exc:
            conn.rollback()
            # A missing `vector` extension degrades the hub to adapter-only
            # detection, which is a working product — don't fail the migration.
            if any(tag in label for tag in soft_fail_labels):
                print(f"  [SKIP] {label}: {exc}")
            else:
                print(f"  [FAIL] {label}: {exc}")
                failed += 1

    try:
        cur.execute(
            "INSERT INTO schema_migrations (label) VALUES (%s) ON CONFLICT DO NOTHING",
            ("review_hub_v1",),
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
