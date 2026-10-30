/**
 * The ops session — deliberately parallel to auth/AuthContext but sharing nothing.
 *
 * Separate storage keys mean an ops user can be signed into the panel *and* into
 * a tenant (via impersonation) simultaneously, which is the whole point: exiting
 * a support session returns to the panel without signing in again.
 */
import {
  createContext, useCallback, useContext, useEffect, useState, type ReactNode,
} from 'react'
import {
  adminLogin, adminLogout, clearAdminSession, readAdminUser, type AdminUser,
} from '../api/admin'

interface AdminAuthState {
  admin: AdminUser | null
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
}

const Ctx = createContext<AdminAuthState | null>(null)

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<AdminUser | null>(() => readAdminUser())

  const login = useCallback(async (email: string, password: string) => {
    setAdmin(await adminLogin(email, password))
  }, [])

  const logout = useCallback(async () => {
    await adminLogout()
    setAdmin(null)
  }, [])

  // api/admin.ts fires this when a refresh fails, so a dead ops session clears
  // the panel rather than leaving it rendering against 401s.
  useEffect(() => {
    const onLogout = () => {
      clearAdminSession()
      setAdmin(null)
    }
    window.addEventListener('admin:auth:logout', onLogout)
    return () => window.removeEventListener('admin:auth:logout', onLogout)
  }, [])

  return <Ctx.Provider value={{ admin, login, logout }}>{children}</Ctx.Provider>
}

export function useAdminAuth(): AdminAuthState {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useAdminAuth must be used inside <AdminAuthProvider>')
  return ctx
}

/** Least → most privileged, mirroring auth.py's _PLATFORM_ROLE_RANK. */
const RANK: Record<string, number> = { readonly: 0, support: 1, admin: 2, owner: 3 }

export function useAdminCan(minimum: 'readonly' | 'support' | 'admin' | 'owner'): boolean {
  const { admin } = useAdminAuth()
  return (RANK[admin?.role ?? 'readonly'] ?? 0) >= RANK[minimum]
}
