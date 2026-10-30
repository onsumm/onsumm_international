"""mapping_review_queue — line items that could not be matched to a catalogue code.

The table has had a writer (scripts/sap_item_match.py) and no API at all since it
was created. This is its first reader.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import FindingDraft, condition_hash, jsonb, table_evidence
from .base import FindingAdapter


class MappingQueueAdapter(FindingAdapter):
    detector = "adapter:mapping_queue"
    family = "data_quality"
    source_service = "sap_matching"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = data_audience()
        rows = await conn.fetch(
            """
            SELECT q.review_id, q.line_item_id, q.query_text, q.candidates, q.created_at,
                   li.stg_bill_id, li.product_name,
                   fi.invoice_id, fi.invoice_number,
                   s.name AS supplier_name
              FROM mapping_review_queue q
              LEFT JOIN stg_bill_line_items li ON li.line_item_id = q.line_item_id
              LEFT JOIN fact_invoices fi ON fi.stg_bill_id = li.stg_bill_id
                                        AND fi.tenant_id   = q.tenant_id
              LEFT JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
                                       AND s.tenant_id   = fi.tenant_id
             WHERE q.tenant_id = $1::uuid
               AND q.status    = 'PENDING'
             ORDER BY q.created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            iid = str(r["invoice_id"]) if r["invoice_id"] else None
            cands = jsonb(r["candidates"], default=[]) or []
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="line_item_unmapped",
                family=self.family,
                subject_type="bill" if iid else "line_item",
                subject_id=iid or str(r["line_item_id"]),
                severity="LOW",
                title="Line item could not be matched to a catalogue item code",
                detail=f"“{r['query_text'] or r['product_name'] or '—'}” has no confident match.",
                deep_link=links.bill(iid, focus="line_items") if iid else links.bills_list(),
                condition_hash=condition_hash("unmapped", r["line_item_id"]),
                detector=self.detector,
                source_service=self.source_service,
                evidence=table_evidence(
                    ["Candidate", "Score"],
                    [[str(c.get("description") or c.get("item_code") or c), str(c.get("score", "—"))]
                     for c in cands[:8]] if isinstance(cands, list) else [],
                    note="Nothing scored high enough to auto-assign.",
                ),
                key_qualifier=str(r["line_item_id"])[:8],
                entities=tuple(e for e in [
                    ("bill", iid, "subject", r["invoice_number"]) if iid else None,
                ] if e),
                audience_roles=aud.roles,
                required_capability="edit",
            )
