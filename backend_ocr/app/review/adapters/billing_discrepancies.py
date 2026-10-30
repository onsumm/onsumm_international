"""billing_reconciliation_discrepancies — line-item reconciliation against Nova usage.

This table is the closest thing the platform already had to a findings store: a
typed discrepancy, a severity, a variance amount and a full OPEN / ACKNOWLEDGED /
RESOLVED / DISPUTED_WITH_VENDOR / IGNORED lifecycle. We mirror only the OPEN
rows; its own lifecycle stays authoritative, and a row moving off OPEN simply
stops being asserted, which the sweep turns into an AUTO_RESOLVE.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import (
    FindingDraft, condition_hash, diff_evidence, jsonb, table_evidence,
)
from .base import FindingAdapter

_TITLES = {
    "QTY_MISMATCH":              "Billed quantity does not match measured usage",
    "RATE_MISMATCH":             "Billed rate does not match the contracted rate",
    "COST_MISMATCH":             "Line cost does not match quantity times rate",
    "MISSING_IN_NOVA":           "Billed for a module with no usage recorded",
    "MISSING_IN_INVOICE":        "Usage recorded but never billed",
    "UNMAPPED_LINE_ITEM":        "Line item could not be matched to a known module",
    "ENTITY_UNRESOLVED":         "Could not identify which entity this line belongs to",
    "NOVA_SNAPSHOT_UNAVAILABLE": "Usage data was unavailable when this bill was checked",
    "NOVA_SELF_INCONSISTENT":    "Usage data disagrees with itself for this period",
    "RATE_CHANGED_MOM":          "Rate changed from last month without a contract change",
}


class BillingDiscrepanciesAdapter(FindingAdapter):
    detector = "adapter:billing_discrepancies"
    family = "reconciliation"
    source_service = "nova_billing"

    async def scan(self, conn, tenant_id: str, limit: int):
        rows = await conn.fetch(
            """
            SELECT d.discrepancy_id, d.run_id, d.nova_module,
                   d.discrepancy_type::text AS dtype, d.severity::text AS severity,
                   d.invoice_value, d.nova_value, d.variance_pct, d.variance_amount,
                   d.created_at,
                   r.vendor_invoice_id AS invoice_id, r.vendor_name,
                   fi.invoice_number, fi.supplier_id, fi.due_date
              FROM billing_reconciliation_discrepancies d
              LEFT JOIN billing_reconciliation_runs r ON r.run_id = d.run_id
              LEFT JOIN fact_invoices fi ON fi.invoice_id = r.vendor_invoice_id
                                        AND fi.tenant_id  = d.tenant_id
             WHERE d.tenant_id = $1::uuid
               AND d.status    = 'OPEN'
             ORDER BY d.created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            dtype = r["dtype"]
            invoice_id = r["invoice_id"]
            variance = r["variance_amount"]

            inv_val = jsonb(r["invoice_value"], default={}) or {}
            nova_val = jsonb(r["nova_value"], default={}) or {}
            keys = sorted(set(inv_val) | set(nova_val))
            if keys:
                evidence = diff_evidence(
                    "On the invoice", "Measured usage",
                    [(k.replace("_", " ").title(), str(inv_val.get(k, "—")), str(nova_val.get(k, "—")))
                     for k in keys[:8]],
                )
            else:
                evidence = table_evidence(
                    ["Module", "Variance", "Variance %"],
                    [[r["nova_module"] or "—", str(variance or "—"), str(r["variance_pct"] or "—")]],
                )

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type=f"recon_{dtype.lower()}",
                family=self.family,
                subject_type="bill",
                subject_id=str(invoice_id) if invoice_id else None,
                severity=r["severity"],
                title=_TITLES.get(dtype, dtype.replace("_", " ").capitalize()),
                detail=(
                    f"Module {r['nova_module']}: invoice and usage differ by "
                    f"{r['variance_pct']}%." if r["variance_pct"] is not None else ""
                ),
                deep_link=links.bill(str(invoice_id), focus="recon") if invoice_id else links.nova(),
                condition_hash=condition_hash(dtype, r["severity"], variance, r["variance_pct"]),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=variance,
                money_at_risk_inr=variance,
                due_at=r["due_date"],
                evidence=evidence,
                # More than one discrepancy can sit on one bill, so the module
                # qualifies the key — otherwise they'd overwrite each other.
                key_qualifier=(r["nova_module"] or str(r["discrepancy_id"])[:8]),
                entities=tuple(
                    e for e in [
                        ("bill", str(invoice_id), "subject", r["invoice_number"]) if invoice_id else None,
                        ("vendor", str(r["supplier_id"]), "counterparty", r["vendor_name"]) if r["supplier_id"] else None,
                    ] if e
                ),
                audience_roles=data_audience().roles,
                required_capability="edit",
            )
