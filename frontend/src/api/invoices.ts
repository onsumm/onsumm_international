import axios from 'axios'

import { reviewHeaders } from './reviewContext'
import type { GstBreakdownData } from '../components/GstBreakdown'

// In production (Netlify/Vercel free tier) call Render directly.
// In dev, vite proxy handles /api → localhost:8001
if (import.meta.env.VITE_API_URL) {
  axios.defaults.baseURL = import.meta.env.VITE_API_URL
}

// ── Auth: attach the login bearer token to every request ─────────────────────
// The backend derives the tenant (and roles) solely from this token, so it must
// ride on every API call. Kept in one place so all axios + fetch traffic is
// covered (see the window.fetch wrapper below for non-axios calls).
export function getAuthToken(): string | null {
  return sessionStorage.getItem('onsum_token')
}

// ── Silent token refresh ─────────────────────────────────────────────────────
// Access tokens are short-lived (60 min). When one expires the backend returns
// 401; we transparently exchange the stored refresh token for a fresh pair and
// retry the request, so the user is never bounced to the login screen mid-work.
const _nativeFetch = window.fetch.bind(window)   // captured before the patch below
const API_ROOT = (import.meta.env.VITE_API_URL ?? '') as string

export function getRefreshToken(): string | null {
  return sessionStorage.getItem('onsum_refresh')
}

export function setTokens(token: string, refreshToken?: string | null): void {
  sessionStorage.setItem('onsum_token', token)
  if (refreshToken) sessionStorage.setItem('onsum_refresh', refreshToken)
}

export function clearTokens(): void {
  sessionStorage.removeItem('onsum_token')
  sessionStorage.removeItem('onsum_refresh')
}

// Only endpoints that mint/exchange tokens — never trigger a refresh-retry loop
// on these.
function isAuthEndpoint(url: string): boolean {
  return url.includes('/api/login') || url.includes('/api/auth/')
}

/**
 * The platform (ops) surface, which authenticates with a different token stored
 * under a different key (api/admin.ts).
 *
 * Without this, an expired ops token would 401 here, trigger a *tenant* refresh,
 * and — if that refresh failed — dispatch 'auth:logout' and sign the tenant user
 * out of a session that was never the problem. api/admin.ts does its own refresh.
 */
export function isAdminApiUrl(url: string): boolean {
  return /\/api\/admin(\/|\?|$)/.test(url)
}

let _refreshPromise: Promise<string | null> | null = null

// Single-flight: many requests can 401 at once; they all await one refresh.
export function refreshTokens(): Promise<string | null> {
  if (_refreshPromise) return _refreshPromise
  const refresh = getRefreshToken()
  if (!refresh) return Promise.resolve(null)

  _refreshPromise = (async () => {
    try {
      const res = await _nativeFetch(`${API_ROOT}/api/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!res.ok) {
        // Refresh token is dead (expired / revoked / reuse-detected) → sign out.
        clearTokens()
        window.dispatchEvent(new CustomEvent('auth:logout'))
        return null
      }
      const data = await res.json()
      setTokens(data.token, data.refresh_token)
      if (data.user) sessionStorage.setItem('onsum_auth', JSON.stringify(data.user))
      return data.token as string
    } catch {
      return null
    } finally {
      _refreshPromise = null
    }
  })()
  return _refreshPromise
}

axios.interceptors.request.use(cfg => {
  const token = getAuthToken()
  if (token) {
    cfg.headers = cfg.headers ?? {}
    ;(cfg.headers as Record<string, string>).Authorization = `Bearer ${token}`
  }
  // A change made from a Review card is tagged so the server audits it.
  const review = reviewHeaders(cfg.method)
  if (Object.keys(review).length) {
    cfg.headers = cfg.headers ?? {}
    Object.assign(cfg.headers as Record<string, string>, review)
  }
  return cfg
})

// On 401, refresh the access token once and replay the request transparently.
axios.interceptors.response.use(
  res => res,
  async err => {
    const cfg = err.config as (typeof err.config & { _retried?: boolean }) | undefined
    const status = err.response?.status
    const url = cfg?.url ?? ''
    if (status === 401 && cfg && !cfg._retried && !isAuthEndpoint(url) && !isAdminApiUrl(url)) {
      cfg._retried = true
      const newToken = await refreshTokens()
      if (newToken) {
        cfg.headers = cfg.headers ?? {}
        ;(cfg.headers as Record<string, string>).Authorization = `Bearer ${newToken}`
        return axios(cfg)
      }
    }
    return Promise.reject(err)
  }
)

// Patch global fetch to (a) attach the bearer token to same-app API calls and
// (b) transparently refresh + retry on 401.
const _origFetch = window.fetch.bind(window)
window.fetch = async (input, init) => {
  const url = typeof input === 'string' ? input : (input as Request).url

  // Attach Authorization for our own API (relative /api/... or the configured
  // API host). Leave third-party requests and requests that already set it
  // untouched. Login / password-reset run before a token exists — harmless
  // no-ops since getAuthToken() returns null then.
  const apiHost = (import.meta.env.VITE_API_URL ?? '') as string
  const isOurApi = url.includes('/api/') && (apiHost === '' || url.startsWith(apiHost) || url.startsWith('/'))
  const canAttach = isOurApi && !(input instanceof Request)
  const review = canAttach ? reviewHeaders(init?.method) : {}
  const buildInit = (tok: string | null): RequestInit | undefined => {
    if ((!tok || !canAttach) && !Object.keys(review).length) return init
    const headers = new Headers(init?.headers || {})
    if (tok && canAttach && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${tok}`)
    // A change made from a Review card is tagged so the server audits it.
    for (const [k, v] of Object.entries(review)) headers.set(k, v)
    return { ...init, headers }
  }

  let res = await _origFetch(input, buildInit(getAuthToken()))

  // Transparent refresh + single retry on 401 (skip the auth endpoints themselves).
  if (res.status === 401 && canAttach && !isAuthEndpoint(url) && !isAdminApiUrl(url)) {
    const newToken = await refreshTokens()
    if (newToken) res = await _origFetch(input, buildInit(newToken))
  }

  return res
}
// ─────────────────────────────────────────────────────────────────────────────

export type InvoicePayload = Record<string, unknown>

// ── File fingerprint ──────────────────────────────────────────────────────
async function fileHash(file: File): Promise<string> {
  const buf = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
}

// ── Cache helpers ─────────────────────────────────────────────────────────
const CACHE_PREFIX = 'onsum_ocr_'
const MAX_CACHE_ENTRIES = 50

function cacheGet(hash: string): InvoicePayload | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + hash)
    if (!raw) return null
    const { data, ts } = JSON.parse(raw)
    // expire after 7 days
    if (Date.now() - ts > 7 * 24 * 60 * 60 * 1000) {
      localStorage.removeItem(CACHE_PREFIX + hash)
      return null
    }
    return data
  } catch {
    return null
  }
}

function cacheSet(hash: string, data: InvoicePayload): void {
  try {
    // evict oldest entries if at limit
    const keys = Object.keys(localStorage)
      .filter(k => k.startsWith(CACHE_PREFIX))
      .map(k => ({ key: k, ts: JSON.parse(localStorage.getItem(k)!).ts as number }))
      .sort((a, b) => a.ts - b.ts)
    while (keys.length >= MAX_CACHE_ENTRIES) {
      localStorage.removeItem(keys.shift()!.key)
    }
    localStorage.setItem(CACHE_PREFIX + hash, JSON.stringify({ data, ts: Date.now() }))
  } catch {
    // localStorage full — skip silently
  }
}

// ── API calls ─────────────────────────────────────────────────────────────
export async function ocrInvoice(file: File): Promise<InvoicePayload> {
  const hash = await fileHash(file)

  const cached = cacheGet(hash)
  if (cached) return cached

  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post<InvoicePayload>('/api/invoices/ocr', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

  cacheSet(hash, data)
  return data
}

export async function ocrInvoiceBulk(files: File[]): Promise<InvoicePayload[]> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  const { data } = await axios.post<{ results: InvoicePayload[] }>('/api/invoices/ocr-bulk', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data.results
}

export interface IngestResult {
  status: string
  supplier_id: string
  upload_id: string
  ocr_id: string
  stg_bill_id: string
  invoice_id: string
  line_items_count: number
  is_duplicate?: boolean
  duplicate_invoice_number?: string | null
}

export async function ingestInvoice(payload: InvoicePayload, companyId?: string, brandId?: string, costCenterId?: string | null): Promise<IngestResult> {
  const { data } = await axios.post<IngestResult>('/api/invoices/ingest', { payload, company_id: companyId ?? null, brand_id: brandId ?? null, cost_center_id: costCenterId ?? null })
  return data
}

export function clearOcrCache(): void {
  Object.keys(localStorage)
    .filter(k => k.startsWith(CACHE_PREFIX))
    .forEach(k => localStorage.removeItem(k))
}

export function getOcrCacheSize(): number {
  return Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX)).length
}

export interface Vendor {
  supplier_id: string
  /** Brand / trading name — what bills and reports show. */
  name: string
  /** Registered name, as on the GST portal. Same as `name` unless the portal
   *  says otherwise; absent from an older backend. */
  legal_name?: string | null
  gstn: string | null
  email: string | null
  payment_terms_days: number
  status: string
  is_active: boolean
  source: string
  total_bills: number
  total_billed: number
  outstanding_amount: number
  last_invoice_date: string | null
  created_at: string | null
  bank_account: string | null
  bank_ifsc: string | null
  bank_verified: boolean
  pan: string | null
  pan_dup_deferred?: boolean
  has_pan_duplicate?: boolean
  vendor_category_id: string | null
  vendor_category_name: string | null
  brand_id: string | null
  /** Where the vendor is based and taxed (ISO country codes); absent for older rows. */
  country_code?: string | null
  tax_residency?: string | null
  /** Foreign tax documents on file, e.g. {"W-8BEN-E": "...", "tax_id": "EIN 88-..."}. */
  tax_docs?: Record<string, string> | null
  /**
   * Verification under the tenant's own rules: GSTIN for an Indian tenant, a
   * verified payout wallet / tax ID for an international one. Absent from an
   * older backend, in which case `status` (the GST check) is all there is.
   */
  verification?: VendorVerification | null
}

export interface VendorVerification {
  status: 'VERIFIED' | 'PENDING' | 'NOT_STARTED' | string
  /** What it rests on, e.g. ["gstin"] or ["wallet", "tax_id"]. */
  basis: string[]
  /** Ready to show, e.g. "Wallet verified". */
  label: string
}

const VENDORS_CACHE_TTL = 5 * 60 * 1000

function vendorsCacheKey(companyId?: string, brandId?: string) {
  return `onsum_vendors_cache_${companyId ?? 'default'}_${brandId ?? 'all'}`
}

function vendorsCacheGet(companyId?: string, brandId?: string): Vendor[] | null {
  try {
    const key = vendorsCacheKey(companyId, brandId)
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const { data, ts } = JSON.parse(raw)
    if (Date.now() - ts > VENDORS_CACHE_TTL) {
      localStorage.removeItem(key)
      return null
    }
    return data
  } catch { return null }
}

function vendorsCacheSet(vendors: Vendor[], companyId?: string, brandId?: string): void {
  try {
    localStorage.setItem(vendorsCacheKey(companyId, brandId), JSON.stringify({ data: vendors, ts: Date.now() }))
  } catch {}
}

export function invalidateVendorsCache(companyId?: string): void {
  Object.keys(localStorage).forEach(k => {
    if (k.startsWith(`onsum_vendors_cache_${companyId ?? 'default'}`)) localStorage.removeItem(k)
  })
}

export async function createVendor(payload: {
  name: string
  gstn?: string
  email?: string
  payment_terms_days?: number
  company_id?: string
  bank_account?: string
  bank_ifsc?: string
  country_code?: string
}): Promise<{
  supplier_id: string
  legal_name?: string
  trade_name?: string
  gstin_verified?: boolean
  gstin_status?: string
  pan?: string
  pan_verified?: boolean
  pan_warning?: string
  bank_verified?: boolean
  bank_skipped?: boolean
  name_at_bank?: string
  bank_warning?: string
}> {
  const { data } = await axios.post('/api/vendors', payload)
  return data
}

export async function updateVendor(
  supplierId: string,
  body: {
    name?: string
    legal_name?: string
    email?: string
    payment_terms_days?: number
    pan?: string
    vendor_category_id?: string | null
  },
  companyId?: string,
): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.patch(`/api/vendors/${supplierId}`, body, { params })
}

export interface SupplierGstin {
  id: string | null
  gstin: string
  pan: string | null
  is_primary: boolean
  status: 'VERIFIED' | 'UNVERIFIED' | 'FAILED'
  trade_name: string | null
  legal_name: string | null
  gstin_status: string | null
  error_message: string | null
  verified_at: string | null
  created_at: string | null
}

export async function fetchSupplierGstins(supplierId: string, companyId?: string): Promise<SupplierGstin[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get(`/api/vendors/${supplierId}/gstins`, { params })
  return data
}

export async function addSupplierGstin(supplierId: string, gstin: string, companyId?: string): Promise<SupplierGstin> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post(`/api/vendors/${supplierId}/gstins`, { gstin }, { params })
  return data
}

export async function reverifySupplierGstin(supplierId: string, gstinId: string, companyId?: string): Promise<SupplierGstin> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post(`/api/vendors/${supplierId}/gstins/${gstinId}/reverify`, {}, { params })
  return data
}

export async function reverifyPrimaryGstin(supplierId: string, companyId?: string): Promise<SupplierGstin> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post(`/api/vendors/${supplierId}/gstins/primary/reverify`, {}, { params })
  return data
}

export async function deleteSupplierGstin(supplierId: string, gstinId: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/vendors/${supplierId}/gstins/${gstinId}`, { params })
}

// ── PAN-based duplicate detection & merge ──────────────────────────────────
export interface PanDuplicate {
  supplier_id: string
  name: string | null
  gstn: string | null
  pan: string | null
  status: string | null
  bill_count: number
}

export async function fetchVendorPanDuplicates(supplierId: string, companyId?: string): Promise<{ pan: string | null; duplicates: PanDuplicate[] }> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get(`/api/vendors/${supplierId}/pan-duplicates`, { params })
  return data
}

export async function mergeVendors(
  sourceSupplierId: string,
  targetSupplierId: string,
  opts?: { actor_name?: string; actor_role?: string },
): Promise<{ status: string; target_supplier_id: string; target_name: string | null; moved: Record<string, number> }> {
  const { data } = await axios.post(`/api/vendors/${sourceSupplierId}/merge`, {
    target_supplier_id: targetSupplierId,
    actor_name: opts?.actor_name,
    actor_role: opts?.actor_role,
  })
  return data
}

// Record (or clear) the user's choice to keep a PAN-duplicate vendor separate.
export async function deferVendorDedup(supplierId: string, deferred = true): Promise<void> {
  await axios.post(`/api/vendors/${supplierId}/defer-dedup`, { deferred })
}

export async function bulkCreateVendorsExcel(file: File, companyId?: string): Promise<{
  created: number; skipped: number; errors: string[]
}> {
  const form = new FormData()
  form.append('file', file)
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post('/api/vendors/bulk-excel', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    params,
  })
  return data
}

export async function fetchVendors(forceRefresh = false, companyId?: string, onBackground?: (v: Vendor[]) => void, brandId?: string): Promise<Vendor[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (brandId) params.brand_id = brandId
  const doFetch = async () => {
    const { data } = await axios.get<{ vendors: Vendor[]; total: number }>('/api/vendors', { params })
    vendorsCacheSet(data.vendors, companyId, brandId)
    return data.vendors
  }
  if (!forceRefresh) {
    const cached = vendorsCacheGet(companyId, brandId)
    if (cached) {
      // Stale-while-revalidate: return cache immediately, refresh silently in background
      doFetch().then(fresh => onBackground?.(fresh)).catch(() => {})
      return cached
    }
  }
  return doFetch()
}

export async function requestVendorInfo(supplierId: string, payload: {
  requested_by: string
  email_to?: string
  company_id?: string
}): Promise<{ status: string; email_to: string; subject: string; body: string }> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/request-info`, payload)
  return data
}

export interface BankAccount {
  id: string
  account_number: string
  ifsc: string | null
  bank_name: string | null
  is_primary: boolean
  verified: boolean
  name_at_bank: string | null
  created_at: string | null
}

export async function fetchVendorBankAccounts(supplierId: string, companyId?: string): Promise<BankAccount[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get(`/api/vendors/${supplierId}/bank-accounts`, { params })
  return data
}

export async function addVendorBankAccount(supplierId: string, body: { account_number: string; ifsc?: string; bank_name?: string; is_primary?: boolean; company_id?: string }): Promise<BankAccount> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/bank-accounts`, body)
  return data
}

export async function deleteVendorBankAccount(supplierId: string, accountId: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/vendors/${supplierId}/bank-accounts/${accountId}`, { params })
}

export async function setPrimaryBankAccount(supplierId: string, accountId: string, companyId?: string): Promise<BankAccount[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post(`/api/vendors/${supplierId}/bank-accounts/${accountId}/set-primary`, null, { params })
  return data
}

export interface VendorContact {
  id: string
  name: string
  role: string | null
  email: string | null
  phone: string | null
  is_primary: boolean
  created_at: string | null
}

export async function fetchVendorContacts(supplierId: string, companyId?: string): Promise<VendorContact[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get(`/api/vendors/${supplierId}/contacts`, { params })
  return data
}

export async function addVendorContact(supplierId: string, body: { name: string; role?: string; email?: string; phone?: string; is_primary?: boolean; company_id?: string }): Promise<VendorContact> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/contacts`, body)
  return data
}

export async function deleteVendorContact(supplierId: string, contactId: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/vendors/${supplierId}/contacts/${contactId}`, { params })
}

export async function setPrimaryVendorContact(supplierId: string, contactId: string, companyId?: string): Promise<VendorContact[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.post(`/api/vendors/${supplierId}/contacts/${contactId}/set-primary`, null, { params })
  return data
}

// ── User email connection (Gmail OAuth) ──────────────────────────────────────
export interface EmailConnection {
  connected: boolean
  provider?: string
  email?: string
  status?: string
}

export async function fetchEmailConnection(companyId: string, username: string): Promise<EmailConnection> {
  const { data } = await axios.get('/api/me/email-connection', { params: { company_id: companyId, username } })
  return data
}

export async function startEmailAuthorize(companyId: string, username: string, provider = 'google'): Promise<string> {
  const { data } = await axios.get('/api/me/email-connection/authorize', { params: { company_id: companyId, username, provider } })
  return data.auth_url as string
}

export async function disconnectEmail(companyId: string, username: string, provider?: string): Promise<void> {
  // No provider => the server disconnects whichever mailbox the user linked.
  await axios.delete('/api/me/email-connection', { params: { company_id: companyId, username, provider } })
}

export async function sendContactEmail(supplierId: string, contactId: string, body: { subject?: string; body: string; company_id: string; username: string; cc?: string; thread_id?: string; files?: File[] }): Promise<{ status: string; email_to: string; from: string; thread_id?: string }> {
  const fd = new FormData()
  fd.append('body', body.body)
  fd.append('company_id', body.company_id)
  fd.append('username', body.username)
  if (body.subject) fd.append('subject', body.subject)
  if (body.cc) fd.append('cc', body.cc)
  if (body.thread_id) fd.append('thread_id', body.thread_id)
  for (const f of body.files ?? []) fd.append('files', f)
  const { data } = await axios.post(`/api/vendors/${supplierId}/contacts/${contactId}/email`, fd)
  return data
}

export interface ThreadAttachment {
  filename: string
  mime_type: string
  size: number
  attachment_id: string
}
export interface ThreadMessage {
  id: string | null
  outbound: boolean
  from: string
  date: string
  subject: string
  body: string
  attachments?: ThreadAttachment[]
}
export interface ThreadSummary {
  thread_id: string
  subject: string
  last_body: string
  last_at: string
  sent_count: number
}
export interface ContactThreads {
  me: string | null
  connected: boolean
  threads: ThreadSummary[]
}
export interface ContactThread {
  me: string | null
  read_available: boolean
  messages: ThreadMessage[]
}

export async function fetchContactThreads(supplierId: string, contactId: string, companyId: string, username: string): Promise<ContactThreads> {
  const { data } = await axios.get(`/api/vendors/${supplierId}/contacts/${contactId}/threads`, { params: { company_id: companyId, username } })
  return data
}

export async function fetchContactThread(supplierId: string, contactId: string, threadId: string, companyId: string, username: string): Promise<ContactThread> {
  const { data } = await axios.get(`/api/vendors/${supplierId}/contacts/${contactId}/thread`, { params: { thread_id: threadId, company_id: companyId, username } })
  return data
}

export interface ThreadAction { label: string; detail: string }
export interface ThreadSummaryAI { summary: string; actions: ThreadAction[] }

export async function summarizeContactThread(supplierId: string, contactId: string, threadId: string, companyId: string, username: string): Promise<ThreadSummaryAI> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/contacts/${contactId}/thread/summarize`, null, { params: { thread_id: threadId, company_id: companyId, username } })
  return data
}

export async function fetchVendorOutreach(supplierId: string, companyId?: string): Promise<{
  logs: { id: string; requested_by: string; requested_at: string | null; email_to: string | null; subject: string | null; status: string }[]
}> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get(`/api/vendors/${supplierId}/outreach`, { params })
  return data
}

export interface Bill {
  invoice_id: string
  id: string
  supplier_id: string | null
  invoice_date: string | null
  due_date: string | null
  status: string
  allowed_roles?: string[]
  allowed_usernames?: string[] | null
  status_label?: string | null
  total_amount: number | null
  currency_code: string
  vendor_name: string | null
  vendor_gstn: string | null
  original_file: string | null
  upload_id: string | null
  stg_bill_id: string | null
  created_at: string | null
  uploaded_by_name: string | null
  uploaded_by_role: string | null
  category: string | null
  department: string | null
  payment_status: string | null   // unpaid | partial | paid
  amount_paid: number | null
  outstanding_amount: number | null
  payment_updated_by: string | null
  payment_updated_at: string | null
  tally_push_pending: boolean
  manual_priority: string | null   // p0 | p1 | p2 | p3 — admin override
  soft_status: 'active' | 'deleted' | 'on_hold'
  subtotal: number | null
  tax_amount: number | null
  tds_status: string | null
  tds_section_code: string | null
  tds_rate: number | null
  tds_amount: number | null
  tds_base_amount: number | null
  net_payable: number | null
  original_amount: number | null
  inr_amount_current: number | null
  brand_id: string | null
  cost_center_id: string | null
  cost_center_name: string | null
  fx_agent_note: string | null
  fx_agent_severity: string | null
  // Vendor KYB/GST verification — the same flag as the Vendors page VERIFIED
  // badge. Gates the row-level Pay Now button; absent on rows served by an older
  // backend, which greys the button rather than offering a payment that can't be
  // released without an override.
  vendor_verified?: boolean
}

const BILLS_CACHE_TTL = 5 * 60 * 1000 // 5 minutes

// Bump this suffix whenever the Bill shape gains a field the cache should not
// silently keep serving without — e.g. `supplier_id` (added for the payment
// gateway's vendor lookup) would otherwise sit undefined in old cached rows
// for up to BILLS_CACHE_TTL after deploy.
const BILLS_CACHE_VERSION = 'v5'   // v5: + vendor_verified (Pay Now gate)
function billsCacheKey(companyId?: string, role?: string, brandId?: string) {
  return `onsum_bills_cache_${BILLS_CACHE_VERSION}_${companyId ?? 'default'}_${role ?? 'all'}_${brandId ?? 'all'}`
}

function billsCacheGet(companyId?: string, role?: string, brandId?: string): Bill[] | null {
  try {
    const key = billsCacheKey(companyId, role, brandId)
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const { data, ts } = JSON.parse(raw)
    if (Date.now() - ts > BILLS_CACHE_TTL) {
      localStorage.removeItem(key)
      return null
    }
    return data
  } catch {
    return null
  }
}

function billsCacheSet(bills: Bill[], companyId?: string, role?: string, brandId?: string): void {
  try {
    localStorage.setItem(billsCacheKey(companyId, role, brandId), JSON.stringify({ data: bills, ts: Date.now() }))
  } catch {
    // localStorage full — skip silently
  }
}

export function invalidateBillsCache(companyId?: string): void {
  localStorage.removeItem(billsCacheKey(companyId))
}

export interface ChainEntry {
  id: number
  step: number
  actor_role: string | null
  actor_name: string | null
  action: string   // submitted | approved | send_back
  comment: string | null
  target_step: number | null
  created_at: string | null
}

export interface BillAttachment {
  id: string
  file_name: string
  file_type: string | null
  file_size: number | null
  uploaded_by: string | null
  uploaded_at: string | null
}

export interface BillDetail {
  invoice_id: string
  id: string
  bill_number: string | null
  service_month: string | null
  invoice_date: string | null
  due_date: string | null
  status: string
  allowed_roles?: string[]
  allowed_usernames?: string[] | null
  status_label?: string | null
  subtotal: number | null
  tax_amount: number | null
  /** The CGST/SGST/IGST split behind `tax_amount`. Null on bills ingested
   *  before the components were kept — the sum was all that was stored. */
  tax_breakdown: GstBreakdownData | null
  total_amount: number | null
  amount_paid: number | null
  outstanding_amount: number | null
  payment_status?: string | null   // unpaid | partial | paid
  discount_amount: number | null
  currency_code: string
  original_amount: number | null
  inr_amount_current: number | null
  fx_gain_loss: number | null
  fx_actual_inr: number | null
  fx_effective_rate: number | null
  fx_realized_cost: number | null
  payment_terms_days: number | null
  upload_id: string | null
  stg_bill_id: string | null
  original_file: string | null
  created_at: string | null
  category: string | null
  department: string | null
  cost_center_name: string | null
  vendor_sap_code: string | null
  verification_flag?: 'GREEN' | 'YELLOW' | 'RED' | null
  verification_detail?: string | null
  verified_at?: string | null
  vendor: {
    name: string | null; gstn: string | null; email: string | null
    /** The account this bill would be paid into. Null means none is on file at
     *  all; an unverified one is still returned, carrying verified: false, and
     *  the badge is what says so. */
    bank: {
      account_number: string; ifsc: string; bank_name: string
      name_at_bank: string; verified: boolean
    } | null
  }
  buyer: { name: string; brand?: string; gstn: string }
  line_items: {
    line_number: number
    product_name: string | null
    hsn_code: string | null
    quantity: number | null
    unit: string | null
    unit_price: number | null
    discount_amount: number | null
    tax_rate_percent: number | null
    tax_amount: number | null
    line_total: number | null
    sap_item_code: string | null
    sap_material_desc: string | null
    sap_map_status: string | null
    sap_map_score: number | null
    /** When set, `quantity` is the base amount and `unit_price` is a percentage
     *  of it — a commission, platform fee, brokerage or interest line. */
    rate_is_percent: boolean
    /** The ledger this line was coded to at review time. */
    ledger_name: string | null
    ledger_code: string | null
  }[]
  chain: ChainEntry[]
  attachments: BillAttachment[]
  manual_priority: string | null
  // Absent from an older backend; read as active.
  soft_status?: 'active' | 'deleted' | 'on_hold'
  tds_status: string | null
  tds_section_code: string | null
  tds_rate: number | null
  tds_amount: number | null
  tds_base_amount: number | null
  net_payable: number | null
  supplier_id: string | null
  vendor_category_id: string | null
}

// Versioned: an entry cached before the payee bank, cost centre, SAP code and
// per-line ledger were added lacks those keys, and `vendor.bank` missing reads
// as "no verified account on file" — a wrong answer, for the two minutes the
// old entry survives. Bumping the prefix retires the old shape outright.
const DETAIL_CACHE_PREFIX = 'onsum_bill_detail_v2_'
const DETAIL_CACHE_TTL = 2 * 60 * 1000 // 2 minutes

function detailCacheGet(invoiceId: string): BillDetail | null {
  try {
    const raw = localStorage.getItem(DETAIL_CACHE_PREFIX + invoiceId)
    if (!raw) return null
    const { data, ts } = JSON.parse(raw)
    if (Date.now() - ts > DETAIL_CACHE_TTL) {
      localStorage.removeItem(DETAIL_CACHE_PREFIX + invoiceId)
      return null
    }
    return data
  } catch { return null }
}

function detailCacheSet(invoiceId: string, data: BillDetail): void {
  try {
    localStorage.setItem(DETAIL_CACHE_PREFIX + invoiceId, JSON.stringify({ data, ts: Date.now() }))
  } catch {}
}

export function invalidateDetailCache(invoiceId?: string): void {
  if (invoiceId) {
    localStorage.removeItem(DETAIL_CACHE_PREFIX + invoiceId)
    return
  }
  Object.keys(localStorage)
    .filter(k => k.startsWith(DETAIL_CACHE_PREFIX))
    .forEach(k => localStorage.removeItem(k))
}

export async function fetchBillDetail(invoiceId: string): Promise<BillDetail> {
  const cached = detailCacheGet(invoiceId)
  if (cached) return cached
  const { data } = await axios.get<BillDetail>(`/api/bills/${invoiceId}`)
  detailCacheSet(invoiceId, data)
  return data
}

export async function billAction(invoiceId: string, payload: {
  action: 'submit' | 'approve' | 'send_back' | 'priority_change'
  actor_role: string
  actor_name: string
  comment?: string
  target_status?: string
}): Promise<{ status: string }> {
  invalidateDetailCache(invoiceId)
  invalidateBillsCache()
  const { data } = await axios.post<{ status: string }>(`/api/bills/${invoiceId}/action`, payload)
  return data
}

export async function updateBill(invoiceId: string, updates: {
  vendor_name?: string
  vendor_gstn?: string
  invoice_date?: string
  due_date?: string
  subtotal?: number
  tax_amount?: number
  total_amount?: number
  discount_amount?: number
  payment_status?: string
  amount_paid?: number
  category?: string
  manual_priority?: string | null
  actor_name?: string
  actor_role?: string
  line_items?: { line_number: number; product_name?: string; hsn_code?: string; quantity?: number | null; unit?: string; unit_price?: number | null; tax_rate_percent?: number | null; line_total?: number | null; rate_is_percent?: boolean }[]
}): Promise<void> {
  invalidateDetailCache(invoiceId)
  await axios.put(`/api/bills/${invoiceId}`, updates)
}

export async function updatePaymentStatus(
  invoiceId: string,
  payload: { payment_status: string; amount_paid?: number; actor_name?: string; actor_role?: string }
): Promise<void> {
  invalidateBillsCache()
  await axios.post(`/api/bills/${invoiceId}/payment`, payload)
}

export type PriorityLabels = { p0: string; p1: string; p2: string; p3: string }

export async function fetchPriorityLabels(): Promise<PriorityLabels> {
  const { data } = await axios.get<PriorityLabels>('/api/settings/priority-labels')
  return data
}

export async function updatePriorityLabels(labels: PriorityLabels): Promise<PriorityLabels> {
  const { data } = await axios.put<PriorityLabels>('/api/settings/priority-labels', labels)
  return data
}

export async function fetchNotificationCount(role: string, companyId?: string): Promise<number> {
  const params: Record<string, string> = { role }
  if (companyId) params.company_id = companyId
  const { data } = await axios.get<{ count: number }>('/api/notifications/count', { params })
  return data.count
}

/** One vendor by id, in the same shape as a fetchVendors() row. */
export async function fetchVendor(supplierId: string, brandId?: string): Promise<Vendor> {
  const { data } = await axios.get<Vendor>(`/api/vendors/${supplierId}`, {
    params: brandId ? { brand_id: brandId } : {},
  })
  return data
}

export async function fetchBills(forceRefresh = false, companyId?: string, onBackground?: (b: Bill[]) => void, role?: string, username?: string, brandId?: string, softStatus?: string, costCenterId?: string): Promise<Bill[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (role) params.role = role
  if (username) params.username = username
  if (brandId) params.brand_id = brandId
  if (softStatus) params.soft_status = softStatus
  if (costCenterId) params.cost_center_id = costCenterId
  const doFetch = async () => {
    const { data } = await axios.get<{ bills: Bill[]; total: number }>('/api/bills', { params })
    billsCacheSet(data.bills, companyId, role, brandId)
    return data.bills
  }
  if (!forceRefresh) {
    const cached = billsCacheGet(companyId, role, brandId)
    if (cached) {
      // Stale-while-revalidate: serve cache instantly, refresh silently
      doFetch().then(fresh => onBackground?.(fresh)).catch(() => {})
      return cached
    }
  }
  return doFetch()
}

export interface ChainStepAPI {
  step_order: number
  label: string
  role: string
  locked: boolean
  assignees?: string[] | null
}

export async function fetchChainConfig(companyId?: string, department?: string): Promise<ChainStepAPI[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (department) params.department = department
  const { data } = await axios.get<{ steps: ChainStepAPI[] }>('/api/settings/chain', { params })
  return data.steps
}

export async function saveChainConfig(steps: ChainStepAPI[], companyId?: string, department?: string): Promise<void> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (department) params.department = department
  await axios.post('/api/settings/chain', steps, { params })
}

export async function fetchDepartments(companyId?: string): Promise<string[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ departments: string[] }>('/api/settings/departments', { params })
  return data.departments
}

export async function fetchUserDepartments(username: string, companyId?: string): Promise<string[]> {
  const params: Record<string, string> = { username }
  if (companyId) params.company_id = companyId
  const { data } = await axios.get<{ departments: string[] }>('/api/settings/user-departments', { params })
  return data.departments
}

export async function addDepartment(department: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.post('/api/settings/departments', { department }, { params })
}

export async function deleteDepartment(department: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/settings/departments/${encodeURIComponent(department)}`, { params })
}

export async function fetchChainDefault(companyId?: string, department?: string): Promise<ChainStepAPI[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (department) params.department = department
  const { data } = await axios.get<{ steps: ChainStepAPI[] }>('/api/settings/chain/default', { params })
  return data.steps
}

export async function saveChainAsDefault(companyId?: string, department?: string): Promise<void> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (department) params.department = department
  await axios.post('/api/settings/chain/save-default', {}, { params })
}

// ── FX / Multi-Currency ────────────────────────────────────────────────────────

export interface FxRate {
  currency_code: string
  inr_per_unit: number | null
  rate_date: string | null
  source: string | null
  fetched_at: string | null
}

export interface FxPayment {
  payment_id: string
  payment_date: string | null
  original_amount: number | null
  currency_code: string
  actual_inr_paid: number | null
  effective_rate: number | null
  rbi_rate: number | null
  bank_spread: number | null
  bank_spread_pct: number | null
  bank_reference: string | null
  bank_name: string | null
  notes: string | null
  created_at: string | null
}

export interface FxExposureItem {
  currency_code: string
  bill_count: number
  total_original: number | null
  total_inr_outstanding: number | null
  total_inr_current: number | null
  total_fx_gain_loss: number | null
  current_rate: number | null
}

export async function fetchFxRates(): Promise<FxRate[]> {
  const { data } = await axios.get<{ rates: FxRate[] }>('/api/fx/rates')
  return data.rates
}

export async function fetchFxExposure(companyId?: string): Promise<FxExposureItem[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ exposure: FxExposureItem[] }>('/api/fx/exposure', { params })
  return data.exposure
}

export async function recordFxPayment(invoiceId: string, payload: {
  actual_inr_paid: number
  payment_date?: string
  bank_name?: string
  bank_reference?: string
  notes?: string
  company_id?: string
}): Promise<{ payment_id: string; effective_rate: number; bank_spread_pct: number | null; fx_realized_cost: number | null }> {
  const { data } = await axios.post(`/api/bills/${invoiceId}/fx-payment`, payload)
  return data
}

export async function fetchFxPayments(invoiceId: string, companyId?: string): Promise<FxPayment[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ payments: FxPayment[] }>(`/api/bills/${invoiceId}/fx-payments`, { params })
  return data.payments
}

// ── Vendor Disbursement (IMPS demo) ──────────────────────────────────────────

export interface Disbursement {
  disbursement_id: string
  amount: number
  status: 'PENDING_OTP' | 'PENDING' | 'SUCCESS' | 'FAILED'
  status_message: string | null
  bank_rrn: string | null
  bank_provider: string
  tran_ref_no: string
  initiated_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
}

export interface DisbursementStats {
  remitter_account_number: string
  bank_name: string
  bank_provider: string
  ifsc: string | null
  balance: number
  total_transactions: number
  successful_payments: number
  failed_payments: number
  pending_payments: number
  total_disbursed: number
  demo: boolean
}

/** `bank` picks whose remitter account to describe (each bank pays from its own). */
export async function fetchDisbursementStats(companyId?: string, bank?: string): Promise<DisbursementStats> {
  const params = { ...(companyId ? { company_id: companyId } : {}), ...(bank ? { bank } : {}) }
  const { data } = await axios.get<DisbursementStats>('/api/disbursements/stats', { params })
  return data
}

export async function fetchDisbursements(invoiceId: string, companyId?: string): Promise<Disbursement[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ disbursements: Disbursement[] }>(`/api/bills/${invoiceId}/disbursements`, { params })
  return data.disbursements
}

export interface VendorPaymentProfile {
  vendor_name: string
  primary_gstin: string | null
  kyb_status: string
  gstin_verified: boolean
  additional_gstins: { gstin: string; status: string; trade_name: string | null }[]
  bank_accounts: { id: string; account_number_masked: string; ifsc: string | null; bank_name: string | null; is_primary: boolean; verified: boolean; name_at_bank: string | null }[]
  bank_verified: boolean
  can_pay: boolean
  bills: { invoice_id: string; id: string; invoice_date: string | null; due_date: string | null; total_amount: number; outstanding_amount: number }[]
}

export async function fetchVendorPaymentProfile(supplierId: string, companyId?: string): Promise<VendorPaymentProfile> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<VendorPaymentProfile>(`/api/vendors/${supplierId}/payment-profile`, { params })
  return data
}

interface OverrideFields {
  override?: boolean; override_account_number?: string; override_ifsc?: string
  override_beneficiary_name?: string; override_reason?: string
  // Which bank moves the money. Omit to use the remitter account's own bank,
  // which is how every payment behaved before this became selectable.
  bank_provider?: string
}

export interface DisbursalBank {
  code: string        // what the gateway expects as bankProvider
  label: string       // what the picker shows
  wire: string
}

/** Served from the backend's adapter registry, never a list held in the UI: a
 *  bank whose status codes the backend cannot read must not be offerable. */
export async function fetchDisbursalBanks(): Promise<{ banks: DisbursalBank[]; default: string }> {
  const { data } = await axios.get('/api/disbursements/banks')
  return data
}

export async function initiateDisbursement(invoiceId: string, payload: {
  amount: number; company_id?: string; actor_name?: string; actor_role?: string
  // `demo_otp` is the fixed code this (demo) backend also accepts, or null when
  // the real emailed OTP is the only way through. `otp_expires_at` is epoch
  // seconds — the payment page counts down to it instead of guessing the TTL.
} & OverrideFields): Promise<{ disbursement_id: string; otp_sent_to: string; status: string; demo_otp?: string | null; demo_payment?: boolean; otp_expires_at?: number }> {
  const { data } = await axios.post(`/api/bills/${invoiceId}/disbursements`, payload)
  return data
}

export async function initiateBulkDisbursement(supplierId: string, payload: {
  bills: { invoice_id: string; amount: number }[]
  company_id?: string; actor_name?: string; actor_role?: string
} & OverrideFields): Promise<{ disbursement_ids: string[]; otp_sent_to: string; status: string; demo_otp?: string | null; demo_payment?: boolean; otp_expires_at?: number }> {
  const { data } = await axios.post(`/api/vendors/${supplierId}/disbursements/bulk`, payload)
  return data
}

export interface BatchDisbursementResult {
  disbursement_id: string; invoice_id: string; amount: number; status: string; status_message: string | null; bank_rrn: string | null
}

export async function verifyBatchDisbursementOtp(payload: { disbursement_ids: string[]; otp: string; company_id?: string }): Promise<{
  results: BatchDisbursementResult[]
}> {
  const { data } = await axios.post('/api/disbursements/batch-verify-otp', payload)
  return data
}

export async function verifyDisbursementOtp(disbursementId: string, payload: { otp: string; company_id?: string }): Promise<{ disbursement_id: string; status: string; status_message: string; bank_rrn: string | null; tran_ref_no: string }> {
  const { data } = await axios.post(`/api/disbursements/${disbursementId}/verify-otp`, payload)
  return data
}

export async function resendDisbursementOtp(disbursementId: string, companyId?: string): Promise<{ otp_sent_to: string; otp_expires_at?: number }> {
  const { data } = await axios.post(`/api/disbursements/${disbursementId}/resend-otp`, { company_id: companyId })
  return data
}

// ── Payment Hub (cross-vendor) ────────────────────────────────────────────────

export interface PaymentQueueVendor {
  supplier_id: string
  vendor_name: string
  gstin: string | null
  kyb_status: string
  gstin_verified: boolean
  bank_verified: boolean
  can_pay: boolean
  bills_count: number
  payable_amount: number
  earliest_due: string | null
  pending_status: 'PENDING_OTP' | 'PENDING' | null
  pending_disbursement_id: string | null
  /** Epoch seconds. Present once disbursement_queue returns it; the hub's OTP
   *  modal counts down to it instead of waiting for a resend. */
  pending_otp_expires_at?: number | null
  /** Every disbursement sharing this payment's OTP — one entry per bill for a
   *  bulk payment. The hub releases the whole batch when there is more than one. */
  pending_disbursement_ids?: string[] | null
  /** Sum of amounts across pending_disbursement_ids — what this OTP actually
   *  releases (one bill's ad-hoc amount, or a batch's total), never the
   *  vendor's whole outstanding. Null until the queue returns it. */
  pending_amount?: number | null
  /** Every FAILED disbursement on record for this vendor, newest first —
   *  orthogonal to pending_status/can_pay, since a failed attempt moves no
   *  money and leaves the bill outstanding (ready/blocked on its own merits
   *  either way). A vendor can fail more than once (wrong account, then an
   *  expired OTP on retry), so this is every attempt, not just the latest. */
  failed_attempts?: FailedAttempt[]
  /** Every outstanding bill making up payable_amount, earliest due first —
   *  the queue's own per-vendor breakdown, not a separate fetch. */
  bills: QueueBill[]
  /** Whatever account is on file (verified or not) — "where this would pay
   *  today". Null when the vendor has no bank account on file at all. */
  beneficiary_account_masked: string | null
  beneficiary_bank_name: string | null
}

export interface QueueBill {
  invoice_id: string
  id: string | null
  due_date: string | null
  amount: number
  /** Raw ingredients for utils/payPriority.ts's getPayPriority() — the same
   *  P0..P3 calculation the Bills page uses, not a second one. */
  total_amount: number | null
  manual_priority: string | null
}

export interface FailedAttempt {
  disbursement_id: string
  invoice_id: string
  amount: number
  reason: string | null
  failed_at: string
}

/** Every vendor with money owed, across the whole tenant — the Payment Hub's landing table. */
export async function fetchDisbursementQueue(companyId?: string): Promise<PaymentQueueVendor[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ vendors: PaymentQueueVendor[] }>('/api/disbursements/queue', { params })
  return data.vendors
}

export interface RecentDisbursement {
  disbursement_id: string
  invoice_id: string
  supplier_id: string | null
  vendor_name: string | null
  invoice_number: string | null
  amount: number
  status: Disbursement['status']
  status_message: string | null
  act_code: string | null
  bank_rrn: string | null
  tran_ref_no: string
  initiated_by: string | null
  updated_by: string | null
  created_at: string
  updated_at: string
  /** For building a real receipt PDF from a past payment — same fields
   *  the wizard's own ReceiptData needs. */
  beneficiary_name: string | null
  beneficiary_account: string | null
  beneficiary_ifsc: string | null
  bank_provider: string | null
  remitter_account_number: string | null
  is_overridden: boolean
}

/** Latest disbursements across every vendor — the Payment Hub's activity feed. */
export async function fetchRecentDisbursements(limit = 25, companyId?: string): Promise<RecentDisbursement[]> {
  const params: Record<string, unknown> = { limit }
  if (companyId) params.company_id = companyId
  const { data } = await axios.get<{ disbursements: RecentDisbursement[] }>('/api/disbursements/recent', { params })
  return data.disbursements
}

export async function refreshDisbursementStatus(disbursementId: string, companyId?: string): Promise<{ status: string; bank_rrn: string | null; status_message?: string | null }> {
  const { data } = await axios.post(`/api/disbursements/${disbursementId}/refresh-status`, { company_id: companyId })
  return data
}

// ── FX Phase 2: Alerts & Agent Notes ─────────────────────────────────────────

export interface FxAlert {
  alert_id: string
  alert_type: string
  currency_code: string
  rate_date: string | null
  rate_inr: number | null
  threshold_inr: number | null
  direction: string | null
  recommendation: string
  affected_bill_ids: string[]
  exposure_foreign: number | null
  exposure_inr: number | null
  status: string
  created_at: string | null
  dismissed_at: string | null
  dismissed_by: string | null
}

export interface FxAgentNote {
  note_id: string
  severity: string
  note_text: string
  confidence: number | null
  currency_code: string
  rate_at_generation: number | null
  created_at: string | null
}

export interface FxAgentBriefing {
  note_id: string
  severity: string
  note_text: string
  confidence: number | null
  created_at: string | null
}

export async function fetchFxAlerts(companyId?: string, status = 'ACTIVE'): Promise<FxAlert[]> {
  const params: Record<string, string> = { status }
  if (companyId) params.company_id = companyId
  const { data } = await axios.get<{ alerts: FxAlert[] }>('/api/fx/alerts', { params })
  return data.alerts
}

export async function dismissFxAlert(alertId: string, companyId?: string, dismissedBy?: string): Promise<void> {
  await axios.post(`/api/fx/alerts/${alertId}/dismiss`, {
    company_id: companyId,
    dismissed_by: dismissedBy,
  })
}

export interface FxSettings {
  enabled: boolean
  reconciliation_tolerance_pct: number
  revaluation_frequency: string
  alerts_enabled: boolean
  stale_alert_days: number
  thresholds: Record<string, {
    pay_now_below?: number
    delay_pay_above?: number
    collect_now_above?: number
    volatility_threshold_pct?: number
  }>
}

/** Demo payments licence: whether Pay is simulated, and the one address its receipts go to. */
export interface DemoPaymentSettings { demo: boolean; poc_email: string | null }

export async function fetchDemoPaymentSettings(): Promise<DemoPaymentSettings> {
  const { data } = await axios.get<DemoPaymentSettings>('/api/disbursements/demo-settings')
  return data
}

export async function saveDemoPaymentSettings(pocEmail: string | null): Promise<DemoPaymentSettings> {
  const { data } = await axios.put<DemoPaymentSettings>('/api/disbursements/demo-settings', { poc_email: pocEmail })
  return data
}

export async function fetchFxSettings(companyId?: string): Promise<FxSettings> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ settings: FxSettings }>('/api/fx/settings', { params })
  return data.settings
}

export async function updateFxSettings(payload: {
  company_id?: string
  alerts_enabled?: boolean
  reconciliation_tolerance_pct?: number
  stale_alert_days?: number
  thresholds?: Record<string, Record<string, number>>
}): Promise<FxSettings> {
  const { data } = await axios.put<{ settings: FxSettings }>('/api/fx/settings', payload)
  return data.settings
}

export async function fetchFxAgentNote(billId: string): Promise<FxAgentNote | null> {
  const { data } = await axios.get<{ note: FxAgentNote | null }>('/api/fx/agent-note', { params: { bill_id: billId } })
  return data.note
}

export async function fetchFxAgentBriefing(companyId?: string): Promise<FxAgentBriefing | null> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ briefing: FxAgentBriefing | null }>('/api/fx/agent-briefing', { params })
  return data.briefing
}

export interface FxReportCurrency {
  currency_code: string
  total_foreign: number
  payment_count: number
  booked_inr: number
  actual_inr: number
  fx_cost: number
  avg_effective_rate: number | null
  avg_bank_spread_pct: number | null
}

export interface FxReportMonthly {
  month: string
  actual_inr: number
  payment_count: number
}

export interface FxReportData {
  quarter_start: string
  quarter_end: string
  total_booked_inr: number
  total_actual_inr: number
  total_fx_cost: number
  currencies: FxReportCurrency[]
  monthly_trend: FxReportMonthly[]
}

export async function fetchFxReport(companyId?: string, quarter?: string, fy?: string): Promise<FxReportData> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (quarter) params.quarter = quarter
  if (fy) params.fy = fy
  const { data } = await axios.get<FxReportData>('/api/fx/report', { params })
  return data
}

/**
 * One row per bill, distilled from the activity trail (`bill_approval_chain` —
 * the same events the per-bill "View activity" timeline shows). Reports joins it
 * to the bill list on invoice_id to chart approval turnaround, per-stage waits
 * and rework without pulling the whole chain into the browser.
 */
export interface ActivityRow {
  invoice_id: string
  /** First recorded action on the bill. */
  first_at: string | null
  /** Last approval action — ends turnaround, starts the payment buffer. */
  approved_at: string | null
  /** Times the bill was pushed back for revision. */
  send_backs: number
  /** Days the bill sat with each role before that role acted. */
  role_days: Record<string, number>
}

export async function fetchActivityReport(companyId?: string, brandId?: string): Promise<ActivityRow[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (brandId) params.brand_id = brandId
  const { data } = await axios.get<{ rows: ActivityRow[] }>('/api/reports/activity', { params })
  return data.rows ?? []
}

export interface CompanyUser {
  username: string
  name: string
  role: string
  email: string
  department: string | null
}

export async function fetchUsers(companyId?: string, department?: string): Promise<CompanyUser[]> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  if (department) params.department = department
  const { data } = await axios.get<{ users: CompanyUser[] }>('/api/users', { params })
  return data.users
}

export async function createUser(payload: {
  username: string; password: string; role: string; name: string; email: string; department?: string; company_id?: string
}): Promise<void> {
  await axios.post('/api/users', payload)
}

export async function updateUser(username: string, payload: {
  role?: string; name?: string; email?: string; department?: string; password?: string
}, companyId?: string): Promise<void> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  await axios.put(`/api/users/${username}`, payload, { params })
}

export async function deleteUser(username: string, companyId?: string): Promise<void> {
  const params: Record<string, string> = {}
  if (companyId) params.company_id = companyId
  await axios.delete(`/api/users/${username}`, { params })
}

export interface Charge {
  charge_id: string
  name: string
  hsn_sac: string | null
  default_ledger_id: string | null
  default_tax_rate: number | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export async function fetchCharges(companyId?: string): Promise<Charge[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<{ charges: Charge[] }>('/api/charges', { params })
  return data.charges
}

export async function createCharge(
  body: { name: string; hsn_sac?: string; default_ledger_id?: string; default_tax_rate?: number; company_id?: string }
): Promise<Charge> {
  const { data } = await axios.post<Charge>('/api/charges', body)
  return data
}

export async function updateCharge(
  chargeId: string,
  body: { name?: string; hsn_sac?: string; default_ledger_id?: string; default_tax_rate?: number; is_active?: boolean; company_id?: string }
): Promise<Charge> {
  const { data } = await axios.put<Charge>(`/api/charges/${chargeId}`, body)
  return data
}

export async function deleteCharge(chargeId: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/charges/${chargeId}`, { params })
}

export interface BulkActionResult {
  bill_id: string
  success: boolean
  status?: string
  error?: string
}

export async function bulkBillAction(payload: {
  bill_ids: string[]
  action: 'APPROVE' | 'PUSH_TALLY' | 'MARK_PAID'
  actor_name: string
  actor_role: string
  company_id?: string
  options?: { payment_date?: string; payment_method?: string }
}): Promise<{ results: BulkActionResult[]; succeeded: number; failed: number; total: number }> {
  invalidateBillsCache()
  const { data } = await axios.post('/api/bills/bulk-action', payload)
  return data
}

// ── COA Rules ─────────────────────────────────────────────────────────────

export interface CoaRule {
  id: string
  exp_head: string
  vendor_name: string | null
  category: string | null
  priority: number
  created_at: string | null
}

export async function fetchCoaRules(companyId?: string): Promise<CoaRule[]> {
  const params = companyId ? { company_id: companyId } : {}
  const { data } = await axios.get<CoaRule[]>('/api/coa-rules', { params })
  return data
}

export async function createCoaRule(body: {
  exp_head: string
  vendor_name?: string
  category?: string
  priority?: number
  company_id?: string
}): Promise<CoaRule> {
  const { data } = await axios.post<CoaRule>('/api/coa-rules', body)
  return data
}

export async function deleteCoaRule(id: string, companyId?: string): Promise<void> {
  const params = companyId ? { company_id: companyId } : {}
  await axios.delete(`/api/coa-rules/${id}`, { params })
}

export async function updateCoaRule(
  id: string,
  body: { exp_head?: string; vendor_name?: string; category?: string; priority?: number; company_id?: string }
): Promise<CoaRule> {
  const { data } = await axios.put<CoaRule>(`/api/coa-rules/${id}`, body)
  return data
}

export async function downloadExpenseReport(month?: string, companyId?: string, status?: string, brandId?: string): Promise<void> {
  const params: Record<string, string> = {}
  if (month) params.month = month
  if (companyId) params.company_id = companyId
  if (status) params.status = status
  if (brandId) params.brand_id = brandId
  const response = await axios.get('/api/reports/expense-xlsx', {
    params,
    responseType: 'blob',
  })
  const blob = new Blob([response.data], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const label = month ? month.replace('-', '-') : 'ALL'
  a.href = url
  a.download = `Expense_Report_${label}.xlsx`
  a.click()
  URL.revokeObjectURL(url)
}

export async function downloadExpenseSummaryCSV(
  month?: string,
  companyId?: string,
  status?: string,
  report: 'summary' | 'raw' | 'both' = 'summary',
  brandId?: string,
): Promise<void> {
  const params: Record<string, string> = { report }
  if (month) params.month = month
  if (companyId) params.company_id = companyId
  if (status) params.status = status
  if (brandId) params.brand_id = brandId
  const response = await axios.get('/api/reports/expense-csv', {
    params,
    responseType: 'blob',
  })
  const label = month
    ? (() => { const d = new Date(month + '-01'); return `${d.toLocaleString('en', { month: 'short' }).toUpperCase()}-${String(d.getFullYear()).slice(2)}` })()
    : 'ALL'

  let filename: string
  let mimeType: string
  if (report === 'both') {
    filename = `Expense_Report_${label}.zip`
    mimeType = 'application/zip'
  } else if (report === 'raw') {
    filename = `Expense_Report_RAW_${label}.csv`
    mimeType = 'text/csv'
  } else {
    filename = `Expense_Report_SUMMARY_${label}.csv`
    mimeType = 'text/csv'
  }

  const blob = new Blob([response.data], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

// ── TDS Settings ───────────────────────────────────────────────────────────────

export interface TdsSection {
  tds_section_id: string
  return_code: string
  old_section_label: string | null
  new_section_label: string | null
  nature_of_payment: string
  default_rate: number
  pan_missing_rate: number
  threshold_type: 'NONE' | 'PER_TXN' | 'AGGREGATE_FY' | 'PER_MONTH' | 'EXCESS_OF' | 'DUAL'
  threshold_amount: number | null
  threshold_amount_2: number | null
  base_calculation: string
  effective_from: string
  effective_to: string | null
  is_active: boolean
}

export interface VendorCategory {
  category_id: string
  name: string
  description: string | null
  display_order: number
  is_active: boolean
  vendor_count: number
  tds_rate: number | null
  section_code: string | null
  section_label: string | null
}

export interface TdsMappingRule {
  rule_id: string
  priority: number
  rate_override: number | null
  description: string | null
  is_active: boolean
  tds_section_id: string
  section_return_code: string
  old_section_label: string | null
  section_nature: string
  vendor_category_id: string | null
  category_name: string | null
}

export interface TdsPreferences {
  company_id?: string
  deduct_on: 'BOOKING' | 'PAYMENT' | 'EARLIER'
  base_default: 'GST_EXCLUSIVE' | 'TOTAL_AMOUNT'
  auto_apply: boolean
  fy_start_month: number
  pan_missing_rate: number
  max_catchup_pct: number
  max_backdate_days: number
}

export interface VendorTdsOverride {
  override_id: string
  supplier_id: string
  tds_section_id: string | null
  tds_mode: 'cumulative' | 'always_deduct' | 'per_bill_threshold'
  override_rate: number | null
  certificate_no: string | null
  effective_from: string | null
  lower_cert_valid_to: string | null
  mode_reason: string | null
  is_active: boolean
  supplier_name?: string
  section_return_code?: string
  old_section_label?: string | null
}

export interface VendorTdsFyState {
  state_id: string
  supplier_id: string
  tds_section_id: string
  fy: string
  cumulative_billed: number
  cumulative_tds_deducted: number
  threshold_crossed: boolean
  catchup_pending: number
  catchup_recovered: number
  catchup_decision: 'recover' | 'absorb' | 'pending' | 'abandoned' | null
  supplier_name?: string
  return_code?: string
  old_section_label?: string | null
  base_calculation?: string | null
  default_rate?: number | null
}

export interface CatchupSummary {
  catchup_pending: number
  affected_bill_count: number
  bills_total_amount: number
  fy: string
  decision_required: boolean
}

export interface TdsBreakdown {
  status: 'CALCULATED' | 'NA' | 'PENDING'
  section_code: string | null
  return_code: string | null
  rate: number | null
  base_amount: number
  tax_amount: number
  total_amount: number
  tds_amount: number
  net_payable: number
  base_calculation: string
  pan_status: string | null
  reason: string
}

export async function previewTds(body: {
  company_id?: string
  vendor_name?: string
  vendor_gstin?: string
  vendor_pan?: string
  subtotal?: number
  tax_amount?: number
  total_amount?: number
  bill_date?: string
}): Promise<TdsBreakdown> {
  const { data } = await axios.post<TdsBreakdown>('/api/settings/tds/preview', body)
  return data
}

export async function fetchTdsSections(companyId?: string): Promise<TdsSection[]> {
  const { data } = await axios.get<TdsSection[]>('/api/settings/tds/sections',
    { params: companyId ? { company_id: companyId } : {} })
  return data
}
export async function createTdsSection(body: object): Promise<{ tds_section_id: string }> {
  const { data } = await axios.post('/api/settings/tds/sections', body)
  return data
}
export async function updateTdsSection(id: string, body: object): Promise<void> {
  await axios.put(`/api/settings/tds/sections/${id}`, body)
}
export async function deleteTdsSection(id: string): Promise<void> {
  await axios.delete(`/api/settings/tds/sections/${id}`)
}

export async function fetchVendorCategories(companyId?: string): Promise<VendorCategory[]> {
  const { data } = await axios.get<VendorCategory[]>('/api/settings/tds/vendor-categories',
    { params: companyId ? { company_id: companyId } : {} })
  return data
}
export async function createVendorCategory(body: { name: string; description?: string; display_order?: number }): Promise<{ category_id: string }> {
  const { data } = await axios.post('/api/settings/tds/vendor-categories', body)
  return data
}
export async function updateVendorCategory(id: string, body: object): Promise<void> {
  await axios.put(`/api/settings/tds/vendor-categories/${id}`, body)
}
export async function deleteVendorCategory(id: string): Promise<void> {
  await axios.delete(`/api/settings/tds/vendor-categories/${id}`)
}

export async function fetchMappingRules(companyId?: string): Promise<TdsMappingRule[]> {
  const { data } = await axios.get<TdsMappingRule[]>('/api/settings/tds/mapping-rules',
    { params: companyId ? { company_id: companyId } : {} })
  return data
}
export async function createMappingRule(body: { tds_section_id: string; vendor_category_id?: string; priority: number; rate_override?: number; description?: string }): Promise<{ rule_id: string }> {
  const { data } = await axios.post('/api/settings/tds/mapping-rules', body)
  return data
}
export async function updateMappingRule(id: string, body: object): Promise<void> {
  await axios.put(`/api/settings/tds/mapping-rules/${id}`, body)
}
export async function deleteMappingRule(id: string): Promise<void> {
  await axios.delete(`/api/settings/tds/mapping-rules/${id}`)
}

export async function fetchTdsPreferences(companyId?: string): Promise<TdsPreferences> {
  const { data } = await axios.get<TdsPreferences>('/api/settings/tds/preferences',
    { params: companyId ? { company_id: companyId } : {} })
  return data
}
export async function saveTdsPreferences(body: TdsPreferences, companyId?: string): Promise<void> {
  await axios.put('/api/settings/tds/preferences', { ...body, company_id: companyId })
}

export async function fetchVendorOverrides(supplierId?: string, companyId?: string): Promise<VendorTdsOverride[]> {
  const { data } = await axios.get<VendorTdsOverride[]>('/api/settings/tds/vendor-overrides',
    { params: { ...(supplierId ? { supplier_id: supplierId } : {}), ...(companyId ? { company_id: companyId } : {}) } })
  return data
}
export async function createVendorOverride(body: {
  supplier_id: string; tds_section_id?: string; tds_mode?: string;
  override_rate?: number; certificate_no?: string; effective_from?: string;
  valid_to?: string; mode_reason?: string; company_id?: string
}): Promise<{ override_id: string; catchup?: CatchupSummary }> {
  const { data } = await axios.post('/api/settings/tds/vendor-overrides', body)
  return data
}
export async function updateVendorOverride(id: string, body: object): Promise<void> {
  await axios.put(`/api/settings/tds/vendor-overrides/${id}`, body)
}
export async function deleteVendorOverride(id: string): Promise<void> {
  await axios.delete(`/api/settings/tds/vendor-overrides/${id}`)
}
export async function submitCatchupDecision(overrideId: string, decision: 'recover' | 'absorb'): Promise<void> {
  await axios.post(`/api/settings/tds/vendor-overrides/${overrideId}/catchup-decision`, { decision })
}
export async function fetchFyState(supplierId?: string, fy?: string, companyId?: string): Promise<VendorTdsFyState[]> {
  const { data } = await axios.get<VendorTdsFyState[]>('/api/settings/tds/fy-state',
    { params: { ...(supplierId ? { supplier_id: supplierId } : {}), ...(fy ? { fy } : {}), ...(companyId ? { company_id: companyId } : {}) } })
  return data
}

// ── Custom Categories ─────────────────────────────────────────────────────────

export interface Category {
  id: string
  name: string
  exp_head?: string | null
}

export async function fetchCategories(companyId?: string): Promise<Category[]> {
  const { data } = await axios.get<{ categories: Category[] }>('/api/categories',
    { params: companyId ? { company_id: companyId } : {} })
  return data.categories
}

export async function createCategory(name: string, expHead?: string, companyId?: string): Promise<Category> {
  const { data } = await axios.post<Category>('/api/categories', { name, exp_head: expHead || null, company_id: companyId })
  return data
}

export async function updateCategoryExpHead(id: string, expHead: string | null, companyId?: string): Promise<Category> {
  const { data } = await axios.patch<Category>(`/api/categories/${id}`, { exp_head: expHead || null, company_id: companyId })
  return data
}

export async function deleteCategory(id: string, companyId?: string): Promise<void> {
  await axios.delete(`/api/categories/${id}`,
    { params: companyId ? { company_id: companyId } : {} })
}

export async function fetchCategoryExpHeads(companyId?: string): Promise<Record<string, string>> {
  const { data } = await axios.get<Record<string, string>>('/api/category-exp-heads',
    { params: companyId ? { company_id: companyId } : {} })
  return data
}

export async function setCategoryExpHead(name: string, expHead: string | null, companyId?: string): Promise<void> {
  await axios.put('/api/category-exp-heads', { name, exp_head: expHead || null, company_id: companyId })
}

// ── Cost centers (optional, admin-configurable, brand-scoped bill tagging) ────
export interface CostCenter {
  id: string
  brand_id: string | null
  name: string
  is_active: boolean
  created_at: string | null
}

export async function fetchCostCenters(companyId?: string, brandId?: string, includeInactive = true): Promise<CostCenter[]> {
  const params: Record<string, string> = { include_inactive: String(includeInactive) }
  if (companyId) params.company_id = companyId
  if (brandId) params.brand_id = brandId
  const { data } = await axios.get<{ cost_centers: CostCenter[] }>('/api/cost-centers', { params })
  return data.cost_centers
}

export async function createCostCenter(name: string, brandId: string, companyId?: string): Promise<CostCenter> {
  const { data } = await axios.post<CostCenter>('/api/cost-centers', { name, brand_id: brandId, company_id: companyId })
  return data
}

export async function updateCostCenter(id: string, body: { name?: string; is_active?: boolean; company_id?: string }): Promise<CostCenter> {
  const { data } = await axios.patch<CostCenter>(`/api/cost-centers/${id}`, body)
  return data
}

export async function deleteCostCenter(id: string, companyId?: string): Promise<void> {
  await axios.delete(`/api/cost-centers/${id}`, { params: companyId ? { company_id: companyId } : {} })
}
