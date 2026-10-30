"""AU Small Finance Bank payout status / error-code lookup — the third of the
bank mappings, alongside icici.py and hdfc.py.

Source: AU's own API Banking Integration Document v1.2 (Payout + Enquiry).

What differs from the other two, and why the signature here is not the same:

**AU resolves a transaction from a compound key.** ICICI answers from one
ActCode and HDFC from a code plus its reason text; AU needs `batchStatusCode`
(batch level) *and* `statusCode` (inside `transactionEnquiryDetails`) together.
Neither is sufficient alone — `batchStatusCode` 00 covers success, failure and
still-processing, and `statusCode` is absent entirely when the batch itself
failed. Passing only one gets you None, not a guess.

Two traps in AU's own flow, both of which cost money if read casually:

1. **`responseCode: "00"` / `"ACCEPTED"` on payment creation is not a payment.**
   It means the batch was accepted and a batchNo issued. Only the Enquiry API
   returning `status: "PAID"` means the beneficiary was credited. Recording
   ACCEPTED as SUCCESS would mark every initiated payment as delivered.
2. **`statusCode` 100/101 is PROCESSING, and the money may still move.** AU's
   document is explicit that the payment must NOT be re-initiated in this state
   — only re-enquired, every 5 minutes. Re-initiating risks paying twice.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict

# Anchored on this module's own directory, never by counting parents — see
# docs/BACKEND_STRUCTURE.md on what parent-counting did to four other modules.
_DATA_PATH = Path(__file__).with_name("au_bank_status_codes.json")


class AuStatusInfo(TypedDict):
    status: str             # PROCESSED | REJECTED | INTERIM
    terminal: bool          # the bank considers this a final state
    action: str             # NO_REENQUIRY | REENQUIRE_5MIN | ENQUIRE
    meaning: str
    note: str
    resolved_by: str        # "enquiry" | "transaction_status" | "initiation"


# PROCESSED/REJECTED/INTERIM -> the SUCCESS|FAILED|PENDING vocabulary
# fact_disbursements.status already uses (see routers/disbursement.py).
_TXN_STATUS = {"PROCESSED": "SUCCESS", "REJECTED": "FAILED", "INTERIM": "PENDING"}


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def _norm(value: Optional[str]) -> str:
    return str(value).strip().upper() if value is not None else ""


def describe_enquiry(batch_status_code: Optional[str],
                     status_code: Optional[str] = None) -> Optional[AuStatusInfo]:
    """Resolve an Enquiry API result from the pair AU actually keys on.

    `status_code` may be absent or "NA" — that is itself meaningful when the
    batch failed. Returns None for any combination the document does not
    define, so the caller falls back to AU's own errorMessage rather than
    inventing an outcome.
    """
    batch = _norm(batch_status_code)
    if not batch:
        return None
    code = _norm(status_code) or "NA"
    for row in _table()["enquiry"]:
        if _norm(row["batch_status_code"]) == batch and _norm(row["status_code"]) == code:
            return AuStatusInfo(
                status=row["status"], terminal=row["terminal"], action=row["action"],
                meaning=row["meaning"], note=row["note"], resolved_by="enquiry",
            )
    return None


def describe_status(status: Optional[str]) -> Optional[AuStatusInfo]:
    """Resolve the textual `status` field (OPEN / TOBEPROCESSED / SENTTOCORE /
    PAID). Only PAID is terminal — the other three are stages on the way."""
    key = _norm(status)
    row = _table()["transaction_statuses"].get(key)
    if row is None:
        return None
    return AuStatusInfo(
        status=row["status"], terminal=row["terminal"],
        action="NO_REENQUIRY" if row["terminal"] else "REENQUIRE_5MIN",
        meaning=key, note=row["note"], resolved_by="transaction_status",
    )


def describe_initiation(response_code: Optional[str]) -> Optional[AuStatusInfo]:
    """Resolve a paymentCreation response. Note that 00/ACCEPTED maps to
    INTERIM, not SUCCESS — acceptance is not settlement."""
    row = _table()["initiation_response_codes"].get(_norm(response_code))
    if row is None:
        return None
    return AuStatusInfo(
        status=row["status"], terminal=row["terminal"], action=row["action"],
        meaning=row["response_message"], note=row["meaning"], resolved_by="initiation",
    )


def to_txn_status(batch_status_code: Optional[str], status_code: Optional[str] = None,
                  status: Optional[str] = None) -> Optional[str]:
    """SUCCESS | FAILED | PENDING for fact_disbursements, or None if unknown.

    Prefers the compound enquiry key and falls back to the textual status when
    only that is available. An unresolved result returns None, never SUCCESS —
    "we cannot tell" must mean keep polling, not record money as delivered.
    """
    info = describe_enquiry(batch_status_code, status_code) or describe_status(status)
    return _TXN_STATUS[info["status"]] if info else None


def is_terminal(batch_status_code: Optional[str], status_code: Optional[str] = None,
                status: Optional[str] = None) -> bool:
    """Whether to stop polling. Unknown resolves to non-terminal so a status
    check keeps running rather than freezing a payment mid-flight."""
    info = describe_enquiry(batch_status_code, status_code) or describe_status(status)
    return bool(info and info["terminal"])


def may_reinitiate(batch_status_code: Optional[str], status_code: Optional[str] = None,
                   status: Optional[str] = None) -> bool:
    """Whether it is safe to send this payment again.

    Only a definitively failed transaction may be re-initiated. PROCESSING and
    every unknown state answer False: AU's document states the payment must not
    be re-sent while it is still in the system, and an unrecognised state is not
    evidence that it failed. Defaulting to False risks a stuck payment; the
    opposite risks paying twice.
    """
    info = describe_enquiry(batch_status_code, status_code) or describe_status(status)
    return bool(info and info["status"] == "REJECTED")


def describe_http(code: Optional[str | int]) -> Optional[dict]:
    """Transport-layer error, distinct from any bank decision about the money."""
    return _table()["http_errors"].get(_norm(str(code) if code is not None else None))


def limits(payment_method: str = "IMPS") -> Optional[dict]:
    return _table()["limits"].get(payment_method)


def explain(batch_status_code: Optional[str], status_code: Optional[str] = None,
            status: Optional[str] = None, fallback_message: str = "") -> str:
    """Human-readable one-liner for a status_message field, mirroring
    icici.explain() and hdfc.explain()."""
    info = describe_enquiry(batch_status_code, status_code) or describe_status(status)
    if not info:
        return fallback_message
    parts = [info["meaning"]]
    if not info["terminal"]:
        parts.append("not final")
    if info["action"] == "REENQUIRE_5MIN":
        parts.append("re-enquire in 5 minutes; do not re-initiate")
    return " — ".join(parts)
