"""
Agent runtime — DB migration. Implements docs/PRD_AGENT_HUB.md §11.

This is Phase 0 of the Agent Hub: the audit and cost substrate every agent
writes to. It ships with the Review Agent because that is the first agent to
need it, but nothing here is Review-Hub-specific — `agent_runs` is where any
future agent records what it did, what it cost, and why.

Worth stating plainly: before this, the product ran gpt-4o over every OCR page
and gpt-4o-mini at eight other call sites with **zero** token accounting. Nobody
could answer "what did this tenant's AI cost last month". These tables are the
answer, independent of whether the Review Agent ever ships.

Reads DB_URL from config.py (.env). Safe to re-run.
"""
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


DDL: list[tuple[str, str]] = [
    ("Create agent_registry", """
        CREATE TABLE IF NOT EXISTS agent_registry (
            agent_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name          text NOT NULL UNIQUE,
            display_name  text NOT NULL,
            description   text,
            -- L0 suggest / L1 draft / L2 act behind a gate / L3 autonomous.
            -- The Review Agent ships at L0 and the column exists so raising it
            -- is a reviewed data change rather than a code change.
            autonomy      smallint NOT NULL DEFAULT 0 CHECK (autonomy BETWEEN 0 AND 3),
            enabled       boolean NOT NULL DEFAULT true,
            config        jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
    """),

    ("Create agent_runs", """
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id      uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            agent_name     text NOT NULL,
            parent_run_id  uuid REFERENCES agent_runs(run_id) ON DELETE SET NULL,

            -- WHY / WHAT / WHOM / HOW, per PRD §12.
            trigger_type   text NOT NULL,
            trigger_ref    text,
            subject_type   text,
            subject_id     uuid,
            requested_by   text,

            status         text NOT NULL DEFAULT 'RUNNING'
                           CHECK (status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED','REFUSED')),
            outcome        jsonb NOT NULL DEFAULT '{}'::jsonb,
            steps          jsonb NOT NULL DEFAULT '[]'::jsonb,
            tools_used     text[] NOT NULL DEFAULT '{}',
            error          text,

            model          text,
            prompt_version text,
            input_tokens   integer NOT NULL DEFAULT 0,
            output_tokens  integer NOT NULL DEFAULT 0,
            cost_usd       numeric(12,6) NOT NULL DEFAULT 0,
            latency_ms     integer,

            started_at     timestamptz NOT NULL DEFAULT now(),
            finished_at    timestamptz
        );
    """),
    ("Create agent_runs tenant index", """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant
            ON agent_runs (tenant_id, started_at DESC);
    """),
    ("Create agent_runs subject index", """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_subject
            ON agent_runs (tenant_id, subject_type, subject_id, started_at DESC);
    """),
    ("Create agent_runs cost index", """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_cost
            ON agent_runs (tenant_id, started_at) WHERE cost_usd > 0;
    """),

    # Spend is tracked per tenant per UTC day. The cap is checked before a call,
    # not after, so a runaway loop stops costing money rather than being noticed
    # in next month's invoice.
    ("Create agent_budgets", """
        CREATE TABLE IF NOT EXISTS agent_budgets (
            tenant_id      uuid NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            day            date NOT NULL,
            spent_usd      numeric(12,6) NOT NULL DEFAULT 0,
            calls          integer NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, day)
        );
    """),
    ("Add dim_tenants agent cap column", """
        ALTER TABLE dim_tenants
            ADD COLUMN IF NOT EXISTS agent_daily_cap_usd numeric(10,4);
    """),

    ("Seed the review agent", """
        INSERT INTO agent_registry (name, display_name, description, autonomy, enabled, config)
        VALUES (
            'review',
            'Review Agent',
            'Reviews an entity from several professional perspectives and proposes '
            'findings. Every numeric claim is recomputed in SQL before publication; '
            'anything it cannot verify is published as a suspicion, not a fact.',
            0, true,
            '{"tier_models": {"cheap": "gpt-4o-mini", "standard": "gpt-4o-mini", "deep": "gpt-4o"},
              "exploration_pct": 3, "materiality_floor_inr": 25000}'::jsonb
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
    print("Agent runtime migration\n")
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
            ("agent_runtime_v1",),
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
