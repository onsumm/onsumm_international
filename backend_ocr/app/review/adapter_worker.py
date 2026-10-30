"""ReviewAdapterWorker — runs every deterministic detector and keeps rh_findings true.

Shape of one pass, per tenant, per adapter:

    scan()  ->  apply_suppression()  ->  upsert_finding()  ->  sweep_autoresolve()

The sweep is the important half and it only runs after a *complete* pass for that
adapter, never after a partial one. If a scan is truncated by the row limit we
have not seen the whole truth, and auto-resolving on incomplete information would
close findings that are still perfectly live.

Backfill comes free: BaseWorker Phase 1 drains until process_batch() returns 0,
so on first boot this reconciles all existing history without a separate script.

Pool note: this worker shares db.py's asyncpg pool (max_size=10) with twelve
others, so a pass is deliberately a handful of bounded queries per adapter rather
than a per-row walk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
from db import get_pool
from .base_worker import BaseWorker

from .adapters import ALL_ADAPTERS
from .store import apply_suppression, sweep_autoresolve, upsert_finding

logger = logging.getLogger(__name__)

# Per adapter, per tenant, per pass. Above this we assume the scan was truncated
# and skip the sweep rather than risk closing live findings.
SCAN_LIMIT = 500


class ReviewAdapterWorker(BaseWorker):
    name = "review_adapters"
    notify_channel = "ch_review_adapters"
    skip_backfill = False

    def __init__(self) -> None:
        self._adapters = ALL_ADAPTERS()

    async def _tenant_ids(self, conn) -> list[str]:
        ids = config.PIPELINE_TENANT_IDS
        if ids:
            rows = await conn.fetch(
                "SELECT tenant_id FROM dim_tenants "
                "WHERE is_active = true AND tenant_id = ANY($1::uuid[])",
                ids,
            )
        else:
            rows = await conn.fetch(
                "SELECT tenant_id FROM dim_tenants WHERE is_active = true"
            )
        return [str(r["tenant_id"]) for r in rows]

    async def _money_cap(self, conn, tenant_id: str):
        """P95 bill amount, so "a lot of money" is relative to this tenant.

        A ₹2L problem is routine for one customer and an emergency for another;
        a single global constant would bury the small tenant's real issues.
        """
        try:
            return await conn.fetchval(
                """
                SELECT percentile_disc(0.95) WITHIN GROUP (ORDER BY total_amount)
                  FROM fact_invoices
                 WHERE tenant_id = $1::uuid AND soft_status = 'active'
                   AND total_amount IS NOT NULL
                """,
                tenant_id,
            )
        except Exception:
            return None

    async def process_batch(self) -> int:
        """Run one complete reconcile pass. **Always returns 0, deliberately.**

        BaseWorker drains with `while True: n = process_batch(); if n == 0: break`.
        A pass here already reconciles every tenant and every adapter, so running
        it a second time would re-assert exactly the same rows for nothing.
        Returning 0 makes one wakeup mean one pass — which is also what keeps the
        notify storm on fact_invoices affordable, together with a long
        poll_interval_s cooldown in PIPELINE_CFG.
        """
        pool = await get_pool()
        written = 0

        async with pool.acquire() as conn:
            tenants = await self._tenant_ids(conn)

            for tenant_id in tenants:
                cap = await self._money_cap(conn, tenant_id)

                for adapter in self._adapters:
                    started = datetime.now(timezone.utc)
                    seen = 0
                    try:
                        async for draft in adapter.scan(conn, tenant_id, SCAN_LIMIT):
                            kept = await apply_suppression(conn, draft)
                            if kept is None:
                                continue
                            await upsert_finding(
                                conn, kept, tenant_money_cap=cap, now=started
                            )
                            seen += 1
                    except Exception:
                        # One broken adapter must not stop the other nine. A
                        # detector that errors simply asserts nothing this pass,
                        # and we skip its sweep so it cannot close live findings.
                        logger.exception(
                            "[review_adapters] %s failed for tenant %s — skipping its sweep",
                            adapter.detector, tenant_id,
                        )
                        continue

                    written += seen

                    if seen < SCAN_LIMIT:
                        closed = await sweep_autoresolve(
                            conn, tenant_id, adapter.detector, started
                        )
                        if closed:
                            logger.info(
                                "[review_adapters] %s auto-resolved %d for tenant %s",
                                adapter.detector, closed, tenant_id,
                            )
                    else:
                        logger.warning(
                            "[review_adapters] %s hit the scan limit for tenant %s — "
                            "sweep skipped this pass", adapter.detector, tenant_id,
                        )

        logger.info(
            "[review_adapters] pass complete — %d findings asserted across %d tenants",
            written, len(tenants),
        )
        return 0
