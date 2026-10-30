"""Starts and stops the disbursement sweeper inside the FastAPI event loop.

Mirrors `app/review/scheduler.py` — same advisory-lock singleton, same
idempotent start — with its own distinct lock key. The key must differ from
the pipeline's (875_291_004_417) and the review workers' (875_291_004_418):
sharing one would mean a process holding it for another purpose blocks this
worker, which is the opposite of the protection intended.

Why it runs here and not in the `pipelines/` process: the sweeper reuses
`routers/disbursement.reconcile_disbursement`, the same psycopg2 code path the
Check status button runs. Settlement credits a bill and draws down a remitter
balance, so the alternative — an asyncpg reimplementation in a pipeline worker
— would be a second copy of the money-moving logic, free to drift from the one
the endpoint uses. One implementation, two callers.

The work itself is blocking (psycopg2 and a synchronous HTTP call per
payment), so `run_sweeper` hands each pass to a thread rather than stalling
this loop.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Distinct from main.py's pipeline lock (…417) and the review workers' (…418).
_SINGLETON_LOCK_KEY = 875_291_004_419

_task: asyncio.Task | None = None
_lock_conn = None


async def start_disbursement_sweeper() -> None:
    """Idempotent: a reload or a second call is a no-op, not a duplicate start.

    A second copy would not double-pay — the sweeper only ever enquires, and
    `reconcile_disbursement` claims the PENDING row before settling — but it
    would double the gateway traffic against a bank that asked for one
    enquiry every five minutes.
    """
    global _task, _lock_conn
    if _task and not _task.done():
        return

    from db import get_pool

    pool = await get_pool()
    conn = await pool.acquire()
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SINGLETON_LOCK_KEY)
    if not got:
        await pool.release(conn)
        logger.warning(
            "[disbursement-sweep] another process already holds the sweeper lock "
            "(key=%d) — not starting a second copy here.",
            _SINGLETON_LOCK_KEY,
        )
        return
    _lock_conn = conn  # held out of the pool for the process lifetime

    from .sweeper import run_sweeper

    _task = asyncio.create_task(run_sweeper(), name="payments.disbursement_sweeper")
    _task.add_done_callback(_on_done)


def _on_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # run_sweeper swallows per-pass errors, so reaching here means the loop
        # itself died and no payment will be reconciled until a restart.
        logger.error("[disbursement-sweep] worker stopped unexpectedly — PENDING payments "
                     "will no longer be reconciled until the API restarts", exc_info=exc)


async def stop_disbursement_sweeper() -> None:
    global _task, _lock_conn
    if _task:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None

    if _lock_conn is not None:
        from db import get_pool
        try:
            await _lock_conn.execute("SELECT pg_advisory_unlock($1)", _SINGLETON_LOCK_KEY)
            pool = await get_pool()
            await pool.release(_lock_conn)
        except Exception:
            logger.exception("[disbursement-sweep] releasing the singleton lock failed")
        _lock_conn = None
