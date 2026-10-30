"""
Bill-level Nova reconciliation, for the Bills page's approval panel.

This is deliberately a **thin adapter over the standalone reconciliation service**
(`services/nova_reconciliation/`), not a second reconciliation engine. The original
`billing_api.py` was 1167 lines and owned its own invoice-vs-ledger logic, its own
LLM line matcher, and its own discrepancy tables; commit 3a7c4e2 deleted it precisely
so one engine decides what a bill reconciles to. Re-adding that logic here — even a
"small" copy of it — would recreate the drift that removal was meant to end: two
answers for the same bill, differing on tolerance, certification, or cost centre.

So this module owns exactly one thing: translating the service's per-line result into
the `BillReconciliation` shape the Bills panel already speaks
(`frontend/src/api/billing.ts`). Every reconciliation decision — certification,
canonical-service resolution, the 2%/20% tolerances, and cost-centre attribution
(NOVA_RECONCILIATION_LOGIC.md §4.4) — is made by the service and only rendered here.

Reads the orchestrator's persisted run rather than recomputing: the orchestrator
(§0) already discovers every mapped-vendor bill, calls Nova, and re-runs itself when
the mapping changes, so a bill's latest run *is* the current answer.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Query
from psycopg2.extras import RealDictCursor

from ..security.auth import require_tenant
from ..config import get_conn, put_conn

log = logging.getLogger("billing_api")

router = APIRouter(prefix="/api/billing", tags=["billing-reconciliation"])

# Where the standalone service lives. Same default as its own config.PORT (8100).
NOVA_RECONCILIATION_URL = os.getenv("NOVA_RECONCILIATION_URL", "http://localhost:8100")
SERVICE_TIMEOUT_SECONDS = 20.0

# The mapping JSON's default tolerance, echoed to the panel for display only — the
# actual comparison already happened in the service, against that same policy.
DISPLAY_TOLERANCE_PCT = 2.0
# Above tolerance but still close enough to read as "look at this" rather than "this is
# wrong". Only used for the combined view's headline (see _headline).
DISPLAY_YELLOW_PCT = 5.0

_RED_CERT = {"UNCERTIFIED", "AMBIGUOUS"}
_YELLOW_CERT = {"UNRESOLVED_MODULE", "PENDING_EXTENDED_LOOKUP", "NOVA_LOGIC_MISSING"}
# §7's on-bill scoping: a bill's panel must describe what the bill actually claimed,
# not the ten-plus services Nova's full-month response happens to carry.
_ON_BILL_MATCH = {"MATCHED", "EXTRA_IN_INVOICE"}


def _line_flag(line: dict) -> str:
    if (
        line.get("qty_status") == "QTY_MISMATCH"
        or line.get("rate_status") == "RATE_MISMATCH"
        or line.get("certification_status") in _RED_CERT
        or line.get("nova_self_consistent") is False
    ):
        return "RED"
    if line.get("certification_status") in _YELLOW_CERT or line.get("match_status") == "EXTRA_IN_INVOICE":
        return "YELLOW"
    return "GREEN"


def _line_note(line: dict) -> str:
    """Why this line is flagged the way it is — including which cost centre owns it,
    which is the whole point of §4.4 and the reason a line can be green on a bill it
    doesn't belong to."""
    bits: list[str] = []
    cert = line.get("certification_status")
    if cert == "NOVA_LOGIC_MISSING":
        bits.append("rate card lists this service, but its Nova module mapping isn't confirmed yet")
    elif cert == "UNCERTIFIED":
        bits.append("not on the certified rate card for this cost centre")
    elif cert == "AMBIGUOUS":
        bits.append("certified in two scopes at once — ownership not resolved")
    elif cert == "UNRESOLVED_MODULE":
        bits.append("couldn't be resolved to a canonical service")
    elif cert == "PENDING_EXTENDED_LOOKUP":
        bits.append("needs a second Nova pull for the owning cost centre")

    attribution = line.get("cost_centre_attribution")
    cost_centre = line.get("cost_centre")
    if attribution == "SIBLING_OWNED":
        bits.append(f"billed on this document but the usage is {cost_centre}'s — attributed there")
    elif attribution == "SHARED":
        bits.append("usage is shared across sibling cost centres")
    elif cost_centre:
        bits.append(f"cost centre: {cost_centre}")

    if line.get("combined_brand_check"):
        bits.append(line["combined_brand_check"])
    if line.get("qty_status") == "QTY_MISMATCH" and line.get("qty_variance_pct") is not None:
        bits.append(f"quantity off by {line['qty_variance_pct']}%")
    if line.get("rate_status") == "RATE_MISMATCH":
        bits.append(f"rate {line.get('billed_rate')} vs certified {line.get('expected_rate')}")
    if line.get("nova_self_consistent") is False:
        bits.append("Nova's own ops x rate disagrees with its own reported cost")
    return " · ".join(bits)


def _to_module(line: dict) -> dict:
    invoice_amount = line.get("invoice_amount")
    nova_cost = line.get("total_cost_rupees")
    variance_amount = (
        round(float(invoice_amount) - float(nova_cost), 2)
        if invoice_amount is not None and nova_cost is not None else None
    )
    return {
        "invoice_description": line.get("invoice_description"),
        "invoice_qty": line.get("invoice_qty"),
        "invoice_rate": line.get("invoice_rate"),
        "invoice_amount": invoice_amount,
        # The service resolves modules by scored matching plus a persisted alias table
        # (§3) — never by an LLM, which the old engine did use. 'alias' is the honest
        # label for both; there is no 'ai' path to report anymore.
        "match_source": "alias" if line.get("module_slug") else None,
        "nova_module": line.get("module_slug"),
        "nova_billable_operations": line.get("billable_operations"),
        "nova_rate": line.get("billed_rate"),
        "nova_cost": nova_cost,
        "variance_amount": variance_amount,
        "variance_pct": line.get("qty_variance_pct"),
        "flag": _line_flag(line),
        "note": _line_note(line),
    }


def _is_comparable(line: dict) -> bool:
    """Whether this line has a figure on BOTH sides to compare.

    Nova carries no entry for a service whose module mapping isn't confirmed yet
    (`NOVA_LOGIC_MISSING`), so such a line has an invoice amount and no Nova amount.
    It is not a mismatch — it's an absence of anything to match against.
    """
    return line.get("invoice_amount") is not None and line.get("total_cost_rupees") is not None


def _headline(on_bill: list[dict], combined: bool, rollup: str) -> dict:
    """The panel's top line: what to compare, against what, and how to colour it.

    Lines Nova has no figure for are summed into the invoice side but contribute nothing
    to the Nova side, so including them makes the percentage measure how much of the bill
    we've mapped rather than how well the bill agrees with Nova — a Signzy bill whose one
    mapped service matched to 0.36% read as 59.12% off because a ₹3.7L unmapped service
    sat on the invoice side alone.

    Only the combined view's headline is derived this way. The per-cost-centre view keeps
    the service's own rollup verbatim, because that is the number cost allocation and the
    dashboard are reconciled against — changing it here would make the panel disagree with
    the persisted run. The combined view has no persisted run to disagree with: it exists
    precisely to answer "does this bill add up if every brand is one pool?".

    The full bill total is still reported (`total_invoice`) alongside the comparable subset,
    so a green headline can never hide what it left out.
    """
    comparable = [l for l in on_bill if _is_comparable(l)]
    unmatched = [l for l in on_bill if not _is_comparable(l)]

    total_invoice = round(sum(float(l.get("invoice_amount") or 0) for l in on_bill), 2)
    total_nova = round(sum(float(l.get("total_cost_rupees") or 0) for l in on_bill), 2)
    comparable_invoice = round(sum(float(l["invoice_amount"]) for l in comparable), 2)
    comparable_nova = round(sum(float(l["total_cost_rupees"]) for l in comparable), 2)
    unmatched_invoice = round(sum(float(l.get("invoice_amount") or 0) for l in unmatched), 2)

    if not combined:
        variance_amount = round(total_invoice - total_nova, 2)
        return {
            "flag": rollup,
            "within_tolerance": rollup == "GREEN",
            "total_invoice": total_invoice,
            "total_nova": total_nova,
            "variance_amount": variance_amount,
            "variance_pct": round(abs(variance_amount) / total_nova * 100, 2) if total_nova else None,
            "comparable_invoice": comparable_invoice,
            "unmatched_invoice_amount": unmatched_invoice or None,
            "unmatched_count": len(unmatched) or None,
        }

    variance_amount = round(comparable_invoice - comparable_nova, 2)
    variance_pct = round(abs(variance_amount) / comparable_nova * 100, 2) if comparable_nova else None
    if variance_pct is None:
        # Nothing comparable at all — there is no percentage to stand behind, so defer to
        # the line-level rollup rather than presenting an absence as agreement.
        flag = rollup
    elif variance_pct <= DISPLAY_TOLERANCE_PCT:
        flag = "GREEN"
    elif variance_pct <= DISPLAY_YELLOW_PCT:
        flag = "YELLOW"
    else:
        flag = "RED"
    return {
        "flag": flag,
        "within_tolerance": flag == "GREEN",
        "total_invoice": total_invoice,
        "total_nova": comparable_nova,
        "variance_amount": variance_amount,
        "variance_pct": variance_pct,
        "comparable_invoice": comparable_invoice,
        "unmatched_invoice_amount": unmatched_invoice or None,
        "unmatched_count": len(unmatched) or None,
    }


def _agent_note(modules: list[dict], lines: list[dict]) -> Optional[str]:
    """One sentence covering the flagged lines. Leads with cost-centre attribution when
    it applies, because "this line is a sibling's cost" explains a flag in a way a bare
    variance number does not."""
    reassigned = [l for l in lines if l.get("cost_centre_attribution") == "SIBLING_OWNED"]
    shared = [l for l in lines if l.get("cost_centre_attribution") == "SHARED"]
    flagged = [m for m in modules if m["flag"] != "GREEN"]

    parts: list[str] = []
    if reassigned:
        names = sorted({l.get("cost_centre") for l in reassigned if l.get("cost_centre")})
        parts.append(
            f"{len(reassigned)} line{'s' if len(reassigned) > 1 else ''} on this bill "
            f"{'are' if len(reassigned) > 1 else 'is'} {', '.join(names)}'s usage, not this cost "
            f"centre's — reconciled against their Nova ledger"
        )
    if shared:
        parts.append(
            f"{len(shared)} line{'s' if len(shared) > 1 else ''} matched only against sibling cost "
            f"centres' combined usage"
        )
    if flagged:
        parts.append(f"{len(flagged)} line{'s differ' if len(flagged) > 1 else ' differs'}")
    return ". ".join(parts) + "." if parts else None


def _queue_state(invoice_id: str, tenant: str) -> tuple[Optional[str], Optional[str]]:
    """(status, reason) from the orchestrator's queue — how we tell "this vendor isn't a
    Nova vendor" (no row at all) apart from "it is, but it couldn't run yet" (SKIPPED with
    a reason). Without this the panel would report every non-Nova bill as an error."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT status, reason FROM nova_reconciliation_bill_queue "
                "WHERE invoice_id = %s AND tenant_id = %s",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        return None, None
    finally:
        put_conn(conn)
    return (row["status"], row["reason"]) if row else (None, None)


def _skip_status(reason: Optional[str]) -> str:
    """Turn the orchestrator's SKIPPED reason into a state the panel can act on.

    Deliberately not one generic "couldn't reconcile": an ambiguous cost centre is
    fixed by tagging the bill (§0.2 refuses to guess between two brands that certify
    identically), a missing service month is an OCR/extraction gap (§0.4), and a
    missing Nova key is configuration. Collapsing them would tell the user something
    went wrong without telling them which lever to pull.
    """
    r = (reason or "").lower()
    if "ambiguous" in r or "cost-centre" in r or "cost centre" in r:
        return "NEEDS_COST_CENTRE"
    if "service month" in r or "no line items" in r:
        return "NEEDS_MONTH"
    # "tenant X has no entry in nova_vendor_mapping.json" means this tenant is not
    # a Nova customer, so reconciliation does not apply — the same ordinary case as
    # a bill with no queue row at all, which already renders nothing. It was showing
    # as a warning naming an internal JSON file and a raw tenant UUID, on every bill
    # of every tenant not on Nova; nothing was wrong and there was nothing to do.
    #
    # Deliberately NOT matched on "nova_vendor_mapping.json": the other reason that
    # names the file — an entity with no certified vendor_rates "until one is
    # assigned" — IS a real configuration gap on a tenant that does use Nova, and
    # has to stay visible.
    if "has no entry in" in r:
        return "NOT_APPLICABLE"
    return "BLOCKED"


@router.get("/bill/{invoice_id}/reconcile")
def reconcile_bill_view(invoice_id: str, refresh: bool = Query(default=False),
                         combined: bool = Query(default=False)):
    """The Bills panel's view of a bill's Nova reconciliation.

    `refresh` re-reads the service rather than recomputing: the orchestrator owns when a
    reconciliation runs (and re-runs it automatically when the mapping changes, §0), so a
    user-triggered recompute here would be a second, competing trigger for the same work.
    """
    tenant = require_tenant()

    # combined=true asks for the all-cost-centres-summed view instead of this cost
    # centre's own — an operator's "does this bill add up if every brand is one pool?".
    path = "/v1/reconciliation/combined" if combined else "/v1/reconciliation"
    try:
        resp = httpx.get(
            f"{NOVA_RECONCILIATION_URL.rstrip('/')}{path}",
            params={"bill_id": invoice_id},
            # The combined view calls Nova once per cost centre, so it needs longer.
            timeout=SERVICE_TIMEOUT_SECONDS * 3 if combined else SERVICE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        log.warning("nova reconciliation service unreachable: %s", e)
        return {"status": "NOVA_UNAVAILABLE", "reason": "the reconciliation service isn't reachable"}

    if combined and resp.status_code == 409:
        # Can't build a combined view — same blocked states as the normal path.
        reason = (resp.json() or {}).get("detail") or "couldn't reconcile across cost centres"
        return {"status": _skip_status(reason), "reason": reason}
    if resp.status_code == 404:
        # No run for this bill. The queue says whether that's expected, and *why* —
        # reported as distinct states rather than one catch-all, because the actions
        # they call for are different: tag a cost centre, wait, or fix configuration.
        status, reason = _queue_state(invoice_id, tenant)
        if status is None:
            # No queue row at all — this supplier isn't a Nova vendor. The ordinary
            # case for most bills, and the panel renders nothing for it.
            return {"status": "NOT_APPLICABLE"}
        if status in ("PENDING", "ERROR"):
            return {"status": "PENDING",
                    "reason": reason or "queued — the reconciliation hasn't run yet"}
        return {"status": _skip_status(reason), "reason": reason or "this bill couldn't be reconciled automatically"}
    if resp.status_code != 200:
        return {"status": "NOVA_UNAVAILABLE",
                "reason": f"the reconciliation service returned {resp.status_code}"}

    body = resp.json()
    run, lines = body["run"], body["lines"]

    # Tenant isolation: the service is keyed by bill_id, so confirm the run it returned
    # actually belongs to the caller's tenant before rendering any of it. The combined
    # view carries no tenant_id (it isn't a persisted run), so it's checked via the queue.
    if combined:
        qstatus, _ = _queue_state(invoice_id, tenant)
        if qstatus is None:
            return {"status": "NOT_APPLICABLE"}
    elif run.get("tenant_id") and str(run["tenant_id"]) != str(tenant):
        return {"status": "NOT_APPLICABLE"}

    on_bill = [l for l in lines if l.get("match_status") in _ON_BILL_MATCH or l.get("invoice_description")]
    modules = [_to_module(l) for l in on_bill]

    headline = _headline(on_bill, combined, run.get("rollup_status") or "GREEN")
    return {
        "status": "COMPLETED",
        "vendor": run.get("vendor"),
        "month": run.get("billing_month"),
        **headline,
        "tolerance_pct": DISPLAY_TOLERANCE_PCT,
        "yellow_pct": DISPLAY_YELLOW_PCT,
        "snapshot_fetched_at": run.get("ingested_at"),
        "computed_at": run.get("ingested_at"),
        "cached": not combined,
        "agent_note": _agent_note(modules, on_bill),
        "modules": modules,
        "combined": combined,
        "combined_over": body.get("combined_over"),
        # Cost centres left out of the sum because their Nova key was missing or the call
        # failed — a partial sum must say so rather than read as complete.
        "combined_missing": body.get("missing_keys") or None,
    }
