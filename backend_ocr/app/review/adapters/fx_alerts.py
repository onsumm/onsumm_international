"""FX rate alerts and agent notes.

fx_rate_alerts already has a list-and-dismiss API (backend_ocr/app/routers/fx.py),
and the frontend already fetches it on the Dashboard — and then never renders it.
Mirroring into the hub gives that dead data a reader. The FX router keeps its own
dismiss endpoint; a dismissed alert simply stops being asserted here.
"""
from __future__ import annotations

from .. import links
from ..routing import data_audience
from ..types import (
    FindingDraft, action, condition_hash, fields_evidence, one_line, text_evidence,
)
from .base import FindingAdapter


_HEADLINES = {
    "delay_payment":         "{ccy} is expensive right now — paying later may cost less",
    "collect_now_opportunity": "{ccy} is strong right now — a good moment to collect",
    "pay_now_opportunity":   "{ccy} is cheap right now — a good moment to pay",
    "threshold_breach":      "{ccy} moved past your threshold",
}


class FxAlertsAdapter(FindingAdapter):
    detector = "adapter:fx_alerts"
    family = "fx"
    source_service = "fx"

    async def scan(self, conn, tenant_id: str, limit: int):
        aud = data_audience()

        rows = await conn.fetch(
            """
            SELECT alert_id, alert_type, currency_code, rate_date, rate_inr,
                   threshold_inr, direction, recommendation,
                   exposure_inr, exposure_foreign, created_at
              FROM fx_rate_alerts
             WHERE tenant_id = $1::uuid AND status = 'ACTIVE'
             ORDER BY exposure_inr DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            aid = str(r["alert_id"])
            # Two alert types previously shared one title, so the grouped queue
            # showed the same sentence twice with different counts. The title has
            # to say which kind it is, because the card is all most users read.
            atype = (r["alert_type"] or "alert").lower()
            headline = _HEADLINES.get(atype, "{ccy} moved past your threshold")

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type=f"fx_{atype}",
                family=self.family,
                subject_type="tenant",
                subject_id=None,
                severity="MEDIUM",
                title=headline.format(ccy=(r["currency_code"] or "").strip()),
                detail=one_line(r["recommendation"]),
                deep_link=links.settings("fx", focus="fx.rates"),
                condition_hash=condition_hash(r["alert_type"], r["currency_code"], r["rate_inr"]),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["exposure_inr"],
                money_at_risk_inr=r["exposure_inr"],
                evidence=fields_evidence([
                    ("Currency", r["currency_code"]),
                    ("Rate", str(r["rate_inr"] or "—")),
                    ("Your threshold", str(r["threshold_inr"] or "—")),
                    ("Direction", r["direction"] or "—"),
                    ("Exposure", str(r["exposure_inr"] or "—")),
                ]),
                key_qualifier=aid[:8],
                audience_roles=aud.roles,
                required_capability="edit",
                actions=[
                    action("dismiss_fx_alert", "Dismiss this alert", kind="safe",
                           endpoint=f"/api/fx/alerts/{aid}/dismiss", capability="edit",
                           preview="Dismisses the alert in FX settings as well as here."),
                ],
            )

        notes = await conn.fetch(
            """
            SELECT n.target_type, n.target_id, n.severity, n.note_text, n.confidence,
                   fi.invoice_number, fi.total_amount, fi.due_date
              FROM fx_agent_notes n
              LEFT JOIN fact_invoices fi ON fi.invoice_id = n.target_id
                                        AND fi.tenant_id  = n.tenant_id
             WHERE n.tenant_id = $1::uuid
               AND n.is_current
               AND n.severity = 'WARNING'
             ORDER BY fi.total_amount DESC NULLS LAST
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in notes:
            target = str(r["target_id"])
            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="fx_agent_warning",
                family=self.family,
                subject_type="bill" if r["target_type"] == "bill" else "tenant",
                subject_id=target,
                severity="MEDIUM",
                title="Currency exposure worth a look on this bill",
                detail=one_line(r["note_text"]),
                deep_link=links.bill(target, focus="fx"),
                condition_hash=condition_hash("fx_note", r["note_text"]),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=r["total_amount"],
                due_at=r["due_date"],
                # An advisory note is a judgement, not a fact we recomputed.
                verification="UNVERIFIABLE",
                confidence=min(float(r["confidence"] or 0.5), 0.5),
                # The full note stays here even though the line above is trimmed.
                evidence=text_evidence(r["note_text"] or ""),
                entities=(("bill", target, "subject", r["invoice_number"]),),
                audience_roles=aud.roles,
                required_capability="edit",
            )
