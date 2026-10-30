"""Recompute every checkable claim in SQL before a finding reaches a human.

This is the anti-hallucination gate, and the shape of it matters more than the
code. The model is a *hypothesis generator*; this module is the adjudicator.
Money never comes from the model's prose — it comes from the tool that recomputed
it, even when the two agree.

Three verdicts, not two. A two-verdict design ("verified" / "unverified") has no
way to tell "I cannot check this" from "I checked this and it is wrong", and so
produces no hallucination metric at all:

    VERIFIED      every checkable claim was recomputed and matched. Publish.
    UNVERIFIABLE  nothing here is machine-checkable — a qualitative observation.
                  Publish quietly, in the Suspected lane, capped confidence,
                  excluded from badge counts and from money totals.
    REFUTED       a claim was recomputable and DISAGREED. Do not publish. Log it.

That last one is the point. `rh_finding_rejects` accumulates every refuted claim
per model and prompt version, which is the only honest measure of whether the
open-ended approach is working. Without it you are guessing.

A finding with any refuted claim is dropped whole, not partially published: one
wrong number destroys the credibility of everything next to it.

Note the limit, and state it out loud rather than hiding it — the verifier
protects *numbers*, not *relevance*. It cannot refute a claim that is arithmetically
correct and simply not worth anyone's time. That is what the Suspected lane, the
per-type precision threshold and the dismissal reasons are for.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import asyncpg

logger = logging.getLogger(__name__)

VERIFIED = "VERIFIED"
UNVERIFIABLE = "UNVERIFIABLE"
REFUTED = "REFUTED"

# Recomputed values are compared with a tolerance, because a model quoting
# "12,40,000" against a stored 1240000.00 is agreeing, not disagreeing.
REL_TOLERANCE = Decimal("0.005")


@dataclass
class Verdict:
    status: str
    checked: int = 0
    refuted: list[dict] = field(default_factory=list)
    values: dict = field(default_factory=dict)
    note: str = ""


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        # Models write Indian-format numbers and currency symbols. Strip the
        # decoration rather than refuting a claim over a comma.
        text = str(value).replace(",", "").replace("₹", "").replace("$", "").strip()
        return Decimal(text) if text else None
    except (InvalidOperation, ValueError):
        return None


def _close(a: Decimal, b: Decimal) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= REL_TOLERANCE


# ── Tools ─────────────────────────────────────────────────────────────────────
# Everything the verifier can recompute. A claim naming a tool outside this
# registry is not "assumed true" — it is unverifiable, which is a different and
# quieter outcome.

async def _bill_total(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        "SELECT total_amount FROM fact_invoices "
        " WHERE tenant_id = $1::uuid AND invoice_id = $2::uuid",
        tenant_id, args.get("invoice_id"),
    )


async def _bill_line_sum(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        """
        SELECT COALESCE(SUM(li.line_total), 0)
          FROM stg_bill_line_items li
          JOIN fact_invoices fi ON fi.stg_bill_id = li.stg_bill_id
         WHERE fi.tenant_id = $1::uuid AND fi.invoice_id = $2::uuid
        """,
        tenant_id, args.get("invoice_id"),
    )


async def _bill_tax(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        "SELECT tax_amount FROM fact_invoices "
        " WHERE tenant_id = $1::uuid AND invoice_id = $2::uuid",
        tenant_id, args.get("invoice_id"),
    )


async def _bill_subtotal(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        "SELECT subtotal FROM fact_invoices "
        " WHERE tenant_id = $1::uuid AND invoice_id = $2::uuid",
        tenant_id, args.get("invoice_id"),
    )


async def _vendor_outstanding(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        """
        SELECT COALESCE(SUM(outstanding_amount), 0) FROM fact_invoices
         WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid AND soft_status = 'active'
        """,
        tenant_id, args.get("supplier_id"),
    )


async def _vendor_bill_count(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        "SELECT COUNT(*) FROM fact_invoices "
        " WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid AND soft_status = 'active'",
        tenant_id, args.get("supplier_id"),
    )


async def _vendor_mean_bill(conn, tenant_id: str, args: dict):
    return await conn.fetchval(
        """
        SELECT AVG(total_amount) FROM fact_invoices
         WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid
           AND soft_status = 'active' AND total_amount IS NOT NULL
        """,
        tenant_id, args.get("supplier_id"),
    )


async def _vendor_field(conn, tenant_id: str, args: dict):
    field_name = args.get("field")
    allowed = {"gstn", "pan", "bank_account", "bank_ifsc", "name", "status"}
    if field_name not in allowed:
        return None
    return await conn.fetchval(
        f"SELECT {field_name}::text FROM dim_suppliers "
        f" WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid",
        tenant_id, args.get("supplier_id"),
    )


async def _bill_field(conn, tenant_id: str, args: dict):
    field_name = args.get("field")
    allowed = {"invoice_number", "currency_code", "status", "category", "payment_status"}
    if field_name not in allowed:
        return None
    return await conn.fetchval(
        f"SELECT {field_name}::text FROM fact_invoices "
        f" WHERE tenant_id = $1::uuid AND invoice_id = $2::uuid",
        tenant_id, args.get("invoice_id"),
    )


TOOLS = {
    "bill_total":         _bill_total,
    "bill_line_sum":      _bill_line_sum,
    "bill_tax":           _bill_tax,
    "bill_subtotal":      _bill_subtotal,
    "bill_field":         _bill_field,
    "vendor_outstanding": _vendor_outstanding,
    "vendor_bill_count":  _vendor_bill_count,
    "vendor_mean_bill":   _vendor_mean_bill,
    "vendor_field":       _vendor_field,
}

_OPS = {
    ">":  lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<":  lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: _close(a, b),
    "!=": lambda a, b: not _close(a, b),
}


async def _resolve(conn, tenant_id: str, side) -> tuple[object, bool]:
    """(value, was_computed). A literal is not evidence — only a tool result is."""
    if isinstance(side, dict) and "tool" in side:
        fn = TOOLS.get(side["tool"])
        if fn is None:
            return None, False
        try:
            return await fn(conn, tenant_id, side.get("args") or {}), True
        except Exception as exc:
            logger.warning("[verifier] tool %s failed: %s", side["tool"], exc)
            return None, False
    if isinstance(side, dict) and "literal" in side:
        return side["literal"], False
    return side, False


async def verify_claims(
    conn: asyncpg.Connection, tenant_id: str, claims: list[dict]
) -> Verdict:
    if not claims:
        return Verdict(UNVERIFIABLE, note="no machine-checkable claim was made")

    checked = 0
    refuted: list[dict] = []
    values: dict = {}

    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        kind = claim.get("kind")

        if kind in ("numeric_compare", "value_compare"):
            left, left_computed = await _resolve(conn, tenant_id, claim.get("left"))
            right, right_computed = await _resolve(conn, tenant_id, claim.get("right"))

            # If neither side came from a tool there is nothing to check against
            # — the model would just be agreeing with itself.
            if not (left_computed or right_computed):
                continue
            if left is None or right is None:
                continue

            op = claim.get("op", "==")
            if kind == "numeric_compare":
                ld, rd = _to_decimal(left), _to_decimal(right)
                if ld is None or rd is None:
                    continue
                holds = _OPS.get(op, _OPS["=="])(ld, rd)
                values[f"claim_{i}"] = {"left": str(ld), "right": str(rd), "op": op}
            else:
                ls, rs = str(left).strip().lower(), str(right).strip().lower()
                holds = (ls == rs) if op == "==" else (ls != rs)
                values[f"claim_{i}"] = {"left": str(left), "right": str(right), "op": op}

            checked += 1
            if not holds:
                refuted.append({
                    "index": i, "claim": claim,
                    "recomputed_left": str(left), "recomputed_right": str(right),
                    "reason": "recomputed values do not satisfy the stated relation",
                })

            # A stated value that disagrees with the database is the clearest
            # hallucination signal there is — the model quoted a number it did
            # not read.
            for side_name, stated_key, actual, computed in (
                ("left", "stated_left", left, left_computed),
                ("right", "stated_right", right, right_computed),
            ):
                stated = claim.get(stated_key)
                if stated is None or not computed:
                    continue
                sd, ad = _to_decimal(stated), _to_decimal(actual)
                if sd is not None and ad is not None and not _close(sd, ad):
                    refuted.append({
                        "index": i, "claim": claim, "side": side_name,
                        "stated": str(sd), "actual": str(ad),
                        "reason": "the model quoted a value the database does not hold",
                    })

    if refuted:
        return Verdict(REFUTED, checked=checked, refuted=refuted, values=values)
    if checked == 0:
        return Verdict(UNVERIFIABLE, note="no claim could be recomputed")
    return Verdict(VERIFIED, checked=checked, values=values)


async def log_reject(
    conn: asyncpg.Connection,
    tenant_id: str,
    *,
    agent_run_id: str | None,
    subject_type: str | None,
    subject_id: str | None,
    proposed_type: str | None,
    claim: dict,
    reason: str,
    model: str | None,
    prompt_version: str | None,
) -> None:
    """Append to the hallucination ledger. Never raises."""
    try:
        await conn.execute(
            """
            INSERT INTO rh_finding_rejects
                (tenant_id, agent_run_id, subject_type, subject_id,
                 proposed_type, claim_jsonb, reject_reason, model, prompt_version)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6::jsonb, $7, $8, $9)
            """,
            tenant_id, agent_run_id, subject_type, subject_id,
            proposed_type, json.dumps(claim, default=str), reason, model, prompt_version,
        )
    except Exception as exc:
        logger.warning("[verifier] could not log a reject: %s", exc)
