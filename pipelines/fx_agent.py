"""
FxCurrencyAgent — AI-powered FX insight generator using GPT-4o-mini.

Three components (per spec section 10):
  1. Rate Engine      — handled by FxRateWorker (deterministic, no LLM)
  2. Analysis Engine  — computes structured FxAgentContext (deterministic)
  3. GPT Insight Gen  — calls GPT-4o-mini with context to produce natural-language notes

Runs daily after FxRateWorker has refreshed rates. Generates per-bill notes
(fx_agent_notes table) and updates fact_bills.fx_agent_note for bills with
material FX impact.

Poll interval: 4 hours (but skips if today's notes already generated).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import httpx

import config
from db import get_pool
from pipelines.base import BaseWorker

logger = logging.getLogger(__name__)

_OPENAI_API_KEY: str = config.OPENAI_API_KEY
_CURRENCIES: list[str] = config.FX_FETCH_CURRENCIES

_RATE_Q = Decimal("0.000001")
_INR_Q = Decimal("0.01")


# ── Structured context for the LLM ──────────────────────────────────────────

@dataclass
class CurrencyRateContext:
    currency: str
    today: float | None = None
    week_ago: float | None = None
    month_ago: float | None = None
    trend_pct: float | None = None
    volatility_pct: float | None = None


@dataclass
class BillContext:
    bill_id: str
    tenant_id: str
    vendor: str | None
    amount_foreign: float
    currency: str
    due_date: str | None
    booked_rate: float | None
    current_rate: float | None
    current_inr: float | None
    fx_impact_inr: float | None
    days_to_due: int | None = None


@dataclass
class PaymentContext:
    bill_id: str
    vendor: str | None
    amount_foreign: float
    currency: str
    booked_rate: float | None
    effective_rate: float
    bank_spread_pct: float | None
    fx_cost: float | None


@dataclass
class ExposureContext:
    currency: str
    ap_foreign: float
    ar_foreign: float
    net_foreign: float
    ap_inr: float
    ar_inr: float


@dataclass
class FxAgentContext:
    rates: list[CurrencyRateContext] = field(default_factory=list)
    outstanding_bills: list[BillContext] = field(default_factory=list)
    recent_payments: list[PaymentContext] = field(default_factory=list)
    net_exposure: list[ExposureContext] = field(default_factory=list)
    avg_bank_spread: dict[str, float] = field(default_factory=dict)
    total_fx_cost_qtd: float = 0.0


# ── GPT prompts (per spec section 10.2) ──────────────────────────────────────

FX_AGENT_PROMPTS = {
    "bill_note": (
        "You are an FX advisor for an Indian SME. Given this bill and rate context, "
        "write 1-2 sentences for the bill detail view.\n\n"
        "Rules:\n"
        "- Only write if there's an actionable insight or notable fact\n"
        "- If FX impact < Rs 500 or rate change < 1%, return empty note\n"
        "- Be specific: mention amounts, dates, percentages\n"
        "- Suggest actions when appropriate\n"
        "- Never use jargon (hedge, forward contract, etc.)\n"
        "- Use Rs or INR for rupee amounts\n\n"
        "Context:\n{context_json}\n\n"
        'Return ONLY valid JSON: {{"note": "...", "severity": "INFO|WARNING|OPPORTUNITY", "confidence": 0.0-1.0}}\n'
        'If nothing worth saying, return: {{"note": "", "severity": "NONE", "confidence": 1.0}}'
    ),
    "payment_analysis": (
        "User just paid a foreign currency bill. Back-calculation done. "
        "Write 1-2 sentences about this payment.\n"
        "Flag if bank spread is higher than their average.\n"
        "Compare to previous payments.\n\n"
        "Payment: {payment_json}\n"
        "History: {history_json}\n\n"
        'Return ONLY valid JSON: {{"note": "...", "severity": "INFO|WARNING|OPPORTUNITY", "confidence": 0.0-1.0}}'
    ),
    "exposure_briefing": (
        "Write a 3-4 sentence briefing for the CFO about their current "
        "foreign currency exposure. Lead with the most important insight. "
        "Quantify everything in rupees. If there's a clear action, say it. "
        "Compare to their own history, not abstract benchmarks.\n\n"
        "Context:\n{context_json}\n\n"
        'Return ONLY valid JSON: {{"note": "...", "severity": "INFO|WARNING|OPPORTUNITY", "confidence": 0.0-1.0}}'
    ),
}


# ── GPT-4o-mini caller ───────────────────────────────────────────────────────

async def _call_gpt(prompt: str) -> dict | None:
    if not _OPENAI_API_KEY:
        logger.warning("[fx_agent] OPENAI_API_KEY not set — skipping GPT call")
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {_OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 200,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:
        logger.warning("[fx_agent] GPT call failed: %s", exc)
        return None


# ── FxCurrencyAgent worker ───────────────────────────────────────────────────

class FxCurrencyAgent(BaseWorker):
    name = "fx_agent"
    notify_channel = None
    skip_backfill = True

    async def process_batch(self) -> int:
        today = date.today()

        pool = await get_pool()
        async with pool.acquire() as conn:
            already_run = await conn.fetchval(
                """SELECT COUNT(*) FROM fx_agent_notes
                   WHERE created_at::date = $1""",
                today,
            )
            if int(already_run) > 0:
                logger.debug("[fx_agent] already generated notes today — skipping")
                return 0

            has_rates = await conn.fetchval(
                """SELECT COUNT(*) FROM dim_fx_rates
                   WHERE rate_date = $1""",
                today,
            )
            if int(has_rates) == 0:
                logger.debug("[fx_agent] no rates for today yet — skipping")
                return 0

            context = await self._build_context(conn, today)
            generated = await self._generate_bill_notes(conn, context, today)

        logger.info("[fx_agent] generated %d bill notes", generated)
        return generated

    # ── Build structured context ─────────────────────────────────────────────

    async def _build_context(self, conn, today: date) -> FxAgentContext:
        ctx = FxAgentContext()
        ctx.rates = await self._build_rate_context(conn, today)
        ctx.outstanding_bills = await self._build_bill_context(conn, today)
        ctx.recent_payments = await self._build_payment_context(conn, today)
        ctx.net_exposure = await self._build_exposure_context(conn)
        ctx.avg_bank_spread = await self._build_avg_spread(conn)
        ctx.total_fx_cost_qtd = await self._build_qtd_cost(conn, today)
        return ctx

    async def _build_rate_context(
        self, conn, today: date
    ) -> list[CurrencyRateContext]:
        contexts = []
        for code in _CURRENCIES:
            rows = await conn.fetch(
                """SELECT rate_date, inr_per_unit FROM dim_fx_rates
                   WHERE currency_code = $1 AND rate_date <= $2
                   ORDER BY rate_date DESC LIMIT 30""",
                code, today,
            )
            if not rows:
                continue

            today_rate = float(rows[0]["inr_per_unit"])
            week_rate = None
            month_rate = None

            for r in rows:
                if r["rate_date"] <= today - timedelta(days=7) and week_rate is None:
                    week_rate = float(r["inr_per_unit"])
                if r["rate_date"] <= today - timedelta(days=30) and month_rate is None:
                    month_rate = float(r["inr_per_unit"])

            trend_pct = None
            if week_rate and week_rate > 0:
                trend_pct = round(((today_rate - week_rate) / week_rate) * 100, 2)

            volatility_pct = None
            if len(rows) >= 7:
                recent_7 = [float(r["inr_per_unit"]) for r in rows[:7]]
                mean = sum(recent_7) / len(recent_7)
                if mean > 0:
                    variance = sum((x - mean) ** 2 for x in recent_7) / len(recent_7)
                    std_dev = variance ** 0.5
                    volatility_pct = round((std_dev / mean) * 100, 4)

            contexts.append(CurrencyRateContext(
                currency=code,
                today=today_rate,
                week_ago=week_rate,
                month_ago=month_rate,
                trend_pct=trend_pct,
                volatility_pct=volatility_pct,
            ))
        return contexts

    async def _build_bill_context(
        self, conn, today: date
    ) -> list[BillContext]:
        rows = await conn.fetch(
            """SELECT fb.bill_id, fb.tenant_id, s.name AS vendor_name,
                      fb.original_amount, fb.currency_code, fb.due_date,
                      fb.total_amount, fb.inr_amount_current, fb.fx_gain_loss,
                      dr.inr_per_unit AS current_rate
               FROM fact_bills fb
               LEFT JOIN dim_suppliers s ON s.supplier_id = fb.supplier_id
               LEFT JOIN LATERAL (
                   SELECT inr_per_unit FROM dim_fx_rates
                   WHERE currency_code = fb.currency_code
                   ORDER BY rate_date DESC LIMIT 1
               ) dr ON true
               WHERE fb.currency_code != 'INR'
                 AND fb.original_amount IS NOT NULL
                 AND fb.status NOT IN ('PAID', 'VOID')
               ORDER BY ABS(COALESCE(fb.fx_gain_loss, 0)) DESC
               LIMIT 50""",
        )

        bills = []
        for r in rows:
            booked_rate = None
            if r["original_amount"] and r["total_amount"] and float(r["original_amount"]) > 0:
                booked_rate = round(float(r["total_amount"]) / float(r["original_amount"]), 4)

            days_to_due = None
            if r["due_date"]:
                days_to_due = (r["due_date"] - today).days

            bills.append(BillContext(
                bill_id=str(r["bill_id"]),
                tenant_id=str(r["tenant_id"]),
                vendor=r["vendor_name"],
                amount_foreign=float(r["original_amount"]) if r["original_amount"] else 0,
                currency=r["currency_code"].strip() if r["currency_code"] else "USD",
                due_date=r["due_date"].isoformat() if r["due_date"] else None,
                booked_rate=booked_rate,
                current_rate=float(r["current_rate"]) if r["current_rate"] else None,
                current_inr=float(r["inr_amount_current"]) if r["inr_amount_current"] else None,
                fx_impact_inr=float(r["fx_gain_loss"]) if r["fx_gain_loss"] else 0,
                days_to_due=days_to_due,
            ))
        return bills

    async def _build_payment_context(
        self, conn, today: date
    ) -> list[PaymentContext]:
        rows = await conn.fetch(
            """SELECT fp.invoice_id, s.name AS vendor_name,
                      fp.foreign_amount, fp.currency_code,
                      fp.effective_rate, fp.bank_spread_pct,
                      fp.actual_inr, fb.total_amount
               FROM fx_bill_payments fp
               JOIN fact_bills fb ON fb.bill_id = fp.invoice_id
               LEFT JOIN dim_suppliers s ON s.supplier_id = fb.supplier_id
               WHERE fp.payment_date >= $1
               ORDER BY fp.payment_date DESC
               LIMIT 20""",
            today - timedelta(days=90),
        )

        payments = []
        for r in rows:
            booked_rate = None
            fx_cost = None
            if r["total_amount"] and r["actual_inr"]:
                fx_cost = float(r["actual_inr"]) - float(r["total_amount"])
            if r["foreign_amount"] and float(r["foreign_amount"]) > 0 and r["total_amount"]:
                booked_rate = round(float(r["total_amount"]) / float(r["foreign_amount"]), 4)

            payments.append(PaymentContext(
                bill_id=str(r["invoice_id"]),
                vendor=r["vendor_name"],
                amount_foreign=float(r["foreign_amount"]) if r["foreign_amount"] else 0,
                currency=(r["currency_code"] or "").strip(),
                booked_rate=booked_rate,
                effective_rate=float(r["effective_rate"]) if r["effective_rate"] else 0,
                bank_spread_pct=float(r["bank_spread_pct"]) if r["bank_spread_pct"] else None,
                fx_cost=fx_cost,
            ))
        return payments

    async def _build_exposure_context(self, conn) -> list[ExposureContext]:
        rows = await conn.fetch(
            """SELECT currency_code,
                      SUM(CASE WHEN original_amount > 0 THEN original_amount ELSE 0 END) AS ap_foreign,
                      SUM(CASE WHEN original_amount > 0 THEN COALESCE(inr_amount_current, total_amount) ELSE 0 END) AS ap_inr
               FROM fact_bills
               WHERE currency_code != 'INR'
                 AND original_amount IS NOT NULL
                 AND status NOT IN ('PAID', 'VOID')
               GROUP BY currency_code""",
        )
        exposures = []
        for r in rows:
            exposures.append(ExposureContext(
                currency=(r["currency_code"] or "").strip(),
                ap_foreign=float(r["ap_foreign"]) if r["ap_foreign"] else 0,
                ar_foreign=0,
                net_foreign=float(r["ap_foreign"]) if r["ap_foreign"] else 0,
                ap_inr=float(r["ap_inr"]) if r["ap_inr"] else 0,
                ar_inr=0,
            ))
        return exposures

    async def _build_avg_spread(self, conn) -> dict[str, float]:
        rows = await conn.fetch(
            """SELECT currency_code, AVG(bank_spread_pct) AS avg_spread
               FROM fx_bill_payments
               WHERE bank_spread_pct IS NOT NULL
               GROUP BY currency_code""",
        )
        return {
            (r["currency_code"] or "").strip(): round(float(r["avg_spread"]), 2)
            for r in rows
        }

    async def _build_qtd_cost(self, conn, today: date) -> float:
        month = today.month
        if month <= 3:
            q_start = date(today.year - 1, 4, 1) if month <= 3 else date(today.year, 1, 1)
        elif month <= 6:
            q_start = date(today.year, 4, 1)
        elif month <= 9:
            q_start = date(today.year, 7, 1)
        else:
            q_start = date(today.year, 10, 1)

        # Indian FY quarters: Apr-Jun, Jul-Sep, Oct-Dec, Jan-Mar
        if month in (1, 2, 3):
            q_start = date(today.year, 1, 1)

        result = await conn.fetchval(
            """SELECT COALESCE(SUM(actual_inr - foreign_amount *
                   (SELECT inr_per_unit FROM dim_fx_rates
                    WHERE currency_code = fp.currency_code
                    ORDER BY rate_date DESC LIMIT 1)), 0)
               FROM fx_bill_payments fp
               WHERE payment_date >= $1""",
            q_start,
        )
        return float(result) if result else 0.0

    # ── Generate per-bill notes ──────────────────────────────────────────────

    async def _generate_bill_notes(
        self, conn, context: FxAgentContext, today: date
    ) -> int:
        generated = 0

        rate_map = {r.currency: r for r in context.rates}

        for bill in context.outstanding_bills:
            if not self._is_worth_generating(bill, rate_map):
                continue

            rate_ctx = rate_map.get(bill.currency)
            bill_with_rates = {
                "bill": {
                    "vendor": bill.vendor,
                    "amount_foreign": bill.amount_foreign,
                    "currency": bill.currency,
                    "due_date": bill.due_date,
                    "booked_rate": bill.booked_rate,
                    "current_rate": bill.current_rate,
                    "fx_impact_inr": bill.fx_impact_inr,
                    "days_to_due": bill.days_to_due,
                },
                "rate": asdict(rate_ctx) if rate_ctx else None,
                "avg_bank_spread": context.avg_bank_spread.get(bill.currency),
            }

            prompt = FX_AGENT_PROMPTS["bill_note"].format(
                context_json=json.dumps(bill_with_rates, default=str)
            )

            result = await _call_gpt(prompt)
            if not result:
                continue

            note_text = result.get("note", "")
            severity = result.get("severity", "NONE")
            confidence = result.get("confidence", 0.5)

            if not note_text or severity == "NONE":
                continue

            # Invalidate previous notes for this bill
            await conn.execute(
                """UPDATE fx_agent_notes SET is_current = false
                   WHERE target_type = 'BILL' AND target_id = $1 AND is_current = true""",
                bill.bill_id,
            )

            # Insert new note
            await conn.execute(
                """INSERT INTO fx_agent_notes
                       (tenant_id, target_type, target_id, currency_code,
                        severity, note_text, confidence,
                        context_snapshot, rate_at_generation, is_current)
                   VALUES ($1, 'BILL', $2, $3, $4, $5, $6, $7, $8, true)""",
                bill.tenant_id,
                bill.bill_id,
                bill.currency,
                severity,
                note_text,
                confidence,
                json.dumps(bill_with_rates, default=str),
                rate_ctx.today if rate_ctx else None,
            )

            # Update denormalized columns on fact_bills
            await conn.execute(
                """UPDATE fact_bills
                   SET fx_agent_note = $1, fx_agent_note_at = now()
                   WHERE bill_id = $2""",
                note_text,
                bill.bill_id,
            )

            generated += 1
            logger.debug(
                "[fx_agent] note for bill %s [%s]: %s",
                bill.bill_id, severity, note_text[:80],
            )

        return generated

    def _is_worth_generating(
        self, bill: BillContext, rate_map: dict[str, CurrencyRateContext]
    ) -> bool:
        """Only call GPT when there's something non-obvious to say (spec 10.4)."""
        if abs(bill.fx_impact_inr or 0) < 500:
            return False

        rate_ctx = rate_map.get(bill.currency)
        if rate_ctx and rate_ctx.trend_pct is not None:
            if abs(rate_ctx.trend_pct) < 1.0:
                return False

        return True

    # ── Public: generate a payment insight on demand ─────────────────────────

    async def generate_payment_note(
        self, conn, bill_id: str, tenant_id: str, payment: dict
    ) -> dict | None:
        history = await conn.fetch(
            """SELECT foreign_amount, actual_inr, effective_rate,
                      bank_spread_pct, payment_date
               FROM fx_bill_payments
               WHERE invoice_id = $1 AND tenant_id = $2
               ORDER BY payment_date DESC LIMIT 5""",
            bill_id, tenant_id,
        )

        history_list = [
            {
                "foreign_amount": float(r["foreign_amount"]),
                "actual_inr": float(r["actual_inr"]),
                "effective_rate": float(r["effective_rate"]),
                "bank_spread_pct": float(r["bank_spread_pct"]) if r["bank_spread_pct"] else None,
                "payment_date": r["payment_date"].isoformat() if r["payment_date"] else None,
            }
            for r in history
        ]

        prompt = FX_AGENT_PROMPTS["payment_analysis"].format(
            payment_json=json.dumps(payment, default=str),
            history_json=json.dumps(history_list, default=str),
        )

        result = await _call_gpt(prompt)
        if not result or not result.get("note"):
            return None

        await conn.execute(
            """UPDATE fx_agent_notes SET is_current = false
               WHERE target_type = 'BILL' AND target_id = $1 AND is_current = true""",
            bill_id,
        )

        await conn.execute(
            """INSERT INTO fx_agent_notes
                   (tenant_id, target_type, target_id, currency_code,
                    severity, note_text, confidence,
                    context_snapshot, is_current)
               VALUES ($1, 'BILL', $2, $3, $4, $5, $6, $7, true)""",
            tenant_id,
            bill_id,
            payment.get("currency_code", "USD"),
            result.get("severity", "INFO"),
            result["note"],
            result.get("confidence", 0.7),
            json.dumps({"payment": payment, "history": history_list}, default=str),
        )

        return result

    # ── Public: generate exposure briefing on demand ─────────────────────────

    async def generate_exposure_briefing(
        self, conn, tenant_id: str
    ) -> dict | None:
        today = date.today()
        context = await self._build_context(conn, today)

        tenant_exposure = [e for e in context.net_exposure]
        if not tenant_exposure:
            return None

        ctx_dict = {
            "rates": [asdict(r) for r in context.rates],
            "exposure": [asdict(e) for e in tenant_exposure],
            "avg_bank_spread": context.avg_bank_spread,
            "total_fx_cost_qtd": context.total_fx_cost_qtd,
            "outstanding_bill_count": len(context.outstanding_bills),
        }

        prompt = FX_AGENT_PROMPTS["exposure_briefing"].format(
            context_json=json.dumps(ctx_dict, default=str)
        )

        result = await _call_gpt(prompt)
        if not result or not result.get("note"):
            return None

        await conn.execute(
            """INSERT INTO fx_agent_notes
                   (tenant_id, target_type, target_id, currency_code,
                    severity, note_text, confidence,
                    context_snapshot, is_current)
               VALUES ($1, 'EXPOSURE', NULL, 'USD', $2, $3, $4, $5, true)""",
            tenant_id,
            result.get("severity", "INFO"),
            result["note"],
            result.get("confidence", 0.7),
            json.dumps(ctx_dict, default=str),
        )

        return result
