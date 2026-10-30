import { useEffect, useState } from 'react'
import { X, Mail, AlertTriangle, RefreshCw, CheckCircle2 } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { fetchEmailConnection, startEmailAuthorize, disconnectEmail, type EmailConnection } from '../api/invoices'

export default function ProfileSettingsModal({ onClose }: { onClose: () => void }) {
  const { user } = useAuth()
  const [conn, setConn] = useState<EmailConnection | null>(null)
  const [loading, setLoading] = useState(true)
  const [connecting, setConnecting] = useState<'google' | 'microsoft' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [disconnecting, setDisconnecting] = useState(false)
  const [err, setErr] = useState('')

  const load = () => {
    if (!user?.company_id || !user?.username) { setLoading(false); return }
    setLoading(true)
    fetchEmailConnection(user.company_id, user.username)
      .then(setConn).catch(() => setConn({ connected: false })).finally(() => setLoading(false))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [user?.company_id, user?.username])

  const connect = async (provider: 'google' | 'microsoft') => {
    if (!user?.company_id || !user?.username) return
    setConnecting(provider); setErr('')
    try {
      const url = await startEmailAuthorize(user.company_id, user.username, provider)
      window.location.href = url
    } catch {
      setErr(`Could not start ${provider === 'google' ? 'Gmail' : 'Outlook'} connection`)
      setConnecting(null)
    }
  }

  const disconnect = async () => {
    if (!user?.company_id || !user?.username || confirmText !== 'disconnect') return
    setDisconnecting(true); setErr('')
    try {
      await disconnectEmail(user.company_id, user.username, conn?.provider)
      setConn({ connected: false }); setConfirmText(''); setConfirming(false)
    } catch { setErr('Failed to disconnect mailbox') }
    finally { setDisconnecting(false) }
  }

  const initials = user?.name ? user.name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2) : 'U'

  return (
    <div className="ps-overlay" onClick={onClose}>
      <div className="ps" onClick={e => e.stopPropagation()}>
        <div className="ps__head">
          <span>Profile settings</span>
          <button className="ps__x" onClick={onClose}><X size={16} /></button>
        </div>

        {/* Account */}
        <div className="ps__account">
          <div className="ps__avatar">{initials}</div>
          <div>
            <div className="ps__name">{user?.name || 'User'}</div>
            <div className="ps__email">{user?.email || ''}</div>
            <div className="ps__role">{user?.role?.replace('_', ' ')}</div>
          </div>
        </div>

        {/* Mailbox */}
        <div className="ps__section">
          <div className="ps__section-title"><Mail size={14} /> Mailbox</div>
          {loading ? (
            <div className="ps__muted">Loading…</div>
          ) : conn?.connected ? (
            <div className="ps__conn">
              <CheckCircle2 size={15} color="#059669" />
              <span>Connected — sending as <b>{conn.email}</b>{conn.provider === 'microsoft' ? ' (Outlook)' : ''}</span>
            </div>
          ) : (
            <div className="ps__conn ps__conn--off">
              <span>No mailbox connected. Connect Gmail or Outlook to send emails from your own address.</span>
              <button className="ps__connect" disabled={connecting !== null} onClick={() => connect('google')}>
                {connecting === 'google' ? 'Connecting…' : 'Connect Gmail'}
              </button>
              <button className="ps__connect" disabled={connecting !== null} onClick={() => connect('microsoft')}>
                {connecting === 'microsoft' ? 'Connecting…' : 'Connect Outlook'}
              </button>
              {err && <div className="ps__err"><AlertTriangle size={12} /> {err}</div>}
            </div>
          )}
        </div>

        {/* Danger zone — only when connected */}
        {conn?.connected && (
          <div className="ps__danger">
            <div className="ps__danger-title"><AlertTriangle size={14} /> Danger zone</div>
            <div className="ps__danger-body">
              <div className="ps__danger-desc">
                <b>Disconnect mailbox.</b> This revokes access to <b>{conn.email}</b>. You will stop being able to
                send or read email conversations.
              </div>

              {!confirming ? (
                <button className="ps__danger-start" onClick={() => { setConfirming(true); setErr('') }}>
                  Disconnect mailbox
                </button>
              ) : (
                <>
                  <label className="ps__danger-label">Type <span className="ps__danger-word">disconnect</span> to confirm</label>
                  <input
                    className="ps__danger-input"
                    value={confirmText}
                    onChange={e => setConfirmText(e.target.value)}
                    placeholder="disconnect"
                    autoComplete="off"
                    autoFocus
                  />
                  {err && <div className="ps__err"><AlertTriangle size={12} /> {err}</div>}
                  <div className="ps__danger-actions">
                    <button
                      className="ps__danger-cancel"
                      onClick={() => { setConfirming(false); setConfirmText(''); setErr('') }}
                      disabled={disconnecting}
                    >
                      Cancel
                    </button>
                    <button
                      className="ps__danger-btn"
                      disabled={confirmText !== 'disconnect' || disconnecting}
                      onClick={disconnect}
                    >
                      {disconnecting
                        ? <><RefreshCw size={13} style={{ animation: 'spin 1s linear infinite' }} /> Disconnecting…</>
                        : 'Confirm disconnect'}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <style>{`
        .ps-overlay {
          position: fixed; inset: 0; z-index: 2000; background: rgba(17,24,39,.45);
          display: flex; align-items: center; justify-content: center; padding: 20px;
          font-family: 'Inter', sans-serif;
        }
        .ps {
          width: 100%; max-width: 460px; background: #fff; border-radius: 16px;
          box-shadow: 0 24px 60px -12px rgba(0,0,0,.35); overflow: hidden;
        }
        .ps__head {
          display: flex; align-items: center; justify-content: space-between;
          padding: 14px 18px; border-bottom: 1px solid #f0f1f3; font-size: 15px; font-weight: 650; color: #111827;
        }
        .ps__x { background: none; border: none; color: #9ca3af; cursor: pointer; display: flex; padding: 4px; border-radius: 7px; }
        .ps__x:hover { color: #111827; background: #f3f4f6; }
        .ps__account { display: flex; align-items: center; gap: 13px; padding: 18px; }
        .ps__avatar {
          width: 46px; height: 46px; border-radius: 50%; flex-shrink: 0;
          background: #004080; color: #fff; font-size: 15px; font-weight: 700;
          display: flex; align-items: center; justify-content: center;
        }
        .ps__name { font-size: 15px; font-weight: 700; color: #0b1c30; }
        .ps__email { font-size: 12.5px; color: #6b7280; margin-top: 2px; }
        .ps__role {
          font-size: 10px; font-weight: 600; color: #004080; background: #eff4ff;
          padding: 2px 7px; border-radius: 99px; display: inline-block; margin-top: 5px; text-transform: capitalize;
        }
        .ps__section { padding: 0 18px 18px; }
        .ps__section-title {
          display: flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700;
          color: #6b7280; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 10px;
        }
        .ps__muted { font-size: 13px; color: #9ca3af; }
        .ps__conn {
          display: flex; align-items: center; gap: 8px; font-size: 13px; color: #111827;
          background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 11px 13px;
        }
        .ps__conn--off { flex-direction: column; align-items: flex-start; gap: 10px; background: #fff7ed; border-color: #fed7aa; color: #9a3412; }
        .ps__connect {
          background: #111827; color: #fff; border: none; border-radius: 8px;
          padding: 8px 14px; font-size: 12.5px; font-weight: 600; cursor: pointer;
        }
        .ps__connect:hover { background: #000; }
        .ps__connect:disabled { opacity: .6; cursor: default; }

        .ps__danger { margin: 0 18px 18px; border: 1px solid #fecaca; border-radius: 12px; overflow: hidden; }
        .ps__danger-title {
          display: flex; align-items: center; gap: 7px; padding: 10px 14px;
          background: #fef2f2; color: #b91c1c; font-size: 12.5px; font-weight: 700;
          border-bottom: 1px solid #fecaca;
        }
        .ps__danger-body { padding: 14px; }
        .ps__danger-desc { font-size: 12.5px; color: #4b5563; line-height: 1.55; }
        .ps__danger-label { display: block; font-size: 12px; color: #6b7280; margin: 12px 0 6px; }
        .ps__danger-word { font-family: ui-monospace, monospace; font-weight: 700; color: #b91c1c; }
        .ps__danger-input {
          width: 100%; box-sizing: border-box; border: 1px solid #e5e7eb; border-radius: 9px;
          padding: 9px 12px; font-size: 13px; outline: none; font-family: ui-monospace, monospace;
        }
        .ps__danger-input:focus { border-color: #fca5a5; }
        .ps__err { display: flex; align-items: center; gap: 5px; font-size: 12px; color: #dc2626; margin-top: 8px; }
        .ps__danger-start {
          margin-top: 12px; background: #fff; color: #dc2626; border: 1px solid #fca5a5; border-radius: 9px;
          padding: 9px 16px; font-size: 13px; font-weight: 650; cursor: pointer;
        }
        .ps__danger-start:hover { background: #fef2f2; border-color: #f87171; }
        .ps__danger-actions { display: flex; gap: 10px; margin-top: 12px; }
        .ps__danger-cancel {
          background: #fff; color: #6b7280; border: 1px solid #e5e7eb; border-radius: 9px;
          padding: 10px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
        }
        .ps__danger-cancel:hover { background: #f9fafb; color: #374151; }
        .ps__danger-cancel:disabled { opacity: .6; cursor: default; }
        .ps__danger-btn {
          flex: 1; background: #dc2626; color: #fff; border: none; border-radius: 9px;
          padding: 10px; font-size: 13px; font-weight: 650; cursor: pointer;
          display: inline-flex; align-items: center; justify-content: center; gap: 7px;
        }
        .ps__danger-btn:hover { background: #b91c1c; }
        .ps__danger-btn:disabled { background: #fca5a5; cursor: not-allowed; }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
