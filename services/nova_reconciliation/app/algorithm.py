"""
Core reconciliation algorithm — NOVA_RECONCILIATION_LOGIC.md §4.

Two vendor-tenant flows, matching the Bill Reconciliation Flow spec exactly:

  A. Recurring vendor tenant (RE, e.g. Bright Loan) — single scope. Every bill line
     item is checked directly against that tenant's own certified catalog. There is
     never a second Nova call for this flow.

  B. Non-recurring vendor tenant (Non-RE, e.g. Vimono Tech) — two-phase:
       Phase 1: reconcile bill line items against the tenant's OWN certified catalog,
                using the Nova response for that pull.
       Phase 2: for line items outside that scope, identify the cost centre(s) mapped
                to that service, and reconcile ONLY against a SECOND, explicitly
                supplied Nova response for that cost centre (`extended_nova_response`).
                If that second response hasn't been supplied yet, the line is
                PENDING_EXTENDED_LOOKUP — not silently treated as uncertified.

Within a scope, every matched line runs a three-way check: certification (is this
service allowed to be billed here), invoice vs Nova (does the bill match Nova's usage
ledger, within the allowed variance — percentage-of-Nova-value by default, or an
absolute |bill_value - nova_value| <= allowed_variance when a line specifies one), and
Nova vs mapping (is Nova's own billed rate within tolerance of the certified rate card,
and is Nova's own arithmetic internally consistent).

Pure functions: no DB, no I/O — callers (routes.py) own persistence.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

NUMERIC_RATE_RE = re.compile(r"^\d+(\.\d+)?$")
NOVA_SELF_CONSISTENCY_TOL_PCT = 2.0  # Nova's own ops x rate vs its own reported cost — rounding only

ResolveAlias = Callable[[str, str], Optional[str]]


# ── Mapping lookups ──────────────────────────────────────────────────────────

def find_entity_by_tenant(mapping: dict, tenant_id: str) -> Optional[dict]:
    for entity in mapping["entities"]:
        tenant = entity.get("tenant")
        if tenant and tenant.get("tenant_id") == tenant_id:
            return entity
    return None


def find_owning_re(mapping: dict, brand: str) -> Optional[dict]:
    """The RE whose brands[] contains this brand/cost-centre name."""
    for entity in mapping["entities"]:
        if entity.get("entity_type") == "RE" and brand in (entity.get("brands") or []):
            return entity
    return None


def certified_pairs(entity: Optional[dict], vendor: str, brand: Optional[str] = None) -> dict[str, dict]:
    """canonical_service -> vendor_rates row, for this entity+vendor. When `brand`
    is given, a row with a non-empty brand_mapping must include it (RE case);
    rows with an empty brand_mapping (Vimanotech's shared catalog) always apply.

    A row with `verified: false` is excluded entirely — the July bill verification
    sweep marked it as "logic not yet developed" (no confirmed match against an
    actual bill), so it must not be treated as certified until that's resolved.
    Absence of the `verified` key means verified (the default for every row that
    hasn't been through that sweep)."""
    if entity is None:
        return {}
    out: dict[str, dict] = {}
    for row in entity.get("vendor_rates", []):
        if row.get("vendor") != vendor:
            continue
        if row.get("verified") is False:
            continue
        bm = row.get("brand_mapping") or []
        if brand is not None and bm and brand not in bm:
            continue
        out[row["service"]] = row
    return out


def unverified_pairs(entity: Optional[dict], vendor: str, brand: Optional[str] = None) -> dict[str, dict]:
    """canonical_service -> vendor_rates row, for the `verified: false` rows
    certified_pairs() excludes. A line resolving to one of these gets
    NOVA_LOGIC_MISSING rather than UNCERTIFIED — the rate card names the
    service, but nobody has confirmed how it maps to Nova's own data yet
    (which live module represents it, if any), so it must not be silently
    aliased to an unrelated module the way "eKYC" briefly was to
    "Phone-to-Prefill" — a real different service, not a stand-in for one
    whose Nova mapping isn't defined."""
    if entity is None:
        return {}
    out: dict[str, dict] = {}
    for row in entity.get("vendor_rates", []):
        if row.get("vendor") != vendor:
            continue
        if row.get("verified") is not False:
            continue
        bm = row.get("brand_mapping") or []
        if brand is not None and bm and brand not in bm:
            continue
        out[row["service"]] = row
    return out


def parse_expected_rate(row: dict) -> tuple[Optional[float], bool]:
    """(numeric_rate, auto_comparable). Only a bare-numeric 'INR/hit' rate is
    auto-comparable — tiered/package rates, and non-hit units (INR/min, INR/page),
    route to NOT_AUTO_COMPARABLE rather than guessing an equivalence the source
    data doesn't state."""
    rate_str = str(row.get("rate", "")).strip()
    unit = row.get("rate_unit")
    if unit == "INR/hit" and NUMERIC_RATE_RE.match(rate_str):
        return float(rate_str), True
    return None, False


def _index_modules(nova_response: Optional[dict], vendor: str, resolve_alias: ResolveAlias) -> dict[str, dict]:
    if not nova_response:
        return {}
    out: dict[str, dict] = {}
    for module in nova_response["data"]["modules"]:
        canonical = resolve_alias(vendor, module["module"])
        key = canonical if canonical is not None else f"__unresolved_nova__:{module['module']}"
        out[key] = {"module": module, "canonical": canonical, "module_slug": module["module"]}
    return out


def _index_invoice(invoice_lines: list[dict], vendor: str, resolve_alias: ResolveAlias) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in invoice_lines:
        canonical = resolve_alias(vendor, line["description"])
        key = canonical if canonical is not None else f"__unresolved_invoice__:{line['description']}"
        out[key] = {"line": line, "canonical": canonical}
    return out


def _pct_tolerance(policy: dict, canonical_service: Optional[str]) -> float:
    return policy["account_aggregator_tolerance_pct"] if canonical_service == "Account Aggregator" \
        else policy["default_tolerance_pct"]


def _within_variance(bill_value: float, nova_value: float, policy: dict,
                      canonical_service: Optional[str], allowed_variance: Optional[float]) -> tuple[bool, Optional[float]]:
    """True/variance_pct. `allowed_variance`, when given on the invoice line, is an
    ABSOLUTE |bill_value - nova_value| <= allowed_variance check — the literal
    reconciliation rule ("configured allowed variance per line item"). Otherwise
    falls back to the mapping policy's percentage-of-Nova-value tolerance."""
    diff = abs(bill_value - nova_value)
    if allowed_variance is not None:
        return diff <= allowed_variance, None
    if nova_value == 0:
        return bill_value == 0, None
    variance_pct = round(diff / nova_value * 100, 2)
    return variance_pct <= _pct_tolerance(policy, canonical_service), variance_pct


def rate_check(certified_row: Optional[dict], module: Optional[dict], policy: dict,
               canonical_service: Optional[str]) -> dict:
    """rate_status/expected_rate/rate_variance_pct against the CERTIFIED rate card.
    None (no check) unless the line is certified with a matched row and Nova billed it."""
    if certified_row is None or module is None:
        return {"rate_status": None, "expected_rate": None, "rate_variance_pct": None}

    billable_ops = module.get("billableOperations") or 0
    billed_rate = module.get("costPerOperationRupees")

    if billable_ops == 0:
        return {"rate_status": "ZERO_VOLUME", "expected_rate": None, "rate_variance_pct": None}

    expected_rate, comparable = parse_expected_rate(certified_row)
    if not comparable:
        return {"rate_status": "NOT_AUTO_COMPARABLE", "expected_rate": None, "rate_variance_pct": None}

    ok, variance_pct = _within_variance(billed_rate or 0, expected_rate, policy, canonical_service, None)
    return {
        "rate_status": "RATE_OK" if ok else "RATE_MISMATCH",
        "expected_rate": expected_rate,
        "rate_variance_pct": variance_pct,
    }


def nova_self_consistency(module: Optional[dict]) -> Optional[bool]:
    """Does Nova's own billableOperations x costPerOperationRupees match its own
    totalCostRupees? A failure here means Nova's usage ledger disagrees with its own
    cost arithmetic — a defect in Nova's data, distinct from an invoice mismatch."""
    if module is None:
        return None
    ops = module.get("billableOperations") or 0
    rate = module.get("costPerOperationRupees") or 0
    cost = module.get("totalCostRupees") or 0
    expected = ops * rate
    if expected == 0:
        return cost == 0
    return abs(expected - cost) / expected * 100 <= NOVA_SELF_CONSISTENCY_TOL_PCT


def qty_cost_check(module: Optional[dict], invoice_line: Optional[dict], policy: dict,
                    canonical_service: Optional[str]) -> dict:
    """match_status + qty/cost variance between the vendor's actual invoice line and
    Nova's usage ledger for the same canonical service — "is the bill value within
    the allowed variance of the Nova value"."""
    if module is not None and invoice_line is None:
        return {"match_status": "MISSING_IN_INVOICE", "qty_status": None, "qty_variance_pct": None,
                "cost_status": None, "cost_variance_pct": None}
    if module is None and invoice_line is not None:
        return {"match_status": "EXTRA_IN_INVOICE", "qty_status": None, "qty_variance_pct": None,
                "cost_status": None, "cost_variance_pct": None}
    if module is None and invoice_line is None:
        return {"match_status": None, "qty_status": None, "qty_variance_pct": None,
                "cost_status": None, "cost_variance_pct": None}

    allowed_variance = invoice_line.get("allowed_variance")

    qty_ok, qty_variance = _within_variance(
        invoice_line.get("quantity") or 0, module.get("billableOperations") or 0,
        policy, canonical_service, allowed_variance,
    )

    # Cost is qty x rate, and both of those already get their own dedicated check
    # (qty here, rate in rate_check) — checking amount too was a redundant third
    # tolerance on the same underlying discrepancy, just double-counting it.
    # Reconciliation now surfaces only qty and rate mismatches.
    return {
        "match_status": "MATCHED",
        "qty_status": "QTY_OK" if qty_ok else "QTY_MISMATCH", "qty_variance_pct": qty_variance,
        "cost_status": None, "cost_variance_pct": None,
    }


# ── Rollup ───────────────────────────────────────────────────────────────────

_RED_CERT = {"UNCERTIFIED", "AMBIGUOUS"}
_YELLOW_MATCH = {"MISSING_IN_INVOICE", "EXTRA_IN_INVOICE"}
# NOVA_LOGIC_MISSING is a data gap (nobody's confirmed how this rate-card service maps to
# a live Nova module), not a confirmed billing violation like UNCERTIFIED — yellow, not red.
_YELLOW_CERT = {"UNRESOLVED_MODULE", "PENDING_EXTENDED_LOOKUP", "NOVA_LOGIC_MISSING"}


def _rollup(lines: list[dict]) -> dict:
    def count(pred):
        return sum(1 for l in lines if pred(l))

    counts = {
        "total_lines": len(lines),
        "certified_count": count(lambda l: l["certification_status"] == "CERTIFIED"),
        "uncertified_count": count(lambda l: l["certification_status"] == "UNCERTIFIED"),
        "ambiguous_count": count(lambda l: l["certification_status"] == "AMBIGUOUS"),
        "unresolved_module_count": count(lambda l: l["certification_status"] == "UNRESOLVED_MODULE"),
        "pending_extended_lookup_count": count(lambda l: l["certification_status"] == "PENDING_EXTENDED_LOOKUP"),
        "nova_logic_missing_count": count(lambda l: l["certification_status"] == "NOVA_LOGIC_MISSING"),
        "rate_mismatch_count": count(lambda l: l.get("rate_status") == "RATE_MISMATCH"),
        "qty_mismatch_count": count(lambda l: l.get("qty_status") == "QTY_MISMATCH"),
        "cost_mismatch_count": count(lambda l: l.get("cost_status") == "COST_MISMATCH"),
        "missing_in_invoice_count": count(lambda l: l.get("match_status") == "MISSING_IN_INVOICE"),
        "extra_in_invoice_count": count(lambda l: l.get("match_status") == "EXTRA_IN_INVOICE"),
        "self_inconsistent_count": count(lambda l: l.get("nova_self_consistent") is False),
    }
    total_cost = sum(l.get("total_cost_rupees") or 0 for l in lines)
    total_invoice_amount = sum(l.get("invoice_amount") or 0 for l in lines)

    is_red = any(
        l["certification_status"] in _RED_CERT
        or l.get("rate_status") == "RATE_MISMATCH"
        or l.get("qty_status") == "QTY_MISMATCH"
        or l.get("cost_status") == "COST_MISMATCH"
        or l.get("nova_self_consistent") is False
        for l in lines
    )
    is_yellow = any(
        l["certification_status"] in _YELLOW_CERT or l.get("match_status") in _YELLOW_MATCH
        for l in lines
    )
    rollup_status = "RED" if is_red else ("YELLOW" if is_yellow else "GREEN")

    return {
        "rollup_status": rollup_status,
        "total_cost_rupees": total_cost,
        "total_invoice_amount": total_invoice_amount,
        "variance_amount_rupees": round(total_invoice_amount - total_cost, 2),
        **counts,
    }


# ── Entry point ──────────────────────────────────────────────────────────────

def reconcile(mapping: dict, tenant_id: str, brand_or_cost_centre: str, vendor: str, month: str,
              nova_response: dict, invoice_lines: list[dict],
              extended_nova_response: Optional[dict], resolve_alias: ResolveAlias) -> dict:
    """Runs the full flow end to end (§4). Raises LookupError (-> HTTP 404 in
    routes.py) when tenant_id has no entry in the mapping JSON, or resolves to an
    entity that can't receive bills directly (entity_type 'unowned' — e.g. a vendor
    tenant named in the flow spec that has no certified catalog in the mapping yet)."""
    entity = find_entity_by_tenant(mapping, tenant_id)
    if entity is None:
        raise LookupError(f"tenant_id {tenant_id} has no entry in nova_vendor_mapping.json")

    policy = mapping["reconciliation_policy"]
    nova_own_by_service = _index_modules(nova_response, vendor, resolve_alias)
    invoice_by_service = _index_invoice(invoice_lines, vendor, resolve_alias)

    if entity["entity_type"] == "RE":
        lines = _reconcile_recurring(entity, brand_or_cost_centre, vendor, policy,
                                      tenant_id, nova_own_by_service, invoice_by_service)
    elif entity["entity_type"] == "NON-RE":
        nova_extended_by_service = _index_modules(extended_nova_response, vendor, resolve_alias)
        lines = _reconcile_non_recurring(mapping, entity, brand_or_cost_centre, vendor, policy, tenant_id,
                                          nova_own_by_service, nova_extended_by_service, invoice_by_service)
    else:
        raise LookupError(
            f"entity_type '{entity['entity_type']}' has no certified vendor_rates in "
            f"nova_vendor_mapping.json — this vendor tenant cannot be reconciled until one is assigned"
        )

    for line in lines:
        line["service_month"] = month
    return {"run": _rollup(lines), "lines": lines}


def _build_line(canonical: Optional[str], scope: Optional[str], owner_tenant_id: Optional[str],
                 cost_centre: Optional[str], certification_status: str, certified_row: Optional[dict],
                 module_entry: Optional[dict], invoice_entry: Optional[dict], policy: dict) -> dict:
    module = module_entry["module"] if module_entry else None
    invoice_line = invoice_entry["line"] if invoice_entry else None

    if certification_status == "CERTIFIED":
        rc = rate_check(certified_row, module, policy, canonical)
        qc = qty_cost_check(module, invoice_line, policy, canonical)
    else:
        rc = {"rate_status": None, "expected_rate": None, "rate_variance_pct": None}
        qc = {"match_status": None, "qty_status": None, "qty_variance_pct": None,
              "cost_status": None, "cost_variance_pct": None}
        # A line still worth surfacing on the dispatch view even when uncertified/pending —
        # note whether the bill or Nova (or both) actually carried this line item.
        if module is not None or invoice_line is not None:
            qc["match_status"] = (
                "MATCHED" if module is not None and invoice_line is not None
                else "MISSING_IN_INVOICE" if module is not None
                else "EXTRA_IN_INVOICE"
            )

    return {
        "line_item_id": invoice_line.get("line_item_id") if invoice_line else None,
        "module_slug": module_entry["module_slug"] if module_entry else None,
        "invoice_description": invoice_line["description"] if invoice_line else None,
        "canonical_service": canonical,
        "scope": scope,
        "owner_tenant_id": owner_tenant_id,
        "cost_centre": cost_centre,
        "certification_status": certification_status,
        **rc,
        **qc,
        "nova_self_consistent": nova_self_consistency(module),
        "billable_operations": module.get("billableOperations") if module else None,
        "api_call_count": module.get("apiCallCount") if module else None,
        "billed_rate": module.get("costPerOperationRupees") if module else None,
        "total_cost_rupees": module.get("totalCostRupees") if module else None,
        "invoice_qty": invoice_line.get("quantity") if invoice_line else None,
        "invoice_rate": invoice_line.get("rate") if invoice_line else None,
        "invoice_amount": invoice_line.get("amount") if invoice_line else None,
        "allowed_variance": invoice_line.get("allowed_variance") if invoice_line else None,
    }


def _reconcile_recurring(entity: dict, brand_or_cost_centre: str, vendor: str, policy: dict, tenant_id: str,
                          nova_by_service: dict[str, dict], invoice_by_service: dict[str, dict]) -> list[dict]:
    """Flow A — recurring vendor tenant (single scope, never a second Nova call):
    reconcile every bill line item that falls within this tenant's own vendor mapping
    scope against the (single) Nova response."""
    certified = certified_pairs(entity, vendor, brand_or_cost_centre)
    unverified = unverified_pairs(entity, vendor, brand_or_cost_centre)

    lines = []
    for key in sorted(set(nova_by_service) | set(invoice_by_service)):
        module_entry = nova_by_service.get(key)
        invoice_entry = invoice_by_service.get(key)
        canonical = (module_entry or invoice_entry)["canonical"]

        if canonical is not None and canonical in certified:
            status, row = "CERTIFIED", certified[canonical]
        elif canonical is not None and canonical in unverified:
            # The rate card names this service but nobody has confirmed which (if
            # any) live Nova module represents it — don't guess an alias, don't
            # call it a plain rate mismatch; flag the mapping itself as missing.
            status, row = "NOVA_LOGIC_MISSING", None
        elif canonical is None:
            status, row = "UNRESOLVED_MODULE", None
        else:
            status, row = "UNCERTIFIED", None

        lines.append(_build_line(
            canonical, "RE" if status == "CERTIFIED" else None,
            tenant_id if status == "CERTIFIED" else None,
            # The bill's own cost centre. Provisional: cost-centre attribution (§4.4,
            # orchestrator.apply_cost_centre_attribution) may reassign it to the sibling
            # cost centre whose Nova ledger actually accounts for the usage.
            brand_or_cost_centre,
            status, row, module_entry, invoice_entry, policy,
        ))
    return lines


def _reconcile_non_recurring(mapping: dict, entity: dict, brand_or_cost_centre: str, vendor: str, policy: dict,
                              tenant_id: str, nova_own_by_service: dict[str, dict],
                              nova_extended_by_service: dict[str, dict],
                              invoice_by_service: dict[str, dict]) -> list[dict]:
    """Flow B — non-recurring vendor tenant, two phases:
    Phase 1: bill line items within this tenant's own scope, against the own-scope Nova response.
    Phase 2: bill line items outside that scope — identify the owning cost centre, and only
    reconcile once a SECOND, explicitly supplied Nova response for that cost centre exists;
    otherwise PENDING_EXTENDED_LOOKUP, naming the cost centre Nova needs to be called again for."""
    own_certified = certified_pairs(entity, vendor)
    own_unverified = unverified_pairs(entity, vendor)
    owning_re = find_owning_re(mapping, brand_or_cost_centre)
    cc_certified = certified_pairs(owning_re, vendor, brand_or_cost_centre)
    cc_unverified = unverified_pairs(owning_re, vendor, brand_or_cost_centre)
    owning_re_tenant_id = ((owning_re or {}).get("tenant") or {}).get("tenant_id")

    all_keys = set(nova_own_by_service) | set(nova_extended_by_service) | set(invoice_by_service)

    lines = []
    for key in sorted(all_keys):
        invoice_entry = invoice_by_service.get(key)
        own_entry = nova_own_by_service.get(key)
        ext_entry = nova_extended_by_service.get(key)
        canonical = (own_entry or ext_entry or invoice_entry)["canonical"]

        in_own = canonical is not None and canonical in own_certified
        in_cc = canonical is not None and canonical in cc_certified

        if in_own and in_cc:
            # Certified in both catalogs at once — never guess which owns it.
            lines.append(_build_line(canonical, "AMBIGUOUS", None, brand_or_cost_centre,
                                      "AMBIGUOUS", None, own_entry or ext_entry, invoice_entry, policy))
        elif in_own:
            # Phase 1: within this tenant's own mapping scope — reconcile directly, no second call.
            lines.append(_build_line(canonical, "NON_RE_SHARED_IT", tenant_id, brand_or_cost_centre,
                                      "CERTIFIED", own_certified[canonical], own_entry, invoice_entry, policy))
        elif in_cc:
            # Phase 2: outside this tenant's scope — identified cost centre is brand_or_cost_centre's
            # owning RE. Only reconcile once the second Nova pull (extended_nova_response) is present.
            if ext_entry is not None or nova_extended_by_service:
                lines.append(_build_line(canonical, "COST_CENTRE_OWNED", owning_re_tenant_id, brand_or_cost_centre,
                                          "CERTIFIED", cc_certified[canonical], ext_entry, invoice_entry, policy))
            else:
                lines.append(_build_line(canonical, "COST_CENTRE_OWNED", owning_re_tenant_id, brand_or_cost_centre,
                                          "PENDING_EXTENDED_LOOKUP", None, None, invoice_entry, policy))
        elif canonical is None:
            lines.append(_build_line(None, None, None, None, "UNRESOLVED_MODULE", None,
                                      own_entry or ext_entry, invoice_entry, policy))
        elif canonical in own_unverified or canonical in cc_unverified:
            lines.append(_build_line(canonical, None, None, None, "NOVA_LOGIC_MISSING", None,
                                      own_entry or ext_entry, invoice_entry, policy))
        else:
            lines.append(_build_line(canonical, None, None, None, "UNCERTIFIED", None,
                                      own_entry or ext_entry, invoice_entry, policy))
    return lines
