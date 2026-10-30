/**
 * Review Hub API client.
 *
 * Note what is deliberately absent: a localStorage cache. Bills and vendors use
 * stale-while-revalidate (see invoices.ts) and that is right for them, but a
 * stale *finding* is a lie — it shows someone a problem they already fixed,
 * which is the fastest way to stop them believing the hub. Counts are cached in
 * sessionStorage purely so the nav badge paints instantly on navigation; finding
 * bodies are never cached across a reload.
 *
 * Base URL: every other module in this directory (invoices.ts, admin.ts,
 * entitlements.ts, factGraph.ts, billing.ts) either sets `axios.defaults.
 * baseURL` or otherwise resolves against `VITE_API_URL`, so a relative path
 * lands on the real backend. This module talks to `fetch()` directly, which
 * has no such base — a bare relative path resolves against the *current
 * page's* origin instead, which on a deployed build is the frontend's own
 * static-hosting domain, not the API. There the request either 404s outright
 * or gets caught by the SPA's own catch-all rewrite, never reaching the
 * backend. `call()` below is the one chokepoint every request in this file
 * goes through (BASE-relative calls and `runAction`'s server-provided
 * `action.endpoint` alike), so prefixing there fixes both in one place.
 */
const API_ROOT = (import.meta.env.VITE_API_URL ?? '') as string

const BASE = '/api/review'

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
export type Lane = 'blocking' | 'money' | 'compliance' | 'quality' | 'suspected'
export type FindingStatus =
  | 'OPEN' | 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED' | 'AUTO_RESOLVED'

/** The five fixed dismissal reasons. Free text would tell the system nothing. */
export const DISMISS_REASONS = [
  { id: 'not_an_issue',    label: "This isn't a problem" },
  { id: 'already_handled', label: 'Already handled elsewhere' },
  { id: 'data_is_wrong',   label: 'The underlying data is wrong' },
  { id: 'not_my_job',      label: "Someone else's to fix" },
  { id: 'never_show_type', label: 'Never show me this kind again' },
] as const
export type DismissReason = (typeof DISMISS_REASONS)[number]['id']

export interface FindingEntity {
  type: string
  id: string
  label: string | null
  role: string
}

export interface FindingAction {
  id: string
  label: string
  /** 'safe' executes here; 'navigate' is a deep link; 'approve' carries an
   *  approval decision, always behind a confirmation; 'notify' sends a message
   *  (to a vendor contact or, honestly, "nowhere yet" for an internal target —
   *  see backend_ocr/app/domain/review/notify_actions.py) and is otherwise
   *  executed exactly like 'safe' — the body is already fully server-composed,
   *  no extra fields to collect. Money never executes here. */
  kind: 'safe' | 'approve' | 'navigate' | 'external' | 'notify'
  endpoint: string | null
  method: string
  /** Inputs the user fills before this runs — a correction, not just a trigger.
   *  `pattern` is applied in the drawer so a malformed value is caught where it
   *  was typed. */
  fields: {
    key: string
    label: string
    kind: string
    value?: unknown
    options?: unknown[]
    placeholder?: string
    pattern?: string
    hint?: string
  }[]
  preview: string
  capability: string
  destructive: boolean
  href: string | null
  body: Record<string, unknown>
  /** Field/value pairs the response must carry for the problem to be gone.
   *  Null means a 2xx is the whole answer. */
  success?: Record<string, unknown> | null
  /** Non-empty when the action only starts work — the answer arrives later, so
   *  the finding stays in the queue. The string is what the user is told.
   *  Optional: findings stored before this existed carry neither key. */
  queued?: string
  /** Which of several executable actions is the expected one. A conflict
   *  offers a real either/or — keep the contract's value, or keep the one on
   *  file — and rendering both as equally weighted buttons leaves the user
   *  guessing which is the change. Absent means 'primary'. */
  emphasis?: 'primary' | 'secondary'
  /** What the user must agree to before this runs. Null means no confirmation. */
  confirm: {
    title?: string
    /** The single change being made, shown as a before/after rather than prose. */
    change?: { label: string; from: string; to: string }
    impact?: { label: string; value: string }[]
    items?: string[]
    requireTyped?: string
  } | null
}

/** A view of the subject the drawer can open in place, declared by the server.
 *  `document` arrives inline; everything else is fetched when it is opened. */
export interface FindingPreview {
  id: string
  label: string
  kind: 'document' | 'timeline' | 'fields' | 'table' | 'text'
  document?: {
    upload_id: string
    label?: string
    page?: number
    highlight?: string
  }
}

export interface Finding {
  finding_id: string
  finding_type: string
  family: string
  /** Set on a queue row that stands for several findings shown as one card
   *  (a vendor's failed payments, every FX item). `count` is the open lines. */
  rollup?: { key: string; count: number; money_at_risk_inr: number | null }
  lane: Lane
  subject: { type: string; id: string | null }
  severity: Severity
  confidence: number
  verification: 'VERIFIED' | 'UNVERIFIABLE' | 'UNVERIFIED' | 'REFUTED'
  is_suspected: boolean
  money_at_risk: number | null
  money_currency: string
  money_at_risk_inr: number | null
  due_at: string | null
  rank: number
  title: string
  detail: string
  /** Arbitrary shape; the renderer dispatches on `kind` and always has a fallback. */
  evidence: Record<string, unknown>
  actions: FindingAction[]
  deep_link: string
  /** The source page this was read off, served by GET /api/docs/{upload_id}. */
  document: {
    upload_id: string
    label?: string
    page?: number
    /** The string worth finding on the page. */
    highlight?: string
  } | null
  /** Present on the detail fetch only — the list has no use for it. */
  previews?: FindingPreview[]
  can_act: boolean
  required_capability: string
  status: FindingStatus
  status_reason: string | null
  status_by: string | null
  snoozed_until: string | null
  detector: string
  source_service: string
  model: string | null
  occurrence_count: number
  first_detected_at: string
  last_seen_at: string
  entities: FindingEntity[]
  /** Present when the list is grouped: this card stands for `count` findings
   *  of the same kind. */
  group?: {
    finding_type: string
    count: number
    money_at_risk_inr: number | null
  }
  cluster?: {
    size: number
    homogeneous: boolean
    money_at_risk_inr: number
    sample: {
      finding_id: string
      subject_id: string | null
      title: string
      money_at_risk_inr: number | null
      differs: boolean
    }[]
  }
  events?: { type: string; actor: string | null; reason: string | null; at: string }[]
}

export interface LaneCount {
  actionable: number
  visible: number
  money_inr: number
}

export interface ReviewCounts {
  lanes: Record<string, LaneCount>
  /** Per type (family) — the queue's Type filter. Absent on an older backend. */
  by_family?: Record<string, { actionable: number; visible: number }>
  by_severity: Record<string, number>
  total_actionable: number
  total_visible: number
  money_at_risk_inr: number
  blocking: number
  snoozed: number
  resolved_this_week: number
  protected_this_week_inr: number
  /** The KPI strip's numbers. Optional because a frontend deploy can land
   *  ahead of the backend that serves them — the tiles fall back rather than
   *  render NaN. */
  resolved_this_month?: number
  resolved_today?: number
  /** Closed as RESOLVED/AUTO_RESOLVED this month; the rest were dismissed. */
  accepted_this_month?: number
  /** Actionable, open, due inside three days / already past due. */
  due_soon?: number
  overdue?: number
  last_scan_at: string | null
  my_capabilities: string[]
  role: string | null
}

export interface FindingQuery {
  lane?: Lane | null
  /** Collapse findings of the same kind into one card. On for the queue. */
  group?: boolean
  finding_type?: string
  scope?: 'actionable' | 'visible' | 'all'
  status?: 'open' | 'snoozed' | 'resolved'
  severity?: Severity[]
  family?: string[]
  subject_type?: string
  subject_id?: string
  q?: string
  min_money_inr?: number
  cursor?: string | null
  limit?: number
  /** Page-at-a-time, for the ungrouped queue. Mutually exclusive with `cursor`
   *  in practice: the server ignores a cursor under any sort but `rank`, and
   *  hands one back only on an unsorted first page. */
  offset?: number
  sort?: SortKey
  direction?: SortDir
}

/** The sortable columns, as the server whitelists them. */
export const SORT_KEYS = ['rank', 'type', 'vendor', 'money', 'priority', 'status', 'age'] as const
export type SortKey = (typeof SORT_KEYS)[number]
export type SortDir = 'asc' | 'desc'

/**
 * `detail` on an error body is a plain string from a handler that raises
 * `HTTPException`, but on a 422 FastAPI puts its own array of pydantic error
 * objects there instead — `[{type, loc, msg, input}, ...]`. Passed straight to
 * `Error()`, that array stringifies as "[object Object],[object Object]",
 * which is what a user saw instead of "account_number: Field required".
 */
function formatErrorDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null | undefined)?.detail
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map(d => {
        if (d && typeof d === 'object') {
          const loc = Array.isArray((d as { loc?: unknown }).loc)
            ? (d as { loc: unknown[] }).loc.filter(p => p !== 'body').join('.')
            : ''
          const msg = (d as { msg?: unknown }).msg
          return loc && msg ? `${loc}: ${msg}` : String(msg ?? d)
        }
        return String(d)
      })
      .join('; ')
  }
  return fallback
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  // Only prefix a bare relative path — a caller that already built an
  // absolute URL (or one already carrying API_ROOT, which BASE-derived paths
  // never do but a future caller might) is left alone rather than double-prefixed.
  const url = path.startsWith('/') && !path.startsWith(API_ROOT + '/') ? `${API_ROOT}${path}` : path
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      detail = formatErrorDetail(await res.json(), detail)
    } catch {
      /* a non-JSON error body is still an error; the status carries it */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export function buildQuery(q: FindingQuery): string {
  const p = new URLSearchParams()
  if (q.lane) p.set('lane', q.lane)
  if (q.group === false) p.set('group', 'false')
  if (q.finding_type) p.append('finding_type', q.finding_type)
  if (q.scope) p.set('scope', q.scope)
  if (q.status) p.set('status', q.status)
  if (q.subject_type) p.set('subject_type', q.subject_type)
  if (q.subject_id) p.set('subject_id', q.subject_id)
  if (q.q) p.set('q', q.q)
  if (q.min_money_inr != null) p.set('min_money_inr', String(q.min_money_inr))
  if (q.cursor) p.set('cursor', q.cursor)
  if (q.limit) p.set('limit', String(q.limit))
  if (q.offset) p.set('offset', String(q.offset))
  if (q.sort && q.sort !== 'rank') p.set('sort', q.sort)
  if (q.direction && q.direction !== 'desc') p.set('direction', q.direction)
  for (const s of q.severity || []) p.append('severity', s)
  for (const f of q.family || []) p.append('family', f)
  return p.toString()
}

export function fetchFindings(q: FindingQuery) {
  return call<{
    items: Finding[]
    next_cursor: string | null
    scope: string
    lane: string | null
    /** How many findings match, ignoring the page. Null on the grouped path,
     *  where there is no page to be 4 of. */
    total: number | null
    offset: number | null
  }>(`${BASE}/findings?${buildQuery(q)}`)
}

export function fetchCounts() {
  return call<ReviewCounts>(`${BASE}/counts`)
}

export function fetchFinding(id: string) {
  return call<Finding>(`${BASE}/findings/${encodeURIComponent(id)}`)
}

/** The open lines of the card `id` is shown in, under the queue's own scope. */
export function fetchRollup(id: string, opts: { status?: 'open' | 'snoozed'; scope?: 'actionable' | 'visible' } = {}) {
  const p = new URLSearchParams()
  if (opts.status) p.set('status', opts.status)
  if (opts.scope) p.set('scope', opts.scope)
  const qs = p.toString()
  return call<{ items: Finding[] }>(`${BASE}/findings/${encodeURIComponent(id)}/rollup${qs ? `?${qs}` : ''}`)
}

/**
 * One preview's payload, fetched when the user opens it.
 *
 * Read fresh on every open rather than cached with the finding: the whole point
 * of showing the chain here is that it is the chain as of now, not as of the
 * last scan.
 */
export function fetchFindingPreview(findingId: string, previewId: string) {
  return call<{ id: string; label: string; kind: string; evidence: Record<string, unknown> }>(
    `${BASE}/findings/${encodeURIComponent(findingId)}/preview/${encodeURIComponent(previewId)}`,
  )
}

export function setFindingStatus(
  id: string,
  body: {
    status: 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED' | 'OPEN'
    reason?: string
    snooze_days?: number
    suppress_scope?: 'exact_key' | 'type_on_subject' | 'type_tenant_wide'
  },
) {
  return call<{ finding: Finding; undo_token: string | null; undo_expires_in: number | null }>(
    `${BASE}/findings/${encodeURIComponent(id)}/status`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

/**
 * Accept some of a bill's proposed corrections.
 *
 * Partial on purpose — see ProposalsPanel. The server applies each id under
 * its own savepoint and reports per id, so one refusal never speaks for the
 * others, and it does NOT close the finding: the adapter's next scan finds
 * nothing left to propose and the worker auto-resolves it.
 */
export function applyBillCoding(body: {
  finding_id: string
  invoice_id: string
  accept: string[]
}) {
  return call<{
    applied: string[]
    failed: { id: string; error: string }[]
    counts: { applied: number; failed: number }
  }>(`${BASE}/bill-coding/apply`, { method: 'POST', body: JSON.stringify(body) })
}

export function undoFinding(id: string, undoToken: string) {
  return call<{ finding: Finding }>(
    `${BASE}/findings/${encodeURIComponent(id)}/undo`,
    { method: 'POST', body: JSON.stringify({ undo_token: undoToken }) },
  )
}

export function bulkStatus(body: {
  finding_ids?: string[]
  finding_type?: string
  /** OPEN un-snoozes: the server clears snoozed_until for every non-SNOOZED
   *  status, so this is how a batch comes back out of the Snoozed tab. */
  status: 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED' | 'OPEN'
  reason?: string
  snooze_days?: number
}) {
  return call<{
    updated: string[]
    failed: { finding_id: string; error: string }[]
    counts: { updated: number; failed: number }
  }>(`${BASE}/findings/bulk`, { method: 'POST', body: JSON.stringify(body) })
}

/**
 * Execute a `kind: 'safe'`, `kind: 'approve'` or `kind: 'notify'` action.
 *
 * These POST to the endpoint the finding names, which is always a route that
 * already owns the operation — the hub never carries a second copy of the logic.
 * An approval reaches this because its endpoint hands straight to the handler
 * that owns approvals, with the actor read from the token. Money does not: a
 * disbursement is `kind: 'navigate'` and stays on the screen with the OTP gate.
 *
 * `notify` is dispatched the same way as `safe` — its `body` is already fully
 * server-composed (destination, subject, message), so there is nothing extra
 * to collect here. The response can legitimately come back `status:
 * "manual_required"` rather than `"ok"` (no mailbox connected, or no inbox to
 * put an internal notification in yet) — that is a real answer for the caller
 * to show, not a fetch failure, so it is returned rather than thrown.
 */
export function runAction(
  finding: Finding,
  action: FindingAction,
  values: Record<string, string> = {},
) {
  if (
    (action.kind !== 'safe' && action.kind !== 'approve' && action.kind !== 'notify') ||
    !action.endpoint
  ) {
    throw new Error('That action is not executable here')
  }
  return call<{ status: string; message?: string }>(action.endpoint, {
    method: action.method || 'POST',
    // What the user typed wins over what the finding was built with: the whole
    // point of an editable field is that the stored value was wrong.
    body: JSON.stringify({ finding_id: finding.finding_id, ...action.body, ...values }),
  })
}

export function rescan(body: { subject_type?: string; subject_id?: string; tier?: string } = {}) {
  return call<{ request_id: string | null; queued: boolean }>(
    `${BASE}/rescan`,
    { method: 'POST', body: JSON.stringify({ subject_type: 'tenant', ...body }) },
  )
}

/** One change made from the Review Hub (backend_ocr/app/review/audit.py). */
export interface AuditEntry {
  audit_id: number
  at: string
  actor: string | null
  actor_role: string | null
  finding_id: string | null
  finding_title: string | null
  finding_type: string | null
  surface: string | null
  method: string
  path: string
  entity_type: string | null
  entity_id: string | null
  entity_label: string | null
  body: Record<string, unknown>
  /** field → [before, after], for a vendor or a bill. */
  changes: Record<string, [unknown, unknown]>
  status_code: number | null
}

export function fetchAudit(opts: { findingId?: string; limit?: number; offset?: number } = {}) {
  const p = new URLSearchParams()
  if (opts.findingId) p.set('finding_id', opts.findingId)
  if (opts.limit) p.set('limit', String(opts.limit))
  if (opts.offset) p.set('offset', String(opts.offset))
  return call<{ items: AuditEntry[]; total: number; offset: number }>(`${BASE}/audit?${p}`)
}
