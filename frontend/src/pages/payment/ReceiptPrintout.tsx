/**
 * The receipt, printing.
 *
 * A payment is the one irreversible thing this app does, and the moment it lands
 * deserves to feel like it happened rather than like a route change. So a settled
 * payment prints its slip immediately, and that slip IS the receipt view — not a
 * flourish on the way out.
 *
 * It therefore never dismisses itself. The money has moved; what happens next is
 * the user's decision, so the actions appear beneath the paper once it is out and
 * the slip stays on screen until one is chosen. Clicking the backdrop finishes the
 * print rather than closing, because a mis-click must not skip past a receipt.
 *
 * It reads from receiptContent.ts like the preview and the PDF do, so the slip
 * cannot quote an amount or a reference the filed document disagrees with.
 *
 * Realism comes from the mechanics, not the gloss:
 *   - the sheet travels. It starts wholly inside the printer and slides down out of
 *     the slot, BOTTOM EDGE FIRST, so the header is the last thing to clear the
 *     slit. An earlier version held the paper still and unveiled it downward from
 *     the header, which reads as a curtain rather than a print — a slip does not
 *     stand still while its content appears on it.
 *   - the feed is linear and composited: a feed roller turns at constant speed, so
 *     easing it would read as a slide. An earlier version used steps() plus a
 *     sub-pixel shudder for "mechanical" character; together with a per-frame
 *     drop-shadow and a 1px paper texture that moired against sub-pixel movement,
 *     it just looked like a dropped frame rate. Motion is one translate3d now.
 *   - the free paper sways once the feed stops
 *   - the torn edge is the LEADING edge, so it is there from the first frame
 *
 * prefers-reduced-motion collapses every duration to ~0 via tokens.css, and each
 * animation ends on its final state (fill both), so that path lands on a fully
 * printed slip instead of a blank window.
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { formatMoney } from '../../ui'
import { receiptRows, receiptTotal, statusWord, type ReceiptData } from './receiptContent'

export interface ReceiptPrintoutProps {
  data: ReceiptData
  /**
   * What the user may do next. Rendered under the paper once printing settles —
   * held back until then so the buttons cannot be hit while the slip is still
   * moving, and supplied by the caller so the printer knows nothing about the
   * payment flow.
   */
  actions?: ReactNode
  /** Fired once the slip is fully out. Purely informational. */
  onPrinted?: () => void
}

/** Feed speed. Duration follows the slip's length, so a three-bill receipt takes
 *  proportionally longer to come out than a one-bill one. */
const FEED_PX_PER_SEC = 540
const MIN_FEED_MS = 420
const SETTLE_MS = 620
/** Used when the paper cannot be measured, so the print always terminates. */
const FALLBACK_H = 420

export function ReceiptPrintout({ data, actions, onPrinted }: ReceiptPrintoutProps) {
  const paperRef = useRef<HTMLDivElement | null>(null)
  const [height, setHeight] = useState(0)
  const [done, setDone] = useState(false)
  const finished = useRef(false)

  // Measure before paint: the feed animates to an explicit pixel height, since
  // `height: auto` is not animatable and the slip's length depends on how many
  // bills were settled.
  //
  // A zero measurement must still yield a finite print. An unlaid-out container
  // (or jsdom, which reports scrollHeight 0) previously left height at 0, which
  // skipped the timers below and stranded the overlay open with no way out but
  // the click-to-skip — so fall back to a nominal slip length instead.
  useLayoutEffect(() => {
    const measured = paperRef.current?.scrollHeight ?? 0
    setHeight(measured > 0 ? measured : FALLBACK_H)
  }, [])

  const feedMs = Math.max(MIN_FEED_MS, Math.round((height / FEED_PX_PER_SEC) * 1000))

  // Jump to the finished state. Skipping ends the *animation*, never the receipt:
  // the slip and its actions stay put, so an impatient click cannot dismiss the
  // record of a payment that has already left the building.
  const finish = () => {
    if (finished.current) return
    finished.current = true
    setDone(true)
    onPrinted?.()
  }

  useEffect(() => {
    if (!height) return
    const t = setTimeout(finish, feedMs)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, feedMs])

  const rows = receiptRows(data)
  const total = receiptTotal(data.results)

  return (
    // Clicking the backdrop fast-forwards the feed. It deliberately does NOT close:
    // this is the receipt, and the only ways out are the actions below it.
    <div
      className="rcpt-stage"
      role="dialog"
      aria-label={done ? 'Payment receipt' : 'Printing payment receipt'}
      onClick={finish}
      title={done ? undefined : 'Click to finish printing'}
    >
      <div className="rcpt-machine">
        <div className="rcpt-printer">
          <span className={`rcpt-led${done ? ' rcpt-led--idle' : ''}`} />
          <span className="rcpt-printer__name">ONSUMM</span>
          <span className="rcpt-slit" />
        </div>

        {/* Three nested elements, one animation each, because swapping an
            animation list mid-flight restarts every animation on that element.
            Outer sways, middle clips and shudders, inner (the sheet) travels. */}
        <div className={`rcpt-sway${done ? ' rcpt-sway--settle' : ''}`}>
          <div
            // The slot is only opened once the sheet has been measured: its height
            // is the slip's length, and the feed animates against it. Mounted at
            // --rcpt-h: 0px the travel resolved to nothing and the slip never
            // appeared at all.
            className={`rcpt-window${height > 0 ? ' rcpt-window--feed' : ''}${done ? ' rcpt-window--done' : ''}`}
            style={{
              ['--rcpt-h' as string]: `${height}px`,
              ['--rcpt-feed' as string]: `${feedMs}ms`,
            }}
          >
          <div className="rcpt-paper" ref={paperRef}>
            {/* Continuous roll. The next form is still on the roll INSIDE the
                printer, so its head and the tear line sit above this slip, nearest
                the slot — that is the paper you would grip to tear along.
                The perforation is punched with a mask so the backdrop shows through
                the holes; painted dots would read as printed ink rather than as
                paper that is missing. */}
            <div className="rcpt-next" aria-hidden="true" />
            <div className="rcpt-perf" aria-hidden="true" />
            <div className="rcpt-doc">
            <div className="rcpt-brand">ONSUMM</div>
            <div className="rcpt-kind">PAYMENT RECEIPT</div>
            <div className="rcpt-rule" />

            {data.demo && (
              <>
                <div className="rcpt-demo">DEMO — NO REAL FUNDS MOVED</div>
                <div className="rcpt-rule" />
              </>
            )}

            <div className="rcpt-total">
              <span>AMOUNT PAID</span>
              <span className="rcpt-total__amt">{formatMoney(total, { precise: true })}</span>
            </div>
            <div className="rcpt-rule" />

            {rows.map(r => (
              <div className="rcpt-line" key={r.label}>
                <span className="rcpt-line__k">{r.label}</span>
                <span className="rcpt-line__v">{r.value}</span>
              </div>
            ))}

            <div className="rcpt-rule" />
            <div className="rcpt-head">BILLS SETTLED</div>
            {data.results.map(r => (
              <div className="rcpt-bill" key={r.disbursement_id}>
                <div className="rcpt-line">
                  <span className="rcpt-line__k">{r.billLabel || r.invoice_id.slice(0, 8)}</span>
                  <span className="rcpt-line__v">{formatMoney(r.amount, { precise: true })}</span>
                </div>
                <div className="rcpt-sub">
                  {statusWord(r.status)}
                  {(r.bank_rrn ?? r.tran_ref_no) ? ` · REF ${r.bank_rrn ?? r.tran_ref_no}` : ''}
                </div>
              </div>
            ))}

            <div className="rcpt-rule" />
            <div className="rcpt-foot">NOT A BANK STATEMENT</div>
            <div className="rcpt-foot rcpt-foot--dim">
              GENERATED FROM THE PAYMENT RECORD
            </div>
            <div className="rcpt-stars">* * *</div>
            </div>
          </div>
          </div>
        </div>

        {/* stopPropagation so pressing a button does not also fire the backdrop's
            fast-forward. Mounted only when done, so the choice appears with the
            finished slip rather than competing with the moving paper. */}
        {done && actions && (
          <div className="rcpt-actions" onClick={e => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
    </div>
  )
}
