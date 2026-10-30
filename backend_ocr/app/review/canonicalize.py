"""Turn a model's free-text label into a stable type in the tenant's catalogue.

The single most important correction to the obvious design: **the model must
never author the dedup key.** Left to itself it will produce
`gst_name_mismatch`, `gstin-name-mismatch` and
`vendor_name_differs_from_gst_portal` on three consecutive runs over the same
vendor. The identity becomes noise, `occurrence_count` never moves, and the hub
shows one problem three times — strictly worse than a fixed rule catalogue.

So: the model proposes a *claim and a label*. We embed the claim, ANN-match it
against the types this tenant already has, and reuse the existing slug when it is
close enough. Only a genuinely new idea mints a new type. The vocabulary is
therefore open-ended and grows from observation, while `finding_key` stays
deterministic and computed by us.

The same mechanism is how the system stops paying to rediscover things: a type
that already exists carries the verifier plan that confirmed it the first time.
"""
from __future__ import annotations

import logging
import re

import asyncpg

logger = logging.getLogger(__name__)

# Cosine distance under which two claims are "the same kind of problem". Tuned
# conservatively: merging two distinct problems is worse than minting one extra
# type, because a merged type is un-actionable while a duplicate type is merely
# untidy and shows up in the catalogue for a human to fold in.
MATCH_DISTANCE = 0.15

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify(label: str, fallback: str = "agent_finding") -> str:
    slug = _SLUG_RE.sub("_", (label or "").strip().lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)[:60]
    # Must satisfy the CHECK on rh_findings.finding_type.
    if not slug or not slug[0].isalpha() or len(slug) < 3:
        return fallback
    return slug


async def _embed(text: str) -> list[float] | None:
    """Reuse the embedding path the fact graph already proved out.

    Deliberately tolerant: without pgvector, or without a key, canonicalisation
    degrades to slug matching and the hub keeps working.
    """
    try:
        from ..domain.fact_graph import _embed as fg_embed
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, fg_embed, text)
    except Exception as exc:
        logger.debug("[canonicalize] embedding unavailable: %s", exc)
        return None


async def canonicalize_type(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    claim_text: str,
    proposed_label: str,
    family: str,
    severity: str,
) -> tuple[str, str | None, dict]:
    """Returns (type_slug, type_id, existing_verifier_spec)."""
    slug = slugify(proposed_label)
    embedding = await _embed(claim_text)

    if embedding is not None:
        try:
            row = await conn.fetchrow(
                """
                SELECT type_id, type_slug, verifier_spec,
                       centroid <=> $2::vector AS distance
                  FROM rh_finding_types
                 WHERE tenant_id = $1::uuid AND centroid IS NOT NULL
                   AND status <> 'RETIRED'
                 ORDER BY centroid <=> $2::vector
                 LIMIT 1
                """,
                tenant_id, str(embedding),
            )
            if row and float(row["distance"]) < MATCH_DISTANCE:
                await conn.execute(
                    "UPDATE rh_finding_types SET occurrences = occurrences + 1, "
                    "updated_at = now() WHERE type_id = $1",
                    row["type_id"],
                )
                return row["type_slug"], str(row["type_id"]), (row["verifier_spec"] or {})
        except Exception as exc:
            logger.debug("[canonicalize] ANN lookup failed, falling back to slug: %s", exc)

    # Fall back to the slug, which still collapses the common case of the model
    # producing the same label twice.
    row = await conn.fetchrow(
        """
        INSERT INTO rh_finding_types
            (tenant_id, type_slug, display_name, description, family, origin,
             default_severity, centroid, status)
        VALUES ($1::uuid, $2, $3, $4, $5, 'agent', $6::severity_level, $7::vector, 'ACTIVE')
        ON CONFLICT (tenant_id, type_slug) DO UPDATE
            SET occurrences = rh_finding_types.occurrences + 1, updated_at = now()
        RETURNING type_id, type_slug, verifier_spec
        """,
        tenant_id, slug, (proposed_label or slug).replace("_", " ").strip().capitalize(),
        claim_text[:1000], family, severity,
        str(embedding) if embedding is not None else None,
    )
    return row["type_slug"], str(row["type_id"]), (row["verifier_spec"] or {})


async def type_is_suppressed(conn: asyncpg.Connection, tenant_id: str, slug: str) -> bool:
    """A type people keep dismissing stops being shown.

    This is the self-correcting loop, and note that nothing about it involves a
    model: deciding that the model is being noisy is entirely deterministic.
    """
    row = await conn.fetchrow(
        """
        SELECT status, confirmed_count, dismissed_count
          FROM rh_finding_types
         WHERE tenant_id = $1::uuid AND type_slug = $2
        """,
        tenant_id, slug,
    )
    if not row:
        return False
    if row["status"] in ("SUPPRESSED", "RETIRED"):
        return True
    actioned = (row["confirmed_count"] or 0) + (row["dismissed_count"] or 0)
    if actioned >= 20:
        precision = (row["confirmed_count"] or 0) / actioned
        if precision < 0.40:
            await conn.execute(
                "UPDATE rh_finding_types SET status = 'SUPPRESSED', updated_at = now() "
                " WHERE tenant_id = $1::uuid AND type_slug = $2",
                tenant_id, slug,
            )
            logger.info("[review] suppressed %s for tenant %s — precision %.2f over %d",
                        slug, tenant_id, precision, actioned)
            return True
    return False
