"""backend_ocr/app/review/adapters/comm_stall.py — a thread stuck on the vendor's side."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters.comm_stall import CommStallAdapter, DEFAULT_STALL_DAYS
from backend_ocr.app.review.types import EVIDENCE_KINDS, FAMILIES, SUBJECT_TYPES

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
THREAD = "thread-abc123"
NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)


def _conn(thread_rows, config_row=None):
    c = AsyncMock()
    c.fetchrow = AsyncMock(return_value=config_row)
    c.fetch = AsyncMock(return_value=thread_rows)
    c.execute = AsyncMock()
    return c


async def _drain(conn, limit=100):
    return [d async for d in CommStallAdapter().scan(conn, TENANT, limit)]


def _thread_row(**over):
    base = dict(
        thread_id=THREAD, supplier_id=VENDOR, contact_id="contact-1",
        purpose="bill_followup",
        last_message_at=NOW - timedelta(days=DEFAULT_STALL_DAYS + 2),
        vendor_name="Acme Traders", contact_name="Priya (Acme)",
    )
    base.update(over)
    return base


def test_declares_a_real_service_and_family():
    a = CommStallAdapter()
    from backend_ocr.app.security.services import SERVICE_KEYS
    assert a.source_service in SERVICE_KEYS
    assert a.family in FAMILIES


@pytest.mark.asyncio
async def test_no_stalled_threads_produces_nothing():
    out = await _drain(_conn([]))
    assert out == []


@pytest.mark.asyncio
async def test_a_stalled_thread_is_flagged():
    out = await _drain(_conn([_thread_row()]))
    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "comm_thread_stalled"
    assert f.subject_type in SUBJECT_TYPES
    assert f.subject_type == "vendor"
    assert f.subject_id == VENDOR
    assert f.evidence["kind"] in EVIDENCE_KINDS
    assert f.key_qualifier == THREAD
    assert "Acme Traders" in f.title


@pytest.mark.asyncio
async def test_notify_action_offered_when_a_contact_is_known():
    out = await _drain(_conn([_thread_row()]))
    f = out[0]
    kinds = [a["kind"] for a in f.actions]
    assert "notify" in kinds
    notify = next(a for a in f.actions if a["kind"] == "notify")
    assert notify["endpoint"] == "/api/review/act/notify"
    assert notify["body"]["destination_type"] == "vendor_contact"
    assert notify["body"]["contact_id"] == "contact-1"
    assert notify["confirm"] is not None


@pytest.mark.asyncio
async def test_no_notify_action_without_a_contact():
    out = await _drain(_conn([_thread_row(contact_id=None)]))
    f = out[0]
    kinds = [a["kind"] for a in f.actions]
    assert "notify" not in kinds
    # Still reachable — never silently unreachable just because nobody's
    # attributed to the thread.
    assert any(a["kind"] == "navigate" for a in f.actions)


@pytest.mark.asyncio
async def test_default_stall_days_used_when_no_policy_row():
    conn = _conn([_thread_row()], config_row=None)
    await _drain(conn)
    # The SQL interval param is the second positional bind.
    args = conn.fetch.await_args.args
    assert args[2] == str(DEFAULT_STALL_DAYS)


@pytest.mark.asyncio
async def test_policy_row_overrides_the_default_stall_days():
    import json
    conn = _conn([_thread_row()], config_row={"config": json.dumps({"stall_days": 10})})
    await _drain(conn)
    args = conn.fetch.await_args.args
    assert args[2] == "10"


@pytest.mark.asyncio
async def test_malformed_policy_falls_back_to_default():
    conn = _conn([_thread_row()], config_row={"config": {"stall_days": "not-a-number"}})
    await _drain(conn)
    args = conn.fetch.await_args.args
    assert args[2] == str(DEFAULT_STALL_DAYS)


@pytest.mark.asyncio
async def test_purpose_shapes_the_message_when_not_general():
    out = await _drain(_conn([_thread_row(purpose="contract_request")]))
    notify = next(a for a in out[0].actions if a["kind"] == "notify")
    assert "contract request" in notify["body"]["subject"].lower()


@pytest.mark.asyncio
async def test_general_purpose_uses_a_generic_subject():
    out = await _drain(_conn([_thread_row(purpose="general")]))
    notify = next(a for a in out[0].actions if a["kind"] == "notify")
    assert notify["body"]["subject"] == "Following up on our last message"
