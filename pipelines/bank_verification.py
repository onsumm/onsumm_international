"""Bank Account Verification worker — penny-less verify via Sandbox.co.in.

Processes three tables per batch:
  - dim_suppliers        (PK: supplier_id,     account: bank_account,    ifsc: bank_ifsc)
  - dim_end_customers    (PK: end_customer_id, account: bank_account_no, ifsc: bank_ifsc)
  - supplier_bank_accounts (PK: id, one row per account — a supplier can have many)

Poll condition per table:
  dim_suppliers / dim_end_customers → bank_verified = false
    false  → not yet attempted (or transient error — will retry)
    true   → verified, account confirmed to exist
    null   → checked definitively, account NOT found (no more retries)

  supplier_bank_accounts → verified = false
    false  → not yet attempted / transient error
    true   → verified

Rows within each batch are verified concurrently (up to _CONCURRENCY in-flight).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import NamedTuple

import httpx

import config
from db import get_pool
from pipelines.base import BaseWorker
# IFSC penny-drop is an India rule pack (backend_ocr/app/core/jurisdiction.py).
from backend_ocr.app.core.jurisdiction import pack_enabled_sql

logger = logging.getLogger(__name__)

_SANDBOX_BASE = "https://api.sandbox.co.in"
_CONCURRENCY  = 2   # max simultaneous Sandbox API calls (pool has 10 slots shared by 12 workers)

_token_cache: tuple[str, float] = ("", 0.0)
_token_lock = asyncio.Lock()

# Cache of (entity_id, ifsc, account_no) tuples whose account was definitively not found.
# Prevents re-calling the API on every safety poll for accounts that don't exist.
# Naturally invalidated when bank_account/bank_ifsc changes — the new (ifsc, account_no)
# tuple won't match the cached one, so the row is re-processed.
_not_found_cache: set[tuple[str, str, str]] = set()


class _Target(NamedTuple):
    table: str
    pk_col: str
    account_col: str        # column name for the account number
    ifsc_col: str           # column name for the IFSC code
    verified_col: str       # column name for the verified flag
    three_state: bool       # True → NULL means "not found, no retry"; False → always false/true
    has_name_col: bool      # True → write name_at_bank back to table
    has_updated_at: bool    # True → also SET updated_at = now()


_TARGETS = [
    _Target(
        table="dim_suppliers",
        pk_col="supplier_id",
        account_col="bank_account",
        ifsc_col="bank_ifsc",
        verified_col="bank_verified",
        three_state=True,    # bank_verified is now nullable — NULL = permanently not found
        has_name_col=False,
        has_updated_at=False,
    ),
    _Target(
        table="dim_end_customers",
        pk_col="end_customer_id",
        account_col="bank_account_no",
        ifsc_col="bank_ifsc",
        verified_col="bank_verified",
        three_state=True,    # bank_verified is now nullable — NULL = permanently not found
        has_name_col=False,
        has_updated_at=True,
    ),
    _Target(
        table="supplier_bank_accounts",
        pk_col="id",
        account_col="account_number",
        ifsc_col="ifsc",
        verified_col="verified",
        three_state=False,   # only boolean; no NULL state
        has_name_col=True,
        has_updated_at=False,
    ),
]


class BankVerificationWorker(BaseWorker):
    name           = "bank_verification"
    notify_channel = "ch_bank_verification"

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def _get_token(self, client: httpx.AsyncClient, force: bool = False) -> str:
        global _token_cache
        async with _token_lock:
            token, expires_at = _token_cache
            if not force and token and time.time() < expires_at:
                return token
            r = await client.post(
                f"{_SANDBOX_BASE}/authenticate",
                headers={
                    "x-api-key": config.SANDBOX_API_KEY,
                    "x-api-secret": config.SANDBOX_API_SECRET,
                    "x-api-version": "1.0.0",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            data = r.json()
            if r.status_code != 200 or "access_token" not in data:
                raise RuntimeError(
                    f"Sandbox auth failed: {data.get('message', r.text)}"
                )
            new_token = data["access_token"]
            _token_cache = (new_token, time.time() + 82800)
            return new_token

    def _req_headers(self, token: str) -> dict:
        return {
            "x-api-key": config.SANDBOX_API_KEY,
            "Authorization": token,
            "x-api-version": "1.0.0",
        }

    # ── Penny-less verify call ────────────────────────────────────────────────

    async def _verify_bank(
        self,
        client: httpx.AsyncClient,
        token: str,
        ifsc: str,
        account_no: str,
    ) -> dict:
        r = await client.get(
            f"{_SANDBOX_BASE}/bank/{ifsc}/accounts/{account_no}/penniless-verify",
            headers=self._req_headers(token),
            timeout=15,
        )
        data = r.json()
        if r.status_code != 200 or data.get("code") != 200:
            return {
                "ok": False,
                "account_exists": False,
                "error": data.get("message", "Bank verification failed"),
                "code": r.status_code,
                "raw": data,
            }
        d = data.get("data", {})
        return {
            "ok": True,
            "account_exists": bool(d.get("account_exists", False)),
            "name_at_bank": d.get("name_at_bank", ""),
            "raw": data,
        }

    # ── Per-table batch (concurrent) ──────────────────────────────────────────

    async def _process_table(
        self,
        client: httpx.AsyncClient,
        target: _Target,
        sem: asyncio.Semaphore,
    ) -> int:
        pool = await get_pool()

        # For three_state targets, NULL means "checked, permanently not found" — it
        # must NOT be re-selected, or a permanently-bad row occupies a batch slot on
        # every single poll forever, starving out genuinely new pending rows behind
        # it (this is exactly what happened: newly-verified vendors could get stuck
        # behind a backlog of un-resolvable bad IFSCs that never left the queue).
        # For non-three_state targets, verified_col is never NULL, so this is a no-op.
        state_clause = (
            f"{target.verified_col} = false" if target.three_state
            else f"({target.verified_col} = false OR {target.verified_col} IS NULL)"
        )

        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    f"""
                    SELECT {target.pk_col}    AS entity_id,
                           tenant_id,
                           {target.account_col} AS account_no,
                           {target.ifsc_col}    AS ifsc
                    FROM   {target.table}
                    WHERE  {state_clause}
                      AND  {target.account_col} IS NOT NULL
                      AND  trim({target.account_col}) <> ''
                      AND  {target.ifsc_col} IS NOT NULL
                      AND  trim({target.ifsc_col}) <> ''
                      AND  {pack_enabled_sql("bank_india", target.table + ".tenant_id")}
                      AND  tenant_id = ANY($2::uuid[])
                    ORDER  BY created_at
                    LIMIT  $1
                    """,
                    self.batch_size, tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT {target.pk_col}    AS entity_id,
                           tenant_id,
                           {target.account_col} AS account_no,
                           {target.ifsc_col}    AS ifsc
                    FROM   {target.table}
                    WHERE  {state_clause}
                      AND  {target.account_col} IS NOT NULL
                      AND  trim({target.account_col}) <> ''
                      AND  {target.ifsc_col} IS NOT NULL
                      AND  trim({target.ifsc_col}) <> ''
                      AND  {pack_enabled_sql("bank_india", target.table + ".tenant_id")}
                    ORDER  BY created_at
                    LIMIT  $1
                    """,
                    self.batch_size,
                )

        if not rows:
            return 0

        # Skip rows whose (entity, ifsc, account) combo was already checked and not found.
        # The CDC reset trigger changes bank_account/bank_ifsc → new tuple → not in cache → re-processed.
        rows = [
            r for r in rows
            if (str(r["entity_id"]), (r["ifsc"] or "").strip().upper(), (r["account_no"] or "").strip())
               not in _not_found_cache
        ]
        if not rows:
            return 0

        async def _mark_permanently_unverifiable(eid, tid, reason: str) -> None:
            # Persist the "give up, stop retrying" state to the DB (not just the
            # in-process cache, which is empty again after every restart — that's
            # exactly how permanently-bad rows kept re-entering the query on every
            # poll and occupying batch slots that new, genuinely pending rows needed).
            if not target.three_state:
                return
            updated_at_clause = ", updated_at = now()" if target.has_updated_at else ""
            async with pool.acquire(timeout=30) as conn2:
                async with conn2.transaction():
                    await conn2.execute(
                        f"UPDATE {target.table} SET {target.verified_col} = NULL{updated_at_clause} "
                        f"WHERE {target.pk_col} = $1",
                        eid,
                    )
                    await self.log_audit(
                        conn2,
                        tenant_id=tid,
                        table_name=target.table,
                        entity_id=eid,
                        action="UPDATE",
                        old_values={target.verified_col: False},
                        new_values={target.verified_col: None, "reason": reason},
                    )

        async def verify_one(row) -> bool:
            eid        = row["entity_id"]
            tid        = row["tenant_id"]
            account_no = (row["account_no"] or "").strip()
            ifsc       = (row["ifsc"] or "").strip().upper()

            # Skip obviously invalid IFSCs — valid IFSC is 11 chars, first 4 alpha
            if len(ifsc) != 11 or not ifsc[:4].isalpha():
                logger.warning(
                    "[bank_verification] skipping invalid IFSC=%s entity=%s table=%s",
                    ifsc, eid, target.table,
                )
                _not_found_cache.add((str(eid), ifsc, account_no))
                await _mark_permanently_unverifiable(eid, tid, "invalid_ifsc_format")
                return True

            async with sem:
                try:
                    token  = await self._get_token(client)
                    result = await self._verify_bank(client, token, ifsc, account_no)

                    if not result["ok"] and result.get("code") == 401:
                        token  = await self._get_token(client, force=True)
                        result = await self._verify_bank(client, token, ifsc, account_no)

                    account_exists = result["account_exists"]
                    name_at_bank: str | None = result.get("name_at_bank") or None
                    updated_at_clause = ", updated_at = now()" if target.has_updated_at else ""

                    if not result["ok"]:
                        # 422 = permanent bad IFSC/account — won't succeed on retry
                        if result.get("code") == 422:
                            logger.warning(
                                "[bank_verification] permanent error (422) table=%s entity=%s ifsc=%s: %s — skipping",
                                target.table, eid, ifsc, result.get("error"),
                            )
                            _not_found_cache.add((str(eid), ifsc, account_no))
                            await _mark_permanently_unverifiable(eid, tid, f"sandbox_422: {result.get('error')}")
                            return True  # count as processed
                        # Other errors are transient — leave flag as false, retry
                        logger.warning(
                            "[bank_verification] API error table=%s entity=%s ifsc=%s: %s",
                            target.table, eid, ifsc, result.get("error"),
                        )
                        return False

                    cache_key = (str(eid), ifsc, account_no)
                    if target.three_state:
                        new_verified: bool | None = True if account_exists else None
                    else:
                        new_verified = True if account_exists else False

                    if not account_exists:
                        # Cache this combo — don't call API again until bank details change
                        _not_found_cache.add(cache_key)
                    else:
                        # Account confirmed — remove from not-found cache if present
                        _not_found_cache.discard(cache_key)

                    async with pool.acquire(timeout=30) as conn:
                        async with conn.transaction():
                            if target.has_name_col:
                                await conn.execute(
                                    f"""
                                    UPDATE {target.table}
                                    SET    {target.verified_col} = $1,
                                           name_at_bank = $3
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $2
                                    """,
                                    new_verified, eid, name_at_bank,
                                )
                            else:
                                await conn.execute(
                                    f"""
                                    UPDATE {target.table}
                                    SET    {target.verified_col} = $1
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $2
                                    """,
                                    new_verified, eid,
                                )
                            await self.log_audit(
                                conn,
                                tenant_id=tid,
                                table_name=target.table,
                                entity_id=eid,
                                action="UPDATE",
                                old_values={target.verified_col: False},
                                new_values={
                                    target.verified_col: new_verified,
                                    "account_exists": account_exists,
                                    "name_at_bank": name_at_bank,
                                    "ifsc": ifsc,
                                    "verified_at": time.strftime(
                                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                                    ),
                                },
                            )

                    logger.info(
                        "[bank_verification] %s account=****%s ifsc=%s exists=%s entity=%s",
                        target.table, account_no[-4:], ifsc, account_exists, eid,
                    )
                    return True

                except Exception as exc:
                    logger.error(
                        "[bank_verification] error table=%s entity=%s ifsc=%s: [%s] %s",
                        target.table, eid, ifsc, type(exc).__name__, exc,
                    )
                    try:
                        async with pool.acquire() as conn:
                            await self.log_error(
                                conn,
                                tenant_id=tid,
                                stage="bank_verification",
                                error_type="BANK_VERIFY_ERROR",
                                details={
                                    "table": target.table,
                                    "ifsc": ifsc,
                                    "account_no_tail": account_no[-4:],
                                    "error": str(exc),
                                },
                            )
                    except Exception:
                        pass
                    return False

        results = await asyncio.gather(*[verify_one(row) for row in rows], return_exceptions=True)
        return sum(1 for r in results if r is True)

    # ── Worker loop ───────────────────────────────────────────────────────────

    async def process_batch(self) -> int:
        sem = asyncio.Semaphore(_CONCURRENCY)
        async with httpx.AsyncClient() as client:
            await self._get_token(client)   # warm the cache once before fan-out
            total = 0
            for target in _TARGETS:
                total += await self._process_table(client, target, sem)
        return total
