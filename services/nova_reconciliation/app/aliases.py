"""
Module-slug -> canonical-service resolution — NOVA_RECONCILIATION_LOGIC.md §3.

No hardcoded slug->service table. lendix/nova_vendor_mapping.json is sample/example
data whose vendors and module names can change, so resolution is computed at request
time from whatever that mapping currently contains — its `vendors{vendor: [service, ...]}`
index (NOVA_ENTITY_BRAND_TENANT_VENDOR_MAP.json's derived catalog) is the candidate
pool, scored by string similarity against the incoming module slug.

DB-persisted corrections (nova_reconciliation_module_aliases) still win outright when
present — that's an operator fixing a specific bad auto-resolution at runtime, not
data baked into this module.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

# Below this, a "best" candidate is more likely noise than a real match — resolution
# reports UNRESOLVED_MODULE rather than guess (NOVA_RECONCILIATION_LOGIC.md §4 step 4).
# Nova's own module slugs are short and clean (all 8 known cases score >= 0.57, most
# via the containment path >= 0.75); free-text invoice descriptions are noisier — an
# unrelated line item can drift into the low 0.3s on shared-letter coincidence alone
# (verified: "Some random consulting fee" scored 0.356 against "Geocoder Address
# Matcher"). 0.45 clears every known-good case with room while rejecting that one.
MIN_SCORE = 0.45


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _score(module_slug: str, service: str) -> float:
    """1.0 = exact match ignoring case/punctuation. A slug fully contained in (or
    containing) a service name scores in [0.75, 1.0], weighted by how much of the
    longer string it covers — so 'pan' vs 'PAN OCR' outranks 'pan' vs 'PAN Premium
    API' without a fixed lookup for either. Anything else falls back to
    difflib's sequence-similarity ratio."""
    a, b = _normalize(module_slug), _normalize(service)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        return 0.75 + 0.25 * (len(shorter) / len(longer))
    return difflib.SequenceMatcher(None, a, b).ratio()


def resolve_dynamic(mapping: dict, vendor: str, module_slug: str) -> Optional[str]:
    """Best-scoring canonical service for this vendor, from the mapping JSON's own
    vendor -> services index — None when nothing clears MIN_SCORE."""
    candidates = mapping.get("vendors", {}).get(vendor, [])
    if not candidates:
        return None
    best_service, best_score = None, 0.0
    for service in candidates:
        s = _score(module_slug, service)
        if s > best_score:
            best_service, best_score = service, s
    return best_service if best_score >= MIN_SCORE else None


def resolve_from_db(conn, vendor: str, module_slug: str) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT canonical_service FROM nova_reconciliation_module_aliases "
            "WHERE vendor = %s AND module_slug = %s",
            (vendor, module_slug),
        )
        row = cur.fetchone()
        return row[0] if row else None


def resolve(conn, mapping: dict, vendor: str, module_slug: str) -> Optional[str]:
    """A persisted DB correction wins outright; otherwise resolve dynamically
    against the current mapping JSON."""
    db_hit = resolve_from_db(conn, vendor, module_slug)
    if db_hit:
        return db_hit
    return resolve_dynamic(mapping, vendor, module_slug)
