"""Assemble everything a reviewer would want to see about one entity.

The highest-leverage file in the agent, and by some distance: **the model cannot
flag what is not in the dossier.** Prompt wording is worth a few percent; a
missing baseline is worth the entire class of findings that depends on it.

Which is why the peer history block below exists. `fact_graph` stores *facts*
("30% due before the 15th"), not *distributions*, and nothing else in the schema
answers "is this rate unusual for this vendor". Without the history section,
"unusual" is literally uncomputable and every comparative claim the model makes
is invention. With it, those claims become checkable — which is what lets the
verifier do its job rather than shrugging.

Everything here is deterministic SQL. No number in a dossier comes from a model.
"""
from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal

import asyncpg

from .redact import Redactor, fence_untrusted

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 12
LINE_LIMIT = 40


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _rows(records) -> list[dict]:
    return [{k: _plain(v) for k, v in dict(r).items()} for r in records]


async def build_bill_dossier(
    conn: asyncpg.Connection, tenant_id: str, invoice_id: str
) -> tuple[dict, Redactor] | None:
    bill = await conn.fetchrow(
        """
        SELECT fi.invoice_id, fi.invoice_number, fi.invoice_date, fi.due_date,
               fi.currency_code, fi.subtotal, fi.tax_amount, fi.total_amount,
               fi.amount_paid, fi.outstanding_amount, fi.status::text AS status,
               fi.payment_status, fi.category, fi.department, fi.original_amount,
               fi.fx_rate_id, fi.verification_flag::text AS verification_flag,
               fi.cost_center_id, fi.supplier_id, fi.stg_bill_id,
               s.name AS vendor_name, s.gstn AS vendor_gstin, s.pan AS vendor_pan,
               s.status AS vendor_status, s.bank_verified, s.bank_account,
               s.bank_ifsc, s.payment_terms_days, s.category AS vendor_category,
               b.gstin_seller, b.validation_flag::text AS validation_flag,
               b.ocr_confidence, b.tds_status, b.tds_amount, b.net_payable
          FROM fact_invoices fi
          LEFT JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
                                   AND s.tenant_id   = fi.tenant_id
          LEFT JOIN stg_bills b ON b.stg_bill_id = fi.stg_bill_id
         WHERE fi.tenant_id = $1::uuid AND fi.invoice_id = $2::uuid
        """,
        tenant_id, invoice_id,
    )
    if not bill:
        return None

    lines = await conn.fetch(
        """
        SELECT line_number, product_name, hsn_code, quantity, unit, unit_price,
               discount_amount, tax_rate_percent, tax_amount, line_total,
               rate_is_percent, service_month
          FROM stg_bill_line_items
         WHERE stg_bill_id = $1::uuid
         ORDER BY line_number LIMIT $2
        """,
        bill["stg_bill_id"], LINE_LIMIT,
    ) if bill["stg_bill_id"] else []

    # The baseline. Everything comparative the model is allowed to say rests on
    # this block, and the verifier can recompute every number in it.
    history = await conn.fetch(
        """
        SELECT invoice_number, invoice_date, total_amount, tax_amount, currency_code,
               category, status::text AS status
          FROM fact_invoices
         WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid
           AND invoice_id <> $3::uuid AND soft_status = 'active'
         ORDER BY invoice_date DESC NULLS LAST LIMIT $4
        """,
        tenant_id, bill["supplier_id"], invoice_id, HISTORY_LIMIT,
    ) if bill["supplier_id"] else []

    stats = await conn.fetchrow(
        """
        SELECT COUNT(*) AS n, AVG(total_amount) AS mean_total,
               MIN(total_amount) AS min_total, MAX(total_amount) AS max_total,
               percentile_disc(0.5) WITHIN GROUP (ORDER BY total_amount) AS median_total
          FROM fact_invoices
         WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid
           AND soft_status = 'active' AND total_amount IS NOT NULL
        """,
        tenant_id, bill["supplier_id"],
    ) if bill["supplier_id"] else None

    chain = await conn.fetch(
        """
        SELECT step, actor_role, actor_name, action, comment, created_at
          FROM bill_approval_chain
         WHERE tenant_id = $1::uuid AND invoice_id = $2::uuid
         ORDER BY created_at DESC LIMIT 20
        """,
        tenant_id, invoice_id,
    )

    existing = await conn.fetch(
        """
        SELECT finding_type, title, severity::text AS severity, detector
          FROM rh_findings
         WHERE tenant_id = $1::uuid AND subject_id = $2::uuid AND resolved_at IS NULL
        """,
        tenant_id, invoice_id,
    )

    policy = await conn.fetch(
        "SELECT step_order, label, role, locked FROM tenant_chain_config "
        " WHERE tenant_id = $1::uuid AND department IS NULL AND is_snapshot = false "
        " ORDER BY step_order",
        tenant_id,
    )

    red = Redactor()

    bill_d = {k: _plain(v) for k, v in dict(bill).items()}
    # Vendor-controlled text is fenced and labelled as data, never as instruction.
    line_rows = _rows(lines)
    for row in line_rows:
        if row.get("product_name"):
            row["product_name"] = fence_untrusted(str(row["product_name"]))

    dossier = {
        "bill": red.scrub_value(bill_d),
        "line_items": red.scrub_value(line_rows),
        "vendor_history": _rows(history),
        "vendor_stats": {k: _plain(v) for k, v in dict(stats).items()} if stats else {},
        "approval_trail": red.scrub_value(_rows(chain)),
        "already_flagged": _rows(existing),
        "tenant_policy": {"approval_chain": _rows(policy)},
        "ids": {"invoice_id": str(invoice_id), "supplier_id": str(bill["supplier_id"])
                if bill["supplier_id"] else None},
    }
    return dossier, red


def dossier_hash(dossier: dict) -> str:
    """Identity of "this entity, in this state".

    Excludes the blocks that move without the entity changing — findings already
    raised on it, and the approval trail — so a re-review is triggered by the
    bill changing, not by our own writes. Getting this wrong is what turns one
    review per bill into fifteen.
    """
    material = {k: v for k, v in dossier.items()
                if k not in ("already_flagged", "approval_trail")}
    blob = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def render(dossier: dict, max_chars: int = 24_000) -> str:
    """The dossier as the model sees it. Truncated rather than silently dropped."""
    text = json.dumps(dossier, indent=1, sort_keys=True, default=str)
    if len(text) <= max_chars:
        return text
    trimmed = dict(dossier)
    trimmed["line_items"] = trimmed.get("line_items", [])[:15]
    trimmed["vendor_history"] = trimmed.get("vendor_history", [])[:6]
    trimmed["_truncated"] = True
    text = json.dumps(trimmed, indent=1, sort_keys=True, default=str)
    return text[:max_chars]
