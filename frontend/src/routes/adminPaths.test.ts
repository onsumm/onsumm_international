import { describe, it, expect } from 'vitest'
import { ADMIN_PATHS, adminPageForPath, isAdminPath, tenantPath } from './adminPaths'
import { isAppPath, isKnownRoute, pageForPath } from './paths'

/**
 * The ops panel has its own URL registry. These tests pin the boundary between it
 * and the tenant contract in routes/paths.ts — if `/admin/*` ever leaked into
 * `isAppPath`, a signed-out deep link to an ops page would be remembered as a
 * tenant destination and replayed after a *customer* signs in.
 */
describe('admin paths stay out of the tenant contract', () => {
  const samples = ['/admin', '/admin/tenants', '/admin/tenants/abc/licensing', '/admin/audit']

  it('is not a tenant app path', () => {
    for (const p of samples) expect(isAppPath(p), p).toBe(false)
  })

  it('has no tenant nav page', () => {
    for (const p of samples) expect(pageForPath(p), p).toBeNull()
  })

  it('is not a known tenant route', () => {
    for (const p of samples) expect(isKnownRoute(p), p).toBe(false)
  })
})

describe('isAdminPath', () => {
  it('matches the admin root and its children', () => {
    expect(isAdminPath('/admin')).toBe(true)
    expect(isAdminPath('/admin/tenants')).toBe(true)
    expect(isAdminPath('/admin/tenants/abc/usage')).toBe(true)
  })

  it('does not match a path that merely starts with the same letters', () => {
    // The same prefix trap paths.test.ts guards with '/billsomething'.
    expect(isAdminPath('/administration')).toBe(false)
    expect(isAdminPath('/admin-tools')).toBe(false)
    expect(isAdminPath('/bills')).toBe(false)
  })

  it('ignores the query string', () => {
    expect(isAdminPath('/admin/tenants?q=acme')).toBe(true)
  })
})

describe('tenantPath', () => {
  it('omits the default tab, like vendorPath does', () => {
    expect(tenantPath('t1')).toBe('/admin/tenants/t1')
    expect(tenantPath('t1', 'overview')).toBe('/admin/tenants/t1')
  })

  it('includes any other tab', () => {
    expect(tenantPath('t1', 'licensing')).toBe('/admin/tenants/t1/licensing')
    expect(tenantPath('t1', 'audit')).toBe('/admin/tenants/t1/audit')
  })
})

describe('ADMIN_PATHS', () => {
  it('has no duplicate targets', () => {
    const values = Object.values(ADMIN_PATHS)
    expect(new Set(values).size).toBe(values.length)
  })

  it('resolves each path back to its page', () => {
    for (const [page, path] of Object.entries(ADMIN_PATHS)) {
      expect(adminPageForPath(path)).toBe(page)
    }
  })
})
