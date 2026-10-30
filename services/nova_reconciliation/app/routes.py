"""
Route handlers for docs/nova-reconciliation-service.openapi.yaml.

Each handler: run algorithm.reconcile() (or read persisted rows), persist/read via
psycopg2 against nova_reconciliation_runs / nova_reconciliation_lines. No business
judgement lives here — that's all in algorithm.py.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg2.extras import RealDictCursor

from . import aliases, algorithm, config
from . import mapping as mapping_module
from .models import (
    DashboardSummary,
    DashboardTotals,
    DashboardVendorRow,
    LineResult,
    ReconcileRequest,
    ReconcileResult,
    RunSummary,
)

router = APIRouter(prefix="/v1")


class _PoolConn:
    """Borrow/return a connection from config's pool; roll back on exception."""

    def __enter__(self):
        self.conn = config.get_conn()
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass
        config.put_conn(self.conn, close=exc_type is not None)


def _line_to_model(row: dict) -> LineResult:
    return LineResult(
        line_id=str(row["line_id"]),
        run_id=str(row["run_id"]),
        line_item_id=row.get("line_item_id"),
        owner_tenant_id=str(row["owner_tenant_id"]) if row.get("owner_tenant_id") else None,
        cost_centre=row.get("cost_centre"),
        service_month=row.get("service_month"),
        module_slug=row.get("module_slug"),
        invoice_description=row.get("invoice_description"),
        canonical_service=row.get("canonical_service"),
        scope=row.get("scope"),
        certification_status=row["certification_status"],
        match_status=row.get("match_status"),
        rate_status=row.get("rate_status"),
        qty_status=row.get("qty_status"),
        cost_status=row.get("cost_status"),
        nova_self_consistent=row.get("nova_self_consistent"),
        billed_rate=row.get("billed_rate"),
        expected_rate=row.get("expected_rate"),
        rate_variance_pct=row.get("rate_variance_pct"),
        qty_variance_pct=row.get("qty_variance_pct"),
        cost_variance_pct=row.get("cost_variance_pct"),
        billable_operations=row.get("billable_operations"),
        api_call_count=row.get("api_call_count"),
        total_cost_rupees=row.get("total_cost_rupees"),
        invoice_qty=row.get("invoice_qty"),
        invoice_rate=row.get("invoice_rate"),
        invoice_amount=row.get("invoice_amount"),
        allowed_variance=row.get("allowed_variance"),
        combined_brand_check=row.get("combined_brand_check"),
        cost_centre_attribution=row.get("cost_centre_attribution"),
    )


def _run_to_model(row: dict) -> RunSummary:
    return RunSummary(
        run_id=str(row["run_id"]),
        bill_id=row["bill_id"],
        tenant_id=str(row["tenant_id"]),
        brand_or_cost_centre=row["brand_or_cost_centre"],
        cost_centre_id=str(row["cost_centre_id"]) if row.get("cost_centre_id") else None,
        vendor=row["vendor"],
        billing_month=row["billing_month"],
        nova_reference_id=row.get("nova_reference_id"),
        rollup_status=row["rollup_status"],
        total_lines=row["total_lines"],
        certified_count=row["certified_count"],
        uncertified_count=row["uncertified_count"],
        ambiguous_count=row["ambiguous_count"],
        unresolved_module_count=row["unresolved_module_count"],
        pending_extended_lookup_count=row["pending_extended_lookup_count"],
        nova_logic_missing_count=row.get("nova_logic_missing_count", 0),
        rate_mismatch_count=row["rate_mismatch_count"],
        qty_mismatch_count=row["qty_mismatch_count"],
        cost_mismatch_count=row["cost_mismatch_count"],
        missing_in_invoice_count=row["missing_in_invoice_count"],
        extra_in_invoice_count=row["extra_in_invoice_count"],
        self_inconsistent_count=row["self_inconsistent_count"],
        total_cost_rupees=row["total_cost_rupees"],
        total_invoice_amount=row["total_invoice_amount"],
        variance_amount_rupees=row["variance_amount_rupees"],
        ingested_at=str(row["ingested_at"]),
    )


def persist_reconcile_result(
    conn, *, bill_id: str, tenant_id: str, brand_or_cost_centre: str, vendor: str, month: str,
    nova_response: dict, extended_nova_response: Optional[dict], nova_reference_id: Optional[str],
    result: dict,
) -> ReconcileResult:
    """Insert one run + its lines from an already-computed algorithm.reconcile() result.
    Shared by the HTTP handler (reconcile_bill, below) and orchestrator.py's automatic
    trigger — both compute the same `result` shape via algorithm.reconcile(), this is
    just the one place that knows the INSERT statements. Caller owns the transaction
    (commits or rolls back conn) and the DB connection's lifecycle."""
    run = result["run"]
    lines = result["lines"]

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            INSERT INTO nova_reconciliation_runs
                (bill_id, tenant_id, brand_or_cost_centre, cost_centre_id, vendor, billing_month,
                 nova_reference_id, nova_response_jsonb, extended_nova_response_jsonb, rollup_status,
                 total_lines, certified_count, uncertified_count, ambiguous_count,
                 unresolved_module_count, pending_extended_lookup_count, nova_logic_missing_count,
                 rate_mismatch_count,
                 qty_mismatch_count, cost_mismatch_count, missing_in_invoice_count,
                 extra_in_invoice_count, self_inconsistent_count, total_cost_rupees,
                 total_invoice_amount, variance_amount_rupees)
            VALUES (
                %(bill_id)s, %(tenant_id)s, %(brand)s,
                (SELECT cost_centre_id FROM dim_cost_centres
                 WHERE tenant_id = %(tenant_id)s AND name = %(brand)s LIMIT 1),
                %(vendor)s, %(month)s, %(ref_id)s, %(raw)s, %(extended_raw)s, %(status)s,
                %(total_lines)s, %(certified)s, %(uncertified)s, %(ambiguous)s, %(unresolved)s,
                %(pending)s, %(nova_logic_missing)s, %(rate_mismatch)s, %(qty_mismatch)s, %(cost_mismatch)s,
                %(missing_inv)s, %(extra_inv)s, %(self_inconsistent)s,
                %(total_cost)s, %(total_invoice)s, %(variance)s
            )
            RETURNING run_id, ingested_at
            """,
            {
                "bill_id": bill_id, "tenant_id": tenant_id, "brand": brand_or_cost_centre,
                "vendor": vendor, "month": month,
                "ref_id": nova_reference_id, "raw": json.dumps(nova_response),
                "extended_raw": json.dumps(extended_nova_response) if extended_nova_response else None,
                "status": run["rollup_status"], "total_lines": run["total_lines"],
                "certified": run["certified_count"], "uncertified": run["uncertified_count"],
                "ambiguous": run["ambiguous_count"], "unresolved": run["unresolved_module_count"],
                "pending": run["pending_extended_lookup_count"],
                "nova_logic_missing": run.get("nova_logic_missing_count", 0),
                "rate_mismatch": run["rate_mismatch_count"], "qty_mismatch": run["qty_mismatch_count"],
                "cost_mismatch": run["cost_mismatch_count"], "missing_inv": run["missing_in_invoice_count"],
                "extra_inv": run["extra_in_invoice_count"], "self_inconsistent": run["self_inconsistent_count"],
                "total_cost": run["total_cost_rupees"], "total_invoice": run["total_invoice_amount"],
                "variance": run["variance_amount_rupees"],
            },
        )
        run_row = dict(cur.fetchone())
        run_id = run_row["run_id"]

        line_models: list[LineResult] = []
        for line in lines:
            owner_tenant_id = line.get("owner_tenant_id") or tenant_id
            cur.execute(
                """
                INSERT INTO nova_reconciliation_lines
                    (run_id, owner_tenant_id, line_item_id, module_slug, invoice_description,
                     canonical_service, scope, cost_centre, service_month, certification_status,
                     match_status, rate_status, qty_status, cost_status, nova_self_consistent,
                     billed_rate, expected_rate, rate_variance_pct, qty_variance_pct, cost_variance_pct,
                     billable_operations, api_call_count, total_cost_rupees, invoice_qty, invoice_rate,
                     invoice_amount, allowed_variance, combined_brand_check, cost_centre_attribution)
                VALUES (%(run_id)s, %(owner)s, %(line_item_id)s, %(slug)s, %(inv_desc)s,
                        %(service)s, %(scope)s, %(cost_centre)s, %(service_month)s, %(cert)s,
                        %(match)s, %(rate_status)s, %(qty_status)s, %(cost_status)s, %(self_consistent)s,
                        %(billed)s, %(expected)s, %(rate_var)s, %(qty_var)s, %(cost_var)s,
                        %(billable_ops)s, %(api_calls)s, %(total_cost)s, %(inv_qty)s, %(inv_rate)s,
                        %(inv_amt)s, %(allowed_var)s, %(combined_check)s, %(cc_attribution)s)
                RETURNING line_id
                """,
                {
                    "run_id": run_id, "owner": owner_tenant_id, "line_item_id": line.get("line_item_id"),
                    "slug": line.get("module_slug"), "inv_desc": line.get("invoice_description"),
                    "service": line.get("canonical_service"), "scope": line.get("scope"),
                    "cost_centre": line.get("cost_centre"), "service_month": line.get("service_month"),
                    "cert": line["certification_status"], "match": line.get("match_status"),
                    "rate_status": line.get("rate_status"), "qty_status": line.get("qty_status"),
                    "cost_status": line.get("cost_status"), "self_consistent": line.get("nova_self_consistent"),
                    "billed": line.get("billed_rate"), "expected": line.get("expected_rate"),
                    "rate_var": line.get("rate_variance_pct"), "qty_var": line.get("qty_variance_pct"),
                    "cost_var": line.get("cost_variance_pct"), "billable_ops": line.get("billable_operations"),
                    "api_calls": line.get("api_call_count"), "total_cost": line.get("total_cost_rupees"),
                    "inv_qty": line.get("invoice_qty"), "inv_rate": line.get("invoice_rate"),
                    "inv_amt": line.get("invoice_amount"), "allowed_var": line.get("allowed_variance"),
                    "combined_check": line.get("combined_brand_check"),
                    "cc_attribution": line.get("cost_centre_attribution"),
                },
            )
            line_id = cur.fetchone()["line_id"]
            line_models.append(_line_to_model({
                **line, "line_id": line_id, "run_id": run_id, "owner_tenant_id": owner_tenant_id,
            }))
    conn.commit()

    run_summary = _run_to_model({
        **run_row, "bill_id": bill_id, "tenant_id": tenant_id,
        "brand_or_cost_centre": brand_or_cost_centre, "vendor": vendor, "billing_month": month,
        "nova_reference_id": nova_reference_id, "cost_centre_id": None, **run,
    })
    return ReconcileResult(run=run_summary, lines=line_models)


@router.post("/reconcile", response_model=ReconcileResult)
def reconcile_bill(body: ReconcileRequest) -> ReconcileResult:
    mapping = mapping_module.load()
    nova_response = body.nova_response.model_dump()
    extended_nova_response = body.extended_nova_response.model_dump() if body.extended_nova_response else None
    invoice_lines = [l.model_dump() for l in body.invoice_lines]

    with _PoolConn() as conn:
        def resolve_alias(vendor: str, text: str) -> Optional[str]:
            return aliases.resolve(conn, mapping, vendor, text)

        try:
            result = algorithm.reconcile(
                mapping, body.tenant_id, body.brand_or_cost_centre, body.vendor, body.month,
                nova_response, invoice_lines, extended_nova_response, resolve_alias,
            )
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

        return persist_reconcile_result(
            conn, bill_id=body.bill_id, tenant_id=body.tenant_id,
            brand_or_cost_centre=body.brand_or_cost_centre, vendor=body.vendor, month=body.month,
            nova_response=nova_response, extended_nova_response=extended_nova_response,
            nova_reference_id=body.nova_response.referenceId, result=result,
        )


@router.get("/reconciliation", response_model=ReconcileResult)
def get_reconciliation(
    bill_id: Optional[str] = Query(None), tenant_id: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None), month: Optional[str] = Query(None),
) -> ReconcileResult:
    """Read the latest run either by `bill_id` (the dispatch view's natural key) or
    by `(tenant_id, vendor, month)` — at least one of those combinations is required."""
    if not bill_id and not (tenant_id and vendor and month):
        raise HTTPException(status_code=400, detail="Provide bill_id, or tenant_id + vendor + month")

    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if bill_id:
                cur.execute(
                    "SELECT * FROM nova_reconciliation_runs WHERE bill_id = %s ORDER BY ingested_at DESC LIMIT 1",
                    (bill_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM nova_reconciliation_runs
                    WHERE tenant_id = %s AND vendor = %s AND billing_month = %s
                    ORDER BY ingested_at DESC LIMIT 1
                    """,
                    (tenant_id, vendor, month),
                )
            run_row = cur.fetchone()
            if not run_row:
                raise HTTPException(status_code=404, detail="No run has ever been ingested for this bill/key")

            cur.execute("SELECT * FROM nova_reconciliation_lines WHERE run_id = %s", (run_row["run_id"],))
            line_rows = cur.fetchall()

    return ReconcileResult(
        run=_run_to_model(dict(run_row)),
        lines=[_line_to_model(dict(r)) for r in line_rows],
    )


@router.get("/reconciliation/combined")
def get_reconciliation_combined(bill_id: str = Query(...)) -> dict:
    """Reconcile a bill against every certified cost centre of its entity, summed.

    Computed live and never persisted — the persisted run stays the per-cost-centre
    answer that cost allocation and the dashboard depend on. `combined_over` names the
    cost centres actually included and `missing_keys` any that couldn't be fetched, so a
    partial sum is visible rather than passed off as complete.
    """
    # Imported here, not at module scope: orchestrator imports persist_reconcile_result
    # from this module, so a top-level import would be circular.
    from . import orchestrator

    mapping = mapping_module.load()
    with _PoolConn() as conn:
        try:
            return orchestrator.reconcile_combined(conn, mapping, bill_id)
        except LookupError as e:
            raise HTTPException(status_code=409, detail=str(e))


# ── Raw Nova API explorer ────────────────────────────────────────────────────
#
# A read-only window onto exactly what Nova returns for a (brand, vendor, month), with
# no reconciliation applied. Its point is to answer "what did Nova actually say?"
# separately from "what did we conclude?" — when a bill looks wrong, the first question
# is which of those two is at fault, and until now that meant reading service logs.
#
# Each module is annotated with what it resolves to (§3) and whether that service is
# certified for the chosen brand (§2.1), because a raw slug on its own doesn't tell you
# why a line ended up UNCERTIFIED or NOVA_LOGIC_MISSING.


@router.get("/nova/options")
def get_nova_options(tenant_id: str = Query(...)) -> dict:
    """Valid brand and vendor choices for this tenant, straight from the mapping JSON —
    so the explorer's inputs can only express combinations that could exist."""
    mapping = mapping_module.load()
    entity = algorithm.find_entity_by_tenant(mapping, tenant_id)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} has no entry in nova_vendor_mapping.json")
    brands = list(entity.get("brands") or [])
    if not brands:
        # NON-RE entities carry cost_centers[] instead of brands[].
        brands = [c.get("name") for c in (entity.get("cost_centers") or []) if c.get("name")]
    vendors = sorted({r["vendor"] for r in entity.get("vendor_rates", []) if r.get("vendor")})
    return {"brands": brands, "vendors": vendors,
            "entity_type": entity.get("entity_type"),
            "registered_entity": entity.get("registered_entity")}


@router.get("/nova/raw")
def get_nova_raw(
    tenant_id: str = Query(...), vendor: str = Query(...), month: str = Query(...),
    brand: Optional[str] = Query(None),
) -> dict:
    """Live, unreconciled Nova output for one (brand, vendor, month).

    Uses that brand's own API key (§0.5) — which is what makes brand a real input here
    rather than a filter: a different brand is a different key and therefore a different
    ledger, not a subset of one response.
    """
    from .credentials import NovaKeyNotConfigured, resolve_tenant_nova_key
    from .nova_client import NovaApiError, fetch_billing_monthly

    mapping = mapping_module.load()
    entity = algorithm.find_entity_by_tenant(mapping, tenant_id)
    policy = mapping["reconciliation_policy"]

    # Reject a brand this entity doesn't have, instead of letting §0.5's tenant-wide
    # fallback answer with some other cost centre's ledger under the requested name.
    # That fallback is right for reconciliation (a tenant with one key shouldn't have to
    # register it per brand) but wrong here: this endpoint exists to show exactly what
    # ONE brand's key returns, so silently substituting a different key is a wrong answer,
    # not a lenient one.
    if brand and entity is not None:
        known = list(entity.get("brands") or []) + \
                [c.get("name") for c in (entity.get("cost_centers") or []) if c.get("name")]
        if known and not any((b or "").casefold() == brand.casefold() for b in known):
            # Deduped case-insensitively: brands[] and cost_centers[].name spell the same
            # cost centre differently ('Kreditbuddha' / 'KreditBuddha'), and listing both
            # as if they were two choices makes the error harder to act on, not easier.
            seen, unique = set(), []
            for b in known:
                if b and b.casefold() not in seen:
                    seen.add(b.casefold())
                    unique.append(b)
            raise HTTPException(
                status_code=400,
                detail=f"'{brand}' is not a brand/cost centre of this entity. Known: {sorted(unique)}",
            )

    with _PoolConn() as conn:
        # Which key actually answered — a valid brand with no brand-specific row still
        # falls back to the tenant default, and the reader needs to know that happened.
        key_scope = "tenant-default"
        if brand:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM int_nova_credentials "
                    "WHERE tenant_id = %s AND brand = %s AND is_active = true",
                    (tenant_id, brand),
                )
                if cur.fetchone():
                    key_scope = "brand"
        try:
            creds = resolve_tenant_nova_key(conn, tenant_id, brand)
        except NovaKeyNotConfigured as e:
            raise HTTPException(status_code=409, detail=str(e))
        try:
            body = fetch_billing_monthly(creds["base_url"], creds["api_key"], vendor, month)
        except NovaApiError as e:
            raise HTTPException(status_code=502, detail=str(e))

        certified = algorithm.certified_pairs(entity, vendor, brand) if entity else {}
        unverified = algorithm.unverified_pairs(entity, vendor, brand) if entity else {}

        rows = []
        for m in (body.get("data") or {}).get("modules") or []:
            canonical = aliases.resolve(conn, mapping, vendor, m["module"])
            row = certified.get(canonical) if canonical else None
            expected_rate, comparable = algorithm.parse_expected_rate(row) if row else (None, False)
            billed = m.get("costPerOperationRupees")
            rate_variance = None
            if comparable and expected_rate:
                rate_variance = round(abs((billed or 0) - expected_rate) / expected_rate * 100, 2)
            rows.append({
                **m,
                "canonical_service": canonical,
                "certification": (
                    "CERTIFIED" if canonical in certified
                    else "NOVA_LOGIC_MISSING" if canonical in unverified
                    else "UNRESOLVED_MODULE" if canonical is None
                    else "UNCERTIFIED"
                ),
                "certified_rate": expected_rate,
                "rate_variance_pct": rate_variance,
                "tolerance_pct": policy["account_aggregator_tolerance_pct"]
                                 if canonical == "Account Aggregator" else policy["default_tolerance_pct"],
                # Nova's own arithmetic, checked independently of any invoice (§4).
                "self_consistent": algorithm.nova_self_consistency(m),
            })

    data = body.get("data") or {}
    return {
        "request": {"tenant_id": tenant_id, "brand": brand, "vendor": vendor, "month": month,
                    "base_url": creds["base_url"], "key_scope": key_scope},
        "vendor": data.get("vendor"), "month": data.get("month"),
        "totals": data.get("totals"),
        "reference_id": body.get("referenceId"), "timestamp": body.get("timestamp"),
        "modules": rows,
        "raw": body,
    }


@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    owner_tenant_id: str = Query(...), vendor: Optional[str] = Query(None), period: Optional[str] = Query(None),
) -> DashboardSummary:
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # One row per (vendor, month) that has a run — the LATEST run per
            # vendor+month, since re-reconciling a bill (a mapping fix, a corrected
            # alias, a resubmitted month) supersedes its prior run rather than
            # adding to it. Both the totals below and by_vendor are scoped to just
            # these run_ids — summing every historical run for the same bill would
            # double-count a bill each time it's ever been re-reconciled.
            cur.execute(
                """
                SELECT DISTINCT ON (r.vendor, r.billing_month)
                    r.vendor, r.billing_month AS month, r.rollup_status, r.run_id, r.bill_id
                FROM nova_reconciliation_runs r
                JOIN nova_reconciliation_lines l ON l.run_id = r.run_id
                WHERE l.owner_tenant_id = %(owner)s
                  AND (%(vendor)s::text IS NULL OR r.vendor = %(vendor)s)
                  AND (%(period)s::text IS NULL OR r.billing_month = %(period)s)
                ORDER BY r.vendor, r.billing_month, r.ingested_at DESC
                """,
                {"owner": owner_tenant_id, "vendor": vendor, "period": period},
            )
            by_vendor_rows = cur.fetchall()
            latest_run_ids = [r["run_id"] for r in by_vendor_rows]

            cur.execute(
                """
                SELECT l.certification_status, l.rate_status, l.qty_status, l.cost_status,
                       l.match_status, l.nova_self_consistent, l.total_cost_rupees, l.invoice_amount
                FROM nova_reconciliation_lines l
                WHERE l.owner_tenant_id = %(owner)s AND l.run_id = ANY(%(run_ids)s::uuid[])
                """,
                {"owner": owner_tenant_id, "run_ids": [str(r) for r in latest_run_ids]},
            )
            lines = cur.fetchall()

    # Certification-status tiles describe services actually ON this bill (the
    # invoice line count, not Nova's whole monthly module list) — a line only
    # gets here via MATCHED or EXTRA_IN_INVOICE, both of which mean an invoice
    # line produced it. MISSING_IN_INVOICE lines (Nova billed it, this document
    # never did) have no invoice line at all, so they're excluded from these
    # counts and left entirely to their own "Missing in invoice" tile below —
    # scoping THAT tile the same way would make it always read zero, which
    # defeats its purpose.
    on_bill = [l for l in lines if l["match_status"] in ("MATCHED", "EXTRA_IN_INVOICE")]

    totals = DashboardTotals(
        certified=sum(1 for l in on_bill if l["certification_status"] == "CERTIFIED"),
        uncertified=sum(1 for l in on_bill if l["certification_status"] == "UNCERTIFIED"),
        ambiguous=sum(1 for l in on_bill if l["certification_status"] == "AMBIGUOUS"),
        unresolved_module=sum(1 for l in on_bill if l["certification_status"] == "UNRESOLVED_MODULE"),
        pending_extended_lookup=sum(1 for l in on_bill if l["certification_status"] == "PENDING_EXTENDED_LOOKUP"),
        nova_logic_missing=sum(1 for l in on_bill if l["certification_status"] == "NOVA_LOGIC_MISSING"),
        rate_ok=sum(1 for l in on_bill if l["rate_status"] == "RATE_OK"),
        rate_mismatch=sum(1 for l in on_bill if l["rate_status"] == "RATE_MISMATCH"),
        qty_mismatch=sum(1 for l in on_bill if l["qty_status"] == "QTY_MISMATCH"),
        cost_mismatch=sum(1 for l in on_bill if l["cost_status"] == "COST_MISMATCH"),
        missing_in_invoice=sum(1 for l in lines if l["match_status"] == "MISSING_IN_INVOICE"),
        extra_in_invoice=sum(1 for l in lines if l["match_status"] == "EXTRA_IN_INVOICE"),
        self_inconsistent=sum(1 for l in on_bill if l["nova_self_consistent"] is False),
        variance_amount_rupees=round(sum(
            (l["invoice_amount"] or 0) - (l["total_cost_rupees"] or 0)
            for l in lines if l["match_status"] == "MATCHED"
        ), 2),
    )
    by_vendor = [
        DashboardVendorRow(vendor=r["vendor"], month=r["month"], rollup_status=r["rollup_status"],
                            run_id=str(r["run_id"]), bill_id=r["bill_id"])
        for r in by_vendor_rows
    ]

    return DashboardSummary(owner_tenant_id=owner_tenant_id, period=period, totals=totals, by_vendor=by_vendor)


@router.post("/orchestrator/run-once")
def run_orchestrator_once() -> dict:
    """Forces one discover+process sweep synchronously instead of waiting for the
    next background poll — useful right after a bulk upload, or for testing.
    The background loop in main.py's startup task runs this same sweep on its
    own schedule (NOVA_ORCHESTRATOR_POLL_SECONDS)."""
    from . import orchestrator  # local import: orchestrator.py imports this module, avoid a cycle
    return orchestrator.run_sweep()


@router.get("/orchestrator/status")
def get_orchestrator_status() -> dict:
    """What the automatic trigger has done — how many bills are queued/done/
    skipped/erroring, and why, so a SKIPPED bill (e.g. no Nova key configured
    yet, ambiguous brand) is visible rather than silently missing from the
    dashboard."""
    with _PoolConn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status, count(*) AS n FROM nova_reconciliation_bill_queue GROUP BY status")
            counts = {r["status"]: r["n"] for r in cur.fetchall()}
            cur.execute(
                """
                SELECT invoice_id, tenant_id, vendor, status, reason, attempt_count, last_attempted_at
                FROM nova_reconciliation_bill_queue
                WHERE status IN ('SKIPPED', 'ERROR') OR (status = 'DONE' AND reason IS NOT NULL)
                ORDER BY updated_at DESC LIMIT 50
                """
            )
            issues = [
                {
                    "invoice_id": str(r["invoice_id"]), "tenant_id": str(r["tenant_id"]), "vendor": r["vendor"],
                    "status": r["status"], "reason": r["reason"], "attempt_count": r["attempt_count"],
                    "last_attempted_at": r["last_attempted_at"].isoformat() if r["last_attempted_at"] else None,
                }
                for r in cur.fetchall()
            ]
    return {"counts": counts, "issues": issues}
