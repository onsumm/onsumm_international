"""Real Lendix disbursement (IMPS) client — docs/DISBURSEMENT_IMPS_API.md.

Same shape as sandbox_kyb.py: plain httpx calls, config-driven secrets, no
client class. Unlike the mock this replaces, every call here is a real network
request to DISBURSEMENT_BASE_URL and can move real (UAT-capped, ≤₹10) money —
callers must treat a non-2xx or success=false response as a real failure, not
retry blindly, and never generate a fresh tran_ref_no for the same payment
attempt (§8.3 of docs/payments_docs/DISBURSEMENT_PAYMENT_INTEGRATION_PLAN_LENDIX.md).

Two endpoint families exist in the doc — the generic Disbursement module
(`/disbursement/imps/initiate`, `/disbursement/imps/status`) and the Vendor
Payment module (`/vendor-payment/pay`, `/vendor-payment/status`, which adds
`vendorName`/`month` metadata). As of 2026-09-12, `disbursement.py`'s
`_real_imps_initiate`/`_real_imps_status` call `vendor_payment_pay`/
`vendor_payment_status` below — the permission gap that blocked this
(`403 No permission for operation: vendor-payment:pay`, confirmed
2026-08-24) is resolved, and vendor-payment is the correct endpoint for what
this module does: pays a vendor's bill, not a generic disbursement.
`imps_initiate`/`imps_status` (the generic module) are kept below only as a
fallback reference, unused."""
from __future__ import annotations

import re
from typing import Optional

import httpx

from ..config import DISBURSEMENT_API_KEY, DISBURSEMENT_BASE_URL, DISBURSEMENT_NBFC_NAME

TIMEOUT_SECONDS = 30.0


class ImpsApiError(Exception):
    """A transport-level or malformed-response failure calling the gateway —
    distinct from a normal bank-side FAILED/PENDING result, which is a valid,
    well-formed response the caller handles itself. `raw_body` carries the
    gateway's actual JSON response (when one was returned, even alongside a
    non-200 status) — without it, a caller logging this error only has the
    derived message string, not the real evidence of what the gateway sent."""

    def __init__(self, message: str, status_code: Optional[int] = None, raw_body: Optional[dict] = None):
        self.status_code = status_code
        self.raw_body = raw_body
        super().__init__(message)


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-api-key": DISBURSEMENT_API_KEY}


def _post(path: str, payload: dict) -> dict:
    url = f"{DISBURSEMENT_BASE_URL.rstrip('/')}{path}"
    try:
        resp = httpx.post(url, headers=_headers(), json=payload, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        raise ImpsApiError(f"Disbursement gateway request failed: {e}") from e

    try:
        body = resp.json()
    except ValueError as e:
        raise ImpsApiError(f"Disbursement gateway returned non-JSON body (status {resp.status_code}): {resp.text[:300]}") from e

    if resp.status_code != 200:
        message = (body.get("error") or {}).get("message") or body.get("message") or resp.text[:300]
        raise ImpsApiError(f"Disbursement gateway returned {resp.status_code}: {message}", resp.status_code, raw_body=body)

    return body


# The gateway rejects a payment whose remarks run past 40 characters
# ("transactionDetails[0].remarks Remarks cannot be more than 40 characters"),
# and banks put the remark on the beneficiary's statement, where punctuation
# is not reliably accepted. Every remark sent is passed through this.
REMARKS_MAX = 40


def clean_remarks(text: Optional[str]) -> str:
    """Remarks the gateway accepts: letters, digits and spaces, at most 40 characters."""
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", text or "")
    s = " ".join(s.split())
    return s[:REMARKS_MAX].rstrip() or "Vendor payment"


def imps_initiate(
    *, lead_id: str, tran_ref_no: str, amount: float, beneficiary_account: str,
    beneficiary_ifsc: str, beneficiary_name: str, beneficiary_mobile: str,
    remitter_account_number: str, bank_provider: str, payment_remarks: str,
    nbfc_name: Optional[str] = None,
) -> dict:
    """POST /disbursement/imps/initiate — the generic Disbursement module (no
    vendorName/month metadata, unlike vendor_payment_pay). This is the
    endpoint DISBURSEMENT_API_KEY actually has permission for. Returns the
    raw response body; callers read `data.status`/`data.bankRRN`/
    `data.tranRefNo`."""
    payload = {
        "leadId": lead_id,
        "remitterAccountNumber": remitter_account_number,
        "beneficiaryAccount": beneficiary_account,
        "beneficiaryIFSC": beneficiary_ifsc,
        "beneficiaryName": beneficiary_name,
        "beneficiaryMobile": beneficiary_mobile,
        "amount": amount,
        "tranRefNo": tran_ref_no,
        "paymentRemarks": clean_remarks(payment_remarks),
        "nbfcName": nbfc_name or DISBURSEMENT_NBFC_NAME,
        "bankProvider": bank_provider,
    }
    return _post("/disbursement/imps/initiate", payload)


def imps_status(
    *, tran_ref_no: str, date: str, remitter_account_number: str,
    bank_provider: str, nbfc_name: Optional[str] = None,
) -> dict:
    """POST /disbursement/imps/status. `tran_ref_no` is the value from the
    `initiate` response's `data.tranRefNo` (see the "Important — tranRefNo in
    Response" note in docs/DISBURSEMENT_IMPS_API.md). `date` is `MM/DD/YYYY`."""
    payload = {
        "tranRefNo": tran_ref_no,
        "date": date,
        "nbfcName": nbfc_name or DISBURSEMENT_NBFC_NAME,
        "bankProvider": bank_provider,
        "remitterAccountNumber": remitter_account_number,
    }
    return _post("/disbursement/imps/status", payload)


def vendor_payment_pay(
    *, lead_id: str, tran_ref_no: str, amount: float, beneficiary_account: str,
    beneficiary_ifsc: str, beneficiary_name: str, beneficiary_mobile: str,
    remitter_account_number: str, bank_provider: str, payment_remarks: str,
    vendor_name: Optional[str] = None, month: Optional[str] = None,
    nbfc_name: Optional[str] = None,
    bene_code: Optional[str] = None, idempotency_key: Optional[str] = None,
) -> dict:
    """POST /vendor-payment/pay. Returns the raw response body (matches
    docs/DISBURSEMENT_IMPS_API.md's Success/Error Response shape) — callers
    read `data.status` (SUCCESS/PENDING/FAILED) and `data.bankRRN`/
    `data.tranRefNo`, the same fields the mock's return value already carried,
    so no caller-side reshaping is needed to swap mock for real.

    `bene_code` is AU Bank's own beneficiary code — only meaningful when
    `bank_provider == 'AUBANK'`, omitted entirely otherwise rather than sent
    as null. `idempotency_key` is separate from `tran_ref_no`: the latter is
    always required and is what we poll status by; the former is optional
    duplicate-prevention the gateway itself keys on, left unset unless a
    caller has a real one to hand — a fabricated key would only pretend a
    retry is a duplicate of a request that never happened."""
    payload = {
        "leadId": lead_id,
        "nbfcName": nbfc_name or DISBURSEMENT_NBFC_NAME,
        "vendorName": vendor_name or beneficiary_name,
        "month": month,
        "remitterAccountNumber": remitter_account_number,
        "beneficiaryAccount": beneficiary_account,
        "beneficiaryIFSC": beneficiary_ifsc,
        "beneficiaryName": beneficiary_name,
        "beneficiaryMobile": beneficiary_mobile,
        "amount": amount,
        "tranRefNo": tran_ref_no,
        "paymentRemarks": clean_remarks(payment_remarks),
        "bankProvider": bank_provider,
    }
    if bene_code:
        payload["beneCode"] = bene_code
    if idempotency_key:
        payload["idempotencyKey"] = idempotency_key
    return _post("/vendor-payment/pay", payload)


def vendor_payment_status(
    *, tran_ref_no: str, date: str, remitter_account_number: str,
    bank_provider: str, nbfc_name: Optional[str] = None,
) -> dict:
    """POST /vendor-payment/status. `tran_ref_no` here is the value from the
    `pay` response's `data.tranRefNo` (the bank's own reference for AU Bank,
    or our original one echoed back for ICICI — see docs/DISBURSEMENT_IMPS_API.md's
    "Important — tranRefNo in Response" note), not necessarily what we originally
    generated. `date` is `MM/DD/YYYY`."""
    payload = {
        "nbfcName": nbfc_name or DISBURSEMENT_NBFC_NAME,
        "tranRefNo": tran_ref_no,
        "date": date,
        "bankProvider": bank_provider,
        "remitterAccountNumber": remitter_account_number,
    }
    return _post("/vendor-payment/status", payload)
