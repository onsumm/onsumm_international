// Tenant entitlements — API client.
// Modelled on api/billing.ts: tenant is resolved server-side from the JWT, so no
// company_id is sent. Deliberately separate from api/admin.ts, because tenant code
// must never import admin code.
import { normalizeEntitlements, type Entitlements } from '../auth/entitlements'

const BASE = () => import.meta.env.VITE_API_URL ?? ''

function authHeaders(): HeadersInit {
  const token = sessionStorage.getItem('onsum_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

/**
 * Fetch every service this tenant is licensed for.
 *
 * Falls back to the legacy single-flag /api/tenant/features when the newer
 * endpoint is missing, so the frontend can deploy ahead of the backend without
 * losing SAP gating in the interim.
 */
export async function fetchEntitlements(): Promise<Entitlements> {
  const res = await fetch(`${BASE()}/api/tenant/entitlements`, { headers: authHeaders() })
  if (res.ok) return normalizeEntitlements(await res.json())

  if (res.status === 404) {
    const legacy = await fetch(`${BASE()}/api/tenant/features`, { headers: authHeaders() })
    if (legacy.ok) return normalizeEntitlements(await legacy.json())
  }
  throw new Error(`Could not load your plan (${res.status})`)
}
