"""GST Verification worker — verifies vendors via Sandbox.co.in GSTIN API.

Processes two tables per batch:
  - dim_end_customers  (gstin col: gstin,  name col: trade_name, status col: kyc_status  PENDING    → VERIFIED/FAILED)
  - dim_suppliers      (gstin col: gstn,   name col: name,       status col: status       UNVERIFIED → VERIFIED/FAILED)

Rows within each batch are verified concurrently (up to _CONCURRENCY in-flight
at once) so a full batch of 20 takes roughly one API round-trip instead of 20.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import NamedTuple

import httpx

import config
from db import get_pool
from pipelines.base import BaseWorker
# GST verification is an India rule pack: tenants outside India are skipped
# unless they switch it back on (backend_ocr/app/core/jurisdiction.py).
from backend_ocr.app.core.jurisdiction import pack_enabled_sql

logger = logging.getLogger(__name__)

_SANDBOX_BASE  = "https://api.sandbox.co.in"
_CONCURRENCY   = 2   # max simultaneous Sandbox API calls (pool has 10 slots shared by 12 workers)

_token_cache: tuple[str, float] = ("", 0.0)
_token_lock = asyncio.Lock()


class _Target(NamedTuple):
    table: str
    pk_col: str
    gstin_col: str
    name_col: str
    pan_col: str
    status_col: str
    pending_val: str
    has_updated_at: bool


_TARGETS = [
    _Target(
        table="dim_end_customers",
        pk_col="end_customer_id",
        gstin_col="gstin",
        name_col="trade_name",
        pan_col="pan",
        status_col="kyc_status",
        pending_val="PENDING",
        has_updated_at=True,
    ),
    _Target(
        table="dim_suppliers",
        pk_col="supplier_id",
        gstin_col="gstn",
        name_col="name",
        pan_col="pan",
        status_col="status",
        pending_val="UNVERIFIED",
        has_updated_at=False,
    ),
]


class GstVerificationWorker(BaseWorker):
    name           = "gst_verification"
    notify_channel = "ch_gst_verification"

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
            "authorization": token,
            "x-api-version": "1.0.0",
            "Content-Type": "application/json",
        }

    # ── GSTIN API call ────────────────────────────────────────────────────────

    async def _verify_gstin(
        self, client: httpx.AsyncClient, token: str, gstin: str
    ) -> dict:
        r = await client.post(
            f"{_SANDBOX_BASE}/gst/compliance/public/gstin/verify",
            headers=self._req_headers(token),
            json={"gstin": gstin},
            timeout=15,
        )
        data = r.json()
        if r.status_code != 200 or data.get("code") != 200:
            return {
                "ok": False,
                "error": data.get("message", "Verification failed"),
                "code": r.status_code,
                "raw": data,
            }
        d = data.get("data", {}).get("data", {})
        return {
            "ok": True,
            "valid_gstin": d.get("validGstin", False),
            "legal_name": (d.get("legalName") or "").strip(),
            "status": (d.get("status") or "").strip(),
            "pan": (d.get("pan") or "").strip().upper(),
            "state_name": d.get("stateName", ""),
            "state_code": d.get("stateCode", ""),
            "reg_start_date": d.get("regStartDate", ""),
            "raw": data,
        }

    # ── Verification logic ────────────────────────────────────────────────────

    # Common abbreviations that should be treated as equivalent
    _ABBREV = {
        "private limited": ["pvt ltd", "pvt. ltd.", "pvt ltd.", "private ltd", "p ltd"],
        "limited":         ["ltd", "ltd."],
        "public limited":  ["pub ltd", "pub. ltd."],
        "llp":             ["limited liability partnership"],
        "and":             ["&"],
    }

    @staticmethod
    def _normalise(name: str) -> str:
        """Lowercase, collapse whitespace, strip punctuation for fuzzy compare."""
        n = name.lower().strip()
        n = re.sub(r"[.,\-/\\()]+", " ", n)   # punctuation → space
        n = re.sub(r"\s+", " ", n).strip()
        # expand common abbreviations to canonical form
        replacements = {
            r"\bpvt\.?\s*ltd\.?\b": "private limited",
            r"\bltd\.?\b":          "limited",
            r"\bllp\b":             "limited liability partnership",
            r"&":                   "and",
        }
        for pattern, repl in replacements.items():
            n = re.sub(pattern, repl, n)
        return re.sub(r"\s+", " ", n).strip()

    @staticmethod
    def _name_match(db_name: str, api_name: str) -> bool:
        """
        True when names are considered equivalent. Checks:
          1. Exact normalised match
          2. One name is fully contained in the other (handles short trade names)
          3. Word-overlap ≥ 80% of the shorter name's words
        """
        if not db_name or not api_name:
            return False
        a = GstVerificationWorker._normalise(db_name)
        b = GstVerificationWorker._normalise(api_name)
        if a == b:
            return True
        # containment check
        if a in b or b in a:
            return True
        # word-overlap check
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / len(min(a_words, b_words, key=len))
        return overlap >= 0.80

    @staticmethod
    def _decide(result: dict, db_pan: str, db_name: str) -> tuple[str, dict, str | None, str | None]:
        # Returns (status, meta, pan_to_write, name_to_write)
        if not result["ok"]:
            return "FAILED", {"error": result.get("error"), "code": result.get("code")}, None, None

        api_pan    = result["pan"]
        api_legal  = result["legal_name"].strip()
        api_valid  = result["valid_gstin"]
        api_status = result["status"].strip()
        api_active = api_status.lower() == "active"

        pan_blank    = not db_pan
        pan_to_write = api_pan if pan_blank and api_pan else None

        def _fail(reason: str, **extra) -> tuple[str, dict, str | None, str | None]:
            return "FAILED", {
                "gstin_valid": api_valid,
                "gstin_active": api_active,
                "api_status": api_status,
                "api_legal_name": api_legal,
                "api_pan": api_pan,
                "fail_reason": reason,
                "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **extra,
            }, pan_to_write, None

        if not api_valid:
            return _fail("invalid_gstin")
        if not api_active:
            return _fail(f"gstin_status_{api_status.lower()}")

        pan_match = True if pan_blank else (db_pan == api_pan)
        if not pan_match:
            return _fail("pan_mismatch", db_pan=db_pan, api_pan=api_pan)

        # If API returned no legal name, skip name check rather than hard-fail
        if api_legal:
            trade_match = GstVerificationWorker._name_match(db_name, api_legal)
            if not trade_match:
                # GSTIN valid + active + PAN matches but name differs:
                # Accept the API legal name as authoritative — update DB name, mark VERIFIED.
                # Pan was already confirmed matching above.
                meta = {
                    "gstin_valid": api_valid,
                    "gstin_active": api_active,
                    "api_status": api_status,
                    "api_legal_name": api_legal,
                    "api_pan": api_pan,
                    "db_pan_was_blank": pan_blank,
                    "pan_match": True,
                    "trade_name_match": False,
                    "name_updated_from": db_name,
                    "name_updated_to": api_legal,
                    "state": result["state_name"],
                    "reg_start_date": result["reg_start_date"],
                    "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                # name_to_write = api_legal so caller updates the vendor name
                return "VERIFIED", meta, pan_to_write, api_legal
        else:
            trade_match = None  # API returned no name — treat as inconclusive, not a failure

        meta = {
            "gstin_valid": api_valid,
            "gstin_active": api_active,
            "api_status": api_status,
            "api_legal_name": api_legal,
            "api_pan": api_pan,
            "db_pan_was_blank": pan_blank,
            "pan_match": pan_match,
            "trade_name_match": trade_match,
            "state": result["state_name"],
            "reg_start_date": result["reg_start_date"],
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        return "VERIFIED", meta, pan_to_write, None

    # ── Per-table batch (concurrent) ──────────────────────────────────────────

    async def _process_table(
        self,
        client: httpx.AsyncClient,
        target: _Target,
        sem: asyncio.Semaphore,
    ) -> int:
        pool = await get_pool()

        tenant_ids = config.PIPELINE_TENANT_IDS
        async with pool.acquire() as conn:
            if tenant_ids:
                rows = await conn.fetch(
                    f"""
                    SELECT {target.pk_col}  AS entity_id,
                           tenant_id,
                           {target.name_col} AS name,
                           {target.gstin_col} AS gstin,
                           {target.pan_col}  AS pan
                    FROM   {target.table}
                    WHERE  {target.status_col} = $1
                      AND  {target.gstin_col} IS NOT NULL
                      AND  trim({target.gstin_col}::text) <> ''
                      AND  {pack_enabled_sql("gst", target.table + ".tenant_id")}
                      AND  tenant_id = ANY($3::uuid[])
                    ORDER  BY created_at
                    LIMIT  $2
                    """,
                    target.pending_val,
                    self.batch_size,
                    tenant_ids,
                )
            else:
                rows = await conn.fetch(
                    f"""
                    SELECT {target.pk_col}  AS entity_id,
                           tenant_id,
                           {target.name_col} AS name,
                           {target.gstin_col} AS gstin,
                           {target.pan_col}  AS pan
                    FROM   {target.table}
                    WHERE  {target.status_col} = $1
                      AND  {target.gstin_col} IS NOT NULL
                      AND  trim({target.gstin_col}::text) <> ''
                      AND  {pack_enabled_sql("gst", target.table + ".tenant_id")}
                    ORDER  BY created_at
                    LIMIT  $2
                    """,
                    target.pending_val,
                    self.batch_size,
                )

        if not rows:
            return 0

        async def verify_one(row) -> bool:
            eid     = row["entity_id"]
            tid     = row["tenant_id"]
            gstin   = (row["gstin"] or "").strip()
            db_pan  = (row["pan"] or "").strip().upper()
            db_name = (row["name"] or "").strip()

            async with sem:
                try:
                    token  = await self._get_token(client)
                    result = await self._verify_gstin(client, token, gstin)

                    if not result["ok"] and result.get("code") == 401:
                        token  = await self._get_token(client, force=True)
                        result = await self._verify_gstin(client, token, gstin)

                    new_status, meta, pan_to_write, name_to_write = self._decide(result, db_pan, db_name)
                    updated_at_clause = ", updated_at = now()" if target.has_updated_at else ""

                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            if name_to_write and target.table == "dim_suppliers":
                                # The portal's name differs from ours: it is the vendor's
                                # *legal* name. Record it as such and keep the brand name
                                # the business uses — overwriting `name` replaced "RANA
                                # ENTERPRISES" with "RAJ KUMAR" on every bill and report.
                                await conn.execute(
                                    f"""
                                    UPDATE dim_suppliers
                                    SET    {target.status_col} = $1,
                                           legal_name = $2
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $3
                                    """,
                                    new_status, name_to_write, eid,
                                )
                                # Audit says what happened: a legal name recorded, the
                                # brand name kept — not a name replaced.
                                meta = {k: v for k, v in meta.items()
                                        if k not in ("name_updated_from", "name_updated_to")}
                                meta.update(legal_name_recorded=name_to_write, brand_name_kept=db_name)
                            elif name_to_write and target.table == "dim_end_customers":
                                # trade_name_mismatch override: GSTIN valid+active+PAN OK → update name
                                name_col = "company_name"
                                await conn.execute(
                                    f"""
                                    UPDATE {target.table}
                                    SET    {target.status_col} = $1,
                                           {name_col} = $2
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $3
                                    """,
                                    new_status, name_to_write, eid,
                                )
                            elif pan_to_write:
                                await conn.execute(
                                    f"""
                                    UPDATE {target.table}
                                    SET    {target.pan_col}    = $1,
                                           {target.status_col} = $2
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $3
                                    """,
                                    pan_to_write, new_status, eid,
                                )
                            else:
                                await conn.execute(
                                    f"""
                                    UPDATE {target.table}
                                    SET    {target.status_col} = $1
                                           {updated_at_clause}
                                    WHERE  {target.pk_col} = $2
                                    """,
                                    new_status, eid,
                                )
                            await self.log_audit(
                                conn,
                                tenant_id=tid,
                                table_name=target.table,
                                entity_id=eid,
                                action="UPDATE",
                                old_values={target.status_col: target.pending_val, target.pan_col: db_pan or None},
                                new_values={target.status_col: new_status, **meta},
                            )

                    logger.info(
                        "[gst_verification] %s %s → %s  gstin=%s trade_match=%s pan_backfilled=%s",
                        target.table, eid, new_status, gstin,
                        meta.get("trade_name_match"), bool(pan_to_write),
                    )
                    return True

                except Exception as exc:
                    logger.error(
                        "[gst_verification] error table=%s entity=%s gstin=%s: %s",
                        target.table, eid, gstin, exc,
                    )
                    try:
                        async with pool.acquire() as conn:
                            await self.log_error(
                                conn,
                                tenant_id=tid,
                                stage="gst_verification",
                                error_type="GST_VERIFY_ERROR",
                                details={"table": target.table, "gstin": gstin, "error": str(exc)},
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
