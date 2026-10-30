import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { HelpCircle, Bell, ChevronDown, FileText, LogOut, Check, Settings, Briefcase } from 'lucide-react'
import { useAuth, type Brand } from '../auth/AuthContext'
import { useEntitlements } from '../auth/EntitlementsContext'
import { fetchBills, fetchNotificationCount, type Bill } from '../api/invoices'
import { useAuthedObjectUrl } from '../api/authedFiles'
import { type Finding, fetchFindings } from '../api/review'
import { SeverityBadge } from '../pages/review/FindingBadges'
import { severityDisplay } from '../pages/review/reviewFormat'
import { useReviewCounts } from '../pages/review/useReviewStore'
import { findingPath, reviewPath } from '../routes/paths'
import { formatMoneyCompact } from '../ui'
import ProfileSettingsModal from './ProfileSettingsModal'
import { formatMoney } from '../ui/format'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

const brandLogoUrl = (brandId: string, companyId?: string) =>
  `${API_BASE}/api/brands/${brandId}/logo?company_id=${companyId ?? ''}`

/** Brand logo in the switcher list, with a neutral placeholder when absent. */
function BrandListLogo({ brand, companyId }: { brand: Brand; companyId?: string }) {
  const logo = useAuthedObjectUrl(brandLogoUrl(brand.id, companyId))
  return (
    <div className="tb__brand-item-logo">
      {logo.url
        ? <img src={logo.url} alt="" style={{ width: 24, height: 24, objectFit: 'contain', borderRadius: 3 }} />
        : <Briefcase size={14} color="#6b7280" aria-hidden="true" />}
    </div>
  )
}

interface Props {
  onGoToBills?: () => void
  onGoToBill?: (id: string) => void
}

export default function TopBar({ onGoToBills, onGoToBill }: Props) {
  const { user, logout, brands, activeBrand, setActiveBrand } = useAuth()
  const navigate = useNavigate()
  const [count, setCount]                 = useState(0)
  const [bills, setBills]                 = useState<Bill[]>([])
  const [open, setOpen]                   = useState(false)
  const [profileOpen, setProfileOpen]     = useState(false)
  const [settingsOpen, setSettingsOpen]   = useState(false)
  const [brandOpen, setBrandOpen]         = useState(false)
  const ref        = useRef<HTMLDivElement>(null)
  const profileRef = useRef<HTMLDivElement>(null)
  const brandRef   = useRef<HTMLDivElement>(null)

  // The bell's "peek" surface for the Review Hub (design brief §4 artboard 9):
  // the top five open findings by rank, the same order /review itself ranks
  // by, plus a link into the hub. Straight from the store for the badge count
  // (it already ticks down live as findings resolve elsewhere in the app —
  // see Sidebar, which reads the same hook), but the five findings themselves
  // are fetched here rather than kept in the store, since nothing else on the
  // page needs a top-five list.
  // Gated on the same entitlement Sidebar's own Review nav item checks — a
  // tenant that has not bought the Review Hub should see nothing about it
  // here either, matching how the Bills page already says nothing about Nova
  // for a tenant not licensed for it.
  const { has } = useEntitlements()
  const reviewEnabled = has('review_hub')
  const reviewCounts = useReviewCounts()
  const [reviewTop, setReviewTop] = useState<Finding[]>([])
  useEffect(() => {
    if (!user || !activeBrand || !reviewEnabled) return
    fetchFindings({ scope: 'actionable', status: 'open', group: true, limit: 5 })
      .then(res => setReviewTop(res.items))
      .catch(() => {})
    // Also on every open, not just on mount/brand-switch — a peek surface
    // showing what was true when the page loaded an hour ago is worse than
    // one that costs a request each time it's actually looked at.
  }, [user, activeBrand, reviewEnabled, open])

  const initials = user?.name
    ? user.name.split(' ').map((w: string) => w[0]).join('').toUpperCase().slice(0, 2)
    : 'U'

  useEffect(() => {
    if (!user || !activeBrand) return
    fetchNotificationCount(user.role, user.company_id).then(setCount).catch(() => {})
    fetchBills(false, user.company_id, undefined, undefined, undefined, activeBrand.id).then(list => {
      setBills(list.filter(b => {
        const roleOk = (b.allowed_roles ?? []).includes(user.role)
        const names  = b.allowed_usernames
        const nameOk = !names || names.length === 0 || names.includes(user.name) || user.role === 'admin'
        return roleOk && nameOk
      }))
    }).catch(() => {})
  }, [user, activeBrand])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current       && !ref.current.contains(e.target as Node))       setOpen(false)
      if (profileRef.current && !profileRef.current.contains(e.target as Node)) setProfileOpen(false)
      if (brandRef.current  && !brandRef.current.contains(e.target as Node))  setBrandOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Bills-pending-approval and review-findings-needing-you are two different
  // signals (Sidebar makes the same distinction), but one bell can only carry
  // one number — combined here rather than picking one and silently dropping
  // the other.
  const bellTotal = count + (reviewEnabled ? (reviewCounts?.total_actionable ?? 0) : 0)

  const fmtAmt = (b: Bill) =>
    b.total_amount != null
      // total_amount is in the tenant's books currency (the FX pipeline converts
      // foreign bills), so it formats in that, not in the bill's own currency.
      ? formatMoney(b.total_amount)
      : '—'

  const statusLabel = (s: string) =>
    s.replace('PENDING_', '').replace('_', ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())

  // Always attempt the brand-specific logo; fall back to /logo.svg while loading
  // or when the brand has none. Keying the fetch on brand ID refreshes it on switch.
  const activeLogo = useAuthedObjectUrl(
    activeBrand ? brandLogoUrl(activeBrand.id, user?.company_id) : null
  )
  const logoSrc = activeLogo.url ?? '/logo.svg'

  const handleBrandSwitch = (brand: Brand) => {
    setActiveBrand(brand)
    setBrandOpen(false)
  }

  return (
    <>
      <div className="tb">
        {/* Brand switcher */}
        <div className="tb__brand-wrap" ref={brandRef}>
          <button
            className="tb__client"
            onClick={() => brands.length > 1 && setBrandOpen(o => !o)}
            style={{ cursor: brands.length > 1 ? 'pointer' : 'default', background: 'none', border: 'none', padding: 0 }}
            title={brands.length > 1 ? 'Switch brand' : undefined}
          >
            {/* Decorative: the brand name is spelled out immediately alongside. */}
            <img
              key={activeBrand?.id ?? 'default'}
              src={logoSrc}
              alt=""
              className="tb__client-logo"
              onError={e => { (e.target as HTMLImageElement).src = '/logo.svg' }}
            />
            <span className="tb__client-name">{activeBrand?.name ?? 'Loading…'}</span>
            {brands.length > 1 && <ChevronDown size={13} style={{ color: '#737781', marginLeft: 2 }} />}
          </button>

          {brandOpen && (
            <div className="tb__brand-dd">
              <div className="tb__brand-dd-hdr">Switch Brand</div>
              {brands.map(b => (
                <button
                  key={b.id}
                  className={`tb__brand-item${b.id === activeBrand?.id ? ' tb__brand-item--active' : ''}`}
                  onClick={() => handleBrandSwitch(b)}
                >
                  <BrandListLogo brand={b} companyId={user?.company_id} />
                  <span className="tb__brand-item-name">{b.name}</span>
                  {b.id === activeBrand?.id && <Check size={13} color="#2563eb" />}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="tb__right">
          <button className="tb__help">
            <HelpCircle size={16} />
            <span>Help</span>
          </button>

          {/* Notification bell — also the Review Hub's "peek" surface (design
              brief §4 artboard 9) when the tenant has that module. */}
          <div className="tb__notif-wrap" ref={ref}>
            <button className="tb__notif" onClick={() => setOpen(o => !o)}>
              <Bell size={17} />
              {bellTotal > 0 && <span className="tb__badge">{bellTotal > 9 ? '9+' : bellTotal}</span>}
            </button>

            {open && (
              <div className="tb__dropdown">
                {reviewEnabled && reviewTop.length > 0 && (
                  <>
                    <div className="tb__dd-hdr">
                      <span className="tb__dd-title">Needs your review</span>
                      {(reviewCounts?.total_actionable ?? 0) > 0 && (
                        <span className="tb__dd-count">{reviewCounts!.total_actionable} open</span>
                      )}
                    </div>
                    <div className="tb__dd-list">
                      {reviewTop.map(f => {
                        const sev = severityDisplay(f.severity)
                        return (
                          <button
                            key={f.finding_id}
                            type="button"
                            className="tb__dd-row"
                            onClick={() => { setOpen(false); navigate(findingPath(f.finding_id)) }}
                          >
                            <div className="tb__dd-info">
                              <div className="tb__dd-vendor">{f.title}</div>
                              <div className="tb__dd-meta">
                                <SeverityBadge sev={sev} />
                                {f.money_at_risk_inr ? <span>{formatMoneyCompact(f.money_at_risk_inr)}</span> : null}
                              </div>
                            </div>
                            <div className="tb__dd-arrow">›</div>
                          </button>
                        )
                      })}
                    </div>
                    <button
                      type="button"
                      className="tb__dd-footer"
                      onClick={() => { setOpen(false); navigate(reviewPath()) }}
                    >
                      View all in Review Hub →
                    </button>
                  </>
                )}

                <div className="tb__dd-hdr">
                  <span className="tb__dd-title">Pending approvals</span>
                  {count > 0 && <span className="tb__dd-count">{count} pending</span>}
                </div>

                {bills.length === 0 ? (
                  <div className="tb__dd-empty">No pending approvals</div>
                ) : (
                  <div className="tb__dd-list">
                    {bills.map(b => (
                      <div key={b.invoice_id} className="tb__dd-row" onClick={() => { setOpen(false); onGoToBill ? onGoToBill(b.invoice_id) : onGoToBills?.() }}>
                        <div className="tb__dd-icon"><FileText size={14} /></div>
                        <div className="tb__dd-info">
                          <div className="tb__dd-vendor">{b.vendor_name || 'Unknown Vendor'}</div>
                          <div className="tb__dd-meta">
                            <span className="tb__dd-status">{statusLabel(b.status)}</span>
                            <span>·</span>
                            <span>{fmtAmt(b)}</span>
                          </div>
                        </div>
                        <div className="tb__dd-arrow">›</div>
                      </div>
                    ))}
                  </div>
                )}

                {onGoToBills && bills.length > 0 && (
                  <div className="tb__dd-footer" onClick={() => { setOpen(false); onGoToBills() }}>
                    View all in Bills →
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="tb__profile-wrap" ref={profileRef}>
            <button className="tb__user" onClick={() => setProfileOpen(o => !o)}>
              <div className="tb__avatar">{initials}</div>
              <span>{user?.name?.split(' ')[0] || 'User'}</span>
              <ChevronDown size={13} />
            </button>

            {profileOpen && (
              <div className="tb__profile-dd">
                <div className="tb__profile-hdr">
                  <div className="tb__profile-avatar-lg">{initials}</div>
                  <div>
                    <div className="tb__profile-name">{user?.name || 'User'}</div>
                    <div className="tb__profile-email">{user?.email || ''}</div>
                    <div className="tb__profile-role">{user?.role?.replace('_', ' ')}</div>
                  </div>
                </div>
                <div className="tb__profile-divider" />
                <button className="tb__profile-item" onClick={() => { setProfileOpen(false); setSettingsOpen(true) }}>
                  <Settings size={14} />
                  <span>Profile settings</span>
                </button>
                <button className="tb__profile-logout" onClick={() => { setProfileOpen(false); logout() }}>
                  <LogOut size={14} />
                  <span>Log out</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      <style>{`
        .tb {
          height: 54px; background: white; border-bottom: 1px solid #e2e8f0;
          display: flex; align-items: center; justify-content: space-between;
          padding: 0 24px; flex-shrink: 0;
          font-family: 'Inter', sans-serif;
        }
        .tb__right { display: flex; align-items: center; gap: 4px; }
        .tb__client { display: flex; align-items: center; gap: 10px; }
        .tb__client-logo { height: 42px; width: auto; object-fit: contain; border-radius: 6px; background: white; padding: 2px 4px; }
        .tb__client-name { font-size: 13px; font-weight: 600; color: #0b1c30; font-family: 'Inter', sans-serif; }

        /* Brand switcher */
        .tb__brand-wrap { position: relative; }
        .tb__brand-dd {
          position: absolute; top: calc(100% + 8px); left: 0;
          min-width: 220px; background: white; border: 1px solid #e2e8f0;
          border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.08);
          z-index: 1500; overflow: hidden;
          font-family: 'Inter', sans-serif;
        }
        .tb__brand-dd-hdr {
          padding: 10px 14px 8px; font-size: 11px; font-weight: 600;
          color: #9ca3af; text-transform: uppercase; letter-spacing: .05em;
          border-bottom: 1px solid #f3f4f6;
        }
        .tb__brand-item {
          display: flex; align-items: center; gap: 10px;
          width: 100%; padding: 10px 14px;
          background: none; border: none; cursor: pointer;
          font-size: 13px; font-weight: 500; color: #374151;
          font-family: 'Inter', sans-serif; text-align: left;
          transition: background 0.1s;
        }
        .tb__brand-item:hover { background: #f9fafb; }
        .tb__brand-item--active { color: #2563eb; background: #eff6ff; }
        .tb__brand-item--active:hover { background: #dbeafe; }
        .tb__brand-item-logo {
          width: 28px; height: 28px; border-radius: 4px; background: #f3f4f6;
          display: flex; align-items: center; justify-content: center; flex-shrink: 0;
          overflow: hidden;
        }
        .tb__brand-item-name { flex: 1; }

        .tb__help {
          display: flex; align-items: center; gap: 5px; padding: 6px 10px;
          border-radius: 4px; background: none; border: none; color: #424750;
          font-size: 13px; font-weight: 500; cursor: pointer;
          font-family: 'Inter', sans-serif;
        }
        .tb__help:hover { background: #eff4ff; color: #004080; }

        .tb__notif-wrap { position: relative; }
        .tb__notif {
          position: relative; width: 34px; height: 34px; border-radius: 4px;
          background: none; border: none; color: #424750;
          display: flex; align-items: center; justify-content: center; cursor: pointer;
        }
        .tb__notif:hover { background: #eff4ff; color: #004080; }
        .tb__badge {
          position: absolute; top: 4px; right: 4px;
          min-width: 15px; height: 15px; padding: 0 3px;
          border-radius: 8px; background: #DC2626; color: white;
          font-size: 9px; font-weight: 700;
          display: flex; align-items: center; justify-content: center; line-height: 1;
        }

        .tb__dropdown {
          position: absolute; top: calc(100% + 8px); right: 0;
          width: min(380px, calc(100vw - 24px)); background: white; border: 1px solid #e2e8f0;
          border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.05);
          z-index: 1500; overflow-y: auto; overflow-x: hidden;
          max-height: min(560px, calc(100vh - 88px));
          font-family: 'Inter', sans-serif;
        }
        .tb__dd-hdr {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 16px 10px; border-bottom: 1px solid #e2e8f0;
        }
        .tb__dd-title { font-size: 13px; font-weight: 700; color: #0b1c30; font-family: 'Public Sans', sans-serif; }
        .tb__dd-count {
          font-size: 11px; font-weight: 600; color: #DC2626;
          background: #fee2e2; padding: 2px 8px; border-radius: 99px;
        }
        .tb__dd-empty { padding: 24px 16px; text-align: center; font-size: 13px; color: #737781; }
        .tb__dd-list { max-height: 320px; overflow-y: auto; }
        .tb__dd-row {
          display: flex; align-items: center; gap: 10px; padding: 10px 16px;
          cursor: pointer; border-bottom: 1px solid #f8f9ff; transition: background 0.1s;
          /* Resets for the review row, a real <button> (keyboard/screen-reader
             operable, unlike the bills row above it) sharing this same class. */
          width: 100%; border-left: 0; border-right: 0; border-top: 0;
          background: none; font: inherit; text-align: left;
        }
        .tb__dd-row:hover { background: #eff4ff; }
        .tb__dd-row:last-child { border-bottom: none; }
        .tb__dd-icon {
          width: 32px; height: 32px; border-radius: 4px; background: #eff4ff;
          color: #004080; display: flex; align-items: center; justify-content: center; flex-shrink: 0;
        }
        .tb__dd-info { flex: 1; min-width: 0; }
        .tb__dd-vendor { font-size: 13px; font-weight: 600; color: #0b1c30; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .tb__dd-meta { display: flex; gap: 5px; align-items: center; font-size: 11px; color: #737781; margin-top: 2px; }
        .tb__dd-status { color: #F59E0B; font-weight: 600; }
        .tb__dd-arrow { color: #737781; font-size: 16px; flex-shrink: 0; }
        .tb__dd-footer {
          display: block; width: 100%;
          padding: 10px 16px; font-size: 12px; font-weight: 600; color: #004080;
          text-align: center; cursor: pointer; border: 0; border-top: 1px solid #e2e8f0;
          background: none; font-family: 'Inter', sans-serif;
        }
        .tb__dd-footer:hover { background: #eff4ff; }

        .tb__profile-wrap { position: relative; }
        .tb__user {
          display: flex; align-items: center; gap: 6px;
          padding: 5px 10px 5px 6px; border-radius: 4px;
          background: none; border: 1px solid #e2e8f0;
          font-size: 13px; font-weight: 500; color: #424750; cursor: pointer;
          font-family: 'Inter', sans-serif;
        }
        .tb__user:hover { background: #eff4ff; color: #004080; }
        .tb__avatar {
          width: 26px; height: 26px; border-radius: 50%;
          background: #004080; color: white;
          font-size: 10px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
        }

        .tb__profile-dd {
          position: absolute; top: calc(100% + 8px); right: 0;
          width: 240px; background: white; border: 1px solid #e2e8f0;
          border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.08);
          z-index: 1500; overflow: hidden;
          font-family: 'Inter', sans-serif;
        }
        .tb__profile-hdr {
          display: flex; align-items: center; gap: 12px; padding: 16px;
        }
        .tb__profile-avatar-lg {
          width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0;
          background: #004080; color: white;
          font-size: 14px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
        }
        .tb__profile-name { font-size: 13px; font-weight: 700; color: #0b1c30; }
        .tb__profile-email { font-size: 11px; color: #737781; margin-top: 2px; }
        .tb__profile-role {
          font-size: 10px; font-weight: 600; color: #004080;
          background: #eff4ff; padding: 2px 6px; border-radius: 99px;
          display: inline-block; margin-top: 4px; text-transform: capitalize;
        }
        .tb__profile-divider { height: 1px; background: #e2e8f0; }
        .tb__profile-logout {
          display: flex; align-items: center; gap: 8px;
          width: 100%; padding: 12px 16px;
          background: none; border: none; cursor: pointer;
          font-size: 13px; font-weight: 500; color: #DC2626;
          font-family: 'Inter', sans-serif; text-align: left;
        }
        .tb__profile-logout:hover { background: #fee2e2; }
        .tb__profile-item {
          display: flex; align-items: center; gap: 8px;
          width: 100%; padding: 12px 16px;
          background: none; border: none; cursor: pointer;
          font-size: 13px; font-weight: 500; color: #374151;
          font-family: 'Inter', sans-serif; text-align: left;
        }
        .tb__profile-item:hover { background: #f9fafb; color: #111827; }

        /* Phones: the bar has ~360px for logo, brand, help, bell and profile.
           Drop the words (the icons stay), let the brand name truncate, and
           keep every control at a thumb-sized 40px. */
        @media (width <= 768px) {
          .tb { padding: 0 12px; gap: 8px; }
          .tb__brand-wrap { min-width: 0; flex: 1; }
          .tb__client { min-width: 0; max-width: 100%; gap: 6px; }
          .tb__client-logo { height: 32px; flex-shrink: 0; }
          .tb__client-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
          .tb__right { flex-shrink: 0; }
          .tb__help { display: none; }
          .tb__notif { width: 40px; height: 40px; }
          .tb__user { padding: 6px; border-color: transparent; }
          .tb__user > span, .tb__user > svg { display: none; }
          .tb__avatar { width: 28px; height: 28px; }
          .tb__brand-dd { max-width: calc(100vw - 24px); }
          .tb__dropdown { position: fixed; top: 58px; left: 12px; right: 12px; width: auto; max-height: calc(100vh - 140px); }
        }
      `}</style>

      {settingsOpen && <ProfileSettingsModal onClose={() => setSettingsOpen(false)} />}
    </>
  )
}
