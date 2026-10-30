"""Unit tests for pipelines/tally_sync.py — Tally HTTP calls fully mocked."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_for_tally(connections=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=connections or [])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool, conn


class TestCircuitBreaker:
    """C6: after 3 consecutive failures the connection status is set to ERROR."""

    @pytest.mark.asyncio
    async def test_marks_error_after_three_failures(self):
        from datetime import datetime, timezone, timedelta

        from pipelines.tally_sync import TallySyncWorker

        conn_row = MagicMock()
        conn_row.__getitem__ = lambda self, k: {
            "connection_id": "conn-1",
            "tenant_id": "tenant-1",
            "last_sync_at": datetime.now(timezone.utc) - timedelta(hours=2),
        }[k]

        pool, db_conn = _make_pool_for_tally(connections=[conn_row])

        worker = TallySyncWorker()
        worker._consec_failures = {"conn-1": 2}  # already 2 failures

        with patch("pipelines.tally_sync.get_pool", return_value=pool), \
             patch("pipelines.tally_sync._pull_ledgers", side_effect=ConnectionError("tally down")), \
             patch("pipelines.tally_sync._warn_circuit_broken_pending", new=AsyncMock()):
            await worker.process_batch()

        # After 3rd failure, status='ERROR' must be set
        sql_calls = [str(c) for c in db_conn.execute.call_args_list]
        assert any("status='ERROR'" in c or "ERROR" in c for c in sql_calls), (
            "Expected circuit breaker to set status=ERROR; calls: " + str(sql_calls)
        )

    @pytest.mark.asyncio
    async def test_resets_failure_count_on_success(self):
        from datetime import datetime, timezone, timedelta

        from pipelines.tally_sync import TallySyncWorker

        conn_row = MagicMock()
        conn_row.__getitem__ = lambda self, k: {
            "connection_id": "conn-2",
            "tenant_id": "tenant-2",
            "last_sync_at": datetime.now(timezone.utc) - timedelta(hours=2),
        }[k]

        pool, db_conn = _make_pool_for_tally(connections=[conn_row])

        worker = TallySyncWorker()
        worker._consec_failures = {"conn-2": 1}

        with patch("pipelines.tally_sync.get_pool", return_value=pool), \
             patch("pipelines.tally_sync._pull_ledgers", return_value=0), \
             patch("pipelines.tally_sync._pull_vouchers", return_value=0), \
             patch("pipelines.tally_sync._push_customer_ledgers", return_value=0), \
             patch("pipelines.tally_sync._push_invoices", return_value=0), \
             patch("pipelines.tally_sync._push_payments", return_value=0), \
             patch("pipelines.tally_sync._update_last_synced", return_value=None), \
             patch("pipelines.tally_sync._warn_circuit_broken_pending", new=AsyncMock()), \
             patch("pipelines.tally_sync._bridge_tally_push_flags", new=AsyncMock(return_value=0)):
            await worker.process_batch()

        assert worker._consec_failures.get("conn-2", 0) == 0


class TestNormalisers:
    def test_normalize_account_type_bank_to_asset(self):
        from pipelines.tally_sync import _normalize_account_type
        assert _normalize_account_type("BANK") == "ASSET"

    def test_normalize_account_type_unknown_falls_back(self):
        from pipelines.tally_sync import _normalize_account_type
        assert _normalize_account_type("WEIRDTYPE") == "ASSET"

    def test_normalize_payment_status_completed_to_cleared(self):
        from pipelines.tally_sync import _normalize_payment_status
        assert _normalize_payment_status("COMPLETED") == "CLEARED"

    def test_is_success_response_detects_errors(self):
        from pipelines.tally_sync import _is_success_response
        assert not _is_success_response("<STATUS>0</STATUS>")
        assert not _is_success_response("UNKNOWN REQUEST")
        assert _is_success_response("<STATUS>1</STATUS>")


class TestBridgeTallyPushFlags:
    """_bridge_tally_push_flags propagates fact_invoices.tally_push_pending to stg_invoices."""

    @pytest.mark.asyncio
    async def test_returns_count_from_execute_result(self):
        from pipelines.tally_sync import _bridge_tally_push_flags

        pool, conn = _make_pool_for_tally()
        conn.execute = AsyncMock(return_value="UPDATE 4")

        with patch("pipelines.tally_sync.write_sync_event"):
            count = await _bridge_tally_push_flags(pool, "tenant-1", "conn-1")

        assert count == 4

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_rows_updated(self):
        from pipelines.tally_sync import _bridge_tally_push_flags

        pool, conn = _make_pool_for_tally()
        conn.execute = AsyncMock(return_value="UPDATE 0")

        with patch("pipelines.tally_sync.write_sync_event"):
            count = await _bridge_tally_push_flags(pool, "tenant-1", "conn-1")

        assert count == 0

    @pytest.mark.asyncio
    async def test_emits_bridge_flags_sync_event(self):
        from pipelines.tally_sync import _bridge_tally_push_flags

        pool, conn = _make_pool_for_tally()
        conn.execute = AsyncMock(return_value="UPDATE 2")

        with patch("pipelines.tally_sync.write_sync_event") as mock_evt:
            await _bridge_tally_push_flags(pool, "tenant-1", "conn-1")

        actions = [c.kwargs.get("action") for c in mock_evt.call_args_list]
        assert "BRIDGE_FLAGS" in actions

    @pytest.mark.asyncio
    async def test_passes_tenant_id_to_execute(self):
        from pipelines.tally_sync import _bridge_tally_push_flags

        pool, conn = _make_pool_for_tally()
        conn.execute = AsyncMock(return_value="UPDATE 1")

        with patch("pipelines.tally_sync.write_sync_event"):
            await _bridge_tally_push_flags(pool, "tenant-99", "conn-1")

        sql_calls = [str(c) for c in conn.execute.call_args_list]
        assert any("tenant-99" in c for c in sql_calls)


class TestWarnCircuitBrokenPending:
    """_warn_circuit_broken_pending logs ERROR connections that have pending bills."""

    def _error_conn_row(self, **overrides):
        data = {
            "connection_id": "conn-err",
            "tenant_id": "tenant-1",
            "error_message": "connection refused",
            "pending_count": 3,
        }
        data.update(overrides)
        row = MagicMock()
        row.__getitem__ = lambda self, k: data[k]
        return row

    @pytest.mark.asyncio
    async def test_emits_circuit_broken_skip_event(self):
        from pipelines.tally_sync import _warn_circuit_broken_pending

        pool, conn = _make_pool_for_tally()
        conn.fetch = AsyncMock(return_value=[self._error_conn_row()])

        with patch("pipelines.tally_sync.write_sync_event") as mock_evt:
            await _warn_circuit_broken_pending(pool)

        actions = [c.kwargs.get("action") for c in mock_evt.call_args_list]
        assert "CIRCUIT_BROKEN_SKIP" in actions

    @pytest.mark.asyncio
    async def test_no_events_when_no_error_connections(self):
        from pipelines.tally_sync import _warn_circuit_broken_pending

        pool, conn = _make_pool_for_tally()
        conn.fetch = AsyncMock(return_value=[])

        with patch("pipelines.tally_sync.write_sync_event") as mock_evt:
            await _warn_circuit_broken_pending(pool)

        assert mock_evt.call_count == 0

    @pytest.mark.asyncio
    async def test_emits_one_event_per_error_connection(self):
        from pipelines.tally_sync import _warn_circuit_broken_pending

        pool, conn = _make_pool_for_tally()
        conn.fetch = AsyncMock(return_value=[
            self._error_conn_row(connection_id="conn-a"),
            self._error_conn_row(connection_id="conn-b"),
        ])

        with patch("pipelines.tally_sync.write_sync_event") as mock_evt:
            await _warn_circuit_broken_pending(pool)

        skip_events = [c for c in mock_evt.call_args_list
                       if c.kwargs.get("action") == "CIRCUIT_BROKEN_SKIP"]
        assert len(skip_events) == 2


class TestPushInvoicesClearsPending:
    """_push_invoices clears fact_invoices.tally_push_pending only on SYNCED."""

    def _inv_row(self):
        from datetime import date as _date
        data = {
            "stg_invoice_id": "stg-001",
            "invoice_number": "INV-001",
            "invoice_date": _date.today(),
            "total_amount": 1000.0,
            "tax_amount": 100.0,
            "narration": "",
            "company_name": "Test Co",
            "lines": [],
        }
        row = MagicMock()
        row.__getitem__ = lambda self, k: data[k]
        return row

    @pytest.mark.asyncio
    async def test_clears_tally_push_pending_on_synced(self):
        from pipelines.tally_sync import _push_invoices

        pool, conn = _make_pool_for_tally(connections=[self._inv_row()])

        with patch("pipelines.tally_sync._in_thread",
                   new=AsyncMock(return_value="<STATUS>1</STATUS>")), \
             patch("pipelines.tally_sync.write_sync_event"), \
             patch("pipelines.tally_sync.build_sales_voucher", return_value="<xml/>"):
            await _push_invoices(pool, "tenant-1", "conn-1")

        sql_calls = [str(c) for c in conn.execute.call_args_list]
        assert any("tally_push_pending" in c for c in sql_calls), (
            "Expected UPDATE fact_invoices SET tally_push_pending=false; calls: " + str(sql_calls)
        )

    @pytest.mark.asyncio
    async def test_does_not_clear_tally_push_pending_on_error(self):
        from pipelines.tally_sync import _push_invoices

        pool, conn = _make_pool_for_tally(connections=[self._inv_row()])

        with patch("pipelines.tally_sync._in_thread",
                   new=AsyncMock(return_value="<STATUS>0</STATUS>")), \
             patch("pipelines.tally_sync.write_sync_event"), \
             patch("pipelines.tally_sync.build_sales_voucher", return_value="<xml/>"):
            await _push_invoices(pool, "tenant-1", "conn-1")

        sql_calls = [str(c) for c in conn.execute.call_args_list]
        assert not any("tally_push_pending" in c for c in sql_calls), (
            "tally_push_pending must NOT be cleared on ERROR; calls: " + str(sql_calls)
        )
