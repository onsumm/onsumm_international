"""Unit tests for pipelines/staging_promotion.py — asyncpg fully mocked."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ── Row helper ─────────────────────────────────────────────────────────────────
# _R is a dict subclass so that dict(row) returns the correct field values.
# _convert_currency calls dict(row) internally; a plain MagicMock would give {}.

class _R(dict):
    """Minimal asyncpg.Record look-alike: a plain dict with all fields."""


def _make_conn(fetchval=None, fetch=None, fetchrow=None):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="INSERT 1")
    conn.fetchval = AsyncMock(return_value=fetchval)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return conn


def _make_pool(conn):
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool


def _invoice_row(**overrides):
    base = {
        "stg_invoice_id": "stg-inv-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "source": "ZOHO",
        "source_updated_at": datetime.now(timezone.utc),
        "end_customer_id": "cust-1",
        "account_id": None,
        "invoice_number": "INV-001",
        "invoice_date": date(2025, 6, 1),
        "due_date": date(2025, 7, 1),
        "currency_code": "INR",
        "subtotal": 10000,
        "tax_amount": 1800,
        "total_amount": 11800,
        "amount_paid": 0,
        "outstanding_amount": 11800,
        "status": "SENT",
    }
    base.update(overrides)
    return _R(base)


class TestPromoteInvoice:
    @pytest.mark.asyncio
    async def test_promote_invoice_calls_upsert(self):
        from pipelines.staging_promotion import _promote_invoice

        conn = _make_conn(fetchval="risk-medium")
        row = _invoice_row()
        await _promote_invoice(conn, row)

        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_invoices" in c for c in calls)
        assert any("SYNCED" in c for c in calls)


class TestPromoteBill:
    @pytest.mark.asyncio
    async def test_promote_bill_writes_to_fact_bills(self):
        """C4: bills must go to fact_bills, not fact_invoices."""
        from pipelines.staging_promotion import _promote_bill

        conn = _make_conn()
        row = _R({
            "stg_bill_id":      "stg-bill-1",
            "tenant_id":        "11111111-1111-1111-1111-111111111111",
            "supplier_id":      "sup-1",
            "bill_number":      "BILL-001",
            "bill_date":        date(2025, 6, 1),
            "due_date":         date(2025, 7, 1),
            "currency_code":    "INR",
            "subtotal":         5000,
            "tax_amount":       900,
            "total_amount":     5900,
            "amount_paid":      0,
            "outstanding_amount": 5900,
            "status":           "SENT",
            "source":           "ZOHO",
            "source_updated_at": datetime.now(timezone.utc),
        })

        await _promote_bill(conn, row)

        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_bills" in c for c in calls), (
            "Expected INSERT into fact_bills, but calls were: " + str(calls)
        )
        assert not any("fact_invoices" in c for c in calls), (
            "Bills must NOT write to fact_invoices"
        )


class TestCashflowDecoupled:
    """C13: StagingPromotionWorker must NOT call _upsert_cashflow_for_invoice."""

    def test_cashflow_function_not_imported(self):
        import pipelines.staging_promotion as mod
        assert not hasattr(mod, "_upsert_cashflow_for_invoice"), (
            "_upsert_cashflow_for_invoice should have been removed from staging_promotion"
        )
