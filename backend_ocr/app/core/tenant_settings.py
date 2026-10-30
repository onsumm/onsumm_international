"""Per-tenant feature flags read from the database.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from ..security.licensing import entitlements, license_gate, impersonation_gate, require_service, invalidate as invalidate_license

def _sap_matching_enabled(tenant: str) -> bool:
    """Whether SAP item-code matching is licensed for this tenant.

    This used to read dim_tenants.sap_matching_enabled through its own 300s cache
    with no invalidation, so flipping the flag took up to five minutes — per
    uvicorn worker. It now reads the shared entitlement cache, which the admin API
    busts on write, so a toggle takes effect immediately. The four call sites and
    the semantics are unchanged.
    """
    return "sap_matching" in entitlements(tenant).enabled
