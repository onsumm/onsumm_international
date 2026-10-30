"""
Smoke test for FxRateWorker.
Fetches live rates, stores them in dim_fx_rates, revalues bills.
"""
import asyncio
import sys
sys.path.insert(0, ".")

from db import init_pool, close_pool, get_pool
from pipelines.fx_rate import FxRateWorker


async def main():
    await init_pool()
    worker = FxRateWorker()

    # ── 1. Fetch primary ────────────────────────────────────────────────────────
    print("Fetching rates from primary source...")
    rates = await worker._fetch_primary()
    if not rates:
        print("[FAIL] Primary source returned no rates")
        sys.exit(1)
    for code, rate in rates.items():
        print(f"  [OK] {code} = INR {rate}")

    # ── 2. Upsert into dim_fx_rates ─────────────────────────────────────────────
    from datetime import date
    today = date.today()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await worker._upsert_rates(conn, today, rates)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM dim_fx_rates WHERE rate_date = $1", today
        )
        print(f"  [OK] Upserted {count} rows into dim_fx_rates for {today}")

        # ── 3. Revaluation ──────────────────────────────────────────────────────
        revalued = await worker._revalue_bills(conn, rates)
        print(f"  [OK] Revalued {revalued} outstanding foreign currency bills")

    await close_pool()
    print("\nSmoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
