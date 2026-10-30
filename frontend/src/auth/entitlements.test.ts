import { describe, it, expect, beforeEach } from 'vitest'
import {
  ENTITLEMENTS_CACHE_KEY, clearCache, hasService, normalizeEntitlements,
  readCache, usageFor, writeCache,
} from './entitlements'
import { ALL_SERVICES, SERVICE_CATEGORIES, SERVICE_DESCRIPTIONS, SERVICE_LABELS } from './services'

describe('normalizeEntitlements', () => {
  it('fills in every known service so "absent" and "off" are the same thing', () => {
    const e = normalizeEntitlements({ company_id: 'c1', services: {} })
    for (const key of ALL_SERVICES) expect(e.services[key]?.enabled).toBe(false)
  })

  it('reads the enabled flag from the server payload', () => {
    const e = normalizeEntitlements({
      company_id: 'c1',
      services: { fx: { enabled: true }, tds: { enabled: false } },
    })
    expect(hasService(e, 'fx')).toBe(true)
    expect(hasService(e, 'tds')).toBe(false)
  })

  it('bridges the legacy single-flag payload from /api/tenant/features', () => {
    // Lets the frontend deploy ahead of the backend without losing SAP gating.
    const e = normalizeEntitlements({ company_id: 'c1', sap_matching: true })
    expect(hasService(e, 'sap_matching')).toBe(true)
    expect(hasService(e, 'fx')).toBe(false)
  })

  it('fails closed on a null or malformed payload', () => {
    expect(hasService(normalizeEntitlements(null), 'bills')).toBe(false)
    expect(hasService(normalizeEntitlements('nonsense'), 'bills')).toBe(false)
    expect(hasService(null, 'bills')).toBe(false)
  })
})

describe('usageFor', () => {
  const withLimits = (limits: Record<string, number>) =>
    normalizeEntitlements({ company_id: 'c1', services: { upload_ocr: { enabled: true, limits } } })

  it('treats a missing limit as unmetered', () => {
    const u = usageFor(withLimits({ used: 40 }), 'upload_ocr')
    expect(u).toEqual({ used: 40, limit: null, pct: null, exceeded: false })
  })

  it('reports a fraction against the limit', () => {
    expect(usageFor(withLimits({ used: 250, limit: 1000 }), 'upload_ocr')?.pct).toBe(0.25)
  })

  it('counts exactly-at-limit as exceeded — the next unit is the one that fails', () => {
    expect(usageFor(withLimits({ used: 500, limit: 500 }), 'upload_ocr')?.exceeded).toBe(true)
    expect(usageFor(withLimits({ used: 499, limit: 500 }), 'upload_ocr')?.exceeded).toBe(false)
  })

  it('clamps the fraction when over the limit', () => {
    expect(usageFor(withLimits({ used: 900, limit: 500 }), 'upload_ocr')?.pct).toBe(1)
  })
})

describe('cache', () => {
  beforeEach(() => { sessionStorage.clear() })

  it('round-trips for the same company', () => {
    writeCache(normalizeEntitlements({ company_id: 'c1', services: { fx: { enabled: true } } }))
    expect(hasService(readCache('c1'), 'fx')).toBe(true)
  })

  it('refuses to serve another company’s entitlements', () => {
    // The guard that matters during impersonation: ops switching tenants must
    // never inherit the previous tenant's modules from cache.
    writeCache(normalizeEntitlements({ company_id: 'c1', services: { fx: { enabled: true } } }))
    expect(readCache('c2')).toBeNull()
  })

  it('returns null rather than throwing on corrupt cache data', () => {
    sessionStorage.setItem(ENTITLEMENTS_CACHE_KEY, '{not json')
    expect(readCache('c1')).toBeNull()
  })

  it('clears', () => {
    writeCache(normalizeEntitlements({ company_id: 'c1' }))
    clearCache()
    expect(readCache('c1')).toBeNull()
  })
})

describe('service vocabulary', () => {
  it('lists every service in exactly one category', () => {
    const flat = SERVICE_CATEGORIES.flatMap(c => c.services)
    expect(new Set(flat).size).toBe(flat.length)
    expect(flat.sort()).toEqual([...ALL_SERVICES].sort())
  })

  it('gives every service a label and a description', () => {
    for (const key of ALL_SERVICES) {
      expect(SERVICE_LABELS[key], `label for ${key}`).toBeTruthy()
      expect(SERVICE_DESCRIPTIONS[key], `description for ${key}`).toBeTruthy()
    }
  })
})
