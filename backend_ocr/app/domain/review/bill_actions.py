"""Acting on a bill from the hub, without the hub deciding who may.

The rule this module lives under is in routers/review_hub.py's header: the hub
never carries a second copy of an operation's logic. So approving here is not a
new approval path — it builds the same request POST /api/bills/{id}/action
already serves and hands it to that handler, which owns the chain rules, the
role gate, the assignee gate, the status transition, the audit row and the
notification email.

What this module adds is the question *should the button be there at all*, and
it answers it with the same helpers the handler enforces with. A button that
appears and then 403s teaches the user the hub is wrong about their permissions,
which is worse than no button: they stop believing the rest of it too.

Identity is taken from the token, never from the request body. The bills
endpoint accepts actor_role/actor_name from its caller — the browser sends the
logged-in user's — so passing them from here rather than from the client is the
one place this path is stricter than the one it delegates to.
"""
from __future__ import annotations

from ...core.approval_chain import (
    _build_status_name_map, _build_status_role_map, _chain_statuses, _load_chain,
)
from ...security.auth import current_role, current_username


def _humanise(v: str | None) -> str:
    return (v or "").replace("_", " ").strip().title()


def actor_display_name(cur, tenant: str, username: str) -> str:
    """The display name the chain's assignee list is written against.

    Assignees are configured by name, not username, so an approval sent with a
    username would be refused for anyone on a name-gated step.
    """
    cur.execute(
        "SELECT name FROM users WHERE username = %s AND company_id::text = %s",
        (username, str(tenant)),
    )
    row = cur.fetchone()
    if not row:
        return username
    return (row["name"] if isinstance(row, dict) else row[0]) or username


def can_approve(cur, tenant: str, bill) -> bool:
    """Whether the caller may approve this bill *right now*.

    Mirrors the three gates in routers/bill_approvals.bill_action: the bill is at
    a pending step of its chain, the caller's role is allowed at that step, and —
    if the step names people — the caller is one of them. Admins bypass the name
    gate there, so they bypass it here.
    """
    status = (bill or {}).get("status") or ""
    if not status.upper().startswith("PENDING"):
        return False

    department = bill.get("department")
    chain = _load_chain(str(tenant), department)
    if status not in _chain_statuses(chain):
        return False

    role = current_role() or ""
    if role not in _build_status_role_map(str(tenant), department).get(status, set()):
        return False

    names = _build_status_name_map(str(tenant), department).get(status)
    if names is not None and role != "admin":
        return (actor_display_name(cur, tenant, current_username() or "") or "").strip() in names
    return True


def approve_action(cur, tenant: str, bill_id: str, bill) -> dict | None:
    """The Approve entry for a finding's action list, or None if they may not.

    Carries a confirmation rather than approving on one click. The original
    objection to acting from a triage queue was that it is a thinner path than
    the screen that owns the decision; naming the vendor, the bill and the amount
    before it commits is what keeps it from being thinner.
    """
    if not can_approve(cur, tenant, bill):
        return None

    currency = (bill.get("currency_code") or "INR").strip().upper()
    total = bill.get("total_amount")
    amount = f"{currency} {total:,.2f}" if total is not None else "—"

    return {
        "id": "approve_bill",
        "label": "Approve",
        # Not 'safe': safe means reversible and unremarkable, and an approval is
        # neither. The drawer renders this kind with a confirmation it cannot
        # skip.
        "kind": "approve",
        "endpoint": "/api/review/act/approve-bill",
        "method": "POST",
        "fields": [],
        "preview": f"Approves {bill.get('invoice_number') or 'this bill'} and moves it to the next step.",
        "capability": "approve",
        "destructive": False,
        "href": None,
        "body": {},
        "confirm": {
            "title": f"Approve {bill.get('invoice_number') or 'this bill'}?",
            "impact": [
                {"label": "Vendor", "value": bill.get("supplier_name") or "—"},
                {"label": "Amount", "value": amount},
                {"label": "Currently at", "value": _humanise(bill.get("status"))},
            ],
            "items": [
                "Recorded against your name in the bill's approval history.",
                "Moves the bill to the next step in the chain.",
            ],
        },
    }
