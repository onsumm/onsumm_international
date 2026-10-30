"""HDFC IMPS disbursement status / error-code lookup — the counterpart to
icici.py, for vendor payments routed over HDFC.

Same job as the ICICI module: turn a bare code the gateway passes through into
what the bank's own table says — what happened, whether it is final, and
whether to re-inquire. Two things differ, both forced by HDFC's data:

1. **The table lives in hdfc_status_codes.json**, beside this module, rather
   than in a Python literal — 95 codes over 142 distinct reason messages.

2. **`codstatus` is not a unique key.** Code "1" alone covers many distinct
   reasons. The *decision* (final? re-inquire?) is still consistent per code
   for 94 of the 95, so code-only lookup is safe — except for `TXSETT`, where
   the reason text is the discriminator and getting it wrong matters:
   "Settled" is INTERIM but "Completed" is terminal success. Pass `reason`
   whenever the response carries it.

Note one genuine trap in the bank's own classification: `TXCOMP`
("Payment Completed") is INTERIM, not terminal. Do not treat the word
"completed" in a code name as settlement — go by this table.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, TypedDict

# Anchored on this module's own directory, never by counting parents — the
# latter is what silently pointed four other modules at the wrong directory
# when they moved packages (docs/BACKEND_STRUCTURE.md).
_DATA_PATH = Path(__file__).with_name("hdfc_status_codes.json")


class HdfcStatusInfo(TypedDict):
    code: str
    description: str        # the bank's own txtreason, or the code's shared meaning
    status: str             # PROCESSED | REJECTED | INTERIM
    terminal: bool          # the bank considers this a final state
    action: str             # NO_REINQUIRY | REINQUIRY
    resolved_by: str        # "code" | "reason" — how the lookup landed


# PROCESSED/REJECTED/INTERIM -> the SUCCESS|PENDING|FAILED vocabulary
# fact_disbursements.status already uses (see routers/disbursement.py).
_TXN_STATUS = {"PROCESSED": "SUCCESS", "REJECTED": "FAILED", "INTERIM": "PENDING"}


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def _norm_reason(text: str) -> str:
    return " ".join(text.split()).casefold()


def describe(code: Optional[str], reason: Optional[str] = None) -> Optional[HdfcStatusInfo]:
    """Resolve a status code (plus the response's reason text, when present).

    Returns None for an unknown code, and None for an ambiguous code whose
    reason could not be matched — in both cases the caller should fall back to
    the gateway's own raw message rather than invent an explanation.
    """
    if not code:
        return None
    codes = _table()["codes"]
    key = str(code).strip()
    entry = codes.get(key) or codes.get(key.upper())
    if entry is None:
        return None

    if reason:
        want = _norm_reason(reason)
        for r in entry["reasons"]:
            if _norm_reason(r["reason"]) == want:
                return HdfcStatusInfo(
                    code=key, description=r["reason"], status=r["status"],
                    terminal=r["terminal"], action=r["action"], resolved_by="reason",
                )

    if entry["ambiguous"]:
        # Decision genuinely is not derivable from the code alone. Say so
        # rather than picking one of the two readings.
        return None

    # Single-reason codes carry their meaning; multi-reason ones share only the
    # decision, so describe the code rather than an arbitrary member reason.
    description = entry["reasons"][0]["reason"] if len(entry["reasons"]) == 1 else key
    return HdfcStatusInfo(
        code=key, description=description, status=entry["status"],
        terminal=entry["terminal"], action=entry["action"], resolved_by="code",
    )


def to_txn_status(code: Optional[str], reason: Optional[str] = None) -> Optional[str]:
    """SUCCESS | FAILED | PENDING for fact_disbursements, or None if unknown.

    An unresolved code returns None, never SUCCESS — the caller decides, and
    the safe reading of "we cannot tell" is to keep polling, not to record
    money as delivered.
    """
    info = describe(code, reason)
    return _TXN_STATUS[info["status"]] if info else None


def is_terminal(code: Optional[str], reason: Optional[str] = None) -> bool:
    """Whether to stop polling. Unknown codes are treated as non-terminal so a
    status check keeps running rather than freezing a payment mid-flight."""
    info = describe(code, reason)
    return bool(info and info["terminal"])


def explain(code: Optional[str], reason: str = "", fallback_message: str = "") -> str:
    """Human-readable one-liner for a status_message field, mirroring
    icici.explain(): "<description> — <state> — <what to do>", or the
    gateway's own message when the code isn't recognized."""
    info = describe(code, reason)
    if not info:
        return reason or fallback_message
    parts = [info["description"]]
    if not info["terminal"]:
        parts.append("not final")
    if info["action"] == "REINQUIRY":
        parts.append("re-inquire for the final status")
    return " — ".join(parts)
