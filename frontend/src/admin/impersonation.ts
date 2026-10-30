/**
 * Entering and leaving a support session.
 *
 * The riskiest thing in the panel: for the duration, the browser holds a tenant
 * token for a company the ops user does not belong to. Getting the teardown wrong
 * leaks one customer's data into the next session, so `clearTenantCaches` is the
 * function to be careful with — see impersonation.test.ts.
 */
import { IMPERSONATION_KEY, type ImpersonationGrant } from '../api/admin'
import { setTokens, clearTokens } from '../api/invoices'
import { ENTITLEMENTS_CACHE_KEY } from '../auth/entitlements'
import { PATHS } from '../routes/paths'
import { ADMIN_PATHS, tenantPath } from '../routes/adminPaths'

export interface ImpersonationMeta {
  id: string
  tenant_id: string
  tenant_name: string
  read_only: boolean
  actor: string
  expires_at: string
}

export function readImpersonation(): ImpersonationMeta | null {
  try {
    const raw = sessionStorage.getItem(IMPERSONATION_KEY)
    return raw ? (JSON.parse(raw) as ImpersonationMeta) : null
  } catch {
    return null
  }
}

/**
 * Remove every trace of the tenant session.
 *
 * The bill and vendor caches are keyed per company/role/brand
 * (`onsum_bills_cache_v3_<company>_<role>_<brand>`), so removing the bare key
 * names achieves nothing — this has to be a prefix sweep. The admin session is
 * deliberately left alone: it is what we are returning to.
 */
export function clearTenantCaches(): void {
  sessionStorage.removeItem('onsum_auth')
  sessionStorage.removeItem('onsum_active_brand')
  sessionStorage.removeItem(ENTITLEMENTS_CACHE_KEY)
  sessionStorage.removeItem(IMPERSONATION_KEY)
  clearTokens()
  Object.keys(localStorage).forEach(k => {
    if (k.startsWith('onsum_bills_cache') || k.startsWith('onsum_vendors_cache')) {
      localStorage.removeItem(k)
    }
  })
}

/**
 * Swap the browser into the tenant session.
 *
 * A full page load rather than a client-side navigate: pages hold module-level
 * caches and state seeded at first render, and trying to purge all of that
 * in-place is how one tenant's rows end up rendered under another's name. A
 * reload rebuilds everything from the new session for free.
 */
export function enterImpersonation(grant: ImpersonationGrant): void {
  clearTenantCaches()
  sessionStorage.setItem('onsum_auth', JSON.stringify({
    username: grant.impersonation.actor,
    name: `${grant.impersonation.actor} (Onsumm Support)`,
    email: grant.impersonation.actor,
    role: 'auditor',
    company_id: grant.impersonation.tenant_id,
    company_name: grant.impersonation.tenant_name,
    department: null,
  }))
  // No refresh token: the session ends at expiry and cannot be extended.
  setTokens(grant.token, null)
  sessionStorage.setItem(IMPERSONATION_KEY, JSON.stringify({
    ...grant.impersonation,
    expires_at: grant.expires_at,
  }))
  window.location.assign(PATHS.bills)
}

export function exitImpersonation(): void {
  const meta = readImpersonation()
  clearTenantCaches()
  window.location.assign(meta ? tenantPath(meta.tenant_id) : ADMIN_PATHS.tenants)
}
