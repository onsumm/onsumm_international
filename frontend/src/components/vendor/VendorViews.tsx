import { useEffect, useState, type ReactNode } from 'react'
import {
  AlertTriangle, ArrowDownLeft, ArrowUpRight, BarChart3, CalendarDays, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  Clock, FileText, Info, Mail, MessageSquare, Paperclip, Plus, Receipt, Sparkles, TrendingUp, Wallet, X,
} from 'lucide-react'
import type { Bill } from '../../api/invoices'
import type { InboxThread, VendorDocument, VendorInbox } from '../../api/vendorCard'
import SpendChart from './SpendChart'
import {
  MONTHS, MONTHS_FULL, STAGES, compactINR, dayOf, daysBetween, money, outstandingOf, shortDate, stageOf,
  billAmount, billMoney, billState, isPaid, isUnconverted, monthLabel, plural, unconvertedNote, type VendorSummary,
} from './vendorStats'

/** Where a fetch is: still on its way, failed, or in. A failed load is never shown as "nothing". */
export type LoadStatus = 'loading' | 'error' | 'ready'

/** Sources that failed to load. Their zeroes aren't real, so they're shown as "—" and their nudges held back. */
export interface SourceFailures { bills?: boolean; bank?: boolean; inbox?: boolean }

/** A skeleton while loading, or "Couldn't load … · Retry". */
export function LoadNote({ status, what, onRetry, className }: { status: 'loading' | 'error'; what: string; onRetry?: () => void; className?: string }) {
  const cls = `vc__loadnote${status === 'error' ? ' vc__loadnote--err' : ''}${className ? ` ${className}` : ''}`
  if (status === 'loading') {
    return (
      <div className={cls} role="status" aria-busy="true">
        <span className="vc__skel" style={{ width: '62%' }} /><span className="vc__skel" style={{ width: '38%' }} />
        <span className="vc__sr">Loading {what}…</span>
      </div>
    )
  }
  return (
    <div className={cls} role="alert">
      <AlertTriangle size={14} />Couldn’t load {what}
      {onRetry && <><span className="vc__sep"> · </span><button type="button" className="vc__lnk vc__lnk--brand" onClick={onRetry}>Retry</button></>}
    </div>
  )
}

/** Noon today, so day arithmetic against bill dates is exact. */
export function today(): Date {
  const d = new Date()
  d.setHours(12, 0, 0, 0)
  return d
}

export function relDay(iso: string | null | undefined, now = today()): string {
  const d = dayOf(iso)
  if (!d) return '—'
  const n = daysBetween(d, now)
  if (n <= 0) return 'Today'
  if (n === 1) return 'Yesterday'
  if (n < 7) return `${n} days ago`
  return shortDate(iso, d.getFullYear() !== now.getFullYear())
}

export function initials(name: string): string {
  // Letters and digits only: "Subhash (IIITDM)" is "SI", not "S(".
  const words = name.replace(/@.*$/, '').split(/[\s._-]+/).map(w => w.replace(/[^\p{L}\p{N}]/gu, '')).filter(Boolean)
  return words.slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?'
}

function hueOf(s: string): number {
  return Math.abs([...s].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) | 0, 7)) % 360
}

export function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' | 'lg' }) {
  return <span className={`vc__av vc__av--${size}`} style={{ ['--h' as string]: hueOf(name) }} title={name}>{initials(name)}</span>
}

/** Bare display name from a "Name <addr>" header. */
export function senderName(from: string | null | undefined): string {
  if (!from) return ''
  const name = from.split('<')[0].trim().replace(/^"|"$/g, '')
  return name || from.replace(/[<>]/g, '')
}

// ── Sidebar section ──────────────────────────────────────────────────────────

/**
 * A sidebar block that folds away from its title. Folded or not is remembered
 * per section on this device. The body stays mounted (just hidden), so the
 * field anchors Review and the guides point at are always there.
 */
export function SideSection({ id, title, count, actions, forceOpen = false, children }: {
  id: string
  title: ReactNode
  count?: number
  /** Buttons on the right of the title; they stay usable while folded. */
  actions?: ReactNode
  /** Held open, e.g. while the section is being edited. */
  forceOpen?: boolean
  children: ReactNode
}) {
  const key = `vc.side.${id}`
  // Remembered choice first; otherwise open on a wide screen and folded on a
  // narrow one, where the sidebar sits above the tabs.
  const [open, setOpen] = useState(() => {
    let saved: string | null = null
    try { saved = localStorage.getItem(key) } catch { /* private mode */ }
    if (saved) return saved !== '0'
    return !(typeof window !== 'undefined' && window.matchMedia?.('(width <= 960px)').matches)
  })
  const shown = open || forceOpen
  const toggle = () => {
    // Held open (being edited): folding now would only be saved and snap it shut after Save.
    if (forceOpen) return
    const next = !shown
    setOpen(next)
    try { localStorage.setItem(key, next ? '1' : '0') } catch { /* private mode: just don't remember */ }
  }
  return (
    <section className={`vc__sside${shown ? '' : ' is-folded'}`}>
      <div className="vc__sside-h">
        <button className="vc__sside-tg" aria-expanded={shown} aria-controls={`vc-side-${id}`} aria-disabled={forceOpen || undefined} onClick={toggle}>
          <ChevronDown size={14} className="vc__sside-chev" />{title}{count != null && <span className="vc__cnt">{count}</span>}
        </button>
        <span className="vc__grow" />
        {actions}
      </div>
      <div id={`vc-side-${id}`} className="vc__sside-b" aria-hidden={!shown}>
        <div className="vc__sside-in">{children}</div>
      </div>
    </section>
  )
}

// ── Bill row ─────────────────────────────────────────────────────────────────

export function Stages({ bill, label = true }: { bill: Bill; label?: boolean }) {
  const st = stageOf(bill)
  const pending = bill.status_label && st === 1 ? bill.status_label : STAGES[st]
  return (
    <div className="vc__stagewrap">
      <div className="vc__stages" title={`${STAGES[st]} · step ${st + 1} of ${STAGES.length}`}>
        {STAGES.map((_, i) => <i key={i} className={i < st ? 'done' : i === st ? 'cur' : ''} />)}
      </div>
      {label && <div className="vc__stages-l">{pending}</div>}
    </div>
  )
}

export function BillStatus({ bill, now }: { bill: Bill; now: Date }) {
  const s = billState(bill, now)
  if (s.tone === 'ok') return <span className="vc__chip vc__chip--ok"><Check size={12} />{s.label}</span>
  if (s.tone !== 'plain') {
    // Past 90 days the chip turns solid, so the oldest debts stand out from the merely late.
    const severe = s.late > 90 ? ' vc__chip--severe' : ''
    return <span className={`vc__chip vc__chip--${s.tone}${severe}`} title={s.blocked ? 'Past its due date and not yet approved' : undefined}><i className="vc__d" />{s.label}</span>
  }
  const due = dayOf(bill.due_date)
  if (!due) return <span className="vc__chip vc__chip--plain">{s.label}</span>
  const d = daysBetween(now, due)
  return (
    <span className="vc__chip vc__chip--plain"><CalendarDays size={13} />{s.label}
      {d <= 3 && <span className="vc__muted"> · {d === 0 ? 'today' : `in ${d}d`}</span>}
    </span>
  )
}

export function BillRow({ bill, now, onOpen }: { bill: Bill; now: Date; onOpen?: (invoiceId: string) => void }) {
  return (
    <div className="vc__brow" role="button" tabIndex={0}
         onClick={() => onOpen?.(bill.invoice_id)}
         onKeyDown={e => { if (e.key === 'Enter') onOpen?.(bill.invoice_id) }}>
      <div className="vc__min0">
        <div className="vc__brow-t">{bill.id || 'No bill number'}</div>
        <div className="vc__brow-s">{[bill.category, shortDate(bill.invoice_date)].filter(Boolean).join(' · ')}</div>
      </div>
      <Stages bill={bill} />
      <div><BillStatus bill={bill} now={now} /></div>
      <div className="vc__brow-amt">{billMoney(bill, outstandingOf(bill) || billAmount(bill))}</div>
      <ChevronRight size={14} className="vc__brow-chev" />
    </div>
  )
}

// ── KPI strip ────────────────────────────────────────────────────────────────

function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return null
  const w = 132, h = 30, p = 3
  const mx = Math.max(...values), mn = Math.min(...values) * 0.9
  const X = (i: number) => p + (i * (w - 2 * p)) / (values.length - 1)
  const Y = (v: number) => h - p - ((v - mn) / (mx - mn || 1)) * (h - 2 * p)
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join('')
  const last = values.length - 1
  return (
    <svg className="vc__spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path className="vc__spark-area" d={`${line}L${X(last)},${h}L${X(0)},${h}Z`} />
      <path className="vc__spark-ln" d={line} />
      <circle className="vc__spark-dt" cx={X(last)} cy={Y(values[last])} r={3} />
    </svg>
  )
}

const BUCKET_TONE = { overdue: 'vc__t-bad', week: 'vc__t-warn', later: 'vc__t-brand', approval: 'vc__t-mute' }

export function KpiStrip({ s, now, inbox, onGo, failed }: {
  s: VendorSummary
  now: Date
  inbox: VendorInbox | null
  onGo: (view: 'spend' | 'documents' | 'conversations', filter?: string) => void
  failed?: SourceFailures
}) {
  const tot = s.outstanding || 1
  const nd = s.nextDue
  const ndIn = nd ? daysBetween(now, dayOf(nd.due_date)!) : null
  const heard = inbox?.threads.filter(t => !t.last_outbound).sort((a, b) => (b.last_at ?? '').localeCompare(a.last_at ?? ''))[0]
  const held = s.lateInApproval
  const down = <><div className="vc__kpi-v">—</div><div className="vc__kpi-s vc__kpi-err"><AlertTriangle size={12} />Couldn’t load bills</div></>
  return (
    <div className="vc__kpis">
      <button className="vc__kpi" onClick={() => onGo('documents', 'outstanding')}>
        <ArrowUpRight size={14} className="vc__kpi-arrow" />
        <div className="vc__kpi-l"><Wallet size={14} />Outstanding</div>
        {failed?.bills ? down : (
          <>
            <div className="vc__kpi-v">{money(s.outstanding)}</div>
            <div className="vc__stack">
              {s.buckets.filter(b => b.amount > 0).map(b => <i key={b.key} className={BUCKET_TONE[b.key]} style={{ width: `${(b.amount / tot) * 100}%` }} title={`${b.label}: ${money(b.amount)}`} />)}
            </div>
          </>
        )}
      </button>
      <button className="vc__kpi" onClick={() => onGo('spend')}>
        <ArrowUpRight size={14} className="vc__kpi-arrow" />
        <div className="vc__kpi-l"><BarChart3 size={14} />Billed · last 12 months</div>
        {failed?.bills ? down : (
          <>
            <div className="vc__kpi-v">{compactINR(s.billed12)}</div>
            <Spark values={s.series.filter(m => !m.partial).slice(-12).map(m => m.total)} />
          </>
        )}
      </button>
      {/* Nothing upcoming but bills overdue is not "nothing due": say how many are late. */}
      <button className="vc__kpi" onClick={() => onGo('documents', nd ? 'outstanding' : s.overdue.length ? 'overdue' : held.length ? 'late' : 'outstanding')}>
        <ArrowUpRight size={14} className="vc__kpi-arrow" />
        <div className="vc__kpi-l"><CalendarDays size={14} />Next payment due</div>
        {failed?.bills ? down : nd ? (
          <div className="vc__kpi-v">{shortDate(nd.due_date)}<small>{ndIn === 0 ? 'today' : `in ${ndIn}d`}</small></div>
        ) : s.overdue.length ? (
          <div className="vc__kpi-v vc__bad">{s.overdue.length} overdue</div>
        ) : held.length ? (
          // Late but unapproved: not payable yet, so it isn't "overdue" and isn't red.
          <div className="vc__kpi-v">{held.length} late in approval</div>
        ) : (
          <div className="vc__kpi-v">—</div>
        )}
      </button>
      <button className="vc__kpi" onClick={() => onGo('conversations')}>
        <ArrowUpRight size={14} className="vc__kpi-arrow" />
        <div className="vc__kpi-l"><MessageSquare size={14} />Last conversation</div>
        <div className="vc__kpi-v">{heard && !failed?.inbox ? relDay(heard.last_at, now) : '—'}</div>
        {failed?.inbox && <div className="vc__kpi-s vc__kpi-err"><AlertTriangle size={12} />Couldn’t load emails</div>}
      </button>
    </div>
  )
}

// ── Copilot suggestions ──────────────────────────────────────────────────────

export interface Nudge {
  id: string
  tone: 'risk' | 'warn' | 'info'
  label: string
  source: string
  title: string
  detail: string
  actions: { label: string; primary?: boolean; run: () => void }[]
}

const readDismissed = (key: string | null): Set<string> => {
  if (!key) return new Set()
  try { return new Set(JSON.parse(localStorage.getItem(key) || '[]') as string[]) } catch { return new Set() }
}

/**
 * The card's suggestions, one at a time. With a storageKey (the vendor's id),
 * a dismissed suggestion stays dismissed on this device; nudge ids carry the
 * bill or thread they're about, so a new problem still shows.
 */
export function Copilot({ nudges, storageKey, failed }: {
  nudges: Nudge[]
  storageKey?: string
  /** Sources that didn't load: their suggestions are missing, and that is said. */
  failed?: SourceFailures
}) {
  const key = storageKey ? `vc.copilot.${storageKey}` : null
  const [idx, setIdx] = useState(0)
  const [dismissed, setDismissed] = useState<Set<string>>(() => readDismissed(key))
  useEffect(() => { setDismissed(readDismissed(key)) }, [key])
  const dismiss = (id: string) => setDismissed(d => {
    const next = new Set(d).add(id)
    if (key) try { localStorage.setItem(key, JSON.stringify([...next].slice(-40))) } catch { /* private mode: dismiss for now only */ }
    return next
  })
  const list = nudges.filter(n => !dismissed.has(n.id))
  if (!list.length) return null
  const i = Math.min(idx, list.length - 1)
  const n = list[i]
  const skipped = [failed?.bills && 'bills', failed?.bank && 'bank details', failed?.inbox && 'emails'].filter(Boolean)
  return (
    <div className={`vc__copilot${list.length > 1 ? ' vc__copilot--stacked' : ''}`} role="region" aria-label="Copilot suggestions">
      {/* Kept outside the keyed card so it stays mounted and each new suggestion is announced.
          It names the suggestion, not its text, which is right there in the card. */}
      <span className="vc__sr" aria-live="polite">{`Copilot suggestion ${i + 1} of ${list.length}: ${n.label}`}</span>
      <div className="vc__copilot-in" key={n.id}>
        <div className="vc__copilot-ic"><Sparkles size={18} /></div>
        <div className="vc__copilot-txt">
          <div className="vc__copilot-k"><b>Onsumm Copilot</b><span className={`vc__tone vc__tone--${n.tone}`}>{n.label}</span><span>{n.source}</span></div>
          <div className="vc__copilot-t">{n.title}</div>
          <div className="vc__copilot-d">{n.detail}</div>
          {skipped.length > 0 && <div className="vc__copilot-skip">Some checks skipped: {skipped.join(' and ')} couldn’t load.</div>}
        </div>
        <div className="vc__copilot-acts">
          {n.actions.map(a => (
            <button key={a.label} className={`vc__btn vc__btn--sm${a.primary ? ' vc__btn--primary' : ''}`} onClick={a.run}>{a.label}</button>
          ))}
          {list.length > 1 && (
            <>
              <button className="vc__iconbtn vc__iconbtn--sm" aria-label="Previous suggestion" onClick={() => setIdx((i - 1 + list.length) % list.length)}><ChevronLeft size={14} /></button>
              <span className="vc__dots" aria-label={`Suggestion ${i + 1} of ${list.length}`}>{list.map((x, k) => <i key={x.id} className={k === i ? 'on' : ''} />)}</span>
              <button className="vc__iconbtn vc__iconbtn--sm" aria-label="Next suggestion" onClick={() => setIdx((i + 1) % list.length)}><ChevronRight size={14} /></button>
            </>
          )}
          <button className="vc__iconbtn vc__iconbtn--sm" aria-label="Dismiss" onClick={() => dismiss(n.id)}><X size={14} /></button>
        </div>
      </div>
    </div>
  )
}

/** Suggestions from what the card already knows. Each points at the fix. */
export function buildNudges({ s, now, inbox, contactNames, bankVerified, hasPan, onOpenBill, onPay, onOpenThread, onAddContact, onGoCompliance, onEditPan, failed }: {
  s: VendorSummary
  now: Date
  inbox: VendorInbox | null
  contactNames: Record<string, string>
  bankVerified: boolean
  hasPan: boolean
  onOpenBill: (invoiceId: string) => void
  onPay?: () => void
  onOpenThread: (threadId: string) => void
  onAddContact: (email: string) => void
  onGoCompliance: (section: 'bank' | 'gstins' | 'tds') => void
  /** Opens the PAN field (Vendor details). Without it the PAN nudge says where PAN is edited instead of linking. */
  onEditPan?: () => void
  /** A source that didn't load can't vouch for "missing" or "overdue": its nudges are held back. */
  failed?: SourceFailures
}): Nudge[] {
  const out: Nudge[] = []
  // Only approved bills are overdue, and only they can be paid.
  if (!failed?.bills && s.overdue.length) {
    const oldest = s.overdue[0]
    const late = daysBetween(dayOf(oldest.due_date)!, now)
    const total = s.overdue.reduce((t, b) => t + outstandingOf(b), 0)
    out.push({
      id: `overdue-${oldest.invoice_id}`, tone: 'risk', label: 'Payment risk', source: 'From this vendor’s bills',
      title: s.overdue.length === 1 ? `${oldest.id || 'A bill'} is ${late} days overdue` : `${s.overdue.length} bills are overdue · ${money(total)}`,
      detail: s.overdue.length === 1
        ? `${money(outstandingOf(oldest))} was due on ${shortDate(oldest.due_date, true)}.`
        : `The oldest, ${oldest.id || 'a bill'}, is ${late} days past its due date.`,
      actions: [
        ...(onPay ? [{ label: 'Pay vendor', primary: true, run: onPay }] : []),
        { label: 'Open bill', run: () => onOpenBill(oldest.invoice_id) },
      ],
    })
  }
  // Late but unapproved: the hold-up is ours and Pay would fail, so the ask is to chase the approver.
  if (!failed?.bills && s.lateInApproval.length) {
    const held = s.lateInApproval, oldest = held[0]
    const late = billState(oldest, now).late
    out.push({
      id: `held-${oldest.invoice_id}`, tone: 'warn', label: 'Held in approval', source: 'From this vendor’s bills',
      title: held.length === 1 ? `${oldest.id || 'A bill'} is ${late} days past due and not yet approved`
        : `${held.length} bills are past due and not yet approved · ${money(held.reduce((t, b) => t + outstandingOf(b), 0))}`,
      detail: held.length === 1
        ? 'It can’t be paid until it’s approved. Open it to see who it’s waiting on.'
        : `The oldest, ${oldest.id || 'a bill'}, is ${late} days past its due date. None can be paid until approved.`,
      actions: [{ label: 'Chase approval', primary: true, run: () => onOpenBill(oldest.invoice_id) }],
    })
  }
  const threads = failed?.inbox ? [] : inbox?.threads ?? []
  const waiting = threads.filter(t => t.needs_reply && t.started_by === 'vendor')
  if (waiting.length) {
    const t = waiting[0]
    out.push({
      id: `reply-${t.thread_id}`, tone: 'info', label: 'Needs a reply', source: `Email · ${relDay(t.last_at, now)}`,
      title: `${senderName(t.last_from)} wrote: “${t.subject}”`,
      detail: waiting.length > 1 ? `${waiting.length} conversations they started are waiting on you.` : (t.snippet || 'They’re waiting on a reply.'),
      actions: [{ label: 'Open conversation', primary: true, run: () => onOpenThread(t.thread_id) }],
    })
  }
  const unknown = [...new Set(threads.flatMap(t => t.unknown_senders))].filter(a => !contactNames[a])
  if (unknown.length) {
    out.push({
      id: `new-${unknown[0]}`, tone: 'info', label: 'New contact', source: 'From your mailbox',
      title: `${unknown[0]} emails you about this vendor`,
      detail: 'They aren’t a contact yet. Add them so their emails count as this vendor’s and the agent can reach them.',
      actions: [{ label: 'Add as contact', primary: true, run: () => onAddContact(unknown[0]) }],
    })
  }
  if (!bankVerified && !failed?.bank) {
    out.push({
      id: 'bank', tone: 'warn', label: 'Payments', source: 'From the vendor record',
      title: 'No verified bank account',
      detail: 'Payments to this vendor can’t be released until a bank account is added and verified.',
      actions: [{ label: 'Go to bank accounts', primary: true, run: () => onGoCompliance('bank') }],
    })
  }
  if (!hasPan) {
    out.push({
      id: 'pan', tone: 'warn', label: 'TDS', source: 'From the vendor record',
      title: 'PAN is missing',
      // PAN is edited in Vendor details, not under TDS, so don't send people to a section that can't take it.
      detail: onEditPan
        ? 'Without a PAN, TDS is deducted at the higher rate under Sec 206AA.'
        : 'Without a PAN, TDS is deducted at the higher rate under Sec 206AA. Add it in Vendor details on this card (admins and finance controllers).',
      actions: onEditPan ? [{ label: 'Add PAN', primary: true, run: onEditPan }] : [],
    })
  }
  return out
}

// ── Overview pieces ──────────────────────────────────────────────────────────

export function SectionHead({ title, count, children }: { title: string; count?: number; children?: ReactNode }) {
  return (
    <div className="vc__sec-h">
      <div className="vc__sec-t">{title}{count != null && <span className="vc__cnt">{count}</span>}</div>
      <span className="vc__grow" />
      {children}
    </div>
  )
}

export function OpenBills({ s, now, onOpenBill, onViewAll, onViewPaid, limit = 6, failed, onRetry }: {
  s: VendorSummary; now: Date; onOpenBill?: (id: string) => void; onViewAll: () => void; onViewPaid: () => void; limit?: number
  failed?: SourceFailures; onRetry?: () => void
}) {
  if (failed?.bills) return <div className="vc__box"><LoadNote status="error" what="bills" onRetry={onRetry} /></div>
  const paidTotal = s.paid.reduce((t, b) => t + billAmount(b), 0)
  return (
    <div className="vc__box">
      {s.open.length === 0
        ? <div className="vc__empty vc__empty--row"><CheckCircle2 size={16} />Nothing outstanding with this vendor.</div>
        : s.open.slice(0, limit).map(b => <BillRow key={b.invoice_id} bill={b} now={now} onOpen={onOpenBill} />)}
      {s.open.length > limit && (
        <button className="vc__bmore" onClick={onViewAll}>{s.open.length - limit} more open {s.open.length - limit === 1 ? 'bill' : 'bills'} <ChevronRight size={14} /></button>
      )}
      {/* No footer for "0 paid bills ₹0". The days are to when the bill was marked paid, the date we have. */}
      {s.paid.length > 0 && (
        <div className="vc__bfoot">
          <CheckCircle2 size={14} className="vc__okc" />
          <span className="vc__strong">{plural(s.paid.length, 'paid bill')}</span>
          <span className="vc__muted">{money(paidTotal)}{s.daysToPay != null ? ` · marked paid ${s.daysToPay} days after the bill date, on average` : ''}</span>
          <span className="vc__grow" />
          <button className="vc__lnk" onClick={onViewPaid}>Show <ChevronRight size={14} /></button>
        </div>
      )}
    </div>
  )
}

export function PaymentSchedule({ s, onPay, failed, onRetry }: { s: VendorSummary; onPay?: () => void; failed?: SourceFailures; onRetry?: () => void }) {
  if (failed?.bills) {
    return (
      <div className="vc__box vc__sched">
        <div className="vc__lbl">Cash going out</div>
        <div className="vc__sched-big">—</div>
        <LoadNote status="error" what="bills" onRetry={onRetry} />
      </div>
    )
  }
  const max = Math.max(1, ...s.buckets.map(b => b.amount))
  const held = s.lateInApproval.length
  const fx = s.open.filter(isUnconverted)
  return (
    <div className="vc__box vc__sched">
      <div className="vc__lbl">Cash going out</div>
      <div className="vc__sched-big">{money(s.outstanding)}</div>
      <div className="vc__muted vc__small">{plural(s.open.length, 'open bill')}, by when they’re due</div>
      {fx.length > 0 && <div className="vc__muted vc__xs vc__fxnote">{unconvertedNote(fx)}</div>}
      <div className="vc__sched-rows">
        {s.buckets.map(b => (
          <div key={b.key}>
            <div className="vc__srow-h">
              <b>{b.label}</b><span className="vc__muted">{b.bills.length}</span>
              {b.key === 'approval' && held > 0 && <span className="vc__srow-late">{held} past due</span>}
              <span className="vc__srow-v">{b.amount ? money(b.amount) : '—'}</span>
            </div>
            <div className="vc__srow-bar"><i className={BUCKET_TONE[b.key]} style={{ width: `${(b.amount / max) * 100}%` }} /></div>
          </div>
        ))}
      </div>
      {onPay && <button className="vc__btn vc__btn--sm vc__sched-pay" onClick={onPay}><Wallet size={14} />Pay vendor</button>}
    </div>
  )
}

/** Why a stopped vendor has no forecast. A bill this month means they didn't stop: last month was a gap. */
function stoppedText(s: VendorSummary, short = false): string {
  const f = s.forecast, cur = s.series.find(m => m.partial)
  if (!f.lastBilled) return 'no bills yet'
  if (f.lastBilled === cur?.ym) return 'no bills last month'
  return `no bills since ${monthLabel(f.lastBilled, short)}`
}

function ForecastPill({ s, withCurrent }: { s: VendorSummary; withCurrent?: boolean }) {
  const f = s.forecast
  const cur = s.series.find(m => m.partial)
  if (!f.ok) {
    return (
      <div className="vc__fcst"><span className="vc__fcst-ic"><Clock size={14} /></span>
        <div><div className="vc__lbl">Forecast</div><div className="vc__fcst-v">
          {f.reason === 'stopped'
            ? <>Not shown <span>· {stoppedText(s, true)}</span></>
            : <>Needs 3 months in a row <span>· {f.months} so far</span></>}
        </div></div>
      </div>
    )
  }
  const next = s.series.length ? MONTHS[(s.series[s.series.length - 1].month + 1) % 12] : ''
  return (
    <>
      {withCurrent && cur && (
        <div className="vc__fcst"><span className="vc__fcst-ic"><CalendarDays size={14} /></span>
          <div><div className="vc__lbl">{MONTHS_FULL[cur.month]} so far</div><div className="vc__fcst-v">{compactINR(cur.total)} <span>of ~{compactINR(f.expected![0], 1)}</span></div></div>
        </div>
      )}
      <div className="vc__fcst"><span className="vc__fcst-ic"><Sparkles size={14} /></span>
        <div><div className="vc__lbl">{next} forecast</div><div className="vc__fcst-v">~{compactINR(f.expected![1], 1)} <span>{compactINR(Math.max(0, f.expected![1] - f.band![1]), 1)}–{compactINR(f.expected![1] + f.band![1], 1)}</span></div></div>
      </div>
    </>
  )
}

function Legend({ forecast }: { forecast: boolean }) {
  return (
    <div className="vc__legend">
      <span><i className="vc__sw vc__sw--bar" />Billed</span>
      {forecast && <><span><i className="vc__sw vc__sw--fc" />Forecast</span><span><i className="vc__sw vc__sw--rng" />Likely range (80%)</span></>}
      <span><i className="vc__sw vc__sw--avg" />Monthly average</span>
    </div>
  )
}

/** The headline average, the same figure the chart's dashed line shows. */
function AvgHead({ s }: { s: VendorSummary }) {
  const fx = s.unconverted.length > 0 && <div className="vc__muted vc__xs vc__fxnote">{unconvertedNote(s.unconverted)}</div>
  if (!s.avgMonths && s.series.length) return <div><div className="vc__lbl">Monthly spend · by bill date</div><div className="vc__chart-big">First bills this month</div>{fx}</div>
  return (
    <div>
      <div className="vc__lbl">Monthly spend · by bill date</div>
      <div className="vc__chart-big">{compactINR(s.avgMonth)} <small>avg / month{s.avgMonths && s.avgMonths < 12 ? ` · ${plural(s.avgMonths, 'month')}` : ''}</small></div>
      {fx}
    </div>
  )
}

export function SpendCard({ s, onOpenSpend, onSelectMonth, status = 'ready', onRetry }: {
  s: VendorSummary; onOpenSpend: () => void; onSelectMonth: (ym: string) => void
  status?: LoadStatus; onRetry?: () => void
}) {
  if (status !== 'ready') return <div className="vc__box vc__chartcard"><div className="vc__lbl">Monthly spend · by bill date</div><LoadNote status={status} what="spend" onRetry={onRetry} /></div>
  return (
    <div className="vc__box vc__chartcard">
      <div className="vc__chart-head">
        <AvgHead s={s} />
        <ForecastPill s={s} />
        <button className="vc__lnk vc__push" onClick={onOpenSpend}>Details <ChevronRight size={14} /></button>
      </div>
      {s.series.length
        ? <><SpendChart series={s.series} forecast={s.forecast} height={232} onSelect={onSelectMonth} billsPerMonth={s.billsPerMonth} average={s.avgMonths ? s.avgMonth : undefined} /><Legend forecast={s.forecast.ok} /></>
        : <div className="vc__empty">No bills yet. Spend shows here once the first bill arrives.</div>}
    </div>
  )
}

function ordinal(d: number): string {
  return d + (d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th')
}

export function SpendView({ s, now, paymentTerms, selected, onSelect, onOpenBill, status = 'ready', onRetry }: {
  s: VendorSummary; now: Date; paymentTerms: number | null
  selected: string | null; onSelect: (ym: string) => void; onOpenBill?: (id: string) => void
  status?: LoadStatus; onRetry?: () => void
}) {
  const [range, setRange] = useState(12)
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const f = s.forecast
  const complete = s.series.filter(m => !m.partial)
  const sel = s.series.find(m => m.ym === selected) ?? complete[complete.length - 1] ?? s.series[s.series.length - 1]
  const cur = s.series.find(m => m.partial)
  const last3 = new Set(complete.slice(-3).map(m => m.ym)), prev3 = new Set(complete.slice(-6, -3).map(m => m.ym))
  // Unconverted foreign bills have no ₹ value, so they'd drag an average down as ₹0.
  const inr = s.bills.filter(b => !isUnconverted(b))
  const avgOf = (set: Set<string>) => { const bs = inr.filter(b => b.invoice_date && set.has(b.invoice_date.slice(0, 7))); return bs.length ? bs.reduce((t, b) => t + billAmount(b), 0) / bs.length : 0 }
  const avgBill = avgOf(last3), prevAvg = avgOf(prev3)
  // The latest 8 by bill date, not whatever order the API sent.
  const latest = [...s.bills].filter(b => b.invoice_date).sort((a, b) => b.invoice_date!.localeCompare(a.invoice_date!)).slice(0, 8)
  const days = [...new Set(latest.map(b => dayOf(b.invoice_date)?.getDate()).filter((d): d is number => !!d))].sort((a, b) => a - b)

  if (status !== 'ready') return <div className="vc__sec"><div className="vc__box"><LoadNote status={status} what="bills" onRetry={onRetry} /></div></div>
  if (!s.series.length) return <div className="vc__sec"><div className="vc__box vc__empty">No bills from this vendor yet.</div></div>

  return (
    <>
      <div className="vc__sec">
        <div className="vc__box vc__chartcard">
          <div className="vc__chart-head">
            <AvgHead s={s} />
            {/* Without a forecast, the card under the chart already says why. */}
            {s.forecast.ok && <ForecastPill s={s} withCurrent />}
            <div className="vc__push vc__row8">
              <div className="vc__seg"><button className={range === 6 ? 'on' : ''} onClick={() => setRange(6)}>6M</button><button className={range === 12 ? 'on' : ''} onClick={() => setRange(12)}>12M</button><button className={range === 24 ? 'on' : ''} onClick={() => setRange(24)}>24M</button></div>
              <div className="vc__seg"><button className={view === 'chart' ? 'on' : ''} onClick={() => setView('chart')} aria-label="Chart view"><BarChart3 size={14} /></button><button className={view === 'table' ? 'on' : ''} onClick={() => setView('table')} aria-label="Table view"><FileText size={14} /></button></div>
            </div>
          </div>
          {view === 'chart' ? (
            <>
              <SpendChart series={s.series} forecast={f} months={range} height={300} selected={sel?.ym ?? null} onSelect={onSelect} billsPerMonth={s.billsPerMonth} average={s.avgMonths ? s.avgMonth : undefined} />
              <Legend forecast={f.ok} />
            </>
          ) : (
            <div className="vc__xscroll">
              <table className="vc__table">
                <thead><tr><th>Month</th><th className="vc__num">Bills</th><th className="vc__num">Billed</th><th className="vc__num">Paid</th><th className="vc__num">Outstanding</th><th className="vc__num">vs prev</th></tr></thead>
                <tbody>
                  {s.series.slice(-range).map((m, i, a) => {
                    const prev = a[i - 1]
                    const ch = prev && !m.partial && prev.total > 0 ? (m.total / prev.total - 1) * 100 : null
                    return (
                      <tr key={m.ym}>
                        <td>{MONTHS_FULL[m.month]} {m.year}{m.partial && <span className="vc__muted"> (so far)</span>}</td>
                        <td className="vc__num">{m.bills.length}</td><td className="vc__num">{money(m.total)}</td>
                        <td className="vc__num">{money(m.paid)}</td><td className="vc__num">{money(m.outstanding)}</td>
                        <td className="vc__num">{ch == null ? '—' : <span className={`vc__delta ${ch >= 0 ? 'vc__delta--up' : 'vc__delta--down'}`}>{ch >= 0 ? '+' : ''}{ch.toFixed(1)}%</span>}</td>
                      </tr>
                    )
                  })}
                  {f.ok && (
                    <tr className="vc__fcrow"><td>Next month · forecast</td><td className="vc__num">~{Math.round(s.billsPerMonth)}</td><td className="vc__num">{money(f.expected![1])}</td><td className="vc__num" colSpan={3}>{money(Math.max(0, f.expected![1] - f.band![1]))} – {money(f.expected![1] + f.band![1])}</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="vc__sec vc__grid3">
        {f.ok ? (
          <>
            <div className="vc__tile">
              <div className="vc__tile-l"><CalendarDays size={14} />{cur ? `${MONTHS_FULL[cur.month]} · in progress` : 'This month'}</div>
              <div className="vc__tile-v">{compactINR(cur?.total ?? 0)} <small>of ~{compactINR(f.expected![0], 1)}</small></div>
              <div className="vc__meter"><i style={{ width: `${Math.min(100, ((cur?.total ?? 0) / (f.expected![0] || 1)) * 100)}%` }} /></div>
              <div className="vc__tile-s">{plural(cur?.bills.length ?? 0, 'bill')} so far. Around {compactINR(Math.max(0, f.expected![0] - (cur?.total ?? 0)), 1)} more expected by month end.</div>
            </div>
            <div className="vc__tile">
              <div className="vc__tile-l"><Sparkles size={14} />Next month</div>
              <div className="vc__tile-v">~{compactINR(f.expected![1], 1)}</div>
              <div className="vc__tile-s">Likely between {compactINR(Math.max(0, f.expected![1] - f.band![1]), 1)} and {compactINR(f.expected![1] + f.band![1], 1)}, about {plural(Math.round(s.billsPerMonth), 'bill')}.</div>
            </div>
            <div className="vc__tile">
              <div className="vc__tile-l"><Info size={14} />How it’s worked out</div>
              <div className="vc__tile-v vc__tile-v--sm">{f.confidence} confidence <span className={`vc__chip vc__chip--xs ${f.confidence === 'High' ? 'vc__chip--ok' : f.confidence === 'Medium' ? 'vc__chip--info' : 'vc__chip--warn'}`}>±{f.within}%</span></div>
              {/* Only a backtest earns "past forecasts landed within"; with 3–5 months it's just the range's width. */}
              <div className="vc__tile-s">{f.method}. {f.backtested
                ? `Past forecasts landed within ±${f.within}% of what was billed.`
                : `With ${f.months} months to go on, the likely range is about ±${f.within}% of the estimate. Past forecasts can be checked from 6 months.`}</div>
            </div>
          </>
        ) : (
          <div className="vc__lock">
            <div className="vc__icotile"><Clock size={16} /></div>
            <div className="vc__grow">
              <div className="vc__strong">A forecast needs bills in each of the last 3 months</div>
              <div className="vc__muted vc__small">
                {f.reason === 'stopped'
                  ? (f.lastBilled ? `${stoppedText(s).replace(/^n/, 'N')}, so there’s no unbroken run to forecast from.` : 'No bills from this vendor yet.')
                  : `${f.months} month${f.months === 1 ? '' : 's'} in a row so far, since ${monthLabel(f.since)}. A gap restarts the count.`}
                {' '}With 3–5 months in a row it uses a weighted average; from 6, a trend model.
              </div>
              <div className="vc__meter vc__meter--short"><i style={{ width: `${Math.round((f.months / 3) * 100)}%` }} /></div>
            </div>
          </div>
        )}
      </div>

      <div className="vc__sec vc__grid3">
        <div className="vc__tile">
          <div className="vc__tile-l"><Clock size={14} />Days to pay</div>
          <div className="vc__tile-v">{s.daysToPay ?? '—'} <small>days</small></div>
          <div className="vc__tile-s">
            {s.daysToPay == null ? 'No paid bills with a payment date yet.'
              : paymentTerms ? (s.daysToPay > paymentTerms
                ? <>Terms are {paymentTerms} days, so you usually pay <b className="vc__bad">{s.daysToPay - paymentTerms} days late</b>.</>
                : <>Within the {paymentTerms}-day terms.</>)
              : 'From bill date to when it was marked paid.'}
          </div>
        </div>
        <div className="vc__tile">
          <div className="vc__tile-l"><Receipt size={14} />Average bill</div>
          <div className="vc__tile-v">{compactINR(avgBill || (s.bills.length ? s.bills.reduce((t, b) => t + billAmount(b), 0) / s.bills.length : 0))}</div>
          <div className="vc__tile-s">{prevAvg > 0 && avgBill > 0
            ? <><span className={`vc__delta ${avgBill >= prevAvg ? 'vc__delta--up' : 'vc__delta--down'}`}>{avgBill >= prevAvg ? '↑' : '↓'} {Math.abs((avgBill / prevAvg - 1) * 100).toFixed(1)}%</span> vs the 3 months before</>
            : 'Across this vendor’s bills.'}</div>
        </div>
        <div className="vc__tile">
          <div className="vc__tile-l"><TrendingUp size={14} />Billing rhythm</div>
          <div className="vc__tile-v">~{s.billsPerMonth.toFixed(1)} <small>bills / month</small></div>
          <div className="vc__tile-s">{days.length ? `Usually dated around the ${days.slice(0, 4).map(ordinal).join(', ')}.` : 'Over the last 3 months.'}</div>
        </div>
      </div>

      {sel && (
        <div className="vc__sec">
          <SectionHead title={`${MONTHS_FULL[sel.month]} ${sel.year}`}>
            {view === 'chart' && <span className="vc__muted vc__small">Pick a bar to switch month</span>}
            <span className="vc__small"><span className="vc__muted">{plural(sel.bills.length, 'bill')} ·</span> <b className="vc__strong">{money(sel.total)}</b></span>
          </SectionHead>
          <div className="vc__box">
            {sel.bills.length ? sel.bills.map(b => <BillRow key={b.invoice_id} bill={b} now={now} onOpen={onOpenBill} />)
              : <div className="vc__empty vc__empty--row">No bills dated in this month.</div>}
          </div>
        </div>
      )}
    </>
  )
}

// ── Overview: conversations and contracts ────────────────────────────────────

export function OriginTag({ t }: { t: Pick<InboxThread, 'started_by'> }) {
  return t.started_by === 'vendor'
    ? <span className="vc__org vc__org--vendor"><ArrowDownLeft size={12} />They started</span>
    : <span className="vc__org vc__org--us"><ArrowUpRight size={12} />You started</span>
}

export function ThreadsCard({ inbox, now, onOpen, onAll, onConnect }: {
  inbox: VendorInbox | null; now: Date; onOpen: (id: string) => void; onAll: () => void; onConnect: () => void
}) {
  if (!inbox) return <div className="vc__box vc__empty">Loading conversations…</div>
  if (!inbox.connected && !inbox.threads.length) {
    return (
      <div className="vc__box vc__empty">
        <div className="vc__empty-ic"><Mail size={20} /></div>
        <div className="vc__empty-t">Connect your mailbox</div>
        Every email with this vendor shows here, including the ones they start.
        <div><button className="vc__btn vc__btn--primary vc__btn--sm vc__mt12" onClick={onConnect}>Connect Gmail</button></div>
      </div>
    )
  }
  if (!inbox.threads.length) return <div className="vc__box vc__empty">No emails with this vendor yet.</div>
  return (
    <div className="vc__box">
      {inbox.threads.slice(0, 4).map(t => (
        <div key={t.thread_id} className="vc__lrow" role="button" tabIndex={0} onClick={() => onOpen(t.thread_id)} onKeyDown={e => { if (e.key === 'Enter') onOpen(t.thread_id) }}>
          <Avatar name={senderName(t.started_by_name) || '?'} />
          <div className="vc__lrow-main">
            <div className="vc__lrow-t">{t.needs_reply && <span className="vc__udot" />}<span className="vc__ellipsis">{t.subject}</span></div>
            <div className="vc__lrow-s">{senderName(t.last_from)} · {t.snippet}</div>
            <div className="vc__lrow-tags">
              <OriginTag t={t} />
              {t.needs_reply && <span className="vc__chip vc__chip--xs vc__chip--warn"><i className="vc__d" />Needs reply</span>}
              {t.has_attachments && <span className="vc__chip vc__chip--xs"><Paperclip size={11} /></span>}
            </div>
          </div>
          <div className="vc__lrow-r">{relDay(t.last_at, now)}</div>
        </div>
      ))}
      {inbox.threads.length > 4 && <button className="vc__bmore" onClick={onAll}>{inbox.threads.length - 4} more <ChevronRight size={14} /></button>}
    </div>
  )
}

export function ContractsCard({ docs, onOpen, onUpload }: { docs: VendorDocument[] | null; onOpen: (d: VendorDocument) => void; onUpload: () => void }) {
  const contracts = (docs ?? []).filter(d => d.doc_type === 'contract')
  if (!docs) return <div className="vc__box vc__empty">Loading…</div>
  if (!contracts.length) {
    return (
      <div className="vc__box vc__empty">
        <div className="vc__empty-ic"><FileText size={20} /></div>
        <div className="vc__empty-t">No contract on file</div>
        Upload the agreement or IO and its terms are checked against this vendor.
        <div><button className="vc__btn vc__btn--sm vc__mt12" onClick={onUpload}><Plus size={14} />Upload contract</button></div>
      </div>
    )
  }
  return (
    <div className="vc__box">
      {contracts.slice(0, 4).map(d => (
        <div key={d.id} className="vc__lrow" role="button" tabIndex={0} onClick={() => onOpen(d)} onKeyDown={e => { if (e.key === 'Enter') onOpen(d) }}>
          <FileIcon name={d.file_name} />
          <div className="vc__lrow-main">
            <div className="vc__lrow-t"><span className="vc__ellipsis">{d.file_name}</span></div>
            <div className="vc__lrow-s">{[d.uploaded_by, shortDate(d.uploaded_at, true)].filter(Boolean).join(' · ')}</div>
          </div>
          <div className="vc__lrow-r"><ClaimsChip status={d.claims_status} /></div>
        </div>
      ))}
    </div>
  )
}

export function ClaimsChip({ status }: { status: string | null }) {
  switch ((status || '').toUpperCase()) {
    case 'DONE': return <span className="vc__chip vc__chip--xs vc__chip--ok"><Check size={11} />Checked</span>
    case 'PENDING': case 'RUNNING': return <span className="vc__chip vc__chip--xs vc__chip--info"><i className="vc__d" />Reading</span>
    case 'FAILED': return <span className="vc__chip vc__chip--xs vc__chip--bad"><AlertTriangle size={11} />Couldn’t read</span>
    case 'SKIPPED': return <span className="vc__chip vc__chip--xs">No text to read</span>
    default: return null
  }
}

export function FileIcon({ name, small }: { name: string; small?: boolean }) {
  const ext = (name.split('.').pop() || '').toLowerCase()
  const kind = ['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext) ? 'IMG' : ['xls', 'xlsx', 'csv'].includes(ext) ? 'XLS' : ext === 'pdf' ? 'PDF' : 'DOC'
  return <span className={`vc__ficon vc__ficon--${kind.toLowerCase()}${small ? ' vc__ficon--sm' : ''}`}>{kind}</span>
}

