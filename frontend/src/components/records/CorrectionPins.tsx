import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowRight, Check, ChevronDown, Info, X } from 'lucide-react'
import { applyBillCoding, fetchFinding, setFindingStatus } from '../../api/review'
import { REVIEW_CHANGED_EVENT } from './reviewEvents'

/**
 * A bill's suggested corrections, built into the fields they would change.
 *
 * The field's value is replaced, in place, by a small box: the current value
 * struck through, the suggested one as a chip with an ⓘ for why — and ✓ / ✕
 * on the field's label row. It is rendered *inside* the bill card's own field
 * (a portal into it), so the card's layout makes room for it rather than
 * something floating over the next field. A bar at the foot of the card saves:
 * accepted ones are written to the bill, kept ones are the user's call — either
 * way the review item closes, and declined ones are not raised again.
 */

interface Proposal {
  id: string
  label: string
  current: string | null
  proposed: string
  why?: string
  writable?: boolean
}

type Decision = 'use' | 'keep'

/**
 * The reason, finished. Findings stored before the scanner said what the habit
 * was read "4 of this vendor's last 5 bills" — complete it from the proposal.
 */
function reasonFor(p: Proposal): string | undefined {
  if (!p.why) return undefined
  if (!/^\d+ of this vendor's last \d+ bills$/.test(p.why.trim())) return p.why
  if (p.id === 'department') return `${p.why} went to the ${p.proposed} department`
  if (p.id === 'cost_centre') return `${p.why} were tagged to cost centre “${p.proposed}”`
  return `${p.why} had ${p.label.toLowerCase()} “${p.proposed}”`
}

/** The bill-card fields (`data-fix`) a suggestion belongs to, best first. */
const ANCHORS: Record<string, string[]> = {
  payment_terms: ['payment_terms', 'due_date'],
  category: ['category'],
  department: ['department'],
  cost_centre: ['cost_centre'],
  gst_split: ['gst_split'],
}

export function findAnchor(id: string): HTMLElement | null {
  for (const key of ANCHORS[id] ?? [id]) {
    const el = document.querySelector<HTMLElement>(`.rc__layer [data-fix="${key}"]`)
    if (el && el.getClientRects().length) return el
  }
  return null
}

interface Hosts { anchor: HTMLElement; box: HTMLElement; actions: HTMLElement }

/** Where, inside a field, the box and the ✓ / ✕ go: a label-over-value field,
 *  or an Accounting row (label | value | badge cells). */
function slotsOf(anchor: HTMLElement): { box: Element; actions: Element } | null {
  if (anchor.tagName === 'TR') {
    const cells = anchor.querySelectorAll(':scope > td')
    return cells.length >= 3 ? { box: cells[1], actions: cells[2] } : null
  }
  // A bill-card Field (.bm__lbl) or a vendor-card detail field (.vd__field-lbl).
  const label = anchor.querySelector(':scope > .bm__lbl, :scope > .vd__field-lbl')
  return label ? { box: anchor, actions: label } : null
}

/**
 * One box host and one actions host per suggestion, appended to the field.
 * Re-attached if the bill card re-renders the field (a refresh after load).
 */
export function useFixHosts(ids: string[]) {
  const [hosts, setHosts] = useState<Record<string, Hosts | null>>({})
  const key = ids.join('|')
  useEffect(() => {
    const made: Record<string, Hosts | null> = {}
    const ensure = () => {
      let changed = false
      for (const id of ids) {
        const cur = made[id]
        if (cur && cur.box.isConnected && cur.actions.isConnected && cur.anchor.isConnected) continue
        const anchor = findAnchor(id)
        const slots = anchor && slotsOf(anchor)
        if (!anchor || !slots) {
          if (cur !== null) { made[id] = null; changed = true }
          continue
        }
        const box = document.createElement('div')
        box.className = 'rc__fix-host'
        const actions = document.createElement('span')
        actions.className = 'rc__fix-acts-host'
        slots.box.appendChild(box)
        slots.actions.appendChild(actions)
        made[id] = { anchor, box, actions }
        changed = true
      }
      if (changed) setHosts({ ...made })
    }
    ensure()
    const t = window.setInterval(ensure, 300)
    return () => {
      window.clearInterval(t)
      for (const h of Object.values(made)) {
        if (!h) continue
        h.box.remove()
        h.actions.remove()
        delete h.anchor.dataset.fixState
      }
    }
  }, [key])
  return hosts
}

export default function CorrectionPins({ findingId, invoiceId, onSaved, onSkip }: {
  findingId: string
  invoiceId: string
  /** Something was written; the bill's own fields need a reload. */
  onSaved: (applied: number) => void
  onSkip: () => void
}) {
  const [proposals, setProposals] = useState<Proposal[] | null>(null)
  const [decided, setDecided] = useState<Record<string, Decision>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // The ⓘ's reason, shown at once and right above the icon — the browser's
  // own title tooltip is slow, small and drawn under the cursor.
  const [tip, setTip] = useState<{ text: string; x: number; y: number } | null>(null)
  const showTip = (text: string, el: HTMLElement) => {
    const r = el.getBoundingClientRect()
    setTip({ text, x: r.left + r.width / 2, y: r.top })
  }

  useEffect(() => {
    let live = true
    fetchFinding(findingId)
      .then(f => {
        if (!live) return
        const ps = (f.evidence as { proposals?: Proposal[] })?.proposals ?? []
        setProposals(ps)
        // Something that cannot be written is the user's to change by hand.
        setDecided(Object.fromEntries(ps.filter(p => !p.writable).map(p => [p.id, 'keep' as Decision])))
      })
      .catch(e => live && setError(e instanceof Error ? e.message : 'Could not load the suggestions'))
    return () => { live = false }
  }, [findingId])

  const ids = useMemo(() => (proposals ?? []).map(p => p.id), [proposals])
  const hosts = useFixHosts(ids)
  const pending = (proposals ?? []).filter(p => !decided[p.id])

  // The field's own value is hidden while its box stands in for it
  // (data-fix-state, styled in records.css).
  useEffect(() => {
    for (const p of proposals ?? []) {
      const h = hosts[p.id]
      if (h) h.anchor.dataset.fixState = decided[p.id] ?? 'pending'
    }
  }, [proposals, decided, hosts])

  // The suggestions in the order they sit on the bill, top to bottom, so
  // "next" always means the one below.
  const pinnedInOrder = useMemo(
    () => (proposals ?? [])
      .filter(p => hosts[p.id])
      .sort((a, b) => hosts[a.id]!.anchor.getBoundingClientRect().top - hosts[b.id]!.anchor.getBoundingClientRect().top)
      .map(p => p.id),
    [proposals, hosts],
  )
  const [cursor, setCursor] = useState(0)
  const [flash, setFlash] = useState<string | null>(null)

  const goTo = (i: number) => {
    const id = pinnedInOrder[i]
    if (!id) return
    setCursor(i)
    hosts[id]?.anchor.scrollIntoView({ block: 'center', behavior: 'smooth' })
    // A brief pulse on the box, so the eye lands on it after the scroll.
    setFlash(id)
    window.setTimeout(() => setFlash(f => (f === id ? null : f)), 1200)
  }

  // The next suggestion the user cannot already see: below the card's scroll
  // view, or hidden behind the save bar at its foot. When every
  // remaining one is on screen there is nowhere to go, and no arrow.
  const [nextIdx, setNextIdx] = useState<number | null>(null)
  useEffect(() => {
    let raf = 0
    let last: number | null = -2
    const BAR_ALLOWANCE = 120 // the save bar and arrow cover the card's last ~120px
    const tick = () => {
      const view = document.querySelector('.rc__layer .bm__scroll')?.getBoundingClientRect()
      let found: number | null = null
      if (view) {
        for (let i = 0; i < pinnedInOrder.length; i++) {
          if (i <= cursor) continue
          const r = hosts[pinnedInOrder[i]]?.box.getBoundingClientRect()
          if (!r) continue
          // Only below counts: one scrolled past above has been seen, and a
          // down arrow that scrolls up would be lying.
          const below = r.bottom > view.bottom - BAR_ALLOWANCE
          if (below) { found = i; break }
        }
      }
      if (found !== last) { last = found; setNextIdx(found) }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [pinnedInOrder, hosts, cursor])

  // On opening, go straight to the first suggestion on the bill.
  const firstId = pinnedInOrder[0]
  const [arrived, setArrived] = useState(false)
  useEffect(() => {
    if (!firstId || arrived) return
    setArrived(true)
    // After the card's open animation, so the scroll lands where it looks.
    const t = window.setTimeout(() => goTo(0), 450)
    return () => window.clearTimeout(t)
  }, [firstId])

  if (error && !proposals) return null
  if (!proposals || proposals.length === 0) return null

  const decide = (id: string, d: Decision) =>
    // Clicking the active choice again clears it, back to undecided.
    setDecided(prev => {
      const next = { ...prev }
      if (next[id] === d) delete next[id]
      else next[id] = d
      return next
    })

  const save = async () => {
    setSaving(true); setError('')
    try {
      const accept = proposals.filter(p => decided[p.id] === 'use').map(p => p.id)
      let applied = 0
      if (accept.length) {
        const res = await applyBillCoding({ finding_id: findingId, invoice_id: invoiceId, accept })
        applied = res.counts.applied
        if (res.failed.length) {
          // A write that did not land leaves the item open: closing it would
          // claim the bill is fixed when it is not.
          setError(`Couldn’t save: ${res.failed.map(f => `${f.id} (${f.error})`).join(', ')}`)
          if (applied) onSaved(applied)
          return
        }
      }
      const declined = proposals.some(p => decided[p.id] !== 'use')
      await setFindingStatus(findingId, declined
        ? { status: 'DISMISSED', reason: 'not_an_issue', suppress_scope: 'exact_key' }
        : { status: 'RESOLVED' })
      window.dispatchEvent(new CustomEvent(REVIEW_CHANGED_EVENT))
      onSaved(applied)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save')
    } finally {
      setSaving(false)
    }
  }

  const unpinned = proposals.filter(p => !hosts[p.id])
  const card = document.querySelector('.rc__layer .bm__card')?.getBoundingClientRect()
  const nUse = proposals.filter(p => decided[p.id] === 'use').length
  const nDone = proposals.length - pending.length

  const actionsFor = (p: Proposal) => {
    const d = decided[p.id]
    return (
      <span className="rc__fix-acts">
        <button type="button" className={`rc__fix-btn rc__fix-btn--use${d === 'use' ? ' rc__fix-btn--on' : ''}`}
                disabled={!p.writable} aria-pressed={d === 'use'}
                onClick={() => decide(p.id, 'use')} aria-label={`Use ${p.proposed}`} title="Use the suggestion">
          <Check size={13} strokeWidth={3} />
        </button>
        <button type="button" className={`rc__fix-btn rc__fix-btn--keep${d === 'keep' ? ' rc__fix-btn--on' : ''}`}
                aria-pressed={d === 'keep'}
                onClick={() => decide(p.id, 'keep')} aria-label="Keep current" title="Keep what the bill says">
          <X size={13} strokeWidth={3} />
        </button>
      </span>
    )
  }

  return (
    <>
      {proposals.map(p => {
        const h = hosts[p.id]
        if (!h) return null
        const d = decided[p.id]
        return (
          <span key={p.id} style={{ display: 'contents' }}>
            {createPortal(actionsFor(p), h.actions)}
            {createPortal(
              <div className={`rc__fix-box rc__fix-box--${d ?? 'pending'}${flash === p.id ? ' rc__fix-box--flash' : ''}`}>
                <div className="rc__fix-old">{p.current || 'Not set'}</div>
                <div className="rc__fix-line">
                  <span className="rc__fix-new">{p.proposed}</span>
                  {reasonFor(p) && (
                    <button
                      type="button"
                      className="rc__fix-info"
                      aria-label={reasonFor(p)}
                      onMouseEnter={e => showTip(reasonFor(p)!, e.currentTarget)}
                      onFocus={e => showTip(reasonFor(p)!, e.currentTarget)}
                      onMouseLeave={() => setTip(null)}
                      onBlur={() => setTip(null)}
                    >
                      <Info size={16} />
                    </button>
                  )}
                </div>
              </div>,
              h.box,
            )}
          </span>
        )
      })}

      {tip && (
        <div className="rc__fix-tip" role="tooltip"
             style={{ left: tip.x, bottom: window.innerHeight - tip.y + 8 }}>
          {tip.text}
        </div>
      )}

      {/* Next suggestion down the bill, until the last one. */}
      {card && nextIdx != null && (
        <button
          type="button"
          className="rc__fix-next"
          style={{ left: card.left + card.width / 2, bottom: window.innerHeight - card.bottom + 96 }}
          onClick={() => goTo(nextIdx)}
          aria-label={`Next suggestion, ${nextIdx + 1} of ${pinnedInOrder.length}`}
        >
          <ChevronDown size={18} strokeWidth={2.5} aria-hidden="true" />
          <span>Next suggestion · {nextIdx + 1} of {pinnedInOrder.length}</span>
        </button>
      )}

      {card && (
        <div className="rc__pin-bar" style={{ left: card.left + card.width / 2, bottom: window.innerHeight - card.bottom + 16 }}>
          {unpinned.map(p => (
            <div key={p.id} className="rc__pin-bar-row">
              <span className="rc__pin-bar-lbl">{p.label}</span>
              <span className="rc__pin-old">{p.current || 'not set'}</span>
              <ArrowRight size={12} aria-hidden="true" />
              <span className="rc__pin-new">{p.proposed}</span>
              <button type="button" className={`rc__pin-mini${decided[p.id] === 'use' ? ' rc__pin-mini--on' : ''}`}
                      disabled={!p.writable} onClick={() => decide(p.id, 'use')} aria-label="Use this">
                <Check size={12} />
              </button>
              <button type="button" className={`rc__pin-mini${decided[p.id] === 'keep' ? ' rc__pin-mini--on' : ''}`}
                      onClick={() => decide(p.id, 'keep')} aria-label="Keep current">
                <X size={12} />
              </button>
            </div>
          ))}
          <div className="rc__pin-bar-main">
            <span className="rc__pin-bar-count">
              {nDone} of {proposals.length} suggestion{proposals.length === 1 ? '' : 's'} decided
            </span>
            {error && <span className="rc__pin-bar-err" role="alert">{error}</span>}
            <button type="button" className="rc__pin-later" disabled={saving} onClick={onSkip}>Decide later</button>
            <button type="button" className="rc__pin-save" disabled={saving || nDone === 0} onClick={() => void save()}>
              {saving ? 'Saving…' : nUse ? `Save ${nUse} change${nUse === 1 ? '' : 's'}` : 'Save'}
            </button>
          </div>
        </div>
      )}
    </>
  )
}
