"""
FxRateWorker — daily FX rate fetch, storage, bill revaluation, and alert engine.

Lifecycle
─────────
Startup:
  • If dim_fx_rates is empty → backfill 90 days via exchangeratesapi.io timeseries
    (1 API call, not 90)

Phase 2 (time-driven poll, no CDC channel):
  1. Already have today's rate for all currencies? → return 0, sleep until next poll
  2. Fetch from primary (open.er-api.com, no key needed)
  3. On primary failure → try fallback (exchangeratesapi.io, key required)
  4. Upsert into dim_fx_rates
  5. Bulk-UPDATE fact_bills.inr_amount_current + fx_gain_loss (4 statements, one per currency)
  6. Run FxAlertEngine for each tenant with FX exposure (Phase 2)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

import httpx

import config
from db import get_pool
from pipelines.base import BaseWorker

logger = logging.getLogger(__name__)

_CURRENCIES: list[str] = config.FX_FETCH_CURRENCIES   # ['USD','EUR','GBP','JPY']
_PRIMARY_URL: str       = config.FX_PRIMARY_SOURCE_URL
_FALLBACK_KEY: str      = config.FX_FALLBACK_API_KEY
_BACKFILL_DAYS: int     = config.FX_BACKFILL_DAYS

_RATE_Q  = Decimal("0.000001")   # 6 dp for inr_per_unit storage
_INR_Q   = Decimal("0.01")       # 2 dp for money


class FxRateWorker(BaseWorker):
    name           = "fx_rate"
    notify_channel = None       # time-driven, no CDC
    skip_backfill  = True       # jump straight to Phase 2

    # ── Entry point ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        await self._maybe_backfill()
        await super().run()

    # ── process_batch (called by BaseWorker._run_poll) ─────────────────────────

    async def process_batch(self) -> int:
        """
        Returns 1 if rates were fetched and stored, 0 if today's rates already
        exist (signals BaseWorker to back off to idle_poll_interval_s).
        """
        today = date.today()

        pool = await get_pool()
        async with pool.acquire() as conn:
            if await self._has_todays_rates(conn, today):
                logger.debug("[fx_rate] today's rates already in DB — skipping")
                return 0

            rates = await self._fetch_rates()
            if not rates:
                logger.error("[fx_rate] all rate sources failed — will retry")
                raise RuntimeError("FX rate fetch failed from all sources")

            await self._upsert_rates(conn, today, rates)
            revalued = await self._revalue_bills(conn, rates)
            alerts = await self._run_alert_engine(conn, today, rates)

        logger.info(
            "[fx_rate] stored rates %s; revalued %d bills; generated %d alerts",
            {k: str(v) for k, v in rates.items()},
            revalued,
            alerts,
        )
        return 1

    # ── Today's rates check ────────────────────────────────────────────────────

    async def _has_todays_rates(self, conn, today: date) -> bool:
        # currency_code is char(3); casting the param to bare `char[]` (== character(1)[])
        # silently truncated 'USD' -> 'U' and never matched, so this always returned 0 and
        # the worker re-fetched/re-stored/re-alerted on every poll with no idle backoff.
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM dim_fx_rates
            WHERE rate_date = $1 AND currency_code = ANY($2::text[])
            """,
            today,
            _CURRENCIES,
        )
        return int(count) >= len(_CURRENCIES)

    # ── Rate fetch (primary → fallback) ───────────────────────────────────────

    async def _fetch_rates(self) -> dict[str, Decimal] | None:
        for fetcher in (self._fetch_primary, self._fetch_fallback):
            try:
                rates = await fetcher()
                if rates:
                    return rates
            except Exception as exc:
                logger.warning("[fx_rate] %s failed: %s", fetcher.__name__, exc)
        return None

    async def _fetch_primary(self) -> dict[str, Decimal] | None:
        """open.er-api.com — free, no key, one call for all currencies."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(_PRIMARY_URL)
            resp.raise_for_status()
            data = resp.json()

        if data.get("result") != "success":
            logger.warning("[fx_rate] primary returned result=%s", data.get("result"))
            return None

        return self._parse_rates(data["rates"], source="PRIMARY")

    async def _fetch_fallback(self) -> dict[str, Decimal] | None:
        """exchangeratesapi.io — free tier, key required."""
        if not _FALLBACK_KEY:
            logger.warning("[fx_rate] FX_FALLBACK_API_KEY not set — skipping fallback")
            return None

        symbols = ",".join(_CURRENCIES)
        url = (
            f"https://api.exchangeratesapi.io/v1/latest"
            f"?access_key={_FALLBACK_KEY}&base=INR&symbols={symbols}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 429:
                logger.warning("[fx_rate] fallback rate-limited (429)")
                return None
            resp.raise_for_status()
            data = resp.json()

        if not data.get("success", False):
            logger.warning("[fx_rate] fallback error: %s", data.get("error"))
            return None

        return self._parse_rates(data["rates"], source="FALLBACK")

    @staticmethod
    def _parse_rates(
        raw_rates: dict,
        source: str,
    ) -> dict[str, Decimal]:
        """
        raw_rates is INR-based: {'USD': 0.01176, 'JPY': 1.7812, ...}
        We invert to get INR per 1 unit of foreign currency.
        """
        out: dict[str, Decimal] = {}
        for code in _CURRENCIES:
            raw = raw_rates.get(code)
            if raw is None:
                logger.warning("[fx_rate] %s missing from %s response", code, source)
                continue
            inr_per_unit = (Decimal(1) / Decimal(str(raw))).quantize(_RATE_Q, rounding=ROUND_HALF_UP)
            out[code] = inr_per_unit
        return out

    # ── Upsert into dim_fx_rates ───────────────────────────────────────────────

    async def _upsert_rates(
        self,
        conn,
        rate_date: date,
        rates: dict[str, Decimal],
        source: str = "PRIMARY",
        is_business_day: bool = True,
    ) -> None:
        for code, inr_per_unit in rates.items():
            await conn.execute(
                """
                INSERT INTO dim_fx_rates
                    (rate_date, currency_code, inr_per_unit, source, is_business_day)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (rate_date, currency_code)
                DO UPDATE SET
                    inr_per_unit    = EXCLUDED.inr_per_unit,
                    source          = EXCLUDED.source,
                    is_business_day = EXCLUDED.is_business_day,
                    fetched_at      = now()
                """,
                rate_date,
                code,
                inr_per_unit,
                source,
                is_business_day,
            )

    # ── Bulk revaluation of outstanding foreign bills ──────────────────────────

    async def _revalue_bills(self, conn, rates: dict[str, Decimal]) -> int:
        total = 0
        for code, inr_per_unit in rates.items():
            # Revalue pipeline fact table
            result = await conn.execute(
                """
                UPDATE fact_bills
                SET inr_amount_current = original_amount * $1,
                    fx_gain_loss       = (original_amount * $1) - total_amount
                WHERE currency_code = $2
                  AND original_amount IS NOT NULL
                  AND status NOT IN ('PAID', 'VOID')
                """,
                inr_per_unit,
                code,
            )
            n = int(result.split()[-1])

            # Fix unconverted bills in fact_invoices (fx_rate_id IS NULL means
            # total_amount is still the raw foreign amount from ingest).
            # Use the rate on/before the bill's own invoice_date (historical rate),
            # not today's rate — so a bill dated Jan 10 gets the Jan 10 rate even
            # if it's being fixed weeks later.
            # Note: LATERAL can't reference the UPDATE target; use DISTINCT ON subquery instead.
            fix_result = await conn.execute(
                """
                UPDATE fact_invoices fi
                SET total_amount       = fi.original_amount * hist.inr_per_unit,
                    outstanding_amount = GREATEST(
                        fi.original_amount * hist.inr_per_unit - COALESCE(fi.amount_paid, 0), 0),
                    subtotal           = CASE WHEN fi.subtotal IS NOT NULL AND fi.subtotal > 0
                                              THEN fi.subtotal * hist.inr_per_unit ELSE fi.subtotal END,
                    tax_amount         = CASE WHEN fi.tax_amount IS NOT NULL AND fi.tax_amount > 0
                                              THEN fi.tax_amount * hist.inr_per_unit ELSE fi.tax_amount END,
                    fx_rate_id         = hist.rate_id,
                    inr_amount_current = fi.original_amount * hist.inr_per_unit,
                    fx_gain_loss       = 0
                FROM (
                    SELECT DISTINCT ON (fi2.invoice_id)
                           fi2.invoice_id, dr.rate_id, dr.inr_per_unit
                    FROM fact_invoices fi2
                    JOIN dim_fx_rates dr
                      ON dr.currency_code = fi2.currency_code
                     AND dr.rate_date <= COALESCE(fi2.invoice_date, CURRENT_DATE)
                    WHERE fi2.currency_code = $1
                      AND fi2.original_amount IS NOT NULL
                      AND fi2.original_amount > 0
                      AND fi2.fx_rate_id IS NULL
                      AND COALESCE(fi2.soft_status, 'active') = 'active'
                    ORDER BY fi2.invoice_id, dr.rate_date DESC
                ) hist
                WHERE fi.invoice_id = hist.invoice_id
                """,
                code,
            )
            fixed = int(fix_result.split()[-1])
            if fixed:
                logger.info("[fx_rate] fixed %d unconverted %s bills in fact_invoices", fixed, code)

            # Revalue frontend fact table (fact_invoices is what list_bills queries)
            await conn.execute(
                """
                UPDATE fact_invoices
                SET inr_amount_current = original_amount * $1,
                    fx_gain_loss       = (original_amount * $1) - total_amount
                WHERE currency_code = $2
                  AND original_amount IS NOT NULL
                  AND fx_rate_id IS NOT NULL
                  AND status NOT IN ('PAID', 'VOID')
                  AND COALESCE(soft_status, 'active') = 'active'
                """,
                inr_per_unit,
                code,
            )

            if n:
                logger.debug("[fx_rate] revalued %d %s bills @ %s", n, code, inr_per_unit)
            total += n
        return total

    # ── Historical backfill ────────────────────────────────────────────────────

    async def _maybe_backfill(self) -> None:
        pool = await get_pool()
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM dim_fx_rates")
            if int(count) > 0:
                logger.info("[fx_rate] dim_fx_rates has %d rows — skipping backfill", count)
                return

        logger.info("[fx_rate] dim_fx_rates empty — starting %d-day backfill", _BACKFILL_DAYS)
        await self._backfill_historical(_BACKFILL_DAYS)

    async def _backfill_historical(self, days: int) -> None:
        """
        Fetch historical rates for the past `days` days.

        Strategy: try exchangeratesapi.io timeseries (1 call for the full range).
        If that fails or no key, fall back to individual daily calls against
        open.er-api.com for each missing date (capped at `days`).
        """
        end_date   = date.today()
        start_date = end_date - timedelta(days=days)

        if _FALLBACK_KEY:
            stored = await self._backfill_via_timeseries(start_date, end_date)
            if stored > 0:
                logger.info("[fx_rate] backfill complete — %d rate-rows stored", stored)
                return

        # Fallback: fetch today's rate and carry-forward for all past dates
        logger.info("[fx_rate] timeseries unavailable — seeding with today's rate")
        rates = await self._fetch_primary()
        if not rates:
            logger.error("[fx_rate] backfill failed: could not fetch any rates")
            return

        pool = await get_pool()
        stored = 0
        async with pool.acquire() as conn:
            current = start_date
            while current <= end_date:
                await self._upsert_rates(
                    conn, current, rates,
                    source="CARRIED_FORWARD",
                    is_business_day=current.weekday() < 5,
                )
                stored += len(rates)
                current += timedelta(days=1)

        logger.info("[fx_rate] carry-forward backfill complete — %d rows stored", stored)

    async def _backfill_via_timeseries(self, start: date, end: date) -> int:
        """
        exchangeratesapi.io timeseries endpoint — 1 call, returns all dates.
        Returns number of rows upserted (0 on failure).
        """
        symbols = ",".join(_CURRENCIES)
        url = (
            f"https://api.exchangeratesapi.io/v1/timeseries"
            f"?access_key={_FALLBACK_KEY}"
            f"&start_date={start}&end_date={end}"
            f"&base=INR&symbols={symbols}"
        )
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                if resp.status_code == 429:
                    logger.warning("[fx_rate] timeseries rate-limited")
                    return 0
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning("[fx_rate] timeseries fetch error: %s", exc)
            return 0

        if not data.get("success", False):
            logger.warning("[fx_rate] timeseries error response: %s", data.get("error"))
            return 0

        rates_by_date: dict = data.get("rates", {})
        if not rates_by_date:
            return 0

        pool = await get_pool()
        stored = 0
        async with pool.acquire() as conn:
            for date_str, day_rates in sorted(rates_by_date.items()):
                try:
                    rate_date = date.fromisoformat(date_str)
                except ValueError:
                    continue

                parsed = self._parse_rates(day_rates, source="FALLBACK")
                if not parsed:
                    continue

                is_bday = rate_date.weekday() < 5
                await self._upsert_rates(
                    conn, rate_date, parsed,
                    source="FALLBACK" if is_bday else "CARRIED_FORWARD",
                    is_business_day=is_bday,
                )
                stored += len(parsed)

        return stored

    # ── Alert Engine (Phase 2) ────────────────────────────────────────────────

    async def _run_alert_engine(
        self, conn, today: date, rates: dict[str, Decimal]
    ) -> int:
        tenants = await conn.fetch(
            """SELECT DISTINCT tenant_id FROM fact_bills
               WHERE currency_code != 'INR'
                 AND original_amount IS NOT NULL
                 AND status NOT IN ('PAID', 'VOID')""",
        )
        if not tenants:
            return 0

        total_alerts = 0
        for row in tenants:
            tenant_id = str(row["tenant_id"])
            settings = await self._get_tenant_fx_settings(conn, tenant_id)
            if not settings.get("alerts_enabled", True):
                continue

            for code, inr_per_unit in rates.items():
                n = await self._evaluate_alerts(
                    conn, today, tenant_id, code, inr_per_unit, settings
                )
                total_alerts += n

        stale = await self._check_stale_rates(conn, today, tenants)
        total_alerts += stale

        return total_alerts

    async def _get_tenant_fx_settings(self, conn, tenant_id: str) -> dict:
        row = await conn.fetchval(
            """SELECT settings_jsonb->'fx' FROM dim_tenants
               WHERE tenant_id = $1""",
            tenant_id,
        )
        if row:
            try:
                return json.loads(row) if isinstance(row, str) else row
            except (json.JSONDecodeError, TypeError):
                pass
        return config.FX_DEFAULT_SETTINGS

    async def _evaluate_alerts(
        self,
        conn,
        today: date,
        tenant_id: str,
        currency: str,
        rate: Decimal,
        settings: dict,
    ) -> int:
        thresholds = settings.get("thresholds", {}).get(currency.strip(), {})
        if not thresholds:
            thresholds = config.FX_DEFAULT_SETTINGS["thresholds"].get(currency.strip(), {})
        if not thresholds:
            return 0

        rate_f = float(rate)
        alerts_created = 0

        exposure = await conn.fetchrow(
            """SELECT ARRAY_AGG(bill_id) AS bill_ids,
                      SUM(original_amount) AS total_foreign,
                      SUM(COALESCE(inr_amount_current, total_amount)) AS total_inr
               FROM fact_bills
               WHERE tenant_id = $1 AND currency_code = $2
                 AND original_amount IS NOT NULL
                 AND status NOT IN ('PAID', 'VOID')""",
            tenant_id, currency,
        )

        bill_ids = exposure["bill_ids"] if exposure and exposure["bill_ids"] else []
        total_foreign = float(exposure["total_foreign"]) if exposure and exposure["total_foreign"] else 0
        total_inr = float(exposure["total_inr"]) if exposure and exposure["total_inr"] else 0

        if total_foreign == 0:
            return 0

        # PAY_NOW_OPPORTUNITY: rate below threshold (currency is cheap)
        pay_now = thresholds.get("pay_now_below")
        if pay_now and rate_f <= pay_now:
            await self._upsert_alert(
                conn, tenant_id, "PAY_NOW_OPPORTUNITY", currency, today, rate_f,
                pay_now, "BELOW",
                f"{currency}/INR at Rs {rate_f:.2f} — below your Rs {pay_now:.2f} threshold. "
                f"You have {len(bill_ids)} {currency} bill(s) worth {currency} {total_foreign:,.0f}. "
                f"Consider paying now while the rate is favourable.",
                bill_ids, total_foreign, total_inr,
            )
            alerts_created += 1

        # DELAY_PAYMENT: rate above threshold (currency is expensive)
        delay_pay = thresholds.get("delay_pay_above")
        if delay_pay and rate_f >= delay_pay:
            await self._upsert_alert(
                conn, tenant_id, "DELAY_PAYMENT", currency, today, rate_f,
                delay_pay, "ABOVE",
                f"{currency}/INR at Rs {rate_f:.2f} — above your Rs {delay_pay:.2f} threshold. "
                f"Deferring {len(bill_ids)} {currency} bill(s) worth {currency} {total_foreign:,.0f} "
                f"could save money if the rate drops.",
                bill_ids, total_foreign, total_inr,
            )
            alerts_created += 1

        # COLLECT_NOW_OPPORTUNITY: rate above collection threshold
        collect_now = thresholds.get("collect_now_above")
        if collect_now and rate_f >= collect_now:
            ar_exposure = await conn.fetchrow(
                """SELECT ARRAY_AGG(invoice_id) AS invoice_ids,
                          SUM(original_amount) AS total_foreign
                   FROM fact_invoices
                   WHERE tenant_id = $1 AND currency_code = $2
                     AND original_amount IS NOT NULL
                     AND status NOT IN ('PAID', 'VOID')
                     AND COALESCE(soft_status, 'active') = 'active'""",
                tenant_id, currency,
            )
            ar_ids = ar_exposure["invoice_ids"] if ar_exposure and ar_exposure["invoice_ids"] else []
            ar_foreign = float(ar_exposure["total_foreign"]) if ar_exposure and ar_exposure["total_foreign"] else 0

            if ar_foreign > 0:
                await self._upsert_alert(
                    conn, tenant_id, "COLLECT_NOW_OPPORTUNITY", currency, today, rate_f,
                    collect_now, "ABOVE",
                    f"{currency}/INR at Rs {rate_f:.2f} — above your Rs {collect_now:.2f} threshold. "
                    f"Chase {len(ar_ids)} {currency} receivable(s) worth {currency} {ar_foreign:,.0f} "
                    f"to lock in the favourable rate.",
                    [], total_foreign, total_inr,
                )
                alerts_created += 1

        # RATE_VOLATILE: 7-day volatility above threshold
        vol_threshold = thresholds.get("volatility_threshold_pct", 1.5)
        volatility = await self._calc_volatility(conn, currency, today)
        if volatility is not None and volatility >= vol_threshold:
            await self._upsert_alert(
                conn, tenant_id, "RATE_VOLATILE", currency, today, rate_f,
                None, None,
                f"{currency}/INR showing {volatility:.2f}% volatility over 7 days "
                f"(threshold: {vol_threshold:.1f}%). "
                f"Your {currency} exposure is {currency} {total_foreign:,.0f}. "
                f"Consider timing payments carefully.",
                bill_ids, total_foreign, total_inr,
            )
            alerts_created += 1

        return alerts_created

    async def _check_stale_rates(self, conn, today: date, tenants) -> int:
        stale_days = config.FX_DEFAULT_SETTINGS.get("stale_alert_days", 3)
        alerts = 0

        for code in _CURRENCIES:
            latest = await conn.fetchval(
                """SELECT MAX(rate_date) FROM dim_fx_rates
                   WHERE currency_code = $1 AND source != 'CARRIED_FORWARD'""",
                code,
            )
            if not latest:
                continue
            gap = (today - latest).days
            if gap < stale_days:
                continue

            for row in tenants:
                tenant_id = str(row["tenant_id"])
                has_exposure = await conn.fetchval(
                    """SELECT COUNT(*) FROM fact_bills
                       WHERE tenant_id = $1 AND currency_code = $2
                         AND original_amount IS NOT NULL
                         AND status NOT IN ('PAID', 'VOID')""",
                    tenant_id, code,
                )
                if int(has_exposure) == 0:
                    continue

                await self._upsert_alert(
                    conn, tenant_id, "RATE_STALE", code, today,
                    float(await conn.fetchval(
                        "SELECT inr_per_unit FROM dim_fx_rates WHERE currency_code = $1 ORDER BY rate_date DESC LIMIT 1",
                        code,
                    ) or 0),
                    None, None,
                    f"No fresh {code}/INR rate for {gap} days. "
                    f"Current rate may be outdated — amounts shown are estimates.",
                    [], 0, 0,
                )
                alerts += 1

        return alerts

    async def _calc_volatility(
        self, conn, currency: str, today: date
    ) -> float | None:
        rows = await conn.fetch(
            """SELECT inr_per_unit FROM dim_fx_rates
               WHERE currency_code = $1 AND rate_date > $2
               ORDER BY rate_date DESC LIMIT 7""",
            currency, today - timedelta(days=10),
        )
        if len(rows) < 5:
            return None

        values = [float(r["inr_per_unit"]) for r in rows]
        mean = sum(values) / len(values)
        if mean == 0:
            return None
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        std_dev = variance ** 0.5
        return round((std_dev / mean) * 100, 4)

    async def _upsert_alert(
        self,
        conn,
        tenant_id: str,
        alert_type: str,
        currency: str,
        rate_date: date,
        rate_inr: float,
        threshold_inr: float | None,
        direction: str | None,
        recommendation: str,
        bill_ids: list,
        exposure_foreign: float,
        exposure_inr: float,
    ) -> None:
        await conn.execute(
            """INSERT INTO fx_rate_alerts
                   (tenant_id, alert_type, currency_code, rate_date,
                    rate_inr, threshold_inr, direction, recommendation,
                    affected_bill_ids, exposure_foreign, exposure_inr, status)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'ACTIVE')
               ON CONFLICT (tenant_id, alert_type, currency_code, rate_date)
               DO UPDATE SET
                   rate_inr        = EXCLUDED.rate_inr,
                   threshold_inr   = EXCLUDED.threshold_inr,
                   recommendation  = EXCLUDED.recommendation,
                   affected_bill_ids = EXCLUDED.affected_bill_ids,
                   exposure_foreign = EXCLUDED.exposure_foreign,
                   exposure_inr    = EXCLUDED.exposure_inr,
                   status          = 'ACTIVE'
            """,
            tenant_id, alert_type, currency, rate_date,
            rate_inr, threshold_inr, direction, recommendation,
            bill_ids if bill_ids else None, exposure_foreign, exposure_inr,
        )
