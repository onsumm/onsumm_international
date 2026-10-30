"""
Repair vendor_tds_fy_state rows inflated by the double-counting bug: bills that
were reprocessed (via vendor category change or /recalc-tds) before the fix in
backend_ocr/app/main.py had their base amount added on top of the existing
cumulative total instead of replacing it.

For every (tenant_id, supplier_id, tds_section_id, fy) combination currently in
vendor_tds_fy_state, this replays the FY state from scratch off stg_bills —
zeroing cumulative_billed / cumulative_tds_deducted / threshold_crossed and
re-running calculate_tds() for each bill in date order.

Run: python scripts/fix_tds_cumulative_state.py [--dry-run]
"""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import init_pool, close_pool
from utils.tds_catchup import replay_fy_state


async def main():
    dry_run = "--dry-run" in sys.argv

    pool = await init_pool()
    conn = await pool.acquire()

    # Scope to one tenant when asked. Without this the replay rewrites FY state
    # for every tenant in the database, which is rarely what the caller wants
    # when repairing one tenant's drift.
    only_tenant = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--tenant=")), None)
    targets = await conn.fetch(
        """SELECT tenant_id, supplier_id, tds_section_id, fy,
                  cumulative_billed AS before_billed,
                  cumulative_tds_deducted AS before_tds
           FROM vendor_tds_fy_state
           WHERE ($1::uuid IS NULL OR tenant_id = $1::uuid)
           ORDER BY tenant_id, supplier_id, fy""",
        only_tenant,
    )
    scope = f"tenant {only_tenant}" if only_tenant else "ALL TENANTS"
    print(f"Found {len(targets)} vendor/section/FY state rows to check ({scope})\n")

    fixed = 0
    for t in targets:
        tenant_id = str(t["tenant_id"])
        supplier_id = str(t["supplier_id"])
        section_id = str(t["tds_section_id"])
        fy = t["fy"]

        if dry_run:
            actual_billed = await conn.fetchval(
                """SELECT COALESCE(SUM(subtotal), 0) FROM stg_bills
                   WHERE tenant_id = $1 AND supplier_id = $2
                     AND tds_section_id = $3::uuid
                     AND bill_date BETWEEN
                       make_date(split_part($4,'-',1)::int, 4, 1)
                       AND make_date(split_part($4,'-',1)::int + 1, 3, 31)""",
                tenant_id, supplier_id, section_id, fy,
            )
            if actual_billed != t["before_billed"]:
                print(f"MISMATCH tenant={tenant_id} supplier={supplier_id} "
                      f"section={section_id} fy={fy}: "
                      f"stored={t['before_billed']} actual_sum={actual_billed}")
                fixed += 1
            continue

        async with conn.transaction():
            count = await replay_fy_state(conn, tenant_id, supplier_id, section_id, fy)

        after = await conn.fetchrow(
            """SELECT cumulative_billed, cumulative_tds_deducted
               FROM vendor_tds_fy_state
               WHERE tenant_id=$1 AND supplier_id=$2 AND tds_section_id=$3::uuid AND fy=$4""",
            tenant_id, supplier_id, section_id, fy,
        )

        changed = (after["cumulative_billed"] != t["before_billed"]
                   or after["cumulative_tds_deducted"] != t["before_tds"])
        if changed:
            fixed += 1
            print(f"FIXED tenant={tenant_id} supplier={supplier_id} section={section_id} fy={fy} "
                  f"bills={count} billed: {t['before_billed']} -> {after['cumulative_billed']}  "
                  f"tds: {t['before_tds']} -> {after['cumulative_tds_deducted']}")

    print(f"\n{'Would fix' if dry_run else 'Fixed'} {fixed} of {len(targets)} state rows")
    await pool.release(conn)
    await close_pool()


asyncio.run(main())
