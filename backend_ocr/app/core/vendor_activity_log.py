"""Write a row to a vendor's Activity log (vendor_activity_log).

For the vendor actions that left no audit trail of their own: master-data
edits, bank accounts, contacts, GSTINs, document re-filing. Called inside the
action's own transaction, so the row commits (or rolls back) with the change.

Logging never breaks the action: the insert runs in a savepoint, and any
failure (say the table is not migrated yet) is rolled back to it, logged as a
warning, and swallowed.

`title` and `changes` are stored display-ready — the timeline shows them as-is.
Never put a full bank account number in either; use mask_account().
"""
from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import Json

from ..domain.vendor_activity import diff_fields, display_value, mask_account, mask_numbers  # noqa: F401  (re-exported)
from ..security.auth import current_role, current_tenant, current_username

log = logging.getLogger(__name__)

_SAVEPOINT = "vendor_activity_log_sp"


def log_vendor_activity(cur, supplier_id: Any, action: str, title: str,
                        changes: list | None = None, meta: dict | None = None,
                        tenant_id: Any = None) -> bool:
    """Record one Activity event for a vendor. Returns True when written.

    The actor is the token's user and role; the tenant is the token's tenant
    unless the caller passes the one it already resolved."""
    tenant = tenant_id or current_tenant()
    if not tenant or not supplier_id:
        return False
    try:
        cur.execute(f"SAVEPOINT {_SAVEPOINT}")
    except Exception as exc:  # aborted transaction etc. — nothing we can do here
        log.warning("[vendor_activity] could not open savepoint for %s: %s", action, exc)
        return False
    try:
        cur.execute(
            """INSERT INTO vendor_activity_log
                   (tenant_id, supplier_id, actor, actor_role, action, title, changes, meta)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (str(tenant), str(supplier_id), current_username(), current_role(), action,
             title,
             Json(changes) if changes else None,
             Json(meta) if meta else None),
        )
        cur.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        return True
    except Exception as exc:
        try:
            cur.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
            cur.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        except Exception:
            pass
        log.warning("[vendor_activity] log write skipped for %s (%s): %s", action, supplier_id, exc)
        return False
