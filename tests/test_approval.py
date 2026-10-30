"""Unit tests for pipelines/approval.py — asyncpg fully mocked."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_conn(fetch=None, fetchval=None, execute_return="UPDATE 1"):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=execute_return)
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return conn


def _txn_row(**overrides):
    base = {
        "txn_id": "txn-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "paid_amount": 10000.0,
    }
    base.update(overrides)
    row = MagicMock()
    row.__getitem__ = lambda self, k: base[k]
    row.get = lambda k, default=None: base.get(k, default)
    return row


class TestAutoApproveWritesAuditLog:
    """C10: auto-approve must write wf_audit_log with user_id=NULL."""

    @pytest.mark.asyncio
    async def test_audit_log_written(self):
        from pipelines.approval import _auto_approve_small_payments

        row = _txn_row(txn_id="txn-audit", paid_amount=1000.0, tenant_id="tenant-1")
        conn = _make_conn(fetch=[row])

        await _auto_approve_small_payments(conn, batch_size=50, default_threshold=50000.0)

        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("wf_audit_log" in c for c in calls), (
            "Expected INSERT into wf_audit_log; calls: " + str(calls)
        )
        assert any("payment_transactions" in c for c in calls)

    @pytest.mark.asyncio
    async def test_null_user_id_in_audit_log(self):
        from pipelines.approval import _auto_approve_small_payments

        row = _txn_row()
        conn = _make_conn(fetch=[row])
        await _auto_approve_small_payments(conn, batch_size=50, default_threshold=50000.0)

        # user_id is hardcoded as NULL in the SQL (not a parameter)
        # Verify the SQL text explicitly contains NULL for user_id
        for c in conn.execute.call_args_list:
            args = c.args if hasattr(c, "args") else c[0]
            if args and "wf_audit_log" in str(args[0]):
                assert "NULL" in str(args[0]), (
                    "SQL must have NULL as the user_id literal"
                )
                return
        pytest.fail("wf_audit_log INSERT not found")

    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        from pipelines.approval import _auto_approve_small_payments

        conn = _make_conn(fetch=[])
        result = await _auto_approve_small_payments(conn, batch_size=50, default_threshold=50000.0)
        assert result == 0
        conn.execute.assert_not_called()


class TestEnsureWfTask:
    @pytest.mark.asyncio
    async def test_creates_new_task_when_none_exists(self):
        from pipelines.approval import _ensure_wf_task
        from models.enums import WfEntityType, WfTaskType

        conn = _make_conn(fetchval=None)
        conn.fetchval = AsyncMock(side_effect=[None, "new-task-id"])

        task_id = await _ensure_wf_task(
            conn,
            tenant_id="t1",
            task_type=WfTaskType.NUDGE,
            entity_type=WfEntityType.INVOICE,
            entity_id="inv-1",
        )
        assert task_id == "new-task-id"

    @pytest.mark.asyncio
    async def test_skips_when_task_exists(self):
        from pipelines.approval import _ensure_wf_task
        from models.enums import WfEntityType, WfTaskType

        conn = _make_conn()
        conn.fetchval = AsyncMock(return_value="existing-task")

        task_id = await _ensure_wf_task(
            conn,
            tenant_id="t1",
            task_type=WfTaskType.NUDGE,
            entity_type=WfEntityType.INVOICE,
            entity_id="inv-1",
        )
        assert task_id is None


class TestQueueVendorOutreachReminders:
    """_queue_vendor_outreach_reminders inserts follow-up rows for overdue outreach."""

    def _outreach_row(self, **overrides):
        from uuid import uuid4
        data = {
            "id": 1,
            "supplier_id": str(uuid4()),
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "requested_fields": ["pan", "bank_details"],
            "email_to": "vendor@example.com",
            "subject": "Please provide info",
            "body": "Dear vendor,",
            "sent_at": datetime(2026, 5, 10, tzinfo=timezone.utc),
            "followup_days": 3,
        }
        data.update(overrides)
        row = MagicMock()
        row.__getitem__ = lambda self, k: data[k]
        return row

    @pytest.mark.asyncio
    async def test_inserts_reminder_and_returns_count(self):
        from pipelines.approval import _queue_vendor_outreach_reminders

        conn = _make_conn(fetch=[self._outreach_row()])

        with patch("pipelines.approval._audit", new_callable=AsyncMock):
            result = await _queue_vendor_outreach_reminders(conn, batch_size=50)

        assert result == 1
        sql_calls = [str(c) for c in conn.execute.call_args_list]
        assert any(
            "vendor_outreach_log" in c and "system_reminder" in c for c in sql_calls
        ), "Expected INSERT into vendor_outreach_log with system_reminder; calls: " + str(sql_calls)

    @pytest.mark.asyncio
    async def test_returns_zero_on_empty_batch(self):
        from pipelines.approval import _queue_vendor_outreach_reminders

        conn = _make_conn(fetch=[])

        with patch("pipelines.approval._audit", new_callable=AsyncMock):
            result = await _queue_vendor_outreach_reminders(conn, batch_size=50)

        assert result == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_writes_audit_log_per_row(self):
        from pipelines.approval import _queue_vendor_outreach_reminders

        conn = _make_conn(fetch=[self._outreach_row(), self._outreach_row(id=2)])

        with patch("pipelines.approval._audit", new_callable=AsyncMock) as mock_audit:
            await _queue_vendor_outreach_reminders(conn, batch_size=50)

        assert mock_audit.call_count == 2
        for call in mock_audit.call_args_list:
            assert call.kwargs.get("table_name") == "vendor_outreach_log"
            assert call.kwargs.get("action") == "INSERT"

    @pytest.mark.asyncio
    async def test_idempotency_guard_in_query(self):
        from pipelines.approval import _queue_vendor_outreach_reminders

        conn = _make_conn(fetch=[])

        with patch("pipelines.approval._audit", new_callable=AsyncMock):
            await _queue_vendor_outreach_reminders(conn, batch_size=50)

        fetch_sql = str(conn.fetch.call_args)
        assert "NOT EXISTS" in fetch_sql
        assert "system_reminder" in fetch_sql
