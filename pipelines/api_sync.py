"""
API Sync Pipeline
─────────────────
Polls raw_api_payloads (is_processed = false) and promotes each record into
the appropriate staging or dimension table based on entity_type.

Supported entity types: INVOICE, PAYMENT, BILL, CUSTOMER, SUPPLIER, JOURNAL
Sources: Apideck-unified (ZOHO, QBO, XERO, FRESHBOOKS, SAGE, MYOB, WAVE)
         Tally (TDL HTTP / XML-socket / ODBC)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg

import config
from db import get_pool
from models.enums import (
    DataSource, EntityType, InvoiceStatus, PaymentStatus,
    SyncStatus, ValidationFlag, PaymentMethod,
)
from pipelines.base import BaseWorker
_audit = BaseWorker.log_audit
from utils.fiscal import fiscal_period

logger = logging.getLogger(__name__)

# ── Status normalisation maps ──────────────────────────────────────────────────

_APIDECK_INVOICE_STATUS: dict[str, InvoiceStatus] = {
    "draft":   InvoiceStatus.DRAFT,
    "open":    InvoiceStatus.SENT,
    "overdue": InvoiceStatus.OVERDUE,
    "paid":    InvoiceStatus.PAID,
    "voided":  InvoiceStatus.VOID,
    "partial": InvoiceStatus.PARTIAL,
}

_APIDECK_PAYMENT_STATUS: dict[str, PaymentStatus] = {
    "pending":   PaymentStatus.PENDING,
    "cleared":   PaymentStatus.CLEARED,
    "bounced":   PaymentStatus.BOUNCED,
    "refunded":  PaymentStatus.REFUNDED,
    "voided":    PaymentStatus.BOUNCED,
    "failed":    PaymentStatus.BOUNCED,
}

_APIDECK_PAYMENT_METHOD: dict[str, PaymentMethod] = {
    "bank_transfer": PaymentMethod.BANK_TRANSFER,
    "credit_card":   PaymentMethod.CARD,
    "debit_card":    PaymentMethod.CARD,
    "upi":           PaymentMethod.UPI,
    "check":         PaymentMethod.CHEQUE,
    "cheque":        PaymentMethod.CHEQUE,
    "cash":          PaymentMethod.CASH,
    "other":         PaymentMethod.GATEWAY,
}


def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_amount(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


# ── Dimension resolvers ────────────────────────────────────────────────────────

async def _resolve_end_customer(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_customer_id: str,
    company_name: str,
    email: str | None = None,
    phone: str | None = None,
    payment_terms_days: int = 30,
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
            (tenant_id, source, source_customer_id, company_name, email,
             phone, payment_terms_days, currency_code)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'INR')
        ON CONFLICT (tenant_id, source, source_customer_id) DO UPDATE
            SET company_name = EXCLUDED.company_name,
                email        = COALESCE(EXCLUDED.email, dim_end_customers.email),
                updated_at   = now()
        RETURNING end_customer_id
        """,
        tenant_id, source, source_customer_id,
        company_name, email, phone, payment_terms_days,
    )
    return str(new_id)


async def _resolve_supplier(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_supplier_id: str,
    name: str,
    email: str | None = None,
    payment_terms_days: int = 30,
) -> str:
    row = await conn.fetchrow(
        "SELECT supplier_id FROM dim_suppliers "
        "WHERE tenant_id=$1 AND source=$2 AND source_supplier_id=$3",
        tenant_id, source, source_supplier_id,
    )
    if row:
        return str(row["supplier_id"])

    new_id = await conn.fetchval(
        """
        INSERT INTO dim_suppliers
            (tenant_id, source, source_supplier_id, name, email, payment_terms_days)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (tenant_id, source, source_supplier_id) DO UPDATE
            SET name  = EXCLUDED.name,
                email = COALESCE(EXCLUDED.email, dim_suppliers.email)
        RETURNING supplier_id
        """,
        tenant_id, source, source_supplier_id, name, email, payment_terms_days,
    )
    return str(new_id)


async def _resolve_bank_account(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_account_id: str,
    account_name: str,
) -> str | None:
    row = await conn.fetchrow(
        "SELECT bank_account_id FROM dim_bank_accounts "
        "WHERE tenant_id=$1 AND source=$2 AND source_account_id=$3",
        tenant_id, source, source_account_id,
    )
    if row:
        return str(row["bank_account_id"])

    new_id = await conn.fetchval(
        """
        INSERT INTO dim_bank_accounts
            (tenant_id, source, source_account_id, account_name)
        VALUES ($1,$2,$3,$4)
        ON CONFLICT (tenant_id, source, source_account_id) DO NOTHING
        RETURNING bank_account_id
        """,
        tenant_id, source, source_account_id, account_name,
    )
    return str(new_id) if new_id else None


# ── Entity processors ──────────────────────────────────────────────────────────

async def _process_invoice(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    payload_id: str,
    raw: dict,
) -> None:
    """Upsert an AR invoice into stg_invoices."""
    s = DataSource(source)

    if s == DataSource.TALLY:
        inv = _normalise_tally_invoice(raw)
    else:
        inv = _normalise_apideck_invoice(raw)

    if not inv:
        raise ValueError("could not normalise invoice payload")

    source_invoice_id  = inv["source_invoice_id"]
    source_updated_at  = inv.get("source_updated_at") or datetime.now(timezone.utc)
    invoice_number     = inv.get("invoice_number", "")
    invoice_date       = inv.get("invoice_date") or date.today()
    due_date           = inv.get("due_date")
    currency_code      = (inv.get("currency_code") or "INR")[:3]
    subtotal           = _parse_amount(inv.get("subtotal"))
    discount_amount    = _parse_amount(inv.get("discount_amount"))
    tax_amount         = _parse_amount(inv.get("tax_amount"))
    total_amount       = _parse_amount(inv.get("total_amount")) or (subtotal + tax_amount - discount_amount)
    amount_paid        = _parse_amount(inv.get("amount_paid"))
    outstanding_amount = _parse_amount(inv.get("outstanding_amount")) or max(total_amount - amount_paid, 0)
    status             = inv.get("status", InvoiceStatus.DRAFT)
    gstin_seller       = inv.get("gstin_seller")
    gstin_buyer        = inv.get("gstin_buyer")

    customer_raw = inv.get("customer") or {}
    end_customer_id = await _resolve_end_customer(
        conn,
        tenant_id=tenant_id,
        source=source,
        source_customer_id=str(customer_raw.get("id", "")),
        company_name=customer_raw.get("name", "Unknown"),
        email=customer_raw.get("email"),
        phone=customer_raw.get("phone"),
    )

    stg_invoice_id = await conn.fetchval(
        """
        INSERT INTO stg_invoices (
            tenant_id, source, source_invoice_id, source_updated_at, raw_payload_id,
            invoice_number, invoice_date, due_date, end_customer_id,
            currency_code, subtotal, discount_amount, tax_amount,
            total_amount, amount_paid, outstanding_amount,
            status, gstin_seller, gstin_buyer,
            reverse_charge, validation_flag, sync_status
        ) VALUES (
            $1,$2,$3,$4,$5,
            $6,$7,$8,$9,
            $10,$11,$12,$13,
            $14,$15,$16,
            $17,$18,$19,
            false,'OK','PENDING'
        )
        ON CONFLICT (tenant_id, source, source_invoice_id) DO UPDATE SET
            source_updated_at  = EXCLUDED.source_updated_at,
            invoice_number     = EXCLUDED.invoice_number,
            due_date           = EXCLUDED.due_date,
            subtotal           = EXCLUDED.subtotal,
            discount_amount    = EXCLUDED.discount_amount,
            tax_amount         = EXCLUDED.tax_amount,
            total_amount       = EXCLUDED.total_amount,
            amount_paid        = EXCLUDED.amount_paid,
            outstanding_amount = EXCLUDED.outstanding_amount,
            status             = EXCLUDED.status,
            gstin_seller       = COALESCE(EXCLUDED.gstin_seller, stg_invoices.gstin_seller),
            gstin_buyer        = COALESCE(EXCLUDED.gstin_buyer,  stg_invoices.gstin_buyer),
            sync_status        = CASE
                WHEN stg_invoices.source_updated_at < EXCLUDED.source_updated_at
                THEN 'PENDING' ELSE stg_invoices.sync_status
            END,
            updated_at         = now()
        RETURNING stg_invoice_id
        """,
        tenant_id, source, source_invoice_id, source_updated_at, payload_id,
        invoice_number, invoice_date, due_date, end_customer_id,
        currency_code, subtotal, discount_amount, tax_amount,
        total_amount, amount_paid, outstanding_amount,
        status, gstin_seller, gstin_buyer,
    )
    await _audit(conn, tenant_id=tenant_id, table_name="stg_invoices",
                 entity_id=stg_invoice_id, action="INSERT",
                 new_values={"source": source, "invoice_number": invoice_number,
                             "total_amount": total_amount, "status": status.value})

    # Upsert line items if present
    for li in inv.get("line_items", []):
        await _upsert_invoice_line_item(conn, tenant_id, source, source_invoice_id, li)


async def _upsert_invoice_line_item(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    source_invoice_id: str,
    li: dict,
) -> None:
    stg_id = await conn.fetchval(
        "SELECT stg_invoice_id FROM stg_invoices "
        "WHERE tenant_id=$1 AND source=$2 AND source_invoice_id=$3",
        tenant_id, source, source_invoice_id,
    )
    if not stg_id:
        return

    line_number  = int(li.get("line_number") or li.get("sequence") or 1)
    product_name = str(li.get("name") or li.get("description") or "")
    quantity     = _parse_amount(li.get("quantity", 1))
    unit_price   = _parse_amount(li.get("unit_price") or li.get("unit_amount"))
    tax_rate_pct = _parse_amount(li.get("tax_rate") or li.get("tax_percentage"))
    tax_amount   = _parse_amount(li.get("tax_amount"))
    discount_amt = _parse_amount(li.get("discount_amount"))
    line_total   = _parse_amount(li.get("total_amount") or li.get("sub_total")) or (
        quantity * unit_price - discount_amt + tax_amount
    )

    await conn.execute(
        """
        INSERT INTO stg_invoice_line_items (
            stg_invoice_id, tenant_id, line_number, product_name,
            hsn_code, quantity, unit_price,
            discount_amount, tax_rate_percent, tax_amount, line_total
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT DO NOTHING
        """,
        stg_id, tenant_id, line_number, product_name,
        li.get("hsn_code"), quantity, unit_price,
        discount_amt, tax_rate_pct, tax_amount, line_total,
    )


async def _process_payment(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    payload_id: str,
    raw: dict,
) -> None:
    """Upsert a payment into stg_payments."""
    s = DataSource(source)
    pmt = _normalise_apideck_payment(raw) if s != DataSource.TALLY else _normalise_tally_payment(raw)

    if not pmt:
        raise ValueError("could not normalise payment payload")

    source_payment_id = pmt["source_payment_id"]
    source_updated_at = pmt.get("source_updated_at") or datetime.now(timezone.utc)
    payment_date      = pmt.get("payment_date") or date.today()
    amount            = _parse_amount(pmt.get("amount"))
    currency_code     = (pmt.get("currency_code") or "INR")[:3]
    status            = pmt.get("status", PaymentStatus.PENDING)
    payment_method    = pmt.get("payment_method")
    transaction_ref   = pmt.get("transaction_ref")
    gateway           = pmt.get("gateway")

    customer_raw = pmt.get("customer") or {}
    end_customer_id = await _resolve_end_customer(
        conn,
        tenant_id=tenant_id,
        source=source,
        source_customer_id=str(customer_raw.get("id", "")),
        company_name=customer_raw.get("name", "Unknown"),
    )

    # Resolve optional linked invoice
    stg_invoice_id = None
    src_inv_id = pmt.get("source_invoice_id")
    if src_inv_id:
        stg_invoice_id = await conn.fetchval(
            "SELECT stg_invoice_id FROM stg_invoices "
            "WHERE tenant_id=$1 AND source=$2 AND source_invoice_id=$3",
            tenant_id, source, str(src_inv_id),
        )

    # Resolve optional bank account
    bank_account_id = None
    bank_acc_raw = pmt.get("bank_account") or {}
    if bank_acc_raw.get("id"):
        bank_account_id = await _resolve_bank_account(
            conn, tenant_id, source,
            str(bank_acc_raw["id"]),
            bank_acc_raw.get("name", ""),
        )

    stg_payment_id = await conn.fetchval(
        """
        INSERT INTO stg_payments (
            tenant_id, source, source_payment_id, source_updated_at,
            stg_invoice_id, end_customer_id, bank_account_id,
            payment_date, amount, currency_code,
            payment_method, transaction_ref, gateway,
            status, is_reconciled, sync_status
        ) VALUES (
            $1,$2,$3,$4,
            $5,$6,$7,
            $8,$9,$10,
            $11,$12,$13,
            $14,false,'PENDING'
        )
        ON CONFLICT (tenant_id, source, source_payment_id) DO UPDATE SET
            source_updated_at = EXCLUDED.source_updated_at,
            amount            = EXCLUDED.amount,
            status            = EXCLUDED.status,
            transaction_ref   = COALESCE(EXCLUDED.transaction_ref, stg_payments.transaction_ref),
            sync_status       = CASE
                WHEN stg_payments.source_updated_at < EXCLUDED.source_updated_at
                THEN 'PENDING' ELSE stg_payments.sync_status
            END
        RETURNING stg_payment_id
        """,
        tenant_id, source, source_payment_id, source_updated_at,
        stg_invoice_id, end_customer_id, bank_account_id,
        payment_date, amount, currency_code,
        payment_method, transaction_ref, gateway,
        status,
    )
    await _audit(conn, tenant_id=tenant_id, table_name="stg_payments",
                 entity_id=stg_payment_id, action="INSERT",
                 new_values={"source": source, "amount": amount, "status": status.value})


async def _process_bill(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    payload_id: str,
    raw: dict,
) -> None:
    """Upsert an AP bill into stg_bills."""
    s = DataSource(source)
    bill = _normalise_apideck_bill(raw) if s != DataSource.TALLY else _normalise_tally_bill(raw)

    if not bill:
        raise ValueError("could not normalise bill payload")

    source_bill_id    = bill["source_bill_id"]
    source_updated_at = bill.get("source_updated_at") or datetime.now(timezone.utc)
    bill_number       = bill.get("bill_number", "")
    bill_date         = bill.get("bill_date") or date.today()
    due_date          = bill.get("due_date")
    subtotal          = _parse_amount(bill.get("subtotal"))
    discount_amount   = _parse_amount(bill.get("discount_amount"))
    tax_amount        = _parse_amount(bill.get("tax_amount"))
    total_amount      = _parse_amount(bill.get("total_amount")) or (subtotal + tax_amount - discount_amount)
    amount_paid       = _parse_amount(bill.get("amount_paid"))
    outstanding_amount = _parse_amount(bill.get("outstanding_amount")) or max(total_amount - amount_paid, 0)
    status            = bill.get("status", InvoiceStatus.DRAFT)
    currency_code     = (bill.get("currency_code") or "INR")[:3].upper()
    if currency_code not in ("USD", "EUR", "GBP", "JPY", "INR"):
        currency_code = "INR"
    original_amount   = total_amount if currency_code != "INR" else None

    supplier_raw = bill.get("supplier") or {}
    supplier_id = await _resolve_supplier(
        conn,
        tenant_id=tenant_id,
        source=source,
        source_supplier_id=str(supplier_raw.get("id", "")),
        name=supplier_raw.get("name", "Unknown Supplier"),
        email=supplier_raw.get("email"),
    )

    stg_bill_id = await conn.fetchval(
        """
        INSERT INTO stg_bills (
            tenant_id, source, source_bill_id, source_updated_at, raw_payload_id,
            bill_number, bill_date, due_date, supplier_id,
            currency_code, subtotal, discount_amount, tax_amount,
            total_amount, amount_paid, outstanding_amount,
            original_amount,
            status, validation_flag, sync_status
        ) VALUES (
            $1,$2,$3,$4,$5,
            $6,$7,$8,$9,
            $10,$11,$12,$13,
            $14,$15,$16,
            $17,
            $18,'OK','PENDING'
        )
        ON CONFLICT (tenant_id, source, source_bill_id) DO UPDATE SET
            source_updated_at  = EXCLUDED.source_updated_at,
            due_date           = EXCLUDED.due_date,
            subtotal           = EXCLUDED.subtotal,
            tax_amount         = EXCLUDED.tax_amount,
            total_amount       = EXCLUDED.total_amount,
            amount_paid        = EXCLUDED.amount_paid,
            outstanding_amount = EXCLUDED.outstanding_amount,
            original_amount    = EXCLUDED.original_amount,
            status             = EXCLUDED.status,
            sync_status        = CASE
                WHEN stg_bills.source_updated_at < EXCLUDED.source_updated_at
                THEN 'PENDING' ELSE stg_bills.sync_status
            END,
            updated_at         = now()
        RETURNING stg_bill_id
        """,
        tenant_id, source, source_bill_id, source_updated_at, payload_id,
        bill_number, bill_date, due_date, supplier_id,
        currency_code, subtotal, discount_amount, tax_amount,
        total_amount, amount_paid, outstanding_amount,
        original_amount,
        status,
    )
    await _audit(conn, tenant_id=tenant_id, table_name="stg_bills",
                 entity_id=stg_bill_id, action="INSERT",
                 new_values={"source": source, "bill_number": str(bill_number),
                             "total_amount": total_amount, "status": status.value})


async def _process_customer(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    raw: dict,
) -> None:
    """Upsert customer into dim_end_customers."""
    cust_id  = str(raw.get("id", raw.get("customer_id", "")))
    name     = raw.get("display_name") or raw.get("company_name") or raw.get("name", "Unknown")
    email    = raw.get("email")
    phone    = raw.get("phone_number") or raw.get("phone")
    terms    = int(raw.get("payment_terms") or raw.get("payment_terms_days") or 30)
    gstin    = (raw.get("tax_number") or raw.get("gstin") or "")[:15] or None
    currency = (raw.get("currency") or "INR")[:3]

    await conn.execute(
        """
        INSERT INTO dim_end_customers
            (tenant_id, source, source_customer_id, company_name, email,
             phone, payment_terms_days, gstin, currency_code)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT (tenant_id, source, source_customer_id) DO UPDATE SET
            company_name       = EXCLUDED.company_name,
            email              = COALESCE(EXCLUDED.email, dim_end_customers.email),
            phone              = COALESCE(EXCLUDED.phone, dim_end_customers.phone),
            payment_terms_days = EXCLUDED.payment_terms_days,
            gstin              = COALESCE(EXCLUDED.gstin, dim_end_customers.gstin),
            updated_at         = now()
        """,
        tenant_id, source, cust_id, name, email, phone, terms, gstin, currency,
    )


async def _process_supplier(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    raw: dict,
) -> None:
    """Upsert supplier into dim_suppliers."""
    sup_id = str(raw.get("id", raw.get("supplier_id", "")))
    name   = raw.get("display_name") or raw.get("company_name") or raw.get("name", "Unknown Supplier")
    email  = raw.get("email")
    gstn   = (raw.get("tax_number") or raw.get("gstn") or "")[:15] or None
    terms  = int(raw.get("payment_terms") or raw.get("payment_terms_days") or 30)

    await conn.execute(
        """
        INSERT INTO dim_suppliers
            (tenant_id, source, source_supplier_id, name, email, gstn, payment_terms_days)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (tenant_id, source, source_supplier_id) DO UPDATE SET
            name               = EXCLUDED.name,
            email              = COALESCE(EXCLUDED.email, dim_suppliers.email),
            gstn               = COALESCE(EXCLUDED.gstn,  dim_suppliers.gstn),
            payment_terms_days = EXCLUDED.payment_terms_days
        """,
        tenant_id, source, sup_id, name, email, gstn, terms,
    )


async def _process_journal(
    conn: asyncpg.Connection,
    tenant_id: str,
    source: str,
    payload_id: str,
    raw: dict,
) -> None:
    """Upsert journal entry into stg_journal_entries + lines."""
    src_journal_id    = str(raw.get("id", ""))
    source_updated_at = datetime.now(timezone.utc)
    journal_number    = raw.get("memo_url") or raw.get("journal_number") or raw.get("number")
    journal_date      = _parse_date(raw.get("transaction_date") or raw.get("journal_date")) or date.today()
    narration         = raw.get("memo") or raw.get("narration")
    lines             = raw.get("line_items") or []

    total_debit  = sum(_parse_amount(l.get("debit_amount",  0)) for l in lines)
    total_credit = sum(_parse_amount(l.get("credit_amount", 0)) for l in lines)
    is_balanced  = abs(total_debit - total_credit) < 0.01

    stg_journal_id = await conn.fetchval(
        """
        INSERT INTO stg_journal_entries
            (tenant_id, source, source_journal_id, source_updated_at, raw_payload_id,
             journal_number, journal_date, narration,
             total_debit, total_credit, is_balanced, sync_status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'PENDING')
        ON CONFLICT (tenant_id, source, source_journal_id) DO UPDATE SET
            source_updated_at = EXCLUDED.source_updated_at,
            total_debit       = EXCLUDED.total_debit,
            total_credit      = EXCLUDED.total_credit,
            is_balanced       = EXCLUDED.is_balanced,
            updated_at        = now()
        RETURNING stg_journal_id
        """,
        tenant_id, source, src_journal_id, source_updated_at, payload_id,
        journal_number, journal_date, narration,
        total_debit, total_credit, is_balanced,
    )

    for idx, line in enumerate(lines, start=1):
        src_acct_id = str(line.get("ledger_account", {}).get("id") or line.get("account_id") or "")
        account_id  = None
        if src_acct_id:
            account_id = await conn.fetchval(
                "SELECT account_id FROM dim_ledger_accounts "
                "WHERE tenant_id=$1 AND source=$2 AND source_account_id=$3",
                tenant_id, source, src_acct_id,
            )
        if not account_id:
            continue  # skip lines without a resolvable account

        debit_amount  = _parse_amount(line.get("debit_amount",  0))
        credit_amount = _parse_amount(line.get("credit_amount", 0))
        await conn.execute(
            """
            INSERT INTO stg_journal_entry_lines
                (stg_journal_id, tenant_id, line_number,
                 account_id, debit_amount, credit_amount, narration)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT DO NOTHING
            """,
            stg_journal_id, tenant_id, idx,
            account_id, debit_amount, credit_amount, line.get("description"),
        )


# ── Source-specific normalisers ───────────────────────────────────────────────

def _normalise_apideck_invoice(raw: dict) -> dict | None:
    """Normalise an Apideck-unified invoice response."""
    if not raw.get("id"):
        return None
    inv_date = _parse_date(raw.get("date") or raw.get("invoice_date"))
    return {
        "source_invoice_id":  str(raw["id"]),
        "source_updated_at":  _parse_date(raw.get("updated_at") or raw.get("modified_at")),
        "invoice_number":     raw.get("number") or raw.get("invoice_number", ""),
        "invoice_date":       inv_date,
        "due_date":           _parse_date(raw.get("due_date")),
        "customer":           raw.get("customer") or {},
        "currency_code":      raw.get("currency", "INR"),
        "subtotal":           raw.get("sub_total") or raw.get("subtotal", 0),
        "discount_amount":    raw.get("discount_amount", 0),
        "tax_amount":         raw.get("tax_amount", 0),
        "total_amount":       raw.get("total", 0),
        "amount_paid":        (raw.get("total", 0) - raw.get("balance", 0)),
        "outstanding_amount": raw.get("balance", 0),
        "status":             _APIDECK_INVOICE_STATUS.get(
                                  (raw.get("status") or "draft").lower(),
                                  InvoiceStatus.DRAFT
                              ),
        "gstin_seller":       None,
        "gstin_buyer":        None,
        "line_items":         raw.get("line_items") or [],
    }


def _normalise_tally_invoice(raw: dict) -> dict | None:
    """Normalise a Tally Sales Voucher JSON payload."""
    voucher = raw.get("VOUCHER") or raw.get("voucher") or raw
    if not voucher:
        return None

    date_str = str(voucher.get("DATE") or voucher.get("date") or "")
    try:
        inv_date = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        inv_date = date.today()

    amount = abs(_parse_amount(voucher.get("AMOUNT") or voucher.get("amount") or 0))
    party  = voucher.get("PARTYLEDGERNAME") or voucher.get("party_name") or "Unknown"

    return {
        "source_invoice_id":  str(voucher.get("REMOTEID") or voucher.get("remote_id") or ""),
        "source_updated_at":  datetime.now(timezone.utc),
        "invoice_number":     str(voucher.get("VOUCHERNUMBER") or voucher.get("voucher_number") or ""),
        "invoice_date":       inv_date,
        "due_date":           None,
        "customer":           {"id": party, "name": party},
        "currency_code":      "INR",
        "subtotal":           amount,
        "discount_amount":    0,
        "tax_amount":         0,
        "total_amount":       amount,
        "amount_paid":        0,
        "outstanding_amount": amount,
        "status":             InvoiceStatus.SENT,
        "line_items":         [],
    }


def _normalise_apideck_payment(raw: dict) -> dict | None:
    if not raw.get("id"):
        return None
    return {
        "source_payment_id": str(raw["id"]),
        "source_updated_at": _parse_date(raw.get("updated_at")),
        "payment_date":      _parse_date(raw.get("transaction_date") or raw.get("date")),
        "amount":            raw.get("total_amount", 0),
        "currency_code":     raw.get("currency", "INR"),
        "status":            _APIDECK_PAYMENT_STATUS.get(
                                 (raw.get("status") or "pending").lower(),
                                 PaymentStatus.PENDING
                             ),
        "payment_method":    _APIDECK_PAYMENT_METHOD.get(
                                 (raw.get("payment_method") or "").lower()
                             ),
        "transaction_ref":   raw.get("reference"),
        "gateway":           raw.get("payment_service_id"),
        "customer":          raw.get("customer") or {},
        "source_invoice_id": (raw.get("invoice") or {}).get("id"),
        "bank_account":      raw.get("account") or {},
    }


def _normalise_tally_payment(raw: dict) -> dict | None:
    voucher = raw.get("VOUCHER") or raw
    amount = abs(_parse_amount(voucher.get("AMOUNT") or 0))
    party  = voucher.get("PARTYLEDGERNAME") or "Unknown"
    return {
        "source_payment_id": str(voucher.get("REMOTEID") or ""),
        "source_updated_at": datetime.now(timezone.utc),
        "payment_date":      date.today(),
        "amount":            amount,
        "currency_code":     "INR",
        "status":            PaymentStatus.CLEARED,
        "payment_method":    PaymentMethod.BANK_TRANSFER,
        "customer":          {"id": party, "name": party},
    }


def _normalise_apideck_bill(raw: dict) -> dict | None:
    if not raw.get("id"):
        return None
    return {
        "source_bill_id":   str(raw["id"]),
        "source_updated_at": _parse_date(raw.get("updated_at")),
        "bill_number":       raw.get("bill_number") or raw.get("number", ""),
        "bill_date":         _parse_date(raw.get("bill_date") or raw.get("date")),
        "due_date":          _parse_date(raw.get("due_date")),
        "supplier":          raw.get("supplier") or {},
        "currency_code":     raw.get("currency", "INR"),
        "subtotal":          raw.get("sub_total", 0),
        "discount_amount":   raw.get("discount_amount", 0),
        "tax_amount":        raw.get("tax_amount", 0),
        "total_amount":      raw.get("total", 0),
        "amount_paid":       (raw.get("total", 0) - raw.get("balance", 0)),
        "outstanding_amount": raw.get("balance", 0),
        "status":            _APIDECK_INVOICE_STATUS.get(
                                 (raw.get("status") or "draft").lower(),
                                 InvoiceStatus.DRAFT
                             ),
    }


def _normalise_tally_bill(raw: dict) -> dict | None:
    voucher = raw.get("VOUCHER") or raw
    amount  = abs(_parse_amount(voucher.get("AMOUNT") or 0))
    party   = voucher.get("PARTYLEDGERNAME") or "Unknown Supplier"
    return {
        "source_bill_id":    str(voucher.get("REMOTEID") or ""),
        "source_updated_at": datetime.now(timezone.utc),
        "bill_number":       str(voucher.get("VOUCHERNUMBER") or ""),
        "bill_date":         date.today(),
        "supplier":          {"id": party, "name": party},
        "currency_code":     "INR",
        "subtotal":          amount,
        "discount_amount":   0,
        "tax_amount":        0,
        "total_amount":      amount,
        "amount_paid":       0,
        "outstanding_amount": amount,
        "status":            InvoiceStatus.SENT,
    }


# ── Worker ─────────────────────────────────────────────────────────────────────

_PROCESSOR_MAP = {
    EntityType.INVOICE:  _process_invoice,
    EntityType.PAYMENT:  _process_payment,
    EntityType.BILL:     _process_bill,
}


class ApiSyncWorker(BaseWorker):
    name           = "api_sync"
    notify_channel = "ch_api_sync"

    async def process_batch(self) -> int:
        pool = await get_pool()
        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    """
                    SELECT payload_id, tenant_id, source, entity_type,
                           raw_payload, sync_job_id, received_at
                    FROM raw_api_payloads
                    WHERE is_processed = false
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
                    SELECT payload_id, tenant_id, source, entity_type,
                           raw_payload, sync_job_id, received_at
                    FROM raw_api_payloads
                    WHERE is_processed = false
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
            payload_id = str(row["payload_id"])
            source     = row["source"]
            entity_str = row["entity_type"]
            raw        = row["raw_payload"]
            if isinstance(raw, str):
                raw = json.loads(raw)

            # C5: strict tenant null guard — quarantine immediately, no fallback
            if not row["tenant_id"]:
                logger.error(
                    "[api_sync] payload=%s has no tenant_id — quarantining", payload_id
                )
                await self._mark_processed(pool, payload_id)
                continue
            tenant_id = str(row["tenant_id"])

            try:
                entity_type = EntityType(entity_str)
            except ValueError:
                logger.warning("[api_sync] unknown entity_type=%s payload=%s", entity_str, payload_id)
                await self._mark_processed(pool, payload_id)
                continue

            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        if entity_type == EntityType.CUSTOMER:
                            await _process_customer(conn, tenant_id, source, raw)
                        elif entity_type == EntityType.SUPPLIER:
                            await _process_supplier(conn, tenant_id, source, raw)
                        elif entity_type == EntityType.JOURNAL:
                            await _process_journal(conn, tenant_id, source, payload_id, raw)
                        elif entity_type in _PROCESSOR_MAP:
                            await _PROCESSOR_MAP[entity_type](conn, tenant_id, source, payload_id, raw)

                        await conn.execute(
                            "UPDATE raw_api_payloads SET is_processed=true WHERE payload_id=$1",
                            payload_id,
                        )
                processed += 1
                logger.debug("[api_sync] processed %s %s for tenant %s", entity_type, payload_id, tenant_id)

            except Exception as exc:
                logger.exception("[api_sync] error processing payload=%s", payload_id)
                # C7: increment retry_count; quarantine to raw_ingestion_errors after 3 failures
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        new_retry = await conn.fetchval(
                            "UPDATE raw_api_payloads SET retry_count = retry_count + 1 "
                            "WHERE payload_id=$1 RETURNING retry_count",
                            payload_id,
                        )
                        if new_retry >= 3:
                            await self.log_error(
                                conn,
                                tenant_id=tenant_id,
                                stage="api_sync",
                                error_type=type(exc).__name__,
                                details={"message": str(exc), "entity_type": entity_str},
                                payload_id=payload_id,
                            )
                            await conn.execute(
                                "UPDATE raw_api_payloads SET is_processed=true WHERE payload_id=$1",
                                payload_id,
                            )
                            logger.warning(
                                "[api_sync] payload=%s quarantined after %d failures", payload_id, new_retry
                            )

        logger.info("[api_sync] batch done: %d processed", processed)
        return processed

    async def _mark_processed(self, pool: asyncpg.Pool, payload_id: str) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE raw_api_payloads SET is_processed=true WHERE payload_id=$1",
                payload_id,
            )
