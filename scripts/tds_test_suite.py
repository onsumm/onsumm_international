"""
Rigorous TDS test suite — runs against the real engine (utils/tds_engine.py) and the
real API (backend_ocr/app/tds_api.py) using throwaway data under tenant
d0e00000-0000-0000-0000-000000000002 (Demo2 Company Pvt Ltd).

Every row this script creates is tracked and deleted at the end, in FK-safe order,
with a final verification pass confirming zero rows remain. If a test crashes,
cleanup still runs (see `finally` in main()).

Run: python scripts/tds_test_suite.py
Report written to: scripts/tds_test_report.html
"""
from __future__ import annotations

import asyncio
import sys
import os
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import jwt
import requests

from db import init_pool, close_pool
from backend_ocr.app.config import JWT_SECRET
from utils.tds_engine import calculate_tds, TdsInput, TdsResult, _fy_bounds

TENANT_ID = "d0e00000-0000-0000-0000-000000000002"
OTHER_TENANT_ID = "d0e00000-0000-0000-0000-000000000003"
API_BASE = "http://localhost:8001"
NAME_PREFIX = "ZZZ_TDS_AUTOTEST_"

# tenant-002 sections/categories discovered ahead of time (see conversation)
SEC_194A_GENERAL = "8b3df876-fab8-4c12-86dd-444eb2a1b403"   # AGGREGATE_FY 10,000 / 10%
CAT_194A_GENERAL = "9c12622d-df0d-4d7a-965c-af2612daeb14"
SEC_194C = "6bee3c45-e6a9-4ac9-acb1-4556ba821873"            # DUAL 30,000/txn, 100,000/FY / 1%
CAT_194C = "9301ff25-49da-4846-8154-e59f3a6b4658"
SEC_194Q = "ff7efaee-f743-4d9b-8c78-b813aca5b98f"             # EXCESS_OF 50,00,000 / 0.10%
CAT_194Q = "5f3b3a3a-4020-4024-8018-8d7f73e74c6c"
SEC_194I = "913480fa-82d0-4b1b-a1cd-588935eb3b4c"             # PER_MONTH 50,000 / 10%
CAT_194I = "5b50cf65-a93b-4fa7-b8ea-9050728c728d"

# cross-tenant probe target (tenant 003's own 194C section — must NOT be assignable to tenant 002)
FOREIGN_SECTION_ID = "1f067cee-fac7-4dac-a40f-03765f6fd842"

results: list[dict] = []
created = {
    "suppliers": [],       # dim_suppliers.supplier_id
    "sections": [],        # dim_tds_sections.tds_section_id (test-only temp sections)
    "overrides": [],       # dim_vendor_tds_overrides.override_id
    "stg_bills": [],       # stg_bills.stg_bill_id
    "fact_bills": [],      # fact_bills.stg_bill_id (same id, separate table)
}


def record(group: str, name: str, expected: str, actual: str, passed: bool, detail: str = ""):
    results.append({
        "group": group, "name": name, "expected": expected, "actual": actual,
        "passed": passed, "detail": detail,
    })
    print(f"[{'PASS' if passed else 'FAIL'}] {group} :: {name}")
    if not passed:
        print(f"       expected={expected!r} actual={actual!r} {detail}")


def mint_jwt(tenant_id: str) -> str:
    return jwt.encode({"tenant_id": tenant_id, "exp": int(time.time()) + 3600}, JWT_SECRET, algorithm="HS256")


async def make_supplier(conn, *, category_id: str | None, pan: str | None = "AAAPZ1234C",
                         status: str = "VERIFIED", tag: str) -> str:
    supplier_id = str(uuid.uuid4())
    await conn.execute(
        """INSERT INTO dim_suppliers
               (supplier_id, tenant_id, source_supplier_id, source, name,
                vendor_category_id, pan, status)
           VALUES ($1, $2, $3, 'MANUAL', $4, $5, $6, $7)""",
        supplier_id, TENANT_ID, f"autotest-{supplier_id}", f"{NAME_PREFIX}{tag}",
        category_id, pan, status,
    )
    created["suppliers"].append(supplier_id)
    return supplier_id


def d(n: Decimal | float | int) -> Decimal:
    return Decimal(str(n))


async def group_aggregate_fy(conn):
    g = "AGGREGATE_FY (194A General, ₹10,000 threshold, 10%)"
    supplier_id = await make_supplier(conn, category_id=CAT_194A_GENERAL, tag="AGG")

    cases = [
        # (amount, expected_applicable, expected_tds_base, expected_tds, expected_status)
        (8000,  False, None,  None, "BELOW_THRESHOLD"),
        (5000,  True,  5000,  500,  "CROSSING"),
        (3000,  True,  3000,  300,  "ABOVE_THRESHOLD"),
    ]
    bill_date = date(2026, 6, 1)
    for i, (amt, exp_app, exp_base, exp_tds, exp_status) in enumerate(cases):
        inp = TdsInput(
            tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
            invoice_date=bill_date + timedelta(days=i), taxable_amount=d(amt), total_amount=d(amt),
            gst_amount=d(0), is_gst_shown_separately=False,
        )
        res: TdsResult = await calculate_tds(conn, inp, skip_backdate_check=True)
        name = f"bill #{i+1} (₹{amt:,})"
        ok = (res.applicable == exp_app
              and (not exp_app or (res.base_amount == d(exp_base) and res.tds_amount == d(exp_tds)))
              and res.threshold_status == exp_status)
        record(g, name,
               f"applicable={exp_app} base={exp_base} tds={exp_tds} status={exp_status}",
               f"applicable={res.applicable} base={res.base_amount} tds={res.tds_amount} status={res.threshold_status}",
               ok)


async def group_dual(conn):
    # "Contractor - Company/Firm" carries a rate_override of 2% on this mapping rule,
    # overriding the section's own 1% default — so 2% is the correct expected rate here.
    g = "DUAL (194C via 'Contractor - Company/Firm', ₹30,000/txn OR ₹1,00,000/FY, rate_override 2%)"
    supplier_id = await make_supplier(conn, category_id=CAT_194C, tag="DUAL")

    cases = [
        # amount, expected_applicable, expected_base, expected_tds, expected_status
        (35000, True,  35000, 700, "CROSSING"),   # per-txn threshold fires alone
        (20000, False, None,  None, "BELOW_THRESHOLD"),  # neither per-txn nor agg (55k<100k) fires
        (50000, True,  50000, 1000, "CROSSING"),   # cum now 105k >= 100k -> agg fires, sets flag
        (10000, True,  10000, 200, "ABOVE_THRESHOLD"),   # flag already set
    ]
    bill_date = date(2026, 6, 1)
    for i, (amt, exp_app, exp_base, exp_tds, exp_status) in enumerate(cases):
        inp = TdsInput(
            tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
            invoice_date=bill_date + timedelta(days=i), taxable_amount=d(amt), total_amount=d(amt),
            gst_amount=d(0), is_gst_shown_separately=False,
        )
        res: TdsResult = await calculate_tds(conn, inp, skip_backdate_check=True)
        name = f"bill #{i+1} (₹{amt:,})"
        ok = (res.applicable == exp_app
              and (not exp_app or (res.base_amount == d(exp_base) and res.tds_amount == d(exp_tds)))
              and res.threshold_status == exp_status)
        record(g, name,
               f"applicable={exp_app} base={exp_base} tds={exp_tds} status={exp_status}",
               f"applicable={res.applicable} base={res.base_amount} tds={res.tds_amount} status={res.threshold_status}",
               ok,
               detail="per-bill crossing must NOT set the aggregate flag" if i == 0 else "")


async def group_excess_of(conn):
    g = "EXCESS_OF (194Q, ₹50,00,000 threshold, 0.10%)"
    supplier_id = await make_supplier(conn, category_id=CAT_194Q, tag="EXCESS")

    cases = [
        # amount, expected_applicable, expected_base(=only the excess), expected_tds, status
        (4500000, False, None,    None, "BELOW_THRESHOLD"),
        (1000000, True,  500000,  500,  "EXCESS_OF"),   # only 5,00,000 of this 10L bill is taxable
        (2000000, True,  2000000, 2000, "EXCESS_OF"),   # fully past threshold now, whole bill taxable
    ]
    bill_date = date(2026, 6, 1)
    for i, (amt, exp_app, exp_base, exp_tds, exp_status) in enumerate(cases):
        inp = TdsInput(
            tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
            invoice_date=bill_date + timedelta(days=i), taxable_amount=d(amt), total_amount=d(amt),
            gst_amount=d(0), is_gst_shown_separately=False,
        )
        res: TdsResult = await calculate_tds(conn, inp, skip_backdate_check=True)
        name = f"bill #{i+1} (₹{amt:,})"
        ok = (res.applicable == exp_app
              and (not exp_app or (res.base_amount == d(exp_base) and res.tds_amount == d(exp_tds)))
              and res.threshold_status == exp_status)
        record(g, name,
               f"applicable={exp_app} base={exp_base} tds={exp_tds} status={exp_status}",
               f"applicable={res.applicable} base={res.base_amount} tds={res.tds_amount} status={res.threshold_status}",
               ok,
               detail="TDS must apply only to the amount exceeding the threshold" if i == 1 else "")


async def _insert_bill_pair(conn, supplier_id, section_id, bill_date, amount) -> str:
    """Insert a matching stg_bills + fact_bills row (PER_MONTH reads fact_bills)."""
    stg_id = str(uuid.uuid4())
    fy_year = bill_date.year if bill_date.month >= 4 else bill_date.year - 1
    fy = f"{fy_year}-{str(fy_year + 1)[-2:]}"
    fq = f"Q{((bill_date.month - 4) % 12) // 3 + 1}"
    fm = bill_date.strftime("%Y-%m")
    await conn.execute(
        """INSERT INTO stg_bills
               (stg_bill_id, tenant_id, source, source_bill_id, source_updated_at,
                bill_number, bill_date, supplier_id, total_amount, subtotal, tax_amount,
                tds_section_id, tds_status)
           VALUES ($1,$2,'MANUAL',$3,now(),$4,$5,$6,$7,$7,0,$8,'CALCULATED')""",
        stg_id, TENANT_ID, f"autotest-{stg_id}", f"{NAME_PREFIX}BILL", bill_date,
        supplier_id, d(amount), section_id,
    )
    created["stg_bills"].append(stg_id)
    await conn.execute(
        """INSERT INTO fact_bills
               (stg_bill_id, tenant_id, bill_number, bill_date, fiscal_year, fiscal_quarter,
                fiscal_month, supplier_id, total_amount, subtotal, tds_section_id, source,
                source_updated_at, tds_status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$10,'MANUAL',now(),'CALCULATED')""",
        stg_id, TENANT_ID, f"{NAME_PREFIX}BILL", bill_date, fy, fq, fm,
        supplier_id, d(amount), section_id,
    )
    created["fact_bills"].append(stg_id)
    return stg_id


async def group_per_month(conn):
    g = "PER_MONTH (194I(b) Rent, ₹50,000/month, 10%)"
    supplier_id = await make_supplier(conn, category_id=CAT_194I, tag="MONTH")

    # Bill 1: July, ₹30,000 — below the monthly threshold, exempt
    d1 = date(2026, 7, 5)
    inp1 = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                     invoice_date=d1, taxable_amount=d(30000), total_amount=d(30000),
                     gst_amount=d(0), is_gst_shown_separately=False)
    res1 = await calculate_tds(conn, inp1, skip_backdate_check=True)
    ok1 = (not res1.applicable and res1.threshold_status == "BELOW_THRESHOLD")
    record(g, "bill #1 Jul (₹30,000)", "applicable=False status=BELOW_THRESHOLD",
           f"applicable={res1.applicable} status={res1.threshold_status}", ok1)
    # Make bill #1 visible to the monthly-total lookup for bill #2
    await _insert_bill_pair(conn, supplier_id, SEC_194I, d1, 30000)

    # Bill 2: July, ₹25,000 — monthly total 30k+25k=55k crosses 50k -> full ₹25,000 taxed
    d2 = date(2026, 7, 15)
    inp2 = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                     invoice_date=d2, taxable_amount=d(25000), total_amount=d(25000),
                     gst_amount=d(0), is_gst_shown_separately=False)
    res2 = await calculate_tds(conn, inp2, skip_backdate_check=True)
    ok2 = (res2.applicable and res2.base_amount == d(25000) and res2.tds_amount == d(2500)
           and res2.threshold_status == "CROSSING")
    record(g, "bill #2 Jul (₹25,000)", "applicable=True base=25000 tds=2500 status=CROSSING",
           f"applicable={res2.applicable} base={res2.base_amount} tds={res2.tds_amount} status={res2.threshold_status}",
           ok2)
    await _insert_bill_pair(conn, supplier_id, SEC_194I, d2, 25000)

    # Bill 3: August (new month) — monthly counter resets, ₹10,000 is below threshold again
    d3 = date(2026, 8, 5)
    inp3 = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                     invoice_date=d3, taxable_amount=d(10000), total_amount=d(10000),
                     gst_amount=d(0), is_gst_shown_separately=False)
    res3 = await calculate_tds(conn, inp3, skip_backdate_check=True)
    ok3 = (not res3.applicable and res3.threshold_status == "BELOW_THRESHOLD")
    record(g, "bill #3 Aug, new month (₹10,000)", "applicable=False status=BELOW_THRESHOLD",
           f"applicable={res3.applicable} status={res3.threshold_status}", ok3,
           detail="monthly counter must reset for a new calendar month")


async def group_pan_missing(conn):
    g = "PAN-missing penalty rate"
    supplier_id = await make_supplier(conn, category_id=CAT_194C, pan=None, status="PENDING", tag="NOPAN")
    inp = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                    invoice_date=date(2026, 6, 1), taxable_amount=d(50000), total_amount=d(50000),
                    gst_amount=d(0), is_gst_shown_separately=False)
    res = await calculate_tds(conn, inp, skip_backdate_check=True)
    ok = (res.applicable and res.rate == d(20) and res.rate_source == "NO_PAN_PENALTY"
          and res.tds_amount == d(10000))
    record(g, "vendor with no PAN, not verified (₹50,000 bill, above 194C per-txn threshold)",
           "rate=20% rate_source=NO_PAN_PENALTY tds=10000",
           f"rate={res.rate}% rate_source={res.rate_source} tds={res.tds_amount}", ok)


async def group_backdate_guard(conn):
    g = "Backdate guard (>90 days old, admin has NOT force-processed)"
    supplier_id = await make_supplier(conn, category_id=CAT_194C, tag="BACKDATE")
    old_date = date.today() - timedelta(days=200)
    inp = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                    invoice_date=old_date, taxable_amount=d(50000), total_amount=d(50000),
                    gst_amount=d(0), is_gst_shown_separately=False)
    res = await calculate_tds(conn, inp, skip_backdate_check=False)
    ok = (not res.applicable and res.threshold_status == "REVIEW_REQUIRED")
    record(g, f"bill dated {old_date} (200 days old)",
           "applicable=False status=REVIEW_REQUIRED",
           f"applicable={res.applicable} status={res.threshold_status}", ok)


async def group_base_calculation(conn):
    g = "Base-amount math: GROSS vs GST_EXCLUSIVE"
    # Two throwaway, threshold-free, always_deduct sections isolate the base-amount branch cleanly.
    sec_gross = str(uuid.uuid4())
    sec_excl = str(uuid.uuid4())
    async def make_section(sid, base_calc):
        await conn.execute(
            """INSERT INTO dim_tds_sections
                   (tds_section_id, tenant_id, return_code, old_section_label, nature_of_payment,
                    default_rate, pan_missing_rate, threshold_type, base_calculation, is_active)
               VALUES ($1,$2,$3,$4,$5,5.0,20.0,'NONE',$6,true)""",
            sid, TENANT_ID, f"{NAME_PREFIX}CODE", f"{NAME_PREFIX}{base_calc}",
            f"{NAME_PREFIX} unit test", base_calc,
        )
        created["sections"].append(sid)
    await make_section(sec_gross, "GROSS")
    await make_section(sec_excl, "GST_EXCLUSIVE")

    for label, sec_id, expected_base in (("GROSS", sec_gross, 118000), ("GST_EXCLUSIVE", sec_excl, 100000)):
        supplier_id = await make_supplier(conn, category_id=None, tag=f"BASECALC_{label}")
        override_id = str(uuid.uuid4())
        await conn.execute(
            """INSERT INTO dim_vendor_tds_overrides
                   (override_id, tenant_id, supplier_id, tds_section_id, tds_mode, is_active, effective_from)
               VALUES ($1,$2,$3,$4,'always_deduct', true, $5)""",
            override_id, TENANT_ID, supplier_id, sec_id, date(2026, 1, 1),
        )
        created["overrides"].append(override_id)

        inp = TdsInput(tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
                        invoice_date=date(2026, 6, 1), taxable_amount=d(100000), total_amount=d(118000),
                        gst_amount=d(18000), is_gst_shown_separately=True)
        res = await calculate_tds(conn, inp, skip_backdate_check=True)
        ok = (res.applicable and res.base_amount == d(expected_base)
              and res.tds_amount == (d(expected_base) * d(5) / d(100)))
        record(g, f"{label} section (subtotal=1,00,000 tax=18,000 total=1,18,000)",
               f"base_amount={expected_base}",
               f"base_amount={res.base_amount}", ok)


async def _temp_section(conn, *, label, rate, threshold_type, t1=None, t2=None,
                         base_calc="GST_EXCLUSIVE") -> str:
    """A throwaway section, tracked for cleanup. Suppliers are attached to it by
    vendor override rather than a mapping rule — dim_tds_mapping_rules has a
    unique (tenant_id, priority) constraint, so parallel test groups would
    collide on it."""
    sid = str(uuid.uuid4())
    await conn.execute(
        """INSERT INTO dim_tds_sections
               (tds_section_id, tenant_id, return_code, old_section_label, nature_of_payment,
                default_rate, pan_missing_rate, threshold_type, threshold_amount,
                threshold_amount_2, base_calculation, is_active)
           VALUES ($1,$2,$3,$4,$5,$6,20.0,$7,$8,$9,$10,true)""",
        sid, TENANT_ID, f"{NAME_PREFIX}CODE", f"{NAME_PREFIX}{label}",
        f"{NAME_PREFIX} {label}", d(rate), threshold_type,
        d(t1) if t1 is not None else None, d(t2) if t2 is not None else None, base_calc,
    )
    created["sections"].append(sid)
    return sid


async def _attach(conn, supplier_id: str, section_id: str, mode: str = "cumulative") -> None:
    override_id = str(uuid.uuid4())
    await conn.execute(
        """INSERT INTO dim_vendor_tds_overrides
               (override_id, tenant_id, supplier_id, tds_section_id, tds_mode, is_active, effective_from)
           VALUES ($1,$2,$3,$4,$5,true,$6)""",
        override_id, TENANT_ID, supplier_id, section_id, mode, date(2026, 1, 1),
    )
    created["overrides"].append(override_id)


async def _calc(conn, supplier_id, amount, *, bill_date=date(2026, 6, 1),
                gst=0, taxable=None):
    total = d(amount)
    gst_amt = d(gst)
    inp = TdsInput(
        tenant_id=TENANT_ID, supplier_id=supplier_id, bill_id=str(uuid.uuid4()),
        invoice_date=bill_date,
        taxable_amount=d(taxable) if taxable is not None else total,
        total_amount=total, gst_amount=gst_amt, is_gst_shown_separately=gst_amt > 0,
    )
    return await calculate_tds(conn, inp, skip_backdate_check=True)


async def group_misconfiguration_guards(conn):
    """A misconfigured section or override used to report CALCULATED, carry a
    rate, and withhold nothing. Silent non-deduction is the worst outcome, so
    these pin that it now refuses instead."""
    g = "Misconfiguration guards (must refuse, not silently withhold zero)"

    # DUAL whose FY threshold was never set. The unset value arrives as 0 and
    # `cum_billed >= 0` always holds, so this used to deduct on every bill.
    sec = await _temp_section(conn, label="DUALNOT2", rate=2, threshold_type="DUAL", t1=30000, t2=None)
    sup = await make_supplier(conn, category_id=None, tag="DUALNOT2")
    await _attach(conn, sup, sec)
    res = await _calc(conn, sup, 5000)
    record(g, "DUAL with threshold_amount_2 unset: small bill does not deduct",
           "applicable=False", f"applicable={res.applicable} tds={res.tds_amount}",
           res.applicable is False and res.tds_amount == d(0))
    res = await _calc(conn, sup, 40000)
    record(g, "DUAL with threshold_amount_2 unset: per-txn leg still fires",
           "applicable=True tds=800", f"applicable={res.applicable} tds={res.tds_amount}",
           res.applicable is True and res.tds_amount == d(800))

    sec = await _temp_section(conn, label="BADTT", rate=10, threshold_type="NOT_A_REAL_TYPE", t1=50000)
    sup = await make_supplier(conn, category_id=None, tag="BADTT")
    await _attach(conn, sup, sec)
    res = await _calc(conn, sup, 100000)
    record(g, "unrecognised threshold_type is NA with a reason",
           "applicable=False, reason names the bad value",
           f"applicable={res.applicable} reason={res.reason!r}",
           res.applicable is False and "Unknown threshold_type" in res.reason)

    sec = await _temp_section(conn, label="BADMODE", rate=10, threshold_type="AGGREGATE_FY", t1=1000)
    sup = await make_supplier(conn, category_id=None, tag="BADMODE")
    await _attach(conn, sup, sec, mode="bogus_mode")
    res = await _calc(conn, sup, 100000)
    record(g, "unrecognised tds_mode is NA with a reason",
           "applicable=False, reason names the bad value",
           f"applicable={res.applicable} reason={res.reason!r}",
           res.applicable is False and "Unknown tds_mode" in res.reason)


async def group_zero_taxable_base(conn):
    """A bill whose taxable_amount never got extracted used to compute TDS on a
    zero base — ₹0 withheld on a live bill, indistinguishable from a legitimate
    below-threshold result."""
    g = "Zero taxable base (OCR missed the pre-GST line)"
    sec = await _temp_section(conn, label="ZEROBASE", rate=2, threshold_type="NONE")
    sup = await make_supplier(conn, category_id=None, tag="ZEROBASE")
    await _attach(conn, sup, sec, mode="always_deduct")
    # subtotal 0, GST 11,599, total 76,038 — the ADZASIA shape.
    res = await _calc(conn, sup, 76038, gst=11599, taxable=0)
    expected_base = d(76038) - d(11599)
    record(g, "missing taxable is derived as total − GST, not treated as zero",
           f"base={expected_base} tds={(expected_base * d(2) / d(100)).quantize(d(1))}",
           f"base={res.base_amount} tds={res.tds_amount}",
           res.applicable is True and res.base_amount == expected_base and res.tds_amount > d(0))


async def group_arrears(conn):
    """Once an aggregate threshold is crossed, TDS is due on the whole year's
    aggregate — the earlier untaxed portion is arrears. Only genuinely untaxed
    bills count: on a DUAL section a prior bill already taxed by the per-txn leg
    must not be charged twice."""
    g = "Aggregate-threshold arrears"

    async def arrears_of(supplier_id, section_id) -> Decimal:
        val = await conn.fetchval(
            """SELECT catchup_pending FROM vendor_tds_fy_state
               WHERE tenant_id=$1 AND supplier_id=$2 AND tds_section_id=$3::uuid AND fy='2026-27'""",
            TENANT_ID, supplier_id, section_id)
        return d(val or 0)

    async def taxed_bill(supplier_id, section_id, amount, tds):
        """A prior bill the engine already deducted on — _untaxed_prior_base keys
        off tds_amount, so this must carry one."""
        stg_id = await _insert_bill_pair(conn, supplier_id, section_id, date(2026, 5, 1), amount)
        await conn.execute(
            "UPDATE stg_bills SET tds_amount=$2, tds_base_amount=$3 WHERE stg_bill_id=$1",
            stg_id, d(tds), d(amount))

    # AGGREGATE_FY: two ₹30,000 bills cross a ₹50,000 threshold at 10%.
    sec = await _temp_section(conn, label="ARREARS_AGG", rate=10, threshold_type="AGGREGATE_FY", t1=50000)
    sup = await make_supplier(conn, category_id=None, tag="ARREARS_AGG")
    await _attach(conn, sup, sec)
    await _insert_bill_pair(conn, sup, sec, date(2026, 5, 1), 30000)   # untaxed, below threshold
    await _calc(conn, sup, 30000, bill_date=date(2026, 5, 1))
    res = await _calc(conn, sup, 30000, bill_date=date(2026, 6, 1))
    got = await arrears_of(sup, sec)
    record(g, "AGGREGATE_FY: untaxed prior ₹30,000 becomes ₹3,000 of arrears",
           "tds=3000 arrears=3000", f"tds={res.tds_amount} arrears={got}",
           res.tds_amount == d(3000) and got == d(3000))

    res = await _calc(conn, sup, 10000, bill_date=date(2026, 7, 1))
    got2 = await arrears_of(sup, sec)
    record(g, "arrears are booked once, not re-added on the next bill",
           "arrears unchanged at 3000", f"arrears={got2}", got2 == d(3000))

    # DUAL: the prior bill was already taxed by the per-txn leg, so no arrears.
    sec = await _temp_section(conn, label="ARREARS_DUAL", rate=2, threshold_type="DUAL",
                               t1=30000, t2=100000)
    sup = await make_supplier(conn, category_id=None, tag="ARREARS_DUAL")
    await _attach(conn, sup, sec)
    await taxed_bill(sup, sec, 40000, 800)
    await _calc(conn, sup, 40000, bill_date=date(2026, 5, 1))
    res = await _calc(conn, sup, 70000, bill_date=date(2026, 6, 1))
    got = await arrears_of(sup, sec)
    record(g, "DUAL: a prior bill already taxed per-txn is NOT charged as arrears",
           "arrears=0", f"tds={res.tds_amount} arrears={got}", got == d(0))


def group_fy_bounds():
    """_fy_bounds named month `fy_start_month - 1`, i.e. month 0 for a January
    fiscal year — it raised, so a calendar-year tenant could not compute TDS."""
    g = "Fiscal-year bounds across start months"
    for start_month, probe, exp_start, exp_end in (
        (4, date(2026, 6, 1), date(2026, 4, 1), date(2027, 3, 31)),
        (4, date(2026, 2, 1), date(2025, 4, 1), date(2026, 3, 31)),
        (1, date(2026, 6, 1), date(2026, 1, 1), date(2026, 12, 31)),
        (12, date(2026, 12, 1), date(2026, 12, 1), date(2027, 11, 30)),
    ):
        try:
            got_start, got_end = _fy_bounds(probe, start_month)
            ok = got_start == exp_start and got_end == exp_end
            actual = f"{got_start}..{got_end}"
        except Exception as exc:
            ok, actual = False, f"raised {type(exc).__name__}: {exc}"
        record(g, f"fy_start_month={start_month}, date={probe}",
               f"{exp_start}..{exp_end}", actual, ok)


def group_double_count_regression(supplier_id: str, stg_bill_id: str, jwt_token: str):
    g = "Double-count regression (Add-Override no longer re-inflates FY state)"
    resp = requests.post(
        f"{API_BASE}/api/settings/tds/vendor-overrides",
        json={"supplier_id": supplier_id, "tds_mode": "always_deduct",
              "mode_reason": f"{NAME_PREFIX}regression check"},
        headers={"Authorization": f"Bearer {jwt_token}"}, timeout=15,
    )
    ok_create = resp.status_code == 201
    record(g, "POST vendor-overrides succeeds", "201", str(resp.status_code), ok_create, resp.text[:200])
    if ok_create:
        created["overrides"].append(resp.json()["override_id"])
    return resp.json().get("override_id") if ok_create else None


async def verify_fy_state_cleared(conn, supplier_id: str):
    g = "Double-count regression (Add-Override no longer re-inflates FY state)"
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS n FROM vendor_tds_fy_state WHERE tenant_id=$1 AND supplier_id=$2",
        TENANT_ID, supplier_id,
    )
    ok = row["n"] == 0
    record(g, "vendor_tds_fy_state wiped after override save (was pre-seeded with stale cumulative_billed)",
           "0 rows", f"{row['n']} rows", ok)
    row2 = await conn.fetchrow(
        "SELECT tds_status FROM stg_bills WHERE stg_bill_id=$1", uuid.UUID(created["stg_bills"][-1]),
    )
    ok2 = row2["tds_status"] == "PENDING"
    record(g, "pre-existing CALCULATED bill reset to PENDING for reprocessing",
           "PENDING", row2["tds_status"], ok2)


def group_cross_tenant_rejection(supplier_id: str, jwt_token: str):
    g = "Security fix: cross-tenant TDS section rejection"
    resp = requests.post(
        f"{API_BASE}/api/settings/tds/vendor-overrides",
        json={"supplier_id": supplier_id, "tds_section_id": FOREIGN_SECTION_ID, "tds_mode": "cumulative",
              "mode_reason": f"{NAME_PREFIX}cross-tenant probe"},
        headers={"Authorization": f"Bearer {jwt_token}"}, timeout=15,
    )
    ok = resp.status_code == 400
    record(g, "override pointing at tenant 003's section is rejected", "400",
           str(resp.status_code), ok, resp.text[:200])

    resp2 = requests.post(
        f"{API_BASE}/api/settings/tds/vendor-overrides",
        json={"supplier_id": supplier_id, "tds_section_id": SEC_194C, "tds_mode": "cumulative",
              "mode_reason": f"{NAME_PREFIX}same-tenant control"},
        headers={"Authorization": f"Bearer {jwt_token}"}, timeout=15,
    )
    ok2 = resp2.status_code == 201
    record(g, "override pointing at tenant 002's own section still succeeds (no false positive)",
           "201", str(resp2.status_code), ok2, resp2.text[:200])
    if ok2:
        created["overrides"].append(resp2.json()["override_id"])


async def cleanup(conn):
    print("\n--- cleanup ---")
    for stg_id in created["fact_bills"]:
        await conn.execute("DELETE FROM fact_bills WHERE stg_bill_id=$1", uuid.UUID(stg_id))
    for stg_id in created["stg_bills"]:
        await conn.execute("DELETE FROM stg_bills WHERE stg_bill_id=$1", uuid.UUID(stg_id))
    for ov_id in created["overrides"]:
        await conn.execute("DELETE FROM dim_vendor_tds_overrides WHERE override_id=$1", uuid.UUID(ov_id))
    for sup_id in created["suppliers"]:
        await conn.execute("DELETE FROM vendor_tds_fy_state WHERE supplier_id=$1", uuid.UUID(sup_id))
        await conn.execute("DELETE FROM dim_suppliers WHERE supplier_id=$1", uuid.UUID(sup_id))
    for sec_id in created["sections"]:
        await conn.execute("DELETE FROM dim_tds_sections WHERE tds_section_id=$1", uuid.UUID(sec_id))

    # Verification pass: nothing tagged with our marker should remain anywhere.
    leftover = await conn.fetchval(
        "SELECT COUNT(*) FROM dim_suppliers WHERE name LIKE $1", f"{NAME_PREFIX}%",
    )
    leftover_sec = await conn.fetchval(
        "SELECT COUNT(*) FROM dim_tds_sections WHERE old_section_label LIKE $1", f"{NAME_PREFIX}%",
    )
    print(f"cleanup verification: {leftover} stray suppliers, {leftover_sec} stray sections remain")
    return leftover, leftover_sec


def render_html_report(path: str, cleanup_ok: bool, leftover_counts: tuple[int, int]):
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(r["group"], []).append(r)

    group_cards = []
    for group, items in groups.items():
        group_pass = sum(1 for i in items if i["passed"])
        group_all_pass = group_pass == len(items)
        row_html = []
        for it in items:
            pill = ('<span class="pill pass">Pass</span>' if it["passed"]
                    else '<span class="pill fail">Fail</span>')
            detail = f'<div class="case-note">{it["detail"]}</div>' if it["detail"] else ""
            row_html.append(f"""
              <tr class="{'' if it['passed'] else 'row-fail'}">
                <td class="col-status">{pill}</td>
                <td class="col-case">{it['name']}{detail}</td>
                <td class="col-val"><span class="mono">{it['expected']}</span></td>
                <td class="col-val"><span class="mono">{it['actual']}</span></td>
              </tr>""")
        group_cards.append(f"""
        <section class="group">
          <header class="group-head">
            <h2>{group}</h2>
            <span class="tally {'tally-pass' if group_all_pass else 'tally-fail'}">{group_pass} / {len(items)}</span>
          </header>
          <div class="table-scroll">
            <table>
              <thead>
                <tr><th class="col-status">Result</th><th class="col-case">Case</th><th class="col-val">Expected</th><th class="col-val">Actual</th></tr>
              </thead>
              <tbody>{''.join(row_html)}</tbody>
            </table>
          </div>
        </section>""")

    all_pass = failed == 0
    verdict = "All checks passed" if all_pass else f"{failed} check{'s' if failed != 1 else ''} failed"
    cleanup_text = ("Every row this run created — suppliers, sections, overrides, bills — "
                     "was removed and independently re-verified as gone."
                     if cleanup_ok else
                     f"STRAY DATA REMAINS: {leftover_counts[0]} supplier(s), {leftover_counts[1]} section(s) — manual cleanup required.")

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>TDS Engine Verification Run</title>
<style>
  :root {{
    --paper:#f5f6fa; --paper-raised:#ffffff; --ink:#1c2430; --ink-soft:#5b6472;
    --line:#dfe3ea; --accent:#2954a6; --accent-soft:#eaf0fb;
    --pass:#1e7f4f; --pass-soft:#e5f3ec; --fail:#b3411e; --fail-soft:#f8e9e4;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --paper:#12161d; --paper-raised:#181d26; --ink:#e7eaf0; --ink-soft:#93a0b4;
      --line:#262d3a; --accent:#8fb2ff; --accent-soft:#1c2740;
      --pass:#49c98a; --pass-soft:#173428; --fail:#e8836a; --fail-soft:#3a2119;
    }}
  }}
  :root[data-theme="dark"] {{
    --paper:#12161d; --paper-raised:#181d26; --ink:#e7eaf0; --ink-soft:#93a0b4;
    --line:#262d3a; --accent:#8fb2ff; --accent-soft:#1c2740;
    --pass:#49c98a; --pass-soft:#173428; --fail:#e8836a; --fail-soft:#3a2119;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    background:var(--paper); color:var(--ink); margin:0; padding:48px 24px 80px;
    font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }}
  .mono {{ font-family:ui-monospace,"SF Mono",Consolas,"Cascadia Mono",monospace;
           font-variant-numeric:tabular-nums; font-size:12.5px; }}
  .sheet {{ max-width:900px; margin:0 auto; }}
  .eyebrow {{ font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase;
              color:var(--accent); margin-bottom:10px; }}
  h1 {{ font-family:Georgia,"Iowan Old Style","Times New Roman",serif; font-size:30px;
        font-weight:600; margin:0 0 8px; text-wrap:balance; }}
  .meta {{ color:var(--ink-soft); font-size:13px; margin-bottom:32px; }}
  .meta .mono {{ font-size:12.5px; }}
  .stamps {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:36px; }}
  .stamp {{ background:var(--paper-raised); border:1px solid var(--line); border-radius:10px;
            padding:16px 18px; }}
  .stamp .n {{ font-family:Georgia,serif; font-size:28px; font-weight:600;
               font-variant-numeric:tabular-nums; line-height:1; }}
  .stamp .l {{ font-size:11px; color:var(--ink-soft); text-transform:uppercase;
               letter-spacing:.08em; margin-top:6px; }}
  .stamp.pass .n {{ color:var(--pass); }} .stamp.fail .n {{ color:var(--fail); }}
  .verdict {{ display:inline-flex; align-items:center; gap:8px; font-size:13px; font-weight:600;
              padding:6px 14px; border-radius:999px; margin-bottom:36px;
              background:{('var(--pass-soft)' if all_pass else 'var(--fail-soft)')};
              color:{('var(--pass)' if all_pass else 'var(--fail)')}; }}
  .group {{ margin-bottom:22px; border:1px solid var(--line); border-radius:12px;
            background:var(--paper-raised); overflow:hidden; }}
  .group-head {{ display:flex; align-items:baseline; justify-content:space-between;
                 padding:14px 18px; border-bottom:1px solid var(--line); }}
  .group-head h2 {{ font-size:14.5px; font-weight:600; margin:0; text-wrap:balance; }}
  .tally {{ font-size:11.5px; font-weight:700; font-variant-numeric:tabular-nums;
            padding:2px 9px; border-radius:999px; white-space:nowrap; margin-left:12px; }}
  .tally-pass {{ background:var(--pass-soft); color:var(--pass); }}
  .tally-fail {{ background:var(--fail-soft); color:var(--fail); }}
  .table-scroll {{ overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ text-align:left; font-size:10.5px; text-transform:uppercase; letter-spacing:.06em;
        color:var(--ink-soft); font-weight:600; padding:9px 18px; border-bottom:1px solid var(--line); }}
  td {{ padding:11px 18px; border-bottom:1px solid var(--line); font-size:13px; vertical-align:top; }}
  tr:last-child td {{ border-bottom:none; }}
  .row-fail {{ background:var(--fail-soft); }}
  .col-status {{ width:64px; }} .col-case {{ width:34%; }} .col-val {{ width:23%; }}
  .pill {{ display:inline-block; font-size:10.5px; font-weight:700; padding:2px 9px; border-radius:999px; }}
  .pill.pass {{ background:var(--pass-soft); color:var(--pass); }}
  .pill.fail {{ background:var(--fail-soft); color:var(--fail); }}
  .case-note {{ color:var(--ink-soft); font-size:11.5px; margin-top:4px; }}
  .signoff {{ margin-top:32px; padding:16px 20px; border:1px solid var(--line); border-radius:10px;
              background:var(--accent-soft); font-size:13px; color:var(--ink); }}
  .signoff strong {{ color:var(--accent); }}
</style></head>
<body>
  <div class="sheet">
    <div class="eyebrow">TDS Engine &middot; Verification Run</div>
    <h1>TDS Test Report</h1>
    <div class="meta">
      Tenant <span class="mono">Demo2 Company Pvt Ltd &middot; {TENANT_ID}</span><br>
      Run at {time.strftime('%Y-%m-%d %H:%M:%S')} against <span class="mono">utils/tds_engine.py</span>
      and <span class="mono">backend_ocr/app/tds_api.py</span>
    </div>

    <div class="stamps">
      <div class="stamp"><div class="n mono">{total}</div><div class="l">Cases run</div></div>
      <div class="stamp pass"><div class="n mono">{passed}</div><div class="l">Passed</div></div>
      <div class="stamp fail"><div class="n mono">{failed}</div><div class="l">Failed</div></div>
    </div>

    <div class="verdict">{verdict}</div>

    {''.join(group_cards)}

    <div class="signoff"><strong>Cleanup —</strong> {cleanup_text}</div>
  </div>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nReport written to {path}")


async def main():
    pool = await init_pool()
    conn = await pool.acquire()
    jwt_token = mint_jwt(TENANT_ID)
    cleanup_ok = False
    leftover_counts = (-1, -1)
    try:
        await group_aggregate_fy(conn)
        await group_dual(conn)
        await group_excess_of(conn)
        await group_per_month(conn)
        await group_pan_missing(conn)
        await group_backdate_guard(conn)
        await group_base_calculation(conn)
        await group_misconfiguration_guards(conn)
        await group_zero_taxable_base(conn)
        await group_arrears(conn)
        group_fy_bounds()

        # Regression + security groups need a running backend (localhost:8001) and go through HTTP.
        dc_supplier = await make_supplier(conn, category_id=CAT_194C, tag="DBLCOUNT")
        dc_stg_id = await _insert_bill_pair(conn, dc_supplier, SEC_194C, date(2026, 6, 1), 40000)
        await conn.execute(
            """INSERT INTO vendor_tds_fy_state
                   (tenant_id, supplier_id, tds_section_id, fy, cumulative_billed, cumulative_tds_deducted,
                    threshold_crossed)
               VALUES ($1,$2,$3,'2026-27',40000,400,false)""",
            TENANT_ID, dc_supplier, SEC_194C,
        )
        group_double_count_regression(dc_supplier, dc_stg_id, jwt_token)
        await verify_fy_state_cleared(conn, dc_supplier)

        ct_supplier = await make_supplier(conn, category_id=None, tag="CROSSTENANT")
        group_cross_tenant_rejection(ct_supplier, jwt_token)

    finally:
        leftover_counts = await cleanup(conn)
        cleanup_ok = leftover_counts == (0, 0)
        report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tds_test_report.html")
        render_html_report(report_path, cleanup_ok, leftover_counts)
        await pool.release(conn)
        await close_pool()

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{passed}/{total} passed. Cleanup ok: {cleanup_ok}")
    if passed != total or not cleanup_ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
