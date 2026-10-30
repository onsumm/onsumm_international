"""Pydantic models mirroring docs/nova-reconciliation-service.openapi.yaml."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class NovaModule(BaseModel):
    module: str
    apiCallCount: float
    billableOperations: float
    costPerOperationRupees: float
    totalCostRupees: float


class NovaApiData(BaseModel):
    vendor: str
    month: str
    modules: list[NovaModule]
    totals: dict = Field(default_factory=dict)


class NovaApiResponse(BaseModel):
    success: bool
    data: NovaApiData
    timestamp: Optional[str] = None
    referenceId: Optional[str] = None


class InvoiceLine(BaseModel):
    """One line item from the vendor's actual invoice — the other half of the
    three-way check alongside Nova's own usage ledger. `line_item_id` is the bill's
    own identifier for this row (from OCR/upload extraction) — required so every
    mismatch can be traced back to one specific row on the dispatch view.
    `allowed_variance` optionally overrides the mapping policy's percentage
    tolerance with an absolute |bill_value - nova_value| <= allowed_variance check
    for this one line, per the "configured allowed variance per line item" rule."""
    line_item_id: str
    description: str
    quantity: float
    rate: float
    amount: float
    allowed_variance: Optional[float] = None


class ReconcileRequest(BaseModel):
    bill_id: str
    tenant_id: str
    brand_or_cost_centre: str
    vendor: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    nova_response: NovaApiResponse
    invoice_lines: list[InvoiceLine] = Field(default_factory=list)
    extended_nova_response: Optional[NovaApiResponse] = Field(
        default=None,
        description=(
            "The SECOND Nova pull, scoped to the cost centre(s) identified for the bill's "
            "out-of-scope line items (non-recurring/Non-RE tenants only). Omit on the first "
            "call — out-of-scope lines then come back PENDING_EXTENDED_LOOKUP, naming the cost "
            "centre to call Nova again for. Re-POST with this filled in to complete them."
        ),
    )


class RunSummary(BaseModel):
    run_id: str
    bill_id: str
    tenant_id: str
    brand_or_cost_centre: str
    cost_centre_id: Optional[str] = None
    vendor: str
    billing_month: str
    nova_reference_id: Optional[str] = None
    rollup_status: str
    total_lines: int
    certified_count: int
    uncertified_count: int
    ambiguous_count: int
    unresolved_module_count: int
    pending_extended_lookup_count: int
    nova_logic_missing_count: int
    rate_mismatch_count: int
    qty_mismatch_count: int
    cost_mismatch_count: int
    missing_in_invoice_count: int
    extra_in_invoice_count: int
    self_inconsistent_count: int
    total_cost_rupees: float
    total_invoice_amount: float
    variance_amount_rupees: float
    ingested_at: str


class LineResult(BaseModel):
    line_id: str
    run_id: str
    line_item_id: Optional[str] = None
    owner_tenant_id: Optional[str] = None
    cost_centre: Optional[str] = None
    service_month: Optional[str] = None
    module_slug: Optional[str] = None
    invoice_description: Optional[str] = None
    canonical_service: Optional[str] = None
    scope: Optional[str] = None
    certification_status: str
    match_status: Optional[str] = None
    rate_status: Optional[str] = None
    qty_status: Optional[str] = None
    cost_status: Optional[str] = None
    nova_self_consistent: Optional[bool] = None
    billed_rate: Optional[float] = None
    expected_rate: Optional[float] = None
    rate_variance_pct: Optional[float] = None
    qty_variance_pct: Optional[float] = None
    cost_variance_pct: Optional[float] = None
    billable_operations: Optional[float] = None
    api_call_count: Optional[float] = None
    total_cost_rupees: Optional[float] = None
    invoice_qty: Optional[float] = None
    invoice_rate: Optional[float] = None
    invoice_amount: Optional[float] = None
    allowed_variance: Optional[float] = None
    combined_brand_check: Optional[str] = None
    # Which cost centre this line is truly billed for, once every sibling cost
    # centre's own Nova ledger has been considered (§4.4):
    # OWN | SIBLING_OWNED | SHARED | UNATTRIBUTED. None on the manual
    # POST /v1/reconcile path, which reconciles a single supplied response.
    cost_centre_attribution: Optional[str] = None


class ReconcileResult(BaseModel):
    run: RunSummary
    lines: list[LineResult]


class DashboardTotals(BaseModel):
    certified: int
    uncertified: int
    ambiguous: int
    unresolved_module: int
    pending_extended_lookup: int
    nova_logic_missing: int
    rate_ok: int
    rate_mismatch: int
    qty_mismatch: int
    cost_mismatch: int
    missing_in_invoice: int
    extra_in_invoice: int
    self_inconsistent: int
    variance_amount_rupees: float


class DashboardVendorRow(BaseModel):
    vendor: str
    month: str
    rollup_status: str
    run_id: str
    bill_id: str


class DashboardSummary(BaseModel):
    owner_tenant_id: str
    period: Optional[str] = None
    totals: DashboardTotals
    by_vendor: list[DashboardVendorRow]
