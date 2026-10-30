"""Review Hub findings raised by the crypto rail.

The Review Hub's own writer (`review/store.py:upsert_finding`) is asyncpg and
runs in the review workers' loop. The crypto rail raises findings from
psycopg2 code paths — the chain watcher thread, the payout endpoints — so this
module issues the same INSERT ... ON CONFLICT against the same partial unique
index, `(tenant_id, finding_key) WHERE resolved_at IS NULL`. That index is what
gives one live finding per problem: a re-detection while it is open refreshes
it in place; a re-detection after someone resolved it opens a new one.

`finding_key` is `<code>:<entity_type>:<entity_id>`, so the same code on the
same entity never produces a second open finding.

Codes come from docs/crypto-rail/00_DECISIONS.md §2.3. Finding *types* are not
registered: `rh_findings.type_id` is nullable and the Review Hub lanes group by
`family`, which each code maps to below.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# code -> (family, default severity)
FINDING_CODES: dict[str, tuple[str, str]] = {
    "WALLET_MISMATCH": ("vendor_identity", "HIGH"),
    "WALLET_CHANGED": ("vendor_identity", "HIGH"),
    "SCREENING_HIT": ("vendor_identity", "CRITICAL"),
    "OVERBILLED_VS_CONTRACT": ("payment", "HIGH"),
    "DUPLICATE_ACROSS_RAILS": ("duplication", "HIGH"),
    "UNMATCHED_OUTBOUND_TRANSFER": ("reconciliation", "CRITICAL"),
    "UNMATCHED_INBOUND_TRANSFER": ("reconciliation", "MEDIUM"),
    "AMOUNT_MISMATCH": ("reconciliation", "HIGH"),
    "PAYOUT_STUCK": ("payment", "HIGH"),
    "REORG_DETECTED": ("reconciliation", "MEDIUM"),
    "UNRECONCILED_DAY": ("reconciliation", "MEDIUM"),
    "TAX_DOCS_MISSING": ("tax", "MEDIUM"),
    "WITHHOLDING_SIGNOFF_REQUIRED": ("tax", "MEDIUM"),
    "ANCHOR_FAILED": ("data_quality", "MEDIUM"),
    "SAFE_CONFIG_CHANGED": ("payment", "HIGH"),
    "TOKEN_DEPEG": ("fx", "HIGH"),
    "AUDIT_CHAIN_BROKEN": ("data_quality", "CRITICAL"),
}

# Our entity names -> rh_findings.subject_type (a CHECKed vocabulary).
_SUBJECT_TYPES = {
    "bill": "bill", "invoice": "bill", "vendor": "vendor", "supplier": "vendor",
    "wallet": "bank_account", "supplier_wallet": "bank_account", "treasury_wallet": "bank_account",
    "disbursement": "disbursement", "payout": "disbursement",
    "transfer": "payment", "chain_transfer": "payment", "payment": "payment",
    "tenant": "tenant", "anchor": "tenant",
}

_CAPABILITY = {"payment": "pay", "reconciliation": "pay", "vendor_identity": "verify"}


def _deep_link(subject_type: str, entity_id: Optional[str]) -> str:
    if subject_type == "bill" and entity_id:
        return f"/bills/{entity_id}"
    if subject_type == "vendor" and entity_id:
        return f"/vendors/{entity_id}"
    if subject_type in ("disbursement", "payment"):
        return "/payments?tab=onchain"
    return "/review"


def _is_uuid(value: Optional[str]) -> bool:
    import uuid
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def raise_finding(cur, tenant_id: str, code: str, severity: str, entity_type: str, entity_id: str,
                  title: str, details: dict) -> None:
    """Open (or refresh) one Review Hub finding. Never raises into the caller:
    a finding that cannot be filed is logged, the money path carries on."""
    try:
        from ...review.ranking import rank_score
        family, default_sev = FINDING_CODES.get(code, ("reconciliation", "MEDIUM"))
        severity = (severity or default_sev).upper()
        subject_type = _SUBJECT_TYPES.get(entity_type, "tenant")
        subject_id = str(entity_id) if _is_uuid(entity_id) else None
        now = datetime.now(timezone.utc)
        money = details.get("money_at_risk") if isinstance(details, dict) else None
        score = rank_score(severity=severity, confidence=1.0, verification="VERIFIED",
                           money_at_risk_inr=None, due_at=None, first_detected_at=now, now=now)
        evidence = json.dumps(details or {}, default=str)
        condition_hash = hashlib.sha256(f"{code}|{evidence}".encode()).hexdigest()
        cur.execute("SAVEPOINT crypto_finding")
        cur.execute(
            """
            INSERT INTO rh_findings (
                tenant_id, finding_key, finding_type, family, subject_type, subject_id,
                severity, confidence, verification, money_at_risk, money_currency, rank_score,
                title, detail, evidence_jsonb, actions_jsonb, deep_link, required_capability,
                source_service, detector, detector_version, condition_hash, first_detected_at, last_seen_at
            ) VALUES (
                %s::uuid, %s, %s, %s, %s, %s::uuid,
                %s::severity_level, 1.0, 'VERIFIED', %s, %s, %s,
                %s, %s, %s::jsonb, '[]'::jsonb, %s, %s,
                'crypto_payouts', %s, 'v1', %s, %s, %s
            )
            ON CONFLICT (tenant_id, finding_key) WHERE resolved_at IS NULL
            DO UPDATE SET
                severity       = EXCLUDED.severity,
                title          = EXCLUDED.title,
                detail         = EXCLUDED.detail,
                evidence_jsonb = EXCLUDED.evidence_jsonb,
                rank_score     = EXCLUDED.rank_score,
                last_seen_at   = EXCLUDED.last_seen_at,
                updated_at     = now(),
                occurrence_count = rh_findings.occurrence_count
                                   + (rh_findings.condition_hash IS DISTINCT FROM EXCLUDED.condition_hash)::int,
                condition_hash = EXCLUDED.condition_hash
            RETURNING finding_id
            """,
            (
                tenant_id, f"{code.lower()}:{entity_type}:{entity_id}", code.lower(), family, subject_type,
                subject_id, severity, money, (details or {}).get("currency", "USD"), score,
                title, (details or {}).get("detail") or title, evidence, _deep_link(subject_type, subject_id),
                _CAPABILITY.get(family, "view"), f"crypto:{code.lower()}", condition_hash, now, now,
            ),
        )
        finding_id = cur.fetchone()
        finding_id = finding_id["finding_id"] if isinstance(finding_id, dict) else finding_id[0]
        if subject_id:
            cur.execute(
                """INSERT INTO rh_finding_entities (finding_id, tenant_id, entity_type, entity_id, entity_role, label)
                   VALUES (%s, %s, %s, %s, 'subject', %s)
                   ON CONFLICT (finding_id, entity_type, entity_id) DO NOTHING""",
                (finding_id, tenant_id, entity_type, subject_id, title[:120]),
            )
        cur.execute("RELEASE SAVEPOINT crypto_finding")
    except Exception:
        logger.exception("[crypto-findings] could not file %s for %s %s", code, entity_type, entity_id)
        try:
            cur.execute("ROLLBACK TO SAVEPOINT crypto_finding")
        except Exception:
            pass


def list_open(cur, tenant_id: str, entity_type: str, entity_id: str) -> list[dict]:
    """Open crypto findings on one entity, newest first."""
    cur.execute(
        """SELECT finding_id::text AS finding_id, upper(finding_type) AS code, severity::text AS severity,
                  title, evidence_jsonb AS details, status, first_detected_at, last_seen_at
             FROM rh_findings
            WHERE tenant_id = %s AND resolved_at IS NULL AND source_service = 'crypto_payouts'
              AND finding_key LIKE %s
            ORDER BY last_seen_at DESC""",
        (tenant_id, f"%:{entity_type}:{entity_id}"),
    )
    rows = cur.fetchall()
    if rows and not isinstance(rows[0], dict):
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in rows]
    return rows
