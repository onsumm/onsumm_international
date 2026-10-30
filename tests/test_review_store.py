"""backend_ocr/app/review/{types,store}.py — identity, lifecycle and the SQL invariants.

asyncpg is mocked, so the ON CONFLICT semantics themselves are Postgres's
problem; what is tested here is that the statements we send carry the guards
they must carry. Those guards are load-bearing and easy to delete by accident:
  * the upsert targets the PARTIAL index, which is what gives re-open semantics
  * the auto-resolve sweep can never overwrite a human's decision
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend_ocr.app.review.store import (
    apply_suppression, record_event, sweep_autoresolve, upsert_finding,
)
from backend_ocr.app.review.types import (
    FindingDraft, condition_hash, diff_evidence, fields_evidence, action,
)

TENANT = "10000000-0000-0000-0000-000000000001"
BILL = "22222222-0000-0000-0000-000000000001"
NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def _conn(**kw):
    c = AsyncMock()
    c.execute = AsyncMock(return_value="UPDATE 1")
    c.executemany = AsyncMock(return_value=None)
    c.fetch = AsyncMock(return_value=kw.get("fetch", []))
    c.fetchrow = AsyncMock(return_value=kw.get("fetchrow"))
    c.fetchval = AsyncMock(return_value=kw.get("fetchval"))
    return c


def _draft(**over):
    base = dict(
        tenant_id=TENANT, finding_type="gstin_mismatch", family="tax",
        subject_type="bill", subject_id=BILL, severity="HIGH",
        title="GSTIN on the bill does not match the vendor master",
        deep_link=f"/bills/{BILL}?focus=vendor.gstin",
        condition_hash=condition_hash("27AABCU9603R1ZX", "29AABCU9603R1ZJ"),
        detector="adapter:test",
    )
    base.update(over)
    return FindingDraft(**base)


# ── Identity ──────────────────────────────────────────────────────────────────

def test_finding_key_is_deterministic_and_structured():
    assert _draft().finding_key == f"gstin_mismatch:bill:{BILL}"


def test_qualifier_lets_one_subject_carry_several_of_a_type():
    """Three unmatched line items on one bill are three findings, not one that
    keeps overwriting itself."""
    a = _draft(key_qualifier="line-1").finding_key
    b = _draft(key_qualifier="line-2").finding_key
    assert a != b and a.endswith(":line-1")


def test_subjectless_findings_still_get_a_key():
    assert _draft(subject_type="tenant", subject_id=None).finding_key.endswith(":tenant:-")


def test_condition_hash_ignores_ordering_of_identical_input():
    assert condition_hash("a", 1, None) == condition_hash("a", 1, None)
    assert condition_hash("a", 1) != condition_hash("a", 2)


def test_condition_hash_tolerates_unserialisable_values():
    """Adapters pass DB values straight in; a Decimal or a date must not raise
    halfway through a pass."""
    assert condition_hash(Decimal("1.50"), NOW, None)


# ── Draft validation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    {"family": "not_a_family"},
    {"subject_type": "spaceship"},
    {"severity": "SEVERE"},
    {"verification": "MAYBE"},
    {"required_capability": "superuser"},
])
def test_invalid_drafts_are_rejected_at_construction(bad):
    """A bad enum value must fail where it is written, not as a Postgres CHECK
    violation that aborts the whole adapter pass."""
    with pytest.raises(ValueError):
        _draft(**bad)


def test_valid_families_all_construct():
    from backend_ocr.app.review.types import FAMILIES
    for fam in FAMILIES:
        assert _draft(family=fam).family == fam


# ── Upsert ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_targets_the_partial_index():
    """`WHERE resolved_at IS NULL` on the conflict target is what makes a
    re-detection after a human resolution insert a NEW row — a real regression
    with its own first_detected_at — rather than resurrecting the old one."""
    c = _conn(fetchrow={"finding_id": "f1", "inserted": True})
    await upsert_finding(c, _draft(), now=NOW)
    sql = c.fetchrow.call_args[0][0]
    assert "ON CONFLICT (tenant_id, finding_key) WHERE resolved_at IS NULL" in sql


@pytest.mark.asyncio
async def test_unchanged_condition_leaves_an_acknowledgement_alone():
    """Otherwise every reconcile pass would un-acknowledge everything the user
    just triaged, which would make the hub unusable within a day."""
    c = _conn(fetchrow={"finding_id": "f1", "inserted": False})
    await upsert_finding(c, _draft(), now=NOW)
    sql = " ".join(c.fetchrow.call_args[0][0].split())
    assert "IS DISTINCT FROM EXCLUDED.condition_hash" in sql
    assert "occurrence_count = rh_findings.occurrence_count" in sql


@pytest.mark.asyncio
async def test_insert_records_a_detected_event_but_an_update_does_not():
    fresh = _conn(fetchrow={"finding_id": "f1", "inserted": True})
    await upsert_finding(fresh, _draft(), now=NOW)
    assert any("rh_finding_events" in str(c[0][0]) for c in fresh.execute.call_args_list)

    repeat = _conn(fetchrow={"finding_id": "f1", "inserted": False})
    await upsert_finding(repeat, _draft(), now=NOW)
    assert not any("rh_finding_events" in str(c[0][0]) for c in repeat.execute.call_args_list)


@pytest.mark.asyncio
async def test_entities_are_written_when_present():
    c = _conn(fetchrow={"finding_id": "f1", "inserted": True})
    await upsert_finding(
        c, _draft(entities=(("vendor", "v1", "counterparty", "Acme Ltd"),)), now=NOW
    )
    c.executemany.assert_awaited()


@pytest.mark.asyncio
async def test_rank_is_computed_server_side_not_taken_from_the_draft():
    """FindingDraft carries no rank on purpose: ordering is the store's job, so a
    detector cannot promote its own findings up the queue."""
    assert not hasattr(_draft(), "rank_score")
    c = _conn(fetchrow={"finding_id": "f1", "inserted": True})
    await upsert_finding(c, _draft(), now=NOW)
    assert c.fetchrow.call_args[0][15] > 0   # rank_score positional


# ── Auto-resolve sweep ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sweep_never_overwrites_a_human_resolution():
    c = _conn(fetch=[])
    await sweep_autoresolve(c, TENANT, "adapter:test", NOW)
    sql = " ".join(c.fetch.call_args[0][0].split())
    assert "status_by IS NULL" in sql
    assert "last_seen_at <" in sql
    assert "resolved_at IS NULL" in sql


@pytest.mark.asyncio
async def test_sweep_is_scoped_to_one_detector():
    """A detector must only be able to close its own findings, or one broken
    adapter silently clears another's work."""
    c = _conn(fetch=[])
    await sweep_autoresolve(c, TENANT, "adapter:test", NOW)
    assert "detector = $2" in " ".join(c.fetch.call_args[0][0].split())


@pytest.mark.asyncio
async def test_sweep_logs_an_event_per_closed_finding():
    c = _conn(fetch=[{"finding_id": "f1"}, {"finding_id": "f2"}])
    n = await sweep_autoresolve(c, TENANT, "adapter:test", NOW)
    assert n == 2
    assert c.execute.await_count == 2


# ── Suppression ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suppressed_draft_is_dropped_before_it_is_written():
    """A mute has to stop the row existing, not just stop it rendering — the
    header promises "N more you can see", and that number must be true."""
    c = _conn(fetchrow={"scope": "type_tenant_wide"})
    assert await apply_suppression(c, _draft()) is None


@pytest.mark.asyncio
async def test_unsuppressed_draft_passes_through_unchanged():
    c = _conn(fetchrow=None)
    d = _draft()
    assert await apply_suppression(c, d) is d


@pytest.mark.asyncio
async def test_suppression_lookup_honours_expiry():
    c = _conn(fetchrow=None)
    await apply_suppression(c, _draft())
    assert "until IS NULL OR until > now()" in c.fetchrow.call_args[0][0]


# ── Events ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_write_failure_never_breaks_the_caller():
    """Same contract as BaseWorker.log_audit: an audit write must not fail the
    operation it is describing."""
    c = _conn()
    c.execute = AsyncMock(side_effect=RuntimeError("db gone"))
    await record_event(c, "f1", TENANT, "DETECTED")   # must not raise


# ── Evidence shapes ───────────────────────────────────────────────────────────

def test_evidence_builders_declare_a_kind():
    """The frontend dispatches on `kind` and falls back to a generic renderer;
    a payload without one renders as a JSON dump."""
    assert diff_evidence("a", "b", [("f", "1", "2")])["kind"] == "diff"
    assert fields_evidence([("a", 1)])["kind"] == "fields"


def test_action_defaults_to_a_deep_link_not_an_executable_write():
    """Safe is opt-in. A detector author who forgets to think about it gets a
    link, not a button that writes."""
    assert action("a", "Label")["kind"] == "navigate"
