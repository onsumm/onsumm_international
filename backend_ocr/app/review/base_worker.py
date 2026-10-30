"""
CDC-aware async worker base — a deliberate copy of pipelines/base.py, scoped to
the three workers in this package (ReviewAdapterWorker, ReviewAgentWorker,
CommExtractionWorker).

Why a copy and not a shared import: `pipelines/base.py` is also the base class
for a dozen unrelated, pre-existing workers (GST/bank verification, FX, tally,
category enrichment, etc.) that still run in the separate `pipelines/` process
started by the repo-root `main.py`. Review Hub, the Compliance Agent adapters,
and the Communication Agent's extraction worker were moved out of that process
entirely — they run as asyncio tasks inside the FastAPI app's own event loop
instead (see review/scheduler.py) — specifically so nothing under
backend_ocr/app depends on the separate `pipelines` package. Importing the
original class would have reintroduced exactly that dependency; forking the
other dozen workers onto a copy here would have (needlessly) touched systems
this move was never about. One class, copied once, is the smaller and more
honest trade-off than either of those.

If `pipelines/base.py`'s CDC/backfill/backoff behaviour ever changes, this copy
does not follow automatically — that is the cost of the split, accepted
because this behaviour is stable, well-exercised infrastructure, not something
expected to change often.

Lifecycle
─────────
Phase 1 — Backfill
    Drain all existing unprocessed rows in large batches with no sleep between
    iterations (just asyncio.sleep(0) to yield the event loop).  Stops when
    process_batch() returns 0.  Workers with skip_backfill = True jump straight
    to Phase 2.

Phase 2 — Incremental (CDC)
    a) notify_channel set  — open a dedicated LISTEN connection.  Any
       pg_notify on that channel immediately wakes the processing loop via an
       asyncio.Event.  A periodic safety-poll (idle_poll_interval_s) runs even
       when no notification arrives so rows that slip through a trigger gap are
       never permanently stuck.
    b) notify_channel None — pure exponential-backoff polling (time-driven
       workers like approval / tally).

Config
──────
All tuning lives in config.PIPELINE_CFG[worker.name].  Workers declare only
`name`, `notify_channel`, and (optionally) `skip_backfill` — nothing else.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

import asyncpg

import config
from db import get_pool

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "backfill_batch":       200,
    "incremental_batch":     50,
    "poll_interval_s":        5.0,
    "idle_poll_interval_s":  60.0,
    "error_backoff_s":        5.0,
    "error_backoff_cap_s":  120.0,
}


class BaseWorker(ABC):
    name: str = "base"

    # Set in subclasses for LISTEN/NOTIFY CDC wakeup
    notify_channel: str | None = None

    # Time-driven workers (approval, tally) set this True to skip Phase 1
    skip_backfill: bool = False

    # ── Internal state ─────────────────────────────────────────────────────────
    _current_batch_size: int = _DEFAULTS["incremental_batch"]

    # ── Config ─────────────────────────────────────────────────────────────────

    @property
    def _cfg(self) -> dict[str, Any]:
        pipeline_cfg = getattr(config, "PIPELINE_CFG", {})
        overrides = pipeline_cfg.get(self.name, {})
        return {**_DEFAULTS, **overrides}

    @property
    def batch_size(self) -> int:
        """Current batch size — large during backfill, normal during CDC."""
        return self._current_batch_size

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        cfg = self._cfg

        if not self.skip_backfill:
            logger.info("[%s] Phase 1 — backfill starting (batch=%d)",
                        self.name, cfg["backfill_batch"])
            total = await self._run_backfill(cfg)
            logger.info("[%s] Phase 1 — complete (%d rows processed); entering CDC mode",
                        self.name, total)
        else:
            logger.info("[%s] skip_backfill=True — entering incremental mode directly", self.name)

        channel_info = f"LISTEN {self.notify_channel}" if self.notify_channel else "poll-only"
        logger.info("[%s] Phase 2 — incremental (%s, batch=%d)",
                    self.name, channel_info, cfg["incremental_batch"])
        await self._run_incremental(cfg)

    # ── Phase 1: backfill ──────────────────────────────────────────────────────

    async def _run_backfill(self, cfg: dict) -> int:
        self._current_batch_size = cfg["backfill_batch"]
        total = 0
        error_backoff = cfg["error_backoff_s"]

        while True:
            try:
                n = await self.process_batch()
                error_backoff = cfg["error_backoff_s"]
                if n == 0:
                    return total
                total += n
                await asyncio.sleep(0)  # yield — don't starve other tasks
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[%s] backfill error — retry in %.0fs",
                                 self.name, error_backoff)
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, cfg["error_backoff_cap_s"])

    # ── Phase 2: incremental ───────────────────────────────────────────────────

    async def _run_incremental(self, cfg: dict) -> None:
        self._current_batch_size = cfg["incremental_batch"]
        if self.notify_channel:
            await self._run_listen_notify(cfg)
        else:
            await self._run_poll(cfg)

    async def _run_listen_notify(self, cfg: dict) -> None:
        """Wake on pg_notify; safety-poll every idle_poll_interval_s."""
        pool = await get_pool()
        event = asyncio.Event()

        def _on_notify(_conn, _pid, _channel, _payload) -> None:
            event.set()

        listen_conn = await pool.acquire()
        try:
            await listen_conn.add_listener(self.notify_channel, _on_notify)
            logger.info("[%s] CDC LISTEN active on channel %r", self.name, self.notify_channel)

            error_backoff = cfg["error_backoff_s"]
            while True:
                try:
                    # Block until notified OR safety-poll timeout
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(event.wait()),
                            timeout=cfg["idle_poll_interval_s"],
                        )
                    except asyncio.TimeoutError:
                        pass  # safety-poll: check even without a notification

                    event.clear()

                    # Drain all rows that are ready
                    while True:
                        n = await self.process_batch()
                        error_backoff = cfg["error_backoff_s"]
                        if n == 0:
                            break
                        await asyncio.sleep(0)

                    # Brief cooldown before re-arming the listener
                    await asyncio.sleep(cfg["poll_interval_s"])

                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("[%s] CDC error — retry in %.0fs",
                                     self.name, error_backoff)
                    await asyncio.sleep(error_backoff)
                    error_backoff = min(error_backoff * 2, cfg["error_backoff_cap_s"])

        except asyncio.CancelledError:
            raise
        finally:
            try:
                await listen_conn.remove_listener(self.notify_channel, _on_notify)
            except Exception:
                pass
            await pool.release(listen_conn)

    async def _run_poll(self, cfg: dict) -> None:
        """Pure polling with exponential (×2) back-off when queue is empty.

        Class-level overrides (checked before config):
            poll_interval  — base sleep between polls (default: cfg["poll_interval_s"])
            _BACKOFF_BASE  — starting sleep for both idle and error back-off
                             (default: cfg["error_backoff_s"])
            _BACKOFF_CAP   — maximum sleep for both idle and error back-off
                             (default: cfg["idle_poll_interval_s"])
        """
        poll_interval = getattr(self, "poll_interval", cfg["poll_interval_s"])
        backoff_base  = getattr(self, "_BACKOFF_BASE",  cfg["error_backoff_s"])
        backoff_cap   = getattr(self, "_BACKOFF_CAP",   cfg["idle_poll_interval_s"])

        error_backoff = backoff_base
        idle_sleep    = poll_interval

        while True:
            try:
                n = await self.process_batch()
                error_backoff = backoff_base   # reset on any success
                if n == 0:
                    await asyncio.sleep(idle_sleep)
                    idle_sleep = min(idle_sleep * 2, backoff_cap)
                else:
                    idle_sleep = poll_interval  # reset idle back-off after real work
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[%s] poll error — retry in %.0fs",
                                 self.name, error_backoff)
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, backoff_cap)

    # ── Interface ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def process_batch(self) -> int:
        """Process one batch of self.batch_size rows. Return count handled."""

    # ── Shared helpers ─────────────────────────────────────────────────────────

    async def log_error(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | str,
        stage: str,
        error_type: str,
        details: dict[str, Any],
        payload_id: UUID | str | None = None,
        upload_id: UUID | str | None = None,
        webhook_event_id: UUID | str | None = None,
    ) -> None:
        await conn.execute(
            """
            INSERT INTO raw_ingestion_errors
                (tenant_id, payload_id, upload_id, webhook_event_id,
                 stage, error_type, validation_errors)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            str(tenant_id),
            str(payload_id) if payload_id else None,
            str(upload_id) if upload_id else None,
            str(webhook_event_id) if webhook_event_id else None,
            stage,
            error_type,
            json.dumps(details),
        )

    @staticmethod
    async def log_audit(
        conn: asyncpg.Connection,
        *,
        tenant_id: "UUID | str",
        table_name: str,
        entity_id: "UUID | str",
        action: str,
        old_values: "dict[str, Any] | None" = None,
        new_values: "dict[str, Any] | None" = None,
    ) -> None:
        """Write one row to wf_audit_log. Never raises — failures are swallowed."""
        import uuid as _uuid
        try:
            await conn.execute(
                """
                INSERT INTO wf_audit_log
                    (tenant_id, table_name, entity_id, action,
                     user_id, old_values_jsonb, new_values_jsonb)
                VALUES ($1, $2, $3, CAST($4 AS audit_action),
                        NULL,
                        $5::jsonb, $6::jsonb)
                """,
                _uuid.UUID(str(tenant_id)),
                table_name,
                _uuid.UUID(str(entity_id)),
                action,
                json.dumps(old_values) if old_values is not None else None,
                json.dumps(new_values) if new_values is not None else None,
            )
        except Exception as exc:
            logger.warning("[audit] write failed for %s/%s: %s", table_name, entity_id, exc)

    @staticmethod
    def _safe(raw: Any, *keys: str, default: Any = None) -> Any:
        """Safely traverse nested dict/list with dot-path keys."""
        cur = raw
        for k in keys:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(k, default)
            if cur is None:
                return default
        return cur
