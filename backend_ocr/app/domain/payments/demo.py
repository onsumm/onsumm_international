"""Demo payments: the `demo_payments` licence.

For a tenant with it ticked, Pay never reaches a bank. The OTP is checked for
the user, the transfer settles as a simulated SUCCESS (disbursement.py's
`_forced_success_initiate`), and the payment receipt the Communication Agent
sends goes to one demo POC address the tenant chose, never to the vendor's real
contacts. Everything else (the bill reading Paid, the audit trail, the agent
run) happens for real, so the walkthrough shows the whole loop.

The POC address lives in `dim_tenants.settings_jsonb -> 'demo_payments'`.
"""
from __future__ import annotations

import json
import re

from ...config import DISBURSEMENT_FORCE_SUCCESS
from ...security.licensing import has_service

LICENCE = "demo_payments"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_demo(tenant_id: str) -> bool:
    """Pay is simulated for this tenant: no bank call, no OTP mail, receipts to the demo POC."""
    try:
        return has_service(str(tenant_id), LICENCE)
    except Exception:
        return False


def skips_gateway(tenant_id: str) -> bool:
    """No bank call: the licence, or the environment-wide forced-success switch."""
    return DISBURSEMENT_FORCE_SUCCESS or is_demo(tenant_id)


def poc_email(cur, tenant_id: str) -> str | None:
    cur.execute("SELECT settings_jsonb -> 'demo_payments' ->> 'poc_email' AS e FROM dim_tenants WHERE tenant_id = %s",
                (tenant_id,))
    row = cur.fetchone()
    e = (row["e"] if isinstance(row, dict) else (row[0] if row else None)) if row else None
    return e.strip() if e and e.strip() else None


def set_poc_email(cur, tenant_id: str, email: str | None) -> str | None:
    email = (email or "").strip() or None
    if email and not _EMAIL.match(email):
        raise ValueError("Enter a valid email address")
    cur.execute(
        """UPDATE dim_tenants
              SET settings_jsonb = jsonb_set(COALESCE(settings_jsonb, '{}'::jsonb), '{demo_payments}', %s::jsonb)
            WHERE tenant_id = %s""",
        (json.dumps({"poc_email": email}), tenant_id),
    )
    return email
