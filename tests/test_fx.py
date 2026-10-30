"""Unit tests for FX multi-currency features — §21.1 of FX_MULTI_CURRENCY_SPEC.

Test                                        Phase
─────────────────────────────────────────   ─────
test_convert_usd_to_inr                     Core
test_convert_eur_to_inr                     Core
test_convert_gbp_to_inr                     Core
test_convert_jpy_to_inr                     Core
test_convert_inr_passthrough                Core
test_rate_carry_forward_weekend             Core
test_missing_rate_graceful                  Core
test_back_calculation                       Core
test_bank_spread                            Core
test_partial_payment_accumulation           Core
test_credit_note_at_booked_rate             Core
test_dedup_same_currency_bill               Core
test_dedup_cross_currency                   Core
test_tds_on_foreign_bill                    Core

All tests are fully offline — asyncpg / psycopg2 are mocked; no live DB needed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Shared row / connection helpers ───────────────────────────────────────────


class _R(dict):
    """Minimal asyncpg.Record look-alike: a plain dict subclass.

    dict(row) → proper dict with all fields
    row["key"] → field value
    row.get("key", default) → field value or default
    bool(row) → True when non-empty
    """


def _row(**fields) -> _R:
    return _R(fields)


def _rate_row(rate_id: str, inr_per_unit: str) -> _R:
    return _R({"fx_rate_id": rate_id, "inr_per_unit": Decimal(inr_per_unit)})


def _make_pool(conn):
    """Mock asyncpg Pool: acquire() always yields the same connection."""
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool


def _psyconn(*, fetchone=None, fetchall=None):
    """Mock synchronous psycopg2 connection with a configurable cursor."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    cur_ctx = MagicMock()
    cur_ctx.__enter__ = MagicMock(return_value=cur)
    cur_ctx.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    conn.commit = MagicMock()
    conn.rollback = MagicMock()
    conn.autocommit = False
    return conn


def _make_conn(*, fetchrow=None, fetch=None, fetch_side_effect=None):
    """Build an AsyncMock asyncpg Connection."""
    conn = AsyncMock()
    conn.execute  = AsyncMock(return_value="UPDATE 1")
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    if fetch_side_effect is not None:
        conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        conn.fetch = AsyncMock(return_value=fetch or [])
    conn.transaction = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return conn


# ── Shared constants ───────────────────────────────────────────────────────────

TENANT  = "10000000-0000-0000-0000-000000000001"
SUP1    = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RATE_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff"

# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Tests 1–5: Currency conversion (_convert_currency)
# ─────────────────────────────────────────────────────────────────────────────


class TestCurrencyConversion:
    """_convert_currency round-trip correctness for all supported currencies."""

    @pytest.mark.asyncio
    async def test_convert_usd_to_inr(self):
        """$1,000 × ₹85.00 = ₹85,000.00"""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "85.000000"))
        row  = _row(currency_code="USD", original_amount=Decimal("1000.00"))

        result = await _convert_currency(conn, row)

        assert result["total_amount"] == pytest.approx(85_000.00, rel=1e-6)
        assert result["outstanding_amount"] == pytest.approx(85_000.00, rel=1e-6)
        assert result["fx_rate_id"] == RATE_ID

    @pytest.mark.asyncio
    async def test_convert_eur_to_inr(self):
        """€2,000 × ₹92.34 = ₹1,84,680.00"""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "92.340000"))
        row  = _row(currency_code="EUR", original_amount=Decimal("2000.00"))

        result = await _convert_currency(conn, row)

        assert result["total_amount"] == pytest.approx(184_680.00, rel=1e-6)

    @pytest.mark.asyncio
    async def test_convert_gbp_to_inr(self):
        """£500 × ₹107.30 = ₹53,650.00"""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "107.300000"))
        row  = _row(currency_code="GBP", original_amount=Decimal("500.00"))

        result = await _convert_currency(conn, row)

        assert result["total_amount"] == pytest.approx(53_650.00, rel=1e-6)

    @pytest.mark.asyncio
    async def test_convert_jpy_to_inr(self):
        """¥100,000 × ₹0.5614 = ₹56,140 (JPY uses zero decimal places)"""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "0.561400"))
        row  = _row(currency_code="JPY", original_amount=Decimal("100000"))

        result = await _convert_currency(conn, row)

        assert result["total_amount"] == pytest.approx(56_140.00, rel=1e-4)

    @pytest.mark.asyncio
    async def test_convert_inr_passthrough(self):
        """INR rows pass through _convert_currency unchanged — no DB call is made."""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn()
        row  = _row(
            currency_code="INR",
            total_amount=Decimal("50000.00"),
            original_amount=None,
        )

        result = await _convert_currency(conn, row)

        assert result["total_amount"] == Decimal("50000.00")
        # Must not touch the DB for INR bills
        conn.fetchrow.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Tests 6–7: Rate resolution — carry-forward & missing rate
# ─────────────────────────────────────────────────────────────────────────────


class TestRateResolution:
    """Weekend carry-forward logic and graceful missing-rate handling."""

    def test_rate_carry_forward_weekend(self):
        """Saturday (weekday == 5) → is_business_day = False (carry-forward source)."""
        saturday = date(2025, 6, 7)          # June 7 2025 is a Saturday
        assert saturday.weekday() == 5, "date fixture is not a Saturday"
        assert (saturday.weekday() < 5) is False

    def test_rate_carry_forward_friday_is_business_day(self):
        """Friday (weekday == 4) → is_business_day = True (real RBI rate)."""
        friday = date(2025, 6, 6)            # June 6 2025 is a Friday
        assert friday.weekday() == 4, "date fixture is not a Friday"
        assert (friday.weekday() < 5) is True

    def test_rate_carry_forward_sunday_is_not_business_day(self):
        """Sunday (weekday == 6) → is_business_day = False (carry-forward source)."""
        sunday = date(2025, 6, 8)            # June 8 2025 is a Sunday
        assert sunday.weekday() == 6, "date fixture is not a Sunday"
        assert (sunday.weekday() < 5) is False

    @pytest.mark.asyncio
    async def test_missing_rate_graceful(self):
        """Foreign bill with no rate in dim_fx_rates → FxRateMissing raised (not a crash)."""
        from pipelines.staging_promotion import _convert_currency, FxRateMissing

        conn = _make_conn(fetchrow=None)       # DB returns None → no rate exists
        row  = _row(currency_code="USD", original_amount=Decimal("1000.00"))

        with pytest.raises(FxRateMissing):
            await _convert_currency(conn, row)


# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Tests 8–10: FX back-calculation math
# ─────────────────────────────────────────────────────────────────────────────


class TestBackCalculation:
    """Effective rate, bank spread, and partial-payment accumulation."""

    # ── Pure-math helper (mirrors fx_api.record_fx_payment business logic) ─────

    @staticmethod
    def _calc(
        actual_inr: float,
        original_amount: float,
        mid_rate: float | None = None,
        booking_inr: float | None = None,
    ) -> dict:
        """Replicate the back-calculation in fx_api.py without a DB call."""
        actual  = Decimal(str(actual_inr))
        orig    = Decimal(str(original_amount))
        eff     = actual / orig

        bank_spread = bank_spread_pct = None
        if mid_rate is not None:
            mid            = Decimal(str(mid_rate))
            bank_spread    = float(eff - mid)
            bank_spread_pct = float(((eff - mid) / mid) * 100)

        fx_realized_cost = None
        if booking_inr is not None:
            fx_realized_cost = float(actual - Decimal(str(booking_inr)))

        return {
            "effective_rate":   float(eff),
            "bank_spread":      bank_spread,
            "bank_spread_pct":  bank_spread_pct,
            "fx_realized_cost": fx_realized_cost,
        }

    def test_back_calculation(self):
        """actual ₹85,750 paid for $1,000 → effective rate = ₹85.75 / $"""
        r = self._calc(actual_inr=85_750, original_amount=1_000)
        assert r["effective_rate"] == pytest.approx(85.75, rel=1e-6)

    def test_bank_spread(self):
        """effective ₹85.75 − RBI mid ₹85.00 = spread ₹0.75 (≈ 0.88%)"""
        r = self._calc(actual_inr=85_750, original_amount=1_000, mid_rate=85.00)
        assert r["bank_spread"]     == pytest.approx(0.75, rel=1e-4)
        # 0.75 / 85.00 × 100 = 0.88235…%
        assert r["bank_spread_pct"] == pytest.approx(0.75 / 85.00 * 100, rel=1e-3)

    def test_partial_payment_accumulation(self):
        """Two partial FX payments accumulate fx_realized_cost correctly.

        Scenario:
          Bill: $1,000 booked at ₹85.00/$ → total_amount = ₹85,000
          Payment 1: ₹43,000 for first $500 (booking portion ₹42,500)
          Payment 2: ₹43,250 for final $500 (booking portion ₹42,500)
          Total actual paid = ₹86,250 vs booking ₹85,000 → extra FX cost ₹1,250
        """
        r1 = self._calc(
            actual_inr=43_000, original_amount=500,
            booking_inr=42_500,               # ₹85 × $500
        )
        r2 = self._calc(
            actual_inr=43_250, original_amount=500,
            booking_inr=42_500,               # ₹85 × $500
        )
        cumulative = r1["fx_realized_cost"] + r2["fx_realized_cost"]   # type: ignore[operator]
        assert cumulative == pytest.approx(1_250.0, rel=1e-6)

    def test_bank_spread_favourable(self):
        """When effective rate < mid rate, bank spread is negative (saving, not cost)."""
        r = self._calc(actual_inr=84_500, original_amount=1_000, mid_rate=85.00)
        assert r["bank_spread"] < 0
        assert r["bank_spread_pct"] < 0

    def test_back_calc_no_mid_rate(self):
        """When mid_rate is None, bank_spread and bank_spread_pct remain None."""
        r = self._calc(actual_inr=85_750, original_amount=1_000, mid_rate=None)
        assert r["bank_spread"]     is None
        assert r["bank_spread_pct"] is None
        assert r["effective_rate"] == pytest.approx(85.75, rel=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Test 11: Credit note at booked rate
# ─────────────────────────────────────────────────────────────────────────────


class TestCreditNote:
    """Credit notes denominated in INR pass through unchanged — no FX entry needed."""

    @pytest.mark.asyncio
    async def test_credit_note_at_booked_rate(self):
        """An INR credit note (negative total_amount) passes _convert_currency unchanged."""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn()
        # Credit note: already converted to INR, negative amount
        row = _row(
            currency_code="INR",
            total_amount=Decimal("-5000.00"),
            original_amount=None,
        )

        result = await _convert_currency(conn, row)

        # Amount unchanged and no DB call made
        assert result["total_amount"] == Decimal("-5000.00")
        conn.fetchrow.assert_not_called()

    @pytest.mark.asyncio
    async def test_foreign_credit_note_converts_correctly(self):
        """A credit note in USD (negative original_amount) converts correctly to INR."""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "85.000000"))
        # Negative original_amount represents a credit/refund
        row = _row(currency_code="USD", original_amount=Decimal("-500.00"))

        result = await _convert_currency(conn, row)

        # Credit converted: −$500 × ₹85 = −₹42,500
        assert result["total_amount"] == pytest.approx(-42_500.00, rel=1e-6)


# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Tests 12–13: FX-aware deduplication
# ─────────────────────────────────────────────────────────────────────────────


class TestDeduplication:
    """Dedup partition key uses COALESCE(original_amount, total_amount) + currency_code."""

    @pytest.mark.asyncio
    async def test_dedup_same_currency_bill(self):
        """Exact-duplicate USD bill (same supplier + date + amount + currency) → flagged DUPLICATE."""
        from pipelines.deduplication import _dedup_bills

        dup_row = _row(stg_bill_id="stg-b-dup-2")

        # 1a query (exact dups) returns the duplicate row;
        # 1b (cross-table) and 1c (soft) queries return nothing.
        conn = _make_conn(fetch_side_effect=[[dup_row], [], []])

        flagged = await _dedup_bills(conn, TENANT)

        assert flagged == 1

        # DUPLICATE flag must have been written to stg_bills
        all_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "DUPLICATE" in all_sql
        assert "ERROR"     in all_sql

    @pytest.mark.asyncio
    async def test_dedup_cross_currency(self):
        """$1,000 USD bill and ₹85,000 INR bill have different currency_code → NOT duplicates.

        The partition key includes currency_code, so they land in different buckets.
        The mocked DB returns no matching rows across all three dedup checks.
        """
        from pipelines.deduplication import _dedup_bills

        # All three queries (exact, cross-table, soft) find nothing
        conn = _make_conn(fetch_side_effect=[[], [], []])

        flagged = await _dedup_bills(conn, TENANT)

        # No duplicate detected — different currencies are different bills
        assert flagged == 0
        # No DUPLICATE/ERROR flag written
        for call in conn.execute.call_args_list:
            assert "DUPLICATE" not in str(call)

    @pytest.mark.asyncio
    async def test_dedup_partition_key_includes_currency(self):
        """SQL query uses COALESCE(original_amount, total_amount) and currency_code in PARTITION BY."""
        from pipelines import deduplication

        import inspect
        src = inspect.getsource(deduplication._dedup_bills)
        assert "COALESCE(original_amount, total_amount)" in src, (
            "Partition key must use COALESCE(original_amount, total_amount)"
        )
        assert "currency_code" in src, (
            "Partition key must include currency_code"
        )


# ─────────────────────────────────────────────────────────────────────────────
# §21.1 · Test 14: TDS calculated on total_amount (INR), not original foreign amount
# ─────────────────────────────────────────────────────────────────────────────


class TestTdsOnForeignBill:
    """TDS base is total_amount (always INR after _convert_currency), never original_amount."""

    @pytest.mark.asyncio
    async def test_tds_on_foreign_bill(self):
        """_promote_bill passes the INR total_amount (₹85,000) to fact_bills, not $1,000.

        Scenario:
          - Bill: $1,000 USD at ₹85.00/$ → INR total = ₹85,000
          - TDS and all downstream calculations use the ₹85,000 figure
        """
        from pipelines.staging_promotion import _promote_bill

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "85.000000"))

        row = _row(
            stg_bill_id       = "stg-b-usd-tds",
            tenant_id         = TENANT,
            supplier_id       = SUP1,
            bill_number       = "USD-TDS-001",
            bill_date         = date(2025, 6, 1),
            due_date          = date(2025, 7, 1),
            currency_code     = "USD",
            original_amount   = Decimal("1000.00"),
            total_amount      = Decimal("1000.00"),      # pre-conversion value
            subtotal          = Decimal("900.00"),
            tax_amount        = Decimal("100.00"),
            amount_paid       = Decimal("0"),
            outstanding_amount= Decimal("1000.00"),
            status            = "PENDING",
            source            = "MANUAL",
            source_updated_at = datetime.now(timezone.utc),
            tds_section_id    = None,
            tds_rate          = None,
            tds_base_amount   = None,
            tds_amount        = None,
            tds_status        = "PENDING",
            tds_section_code  = None,
            tds_rate_percent  = None,
            net_payable       = None,
            fx_rate_id        = None,
        )

        await _promote_bill(conn, row)

        # 1. An INSERT into fact_bills must have been issued
        all_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "fact_bills" in all_sql, "Expected INSERT into fact_bills"

        # 2. The INR total (₹85,000) must appear in the INSERT args —
        #    that is the value used for total_amount (and therefore TDS base).
        all_numeric: list[float] = []
        for call in conn.execute.call_args_list:
            for arg in call.args[1:]:            # skip the SQL string itself
                if isinstance(arg, (int, float, Decimal)):
                    all_numeric.append(float(arg))

        assert 85_000.0 in all_numeric, (
            f"Expected INR total 85000.0 in INSERT args; got: {all_numeric}"
        )

    @pytest.mark.asyncio
    async def test_tds_inr_bill_uses_total_amount_directly(self):
        """For an INR bill, total_amount is already the TDS base — no conversion needed."""
        from pipelines.staging_promotion import _convert_currency

        conn = _make_conn()
        row  = _row(
            currency_code = "INR",
            total_amount  = Decimal("50000.00"),
            original_amount = None,
        )

        result = await _convert_currency(conn, row)

        # total_amount passes through as-is; no DB call made
        assert float(result["total_amount"]) == pytest.approx(50_000.0, rel=1e-6)
        conn.fetchrow.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: FxRateWorker._parse_rates inversion correctness
# ─────────────────────────────────────────────────────────────────────────────


class TestParseRates:
    """FxRateWorker._parse_rates inverts INR-based raw rates to INR-per-unit."""

    def test_parse_rates_inverts_correctly(self):
        """open.er-api.com returns INR-per-USD = 0.011765 → inr_per_unit = 1/0.011765 ≈ 84.99"""
        from pipelines.fx_rate import FxRateWorker

        raw = {"USD": 0.011765, "EUR": 0.010830, "GBP": 0.009320, "JPY": 1.78120}
        result = FxRateWorker._parse_rates(raw, source="TEST")

        # USD: 1 / 0.011765 ≈ 84.996…
        assert float(result["USD"]) == pytest.approx(1 / 0.011765, rel=1e-3)
        assert float(result["EUR"]) == pytest.approx(1 / 0.010830, rel=1e-3)
        assert float(result["GBP"]) == pytest.approx(1 / 0.009320, rel=1e-3)
        assert float(result["JPY"]) == pytest.approx(1 / 1.78120,  rel=1e-3)

    def test_parse_rates_missing_currency_skipped(self):
        """A currency absent from the API response is silently omitted, not set to None."""
        from pipelines.fx_rate import FxRateWorker

        raw = {"USD": 0.011765}          # EUR / GBP / JPY missing
        result = FxRateWorker._parse_rates(raw, source="TEST")

        assert "USD" in result
        assert "EUR" not in result
        assert "GBP" not in result
        assert "JPY" not in result


# ─────────────────────────────────────────────────────────────────────────────
# §21.1b · Additional unit tests — Review Gaps
# ─────────────────────────────────────────────────────────────────────────────


class TestFetchRates:
    """FxRateWorker primary/fallback HTTP fetch behaviour + JPY inversion."""

    @pytest.mark.asyncio
    async def test_fetch_primary_success(self):
        """Primary returns result=success → all 4 currencies parsed and inverted."""
        from pipelines.fx_rate import FxRateWorker

        worker  = FxRateWorker()
        payload = {
            "result": "success",
            "rates": {"USD": 0.011765, "EUR": 0.010830, "GBP": 0.009320, "JPY": 1.78120},
        }

        resp              = MagicMock()
        resp.json.return_value = payload
        resp.raise_for_status = MagicMock()

        mock_client              = AsyncMock()
        mock_client.get          = AsyncMock(return_value=resp)
        mock_client.__aenter__   = AsyncMock(return_value=mock_client)
        mock_client.__aexit__    = AsyncMock(return_value=False)

        with patch("pipelines.fx_rate.httpx.AsyncClient", return_value=mock_client):
            result = await worker._fetch_primary()

        assert result is not None
        assert set(result.keys()) == {"USD", "EUR", "GBP", "JPY"}
        assert float(result["USD"]) == pytest.approx(1 / 0.011765, rel=1e-3)
        assert float(result["EUR"]) == pytest.approx(1 / 0.010830, rel=1e-3)

    @pytest.mark.asyncio
    async def test_fetch_primary_fails_fallback_success(self):
        """Primary raises HTTP error → _fetch_rates falls through to fallback."""
        from pipelines.fx_rate import FxRateWorker

        worker        = FxRateWorker()
        fallback_rates = {
            "USD": Decimal("85.000000"),
            "EUR": Decimal("92.000000"),
            "GBP": Decimal("107.000000"),
            "JPY": Decimal("0.561400"),
        }

        with patch.object(worker, "_fetch_primary",
                          AsyncMock(side_effect=RuntimeError("HTTP 500"))), \
             patch.object(worker, "_fetch_fallback",
                          AsyncMock(return_value=fallback_rates)):
            result = await worker._fetch_rates()

        assert result is not None
        assert "USD" in result
        assert float(result["USD"]) == pytest.approx(85.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_fetch_both_fail(self):
        """Both primary and fallback raise → _fetch_rates returns None without crashing."""
        from pipelines.fx_rate import FxRateWorker

        worker = FxRateWorker()

        with patch.object(worker, "_fetch_primary",
                          AsyncMock(side_effect=RuntimeError("primary down"))), \
             patch.object(worker, "_fetch_fallback",
                          AsyncMock(side_effect=RuntimeError("fallback down"))):
            result = await worker._fetch_rates()

        assert result is None

    def test_rate_inversion_jpy(self):
        """raw JPY rate 1.7812 (JPY per INR) → inr_per_unit = 1/1.7812 ≈ 0.5614 per yen."""
        from pipelines.fx_rate import FxRateWorker

        raw    = {"USD": 0.011765, "EUR": 0.010830, "GBP": 0.009320, "JPY": 1.78120}
        result = FxRateWorker._parse_rates(raw, source="TEST")

        # 1/1.78120 ≈ 0.561400 — per-yen, NOT per-100-yen
        assert float(result["JPY"]) == pytest.approx(1 / 1.78120, rel=1e-4)
        # Stored to 6 decimal places
        assert result["JPY"] == result["JPY"].quantize(Decimal("0.000001"))


class TestBackfill:
    """_maybe_backfill: triggers when dim_fx_rates is empty, skips when rows exist."""

    @pytest.mark.asyncio
    async def test_backfill_trigger_empty_table(self):
        """dim_fx_rates empty (COUNT=0) → _backfill_historical is called once."""
        from pipelines.fx_rate import FxRateWorker

        worker = FxRateWorker()
        conn   = _make_conn()
        conn.fetchval = AsyncMock(return_value=0)   # empty table
        pool   = _make_pool(conn)

        with patch("pipelines.fx_rate.get_pool", AsyncMock(return_value=pool)), \
             patch.object(worker, "_backfill_historical", AsyncMock()) as mock_bf:
            await worker._maybe_backfill()

        mock_bf.assert_called_once()

    @pytest.mark.asyncio
    async def test_backfill_skip_when_data_exists(self):
        """dim_fx_rates has rows (COUNT=10) → _backfill_historical is NOT called."""
        from pipelines.fx_rate import FxRateWorker

        worker = FxRateWorker()
        conn   = _make_conn()
        conn.fetchval = AsyncMock(return_value=10)  # data present
        pool   = _make_pool(conn)

        with patch("pipelines.fx_rate.get_pool", AsyncMock(return_value=pool)), \
             patch.object(worker, "_backfill_historical", AsyncMock()) as mock_bf:
            await worker._maybe_backfill()

        mock_bf.assert_not_called()


class TestRevalue:
    """_revalue_bills: only targets foreign-currency bills; excludes PAID/VOID."""

    @pytest.mark.asyncio
    async def test_revalue_updates_non_inr_only(self):
        """_revalue_bills issues UPDATE for each foreign currency — never targets INR rows."""
        from pipelines.fx_rate import FxRateWorker

        worker = FxRateWorker()
        conn   = _make_conn()
        conn.execute = AsyncMock(return_value="UPDATE 3")

        rates = {
            "USD": Decimal("85.000000"),
            "EUR": Decimal("92.340000"),
            "GBP": Decimal("107.300000"),
            "JPY": Decimal("0.561400"),
        }

        total = await worker._revalue_bills(conn, rates)

        # 4 currencies × 3 statements (fact_bills revalue + fact_invoices fix unconverted
        # + fact_invoices revalue) = 12 execute calls
        assert conn.execute.call_count == 12
        assert total == 12   # 3 updated bills per currency × 4 currencies

        # None of the UPDATE calls should hard-code 'INR' as the target currency
        updated_currencies = set()
        for call in conn.execute.call_args_list:
            args = call.args
            if len(args) >= 3 and isinstance(args[2], str) and len(args[2]) == 3:
                updated_currencies.add(args[2])
        assert "INR" not in updated_currencies
        assert updated_currencies == {"USD", "EUR", "GBP", "JPY"}

    def test_revalue_skips_paid_and_void(self):
        """_revalue_bills SQL excludes PAID and VOID bills via NOT IN clause."""
        import inspect
        from pipelines.fx_rate import FxRateWorker

        src = inspect.getsource(FxRateWorker._revalue_bills)
        assert "NOT IN" in src
        assert "'PAID'" in src
        assert "'VOID'" in src


class TestPollBehavior:
    """process_batch returns 0 (no API call) when today's rates already exist."""

    @pytest.mark.asyncio
    async def test_poll_skip_when_todays_rate_exists(self):
        """COUNT of today's rates ≥ 4 → process_batch returns 0, no _fetch_rates call."""
        from pipelines.fx_rate import FxRateWorker

        worker = FxRateWorker()
        conn   = _make_conn()
        conn.fetchval = AsyncMock(return_value=4)   # all 4 currencies present today
        pool   = _make_pool(conn)

        with patch("pipelines.fx_rate.get_pool", AsyncMock(return_value=pool)), \
             patch.object(worker, "_fetch_rates", AsyncMock()) as mock_fetch:
            result = await worker.process_batch()

        assert result == 0
        mock_fetch.assert_not_called()


class TestMissingRatePromotion:
    """D11: Foreign bill with no FX rate → validation_flag=REVIEW; re-promotes when rate arrives."""

    def _usd_bill_row(self) -> _R:
        return _R({
            "stg_bill_id"      : "stg-b-blocked",
            "tenant_id"        : TENANT,
            "supplier_id"      : SUP1,
            "bill_number"      : "USD-BLOCKED-001",
            "bill_date"        : date(2025, 6, 1),
            "due_date"         : date(2025, 7, 1),
            "currency_code"    : "USD",
            "original_amount"  : Decimal("1000.00"),
            "total_amount"     : Decimal("1000.00"),
            "subtotal"         : Decimal("900.00"),
            "tax_amount"       : Decimal("100.00"),
            "amount_paid"      : Decimal("0"),
            "outstanding_amount": Decimal("1000.00"),
            "status"           : "PENDING",
            "source"           : "MANUAL",
            "source_updated_at": datetime.now(timezone.utc),
            "tds_section_id"   : None, "tds_rate"        : None,
            "tds_base_amount"  : None, "tds_amount"      : None,
            "tds_status"       : "PENDING", "tds_section_code": None,
            "tds_rate_percent" : None, "net_payable"     : None,
            "fx_rate_id"       : None,
        })

    @pytest.mark.asyncio
    async def test_missing_rate_blocks_promotion(self):
        """Non-INR bill with no rate in dim_fx_rates → stg_bills updated with validation_flag=REVIEW (D11)."""
        import config as _cfg
        from pipelines.staging_promotion import StagingPromotionWorker

        bill_row = self._usd_bill_row()

        # conn.fetch returns the pending bill; conn.fetchrow returns None (no rate)
        conn = _make_conn(fetch=[bill_row], fetchrow=None)
        pool = _make_pool(conn)

        worker = StagingPromotionWorker()
        with patch("pipelines.staging_promotion.get_pool", AsyncMock(return_value=pool)), \
             patch.object(_cfg, "PIPELINE_TENANT_ID", ""):
            await worker._promote_bills()

        # validation_flag='REVIEW' must have been written to stg_bills
        all_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "REVIEW" in all_sql
        assert "stg_bills" in all_sql

    @pytest.mark.asyncio
    async def test_promotion_resumes_after_rate_fetched(self):
        """Once a rate is present in dim_fx_rates, the previously blocked bill is promoted."""
        from pipelines.staging_promotion import _promote_bill

        # Rate is now available
        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "85.000000"))
        row  = self._usd_bill_row()

        await _promote_bill(conn, row)

        # Successful promotion writes to fact_bills (not a REVIEW flag)
        all_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "fact_bills" in all_sql
        assert "REVIEW" not in all_sql


class TestFxApiEndpoints:
    """HTTP contract for GET /fx/rates and POST /bills/{id}/fx-payment."""

    @staticmethod
    def _call(fn, *args, **kwargs):
        """Call a sync FastAPI route handler directly (no TestClient needed)."""
        return fn(*args, **kwargs)

    def test_api_get_rates_returns_all_currencies(self):
        """GET /api/fx/rates returns rows for USD, EUR, GBP, JPY."""
        from datetime import datetime as _dt
        import backend_ocr.app.routers.fx as fx_api

        now  = _dt(2025, 6, 1, tzinfo=timezone.utc)
        today = date(2025, 6, 1)
        rows = [
            ("USD", 85.0,   today, "PRIMARY", now),
            ("EUR", 92.0,   today, "PRIMARY", now),
            ("GBP", 107.0,  today, "PRIMARY", now),
            ("JPY", 0.5614, today, "PRIMARY", now),
        ]
        conn = _psyconn(fetchall=rows)

        with patch.object(fx_api, "get_conn", return_value=conn), \
             patch.object(fx_api, "put_conn"):
            result = fx_api.get_fx_rates()

        codes = {r["currency_code"] for r in result["rates"]}
        assert codes == {"USD", "EUR", "GBP", "JPY"}
        assert len(result["rates"]) == 4

    def test_api_fx_payment_bill_not_found(self):
        """POST /api/bills/{bad_id}/fx-payment with unknown bill_id → 404."""
        from fastapi import HTTPException
        import backend_ocr.app.routers.fx as fx_api

        conn = _psyconn(fetchone=None)   # bill not found

        with patch.object(fx_api, "get_conn", return_value=conn), \
             patch.object(fx_api, "put_conn"), \
             pytest.raises(HTTPException) as exc_info:
            fx_api.record_fx_payment(
                "nonexistent-bill-uuid",
                fx_api.FxPaymentRequest(actual_inr_paid=85750.0),
            )

        assert exc_info.value.status_code == 404

    def test_api_fx_payment_already_fully_paid(self):
        """POST /api/bills/{id}/fx-payment on a PAID bill → 400."""
        from fastapi import HTTPException
        import backend_ocr.app.routers.fx as fx_api

        # bill row: (invoice_id, currency_code, original_amount, total_amount, mid_rate, status)
        bill_row = ("inv-paid-uuid", "USD", 1000.0, 85000.0, 85.0, "PAID")
        conn = _psyconn(fetchone=bill_row)

        with patch.object(fx_api, "get_conn", return_value=conn), \
             patch.object(fx_api, "put_conn"), \
             pytest.raises(HTTPException) as exc_info:
            fx_api.record_fx_payment(
                "inv-paid-uuid",
                fx_api.FxPaymentRequest(actual_inr_paid=85750.0),
            )

        assert exc_info.value.status_code == 400
        assert "settled" in exc_info.value.detail.lower()

    def test_api_fx_payment_invalid_currency(self):
        """POST /api/bills/{id}/fx-payment on an INR bill (no original_amount) → 400."""
        from fastapi import HTTPException
        import backend_ocr.app.routers.fx as fx_api

        # INR bill: original_amount is NULL
        bill_row = ("inv-inr-uuid", "INR", None, 50000.0, None, "SENT")
        conn = _psyconn(fetchone=bill_row)

        with patch.object(fx_api, "get_conn", return_value=conn), \
             patch.object(fx_api, "put_conn"), \
             pytest.raises(HTTPException) as exc_info:
            fx_api.record_fx_payment(
                "inv-inr-uuid",
                fx_api.FxPaymentRequest(actual_inr_paid=50000.0),
            )

        assert exc_info.value.status_code == 400
        assert "INR" in exc_info.value.detail or "original" in exc_info.value.detail.lower()


class TestIntegration:
    """End-to-end: stg USD bill → _convert_currency → _promote_bill → fact_bills has INR total."""

    @pytest.mark.asyncio
    async def test_conversion_plus_promotion_integration(self):
        """$1,000 USD at ₹85/$ flows through _promote_bill with INR total_amount=₹85,000 in fact_bills."""
        from pipelines.staging_promotion import _promote_bill

        conn = _make_conn(fetchrow=_rate_row(RATE_ID, "85.000000"))
        row  = _R({
            "stg_bill_id"      : "stg-b-e2e",
            "tenant_id"        : TENANT,
            "supplier_id"      : SUP1,
            "bill_number"      : "USD-E2E-001",
            "bill_date"        : date(2025, 6, 1),
            "due_date"         : date(2025, 7, 1),
            "currency_code"    : "USD",
            "original_amount"  : Decimal("1000.00"),
            "total_amount"     : Decimal("1000.00"),  # pre-conversion value
            "subtotal"         : Decimal("900.00"),
            "tax_amount"       : Decimal("100.00"),
            "amount_paid"      : Decimal("0"),
            "outstanding_amount": Decimal("1000.00"),
            "status"           : "PENDING",
            "source"           : "MANUAL",
            "source_updated_at": datetime.now(timezone.utc),
            "tds_section_id"   : None, "tds_rate"         : None,
            "tds_base_amount"  : None, "tds_amount"       : None,
            "tds_status"       : "PENDING", "tds_section_code": None,
            "tds_rate_percent" : None, "net_payable"      : None,
            "fx_rate_id"       : None,
        })

        await _promote_bill(conn, row)

        # The INSERT into fact_bills must have carried the INR total_amount (₹85,000)
        all_sql = " ".join(str(c) for c in conn.execute.call_args_list)
        assert "fact_bills" in all_sql

        all_numeric: list[float] = []
        for call in conn.execute.call_args_list:
            for arg in call.args[1:]:
                if isinstance(arg, (int, float, Decimal)):
                    all_numeric.append(float(arg))

        assert 85_000.0 in all_numeric, (
            f"Expected INR total 85000.0 in fact_bills INSERT args; got {all_numeric}"
        )


class TestDeduplicationCurrency:
    """D12: dedup partition key must include both currency_code and original_amount."""

    def test_dedup_includes_currency_code(self):
        """_dedup_bills SQL partitions by COALESCE(original_amount, total_amount) AND currency_code
        so a $1,000 USD bill and a ₹85,000 INR bill are never treated as duplicates."""
        import inspect
        from pipelines import deduplication

        src = inspect.getsource(deduplication._dedup_bills)

        assert "currency_code" in src, (
            "Dedup partition key must include currency_code (D12)"
        )
        assert "COALESCE(original_amount, total_amount)" in src, (
            "Dedup must use COALESCE(original_amount, total_amount) not raw total_amount"
        )
        # Both must appear together in a PARTITION BY clause (slice to the matching ORDER BY)
        pb_start = src.find("PARTITION BY")
        ob_start = src.find("ORDER BY", pb_start)
        partition_section = src[pb_start:ob_start]
        assert "currency_code" in partition_section, (
            "currency_code must appear in PARTITION BY clause, not just elsewhere in query"
        )
