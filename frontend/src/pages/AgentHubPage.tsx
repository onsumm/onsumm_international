import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Cpu, RefreshCw, FileText, Mail, XCircle, AlertTriangle, Clock, Network,
  GitCompare, ShieldCheck, Radar, Brain, Banknote, ScrollText, Percent, Landmark, HandCoins,
  ClipboardCheck,
} from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import FactGraphModal from '../components/factgraph/FactGraphModal'
import { Switch, Button, useToast } from '../ui'
import { reviewPath } from '../routes/paths'
import {
  fetchAgentStatus, toggleAgent, triggerAgentScan, fetchAgentEvents, fetchAgentPocs, setContactAgent,
  fetchAgentDrafts, fetchAgentDraft, fetchAgentDraftFile,
  type AgentStatus, type AgentEvent, type AgentPoc, type AgentDraft,
} from '../api/agent'
import './agenthub.css'
import { displayCurrency, localeForCurrency } from '../ui/format'

interface Props {
  onReviewDraft?: (item: { file: File; data: Record<string, unknown> }, draftId: string, origin?: string) => void
}

const ACTION_META: Record<string, { label: string; color: string; icon: React.ElementType }> = {
  draft_created:   { label: 'Draft created',      color: 'var(--cat-4)', icon: FileText },
  skipped_not_bill:{ label: 'Skipped — not a bill', color: 'var(--ink-500)', icon: XCircle },
  skipped_no_pdf:  { label: 'Skipped — no PDF',    color: 'var(--ink-500)', icon: XCircle },
  skipped_no_poc:  { label: 'Skipped — not a watched POC', color: 'var(--ink-500)', icon: XCircle },
  skipped_duplicate:{ label: 'Skipped — duplicate', color: 'var(--warn-600)', icon: XCircle },
  processing:      { label: 'Processing…',         color: 'var(--info-600)', icon: Clock },
  ocr_failed:      { label: 'OCR failed',          color: 'var(--danger-600)', icon: AlertTriangle },
}

/**
 * The rest of the fleet, from docs/PRD_AGENT_HUB.md §13. They are shown before
 * they ship on purpose: the hub is a roster, and an empty roster with one live
 * agent reads as a single-feature page rather than a fleet console.
 */
const ROADMAP: { name: string; desc: string; phase: string; icon: React.ElementType }[] = [
  { name: 'Verification', desc: 'GSTIN, PAN, bank and MSME checks → trust score.', phase: 'P0', icon: ShieldCheck },
  { name: 'Recon',        desc: 'Invoice lines vs ERP usage → discrepancy set.',   phase: 'P0', icon: GitCompare },
  { name: 'Analytics',    desc: 'Weekly spend, exceptions and run rollup.',        phase: 'P0', icon: Radar },
  { name: 'CFO Orchestrator', desc: 'Plans a goal, delegates it across the fleet.', phase: 'PHASE 2', icon: Brain },
  { name: 'Payable',      desc: 'Gated disbursement, OTP, chain follow-ups.',      phase: 'NEXT', icon: Banknote },
  { name: 'Contract',     desc: 'Parses contracts, extracts and links terms.',     phase: 'P1', icon: ScrollText },
  { name: 'TDS',          desc: 'Computes liability, drafts the return.',          phase: 'P2', icon: Percent },
  { name: 'GST',          desc: 'Assembles returns, checks vendor filings.',       phase: 'P2', icon: Landmark },
  { name: 'Receivable',   desc: 'Tracks AR, chases overdue invoices.',             phase: 'P2', icon: HandCoins },
]

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const s = Math.floor((Date.now() - then) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return new Date(iso).toLocaleDateString()
}

export default function AgentHubPage({ onReviewDraft }: Props) {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const companyId = user?.company_id

  const [status, setStatus]   = useState<AgentStatus | null>(null)
  const [events, setEvents]   = useState<AgentEvent[]>([])
  const [pocs, setPocs]       = useState<AgentPoc[]>([])
  const [drafts, setDrafts]   = useState<AgentDraft[]>([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [opening, setOpening] = useState<string | null>(null)
  const [savingToggle, setSavingToggle] = useState(false)
  const [showGraph, setShowGraph] = useState(false)

  const refresh = useCallback(async () => {
    if (!companyId) return
    try {
      const [s, e, p, d] = await Promise.all([
        fetchAgentStatus(companyId),
        fetchAgentEvents(companyId, 50),
        fetchAgentPocs(companyId),
        fetchAgentDrafts(companyId),
      ])
      setStatus(s); setEvents(e); setPocs(p); setDrafts(d)
    } catch { /* silent */ } finally { setLoading(false) }
  }, [companyId])

  // Manual "Scan now" also runs a live mailbox scan, then reloads the results.
  const manualRefresh = useCallback(async () => {
    if (!companyId || scanning) return
    setScanning(true)
    try { await triggerAgentScan(companyId) }
    catch { /* still refresh below to reflect whatever changed */ }
    finally { await refresh(); setScanning(false) }
  }, [companyId, scanning, refresh])

  useEffect(() => { refresh() }, [refresh])
  // Light auto-refresh so the log stays current while the page is open.
  useEffect(() => {
    // Pause polling while the tab is hidden — no point scanning on behalf of a
    // window nobody is looking at.
    const t = setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, 30000)
    return () => clearInterval(t)
  }, [refresh])

  const handleToggleAgent = async () => {
    if (!companyId || !status) return
    setSavingToggle(true)
    const next = !status.enabled
    setStatus({ ...status, enabled: next })
    try {
      await toggleAgent(companyId, next)
      toast.success(next ? 'Vendor Bill Agent started' : 'Vendor Bill Agent paused')
    } catch {
      // The toggle used to flip back with no explanation, leaving the user
      // believing a mailbox-reading agent was off when it was still running.
      setStatus({ ...status, enabled: !next })
      toast.error(
        next ? 'Could not start the agent' : 'Could not pause the agent',
        'Nothing changed — the agent is still in its previous state. Try again.',
      )
    } finally { setSavingToggle(false) }
  }

  const handleTogglePoc = async (poc: AgentPoc) => {
    if (!companyId) return
    const next = !poc.agent_enabled
    setPocs(prev => prev.map(p => p.id === poc.id ? { ...p, agent_enabled: next } : p))
    try { await setContactAgent(poc.supplier_id, poc.id, companyId, next) }
    catch {
      setPocs(prev => prev.map(p => p.id === poc.id ? { ...p, agent_enabled: !next } : p))
      toast.error(`Could not ${next ? 'start' : 'stop'} watching ${poc.name}`, 'Nothing changed. Try again.')
    }
  }

  const openDraft = async (draftId: string, sourceFile: string) => {
    if (!companyId || !onReviewDraft) return
    setOpening(draftId)
    try {
      const [detail, file] = await Promise.all([
        fetchAgentDraft(draftId, companyId),
        fetchAgentDraftFile(draftId, companyId, sourceFile),
      ])
      onReviewDraft({ file, data: detail.payload }, draftId, 'agent-hub')
    } catch { /* silent */ } finally { setOpening(null) }
  }

  const watched = pocs.filter(p => p.agent_enabled)
  const live = !!status?.enabled
  // Compliance has no on/off switch of its own — it's the existing Review Hub
  // adapter mesh (pipelines/review/adapters/{vendor_compliance,
  // master_data_conflict,contract_bill_variance}.py), which always sweeps
  // every tenant on every pass. "Deployed" here means "shipped and running,"
  // not "toggleable from this page" — there's nothing to toggle.
  const complianceOnline = true
  const fleetTotal = ROADMAP.length + 1 /* Communication */ + 1 /* Compliance */

  const telemetry = [
    { key: 'Watched contacts', val: status?.watched_pocs ?? 0 },
    { key: 'Pending drafts', val: status?.pending_drafts ?? 0 },
    { key: 'Events logged', val: status?.events_total ?? 0 },
    { key: 'Scan interval', val: status ? `${Math.round(status.poll_interval / 60)}m` : '—' },
    { key: 'Last scan', val: status?.last_scan_at ? timeAgo(status.last_scan_at) : 'never' },
  ]

  return (
    <div className="ah">
      {/* ── Command deck ─────────────────────────────────────────────────── */}
      <div className="ah-deck">
        <div className="ah-deck__row">
          <div className="ah-deck__id">
            <div className="ah-deck__mark"><Cpu size={22} /></div>
            <div style={{ minWidth: 0 }}>
              <h1 className="ah-deck__title">Agent Hub</h1>
              <div className="ah-deck__meta">
                <span className="ah-dot ah-dot--deck ah-dot--on" aria-hidden="true" />
                <span>{(live ? 1 : 0) + (complianceOnline ? 1 : 0)} of {fleetTotal} online</span>
                <span className="ah-deck__sep">·</span>
                <span>{status?.events_total ?? 0} events</span>
                <span className="ah-deck__sep">·</span>
                <span>last scan {status?.last_scan_at ? timeAgo(status.last_scan_at) : 'never'}</span>
              </div>
            </div>
          </div>
          <div className="ah-deck__actions">
            <button
              className="ah-deck__btn"
              onClick={() => setShowGraph(true)}
              title="Shared memory: every vendor, bill and contact, plus the facts agents extract"
            >
              <Network size={14} aria-hidden="true" /> Fact Graph
            </button>
            <button
              className="ah-deck__btn ah-deck__btn--accent"
              onClick={manualRefresh}
              disabled={scanning}
              title="Scan the watched mailboxes now and reload"
            >
              <RefreshCw size={14} className={scanning || loading ? 'ah-spin' : ''} aria-hidden="true" />
              {scanning ? 'Scanning…' : 'Scan now'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Fleet roster ─────────────────────────────────────────────────── */}
      <div className="ah-label">
        <span>Fleet</span>
        <span className="ah-label__rule" />
        <span>{(live ? 1 : 0) + (complianceOnline ? 1 : 0)} online · {fleetTotal - (live ? 1 : 0) - (complianceOnline ? 1 : 0)} queued</span>
      </div>

      <div className="ah-fleet">
        <div className="ah-agent ah-agent--sel">
          <div className="ah-agent__top">
            <div className="ah-agent__icon"><Mail size={16} /></div>
            <span className="ah-agent__name">Communication Agent</span>
            <Switch
              checked={live}
              onChange={handleToggleAgent}
              disabled={savingToggle}
              label="Vendor Bill Agent"
            />
          </div>
          <div className="ah-agent__desc">
            Watched mailbox → bill or contract → OCR/extraction → draft bill or typed claim.
          </div>
          <div className="ah-agent__foot">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className={`ah-dot ${live ? 'ah-dot--on' : ''}`} aria-hidden="true" />
              {live ? 'Active' : 'Paused'} · Vendor Bill watcher
            </span>
            <span>{status?.watched_pocs ?? 0} watched · {status?.pending_drafts ?? 0} drafts</span>
          </div>
        </div>

        <div className="ah-agent">
          <div className="ah-agent__top">
            <div className="ah-agent__icon"><ClipboardCheck size={16} /></div>
            <span className="ah-agent__name">Compliance Agent</span>
            <span className="ah-agent__phase">ACTIVE</span>
          </div>
          <div className="ah-agent__desc">
            Vendor completeness (GSTIN/PAN/bank), comm-extracted master-data conflicts, and
            contract-vs-bill rate/quantity variance — surfaced as Review Hub findings.
          </div>
          <div className="ah-agent__foot">
            <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span className="ah-dot ah-dot--on" aria-hidden="true" /> Active
            </span>
            <Button size="sm" variant="secondary" onClick={() => navigate(reviewPath())}>
              View findings
            </Button>
          </div>
        </div>

        {ROADMAP.map(a => {
          const Icon = a.icon
          return (
            <div key={a.name} className="ah-agent ah-agent--soon">
              <div className="ah-agent__top">
                <div className="ah-agent__icon"><Icon size={16} /></div>
                <span className="ah-agent__name">{a.name}</span>
                <span className="ah-agent__phase">{a.phase}</span>
              </div>
              <div className="ah-agent__desc">{a.desc}</div>
              <div className="ah-agent__foot">
                <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span className="ah-dot" aria-hidden="true" /> Not deployed
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Selected agent telemetry ─────────────────────────────────────── */}
      <div className="ah-label">
        <span>Communication Agent · Vendor Bill telemetry</span>
        <span className="ah-label__rule" />
      </div>

      <div className="ah-tele">
        {telemetry.map(t => (
          <div key={t.key} className="ah-tele__cell">
            <div className="ah-tele__val">{t.val}</div>
            <div className="ah-tele__key">{t.key}</div>
          </div>
        ))}
      </div>

      <div className="ah-panels">
        {/* Activity stream */}
        <div className="ah-panel">
          <div className="ah-panel__head">
            <div className="ah-panel__title">Activity</div>
            <span className="ah-panel__count">{events.length} events</span>
          </div>
          <div className="ah-panel__body ah-panel__body--tall">
            {events.length === 0 && <div className="ah-empty">No activity yet.</div>}
            {events.map(ev => {
              const meta = ACTION_META[ev.action] ?? { label: ev.action, color: 'var(--ink-500)', icon: Clock }
              const Icon = meta.icon
              const draft = ev.action === 'draft_created' && ev.draft_id
                ? drafts.find(x => x.id === ev.draft_id)
                : undefined
              const body = (
                <>
                  <Icon size={15} color={meta.color} className="ah-ev__icon" aria-hidden="true" />
                  <div style={{ minWidth: 0 }}>
                    <div className="ah-ev__head">
                      <span className="ah-ev__action" style={{ color: meta.color }}>{meta.label}</span>
                      {ev.supplier_name && <span className="ah-ev__who"> · {ev.supplier_name}</span>}
                    </div>
                    <div className="ah-ev__sub" title={`${ev.subject ?? ''}${ev.detail ? ` — ${ev.detail}` : ''}`}>
                      {ev.subject || ev.from_email || '—'}{ev.detail ? ` — ${ev.detail}` : ''}
                    </div>
                  </div>
                  <div className="ah-ev__time">{timeAgo(ev.created_at)}</div>
                </>
              )
              // A row that opens a draft is a real button — it used to be a
              // <div onClick>, unreachable by keyboard.
              return draft
                ? <button key={ev.id} className="ah-ev" onClick={() => openDraft(draft.id, draft.source_file)}>{body}</button>
                : <div key={ev.id} className="ah-ev">{body}</div>
            })}
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)', minWidth: 0 }}>
          {/* Watched contacts */}
          <div className="ah-panel">
            <div className="ah-panel__head">
              <div className="ah-panel__title">Watched contacts</div>
              <span className="ah-panel__count">{watched.length}/{pocs.length}</span>
            </div>
            <div className="ah-panel__body ah-panel__body--tall">
              {pocs.length === 0 && (
                <div className="ah-empty">No vendor contacts with an email. Add points of contact on Vendors first.</div>
              )}
              {pocs.map(p => (
                <div key={p.id} className="ah-poc">
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="ah-poc__name">
                      {p.name}{p.supplier_name && <span className="ah-poc__org"> · {p.supplier_name}</span>}
                    </div>
                    <div className="ah-poc__mail">{p.email}</div>
                  </div>
                  <Switch
                    checked={p.agent_enabled}
                    onChange={() => handleTogglePoc(p)}
                    label={`Watch ${p.name}${p.email ? ` (${p.email})` : ''}`}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* Drafts waiting */}
          {drafts.length > 0 && (
            <div className="ah-panel">
              <div className="ah-panel__head">
                <div className="ah-panel__title"><FileText size={13} aria-hidden="true" /> Drafts waiting</div>
                <span className="ah-panel__count">{drafts.length}</span>
              </div>
              <div className="ah-panel__body">
                {drafts.map(d => (
                  <div key={d.id} className="ah-draft">
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="ah-draft__name">
                        {d.vendor_name ?? 'Unknown vendor'}
                        {d.invoice_number && <span className="ah-poc__org"> · {d.invoice_number}</span>}
                      </div>
                      <div className="ah-draft__sub" title={d.subject ?? ''}>{d.subject ?? d.from_email ?? '—'}</div>
                    </div>
                    {d.total_amount != null && (
                      <div className="ah-draft__amt">
                        {d.currency} {Number(d.total_amount).toLocaleString(localeForCurrency(String(d.currency || displayCurrency())), { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                    )}
                    <Button size="sm" variant="primary" onClick={() => openDraft(d.id, d.source_file)} loading={opening === d.id}>
                      Review
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {showGraph && <FactGraphModal companyId={companyId} onClose={() => setShowGraph(false)} />}
    </div>
  )
}
