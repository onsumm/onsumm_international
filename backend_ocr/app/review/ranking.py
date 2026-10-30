"""What order the queue is in. Pure function, no I/O — see tests/test_review_ranking.py.

The ranking is deliberately **user-independent**. It is tempting to fold
"can this user act on it" into the score, but that would make the sort order
per-user, which breaks keyset pagination (the cursor (rank_score, finding_id)
has to be a total order that is stable across requests) and makes the single
stream index unusable. User relevance is expressed as lanes and filters instead,
so "things I can actually fix" is still one click and still exactly paginated.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from math import log1p

WEIGHTS: dict[str, float] = {
    "severity":   0.30,
    "money":      0.28,
    "urgency":    0.20,
    "confidence": 0.14,
    "age":        0.08,
}

# Applied to non-primary members of a cluster so 37 copies of one problem don't
# occupy the whole first page.
CLUSTER_PENALTY = 0.25

SEVERITY_WEIGHT: dict[str, float] = {
    "CRITICAL": 1.0,
    "HIGH":     0.7,
    "MEDIUM":   0.4,
    "LOW":      0.2,
}

# A refuted finding should never have been published; if one leaks through, this
# pins it to the bottom rather than letting a high stated confidence carry it up.
VERIFICATION_MULT: dict[str, float] = {
    "VERIFIED":     1.0,
    "UNVERIFIED":   0.7,
    "UNVERIFIABLE": 0.5,
    "REFUTED":      0.0,
}

DEFAULT_MONEY_CAP_INR = Decimal("2500000")  # ₹25 L — overridden per tenant


def _as_utc(value) -> datetime | None:
    """Coerce whatever the database handed us into an aware UTC datetime.

    `fact_invoices.due_date` and `stg_bills.bill_date` are DATE columns, so
    asyncpg returns `datetime.date`, which has no `tzinfo` at all. Reading
    `.tzinfo` on one raises AttributeError and takes down the whole adapter pass
    — which is exactly how the approval and payment adapters failed the first
    time this ran against a real database.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    return None


def _urgency(due_at: datetime | None, now: datetime) -> float:
    if due_at is None:
        return 0.15
    days = (due_at - now).total_seconds() / 86400.0
    if days < 0:
        return 1.0
    if days <= 3:
        return 0.8
    if days <= 7:
        return 0.5
    if days <= 30:
        return 0.2
    return 0.05


def rank_score(
    *,
    severity: str,
    confidence: float,
    verification: str,
    money_at_risk_inr: Decimal | float | None,
    due_at: datetime | None,
    first_detected_at: datetime,
    is_cluster_primary: bool = True,
    tenant_money_cap: Decimal | float | None = None,
    now: datetime | None = None,
) -> Decimal:
    now = now or datetime.now(timezone.utc)
    first_detected_at = _as_utc(first_detected_at) or now
    due_at = _as_utc(due_at)

    cap = float(tenant_money_cap or DEFAULT_MONEY_CAP_INR)
    if cap <= 0:
        cap = float(DEFAULT_MONEY_CAP_INR)

    sev = SEVERITY_WEIGHT.get(severity, 0.4)

    # Log-scaled on purpose: a ₹10L problem should outrank a ₹1L one, but not by
    # 10x, or the hub degenerates into a list of the largest bills and the small
    # blocking problems never surface.
    money = min(log1p(float(money_at_risk_inr or 0)) / log1p(cap), 1.0)

    urgency = _urgency(due_at, now)
    conf = max(0.0, min(1.0, confidence)) * VERIFICATION_MULT.get(verification, 0.7)
    age = min(max((now - first_detected_at).total_seconds() / 86400.0, 0.0) / 14.0, 1.0)

    score = (
        WEIGHTS["severity"]   * sev
        + WEIGHTS["money"]      * money
        + WEIGHTS["urgency"]    * urgency
        + WEIGHTS["confidence"] * conf
        + WEIGHTS["age"]        * age
    )
    if not is_cluster_primary:
        score *= (1.0 - CLUSTER_PENALTY)

    return Decimal(f"{score:.6f}")


# The four lanes the UI renders. Server-assigned, derived from family, so a
# finding type invented by the agent tomorrow lands in a real lane today without
# a frontend deploy.
LANE_BY_FAMILY: dict[str, str] = {
    "approval":        "blocking",
    "payment":         "blocking",
    "reconciliation":  "money",
    "fx":              "money",
    "duplication":     "money",
    "tax":             "compliance",
    "vendor_identity": "compliance",
    "data_quality":    "quality",
}

LANES: tuple[str, ...] = ("blocking", "money", "compliance", "quality")


def lane_for(family: str, verification: str = "VERIFIED") -> str:
    """Unverified agent output goes to its own lane regardless of family.

    Mixing a model's guess into the same stream as a deterministic check is the
    fastest way to lose trust in both, so 'suspected' is a lane, not a badge.
    """
    if verification in ("UNVERIFIABLE", "UNVERIFIED"):
        return "suspected"
    return LANE_BY_FAMILY.get(family, "quality")
