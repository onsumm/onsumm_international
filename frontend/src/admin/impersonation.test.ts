import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { clearTenantCaches, readImpersonation } from './impersonation'

/**
 * The cross-tenant leak guard.
 *
 * During a support session the browser holds a tenant token for a company the ops
 * user does not belong to. If teardown misses a cache, one customer's bills are
 * still in localStorage for the next session — which is exactly the bug that was
 * already latent in AuthContext.clearSession(): it removed `onsum_bills_cache`,
 * while the real keys are `onsum_bills_cache_<ver>_<company>_<role>_<brand>`.
 */
describe('clearTenantCaches', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
  })

  it('removes brand- and company-suffixed data caches, not just the bare names', () => {
    localStorage.setItem('onsum_bills_cache', 'x')
    localStorage.setItem('onsum_bills_cache_v3_company-1_admin_brand-1', 'x')
    localStorage.setItem('onsum_vendors_cache_company-1_brand-2', 'x')

    clearTenantCaches()

    expect(Object.keys(localStorage).filter(k => k.startsWith('onsum_bills_cache'))).toEqual([])
    expect(Object.keys(localStorage).filter(k => k.startsWith('onsum_vendors_cache'))).toEqual([])
  })

  it('clears the tenant session and its entitlements', () => {
    sessionStorage.setItem('onsum_auth', '{}')
    sessionStorage.setItem('onsum_token', 't')
    sessionStorage.setItem('onsum_refresh', 'r')
    sessionStorage.setItem('onsum_active_brand', '{}')
    sessionStorage.setItem('onsum_entitlements', '{}')
    sessionStorage.setItem('onsum_impersonation', '{}')

    clearTenantCaches()

    for (const k of ['onsum_auth', 'onsum_token', 'onsum_refresh',
                     'onsum_active_brand', 'onsum_entitlements', 'onsum_impersonation']) {
      expect(sessionStorage.getItem(k), k).toBeNull()
    }
  })

  it('leaves the ops session intact — it is what we return to', () => {
    sessionStorage.setItem('onsum_admin_token', 'admin-token')
    sessionStorage.setItem('onsum_admin_auth', '{}')

    clearTenantCaches()

    expect(sessionStorage.getItem('onsum_admin_token')).toBe('admin-token')
    expect(sessionStorage.getItem('onsum_admin_auth')).toBe('{}')
  })
})

describe('readImpersonation', () => {
  beforeEach(() => sessionStorage.clear())

  it('returns null when there is no session', () => {
    expect(readImpersonation()).toBeNull()
  })

  it('returns null rather than throwing on corrupt data', () => {
    sessionStorage.setItem('onsum_impersonation', '{not json')
    expect(readImpersonation()).toBeNull()
  })

  it('reads the session back', () => {
    sessionStorage.setItem('onsum_impersonation', JSON.stringify({
      id: 's1', tenant_id: 't1', tenant_name: 'Acme', read_only: true,
      actor: 'ops@onsumm.ai', expires_at: '2026-01-01T00:00:00Z',
    }))
    expect(readImpersonation()?.tenant_name).toBe('Acme')
  })
})

describe('the admin API client never uses axios', () => {
  it('has no axios import or call', () => {
    // The global axios request interceptor attaches the *tenant* token to every
    // request. An axios call from api/admin.ts would silently authenticate the ops
    // surface as whichever tenant happens to be signed in.
    const raw = readFileSync(join(process.cwd(), 'src/api/admin.ts'), 'utf8')
    // Strip comments first — the file explains *why* it avoids axios, and that
    // prose should not trip the check.
    const code = raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/from ['"]axios['"]/)
    expect(code).not.toMatch(/\baxios\s*[.(]/)
  })
})
