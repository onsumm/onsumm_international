"""
Automatic reconciliation trigger — lives inside this service (not a
pipelines/ worker: this service owns the whole Nova-billing lifecycle end to
end, including deciding when to run itself, per NOVA_RECONCILIATION_LOGIC.md
§1's "independently deployed" principle).

Two jobs, run from the same polling loop:
  1. Discover: find fact_invoices rows whose supplier is a Nova-mapped vendor
     that this service hasn't seen before, and queue them
     (nova_reconciliation_bill_queue, status PENDING).
  2. Process: for every PENDING/ERROR row in the queue — resolve which entity/
     brand it belongs to, resolve that tenant's own Nova API key, call the
     live Nova API, build invoice_lines from stg_bill_line_items, run
     algorithm.reconcile(), and persist via routes.persist_reconcile_result().

This same loop backfills the existing backlog (every bill already sitting in
fact_invoices gets discovered and queued on the very first sweep) and keeps
up with new uploads (polled at ORCHESTRATOR_POLL_SECONDS) — one mechanism for
both, per the request that both cases just work.

A bill that can't be auto-resolved (ambiguous brand/cost-centre, no Nova key
configured yet, live API error) is marked SKIPPED/ERROR with a `reason`, not
silently dropped — visible via GET /v1/orchestrator/status and retried on
later sweeps.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Optional

from psycopg2.extras import RealDictCursor

from . import algorithm, config
from . import mapping as mapping_module
from . import vendor_match
from .aliases import resolve as resolve_alias_db
from .credentials import NovaKeyNotConfigured, resolve_tenant_nova_key
from .nova_client import NovaApiError, fetch_billing_monthly
from .routes import persist_reconcile_result

log = logging.getLogger("nova_reconciliation.orchestrator")

POLL_SECONDS = int(os.getenv("NOVA_ORCHESTRATOR_POLL_SECONDS", "120"))
ENABLED = os.getenv("NOVA_ORCHESTRATOR_ENABLED", "true").lower() not in ("false", "0", "")
MIN_INVOICE_DATE = os.getenv("NOVA_ORCHESTRATOR_MIN_INVOICE_DATE") or None  # e.g. "2026-08-01"
MAX_ATTEMPTS = 5  # ERROR rows stop being retried after this many sweeps (a transient outage
                   # should resolve well before 5; past that it's a real problem worth a human look)

# A single-brand qty mismatch this large is worth the extra Nova call to check whether a
# sibling brand of the same registered entity explains it (confirmed real case: Avinash
# Capital Markets bills Brightloans+Kreditbuddha's combined PAN Premium API usage on one
# Brightloans invoice — reconciling against Brightloans alone showed 47.51% off; against
# the combined usage, 0.36%). The recheck's own pass/fail still uses the mapping's normal
# tolerance (2%, or 20% for Account Aggregator) — this constant only gates when it's worth
# trying, so a small variance doesn't trigger an extra live API call per sibling brand.
QTY_COMBINED_CHECK_TRIGGER_PCT = 5.0


def _month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_eligible_bills(conn, mapping: dict) -> int:
    """Scan fact_invoices for bills from a Nova-mapped vendor never seen before
    and insert a PENDING row per invoice_id. Idempotent — ON CONFLICT DO NOTHING,
    so re-running this is always safe and is how both the backlog and new
    uploads get picked up by the same code path.

    MIN_INVOICE_DATE excludes anything dated before it from ever being queued —
    without this, deleting a queue row (e.g. to clear out a stale/test run) makes
    that bill look "never seen" again and the very next sweep re-discovers and
    re-reconciles it. Pre-cutoff bills are cleanup, not backlog."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT fi.invoice_id, fi.tenant_id, s.name AS supplier_name
            FROM fact_invoices fi
            JOIN dim_suppliers s ON s.supplier_id = fi.supplier_id
            WHERE fi.invoice_id NOT IN (SELECT invoice_id FROM nova_reconciliation_bill_queue)
              AND (%(min_date)s::date IS NULL OR fi.invoice_date >= %(min_date)s::date)
            """,
            {"min_date": MIN_INVOICE_DATE},
        )
        candidates = cur.fetchall()

    queued = 0
    with conn.cursor() as cur:
        for row in candidates:
            vendor = vendor_match.match_nova_vendor(row["supplier_name"], mapping)
            if vendor is None:
                continue
            cur.execute(
                """
                INSERT INTO nova_reconciliation_bill_queue (invoice_id, tenant_id, vendor, status)
                VALUES (%s, %s, %s, 'PENDING')
                ON CONFLICT (invoice_id) DO NOTHING
                """,
                (row["invoice_id"], row["tenant_id"], vendor),
            )
            queued += cur.rowcount
    conn.commit()
    return queued


# ── Brand / cost-centre resolution ──────────────────────────────────────────

def _norm(name: str) -> str:
    """Lowercase, alphanumerics only — so "Brightloans Private Limited" (brands table)
    and "Brightloans" (mapping JSON) compare, and case disagreements between
    brands[]/cost_centers[].name ('Kreditbuddha' vs 'KreditBuddha') don't matter."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def resolve_brand_or_cost_centre(conn, entity: dict, vendor: str, invoice_id: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (brand_or_cost_centre, skip_reason). Never guesses: if the bill
    itself doesn't say which brand/cost-centre it belongs to and there isn't
    exactly one certified candidate for this vendor, this returns
    (None, <reason>) and the caller marks the bill SKIPPED rather than
    reconciling against a scope nobody confirmed."""
    brands = entity.get("brands") or []

    # 1) Does the bill (invoice-level, or any of its line items) carry a
    #    cost-centre that matches one of this entity's brand/cost-centre names?
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT cc.name
            FROM dim_cost_centres cc
            WHERE cc.cost_centre_id IN (
                SELECT cost_center_id FROM fact_invoices WHERE invoice_id = %(inv)s AND cost_center_id IS NOT NULL
                UNION
                SELECT li.cost_centre_id FROM stg_bill_line_items li
                JOIN fact_invoices fi ON fi.stg_bill_id = li.stg_bill_id
                WHERE fi.invoice_id = %(inv)s AND li.cost_centre_id IS NOT NULL
            )
            """,
            {"inv": invoice_id},
        )
        cc_names = {r[0] for r in cur.fetchall()}
    matches = cc_names & set(brands)
    if len(matches) == 1:
        return next(iter(matches)), None
    if len(matches) > 1:
        return None, f"bill's cost-centre signal matches multiple brands ({sorted(matches)}) — ambiguous"

    # 2) No cost-centre tag, but the bill may still carry its brand association
    #    (fact_invoices.brand_id -> brands.name, what the Bills page shows as
    #    "Brand name"). That is a real signal on the bill, not an inference: a
    #    Signzy invoice addressed to Avinash Capital Markets with brand
    #    "Brightloans Private Limited" is a Brightloans bill, and treating it as
    #    ambiguous ignores something the platform already knows.
    #
    #    Matched on a normalized prefix because the two sources spell the same brand
    #    differently — the mapping JSON says "Brightloans", the brands table says
    #    "Brightloans Private Limited". Still requires exactly one match, so two
    #    brands sharing a prefix stay ambiguous rather than resolving to whichever
    #    was checked first.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT b.name FROM fact_invoices fi
            JOIN brands b ON b.id = fi.brand_id
            WHERE fi.invoice_id = %s AND fi.brand_id IS NOT NULL
            """,
            (invoice_id,),
        )
        row = cur.fetchone()
    if row and row[0]:
        bill_brand = _norm(row[0])
        brand_matches = [b for b in brands if bill_brand.startswith(_norm(b))]
        if len(brand_matches) == 1:
            return brand_matches[0], None
        if len(brand_matches) > 1:
            return None, (
                f"bill's brand '{row[0]}' matches multiple certified brands "
                f"({sorted(brand_matches)}) — ambiguous"
            )

    # 3) No signal on the bill at all — fall back to whichever of this entity's
    #    brands actually has a certified rate card for this vendor. Exactly one
    #    candidate is safe to default to; zero or multiple is not.
    candidates = [b for b in brands if algorithm.certified_pairs(entity, vendor, b)]
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"no brand of {entity.get('registered_entity')} has a certified {vendor} rate card"
    return None, f"bill has no cost-centre signal and {len(candidates)} brands are all certified for {vendor} — ambiguous"


# ── Invoice line extraction ─────────────────────────────────────────────────

def _net_of_tax(line_total, tax_amount) -> Optional[float]:
    """The invoice amount to reconcile against Nova: **excluding tax**.

    Nova's ledger is a usage cost — it carries no GST. `stg_bill_line_items.line_total`
    is tax-INCLUSIVE (a real Signzy line: 806901 x Rs.0.79 = Rs.6,37,451.79 taxable,
    + Rs.1,14,741.32 IGST @18% = Rs.7,52,193.11 stored in line_total). Comparing that
    against Nova inflated the variance by exactly the tax rate and made a matching bill
    look 175.98% off.

    Subtracts the extracted `tax_amount` rather than re-deriving `quantity x rate`,
    deliberately: §2.3 records that OCR sometimes captures a wrong `line_total`, and
    re-deriving would silently paper over that instead of surfacing it. Quantity and
    rate checks were never affected by any of this — neither carries tax.
    """
    if line_total is None:
        return None
    return round(float(line_total) - float(tax_amount or 0), 2)


def _load_invoice_lines(conn, invoice_id: str) -> tuple[list[dict], Optional[str]]:
    """Returns (invoice_lines in algorithm.py's expected shape, service_month).
    service_month priority: the line item's own service_month, else the bill
    header's service_month (stg_bills.service_month — OCR-extracted "Billing
    Period", which can differ from the invoice date: a bill dated 14 May can
    carry billing period "Apr 2026"), else the invoice date as a last resort.
    That last fallback is a known approximation — when a vendor bills a prior
    month's usage, invoice date and service month diverge, and Nova gets
    queried for the wrong month. Flagged in the return so the caller can
    surface it rather than reconcile silently against a guessed month."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT li.line_item_id, li.product_name, li.quantity, li.unit_price,
                   li.line_total, li.tax_amount, li.service_month AS line_service_month,
                   sb.service_month AS bill_service_month, fi.invoice_date
            FROM stg_bill_line_items li
            JOIN fact_invoices fi ON fi.stg_bill_id = li.stg_bill_id
            LEFT JOIN stg_bills sb ON sb.stg_bill_id = li.stg_bill_id
            WHERE fi.invoice_id = %s
            """,
            (invoice_id,),
        )
        rows = cur.fetchall()

    lines = []
    month = None
    month_is_approximate = False
    for r in rows:
        lines.append({
            "line_item_id": str(r["line_item_id"]),
            "description": r["product_name"] or "",
            "quantity": float(r["quantity"]) if r["quantity"] is not None else None,
            "rate": float(r["unit_price"]) if r["unit_price"] is not None else None,
            "amount": _net_of_tax(r["line_total"], r["tax_amount"]),
        })
        if r["line_service_month"] or r["bill_service_month"]:
            line_month = r["line_service_month"] or r["bill_service_month"]
        else:
            line_month = _month_str(r["invoice_date"]) if r["invoice_date"] else None
            month_is_approximate = month_is_approximate or line_month is not None
        month = month or line_month
    return lines, month, month_is_approximate


# ── Combined-cost-centre view (all brands summed) ────────────────────────────
#
# Normal reconciliation measures a bill against its OWN cost centre's ledger, and §4.4
# only reaches for a sibling when a specific line doesn't add up. This is the explicit
# whole-bill alternative: reconcile against EVERY certified cost centre of the entity,
# summed. It exists because a vendor genuinely can bill one document covering all of an
# entity's brands (confirmed for Signzy/Avinash), in which case the per-cost-centre view
# is the wrong frame for the entire bill rather than for one line.
#
# Deliberately NOT persisted and NOT the default: it answers "does this bill reconcile if
# we treat every brand as one pool?", which is a question an operator asks, not a fact
# about which cost centre owns the cost. Persisting it would overwrite the per-cost-centre
# run that the dashboard and cost allocation depend on.


def _merge_nova_responses(responses: list[dict], vendor: str, month: str) -> dict:
    """Sum several cost centres' Nova ledgers into one response, per module slug.

    `costPerOperationRupees` is recomputed as total cost / total ops rather than copied
    from any one cost centre — a weighted effective rate, so the rate check still compares
    like with like if two cost centres were ever billed at different per-op rates.
    """
    merged: dict[str, dict] = {}
    for resp in responses:
        for m in (resp.get("data") or {}).get("modules") or []:
            acc = merged.setdefault(m["module"], {
                "module": m["module"], "apiCallCount": 0, "billableOperations": 0,
                "totalCostRupees": 0.0, "costPerOperationRupees": m.get("costPerOperationRupees"),
            })
            acc["apiCallCount"] += m.get("apiCallCount") or 0
            acc["billableOperations"] += m.get("billableOperations") or 0
            acc["totalCostRupees"] += m.get("totalCostRupees") or 0
    for acc in merged.values():
        if acc["billableOperations"]:
            acc["costPerOperationRupees"] = round(acc["totalCostRupees"] / acc["billableOperations"], 6)
    modules = list(merged.values())
    return {
        "success": True,
        "data": {
            "vendor": vendor, "month": month, "modules": modules,
            "totals": {
                "totalBillableOperations": sum(m["billableOperations"] for m in modules),
                "totalCostRupees": round(sum(m["totalCostRupees"] for m in modules), 2),
            },
        },
        "timestamp": None, "referenceId": None,
    }


def reconcile_combined(conn, mapping: dict, invoice_id: str) -> dict:
    """Reconcile one bill against all its entity's certified cost centres, summed.

    Returns {combined_over, missing_keys, brand, vendor, month, run, lines}. Raises
    LookupError with a human reason when the bill can't be reconciled at all — the caller
    maps that to a 404/409 rather than inventing an empty result.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT tenant_id, vendor, resolved_brand FROM nova_reconciliation_bill_queue WHERE invoice_id = %s",
            (invoice_id,),
        )
        q = cur.fetchone()
    if not q:
        raise LookupError("this bill has never been queued for Nova reconciliation")

    tenant_id, vendor = str(q["tenant_id"]), q["vendor"]
    entity = algorithm.find_entity_by_tenant(mapping, tenant_id)
    if entity is None:
        raise LookupError(f"tenant {tenant_id} has no entry in nova_vendor_mapping.json")

    brand = q.get("resolved_brand")
    if not brand:
        brand, skip_reason = resolve_brand_or_cost_centre(conn, entity, vendor, invoice_id)
        if brand is None:
            raise LookupError(skip_reason or "the bill's cost centre couldn't be resolved")

    invoice_lines, month, month_is_approximate = _load_invoice_lines(conn, invoice_id)
    if not invoice_lines or not month:
        raise LookupError("no line items (or no service month) found for this bill yet")

    # Every cost centre of this entity that certifies this vendor — the pool to sum.
    candidates = [b for b in (entity.get("brands") or []) if algorithm.certified_pairs(entity, vendor, b)]
    if brand not in candidates:
        candidates.append(brand)

    responses, contributed, missing = [], [], []
    for cc in candidates:
        try:
            creds = resolve_tenant_nova_key(conn, tenant_id, cc)
        except NovaKeyNotConfigured:
            # Named honestly rather than silently dropped: a cost centre missing from the
            # sum changes the answer, and the caller must be able to say so.
            missing.append(cc)
            continue
        try:
            responses.append(fetch_billing_monthly(creds["base_url"], creds["api_key"], vendor, month))
            contributed.append(cc)
        except NovaApiError:
            missing.append(cc)
    if not responses:
        raise LookupError("no cost centre of this entity has a usable Nova API key configured")

    merged = _merge_nova_responses(responses, vendor, month)

    def resolve_alias(v: str, text: str) -> Optional[str]:
        return resolve_alias_db(conn, mapping, v, text)

    result = algorithm.reconcile(
        mapping, tenant_id, brand, vendor, month, merged, invoice_lines, None, resolve_alias,
    )
    # Every certified line here was measured against the summed pool, so attribution is
    # SHARED by construction. Sibling attribution (§4.4) is meaningless in this frame —
    # there is no "other" cost centre left to attribute to.
    for line in result["lines"]:
        if line.get("certification_status") == "CERTIFIED":
            line["cost_centre_attribution"] = ATTR_SHARED
        if not line.get("cost_centre"):
            line["cost_centre"] = brand
    result["run"] = algorithm._rollup(result["lines"])

    return {
        "combined_over": contributed,
        "missing_keys": missing,
        "brand": brand,
        "vendor": vendor,
        "month": month,
        "month_is_approximate": month_is_approximate,
        **result,
    }


# ── Process one queued bill ──────────────────────────────────────────────────

def _mark(conn, invoice_id: str, status: str, *, run_id: Optional[str] = None, reason: Optional[str] = None,
          resolved_brand: Optional[str] = None):
    """resolved_brand is sticky — passing None (the default, for SKIPPED/ERROR/
    month-only marks) leaves whatever was previously resolved untouched via
    COALESCE, rather than wiping it out on a later transient failure or an
    unrelated re-mark."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE nova_reconciliation_bill_queue
            SET status = %s, run_id = %s, reason = %s,
                resolved_brand = COALESCE(%s, resolved_brand),
                attempt_count = attempt_count + 1, last_attempted_at = now(), updated_at = now()
            WHERE invoice_id = %s
            """,
            (status, run_id, reason, resolved_brand, invoice_id),
        )
    conn.commit()


# ── Sibling-brand combined qty recheck ──────────────────────────────────────

def _canonical_to_billable_ops(nova_response: Optional[dict], vendor: str, resolve_alias) -> dict[str, float]:
    out: dict[str, float] = {}
    if not nova_response:
        return out
    for module in nova_response["data"]["modules"]:
        canonical = resolve_alias(vendor, module["module"])
        if canonical:
            out[canonical] = out.get(canonical, 0) + (module.get("billableOperations") or 0)
    return out


def _fetch_sibling_ops(conn, tenant_id: str, sibling_brand: str, vendor: str, month: str, resolve_alias) -> dict[str, float]:
    try:
        creds = resolve_tenant_nova_key(conn, tenant_id, sibling_brand)
    except NovaKeyNotConfigured:
        return {}
    try:
        resp = fetch_billing_monthly(creds["base_url"], creds["api_key"], vendor, month)
    except NovaApiError:
        return {}
    return _canonical_to_billable_ops(resp, vendor, resolve_alias)


# ── Cost-centre attribution (§4.4) ───────────────────────────────────────────
#
# A vendor addresses one document to one cost centre, but the usage on it can belong to
# that cost centre, to a sibling cost centre of the same registered entity, or to several
# of them together — Nova tracks each cost centre separately, under its own API key
# (int_nova_credentials.brand). Attribution decides, per certified line, which cost centre
# the line is *truly* billed for, so a bill reconciles only the lines genuinely its own and
# a sibling's usage is named as the sibling's rather than counted as this bill's mismatch.
#
# Certification, canonical-service resolution and the 2%/20% tolerances are unchanged and
# still come from the mapping JSON — this only decides *whose* Nova ledger a line is
# measured against.

ATTR_OWN = "OWN"                      # this cost centre's own ledger accounts for the line
ATTR_SIBLING_OWNED = "SIBLING_OWNED"  # a sibling's ledger alone accounts for it — not this cost centre's cost
ATTR_SHARED = "SHARED"                # only the combined ledger accounts for it — billed across cost centres
ATTR_UNATTRIBUTED = "UNATTRIBUTED"    # no cost centre, alone or combined, accounts for it — a real mismatch


def _sibling_cost_centres(entity: dict, brand: str) -> list[str]:
    """Sibling brands/cost centres of the same RE. Compared case-insensitively because
    real data disagrees on case for the same cost centre — the mapping JSON's brands[]
    says 'Kreditbuddha' while its own cost_centers[].name (and dim_cost_centres) say
    'KreditBuddha' — and a cost-centre tag on a bill can differ again."""
    own = (brand or "").casefold()
    return [b for b in (entity.get("brands") or []) if b.casefold() != own]


def apply_cost_centre_attribution(
    conn, mapping: dict, entity: dict, tenant_id: str, vendor: str, month: str, brand: str,
    lines: list[dict], resolve_alias,
) -> list[dict]:
    """Resolve, per CERTIFIED line, which cost centre actually owns the usage.

    Order matters — most specific explanation first:

      1. The bill's own cost centre already matches within tolerance -> OWN. Costs
         nothing: no extra Nova call is made for a line that already reconciles.
      2. Exactly one sibling's ledger alone matches -> SIBLING_OWNED. The line is that
         cost centre's cost, not this one's; `cost_centre` is reassigned to it and the
         line stops being counted as this bill's quantity mismatch. Two or more siblings
         matching alone is genuinely ambiguous, so it is never guessed — it falls
         through to the combined check, which is a claim about all of them together.
      3. The combined ledger matches -> SHARED. The vendor billed several cost centres
         on one document (the confirmed Brightloans+Kreditbuddha PAN case). `cost_centre`
         stays the bill's own — the line really does span both — and the participants are
         named in `combined_brand_check`.
      4. Nothing explains it -> UNATTRIBUTED. Left exactly as the single-cost-centre
         reconciliation found it.

    Never invents a pass: every flip to QTY_OK is a real match against a real Nova ledger,
    and a non-match leaves the original result untouched.
    """
    if entity.get("entity_type") != "RE":
        return lines

    siblings = _sibling_cost_centres(entity, brand)
    policy = mapping["reconciliation_policy"]
    sibling_ops_cache: dict[str, dict[str, float]] = {}

    def sibling_ops(sib: str) -> dict[str, float]:
        if sib not in sibling_ops_cache:
            sibling_ops_cache[sib] = _fetch_sibling_ops(conn, tenant_id, sib, vendor, month, resolve_alias)
        return sibling_ops_cache[sib]

    for line in lines:
        if not line.get("cost_centre"):
            line["cost_centre"] = brand
        if line.get("certification_status") != "CERTIFIED":
            continue

        canonical = line.get("canonical_service")
        qty_status = line.get("qty_status")

        if qty_status == "QTY_OK":
            line["cost_centre_attribution"] = ATTR_OWN
            continue
        if qty_status != "QTY_MISMATCH" or not canonical:
            continue

        # A real mismatch against this cost centre until some other ledger explains it.
        line["cost_centre_attribution"] = ATTR_UNATTRIBUTED

        variance = line.get("qty_variance_pct")
        if variance is None or variance < QTY_COMBINED_CHECK_TRIGGER_PCT:
            # Gap too small to justify a live Nova call per sibling — a gate on cost,
            # not the tolerance the checks below actually use.
            continue
        if not siblings:
            continue

        # Only a sibling that certifies this service can own it.
        candidates = [s for s in siblings if canonical in algorithm.certified_pairs(entity, vendor, s)]
        if not candidates:
            continue

        invoice_qty = line.get("invoice_qty") or 0
        own_ops = line.get("billable_operations") or 0
        allowed = line.get("allowed_variance")

        # 2. One sibling's ledger alone.
        sole_matches = []
        for sib in candidates:
            ops = sibling_ops(sib).get(canonical)
            if not ops:
                continue
            ok, var = algorithm._within_variance(invoice_qty, ops, policy, canonical, allowed)
            if ok:
                sole_matches.append((sib, ops, var))

        if len(sole_matches) == 1:
            sib, ops, var = sole_matches[0]
            line["cost_centre"] = sib
            line["cost_centre_attribution"] = ATTR_SIBLING_OWNED
            line["qty_status"] = "QTY_OK"
            line["qty_variance_pct"] = var
            line["combined_brand_check"] = (
                f"billed on a {brand} document but matches {sib}'s own Nova usage: "
                f"{ops:.0f} billable ops vs invoice qty {invoice_qty:.0f} ({var}% variance, "
                f"was {variance}% against {brand}) — attributed to {sib}"
            )
            continue

        # 3. All of them together.
        combined_ops = own_ops
        contributing = []
        for sib in candidates:
            ops = sibling_ops(sib).get(canonical)
            if ops:
                combined_ops += ops
                contributing.append(sib)
        if not contributing:
            continue

        ok, new_variance = algorithm._within_variance(invoice_qty, combined_ops, policy, canonical, allowed)
        line["combined_brand_check"] = (
            f"{brand}+{'+'.join(contributing)} combined billable ops: {combined_ops:.0f} vs invoice qty "
            f"{invoice_qty:.0f} ({new_variance}% variance, was {variance}% on {brand} alone)"
        )
        if ok:
            line["qty_status"] = "QTY_OK"
            line["qty_variance_pct"] = new_variance
            line["cost_centre_attribution"] = ATTR_SHARED
    return lines


# The narrower sibling-only recheck this generalizes; kept so any existing import or
# caller keeps working.
apply_sibling_brand_qty_recheck = apply_cost_centre_attribution


def process_one(conn, mapping: dict, queue_row: dict) -> str:
    """Returns the resulting status (DONE/SKIPPED/ERROR)."""
    invoice_id = queue_row["invoice_id"]
    tenant_id = str(queue_row["tenant_id"])
    vendor = queue_row["vendor"]

    entity = algorithm.find_entity_by_tenant(mapping, tenant_id)
    if entity is None:
        _mark(conn, invoice_id, "SKIPPED", reason=f"tenant {tenant_id} has no entry in nova_vendor_mapping.json")
        return "SKIPPED"

    if queue_row.get("resolved_brand"):
        # Already resolved on a prior successful run (possibly by a human via a
        # direct /v1/reconcile call, when auto-resolution was ambiguous) — reuse
        # it rather than re-running resolve_brand_or_cost_centre, which could
        # come back ambiguous again after an unrelated mapping change (e.g. two
        # brands newly sharing a rate card) and silently regress an already-fixed bill.
        brand = queue_row["resolved_brand"]
    else:
        brand, skip_reason = resolve_brand_or_cost_centre(conn, entity, vendor, invoice_id)
        if brand is None:
            _mark(conn, invoice_id, "SKIPPED", reason=skip_reason)
            return "SKIPPED"

    invoice_lines, month, month_is_approximate = _load_invoice_lines(conn, invoice_id)
    if not invoice_lines or not month:
        _mark(conn, invoice_id, "SKIPPED", reason="no line items (or no service month) found for this bill yet")
        return "SKIPPED"

    try:
        creds = resolve_tenant_nova_key(conn, tenant_id, brand)
    except NovaKeyNotConfigured as e:
        _mark(conn, invoice_id, "SKIPPED", reason=str(e))
        return "SKIPPED"

    try:
        nova_response = fetch_billing_monthly(creds["base_url"], creds["api_key"], vendor, month)
    except NovaApiError as e:
        _mark(conn, invoice_id, "ERROR", reason=f"Nova API call failed: {e}")
        return "ERROR"

    try:
        def resolve_alias(v: str, text: str) -> Optional[str]:
            return resolve_alias_db(conn, mapping, v, text)

        result = algorithm.reconcile(
            mapping, tenant_id, brand, vendor, month,
            nova_response, invoice_lines, None, resolve_alias,
        )
        result["lines"] = apply_cost_centre_attribution(
            conn, mapping, entity, tenant_id, vendor, month, brand, result["lines"], resolve_alias,
        )
        result["run"] = algorithm._rollup(result["lines"])
        run_result = persist_reconcile_result(
            conn, bill_id=str(invoice_id), tenant_id=tenant_id, brand_or_cost_centre=brand,
            vendor=vendor, month=month, nova_response=nova_response, extended_nova_response=None,
            nova_reference_id=nova_response.get("referenceId"), result=result,
        )
    except LookupError as e:
        _mark(conn, invoice_id, "SKIPPED", reason=str(e))
        return "SKIPPED"
    except Exception as e:
        conn.rollback()
        log.exception("reconciliation failed for invoice %s", invoice_id)
        _mark(conn, invoice_id, "ERROR", reason=f"reconciliation failed: {e}")
        return "ERROR"

    done_reason = (
        f"service_month approximated from invoice_date ({month}) — neither the bill nor its line "
        f"items had an extracted billing period; verify this against the actual invoice"
    ) if month_is_approximate else None
    _mark(conn, invoice_id, "DONE", run_id=run_result.run.run_id, reason=done_reason, resolved_brand=brand)
    return "DONE"


def process_pending(conn, mapping: dict) -> dict:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT invoice_id, tenant_id, vendor, resolved_brand FROM nova_reconciliation_bill_queue
            WHERE status = 'PENDING' OR (status = 'ERROR' AND attempt_count < %s)
            ORDER BY created_at
            """,
            (MAX_ATTEMPTS,),
        )
        rows = cur.fetchall()

    counts = {"DONE": 0, "SKIPPED": 0, "ERROR": 0}
    for row in rows:
        try:
            status = process_one(conn, mapping, row)
        except Exception:
            conn.rollback()
            log.exception("unexpected failure processing invoice %s", row["invoice_id"])
            status = "ERROR"
        counts[status] = counts.get(status, 0) + 1
    return counts


# ── Sweep + background loop ─────────────────────────────────────────────────

_last_mapping_mtime: Optional[float] = None


def _requeue_if_mapping_changed(conn) -> int:
    """If nova_vendor_mapping.json has changed since the last sweep (a corrected
    rate, a service flipped verified/unverified, a renamed alias, a brand's
    rate card extended to a sibling), reset every previously-DONE queue row
    back to PENDING so process_pending() re-reconciles it against the current
    mapping in this same sweep. The dashboard should reflect a mapping fix
    automatically — nobody should have to remember to manually re-run bills
    that were already reconciled under the old, wrong mapping."""
    global _last_mapping_mtime
    mtime = mapping_module.MAPPING_PATH.stat().st_mtime
    if _last_mapping_mtime is None:
        _last_mapping_mtime = mtime
        return 0
    if mtime == _last_mapping_mtime:
        return 0
    _last_mapping_mtime = mtime
    with conn.cursor() as cur:
        cur.execute("UPDATE nova_reconciliation_bill_queue SET status = 'PENDING', reason = NULL WHERE status = 'DONE'")
        n = cur.rowcount
    conn.commit()
    if n:
        log.info("nova_vendor_mapping.json changed — requeued %d previously reconciled bill(s)", n)
    return n


def run_sweep() -> dict:
    """One full discover+process pass. Safe to call repeatedly (idempotent
    discovery, retry-bounded processing) — this is what both the periodic
    background loop and the manual POST /v1/orchestrator/run-once call."""
    mapping = mapping_module.load()  # hot-reloads from disk if the file's mtime changed
    conn = config.get_conn()
    try:
        requeued = _requeue_if_mapping_changed(conn)
        queued = discover_eligible_bills(conn, mapping)
        counts = process_pending(conn, mapping)
    finally:
        config.put_conn(conn)
    return {"newly_queued": queued, "requeued_for_mapping_change": requeued, **counts}


async def run_forever():
    if not ENABLED:
        log.info("Nova orchestrator disabled (NOVA_ORCHESTRATOR_ENABLED=false)")
        return
    log.info("Nova orchestrator starting — polling every %ss", POLL_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(run_sweep)
            if result["newly_queued"] or result["DONE"] or result["SKIPPED"] or result["ERROR"]:
                log.info("Nova orchestrator sweep: %s", result)
        except Exception:
            log.exception("Nova orchestrator sweep failed")
        await asyncio.sleep(POLL_SECONDS)
