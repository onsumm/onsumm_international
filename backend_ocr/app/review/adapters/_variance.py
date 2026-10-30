"""Shared tolerance-band comparison — "is this number close enough to that one".

The HLD (§5.2.5) suggested `contract_bill_variance.py` share a comparison
helper with `billing_discrepancies.py` rather than duplicate its tolerance
math. Reading that adapter turned up a wrinkle: `billing_discrepancies.py`
does not itself compute a variance — it only *mirrors* rows a wholly separate
microservice (`services/nova_reconciliation/app/algorithm.py`) already
computed and wrote to `billing_reconciliation_discrepancies`. There is no
in-adapter tolerance logic in this package to literally import; importing the
standalone reconciliation service's `algorithm.py` from here would also be a
layering violation (`pipelines/review` has no business depending on a
separate deployable service's internals for an unrelated comparison).

So this module gives `contract_bill_variance.py` the *same shape of rule*
locally: a relative-percentage tolerance, exactly the convention
`services/nova_reconciliation/app/algorithm.py::_within_variance` already
uses for "is the bill within tolerance of the reference value" (Nova usage,
there; a contracted rate/quantity, here). The default threshold mirrors
`backend_ocr/app/routers/billing.py::DISPLAY_TOLERANCE_PCT` (2.0%) — the
number this codebase already treats as "materially the same" for a bill
vs. a reference value — rather than inventing a new one.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Same number as billing.py's DISPLAY_TOLERANCE_PCT and the nova_reconciliation
# service's default_tolerance_pct convention — "more than 2% off is a genuine
# mismatch worth a look," not a new threshold invented for this adapter.
DEFAULT_TOLERANCE_PCT = Decimal("2.0")


def to_decimal(value) -> Decimal | None:
    """Best-effort numeric coercion. None (not 0, not an exception) for
    anything that is not a real number — a blank string, a unit label that
    slipped into a value field, etc. — so callers can tell "present" from
    "absent" without a try/except of their own."""
    if value is None:
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None
    return d


def variance_pct(billed: Decimal, reference: Decimal) -> Decimal | None:
    """Relative percentage difference of `billed` from `reference`.

    None when `reference` is zero — a percentage against zero is undefined,
    and the caller should fall back to an absolute comparison or skip, never
    divide by zero.
    """
    if reference == 0:
        return None
    return abs(billed - reference) / abs(reference) * 100


def within_tolerance(
    billed: Decimal, reference: Decimal, tolerance_pct: Decimal = DEFAULT_TOLERANCE_PCT,
) -> tuple[bool, Decimal | None]:
    """True/variance_pct — whether `billed` is within `tolerance_pct` percent
    of `reference`. When `reference` is zero, "within tolerance" means
    `billed` is also (exactly) zero — there is no percentage to compare.
    """
    if reference == 0:
        return billed == 0, None
    pct = variance_pct(billed, reference)
    return pct <= tolerance_pct, pct
