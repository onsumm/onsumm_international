"""
Staging → Fact Promotion Pipeline
───────────────────────────────────
Reads staging records with sync_status = 'PENDING' and promotes them to the
corresponding fact tables.

Order matters:
  1. Promote invoices (creates fact_invoices rows — AR)
  2. Promote bills    (creates fact_bills rows — AP, separate table)
  3. Promote payments (creates fact_payments rows, updates fact_invoices.amount_paid)

fact_cashflow_daily is maintained separately by a pg_cron job (requires pg_cron extension).
Enable with: CREATE EXTENSION IF NOT EXISTS pg_cron;
Then schedule:
  SELECT cron.schedule('cashflow-recompute', '*/15 * * * *', $$
      INSERT INTO fact_cashflow_daily (
          tenant_id, cashflow_date, currency_code,
          inflows_actual, outflows_actual, net_cashflow,
          ar_outstanding, invoice_id)
      SELECT fi.tenant_id, CURRENT_DATE, fi.currency_code,
             fi.amount_paid, 0, fi.amount_paid, fi.outstanding_amount, fi.invoice_id
      FROM fact_invoices fi WHERE fi.invoice_type = 'AR'
      ON CONFLICT (tenant_id, invoice_id) DO UPDATE SET
          cashflow_date=EXCLUDED.cashflow_date, inflows_actual=EXCLUDED.inflows_actual,
          ar_outstanding=EXCLUDED.ar_outstanding, updated_at=now()
  $$);
"""
from __future__ import annotations

import logging
from decimal import Decimal

import asyncpg

import config
from db import get_pool
from models.enums import InvoiceStatus, RiskBucket, SyncStatus
from pipelines.base import BaseWorker
from utils.fiscal import fiscal_period

logger = logging.getLogger(__name__)


class FxRateMissing(Exception):
    """Raised when a foreign-currency bill/invoice has no FX rate in dim_fx_rates."""


async def _fetch_live_rate(conn: asyncpg.Connection, currency: str):
    """Fetch live rate from exchange rate API when dim_fx_rates is empty."""
    import httpx
    from datetime import date
    from decimal import ROUND_HALF_UP

    url = "https://open.er-api.com/v6/latest/INR"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("[fx] live rate fetch failed: %s", exc)
        return None

    if data.get("result") != "success":
        return None

    raw = data.get("rates", {}).get(currency)
    if not raw:
        return None

    inr_per_unit = (Decimal(1) / Decimal(str(raw))).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    today = date.today()

    rate_id = await conn.fetchval(
        """INSERT INTO dim_fx_rates (rate_date, currency_code, inr_per_unit, source, is_business_day)
           VALUES ($1, $2, $3, 'PRIMARY', $4)
           ON CONFLICT (rate_date, currency_code) DO UPDATE SET
               inr_per_unit = EXCLUDED.inr_per_unit, source = EXCLUDED.source, fetched_at = now()
           RETURNING rate_id""",
        today, currency, inr_per_unit, today.weekday() < 5,
    )
    logger.info("[fx] fetched live rate for %s: %s INR/unit", currency, inr_per_unit)
    return {"rate_id": rate_id, "inr_per_unit": inr_per_unit}


async def _convert_currency(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    as_of_date=None,
) -> dict:
    """
    Resolve FX for a staging row.  Returns a plain dict with the same keys as
    the original record plus any overrides.

    For INR rows: no-op — returns the row fields unchanged.
    For foreign rows:
      1. Look up the rate on or before as_of_date (bill_date/invoice_date) in
         dim_fx_rates — so a bill dated 2025-01-10 uses the Jan-10 rate, not
         today's rate, even if it's uploaded weeks later.
      2. If no rate exists before as_of_date, fall back to the earliest stored rate.
      3. If still not found: fetch live from exchange rate API (stored as today).
      4. If all else fails: raise FxRateMissing (caller sets validation_flag=REVIEW).
      5. Set total_amount = original_amount * inr_per_unit (INR canonical booking value).
      6. Return updated dict including fx_rate_id.
    """
    from datetime import date as _date
    d = dict(row)
    currency = (d.get("currency_code") or "INR")[:3].upper()
    if currency == "INR":
        return d

    original_amount = d.get("original_amount")
    if not original_amount:
        return d

    ref_date = as_of_date or _date.today()

    # Primary: rate on or before the bill/invoice date (historical lookup)
    rate_row = await conn.fetchrow(
        """
        SELECT rate_id, inr_per_unit
        FROM dim_fx_rates
        WHERE currency_code = $1
          AND rate_date <= $2
        ORDER BY rate_date DESC
        LIMIT 1
        """,
        currency,
        ref_date,
    )

    # Fallback 1: earliest stored rate (bill predates our history)
    if not rate_row:
        rate_row = await conn.fetchrow(
            """
            SELECT rate_id, inr_per_unit
            FROM dim_fx_rates
            WHERE currency_code = $1
            ORDER BY rate_date ASC
            LIMIT 1
            """,
            currency,
        )
        if rate_row:
            logger.warning(
                "[fx] no rate on/before %s for %s — using earliest available rate",
                ref_date, currency,
            )

    # Fallback 2: live fetch (stores as today)
    if not rate_row:
        rate_row = await _fetch_live_rate(conn, currency)
        if not rate_row:
            raise FxRateMissing(f"No FX rate found for {currency}")

    inr_per_unit = Decimal(str(rate_row["inr_per_unit"]))
    d["fx_rate_id"]    = rate_row["rate_id"]
    d["total_amount"]  = float(Decimal(str(original_amount)) * inr_per_unit)
    d["outstanding_amount"] = d["total_amount"]
    return d


# ── Invoice promotion ──────────────────────────────────────────────────────────

async def _promote_invoice(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
) -> None:
    """Upsert stg_invoice → fact_invoices."""
    stg_id   = row["stg_invoice_id"]
    tenant   = str(row["tenant_id"])
    inv_date = row["invoice_date"]

    fy, fq, fm = fiscal_period(inv_date)

    # FX conversion: use invoice_date as reference so historical rate is applied
    d = await _convert_currency(conn, row, as_of_date=inv_date)

    # Fetch current risk_bucket from the customer
    risk_bucket = await conn.fetchval(
        "SELECT risk_bucket FROM dim_end_customers WHERE end_customer_id=$1",
        row["end_customer_id"],
    )

    await conn.execute(
        """
        INSERT INTO fact_invoices (
            tenant_id, stg_invoice_id, end_customer_id, account_id,
            invoice_number, invoice_date, due_date,
            fiscal_year, fiscal_quarter, fiscal_month,
            currency_code, subtotal, tax_amount, total_amount,
            amount_paid, outstanding_amount,
            status, risk_bucket,
            source, source_updated_at,
            original_amount, fx_rate_id,
            invoice_type
        ) VALUES (
            $1,$2,$3,$4,
            $5,$6,$7,
            $8,$9,$10,
            $11,$12,$13,$14,
            $15,$16,
            $17,$18,
            $19,$20,
            $21,$22,
            'AR'
        )
        ON CONFLICT (stg_invoice_id) DO UPDATE SET
            status             = EXCLUDED.status,
            amount_paid        = EXCLUDED.amount_paid,
            outstanding_amount = EXCLUDED.outstanding_amount,
            risk_bucket        = EXCLUDED.risk_bucket,
            due_date           = EXCLUDED.due_date,
            source_updated_at  = EXCLUDED.source_updated_at,
            original_amount    = EXCLUDED.original_amount,
            fx_rate_id         = EXCLUDED.fx_rate_id,
            updated_at         = now()
        """,
        tenant, stg_id, d["end_customer_id"], d.get("account_id"),
        d["invoice_number"], inv_date, d.get("due_date"),
        fy, fq, fm,
        (d["currency_code"] or "INR")[:3],
        d["subtotal"], d["tax_amount"], d["total_amount"],
        d["amount_paid"], d.get("outstanding_amount"),
        d["status"], risk_bucket,
        d["source"], d["source_updated_at"],
        d.get("original_amount"), d.get("fx_rate_id"),
    )

    await conn.execute(
        "UPDATE stg_invoices SET sync_status='SYNCED' WHERE stg_invoice_id=$1",
        stg_id,
    )
    await BaseWorker.log_audit(
        conn,
        tenant_id=tenant,
        table_name="fact_invoices",
        entity_id=stg_id,
        action="INSERT",
        new_values={"invoice_number": row["invoice_number"], "status": row["status"],
                    "total_amount": float(row["total_amount"] or 0)},
    )


async def _promote_bill(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
) -> None:
    """Upsert stg_bill → fact_bills (AP fact table, separate from fact_invoices)."""
    stg_bill_id = row["stg_bill_id"]
    tenant      = str(row["tenant_id"])
    bill_date   = row["bill_date"]

    fy, fq, fm = fiscal_period(bill_date)

    # FX conversion: use bill_date as reference so historical rate is applied
    d = await _convert_currency(conn, row, as_of_date=bill_date)

    await conn.execute(
        """
        INSERT INTO fact_bills (
            tenant_id, stg_bill_id, supplier_id,
            bill_number, bill_date, due_date,
            fiscal_year, fiscal_quarter, fiscal_month,
            currency_code, subtotal, tax_amount, total_amount,
            amount_paid, outstanding_amount,
            status, source, source_updated_at,
            tds_section_id, tds_rate, tds_base_amount, tds_amount,
            tds_status, tds_section_code, tds_rate_percent, net_payable,
            original_amount, fx_rate_id
        ) VALUES (
            $1,$2,$3,
            $4,$5,$6,
            $7,$8,$9,
            $10,$11,$12,$13,
            $14,$15,
            $16,$17,$18,
            $19,$20,$21,$22,
            $23,$24,$25,$26,
            $27,$28
        )
        ON CONFLICT (stg_bill_id) DO UPDATE SET
            status             = EXCLUDED.status,
            amount_paid        = EXCLUDED.amount_paid,
            outstanding_amount = EXCLUDED.outstanding_amount,
            due_date           = EXCLUDED.due_date,
            source_updated_at  = EXCLUDED.source_updated_at,
            tds_section_id     = EXCLUDED.tds_section_id,
            tds_rate           = EXCLUDED.tds_rate,
            tds_base_amount    = EXCLUDED.tds_base_amount,
            tds_amount         = EXCLUDED.tds_amount,
            tds_status         = EXCLUDED.tds_status,
            tds_section_code   = EXCLUDED.tds_section_code,
            tds_rate_percent   = EXCLUDED.tds_rate_percent,
            net_payable        = EXCLUDED.net_payable,
            original_amount    = EXCLUDED.original_amount,
            fx_rate_id         = EXCLUDED.fx_rate_id,
            updated_at         = now()
        """,
        tenant, stg_bill_id, d["supplier_id"],
        d["bill_number"], bill_date, d.get("due_date"),
        str(fy), str(fq), str(fm),
        (d["currency_code"] or "INR")[:3],
        d["subtotal"], d["tax_amount"], d["total_amount"],
        d["amount_paid"], d.get("outstanding_amount"),
        d["status"], d["source"], d["source_updated_at"],
        d.get("tds_section_id"), d.get("tds_rate"), d.get("tds_base_amount"),
        d.get("tds_amount"), d.get("tds_status") or "PENDING",
        d.get("tds_section_code"), d.get("tds_rate_percent"), d.get("net_payable"),
        d.get("original_amount"), d.get("fx_rate_id"),
    )

    await conn.execute(
        "UPDATE stg_bills SET sync_status='SYNCED' WHERE stg_bill_id=$1",
        stg_bill_id,
    )
    await BaseWorker.log_audit(
        conn,
        tenant_id=tenant,
        table_name="fact_bills",
        entity_id=stg_bill_id,
        action="INSERT",
        new_values={"bill_number": row["bill_number"], "status": row["status"],
                    "total_amount": float(row["total_amount"] or 0)},
    )


async def _promote_payment(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
) -> None:
    """Upsert stg_payment → fact_payments and update linked invoice amounts."""
    stg_pmt_id = row["stg_payment_id"]
    tenant     = str(row["tenant_id"])
    pmt_date   = row["payment_date"]

    fy, fq, _ = fiscal_period(pmt_date)

    # Resolve fact_invoice_id from stg_invoice_id if linked
    fact_invoice_id = None
    if row.get("stg_invoice_id"):
        fact_invoice_id = await conn.fetchval(
            "SELECT invoice_id FROM fact_invoices WHERE stg_invoice_id=$1",
            row["stg_invoice_id"],
        )

    await conn.execute(
        """
        INSERT INTO fact_payments (
            tenant_id, stg_payment_id, invoice_id, end_customer_id,
            bank_account_id, payment_date,
            fiscal_year, fiscal_quarter,
            amount, currency_code,
            payment_method, transaction_ref, gateway,
            status, source, source_updated_at
        ) VALUES (
            $1,$2,$3,$4,
            $5,$6,
            $7,$8,
            $9,$10,
            $11,$12,$13,
            $14,$15,$16
        )
        ON CONFLICT (stg_payment_id) DO UPDATE SET
            status            = EXCLUDED.status,
            invoice_id        = COALESCE(EXCLUDED.invoice_id, fact_payments.invoice_id),
            source_updated_at = EXCLUDED.source_updated_at
        """,
        tenant, stg_pmt_id, fact_invoice_id, row["end_customer_id"],
        row.get("bank_account_id"), pmt_date,
        fy, fq,
        row["amount"], (row["currency_code"] or "INR")[:3],
        row.get("payment_method"), row.get("transaction_ref"), row.get("gateway"),
        row["status"], row["source"], row["source_updated_at"],
    )

    # Update the linked invoice's amount_paid and outstanding_amount
    if fact_invoice_id:
        await conn.execute(
            """
            UPDATE fact_invoices SET
                amount_paid        = (
                    SELECT COALESCE(SUM(amount), 0)
                    FROM fact_payments
                    WHERE invoice_id = $1 AND status = 'CLEARED'
                ),
                outstanding_amount = GREATEST(
                    total_amount - (
                        SELECT COALESCE(SUM(amount), 0)
                        FROM fact_payments
                        WHERE invoice_id = $1 AND status = 'CLEARED'
                    ),
                    0
                ),
                status = CASE
                    WHEN total_amount <= (
                        SELECT COALESCE(SUM(amount), 0)
                        FROM fact_payments
                        WHERE invoice_id = $1 AND status = 'CLEARED'
                    ) THEN 'PAID'
                    WHEN (
                        SELECT COALESCE(SUM(amount), 0)
                        FROM fact_payments
                        WHERE invoice_id = $1 AND status = 'CLEARED'
                    ) > 0 THEN 'PARTIAL'
                    ELSE status
                END,
                updated_at = now()
            WHERE invoice_id = $1
            """,
            fact_invoice_id,
        )
        # Mirror back to stg_invoices
        if row.get("stg_invoice_id"):
            await conn.execute(
                """
                UPDATE stg_invoices SET
                    amount_paid        = fi.amount_paid,
                    outstanding_amount = fi.outstanding_amount,
                    status             = fi.status,
                    updated_at         = now()
                FROM fact_invoices fi
                WHERE stg_invoices.stg_invoice_id = $1
                  AND fi.stg_invoice_id = $1
                """,
                row["stg_invoice_id"],
            )

    await conn.execute(
        "UPDATE stg_payments SET sync_status='SYNCED' WHERE stg_payment_id=$1",
        stg_pmt_id,
    )
    await BaseWorker.log_audit(
        conn,
        tenant_id=tenant,
        table_name="fact_payments",
        entity_id=stg_pmt_id,
        action="INSERT",
        new_values={"amount": float(row["amount"] or 0), "status": row["status"],
                    "payment_method": row.get("payment_method")},
    )


# ── Worker ─────────────────────────────────────────────────────────────────────

class StagingPromotionWorker(BaseWorker):
    name           = "staging_promotion"
    notify_channel = "ch_staging_promotion"

    async def process_batch(self) -> int:
        total = 0
        total += await self._promote_invoices()
        total += await self._promote_bills()
        total += await self._promote_payments()
        return total

    async def _promote_invoices(self) -> int:
        pool = await get_pool()
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT stg_invoice_id, tenant_id, source, source_updated_at,
                           end_customer_id, account_id,
                           invoice_number, invoice_date, due_date,
                           currency_code, subtotal, tax_amount, total_amount,
                           amount_paid, outstanding_amount,
                           original_amount, fx_rate_id,
                           status
                    FROM stg_invoices
                    WHERE sync_status = 'PENDING'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT stg_invoice_id, tenant_id, source, source_updated_at,
                           end_customer_id, account_id,
                           invoice_number, invoice_date, due_date,
                           currency_code, subtotal, tax_amount, total_amount,
                           amount_paid, outstanding_amount,
                           original_amount, fx_rate_id,
                           status
                    FROM stg_invoices
                    WHERE sync_status = 'PENDING'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        promoted = 0
        for row in rows:
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _promote_invoice(conn, row)
                promoted += 1
                logger.debug("[promotion] invoice stg=%s promoted", row["stg_invoice_id"])

            except FxRateMissing as exc:
                logger.warning(
                    "[promotion] invoice stg=%s blocked — no FX rate (%s), set REVIEW",
                    row["stg_invoice_id"], exc,
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE stg_invoices SET validation_flag='REVIEW', sync_status='PENDING' WHERE stg_invoice_id=$1",
                            row["stg_invoice_id"],
                        )

            except Exception as exc:
                logger.exception("[promotion] invoice stg=%s failed", row["stg_invoice_id"])
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE stg_invoices SET sync_status='ERROR' WHERE stg_invoice_id=$1",
                            row["stg_invoice_id"],
                        )
                        await self.log_error(
                            conn,
                            tenant_id=str(row["tenant_id"]),
                            stage="promote_invoice",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)},
                        )

        if promoted:
            logger.info("[promotion] invoices: %d promoted", promoted)
        return promoted

    async def _promote_bills(self) -> int:
        pool = await get_pool()
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT stg_bill_id, tenant_id, source, source_updated_at,
                           supplier_id,
                           bill_number, bill_date, due_date,
                           currency_code, subtotal, tax_amount, total_amount,
                           amount_paid, outstanding_amount,
                           original_amount, fx_rate_id,
                           status,
                           tds_section_id, tds_rate, tds_base_amount, tds_amount,
                           tds_status, tds_section_code, tds_rate_percent, net_payable
                    FROM stg_bills
                    WHERE sync_status = 'PENDING'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT stg_bill_id, tenant_id, source, source_updated_at,
                           supplier_id,
                           bill_number, bill_date, due_date,
                           currency_code, subtotal, tax_amount, total_amount,
                           amount_paid, outstanding_amount,
                           original_amount, fx_rate_id,
                           status,
                           tds_section_id, tds_rate, tds_base_amount, tds_amount,
                           tds_status, tds_section_code, tds_rate_percent, net_payable
                    FROM stg_bills
                    WHERE sync_status = 'PENDING'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        promoted = 0
        for row in rows:
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _promote_bill(conn, row)
                promoted += 1

            except FxRateMissing as exc:
                logger.warning(
                    "[promotion] bill stg=%s blocked — no FX rate (%s), set REVIEW",
                    row["stg_bill_id"], exc,
                )
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE stg_bills SET validation_flag='REVIEW', sync_status='PENDING' WHERE stg_bill_id=$1",
                            row["stg_bill_id"],
                        )

            except Exception as exc:
                logger.exception("[promotion] bill stg=%s failed", row["stg_bill_id"])
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE stg_bills SET sync_status='ERROR' WHERE stg_bill_id=$1",
                            row["stg_bill_id"],
                        )
                        await self.log_error(
                            conn,
                            tenant_id=str(row["tenant_id"]),
                            stage="promote_bill",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)},
                        )

        if promoted:
            logger.info("[promotion] bills: %d promoted", promoted)
        return promoted

    async def _promote_payments(self) -> int:
        pool = await get_pool()
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT stg_payment_id, tenant_id, source, source_updated_at,
                           stg_invoice_id, end_customer_id, bank_account_id,
                           payment_date, amount, currency_code,
                           payment_method, transaction_ref, gateway, status
                    FROM stg_payments
                    WHERE sync_status = 'PENDING'
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT stg_payment_id, tenant_id, source, source_updated_at,
                           stg_invoice_id, end_customer_id, bank_account_id,
                           payment_date, amount, currency_code,
                           payment_method, transaction_ref, gateway, status
                    FROM stg_payments
                    WHERE sync_status = 'PENDING'
                    ORDER BY created_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        promoted = 0
        for row in rows:
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _promote_payment(conn, row)
                promoted += 1

            except Exception as exc:
                logger.exception("[promotion] payment stg=%s failed", row["stg_payment_id"])
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "UPDATE stg_payments SET sync_status='ERROR' WHERE stg_payment_id=$1",
                            row["stg_payment_id"],
                        )
                        await self.log_error(
                            conn,
                            tenant_id=str(row["tenant_id"]),
                            stage="promote_payment",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)},
                        )

        if promoted:
            logger.info("[promotion] payments: %d promoted", promoted)
        return promoted
