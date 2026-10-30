/**
 * Platform (ops) API client for /api/admin/*.
 *
 * Uses `fetch` exclusively and **never** axios. The global axios request
 * interceptor in api/invoices.ts unconditionally attaches the *tenant* bearer
 * token; an axios call from here would silently authenticate as whichever tenant
 * happens to be signed in. admin.test.ts enforces this mechanically.
 *
 * The admin session lives in its own sessionStorage namespace so ops can be
 * signed into the panel and into a tenant (via impersonation) at the same time
 * without the two clobbering each other.
 */
import type { ServiceKey } from '../auth/services'

const BASE = () => import.meta.env.VITE_API_URL ?? ''

export const ADMIN_TOKEN_KEY = 'onsum_admin_token'
export const ADMIN_REFRESH_KEY = 'onsum_admin_refresh'
export const ADMIN_USER_KEY = 'onsum_admin_auth'
export const IMPERSONATION_KEY = 'onsum_impersonation'

export type AdminRole = 'owner' | 'admin' | 'support' | 'readonly'

export interface AdminUser {
  email: string
  name: string
  role: AdminRole
}

export function getAdminToken(): string | null {
  return sessionStorage.getItem(ADMIN_TOKEN_KEY)
}

export function setAdminSession(token: string, refresh: string, admin: AdminUser): void {
  sessionStorage.setItem(ADMIN_TOKEN_KEY, token)
  sessionStorage.setItem(ADMIN_REFRESH_KEY, refresh)
  sessionStorage.setItem(ADMIN_USER_KEY, JSON.stringify(admin))
}

export function clearAdminSession(): void {
  sessionStorage.removeItem(ADMIN_TOKEN_KEY)
  sessionStorage.removeItem(ADMIN_REFRESH_KEY)
  sessionStorage.removeItem(ADMIN_USER_KEY)
}

export function readAdminUser(): AdminUser | null {
  try {
    const raw = sessionStorage.getItem(ADMIN_USER_KEY)
    return raw ? (JSON.parse(raw) as AdminUser) : null
  } catch {
    return null
  }
}

async function readError(r: Response): Promise<string> {
  const raw = await r.text().catch(() => '')
  try {
    const j = JSON.parse(raw)
    const d = j?.detail ?? j?.message
    if (d) return typeof d === 'string' ? d : (d.message ?? JSON.stringify(d))
  } catch { /* not JSON */ }
  if (r.status === 401) return 'Your ops session has expired. Please sign in again.'
  if (r.status === 403) return 'Your ops role does not allow this.'
  if (r.status >= 500) return 'The server ran into a problem. Please try again in a moment.'
  return raw && !raw.trimStart().startsWith('<') ? raw : `Request failed (${r.status}).`
}

let refreshing: Promise<string | null> | null = null

async function refreshAdminToken(): Promise<string | null> {
  if (refreshing) return refreshing
  const refresh = sessionStorage.getItem(ADMIN_REFRESH_KEY)
  if (!refresh) return null
  refreshing = (async () => {
    try {
      const r = await fetch(`${BASE()}/api/admin/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      })
      if (!r.ok) throw new Error('refresh failed')
      const d = await r.json()
      setAdminSession(d.token, d.refresh_token, d.admin)
      return d.token as string
    } catch {
      clearAdminSession()
      window.dispatchEvent(new Event('admin:auth:logout'))
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

async function call<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const token = getAdminToken()
  const res = await fetch(`${BASE()}/api/admin${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  })
  // A 401 here must refresh the *admin* session — never the tenant one, which is
  // what the global interceptor would otherwise do.
  if (res.status === 401 && retry && !path.startsWith('/login')) {
    const fresh = await refreshAdminToken()
    if (fresh) return call<T>(path, init, false)
  }
  if (!res.ok) throw new Error(await readError(res))
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

// ── Auth ─────────────────────────────────────────────────────────────────────
export async function adminLogin(email: string, password: string) {
  const d = await call<{ token: string; refresh_token: string; admin: AdminUser }>(
    '/login', { method: 'POST', body: JSON.stringify({ email, password }) },
  )
  setAdminSession(d.token, d.refresh_token, d.admin)
  return d.admin
}

export async function adminLogout(): Promise<void> {
  const refresh = sessionStorage.getItem(ADMIN_REFRESH_KEY)
  if (refresh) {
    await call('/auth/logout', {
      method: 'POST', body: JSON.stringify({ refresh_token: refresh }),
    }).catch(() => {})
  }
  clearAdminSession()
}

// ── Tenants ──────────────────────────────────────────────────────────────────
export interface TenantSummary {
  tenant_id: string
  company_name: string
  legal_name: string | null
  gstn: string | null
  plan_tier: string
  package_key: string | null
  is_active: boolean
  pipeline_enabled: boolean
  created_at: string
  onboarded_at: string | null
  primary_contact_email: string | null
  user_count: number
  service_count: number
}

export interface TenantServiceRow {
  service_key: ServiceKey
  display_name: string
  category: string
  is_core: boolean
  metered_unit: string | null
  enabled: boolean
  limits: Record<string, number | boolean | null>
  notes: string | null
  updated_at: string | null
}

export function listTenants(q?: string, service?: string) {
  const params = new URLSearchParams()
  if (q) params.set('q', q)
  if (service) params.set('service', service)
  const qs = params.toString()
  return call<{ tenants: TenantSummary[] }>(`/tenants${qs ? `?${qs}` : ''}`)
    .then(d => d.tenants)
}

export function fetchTenant(tenantId: string) {
  return call<{ tenant: TenantSummary & Record<string, unknown>; services: TenantServiceRow[] }>(
    `/tenants/${tenantId}`,
  )
}

export function patchTenant(tenantId: string, patch: Record<string, unknown>) {
  return call<{ tenant: TenantSummary }>(`/tenants/${tenantId}`, {
    method: 'PATCH', body: JSON.stringify(patch),
  })
}

// ── Licensing ────────────────────────────────────────────────────────────────
export interface ServiceChange {
  service_key: ServiceKey
  enabled: boolean
  limits?: Record<string, number | boolean | null>
  notes?: string | null
}

/** `force` is required to revoke a core service — the API 409s otherwise. */
export function saveTenantServices(tenantId: string, changes: ServiceChange[], force = false) {
  return call<{ ok: boolean; changed: number }>(
    `/tenants/${tenantId}/services${force ? '?force=true' : ''}`,
    { method: 'PUT', body: JSON.stringify({ changes }) },
  )
}

export function applyPackage(tenantId: string, packageKey: string) {
  return call<{ ok: boolean; services: number }>(`/tenants/${tenantId}/apply-package`, {
    method: 'POST', body: JSON.stringify({ package_key: packageKey }),
  })
}

export interface ServicePackage {
  key: string
  name: string
  description: string
  services: ServiceKey[] | null
}

export function listPackages() {
  return call<{ packages: ServicePackage[] }>('/packages').then(d => d.packages)
}

export function listServiceCatalog() {
  return call<{ services: Array<{ key: ServiceKey; display_name: string; description: string; category: string; is_core: boolean; metered_unit: string | null }> }>('/services')
    .then(d => d.services)
}

// ── Usage / users / audit ────────────────────────────────────────────────────
export function fetchTenantUsage(tenantId: string, months = 3) {
  return call<{ usage: Array<{ service_key: string; metric: string; period_month: string; used: number; limits: Record<string, number> }> }>(
    `/tenants/${tenantId}/usage?months=${months}`,
  ).then(d => d.usage)
}

export function fetchTenantUsers(tenantId: string) {
  return call<{ users: Array<{ username: string; name: string; role: string; email: string; department: string | null }> }>(
    `/tenants/${tenantId}/users`,
  ).then(d => d.users)
}

export interface AuditEntry {
  id: number
  actor_email: string
  action: string
  target_type: string
  target_id: string | null
  tenant_id: string | null
  company_name: string | null
  before: unknown
  after: unknown
  ip: string | null
  created_at: string
}

export function fetchAudit(params: {
  tenant_id?: string; action?: string; actor?: string; limit?: number; before_id?: number
} = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v != null) qs.set(k, String(v)) })
  const s = qs.toString()
  return call<{ entries: AuditEntry[]; next_before_id: number | null }>(`/audit${s ? `?${s}` : ''}`)
}

// ── Onboarding ───────────────────────────────────────────────────────────────
export interface OnboardPayload {
  company_name: string
  legal_name?: string
  gstn?: string
  timezone?: string
  region?: string
  primary_contact_email?: string
  package_key?: string
  admin: { username: string; password: string; name: string; email: string }
  services_override?: Record<string, boolean>
}

export function validateOnboarding(payload: OnboardPayload) {
  return call<{ ok: boolean; problems: Array<{ field: string; message: string; severity?: string }> }>(
    '/onboarding/validate', { method: 'POST', body: JSON.stringify(payload) },
  )
}

export function provisionTenant(payload: OnboardPayload) {
  return call<{ tenant_id: string; admin_username: string }>(
    '/onboarding/provision', { method: 'POST', body: JSON.stringify(payload) },
  )
}

// ── Impersonation ────────────────────────────────────────────────────────────
export interface ImpersonationGrant {
  token: string
  expires_at: string
  impersonation: {
    id: string
    tenant_id: string
    tenant_name: string
    read_only: boolean
    actor: string
  }
}

export function startImpersonation(tenantId: string, reason: string, ttlMinutes = 15) {
  return call<ImpersonationGrant>(`/tenants/${tenantId}/impersonate`, {
    method: 'POST',
    body: JSON.stringify({ reason, ttl_minutes: ttlMinutes }),
  })
}

export function endImpersonation(sessionId: string) {
  return call(`/impersonations/${sessionId}/revoke`, { method: 'POST' })
}

// ── Platform users ───────────────────────────────────────────────────────────
export interface PlatformUser {
  id: string
  email: string
  name: string
  role: AdminRole
  is_active: boolean
  last_login_at: string | null
  created_at: string
}

export function listPlatformUsers() {
  return call<{ platform_users: PlatformUser[] }>('/platform-users').then(d => d.platform_users)
}

export function createPlatformUser(input: { email: string; name: string; password: string; role: AdminRole }) {
  return call<{ platform_user: PlatformUser }>('/platform-users', {
    method: 'POST', body: JSON.stringify(input),
  })
}

export function patchPlatformUser(id: string, patch: { role?: AdminRole; is_active?: boolean }) {
  return call<{ platform_user: PlatformUser }>(`/platform-users/${id}`, {
    method: 'PATCH', body: JSON.stringify(patch),
  })
}
