import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../auth/AuthContext'
import { useService } from '../auth/EntitlementsContext'
import {
  Zap, CheckCircle2, AlertCircle, Clock, RefreshCw,
  Copy, Download, ChevronRight, Loader2, WifiOff,
  KeyRound, Trash2, Save, Eye, EyeOff,
} from 'lucide-react'
import { displayLocale } from '../ui/format'

// ── Types ──────────────────────────────────────────────────────────────────────

type ConnectionStatus = 'NOT_CONNECTED' | 'PENDING' | 'ACTIVE' | 'ERROR' | 'REVOKED'

interface TallyConnection {
  connection_id:   string
  status:          ConnectionStatus
  agent_version:   string | null
  last_sync_at:    string | null
  error_message:   string | null
  paired_at:       string | null
}

// ── API calls ──────────────────────────────────────────────────────────────────

// Turn a failed Response into a clear, human-readable message (parses FastAPI's
// {detail}, falls back to a friendly line for 5xx, never dumps raw HTML).
async function readError(r: Response): Promise<string> {
  const raw = await r.text().catch(() => '')
  try {
    const j = JSON.parse(raw)
    const d = j?.detail ?? j?.message
    if (d) return typeof d === 'string' ? d : JSON.stringify(d)
  } catch { /* not JSON */ }
  if (r.status >= 500) return 'The server ran into a problem. Please try again in a moment.'
  if (r.status === 404) return 'Service not found.'
  if (r.status === 401 || r.status === 403) return 'You are not authorised to do this.'
  // Avoid showing a raw HTML error page.
  return raw && !raw.trimStart().startsWith('<') ? raw : `Request failed (${r.status}).`
}

async function generateToken(): Promise<{ token: string; expires_at: string }> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/tally/generate-token`, { method: 'POST' })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

async function fetchConnection(): Promise<TallyConnection | null> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/tally/connection`)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

async function generateSapToken(): Promise<{ token: string; expires_at: string }> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/sap/generate-token`, { method: 'POST' })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

async function fetchSapConnection(): Promise<TallyConnection | null> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/sap/connection`)
  if (r.status === 404) return null
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

// ── Nova API key ───────────────────────────────────────────────────────────────
// Unlike Tally/SAP's agent-pairing token, this is a literal secret the tenant
// already holds (issued by Nova) and pastes in directly — no download/agent
// flow. Backend: backend_ocr/app/nova_credentials_api.py.

interface NovaCredentials {
  configured:  boolean
  masked_key:  string | null
  base_url:    string | null
  is_active:   boolean
  updated_at:  string | null
}

async function fetchNovaCredentials(): Promise<NovaCredentials> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/nova/credentials`)
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

async function saveNovaCredentials(apiKey: string, baseUrl: string): Promise<NovaCredentials> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/nova/credentials`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey, base_url: baseUrl || null }),
  })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

async function deleteNovaCredentials(): Promise<void> {
  const r = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/nova/credentials`, { method: 'DELETE' })
  if (!r.ok) throw new Error(await readError(r))
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmt(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(displayLocale(), {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function timeAgo(iso: string | null) {
  if (!iso) return null
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

// ── Status badge ───────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ConnectionStatus }) {
  const cfg: Record<ConnectionStatus, { label: string; color: string; bg: string; icon: React.ReactNode }> = {
    NOT_CONNECTED: { label: 'Not Connected', color: '#64748b', bg: '#f1f5f9', icon: <WifiOff size={12} /> },
    PENDING:       { label: 'Awaiting Agent', color: '#d97706', bg: '#fffbeb', icon: <Clock size={12} /> },
    ACTIVE:        { label: 'Connected',      color: '#16a34a', bg: '#f0fdf4', icon: <CheckCircle2 size={12} /> },
    ERROR:         { label: 'Error',          color: '#dc2626', bg: '#fef2f2', icon: <AlertCircle size={12} /> },
    REVOKED:       { label: 'Revoked',        color: '#64748b', bg: '#f1f5f9', icon: <WifiOff size={12} /> },
  }
  const { label, color, bg, icon } = cfg[status]
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 20,
      fontSize: 12, fontWeight: 600,
      color, background: bg,
    }}>
      {icon}{label}
    </span>
  )
}

// ── Token display ──────────────────────────────────────────────────────────────

function TokenBox({ token, expiresAt, onClose, agent = 'Tally', host = "the customer's PC" }: {
  token: string; expiresAt: string; onClose: () => void; agent?: string; host?: string
}) {
  const [copied, setCopied] = useState(false)

  const copy = () => {
    navigator.clipboard.writeText(token)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div style={{
      background: '#f0fdf4', border: '1px solid #bbf7d0',
      borderRadius: 10, padding: '18px 20px', marginTop: 20,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#15803d', marginBottom: 6 }}>
        Token generated — expires {fmt(expiresAt)}
      </div>
      <div style={{ fontSize: 12, color: '#374151', marginBottom: 12 }}>
        Copy this token and paste it into the Onsumm {agent} Agent on {host}.
      </div>

      {/* Token value */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        background: '#fff', border: '1px solid #d1fae5',
        borderRadius: 6, padding: '8px 12px',
      }}>
        <code style={{ flex: 1, fontSize: 13, letterSpacing: 1, color: '#111', wordBreak: 'break-all' }}>
          {token}
        </code>
        <button onClick={copy} style={{
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '5px 12px', borderRadius: 5,
          background: copied ? '#16a34a' : '#004080',
          color: '#fff', border: 'none', cursor: 'pointer',
          fontSize: 12, fontWeight: 600, flexShrink: 0,
        }}>
          <Copy size={13} />
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>

      {/* Steps */}
      <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {[
          `Download the Onsumm ${agent} Agent .exe (button below)`,
          `Double-click the .exe on the machine where ${agent} runs`,
          'Paste this token when prompted',
          'Click Connect — done',
        ].map((step, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <span style={{
              width: 20, height: 20, borderRadius: '50%',
              background: '#004080', color: '#fff',
              fontSize: 11, fontWeight: 700,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              flexShrink: 0, marginTop: 1,
            }}>{i + 1}</span>
            <span style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{step}</span>
          </div>
        ))}
      </div>

      <button onClick={onClose} style={{
        marginTop: 14, fontSize: 12, color: '#64748b',
        background: 'none', border: 'none', cursor: 'pointer', padding: 0,
      }}>
        Dismiss
      </button>
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function IntegrationsPage() {
  const [connection, setConnection]     = useState<TallyConnection | null>(null)
  const [loading, setLoading]           = useState(true)
  const [genLoading, setGenLoading]     = useState(false)
  const [token, setToken]               = useState<{ token: string; expires_at: string } | null>(null)
  const [error, setError]               = useState('')
  const [dlNote, setDlNote]             = useState<'tally' | 'sap' | null>(null)
  // SAP connection (mirrors the Tally flow)
  const [sapConn, setSapConn]           = useState<TallyConnection | null>(null)
  const [sapToken, setSapToken]         = useState<{ token: string; expires_at: string } | null>(null)
  const [sapGenLoading, setSapGenLoading] = useState(false)
  const [sapError, setSapError]         = useState('')
  const { user } = useAuth()
  const sapEnabled                      = useService('sap_matching')
  const novaEnabled                     = useService('nova_billing')

  // Nova API key card
  const [novaCreds, setNovaCreds]       = useState<NovaCredentials | null>(null)
  const [novaLoading, setNovaLoading]   = useState(true)
  const [novaEditing, setNovaEditing]   = useState(false)
  const [novaSaving, setNovaSaving]     = useState(false)
  const [novaKeyInput, setNovaKeyInput] = useState('')
  const [novaBaseUrlInput, setNovaBaseUrlInput] = useState('')
  const [novaShowKey, setNovaShowKey]   = useState(false)
  const [novaError, setNovaError]       = useState('')

  const loadNova = useCallback(async () => {
    try {
      const c = await fetchNovaCredentials()
      setNovaCreds(c)
      setNovaBaseUrlInput(c.base_url ?? '')
    } catch (e: any) {
      setNovaError(e.message)
    } finally {
      setNovaLoading(false)
    }
  }, [])

  useEffect(() => {
    if (novaEnabled) loadNova()
  }, [novaEnabled, loadNova])

  const handleNovaSave = async () => {
    if (!novaKeyInput.trim()) {
      setNovaError('Enter your Nova API key first.')
      return
    }
    setNovaSaving(true)
    setNovaError('')
    try {
      const c = await saveNovaCredentials(novaKeyInput.trim(), novaBaseUrlInput.trim())
      setNovaCreds(c)
      setNovaKeyInput('')
      setNovaEditing(false)
    } catch (e: any) {
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      const isNetwork = e instanceof TypeError
      setNovaError(
        offline ? 'You appear to be offline — check your connection and try again.'
        : isNetwork ? 'Could not reach the server. Please check your connection and try again.'
        : `Could not save the Nova API key: ${e?.message || 'unexpected error'}`,
      )
    } finally {
      setNovaSaving(false)
    }
  }

  const handleNovaRemove = async () => {
    setNovaSaving(true)
    setNovaError('')
    try {
      await deleteNovaCredentials()
      setNovaCreds({ configured: false, masked_key: null, base_url: null, is_active: false, updated_at: null })
      setNovaEditing(false)
      setNovaKeyInput('')
      setNovaBaseUrlInput('')
    } catch (e: any) {
      setNovaError(`Could not remove the Nova API key: ${e?.message || 'unexpected error'}`)
    } finally {
      setNovaSaving(false)
    }
  }

  const load = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([fetchConnection(), fetchSapConnection()])
      setConnection(c)
      setSapConn(s)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleSapGenerate = async () => {
    setSapGenLoading(true)
    setSapError('')
    setSapToken(null)
    try {
      setSapToken(await generateSapToken())
      await load()
    } catch (e: any) {
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      const isNetwork = e instanceof TypeError
      setSapError(
        offline ? 'You appear to be offline — check your connection and try again.'
        : isNetwork ? 'Could not reach the server. Please check your connection and try again.'
        : `Could not connect SAP: ${e?.message || 'unexpected error'}`,
      )
    } finally {
      setSapGenLoading(false)
    }
  }

  useEffect(() => {
    load()
    // Poll every 10s while PENDING so page updates when agent connects
    const iv = setInterval(load, 10_000)
    return () => clearInterval(iv)
  }, [load])

  const handleGenerate = async () => {
    setGenLoading(true)
    setError('')
    setToken(null)
    try {
      const t = await generateToken()
      setToken(t)
      // Refresh connection row (status → PENDING)
      await load()
    } catch (e: any) {
      // Network failures throw a TypeError with no useful message.
      const offline = typeof navigator !== 'undefined' && navigator.onLine === false
      const isNetwork = e instanceof TypeError
      setError(
        offline ? 'You appear to be offline — check your connection and try again.'
        : isNetwork ? 'Could not reach the server. Please check your connection and try again.'
        : `Could not connect Tally: ${e?.message || 'unexpected error'}`,
      )
    } finally {
      setGenLoading(false)
    }
  }

  const status: ConnectionStatus = connection?.status ?? 'NOT_CONNECTED'
  const sapStatus: ConnectionStatus = sapConn?.status ?? 'NOT_CONNECTED'

  return (
    <div style={{ height: '100%', overflow: 'auto', background: '#f8fafc', padding: '28px 32px' }}>

      {/* Page header */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <Zap size={20} color="#004080" strokeWidth={2} />
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700, color: '#0b1c30' }}>Integrations</h1>
        </div>
        <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>
          Connect your accounting &amp; ERP systems to automatically sync invoices, bills, item masters and payments.
        </p>
      </div>

      {/* Tally card */}
      <div style={{
        background: '#fff', borderRadius: 12,
        border: '1px solid #e2e8f0', maxWidth: 680,
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      }}>

        {/* Card header */}
        <div style={{
          padding: '18px 24px', borderBottom: '1px solid #f1f5f9',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* Tally logo placeholder */}
            <div style={{
              width: 44, height: 44, borderRadius: 10,
              background: '#004080', color: '#fff',
              fontSize: 16, fontWeight: 800,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>T</div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#0b1c30' }}>Tally Prime</div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                Sync ledgers, vouchers, invoices &amp; payments
              </div>
            </div>
          </div>
          <StatusBadge status={status} />
        </div>

        {/* Card body */}
        <div style={{ padding: '20px 24px' }}>
          {loading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#64748b', fontSize: 13 }}>
              <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
              Loading connection status...
            </div>
          ) : (
            <>
              {/* Stats grid — shown when ACTIVE */}
              {status === 'ACTIVE' && connection && (
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
                  gap: 12, marginBottom: 20,
                }}>
                  {[
                    { label: 'Last Sync',      value: timeAgo(connection.last_sync_at) ?? '—' },
                    { label: 'Agent Version',  value: connection.agent_version ?? '—' },
                    { label: 'Connected Since',value: fmt(connection.paired_at).split(',')[0] },
                  ].map(({ label, value }) => (
                    <div key={label} style={{
                      background: '#f8fafc', borderRadius: 8,
                      padding: '12px 14px', border: '1px solid #e2e8f0',
                    }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 500, marginBottom: 4 }}>{label}</div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: '#0b1c30' }}>{value}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Error message */}
              {status === 'ERROR' && connection?.error_message && (
                <div style={{
                  background: '#fef2f2', border: '1px solid #fecaca',
                  borderRadius: 8, padding: '10px 14px', marginBottom: 16,
                  fontSize: 13, color: '#dc2626',
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                }}>
                  <AlertCircle size={15} style={{ flexShrink: 0, marginTop: 1 }} />
                  {connection.error_message}
                </div>
              )}

              {/* Pending — waiting for agent */}
              {status === 'PENDING' && (
                <div style={{
                  background: '#fffbeb', border: '1px solid #fde68a',
                  borderRadius: 8, padding: '12px 14px', marginBottom: 16,
                  fontSize: 13, color: '#92400e',
                  display: 'flex', gap: 8, alignItems: 'center',
                }}>
                  <Loader2 size={14} style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }} />
                  Waiting for the agent to connect… Run OnsummTallyAgent.exe on the customer's PC and paste the token.
                </div>
              )}

              {/* Last sync line */}
              {connection?.last_sync_at && (
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 16 }}>
                  Last sync: <strong>{fmt(connection.last_sync_at)}</strong>
                </div>
              )}

              {/* Actions row */}
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>

                {/* Generate / Re-generate token button */}
                <button
                  onClick={handleGenerate}
                  disabled={genLoading}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 7,
                    padding: '9px 18px', borderRadius: 7,
                    background: '#004080', color: '#fff',
                    border: 'none', cursor: genLoading ? 'not-allowed' : 'pointer',
                    fontSize: 13, fontWeight: 600,
                    opacity: genLoading ? 0.7 : 1,
                  }}
                >
                  {genLoading
                    ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                    : <Zap size={14} />
                  }
                  {status === 'NOT_CONNECTED' ? 'Connect Tally' : 'Re-generate Token'}
                </button>

                {/* Download agent .exe — served by GET /agent/download */}
                <a
                  href={`${import.meta.env.VITE_API_URL ?? ''}/agent/download`}
                  download="OnsummTallyAgent.exe"
                  onClick={() => setDlNote('tally')}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 7,
                    padding: '9px 18px', borderRadius: 7,
                    background: '#fff', color: '#004080',
                    border: '1px solid #004080', cursor: 'pointer',
                    fontSize: 13, fontWeight: 600, textDecoration: 'none',
                  }}
                >
                  <Download size={14} />
                  Download Agent (.exe)
                </a>

                {/* Refresh status */}
                <button
                  onClick={load}
                  title="Refresh status"
                  style={{
                    width: 36, height: 36, borderRadius: 7,
                    border: '1px solid #e2e8f0', background: '#fff',
                    color: '#64748b', cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  <RefreshCw size={14} />
                </button>
              </div>

              {/* Token box (shown after generation) */}
              {token && (
                <TokenBox
                  token={token.token}
                  expiresAt={token.expires_at}
                  onClose={() => setToken(null)}
                />
              )}

              {/* Download note (no external redirect) */}
              {dlNote === 'tally' && (
                <div style={{
                  marginTop: 14, fontSize: 13, color: '#1e3a5f',
                  background: '#eff6ff', border: '1px solid #bfdbfe',
                  borderRadius: 8, padding: '10px 14px',
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                }}>
                  <Download size={15} style={{ flexShrink: 0, marginTop: 1 }} />
                  <span>Copy <strong>OnsummTallyAgent.exe</strong> to the Windows PC running Tally, double-click it, and paste the token above.
                    If Windows SmartScreen warns, click <em>More info → Run anyway</em>. Download not starting? Email{' '}
                    <a href="mailto:support@onsumm.ai?subject=Tally%20Agent%20installer" style={{ color: '#2563eb', fontWeight: 600 }}>support@onsumm.ai</a>.</span>
                </div>
              )}

              {/* API error */}
              {error && (
                <div style={{
                  marginTop: 14, fontSize: 13, color: '#dc2626',
                  background: '#fef2f2', borderRadius: 8, padding: '10px 14px',
                  border: '1px solid #fecaca',
                  display: 'flex', gap: 8, alignItems: 'center',
                }}>
                  <AlertCircle size={15} style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{error}</span>
                  <button onClick={handleGenerate} disabled={genLoading} style={{
                    fontSize: 12, fontWeight: 600, color: '#dc2626',
                    background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline',
                    whiteSpace: 'nowrap',
                  }}>Try again</button>
                </div>
              )}
            </>
          )}
        </div>

        {/* How it works footer */}
        <details style={{
          borderTop: '1px solid #f1f5f9',
          padding: '14px 24px',
        }}>
          <summary style={{
            cursor: 'pointer', fontSize: 13, fontWeight: 600,
            color: '#374151', userSelect: 'none',
            display: 'flex', alignItems: 'center', gap: 6,
            listStyle: 'none',
          }}>
            <ChevronRight size={14} />
            How does this work?
          </summary>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              { title: 'Zero credentials',  desc: 'The agent runs on the customer\'s Windows PC and talks to Tally locally — you never need their server password.' },
              { title: 'Outbound only',     desc: 'The agent pushes data to Onsumm via HTTPS. No inbound ports are opened, so firewalls are never an issue.' },
              { title: 'Auto-sync',         desc: 'Once connected, the agent syncs every 15 minutes automatically. Invoices, bills and payments appear here instantly.' },
              { title: 'Self-updating',     desc: 'When you release a new agent version, customers\' agents update silently on next startup — no reinstall needed.' },
            ].map(({ title, desc }) => (
              <div key={title} style={{ display: 'flex', gap: 10 }}>
                <CheckCircle2 size={15} color="#16a34a" style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#0b1c30' }}>{title}: </span>
                  <span style={{ fontSize: 13, color: '#374151' }}>{desc}</span>
                </div>
              </div>
            ))}
          </div>
        </details>
      </div>

      {/* SAP card — only for tenants with the DB flag enabled */}
      {sapEnabled && (
      <div style={{
        background: '#fff', borderRadius: 12,
        border: '1px solid #e2e8f0', maxWidth: 680, marginTop: 20,
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      }}>

        {/* Card header */}
        <div style={{
          padding: '18px 24px', borderBottom: '1px solid #f1f5f9',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            {/* SAP logo placeholder */}
            <div style={{
              width: 44, height: 44, borderRadius: 10,
              background: '#0a6ed1', color: '#fff',
              fontSize: 14, fontWeight: 800,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>SAP</div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#0b1c30' }}>SAP Integration</div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                Sync material master, item codes, HSN &amp; GST data
              </div>
            </div>
          </div>
          <StatusBadge status={sapStatus} />
        </div>

        {/* Card body */}
        <div style={{ padding: '20px 24px' }}>
          {/* Waiting for agent */}
          {sapStatus === 'PENDING' && (
            <div style={{
              background: '#fffbeb', border: '1px solid #fde68a',
              borderRadius: 8, padding: '12px 14px', marginBottom: 16,
              fontSize: 13, color: '#92400e',
              display: 'flex', gap: 8, alignItems: 'center',
            }}>
              <Loader2 size={14} style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }} />
              Waiting for the agent to connect… Run OnsummSapAgent.exe on the SAP server and paste the token.
            </div>
          )}

          {/* Error from agent */}
          {sapStatus === 'ERROR' && sapConn?.error_message && (
            <div style={{
              background: '#fef2f2', border: '1px solid #fecaca',
              borderRadius: 8, padding: '10px 14px', marginBottom: 16,
              fontSize: 13, color: '#dc2626', display: 'flex', gap: 8, alignItems: 'flex-start',
            }}>
              <AlertCircle size={15} style={{ flexShrink: 0, marginTop: 1 }} />
              {sapConn.error_message}
            </div>
          )}

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            {/* Connect / Re-generate SAP */}
            <button
              onClick={handleSapGenerate}
              disabled={sapGenLoading}
              style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '9px 18px', borderRadius: 7,
                background: '#0a6ed1', color: '#fff',
                border: 'none', cursor: sapGenLoading ? 'not-allowed' : 'pointer',
                fontSize: 13, fontWeight: 600, opacity: sapGenLoading ? 0.7 : 1,
              }}
            >
              {sapGenLoading
                ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                : <Zap size={14} />}
              {sapStatus === 'NOT_CONNECTED' ? 'Connect SAP' : 'Re-generate Token'}
            </button>

            {/* Download SAP agent */}
            <button
              onClick={() => setDlNote(dlNote === 'sap' ? null : 'sap')}
              style={{
                display: 'flex', alignItems: 'center', gap: 7,
                padding: '9px 18px', borderRadius: 7,
                background: '#fff', color: '#0a6ed1',
                border: '1px solid #0a6ed1', cursor: 'pointer',
                fontSize: 13, fontWeight: 600,
              }}
            >
              <Download size={14} />
              Download Agent (.exe)
            </button>

            {/* Refresh status */}
            <button
              onClick={load}
              title="Refresh status"
              style={{
                width: 36, height: 36, borderRadius: 7,
                border: '1px solid #e2e8f0', background: '#fff',
                color: '#64748b', cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <RefreshCw size={14} />
            </button>
          </div>

          {/* Token box (shown after generation) */}
          {sapToken && (
            <TokenBox
              token={sapToken.token}
              expiresAt={sapToken.expires_at}
              onClose={() => setSapToken(null)}
              agent="SAP"
              host="the SAP server"
            />
          )}

          {/* Download note (no external redirect) */}
          {dlNote === 'sap' && (
            <div style={{
              marginTop: 14, fontSize: 13, color: '#1e3a5f',
              background: '#eff6ff', border: '1px solid #bfdbfe',
              borderRadius: 8, padding: '10px 14px',
              display: 'flex', gap: 8, alignItems: 'flex-start',
            }}>
              <Download size={15} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>The Onsumm SAP Agent installer is provided by your account manager — email{' '}
                <a href="mailto:support@onsumm.ai?subject=SAP%20Agent%20installer" style={{ color: '#2563eb', fontWeight: 600 }}>support@onsumm.ai</a>{' '}
                to receive <strong>OnsummSapAgent.exe</strong>.</span>
            </div>
          )}

          {/* SAP API error */}
          {sapError && (
            <div style={{
              marginTop: 14, fontSize: 13, color: '#dc2626',
              background: '#fef2f2', borderRadius: 8, padding: '10px 14px',
              border: '1px solid #fecaca', display: 'flex', gap: 8, alignItems: 'center',
            }}>
              <AlertCircle size={15} style={{ flexShrink: 0 }} />
              <span style={{ flex: 1 }}>{sapError}</span>
              <button onClick={handleSapGenerate} disabled={sapGenLoading} style={{
                fontSize: 12, fontWeight: 600, color: '#dc2626',
                background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline',
                whiteSpace: 'nowrap',
              }}>Try again</button>
            </div>
          )}
        </div>

        {/* How it works footer */}
        <details style={{
          borderTop: '1px solid #f1f5f9',
          padding: '14px 24px',
        }}>
          <summary style={{
            cursor: 'pointer', fontSize: 13, fontWeight: 600,
            color: '#374151', userSelect: 'none',
            display: 'flex', alignItems: 'center', gap: 6,
            listStyle: 'none',
          }}>
            <ChevronRight size={14} />
            How does this work?
          </summary>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              { title: 'Material master sync', desc: 'The agent reads your SAP material master (item code, description, HSN) and keeps the matching catalog up to date.' },
              { title: 'Read-only',            desc: 'The agent only reads master data — it never writes back to SAP, so your ERP stays untouched.' },
              { title: 'Better matching',      desc: 'A complete, current catalog means line items map to the right SAP item code with higher confidence.' },
            ].map(({ title, desc }) => (
              <div key={title} style={{ display: 'flex', gap: 10 }}>
                <CheckCircle2 size={15} color="#16a34a" style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#0b1c30' }}>{title}: </span>
                  <span style={{ fontSize: 13, color: '#374151' }}>{desc}</span>
                </div>
              </div>
            ))}
          </div>
        </details>
      </div>
      )}

      {/* Nova card — tenant-supplied API key, no agent/token pairing flow */}
      {novaEnabled && (
      <div style={{
        background: '#fff', borderRadius: 12,
        border: '1px solid #e2e8f0', maxWidth: 680, marginTop: 20,
        boxShadow: '0 1px 4px rgba(0,0,0,0.04)',
      }}>

        {/* Card header */}
        <div style={{
          padding: '18px 24px', borderBottom: '1px solid #f1f5f9',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <div style={{
              width: 44, height: 44, borderRadius: 10,
              background: '#4338ca', color: '#fff',
              fontSize: 16, fontWeight: 800,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>N</div>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: '#0b1c30' }}>Nova</div>
              <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                Vendor billing reconciliation &amp; rate certification
              </div>
            </div>
          </div>
          <StatusBadge status={
            novaCreds?.configured ? (novaCreds.is_active ? 'ACTIVE' : 'REVOKED') : 'NOT_CONNECTED'
          } />
        </div>

        {/* Card body */}
        <div style={{ padding: '20px 24px' }}>
          {novaLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#64748b', fontSize: 13 }}>
              <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
              Loading connection status...
            </div>
          ) : (
            <>
              {/* Configured, not editing — summary view */}
              {novaCreds?.configured && !novaEditing && (
                <>
                  <div style={{
                    display: 'grid', gridTemplateColumns: novaCreds.base_url ? '1fr 1fr' : '1fr',
                    gap: 12, marginBottom: 20,
                  }}>
                    <div style={{ background: '#f8fafc', borderRadius: 8, padding: '12px 14px', border: '1px solid #e2e8f0' }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 500, marginBottom: 4 }}>API Key</div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: '#0b1c30', fontFamily: 'monospace' }}>{novaCreds.masked_key}</div>
                    </div>
                    {novaCreds.base_url && (
                      <div style={{ background: '#f8fafc', borderRadius: 8, padding: '12px 14px', border: '1px solid #e2e8f0' }}>
                        <div style={{ fontSize: 11, color: '#64748b', fontWeight: 500, marginBottom: 4 }}>Base URL override</div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: '#0b1c30', wordBreak: 'break-all' }}>{novaCreds.base_url}</div>
                      </div>
                    )}
                  </div>
                  {novaCreds.updated_at && (
                    <div style={{ fontSize: 12, color: '#64748b', marginBottom: 16 }}>
                      Last updated: <strong>{fmt(novaCreds.updated_at)}</strong>
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                    <button
                      onClick={() => setNovaEditing(true)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 7,
                        padding: '9px 18px', borderRadius: 7,
                        background: '#4338ca', color: '#fff',
                        border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 600,
                      }}
                    >
                      <KeyRound size={14} />
                      Update Key
                    </button>
                    <button
                      onClick={handleNovaRemove}
                      disabled={novaSaving}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 7,
                        padding: '9px 18px', borderRadius: 7,
                        background: '#fff', color: '#dc2626',
                        border: '1px solid #fecaca', cursor: novaSaving ? 'not-allowed' : 'pointer',
                        fontSize: 13, fontWeight: 600, opacity: novaSaving ? 0.7 : 1,
                      }}
                    >
                      <Trash2 size={14} />
                      Remove
                    </button>
                  </div>
                </>
              )}

              {/* Not configured, or editing — key entry form */}
              {(!novaCreds?.configured || novaEditing) && (
                <>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginBottom: 16 }}>
                    <label style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                      Nova API Key
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6 }}>
                        <input
                          type={novaShowKey ? 'text' : 'password'}
                          value={novaKeyInput}
                          onChange={e => setNovaKeyInput(e.target.value)}
                          placeholder="Paste the API key from your Nova account"
                          style={{
                            flex: 1, padding: '9px 12px', borderRadius: 7,
                            border: '1px solid #dfe3ea', fontSize: 13, fontFamily: 'monospace',
                          }}
                        />
                        <button
                          type="button"
                          onClick={() => setNovaShowKey(s => !s)}
                          title={novaShowKey ? 'Hide' : 'Show'}
                          style={{
                            width: 36, height: 36, borderRadius: 7, flexShrink: 0,
                            border: '1px solid #e2e8f0', background: '#fff', color: '#64748b',
                            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
                          }}
                        >
                          {novaShowKey ? <EyeOff size={14} /> : <Eye size={14} />}
                        </button>
                      </div>
                    </label>
                    <label style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                      Base URL (optional — leave blank to use the default Nova endpoint)
                      <input
                        type="text"
                        value={novaBaseUrlInput}
                        onChange={e => setNovaBaseUrlInput(e.target.value)}
                        placeholder="https://api.nova.example.com"
                        style={{
                          display: 'block', width: '100%', marginTop: 6,
                          padding: '9px 12px', borderRadius: 7,
                          border: '1px solid #dfe3ea', fontSize: 13,
                        }}
                      />
                    </label>
                  </div>

                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                    <button
                      onClick={handleNovaSave}
                      disabled={novaSaving}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 7,
                        padding: '9px 18px', borderRadius: 7,
                        background: '#4338ca', color: '#fff',
                        border: 'none', cursor: novaSaving ? 'not-allowed' : 'pointer',
                        fontSize: 13, fontWeight: 600, opacity: novaSaving ? 0.7 : 1,
                      }}
                    >
                      {novaSaving
                        ? <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                        : <Save size={14} />}
                      Save Key
                    </button>
                    {novaCreds?.configured && (
                      <button
                        onClick={() => { setNovaEditing(false); setNovaKeyInput(''); setNovaBaseUrlInput(novaCreds.base_url ?? '') }}
                        style={{
                          padding: '9px 18px', borderRadius: 7,
                          background: '#fff', color: '#64748b',
                          border: '1px solid #e2e8f0', cursor: 'pointer', fontSize: 13, fontWeight: 600,
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </>
              )}

              {/* Nova API error */}
              {novaError && (
                <div style={{
                  marginTop: 14, fontSize: 13, color: '#dc2626',
                  background: '#fef2f2', borderRadius: 8, padding: '10px 14px',
                  border: '1px solid #fecaca', display: 'flex', gap: 8, alignItems: 'center',
                }}>
                  <AlertCircle size={15} style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{novaError}</span>
                </div>
              )}
            </>
          )}
        </div>

        {/* How it works footer */}
        <details style={{ borderTop: '1px solid #f1f5f9', padding: '14px 24px' }}>
          <summary style={{
            cursor: 'pointer', fontSize: 13, fontWeight: 600,
            color: '#374151', userSelect: 'none',
            display: 'flex', alignItems: 'center', gap: 6,
            listStyle: 'none',
          }}>
            <ChevronRight size={14} />
            How does this work?
          </summary>
          <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {[
              { title: 'Your key, your account',  desc: 'This is the API key issued directly by Nova to your account — Onsumm never issues or shares a key on your behalf.' },
              { title: 'Encrypted at rest',        desc: "Once saved, the full key is never shown again — only a masked preview. Onsumm calls Nova's billing API with it on your behalf to pull usage and reconcile bills." },
              { title: 'Used for reconciliation',  desc: "Powers the Ownership & Rate Certification view under Nova (see the Nova page in the sidebar) — checking each vendor bill line item against your certified rate card." },
            ].map(({ title, desc }) => (
              <div key={title} style={{ display: 'flex', gap: 10 }}>
                <CheckCircle2 size={15} color="#16a34a" style={{ flexShrink: 0, marginTop: 2 }} />
                <div>
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#0b1c30' }}>{title}: </span>
                  <span style={{ fontSize: 13, color: '#374151' }}>{desc}</span>
                </div>
              </div>
            ))}
          </div>
        </details>
      </div>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }
      `}</style>
    </div>
  )
}
