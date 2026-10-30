"""
TDS Catch-Up Computation — called when admin changes vendor tds_mode mid-year.

compute_catchup(): calculates TDS owed on prior bills not yet recovered.
replay_fy_state(): recalculates all FY state from scratch after rule changes.
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


async def compute_catchup(
    conn,
    tenant_id: str,
    supplier_id: str,
    tds_section_id: str,
    new_mode: str,
    effective_from: date,
    fy_start: date,
    rate: Decimal,
    threshold: Decimal,
) -> Decimal:
    """Compute catch-up TDS pending from prior bills in the current FY.

    Returns catchup_pending amount. Updates vendor_tds_fy_state.
    """
    prior_bills = await conn.fetch(
        """SELECT stg_bill_id, tds_base_amount, tds_amount
           FROM stg_bills
           WHERE tenant_id = $1 AND supplier_id = $2
             AND tds_section_id = $3::uuid
             AND bill_date >= $4 AND bill_date < $5
             AND tds_status != 'REVIEW_REQUIRED'
           ORDER BY bill_date, created_at""",
        tenant_id, supplier_id, tds_section_id, fy_start, effective_from,
    )

    if not prior_bills:
        return _ZERO

    catchup_pending = _ZERO

    if new_mode == "always_deduct":
        theoretical_tds = _ZERO
        already_deducted = _ZERO
        for bill in prior_bills:
            base = Decimal(str(bill["tds_base_amount"] or 0))
            theoretical_tds += (base * rate / _HUNDRED).quantize(Decimal("1"))
            already_deducted += Decimal(str(bill["tds_amount"] or 0))
        catchup_pending = max(_ZERO, theoretical_tds - already_deducted)

    elif new_mode == "per_bill_threshold":
        theoretical_tds = _ZERO
        already_deducted = _ZERO
        for bill in prior_bills:
            base = Decimal(str(bill["tds_base_amount"] or 0))
            if base >= threshold:
                theoretical_tds += (base * rate / _HUNDRED).quantize(Decimal("1"))
            already_deducted += Decimal(str(bill["tds_amount"] or 0))
        catchup_pending = max(_ZERO, theoretical_tds - already_deducted)

    elif new_mode == "cumulative":
        cum = _ZERO
        for bill in prior_bills:
            cum += Decimal(str(bill["tds_base_amount"] or 0))
        await conn.execute(
            """UPDATE vendor_tds_fy_state SET
                   cumulative_billed = $5, updated_at = now()
               WHERE tenant_id = $1 AND supplier_id = $2
                 AND tds_section_id = $3::uuid AND fy = $4""",
            tenant_id, supplier_id, tds_section_id,
            _fy_label(fy_start), cum,
        )
        return _ZERO

    if catchup_pending > 0:
        fy = _fy_label(fy_start)
        await conn.execute(
            """UPDATE vendor_tds_fy_state SET
                   catchup_pending = $5,
                   catchup_decision = 'pending',
                   updated_at = now()
               WHERE tenant_id = $1 AND supplier_id = $2
                 AND tds_section_id = $3::uuid AND fy = $4""",
            tenant_id, supplier_id, tds_section_id, fy, catchup_pending,
        )
        logger.info(
            "[tds_catchup] supplier=%s section=%s catchup_pending=%.0f (%d prior bills)",
            supplier_id, tds_section_id, catchup_pending, len(prior_bills),
        )

    return catchup_pending


async def replay_fy_state(
    conn,
    tenant_id: str,
    supplier_id: str,
    tds_section_id: str,
    fy: str,
) -> int:
    """Recalculate all FY state from scratch.

    Locks the state row (replay_in_progress=true), resets counters, re-runs
    calculate_tds for every bill in date order, then unlocks.

    Returns number of bills re-processed.
    """
    from utils.tds_engine import calculate_tds, TdsInput

    state = await conn.fetchrow(
        """SELECT * FROM vendor_tds_fy_state
           WHERE tenant_id = $1 AND supplier_id = $2
             AND tds_section_id = $3::uuid AND fy = $4
           FOR UPDATE""",
        tenant_id, supplier_id, tds_section_id, fy,
    )

    if not state:
        return 0

    await conn.execute(
        """UPDATE vendor_tds_fy_state SET
               replay_in_progress = true, updated_at = now()
           WHERE state_id = $1""",
        state["state_id"],
    )

    # catchup_pending is reset too, because the engine *adds* to it when a
    # threshold is crossed. Leaving it would make every replay stack another
    # copy of the same arrears on top — the same accumulate-instead-of-replace
    # mistake this whole script exists to repair.
    # catchup_recovered is deliberately NOT reset: it records catch-up money
    # already withheld from the vendor, which a recalculation does not undo.
    await conn.execute(
        """UPDATE vendor_tds_fy_state SET
               cumulative_billed = 0,
               cumulative_tds_deducted = 0,
               threshold_crossed = false,
               threshold_crossed_on_bill_id = NULL,
               catchup_pending = 0,
               updated_at = now()
           WHERE state_id = $1""",
        state["state_id"],
    )

    fy_year = int(fy.split("-")[0])
    fy_start = date(fy_year, 4, 1)
    fy_end = date(fy_year + 1, 3, 31)

    bills = await conn.fetch(
        """SELECT stg_bill_id, bill_date, subtotal, tax_amount, total_amount
           FROM stg_bills
           WHERE tenant_id = $1 AND supplier_id = $2
             AND tds_section_id = $3::uuid
             AND bill_date BETWEEN $4 AND $5
           ORDER BY bill_date ASC, created_at ASC""",
        tenant_id, supplier_id, tds_section_id, fy_start, fy_end,
    )

    count = 0
    for bill in bills:
        subtotal = Decimal(str(bill["subtotal"] or 0))
        tax_amt = Decimal(str(bill["tax_amount"] or 0))
        total = Decimal(str(bill["total_amount"] or 0))

        inp = TdsInput(
            tenant_id=tenant_id,
            supplier_id=supplier_id,
            bill_id=str(bill["stg_bill_id"]),
            invoice_date=bill["bill_date"],
            taxable_amount=subtotal,
            total_amount=total,
            gst_amount=tax_amt,
            is_gst_shown_separately=tax_amt > 0,
        )

        result = await calculate_tds(conn, inp, skip_backdate_check=True, skip_replay_check=True)

        # tds_rate_percent, not just tds_rate: the bills API serves
        # tds_rate_percent as the `tds_rate` the UI renders
        # (routers/bills.py), so writing only tds_rate left the screen showing
        # the pre-replay rate beside a freshly recalculated TDS amount.
        # pipelines/tds_processing.py writes both — this must match it, and
        # tds_section_code with it.
        section_code = result.old_section or result.return_code
        status = "CALCULATED" if result.applicable else "NA"
        for table in ("stg_bills", "fact_bills"):
            await conn.execute(
                f"""UPDATE {table} SET
                       tds_amount = $2, tds_rate = $3, tds_rate_percent = $3,
                       tds_base_amount = $4, tds_status = $5, net_payable = $6,
                       tds_section_code = $7
                   WHERE stg_bill_id = $1""",
                bill["stg_bill_id"],
                result.tds_amount or _ZERO,
                result.rate,
                result.base_amount,
                status,
                result.net_payable,
                section_code,
            )
        count += 1

    await conn.execute(
        """UPDATE vendor_tds_fy_state SET
               replay_in_progress = false, updated_at = now()
           WHERE state_id = $1""",
        state["state_id"],
    )

    logger.info("[tds_replay] replayed %d bills for %s/%s/%s", count, supplier_id, tds_section_id, fy)
    return count


def _fy_label(d: date) -> str:
    year = d.year if d.month >= 4 else d.year - 1
    return f"{year}-{str(year + 1)[-2:]}"
