/**
 * Route guards.
 *
 * There was no reusable guard before this: `user?.role === 'admin'` appears 28
 * times across six pages, and SettingsPage checked it only *after* mounting a
 * 4,300-line component and firing its data loads.
 *
 * Note what these are and aren't. `RequireRole` and `RequireService` decide what
 * to render; the server decides what is allowed. An unlicensed endpoint 403s
 * whatever the client believes, so these exist to avoid dead links, not to secure
 * anything.
 */
import type { ReactNode } from 'react'
import { Lock } from 'lucide-react'
import { EmptyState, LoadingMark } from '../ui'
import { useAuth } from './AuthContext'
import { useServiceState } from './EntitlementsContext'
import { SERVICE_DESCRIPTIONS, SERVICE_LABELS, type ServiceKey } from './services'
import type { Role } from './permissions'

export function RequireRole({
  roles, children, fallback,
}: { roles: Role[]; children: ReactNode; fallback?: ReactNode }) {
  const { user } = useAuth()
  if (user && roles.includes(user.role)) return <>{children}</>
  return (
    <>
      {fallback ?? (
        <div className="guard__wrap">
          <EmptyState
            icon={<Lock size={20} />}
            title="Admin access only"
            message="Ask an administrator to make configuration changes."
          />
        </div>
      )}
    </>
  )
}

/**
 * Renders children only when the tenant is licensed for `service`.
 *
 * An unlicensed deep link lands here rather than on a 404 or a redirect: the route
 * exists, the licence doesn't, and those are different things. Staying at the
 * requested URL means the user can forward the link to their admin, and a reload
 * works the moment ops enables the service.
 */
export function RequireService({
  service, children, fallback,
}: { service: ServiceKey; children: ReactNode; fallback?: ReactNode }) {
  const state = useServiceState(service)

  if (state === 'loading') {
    return (
      <div className="guard__wrap">
        <LoadingMark size={22} />
      </div>
    )
  }
  if (state === 'off') {
    return <>{fallback ?? <ServiceUnavailable service={service} />}</>
  }
  return <>{children}</>
}

export function ServiceUnavailable({ service }: { service: ServiceKey }) {
  return (
    <div className="guard__wrap">
      <EmptyState
        icon={<Lock size={20} />}
        title={`${SERVICE_LABELS[service]} isn't part of your plan`}
        message={SERVICE_DESCRIPTIONS[service]}
      />
    </div>
  )
}
