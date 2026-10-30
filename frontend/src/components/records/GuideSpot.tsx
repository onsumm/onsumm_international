import { useEffect, useState } from 'react'
import type { CardGuide } from './recordLinks'

const TIP_W = 280
const GAP = 10
const PAD = 6
/** How long to wait for the control to render (tabs load their data first). */
const FIND_FOR_MS = 6000
/** How long the card must stay unchanged before a fallback control is used. */
const SETTLE_MS = 450

function findTarget(targets: string[]): { el: HTMLElement; key: string } | null {
  for (const key of targets) {
    const el = document.querySelector<HTMLElement>(`[data-guide="${key}"]`)
    // A display:none control (a collapsed section) is not something to point at.
    if (el && el.getClientRects().length) return { el, key }
  }
  return null
}

/**
 * A ring around the control that fixes the problem the card was opened for,
 * with one line saying what to do. Done when that control is clicked, or on
 * "Got it". Draws nothing if the control never appears — a guide that points
 * at empty space is worse than none.
 */
export default function GuideSpot({ guide, onDone }: { guide: CardGuide; onDone: () => void }) {
  const [el, setEl] = useState<HTMLElement | null>(null)
  const [foundKey, setFoundKey] = useState<string | null>(null)
  const [rect, setRect] = useState<DOMRect | null>(null)
  // Bumped when the control leaves the page (e.g. a read-only field swapped
  // for its edit input), so the search runs again and the guide follows it.
  const [search, setSearch] = useState(0)
  const targetsKey = guide.targets.join('|')

  // Find it — now, or once the tab has loaded and rendered it.
  //
  // The first-choice control is pointed at the moment it exists. A fallback
  // only once the card has gone quiet: a tab draws its header ("+ Add
  // Account") while its list is still a loading skeleton, and pointing at the
  // fallback then — with "no bank account" — before jumping to "Retry
  // Verification" a beat later reads as broken. Quiet means no DOM change for
  // SETTLE_MS, i.e. the list has rendered and the first choice really is absent.
  useEffect(() => {
    let committed = -1
    let settle = 0
    const commit = (t: { el: HTMLElement; key: string }, rank: number) => {
      committed = rank
      setEl(t.el)
      setFoundKey(t.key)
      // A control below the fold of the card's own scroller is no use pointed at.
      t.el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
    const look = () => {
      const t = findTarget(guide.targets)
      if (!t) return false
      const rank = guide.targets.indexOf(t.key)
      if (rank === 0) {
        window.clearTimeout(settle)
        if (committed !== 0) commit(t, 0)
        return true
      }
      if (committed === -1 || rank < committed) {
        window.clearTimeout(settle)
        settle = window.setTimeout(() => {
          const again = findTarget(guide.targets)
          if (again) commit(again, guide.targets.indexOf(again.key))
        }, SETTLE_MS)
      }
      return false
    }
    if (look()) return
    const obs = new MutationObserver(() => { if (look()) obs.disconnect() })
    obs.observe(document.body, { childList: true, subtree: true })
    const giveUp = window.setTimeout(() => obs.disconnect(), FIND_FOR_MS)
    return () => { obs.disconnect(); window.clearTimeout(giveUp); window.clearTimeout(settle) }
  }, [targetsKey, search])

  // Follow it while the card scrolls, animates in or resizes. A frame loop is
  // the one thing that also tracks the card's own open animation.
  useEffect(() => {
    if (!el) return
    let raf = 0
    let last = ''
    const tick = () => {
      if (!el.isConnected) { setEl(null); setRect(null); setSearch(n => n + 1); return }
      const r = el.getBoundingClientRect()
      const sig = `${r.left}|${r.top}|${r.width}|${r.height}`
      if (sig !== last) { last = sig; setRect(r) }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    // Using the control is the point of the guide; doing so ends it. Pointing
    // at a plain field (a read-only value) is not "using" anything, so a click
    // there leaves the guide up.
    const usable = el.matches('button, a, input, select, textarea, [role="button"]')
    if (usable) el.addEventListener(el.matches('input, select, textarea') ? 'focus' : 'click', onDone, { once: true })
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('click', onDone)
      el.removeEventListener('focus', onDone)
    }
  }, [el, onDone])

  if (!el || !rect || rect.width === 0) return null

  const below = rect.bottom + GAP + 90 < window.innerHeight
  const left = Math.min(
    Math.max(rect.left + rect.width / 2 - TIP_W / 2, 12),
    window.innerWidth - TIP_W - 12,
  )
  const arrowLeft = Math.min(Math.max(rect.left + rect.width / 2 - left, 16), TIP_W - 16)

  return (
    <>
      <div
        className="rc__guide-ring"
        aria-hidden="true"
        style={{
          left: rect.left - PAD, top: rect.top - PAD,
          width: rect.width + PAD * 2, height: rect.height + PAD * 2,
        }}
      />
      <div
        className={`rc__guide-tip rc__guide-tip--${below ? 'below' : 'above'}`}
        role="status"
        style={{
          left, width: TIP_W,
          ...(below ? { top: rect.bottom + PAD + GAP } : { bottom: window.innerHeight - rect.top + PAD + GAP }),
          ['--rc-arrow-left' as string]: `${arrowLeft}px`,
        }}
      >
        <p className="rc__guide-msg">{(foundKey && guide.messages?.[foundKey]) || guide.message}</p>
        <button type="button" className="rc__guide-done" onClick={onDone}>Got it</button>
      </div>
    </>
  )
}
