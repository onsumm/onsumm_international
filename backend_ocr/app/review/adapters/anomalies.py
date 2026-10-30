"""ai_anomalies — the table with seven working detectors and, until now, no readers.

Written by pipelines/deduplication.py:_upsert_anomaly. Covers duplicate bills and
invoices (exact and soft), duplicate vendors (matched on a shared bank account,
which is the strong signal, or on an identical name with different GSTINs), and
payment failures on both the AP and AR sides.
"""
from __future__ import annotations

from decimal import Decimal

from .. import links
from ..routing import data_audience, payment_audience, vendor_audience
from ..types import (
    FindingDraft, condition_hash, fields_evidence, jsonb, one_line, table_evidence,
)
from .base import FindingAdapter

# anomaly_type -> (finding_type, family, severity floor, subject_type)
_MAP: dict[str, tuple[str, str, str]] = {
    "DUPLICATE_BILL":         ("duplicate_bill_exact",   "duplication", "bill"),
    "SOFT_DUPLICATE_BILL":    ("duplicate_bill_likely",  "duplication", "bill"),
    "DUPLICATE_INVOICE":      ("duplicate_invoice_exact", "duplication", "invoice"),
    "SOFT_DUPLICATE_INVOICE": ("duplicate_invoice_likely", "duplication", "invoice"),
    "DUPLICATE_VENDOR":       ("duplicate_vendor",       "duplication", "vendor"),
    "PAYMENT_FAILURE_AP":     ("payment_failed_ap",      "payment",     "payment"),
    "PAYMENT_FAILURE_AR":     ("payment_failed_ar",      "payment",     "payment"),
}

_TITLES = {
    "duplicate_bill_exact":     "Duplicate bill — same vendor, number, date and amount",
    "duplicate_bill_likely":    "Probable duplicate — same vendor and amount within a week",
    "duplicate_invoice_exact":  "Duplicate invoice — same customer, number, date and amount",
    "duplicate_invoice_likely": "Probable duplicate invoice — same customer and amount within a week",
    "duplicate_vendor":         "Two vendor records look like the same company",
    "payment_failed_ap":        "Payment to this vendor failed and has not been retried",
    "payment_failed_ar":        "Incoming payment failed and has not been retried",
}


class AnomaliesAdapter(FindingAdapter):
    detector = "adapter:ai_anomalies"
    family = "duplication"
    source_service = "anomaly_ai"

    async def scan(self, conn, tenant_id: str, limit: int):
        rows = await conn.fetch(
            """
            SELECT a.anomaly_id, a.entity_type::text AS entity_type, a.entity_id,
                   a.anomaly_type, a.severity::text AS severity,
                   a.description, a.features_jsonb, a.detected_at,
                   fi.invoice_number, fi.total_amount, fi.supplier_id, fi.due_date,
                   s.name AS supplier_name,
                   -- Whether the bill an anomaly names still exists — as a bill, or
                   -- held in staging (a duplicate blocked from promotion).
                   (fi.invoice_id IS NOT NULL
                    OR EXISTS (SELECT 1 FROM stg_bills sb
                                WHERE sb.stg_bill_id = a.entity_id
                                  AND sb.tenant_id   = a.tenant_id)) AS bill_exists
              FROM ai_anomalies a
              LEFT JOIN fact_invoices fi ON fi.invoice_id = a.entity_id
                                        AND fi.tenant_id  = a.tenant_id
              LEFT JOIN dim_suppliers s  ON s.supplier_id = COALESCE(fi.supplier_id, a.entity_id)
                                        AND s.tenant_id   = a.tenant_id
             WHERE a.tenant_id   = $1::uuid
               AND a.is_resolved = false
             ORDER BY a.detected_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            mapped = _MAP.get(r["anomaly_type"])
            if not mapped:
                # An anomaly type we have no mapping for is still a real signal —
                # surface it generically rather than dropping it on the floor.
                finding_type = r["anomaly_type"].lower()
                family, subject_type = "data_quality", "bill"
            else:
                finding_type, family, subject_type = mapped

            # A bill deleted or purged since the anomaly was recorded leaves
            # nothing to look at; a finding pointing at it cannot be found.
            if subject_type == "bill" and not r.get("bill_exists", True):
                continue

            features = jsonb(r["features_jsonb"], default={}) or {}
            is_vendor = subject_type == "vendor"
            is_payment = subject_type == "payment"

            if is_vendor:
                audience = vendor_audience()
                deep_link = links.vendor(str(r["entity_id"]), tab="details", focus="pan_duplicate")
            elif is_payment:
                audience = payment_audience()
                deep_link = links.bills_list()
            else:
                audience = data_audience()
                deep_link = links.bill(str(r["entity_id"]), focus="total_amount")

            money = r["total_amount"]
            title = _TITLES.get(finding_type,
                                one_line(r["description"], 80)
                                or finding_type.replace("_", " "))

            if is_vendor:
                evidence = fields_evidence(
                    [
                        ("Matched on", features.get("match_signal", "unknown")),
                        ("Other vendor", features.get("duplicate_of_supplier_id", "—")),
                        ("This vendor", r["supplier_name"] or str(r["entity_id"])),
                    ],
                    note="Vendors are never merged automatically — this is a flag, not an action.",
                )
            else:
                evidence = table_evidence(
                    ["Field", "Value"],
                    [[k, str(v)] for k, v in list(features.items())[:12]],
                    note=r["description"] or "",
                )

            entities = [("bill" if not is_vendor else "vendor", str(r["entity_id"]), "subject", r["invoice_number"] or r["supplier_name"])]
            if r["supplier_id"] and not is_vendor:
                entities.append(("vendor", str(r["supplier_id"]), "counterparty", r["supplier_name"]))

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type=finding_type,
                family=family,
                subject_type=subject_type,
                subject_id=str(r["entity_id"]),
                severity=r["severity"],
                title=title,
                # Upstream prose — first sentence here, the whole thing in evidence.
                detail=one_line(r["description"]),
                deep_link=deep_link,
                condition_hash=condition_hash(r["anomaly_type"], r["severity"], sorted(features.items()) if features else None),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=money,
                money_at_risk_inr=money,
                due_at=r["due_date"],
                evidence=evidence,
                entities=tuple(entities),
                audience_roles=audience.roles,
                required_capability=audience.capability,
                actions=[],
            )
