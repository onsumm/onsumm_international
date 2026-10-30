"""
Tally Read Pipeline
───────────────────
Read-only sync from Tally Prime into the staging DB (PULL only).

PULL (Tally → DB):
  1. Fetch ledger accounts  → dim_ledger_accounts + dim_end_customers
  2. Fetch vouchers (incremental window) → stg_invoices / stg_bills / stg_payments

A cycle runs for every active int_connections row whose last_sync_at is older
than TALLY_SYNC_INTERVAL_MINUTES minutes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
from concurrent.futures import ThreadPoolExecutor

import config
from db import get_pool
from pipelines.base import BaseWorker

# ── Import pure-Python helpers from tally2database ────────────────────────────
_TALLY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tally2database")
if _TALLY_DIR not in sys.path:
    sys.path.insert(0, _TALLY_DIR)

from tally_client import fetch_ledgers, fetch_vouchers  # noqa: E402
from tally_parser import parse_ledgers, parse_vouchers  # noqa: E402
from sync_audit_logger import write_sync_event          # noqa: E402

logger = logging.getLogger(__name__)

_audit = BaseWorker.log_audit

DATA_SOURCE = "TALLY"

_tally_executor = ThreadPoolExecutor(max_workers=config.TALLY_THREAD_POOL)

_VALID_ACCOUNT_TYPES   = {"ASSET", "LIABILITY", "INCOME", "EXPENSE", "EQUITY"}
_VALID_INVOICE_STATUS  = {"DRAFT", "SENT", "PARTIAL", "PAID", "OVERDUE", "VOID"}
_VALID_PAYMENT_STATUS  = {"PENDING", "CLEARED", "BOUNCED", "REFUNDED"}


# ── Thread helpers ─────────────────────────────────────────────────────────────

async def _in_thread(fn, *args):
    """Run a blocking Tally HTTP call in the dedicated thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_tally_executor, fn, *args)


# ── Normalisation helpers ──────────────────────────────────────────────────────

def _safe_date(value, default=None):
    if isinstance(value, date):
        return value
    return default or date.today()


def _safe_amount(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_account_type(value: str) -> str:
    mapping = {"BANK": "ASSET", "CASH": "ASSET", "REVENUE": "INCOME"}
    candidate = mapping.get((value or "").upper(), (value or "").upper())
    return candidate if candidate in _VALID_ACCOUNT_TYPES else "ASSET"


def _normalize_invoice_status(value: str) -> str:
    candidate = (value or "").upper()
    return candidate if candidate in _VALID_INVOICE_STATUS else "DRAFT"


def _normalize_payment_status(value: str) -> str:
    mapping = {"COMPLETED": "CLEARED", "PROCESSING": "PENDING", "INITIATED": "PENDING"}
    candidate = mapping.get((value or "").upper(), (value or "").upper())
    return candidate if candidate in _VALID_PAYMENT_STATUS else "PENDING"


# ── Connection management ─────────────────────────────────────────────────────

async def _ensure_tally_connection(
    conn: asyncpg.Connection, tenant_id: str
) -> tuple[str, datetime | None]:
    """Return (connection_id, last_sync_at) for an active Tally connection, creating one if absent."""
    row = await conn.fetchrow(
        """
        SELECT connection_id, last_sync_at
        FROM int_connections
        WHERE tenant_id = $1 AND source = 'TALLY' AND status = 'ACTIVE'
        LIMIT 1
        """,
        tenant_id,
    )
    if row:
        return str(row["connection_id"]), row["last_sync_at"]

    conn_id = str(uuid.uuid4())
    result = await conn.fetchrow(
        """
        INSERT INTO int_connections
            (connection_id, tenant_id, source, tally_mode, status, last_sync_at, sync_config_jsonb)
        VALUES ($1, $2, 'TALLY', 'XML_SOCKET', 'ACTIVE'::connection_status, now(), '{}')
        ON CONFLICT (tenant_id, source) DO UPDATE
            SET status = 'ACTIVE'::connection_status
        RETURNING connection_id, last_sync_at
        """,
        conn_id, tenant_id,
    )
    logger.info("[tally_read] Ensured Tally connection %s for tenant %s", result["connection_id"], tenant_id)
    return str(result["connection_id"]), result["last_sync_at"]


async def _update_last_synced(conn: asyncpg.Connection, connection_id: str, tenant_id: str = "") -> None:
    await conn.execute(
        """
        UPDATE int_connections
        SET last_sync_at         = now(),
            consecutive_failures = 0,
            error_message        = NULL,
            status               = 'ACTIVE'::connection_status
        WHERE connection_id = $1
        """,
        connection_id,
    )
    if tenant_id:
        await _audit(
            conn, tenant_id=tenant_id,
            table_name="int_connections", entity_id=connection_id,
            action="UPDATE",
            new_values={"status": "ACTIVE", "last_sync_at": "now", "consecutive_failures": 0},
        )


async def _mark_connection_error(
    conn: asyncpg.Connection, connection_id: str, error_message: str
) -> None:
    await conn.execute(
        """
        UPDATE int_connections
        SET error_message        = $1,
            consecutive_failures = consecutive_failures + 1
        WHERE connection_id = $2
        """,
        error_message, connection_id,
    )


# ── Raw payload store ──────────────────────────────────────────────────────────

async def _store_raw_payload(
    conn: asyncpg.Connection, tenant_id: str, endpoint: str, raw_xml: str
) -> str:
    entity_type = "JOURNAL" if endpoint == "ledgers" else "INVOICE"
    payload_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO raw_api_payloads
            (payload_id, tenant_id, source, entity_type, raw_payload, received_at)
        VALUES ($1, $2, $3, $4::entity_type, $5::jsonb, now())
        """,
        payload_id, tenant_id, DATA_SOURCE, entity_type,
        json.dumps({"xml": raw_xml}),
    )
    return payload_id


# ── Dimension upserts ─────────────────────────────────────────────────────────

async def _get_or_create_ledger_account(
    conn: asyncpg.Connection,
    tenant_id: str,
    source_account_id: str,
    account_name: str,
    account_type: str,
) -> str:
    existing = await conn.fetchval(
        """
        SELECT account_id FROM dim_ledger_accounts
        WHERE tenant_id = $1 AND source = $2 AND source_account_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, source_account_id,
    )
    norm_type = _normalize_account_type(account_type)
    if existing:
        await conn.execute(
            """
            UPDATE dim_ledger_accounts
            SET account_name = $1, account_type = $2::account_type, is_active = true
            WHERE account_id = $3
            """,
            account_name, norm_type, existing,
        )
        return str(existing)

    account_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO dim_ledger_accounts
            (account_id, tenant_id, source_account_id, source, account_name, account_type, is_active)
        VALUES ($1, $2, $3, $4, $5, $6::account_type, true)
        """,
        account_id, tenant_id, source_account_id, DATA_SOURCE, account_name, norm_type,
    )
    return account_id


async def _get_or_create_end_customer(
    conn: asyncpg.Connection,
    tenant_id: str,
    party_name: str,
    gstin: str | None,
    billing_address: dict | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> str:
    existing = await conn.fetchval(
        """
        SELECT end_customer_id FROM dim_end_customers
        WHERE tenant_id = $1 AND source = $2 AND source_customer_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, party_name,
    )
    gstin_clean = gstin[:15] if gstin else None
    addr_json = json.dumps(billing_address or {})

    if existing:
        await conn.execute(
            """
            UPDATE dim_end_customers
            SET company_name            = $1,
                trade_name              = $1,
                gstin                   = COALESCE($2, gstin),
                email                   = COALESCE($3, email),
                phone                   = COALESCE($4, phone),
                billing_address_jsonb   = CASE
                    WHEN $5::jsonb = '{}'::jsonb THEN billing_address_jsonb
                    ELSE $5::jsonb
                END,
                is_active               = true,
                updated_at              = now()
            WHERE end_customer_id = $6
            """,
            party_name, gstin_clean, email, phone, addr_json, existing,
        )
        return str(existing)

    ec_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO dim_end_customers
            (end_customer_id, tenant_id, source, source_customer_id, company_name, trade_name,
             gstin, email, phone, billing_address_jsonb, shipping_address_jsonb,
             payment_terms_days, credit_limit, currency_code, risk_score, risk_bucket,
             kyc_status, is_active)
        VALUES ($1,$2,$3,$4,$5,$5,
                $6,$7,$8,$9::jsonb,'{}'::jsonb,
                30,0,'INR',50.00,'MEDIUM'::risk_bucket,
                'PENDING',true)
        """,
        ec_id, tenant_id, DATA_SOURCE, party_name, party_name,
        gstin_clean, email, phone, addr_json,
    )
    return ec_id


async def _get_or_create_supplier(
    conn: asyncpg.Connection,
    tenant_id: str,
    supplier_name: str,
    gstn: str | None = None,
) -> str:
    existing = await conn.fetchval(
        """
        SELECT supplier_id FROM dim_suppliers
        WHERE tenant_id = $1 AND source = $2 AND source_supplier_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, supplier_name,
    )
    gstn_clean = gstn[:15] if gstn else None

    if existing:
        await conn.execute(
            """
            UPDATE dim_suppliers
            SET name = $1, gstn = COALESCE($2, gstn), is_active = true
            WHERE supplier_id = $3
            """,
            supplier_name, gstn_clean, existing,
        )
        return str(existing)

    supplier_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO dim_suppliers
            (supplier_id, tenant_id, source_supplier_id, source, name, gstn,
             payment_terms_days, status, is_active)
        VALUES ($1,$2,$3,$4,$5,$6,30,'ACTIVE'::supplier_status,true)
        """,
        supplier_id, tenant_id, supplier_name, DATA_SOURCE, supplier_name, gstn_clean,
    )
    return supplier_id


# ── Staging upserts ────────────────────────────────────────────────────────────

async def _upsert_invoice(
    conn: asyncpg.Connection,
    tenant_id: str,
    payload_id: str,
    inv: dict,
    end_customer_id: str,
) -> str:
    source_invoice_id = inv["source_invoice_id"]
    existing = await conn.fetchval(
        """
        SELECT stg_invoice_id FROM stg_invoices
        WHERE tenant_id = $1 AND source = $2 AND source_invoice_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, source_invoice_id,
    )

    inv_number     = inv.get("invoice_number") or source_invoice_id
    inv_date       = _safe_date(inv.get("invoice_date"))
    due_date       = inv.get("due_date")
    currency       = inv.get("currency_code") or "INR"
    subtotal       = _safe_amount(inv.get("subtotal"))
    tax_amount     = _safe_amount(inv.get("tax_amount"))
    total_amount   = _safe_amount(inv.get("total_amount"))
    status         = _normalize_invoice_status(inv.get("status"))
    gstin_buyer    = inv.get("gstin_buyer")
    narration      = inv.get("narration")
    src_updated_at = _safe_date(inv.get("source_updated_at"))

    if existing:
        await conn.execute(
            """
            UPDATE stg_invoices
            SET source_updated_at  = $1,
                raw_payload_id     = $2,
                invoice_number     = $3,
                invoice_date       = $4,
                due_date           = $5,
                end_customer_id    = $6,
                currency_code      = $7,
                subtotal           = $8,
                tax_amount         = $9,
                total_amount       = $10,
                amount_paid        = 0,
                outstanding_amount = $10,
                status             = $11::invoice_status,
                gstin_buyer        = $12,
                validation_flag    = 'OK'::validation_flag,
                narration          = $13,
                updated_at         = now()
            WHERE stg_invoice_id = $14
            """,
            src_updated_at, payload_id, inv_number, inv_date, due_date,
            end_customer_id, currency, subtotal, tax_amount, total_amount,
            status, gstin_buyer, narration, existing,
        )
        stg_invoice_id = str(existing)
        action = "updated"
    else:
        stg_invoice_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO stg_invoices
                (stg_invoice_id, tenant_id, source, source_invoice_id, source_updated_at,
                 raw_payload_id, invoice_number, invoice_date, due_date, end_customer_id,
                 currency_code, subtotal, discount_amount, tax_amount, total_amount,
                 amount_paid, outstanding_amount, status, gstin_buyer,
                 validation_flag, sync_status, narration, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,
                    $6,$7,$8,$9,$10,
                    $11,$12,0,$13,$14,
                    0,$14,$15::invoice_status,$16,
                    'OK'::validation_flag,'PENDING'::sync_status,$17,now(),now())
            """,
            stg_invoice_id, tenant_id, DATA_SOURCE, source_invoice_id, src_updated_at,
            payload_id, inv_number, inv_date, due_date, end_customer_id,
            currency, subtotal, tax_amount, total_amount, status, gstin_buyer, narration,
        )
        action = "inserted"

    await conn.execute(
        "DELETE FROM stg_invoice_line_items WHERE stg_invoice_id = $1", stg_invoice_id
    )
    for idx, line in enumerate(inv.get("_lines", []), start=1):
        await conn.execute(
            """
            INSERT INTO stg_invoice_line_items
                (line_item_id, stg_invoice_id, tenant_id, line_number, product_name,
                 hsn_code, quantity, unit, unit_price, discount_percent,
                 discount_amount, tax_rate_percent, tax_amount, line_total)
            VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,0,$9,$10,$11,$12)
            """,
            stg_invoice_id, tenant_id, idx,
            (line.get("product_name") or "Item").strip(),
            line.get("hsn_code"),
            _safe_amount(line.get("quantity")) or 1,
            line.get("unit"),
            _safe_amount(line.get("unit_price")),
            _safe_amount(line.get("discount_amount")),
            _safe_amount(line.get("tax_rate_percent")),
            _safe_amount(line.get("tax_amount")),
            _safe_amount(line.get("line_total")),
        )
    return action


async def _upsert_bill(
    conn: asyncpg.Connection,
    tenant_id: str,
    payload_id: str,
    bill: dict,
    supplier_id: str,
) -> str:
    source_bill_id = bill["source_bill_id"]
    existing = await conn.fetchval(
        """
        SELECT stg_bill_id FROM stg_bills
        WHERE tenant_id = $1 AND source = $2 AND source_bill_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, source_bill_id,
    )

    bill_number    = bill.get("bill_number") or source_bill_id
    bill_date      = _safe_date(bill.get("bill_date"))
    due_date       = bill.get("due_date")
    currency       = bill.get("currency_code") or "INR"
    subtotal       = _safe_amount(bill.get("subtotal"))
    tax_amount     = _safe_amount(bill.get("tax_amount"))
    total_amount   = _safe_amount(bill.get("total_amount"))
    status         = _normalize_invoice_status(bill.get("status"))
    gstin_seller   = bill.get("gstin_seller")
    src_updated_at = _safe_date(bill.get("source_updated_at"))

    if existing:
        await conn.execute(
            """
            UPDATE stg_bills
            SET source_updated_at  = $1,
                raw_payload_id     = $2,
                bill_number        = $3,
                bill_date          = $4,
                due_date           = $5,
                supplier_id        = $6,
                currency_code      = $7,
                subtotal           = $8,
                tax_amount         = $9,
                total_amount       = $10,
                amount_paid        = 0,
                outstanding_amount = $10,
                status             = $11::invoice_status,
                gstin_seller       = $12,
                validation_flag    = 'OK'::validation_flag,
                updated_at         = now()
            WHERE stg_bill_id = $13
            """,
            src_updated_at, payload_id, bill_number, bill_date, due_date,
            supplier_id, currency, subtotal, tax_amount, total_amount,
            status, gstin_seller, existing,
        )
        stg_bill_id = str(existing)
        action = "updated"
    else:
        stg_bill_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO stg_bills
                (stg_bill_id, tenant_id, source, source_bill_id, source_updated_at,
                 raw_payload_id, bill_number, bill_date, due_date, supplier_id,
                 currency_code, subtotal, discount_amount, tax_amount, total_amount,
                 amount_paid, outstanding_amount, status, gstin_seller,
                 validation_flag, sync_status, created_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,
                    $6,$7,$8,$9,$10,
                    $11,$12,0,$13,$14,
                    0,$14,$15::invoice_status,$16,
                    'OK'::validation_flag,'PENDING'::sync_status,now(),now())
            """,
            stg_bill_id, tenant_id, DATA_SOURCE, source_bill_id, src_updated_at,
            payload_id, bill_number, bill_date, due_date, supplier_id,
            currency, subtotal, tax_amount, total_amount, status, gstin_seller,
        )
        action = "inserted"

    await conn.execute(
        "DELETE FROM stg_bill_line_items WHERE stg_bill_id = $1", stg_bill_id
    )
    for idx, line in enumerate(bill.get("_lines", []), start=1):
        await conn.execute(
            """
            INSERT INTO stg_bill_line_items
                (line_item_id, stg_bill_id, tenant_id, line_number, product_name,
                 hsn_code, quantity, unit, unit_price, discount_amount,
                 tax_rate_percent, tax_amount, line_total)
            VALUES (gen_random_uuid(),$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
            stg_bill_id, tenant_id, idx,
            (line.get("product_name") or "Item").strip(),
            line.get("hsn_code"),
            _safe_amount(line.get("quantity")) or 1,
            line.get("unit"),
            _safe_amount(line.get("unit_price")),
            _safe_amount(line.get("discount_amount")),
            _safe_amount(line.get("tax_rate_percent")),
            _safe_amount(line.get("tax_amount")),
            _safe_amount(line.get("line_total")),
        )
    return action


async def _upsert_payment(
    conn: asyncpg.Connection,
    tenant_id: str,
    pmt: dict,
    end_customer_id: str,
) -> str:
    source_payment_id = pmt["source_payment_id"]
    existing = await conn.fetchval(
        """
        SELECT stg_payment_id FROM stg_payments
        WHERE tenant_id = $1 AND source = $2 AND source_payment_id = $3
        LIMIT 1
        """,
        tenant_id, DATA_SOURCE, source_payment_id,
    )

    pmt_date       = _safe_date(pmt.get("payment_date"))
    amount         = _safe_amount(pmt.get("amount"))
    currency       = pmt.get("currency_code") or "INR"
    txn_ref        = pmt.get("transaction_ref")
    status         = _normalize_payment_status(pmt.get("status"))
    src_updated_at = _safe_date(pmt.get("source_updated_at"))

    if existing:
        await conn.execute(
            """
            UPDATE stg_payments
            SET source_updated_at = $1,
                end_customer_id   = $2,
                payment_date      = $3,
                amount            = $4,
                currency_code     = $5,
                transaction_ref   = $6,
                status            = $7::payment_status
            WHERE stg_payment_id = $8
            """,
            src_updated_at, end_customer_id, pmt_date, amount,
            currency, txn_ref, status, existing,
        )
        return "updated"

    await conn.execute(
        """
        INSERT INTO stg_payments
            (stg_payment_id, tenant_id, source, source_payment_id, source_updated_at,
             end_customer_id, payment_date, amount, currency_code, transaction_ref,
             status, is_reconciled, sync_status, created_at)
        VALUES (gen_random_uuid(),$1,$2,$3,$4,
                $5,$6,$7,$8,$9,
                $10::payment_status,false,'PENDING'::sync_status,now())
        """,
        tenant_id, DATA_SOURCE, source_payment_id, src_updated_at,
        end_customer_id, pmt_date, amount, currency, txn_ref, status,
    )
    return "inserted"


# ── PULL ───────────────────────────────────────────────────────────────────────

async def _pull_ledgers(
    pool: asyncpg.Pool, tenant_id: str, connection_id: str
) -> int:
    logger.info("[tally_read] PULL ledgers for tenant=%s", tenant_id)
    raw_xml: str = await _in_thread(fetch_ledgers)
    ledgers: list = parse_ledgers(raw_xml)

    write_sync_event(
        direction="PULL", entity="LEDGER", action="FETCH",
        tenant_id=tenant_id, connection_id=connection_id,
        details={"records_in_payload": len(ledgers), "xml_length": len(raw_xml or "")},
    )

    count = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            payload_id = await _store_raw_payload(conn, tenant_id, "ledgers", raw_xml)
            write_sync_event(
                direction="PULL", entity="LEDGER", action="RAW_PAYLOAD_STORED",
                tenant_id=tenant_id, connection_id=connection_id,
                details={"payload_id": payload_id},
            )
            for ledger in ledgers:
                await _get_or_create_ledger_account(
                    conn, tenant_id,
                    ledger["source_account_id"],
                    ledger["account_name"],
                    ledger["account_type"],
                )
                if ledger.get("_is_party"):
                    await _get_or_create_end_customer(
                        conn, tenant_id,
                        ledger.get("company_name") or ledger["account_name"],
                        ledger.get("gstin"),
                        ledger.get("billing_address_jsonb"),
                        ledger.get("email"),
                        ledger.get("phone"),
                    )
                count += 1

    logger.info("[tally_read] PULL ledgers: %d records", count)
    write_sync_event(
        direction="PULL", entity="LEDGER", action="SUMMARY",
        tenant_id=tenant_id, connection_id=connection_id,
        details={"records_synced": count},
    )
    return count


async def _pull_vouchers(
    pool: asyncpg.Pool,
    tenant_id: str,
    connection_id: str,
    from_date: date,
    to_date: date,
) -> int:
    from_str = from_date.strftime("%Y%m%d")
    to_str   = to_date.strftime("%Y%m%d")

    logger.info("[tally_read] PULL vouchers %s→%s for tenant=%s", from_str, to_str, tenant_id)
    raw_xml: str = await _in_thread(fetch_vouchers, from_str, to_str)
    invoices, bills, payments = parse_vouchers(raw_xml)

    write_sync_event(
        direction="PULL", entity="VOUCHER_BATCH", action="FETCH",
        tenant_id=tenant_id, connection_id=connection_id,
        details={
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat(),
            "xml_length": len(raw_xml or ""),
            "parsed_invoices": len(invoices), "parsed_bills": len(bills),
            "parsed_payments": len(payments),
        },
    )

    stats: dict[str, int] = {
        "invoices": 0, "bills": 0, "payments": 0,
        "inserted": 0, "updated": 0, "errored": 0,
    }

    async with pool.acquire() as conn:
        async with conn.transaction():
            payload_id = await _store_raw_payload(conn, tenant_id, "vouchers", raw_xml)

            for inv in invoices:
                try:
                    ec_id  = await _get_or_create_end_customer(
                        conn, tenant_id, inv.get("_party") or "Unknown", inv.get("_gstin_party"),
                    )
                    action = await _upsert_invoice(conn, tenant_id, payload_id, inv, ec_id)
                    stats[action]    += 1
                    stats["invoices"] += 1
                    write_sync_event(
                        direction="PULL", entity="INVOICE", action="UPSERT",
                        tenant_id=tenant_id, connection_id=connection_id,
                        details={
                            "source_invoice_id": inv.get("source_invoice_id"),
                            "invoice_number": inv.get("invoice_number"),
                            "result": action,
                        },
                    )
                except Exception:
                    stats["errored"] += 1
                    logger.exception(
                        "[tally_read] Invoice upsert failed source_invoice_id=%s",
                        inv.get("source_invoice_id"),
                    )

            for bill in bills:
                try:
                    supplier_id = await _get_or_create_supplier(
                        conn, tenant_id, bill.get("_party") or "Unknown", bill.get("gstin_seller"),
                    )
                    action = await _upsert_bill(conn, tenant_id, payload_id, bill, supplier_id)
                    stats[action]  += 1
                    stats["bills"] += 1
                    write_sync_event(
                        direction="PULL", entity="BILL", action="UPSERT",
                        tenant_id=tenant_id, connection_id=connection_id,
                        details={
                            "source_bill_id": bill.get("source_bill_id"),
                            "bill_number": bill.get("bill_number"),
                            "result": action,
                        },
                    )
                except Exception:
                    stats["errored"] += 1
                    logger.exception(
                        "[tally_read] Bill upsert failed source_bill_id=%s",
                        bill.get("source_bill_id"),
                    )

            for pmt in payments:
                try:
                    ec_id  = await _get_or_create_end_customer(
                        conn, tenant_id, pmt.get("_party") or "Unknown", None,
                    )
                    action = await _upsert_payment(conn, tenant_id, pmt, ec_id)
                    stats[action]      += 1
                    stats["payments"]  += 1
                    write_sync_event(
                        direction="PULL", entity="PAYMENT", action="UPSERT",
                        tenant_id=tenant_id, connection_id=connection_id,
                        details={
                            "source_payment_id": pmt.get("source_payment_id"),
                            "result": action, "amount": pmt.get("amount"),
                        },
                    )
                except Exception:
                    stats["errored"] += 1
                    logger.exception(
                        "[tally_read] Payment upsert failed source_payment_id=%s",
                        pmt.get("source_payment_id"),
                    )

            total_fetched = stats["invoices"] + stats["bills"] + stats["payments"]
            job_status = "PARTIAL" if stats["errored"] else "COMPLETED"
            await conn.execute(
                """
                INSERT INTO int_sync_log
                    (log_id, tenant_id, connection_id, source, entity_type,
                     started_at, completed_at,
                     records_fetched, records_inserted, records_updated,
                     records_errored, status, created_at)
                VALUES (gen_random_uuid(),$1,$2,$3,$4::entity_type,
                        $5,now(),
                        $6,$7,$8,
                        $9,$10::sync_job_status,now())
                """,
                tenant_id, connection_id, DATA_SOURCE, "JOURNAL",
                from_date,
                total_fetched, stats["inserted"], stats["updated"],
                stats["errored"], job_status,
            )

    logger.info("[tally_read] PULL vouchers: %s", stats)
    write_sync_event(
        direction="PULL", entity="VOUCHER_BATCH", action="SUMMARY",
        tenant_id=tenant_id, connection_id=connection_id,
        status="ERROR" if stats["errored"] else "SUCCESS",
        details=stats,
    )
    return total_fetched


# ── Worker ─────────────────────────────────────────────────────────────────────

class TallyReadWorker(BaseWorker):
    """
    Polls int_connections for active Tally connections that are due for a sync
    cycle and runs PULL (Tally→DB) only — no writes back to Tally.
    """
    name          = "tally_read"
    skip_backfill = True  # time-driven; Tally is polled on a fixed schedule

    _consec_failures: dict[str, int] = {}
    _CIRCUIT_BREAKER_THRESHOLD = 3

    async def process_batch(self) -> int:
        pool = await get_pool()
        total = 0

        async with pool.acquire() as conn:
            connections = await conn.fetch(
                """
                SELECT connection_id, tenant_id, last_sync_at
                FROM int_connections
                WHERE source = 'TALLY' AND status = 'ACTIVE'
                """
            )

        if not connections:
            if config.TENANT_ID:
                async with pool.acquire() as conn:
                    conn_id, _ = await _ensure_tally_connection(conn, config.TENANT_ID)
                    connections = [{"connection_id": conn_id,
                                    "tenant_id": config.TENANT_ID,
                                    "last_sync_at": None}]

        interval = timedelta(minutes=config.TALLY_SYNC_INTERVAL_MINUTES)
        now      = datetime.now(timezone.utc)

        for row in connections:
            last = row["last_sync_at"]
            if last is not None:
                if hasattr(last, "tzinfo") and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if now - last < interval:
                    continue

            tenant_id     = str(row["tenant_id"])
            connection_id = str(row["connection_id"])

            logger.info(
                "[tally_read] Starting pull cycle for tenant=%s connection=%s",
                tenant_id, connection_id,
            )

            try:
                total += await _pull_ledgers(pool, tenant_id, connection_id)

                to_date   = date.today()
                from_date = last.date() if last else to_date - timedelta(days=config.TALLY_PULL_DAYS)
                total    += await _pull_vouchers(pool, tenant_id, connection_id, from_date, to_date)

                async with pool.acquire() as conn:
                    await _update_last_synced(conn, connection_id, tenant_id)

                self._consec_failures[connection_id] = 0
                logger.info("[tally_read] Pull cycle complete for tenant=%s", tenant_id)

            except Exception as e:
                logger.exception("[tally_read] Pull failed for tenant=%s", tenant_id)
                failures = self._consec_failures.get(connection_id, 0) + 1
                self._consec_failures[connection_id] = failures
                async with pool.acquire() as conn:
                    if failures >= self._CIRCUIT_BREAKER_THRESHOLD:
                        logger.error(
                            "[tally_read] Circuit breaker tripped for connection=%s "
                            "after %d failures — marking ERROR",
                            connection_id, failures,
                        )
                        await conn.execute(
                            "UPDATE int_connections SET status='ERROR', error_message=$1 "
                            "WHERE connection_id=$2",
                            str(e), connection_id,
                        )
                        del self._consec_failures[connection_id]
                    else:
                        await _mark_connection_error(conn, connection_id, str(e))

        return total
