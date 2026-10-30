"""Unit tests for pipelines/webhook.py — asyncpg fully mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool(rows=None, fetchval_return=None):
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=fetchval_return)
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


def _make_row(**kwargs):
    row = MagicMock()
    row.__getitem__ = lambda self, k: kwargs[k]
    row.get = lambda k, default=None: kwargs.get(k, default)
    return row


class TestWebhookWorkerTenantGuard:
    """C5: NULL tenant_id must be quarantined immediately."""

    @pytest.mark.asyncio
    async def test_null_tenant_id_quarantined(self):
        from pipelines.webhook import WebhookWorker

        row = _make_row(
            event_id="evt-1",
            tenant_id=None,
            source="RAZORPAY",
            event_type="payment.captured",
            raw_payload=json.dumps({"payment": {"entity": {"id": "pay_123"}}}),
            received_at=None,
        )

        pool, conn = _make_pool(rows=[row])
        with patch("pipelines.webhook.get_pool", return_value=pool):
            worker = WebhookWorker()
            result = await worker.process_batch()

        assert result == 0
        update_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "is_processed=true" in update_sql or "no tenant_id" in update_sql


class TestWebhookWorkerRetryCount:
    """C7: errors increment retry_count."""

    @pytest.mark.asyncio
    async def test_handler_error_increments_retry(self):
        from pipelines.webhook import WebhookWorker
        from models.enums import DataSource

        row = _make_row(
            event_id="evt-2",
            tenant_id="11111111-1111-1111-1111-111111111111",
            source="RAZORPAY",
            event_type="payment.captured",
            raw_payload=json.dumps({"payment": {"entity": {"id": "pay_456"}}}),
            received_at=None,
        )

        pool, conn = _make_pool(rows=[row], fetchval_return=1)

        # Patch _SOURCE_HANDLERS so the handler raises
        failing_handler = AsyncMock(side_effect=RuntimeError("razorpay down"))
        with patch("pipelines.webhook.get_pool", return_value=pool), \
             patch("pipelines.webhook._SOURCE_HANDLERS",
                   {DataSource.RAZORPAY: failing_handler}):
            worker = WebhookWorker()
            result = await worker.process_batch()

        assert result == 0
        # retry_count increment uses fetchval (UPDATE ... RETURNING retry_count)
        fetchval_calls = [str(c) for c in conn.fetchval.call_args_list]
        assert any("retry_count" in a for a in fetchval_calls), (
            "retry_count fetchval expected; calls: " + str(fetchval_calls)
        )


class TestWebhookHelpers:
    def test_paise_to_rupees(self):
        from pipelines.webhook import _paise_to_rupees
        assert _paise_to_rupees(100000) == pytest.approx(1000.0)
        assert _paise_to_rupees(None) == 0.0
        assert _paise_to_rupees("abc") == 0.0

    def test_stripe_cents_to_units(self):
        from pipelines.webhook import _stripe_cents_to_units
        assert _stripe_cents_to_units(100000) == pytest.approx(1000.0)

    def test_ts_to_date(self):
        from datetime import date as dt
        from pipelines.webhook import _ts_to_date
        d = _ts_to_date(0)
        assert isinstance(d, dt)
