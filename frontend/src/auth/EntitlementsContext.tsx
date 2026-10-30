/**
 * One place that knows what the signed-in tenant is licensed for.
 *
 * Before this, three pages each fetched `/api/tenant/features` independently and
 * kept their own `sapEnabled` state — three requests per visit for one boolean,
 * and no way to gate anything else.
 *
 * Loading is treated as "not licensed" on purpose: briefly hiding a module the
 * customer owns is recoverable, flashing one they don't own is not.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from 'react'
import { fetchEntitlements } from '../api/entitlements'
import { useAuth } from './AuthContext'
import {
  clearCache, hasService, normalizeEntitlements, readCache, usageFor, writeCache,
  type Entitlements, type UsageView,
} from './entitlements'
import type { ServiceKey } from './services'
import { INDIA_JURISDICTION, type Jurisdiction } from './jurisdiction'

type Status = 'idle' | 'loading' | 'ready' | 'error'

interface EntitlementsState {
  status: Status
  entitlements: Entitlements | null
  error: string | null
  has: (key: ServiceKey) => boolean
  usage: (key: ServiceKey) => UsageView | null
  refresh: () => Promise<void>
}

const Ctx = createContext<EntitlementsState | null>(null)

/** Dispatch to force a re-read, e.g. after ops changes this tenant's licensing. */
export const ENTITLEMENTS_INVALIDATE_EVENT = 'entitlements:invalidate'

export function EntitlementsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const companyId = user?.company_id ?? null

  const [entitlements, setEntitlements] = useState<Entitlements | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  // Guards against a slow response for a previous tenant overwriting a newer one.
  const requestId = useRef(0)

  const load = useCallback(async () => {
    if (!companyId) {
      setEntitlements(null)
      setStatus('idle')
      return
    }
    const id = ++requestId.current
    const cached = readCache(companyId)
    if (cached) setEntitlements(cached)
    setStatus('loading')
    setError(null)
    try {
      const fresh = await fetchEntitlements()
      if (id !== requestId.current) return
      setEntitlements(fresh)
      writeCache(fresh)
      setStatus('ready')
    } catch (err) {
      if (id !== requestId.current) return
      if (cached) {
        // Serve the cached view rather than blanking the nav on a transient error.
        setStatus('ready')
        return
      }
      setEntitlements(normalizeEntitlements({ company_id: companyId }))
      setError(err instanceof Error ? err.message : 'Could not load your plan')
      setStatus('error')
    }
  }, [companyId])

  useEffect(() => { void load() }, [load])

  // Signing out must drop the cache, or the next person to sign in on this
  // browser briefly sees the previous tenant's modules.
  useEffect(() => {
    const onLogout = () => {
      clearCache()
      setEntitlements(null)
      setStatus('idle')
    }
    const onInvalidate = () => { void load() }
    window.addEventListener('auth:logout', onLogout)
    window.addEventListener(ENTITLEMENTS_INVALIDATE_EVENT, onInvalidate)
    return () => {
      window.removeEventListener('auth:logout', onLogout)
      window.removeEventListener(ENTITLEMENTS_INVALIDATE_EVENT, onInvalidate)
    }
  }, [load])

  const value = useMemo<EntitlementsState>(() => ({
    status,
    entitlements,
    error,
    has: (key) => (status === 'loading' && !entitlements ? false : hasService(entitlements, key)),
    usage: (key) => usageFor(entitlements, key),
    refresh: load,
  }), [status, entitlements, error, load])

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useEntitlements(): EntitlementsState {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useEntitlements must be used inside <EntitlementsProvider>')
  return ctx
}

/** True only when the service is known to be licensed. False while loading. */
export function useService(key: ServiceKey): boolean {
  return useEntitlements().has(key)
}

/** Three-state, for guards that should show a spinner rather than "unavailable". */
export function useServiceState(key: ServiceKey): 'loading' | 'on' | 'off' {
  const { status, has } = useEntitlements()
  if (status === 'loading' || status === 'idle') return 'loading'
  return has(key) ? 'on' : 'off'
}

/**
 * The currency the signed-in tenant keeps its books in ('INR' for Indian
 * tenants, 'USD' for the crypto rail's Lumen Labs). 'INR' until loaded, which
 * matches every tenant that existed before the field did.
 */
export function useBaseCurrency(): string {
  return useEntitlements().entitlements?.base_currency ?? 'INR'
}

/**
 * Which country's rules apply to the signed-in tenant (auth/jurisdiction.ts).
 * India until loaded, so an Indian tenant never sees an India-only field flicker.
 */
export function useJurisdiction(): Jurisdiction {
  return useEntitlements().entitlements?.jurisdiction ?? INDIA_JURISDICTION
}
