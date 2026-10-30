"""Starts/stops the Review Hub, Compliance Agent and Communication Agent's
extraction worker as asyncio tasks inside the FastAPI app's own event loop —
called once from `app/main.py`'s startup/shutdown handlers.

Why tasks in this loop rather than a thread (the Vendor Bill Agent's own
mailbox poller, `routers/agent.py::start_agent_poller`, uses a separate thread
with its own event loop): that poller does blocking psycopg2 + Gmail calls and
would stall this loop if run inline. `ReviewAdapterWorker`, `ReviewAgentWorker`
and `CommExtractionWorker` are already fully async (asyncpg, `AsyncOpenAI`), so
`asyncio.create_task` is enough — no thread, no second event loop, one fewer
moving part than the mailbox poller needed.

Why these three specifically moved out of the separate `pipelines/` process
(started by the repo-root `main.py`) and in here: see that file's own comment
at the point their imports used to be, and `base_worker.py`'s docstring for
why they carry their own copy of the CDC/backfill machinery rather than still
importing `pipelines.base.BaseWorker`.

CDC NOTIFY triggers (`ch_review_adapters`, `ch_review_agent`,
`ch_comm_extract`) are Postgres-level objects installed by
`pipelines/cdc.py::create_notify_triggers`, still called by the repo-root
`main.py` on its own startup — kept alive there for a dozen unrelated workers
(GST/bank verification, FX, tally, …) that this move was never about. This
module does not reinstall them; it assumes the two processes share one
database on which that install has already run at least once. If the separate
pipeline process is ever retired entirely, trigger installation needs a new
home — tracked here rather than silently assumed.

Singleton guard: mirrors `main.py`'s own `pg_try_advisory_lock` pattern with a
*different* lock key (a second process group holding the pipeline's own key
would conflict with, not protect against, this one). Today's deployment is a
single `onsumm-api-dev` process, so this never actually contends — it exists
so that if the API is ever scaled to multiple processes, a second instance
skips starting these workers instead of double-processing, rather than
silently doing so from day one of that change.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Arbitrary, distinct from main.py's own pipeline lock key (875_291_004_417) —
# these are two separate process groups, each entitled to run exactly once.
_SINGLETON_LOCK_KEY = 875_291_004_418

_tasks: list[asyncio.Task] = []
_lock_conn = None


async def start_review_workers() -> None:
    """Idempotent: a reload or a second call is a no-op, not a duplicate start."""
    global _tasks, _lock_conn
    if _tasks:
        return

    from db import get_pool

    pool = await get_pool()
    conn = await pool.acquire()
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SINGLETON_LOCK_KEY)
    if not got:
        await pool.release(conn)
        logger.warning(
            "[review] another process already holds the review-workers lock "
            "(key=%d) — not starting a second copy here.",
            _SINGLETON_LOCK_KEY,
        )
        return
    _lock_conn = conn  # held out of the pool for the process lifetime, same as main.py's

    from .adapter_worker import ReviewAdapterWorker
    from .agent_worker import ReviewAgentWorker
    from .comm_extraction_worker import CommExtractionWorker

    workers = [ReviewAdapterWorker(), ReviewAgentWorker(), CommExtractionWorker()]
    for w in workers:
        task = asyncio.create_task(w.run(), name=f"review.{w.name}")
        task.add_done_callback(_on_worker_done)
        _tasks.append(task)

    logger.info("[review] started %d background worker(s): %s",
                len(_tasks), ", ".join(w.name for w in workers))


def _on_worker_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("[review] worker %s exited with an error — it will not restart "
                     "on its own; this needs a process restart.", task.get_name(), exc_info=exc)
    else:
        logger.warning("[review] worker %s exited (its run() loop is supposed to be "
                       "infinite) — treat this as a bug.", task.get_name())


async def stop_review_workers() -> None:
    """Cancel cleanly on app shutdown so a redeploy doesn't leave orphaned
    asyncpg connections holding the LISTEN sockets or the advisory lock."""
    global _tasks, _lock_conn
    for t in _tasks:
        t.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks = []

    if _lock_conn is not None:
        try:
            from db import get_pool
            pool = await get_pool()
            await _lock_conn.fetchval("SELECT pg_advisory_unlock($1)", _SINGLETON_LOCK_KEY)
            await pool.release(_lock_conn)
        except Exception:
            logger.exception("[review] failed to release the singleton lock cleanly")
        _lock_conn = None
