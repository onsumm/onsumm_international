"""DDL for the licensing + platform-admin tables.

Deliberately one list, imported by two callers so they can never drift:

  * ``main.py``'s startup hook, following the ~20 existing
    ``CREATE TABLE IF NOT EXISTS`` handlers — this guarantees the app never boots
    against a database missing a table it is about to query.
  * ``scripts/licensing_migrate.py``, which can point at ``DB_URL`` *or*
    ``DB_PROD_URL``. Startup DDL only ever reaches the one database the running
    process is configured for, so prod needs the script.

Every statement is idempotent and safe to re-run.
"""
from __future__ import annotations

# (label, sql) — the shape scripts/sap_catalog_migrate.py already uses.
DDL: tuple[tuple[str, str], ...] = (
    ("service_catalog", """
        CREATE TABLE IF NOT EXISTS service_catalog (
            service_key     text        PRIMARY KEY,
            display_name    text        NOT NULL,
            description     text        NOT NULL DEFAULT '',
            category        text        NOT NULL,
            is_core         boolean     NOT NULL DEFAULT false,
            default_enabled boolean     NOT NULL DEFAULT false,
            metered_unit    text,
            sort_order      integer     NOT NULL DEFAULT 0,
            retired_at      timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
    """),

    # The source of truth. One row per (tenant, service); absence means "inherit
    # service_catalog.default_enabled", so adding a core service lights it up for
    # every existing tenant without a backfill write.
    ("tenant_service", """
        CREATE TABLE IF NOT EXISTS tenant_service (
            tenant_id   uuid        NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            service_key text        NOT NULL REFERENCES service_catalog(service_key),
            enabled     boolean     NOT NULL DEFAULT false,
            limits      jsonb       NOT NULL DEFAULT '{}'::jsonb,
            config      jsonb       NOT NULL DEFAULT '{}'::jsonb,
            enabled_at  timestamptz,
            disabled_at timestamptz,
            updated_by  uuid,
            notes       text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, service_key)
        )
    """),
    ("tenant_service indexes", """
        CREATE INDEX IF NOT EXISTS idx_tenant_service_enabled
            ON tenant_service (tenant_id) WHERE enabled;
        CREATE INDEX IF NOT EXISTS idx_tenant_service_key
            ON tenant_service (service_key) WHERE enabled;
    """),

    # Onboarding convenience only. Applying a package COPIES rows into
    # tenant_service; nothing resolves through a package at request time, so
    # editing one never silently changes a live tenant.
    ("service_package", """
        CREATE TABLE IF NOT EXISTS service_package (
            package_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            key         text        NOT NULL UNIQUE,
            name        text        NOT NULL,
            description text        NOT NULL DEFAULT '',
            is_active   boolean     NOT NULL DEFAULT true,
            sort_order  integer     NOT NULL DEFAULT 0,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
    """),
    ("service_package_item", """
        CREATE TABLE IF NOT EXISTS service_package_item (
            package_id  uuid    NOT NULL REFERENCES service_package(package_id) ON DELETE CASCADE,
            service_key text    NOT NULL REFERENCES service_catalog(service_key),
            enabled     boolean NOT NULL DEFAULT true,
            limits      jsonb   NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (package_id, service_key)
        )
    """),

    # ── Platform admin identity — strictly separate from `users` ──────────────
    ("platform_users", """
        CREATE TABLE IF NOT EXISTS platform_users (
            id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            email         text        NOT NULL,
            password_hash text        NOT NULL,
            name          text        NOT NULL,
            role          text        NOT NULL DEFAULT 'support',
            is_active     boolean     NOT NULL DEFAULT true,
            last_login_at timestamptz,
            failed_logins integer     NOT NULL DEFAULT 0,
            locked_until  timestamptz,
            created_by    uuid,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT platform_users_role_chk
                CHECK (role IN ('owner','admin','support','readonly'))
        )
    """),
    ("platform_users email index", """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_users_email
            ON platform_users (lower(email))
    """),

    # Mirrors auth_refresh_tokens so rotation + reuse-detection are identical.
    ("platform_refresh_tokens", """
        CREATE TABLE IF NOT EXISTS platform_refresh_tokens (
            id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash       text        UNIQUE NOT NULL,
            platform_user_id uuid        NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
            issued_at        timestamptz NOT NULL DEFAULT now(),
            expires_at       timestamptz NOT NULL,
            revoked          boolean     NOT NULL DEFAULT false,
            revoked_at       timestamptz,
            replaced_by      text
        )
    """),
    ("platform_refresh index", """
        CREATE INDEX IF NOT EXISTS idx_platform_refresh_user
            ON platform_refresh_tokens (platform_user_id) WHERE NOT revoked
    """),

    ("platform_impersonation", """
        CREATE TABLE IF NOT EXISTS platform_impersonation (
            session_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
            platform_user_id uuid        NOT NULL REFERENCES platform_users(id),
            tenant_id        uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
            assumed_role     text        NOT NULL DEFAULT 'auditor',
            reason           text        NOT NULL,
            read_only        boolean     NOT NULL DEFAULT true,
            issued_at        timestamptz NOT NULL DEFAULT now(),
            expires_at       timestamptz NOT NULL,
            revoked_at       timestamptz
        )
    """),
    ("platform_impersonation indexes", """
        CREATE INDEX IF NOT EXISTS idx_impersonation_live
            ON platform_impersonation (expires_at) WHERE revoked_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_impersonation_tenant
            ON platform_impersonation (tenant_id, issued_at DESC);
    """),

    # ── Usage metering ────────────────────────────────────────────────────────
    ("tenant_usage", """
        CREATE TABLE IF NOT EXISTS tenant_usage (
            tenant_id     uuid        NOT NULL REFERENCES dim_tenants(tenant_id) ON DELETE CASCADE,
            service_key   text        NOT NULL REFERENCES service_catalog(service_key),
            metric        text        NOT NULL,
            period_month  date        NOT NULL,
            used          numeric     NOT NULL DEFAULT 0,
            last_event_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, service_key, metric, period_month)
        )
    """),
    ("tenant_usage index", """
        CREATE INDEX IF NOT EXISTS idx_tenant_usage_period
            ON tenant_usage (period_month DESC, tenant_id)
    """),

    # ── Admin audit log ───────────────────────────────────────────────────────
    ("admin_audit_log", """
        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id                     bigserial   PRIMARY KEY,
            actor_platform_user_id uuid,
            actor_email            text        NOT NULL DEFAULT 'system',
            action                 text        NOT NULL,
            target_type            text        NOT NULL,
            target_id              text,
            tenant_id              uuid,
            before                 jsonb,
            after                  jsonb,
            impersonation_id       uuid,
            ip                     text,
            user_agent             text,
            created_at             timestamptz NOT NULL DEFAULT now()
        )
    """),
    ("admin_audit_log indexes", """
        CREATE INDEX IF NOT EXISTS idx_audit_time   ON admin_audit_log (created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_tenant ON admin_audit_log (tenant_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_actor  ON admin_audit_log (actor_platform_user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_action ON admin_audit_log (action, created_at DESC);
    """),

    # ── dim_tenants additions ─────────────────────────────────────────────────
    # dim_tenants itself has no CREATE TABLE anywhere in the repo — it exists only
    # in the live databases. These are additive only.
    ("dim_tenants columns", """
        ALTER TABLE dim_tenants ADD COLUMN IF NOT EXISTS pipeline_enabled      boolean NOT NULL DEFAULT true;
        ALTER TABLE dim_tenants ADD COLUMN IF NOT EXISTS onboarded_at          timestamptz;
        ALTER TABLE dim_tenants ADD COLUMN IF NOT EXISTS package_key           text;
        ALTER TABLE dim_tenants ADD COLUMN IF NOT EXISTS legal_name            text;
        ALTER TABLE dim_tenants ADD COLUMN IF NOT EXISTS primary_contact_email text;
    """),

    # Ledger of what has run, so the one-shot backfill can never run twice.
    ("schema_migrations", """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            label      text        PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
        )
    """),
)


def sync_catalog_sql() -> tuple[str, str]:
    """SQL to upsert `services.SERVICES` into `service_catalog`, and to retire
    catalog rows the registry no longer declares.

    Runs at startup so adding a key in code lights it up in whichever database the
    process points at — without that, the FK on `tenant_service.service_key` would
    reject the admin's first toggle on a new service.
    """
    upsert = """
        INSERT INTO service_catalog
            (service_key, display_name, description, category,
             is_core, default_enabled, metered_unit, sort_order, retired_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, now())
        ON CONFLICT (service_key) DO UPDATE SET
            display_name    = EXCLUDED.display_name,
            description     = EXCLUDED.description,
            category        = EXCLUDED.category,
            is_core         = EXCLUDED.is_core,
            default_enabled = EXCLUDED.default_enabled,
            metered_unit    = EXCLUDED.metered_unit,
            sort_order      = EXCLUDED.sort_order,
            retired_at      = NULL,
            updated_at      = now()
    """
    # Retire, never delete — tenant_service rows must survive for audit.
    retire = """
        UPDATE service_catalog
           SET retired_at = now(), updated_at = now()
         WHERE retired_at IS NULL
           AND service_key <> ALL(%s)
    """
    return upsert, retire
