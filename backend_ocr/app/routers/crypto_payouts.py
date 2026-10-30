"""USDC payouts: preview, Safe signing, status and cancel (08_API_CONTRACT.md §4).

Starting a payout and releasing it with the OTP use the existing disbursement
routes — `POST /api/bills/{invoice_id}/disbursements` with `rail: "USDC"` and
`POST /api/disbursements/{id}/verify-otp` — which delegate to
domain/crypto/payouts.py. This router holds what only the crypto rail has.

Routes
──────
GET  /api/withholding/preview                         rule pack, rate, units; India alternative
GET  /api/disbursements/{disbursement_id}/safe-tx     EIP-712 typed data for the Safe owners
POST /api/disbursements/{disbursement_id}/signatures  add an owner signature; relays at threshold
GET  /api/disbursements/{disbursement_id}/crypto-status
POST /api/disbursements/{disbursement_id}/cancel      only before the transaction is submitted

Signing is open to any user of the tenant: the gate is cryptographic. Only a
current owner of the Safe can produce a signature the Safe accepts, and owners
are read from the chain at every signature.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from ..domain.crypto.payouts import PayoutService
from ..domain.crypto.policy import error
from ..security.auth import current_username, require_role, require_tenant

router = APIRouter(prefix="/api", tags=["Crypto rail: payouts"])


@router.get("/withholding/preview")
def withholding_preview(invoice_id: str, rail: str = "USDC", amount: Optional[str] = None):
    tenant_id = require_tenant()
    try:
        amt = Decimal(amount) if amount not in (None, "") else None
    except InvalidOperation:
        raise error(400, "INVALID_AMOUNT", "amount must be a number.")
    return PayoutService().preview_withholding(tenant_id, invoice_id, rail, amt)


@router.get("/disbursements/{disbursement_id}/safe-tx")
def safe_tx(disbursement_id: str):
    tenant_id = require_tenant()
    return PayoutService().safe_tx_view(tenant_id, disbursement_id)


class SignatureBody(BaseModel):
    signer: str
    signature: str


@router.post("/disbursements/{disbursement_id}/signatures")
def add_signature(disbursement_id: str, body: SignatureBody):
    tenant_id = require_tenant()
    return PayoutService().add_signature(tenant_id, disbursement_id, body.signer, body.signature, current_username())


@router.get("/disbursements/{disbursement_id}/crypto-status")
def crypto_status(disbursement_id: str):
    tenant_id = require_tenant()
    return PayoutService().status(tenant_id, disbursement_id)


class CancelBody(BaseModel):
    reason: str = ""


@router.get("/disbursements/pending-usdc")
def pending_usdc(supplier_id: Optional[str] = None):
    """USDC payouts waiting for Safe signatures or for the chain, oldest nonce first."""
    tenant_id = require_tenant()
    return {"payouts": PayoutService().pending(tenant_id, supplier_id)}


@router.post("/disbursements/{disbursement_id}/keep-only")
def keep_only(disbursement_id: str):
    """Cancel this vendor's other unsent USDC payouts and rebuild this one at the next free nonce."""
    tenant_id = require_tenant()
    require_role("admin")
    return PayoutService().keep_only(tenant_id, disbursement_id, current_username())


@router.post("/disbursements/{disbursement_id}/repropose")
def repropose(disbursement_id: str):
    """Rebuild an unsubmitted payout at the Safe's next free nonce (signatures are cleared)."""
    tenant_id = require_tenant()
    require_role("admin")
    return PayoutService().repropose(tenant_id, disbursement_id, current_username())


@router.post("/disbursements/{disbursement_id}/cancel")
def cancel(disbursement_id: str, body: CancelBody):
    tenant_id = require_tenant()
    require_role("admin")
    return PayoutService().cancel(tenant_id, disbursement_id, body.reason, current_username())
