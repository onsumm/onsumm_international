"""
TDS Calculation Engine — pure-logic module.
All rules come from the database. Zero hardcoded section numbers.

Entry point: calculate_tds(conn, input) -> TdsResult
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


@dataclass
class TdsInput:
    tenant_id: str
    supplier_id: str
    bill_id: str
    invoice_date: date
    taxable_amount: Decimal
    total_amount: Decimal
    gst_amount: Decimal
    is_gst_shown_separately: bool


@dataclass
class TdsResult:
    applicable: bool
    section_id: str | None = None
    return_code: str | None = None
    old_section: str | None = None
    tds_mode: str | None = None
    rate: Decimal | None = None
    base_amount: Decimal | None = None
    tds_amount: Decimal | None = None
    catchup_amount: Decimal | None = None
    net_payable: Decimal | None = None
    reason: str = ""
    rate_source: str = "DEFAULT"
    threshold_status: str = "NA"


class TdsLockError(Exception):
    pass


class TdsReplayInProgress(Exception):
    pass


def _fy_label(d: date, fy_start_month: int = 4) -> str:
    year = d.year if d.month >= fy_start_month else d.year - 1
    return f"{year}-{str(year + 1)[-2:]}"


def _fy_bounds(d: date, fy_start_month: int = 4) -> tuple[date, date]:
    """First and last day of the fiscal year containing `d`.

    The end is derived as "the day before the next year starts" rather than by
    naming month `fy_start_month - 1`: for a January fiscal year that arithmetic
    gives month 0 and raises, so a calendar-year tenant could not compute TDS at
    all.
    """
    year = d.year if d.month >= fy_start_month else d.year - 1
    start = date(year, fy_start_month, 1)
    next_start = date(year + 1, fy_start_month, 1)
    return start, next_start - timedelta(days=1)


def _month_bounds(d: date) -> tuple[date, date]:
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=1), d.replace(day=last)


def _not_applicable(inp: TdsInput, reason: str, **kw) -> TdsResult:
    return TdsResult(
        applicable=False,
        base_amount=inp.taxable_amount,
        tds_amount=_ZERO,
        net_payable=inp.total_amount,
        reason=reason,
        **kw,
    )


async def _load_prefs(conn, tenant_id: str) -> dict:
    row = await conn.fetchval(
        "SELECT settings_jsonb FROM dim_tenants WHERE tenant_id = $1",
        tenant_id,
    )
    defaults = {
        "deduct_on": "BOOKING",
        "base_default": "GST_EXCLUSIVE",
        "auto_apply": True,
        "fy_start_month": 4,
        "pan_missing_rate": 20.0,
        "max_catchup_pct": 50,
        "max_backdate_days": 90,
    }
    import json
    settings = json.loads(row) if isinstance(row, str) else (row or {})
    tds = settings.get("tds") or {}
    return {**defaults, **tds}


async def _cumulative_from_bills(conn, tenant_id, supplier_id, section_id, start, end) -> Decimal:
    val = await conn.fetchval(
        """SELECT COALESCE(SUM(subtotal), 0)
           FROM fact_bills
           WHERE tenant_id = $1 AND supplier_id = $2
             AND tds_section_id = $3::uuid
             AND bill_date BETWEEN $4 AND $5""",
        tenant_id, supplier_id, section_id, start, end,
    )
    return Decimal(str(val or 0))


async def _untaxed_prior_base(conn, tenant_id, supplier_id, section_id, start, end, this_bill_id) -> Decimal:
    """Base amount already billed this FY on which no TDS was deducted.

    This is what becomes arrears when an aggregate threshold is crossed — and it
    is NOT simply the prior cumulative. On a DUAL section (194C: ₹30k per bill
    OR ₹1L per year) an earlier bill over the per-transaction limit was already
    taxed; counting it again as arrears would charge the vendor twice for it.
    Only bills that actually escaped deduction count.
    """
    val = await conn.fetchval(
        """SELECT COALESCE(SUM(COALESCE(tds_base_amount, subtotal)), 0)
           FROM stg_bills
           WHERE tenant_id = $1 AND supplier_id = $2
             AND tds_section_id = $3::uuid
             AND bill_date BETWEEN $4 AND $5
             AND stg_bill_id <> $6::uuid
             AND COALESCE(tds_amount, 0) = 0""",
        tenant_id, supplier_id, section_id, start, end, this_bill_id,
    )
    return Decimal(str(val or 0))


async def _monthly_total(conn, tenant_id, supplier_id, section_id, d: date) -> Decimal:
    ms, me = _month_bounds(d)
    return await _cumulative_from_bills(conn, tenant_id, supplier_id, section_id, ms, me)


async def calculate_tds(conn, inp: TdsInput, skip_backdate_check: bool = False, skip_replay_check: bool = False) -> TdsResult:
    """Main entry point. conn must be inside a transaction."""
    import asyncio

    prefs = await _load_prefs(conn, inp.tenant_id)

    # PRE-CHECK: backdating guard (skipped when admin force-processes)
    if not skip_backdate_check:
        max_backdate = prefs.get("max_backdate_days", 90)
        if (date.today() - inp.invoice_date).days > max_backdate:
            return _not_applicable(inp, f"Invoice date exceeds backdate limit ({max_backdate} days)",
                                   threshold_status="REVIEW_REQUIRED")

    # 1. Load vendor details
    vendor = await conn.fetchrow(
        "SELECT vendor_category_id, pan, status FROM dim_suppliers WHERE supplier_id = $1",
        inp.supplier_id,
    )
    if not vendor:
        return _not_applicable(inp, "Vendor not found")

    # 2. Check for active vendor override
    override = await conn.fetchrow(
        """SELECT override_id, tds_section_id, tds_mode, override_rate, certificate_no,
                  effective_from
           FROM dim_vendor_tds_overrides
           WHERE tenant_id = $1 AND supplier_id = $2 AND is_active = true
             AND effective_from <= $3
             AND (lower_cert_valid_to IS NULL OR lower_cert_valid_to >= $3)
           ORDER BY created_at DESC LIMIT 1""",
        inp.tenant_id, inp.supplier_id, inp.invoice_date,
    )

    section_id = None
    tds_mode = "cumulative"
    rate_candidate = None
    rate_source = "DEFAULT"

    # 3. RESOLVE SECTION + MODE
    if override:
        section_id = str(override["tds_section_id"]) if override["tds_section_id"] else None
        tds_mode = override["tds_mode"] or "cumulative"
        if override["override_rate"] is not None:
            rate_candidate = Decimal(str(override["override_rate"]))
            rate_source = "VENDOR_OVERRIDE"

    if not section_id and vendor["vendor_category_id"]:
        rule = await conn.fetchrow(
            """SELECT mr.tds_section_id, mr.rate_override,
                      sec.return_code, sec.old_section_label
               FROM dim_tds_mapping_rules mr
               JOIN dim_tds_sections sec ON sec.tds_section_id = mr.tds_section_id AND sec.is_active = true
               WHERE mr.tenant_id = $1 AND mr.is_active = true
                 AND mr.vendor_category_id = $2
               ORDER BY mr.priority ASC LIMIT 1""",
            inp.tenant_id, vendor["vendor_category_id"],
        )
        if rule:
            section_id = str(rule["tds_section_id"])
            if rate_candidate is None and rule["rate_override"] is not None:
                rate_candidate = Decimal(str(rule["rate_override"]))
                rate_source = "MAPPING_OVERRIDE"

    if not section_id:
        return _not_applicable(inp, "No mapping rule - vendor not categorised")

    # 4. Load section
    sec = await conn.fetchrow(
        """SELECT tds_section_id, return_code, old_section_label, default_rate, pan_missing_rate,
                  threshold_type, threshold_amount, threshold_amount_2,
                  base_calculation, effective_from, effective_to, has_split_rate
           FROM dim_tds_sections
           WHERE tds_section_id = $1::uuid AND is_active = true""",
        section_id,
    )
    if not sec:
        return _not_applicable(inp, f"Section {section_id} inactive or not found")

    if sec["effective_from"] and inp.invoice_date < sec["effective_from"]:
        return _not_applicable(inp, "Section not yet effective")
    if sec["effective_to"] and inp.invoice_date > sec["effective_to"]:
        return _not_applicable(inp, "Section expired")

    return_code = sec["return_code"]
    old_section = sec["old_section_label"] or return_code

    # dim_tds_sections carries has_split_rate / rate_individual_huf /
    # rate_others, and nothing in this codebase reads them — split rates such as
    # 194C's 1% vs 2% are expressed as two vendor categories with a
    # rate_override each. Anyone who sets these columns gets no effect at all, so
    # say so rather than letting the section quietly deduct at default_rate.
    if sec.get("has_split_rate"):
        logger.warning(
            "[tds] section %s (%s) has has_split_rate set, which this engine does not "
            "implement — it will deduct at default_rate %s%%. Model the split as two "
            "vendor categories with a rate_override instead.",
            old_section, section_id, sec["default_rate"])
    ttype = sec["threshold_type"] or "NONE"
    t1 = Decimal(str(sec["threshold_amount"])) if sec["threshold_amount"] else _ZERO
    t2 = Decimal(str(sec["threshold_amount_2"])) if sec["threshold_amount_2"] else _ZERO

    # 5. Determine base amount
    #
    # A bill whose taxable_amount never got extracted (OCR missed the pre-GST
    # line) arrives here as 0 with a real total. Deducting on that gives ₹0 TDS
    # on a live bill — silently, and it looks like a legitimate below-threshold
    # result. pipelines/tds_processing.py guards this before calling in, but
    # replay_fy_state and the dedup recompute did not, so repaired bills lost
    # their deduction: ADZASIA had ₹76,038 and ₹43,478 bills land at zero TDS.
    # The guard belongs here, where every caller gets it.
    taxable = inp.taxable_amount
    if taxable <= _ZERO and inp.total_amount > _ZERO:
        taxable = inp.total_amount - inp.gst_amount if inp.gst_amount > _ZERO else inp.total_amount
        logger.warning(
            "[tds] bill %s had no taxable_amount; derived %s from total %s less GST %s",
            inp.bill_id, taxable, inp.total_amount, inp.gst_amount)

    base_calc = sec["base_calculation"] or "GST_EXCLUSIVE"
    if base_calc in ("GST_EXCLUSIVE", "TAXABLE") and inp.is_gst_shown_separately and inp.gst_amount > 0:
        base_amount = taxable
    else:
        base_amount = inp.total_amount

    # 6. Load/create FY state with lock
    fy_start_month = prefs.get("fy_start_month", 4)
    fy_label = _fy_label(inp.invoice_date, fy_start_month)
    fy_start, fy_end = _fy_bounds(inp.invoice_date, fy_start_month)

    state = None
    for attempt in range(3):
        try:
            state = await conn.fetchrow(
                """SELECT * FROM vendor_tds_fy_state
                   WHERE tenant_id = $1 AND supplier_id = $2
                     AND tds_section_id = $3::uuid AND fy = $4
                   FOR UPDATE NOWAIT""",
                inp.tenant_id, inp.supplier_id, section_id, fy_label,
            )
            break
        except Exception as e:
            if "could not obtain lock" in str(e).lower() and attempt < 2:
                await asyncio.sleep(0.2 * (2 ** attempt))
            else:
                if attempt == 2:
                    raise TdsLockError(f"Could not lock FY state for bill {inp.bill_id}")
                raise

    if state and state["replay_in_progress"] and not skip_replay_check:
        raise TdsReplayInProgress(f"Replay in progress for {inp.supplier_id}/{section_id}/{fy_label}")

    cum_billed = Decimal(str(state["cumulative_billed"])) if state else _ZERO
    threshold_crossed = state["threshold_crossed"] if state else False
    cum_tds = Decimal(str(state["cumulative_tds_deducted"])) if state else _ZERO
    catchup_pending = Decimal(str(state["catchup_pending"])) if state else _ZERO
    catchup_recovered = Decimal(str(state["catchup_recovered"])) if state else _ZERO
    catchup_decision = state["catchup_decision"] if state else None

    # 7. Branch by tds_mode
    tds_base = _ZERO
    threshold_status = "NA"
    tds_applicable = True
    exempt_reason = ""
    # Amount already billed this FY that escaped deduction because every bill
    # so far sat under the aggregate threshold. Once the threshold is crossed,
    # TDS is due on the whole year's aggregate, not just the bill that crossed
    # it — so that earlier portion becomes arrears. Set only on the crossing
    # bill; `threshold_crossed` latches, so it is booked once per FY.
    arrears_base = _ZERO

    cum_billed += base_amount

    if tds_mode == "always_deduct":
        tds_base = base_amount
        threshold_status = "NA"

    elif tds_mode == "per_bill_threshold":
        if base_amount >= t1:
            tds_base = base_amount
            threshold_status = "ABOVE_THRESHOLD"
        else:
            tds_applicable = False
            exempt_reason = f"Per-bill: amount {base_amount:,.0f} < threshold {t1:,.0f}"

    elif tds_mode == "cumulative":
        if ttype == "NONE":
            tds_base = base_amount

        elif ttype == "PER_TXN":
            if base_amount < t1:
                tds_applicable = False
                exempt_reason = f"Below per-txn threshold {t1:,.0f}"
            else:
                tds_base = base_amount

        elif ttype == "AGGREGATE_FY":
            if threshold_crossed:
                tds_base = base_amount
                threshold_status = "ABOVE_THRESHOLD"
            elif cum_billed >= t1:
                tds_base = base_amount
                threshold_status = "CROSSING"
                threshold_crossed = True
                arrears_base = await _untaxed_prior_base(
                    conn, inp.tenant_id, inp.supplier_id, section_id,
                    fy_start, fy_end, inp.bill_id)
            else:
                tds_applicable = False
                exempt_reason = f"Below FY threshold {t1:,.0f} (cumulative {cum_billed:,.0f})"

        elif ttype == "PER_MONTH":
            monthly = await _monthly_total(conn, inp.tenant_id, inp.supplier_id, section_id, inp.invoice_date)
            monthly_with = monthly + base_amount
            if monthly >= t1:
                tds_base = base_amount
                threshold_status = "ABOVE_THRESHOLD"
            elif monthly_with >= t1:
                tds_base = base_amount
                threshold_status = "CROSSING"
            else:
                tds_applicable = False
                exempt_reason = f"Below monthly threshold {t1:,.0f} (month {monthly:,.0f})"

        elif ttype == "EXCESS_OF":
            prior = cum_billed - base_amount
            taxable_portion = max(_ZERO, cum_billed - t1) - max(_ZERO, prior - t1)
            if taxable_portion <= 0:
                tds_applicable = False
                exempt_reason = f"Under excess-of threshold {t1:,.0f}"
            else:
                tds_base = taxable_portion
                threshold_status = "EXCESS_OF"

        elif ttype == "DUAL":
            # Each leg only fires if its threshold is actually configured. An
            # unset threshold_amount_2 arrives here as 0, and `cum_billed >= 0`
            # is always true — so a DUAL section missing its FY figure used to
            # deduct on every bill regardless of either threshold.
            per_txn_fires = t1 > _ZERO and base_amount >= t1
            agg_fires = threshold_crossed or (t2 > _ZERO and cum_billed >= t2)
            if per_txn_fires or agg_fires:
                tds_base = base_amount
                if not threshold_crossed and agg_fires:
                    threshold_crossed = True
                    # Only the aggregate leg creates arrears, and only on bills
                    # that escaped deduction — an earlier bill over the per-txn
                    # limit was already taxed.
                    arrears_base = await _untaxed_prior_base(
                        conn, inp.tenant_id, inp.supplier_id, section_id,
                        fy_start, fy_end, inp.bill_id)
                threshold_status = "CROSSING" if not (state and state["threshold_crossed"]) else "ABOVE_THRESHOLD"
            else:
                txn_leg = (f"txn {base_amount:,.0f}<{t1:,.0f}" if t1 > _ZERO
                           else "per-bill threshold not configured")
                agg_leg = (f"FY {cum_billed:,.0f}<{t2:,.0f}" if t2 > _ZERO
                           else "FY threshold not configured")
                exempt_reason = f"DUAL: {txn_leg} AND {agg_leg}"
                tds_applicable = False

        else:
            # An unrecognised threshold_type used to fall through every branch
            # with tds_base left at zero while tds_applicable stayed True: the
            # bill was reported as CALCULATED, carrying a rate, with ₹0 withheld.
            # Silent non-deduction is the worst outcome, so say so instead.
            tds_applicable = False
            exempt_reason = f"Unknown threshold_type {ttype!r} — section misconfigured"

    else:
        # Same failure for tds_mode: anything other than the three known modes
        # produced a zero-TDS "CALCULATED" bill rather than an error.
        tds_applicable = False
        exempt_reason = f"Unknown tds_mode {tds_mode!r} — vendor override misconfigured"

    # Not applicable — still update FY state
    if not tds_applicable:
        await _upsert_fy_state(conn, inp, section_id, fy_label, cum_billed,
                               cum_tds, threshold_crossed, catchup_pending,
                               catchup_recovered, catchup_decision)
        return TdsResult(
            applicable=False,
            section_id=section_id,
            return_code=return_code,
            old_section=old_section,
            tds_mode=tds_mode,
            base_amount=base_amount,
            tds_amount=_ZERO,
            catchup_amount=_ZERO,
            net_payable=inp.total_amount,
            reason=exempt_reason,
            rate_source=rate_source,
            threshold_status="BELOW_THRESHOLD",
        )

    # 8. Determine rate
    pan = ((vendor["pan"] or "").strip().upper()) if vendor else ""
    vendor_status = vendor["status"] if vendor else None
    # GSTIN verification = PAN verification: a verified GSTIN proves the PAN is valid
    # (GSTIN chars 3-12 encode the PAN). Skip Section 206AA 20% penalty if the vendor
    # is GSTIN-verified OR has an explicit PAN string on record.
    is_pan_available_or_verified = bool(pan) or (vendor_status == "VERIFIED")

    if not is_pan_available_or_verified:
        rate = Decimal(str(sec["pan_missing_rate"] or 20))
        rate_source = "NO_PAN_PENALTY"
    elif rate_candidate is not None:
        rate = rate_candidate
    else:
        rate = Decimal(str(sec["default_rate"]))

    # 9. Compute TDS
    tds_amount = (tds_base * rate / _HUNDRED).quantize(Decimal("1"))

    # 9b. Book arrears on the bill that crossed an aggregate threshold.
    # Deliberately recorded as catchup_pending rather than added to this bill's
    # tds_amount: recovering it changes what the vendor is actually paid, and
    # the existing catch-up flow already gates that behind an explicit
    # catchup_decision plus the max_catchup_pct cap. So this surfaces the
    # liability; step 10 recovers it once someone decides to.
    if arrears_base > _ZERO:
        catchup_pending += (arrears_base * rate / _HUNDRED).quantize(Decimal("1"))

    # 10. Catch-up recovery
    catchup_this_bill = _ZERO
    max_catchup_pct = Decimal(str(prefs.get("max_catchup_pct", 50)))
    if catchup_decision == "recover" and catchup_pending > catchup_recovered:
        remaining = catchup_pending - catchup_recovered
        max_allowed = base_amount * max_catchup_pct / _HUNDRED
        catchup_this_bill = min(remaining, max_allowed).quantize(Decimal("1"))
        catchup_recovered += catchup_this_bill

    # 11. Update FY state
    cum_tds += tds_amount
    await _upsert_fy_state(conn, inp, section_id, fy_label, cum_billed,
                           cum_tds, threshold_crossed, catchup_pending,
                           catchup_recovered, catchup_decision,
                           threshold_bill_id=inp.bill_id if threshold_status == "CROSSING" else None)

    net_payable = inp.total_amount - tds_amount - catchup_this_bill

    return TdsResult(
        applicable=True,
        section_id=section_id,
        return_code=return_code,
        old_section=old_section,
        tds_mode=tds_mode,
        rate=rate,
        base_amount=tds_base,
        tds_amount=tds_amount,
        catchup_amount=catchup_this_bill,
        net_payable=net_payable,
        reason=f"rate={rate}% base={tds_base:,.0f} mode={tds_mode}",
        rate_source=rate_source,
        threshold_status=threshold_status,
    )


async def _upsert_fy_state(conn, inp, section_id, fy_label, cum_billed,
                           cum_tds, threshold_crossed, catchup_pending,
                           catchup_recovered, catchup_decision,
                           threshold_bill_id=None):
    await conn.execute(
        """INSERT INTO vendor_tds_fy_state
               (tenant_id, supplier_id, tds_section_id, fy,
                cumulative_billed, cumulative_tds_deducted,
                threshold_crossed, threshold_crossed_on_bill_id,
                catchup_pending, catchup_recovered, catchup_decision, updated_at)
           VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, now())
           ON CONFLICT (tenant_id, supplier_id, tds_section_id, fy) DO UPDATE SET
               cumulative_billed = $5,
               cumulative_tds_deducted = $6,
               threshold_crossed = $7,
               threshold_crossed_on_bill_id = COALESCE($8, vendor_tds_fy_state.threshold_crossed_on_bill_id),
               catchup_pending = $9,
               catchup_recovered = $10,
               catchup_decision = $11,
               updated_at = now()""",
        inp.tenant_id, inp.supplier_id, section_id, fy_label,
        cum_billed, cum_tds, threshold_crossed, threshold_bill_id,
        catchup_pending, catchup_recovered, catchup_decision,
    )
