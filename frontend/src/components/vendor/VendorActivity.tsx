import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, FileText, Info, Mail, PenLine, Receipt, Search, ShieldCheck, UserRound, Wallet } from 'lucide-react'
import { fetchVendorActivity, type VendorActivityEvent, type VendorActivityKind } from '../../api/vendorCard'
import { LoadNote, type LoadStatus } from './VendorViews'
import { MONTHS } from './vendorStats'

/**
 * The vendor's history in one timeline: bills, approvals, payments, edits,
 * compliance checks, documents and emails, newest first, a page at a time.
 */

type Filter = 'all' | 'bills' | 'payments' | 'changes' | 'compliance' | 'documents' | 'emails'

// Approvals are part of a bill's story and contacts are vendor edits, so they
// share a chip rather than adding two more that are usually empty.
const FILTERS: { k: Filter; label: string; kinds: VendorActivityKind[] | null }[] = [
  { k: 'all', label: 'All', kinds: null },
  { k: 'bills', label: 'Bills', kinds: ['bill', 'approval'] },
  { k: 'payments', label: 'Payments', kinds: ['payment'] },
  { k: 'changes', label: 'Changes', kinds: ['change', 'contact'] },
  { k: 'compliance', label: 'Compliance', kinds: ['compliance'] },
  { k: 'documents', label: 'Documents', kinds: ['document'] },
  { k: 'emails', label: 'Emails', kinds: ['email'] },
]

const ICON: Record<VendorActivityKind, ReactNode> = {
  bill: <Receipt size={14} />,
  approval: <CheckCircle2 size={14} />,
  payment: <Wallet size={14} />,
  change: <PenLine size={14} />,
  compliance: <ShieldCheck size={14} />,
  document: <FileText size={14} />,
  contact: <UserRound size={14} />,
  email: <Mail size={14} />,
}

const FAILURES = new Set(['payment_failed', 'gst_failed', 'bank_failed', 'bill_rejected'])
const SUCCESSES = new Set(['payment_succeeded', 'bill_approved', 'payment_marked_paid'])

/** Tone sits on the icon only: the timeline stays calm, failures still stand out. */
export function toneOf(action: string): 'bad' | 'ok' | null {
  if (FAILURES.has(action)) return 'bad'
  if (SUCCESSES.has(action) || action.endsWith('_verified')) return 'ok'
  return null
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`

function dayLabel(d: Date, now: Date): string {
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (dayKey(d) === dayKey(now)) return 'Today'
  if (dayKey(d) === dayKey(yesterday)) return 'Yesterday'
  const base = `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`
  return d.getFullYear() === now.getFullYear() ? base : `${base} ${d.getFullYear()}`
}

const clock = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

/** Long values (addresses, notes) are cut; the full value is on hover. */
function Val({ v }: { v: string | null }) {
  if (v == null || v === '') return <span className="vc__act-none">empty</span>
  const cut = v.length > 40 ? `${v.slice(0, 38)}…` : v
  return <span className="vc__act-val" title={cut !== v ? v : undefined}>{cut}</span>
}

/** The title, with the bill number (or the whole title) opening the bill. */
function Title({ e, onOpenBill }: { e: VendorActivityEvent; onOpenBill?: (invoiceId: string) => void }) {
  if (!e.invoice_id || !onOpenBill) return <>{e.title}</>
  const open = () => onOpenBill(e.invoice_id!)
  const at = e.bill_no ? e.title.indexOf(e.bill_no) : -1
  if (at < 0) return <button type="button" className="vc__act-link" onClick={open}>{e.title}</button>
  return (
    <>
      {e.title.slice(0, at)}
      <button type="button" className="vc__act-link" onClick={open}>{e.bill_no}</button>
      {e.title.slice(at + e.bill_no!.length)}
    </>
  )
}

/**
 * A bill that arrives and goes straight to approval writes two rows a moment
 * apart ("Received bill X", "Submitted bill X for approval"). Read as one:
 * "Received bill X · ₹… and sent it for approval". Events are newest first,
 * so the submit sits just above its receipt.
 */
const PAIR_MS = 10 * 60 * 1000
export function collapseBillPairs(events: VendorActivityEvent[]): VendorActivityEvent[] {
  const out: VendorActivityEvent[] = []
  for (let i = 0; i < events.length; i++) {
    const sub = events[i], rec = events[i + 1]
    if (sub.action === 'bill_submitted' && rec?.action === 'bill_received' && rec.invoice_id && rec.invoice_id === sub.invoice_id
        && (rec.actor ?? '') === (sub.actor ?? '') && Math.abs(new Date(sub.at).getTime() - new Date(rec.at).getTime()) <= PAIR_MS) {
      out.push({ ...rec, id: `${rec.id}+${sub.id}`, at: sub.at, title: `${rec.title} and sent it for approval`, detail: sub.detail ?? rec.detail })
      i++
      continue
    }
    out.push(sub)
  }
  return out
}

/** A login name like "vibhanshu" reads as a person: "Vibhanshu". Names already cased are left alone. */
function personName(n: string): string {
  return n === n.toLowerCase() && !n.includes('@') ? n.replace(/\b\p{L}/gu, ch => ch.toUpperCase()) : n
}

/** Free text from bills and comments carries raw amounts ("₹2673826.56"); show them the Indian way. */
function readableAmounts(text: string): string {
  return text.replace(/₹\s?(\d{4,})(\.\d+)?/g, (_, whole: string, dec?: string) => {
    const n = Number(`${whole}${dec ?? ''}`)
    return Number.isFinite(n) ? `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}` : _
  })
}

function Row({ e, onOpenBill }: { e: VendorActivityEvent; onOpenBill?: (invoiceId: string) => void }) {
  const tone = toneOf(e.action)
  const at = new Date(e.at)
  return (
    <li className="vc__act-row">
      <span className={`vc__act-ic vc__act-ic--${e.kind}${tone ? ` vc__act-ic--${tone}` : ''}`} aria-hidden="true">{ICON[e.kind] ?? <Info size={14} />}</span>
      <div className="vc__act-body">
        <div className="vc__act-t"><Title e={e} onOpenBill={onOpenBill} /></div>
        <div className="vc__act-meta">
          <span>{e.actor ? `by ${personName(e.actor)}${e.actor_role ? ` · ${e.actor_role}` : ''}` : 'System'}</span>
          <span aria-hidden="true"> · </span>
          <time dateTime={e.at}>{clock(at)}</time>
        </div>
        {e.detail && <div className="vc__act-detail" title={e.detail}>{readableAmounts(e.detail)}</div>}
        {!!e.changes?.length && (
          <ul className="vc__act-chg">
            {e.changes.map((c, i) => (
              <li key={`${c.field}-${i}`}>
                <span className="vc__act-field">{c.field}:</span> <Val v={c.from} /> <span className="vc__act-arrow">→</span> <Val v={c.to} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function Skeleton() {
  return (
    <div className="vc__box vc__act-list" role="status" aria-busy="true">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="vc__act-row vc__act-skel">
          <span className="vc__skel vc__skel--av vc__act-skel-ic" />
          <div className="vc__act-body">
            <span className="vc__skel" style={{ width: `${70 - (i % 3) * 12}%` }} />
            <span className="vc__skel" style={{ width: '28%' }} />
          </div>
        </div>
      ))}
      <span className="vc__sr">Loading activity…</span>
    </div>
  )
}

export default function VendorActivity({ supplierId, onOpenBill, now }: {
  supplierId: string
  onOpenBill?: (invoiceId: string) => void
  now: Date
}) {
  const [status, setStatus] = useState<LoadStatus>('loading')
  const [events, setEvents] = useState<VendorActivityEvent[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [partial, setPartial] = useState(false)
  const [older, setOlder] = useState<'idle' | 'loading' | 'error'>('idle')
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  // Bumped per vendor and per reload, so a slow answer for the last vendor
  // never lands on this one.
  const seq = useRef(0)

  const load = () => {
    const n = ++seq.current
    setStatus('loading'); setOlder('idle')
    fetchVendorActivity(supplierId)
      .then(p => {
        if (n !== seq.current) return
        setEvents(p.events); setNextBefore(p.next_before); setPartial(p.partial); setStatus('ready')
      })
      .catch(() => { if (n === seq.current) setStatus('error') })
  }
  useEffect(() => {
    setEvents([]); setNextBefore(null); setPartial(false); setFilter('all'); setQuery('')
    load()
  }, [supplierId])

  const loadOlder = () => {
    if (!nextBefore || older === 'loading') return
    const n = seq.current
    setOlder('loading')
    fetchVendorActivity(supplierId, { before: nextBefore })
      .then(p => {
        if (n !== seq.current) return
        // A page boundary can repeat an event; keep the first copy.
        setEvents(prev => { const seen = new Set(prev.map(e => e.id)); return [...prev, ...p.events.filter(e => !seen.has(e.id))] })
        setNextBefore(p.next_before); setPartial(was => was || p.partial); setOlder('idle')
      })
      .catch(() => { if (n === seq.current) setOlder('error') })
  }

  const merged = useMemo(() => collapseBillPairs(events), [events])
  const counts = useMemo(() => {
    const c = {} as Record<Filter, number>
    for (const f of FILTERS) c[f.k] = f.kinds ? merged.filter(e => f.kinds!.includes(e.kind)).length : merged.length
    return c
  }, [merged])

  const days = useMemo(() => {
    const kinds = FILTERS.find(f => f.k === filter)?.kinds
    const q = query.trim().toLowerCase()
    const shown = merged.filter(e => (!kinds || kinds.includes(e.kind))
      && (!q || [e.title, e.bill_no, e.actor].some(s => s?.toLowerCase().includes(q))))
    const out: { key: string; label: string; items: VendorActivityEvent[] }[] = []
    for (const e of shown) {
      const d = new Date(e.at)
      const key = dayKey(d)
      const last = out[out.length - 1]
      if (last?.key === key) last.items.push(e)
      else out.push({ key, label: dayLabel(d, now), items: [e] })
    }
    return out
  }, [merged, filter, query, now])

  return (
    <div className="vc__act">
      {/* The tab already says "Activity": no second heading, the toolbar leads. */}
      <h3 className="vc__sr">Activity</h3>

      {status === 'loading' && <Skeleton />}
      {status === 'error' && <LoadNote status="error" what="activity" onRetry={load} className="vc__box" />}

      {status === 'ready' && (events.length === 0 ? (
        <>
          {partial && <p className="vc__act-note"><Info size={14} aria-hidden="true" />Some older history couldn’t be loaded.</p>}
          <div className="vc__box vc__empty">No activity recorded for this vendor yet.</div>
        </>
      ) : (
        <>
          <div className="vc__act-bar">
            <div className="vc__seg vc__act-chips" role="group" aria-label="Filter by kind">
              {FILTERS.filter(f => counts[f.k] > 0 || filter === f.k).map(f => (
                <button key={f.k} type="button" className={filter === f.k ? 'on' : ''} aria-pressed={filter === f.k} onClick={() => setFilter(f.k)}>
                  {f.label}<span className="vc__c">{counts[f.k]}</span>
                </button>
              ))}
            </div>
            <label className="vc__search vc__act-search">
              <Search size={14} aria-hidden="true" />
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search by title, bill no. or person" aria-label="Search activity" />
            </label>
          </div>

          {partial && <p className="vc__act-note"><Info size={14} aria-hidden="true" />Some older history couldn’t be loaded.</p>}

          {days.length === 0
            ? <div className="vc__box vc__empty">{nextBefore ? 'Nothing matches in what’s loaded. Load older activity to search further back.' : 'Nothing matches.'}</div>
            : (
              <div className="vc__box vc__act-list">
                {days.map(d => (
                  <section key={d.key} className="vc__act-day" aria-labelledby={`vc-act-${d.key}`}>
                    <h4 id={`vc-act-${d.key}`} className="vc__act-dayh">{d.label}</h4>
                    <ul className="vc__act-ul">
                      {d.items.map(e => <Row key={e.id} e={e} onOpenBill={onOpenBill} />)}
                    </ul>
                  </section>
                ))}
              </div>
            )}

          {older === 'error' && <LoadNote status="error" what="older activity" onRetry={loadOlder} className="vc__act-more" />}
          {nextBefore && older !== 'error' && (
            <div className="vc__act-more">
              <button type="button" className="vc__btn vc__btn--sm" disabled={older === 'loading'} onClick={loadOlder}>
                {older === 'loading' ? 'Loading…' : 'Load older'}
              </button>
            </div>
          )}
        </>
      ))}
    </div>
  )
}
