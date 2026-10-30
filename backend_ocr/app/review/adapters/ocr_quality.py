"""Things the extraction probably got wrong, reported as suspicions rather than faults.

The distinction matters. A line item carrying an amount but no quantity and no
rate is *usually fine* — plenty of real invoices bill a lump sum, and the Kashti
referral commission bills in this tenant are exactly that. But it is also what an
OCR misread looks like when the qty and rate columns were dropped.

So this is deliberately a suspicion, never a blocker: it publishes as
UNVERIFIABLE, which routes it to the Suspected lane, keeps it out of the badge
counts, and makes its primary affordance "open and check" rather than a one-click
write. Reporting it as a hard finding would be worse than not reporting it —
there are 102 such lines in one tenant alone, and blocking on all of them would
bury everything that actually matters.

One finding per bill, not per line, for the same reason.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import (
    FindingDraft, action, condition_hash, document_ref, jsonb, table_evidence,
)
from .base import FindingAdapter


class OcrQualityAdapter(FindingAdapter):
    detector = "adapter:ocr_quality"
    family = "data_quality"
    source_service = "upload_ocr"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = data_audience()

        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.invoice_number, fi.total_amount, fi.due_date,
                   fi.supplier_id, fi.upload_id, s.name AS supplier_name,
                   count(*)                              AS bare_lines,
                   sum(li.line_total)                    AS bare_total,
                   json_agg(json_build_object(
                       'line', li.line_number,
                       'description', left(replace(li.product_name, E'\\n', ' '), 70),
                       'total', li.line_total)
                       ORDER BY li.line_number)           AS lines
              FROM stg_bill_line_items li
              JOIN stg_bills b   ON b.stg_bill_id = li.stg_bill_id
              JOIN fact_invoices fi ON fi.stg_bill_id = b.stg_bill_id
                                    AND fi.tenant_id  = b.tenant_id
              LEFT JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
                                       AND s.tenant_id   = fi.tenant_id
             WHERE b.tenant_id = $1::uuid
               AND fi.soft_status = 'active'
               AND li.line_total IS NOT NULL
               AND li.quantity   IS NULL
               AND li.unit_price IS NULL
             GROUP BY fi.invoice_id, fi.invoice_number, fi.total_amount, fi.due_date,
                      fi.supplier_id, fi.upload_id, s.name
             ORDER BY fi.total_amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            iid = str(r["invoice_id"])
            n = int(r["bare_lines"] or 0)
            lines = jsonb(r["lines"], default=[]) or []

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="line_amount_without_qty_or_rate",
                family=self.family,
                subject_type="bill",
                subject_id=iid,
                severity="LOW",
                title="Line items have an amount but no quantity or rate",
                detail=(
                    f"{n} line{'s' if n != 1 else ''} {'have' if n != 1 else 'has'} a total "
                    "but no quantity or rate, so the arithmetic cannot be checked."
                ),
                deep_link=links.bill(iid, focus="line_items"),
                condition_hash=condition_hash("bare_lines", n, r["bare_total"]),
                detector=self.detector,
                source_service=self.source_service,
                # Suspicion, not fact: it lands in the Suspected lane, stays out of
                # the badge count, and never offers a one-click write.
                verification="UNVERIFIABLE",
                confidence=0.35,
                # No money at risk — nothing here says the amount is wrong, only
                # that it cannot be recomputed.
                money_at_risk=None,
                due_at=r["due_date"],
                evidence=table_evidence(
                    ["#", "Description", "Amount"],
                    [[str(l.get("line") or "—"), l.get("description") or "—",
                      str(l.get("total") or "—")] for l in (lines or [])[:12]],
                    note="If those columns are on the document beside this, the "
                         "extraction dropped them.",
                    document=document_ref(
                        r["upload_id"], label=r["invoice_number"] or "the bill",
                    ),
                ),
                entities=tuple(e for e in [
                    ("bill", iid, "subject", r["invoice_number"]),
                    ("vendor", str(r["supplier_id"]), "counterparty", r["supplier_name"])
                    if r["supplier_id"] else None,
                ] if e),
                audience_roles=aud.roles,
                required_capability="edit",
                actions=[action("open_lines", "Open & check the line items", kind="navigate",
                                href=links.bill(iid, focus="line_items"))],
            )
