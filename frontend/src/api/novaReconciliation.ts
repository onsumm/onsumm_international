// Client for the Nova Reconciliation Service (services/nova_reconciliation/) —
// a separate deploy from the main API, hence its own base URL and its own
// small fetch helper rather than reusing invoices.ts's axios instance.
// Contract: docs/nova-reconciliation-service.openapi.yaml.
// Design: docs/NOVA_RECONCILIATION_LOGIC.md.

const BASE = () => import.meta.env.VITE_NOVA_RECONCILIATION_API_URL ?? 'http://localhost:8100'

function authHeaders(): HeadersInit {
  const token = sessionStorage.getItem('onsum_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function readError(r: Response): Promise<string> {
  const raw = await r.text().catch(() => '')
  try {
    const j = JSON.parse(raw)
    if (j?.detail) return typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
  } catch { /* not JSON */ }
  return `Request failed (${r.status}).`
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE()}${path}`, { headers: authHeaders() })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

export type Scope = 'RE' | 'NON_RE_SHARED_IT' | 'COST_CENTRE_OWNED' | 'AMBIGUOUS' | null
export type CertificationStatus = 'CERTIFIED' | 'UNCERTIFIED' | 'AMBIGUOUS' | 'UNRESOLVED_MODULE' | 'PENDING_EXTENDED_LOOKUP' | 'NOVA_LOGIC_MISSING'
export type MatchStatus = 'MATCHED' | 'MISSING_IN_INVOICE' | 'EXTRA_IN_INVOICE' | null
export type RateStatus = 'RATE_OK' | 'RATE_MISMATCH' | 'ZERO_VOLUME' | 'NOT_AUTO_COMPARABLE' | null
export type QtyStatus = 'QTY_OK' | 'QTY_MISMATCH' | null
export type CostStatus = 'COST_OK' | 'COST_MISMATCH' | null
export type RollupStatus = 'RED' | 'YELLOW' | 'GREEN'

export type CostCentreAttribution =
  | 'OWN'             // this cost centre's own Nova ledger accounts for the line
  | 'SIBLING_OWNED'   // a sibling cost centre's ledger alone accounts for it
  | 'SHARED'          // only the combined ledger accounts for it — billed across cost centres
  | 'UNATTRIBUTED'    // no cost centre, alone or combined, accounts for it
  | null

export interface LineResult {
  line_id: string
  run_id: string
  line_item_id: string | null
  owner_tenant_id: string | null
  cost_centre: string | null
  service_month: string | null
  module_slug: string | null
  invoice_description: string | null
  canonical_service: string | null
  scope: Scope
  certification_status: CertificationStatus
  match_status: MatchStatus
  rate_status: RateStatus
  qty_status: QtyStatus
  cost_status: CostStatus
  nova_self_consistent: boolean | null
  billed_rate: number | null
  expected_rate: number | null
  rate_variance_pct: number | null
  qty_variance_pct: number | null
  cost_variance_pct: number | null
  billable_operations: number | null
  api_call_count: number | null
  total_cost_rupees: number | null
  invoice_qty: number | null
  invoice_rate: number | null
  invoice_amount: number | null
  allowed_variance: number | null
  combined_brand_check: string | null
  /** Which cost centre the line is truly billed for, once every sibling cost
   *  centre's own Nova ledger has been considered (NOVA_RECONCILIATION_LOGIC §4.4). */
  cost_centre_attribution: CostCentreAttribution
}

export interface RunSummary {
  run_id: string
  bill_id: string
  tenant_id: string
  brand_or_cost_centre: string
  cost_centre_id: string | null
  vendor: string
  billing_month: string
  nova_reference_id: string | null
  rollup_status: RollupStatus
  total_lines: number
  certified_count: number
  uncertified_count: number
  ambiguous_count: number
  unresolved_module_count: number
  pending_extended_lookup_count: number
  nova_logic_missing_count: number
  rate_mismatch_count: number
  qty_mismatch_count: number
  cost_mismatch_count: number
  missing_in_invoice_count: number
  extra_in_invoice_count: number
  self_inconsistent_count: number
  total_cost_rupees: number
  total_invoice_amount: number
  variance_amount_rupees: number
  ingested_at: string
}

export interface ReconcileResult {
  run: RunSummary
  lines: LineResult[]
}

export interface DashboardVendorRow {
  vendor: string
  month: string
  rollup_status: RollupStatus
  run_id: string
  bill_id: string
}

export interface DashboardSummary {
  owner_tenant_id: string
  period: string | null
  totals: {
    certified: number
    uncertified: number
    ambiguous: number
    unresolved_module: number
    pending_extended_lookup: number
    nova_logic_missing: number
    rate_ok: number
    rate_mismatch: number
    qty_mismatch: number
    cost_mismatch: number
    missing_in_invoice: number
    extra_in_invoice: number
    self_inconsistent: number
    variance_amount_rupees: number
  }
  by_vendor: DashboardVendorRow[]
}

export function getDashboardSummary(ownerTenantId: string, opts?: { vendor?: string; period?: string }): Promise<DashboardSummary> {
  const q = new URLSearchParams({ owner_tenant_id: ownerTenantId })
  if (opts?.vendor) q.set('vendor', opts.vendor)
  if (opts?.period) q.set('period', opts.period)
  return get<DashboardSummary>(`/v1/dashboard/summary?${q.toString()}`)
}

export function getReconciliation(vendor: string, month: string, tenantId: string): Promise<ReconcileResult>
export function getReconciliation(billId: string): Promise<ReconcileResult>
export function getReconciliation(a: string, b?: string, c?: string): Promise<ReconcileResult> {
  const q = new URLSearchParams(
    b !== undefined && c !== undefined
      ? { tenant_id: c, vendor: a, month: b }
      : { bill_id: a },
  )
  return get<ReconcileResult>(`/v1/reconciliation?${q.toString()}`)
}

// ── Raw Nova API explorer ────────────────────────────────────────────────────
// Shows what Nova actually returned for a (brand, vendor, month), with no
// reconciliation applied — so "what did Nova say?" can be answered separately
// from "what did we conclude?".

export interface NovaOptions {
  brands: string[]
  vendors: string[]
  entity_type: string | null
  registered_entity: string | null
}

export interface NovaRawModule {
  module: string
  apiCallCount: number
  billableOperations: number
  costPerOperationRupees: number
  totalCostRupees: number
  canonical_service: string | null
  certification: CertificationStatus
  certified_rate: number | null
  rate_variance_pct: number | null
  tolerance_pct: number
  self_consistent: boolean | null
}

export interface NovaRawResponse {
  request: {
    tenant_id: string; brand: string | null; vendor: string; month: string; base_url: string
    /** Which credential answered: the brand's own key, or the tenant-wide default
     *  (§0.5's fallback). A brand with no key of its own still returns data — just
     *  not its own ledger — so the UI has to say which it was. */
    key_scope: 'brand' | 'tenant-default'
  }
  vendor: string | null
  month: string | null
  totals: { totalBillableOperations: number; totalCostRupees: number } | null
  reference_id: string | null
  timestamp: string | null
  modules: NovaRawModule[]
  raw: unknown
}

export function getNovaOptions(tenantId: string): Promise<NovaOptions> {
  return get<NovaOptions>(`/v1/nova/options?tenant_id=${encodeURIComponent(tenantId)}`)
}

export function getNovaRaw(
  tenantId: string, vendor: string, month: string, brand?: string,
): Promise<NovaRawResponse> {
  const q = new URLSearchParams({ tenant_id: tenantId, vendor, month })
  if (brand) q.set('brand', brand)
  return get<NovaRawResponse>(`/v1/nova/raw?${q.toString()}`)
}
