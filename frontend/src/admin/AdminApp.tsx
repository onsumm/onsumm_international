/**
 * The ops panel: its own routes, its own identity, its own shell.
 *
 * Mounted from App.tsx via a lazy import, so a tenant user never downloads any of
 * this — defence in depth on top of the server-side identity split.
 */
import { useEffect } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { Building2, ClipboardList, LogOut, ScrollText, UserPlus, Users2 } from 'lucide-react'
import { Button } from '../ui'
import {
  ADMIN_LOGIN_PATH, ADMIN_PATHS, adminPageForPath, adminTitleForPath, type AdminPage,
} from '../routes/adminPaths'
import { AdminAuthProvider, useAdminAuth } from './AdminAuthContext'
import AdminLoginPage from './pages/AdminLoginPage'
import TenantsPage from './pages/TenantsPage'
import TenantDetailPage from './pages/TenantDetailPage'
import OnboardingWizard from './pages/OnboardingWizard'
import AuditLogPage from './pages/AuditLogPage'
import PlatformUsersPage from './pages/PlatformUsersPage'
import './admin.css'

const NAV: { page: AdminPage; label: string; icon: React.ElementType }[] = [
  { page: 'tenants', label: 'Tenants', icon: Building2 },
  { page: 'onboarding', label: 'Onboard a client', icon: UserPlus },
  { page: 'platform-users', label: 'Platform users', icon: Users2 },
  { page: 'audit', label: 'Audit log', icon: ScrollText },
  { page: 'catalog', label: 'Service catalog', icon: ClipboardList },
]

function AdminShell({ children }: { children: React.ReactNode }) {
  const { admin, logout } = useAdminAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const active = adminPageForPath(location.pathname)

  return (
    <div className="adm">
      <nav className="adm__rail" aria-label="Ops">
        <div className="adm__brand">
          Onsumm <span className="adm__brand-tag">Ops</span>
        </div>
        {NAV.map(({ page, label, icon: Icon }) => (
          <button
            key={page}
            className={`adm__navitem${active === page ? ' adm__navitem--active' : ''}`}
            onClick={() => navigate(ADMIN_PATHS[page])}
          >
            <Icon size={16} aria-hidden="true" />
            {label}
          </button>
        ))}
      </nav>
      <div className="adm__main">
        <header className="adm__topbar">
          <span className="adm__who">
            {admin?.name} · <strong>{admin?.role}</strong>
          </span>
          <Button
            size="sm"
            variant="secondary"
            iconLeft={<LogOut size={14} />}
            onClick={() => { void logout() }}
          >
            Sign out
          </Button>
        </header>
        <div className="adm__body">{children}</div>
      </div>
    </div>
  )
}

function AdminRoutes() {
  const { admin } = useAdminAuth()
  const location = useLocation()

  useEffect(() => {
    document.title = adminTitleForPath(location.pathname)
  }, [location.pathname])

  if (!admin) {
    // Remember where they were headed so the deep link survives sign-in.
    return location.pathname === ADMIN_LOGIN_PATH
      ? <AdminLoginPage />
      : <Navigate to={ADMIN_LOGIN_PATH} replace state={{ from: location.pathname }} />
  }

  return (
    <AdminShell>
      <Routes>
        <Route path="/admin" element={<Navigate to={ADMIN_PATHS.tenants} replace />} />
        <Route path={ADMIN_LOGIN_PATH} element={<Navigate to={ADMIN_PATHS.tenants} replace />} />
        <Route path={ADMIN_PATHS.tenants} element={<TenantsPage />} />
        <Route path={`${ADMIN_PATHS.tenants}/:tenantId`} element={<TenantDetailPage />} />
        <Route path={`${ADMIN_PATHS.tenants}/:tenantId/:tab`} element={<TenantDetailPage />} />
        <Route path={ADMIN_PATHS.onboarding} element={<OnboardingWizard />} />
        <Route path={ADMIN_PATHS['platform-users']} element={<PlatformUsersPage />} />
        <Route path={ADMIN_PATHS.audit} element={<AuditLogPage />} />
        <Route path={ADMIN_PATHS.catalog} element={<TenantsPage catalogOnly />} />
        <Route path="*" element={<Navigate to={ADMIN_PATHS.tenants} replace />} />
      </Routes>
    </AdminShell>
  )
}

export default function AdminApp() {
  return (
    <AdminAuthProvider>
      <AdminRoutes />
    </AdminAuthProvider>
  )
}
