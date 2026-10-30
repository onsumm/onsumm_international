"""Starts and stops the crypto rail's background loops inside the FastAPI event loop.

Same shape as domain/payments/scheduler.py: an advisory-lock singleton so two
API processes don't both watch the chain, and an idempotent start. Its lock key
is distinct from the pipeline (…417), the review workers (…418), the
disbursement sweeper (…419) and the anchor builder (…421).

Four loops, each catching and logging its own errors so one bad pass never
stops the loop:

  - chain:  ChainWatcher.tick + OnchainMatcher.run every WATCH_POLL_SECONDS.
            Only when RPC_URL and USDC_ADDRESS are configured.
  - relay:  payouts.retry_ready_payouts every 30 seconds, so a payout left READY
            (relayer out of gas, RPC blip) is re-submitted. Only with the chain configured.
  - ledger: LedgerPoster.process_pending every 30 seconds.
  - anchor: AnchorService.anchor_now daily at 00:05 UTC. Only when
            ANCHOR_SIGNER_KEY is set; without it an admin can still build an
            anchor on demand (POST /api/audit/anchor).

The work is blocking psycopg2 and RPC calls, so each pass runs in a thread.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .settings import get_settings

logger = logging.getLogger(__name__)

_SINGLETON_LOCK_KEY = 875_291_004_420
LEDGER_INTERVAL_SECONDS = 30
RELAY_INTERVAL_SECONDS = 30
ANCHOR_AT = (0, 5)  # 00:05 UTC

_tasks: list[asyncio.Task] = []
_lock_conn = None


def _with_conn(fn):
    from ...config import get_conn, put_conn
    conn = get_conn()
    try:
        return fn(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        put_conn(conn)


def chain_pass() -> dict:
    """One watcher tick and one matcher run. Also used by POST /api/onchain/rescan."""
    from ...integrations.chains.evm import get_chain
    from .recon import OnchainMatcher
    from .watcher import ChainWatcher
    chain = get_chain()

    def _run(conn):
        # The matcher works on rows already recorded, so it must not wait on the
        # watcher: after downtime, or when an RPC refuses log queries, the watcher
        # can fail or lag for a while and payouts would sit unmatched behind it.
        try:
            tick = ChainWatcher(chain).tick(conn)
        except Exception as e:
            conn.rollback()
            logger.warning("[crypto-chain] watcher tick failed (%s); running the matcher anyway", e)
            tick = {"watcher_error": str(e)[:200]}
        stats = OnchainMatcher(chain).run(conn)
        return {**tick, **stats}
    return _with_conn(_run)


def relay_pass() -> int:
    """Re-submit payouts left READY (relayer out of gas, RPC blip, queued Safe nonce)."""
    from .payouts import retry_ready_payouts
    return retry_ready_payouts()


def ledger_pass() -> int:
    from .ledger import LedgerPoster
    return _with_conn(lambda conn: LedgerPoster().process_pending(conn))


def anchor_pass():
    from ..audit.anchor import AnchorService
    return _with_conn(lambda conn: AnchorService().anchor_now(conn))


async def _loop(name: str, interval: float, work) -> None:
    while True:
        try:
            await asyncio.to_thread(work)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[crypto-%s] pass failed; retrying in %.0fs", name, interval)
        await asyncio.sleep(interval)


def _seconds_until_next_anchor(now: datetime) -> float:
    target = now.replace(hour=ANCHOR_AT[0], minute=ANCHOR_AT[1], second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _anchor_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until_next_anchor(datetime.now(timezone.utc)))
        try:
            result = await asyncio.to_thread(anchor_pass)
            logger.info("[crypto-anchor] daily anchor: %s rows", getattr(result, "rows", 0))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[crypto-anchor] daily anchor failed; next attempt tomorrow")


LOCK_RETRY_SECONDS = 30


async def _try_lock():
    """Take the singleton worker lock on a dedicated connection, or return None.

    The connection asks Postgres to probe it with TCP keepalives, so if this
    process dies without releasing the lock (a killed server, a dropped network
    on a laptop) Postgres notices within about a minute and frees the lock,
    instead of holding it on a dead session for hours.
    """
    from db import get_pool

    pool = await get_pool()
    conn = await pool.acquire()
    try:
        await conn.execute("SET tcp_keepalives_idle = 30; SET tcp_keepalives_interval = 10; "
                           "SET tcp_keepalives_count = 3")
    except Exception:
        pass  # keepalive tuning is best effort; the lock still works without it
    if await conn.fetchval("SELECT pg_try_advisory_lock($1)", _SINGLETON_LOCK_KEY):
        return conn
    await pool.release(conn)
    return None


def _start_tasks() -> None:
    settings = get_settings()
    if settings.configured():
        _tasks.append(asyncio.create_task(_loop("chain", settings.watch_poll_seconds, chain_pass),
                                          name="crypto.chain_watcher"))
        _tasks.append(asyncio.create_task(_loop("relay", RELAY_INTERVAL_SECONDS, relay_pass),
                                          name="crypto.relay_retry"))
    else:
        logger.info("[crypto] RPC_URL / USDC_ADDRESS not set; chain watcher not started.")
    _tasks.append(asyncio.create_task(_loop("ledger", LEDGER_INTERVAL_SECONDS, ledger_pass),
                                      name="crypto.ledger_poster"))
    if settings.anchor_signer_key and settings.configured():
        _tasks.append(asyncio.create_task(_anchor_loop(), name="crypto.daily_anchor"))
    else:
        logger.info("[crypto] ANCHOR_SIGNER_KEY not set; daily anchor not scheduled (on-demand still works).")


async def _acquire_later() -> None:
    """Keep trying for the worker lock.

    Another live process may legitimately hold it — or a dead one whose session
    Postgres has not reaped yet. Giving up at startup left the chain watcher and
    matcher off until the next restart; retrying lets this process take over as
    soon as the lock is free.
    """
    global _lock_conn
    while True:
        await asyncio.sleep(LOCK_RETRY_SECONDS)
        try:
            conn = await _try_lock()
        except Exception:
            logger.exception("[crypto] lock retry failed; trying again in %ds", LOCK_RETRY_SECONDS)
            continue
        if conn is not None:
            _lock_conn = conn
            logger.info("[crypto] acquired the crypto worker lock; starting workers")
            _start_tasks()
            return


async def start_crypto_workers() -> None:
    """Idempotent: a reload or a second call is a no-op."""
    global _lock_conn
    if any(not t.done() for t in _tasks):
        return

    conn = await _try_lock()
    if conn is None:
        logger.warning("[crypto] another process holds the crypto worker lock (key=%d); retrying every %ds "
                       "and taking over when it is released.", _SINGLETON_LOCK_KEY, LOCK_RETRY_SECONDS)
        _tasks.append(asyncio.create_task(_acquire_later(), name="crypto.lock_retry"))
        return
    _lock_conn = conn
    _start_tasks()


async def stop_crypto_workers() -> None:
    global _lock_conn
    for t in _tasks:
        t.cancel()
    for t in _tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    _tasks.clear()
    if _lock_conn is not None:
        from db import get_pool
        try:
            await _lock_conn.execute("SELECT pg_advisory_unlock($1)", _SINGLETON_LOCK_KEY)
            pool = await get_pool()
            await pool.release(_lock_conn)
        except Exception:
            logger.exception("[crypto] could not release the worker lock cleanly")
        _lock_conn = None
