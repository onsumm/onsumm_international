"""Unit tests for pipelines/api_sync.py — asyncpg fully mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_pool(rows=None, fetchval_return=None):
    """Return a minimal mock asyncpg pool."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=fetchval_return)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.transaction = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn),
                                                        __aexit__=AsyncMock(return_value=False)))
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=conn),
                                                    __aexit__=AsyncMock(return_value=False)))
    return pool, conn


def _make_row(**kwargs):
    row = MagicMock()
    row.__getitem__ = lambda self, k: kwargs[k]
    row.get = lambda k, default=None: kwargs.get(k, default)
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


# ── ApiSyncWorker.process_batch ────────────────────────────────────────────────

class TestApiSyncWorkerTenantGuard:
    """C5: rows with NULL tenant_id must be quarantined without processing."""

    @pytest.mark.asyncio
    async def test_null_tenant_id_marks_processed_and_skips(self):
        from pipelines.api_sync import ApiSyncWorker

        null_row = _make_row(
            payload_id="payload-1",
            tenant_id=None,
            source="ZOHO",
            entity_type="INVOICE",
            raw_payload=json.dumps({"id": "inv-1"}),
            sync_job_id=None,
            received_at=None,
        )

        pool, conn = _make_pool(rows=[null_row])

        with patch("pipelines.api_sync.get_pool", return_value=pool):
            worker = ApiSyncWorker()
            result = await worker.process_batch()

        assert result == 0
        conn.execute.assert_called()
        call_args = [str(c) for c in conn.execute.call_args_list]
        assert any("is_processed=true" in a or "UPDATE" in a for a in call_args)


class TestApiSyncWorkerRetryCount:
    """C7: errors increment retry_count; quarantine only after 3 failures."""

    @pytest.mark.asyncio
    async def test_error_increments_retry_count(self):
        from pipelines.api_sync import ApiSyncWorker
        from models.enums import EntityType

        row = _make_row(
            payload_id="payload-2",
            tenant_id="11111111-1111-1111-1111-111111111111",
            source="ZOHO",
            entity_type="INVOICE",
            raw_payload=json.dumps({"id": "inv-2"}),
            sync_job_id=None,
            received_at=None,
        )

        pool, conn = _make_pool(rows=[row], fetchval_return=1)
        # Make the processing transaction raise, but allow retry_count update
        call_count = 0
        original_transaction = conn.transaction

        def failing_transaction():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First transaction (processing) — raise during __aenter__
                mock = MagicMock()
                mock.__aenter__ = AsyncMock(side_effect=ValueError("boom"))
                mock.__aexit__ = AsyncMock(return_value=False)
                return mock
            return original_transaction()

        conn.transaction = failing_transaction

        with patch("pipelines.api_sync.get_pool", return_value=pool):
            worker = ApiSyncWorker()
            result = await worker.process_batch()

        assert result == 0
        # retry_count increment uses fetchval (UPDATE ... RETURNING retry_count)
        fetchval_calls = [str(c) for c in conn.fetchval.call_args_list]
        assert any("retry_count" in a for a in fetchval_calls), (
            "retry_count fetchval expected; calls: " + str(fetchval_calls)
        )


class TestApiSyncNormalisers:
    """Normaliser functions — pure Python, no DB."""

    def test_normalise_apideck_invoice(self):
        from pipelines.api_sync import _normalise_apideck_invoice

        raw = {
            "id": "INV-001",
            "number": "2025-001",
            "date": "2025-04-01",
            "due_date": "2025-04-30",
            "sub_total": 10000,
            "tax_amount": 1800,
            "total": 11800,
            "balance": 11800,
            "currency": "INR",
            "status": "open",
            "customer": {"id": "CUST-1", "name": "Acme"},
        }
        result = _normalise_apideck_invoice(raw)
        assert result is not None
        assert result["source_invoice_id"] == "INV-001"
        assert result["total_amount"] == 11800
        assert result["outstanding_amount"] == 11800

    def test_normalise_apideck_invoice_no_id_returns_none(self):
        from pipelines.api_sync import _normalise_apideck_invoice
        assert _normalise_apideck_invoice({}) is None
