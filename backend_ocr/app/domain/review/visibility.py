"""Which findings the current identity may see, as one SQL fragment.

Used by every endpoint in routers/review_hub.py. Having exactly one predicate is
the point: a second, slightly different WHERE clause somewhere is how a list and
its badge count start disagreeing, and a hub whose count doesn't match its
contents is a hub nobody believes.
"""
from __future__ import annotations

from ...security.auth import current_role, current_username
from ...security.capabilities import GLOBAL_VIEW_ROLES


def visibility_sql(alias: str = "f") -> tuple[str, dict]:
    """Returns (sql_fragment, params) scoping findings to the current identity.

    For admin / finance_controller / auditor this is constant-true, so the
    planner uses the stream index alone. For the narrower roles it is a GIN
    bitmap-or over the audience arrays, which is a small result set and cheap.
    """
    role = current_role() or ""
    username = current_username() or ""

    if role in GLOBAL_VIEW_ROLES:
        return "TRUE", {}

    frag = (
        f"( %(vis_role)s = ANY({alias}.audience_roles)"
        f"  OR %(vis_user)s = ANY({alias}.audience_users) )"
    )
    return frag, {"vis_role": role, "vis_user": username}


def actionable_sql(alias: str = "f", capabilities: list[str] | None = None) -> tuple[str, dict]:
    """Findings the current user could actually resolve, not merely read.

    This is the default lane. "Everything I can see" stays one click away, and
    the header always states the difference in words, so nothing is hidden —
    it is just not first.
    """
    from ...security.capabilities import capabilities_for

    caps = capabilities if capabilities is not None else capabilities_for()
    if not caps:
        return "FALSE", {}
    return f"{alias}.required_capability = ANY(%(act_caps)s)", {"act_caps": caps}
