"""What a brand-new tenant needs before anyone can log in and use it.

Everything here takes an explicit ``cur`` and ``tenant_id`` so it can run inside
the onboarding transaction *and* be reused by the existing tenant-scoped endpoints
in main.py. That is the point: before this module, provisioning lived in three
places — a side effect of the first invoice ingest, a startup migration, and a
seed script — none of which could be driven from an admin panel.

No module here may import from ``main``; ``main`` imports these.
"""
from __future__ import annotations

import bcrypt

# The chain every tenant starts with. Previously a literal inside main._load_chain,
# which meant a new tenant's GET /api/settings/chain/default returned nothing until
# someone saved a chain by hand.
DEFAULT_APPROVAL_CHAIN: tuple[dict, ...] = (
    {"step_order": 0, "label": "Submitted",          "role": "member",             "locked": True},
    {"step_order": 1, "label": "Approver",           "role": "approver",           "locked": False},
    {"step_order": 2, "label": "Accountant",         "role": "accountant",         "locked": False},
    {"step_order": 3, "label": "Finance Controller", "role": "finance_controller", "locked": False},
)


def ensure_tenant(cur, tenant_id: str, company_name: str, gstn: str | None,
                  timezone: str = "Asia/Kolkata", region: str = "ap-south-1",
                  legal_name: str | None = None,
                  primary_contact_email: str | None = None,
                  package_key: str | None = None) -> None:
    """Create the dim_tenants row.

    The GSTN guard is carried over from ingest._ensure_tenant: dim_tenants has a
    UNIQUE index on gstn, so inserting a duplicate would abort the whole onboarding
    transaction. Storing NULL instead lets provisioning succeed and leaves the
    conflict visible to ops.
    """
    cur.execute(
        """
        INSERT INTO dim_tenants (
            tenant_id, company_name, gstn, plan_tier, timezone, region, is_active,
            legal_name, primary_contact_email, package_key, onboarded_at, pipeline_enabled
        )
        VALUES (
            %s, %s,
            CASE WHEN %s IS NULL THEN NULL
                 WHEN EXISTS (SELECT 1 FROM dim_tenants d WHERE d.gstn = %s) THEN NULL
                 ELSE %s::char(15) END,
            COALESCE(%s, 'starter'), %s, %s, true,
            %s, %s, %s, now(), true
        )
        ON CONFLICT (tenant_id) DO NOTHING
        """,
        (tenant_id, company_name, gstn, gstn, gstn,
         package_key, timezone, region,
         legal_name or company_name, primary_contact_email, package_key),
    )


def seed_default_brand(cur, tenant_id: str, brand_name: str) -> None:
    """Create the tenant's default brand, named after the tenant.

    The startup migration used to seed the literal 'Brightloans Private Limited'
    as the default brand for every tenant in the database — one customer's legal
    name on everybody's invoices.
    """
    cur.execute(
        """
        INSERT INTO brands (tenant_id, name, is_default)
        SELECT %s, %s, TRUE
        WHERE NOT EXISTS (SELECT 1 FROM brands b WHERE b.tenant_id = %s)
        """,
        (tenant_id, brand_name, tenant_id),
    )


def seed_default_chain(cur, tenant_id: str) -> None:
    """Insert the default approval chain as both the working copy and the snapshot.

    The snapshot is what "Reset to Default" restores, so seeding both means the
    button works on day one instead of resetting to an empty chain.
    """
    cur.execute(
        "SELECT 1 FROM tenant_chain_config WHERE tenant_id = %s LIMIT 1", (tenant_id,)
    )
    if cur.fetchone():
        return
    for is_snapshot in (False, True):
        for step in DEFAULT_APPROVAL_CHAIN:
            cur.execute(
                """
                INSERT INTO tenant_chain_config
                    (tenant_id, department, step_order, label, role, locked, is_snapshot)
                VALUES (%s, NULL, %s, %s, %s, %s, %s)
                """,
                (tenant_id, step["step_order"], step["label"],
                 step["role"], step["locked"], is_snapshot),
            )


def username_taken(cur, username: str) -> bool:
    """users.username is globally unique, so a name can collide across tenants.

    Onboarding checks this up front and reports it, rather than letting the INSERT
    abort the whole provisioning transaction with a constraint error.
    """
    cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
    return cur.fetchone() is not None


def create_tenant_user(cur, username: str, password: str, role: str, name: str,
                       email: str, tenant_id: str, department: str | None = None) -> None:
    """Create a login user for a tenant.

    Deliberately a plain INSERT, not an upsert: onboarding must fail loudly on a
    username collision rather than quietly move an existing user — possibly another
    customer's admin — into the new tenant.
    """
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cur.execute(
        """INSERT INTO users (username, password_hash, role, name, email, company_id, department)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (username, hashed, role, name, email, tenant_id, department),
    )


def apply_package(cur, tenant_id: str, package_key: str) -> int:
    """Copy a package's services onto the tenant.

    A copy, not a link: nothing resolves through a package at request time, so
    editing a package later never silently changes a live tenant's entitlements.
    Returns the number of services written.
    """
    cur.execute(
        """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, limits, enabled_at, notes)
        SELECT %s, i.service_key, i.enabled, i.limits,
               CASE WHEN i.enabled THEN now() END,
               'onboarding: package ' || p.key
          FROM service_package p
          JOIN service_package_item i ON i.package_id = p.package_id
         WHERE p.key = %s
        ON CONFLICT (tenant_id, service_key) DO UPDATE
           SET enabled = EXCLUDED.enabled,
               limits  = EXCLUDED.limits,
               updated_at = now()
        """,
        (tenant_id, package_key),
    )
    return cur.rowcount


def seed_core_services(cur, tenant_id: str) -> None:
    """Fallback when no package is chosen: grant exactly the core services."""
    cur.execute(
        """
        INSERT INTO tenant_service (tenant_id, service_key, enabled, enabled_at, notes)
        SELECT %s, c.service_key, true, now(), 'onboarding: core default'
          FROM service_catalog c
         WHERE c.is_core AND c.retired_at IS NULL
        ON CONFLICT (tenant_id, service_key) DO NOTHING
        """,
        (tenant_id,),
    )
