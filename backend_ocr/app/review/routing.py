"""Who should see a finding.

Deliberately narrow, because the identity primitives that actually exist are
narrow. The JWT mints sub/role/name/email/company_id/company_name/department and
nothing else (backend_ocr/app/core/tokens.py:_build_access_token), so
`has_permission()` can never return true and `org_user_access_levels` has no
writer and no join to users.username. Routing therefore keys on role, username
and department — real, coarse, and honest about it.

The approval-step half MUST agree with what the API actually authorises, or the
hub tells someone to approve a bill it will then 403 them for. The API computes
that in backend_ocr/app/core/approval_chain.py using psycopg2; this is the
asyncpg twin, and tests/test_review_routing.py asserts the two produce identical
maps for the same tenant_chain_config fixture.
"""
from __future__ import annotations

import asyncpg

from .types import jsonb

# Mirrors approval_chain._STEP_STATUS_NAMES. Guarded by the consistency test.
_STEP_STATUS_NAMES = ["PENDING_APPROVER", "PENDING_ACCOUNTANT", "PENDING_FC"]

DEFAULT_CHAIN: tuple[dict, ...] = (
    {"step_order": 0, "label": "Submitted",          "role": "member",             "locked": True},
    {"step_order": 1, "label": "Approver",           "role": "approver",           "locked": False},
    {"step_order": 2, "label": "Accountant",         "role": "accountant",         "locked": False},
    {"step_order": 3, "label": "Finance Controller", "role": "finance_controller", "locked": False},
)

# Roles that can see everything in the tenant. These are the hub's primary
# personas; for them the visibility predicate collapses to constant-true and the
# stream index does all the work.
GLOBAL_VIEW_ROLES: frozenset[str] = frozenset({"admin", "finance_controller", "auditor"})


def step_idx_to_status(idx: int) -> str:
    return _STEP_STATUS_NAMES[idx] if idx < len(_STEP_STATUS_NAMES) else f"PENDING_STEP_{idx + 1}"


def status_role_map(chain: list[dict]) -> dict[str, set[str]]:
    """{pending_status -> allowed roles}. Index-based, skipping locked steps."""
    result: dict[str, set[str]] = {}
    idx = 0
    for step in chain:
        if step.get("locked"):
            continue
        result[step_idx_to_status(idx)] = {step["role"], "admin"}
        idx += 1
    return result


def status_name_map(chain: list[dict]) -> dict[str, list[str] | None]:
    """{pending_status -> named assignees or None}. None means "any holder of the role"."""
    result: dict[str, list[str] | None] = {}
    idx = 0
    for step in chain:
        if step.get("locked"):
            continue
        assignees = step.get("assignees")
        result[step_idx_to_status(idx)] = (
            [n.strip() for n in assignees if n and n.strip()] if assignees else None
        )
        idx += 1
    return result


def _decode(row) -> dict:
    """Chain rows carry `assignees` as jsonb. Decode once, here, so
    status_name_map and status_role_map stay pure and match what
    backend_ocr/app/core/approval_chain.py (psycopg2, which decodes for us)
    computes from the same config."""
    d = dict(row)
    d["assignees"] = jsonb(d.get("assignees"), default=None)
    return d


async def load_chain(
    conn: asyncpg.Connection, tenant_id: str, department: str | None = None
) -> list[dict]:
    if department:
        rows = await conn.fetch(
            "SELECT step_order, label, role, locked, assignees FROM tenant_chain_config "
            "WHERE tenant_id = $1::uuid AND department = $2 AND is_snapshot = false "
            "ORDER BY step_order",
            tenant_id, department,
        )
        if rows:
            return [_decode(r) for r in rows]
    rows = await conn.fetch(
        "SELECT step_order, label, role, locked, assignees FROM tenant_chain_config "
        "WHERE tenant_id = $1::uuid AND department IS NULL AND is_snapshot = false "
        "ORDER BY step_order",
        tenant_id,
    )
    return [_decode(r) for r in rows] if rows else [dict(s) for s in DEFAULT_CHAIN]


class Audience:
    __slots__ = ("roles", "users", "departments", "capability")

    def __init__(self, roles=(), users=(), departments=(), capability="view"):
        self.roles = tuple(sorted(set(roles)))
        self.users = tuple(sorted(set(u for u in users if u)))
        self.departments = tuple(sorted(set(d for d in departments if d)))
        self.capability = capability


async def audience_for_bill_status(
    conn: asyncpg.Connection,
    tenant_id: str,
    status: str,
    department: str | None = None,
    capability: str = "approve",
) -> Audience:
    """Who may act on a bill sitting at `status`, straight from the chain config."""
    chain = await load_chain(conn, tenant_id, department)
    roles = status_role_map(chain).get(status) or {"admin", "finance_controller"}
    names = status_name_map(chain).get(status) or ()
    return Audience(
        roles=roles,
        users=names,
        departments=[department] if department else [],
        capability=capability,
    )


def vendor_audience(capability: str = "verify") -> Audience:
    """Vendor master problems. There is no "vendor owner" concept in the schema —
    do not invent one; fall back to the roles that can actually fix it."""
    return Audience(
        roles=("admin", "finance_controller", "accountant"), capability=capability
    )


def payment_audience(capability: str = "pay") -> Audience:
    return Audience(roles=("admin", "finance_controller"), capability=capability)


def data_audience(capability: str = "edit") -> Audience:
    return Audience(
        roles=("admin", "finance_controller", "accountant"), capability=capability
    )


async def default_notify_target(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    audience_roles: tuple[str, ...] = (),
    bill_status: str | None = None,
    department: str | None = None,
) -> dict:
    """Who a `notify` action's confirm dialog should default to.

    A *suggestion* only (§5.3.2) — the human still confirms the destination
    before anything sends, so getting this wrong costs a re-pick in the dialog,
    not a misdirected message. Tries, in order:

    1. The named assignee on the approval step this finding is blocking, if it
       is blocking one (`bill_status` non-None and the chain names someone at
       that status) — reuses `status_name_map`, already computed for bill
       findings, rather than inventing a second "assignee" concept.
    2. Else the first role in `audience_roles` that has an active user in this
       tenant — "active" meaning nothing more than "has a row in `users`":
       the schema carries no enabled/disabled flag, so a user existing at all
       is the only signal there is.
    3. Else `finance_controller`, the same tenant-wide fallback
       `vendor_audience()` already uses when there is no better answer.
    """
    if bill_status:
        chain = await load_chain(conn, tenant_id, department)
        names = status_name_map(chain).get(bill_status)
        if names:
            return {"kind": "user", "value": names[0], "label": names[0]}

    for role in audience_roles:
        row = await conn.fetchrow(
            "SELECT username, name FROM users WHERE company_id = $1::uuid AND role = $2 "
            "ORDER BY username LIMIT 1",
            tenant_id, role,
        )
        if row:
            return {"kind": "user", "value": row["username"],
                    "label": row["name"] or row["username"]}

    return {"kind": "role", "value": "finance_controller", "label": "Finance Controller"}
