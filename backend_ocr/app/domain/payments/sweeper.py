"""Background reconciliation for payments the bank has not finished.

Why this exists
---------------
A disbursement leaves `verify-otp` as PENDING and only becomes SUCCESS or
FAILED when the bank is asked again. Until this worker, the only thing that
ever asked was the receipt card in the browser, polling while — and only
while — the user sat on that screen. Navigate away and the payment froze as
PENDING for good: on 2026-10-15 three AU payments resolved to FAILED at the
bank and the local rows still read "processing" the next day, because nobody
was looking when it happened.

That is a reporting hole in both directions. A settled payment never credits
its bill or sends its receipt, and a failed one leaves money looking spent.
The in-flight guard also refuses a fresh attempt while a stale PENDING row
sits there, so a payment that failed at the bank blocks its own retry.

Cadence
-------
AU's integration document is explicit for statusCode 100/101: "Re-enquire
every 5 minutes. DO NOT re-initiate the payment." That is the contract this
worker keeps — it only ever enquires, never initiates, so the worst case of a
bug here is a stale row, never a second transfer.

Enquiries stop when the payment reaches a terminal status, or when it is old
enough that the bank is no longer the place to ask (`_GIVE_UP_AFTER`). A row
that ages out keeps its PENDING status and is reported, not quietly closed:
"we never found out" is a real state and operations needs to see it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

logger = logging.getLogger(__name__)

# AU's documented re-enquiry cadence for statusCode 100/101.
_SWEEP_INTERVAL_S = float(os.getenv("DISBURSEMENT_SWEEP_INTERVAL_S", "300"))

# Leave a payment alone briefly after it is created: the receipt card is
# polling it right then, and the bank has had no time to do anything.
_MIN_AGE_S = float(os.getenv("DISBURSEMENT_SWEEP_MIN_AGE_S", "60"))

# After this, stop asking. The row stays PENDING and is logged for a human —
# an unanswered payment is a fact to escalate, not one to age out silently.
_GIVE_UP_AFTER = timedelta(days=float(os.getenv("DISBURSEMENT_SWEEP_GIVE_UP_DAYS", "7")))

# Bounded so one pass cannot turn into a long run of gateway calls.
_BATCH = int(os.getenv("DISBURSEMENT_SWEEP_BATCH", "25"))

_ACTOR = "system:disbursement-sweeper"


def _due_rows(cur) -> list[tuple[str, str]]:
    """PENDING payments old enough to be worth asking about, oldest first."""
    cur.execute(
        """SELECT disbursement_id, tenant_id
             FROM fact_disbursements
            WHERE status = 'PENDING'
              -- USDC payouts are confirmed from the chain, never by asking a bank.
              AND COALESCE(rail, 'IMPS') <> 'USDC'
              AND created_at < now() - make_interval(secs => %s)
              AND created_at > now() - %s
            ORDER BY updated_at ASC
            LIMIT %s""",
        (_MIN_AGE_S, _GIVE_UP_AFTER, _BATCH),
    )
    return [(str(r[0]), str(r[1])) for r in cur.fetchall()]


def sweep_once() -> dict:
    """One pass. Synchronous (psycopg2 plus a blocking HTTP call per payment),
    so callers on the event loop hand it to a thread.

    Each payment is reconciled on its own connection and committed
    independently: one bank call failing, or one row erroring, must not
    discard the outcome already recorded for another payment.
    """
    # Imported here, not at module scope: the router pulls in FastAPI and the
    # whole disbursement stack, and this module is imported during app startup.
    from ...routers.disbursement import reconcile_disbursement
    from ...config import get_conn, put_conn

    stats = {"checked": 0, "settled": 0, "failed": 0, "still_pending": 0, "errors": 0}

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            due = _due_rows(cur)
        conn.commit()
    finally:
        put_conn(conn)

    if not due:
        return stats

    for disbursement_id, tenant_id in due:
        conn = get_conn()
        conn.autocommit = False
        try:
            result = reconcile_disbursement(
                conn, tenant_id, disbursement_id, actor=_ACTOR, event="status_swept",
            )
            stats["checked"] += 1
            status = result.get("status")
            if result.get("settled"):
                stats["settled"] += 1
                # The same receipt the user's own Check status would have sent.
                # `settled` is only ever True for the caller that claimed the
                # PENDING->SUCCESS transition, so this sends exactly once.
                try:
                    from . import notify as payment_notify
                    payment_notify.enqueue_payment_receipt(tenant_id, [disbursement_id], _ACTOR)
                except Exception:
                    logger.exception("[disbursement-sweep] receipt enqueue failed for %s "
                                     "(the payment is settled; only the mail is missing)",
                                     disbursement_id)
                logger.info("[disbursement-sweep] %s settled at the bank", disbursement_id)
            elif status == "FAILED":
                stats["failed"] += 1
                logger.info("[disbursement-sweep] %s failed at the bank: %s",
                            disbursement_id, result.get("status_message"))
            else:
                stats["still_pending"] += 1
        except Exception:
            conn.rollback()
            stats["errors"] += 1
            logger.exception("[disbursement-sweep] %s could not be reconciled", disbursement_id)
        finally:
            put_conn(conn)

    return stats


def _report_abandoned() -> None:
    """Payments too old to keep asking about. Logged every pass, deliberately:
    these need a human, and a line that appears once at the moment everyone
    happened to be looking elsewhere is no better than no line at all."""
    from ...config import get_conn, put_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT count(*), COALESCE(sum(amount), 0)
                     FROM fact_disbursements
                    WHERE status = 'PENDING' AND COALESCE(rail, 'IMPS') <> 'USDC'
                      AND created_at <= now() - %s""",
                (_GIVE_UP_AFTER,),
            )
            n, amount = cur.fetchone()
        conn.commit()
        if n:
            logger.warning("[disbursement-sweep] %d payment(s) totalling %s have been PENDING "
                           "longer than %s and are no longer being enquired about — these need "
                           "a manual check with the bank.", n, amount, _GIVE_UP_AFTER)
    finally:
        put_conn(conn)


async def run_sweeper() -> None:
    """Enquire on AU's cadence, forever. Never initiates a payment."""
    logger.info("[disbursement-sweep] started — every %.0fs, batch %d, giving up after %s",
                _SWEEP_INTERVAL_S, _BATCH, _GIVE_UP_AFTER)
    while True:
        try:
            stats = await asyncio.to_thread(sweep_once)
            if stats["checked"]:
                logger.info("[disbursement-sweep] %s", stats)
            await asyncio.to_thread(_report_abandoned)
        except asyncio.CancelledError:
            logger.info("[disbursement-sweep] stopped")
            raise
        except Exception:
            # A pass that blows up must not end the worker — the next payment
            # to go PENDING still needs someone to ask about it.
            logger.exception("[disbursement-sweep] pass failed — continuing")
        await asyncio.sleep(_SWEEP_INTERVAL_S)
