"""What a role is allowed to *do*, in one place.

Why this exists rather than reusing `auth.has_permission()`: that function reads
a `permissions`/`scopes` claim, and `core/tokens.py:_build_access_token` mints
sub/role/name/email/company_id/company_name/department and nothing else. So
`has_permission()` returns False for every user, for every permission, always.
Anything routed through it would silently route to nobody. `org_user_access_levels`
is no better — nothing writes it, and `org_users.user_id` (uuid) has no join to
`users.username`, which is what the JWT actually carries.

So capabilities are derived from `users.role`, which is real and is what the rest
of the app already enforces on. This module is deliberately the single seam: when
per-user access levels are eventually wired up, `can()` is the one function that
changes and nothing in the Review Hub moves.
"""
from __future__ import annotations

from fastapi import HTTPException

from .auth import current_role

# A capability is a verb a finding can require. Kept small on purpose — six
# verbs a finance team would recognise, not a permission matrix nobody maintains.
CAPABILITY_ROLES: dict[str, frozenset[str]] = {
    "view":    frozenset({"admin", "finance_controller", "accountant",
                          "approver", "member", "auditor"}),
    "edit":    frozenset({"admin", "finance_controller", "accountant"}),
    "approve": frozenset({"admin", "finance_controller", "approver"}),
    "verify":  frozenset({"admin", "finance_controller", "accountant"}),
    "pay":     frozenset({"admin", "finance_controller"}),
    "admin":   frozenset({"admin"}),
}

CAPABILITIES: tuple[str, ...] = tuple(CAPABILITY_ROLES)

# Roles that see every finding in the tenant. These are the hub's primary
# personas, and for them the visibility predicate collapses to constant-true.
# `auditor` is included deliberately: they may see and acknowledge everything and
# resolve nothing, which is what an auditor should be able to do.
GLOBAL_VIEW_ROLES: frozenset[str] = frozenset({"admin", "finance_controller", "auditor"})

# An auditor's read-everything access must not become write access.
READ_ONLY_ROLES: frozenset[str] = frozenset({"auditor"})


def can(capability: str, role: str | None = None) -> bool:
    role = role or current_role()
    if not role:
        return False
    if capability != "view" and role in READ_ONLY_ROLES:
        return False
    return role in CAPABILITY_ROLES.get(capability, frozenset())


def capabilities_for(role: str | None = None) -> list[str]:
    role = role or current_role()
    return [c for c in CAPABILITIES if can(c, role)]


def require_capability(capability: str) -> None:
    """Mirrors auth.require_role: raises 403 rather than returning a bool."""
    if not can(capability):
        raise HTTPException(
            status_code=403,
            detail=f"This action needs the '{capability}' capability.",
        )
