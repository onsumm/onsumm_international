"""One adapter per disbursal bank, behind one interface.

Each bank reads its own status codes differently, and the differences are not
cosmetic — they decide whether money is recorded as delivered:

  * **ICICI** answers from a single ActCode.
  * **HDFC** needs the code *and* its reason text: `codstatus` is not unique,
    and `TXSETT` means "Settled" (interim) or "Completed" (final) depending on
    it.
  * **AU** needs a compound key, `batchStatusCode` *and* `statusCode` — neither
    carries an outcome alone.

Before this package those tables sat loose in `integrations/` and the
disbursement router imported ICICI's `explain` directly, so every payment was
explained with ICICI's vocabulary regardless of which bank actually processed
it. An AU `statusCode` of 100 ("processing — do not re-initiate") would have
been looked up in ICICI's table, missed, and shown as a raw gateway string.

So the rule here is: **the router never imports a bank module.** It calls
`for_bank(code)` and uses whatever comes back. Adding a fourth bank is a new
module plus one line in `_ADAPTERS` — not an edit to the payment flow.

`normalise()` exists because the wire format differs from the display name:
Lendix expects `bankProvider` of `ICICI` or `AUBANK`, while the UI shows
"AU Small Finance Bank".
"""
from __future__ import annotations

from typing import Callable, Optional, Protocol

from . import au_bank, hdfc, icici


class BankAdapter(Protocol):
    """What the payment flow may rely on, whichever bank processed the payment.

    Every method tolerates partial input: a bank that cannot resolve an outcome
    from what it was given returns None/False rather than guessing, because the
    caller's fallback (keep polling) is safe and a guess is not.
    """

    code: str
    label: str

    def to_txn_status(self, **fields) -> Optional[str]:
        """SUCCESS | FAILED | PENDING for fact_disbursements, or None."""

    def is_terminal(self, **fields) -> bool:
        """Whether to stop polling."""

    def may_reinitiate(self, **fields) -> bool:
        """Whether this payment may safely be sent again."""

    def explain(self, **fields) -> str:
        """One human-readable line for status_message."""


class _Adapter:
    """Binds one bank's module to the shared interface.

    The modules keep their own natural signatures — ICICI takes a code, HDFC a
    code and reason, AU two codes — and the lambdas here map the router's
    keyword bag onto each. That keeps the per-bank code readable as the bank's
    own documentation rather than bent into a common shape.
    """

    def __init__(self, code: str, label: str, wire: str, *,
                 txn_status: Callable, terminal: Callable,
                 explain: Callable, reinitiate: Optional[Callable] = None,
                 min_amount: Optional[float] = None, max_amount: Optional[float] = None,
                 bulk_max: Optional[int] = None):
        self.code = code
        self.label = label
        self.wire = wire            # what the gateway expects as bankProvider
        # Per-transaction bounds the bank itself enforces. None means the bank's
        # document does not state one — left unenforced rather than invented,
        # since a guessed ceiling would block legitimate payments.
        self.min_amount = min_amount
        self.max_amount = max_amount
        self.bulk_max = bulk_max
        self._txn_status = txn_status
        self._terminal = terminal
        self._explain = explain
        self._reinitiate = reinitiate

    def check_amount(self, amount: float) -> Optional[str]:
        """Why this amount would be rejected by the bank, or None if it is fine.

        Checked before the gateway call so an out-of-range payment fails with a
        message naming the limit, rather than burning a transaction reference on
        a rejection the bank words obscurely.
        """
        if self.min_amount is not None and amount < self.min_amount:
            return f"{self.label} requires at least ₹{self.min_amount:,.0f} per transaction"
        if self.max_amount is not None and amount > self.max_amount:
            return (f"{self.label} caps a single transaction at ₹{self.max_amount:,.0f}; "
                    f"split ₹{amount:,.2f} across several payments")
        return None

    def check_batch(self, count: int) -> Optional[str]:
        if self.bulk_max is not None and count > self.bulk_max:
            return f"{self.label} accepts at most {self.bulk_max} transactions per batch, got {count}"
        return None

    def to_txn_status(self, **f) -> Optional[str]:
        return self._txn_status(f)

    def is_terminal(self, **f) -> bool:
        return bool(self._terminal(f))

    def may_reinitiate(self, **f) -> bool:
        if self._reinitiate is not None:
            return bool(self._reinitiate(f))
        # Banks with no explicit rule: only a resolved failure may be re-sent.
        # Unknown is never treated as failed — that is how a payment gets made
        # twice.
        return self._txn_status(f) == "FAILED"

    def explain(self, **f) -> str:
        return self._explain(f)


_ICICI_STATUS = {"Successful": "SUCCESS", "Deemed Approved": "PENDING"}


def _icici_txn_status(f: dict) -> Optional[str]:
    info = icici.describe_act_code(f.get("act_code"))
    if not info:
        return None
    # ICICI's klass is about who declined and whether it is retryable; TD/BD are
    # both terminal failures, "Deemed Approved" explicitly is not resolved.
    return _ICICI_STATUS.get(info["klass"], "FAILED")


_ADAPTERS: dict[str, _Adapter] = {
    "ICICI": _Adapter(
        # docs/DISBURSEMENT_IMPS_API.md: "Minimum: Rs 1 (ICICI requirement)",
        # "ICICI hard limit: Rs 5,00,000".
        "ICICI", "ICICI Bank", "ICICI", min_amount=1, max_amount=500000,
        txn_status=_icici_txn_status,
        terminal=lambda f: _icici_txn_status(f) in ("SUCCESS", "FAILED"),
        explain=lambda f: icici.explain(f.get("act_code"), f.get("fallback_message", "")),
    ),
    "HDFC": _Adapter(
        # HDFC's sheet documents status codes only, no per-transaction bounds,
        # so none are enforced rather than borrowed from another bank.
        "HDFC", "HDFC Bank", "HDFC",
        txn_status=lambda f: hdfc.to_txn_status(f.get("status_code"), f.get("reason")),
        terminal=lambda f: hdfc.is_terminal(f.get("status_code"), f.get("reason")),
        explain=lambda f: hdfc.explain(f.get("status_code"), f.get("reason") or "",
                                       f.get("fallback_message", "")),
    ),
    "AUBANK": _Adapter(
        # AU API Banking doc v1.2: IMPS min Rs 1 / max Rs 5,00,000 per txn,
        # bulk up to 50 transactions in one call.
        "AUBANK", "AU Small Finance Bank", "AUBANK", min_amount=1, max_amount=500000, bulk_max=50,
        txn_status=lambda f: au_bank.to_txn_status(f.get("batch_status_code"),
                                                   f.get("status_code"), f.get("status")),
        terminal=lambda f: au_bank.is_terminal(f.get("batch_status_code"),
                                               f.get("status_code"), f.get("status")),
        reinitiate=lambda f: au_bank.may_reinitiate(f.get("batch_status_code"),
                                                    f.get("status_code"), f.get("status")),
        explain=lambda f: au_bank.explain(f.get("batch_status_code"), f.get("status_code"),
                                          f.get("status"), f.get("fallback_message", "")),
    ),
}

# Aliases people and older rows actually use. Kept narrow on purpose: a typo
# should fail loudly at selection rather than silently route a payment to the
# wrong bank.
_ALIASES = {
    "AU": "AUBANK",
    "AU BANK": "AUBANK",
    "AU SMALL FINANCE BANK": "AUBANK",
    "ICICI BANK": "ICICI",
    "HDFC BANK": "HDFC",
}

DEFAULT_BANK = "ICICI"


def normalise(code: Optional[str]) -> Optional[str]:
    """Canonical bank code, or None if it is not one we support."""
    if not code:
        return None
    key = " ".join(str(code).split()).upper()
    key = _ALIASES.get(key, key)
    return key if key in _ADAPTERS else None


def for_bank(code: Optional[str]) -> _Adapter:
    """The adapter for a bank code, falling back to ICICI.

    The fallback exists for rows written before the bank became selectable —
    `fact_disbursements.bank_provider` defaults to 'ICICI' in the schema, so an
    unrecognised value is far more likely to be legacy data than a new bank.
    """
    return _ADAPTERS[normalise(code) or DEFAULT_BANK]


def supported_banks() -> list[dict]:
    """For the UI's disbursal-bank picker."""
    return [{"code": a.code, "label": a.label, "wire": a.wire,
             "min_amount": a.min_amount, "max_amount": a.max_amount,
             "bulk_max": a.bulk_max} for a in _ADAPTERS.values()]


__all__ = ["BankAdapter", "DEFAULT_BANK", "for_bank", "normalise", "supported_banks",
           "au_bank", "hdfc", "icici"]
