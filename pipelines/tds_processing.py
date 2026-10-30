"""TDS Processing Pipeline — Dynamic Engine
─────────────────────────────────────────────
Uses utils/tds_engine.py for all calculation. Supports tds_mode branching,
FY state tracking, and catch-up recovery.

For each stg_bill with tds_status PENDING/NULL:
  1. Build TdsInput from bill row
  2. Call calculate_tds() — handles mode, thresholds, FY state, catch-up
  3. Write results to stg_bills and back-patch fact_bills
"""
from __future__ import annotations

import logging
from decimal import Decimal

import config
from db import get_pool
from pipelines.base import BaseWorker
# Indian TDS is a rule pack (backend_ocr/app/core/jurisdiction.py): a tenant
# outside India has no TDS, so its bills are settled as NA instead of computed.
from backend_ocr.app.core.jurisdiction import pack_enabled_sql
from utils.tds_engine import TdsInput, TdsResult, TdsLockError, TdsReplayInProgress, calculate_tds

logger = logging.getLogger(__name__)


class TdsProcessingWorker(BaseWorker):
    name           = "tds_processing"
    notify_channel = "ch_tds_processing"

    async def process_batch(self) -> int:
        pool = await get_pool()

        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            await self._settle_not_applicable(conn, tenant_ids)
            if tenant_ids:
                rows = await conn.fetch(
                    f"""
                    SELECT sb.stg_bill_id, sb.tenant_id, sb.supplier_id,
                           sb.bill_date, sb.subtotal, sb.tax_amount, sb.total_amount
                    FROM stg_bills sb
                    WHERE (sb.tds_status = 'PENDING' OR sb.tds_status IS NULL)
                      AND {pack_enabled_sql("tds_india", "sb.tenant_id")}
                      AND sb.tenant_id = ANY($2::uuid[])
                    ORDER BY sb.created_at
                    LIMIT $1
                    FOR UPDATE OF sb SKIP LOCKED
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT sb.stg_bill_id, sb.tenant_id, sb.supplier_id,
                           sb.bill_date, sb.subtotal, sb.tax_amount, sb.total_amount
                    FROM stg_bills sb
                    WHERE (sb.tds_status = 'PENDING' OR sb.tds_status IS NULL)
                      AND {pack_enabled_sql("tds_india", "sb.tenant_id")}
                    ORDER BY sb.created_at
                    LIMIT $1
                    FOR UPDATE OF sb SKIP LOCKED
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        processed = 0
        for row in rows:
            try:
                await self._process_one(pool, row)
                processed += 1
            except TdsReplayInProgress:
                logger.info("[tds_processing] bill=%s replay in progress — requeue", row["stg_bill_id"])
            except TdsLockError:
                logger.warning("[tds_processing] bill=%s lock contention — requeue", row["stg_bill_id"])
            except Exception as exc:
                logger.exception("[tds_processing] bill=%s failed: %s", row["stg_bill_id"], exc)
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE stg_bills SET tds_status='ERROR' WHERE stg_bill_id=$1",
                        row["stg_bill_id"],
                    )

        if processed:
            logger.info("[tds_processing] processed %d bills", processed)
        return processed

    async def _process_one(self, pool, row) -> None:
        total_amt = Decimal(str(row["total_amount"] or 0))
        subtotal  = Decimal(str(row["subtotal"] or 0))
        tax_amt   = Decimal(str(row["tax_amount"] or 0))
        if subtotal == 0 and total_amt > 0:
            subtotal = total_amt - tax_amt if tax_amt > 0 else total_amt

        inp = TdsInput(
            tenant_id=str(row["tenant_id"]),
            supplier_id=str(row["supplier_id"]),
            bill_id=str(row["stg_bill_id"]),
            invoice_date=row["bill_date"],
            taxable_amount=subtotal,
            total_amount=total_amt,
            gst_amount=tax_amt,
            is_gst_shown_separately=tax_amt > 0,
        )

        async with pool.acquire() as conn:
            async with conn.transaction():
                # The SELECT in process_batch took `FOR UPDATE ... SKIP LOCKED`
                # on its own connection, which was released before we got here —
                # so that lock guards nothing by the time the work runs, and a
                # second worker or replica can pick the same bill up. Re-take the
                # row here, inside the transaction that actually writes, and bail
                # if someone else already moved it off PENDING. Without this the
                # bill's amount is added to cumulative_billed twice, which is the
                # drift scripts/fix_tds_cumulative_state.py exists to repair.
                # fetchrow, not fetchval: SKIP LOCKED returning nothing and a row
                # whose tds_status is NULL both give None from fetchval, and those
                # mean opposite things — "someone else holds it, back off" versus
                # "never processed, go ahead".
                claimed = await conn.fetchrow(
                    """SELECT tds_status FROM stg_bills
                       WHERE stg_bill_id = $1 FOR UPDATE SKIP LOCKED""",
                    row["stg_bill_id"],
                )
                if claimed is None:
                    logger.info("[tds_processing] bill=%s locked by another worker — skipping",
                                row["stg_bill_id"])
                    return
                if claimed["tds_status"] not in (None, "PENDING"):
                    logger.info("[tds_processing] bill=%s already %s — skipping",
                                row["stg_bill_id"], claimed["tds_status"])
                    return

                # Skip backdate check if vendor has category assigned (admin explicitly configured)
                vendor_cat = await conn.fetchval(
                    "SELECT vendor_category_id FROM dim_suppliers WHERE supplier_id = $1",
                    str(row["supplier_id"]),
                )
                skip_backdate = vendor_cat is not None
                result = await calculate_tds(conn, inp, skip_backdate_check=skip_backdate)

                section_code = result.old_section or result.return_code
                status = "CALCULATED" if result.applicable else "NA"
                if result.threshold_status == "REVIEW_REQUIRED":
                    status = "REVIEW_REQUIRED"

                await self._write(
                    conn, row,
                    section_id=result.section_id,
                    section_code=section_code,
                    rate=result.rate,
                    tds_base=result.base_amount or subtotal,
                    tds_amount=result.tds_amount or Decimal(0),
                    net_payable=result.net_payable or total_amt,
                    status=status,
                    reason=result.reason,
                )

        logger.info(
            "[tds_processing] bill=%s %s section=%s mode=%s tds=%s",
            row["stg_bill_id"], status, section_code,
            result.tds_mode or "-", result.tds_amount or 0,
        )

    async def _settle_not_applicable(self, conn, tenant_ids) -> None:
        """Mark pending bills of tenants without Indian TDS as NA: no deduction,
        net payable = total. Without this they would stay PENDING forever and be
        re-read every batch. Indian tenants are never touched (the predicate is
        true for them)."""
        tenant_clause = "AND sb.tenant_id = ANY($1::uuid[])" if tenant_ids else ""
        args = (tenant_ids,) if tenant_ids else ()
        settled = await conn.fetch(
            f"""UPDATE stg_bills sb SET
                    tds_status = 'NA', tds_amount = 0, tds_rate = 0, tds_rate_percent = 0,
                    tds_base_amount = 0, net_payable = sb.total_amount
                WHERE (sb.tds_status = 'PENDING' OR sb.tds_status IS NULL)
                  AND NOT {pack_enabled_sql("tds_india", "sb.tenant_id")}
                  {tenant_clause}
                RETURNING sb.stg_bill_id""",
            *args,
        )
        if settled:
            await conn.execute(
                """UPDATE fact_bills SET tds_status = 'NA', tds_amount = 0, tds_rate = 0, tds_rate_percent = 0,
                       tds_base_amount = 0, net_payable = total_amount
                   WHERE stg_bill_id = ANY($1::uuid[])""",
                [r["stg_bill_id"] for r in settled],
            )
            logger.info("[tds_processing] %d bill(s) of tenants without Indian TDS set to NA", len(settled))

    async def _write(
        self, conn, row, *,
        section_id, section_code, rate, tds_base,
        tds_amount, net_payable, status, reason,
    ) -> None:
        await conn.execute(
            """UPDATE stg_bills SET
                   tds_section_id   = $2::uuid,
                   tds_section_code = $3,
                   tds_rate         = $4,
                   tds_rate_percent = $4,
                   tds_base_amount  = $5,
                   tds_amount       = $6,
                   net_payable      = $7,
                   tds_status       = $8
               WHERE stg_bill_id = $1""",
            row["stg_bill_id"], section_id, section_code,
            rate, tds_base, tds_amount, net_payable, status,
        )
        await conn.execute(
            """UPDATE fact_bills SET
                   tds_section_id   = $2::uuid,
                   tds_section_code = $3,
                   tds_rate         = $4,
                   tds_rate_percent = $4,
                   tds_base_amount  = $5,
                   tds_amount       = $6,
                   net_payable      = $7,
                   tds_status       = $8
               WHERE stg_bill_id = $1""",
            row["stg_bill_id"], section_id, section_code,
            rate, tds_base, tds_amount, net_payable, status,
        )
        await self.log_audit(
            conn,
            tenant_id  = str(row["tenant_id"]),
            table_name = "stg_bills",
            entity_id  = row["stg_bill_id"],
            action     = "UPDATE",
            new_values = {
                "tds_status":       status,
                "tds_amount":       float(tds_amount),
                "tds_rate":         float(rate or 0),
                "tds_section_code": section_code,
                "reason":           reason,
            },
        )
