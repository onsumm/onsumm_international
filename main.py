"""
Processing Pipeline — Entry Point
──────────────────────────────────
Starts all pipeline workers concurrently as asyncio tasks.
Each worker polls its source table independently and sleeps when idle.

Usage:
    python main.py

Environment variables are read from .env (see config.py).
"""
import asyncio
import logging
import logging.handlers
import os
import signal
import sys

# Which tenants this pipeline instance processes — configured via the
# PIPELINE_TENANT_ID environment variable (comma-separated; empty = ALL tenants).
#
# This used to be a literal list of tenant UUIDs right here in the source. Every
# worker filters on it, so a newly onboarded tenant got zero background processing
# — no OCR, no approvals, no verification — until someone edited this file and
# redeployed. Onboarding a client from the admin panel cannot depend on a code
# change, so the value now lives in .env alongside the other per-environment
# settings. See docs/LICENSING_AND_ADMIN.md.

import config
from db import close_pool, get_pool, init_pool
from pipelines.cdc                   import create_notify_triggers
from pipelines.api_sync              import ApiSyncWorker
from pipelines.approval              import ApprovalWorker
from pipelines.bank_verification     import BankVerificationWorker
from pipelines.category_enrichment   import CategoryEnrichmentWorker
from pipelines.deduplication         import DeduplicationWorker
from pipelines.gst_verification      import GstVerificationWorker
from pipelines.staging_promotion     import StagingPromotionWorker
from pipelines.tally_sync            import TallySyncWorker
from pipelines.tally_read            import TallyReadWorker
from pipelines.tds_processing        import TdsProcessingWorker
from pipelines.upload_ocr            import UploadOcrWorker
from pipelines.webhook               import WebhookWorker
from pipelines.fx_rate               import FxRateWorker
from pipelines.fx_agent              import FxCurrencyAgent
# Review Hub / Compliance Agent adapters, the review AI agent, and the
# Communication Agent's claim extraction worker now run as asyncio tasks
# inside the FastAPI app process (backend_ocr/app/review/scheduler.py),
# not here — see that module's docstring for why.

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s  %(message)s"
_LOG_DATE   = "%Y-%m-%d %H:%M:%S"
_LOG_LEVEL  = getattr(logging, config.LOG_LEVEL, logging.INFO)

os.makedirs("logs", exist_ok=True)

_file_handler = logging.handlers.RotatingFileHandler(
    "logs/pipeline.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB per file
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE))

logging.basicConfig(
    level=_LOG_LEVEL,
    format=_LOG_FORMAT,
    datefmt=_LOG_DATE,
)
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)


class _WorkerLogFilter(logging.Filter):
    """Matches a worker's own module logger, plus any log record from a shared
    module (pipelines.base, pipelines.cdc) that's tagged "[worker_name]" in its
    message — that's how BaseWorker's own lifecycle logs (Phase 1/2, CDC LISTEN,
    poll errors) identify which worker they belong to, since they're logged
    through one shared logger for every worker.
    """

    def __init__(self, worker_name: str, own_module: str):
        super().__init__()
        self._own_module = own_module
        self._tag = f"[{worker_name}]"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == self._own_module or record.name.startswith(self._own_module + "."):
            return True
        return self._tag in record.getMessage()


def _attach_per_worker_log_files(workers: list) -> None:
    """Give each worker its own rotating log file under logs/, in addition to
    the combined logs/pipeline.log — so a single worker's activity/errors can
    be read (or tailed) without wading through all 13 workers' output.
    """
    root = logging.getLogger()
    for w in workers:
        handler = logging.handlers.RotatingFileHandler(
            f"logs/{w.name}.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE))
        handler.addFilter(_WorkerLogFilter(w.name, type(w).__module__))
        root.addHandler(handler)

# ── Graceful shutdown ──────────────────────────────────────────────────────────

_shutdown = asyncio.Event()


def _handle_signal(sig: int, _frame: object) -> None:
    logger.info("Received signal %s — shutting down…", signal.Signals(sig).name)
    _shutdown.set()


async def _watchdog(tasks: list[asyncio.Task]) -> None:
    """Cancel all worker tasks once the shutdown event is set."""
    await _shutdown.wait()
    for t in tasks:
        t.cancel()


# ── Singleton guard ──────────────────────────────────────────────────────────
# A second `python main.py` (e.g. after a restart that didn't actually kill the
# first one) used to silently start a second full set of workers against the
# same database — double OCR, double approvals, double everything. A Postgres
# advisory lock on one dedicated connection (session-scoped, held for the
# process lifetime) makes a second instance fail fast instead.
_SINGLETON_LOCK_KEY = 875_291_004_417  # arbitrary constant identifying this pipeline
_singleton_conn = None


async def _acquire_singleton_lock() -> bool:
    global _singleton_conn
    pool = await get_pool()
    conn = await pool.acquire()
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SINGLETON_LOCK_KEY)
    if not got:
        await pool.release(conn)
        return False
    _singleton_conn = conn  # keep this connection out of the pool so the lock isn't released
    return True


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info("Initialising database pool…")
    await init_pool()
    logger.info("Database pool ready.")

    if not await _acquire_singleton_lock():
        logger.error(
            "Another instance of this pipeline already holds the singleton lock "
            "(pg_try_advisory_lock key=%d) — exiting rather than double-processing.",
            _SINGLETON_LOCK_KEY,
        )
        await close_pool()
        sys.exit(1)
    logger.info("Singleton lock acquired.")

    logger.info("Installing CDC NOTIFY triggers…")
    pool = await get_pool()
    await create_notify_triggers(pool)
    logger.info("CDC triggers ready.")

    workers = [
        ApiSyncWorker(),
        WebhookWorker(),
        UploadOcrWorker(),
        StagingPromotionWorker(),
        ApprovalWorker(),
        # TallyReadWorker(),   # disabled — Tally Prime not running
        # TallySyncWorker(),   # disabled — Tally Prime not running
        DeduplicationWorker(),
        GstVerificationWorker(),
        # BankVerificationWorker(),  # disabled — it declares three_state=True
        # ("NULL = permanently not found") but dim_suppliers.bank_verified and
        # dim_end_customers.bank_verified are still `boolean NOT NULL`, so the
        # not-found write raises NotNullViolationError, the row stays `false`,
        # and the worker re-queries it every cycle — ~60 paid Sandbox API calls
        # per poll, forever. It has never once written a NULL.
        # Re-enable after:
        #   ALTER TABLE dim_suppliers     ALTER COLUMN bank_verified DROP NOT NULL;
        #   ALTER TABLE dim_end_customers ALTER COLUMN bank_verified DROP NOT NULL;
        CategoryEnrichmentWorker(),
        TdsProcessingWorker(),
        FxRateWorker(),
        FxCurrencyAgent(),
    ]

    _attach_per_worker_log_files(workers)

    tasks = [
        asyncio.create_task(w.run(), name=w.name)
        for w in workers
    ]

    logger.info(
        "Started %d workers: %s",
        len(workers),
        ", ".join(w.name for w in workers),
    )

    watchdog = asyncio.create_task(_watchdog(tasks))

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("Workers cancelled — cleaning up…")
    finally:
        watchdog.cancel()
        await close_pool()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass  # not supported on all platforms

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
