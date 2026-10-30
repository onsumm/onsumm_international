"""The adapter contract.

**scan() yields the complete current truth for its slice, not a delta.**

That is the single most important property in this package. It is what lets the
worker AUTO_RESOLVE anything a detector stopped asserting, which in turn is what
makes mirroring existing signal into rh_findings safe rather than a staleness
bug waiting to happen.

It also means a pass must stay **bounded**. The CDC triggers are FOR EACH
STATEMENT and staging_promotion / tds_processing / category_enrichment all write
fact_invoices constantly, so this worker is woken often. BaseWorker coalesces a
notify storm into one wakeup, but only because a pass is cheap and idempotent.
If an adapter ever does unbounded work per wakeup, that protection disappears.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import asyncpg

from ...core.jurisdiction import Jurisdiction, build as build_jurisdiction
from ..types import FindingDraft


async def tenant_jurisdiction(conn: asyncpg.Connection, tenant_id: str) -> Jurisdiction:
    """The tenant's jurisdiction (core/jurisdiction.py), read on the adapter's
    own connection. Adapters that raise India-only findings (GSTIN, PAN, IFSC
    bank verification, TDS, MSME) ask it which rule packs apply: scan() yields
    the full current truth, so a finding for a pack that is off is simply not
    yielded, and any earlier one auto-resolves."""
    # A tenant that cannot be read is treated as India — today's behaviour —
    # so a lookup problem can never silently switch an Indian tenant's checks off.
    try:
        row = await conn.fetchrow("SELECT * FROM dim_tenants WHERE tenant_id = $1::uuid", tenant_id)
        return build_jurisdiction(dict(row) if row else None)
    except Exception:
        return build_jurisdiction(None)


class FindingAdapter(ABC):
    #: stable identifier, also the AUTO_RESOLVE sweep scope
    detector: str
    #: the closed family spine this adapter writes into
    family: str
    #: which licensed service the findings belong to, so a tenant without that
    #: licence is not shown them even though the hub itself is core
    source_service: str = "bills"

    @abstractmethod
    def scan(
        self, conn: asyncpg.Connection, tenant_id: str, limit: int
    ) -> AsyncIterator[FindingDraft]:
        """Yield the current full truth for this slice."""
        raise NotImplementedError
