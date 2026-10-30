"""ReviewAgentWorker — claims review requests, runs the agent, publishes findings.

Per request: dossier -> triage -> budget -> LLM -> canonicalise -> verify -> upsert.

Two properties that are load-bearing rather than incidental:

**skip_backfill = True.** Without it, BaseWorker Phase 1 would drain every
historical entity in the database through a model on first boot. That is the cost
bomb in this feature, and it is one attribute away at all times — there is a
regression test asserting this flag.

**The agent runs here, never in the API process.** The API is sync psycopg2 on a
pool of 50; an inline model call would hold a connection for seconds. It only
ever inserts a request row and notifies.

Pool discipline matters too: this worker shares db.py's asyncpg pool (max_size=10)
with twelve others, in one event loop under the advisory-lock singleton. Hence
the concurrency semaphore, and hence AsyncOpenAI — the sync client used in
fact_graph.py would stall all thirteen workers on every call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import config
from db import get_pool
from .base_worker import BaseWorker

from . import links
from .canonicalize import canonicalize_type, type_is_suppressed
from .dossier import build_bill_dossier, dossier_hash, render
from .prompts import PROMPT_VERSION, RESPONSE_SCHEMA, SYSTEM, build_user
from .routing import data_audience
from .store import apply_suppression, upsert_finding
from .triage import Subject, baseline_is_usable, should_review
from .types import FindingDraft, condition_hash, text_evidence
from .verifier import REFUTED, UNVERIFIABLE, VERIFIED, log_reject, verify_claims

logger = logging.getLogger(__name__)

# Small on purpose. Every concurrent run holds a pool connection while it waits
# on the network, and there are only ten to share with twelve other workers.
MAX_CONCURRENCY = 2

TIER_MODELS = {"cheap": "gpt-4o-mini", "standard": "gpt-4o-mini", "deep": "gpt-4o"}


class ReviewAgentWorker(BaseWorker):
    name = "review_agent"
    notify_channel = "ch_review_agent"
    skip_backfill = True   # load-bearing — see the module docstring

    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def process_batch(self) -> int:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE rh_review_requests
                   SET status = 'RUNNING', started_at = now()
                 WHERE request_id IN (
                     SELECT request_id FROM rh_review_requests
                      WHERE status = 'PENDING'
                      ORDER BY created_at
                      FOR UPDATE SKIP LOCKED
                      LIMIT $1
                 )
                RETURNING request_id, tenant_id, subject_type, subject_id, tier,
                          trigger_type, requested_by
                """,
                self.batch_size,
            )

        if not rows:
            return 0

        await asyncio.gather(*(self._run_one(dict(r)) for r in rows))
        return len(rows)

    async def _run_one(self, req: dict) -> None:
        async with self._sem:
            pool = await get_pool()
            async with pool.acquire() as conn:
                try:
                    published = await self._review(conn, req)
                    await conn.execute(
                        "UPDATE rh_review_requests SET status = 'DONE', finished_at = now() "
                        " WHERE request_id = $1",
                        req["request_id"],
                    )
                    if published:
                        logger.info("[review_agent] published %d finding(s) for %s %s",
                                    published, req["subject_type"], req["subject_id"])
                except Exception as exc:
                    logger.exception("[review_agent] request %s failed", req["request_id"])
                    await conn.execute(
                        "UPDATE rh_review_requests SET status = 'FAILED', error = $2, "
                        " finished_at = now() WHERE request_id = $1",
                        req["request_id"], str(exc)[:500],
                    )

    async def _skip(self, conn, req: dict, reason: str) -> int:
        await conn.execute(
            "UPDATE rh_review_requests SET status = 'SKIPPED', error = $2, finished_at = now() "
            " WHERE request_id = $1",
            req["request_id"], reason,
        )
        return 0

    async def _review(self, conn, req: dict) -> int:
        tenant_id = str(req["tenant_id"])
        subject_type = req["subject_type"]
        subject_id = str(req["subject_id"]) if req["subject_id"] else None

        if subject_type not in ("bill", "invoice") or not subject_id:
            return await self._skip(conn, req, "only bill review is implemented")

        built = await build_bill_dossier(conn, tenant_id, subject_id)
        if built is None:
            return await self._skip(conn, req, "subject not found")
        dossier, red = built
        d_hash = dossier_hash(dossier)

        last_hash = await conn.fetchval(
            """
            SELECT r.idempotency_key FROM rh_review_requests r
             WHERE r.tenant_id = $1::uuid AND r.subject_id = $2::uuid
               AND r.status = 'DONE' AND r.request_id <> $3
             ORDER BY r.finished_at DESC LIMIT 1
            """,
            tenant_id, subject_id, req["request_id"],
        )

        amount = dossier["bill"].get("total_amount")
        history_n = int(dossier.get("vendor_stats", {}).get("n") or 0)
        subject = Subject(
            tenant_id=tenant_id,
            subject_type="bill",
            subject_id=subject_id,
            dossier_hash=d_hash,
            amount_inr=Decimal(str(amount)) if amount is not None else None,
            has_existing_finding=bool(dossier.get("already_flagged")),
            history_count=history_n,
        )

        # A user pressing Rescan is an explicit instruction; the gate exists to
        # stop us reviewing things nobody asked about, not to argue with someone
        # who did.
        if req["trigger_type"] != "user":
            decision = should_review(subject, last_dossier_hash=last_hash)
            if not decision.run:
                return await self._skip(conn, req, decision.reason)
            tier = decision.tier
        else:
            tier = req["tier"] or "standard"

        model = TIER_MODELS.get(tier, "gpt-4o-mini")

        run_id = await conn.fetchval(
            """
            INSERT INTO agent_runs
                (tenant_id, agent_name, trigger_type, trigger_ref, subject_type,
                 subject_id, requested_by, model, prompt_version)
            VALUES ($1::uuid, 'review', $2, $3, $4, $5::uuid, $6, $7, $8)
            RETURNING run_id
            """,
            tenant_id, req["trigger_type"], str(req["request_id"]), "bill",
            subject_id, req.get("requested_by"), model, PROMPT_VERSION,
        )

        try:
            result = await self._call_model(tenant_id, dossier, model)
        except Exception as exc:
            await conn.execute(
                "UPDATE agent_runs SET status = $2, error = $3, finished_at = now() "
                " WHERE run_id = $1",
                run_id, "REFUSED" if "cap" in str(exc).lower() or "kill" in str(exc).lower()
                else "FAILED", str(exc)[:500],
            )
            return await self._skip(conn, req, str(exc)[:200])

        findings = result.content.get("findings") or []
        published = 0
        rejected = 0

        for raw in findings[:8]:
            ok = await self._publish(
                conn, tenant_id, subject_id, dossier, red, raw,
                run_id=str(run_id), model=model, history_n=history_n,
            )
            published += 1 if ok else 0
            rejected += 0 if ok else 1

        await conn.execute(
            """
            UPDATE agent_runs
               SET status = 'SUCCEEDED', finished_at = now(),
                   input_tokens = $2, output_tokens = $3, cost_usd = $4,
                   latency_ms = $5, steps = $6::jsonb,
                   outcome = $7::jsonb,
                   tools_used = $8::text[]
             WHERE run_id = $1
            """,
            run_id, result.input_tokens, result.output_tokens, result.cost_usd,
            result.latency_ms, json.dumps(result.steps),
            json.dumps({"proposed": len(findings), "published": published,
                        "rejected": rejected, "dossier_hash": d_hash}),
            ["dossier", "verifier"],
        )
        return published

    async def _call_model(self, tenant_id: str, dossier: dict, model: str):
        # Local import: this module now lives inside backend_ocr.app, so this
        # is a plain sibling-package import, not the sys.path reach-around it
        # needed back when pipelines/review/ was a separate top-level package.
        from ..ai.llm import complete_json

        return await complete_json(
            tenant_id=tenant_id,
            system=SYSTEM,
            user=build_user("bill", render(dossier)),
            model=model,
            schema=RESPONSE_SCHEMA,
            temperature=0.0,
            max_tokens=2000,
            purpose="review-agent",
        )

    async def _publish(
        self, conn, tenant_id: str, subject_id: str, dossier: dict, red,
        raw: dict, *, run_id: str, model: str, history_n: int,
    ) -> bool:
        title = (raw.get("title") or "").strip()
        explanation = (raw.get("explanation") or "").strip()
        if not title:
            return False

        claims = raw.get("claims") if isinstance(raw.get("claims"), list) else []

        # A comparative claim with no baseline behind it is invention by
        # construction; drop the claims rather than let them look checkable.
        if not baseline_is_usable(history_n):
            claims = [c for c in claims
                      if isinstance(c, dict) and "mean" not in json.dumps(c).lower()]

        verdict = await verify_claims(conn, tenant_id, claims)

        if verdict.status == REFUTED:
            # Never published. This is the hallucination ledger, and it is the
            # only honest read on whether open-ended detection is working.
            for r in verdict.refuted:
                await log_reject(
                    conn, tenant_id, agent_run_id=run_id, subject_type="bill",
                    subject_id=subject_id, proposed_type=raw.get("proposed_label"),
                    claim=r, reason=r.get("reason", "refuted"),
                    model=model, prompt_version=PROMPT_VERSION,
                )
            logger.info("[review_agent] refuted %r for bill %s", title[:60], subject_id)
            return False

        slug, type_id, _spec = await canonicalize_type(
            conn, tenant_id,
            claim_text=f"{title}. {explanation}",
            proposed_label=raw.get("proposed_label") or "agent_finding",
            family=raw.get("family") or "data_quality",
            severity=raw.get("severity") or "MEDIUM",
        )

        if await type_is_suppressed(conn, tenant_id, slug):
            return False

        stated_confidence = raw.get("confidence")
        confidence = float(stated_confidence) if isinstance(stated_confidence, (int, float)) else 0.6
        if verdict.status == UNVERIFIABLE:
            # Capped, and routed to the Suspected lane by lane_for(). The model's
            # own confidence is not evidence of anything.
            confidence = min(confidence, 0.5)

        # Money comes from what the verifier recomputed, never from the model's
        # prose — the one rule that makes any of this safe in a finance product.
        money = None
        for entry in verdict.values.values():
            for side in ("left", "right"):
                try:
                    money = max(money or Decimal("0"), Decimal(str(entry[side])))
                except Exception:
                    pass
        if verdict.status != VERIFIED:
            money = None

        draft = FindingDraft(
            tenant_id=tenant_id,
            finding_type=slug,
            family=raw.get("family") or "data_quality",
            subject_type="bill",
            subject_id=subject_id,
            severity=raw.get("severity") or "MEDIUM",
            title=red.restore(title)[:200],
            detail=red.restore(explanation),
            deep_link=links.bill(subject_id),
            condition_hash=condition_hash(slug, title, verdict.status),
            detector="agent:review",
            detector_version=PROMPT_VERSION,
            source_service="anomaly_ai",
            confidence=confidence,
            verification=verdict.status,
            money_at_risk=money,
            money_at_risk_inr=money,
            evidence=text_evidence(
                red.restore(explanation),
                [f"Checked {verdict.checked} claim(s) against your data."
                 if verdict.checked else
                 "Nothing here could be checked against your data automatically."],
            ),
            entities=(("bill", subject_id, "subject", dossier["bill"].get("invoice_number")),),
            audience_roles=data_audience().roles,
            required_capability="edit",
            agent_run_id=run_id,
            model=model,
            type_id=type_id,
        )

        kept = await apply_suppression(conn, draft)
        if kept is None:
            return False
        await upsert_finding(conn, kept)
        return True
