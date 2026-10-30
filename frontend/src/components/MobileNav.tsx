import { useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, FileText, Upload, Users, MoreHorizontal } from 'lucide-react'
import { useState } from 'react'
import { PATHS, pageForPath, type NavPage } from '../routes/paths'
import { useAuth } from '../auth/AuthContext'
import { can } from '../auth/permissions'
import { useEntitlements } from '../auth/EntitlementsContext'
import type { ServiceKey } from '../auth/services'
import { Modal } from '../ui'

// `perm` mirrors Sidebar's requiredPermission: any one of them is enough.
const PRIMARY: { icon: React.ElementType; label: string; page: NavPage; perm: string | string[] }[] = [
  { icon: LayoutDashboard, label: 'Dashboard', page: 'dashboard', perm: 'payables.view' },
  { icon: FileText, label: 'Bills', page: 'bills', perm: ['payables.view', 'payables.approve'] },
  { icon: Upload, label: 'Upload', page: 'upload', perm: 'payables.initiate' },
  { icon: Users, label: 'Vendors', page: 'vendors', perm: 'payables.view' },
]

// `service` mirrors Sidebar's requiredService: hidden unless the tenant bought it.
const OVERFLOW: { label: string; page: NavPage; perm: string | string[]; service?: ServiceKey }[] = [
  { label: 'Review', page: 'review', perm: 'payables.view', service: 'review_hub' },
  { label: 'Payment Hub', page: 'payments', perm: ['payables.view', 'payables.approve'], service: 'disbursement' },
  { label: 'Reports', page: 'reports', perm: 'payables.view' },
  { label: 'Data Extraction', page: 'data-extraction', perm: 'payables.view', service: 'data_extraction' },
  { label: 'Agent Hub', page: 'agent-hub', perm: 'payables.view', service: 'agent_hub' },
  { label: 'Nova', page: 'nova', perm: 'payables.view', service: 'nova_billing' },
  { label: 'Integrations', page: 'integrations', perm: 'team.edit_permissions' },
  { label: 'Settings', page: 'settings', perm: 'team.edit_permissions' },
]

/**
 * Bottom navigation for phones and small tablets, where the sidebar is hidden.
 *
 * The audit's E8 finding was that there is no mobile story at all — and approver
 * mobility is the single most-cited reason finance teams buy AP software.
 */
export default function MobileNav({ pendingCount = 0 }: { pendingCount?: number }) {
  const { user } = useAuth()
  const { has } = useEntitlements()
  const navigate = useNavigate()
  const location = useLocation()
  const active = pageForPath(location.pathname)
  const [moreOpen, setMoreOpen] = useState(false)

  if (!user) return null
  const allowed = (perm: string | string[]) =>
    (Array.isArray(perm) ? perm : [perm]).some(p => can(user.role, p as Parameters<typeof can>[1]))
  const primary = PRIMARY.filter(i => allowed(i.perm))
  const overflow = OVERFLOW.filter(i => allowed(i.perm) && (!i.service || has(i.service)))

  return (
    <>
      <nav className="mn" aria-label="Main">
        {primary.map(({ icon: Icon, label, page }) => (
          <button
            key={page}
            className={`mn__item${active === page ? ' mn__item--active' : ''}`}
            onClick={() => navigate(PATHS[page])}
            aria-current={active === page ? 'page' : undefined}
          >
            <span className="mn__icon">
              <Icon size={19} aria-hidden="true" />
              {page === 'bills' && pendingCount > 0 && <span className="mn__dot" aria-hidden="true" />}
            </span>
            {label}
          </button>
        ))}
        {overflow.length > 0 && (
          <button
            className={`mn__item${overflow.some(i => i.page === active) ? ' mn__item--active' : ''}`}
            onClick={() => setMoreOpen(true)}
            aria-haspopup="dialog"
          >
            <span className="mn__icon">
              <MoreHorizontal size={19} aria-hidden="true" />
            </span>
            More
          </button>
        )}
      </nav>

      <Modal open={moreOpen} onClose={() => setMoreOpen(false)} title="All sections" size="sm">
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {overflow.map(({ label, page }) => (
            <button
              key={page}
              className="mn__sheet-item"
              onClick={() => {
                setMoreOpen(false)
                navigate(PATHS[page])
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </Modal>
    </>
  )
}
