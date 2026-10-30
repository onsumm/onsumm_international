"""
FX Historical Rate Re-booking Migration
─────────────────────────────────────────
Bills that were ingested before the historical-rate fix were booked at the
upload date's rate instead of the bill/invoice date's rate.

This script:
  1. Resets fx_rate_id = NULL for fact_invoices rows where the stored rate's
     date does NOT match the bill's invoice_date (i.e., wrong rate was used).
  2. The daily FxRateWorker._revalue_bills fix-loop will then re-apply the
     correct historical rate (rate on/before invoice_date) on next run.

Safe to re-run — only affects rows where rate date mismatches bill date.
"""

import os
import psycopg2

def _db_config() -> dict:
    """Resolved when called, not at import.

    As a module-level constant this raised KeyError on `import` for anyone whose
    DB_HOST/DB_USER were unset — including a test collector or any module that
    imported this one for a helper, neither of which intends to connect.
    Required variables still fail loudly, just at the point of connecting.
    """
    db_url = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
    if db_url:
        return {"dsn": db_url}
    return dict(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.getenv("DB_SSLMODE", "prefer"),
    )


def run():
    # psycopg2.connect takes dsn as a keyword, so one call covers both shapes.
    conn = psycopg2.connect(**_db_config())
    conn.autocommit = False
    cur = conn.cursor()

    # Count affected rows first
    cur.execute("""
        SELECT COUNT(*)
        FROM fact_invoices fi
        JOIN dim_fx_rates dr ON dr.rate_id = fi.fx_rate_id
        WHERE fi.currency_code != 'INR'
          AND fi.original_amount IS NOT NULL
          AND fi.original_amount > 0
          AND fi.invoice_date IS NOT NULL
          AND dr.rate_date != fi.invoice_date
          AND COALESCE(fi.soft_status, 'active') = 'active'
    """)
    count = cur.fetchone()[0]
    print(f"Found {count} fact_invoices rows with mismatched booking rate dates.")

    if count == 0:
        print("Nothing to fix — all bills already use correct historical rates.")
        conn.close()
        return

    # Reset fx_rate_id so the daily fix-loop re-books at the correct date
    cur.execute("""
        UPDATE fact_invoices fi
        SET fx_rate_id = NULL
        FROM dim_fx_rates dr
        WHERE dr.rate_id = fi.fx_rate_id
          AND fi.currency_code != 'INR'
          AND fi.original_amount IS NOT NULL
          AND fi.original_amount > 0
          AND fi.invoice_date IS NOT NULL
          AND dr.rate_date != fi.invoice_date
          AND COALESCE(fi.soft_status, 'active') = 'active'
    """)
    updated = cur.rowcount
    print(f"Reset fx_rate_id for {updated} rows → will be re-booked at historical rates on next FxRateWorker run.")

    conn.commit()
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    run()
