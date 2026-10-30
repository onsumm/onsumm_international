"""Bills parked in staging: validation flags, sync errors and low OCR confidence.

The FX case is the interesting one. pipelines/staging_promotion.py:_convert_currency
raises FxRateMissing when no rate exists on or before the bill date, and the
caller parks the bill as validation_flag='REVIEW' with sync_status still PENDING
rather than erroring it. That is a deliberate, sensible choice — and it is also
completely silent, so a foreign-currency bill can sit unpromoted indefinitely
with nobody looking for it.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import FindingDraft, action, condition_hash, fields_evidence
from .base import FindingAdapter, tenant_jurisdiction

LOW_CONFIDENCE = 0.7


class StagingValidationAdapter(FindingAdapter):
    detector = "adapter:staging_validation"
    family = "data_quality"
    source_service = "upload_ocr"

    async def scan(self, conn, tenant_id: str, limit: int):
        # "Foreign" means "not in the tenant's books currency" (INR for an
        # Indian tenant; USD for one that keeps USD books) — core/jurisdiction.py.
        base_ccy = (await tenant_jurisdiction(conn, tenant_id)).base_currency
        rows = await conn.fetch(
            """
            SELECT b.stg_bill_id, b.bill_number, b.bill_date, b.due_date,
                   b.total_amount, b.currency_code, b.original_amount, b.fx_rate_id,
                   b.validation_flag::text AS vflag, b.sync_status::text AS sync_status,
                   b.supplier_id, b.upload_id,
                   -- Extraction confidence is NOT a column on stg_bills, despite
                   -- what docs/full_database_schema.md implies: only stg_invoices
                   -- (the AR side) carries one. For bills it lives in the OCR
                   -- result envelope, written by pipelines/upload_ocr.py.
                   (SELECT (o.confidence_jsonb ->> 'overall')::numeric
                      FROM raw_ocr_results o
                     WHERE o.upload_id = b.upload_id AND o.tenant_id = b.tenant_id
                     ORDER BY o.created_at DESC LIMIT 1) AS ocr_confidence,
                   s.name AS supplier_name,
                   fi.invoice_id
              FROM stg_bills b
              LEFT JOIN dim_suppliers s ON s.supplier_id = b.supplier_id
                                       AND s.tenant_id   = b.tenant_id
              LEFT JOIN fact_invoices fi ON fi.stg_bill_id = b.stg_bill_id
                                        AND fi.tenant_id   = b.tenant_id
             WHERE b.tenant_id = $1::uuid
               AND (b.validation_flag::text IN ('MISMATCH', 'REVIEW')
                 OR b.sync_status::text = 'ERROR'
                 OR EXISTS (
                      SELECT 1 FROM raw_ocr_results o
                       WHERE o.upload_id = b.upload_id AND o.tenant_id = b.tenant_id
                         AND (o.confidence_jsonb ->> 'overall')::numeric < $3))
             ORDER BY b.total_amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit, LOW_CONFIDENCE,
        )

        aud = data_audience()

        for r in rows:
            sid = str(r["stg_bill_id"])
            # Prefer the promoted bill for the deep link — that is the screen a
            # user can actually do something on.
            target = links.bill(str(r["invoice_id"])) if r["invoice_id"] else links.data_extraction()
            foreign = (r["currency_code"] or base_ccy) != base_ccy

            common = dict(
                tenant_id=tenant_id,
                subject_type="bill",
                subject_id=str(r["invoice_id"]) if r["invoice_id"] else sid,
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["total_amount"],
                due_at=r["due_date"],
                money_currency=r["currency_code"] or "INR",
                entities=tuple(e for e in [
                    ("bill", str(r["invoice_id"]), "subject", r["bill_number"]) if r["invoice_id"] else None,
                    ("vendor", str(r["supplier_id"]), "counterparty", r["supplier_name"]) if r["supplier_id"] else None,
                ] if e),
                audience_roles=aud.roles,
                required_capability="edit",
            )

            if foreign and r["fx_rate_id"] is None and r["vflag"] == "REVIEW":
                yield FindingDraft(
                    finding_type="fx_rate_missing_bill_parked",
                    family="fx",
                    severity="HIGH",
                    title="No exchange rate on file, so this bill was never promoted",
                    detail=(
                        f"Parked in staging until a {r['currency_code']} rate exists for "
                        f"{r['bill_date']} — it is in no bill list and no approval chain."
                    ),
                    deep_link=links.settings("fx", focus="fx.rates"),
                    condition_hash=condition_hash("fx_missing", r["currency_code"], r["bill_date"]),
                    evidence=fields_evidence([
                        ("Currency", r["currency_code"]),
                        ("Original amount", str(r["original_amount"] or "—")),
                        ("Bill date", str(r["bill_date"])),
                        ("Vendor", r["supplier_name"] or "—"),
                    ]),
                    actions=[action("open_fx", "Open FX rates", kind="navigate",
                                    href=links.settings("fx", focus="fx.rates"))],
                    **common,
                )
                continue

            if r["sync_status"] == "ERROR":
                yield FindingDraft(
                    finding_type="bill_promotion_failed",
                    family=self.family,
                    severity="HIGH",
                    title="Bill failed to promote out of staging",
                    detail="An error stopped this bill becoming a real bill. It is invisible everywhere else.",
                    deep_link=target,
                    condition_hash=condition_hash("sync_error", r["vflag"]),
                    evidence=fields_evidence([
                        ("Validation flag", r["vflag"]),
                        ("Bill number", r["bill_number"] or "—"),
                        ("Vendor", r["supplier_name"] or "—"),
                    ]),
                    **common,
                )
            elif r["vflag"] == "MISMATCH":
                yield FindingDraft(
                    finding_type="bill_totals_mismatch",
                    family=self.family,
                    severity="MEDIUM",
                    title="Bill totals do not add up",
                    detail="Line items, tax and the stated total disagree.",
                    deep_link=links.bill(str(r["invoice_id"]), focus="line_items") if r["invoice_id"] else target,
                    condition_hash=condition_hash("mismatch", r["total_amount"]),
                    evidence=fields_evidence([
                        ("Stated total", str(r["total_amount"] or "—")),
                        ("Vendor", r["supplier_name"] or "—"),
                    ]),
                    **common,
                )

            if r["ocr_confidence"] is not None and float(r["ocr_confidence"]) < LOW_CONFIDENCE:
                yield FindingDraft(
                    finding_type="extraction_low_confidence",
                    family=self.family,
                    severity="LOW",
                    title="Extraction was uncertain — the figures may be wrong",
                    detail=(
                        f"Extraction confidence {float(r['ocr_confidence']):.0%} — check the "
                        "figures against the document before approving."
                    ),
                    deep_link=target,
                    condition_hash=condition_hash("lowconf", round(float(r["ocr_confidence"]), 2)),
                    evidence=fields_evidence([
                        ("Confidence", f"{float(r['ocr_confidence']):.0%}"),
                        ("Bill number", r["bill_number"] or "—"),
                        ("Total", str(r["total_amount"] or "—")),
                    ]),
                    key_qualifier="ocr",
                    **common,
                )
