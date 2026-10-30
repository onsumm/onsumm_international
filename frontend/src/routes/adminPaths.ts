/**
 * URL structure for the platform (ops) panel.
 *
 * Deliberately a separate registry from routes/paths.ts. The tenant contract
 * there — `NavPage`, `PATHS`, `isAppPath`, `pageForPath` — is asserted by
 * paths.test.ts, and admin routes must not leak into it: `/admin/tenants` is not
 * a tenant page, has no sidebar entry, and must not be a redirect target after
 * a tenant login.
 */

export const ADMIN_ROOT = '/admin'
export const ADMIN_LOGIN_PATH = '/admin/login'
export const ADMIN_APP_NAME = 'Onsumm Ops'

export type AdminPage = 'tenants' | 'onboarding' | 'platform-users' | 'audit' | 'catalog'

export const ADMIN_PATHS: Record<AdminPage, string> = {
  tenants: '/admin/tenants',
  onboarding: '/admin/onboarding',
  'platform-users': '/admin/users',
  audit: '/admin/audit',
  catalog: '/admin/catalog',
}

export const TENANT_TABS = ['overview', 'licensing', 'users', 'usage', 'audit'] as const
export type TenantTab = (typeof TENANT_TABS)[number]

/** 'overview' is the landing tab, so it stays out of the URL — mirrors vendorPath(). */
export function tenantPath(tenantId: string, tab?: TenantTab): string {
  return tab && tab !== 'overview'
    ? `${ADMIN_PATHS.tenants}/${tenantId}/${tab}`
    : `${ADMIN_PATHS.tenants}/${tenantId}`
}

/**
 * True for any ops surface.
 *
 * Segment-aware so `/administration` is not treated as admin — the same prefix
 * trap paths.test.ts guards with '/billsomething'.
 */
export function isAdminPath(pathname: string): boolean {
  const path = pathname.split('?')[0]
  return path === ADMIN_ROOT || path.startsWith(`${ADMIN_ROOT}/`)
}

export function adminPageForPath(pathname: string): AdminPage | null {
  const entry = (Object.entries(ADMIN_PATHS) as [AdminPage, string][])
    .filter(([, p]) => pathname === p || pathname.startsWith(`${p}/`))
    .sort((a, b) => b[1].length - a[1].length)[0]
  return entry ? entry[0] : null
}

const TITLES: Record<string, string> = {
  [ADMIN_PATHS.tenants]: 'Tenants',
  [ADMIN_PATHS.onboarding]: 'Onboard a client',
  [ADMIN_PATHS['platform-users']]: 'Platform users',
  [ADMIN_PATHS.audit]: 'Audit log',
  [ADMIN_PATHS.catalog]: 'Service catalog',
  [ADMIN_LOGIN_PATH]: 'Sign in',
}

/** A distinct app name in the tab title, so ops can tell an ops tab from a tenant tab. */
export function adminTitleForPath(pathname: string): string {
  const exact = TITLES[pathname]
  if (exact) return `${exact} · ${ADMIN_APP_NAME}`
  const prefix = Object.keys(TITLES)
    .filter(p => pathname.startsWith(p))
    .sort((a, b) => b.length - a.length)[0]
  return prefix ? `${TITLES[prefix]} · ${ADMIN_APP_NAME}` : ADMIN_APP_NAME
}
