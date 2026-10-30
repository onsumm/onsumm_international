"""
Expense Report CSV Generator
─────────────────────────────
Queries fact_invoices + coa_rules for a given month and tenant,
resolves EXP HEAD via COA rules (3-tier: vendor+category > vendor > category),
and writes two CSV files:

  reports/Expense_Report_RAW_<MON-YY>.csv      — one row per bill
  reports/Expense_Report_SUMMARY_<MON-YY>.csv  — EXP HEAD totals (matches screenshot format)

Usage:
    python pipelines/expense_report.py
    python pipelines/expense_report.py --month 2026-04
    python pipelines/expense_report.py --month 2026-04 --status APPROVED
    python pipelines/expense_report.py --month 2026-04 --tenant <uuid> --out reports/
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg

# Allow running both as `python pipelines/expense_report.py` and as an import
try:
    import config
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config

logger = logging.getLogger(__name__)

# ── Valid EXP HEADs ────────────────────────────────────────────────────────────

VALID_EXP_HEADS = {
    "ACCOUNTS", "ADMINISTRATION", "ASSETS", "DUTIES & TAXES",
    "IT", "LEGAL", "MAN POWER", "MARKETING", "OFFICE",
    "PANTRY", "PROFESSIONAL", "RENT", "UTILITY", "OTHER",
}


# ── COA resolution ─────────────────────────────────────────────────────────────

def _resolve_exp_head(
    vendor: str,
    category: str,
    rules: list[dict[str, Any]],
) -> str:
    """
    3-tier COA matching (highest priority first):
      1. vendor + category match
      2. vendor-only match
      3. category-only match
      4. default → OTHER
    """
    v = (vendor or "").lower()
    c = (category or "").lower()

    for r in rules:
        rv = (r["vendor_name"] or "").lower()
        rc = (r["category"] or "").lower()
        if rv and rc and rv in v and rc == c:
            return r["exp_head"]

    for r in rules:
        rv = (r["vendor_name"] or "").lower()
        if rv and not r["category"] and rv in v:
            return r["exp_head"]

    for r in rules:
        rc = (r["category"] or "").lower()
        if not r["vendor_name"] and rc and rc == c:
            return r["exp_head"]

    return "OTHER"


# ── Core async generator ────────────────────────────────────────────────────────

async def generate_expense_csv(
    *,
    tenant_id: str,
    month: str | None = None,
    status_filter: str | None = None,
    output_dir: str | Path = "reports",
    company_name: str | None = None,
) -> tuple[Path, Path]:
    """
    Generate RAW and SUMMARY expense CSV files for the given tenant/month.

    Returns:
        (raw_csv_path, summary_csv_path)
    """
    # ── Resolve month range ────────────────────────────────────────────────────
    if month:
        year, mon = int(month.split("-")[0]), int(month.split("-")[1])
    else:
        today = date.today()
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    next_first = date(year, mon + 1, 1) if mon < 12 else date(year + 1, 1, 1)
    month_label = first_day.strftime("%b-%y").upper()  # "APR-26"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Fetch data ─────────────────────────────────────────────────────────────
    pool = await asyncpg.create_pool(config.DB_URL, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            sql = """
                SELECT
                    fi.invoice_id,
                    fi.stg_bill_id,
                    fi.invoice_date,
                    fi.invoice_number,
                    fi.total_amount,
                    fi.category,
                    fi.status,
                    fi.amount_paid,
                    fi.outstanding_amount,
                    ds.name  AS vendor_name,
                    ds.gstn  AS vendor_gstn
                FROM fact_invoices fi
                LEFT JOIN dim_suppliers ds
                       ON ds.supplier_id = fi.supplier_id
                      AND ds.tenant_id   = fi.tenant_id
                WHERE fi.tenant_id   = $1
                  AND fi.invoice_date >= $2
                  AND fi.invoice_date  < $3
            """
            params: list[Any] = [tenant_id, first_day, next_first]
            if status_filter:
                sql += f" AND UPPER(fi.status::text) = ${len(params) + 1}"
                params.append(status_filter.upper())
            sql += " ORDER BY fi.invoice_date, ds.name"

            bills = await conn.fetch(sql, *params)

            rules_rows = await conn.fetch(
                """
                SELECT exp_head, vendor_name, category, priority
                FROM coa_rules
                WHERE tenant_id = $1
                ORDER BY priority DESC
                """,
                tenant_id,
            )

            # Resolve company name from dim_tenants if not supplied
            if not company_name:
                row = await conn.fetchrow(
                    "SELECT company_name FROM dim_tenants WHERE tenant_id = $1",
                    tenant_id,
                )
                company_name = (row["company_name"] if row else "") or "COMPANY"
    finally:
        await pool.close()

    rules_list = [dict(r) for r in rules_rows]
    logger.info(
        "[expense_report] %d bills fetched, %d COA rules loaded for %s",
        len(bills), len(rules_list), month_label,
    )

    # ── Write RAW DATA CSV ─────────────────────────────────────────────────────
    raw_path = output_dir / f"Expense_Report_RAW_{month_label}.csv"
    exp_head_totals: dict[str, float] = {}

    with open(raw_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "COMPANY", "VENDOR NAME", "INVOICE NO", "SERVICE MONTH",
            "SUB HEAD", "SERVICE (CATEGORY)", "EXP HEAD",
            "TOTAL", "OPEX", "MONTHLY EXPENSE",
        ])

        for bill in bills:
            vendor   = (bill["vendor_name"] or "").strip()
            category = (bill["category"] or "General").strip()
            exp_head = _resolve_exp_head(vendor, category, rules_list)
            total    = float(bill["total_amount"] or 0)

            # OPEX = Y when paid or partially paid
            status_val = (bill["status"] or "").upper()
            opex = "Y" if status_val in ("PAID", "PARTIAL") else ""

            inv_date  = bill["invoice_date"]
            svc_month = inv_date.strftime("%b-%y").upper() if inv_date else month_label
            inv_no    = str(bill["stg_bill_id"] or bill["invoice_id"])

            writer.writerow([
                company_name,
                vendor,
                inv_no,
                svc_month,
                vendor,       # SUB HEAD = vendor short name
                category,
                exp_head,
                round(total, 2),
                opex,
                round(total, 2),   # MONTHLY EXPENSE = TOTAL
            ])

            exp_head_totals[exp_head] = exp_head_totals.get(exp_head, 0.0) + total

    logger.info("[expense_report] RAW written → %s (%d rows)", raw_path.name, len(bills))

    # ── Write SUMMARY CSV ─────────────────────────────────────────────────────
    summary_path = output_dir / f"Expense_Report_SUMMARY_{month_label}.csv"
    grand_total  = sum(exp_head_totals.values())

    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["EXP HEAD", "AMOUNT", "EXP RATIO IN %"])

        for eh, amt in sorted(exp_head_totals.items()):
            pct = (amt / grand_total * 100) if grand_total else 0.0
            writer.writerow([f"{eh} TOTAL", round(amt, 2), round(pct, 2)])

        # Grand total row
        writer.writerow(["GRAND TOTAL", round(grand_total, 2), "100.00"])

    logger.info(
        "[expense_report] SUMMARY written → %s (%d EXP HEADs, grand total %.2f)",
        summary_path.name, len(exp_head_totals), grand_total,
    )

    return raw_path, summary_path


# ── CLI entry point ────────────────────────────────────────────────────────────

async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate vendor expense report CSVs from the pipeline database."
    )
    parser.add_argument(
        "--month",
        default=None,
        metavar="YYYY-MM",
        help="Month to report (default: current month)",
    )
    parser.add_argument(
        "--status",
        default=None,
        metavar="STATUS",
        help="Filter by bill status, e.g. APPROVED (default: all statuses)",
    )
    parser.add_argument(
        "--tenant",
        default=None,
        metavar="UUID",
        help="Tenant UUID (default: TENANT_ID from .env)",
    )
    parser.add_argument(
        "--out",
        default="reports",
        metavar="DIR",
        help="Output directory (default: reports/)",
    )
    args = parser.parse_args()

    tenant_id = args.tenant or config.TENANT_ID
    if not tenant_id:
        print("ERROR: provide --tenant <uuid> or set TENANT_ID in .env", file=sys.stderr)
        sys.exit(1)

    raw_path, summary_path = await generate_expense_csv(
        tenant_id=tenant_id,
        month=args.month,
        status_filter=args.status,
        output_dir=args.out,
    )
    print(f"RAW DATA  → {raw_path}")
    print(f"SUMMARY   → {summary_path}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(_main())
