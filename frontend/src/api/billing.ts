// Nova Vendor Billing Reconciliation — API client.
//
// Scoped to the two endpoints that still exist. The legacy engine's surface
// (/health, /discrepancies, /vendor-accounts, POST /reconcile) went away with
// backend_ocr/app/billing_api.py in commit 3a7c4e2; its client functions had no
// callers left and would have 404'd if anyone had added one.

async function readError(r: Response): Promise<string> {
  const raw = await r.text().catch(() => '')
  try {
    const j = JSON.parse(raw)
    const d = j?.detail ?? j?.message
    if (d) return typeof d === 'string' ? d : JSON.stringify(d)
  } catch { /* not JSON */ }
  if (r.status >= 500) return 'The server ran into a problem. Please try again in a moment.'
  if (r.status === 404) return 'Not found.'
  if (r.status === 401 || r.status === 403) return 'You are not authorised to do this.'
  return raw && !raw.trimStart().startsWith('<') ? raw : `Request failed (${r.status}).`
}

const BASE = () => import.meta.env.VITE_API_URL ?? ''

function authHeaders(): HeadersInit {
  const token = sessionStorage.getItem('onsum_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE()}${path}`, { headers: authHeaders() })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

export interface ReconModule {
  invoice_description: string | null
  invoice_qty: number | null
  invoice_rate: number | null
  invoice_amount: number | null
  match_source: 'alias' | 'ai' | null
  nova_module: string | null
  nova_billable_operations: number | null
  nova_rate: number | null
  nova_cost: number | null
  variance_amount: number | null
  variance_pct: number | null
  flag: 'RED' | 'YELLOW' | 'GREEN'
  note: string
}

export interface BillReconciliation {
  // Distinct blocked states, because each calls for a different action: tag a cost
  // centre, wait for the sweep, fix configuration, or nothing at all.
  status: 'COMPLETED'
        | 'NOT_APPLICABLE'      // supplier isn't a Nova vendor — panel renders nothing
        | 'PENDING'             // queued, the orchestrator hasn't reconciled it yet
        | 'NEEDS_COST_CENTRE'   // ambiguous between sibling cost centres (§0.2) — tag the bill
        | 'NEEDS_MONTH'         // no service month could be resolved (§0.4)
        | 'BLOCKED'             // some other SKIPPED reason, e.g. no Nova key configured
        | 'NOVA_UNAVAILABLE'
  vendor?: string
  month?: string
  reason?: string
  flag?: 'RED' | 'YELLOW' | 'GREEN'
  within_tolerance?: boolean
  tolerance_pct?: number
  /** Variance above tolerance but below this reads YELLOW rather than RED (combined view). */
  yellow_pct?: number
  /** The whole bill, including lines Nova has no figure for. */
  total_invoice?: number
  total_nova?: number
  variance_amount?: number
  variance_pct?: number | null
  /** Invoice total restricted to lines that have a Nova figure to compare against —
   *  what `variance_pct` is actually measured over in the combined view. Equal to
   *  `total_invoice` when every line matched. */
  comparable_invoice?: number
  /** Invoice value that had nothing on Nova's side to compare against (e.g. a service
   *  whose module mapping isn't confirmed). Never folded into the variance, always shown,
   *  so a green headline cannot hide it. */
  unmatched_invoice_amount?: number | null
  unmatched_count?: number | null
  snapshot_fetched_at?: string | null
  agent_note?: string | null   // AI: one overall note explaining the flagged (ⓘ) lines
  modules?: ReconModule[]
  cached?: boolean          // true when served from the persisted result (not freshly recomputed)
  computed_at?: string | null
  /** True when this is the all-cost-centres-summed view rather than this bill's own. */
  combined?: boolean
  /** Cost centres actually included in the sum. */
  combined_over?: string[] | null
  /** Cost centres left OUT of the sum (no Nova key, or the call failed) — a partial
   *  sum must say so rather than read as complete. */
  combined_missing?: string[] | null
}

// Per-bill reconciliation for the approval panel.
// `combined` asks for the all-cost-centres-summed view instead of this bill's own cost
// centre — used when a vendor bills every brand of an entity on one document.
export function fetchBillReconciliation(
  invoiceId: string, refresh = false, combined = false,
): Promise<BillReconciliation> {
  const q = new URLSearchParams()
  if (refresh) q.set('refresh', 'true')
  if (combined) q.set('combined', 'true')
  const qs = q.toString()
  return get(`/api/billing/bill/${invoiceId}/reconcile${qs ? `?${qs}` : ''}`)
}

// Approver's decision on the reconciliation discrepancies for a bill.
// approve → audit only; reject → carry the bill forward to the next approver.
export async function reconcileAction(invoiceId: string, payload: {
  decision: 'approve' | 'reject'
  actor_role: string
  actor_name: string
  comment?: string
}): Promise<{ status: string; decision: string; advanced: boolean }> {
  const r = await fetch(`${BASE()}/api/bills/${invoiceId}/reconcile-action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(payload),
  })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

