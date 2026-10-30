"""What "verified" means for a vendor, per the tenant's jurisdiction.

The vendor card's "Verified / Not verified" badge used to mean one thing: the
vendor's GSTIN passed the GST portal (dim_suppliers.status = 'VERIFIED'), with
a verified IFSC bank account alongside. A vendor of a tenant outside India has
no GSTIN, so it could never become verified — every international vendor wore a
permanent "Not verified" badge.

`verification` keeps that exact meaning for Indian tenants and gives other
tenants their own basis (core/jurisdiction.py):

    India          GSTIN verified (the 'gst' pack) and a verified bank account
                   ('bank_india'), as before
    International  a payable destination — an ACTIVE crypto-rail wallet or a
                   verified bank account — and tax documents on file
                   (e.g. W-8BEN-E)

    {"status": "VERIFIED" | "PENDING" | "NOT_STARTED",
     "basis":  ["gstin", "bank", "wallet", "tax_docs"],   # what counted
     "label":  "Verified" | "Verification pending" | "Not verified"}

It is an added field; `status`, `bank_verified` and the rest are unchanged.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..core.jurisdiction import Jurisdiction

_LABELS = {"VERIFIED": "Verified", "PENDING": "Verification pending", "NOT_STARTED": "Not verified"}


def derive_verification(
    j: Jurisdiction,
    *,
    gst_status: Optional[str],
    bank_verified: bool,
    has_active_wallet: bool = False,
    tax_docs: Optional[dict] = None,
) -> dict:
    """Pure: the verification badge for one vendor."""
    basis: list[str] = []
    if j.has("gst") or j.has("bank_india"):
        gst_ok = (gst_status or "").upper() == "VERIFIED" if j.has("gst") else True
        bank_ok = bool(bank_verified) if j.has("bank_india") else True
        if j.has("gst") and gst_ok:
            basis.append("gstin")
        if j.has("bank_india") and bank_ok:
            basis.append("bank")
        if gst_ok and bank_ok:
            status = "VERIFIED"
        elif basis:
            status = "PENDING"
        else:
            status = "NOT_STARTED"
    else:
        if has_active_wallet:
            basis.append("wallet")
        if bank_verified:
            basis.append("bank")
        has_docs = bool({k: v for k, v in (tax_docs or {}).items() if v})
        if has_docs:
            basis.append("tax_docs")
        payable = has_active_wallet or bool(bank_verified)
        if payable and has_docs:
            status = "VERIFIED"
        elif payable or has_docs:
            status = "PENDING"
        else:
            status = "NOT_STARTED"
    return {"status": status, "basis": basis, "label": _LABELS[status]}


def load_verification_inputs(cur, tenant_id: str, supplier_ids: Iterable[str]) -> dict[str, dict]:
    """{supplier_id: {"has_active_wallet", "has_verified_bank_row", "tax_docs",
    "country_code", "tax_residency"}}.

    Read in one query for a whole vendor list. The wallet and tax_docs columns
    come with the crypto rail's migrations; on a database without them this
    returns {} (savepoint-protected), and the badge falls back to the
    dim_suppliers fields alone.
    """
    ids = [str(s) for s in supplier_ids]
    if not ids:
        return {}
    try:
        cur.execute("SAVEPOINT vendor_verification")
        cur.execute(
            """SELECT s.supplier_id::text AS supplier_id,
                      s.tax_docs, s.country_code, s.tax_residency,
                      EXISTS (SELECT 1 FROM supplier_wallets w
                               WHERE w.tenant_id = s.tenant_id AND w.supplier_id = s.supplier_id
                                 AND w.status = 'ACTIVE') AS has_active_wallet,
                      EXISTS (SELECT 1 FROM supplier_bank_accounts b
                               WHERE b.tenant_id = s.tenant_id AND b.supplier_id = s.supplier_id
                                 AND b.verified) AS has_verified_bank_row
                 FROM dim_suppliers s
                WHERE s.tenant_id = %s AND s.supplier_id::text = ANY(%s)""",
            (tenant_id, ids),
        )
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT vendor_verification")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT vendor_verification")
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        r = dict(r) if not isinstance(r, dict) else r
        out[r["supplier_id"]] = {
            "has_active_wallet": bool(r["has_active_wallet"]),
            "has_verified_bank_row": bool(r["has_verified_bank_row"]),
            "tax_docs": r["tax_docs"] or {},
            "country_code": (r.get("country_code") or None),
            "tax_residency": (r.get("tax_residency") or None),
        }
    return out
