import { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import { type Role } from './permissions'
import { setTokens, clearTokens, getAuthToken, getRefreshToken, refreshTokens } from '../api/invoices'
import { clearCache as clearEntitlementsCache } from './entitlements'

export interface AuthUser {
  username: string
  role: Role
  name: string
  email: string
  company_id: string
  company_name: string
  department: string | null
}

export interface Brand {
  id: string
  name: string
  has_logo: boolean
  is_default: boolean
  created_at: string | null
}

interface AuthState {
  user: AuthUser | null
  brands: Brand[]
  activeBrand: Brand | null
  setActiveBrand: (brand: Brand) => void
  login: (username: string, password: string) => Promise<boolean>
  loginWithSso: (code: string) => Promise<boolean>
  logout: () => void
}

const AuthContext = createContext<AuthState>({
  user: null,
  brands: [],
  activeBrand: null,
  setActiveBrand: () => {},
  login: async () => false,
  loginWithSso: async () => false,
  logout: () => {},
})

const API_BASE = import.meta.env.VITE_API_URL ?? ''

/**
 * Drop every cached bill/vendor row.
 *
 * These caches are keyed per company, role and brand — the real keys look like
 * `onsum_bills_cache_v3_<companyId>_<role>_<brandId>` (api/invoices.ts). Sign-out
 * previously removed the bare names `onsum_bills_cache` / `onsum_vendors_cache`,
 * which no code ever writes, so one tenant's bills stayed in localStorage after
 * signing out and were still there for whoever signed in next on that browser.
 * That matters much more now that ops can switch tenants via impersonation.
 */
function clearDataCaches(): void {
  Object.keys(localStorage).forEach(k => {
    if (k.startsWith('onsum_bills_cache') || k.startsWith('onsum_vendors_cache')) {
      localStorage.removeItem(k)
    }
  })
}

// The /api/login payload carries the username as `sub` (JWT subject), not
// `username`. Normalize so `user.username` is always populated.
function hydrateUser(raw: unknown): AuthUser | null {
  if (!raw || typeof raw !== 'object') return null
  const u = raw as AuthUser & { sub?: string }
  return { ...u, username: u.username ?? u.sub ?? '' }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => {
    try {
      const raw = sessionStorage.getItem('onsum_auth')
      return raw ? hydrateUser(JSON.parse(raw)) : null
    } catch { return null }
  })

  const [brands, setBrands] = useState<Brand[]>([])
  const [activeBrand, setActiveBrandState] = useState<Brand | null>(() => {
    try {
      const raw = sessionStorage.getItem('onsum_active_brand')
      return raw ? JSON.parse(raw) : null
    } catch { return null }
  })

  // Load brands whenever user logs in or changes
  useEffect(() => {
    if (!user) { setBrands([]); setActiveBrandState(null); return }
    fetch(`${API_BASE}/api/brands?company_id=${user.company_id}`)
      .then(r => r.json())
      .then((data: { brands: Brand[] }) => {
        const list = data.brands ?? []
        setBrands(list)
        // Restore saved active brand or fall back to default
        const saved = sessionStorage.getItem('onsum_active_brand')
        if (saved) {
          try {
            const parsed: Brand = JSON.parse(saved)
            const still = list.find(b => b.id === parsed.id)
            if (still) { setActiveBrandState(still); return }
          } catch { /* ignore */ }
        }
        const def = list.find(b => b.is_default) ?? list[0] ?? null
        setActiveBrandState(def)
        if (def) sessionStorage.setItem('onsum_active_brand', JSON.stringify(def))
      })
      .catch(() => {})
  }, [user?.company_id])

  const setActiveBrand = (brand: Brand) => {
    setActiveBrandState(brand)
    sessionStorage.setItem('onsum_active_brand', JSON.stringify(brand))
    clearDataCaches()   // new brand ⇒ different rows
  }

  // Both sign-in paths end the same way; only the credential differs.
  const applySession = (token: string, refresh_token: string, u: unknown) => {
    const nu = hydrateUser(u)
    sessionStorage.setItem('onsum_auth', JSON.stringify(nu))
    setTokens(token, refresh_token)
    sessionStorage.removeItem('onsum_active_brand')
    setUser(nu)
  }

  const login = async (username: string, password: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) return false
      const { token, refresh_token, user: u } = await res.json()
      applySession(token, refresh_token, u)
      return true
    } catch {
      return false
    }
  }

  // Redeems the single-use code the SSO callback put in the URL. The code is
  // worthless after one use, so a failure here means re-running the sign-in.
  const loginWithSso = async (code: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/sso/exchange`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      })
      if (!res.ok) return false
      const { token, refresh_token, user: u } = await res.json()
      applySession(token, refresh_token, u)
      return true
    } catch {
      return false
    }
  }

  // Clear local session state (no network). Used by explicit logout and when a
  // refresh fails (dead refresh token → forced sign-out via the auth:logout event).
  const clearSession = () => {
    setUser(null)
    setBrands([])
    setActiveBrandState(null)
    sessionStorage.removeItem('onsum_auth')
    clearTokens()
    sessionStorage.removeItem('onsum_active_brand')
    clearEntitlementsCache()
    clearDataCaches()
  }

  const logout = () => {
    // Best-effort server-side revocation of the refresh token, then clear locally.
    const refresh = getRefreshToken()
    if (refresh) {
      fetch(`${API_BASE}/api/auth/logout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      }).catch(() => {})
    }
    clearSession()
  }

  // Sync React state when the API layer forces a sign-out (refresh token dead).
  useEffect(() => {
    const onForcedLogout = () => clearSession()
    window.addEventListener('auth:logout', onForcedLogout)
    return () => window.removeEventListener('auth:logout', onForcedLogout)
  }, [])

  // Proactively refresh the access token shortly before it expires, so an active
  // user never hits a 401 mid-action.
  useEffect(() => {
    if (!user) return
    let timer: number
    const tokenExpMs = (): number | null => {
      const t = getAuthToken()
      if (!t) return null
      try { const p = JSON.parse(atob(t.split('.')[1])); return p.exp ? p.exp * 1000 : null } catch { return null }
    }
    const schedule = () => {
      const exp = tokenExpMs()
      const delay = exp ? Math.max(5_000, exp - Date.now() - 60_000) : 5 * 60_000
      timer = window.setTimeout(async () => {
        await refreshTokens()
        schedule()
      }, delay)
    }
    schedule()
    return () => window.clearTimeout(timer)
  }, [user?.username])

  return (
    <AuthContext.Provider value={{ user, brands, activeBrand, setActiveBrand, login, loginWithSso, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() { return useContext(AuthContext) }
