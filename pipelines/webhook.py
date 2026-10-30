"""
Webhook Pipeline
────────────────
Polls raw_webhook_events (is_processed = false) and promotes payment gateway
events into stg_payments (and optionally stg_invoices for Stripe invoices).

Supported sources: RAZORPAY, STRIPE
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

import asyncpg

import config
from db import get_pool
from models.enums import DataSource, PaymentMethod, PaymentStatus, SyncStatus
from pipelines.base import BaseWorker

logger = logging.getLogger(__name__)

_audit = BaseWorker.log_audit


# ── Helpers ────────────────────────────────────────────────────────────────────

def _paise_to_rupees(amount_paise: Any) -> float:
    try:
        return float(amount_paise) / 100
    except (TypeError, ValueError):
        return 0.0


def _stripe_cents_to_units(amount: Any) -> float:
    try:
        return float(amount) / 100
    except (TypeError, ValueError):
        return 0.0


def _ts_to_date(ts: Any) -> date:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
    except (TypeError, ValueError):
        return date.today()


_RAZORPAY_METHOD_MAP: dict[str, PaymentMethod] = {
    "card":          PaymentMethod.CARD,
    "netbanking":    PaymentMethod.BANK_TRANSFER,
    "wallet":        PaymentMethod.GATEWAY,
    "emi":           PaymentMethod.CARD,
    "upi":           PaymentMethod.UPI,
    "bank_transfer": PaymentMethod.BANK_TRANSFER,
}


async def _resolve_or_create_customer(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_customer_id: str,
    company_name: str,
    email: str | None = None,
) -> str:
    row = await conn.fetchrow(
        "SELECT end_customer_id FROM dim_end_customers "
        "WHERE tenant_id=$1 AND source=$2 AND source_customer_id=$3",
        tenant_id, source, source_customer_id,
    )
    if row:
        return str(row["end_customer_id"])

    new_id = await conn.fetchval(
        """
        INSERT INTO dim_end_customers
            (tenant_id, source, source_customer_id, company_name, email, currency_code)
        VALUES ($1,$2,$3,$4,$5,'INR')
        ON CONFLICT (tenant_id, source, source_customer_id) DO UPDATE
            SET company_name = EXCLUDED.company_name,
                email        = COALESCE(EXCLUDED.email, dim_end_customers.email),
                updated_at   = now()
        RETURNING end_customer_id
        """,
        tenant_id, source, source_customer_id, company_name, email,
    )
    return str(new_id)


# ── Razorpay handlers ──────────────────────────────────────────────────────────

async def _handle_razorpay(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Route Razorpay webhook events."""
    pmt_entity = (payload.get("payment") or {}).get("entity") or {}

    if event_type in ("payment.captured", "payment.authorized"):
        await _razorpay_payment_captured(conn, tenant_id, event_id, pmt_entity)
    elif event_type == "payment.failed":
        await _razorpay_payment_failed(conn, tenant_id, event_id, pmt_entity)
    elif event_type == "refund.created":
        ref_entity = (payload.get("refund") or {}).get("entity") or {}
        await _razorpay_refund_created(conn, tenant_id, event_id, ref_entity)
    else:
        logger.debug("[webhook] razorpay unhandled event_type=%s", event_type)


async def _razorpay_payment_captured(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    pmt: dict,
) -> None:
    source      = DataSource.RAZORPAY
    pmt_id      = str(pmt.get("id", ""))
    amount      = _paise_to_rupees(pmt.get("amount"))
    currency    = (pmt.get("currency") or "INR")[:3]
    method      = _RAZORPAY_METHOD_MAP.get(pmt.get("method") or "", PaymentMethod.GATEWAY)
    txn_ref     = pmt.get("acquirer_data", {}).get("rrn") or pmt.get("id")
    pmt_date    = _ts_to_date(pmt.get("created_at"))
    contact     = pmt.get("contact") or ""
    email       = pmt.get("email") or ""
    description = pmt.get("description") or ""
    order_id    = pmt.get("order_id") or ""

    # Use email or contact as customer identifier
    cust_source_id = email or contact or pmt_id
    cust_name      = email or description or "Razorpay Customer"
    end_customer_id = await _resolve_or_create_customer(
        conn, tenant_id, source, cust_source_id, cust_name, email or None
    )

    # Try to link to an existing stg_invoice via notes.invoice_id
    notes = pmt.get("notes") or {}
    stg_invoice_id = None
    inv_ref = notes.get("invoice_id") or notes.get("stg_invoice_id")
    if inv_ref:
        stg_invoice_id = await conn.fetchval(
            "SELECT stg_invoice_id FROM stg_invoices WHERE stg_invoice_id=$1 AND tenant_id=$2",
            inv_ref, tenant_id,
        )

    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            stg_invoice_id, end_customer_id,
            payment_date, amount, currency_code,
            payment_method, transaction_ref, gateway,
            status, is_reconciled, sync_status
        ) VALUES (
            $1,$2,$3,now(),
            $4,$5,
            $6,$7,$8,
            $9,$10,$11,
            'CLEARED',false,'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            status      = 'CLEARED',
            sync_status = 'PENDING',
            updated_at  = now()
        RETURNING stg_payment_id
        """,
        tenant_id, source, pmt_id,
        stg_invoice_id, end_customer_id,
        pmt_date, amount, currency,
        method, txn_ref, "razorpay",
    )
    if stg_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=stg_payment_id, action="INSERT",
                     new_values={"source_payment_id": pmt_id, "status": "CLEARED",
                                 "amount": amount, "gateway": "razorpay"})
    logger.info("[webhook] razorpay captured pmt=%s tenant=%s amount=%.2f", pmt_id, tenant_id, amount)


async def _razorpay_payment_failed(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    pmt: dict,
) -> None:
    source = DataSource.RAZORPAY
    pmt_id = str(pmt.get("id", ""))
    amount = _paise_to_rupees(pmt.get("amount"))
    currency = (pmt.get("currency") or "INR")[:3]
    pmt_date = _ts_to_date(pmt.get("created_at"))
    contact  = pmt.get("contact") or pmt_id

    end_customer_id = await _resolve_or_create_customer(
        conn, tenant_id, source, contact, contact,
    )

    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            end_customer_id, payment_date, amount, currency_code,
            status, is_reconciled, sync_status
        ) VALUES (
            $1,$2,$3,now(),
            $4,$5,$6,$7,
            'BOUNCED',false,'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            status      = 'BOUNCED',
            sync_status = 'PENDING'
        RETURNING stg_payment_id
        """,
        tenant_id, source, pmt_id,
        end_customer_id, pmt_date, amount, currency,
    )
    if stg_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=stg_payment_id, action="INSERT",
                     new_values={"source_payment_id": pmt_id, "status": "BOUNCED",
                                 "amount": amount, "gateway": "razorpay"})


async def _razorpay_refund_created(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    ref: dict,
) -> None:
    source    = DataSource.RAZORPAY
    ref_id    = str(ref.get("id", ""))
    amount    = _paise_to_rupees(ref.get("amount"))
    currency  = (ref.get("currency") or "INR")[:3]
    ref_date  = _ts_to_date(ref.get("created_at"))
    # Mark original payment as REFUNDED
    pmt_id    = str(ref.get("payment_id") or "")
    if pmt_id:
        await conn.execute(
            "UPDATE stg_payments SET status='REFUNDED', sync_status='PENDING' "
            "WHERE tenant_id=$1 AND source=$2 AND source_payment_id=$3",
            tenant_id, source, pmt_id,
        )

    row = await conn.fetchrow(
        "SELECT end_customer_id FROM stg_payments "
        "WHERE tenant_id=$1 AND source=$2 AND source_payment_id=$3",
        tenant_id, source, pmt_id,
    )
    end_customer_id = str(row["end_customer_id"]) if row else None
    if not end_customer_id:
        end_customer_id = await _resolve_or_create_customer(
            conn, tenant_id, source, ref_id, "Razorpay Refund",
        )

    # Insert refund as a separate negative payment
    refund_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            end_customer_id, payment_date, amount, currency_code,
            gateway, status, is_reconciled, sync_status
        ) VALUES ($1,$2,$3,now(),$4,$5,$6,$7,'razorpay','REFUNDED',false,'PENDING')
        ON CONFLICT (tenant_id, source, source_payment_id) DO NOTHING
        RETURNING stg_payment_id
        """,
        tenant_id, source, f"refund_{ref_id}",
        end_customer_id, ref_date, -amount, currency,
    )
    if refund_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=refund_payment_id, action="INSERT",
                     new_values={"source_payment_id": f"refund_{ref_id}", "status": "REFUNDED",
                                 "amount": -amount, "gateway": "razorpay"})


# ── Stripe handlers ────────────────────────────────────────────────────────────

async def _handle_stripe(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Route Stripe webhook events."""
    obj = payload.get("object") or {}

    if event_type in ("payment_intent.succeeded", "payment_intent.amount_capturable_updated"):
        await _stripe_payment_intent_succeeded(conn, tenant_id, event_id, obj)
    elif event_type in ("payment_intent.payment_failed", "payment_intent.canceled"):
        await _stripe_payment_intent_failed(conn, tenant_id, event_id, obj)
    elif event_type == "invoice.paid":
        await _stripe_invoice_paid(conn, tenant_id, event_id, obj)
    elif event_type == "charge.refunded":
        await _stripe_charge_refunded(conn, tenant_id, event_id, obj)
    else:
        logger.debug("[webhook] stripe unhandled event_type=%s", event_type)


async def _stripe_payment_intent_succeeded(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    pi: dict,
) -> None:
    source   = DataSource.STRIPE
    pi_id    = str(pi.get("id", ""))
    amount   = _stripe_cents_to_units(pi.get("amount_received") or pi.get("amount"))
    currency = (pi.get("currency") or "INR").upper()[:3]
    pmt_date = _ts_to_date(pi.get("created"))
    txn_ref  = pi.get("latest_charge") or pi_id

    meta          = pi.get("metadata") or {}
    cust_id       = str(pi.get("customer") or meta.get("customer_id") or pi_id)
    cust_email    = pi.get("receipt_email") or ""
    cust_name     = meta.get("customer_name") or cust_email or "Stripe Customer"

    end_customer_id = await _resolve_or_create_customer(
        conn, tenant_id, source, cust_id, cust_name, cust_email or None,
    )

    stg_invoice_id = None
    inv_ref = meta.get("invoice_id") or meta.get("stg_invoice_id")
    if inv_ref:
        stg_invoice_id = await conn.fetchval(
            "SELECT stg_invoice_id FROM stg_invoices WHERE stg_invoice_id=$1 AND tenant_id=$2",
            inv_ref, tenant_id,
        )

    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            stg_invoice_id, end_customer_id,
            payment_date, amount, currency_code,
            payment_method, transaction_ref, gateway,
            status, is_reconciled, sync_status
        ) VALUES (
            $1,$2,$3,now(),
            $4,$5,
            $6,$7,$8,
            'GATEWAY',$9,'stripe',
            'CLEARED',false,'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            status      = 'CLEARED',
            sync_status = 'PENDING',
            updated_at  = now()
        RETURNING stg_payment_id
        """,
        tenant_id, source, pi_id,
        stg_invoice_id, end_customer_id,
        pmt_date, amount, currency, txn_ref,
    )
    if stg_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=stg_payment_id, action="INSERT",
                     new_values={"source_payment_id": pi_id, "status": "CLEARED",
                                 "amount": amount, "gateway": "stripe"})
    logger.info("[webhook] stripe pi.succeeded id=%s tenant=%s amount=%.2f", pi_id, tenant_id, amount)


async def _stripe_payment_intent_failed(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    pi: dict,
) -> None:
    source   = DataSource.STRIPE
    pi_id    = str(pi.get("id", ""))
    amount   = _stripe_cents_to_units(pi.get("amount"))
    currency = (pi.get("currency") or "INR").upper()[:3]
    pmt_date = _ts_to_date(pi.get("created"))
    cust_id  = str(pi.get("customer") or pi_id)

    end_customer_id = await _resolve_or_create_customer(
        conn, tenant_id, source, cust_id, "Stripe Customer",
    )
    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            end_customer_id, payment_date, amount, currency_code,
            gateway, status, is_reconciled, sync_status
        ) VALUES ($1,$2,$3,now(),$4,$5,$6,$7,'stripe','BOUNCED',false,'PENDING')
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            status = 'BOUNCED', sync_status = 'PENDING'
        RETURNING stg_payment_id
        """,
        tenant_id, source, pi_id,
        end_customer_id, pmt_date, amount, currency,
    )
    if stg_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=stg_payment_id, action="INSERT",
                     new_values={"source_payment_id": pi_id, "status": "BOUNCED",
                                 "amount": amount, "gateway": "stripe"})


async def _stripe_invoice_paid(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    inv: dict,
) -> None:
    source     = DataSource.STRIPE
    inv_id     = str(inv.get("id", ""))
    amount     = _stripe_cents_to_units(inv.get("amount_paid"))
    currency   = (inv.get("currency") or "INR").upper()[:3]
    pmt_date   = _ts_to_date(inv.get("status_transitions", {}).get("paid_at") or inv.get("created"))
    cust_id    = str(inv.get("customer") or "")
    cust_email = inv.get("customer_email") or ""
    cust_name  = inv.get("customer_name") or cust_email or "Stripe Customer"
    pi_id      = str(inv.get("payment_intent") or inv_id)

    end_customer_id = await _resolve_or_create_customer(
        conn, tenant_id, source, cust_id or cust_email, cust_name, cust_email or None,
    )

    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            end_customer_id, payment_date, amount, currency_code,
            transaction_ref, gateway, status, is_reconciled, sync_status
        ) VALUES ($1,$2,$3,now(),$4,$5,$6,$7,$8,'stripe','CLEARED',false,'PENDING')
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            status = 'CLEARED', sync_status = 'PENDING', updated_at = now()
        RETURNING stg_payment_id
        """,
        tenant_id, source, f"stripe_inv_{inv_id}",
        end_customer_id, pmt_date, amount, currency, pi_id,
    )
    if stg_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=stg_payment_id, action="INSERT",
                     new_values={"source_payment_id": f"stripe_inv_{inv_id}", "status": "CLEARED",
                                 "amount": amount, "gateway": "stripe"})


async def _stripe_charge_refunded(
    conn: asyncpg.Connection,
    tenant_id: str,
    event_id: str,
    charge: dict,
) -> None:
    source    = DataSource.STRIPE
    charge_id = str(charge.get("id", ""))
    amount    = _stripe_cents_to_units(charge.get("amount_refunded"))
    currency  = (charge.get("currency") or "INR").upper()[:3]
    ref_date  = date.today()

    await conn.execute(
        "UPDATE stg_payments SET status='REFUNDED', sync_status='PENDING' "
        "WHERE tenant_id=$1 AND source=$2 AND source_payment_id=$3",
        tenant_id, source, charge_id,
    )

    row = await conn.fetchrow(
        "SELECT end_customer_id FROM stg_payments "
        "WHERE tenant_id=$1 AND source=$2 AND source_payment_id=$3",
        tenant_id, source, charge_id,
    )
    end_customer_id = str(row["end_customer_id"]) if row else None
    if not end_customer_id:
        end_customer_id = await _resolve_or_create_customer(
            conn, tenant_id, source, charge_id, "Stripe Refund",
        )

    refund_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            end_customer_id, payment_date, amount, currency_code,
            gateway, status, is_reconciled, sync_status
        ) VALUES ($1,$2,$3,now(),$4,$5,$6,$7,'stripe','REFUNDED',false,'PENDING')
        ON CONFLICT (tenant_id, source, source_payment_id) DO NOTHING
        RETURNING stg_payment_id
        """,
        tenant_id, source, f"refund_{charge_id}",
        end_customer_id, ref_date, -amount, currency,
    )
    if refund_payment_id:
        await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                     entity_id=refund_payment_id, action="INSERT",
                     new_values={"source_payment_id": f"refund_{charge_id}", "status": "REFUNDED",
                                 "amount": -amount, "gateway": "stripe"})


# ── Worker ─────────────────────────────────────────────────────────────────────

_SOURCE_HANDLERS = {
    DataSource.RAZORPAY: _handle_razorpay,
    DataSource.STRIPE:   _handle_stripe,
}


class WebhookWorker(BaseWorker):
    name           = "webhook"
    notify_channel = "ch_webhook"

    async def process_batch(self) -> int:
        pool = await get_pool()
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT event_id, tenant_id, source, event_type,
                           raw_payload, received_at
                    FROM raw_webhook_events
                    WHERE is_processed = false
                      AND is_signature_verified = true
                      AND tenant_id = ANY($2::uuid[])
                    ORDER BY received_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT event_id, tenant_id, source, event_type,
                           raw_payload, received_at
                    FROM raw_webhook_events
                    WHERE is_processed = false
                      AND is_signature_verified = true
                    ORDER BY received_at
                    LIMIT $1
                    FOR UPDATE SKIP LOCKED
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        processed = 0
        for row in rows:
            event_id = str(row["event_id"])
            source   = row["source"]
            evt_type = row["event_type"]
            raw      = row["raw_payload"]
            if isinstance(raw, str):
                raw = json.loads(raw)

            # C5: strict tenant null guard — no payload fallback
            if not row["tenant_id"]:
                logger.error(
                    "[webhook] event=%s has no tenant_id — quarantining", event_id
                )
                await self._mark_processed(pool, event_id, error="no tenant_id")
                continue
            tenant_id = str(row["tenant_id"])

            try:
                src_enum = DataSource(source)
            except ValueError:
                logger.warning("[webhook] unknown source=%s event=%s", source, event_id)
                await self._mark_processed(pool, event_id)
                continue

            handler = _SOURCE_HANDLERS.get(src_enum)
            if not handler:
                logger.debug("[webhook] no handler for source=%s", source)
                await self._mark_processed(pool, event_id)
                continue

            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await handler(conn, tenant_id, event_id, evt_type, raw)
                        await conn.execute(
                            "UPDATE raw_webhook_events SET is_processed=true "
                            "WHERE event_id=$1",
                            event_id,
                        )
                processed += 1

            except Exception as exc:
                logger.exception("[webhook] error event=%s", event_id)
                # C7: increment retry_count; quarantine after 3 failures
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        new_retry = await conn.fetchval(
                            "UPDATE raw_webhook_events SET retry_count = retry_count + 1 "
                            "WHERE event_id=$1 RETURNING retry_count",
                            event_id,
                        )
                        if new_retry >= 3:
                            err_msg = str(exc)
                            await conn.execute(
                                "UPDATE raw_webhook_events "
                                "SET is_processed=true, processing_error=$1 "
                                "WHERE event_id=$2",
                                err_msg, event_id,
                            )
                            await self.log_error(
                                conn,
                                tenant_id=tenant_id,
                                stage="webhook",
                                error_type=type(exc).__name__,
                                details={"message": err_msg, "event_type": evt_type},
                                webhook_event_id=event_id,
                            )
                            logger.warning(
                                "[webhook] event=%s quarantined after %d failures", event_id, new_retry
                            )

        logger.info("[webhook] batch done: %d processed", processed)
        return processed

    async def _mark_processed(
        self,
        pool: asyncpg.Pool,
        event_id: str,
        error: str | None = None,
    ) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE raw_webhook_events "
                "SET is_processed=true, processing_error=$1 "
                "WHERE event_id=$2",
                error, event_id,
            )
