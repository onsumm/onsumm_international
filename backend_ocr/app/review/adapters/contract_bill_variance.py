"""contract_bill_variance.py — contracted rate vs. what was actually billed.

The RATE is what a contract fixes, so the rate is what is compared. A
contract's quantity is a commitment or an estimate; for usage-billed services
(an API, metered compute) the billed quantity legitimately differs every
month, and flagging it produced findings like "Route Distance API: quantity
167726 vs 160000" on a bill charging exactly the contracted 0.79 per unit.
Quantities are still shown on a finding, for context, but never raise one.

Original design notes follow; the both-rate-and-quantity rule they describe
is superseded by the rate-only comparison above.

§5.2.5 of the HLD. Reads `bill_line_item`-scoped claims (produced by
`extract_claims_from_contract`, comm_extract.py) — a contract's stated
per-line rate and/or quantity — and compares them to the matching line item
on the supplier's bills, ONLY when both a rate AND a quantity are present on
BOTH the contract side and the billed side. Either side missing either value
means the comparison is skipped entirely for that pairing — not "compare
whatever's available" — this is a literal rule from the original whiteboard
brief, repeated in the HLD, meant to stop a partial extraction from producing
a false compliance signal (the same discipline `vendor_compliance.py`'s
`grace_days_new_vendor` and `triage.baseline_is_usable` already apply
elsewhere in this package).

Matching a claim's `target_ref` to a billed line item is deliberately simple —
case-insensitive exact-or-substring match on the normalized description, per
the brief's explicit "don't build a real fuzzy-matching engine" instruction —
not a scored/fuzzy matcher.

Tolerance-band sharing: `billing_discrepancies.py` (the adapter this module
most resembles) turns out to have no comparison logic of its own to share —
it only mirrors rows a separate microservice
(`services/nova_reconciliation/app/algorithm.py`) already computed. This
module instead shares `_variance.py`, which reproduces that same
relative-percentage-tolerance convention locally (see that module's
docstring) rather than reaching into a different deployable service's
internals.

Claim status: matched within tolerance on at least one bill -> APPLIED (the
term is consistent with what's billed; terminal, not re-checked, same
convention `master_data_conflict.py` uses for a resolved single-valid claim).
A genuine mismatch -> a NEW status, VARIANCE, alongside
`master_data_conflict.py`'s PENDING/APPLIED/DISCARDED/CONFLICTED vocabulary.
VARIANCE rather than reusing CONFLICTED deliberately: CONFLICTED means "two
sources disagree about the vendor's own master data, and a human must pick
one" (a 2-/3-way choice). Here there is no such choice — the contract is
presumed the source of truth for its own terms, and the question is only
"does this bill match it," i.e. a reconciliation exception, not a master-data
overwrite decision. Reusing CONFLICTED would make the Review Hub reader
expect a keep-new/keep-existing choice that does not apply here. A claim with
no matching bill yet (or a match with an incomplete pairing) is left at
PENDING so a later, more complete bill can still be checked against it.
"""
from __future__ import annotations

import json

from .. import links
from ..routing import data_audience
from ..types import FindingDraft, action, condition_hash, jsonb, table_evidence
from . import _variance
from .base import FindingAdapter

# Bound per-extraction bill-line lookup — same class of limit as
# dossier.py's LINE_LIMIT, so one contract term never triggers an unbounded
# scan of a supplier's entire billing history.
LINE_LIMIT = 50


def _norm_desc(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _matches(target_ref: str, product_name: str) -> bool:
    a, b = _norm_desc(target_ref), _norm_desc(product_name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


class ContractBillVarianceAdapter(FindingAdapter):
    detector = "adapter:contract_bill_variance"
    family = "reconciliation"
    source_service = "bills"

    async def scan(self, conn, tenant_id: str, limit: int):
        rows = await conn.fetch(
            """
            SELECT extraction_id, supplier_id, claims
              FROM comm_extractions
             WHERE tenant_id = $1::uuid
               AND source_type = 'contract'
               AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(claims) c
                    WHERE c->>'scope' = 'bill_line_item'
                      AND c->>'status' IN ('PENDING', 'VARIANCE')
               )
             ORDER BY created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        aud = data_audience(capability="edit")
        vendor_names: dict[str, str | None] = {}

        for row in rows:
            extraction_id = str(row["extraction_id"])
            supplier_id = str(row["supplier_id"]) if row["supplier_id"] else None
            claims = jsonb(row["claims"], default=[]) or []
            if not supplier_id or not claims:
                continue

            # Group open bill_line_item claims by target_ref — CLAIM_SCHEMA
            # carries one value per claim, so a rate and a quantity term for
            # the same line item are two separate claim objects that must be
            # paired here before the brief's "only when both present" rule
            # can even be evaluated.
            groups: dict[str, dict[str, dict]] = {}
            for claim in claims:
                if not isinstance(claim, dict):
                    continue
                if claim.get("scope") != "bill_line_item":
                    continue
                if claim.get("status") not in ("PENDING", "VARIANCE"):
                    continue
                target_ref = (claim.get("target_ref") or "").strip()
                field = claim.get("field")
                if not target_ref or field not in ("rate", "quantity"):
                    continue
                groups.setdefault(target_ref, {})[field] = claim

            if not groups:
                continue

            if supplier_id not in vendor_names:
                vendor_names[supplier_id] = await self._vendor_name(conn, tenant_id, supplier_id)
            vendor_name = vendor_names[supplier_id]

            bill_lines: list[dict] | None = None
            dirty = False

            for target_ref, fields in groups.items():
                rate_claim = fields.get("rate")
                qty_claim = fields.get("quantity")
                if not rate_claim:
                    # No contracted rate, nothing to compare: a quantity alone
                    # is an estimate, not a term a bill can breach.
                    continue

                contract_rate = _variance.to_decimal(rate_claim.get("new_value"))
                contract_qty = _variance.to_decimal(qty_claim.get("new_value")) if qty_claim else None
                if contract_rate is None:
                    continue  # malformed value — never fabricate a comparison

                if bill_lines is None:
                    bill_lines = await self._load_bill_lines(conn, tenant_id, supplier_id)

                matched_lines = [ln for ln in bill_lines if _matches(target_ref, ln["product_name"])]
                if not matched_lines:
                    continue  # no billed line yet for this term — stays PENDING

                any_variance = False
                any_checked = False

                for ln in matched_lines:
                    billed_rate = _variance.to_decimal(ln["unit_price"])
                    billed_qty = _variance.to_decimal(ln["quantity"])
                    # A bill line without a unit price cannot be checked; that
                    # skips this line, not the whole term — another bill may.
                    if billed_rate is None:
                        continue
                    any_checked = True

                    rate_ok, _rate_pct = _variance.within_tolerance(billed_rate, contract_rate)
                    if rate_ok:
                        continue  # the contracted rate was charged; quantity is usage

                    any_variance = True
                    yield FindingDraft(
                        tenant_id=tenant_id,
                        finding_type="contract_bill_variance",
                        family=self.family,
                        subject_type="bill",
                        subject_id=str(ln["invoice_id"]),
                        severity="MEDIUM",
                        title=f'Billed rate for "{target_ref}" does not match the contract',
                        detail=(
                            f'The contract rate for "{target_ref}" is {contract_rate}; '
                            f"the bill charges {billed_rate}."
                        ),
                        deep_link=links.bill(str(ln["invoice_id"]), focus="line_items"),
                        condition_hash=condition_hash(
                            "contract_bill_variance", target_ref, ln["invoice_id"],
                            contract_rate, billed_rate,
                        ),
                        detector=self.detector,
                        source_service=self.source_service,
                        # Both sides are stored, exact values — the diff itself
                        # needs no further verification — but the claim came
                        # from an LLM read of a contract, so confidence still
                        # reflects that extraction's own stated confidence.
                        verification="VERIFIED",
                        confidence=float(rate_claim.get("confidence") or 0.6),
                        key_qualifier=f"{extraction_id}:{target_ref}:{ln['invoice_id']}",
                        evidence=table_evidence(
                            ["Field", "Per the contract", "Billed"],
                            [["Rate", str(contract_rate), str(billed_rate)]] + (
                                [["Quantity (not compared)",
                                  str(contract_qty) if contract_qty is not None else "—",
                                  str(billed_qty) if billed_qty is not None else "—"]]
                                if (contract_qty is not None or billed_qty is not None) else []
                            ),
                            note=f"Line item: {target_ref}. Quantity varies with usage, "
                                 f"so only the rate is checked against the contract.",
                        ),
                        actions=[
                            action("open_bill", "Review the bill", kind="navigate",
                                   href=links.bill(str(ln["invoice_id"]), focus="line_items")),
                        ],
                        audience_roles=aud.roles,
                        required_capability="edit",
                        entities=tuple(
                            e for e in [
                                ("bill", str(ln["invoice_id"]), "subject", ln["invoice_number"]),
                                ("vendor", supplier_id, "counterparty", vendor_name) if vendor_name else None,
                            ] if e
                        ),
                    )

                if any_checked:
                    new_status = "VARIANCE" if any_variance else "APPLIED"
                    if rate_claim.get("status") != new_status:
                        rate_claim["status"] = new_status
                        dirty = True
                    if qty_claim and qty_claim.get("status") != new_status:
                        qty_claim["status"] = new_status
                        dirty = True

            if dirty:
                await conn.execute(
                    "UPDATE comm_extractions SET claims = $2::jsonb WHERE extraction_id = $1::uuid",
                    extraction_id, json.dumps(claims),
                )

    async def _vendor_name(self, conn, tenant_id: str, supplier_id: str) -> str | None:
        row = await conn.fetchrow(
            "SELECT name FROM dim_suppliers WHERE supplier_id = $1::uuid AND tenant_id = $2::uuid",
            supplier_id, tenant_id,
        )
        return row["name"] if row else None

    async def _load_bill_lines(self, conn, tenant_id: str, supplier_id: str) -> list[dict]:
        rows = await conn.fetch(
            """
            SELECT fi.invoice_id, fi.invoice_number, li.product_name,
                   li.quantity, li.unit_price
              FROM fact_invoices fi
              JOIN stg_bill_line_items li ON li.stg_bill_id = fi.stg_bill_id
             WHERE fi.tenant_id = $1::uuid AND fi.supplier_id = $2::uuid
               AND fi.soft_status = 'active'
             ORDER BY fi.invoice_date DESC NULLS LAST
             LIMIT $3
            """,
            tenant_id, supplier_id, LINE_LIMIT,
        )
        return [dict(r) for r in rows]
