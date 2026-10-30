"""Unit tests for backend_ocr/app/review/ranking.py — pure function, no mocks needed.

The properties tested here are the ones that decide what a user sees first, so
each is asserted as a property rather than against a magic number: the weights
can be retuned without rewriting the suite, but the ordering guarantees cannot
quietly break.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend_ocr.app.review.ranking import (
    LANE_BY_FAMILY, LANES, lane_for, rank_score,
)

NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def score(**over):
    base = dict(
        severity="MEDIUM", confidence=1.0, verification="VERIFIED",
        money_at_risk_inr=None, due_at=None, first_detected_at=NOW, now=NOW,
    )
    base.update(over)
    return rank_score(**base)


# ── Bounds ────────────────────────────────────────────────────────────────────

def test_score_is_bounded_0_to_1():
    worst = score(severity="LOW", confidence=0.0, verification="REFUTED")
    best = score(severity="CRITICAL", money_at_risk_inr=Decimal("1e9"),
                 due_at=NOW - timedelta(days=5),
                 first_detected_at=NOW - timedelta(days=90))
    assert 0.0 <= float(worst) <= 1.0
    assert 0.0 <= float(best) <= 1.0
    assert worst < best


# ── Monotonicity ──────────────────────────────────────────────────────────────

def test_monotonic_in_severity():
    scores = [score(severity=s) for s in ("LOW", "MEDIUM", "HIGH", "CRITICAL")]
    assert scores == sorted(scores)


def test_monotonic_in_money():
    small = score(money_at_risk_inr=Decimal("10000"))
    large = score(money_at_risk_inr=Decimal("1000000"))
    assert small < large


def test_money_is_log_scaled_not_linear():
    """A 10x bigger problem must rank higher, but nowhere near 10x higher —
    otherwise the queue is just a list of the largest bills and the small
    blocking problems never surface."""
    one_lakh = score(money_at_risk_inr=Decimal("100000"))
    ten_lakh = score(money_at_risk_inr=Decimal("1000000"))
    assert one_lakh < ten_lakh < one_lakh * 10


def test_overdue_outranks_far_future():
    overdue = score(due_at=NOW - timedelta(days=1))
    soon = score(due_at=NOW + timedelta(days=2))
    later = score(due_at=NOW + timedelta(days=60))
    assert later < soon < overdue


def test_older_findings_drift_up():
    fresh = score(first_detected_at=NOW)
    stale = score(first_detected_at=NOW - timedelta(days=30))
    assert fresh < stale


# ── Verification and confidence ───────────────────────────────────────────────

def test_verified_outranks_suspected_all_else_equal():
    verified = score(verification="VERIFIED")
    unverified = score(verification="UNVERIFIED")
    unverifiable = score(verification="UNVERIFIABLE")
    assert unverifiable < unverified < verified


def test_refuted_contributes_no_confidence():
    """A refuted finding should never have been published at all; if one leaks
    through, its stated confidence must not carry it up the queue."""
    refuted = score(verification="REFUTED", confidence=1.0)
    refuted_zero = score(verification="REFUTED", confidence=0.0)
    assert refuted == refuted_zero


def test_confidence_is_clamped():
    assert score(confidence=5.0) == score(confidence=1.0)
    assert score(confidence=-2.0) == score(confidence=0.0)


# ── Clusters ──────────────────────────────────────────────────────────────────

def test_cluster_members_rank_below_their_primary():
    primary = score(is_cluster_primary=True)
    member = score(is_cluster_primary=False)
    assert member < primary


# ── Tenant-relative money ─────────────────────────────────────────────────────

def test_money_cap_is_tenant_relative():
    """The same rupee amount should weigh more for a small tenant than a large
    one, or one customer's routine bill buries another customer's emergency."""
    amount = Decimal("200000")
    small_tenant = score(money_at_risk_inr=amount, tenant_money_cap=Decimal("300000"))
    large_tenant = score(money_at_risk_inr=amount, tenant_money_cap=Decimal("50000000"))
    assert large_tenant < small_tenant


def test_zero_or_negative_cap_falls_back():
    assert score(money_at_risk_inr=Decimal("1000"), tenant_money_cap=0) == \
           score(money_at_risk_inr=Decimal("1000"))


# ── Naive datetimes ───────────────────────────────────────────────────────────

def test_naive_datetimes_are_accepted():
    """asyncpg hands back naive datetimes for some column types; the scorer must
    not explode on one halfway through a pass."""
    naive = datetime(2026, 9, 1, 9, 0)
    assert float(score(first_detected_at=naive, due_at=naive)) > 0


# ── Lanes ─────────────────────────────────────────────────────────────────────

def test_every_family_maps_to_a_real_lane():
    from backend_ocr.app.review.types import FAMILIES
    for fam in FAMILIES:
        assert fam in LANE_BY_FAMILY, f"family {fam} has no lane"
        assert LANE_BY_FAMILY[fam] in LANES


def test_unverified_output_lands_in_the_suspected_lane():
    """A model's guess must not share a lane with a deterministic check,
    whatever family it claims."""
    assert lane_for("payment", "VERIFIED") == "blocking"
    assert lane_for("payment", "UNVERIFIED") == "suspected"
    assert lane_for("tax", "UNVERIFIABLE") == "suspected"


def test_unknown_family_still_gets_a_lane():
    """The agent can invent a finding type; it must never produce a card with
    nowhere to go."""
    assert lane_for("something_new_entirely") in LANES
