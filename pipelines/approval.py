"""
Approval Pipeline
─────────────────
Automates the workflow layer on top of invoices and payment schedules:

  1. Mark overdue payment_schedules (due_date < today, status PENDING/PARTIAL).
  2. Create wf_tasks (NUDGE) for overdue AR invoices that don't yet have one.
  3. Create wf_tasks (APPROVE) for large payment_transactions that need sign-off.
  4. Auto-approve payment_transactions below the configured threshold.
  5. Generate wf_nudges for pending NUDGE tasks (one nudge per task per channel).
  6. Escalate NUDGE tasks that remain unresolved after their due_at timestamp.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import asyncpg

import config
from db import get_pool
from models.enums import (
    NudgeChannel, NudgeStatus, ScheduleStatus,
    TxnStatus, WfEntityType, WfTaskStatus, WfTaskType,
)
from pipelines.base import BaseWorker
_audit = BaseWorker.log_audit

logger = logging.getLogger(__name__)

# Days after due_date before a task is escalated
ESCALATION_THRESHOLD_DAYS = 7
# Default nudge channel
DEFAULT_NUDGE_CHANNEL = NudgeChannel.EMAIL


# ── Payment schedule overdue sweep ────────────────────────────────────────────

async def _mark_overdue_schedules(conn: asyncpg.Connection) -> int:
    """
    Set payment_schedules.status = OVERDUE where due_date < today
    and status is still PENDING or PARTIAL.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        result = await conn.execute(
            """
            UPDATE payment_schedules
            SET status     = 'OVERDUE',
                updated_at = now()
            WHERE due_date  < CURRENT_DATE
              AND status IN ('PENDING', 'PARTIAL')
              AND tenant_id = ANY($1::uuid[])
            """,
            tenant_ids,
        )
    else:
        result = await conn.execute(
            """
            UPDATE payment_schedules
            SET status     = 'OVERDUE',
                updated_at = now()
            WHERE due_date  < CURRENT_DATE
              AND status IN ('PENDING', 'PARTIAL')
            """,
        )
    count = int(result.split()[-1])
    if count:
        logger.info("[approval] marked %d schedules OVERDUE", count)
    return count


# ── wf_task creation ──────────────────────────────────────────────────────────

async def _ensure_wf_task(
    conn: asyncpg.Connection,
    tenant_id: str,
    task_type: WfTaskType,
    entity_type: WfEntityType,
    entity_id: str,
    due_at: datetime | None = None,
    priority: int = 3,
    metadata: dict | None = None,
) -> str | None:
    """
    Create a wf_task only if no active (non-DONE, non-SKIPPED) task exists
    for this entity.  Returns the task_id, or None if skipped.
    """
    import json
    existing = await conn.fetchval(
        """
        SELECT task_id FROM wf_tasks
        WHERE tenant_id   = $1
          AND task_type   = $2
          AND entity_type = $3
          AND entity_id   = $4
          AND status NOT IN ('DONE', 'SKIPPED')
        LIMIT 1
        """,
        tenant_id, task_type, entity_type, str(entity_id),
    )
    if existing:
        return None  # already has an active task

    task_id = await conn.fetchval(
        """
        INSERT INTO wf_tasks
            (tenant_id, task_type, entity_type, entity_id,
             status, priority, due_at, metadata_jsonb)
        VALUES ($1,$2,$3,$4,'PENDING',$5,$6,$7)
        RETURNING task_id
        """,
        tenant_id, task_type, entity_type, str(entity_id),
        priority, due_at, json.dumps(metadata or {}),
    )
    logger.debug(
        "[approval] created wf_task type=%s entity=%s/%s",
        task_type, entity_type, entity_id,
    )
    return str(task_id)


# ── NUDGE task creation for overdue invoices ──────────────────────────────────

async def _create_nudge_tasks_for_overdue_invoices(
    conn: asyncpg.Connection,
    batch_size: int,
) -> int:
    """
    Find AR invoices that are OVERDUE (or past due_date) without a pending NUDGE
    task and create one.
    """
    today = date.today()

    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.tenant_id, fi.end_customer_id,
                   fi.invoice_number, fi.due_date, fi.outstanding_amount,
                   fi.status
            FROM fact_invoices fi
            WHERE fi.invoice_type = 'AR'
              AND fi.status IN ('SENT', 'PARTIAL', 'OVERDUE')
              AND (fi.due_date < $1 OR fi.status = 'OVERDUE')
              AND fi.outstanding_amount > 0
              AND fi.tenant_id = ANY($3::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = fi.tenant_id
                    AND wt.entity_type = 'INVOICE'
                    AND wt.entity_id   = fi.invoice_id
                    AND wt.task_type   = 'NUDGE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            today, batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.tenant_id, fi.end_customer_id,
                   fi.invoice_number, fi.due_date, fi.outstanding_amount,
                   fi.status
            FROM fact_invoices fi
            WHERE fi.invoice_type = 'AR'
              AND fi.status IN ('SENT', 'PARTIAL', 'OVERDUE')
              AND (fi.due_date < $1 OR fi.status = 'OVERDUE')
              AND fi.outstanding_amount > 0
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = fi.tenant_id
                    AND wt.entity_type = 'INVOICE'
                    AND wt.entity_id   = fi.invoice_id
                    AND wt.task_type   = 'NUDGE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            today, batch_size,
        )

    created = 0
    for row in rows:
        due_at = datetime.now(timezone.utc) + timedelta(hours=2)
        task_id = await _ensure_wf_task(
            conn,
            tenant_id=str(row["tenant_id"]),
            task_type=WfTaskType.NUDGE,
            entity_type=WfEntityType.INVOICE,
            entity_id=str(row["invoice_id"]),
            due_at=due_at,
            priority=2,
            metadata={
                "invoice_number":   row["invoice_number"],
                "outstanding":      float(row["outstanding_amount"] or 0),
                "due_date":         str(row["due_date"]),
                "end_customer_id":  str(row["end_customer_id"]),
            },
        )
        if task_id:
            # Immediately update fact_invoices status to OVERDUE if not already
            await conn.execute(
                """
                UPDATE fact_invoices SET status='OVERDUE', updated_at=now()
                WHERE invoice_id=$1 AND status NOT IN ('PAID','VOID','OVERDUE')
                """,
                row["invoice_id"],
            )
            await _audit(conn, tenant_id=str(row["tenant_id"]),
                         table_name="fact_invoices", entity_id=row["invoice_id"],
                         action="UPDATE",
                         old_values={"status": row["status"]},
                         new_values={"status": "OVERDUE",
                                     "invoice_number": row["invoice_number"]})
            await _audit(conn, tenant_id=str(row["tenant_id"]),
                         table_name="wf_tasks", entity_id=task_id,
                         action="INSERT",
                         new_values={"task_type": "NUDGE", "entity_id": str(row["invoice_id"])})
            created += 1

    return created


# ── APPROVE task creation for newly promoted AR invoices ─────────────────────

async def _create_approve_tasks_for_new_ar_invoices(
    conn: asyncpg.Connection,
    batch_size: int,
) -> int:
    """
    Find AR invoices promoted to fact_invoices within the last 48 hours that
    don't yet have an APPROVE wf_task, and create one for internal review.
    Covers OCR uploads, API sync, and Tally pull — any source that lands in
    fact_invoices with invoice_type='AR'.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.tenant_id, fi.invoice_number,
                   fi.status, fi.total_amount, fi.outstanding_amount
            FROM fact_invoices fi
            WHERE fi.invoice_type = 'AR'
              AND fi.status NOT IN ('PAID', 'VOID')
              AND fi.created_at > NOW() - INTERVAL '48 hours'
              AND fi.tenant_id = ANY($2::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = fi.tenant_id
                    AND wt.entity_type = 'INVOICE'
                    AND wt.entity_id   = fi.invoice_id
                    AND wt.task_type   = 'APPROVE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            ORDER BY fi.created_at DESC
            LIMIT $1
            """,
            batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.tenant_id, fi.invoice_number,
                   fi.status, fi.total_amount, fi.outstanding_amount
            FROM fact_invoices fi
            WHERE fi.invoice_type = 'AR'
              AND fi.status NOT IN ('PAID', 'VOID')
              AND fi.created_at > NOW() - INTERVAL '48 hours'
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = fi.tenant_id
                    AND wt.entity_type = 'INVOICE'
                    AND wt.entity_id   = fi.invoice_id
                    AND wt.task_type   = 'APPROVE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            ORDER BY fi.created_at DESC
            LIMIT $1
            """,
            batch_size,
        )

    created = 0
    for row in rows:
        due_at = datetime.now(timezone.utc) + timedelta(hours=48)
        task_id = await _ensure_wf_task(
            conn,
            tenant_id=str(row["tenant_id"]),
            task_type=WfTaskType.APPROVE,
            entity_type=WfEntityType.INVOICE,
            entity_id=str(row["invoice_id"]),
            due_at=due_at,
            priority=2,
            metadata={
                "invoice_number": row["invoice_number"],
                "total_amount":   float(row["total_amount"] or 0),
                "status":         row["status"],
                "invoice_type":   "AR",
            },
        )
        if task_id:
            await _audit(conn, tenant_id=str(row["tenant_id"]),
                         table_name="wf_tasks", entity_id=task_id,
                         action="INSERT",
                         new_values={"task_type": "APPROVE",
                                     "entity_id": str(row["invoice_id"]),
                                     "invoice_number": row["invoice_number"]})
            created += 1
            logger.info(
                "[approval] created APPROVE task for AR invoice=%s", row["invoice_number"]
            )

    return created


# ── APPROVE task creation for large payment transactions ──────────────────────

async def _create_approve_tasks_for_large_payments(
    conn: asyncpg.Connection,
    batch_size: int,
    default_threshold: float,
) -> int:
    """
    Find payment_transactions with status=INITIATED, paid_amount above the
    per-tenant threshold (falls back to default_threshold), and no pending
    APPROVE wf_task.  Create an APPROVE task for each.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT pt.txn_id, pt.tenant_id, pt.schedule_id,
                   pt.paid_amount, pt.payment_mode, pt.cycle_type
            FROM payment_transactions pt
            LEFT JOIN dim_tenants dt ON dt.tenant_id = pt.tenant_id
            WHERE pt.status      = 'INITIATED'
              AND pt.tenant_id   = ANY($3::uuid[])
              -- The threshold is in the tenant's books currency (core/jurisdiction.py):
              -- 'auto_approve_threshold' first, the older INR-named key for back-compat.
              AND pt.paid_amount > COALESCE(
                      COALESCE(dt.settings_jsonb->>'auto_approve_threshold', dt.settings_jsonb->>'auto_approve_threshold_inr')::numeric,
                      $1
                  )
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = pt.tenant_id
                    AND wt.entity_type = 'PAYMENT'
                    AND wt.entity_id   = pt.txn_id
                    AND wt.task_type   = 'APPROVE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            default_threshold, batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT pt.txn_id, pt.tenant_id, pt.schedule_id,
                   pt.paid_amount, pt.payment_mode, pt.cycle_type
            FROM payment_transactions pt
            LEFT JOIN dim_tenants dt ON dt.tenant_id = pt.tenant_id
            WHERE pt.status      = 'INITIATED'
              AND pt.paid_amount > COALESCE(
                      COALESCE(dt.settings_jsonb->>'auto_approve_threshold', dt.settings_jsonb->>'auto_approve_threshold_inr')::numeric,
                      $1
                  )
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks wt
                  WHERE wt.tenant_id   = pt.tenant_id
                    AND wt.entity_type = 'PAYMENT'
                    AND wt.entity_id   = pt.txn_id
                    AND wt.task_type   = 'APPROVE'
                    AND wt.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            default_threshold, batch_size,
        )

    created = 0
    for row in rows:
        import json
        due_at = datetime.now(timezone.utc) + timedelta(hours=24)
        task_id = await _ensure_wf_task(
            conn,
            tenant_id=str(row["tenant_id"]),
            task_type=WfTaskType.APPROVE,
            entity_type=WfEntityType.PAYMENT,
            entity_id=str(row["txn_id"]),
            due_at=due_at,
            priority=1,
            metadata={
                "paid_amount":  float(row["paid_amount"] or 0),
                "payment_mode": row["payment_mode"],
                "cycle_type":   row["cycle_type"],
                "schedule_id":  str(row["schedule_id"]),
            },
        )
        if task_id:
            created += 1

    return created


# ── Auto-approve small payment transactions ───────────────────────────────────

async def _auto_approve_small_payments(
    conn: asyncpg.Connection,
    batch_size: int,
    default_threshold: float,
) -> int:
    """
    Move payment_transactions with paid_amount <= per-tenant threshold and
    status=INITIATED directly to PROCESSING, then write wf_audit_log entries.
    Per-tenant threshold comes from dim_tenants.settings_jsonb; falls back to
    default_threshold when the tenant row is absent or the key is unset.
    auto_approve_enabled=false in settings_jsonb skips the tenant entirely.
    """
    import json

    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT pt.txn_id, pt.tenant_id, pt.paid_amount
            FROM payment_transactions pt
            LEFT JOIN dim_tenants dt ON dt.tenant_id = pt.tenant_id
            WHERE pt.status      = 'INITIATED'
              AND pt.tenant_id   = ANY($3::uuid[])
              AND COALESCE((dt.settings_jsonb->>'auto_approve_enabled')::boolean, true) = true
              AND pt.paid_amount <= COALESCE(
                      COALESCE(dt.settings_jsonb->>'auto_approve_threshold', dt.settings_jsonb->>'auto_approve_threshold_inr')::numeric,
                      $1
                  )
            ORDER BY pt.created_at
            LIMIT $2
            FOR UPDATE OF pt SKIP LOCKED
            """,
            default_threshold, batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT pt.txn_id, pt.tenant_id, pt.paid_amount
            FROM payment_transactions pt
            LEFT JOIN dim_tenants dt ON dt.tenant_id = pt.tenant_id
            WHERE pt.status      = 'INITIATED'
              AND COALESCE((dt.settings_jsonb->>'auto_approve_enabled')::boolean, true) = true
              AND pt.paid_amount <= COALESCE(
                      COALESCE(dt.settings_jsonb->>'auto_approve_threshold', dt.settings_jsonb->>'auto_approve_threshold_inr')::numeric,
                      $1
                  )
            ORDER BY pt.created_at
            LIMIT $2
            FOR UPDATE OF pt SKIP LOCKED
            """,
            default_threshold, batch_size,
        )

    if not rows:
        return 0

    txn_ids = [row["txn_id"] for row in rows]
    await conn.execute(
        """
        UPDATE payment_transactions
        SET status      = 'PROCESSING',
            approved_at = now(),
            updated_at  = now()
        WHERE txn_id = ANY($1)
        """,
        txn_ids,
    )

    # Write audit log for every auto-approved transaction
    for row in rows:
        await conn.execute(
            """
            INSERT INTO wf_audit_log
                (tenant_id, table_name, entity_id, user_id, action,
                 old_values_jsonb, new_values_jsonb)
            VALUES ($1, 'payment_transactions', $2::uuid, NULL, 'UPDATE',
                    jsonb_build_object('status', 'INITIATED'),
                    jsonb_build_object('status', 'PROCESSING', 'paid_amount', $3::numeric,
                                       'approved_by', 'system'))
            """,
            str(row["tenant_id"]), str(row["txn_id"]), float(row["paid_amount"] or 0),
        )

    count = len(rows)
    logger.info("[approval] auto-approved %d payments", count)
    return count


# ── Nudge generation from pending NUDGE tasks ─────────────────────────────────

async def _generate_nudges(
    conn: asyncpg.Connection,
    batch_size: int,
) -> int:
    """
    For each PENDING NUDGE task that doesn't yet have a wf_nudge, create one.
    Uses the customer's primary contact for targeting.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT wt.task_id, wt.tenant_id, wt.entity_id, wt.metadata_jsonb
            FROM wf_tasks wt
            WHERE wt.task_type  = 'NUDGE'
              AND wt.status     = 'PENDING'
              AND wt.tenant_id  = ANY($2::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM wf_nudges wn
                  WHERE wn.task_id = wt.task_id
                    AND wn.status IN ('PENDING', 'SENT')
              )
            LIMIT $1
            """,
            batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT wt.task_id, wt.tenant_id, wt.entity_id, wt.metadata_jsonb
            FROM wf_tasks wt
            WHERE wt.task_type = 'NUDGE'
              AND wt.status    = 'PENDING'
              AND NOT EXISTS (
                  SELECT 1 FROM wf_nudges wn
                  WHERE wn.task_id = wt.task_id
                    AND wn.status IN ('PENDING', 'SENT')
              )
            LIMIT $1
            """,
            batch_size,
        )

    created = 0
    for row in rows:
        task_id   = str(row["task_id"])
        tenant_id = str(row["tenant_id"])
        meta      = row["metadata_jsonb"] or {}
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)

        cust_id     = meta.get("end_customer_id")
        invoice_id  = str(row["entity_id"])  # entity is invoice

        # Resolve stg_invoice_id for linking
        stg_invoice_id = await conn.fetchval(
            "SELECT stg_invoice_id FROM fact_invoices WHERE invoice_id=$1",
            invoice_id,
        )

        # Resolve primary contact
        contact_row = await conn.fetchrow(
            """
            SELECT contact_id, email, phone, full_name
            FROM dim_end_customer_contacts
            WHERE end_customer_id = $1 AND is_primary = true AND is_active = true
            LIMIT 1
            """,
            cust_id,
        ) if cust_id else None

        import json
        content = {
            "invoice_number":   meta.get("invoice_number", ""),
            "outstanding":      meta.get("outstanding", 0),
            "due_date":         meta.get("due_date", ""),
            "recipient_name":   contact_row["full_name"] if contact_row else "",
            "recipient_email":  contact_row["email"]     if contact_row else "",
        }

        await conn.execute(
            """
            INSERT INTO wf_nudges (
                tenant_id, task_id, end_customer_id, stg_invoice_id,
                contact_id, channel, content_jsonb, status
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,'PENDING')
            """,
            tenant_id, task_id, cust_id, stg_invoice_id,
            str(contact_row["contact_id"]) if contact_row else None,
            DEFAULT_NUDGE_CHANNEL,
            json.dumps(content),
        )

        # Move task to IN_PROGRESS
        await conn.execute(
            "UPDATE wf_tasks SET status='IN_PROGRESS' WHERE task_id=$1",
            task_id,
        )
        created += 1

    return created


# ── Escalation of stale NUDGE tasks ───────────────────────────────────────────

async def _escalate_stale_tasks(conn: asyncpg.Connection, batch_size: int) -> int:
    """
    Find NUDGE tasks that are still IN_PROGRESS past their due_at and no payment
    has arrived, then create an ESCALATE task for them.
    """
    import json
    cutoff = datetime.now(timezone.utc) - timedelta(days=ESCALATION_THRESHOLD_DAYS)

    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT wt.task_id, wt.tenant_id, wt.entity_id, wt.metadata_jsonb
            FROM wf_tasks wt
            WHERE wt.task_type  = 'NUDGE'
              AND wt.status     = 'IN_PROGRESS'
              AND wt.due_at     < $1
              AND wt.tenant_id  = ANY($3::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks esc
                  WHERE esc.tenant_id   = wt.tenant_id
                    AND esc.task_type   = 'ESCALATE'
                    AND esc.entity_id   = wt.entity_id
                    AND esc.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            cutoff, batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT wt.task_id, wt.tenant_id, wt.entity_id, wt.metadata_jsonb
            FROM wf_tasks wt
            WHERE wt.task_type = 'NUDGE'
              AND wt.status    = 'IN_PROGRESS'
              AND wt.due_at    < $1
              AND NOT EXISTS (
                  SELECT 1 FROM wf_tasks esc
                  WHERE esc.tenant_id   = wt.tenant_id
                    AND esc.task_type   = 'ESCALATE'
                    AND esc.entity_id   = wt.entity_id
                    AND esc.status NOT IN ('DONE', 'SKIPPED')
              )
            LIMIT $2
            """,
            cutoff, batch_size,
        )

    escalated = 0
    for row in rows:
        task_id   = str(row["task_id"])
        tenant_id = str(row["tenant_id"])
        entity_id = str(row["entity_id"])
        meta      = row["metadata_jsonb"] or {}
        if isinstance(meta, str):
            meta = json.loads(meta)

        esc_task_id = await _ensure_wf_task(
            conn,
            tenant_id=tenant_id,
            task_type=WfTaskType.ESCALATE,
            entity_type=WfEntityType.INVOICE,
            entity_id=entity_id,
            due_at=datetime.now(timezone.utc) + timedelta(hours=4),
            priority=1,
            metadata={**meta, "original_task_id": task_id},
        )
        if esc_task_id:
            await conn.execute(
                """
                INSERT INTO wf_escalations (tenant_id, task_id, reason)
                VALUES ($1,$2,$3)
                """,
                tenant_id, esc_task_id,
                f"No payment after {ESCALATION_THRESHOLD_DAYS} days past due",
            )
            await _audit(conn, tenant_id=tenant_id,
                         table_name="wf_escalations", entity_id=esc_task_id,
                         action="INSERT",
                         new_values={"task_type": "ESCALATE", "entity_id": entity_id,
                                     "reason": f"No payment after {ESCALATION_THRESHOLD_DAYS} days"})
            escalated += 1

    return escalated


# ── Vendor outreach follow-up reminders ──────────────────────────────────────

async def _queue_vendor_outreach_reminders(
    conn: asyncpg.Connection,
    batch_size: int,
) -> int:
    """
    Find PENDING vendor_outreach_log rows where sent_at + N days < now with no
    subsequent follow-up already queued.  N comes from
    dim_tenants.settings_jsonb->>'vendor_followup_days' (default 3).

    Inserts a new PENDING row per overdue outreach so the backend's
    POST /api/vendors/{id}/request-info can pick it up on the next send cycle.
    The new row is stamped requested_by='system_reminder' to distinguish it
    from manually triggered outreach and to prevent the NOT EXISTS guard from
    double-queuing on consecutive ApprovalWorker cycles.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT vol.id, vol.supplier_id, vol.tenant_id,
                   vol.requested_fields, vol.email_to,
                   vol.subject, vol.body, vol.sent_at,
                   COALESCE(
                       (dt.settings_jsonb->>'vendor_followup_days')::integer,
                       3
                   ) AS followup_days
            FROM   vendor_outreach_log vol
            LEFT JOIN dim_tenants dt ON dt.tenant_id = vol.tenant_id
            WHERE  vol.status   = 'PENDING'
              AND  vol.tenant_id = ANY($2::uuid[])
              AND  vol.sent_at  IS NOT NULL
              AND  vol.sent_at  + COALESCE(
                       (dt.settings_jsonb->>'vendor_followup_days')::integer, 3
                   ) * INTERVAL '1 day' < NOW()
              AND  NOT EXISTS (
                   SELECT 1 FROM vendor_outreach_log f
                   WHERE  f.supplier_id   = vol.supplier_id
                     AND  f.tenant_id     = vol.tenant_id
                     AND  f.requested_by  = 'system_reminder'
                     AND  f.requested_at  > vol.sent_at
                     AND  f.status        = 'PENDING'
              )
            ORDER BY vol.sent_at
            LIMIT $1
            """,
            batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT vol.id, vol.supplier_id, vol.tenant_id,
                   vol.requested_fields, vol.email_to,
                   vol.subject, vol.body, vol.sent_at,
                   COALESCE(
                       (dt.settings_jsonb->>'vendor_followup_days')::integer,
                       3
                   ) AS followup_days
            FROM   vendor_outreach_log vol
            LEFT JOIN dim_tenants dt ON dt.tenant_id = vol.tenant_id
            WHERE  vol.status   = 'PENDING'
              AND  vol.sent_at  IS NOT NULL
              AND  vol.sent_at  + COALESCE(
                       (dt.settings_jsonb->>'vendor_followup_days')::integer, 3
                   ) * INTERVAL '1 day' < NOW()
              AND  NOT EXISTS (
                   SELECT 1 FROM vendor_outreach_log f
                   WHERE  f.supplier_id   = vol.supplier_id
                     AND  f.tenant_id     = vol.tenant_id
                     AND  f.requested_by  = 'system_reminder'
                     AND  f.requested_at  > vol.sent_at
                     AND  f.status        = 'PENDING'
              )
            ORDER BY vol.sent_at
            LIMIT $1
            """,
            batch_size,
        )

    queued = 0
    for row in rows:
        await conn.execute(
            """
            INSERT INTO vendor_outreach_log
                (supplier_id, tenant_id, requested_fields,
                 requested_by, requested_at,
                 email_to, subject, body, status)
            VALUES ($1, $2, $3,
                    'system_reminder', now(),
                    $4, $5, $6, 'PENDING')
            """,
            row["supplier_id"], row["tenant_id"], row["requested_fields"],
            row["email_to"], row["subject"], row["body"],
        )
        await _audit(
            conn,
            tenant_id=str(row["tenant_id"]),
            table_name="vendor_outreach_log",
            entity_id=str(row["id"]),
            action="INSERT",
            new_values={
                "reason":               "auto_reminder",
                "original_outreach_id": str(row["id"]),
                "followup_days":        row["followup_days"],
                "supplier_id":          str(row["supplier_id"]),
            },
        )
        logger.info(
            "[approval] queued vendor follow-up supplier=%s original_outreach=%s followup_days=%d",
            row["supplier_id"], row["id"], row["followup_days"],
        )
        queued += 1

    if queued:
        logger.info("[approval] queued %d vendor outreach follow-up(s)", queued)
    return queued


# ── Audit bill_approval_chain activity ───────────────────────────────────────

async def _audit_bill_approval_chain(
    conn: asyncpg.Connection,
    batch_size: int,
) -> int:
    """
    Poll bill_approval_chain for rows that don't yet have a wf_audit_log entry
    (matched by new_values_jsonb->>'chain_id').  Write one audit INSERT per
    unaudited chain row so every submit/approve/send_back action is captured.
    """
    tenant_ids = config.PIPELINE_TENANT_IDS
    if tenant_ids:
        rows = await conn.fetch(
            """
            SELECT bac.id, bac.invoice_id, bac.tenant_id,
                   bac.step, bac.actor_role, bac.actor_name,
                   bac.action, bac.comment, bac.created_at,
                   fi.status AS current_status
            FROM bill_approval_chain bac
            LEFT JOIN fact_invoices fi
                   ON fi.invoice_id = bac.invoice_id
            WHERE bac.tenant_id = ANY($2::uuid[])
              AND NOT EXISTS (
                SELECT 1 FROM wf_audit_log wal
                WHERE wal.table_name = 'bill_approval_chain'
                  AND (wal.new_values_jsonb->>'chain_id')::bigint = bac.id
            )
            ORDER BY bac.created_at
            LIMIT $1
            """,
            batch_size, tenant_ids,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT bac.id, bac.invoice_id, bac.tenant_id,
                   bac.step, bac.actor_role, bac.actor_name,
                   bac.action, bac.comment, bac.created_at,
                   fi.status AS current_status
            FROM bill_approval_chain bac
            LEFT JOIN fact_invoices fi
                   ON fi.invoice_id = bac.invoice_id
            WHERE NOT EXISTS (
                SELECT 1 FROM wf_audit_log wal
                WHERE wal.table_name = 'bill_approval_chain'
                  AND (wal.new_values_jsonb->>'chain_id')::bigint = bac.id
            )
            ORDER BY bac.created_at
            LIMIT $1
            """,
            batch_size,
        )

    written = 0
    for row in rows:
        await _audit(
            conn,
            tenant_id=str(row["tenant_id"]),
            table_name="bill_approval_chain",
            entity_id=str(row["invoice_id"]),
            action="INSERT",
            new_values={
                "chain_id":      row["id"],
                "step":          row["step"],
                "action":        row["action"],
                "actor_role":    row["actor_role"],
                "actor_name":    row["actor_name"],
                "comment":       row["comment"],
                "current_status": row["current_status"],
            },
        )
        written += 1
        logger.info(
            "[approval] audited bill_approval_chain id=%d invoice=%s action=%s actor=%s",
            row["id"], str(row["invoice_id"])[:8], row["action"], row["actor_name"],
        )

    return written


# ── Worker ─────────────────────────────────────────────────────────────────────

class ApprovalWorker(BaseWorker):
    name          = "approval"
    skip_backfill = True  # time-driven; no pending-row drain needed

    async def process_batch(self) -> int:
        pool  = await get_pool()
        total = 0

        async with pool.acquire() as conn:
            async with conn.transaction():
                total += await _mark_overdue_schedules(conn)

                total += await _create_nudge_tasks_for_overdue_invoices(
                    conn, config.APPROVAL_BATCH
                )

                total += await _create_approve_tasks_for_new_ar_invoices(
                    conn, config.APPROVAL_BATCH
                )

                total += await _create_approve_tasks_for_large_payments(
                    conn, config.APPROVAL_BATCH,
                    default_threshold=config.AUTO_APPROVE_THRESHOLD,
                )

                total += await _auto_approve_small_payments(
                    conn, config.APPROVAL_BATCH,
                    default_threshold=config.AUTO_APPROVE_THRESHOLD,
                )

                total += await _generate_nudges(conn, config.APPROVAL_BATCH)

                total += await _escalate_stale_tasks(conn, config.APPROVAL_BATCH)

                total += await _queue_vendor_outreach_reminders(conn, config.APPROVAL_BATCH)

                total += await _audit_bill_approval_chain(conn, config.APPROVAL_BATCH)

        if total:
            logger.info("[approval] batch done: %d actions", total)
        return total
