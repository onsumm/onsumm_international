"""Failed payments and disbursements that nobody has picked back up."""
from __future__ import annotations

from .. import links
from ..routing import payment_audience
from ..types import FindingDraft, action, condition_hash, fields_evidence
from .base import FindingAdapter


class PaymentFailuresAdapter(FindingAdapter):
    detector = "adapter:payment_failures"
    family = "payment"
    source_service = "disbursement"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = payment_audience()

        rows = await conn.fetch(
            """
            SELECT d.disbursement_id, d.invoice_id, d.supplier_id, d.amount,
                   d.status, d.status_message, d.act_code, d.beneficiary_name,
                   d.beneficiary_account, d.is_overridden, d.created_at, d.initiated_by,
                   fi.invoice_number, fi.due_date,
                   s.name AS supplier_name
              FROM fact_disbursements d
              LEFT JOIN fact_invoices fi ON fi.invoice_id = d.invoice_id
                                        AND fi.tenant_id  = d.tenant_id
              LEFT JOIN dim_suppliers s ON s.supplier_id = d.supplier_id
                                       AND s.tenant_id   = d.tenant_id
             WHERE d.tenant_id = $1::uuid
               AND d.status IN ('FAILED', 'REJECTED', 'REVERSED', 'CANCELLED')
               AND d.created_at > now() - interval '60 days'
             ORDER BY d.amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            did = str(r["disbursement_id"])
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="disbursement_failed",
                family=self.family,
                subject_type="disbursement",
                subject_id=did,
                severity="HIGH",
                title="Payment to this vendor failed and the bill is still unpaid",
                detail=r["status_message"] or f"The bank returned {r['status']}.",
                deep_link=links.pay(str(r["supplier_id"]), str(r["invoice_id"]) if r["invoice_id"] else None)
                          if r["supplier_id"] else links.bills_list(),
                condition_hash=condition_hash(r["status"], r["act_code"], r["amount"]),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["amount"],
                money_at_risk_inr=r["amount"],
                due_at=r["due_date"],
                evidence=fields_evidence([
                    ("Bank status", r["status"]),
                    ("Reason", r["status_message"] or "—"),
                    ("Response code", r["act_code"] or "—"),
                    ("Beneficiary", r["beneficiary_name"] or "—"),
                    ("Bank details were overridden", "yes" if r["is_overridden"] else "no"),
                    ("Attempted by", r.get("initiated_by") or "—"),
                    ("Attempted at", r["created_at"].isoformat() if r["created_at"] else "—"),
                ]),
                entities=tuple(e for e in [
                    ("disbursement", did, "subject", None),
                    ("bill", str(r["invoice_id"]), "related", r["invoice_number"]) if r["invoice_id"] else None,
                    ("vendor", str(r["supplier_id"]), "counterparty", r["supplier_name"]) if r["supplier_id"] else None,
                ] if e),
                audience_roles=aud.roles,
                required_capability="pay",
                # Retrying a payment moves money, so it is a deep link into the
                # flow that already has the OTP gate and the audit trail — never
                # a button in a triage queue.
                actions=[action("open_payment", "Open the payment", kind="navigate",
                                href=links.pay(str(r["supplier_id"]), str(r["invoice_id"]) if r["invoice_id"] else None))]
                        if r["supplier_id"] else [],
            )

        txn = await conn.fetch(
            """
            SELECT t.txn_id, t.paid_amount, t.status::text AS status, t.failure_reason,
                   t.payment_date, t.reference_number, t.schedule_id
              FROM payment_transactions t
             WHERE t.tenant_id = $1::uuid
               AND t.status::text IN ('FAILED', 'BOUNCED', 'REVERSED', 'CANCELLED')
               AND t.is_reconciled = false
               AND t.created_at > now() - interval '60 days'
             ORDER BY t.paid_amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in txn:
            tid = str(r["txn_id"])
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="payment_transaction_failed_unreconciled",
                family=self.family,
                subject_type="payment",
                subject_id=tid,
                severity="MEDIUM",
                title="Failed payment has not been reconciled",
                detail=r["failure_reason"] or f"Transaction is {r['status']} and still unreconciled.",
                deep_link=links.bills_list(),
                condition_hash=condition_hash(r["status"], r["failure_reason"], r["paid_amount"]),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["paid_amount"],
                money_at_risk_inr=r["paid_amount"],
                evidence=fields_evidence([
                    ("Status", r["status"]),
                    ("Reason", r["failure_reason"] or "—"),
                    ("Reference", r["reference_number"] or "—"),
                    ("Payment date", str(r["payment_date"] or "—")),
                ]),
                entities=(("payment", tid, "subject", r["reference_number"]),),
                audience_roles=aud.roles,
                required_capability="pay",
            )
