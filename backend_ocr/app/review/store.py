"""The one and only writer into rh_findings.

Adapters and the review agent both come through here. Two invariants this module
exists to hold:

1. **One live finding per (tenant, finding_key).** Enforced by a *partial*
   unique index on `resolved_at IS NULL`, which gives us re-open semantics for
   free: a re-detection while the finding is still live updates it in place,
   while a re-detection after someone resolved it inserts a NEW row — correctly
   a distinct regression with its own first_detected_at, rather than quietly
   resurrecting a row the user already dealt with.

2. **Adapters declare truth, not deltas.** Each pass yields the complete current
   state of its slice; anything of that detector not re-asserted during the pass
   is AUTO_RESOLVED. That is what makes mirroring existing signal safe: the
   mirror cannot go stale, because staleness is what the sweep closes.

The upsert replaces the check-then-act in pipelines/deduplication.py:_upsert_anomaly
(SELECT 1 ... then INSERT), which two concurrent workers can both pass and which
cannot update anything — a re-detection at a worse severity was silently dropped.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

import asyncpg

from .ranking import rank_score
from .types import FindingDraft

logger = logging.getLogger(__name__)

UNDO_TTL_SECONDS = 30


# Families whose findings are always worked at one fixed priority, whatever the
# detector (or the review agent) emitted. Approvals are answered by approving:
# they sit at P2 so they never crowd out the P0/P1 problems that block payment.
FAMILY_SEVERITY: dict[str, str] = {"approval": "MEDIUM"}


def policy_severity(draft: FindingDraft) -> str:
    """The severity a finding is stored at: the family's fixed level, if it has one."""
    return FAMILY_SEVERITY.get(draft.family, draft.severity)


def _jsonb(value) -> str:
    return json.dumps(value, default=str)


async def upsert_finding(
    conn: asyncpg.Connection,
    draft: FindingDraft,
    *,
    tenant_money_cap=None,
    now: datetime | None = None,
) -> tuple[str, bool]:
    """Insert or refresh one finding. Returns (finding_id, was_inserted)."""
    now = now or datetime.now(timezone.utc)
    severity = policy_severity(draft)
    score = rank_score(
        severity=severity,
        confidence=draft.confidence,
        verification=draft.verification,
        money_at_risk_inr=draft.money_at_risk_inr,
        due_at=draft.due_at,
        first_detected_at=now,
        tenant_money_cap=tenant_money_cap,
        now=now,
    )

    row = await conn.fetchrow(
        """
        INSERT INTO rh_findings (
            tenant_id, finding_key, finding_type, family, type_id,
            subject_type, subject_id,
            severity, confidence, verification,
            money_at_risk, money_currency, money_at_risk_inr, due_at, rank_score,
            title, detail, evidence_jsonb, actions_jsonb, deep_link,
            audience_roles, audience_users, audience_departments, required_capability,
            source_service, detector, detector_version, agent_run_id, model, cost_usd,
            condition_hash, first_detected_at, last_seen_at
        ) VALUES (
            $1::uuid, $2, $3, $4, $5::uuid,
            $6, $7::uuid,
            $8::severity_level, $9, $10,
            $11, $12, $13, $14, $15,
            $16, $17, $18::jsonb, $19::jsonb, $20,
            $21::text[], $22::text[], $23::text[], $24,
            $25, $26, $27, $28::uuid, $29, $30,
            $31, $32, $32
        )
        ON CONFLICT (tenant_id, finding_key) WHERE resolved_at IS NULL
        DO UPDATE SET
            severity          = EXCLUDED.severity,
            confidence        = EXCLUDED.confidence,
            verification      = EXCLUDED.verification,
            money_at_risk     = EXCLUDED.money_at_risk,
            money_at_risk_inr = EXCLUDED.money_at_risk_inr,
            money_currency    = EXCLUDED.money_currency,
            due_at            = EXCLUDED.due_at,
            rank_score        = EXCLUDED.rank_score,
            title             = EXCLUDED.title,
            detail            = EXCLUDED.detail,
            evidence_jsonb    = EXCLUDED.evidence_jsonb,
            actions_jsonb     = EXCLUDED.actions_jsonb,
            deep_link         = EXCLUDED.deep_link,
            audience_roles    = EXCLUDED.audience_roles,
            audience_users    = EXCLUDED.audience_users,
            audience_departments = EXCLUDED.audience_departments,
            type_id           = COALESCE(EXCLUDED.type_id, rh_findings.type_id),
            last_seen_at      = EXCLUDED.last_seen_at,
            updated_at        = now(),
            -- Same problem, new facts => count it and pull it back into view.
            -- Same problem, unchanged => leave the user's acknowledgement alone,
            -- or every pass would un-acknowledge everything they triaged.
            occurrence_count  = rh_findings.occurrence_count
                                + (rh_findings.condition_hash
                                   IS DISTINCT FROM EXCLUDED.condition_hash)::int,
            status            = CASE
                                  WHEN rh_findings.condition_hash
                                       IS DISTINCT FROM EXCLUDED.condition_hash
                                   AND rh_findings.status = 'ACKNOWLEDGED'
                                  THEN 'OPEN' ELSE rh_findings.status END,
            condition_hash    = EXCLUDED.condition_hash
        RETURNING finding_id, (xmax = 0) AS inserted
        """,
        draft.tenant_id, draft.finding_key, draft.finding_type, draft.family, draft.type_id,
        draft.subject_type, draft.subject_id,
        severity, draft.confidence, draft.verification,
        draft.money_at_risk, draft.money_currency, draft.money_at_risk_inr, draft.due_at, score,
        draft.title, draft.detail, _jsonb(draft.evidence), _jsonb(draft.actions), draft.deep_link,
        list(draft.audience_roles), list(draft.audience_users), list(draft.audience_departments),
        draft.required_capability,
        draft.source_service, draft.detector, draft.detector_version, draft.agent_run_id,
        draft.model, draft.cost_usd,
        draft.condition_hash, now,
    )

    finding_id = str(row["finding_id"])
    inserted = bool(row["inserted"])

    if draft.entities:
        await conn.executemany(
            """
            INSERT INTO rh_finding_entities
                (finding_id, tenant_id, entity_type, entity_id, entity_role, label)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6)
            ON CONFLICT (finding_id, entity_type, entity_id) DO UPDATE
                SET entity_role = EXCLUDED.entity_role, label = EXCLUDED.label
            """,
            [
                (finding_id, draft.tenant_id, et, eid, role, label)
                for et, eid, role, label in draft.entities
            ],
        )

    if inserted:
        await record_event(
            conn, finding_id, draft.tenant_id, "DETECTED",
            actor=f"system:{draft.detector}",
        )

    return finding_id, inserted


async def sweep_autoresolve(
    conn: asyncpg.Connection,
    tenant_id: str,
    detector: str,
    pass_started_at: datetime,
) -> int:
    """Close findings this detector stopped asserting during a complete pass.

    A human's resolution is never touched: resolved and dismissed findings
    already carry resolved_at and are outside this UPDATE. A snooze or an
    acknowledgement is not a resolution, though — it is "remind me later" — so
    a finding whose condition has gone is closed even if someone snoozed it.
    Guarding on `status_by IS NULL` left every snoozed false positive open
    forever once its cause was fixed.
    """
    rows = await conn.fetch(
        """
        UPDATE rh_findings
           SET status        = 'AUTO_RESOLVED',
               resolved_at   = now(),
               status_at     = now(),
               status_reason = 'condition no longer present',
               updated_at    = now()
         WHERE tenant_id    = $1::uuid
           AND detector     = $2
           AND resolved_at IS NULL
           AND (status_by IS NULL OR status IN ('OPEN', 'SNOOZED', 'ACKNOWLEDGED'))
           AND last_seen_at < $3
        RETURNING finding_id
        """,
        tenant_id, detector, pass_started_at,
    )
    for r in rows:
        await record_event(
            conn, str(r["finding_id"]), tenant_id, "AUTO_RESOLVED",
            actor=f"system:{detector}", reason="condition no longer present",
        )
    return len(rows)


async def apply_suppression(
    conn: asyncpg.Connection, draft: FindingDraft
) -> FindingDraft | None:
    """Honour "don't tell me again". Returns None if the draft should be dropped.

    Checked at write time as well as query time: a tenant-wide mute should stop
    the row existing, not just stop it rendering, or counts stay wrong and the
    "N more you can see" line becomes a lie.
    """
    row = await conn.fetchrow(
        """
        SELECT scope FROM rh_suppressions
         WHERE tenant_id = $1::uuid
           AND (until IS NULL OR until > now())
           AND (
                 (scope = 'exact_key'        AND finding_key = $2)
              OR (scope = 'type_on_subject'  AND finding_type = $3 AND subject_id = $4::uuid)
              OR (scope = 'type_tenant_wide' AND finding_type = $3)
           )
         LIMIT 1
        """,
        draft.tenant_id, draft.finding_key, draft.finding_type, draft.subject_id,
    )
    return None if row else draft


async def record_event(
    conn: asyncpg.Connection,
    finding_id: str,
    tenant_id: str,
    event_type: str,
    *,
    actor: str | None = None,
    reason: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append to the finding's timeline. Never raises — an audit write must not
    fail the operation it is describing (same contract as BaseWorker.log_audit)."""
    try:
        await conn.execute(
            """
            INSERT INTO rh_finding_events
                (finding_id, tenant_id, event_type, actor, reason, payload_jsonb)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
            """,
            finding_id, tenant_id, event_type, actor, reason, _jsonb(payload or {}),
        )
    except Exception as exc:
        logger.warning("[review] event write failed for %s/%s: %s", finding_id, event_type, exc)


async def set_status(
    conn: asyncpg.Connection,
    tenant_id: str,
    finding_id: str,
    status: str,
    *,
    actor: str,
    reason: str | None = None,
    snooze_until: datetime | None = None,
    make_undo_token: bool = True,
) -> tuple[dict | None, str | None]:
    """Move a finding through its lifecycle. Returns (new_row, undo_token).

    RESOLVED / DISMISSED / AUTO_RESOLVED set resolved_at, which is what removes
    the row from every hot index and frees the finding_key for a future
    regression to claim.
    """
    terminal = status in ("RESOLVED", "DISMISSED", "AUTO_RESOLVED")

    prev = await conn.fetchrow(
        "SELECT status, status_reason, status_by, status_at, snoozed_until, resolved_at "
        "FROM rh_findings WHERE tenant_id = $1::uuid AND finding_id = $2::uuid",
        tenant_id, finding_id,
    )
    if prev is None:
        return None, None

    row = await conn.fetchrow(
        """
        UPDATE rh_findings
           SET status        = $3,
               status_reason = $4,
               status_by     = $5,
               status_at     = now(),
               snoozed_until = $6,
               resolved_at   = CASE WHEN $7 THEN now() ELSE NULL END,
               updated_at    = now()
         WHERE tenant_id = $1::uuid AND finding_id = $2::uuid
        RETURNING *
        """,
        tenant_id, finding_id, status, reason, actor, snooze_until, terminal,
    )

    await record_event(
        conn, finding_id, tenant_id, status,
        actor=actor, reason=reason,
    )

    token = None
    if make_undo_token:
        token = secrets.token_urlsafe(18)
        await conn.execute(
            """
            INSERT INTO rh_undo_tokens (token, tenant_id, finding_id, prev_state, created_by, expires_at)
            VALUES ($1, $2::uuid, $3::uuid, $4::jsonb, $5, now() + ($6 || ' seconds')::interval)
            """,
            token, tenant_id, finding_id, _jsonb(dict(prev)), actor, str(UNDO_TTL_SECONDS),
        )

    return dict(row), token


async def undo(
    conn: asyncpg.Connection, tenant_id: str, token: str, actor: str
) -> dict | None:
    """Restore the pre-action state. Optimistic-only undo is not enough at triage
    speed: a user resolves eight findings in ten seconds and needs the third back
    after it has already committed."""
    tok = await conn.fetchrow(
        """
        UPDATE rh_undo_tokens SET used_at = now()
         WHERE token = $1 AND tenant_id = $2::uuid
           AND used_at IS NULL AND expires_at > now()
        RETURNING finding_id, prev_state
        """,
        token, tenant_id,
    )
    if tok is None:
        return None

    prev = json.loads(tok["prev_state"]) if isinstance(tok["prev_state"], str) else tok["prev_state"]
    row = await conn.fetchrow(
        """
        UPDATE rh_findings
           SET status        = $3,
               status_reason = $4,
               status_by     = $5,
               status_at     = $6::timestamptz,
               snoozed_until = $7::timestamptz,
               resolved_at   = $8::timestamptz,
               updated_at    = now()
         WHERE tenant_id = $1::uuid AND finding_id = $2::uuid
        RETURNING *
        """,
        tenant_id, str(tok["finding_id"]),
        prev.get("status") or "OPEN", prev.get("status_reason"), prev.get("status_by"),
        prev.get("status_at"), prev.get("snoozed_until"), prev.get("resolved_at"),
    )
    await record_event(
        conn, str(tok["finding_id"]), tenant_id, "COMMENT",
        actor=actor, reason="undo",
    )
    return dict(row) if row else None
