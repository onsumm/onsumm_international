"""Unit tests for pipelines/deduplication.py — asyncpg fully mocked.

Edge cases covered
──────────────────
Bill dedup:    exact-dup, cross-table, soft-dup, clean (no-op), multiple dups at once
Invoice dedup: exact-dup, cross-table, soft-dup, clean (no-op)
Credit recalc: fully-paid, partial-paid, zero-paid, over-paid (outstanding clamped to 0)
Failed txns:   FAILED AP, BOUNCED AR, CANCELLED+REVERSED, no failures (no-op)
Anomaly upsert: new insert, idempotent skip, different type on same entity
Flag anomalies: each of 6 anomaly types, idempotent cycle, all 6 in one cycle
Worker smoke:  class exists, constants sane
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_conn(**kwargs):
    """
    Build an AsyncMock asyncpg Connection.

    Keyword args:
        fetch_side_effect: list of per-call return values for conn.fetch()
        fetch:             single return value for all conn.fetch() calls
        fetchval:          return value for conn.fetchval()
        fetchrow:          return value for conn.fetchrow()
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow = AsyncMock(return_value=kwargs.get("fetchrow"))
    conn.fetchval = AsyncMock(return_value=kwargs.get("fetchval"))
    if "fetch_side_effect" in kwargs:
        conn.fetch = AsyncMock(side_effect=kwargs["fetch_side_effect"])
    else:
        conn.fetch = AsyncMock(return_value=kwargs.get("fetch", []))
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return conn


def _row(**fields):
    r = MagicMock()
    r.__getitem__ = lambda self, k: fields[k]
    r.get = lambda k, default=None: fields.get(k, default)
    return r


# ── Shared test constants ──────────────────────────────────────────────────────

TENANT   = "10000000-0000-0000-0000-000000000001"
SUP1     = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CUST1    = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
STG_B1   = "11111111-0000-0000-0000-000000000001"
STG_B2   = "11111111-0000-0000-0000-000000000002"
STG_I1   = "22222222-0000-0000-0000-000000000001"
STG_I2   = "22222222-0000-0000-0000-000000000002"
BILL_ID  = "33333333-0000-0000-0000-000000000001"
INV_ID   = "44444444-0000-0000-0000-000000000001"
SCHED_ID = "55555555-0000-0000-0000-000000000001"


# ── Bill deduplication ─────────────────────────────────────────────────────────

class TestDedupBills:

    @pytest.mark.asyncio
    async def test_exact_dup_bill_flagged_duplicate(self):
        """ROW_NUMBER > 1 row → UPDATE stg_bills SET validation_flag='DUPLICATE', sync_status='ERROR'."""
        from pipelines.deduplication import _dedup_bills

        dup_row = _row(stg_bill_id=STG_B2)
        conn = _make_conn(fetch_side_effect=[[dup_row], [], []])

        count = await _dedup_bills(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("DUPLICATE" in s for s in sqls)
        assert any("ERROR" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_cross_table_bill_flagged_duplicate(self):
        """stg_bill matching a fact_bill from different stg_bill_id → DUPLICATE."""
        from pipelines.deduplication import _dedup_bills

        cross_row = _row(stg_bill_id=STG_B1)
        conn = _make_conn(fetch_side_effect=[[], [cross_row], []])

        count = await _dedup_bills(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("DUPLICATE" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_soft_dup_bill_flagged_review(self):
        """Same supplier + approx amount + ≤7 days + different bill_number → REVIEW (not ERROR)."""
        from pipelines.deduplication import _dedup_bills

        soft_row = _row(stg_bill_id=STG_B2)
        conn = _make_conn(fetch_side_effect=[[], [], [soft_row]])

        count = await _dedup_bills(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("REVIEW" in s for s in sqls)
        assert not any("ERROR" in s for s in sqls), "Soft dup must not set sync_status=ERROR"

    @pytest.mark.asyncio
    async def test_clean_bills_not_touched(self):
        """No duplicates → no UPDATE calls at all."""
        from pipelines.deduplication import _dedup_bills

        conn = _make_conn(fetch_side_effect=[[], [], []])
        count = await _dedup_bills(conn, TENANT)

        assert count == 0
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_exact_dups_each_flagged(self):
        """Three exact-dup rows → UPDATE + audit INSERT per bill = 6 execute calls."""
        from pipelines.deduplication import _dedup_bills

        dup_ids = [
            "aaaa0000-0000-0000-0000-000000000001",
            "aaaa0000-0000-0000-0000-000000000002",
            "aaaa0000-0000-0000-0000-000000000003",
        ]
        rows = [_row(stg_bill_id=d) for d in dup_ids]
        conn = _make_conn(fetch_side_effect=[rows, [], []])

        count = await _dedup_bills(conn, TENANT)

        assert count == 3
        assert conn.execute.call_count == 6

    @pytest.mark.asyncio
    async def test_all_three_dedup_types_in_one_cycle(self):
        """Exact + cross-table + soft all flagged in a single call."""
        from pipelines.deduplication import _dedup_bills

        conn = _make_conn(fetch_side_effect=[
            [_row(stg_bill_id=STG_B1)],   # exact
            [_row(stg_bill_id=STG_B2)],   # cross-table
            [_row(stg_bill_id="soft-1")], # soft
        ])

        count = await _dedup_bills(conn, TENANT)
        assert count == 3


# ── Invoice deduplication ──────────────────────────────────────────────────────

class TestDedupInvoices:

    @pytest.mark.asyncio
    async def test_exact_dup_invoice_flagged(self):
        from pipelines.deduplication import _dedup_invoices

        dup_row = _row(stg_invoice_id=STG_I2)
        conn = _make_conn(fetch_side_effect=[[dup_row], [], []])

        count = await _dedup_invoices(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("DUPLICATE" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_cross_table_invoice_flagged(self):
        from pipelines.deduplication import _dedup_invoices

        conn = _make_conn(fetch_side_effect=[[], [_row(stg_invoice_id=STG_I1)], []])
        count = await _dedup_invoices(conn, TENANT)

        assert count == 1

    @pytest.mark.asyncio
    async def test_soft_dup_invoice_flagged_review(self):
        from pipelines.deduplication import _dedup_invoices

        soft_row = _row(stg_invoice_id=STG_I2)
        conn = _make_conn(fetch_side_effect=[[], [], [soft_row]])

        count = await _dedup_invoices(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("REVIEW" in s for s in sqls)
        assert not any("ERROR" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_clean_invoices_not_touched(self):
        from pipelines.deduplication import _dedup_invoices

        conn = _make_conn(fetch_side_effect=[[], [], []])
        count = await _dedup_invoices(conn, TENANT)

        assert count == 0
        conn.execute.assert_not_called()


# ── Credit recalculation (bills) ───────────────────────────────────────────────

class TestRecalcBillCredit:

    @pytest.mark.asyncio
    async def test_fully_paid_sets_paid_status(self):
        """SUM(COMPLETED) == total_amount → status=PAID, outstanding=0."""
        from pipelines.deduplication import _recalc_bill_credit

        conn = _make_conn(fetchval=10000)
        await _recalc_bill_credit(conn, BILL_ID, STG_B1, Decimal("10000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("PAID" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_partial_payment_sets_partial_status(self):
        """SUM(COMPLETED) < total_amount → status=PARTIAL."""
        from pipelines.deduplication import _recalc_bill_credit

        conn = _make_conn(fetchval=5000)
        await _recalc_bill_credit(conn, BILL_ID, STG_B1, Decimal("10000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("PARTIAL" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_zero_completed_txns_sets_sent_status(self):
        """No COMPLETED txns → status=SENT."""
        from pipelines.deduplication import _recalc_bill_credit

        conn = _make_conn(fetchval=0)
        await _recalc_bill_credit(conn, BILL_ID, STG_B1, Decimal("10000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("SENT" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_overpaid_outstanding_clamped_to_zero(self):
        """COMPLETED txns > total (overpayment) → outstanding_amount must not go negative."""
        from pipelines.deduplication import _recalc_bill_credit

        conn = _make_conn(fetchval=15000)
        await _recalc_bill_credit(conn, BILL_ID, STG_B1, Decimal("10000"))

        for c in conn.execute.call_args_list:
            args = c.args if hasattr(c, "args") else c[0]
            if args and "fact_bills" in str(args[0]):
                outstanding = args[3]  # 4th positional param
                assert outstanding == 0.0, f"outstanding must be 0, got {outstanding}"
                return
        pytest.fail("fact_bills UPDATE not found")

    @pytest.mark.asyncio
    async def test_recalc_writes_to_fact_bills_and_stg_bills(self):
        """Both fact_bills and stg_bills must be updated."""
        from pipelines.deduplication import _recalc_bill_credit

        conn = _make_conn(fetchval=3000)
        await _recalc_bill_credit(conn, BILL_ID, STG_B1, Decimal("10000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_bills" in s for s in sqls)
        assert any("stg_bills" in s for s in sqls)
        assert any("payment_schedules" in s for s in sqls)


# ── Credit recalculation (invoices) ───────────────────────────────────────────

class TestRecalcInvoiceCredit:

    @pytest.mark.asyncio
    async def test_bounced_ar_sets_sent_status(self):
        """After a bounce with no COMPLETED txns → status=SENT, outstanding=total."""
        from pipelines.deduplication import _recalc_invoice_credit

        conn = _make_conn(fetchval=0)
        await _recalc_invoice_credit(conn, INV_ID, STG_I1, Decimal("50000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_invoices" in s for s in sqls)
        assert any("SENT" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_one_completed_one_reversed_is_partial(self):
        """One payment COMPLETED (₹20k), one REVERSED → PARTIAL outstanding (₹30k)."""
        from pipelines.deduplication import _recalc_invoice_credit

        conn = _make_conn(fetchval=20000)
        await _recalc_invoice_credit(conn, INV_ID, STG_I1, Decimal("50000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("PARTIAL" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_recalc_writes_to_fact_invoices_and_schedules(self):
        from pipelines.deduplication import _recalc_invoice_credit

        conn = _make_conn(fetchval=0)
        await _recalc_invoice_credit(conn, INV_ID, STG_I1, Decimal("10000"))

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_invoices" in s for s in sqls)
        assert any("payment_schedules" in s for s in sqls)


# ── Failed payment reconciliation ─────────────────────────────────────────────

class TestReconcileFailedPayments:

    @pytest.mark.asyncio
    async def test_failed_ap_triggers_bill_credit_recalc(self):
        """FAILED payment on AP bill → fact_bills recalculated."""
        from pipelines.deduplication import _reconcile_failed_payments

        ap_row = _row(
            bill_id=BILL_ID, stg_bill_id=STG_B1,
            total_amount=Decimal("10000"),
            txn_status="FAILED",
            failure_reason="Insufficient funds",
            reference_number="TXN-001",
        )
        conn = _make_conn(fetch_side_effect=[[ap_row], []], fetchval=0)

        count = await _reconcile_failed_payments(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_bills" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_bounced_ar_triggers_invoice_credit_recalc(self):
        """BOUNCED receivable → fact_invoices recalculated."""
        from pipelines.deduplication import _reconcile_failed_payments

        ar_row = _row(
            invoice_id=INV_ID, stg_invoice_id=STG_I1,
            total_amount=Decimal("25000"),
            txn_status="BOUNCED",
            failure_reason="Account closed",
            reference_number="RCV-456",
        )
        conn = _make_conn(fetch_side_effect=[[], [ar_row]], fetchval=0)

        count = await _reconcile_failed_payments(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("fact_invoices" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_cancelled_and_reversed_both_caught(self):
        """CANCELLED and REVERSED are both in the failure status list."""
        from pipelines.deduplication import _reconcile_failed_payments

        def _ap(status):
            return _row(
                bill_id=BILL_ID, stg_bill_id=STG_B1,
                total_amount=Decimal("5000"),
                txn_status=status,
                failure_reason=None,
                reference_number=None,
            )

        conn = _make_conn(
            fetch_side_effect=[[_ap("CANCELLED"), _ap("REVERSED")], []],
            fetchval=0,
        )

        count = await _reconcile_failed_payments(conn, TENANT)
        assert count == 2

    @pytest.mark.asyncio
    async def test_no_failures_returns_zero(self):
        """No failed payments → no UPDATEs, count=0."""
        from pipelines.deduplication import _reconcile_failed_payments

        conn = _make_conn(fetch_side_effect=[[], []])
        count = await _reconcile_failed_payments(conn, TENANT)

        assert count == 0
        conn.execute.assert_not_called()


# ── Anomaly upsert (idempotency) ───────────────────────────────────────────────

class TestUpsertAnomaly:

    @pytest.mark.asyncio
    async def test_inserts_when_no_existing_anomaly(self):
        """No unresolved anomaly → INSERT into ai_anomalies, returns True."""
        from pipelines.deduplication import _upsert_anomaly

        conn = _make_conn(fetchval=None)
        result = await _upsert_anomaly(
            conn, TENANT, "BILL", STG_B1,
            "DUPLICATE_BILL", "HIGH", "Exact duplicate bill blocked.", {"bill_number": "B001"},
        )

        assert result is True
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("ai_anomalies" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_skips_when_unresolved_anomaly_exists(self):
        """Existing unresolved anomaly → skip INSERT, returns False."""
        from pipelines.deduplication import _upsert_anomaly

        conn = _make_conn(fetchval=1)
        result = await _upsert_anomaly(
            conn, TENANT, "BILL", STG_B1,
            "DUPLICATE_BILL", "HIGH", "Should not insert.", {},
        )

        assert result is False
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_different_anomaly_type_not_blocked(self):
        """An existing DUPLICATE_BILL does NOT block PAYMENT_FAILURE_AP for same entity."""
        from pipelines.deduplication import _upsert_anomaly

        conn = _make_conn(fetchval=None)  # no matching (type, entity) combo
        result = await _upsert_anomaly(
            conn, TENANT, "BILL", STG_B1,
            "PAYMENT_FAILURE_AP", "HIGH", "Payment bounced.", {},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_severity_high_for_exact_duplicates(self):
        """DUPLICATE_BILL must be inserted with severity=HIGH."""
        from pipelines.deduplication import _upsert_anomaly

        conn = _make_conn(fetchval=None)
        await _upsert_anomaly(
            conn, TENANT, "BILL", STG_B1,
            "DUPLICATE_BILL", "HIGH", "Dup", {},
        )

        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("HIGH" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_features_jsonb_serialised(self):
        """features dict must be serialised to JSON before insert."""
        from pipelines.deduplication import _upsert_anomaly
        import json

        conn = _make_conn(fetchval=None)
        features = {"bill_number": "B001", "total_amount": "10000"}
        await _upsert_anomaly(
            conn, TENANT, "BILL", STG_B1,
            "DUPLICATE_BILL", "HIGH", "Dup", features,
        )

        for c in conn.execute.call_args_list:
            args = c.args if hasattr(c, "args") else c[0]
            if args and "ai_anomalies" in str(args[0]):
                # Last positional param is json.dumps(features)
                json_param = args[-1]
                parsed = json.loads(json_param)
                assert parsed == features
                return
        pytest.fail("ai_anomalies INSERT not found")


# ── Flag anomalies (all 6 types) ───────────────────────────────────────────────

class TestFlagAnomalies:

    def _dup_bill(self):
        return _row(stg_bill_id=STG_B1, bill_number="B001",
                    total_amount=Decimal("10000"), supplier_id=SUP1)

    def _review_bill(self):
        return _row(stg_bill_id=STG_B2, bill_number="B002",
                    total_amount=Decimal("9999.50"), bill_date=date(2025, 1, 3))

    def _failed_ap(self):
        return _row(stg_bill_id=STG_B1, bill_number="B001",
                    txn_status="BOUNCED", failure_reason="NSF", reference_number="REF-1")

    def _dup_inv(self):
        return _row(stg_invoice_id=STG_I1, invoice_number="I001",
                    total_amount=Decimal("20000"))

    def _review_inv(self):
        return _row(stg_invoice_id=STG_I2, invoice_number="I002",
                    total_amount=Decimal("19999.50"), invoice_date=date(2025, 1, 4))

    def _failed_ar(self):
        return _row(stg_invoice_id=STG_I1, invoice_number="I001",
                    txn_status="FAILED", failure_reason="Closed", reference_number="REF-2")

    @pytest.mark.asyncio
    async def test_duplicate_bill_anomaly_written(self):
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[self._dup_bill()], [], [], [], [], []],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("DUPLICATE_BILL" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_soft_dup_bill_medium_severity(self):
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[], [self._review_bill()], [], [], [], []],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("MEDIUM" in s for s in sqls)
        assert any("SOFT_DUPLICATE_BILL" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_failed_ap_high_severity(self):
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[], [], [self._failed_ap()], [], [], []],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)

        assert count == 1
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("PAYMENT_FAILURE_AP" in s for s in sqls)
        assert any("HIGH" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_duplicate_invoice_anomaly_written(self):
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[], [], [], [self._dup_inv()], [], []],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("DUPLICATE_INVOICE" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_failed_ar_anomaly_written(self):
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[], [], [], [], [], [self._failed_ar()]],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)
        sqls = [str(c) for c in conn.execute.call_args_list]
        assert any("PAYMENT_FAILURE_AR" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_idempotent_cycle_returns_zero(self):
        """When anomalies already exist (fetchval=1), nothing new inserted."""
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[[self._dup_bill()], [], [], [], [], []],
            fetchval=1,  # existing unresolved anomaly
        )

        count = await _flag_anomalies(conn, TENANT)
        assert count == 0

    @pytest.mark.asyncio
    async def test_all_six_anomaly_types_in_one_cycle(self):
        """Full cycle: all 6 fetch queries each return one row → 6 anomalies inserted."""
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(
            fetch_side_effect=[
                [self._dup_bill()],
                [self._review_bill()],
                [self._failed_ap()],
                [self._dup_inv()],
                [self._review_inv()],
                [self._failed_ar()],
            ],
            fetchval=None,
        )

        count = await _flag_anomalies(conn, TENANT)
        assert count == 6

    @pytest.mark.asyncio
    async def test_empty_cycle_returns_zero(self):
        """No flagged records → zero anomalies, no DB writes."""
        from pipelines.deduplication import _flag_anomalies

        conn = _make_conn(fetch_side_effect=[[], [], [], [], [], []])

        count = await _flag_anomalies(conn, TENANT)

        assert count == 0
        conn.execute.assert_not_called()


# ── Worker smoke tests ─────────────────────────────────────────────────────────

class TestDeduplicationWorkerSmoke:

    def test_worker_class_importable(self):
        from pipelines.deduplication import DeduplicationWorker
        w = DeduplicationWorker()
        assert w.name == "deduplication"

    def test_amount_tolerance_is_one_rupee(self):
        from pipelines.deduplication import _AMOUNT_TOL
        assert _AMOUNT_TOL == 1.0, "_AMOUNT_TOL must be 1 INR"

    def test_soft_dup_window_is_seven_days(self):
        from pipelines.deduplication import _SOFT_DUP_DAYS
        assert _SOFT_DUP_DAYS == 7, "_SOFT_DUP_DAYS must be 7"

    def test_deduplication_module_not_circular(self):
        """All internal imports must resolve without circular dependency."""
        import importlib
        mod = importlib.import_module("pipelines.deduplication")
        assert hasattr(mod, "_dedup_bills")
        assert hasattr(mod, "_dedup_invoices")
        assert hasattr(mod, "_reconcile_failed_payments")
        assert hasattr(mod, "_upsert_anomaly")
        assert hasattr(mod, "_flag_anomalies")
        assert hasattr(mod, "DeduplicationWorker")
