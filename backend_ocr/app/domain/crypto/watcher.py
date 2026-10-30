"""Chain watcher: copies USDC transfers touching client treasuries into `chain_transfers`.

The chain, not the custodian and not our own records, is the source of truth
for what settled. The watcher reads USDC `Transfer` logs where any registered
treasury or tax wallet is the sender or receiver, and stores each log once,
keyed by (chain, tx_hash, log_index). Matching those rows to payouts is the
matcher's job (recon.py); the watcher only records what happened.

Cursor and reorgs
─────────────────
`chain_watch_cursors` keeps the last fully processed block *and its hash*.
Each tick first re-reads that block's hash. If the chain no longer agrees, the
recent blocks were replaced (a reorg): every transfer above the rewind point is
flagged `removed`, its match reopened, and the window is read again. A log
that survives the reorg is seen again and un-flagged by the upsert; one that
did not stays removed and can no longer confirm a payout.

Only treasury and tax wallets are watched — not vendor wallets. Vendor wallets
receive money from many payers we have no business watching.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg2.extras

from ...integrations.chains.evm import EvmChain, TransferLog
from .settings import get_settings

logger = logging.getLogger(__name__)

REORG_WINDOW = 20        # blocks re-read after a detected reorg
MAX_RANGE = 2000         # blocks per eth_getLogs call (public RPC limits)
MAX_CHUNKS_PER_TICK = 10  # bounds one tick's work after downtime
FIRST_RUN_LOOKBACK = 2000
CONFIRMATION_TRACK_LIMIT = 64  # stop refreshing confirmations past this depth


def watched_wallets(cur, chain: str) -> dict[str, dict]:
    """lower(address) -> {tenant_id, role, wallet_id} for every treasury and tax wallet."""
    cur.execute(
        """SELECT id::text AS wallet_id, tenant_id::text AS tenant_id, role, address
             FROM treasury_wallets
            WHERE chain = %s AND COALESCE(status, 'ACTIVE') NOT IN ('REMOVED', 'DISABLED')""",
        (chain,),
    )
    return {r["address"].lower(): dict(r) for r in cur.fetchall()}


def classify(log: TransferLog, watched: dict[str, dict]) -> Optional[tuple[str, str]]:
    """(tenant_id, direction) for a log, or None if no watched wallet is involved.

    Money leaving a watched wallet is OUT for that wallet's tenant, even when it
    lands in another watched wallet (a Safe paying its own tax wallet is still a
    payout leg)."""
    sender = watched.get(log.from_address.lower())
    if sender:
        return sender["tenant_id"], "OUT"
    receiver = watched.get(log.to_address.lower())
    if receiver:
        return receiver["tenant_id"], "IN"
    return None


class ChainWatcher:
    def __init__(self, chain: EvmChain):
        self.chain = chain
        self.name = chain.settings.chain

    # ── cursor ──────────────────────────────────────────────────────────────
    def _load_cursor(self, cur) -> Optional[dict]:
        cur.execute("SELECT last_block, last_block_hash FROM chain_watch_cursors WHERE chain = %s", (self.name,))
        return cur.fetchone()

    def _save_cursor(self, cur, block: int, block_hash: str) -> None:
        cur.execute(
            """INSERT INTO chain_watch_cursors (chain, last_block, last_block_hash, updated_at)
               VALUES (%s, %s, %s, now())
               ON CONFLICT (chain) DO UPDATE
                  SET last_block = EXCLUDED.last_block, last_block_hash = EXCLUDED.last_block_hash,
                      updated_at = now()""",
            (self.name, block, block_hash),
        )

    def _handle_reorg(self, cur, cursor_block: int) -> int:
        rewind = max(0, cursor_block - REORG_WINDOW)
        cur.execute(
            """UPDATE chain_transfers
                  SET removed = true, status = 'UNMATCHED', match_type = NULL
                WHERE chain = %s AND block_number > %s AND NOT removed
                  AND NOT simulated              -- demo-settlement rows were never on the chain
            RETURNING tenant_id::text AS tenant_id""",
            (self.name, rewind),
        )
        tenants = {r["tenant_id"] for r in cur.fetchall() if r["tenant_id"]}
        from .findings import raise_finding
        for tenant_id in tenants:
            raise_finding(cur, tenant_id, "REORG_DETECTED", "MEDIUM", "tenant", tenant_id,
                          f"Chain reorganisation detected near block {cursor_block}",
                          {"chain": self.name, "cursor_block": cursor_block, "rewind_to": rewind})
        logger.warning("[chain-watch] reorg detected at %d on %s; rewinding to %d", cursor_block, self.name, rewind)
        return rewind

    # ── tick ────────────────────────────────────────────────────────────────
    def tick(self, conn, from_block: Optional[int] = None) -> dict:
        """Read new blocks into chain_transfers. Returns {processed, cursor_block, head_block}."""
        head = self.chain.latest_block()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            watched = watched_wallets(cur, self.name)
            cursor = self._load_cursor(cur)
            if from_block is not None:
                start = max(0, int(from_block) - 1)
            elif cursor is None:
                start = max(0, head - FIRST_RUN_LOOKBACK)
            else:
                start = int(cursor["last_block"])
                if cursor["last_block_hash"] and start <= head:
                    if self.chain.block_hash(start).lower() != str(cursor["last_block_hash"]).lower():
                        start = self._handle_reorg(cur, start)

            if not watched:
                # Nothing to watch yet: keep up with the head so the first wallet
                # registered later does not trigger a long historical scan.
                self._save_cursor(cur, head, self.chain.block_hash(head))
                conn.commit()
                return {"processed": 0, "cursor_block": head, "head_block": head}

            processed = 0
            block_times: dict[int, datetime] = {}
            to_block = start
            for _ in range(MAX_CHUNKS_PER_TICK):
                if to_block >= head:
                    break
                lo, hi = to_block + 1, min(head, to_block + MAX_RANGE)
                for log in self._logs(lo, hi, list(watched.keys())):
                    kind = classify(log, watched)
                    if kind is None:
                        continue
                    if log.block_number not in block_times:
                        block_times[log.block_number] = datetime.fromtimestamp(
                            self.chain.block_time(log.block_number), tz=timezone.utc)
                    self._upsert(cur, log, kind, head, block_times[log.block_number])
                    processed += 1
                to_block = hi

            cur.execute(
                """UPDATE chain_transfers
                      SET confirmations = GREATEST(0, %s - block_number + 1)
                    WHERE chain = %s AND NOT removed AND NOT simulated AND confirmations < %s""",
                (head, self.name, CONFIRMATION_TRACK_LIMIT),
            )
            self._save_cursor(cur, to_block, self.chain.block_hash(to_block))
            conn.commit()
        return {"processed": processed, "cursor_block": to_block, "head_block": head}

    def _logs(self, lo: int, hi: int, addresses: list[str]) -> list[TransferLog]:
        """get_transfer_logs, halving the range when the RPC refuses it (payload or
        range limits differ by provider). Gives up only on a single block."""
        try:
            return self.chain.get_transfer_logs(lo, hi, addresses)
        except Exception:
            if hi <= lo:
                raise
            mid = (lo + hi) // 2
            return self._logs(lo, mid, addresses) + self._logs(mid + 1, hi, addresses)

    def _upsert(self, cur, log: TransferLog, kind: tuple[str, str], head: int, block_time: datetime) -> None:
        tenant_id, direction = kind
        cur.execute(
            """INSERT INTO chain_transfers
                   (chain, tx_hash, log_index, block_number, block_hash, block_time, token_contract, from_address,
                    to_address, amount_units, tenant_id, direction, match_type, status, confirmations, removed)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, 'UNMATCHED', %s, false)
               ON CONFLICT (chain, tx_hash, log_index) DO UPDATE
                  SET block_number  = EXCLUDED.block_number,
                      block_hash    = EXCLUDED.block_hash,
                      block_time    = EXCLUDED.block_time,
                      confirmations = EXCLUDED.confirmations,
                      removed       = false""",
            (self.name, log.tx_hash, log.log_index, log.block_number, log.block_hash, block_time, log.token_contract,
             log.from_address, log.to_address, log.amount_units, tenant_id, direction,
             max(0, head - log.block_number + 1)),
        )


def watcher_status(conn, chain: Optional[EvmChain], tenant_id: str) -> dict:
    """GET /api/onchain/status body. Lists only the caller's own watched wallets."""
    settings = get_settings()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT last_block FROM chain_watch_cursors WHERE chain = %s", (settings.chain,))
        row = cur.fetchone()
        watched = watched_wallets(cur, settings.chain)
    head = None
    if chain is not None:
        try:
            head = chain.latest_block()
        except Exception:
            head = None
    cursor_block = int(row["last_block"]) if row else None
    return {
        "configured": settings.configured() and chain is not None,
        "chain": settings.chain,
        "chain_id": settings.chain_id,
        "head_block": head,
        "cursor_block": cursor_block,
        "lag_blocks": (head - cursor_block) if (head is not None and cursor_block is not None) else None,
        "watched_addresses": sorted({w["address"] for w in watched.values() if w["tenant_id"] == tenant_id}),
    }
