"""One-shot live TDS test — triggers TDS pipeline for PENDING bills.

Run from repo root:
    python scripts/test_tds_live.py [--dry-run]

--dry-run  Shows which bills would be processed (no writes).
(default)  Runs one batch of TdsProcessingWorker against live DB.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import asyncpg
import config
from db import get_pool


async def main(dry_run: bool) -> None:
    ssl_cert = os.path.join(os.path.dirname(os.path.dirname(__file__)), "global-bundle.pem")
    pool = await asyncpg.create_pool(
        config.DATABASE_URL,
        ssl={"ca_file": ssl_cert} if os.path.exists(ssl_cert) else None,
        min_size=1,
        max_size=3,
    )

    try:
        # Preview: show bills eligible for TDS processing
        rows = await pool.fetchrow(
            """
            SELECT
                COUNT(*)                              AS total,
                COUNT(*) FILTER (WHERE tds_status IS NULL
                              OR tds_status = 'PENDING') AS pending
            FROM stg_bills
            """
        )
        print(f"\nstg_bills: total={rows['total']}  pending={rows['pending']}")

        preview = await pool.fetch(
            """
            SELECT sb.stg_bill_id, s.name AS supplier_name,
                   sb.bill_date, sb.total_amount, sb.tds_status,
                   vc.name AS vendor_category
            FROM   stg_bills sb
            JOIN   dim_suppliers s  ON s.supplier_id = sb.supplier_id
            LEFT   JOIN dim_vendor_categories vc ON vc.category_id = s.vendor_category_id
            WHERE  (sb.tds_status = 'PENDING' OR sb.tds_status IS NULL)
            ORDER  BY sb.created_at
            LIMIT  20
            """
        )

        if not preview:
            print("No pending bills found.")
            return

        print(f"\nEligible bills (first {len(preview)}):")
        print(f"  {'Supplier':<30} {'Date':<12} {'Total':>12} {'Category'}")
        print("  " + "-" * 75)
        for r in preview:
            cat = r["vendor_category"] or "(no category)"
            print(
                f"  {(r['supplier_name'] or '')[:29]:<30} "
                f"{str(r['bill_date']):<12} "
                f"{float(r['total_amount'] or 0):>12,.0f}  "
                f"{cat}"
            )

        if dry_run:
            print("\n[dry-run] No writes performed.")
            return

        answer = input(f"\nRun TDS pipeline on {len(preview)} bills? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

        # Run the pipeline
        import db as db_module
        db_module._pool = pool  # inject pool so get_pool() returns it

        from pipelines.tds_processing import TdsProcessingWorker
        worker = TdsProcessingWorker()
        processed = await worker.process_batch()
        print(f"\nProcessed: {processed} bills")

        # Show summary
        summary = await pool.fetch(
            """
            SELECT tds_status,
                   COUNT(*)          AS cnt,
                   SUM(tds_amount)   AS total_tds,
                   SUM(net_payable)  AS total_net
            FROM   stg_bills
            GROUP  BY tds_status
            ORDER  BY tds_status
            """
        )
        print("\nstg_bills TDS status summary:")
        for r in summary:
            print(
                f"  {(r['tds_status'] or 'NULL'):<14}  {r['cnt']:>4} bills  "
                f"TDS=₹{float(r['total_tds'] or 0):>12,.0f}  "
                f"net=₹{float(r['total_net'] or 0):>14,.0f}"
            )

    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
