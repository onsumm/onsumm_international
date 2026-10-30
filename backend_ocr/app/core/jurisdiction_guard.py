"""FastAPI guards for India-only rule packs (see core/jurisdiction.py).

A tenant outside India has no GSTIN to verify, no IFSC account to penny-drop,
no Indian TDS to deduct. Endpoints that *act* on one of those rules attach
`require_pack(...)`, so such a tenant gets a clear 400 instead of a call to the
GST portal or a TDS computation that does not apply to it:

    {"error": "NOT_APPLICABLE", "pack": "gst",
     "detail": "GST verification does not apply to SG tenants"}

Reads (GET) are left alone by default: listing what is on file is harmless and
keeps existing screens loading; pass `methods=None` to guard every method.
"""
from __future__ import annotations

from typing import Iterable, Optional

from fastapi import HTTPException, Request

from ..security.auth import require_tenant
from .jurisdiction import Jurisdiction, get_jurisdiction

_WHAT = {
    "gst": "GST verification",
    "pan": "PAN",
    "tds_india": "Indian TDS",
    "bank_india": "Indian (IFSC) bank verification",
    "imps": "The IMPS rail",
    "msme": "The MSME payment rule",
}


def not_applicable(pack: str, j: Jurisdiction) -> HTTPException:
    return HTTPException(status_code=400, detail={
        "error": "NOT_APPLICABLE",
        "pack": pack,
        "detail": f"{_WHAT.get(pack, pack)} does not apply to {j.country_code} tenants",
    })


def assert_pack(tenant_id: str, pack: str) -> Jurisdiction:
    j = get_jurisdiction(tenant_id)
    if not j.has(pack):
        raise not_applicable(pack, j)
    return j


def require_pack(pack: str, methods: Optional[Iterable[str]] = ("POST", "PUT", "PATCH", "DELETE")):
    """Route/router dependency: 400 NOT_APPLICABLE when `pack` is off for the
    caller's tenant (only for `methods`; None = every method)."""
    guarded = None if methods is None else {m.upper() for m in methods}

    def _dep(request: Request) -> None:
        if guarded is not None and request.method.upper() not in guarded:
            return
        assert_pack(require_tenant(), pack)

    return _dep
