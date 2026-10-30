/**
 * Pure entitlement logic — no React, no network, so it can be tested directly.
 *
 * The guiding rule here is **fail closed**: when we don't know whether a tenant
 * owns a service, we behave as if they don't. Showing a module the customer never
 * bought — and having every click 403 — is a worse failure than briefly hiding one
 * they do own, which a refresh fixes.
 */
import type { ServiceKey } from './services'
import { ALL_SERVICES } from './services'

export interface ServiceEntitlement {
  enabled: boolean
  display_name?: string
  category?: string
  limits?: Record<string, number | boolean | null>
}

import { normalizeJurisdiction, type Jurisdiction } from './jurisdiction'

export interface Entitlements {
  company_id: string
  services: Partial<Record<ServiceKey, ServiceEntitlement>>
  /** ISO 4217 currency the tenant keeps its books in: 'INR' for Indian tenants. */
  base_currency: string
  country_code: string
  is_resident_india: boolean
  /** Which country's rules apply: packs, locale, fiscal year, tax labels. */
  jurisdiction: Jurisdiction
}

export const ENTITLEMENTS_CACHE_KEY = 'onsum_entitlements'

/**
 * Coerce whatever the server returned into a complete, predictable shape.
 *
 * Also bridges the legacy `{ sap_matching: true }` payload from
 * GET /api/tenant/features, so a client can talk to an older backend without a
 * flag day.
 */
export function normalizeEntitlements(raw: unknown): Entitlements {
  const obj = (raw ?? {}) as Record<string, unknown>
  const rawServices = (obj.services ?? {}) as Record<string, unknown>
  const services: Partial<Record<ServiceKey, ServiceEntitlement>> = {}

  for (const key of ALL_SERVICES) {
    const entry = rawServices[key] as Record<string, unknown> | undefined
    services[key] = {
      enabled: Boolean(entry?.enabled),
      display_name: entry?.display_name as string | undefined,
      category: entry?.category as string | undefined,
      limits: (entry?.limits ?? {}) as ServiceEntitlement['limits'],
    }
  }

  // Legacy single-flag payload: no `services` object, just `sap_matching`.
  if (!obj.services && typeof obj.sap_matching === 'boolean') {
    services.sap_matching = { ...services.sap_matching, enabled: obj.sap_matching }
  }

  return {
    company_id: String(obj.company_id ?? ''),
    services,
    // An older backend sends none of these: every tenant was an Indian, INR one.
    base_currency: typeof obj.base_currency === 'string' && obj.base_currency ? obj.base_currency.toUpperCase() : 'INR',
    country_code: typeof obj.country_code === 'string' && obj.country_code ? obj.country_code.toUpperCase() : 'IN',
    is_resident_india: obj.is_resident_india !== false,
    jurisdiction: normalizeJurisdiction(obj.jurisdiction),
  }
}

export function hasService(e: Entitlements | null, key: ServiceKey): boolean {
  return Boolean(e?.services?.[key]?.enabled)
}

export interface UsageView {
  used: number
  limit: number | null
  pct: number | null
  exceeded: boolean
}

export function usageFor(e: Entitlements | null, key: ServiceKey): UsageView | null {
  const limits = e?.services?.[key]?.limits
  if (!limits) return null
  const used = Number(limits.used ?? 0)
  const rawLimit = limits.limit
  const limit = typeof rawLimit === 'number' ? rawLimit : null
  if (limit === null) return { used, limit: null, pct: null, exceeded: false }
  return {
    used,
    limit,
    pct: limit > 0 ? Math.min(used / limit, 1) : null,
    // At the limit counts as exceeded — the next unit is the one that fails.
    exceeded: used >= limit,
  }
}

// ── sessionStorage cache ─────────────────────────────────────────────────────
// Seeds the first paint after a reload so the sidebar doesn't reshuffle once the
// fetch lands. Keyed by company so an impersonation switch can never serve the
// previous tenant's entitlements.
export function readCache(companyId: string): Entitlements | null {
  try {
    const raw = sessionStorage.getItem(ENTITLEMENTS_CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Entitlements
    if (!parsed?.company_id || parsed.company_id !== companyId) return null
    return normalizeEntitlements(parsed)
  } catch {
    return null
  }
}

export function writeCache(e: Entitlements): void {
  try {
    sessionStorage.setItem(ENTITLEMENTS_CACHE_KEY, JSON.stringify(e))
  } catch {
    /* quota or private mode — the cache is an optimisation, not a requirement */
  }
}

export function clearCache(): void {
  try {
    sessionStorage.removeItem(ENTITLEMENTS_CACHE_KEY)
  } catch {
    /* ignore */
  }
}
