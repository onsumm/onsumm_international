"""
Deduplication & Credit-Integrity Pipeline
──────────────────────────────────────────
Runs continuously alongside the other workers. Per tenant, each cycle:

  1. DEDUP BILLS (AP — money the org owes)
       a. Exact duplicate within stg_bills
          (same tenant + supplier + bill_number + bill_date + total_amount)
          → validation_flag='DUPLICATE', sync_status='ERROR'  (blocks promotion)
       b. Already-promoted cross-table duplicate
          (stg_bill matches an existing fact_bill by a different stg_bill_id)
          → same flags
       c. Soft duplicate
          (same supplier + ≈amount within ±7 days, different bill_number)
          → validation_flag='REVIEW'  (held for human inspection)

  2. DEDUP INVOICES (AR — money owed to the org)
       Same three checks against stg_invoices / fact_invoices.

  3. RECONCILE FAILED PAYMENTS
       Scans payment_transactions with status FAILED / BOUNCED / REVERSED /
       CANCELLED that were updated in the last 48 hours, then:
         - Recalculates fact_bills.amount_paid  (COMPLETED txns only)
         - Recalculates fact_invoices.amount_paid
         - Resets payment_schedules.paid_amount + outstanding_amount
         - Corrects status back from PAID → PARTIAL/SENT if credit drops

  4. WRITE ai_anomalies
       Idempotent: one unresolved anomaly row per (entity, anomaly_type).
       Types emitted:
         DUPLICATE_BILL / SOFT_DUPLICATE_BILL
         DUPLICATE_INVOICE / SOFT_DUPLICATE_INVOICE
         PAYMENT_FAILURE_AP / PAYMENT_FAILURE_AR
         DUPLICATE_VENDOR

  5. DEDUP VENDORS (dim_suppliers)
       Same company registered under two supplier_ids — typically because a
       later bill upload extracted a GSTIN with a typo (e.g. a transposed
       character), which broke exact-GSTIN vendor matching and silently
       created a second vendor instead of matching the existing one.
         a. Same bank_account + bank_ifsc under a different supplier_id
            → DUPLICATE_VENDOR, severity HIGH (a bank account can't
              legitimately belong to two different vendors)
         b. Same name (case/whitespace-insensitive), different GSTIN,
            no bank-account match yet available
            → DUPLICATE_VENDOR, severity MEDIUM
       Unlike bill/invoice dedup, this never auto-merges or deletes —
       vendor master data fans out into bank accounts, bills, TDS overrides,
       etc., so a wrong auto-merge is far more damaging than a wrong
       auto-block. It only writes a review anomaly for a human to resolve.

Credit-sum invariant:
  amount_paid        = SUM(paid_amount) of COMPLETED payment_transactions
                       linked via payment_schedules
  outstanding_amount = GREATEST(total_amount - amount_paid, 0)
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

import asyncpg

import config
from db import get_pool
from pipelines.base import BaseWorker
_audit = BaseWorker.log_audit

logger = logging.getLogger(__name__)

# ₹1 tolerance for "same amount" soft-duplicate matching
_AMOUNT_TOL = 1.0
# Day window for soft-duplicate date matching
_SOFT_DUP_DAYS = 7


# ── 1. Bill deduplication ──────────────────────────────────────────────────────

async def _dedup_bills(conn: asyncpg.Connection, tenant_id: str) -> int:
    flagged = 0

    # 1a. Exact duplicates within stg_bills — keep the earliest, flag the rest
    dup_ids = await conn.fetch(
        """
        WITH ranked AS (
            SELECT stg_bill_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, supplier_id,
                                    bill_number, bill_date,
                                    COALESCE(original_amount, total_amount),
                                    currency_code
                       ORDER BY created_at ASC
                   ) AS rn
            FROM stg_bills
            WHERE tenant_id     = $1::uuid
              AND validation_flag = 'OK'
              AND sync_status   IN ('PENDING', 'ERROR')
        )
        SELECT stg_bill_id FROM ranked WHERE rn > 1
        """,
        tenant_id,
    )
    for row in dup_ids:
        await conn.execute(
            """
            UPDATE stg_bills
            SET validation_flag = 'DUPLICATE',
                sync_status     = 'ERROR',
                updated_at      = now()
            WHERE stg_bill_id = $1
            """,
            row["stg_bill_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_bills",
                     entity_id=row["stg_bill_id"], action="UPDATE",
                     new_values={"validation_flag": "DUPLICATE", "sync_status": "ERROR"})
        flagged += 1

    # 1b. Cross-table: stg_bill matches a fact_bill promoted from a different stg_bill_id
    cross_ids = await conn.fetch(
        """
        SELECT sb.stg_bill_id
        FROM stg_bills sb
        JOIN fact_bills fb
          ON  fb.tenant_id   = sb.tenant_id
          AND fb.supplier_id = sb.supplier_id
          AND fb.bill_number = sb.bill_number
          AND fb.bill_date   = sb.bill_date
          AND ABS(fb.total_amount - sb.total_amount) < $2
          AND fb.stg_bill_id != sb.stg_bill_id
        WHERE sb.tenant_id      = $1::uuid
          AND sb.validation_flag = 'OK'
          AND sb.sync_status    = 'PENDING'
        """,
        tenant_id, _AMOUNT_TOL,
    )
    for row in cross_ids:
        await conn.execute(
            """
            UPDATE stg_bills
            SET validation_flag = 'DUPLICATE',
                sync_status     = 'ERROR',
                updated_at      = now()
            WHERE stg_bill_id = $1
            """,
            row["stg_bill_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_bills",
                     entity_id=row["stg_bill_id"], action="UPDATE",
                     new_values={"validation_flag": "DUPLICATE", "sync_status": "ERROR",
                                 "reason": "cross_table"})
        flagged += 1

    # 1c. Soft duplicates — same supplier + ≈amount + ±7 days + different bill_number
    soft_ids = await conn.fetch(
        """
        SELECT DISTINCT sb1.stg_bill_id
        FROM stg_bills sb1
        JOIN stg_bills sb2
          ON  sb2.tenant_id   = sb1.tenant_id
          AND sb2.supplier_id = sb1.supplier_id
          AND sb2.stg_bill_id != sb1.stg_bill_id
          AND sb2.bill_number != sb1.bill_number
          AND ABS(sb2.total_amount - sb1.total_amount) < $2
          AND ABS(sb2.bill_date   - sb1.bill_date)    <= $3
          AND sb2.created_at  < sb1.created_at
        WHERE sb1.tenant_id      = $1::uuid
          AND sb1.validation_flag = 'OK'
          AND sb1.sync_status    = 'PENDING'
        """,
        tenant_id, _AMOUNT_TOL, _SOFT_DUP_DAYS,
    )
    for row in soft_ids:
        await conn.execute(
            """
            UPDATE stg_bills
            SET validation_flag = 'REVIEW',
                updated_at      = now()
            WHERE stg_bill_id = $1
              AND validation_flag = 'OK'
            """,
            row["stg_bill_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_bills",
                     entity_id=row["stg_bill_id"], action="UPDATE",
                     new_values={"validation_flag": "REVIEW", "reason": "soft_duplicate"})
        flagged += 1

    return flagged


# ── 2. Invoice deduplication ───────────────────────────────────────────────────

async def _dedup_invoices(conn: asyncpg.Connection, tenant_id: str) -> int:
    flagged = 0

    # 2a. Exact duplicates within stg_invoices
    dup_ids = await conn.fetch(
        """
        WITH ranked AS (
            SELECT stg_invoice_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY tenant_id, end_customer_id,
                                    invoice_number, invoice_date, total_amount
                       ORDER BY created_at ASC
                   ) AS rn
            FROM stg_invoices
            WHERE tenant_id      = $1::uuid
              AND validation_flag = 'OK'
              AND sync_status    IN ('PENDING', 'ERROR')
        )
        SELECT stg_invoice_id FROM ranked WHERE rn > 1
        """,
        tenant_id,
    )
    for row in dup_ids:
        await conn.execute(
            """
            UPDATE stg_invoices
            SET validation_flag = 'DUPLICATE',
                sync_status     = 'ERROR',
                updated_at      = now()
            WHERE stg_invoice_id = $1
            """,
            row["stg_invoice_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_invoices",
                     entity_id=row["stg_invoice_id"], action="UPDATE",
                     new_values={"validation_flag": "DUPLICATE", "sync_status": "ERROR"})
        flagged += 1

    # 2b. Cross-table: stg_invoice matches a fact_invoice from a different stg_invoice_id
    cross_ids = await conn.fetch(
        """
        SELECT si.stg_invoice_id
        FROM stg_invoices si
        JOIN fact_invoices fi
          ON  fi.tenant_id       = si.tenant_id
          AND fi.end_customer_id = si.end_customer_id
          AND fi.invoice_number  = si.invoice_number
          AND fi.invoice_date    = si.invoice_date
          AND ABS(fi.total_amount - si.total_amount) < $2
          AND fi.stg_invoice_id != si.stg_invoice_id
        WHERE si.tenant_id      = $1::uuid
          AND si.validation_flag = 'OK'
          AND si.sync_status    = 'PENDING'
        """,
        tenant_id, _AMOUNT_TOL,
    )
    for row in cross_ids:
        await conn.execute(
            """
            UPDATE stg_invoices
            SET validation_flag = 'DUPLICATE',
                sync_status     = 'ERROR',
                updated_at      = now()
            WHERE stg_invoice_id = $1
            """,
            row["stg_invoice_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_invoices",
                     entity_id=row["stg_invoice_id"], action="UPDATE",
                     new_values={"validation_flag": "DUPLICATE", "sync_status": "ERROR",
                                 "reason": "cross_table"})
        flagged += 1

    # 2c. Soft duplicates — same customer + ≈amount + ±7 days + different invoice_number
    soft_ids = await conn.fetch(
        """
        SELECT DISTINCT si1.stg_invoice_id
        FROM stg_invoices si1
        JOIN stg_invoices si2
          ON  si2.tenant_id       = si1.tenant_id
          AND si2.end_customer_id = si1.end_customer_id
          AND si2.stg_invoice_id  != si1.stg_invoice_id
          AND si2.invoice_number  != si1.invoice_number
          AND ABS(si2.total_amount  - si1.total_amount)  < $2
          AND ABS(si2.invoice_date  - si1.invoice_date)  <= $3
          AND si2.created_at < si1.created_at
        WHERE si1.tenant_id      = $1::uuid
          AND si1.validation_flag = 'OK'
          AND si1.sync_status    = 'PENDING'
        """,
        tenant_id, _AMOUNT_TOL, _SOFT_DUP_DAYS,
    )
    for row in soft_ids:
        await conn.execute(
            """
            UPDATE stg_invoices
            SET validation_flag = 'REVIEW',
                updated_at      = now()
            WHERE stg_invoice_id = $1
              AND validation_flag = 'OK'
            """,
            row["stg_invoice_id"],
        )
        await _audit(conn, tenant_id=tenant_id, table_name="stg_invoices",
                     entity_id=row["stg_invoice_id"], action="UPDATE",
                     new_values={"validation_flag": "REVIEW", "reason": "soft_duplicate"})
        flagged += 1

    return flagged


# ── 3. Reconcile failed / bounced payments ─────────────────────────────────────

async def _recalc_bill_credit(
    conn: asyncpg.Connection,
    bill_id: str,
    stg_bill_id: str,
    total_amount: Decimal,
    tenant_id: str = "",
) -> None:
    """Recompute amount_paid/outstanding/status for one fact_bill from COMPLETED txns only."""
    new_paid = Decimal(str(await conn.fetchval(
        """
        SELECT COALESCE(SUM(pt.paid_amount), 0)
        FROM payment_transactions pt
        JOIN payment_schedules ps ON pt.schedule_id = ps.schedule_id
        WHERE ps.stg_bill_id = $1
          AND pt.cycle_type  = 'PAYABLE'
          AND pt.status      = 'COMPLETED'
        """,
        stg_bill_id,
    ) or 0))

    new_outstanding = max(total_amount - new_paid, Decimal("0"))
    new_status = (
        "PAID"    if new_paid >= total_amount and total_amount > 0 else
        "PARTIAL" if new_paid > 0 else
        "SENT"
    )

    await conn.execute(
        """
        UPDATE fact_bills
        SET amount_paid        = $2,
            outstanding_amount = $3,
            status             = $4::invoice_status,
            updated_at         = now()
        WHERE bill_id = $1
          AND (amount_paid != $2 OR outstanding_amount != $3)
        """,
        bill_id, float(new_paid), float(new_outstanding), new_status,
    )
    if tenant_id:
        await _audit(conn, tenant_id=tenant_id, table_name="fact_bills",
                     entity_id=bill_id, action="UPDATE",
                     new_values={"amount_paid": float(new_paid),
                                 "outstanding_amount": float(new_outstanding),
                                 "status": new_status, "reason": "payment_reconciliation"})

    # Mirror back to stg_bills
    await conn.execute(
        """
        UPDATE stg_bills
        SET amount_paid        = $2,
            outstanding_amount = $3,
            status             = $4::invoice_status,
            updated_at         = now()
        WHERE stg_bill_id = $1
        """,
        stg_bill_id, float(new_paid), float(new_outstanding), new_status,
    )

    # Correct each payment_schedule installment
    await conn.execute(
        """
        UPDATE payment_schedules ps
        SET paid_amount = COALESCE((
                SELECT SUM(pt.paid_amount)
                FROM payment_transactions pt
                WHERE pt.schedule_id = ps.schedule_id
                  AND pt.status = 'COMPLETED'
            ), 0),
            outstanding_amount = ps.scheduled_amount - COALESCE((
                SELECT SUM(pt.paid_amount)
                FROM payment_transactions pt
                WHERE pt.schedule_id = ps.schedule_id
                  AND pt.status = 'COMPLETED'
            ), 0),
            status = CASE
                WHEN ps.scheduled_amount <= COALESCE((
                    SELECT SUM(pt.paid_amount) FROM payment_transactions pt
                    WHERE pt.schedule_id = ps.schedule_id AND pt.status = 'COMPLETED'
                ), 0) THEN 'PAID'
                WHEN COALESCE((
                    SELECT SUM(pt.paid_amount) FROM payment_transactions pt
                    WHERE pt.schedule_id = ps.schedule_id AND pt.status = 'COMPLETED'
                ), 0) > 0 THEN 'PARTIAL'
                WHEN ps.due_date < CURRENT_DATE THEN 'OVERDUE'
                ELSE 'PENDING'
            END::schedule_status,
            updated_at = now()
        WHERE ps.stg_bill_id = $1
        """,
        stg_bill_id,
    )


async def _recalc_invoice_credit(
    conn: asyncpg.Connection,
    invoice_id: str,
    stg_invoice_id: str,
    total_amount: Decimal,
    tenant_id: str = "",
) -> None:
    """Recompute amount_paid/outstanding/status for one fact_invoice from COMPLETED txns only."""
    new_paid = Decimal(str(await conn.fetchval(
        """
        SELECT COALESCE(SUM(pt.paid_amount), 0)
        FROM payment_transactions pt
        JOIN payment_schedules ps ON pt.schedule_id = ps.schedule_id
        WHERE ps.stg_invoice_id = $1
          AND pt.cycle_type     = 'RECEIVABLE'
          AND pt.status         = 'COMPLETED'
        """,
        stg_invoice_id,
    ) or 0))

    new_outstanding = max(total_amount - new_paid, Decimal("0"))
    new_status = (
        "PAID"    if new_paid >= total_amount and total_amount > 0 else
        "PARTIAL" if new_paid > 0 else
        "SENT"
    )

    await conn.execute(
        """
        UPDATE fact_invoices
        SET amount_paid        = $2,
            outstanding_amount = $3,
            status             = $4::invoice_status,
            updated_at         = now()
        WHERE invoice_id = $1
          AND (amount_paid != $2 OR outstanding_amount != $3)
        """,
        invoice_id, float(new_paid), float(new_outstanding), new_status,
    )
    if tenant_id:
        await _audit(conn, tenant_id=tenant_id, table_name="fact_invoices",
                     entity_id=invoice_id, action="UPDATE",
                     new_values={"amount_paid": float(new_paid),
                                 "outstanding_amount": float(new_outstanding),
                                 "status": new_status, "reason": "payment_reconciliation"})

    # Correct each payment_schedule installment
    await conn.execute(
        """
        UPDATE payment_schedules ps
        SET paid_amount = COALESCE((
                SELECT SUM(pt.paid_amount)
                FROM payment_transactions pt
                WHERE pt.schedule_id = ps.schedule_id
                  AND pt.status = 'COMPLETED'
            ), 0),
            outstanding_amount = ps.scheduled_amount - COALESCE((
                SELECT SUM(pt.paid_amount)
                FROM payment_transactions pt
                WHERE pt.schedule_id = ps.schedule_id
                  AND pt.status = 'COMPLETED'
            ), 0),
            status = CASE
                WHEN ps.scheduled_amount <= COALESCE((
                    SELECT SUM(pt.paid_amount) FROM payment_transactions pt
                    WHERE pt.schedule_id = ps.schedule_id AND pt.status = 'COMPLETED'
                ), 0) THEN 'PAID'
                WHEN COALESCE((
                    SELECT SUM(pt.paid_amount) FROM payment_transactions pt
                    WHERE pt.schedule_id = ps.schedule_id AND pt.status = 'COMPLETED'
                ), 0) > 0 THEN 'PARTIAL'
                WHEN ps.due_date < CURRENT_DATE THEN 'OVERDUE'
                ELSE 'PENDING'
            END::schedule_status,
            updated_at = now()
        WHERE ps.stg_invoice_id = $1
        """,
        stg_invoice_id,
    )


async def _reconcile_failed_payments(conn: asyncpg.Connection, tenant_id: str) -> int:
    """
    Find payment_transactions with failure status updated in the last 48 h,
    then correct the credit balance on the linked fact_bills / fact_invoices.
    Returns count of bills + invoices recalculated.
    """
    recalculated = 0

    # AP: bills paid by the org that bounced / failed
    ap_rows = await conn.fetch(
        """
        SELECT DISTINCT
            fb.bill_id, fb.stg_bill_id,
            fb.total_amount,
            pt.status      AS txn_status,
            pt.failure_reason,
            pt.reference_number
        FROM fact_bills fb
        JOIN payment_schedules ps
          ON  ps.stg_bill_id = fb.stg_bill_id
        JOIN payment_transactions pt
          ON  pt.schedule_id = ps.schedule_id
        WHERE fb.tenant_id   = $1::uuid
          AND pt.cycle_type  = 'PAYABLE'
          AND pt.status     IN ('FAILED', 'BOUNCED', 'REVERSED', 'CANCELLED')
          AND pt.updated_at  > now() - interval '48 hours'
        """,
        tenant_id,
    )
    for row in ap_rows:
        await _recalc_bill_credit(
            conn,
            str(row["bill_id"]),
            str(row["stg_bill_id"]),
            Decimal(str(row["total_amount"])),
            tenant_id,
        )
        recalculated += 1

    # AR: receivables from customers that bounced / failed
    ar_rows = await conn.fetch(
        """
        SELECT DISTINCT
            fi.invoice_id, fi.stg_invoice_id,
            fi.total_amount,
            pt.status      AS txn_status,
            pt.failure_reason,
            pt.reference_number
        FROM fact_invoices fi
        JOIN payment_schedules ps
          ON  ps.stg_invoice_id = fi.stg_invoice_id
        JOIN payment_transactions pt
          ON  pt.schedule_id = ps.schedule_id
        WHERE fi.tenant_id    = $1::uuid
          AND fi.invoice_type = 'AR'
          AND pt.cycle_type   = 'RECEIVABLE'
          AND pt.status      IN ('FAILED', 'BOUNCED', 'REVERSED', 'CANCELLED')
          AND pt.updated_at   > now() - interval '48 hours'
        """,
        tenant_id,
    )
    for row in ar_rows:
        await _recalc_invoice_credit(
            conn,
            str(row["invoice_id"]),
            str(row["stg_invoice_id"]),
            Decimal(str(row["total_amount"])),
            tenant_id,
        )
        recalculated += 1

    return recalculated


# ── 4. Write ai_anomalies ──────────────────────────────────────────────────────

async def _upsert_anomaly(
    conn: asyncpg.Connection,
    tenant_id: str,
    entity_type: str,  # 'BILL' or 'INVOICE'
    entity_id: str,
    anomaly_type: str,
    severity: str,
    description: str,
    features: dict,
) -> bool:
    """Insert anomaly row if no identical unresolved record exists. Returns True if inserted."""
    exists = await conn.fetchval(
        """
        SELECT 1 FROM ai_anomalies
        WHERE tenant_id    = $1::uuid
          AND entity_type  = $2::entity_type
          AND entity_id    = $3::uuid
          AND anomaly_type = $4
          AND is_resolved  = false
        LIMIT 1
        """,
        tenant_id, entity_type, entity_id, anomaly_type,
    )
    if exists:
        return False
    await conn.execute(
        """
        INSERT INTO ai_anomalies
            (tenant_id, entity_type, entity_id,
             anomaly_type, severity, description, features_jsonb)
        VALUES ($1::uuid, $2::entity_type, $3::uuid,
                $4, $5::severity_level, $6, $7::jsonb)
        """,
        tenant_id, entity_type, entity_id,
        anomaly_type, severity, description, json.dumps(features),
    )
    return True


async def _flag_anomalies(conn: asyncpg.Connection, tenant_id: str) -> int:
    """Write ai_anomaly rows for all newly flagged bills, invoices, and payment failures."""
    written = 0

    # ── Duplicate bills (exact) ──
    dup_bills = await conn.fetch(
        """
        SELECT stg_bill_id, bill_number, total_amount, supplier_id
        FROM stg_bills
        WHERE tenant_id     = $1::uuid
          AND validation_flag = 'DUPLICATE'
          AND updated_at    > now() - interval '2 hours'
        """,
        tenant_id,
    )
    for row in dup_bills:
        ok = await _upsert_anomaly(
            conn, tenant_id, "BILL", str(row["stg_bill_id"]),
            anomaly_type="DUPLICATE_BILL",
            severity="HIGH",
            description=(
                f"Bill {row['bill_number']} (₹{row['total_amount']}) is an exact duplicate "
                f"— blocked from promotion to fact_bills."
            ),
            features={
                "bill_number": row["bill_number"],
                "total_amount": str(row["total_amount"]),
            },
        )
        written += int(ok)

    # ── Soft-duplicate bills ──
    review_bills = await conn.fetch(
        """
        SELECT stg_bill_id, bill_number, total_amount, bill_date
        FROM stg_bills
        WHERE tenant_id     = $1::uuid
          AND validation_flag = 'REVIEW'
          AND updated_at    > now() - interval '2 hours'
        """,
        tenant_id,
    )
    for row in review_bills:
        ok = await _upsert_anomaly(
            conn, tenant_id, "BILL", str(row["stg_bill_id"]),
            anomaly_type="SOFT_DUPLICATE_BILL",
            severity="MEDIUM",
            description=(
                f"Bill {row['bill_number']} (₹{row['total_amount']} on {row['bill_date']}) "
                f"closely matches an existing bill within {_SOFT_DUP_DAYS} days — needs review."
            ),
            features={
                "bill_number": row["bill_number"],
                "total_amount": str(row["total_amount"]),
                "bill_date": str(row["bill_date"]),
            },
        )
        written += int(ok)

    # ── Failed AP payments ──
    failed_ap = await conn.fetch(
        """
        SELECT DISTINCT
            fb.stg_bill_id, fb.bill_number,
            pt.status AS txn_status, pt.failure_reason, pt.reference_number
        FROM fact_bills fb
        JOIN payment_schedules ps ON ps.stg_bill_id = fb.stg_bill_id
        JOIN payment_transactions pt ON pt.schedule_id = ps.schedule_id
        WHERE fb.tenant_id  = $1::uuid
          AND pt.cycle_type = 'PAYABLE'
          AND pt.status    IN ('FAILED', 'BOUNCED', 'REVERSED', 'CANCELLED')
          AND pt.updated_at > now() - interval '48 hours'
        """,
        tenant_id,
    )
    for row in failed_ap:
        ok = await _upsert_anomaly(
            conn, tenant_id, "BILL", str(row["stg_bill_id"]),
            anomaly_type="PAYMENT_FAILURE_AP",
            severity="HIGH",
            description=(
                f"Payment for bill {row['bill_number']} {row['txn_status']}. "
                f"Ref: {row['reference_number'] or 'N/A'}. "
                f"AP credit balance recalculated from COMPLETED transactions only."
            ),
            features={
                "txn_status": row["txn_status"],
                "failure_reason": row["failure_reason"],
                "reference_number": row["reference_number"],
            },
        )
        written += int(ok)

    # ── Duplicate invoices (exact) ──
    dup_invoices = await conn.fetch(
        """
        SELECT stg_invoice_id, invoice_number, total_amount
        FROM stg_invoices
        WHERE tenant_id     = $1::uuid
          AND validation_flag = 'DUPLICATE'
          AND updated_at    > now() - interval '2 hours'
        """,
        tenant_id,
    )
    for row in dup_invoices:
        ok = await _upsert_anomaly(
            conn, tenant_id, "INVOICE", str(row["stg_invoice_id"]),
            anomaly_type="DUPLICATE_INVOICE",
            severity="HIGH",
            description=(
                f"Invoice {row['invoice_number']} (₹{row['total_amount']}) is an exact duplicate "
                f"— blocked from promotion to fact_invoices."
            ),
            features={
                "invoice_number": row["invoice_number"],
                "total_amount": str(row["total_amount"]),
            },
        )
        written += int(ok)

    # ── Soft-duplicate invoices ──
    review_invoices = await conn.fetch(
        """
        SELECT stg_invoice_id, invoice_number, total_amount, invoice_date
        FROM stg_invoices
        WHERE tenant_id     = $1::uuid
          AND validation_flag = 'REVIEW'
          AND updated_at    > now() - interval '2 hours'
        """,
        tenant_id,
    )
    for row in review_invoices:
        ok = await _upsert_anomaly(
            conn, tenant_id, "INVOICE", str(row["stg_invoice_id"]),
            anomaly_type="SOFT_DUPLICATE_INVOICE",
            severity="MEDIUM",
            description=(
                f"Invoice {row['invoice_number']} (₹{row['total_amount']} on {row['invoice_date']}) "
                f"closely matches an existing invoice within {_SOFT_DUP_DAYS} days — needs review."
            ),
            features={
                "invoice_number": row["invoice_number"],
                "total_amount": str(row["total_amount"]),
                "invoice_date": str(row["invoice_date"]),
            },
        )
        written += int(ok)

    # ── Failed AR payments ──
    failed_ar = await conn.fetch(
        """
        SELECT DISTINCT
            fi.stg_invoice_id, fi.invoice_number,
            pt.status AS txn_status, pt.failure_reason, pt.reference_number
        FROM fact_invoices fi
        JOIN payment_schedules ps ON ps.stg_invoice_id = fi.stg_invoice_id
        JOIN payment_transactions pt ON pt.schedule_id = ps.schedule_id
        WHERE fi.tenant_id    = $1::uuid
          AND fi.invoice_type = 'AR'
          AND pt.cycle_type   = 'RECEIVABLE'
          AND pt.status      IN ('FAILED', 'BOUNCED', 'REVERSED', 'CANCELLED')
          AND pt.updated_at   > now() - interval '48 hours'
        """,
        tenant_id,
    )
    for row in failed_ar:
        ok = await _upsert_anomaly(
            conn, tenant_id, "INVOICE", str(row["stg_invoice_id"]),
            anomaly_type="PAYMENT_FAILURE_AR",
            severity="HIGH",
            description=(
                f"Receivable payment for invoice {row['invoice_number']} {row['txn_status']}. "
                f"Ref: {row['reference_number'] or 'N/A'}. "
                f"AR credit balance recalculated from COMPLETED transactions only."
            ),
            features={
                "txn_status": row["txn_status"],
                "failure_reason": row["failure_reason"],
                "reference_number": row["reference_number"],
            },
        )
        written += int(ok)

    return written


# ── 5. Vendor deduplication ─────────────────────────────────────────────────────

async def _dedup_vendors(conn: asyncpg.Connection, tenant_id: str) -> int:
    """
    Flag likely-duplicate vendor records within a tenant. Detection only —
    never merges or deletes (see module docstring for why). Returns count of
    new anomalies written.
    """
    flagged = 0

    # Strong signal: identical bank account + IFSC under two different
    # supplier_ids. The same real-world account can't belong to two
    # different vendors, so this is a duplicate almost by definition —
    # exactly what caught the Ignosi Systems case (GSTIN typo on a later
    # upload, same bank account reused).
    bank_dups = await conn.fetch(
        """
        SELECT s1.supplier_id AS dup_id, s1.name AS dup_name, s1.gstn AS dup_gstn,
               s2.supplier_id AS orig_id, s2.name AS orig_name, s2.gstn AS orig_gstn,
               s1.bank_account, s1.bank_ifsc
        FROM dim_suppliers s1
        JOIN dim_suppliers s2
          ON  s2.tenant_id     = s1.tenant_id
          AND s2.supplier_id  != s1.supplier_id
          AND s2.bank_account  = s1.bank_account
          AND s2.bank_ifsc     = s1.bank_ifsc
          AND s2.created_at    < s1.created_at
        WHERE s1.tenant_id    = $1::uuid
          AND s1.bank_account IS NOT NULL AND trim(s1.bank_account) <> ''
          AND s1.bank_ifsc    IS NOT NULL AND trim(s1.bank_ifsc)    <> ''
        """,
        tenant_id,
    )
    for row in bank_dups:
        ok = await _upsert_anomaly(
            conn, tenant_id, "SUPPLIER", str(row["dup_id"]),
            anomaly_type="DUPLICATE_VENDOR",
            severity="HIGH",
            description=(
                f"'{row['dup_name']}' (GSTIN {row['dup_gstn'] or 'none'}) shares its bank account "
                f"({row['bank_ifsc']} ••••{str(row['bank_account'])[-4:]}) with an earlier vendor "
                f"'{row['orig_name']}' (GSTIN {row['orig_gstn'] or 'none'}) — likely the same company "
                f"registered twice, possibly from a GSTIN typo on a later bill upload."
            ),
            features={
                "match_signal": "bank_account",
                "duplicate_of_supplier_id": str(row["orig_id"]),
                "bank_ifsc": row["bank_ifsc"],
            },
        )
        flagged += int(ok)

    # Weaker signal: exact same name, different GSTIN, no bank-account match
    # available yet (e.g. the duplicate hasn't had a bank account added). A
    # plain case/whitespace-insensitive equality — deliberately conservative,
    # not fuzzy matching, to avoid false positives between distinct
    # similarly-named companies.
    name_dups = await conn.fetch(
        """
        SELECT s1.supplier_id AS dup_id, s1.name AS dup_name, s1.gstn AS dup_gstn,
               s2.supplier_id AS orig_id, s2.name AS orig_name, s2.gstn AS orig_gstn
        FROM dim_suppliers s1
        JOIN dim_suppliers s2
          ON  s2.tenant_id    = s1.tenant_id
          AND s2.supplier_id != s1.supplier_id
          AND lower(trim(s2.name)) = lower(trim(s1.name))
          AND s2.created_at   < s1.created_at
          AND COALESCE(s2.gstn, '') != COALESCE(s1.gstn, '')
        WHERE s1.tenant_id = $1::uuid
          AND trim(s1.name) <> ''
        """,
        tenant_id,
    )
    for row in name_dups:
        ok = await _upsert_anomaly(
            conn, tenant_id, "SUPPLIER", str(row["dup_id"]),
            anomaly_type="DUPLICATE_VENDOR",
            severity="MEDIUM",
            description=(
                f"'{row['dup_name']}' (GSTIN {row['dup_gstn'] or 'none'}) has the exact same name as "
                f"an earlier vendor '{row['orig_name']}' (GSTIN {row['orig_gstn'] or 'none'}) but a "
                f"different GSTIN — check whether this is a duplicate before it accumulates its own bills."
            ),
            features={
                "match_signal": "exact_name",
                "duplicate_of_supplier_id": str(row["orig_id"]),
            },
        )
        flagged += int(ok)

    return flagged


# ── Worker ─────────────────────────────────────────────────────────────────────

class DeduplicationWorker(BaseWorker):
    """
    Continuous deduplication and credit-integrity guard.

    Runs after StagingPromotionWorker in the same process so that
    dedup flags are applied before staging records are promoted to fact tables.

    Poll order per tenant:
      1. Dedup stg_bills  (AP)
      2. Dedup stg_invoices  (AR)
      3. Reconcile failed payment credits
      4. Write ai_anomalies for anything flagged
      5. Dedup dim_suppliers (vendors) — flag-only, see module docstring
    """
    name           = "deduplication"
    notify_channel = "ch_deduplication"

    async def process_batch(self) -> int:
        pool = await get_pool()

        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                tenants = await conn.fetch(
                    "SELECT tenant_id FROM dim_tenants WHERE is_active = true AND tenant_id = ANY($1::uuid[])",
                    tenant_ids,
                )
            else:
                tenants = await conn.fetch(
                    "SELECT tenant_id FROM dim_tenants WHERE is_active = true"
                )

        total = 0
        for t in tenants:
            tenant_id = str(t["tenant_id"])
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        flagged_bills    = await _dedup_bills(conn, tenant_id)
                        flagged_invoices = await _dedup_invoices(conn, tenant_id)
                        reconciled       = await _reconcile_failed_payments(conn, tenant_id)
                        anomalies        = await _flag_anomalies(conn, tenant_id)
                        flagged_vendors  = await _dedup_vendors(conn, tenant_id)

                batch = flagged_bills + flagged_invoices + reconciled + anomalies + flagged_vendors
                if batch:
                    logger.info(
                        "[dedup] tenant=%s  bills_flagged=%d  invoices_flagged=%d  "
                        "payments_reconciled=%d  anomalies=%d  vendors_flagged=%d",
                        tenant_id, flagged_bills, flagged_invoices, reconciled, anomalies, flagged_vendors,
                    )
                total += batch

            except Exception as exc:
                logger.exception("[dedup] tenant=%s unhandled error", tenant_id)
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await self.log_error(
                            conn,
                            tenant_id=tenant_id,
                            stage="deduplication",
                            error_type=type(exc).__name__,
                            details={"message": str(exc)},
                        )

        return total
