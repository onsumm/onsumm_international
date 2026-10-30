"""Why did (or didn't) bill_coding propose a coding correction?

The habit rule is quiet by design: it will not call a bill's category,
department or cost centre odd until the vendor has an established habit to be
odd against. That makes "it didn't appear" ambiguous — no habit yet, no
outlier, or the adapter never ran.

This prints what the detector sees, per vendor, and names the gate that
stopped it. Read-only; safe against production.

    python3 scripts/explain_field_drift.py --tenant <uuid>
    python3 scripts/explain_field_drift.py --tenant <uuid> --vendor signzy
    python3 scripts/explain_field_drift.py --tenant <uuid> --field department

Reads DATABASE_URL from config.py (.env) — no hardcoded credentials.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

from backend_ocr.app.review.adapters.bill_coding import (  # noqa: E402
    _DOMINANCE, _HISTORY_DAYS, _MIN_SAME, _WINDOW_DAYS,
)

# The columns bill_coding proposes from a habit. Cost centre is an id joined
# to a name, so it is expressed here the same way the adapter expresses it.
_FIELDS = [
    SimpleNamespace(key="category", label="category", value_sql="btrim(fi.category)"),
    SimpleNamespace(key="department", label="department", value_sql="btrim(fi.department)"),
    SimpleNamespace(
        key="cost_centre", label="cost centre",
        value_sql=("(SELECT cc.name FROM cost_centers cc "
                   " WHERE cc.id = fi.cost_center_id AND cc.tenant_id = fi.tenant_id)"),
    ),
]


def _connect():
    try:
        return psycopg2.connect(
            config.DATABASE_URL, sslmode=config.DB_SSL, sslrootcert=config.DB_SSL_CERT
        )
    except psycopg2.OperationalError as exc:
        if "certificate verify failed" not in str(exc):
            raise
        return psycopg2.connect(config.DATABASE_URL, sslmode="require")


# Deliberately the detector's own history CTE, minus the gates — the gates are
# what this script is here to evaluate in the open.
SQL = """
    SELECT s.name AS vendor, {value_sql} AS val,
           count(*) AS n,
           max(fi.created_at) AS newest,
           count(*) FILTER (
               WHERE fi.created_at > now() - (%(flag_days)s || ' days')::interval
           ) AS recent_n
      FROM fact_invoices fi
      JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
                          AND s.tenant_id   = fi.tenant_id
     WHERE fi.tenant_id      = %(tenant)s
       AND fi.invoice_type   = 'AP'
       AND fi.supplier_id IS NOT NULL
       AND COALESCE(fi.soft_status, 'active') = 'active'
       AND fi.created_at > now() - (%(history_days)s || ' days')::interval
       AND COALESCE(btrim({value_sql}), '') <> ''
       AND (%(vendor)s IS NULL OR s.name ILIKE %(vendor)s)
     GROUP BY s.name, {value_sql}
     ORDER BY s.name, n DESC
"""


def explain(cur, field, tenant: str, vendor: str | None) -> None:
    cur.execute(
        SQL.format(value_sql=field.value_sql),
        {
            "tenant": tenant,
            "history_days": str(_HISTORY_DAYS),
            "flag_days": str(_WINDOW_DAYS),
            "vendor": f"%{vendor}%" if vendor else None,
        },
    )

    by_vendor: dict[str, list] = {}
    for r in cur.fetchall():
        by_vendor.setdefault(r["vendor"], []).append(r)

    if not by_vendor:
        print(f"  no bills at all with a {field.label} set in the last {_HISTORY_DAYS} days")
        return

    for name, rows in sorted(by_vendor.items()):
        total = sum(r["n"] for r in rows)
        usual = rows[0]
        same_n = usual["n"]
        share = same_n / total
        others = [r for r in rows[1:]]

        print(f"  {name}")
        for r in rows:
            mark = "  <- usual" if r is usual else ""
            recent = f", {r['recent_n']} in the last {_WINDOW_DAYS}d" if r["recent_n"] else ""
            print(f"      {r['n']:>3} x {r['val']}{recent}{mark}")

        # The gates, in the order the SQL applies them.
        if same_n < _MIN_SAME:
            print(f"      → no finding: the habit is {same_n} bill(s), and the "
                  f"floor is {_MIN_SAME}. Not enough history to call anything odd.")
        elif share < _DOMINANCE:
            print(f"      → no finding: the usual value is {share:.0%} of {total} "
                  f"bills, below the {_DOMINANCE:.0%} needed. This vendor uses "
                  f"more than one {field.label}, so there is no habit to break.")
        elif not others:
            print("      → no finding: every bill agrees. Nothing is odd.")
        elif not any(r["recent_n"] for r in others):
            print(f"      → no finding: the odd bill(s) are older than {_WINDOW_DAYS} "
                  f"days. Only recent bills are flagged, so a settled change "
                  f"never raises its own past.")
        else:
            odd = sum(r["recent_n"] for r in others)
            print(f"      → {odd} finding(s) expected: {same_n} of {total} are "
                  f"{usual['val']}, and {odd} recent bill(s) are not.")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tenant", required=True, help="tenant uuid")
    ap.add_argument("--vendor", help="substring of the vendor name, case-insensitive")
    ap.add_argument("--field", choices=[f.key for f in _FIELDS],
                    help="one field; default is all of them")
    args = ap.parse_args()

    fields = [f for f in _FIELDS if not args.field or f.key == args.field]

    print(f"adapter:bill_coding — habit needs {_MIN_SAME}+ bills at "
          f"{_DOMINANCE:.0%}; history {_HISTORY_DAYS}d, flags the last {_WINDOW_DAYS}d\n")

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            for f in fields:
                print(f"── {f.label} ──")
                explain(cur, f, args.tenant, args.vendor)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
