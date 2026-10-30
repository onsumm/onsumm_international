/**
 * The wait while a payment settles, as the Onsumm mark in motion.
 *
 * Geometry, gradients and the four electron glyphs are lifted from the product
 * deck's atom (onsumm-product-deck.html) so this is the logo animating, not a
 * lookalike: nucleus at r=120, three orbits at rx 212 / ry 80 rotated −32°, 28°
 * and 90°, and the banking / cashflow / insight / bills diamonds riding them.
 *
 * Each electron follows its orbit with SVG animateMotion along that orbit's own
 * ellipse. Rotating the orbit and spinning a child would need a non-uniform
 * scale to make the circle elliptical, which shears the glyph as it travels;
 * path-following keeps every diamond upright and undistorted.
 *
 * The bank is the one thing on screen that knows when this ends, so the copy
 * never claims progress it cannot measure — it reports elapsed time and states
 * what happens at the cut-off.
 */

import { useEffect, useState } from 'react'
import { formatMoney } from '../../ui'

export interface PaymentProcessingProps {
  /** Who is being paid, for the line under the mark. */
  vendorName: string
  amount: number
  /** How many bills this payment settles. */
  billCount: number
  /** Epoch ms the OTP was accepted — elapsed time counts from here. */
  startedAt: number
  /** Total wait allowed before the payment is reported as still processing. */
  budgetMs: number
  /** True once the bank has said PENDING and we are polling for a resolution. */
  awaitingBank: boolean
}

/** One diamond electron: the deck's chip, with the glyph centred on the origin. */
function Electron({ glyph }: { glyph: React.ReactNode }) {
  return (
    <g>
      <rect x="-27" y="-27" width="54" height="54" rx="14" transform="rotate(45)" fill="url(#payDia)" stroke="rgb(255 255 255 / 32%)" strokeWidth="1.2" />
      <rect x="-27" y="-27" width="54" height="27" rx="14" transform="rotate(45)" fill="url(#payDiaHi)" />
      {glyph}
    </g>
  )
}

const GLYPHS = {
  // Classical bank: pediment, colonnade, plinth.
  banking: (
    <g transform="translate(-16.2,-17.4) scale(1.35)" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.8 9.8 12 4.2l9.2 5.6" /><path d="M4.6 11.6h14.8" />
      <path d="M7.5 19.2v-7.6M12 19.2v-7.6M16.5 19.2v-7.6" />
      <path d="M4.6 19.2h14.8M2.8 21.6h18.4" />
    </g>
  ),
  // Rupee flanked by money-out (up) and money-in (down).
  cashflow: (
    <g transform="translate(-16.2,-16.2) scale(1.35)" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3.8 19.4V4.6M1 7.4 3.8 4.6l2.8 2.8" />
      <path d="M20.2 4.6v14.8M17.4 16.6l2.8 2.8 2.8-2.8" />
      <path d="M9 7.8h6M9 10.4h6M13.8 7.8c0 2.7-2 3.8-4.8 3.8M9 11.6l5.4 4.8" />
    </g>
  ),
  // Bars with a trendline.
  insight: (
    <g transform="translate(-16.2,-17.7) scale(1.35)" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3.2 20.6h17.6" />
      <path d="M6.8 20.6v-4.2M12 20.6v-7M17.2 20.6v-9.8" />
      <path d="M5.6 13.6 10.2 9.2l3.4 2.8 5.4-5.8" />
      <path d="M16.6 5.6h2.8v2.8" />
    </g>
  ),
  // Torn receipt with line items.
  bills: (
    <g transform="translate(-16.2,-16.3) scale(1.35)" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5.6 2.8h12.8v18.6l-2.13-1.7-2.13 1.7-2.14-1.7-2.13 1.7-2.13-1.7-2.14 1.7z" />
      <path d="M8.6 7.6h6.8M8.6 10.6h6.8M8.6 13.6h4.2" />
    </g>
  ),
}

/** The deck's ellipse, as a path animateMotion can follow. */
const ORBIT_PATH = 'M 28,240 A 212,80 0 0 1 452,240 A 212,80 0 0 1 28,240'

/**
 * Which glyph rides which orbit, at what speed and phase.
 *
 * `tilt` matches the deck's three orbit rotations. `counter` cancels that tilt on
 * the glyph so a receipt never travels upside down. Durations are deliberately
 * unequal — matched speeds would lock the four into a pattern that reads as a
 * single spinning rig rather than four independent things in motion.
 */
const ORBITS = [
  { key: 'banking',  tilt: -32, dur: 7.5, begin: 0,    glyph: GLYPHS.banking },
  { key: 'cashflow', tilt: 28,  dur: 9,   begin: -2.5, glyph: GLYPHS.cashflow },
  { key: 'insight',  tilt: 28,  dur: 9,   begin: -6.5, glyph: GLYPHS.insight },
  { key: 'bills',    tilt: -32, dur: 7.5, begin: -3.8, glyph: GLYPHS.bills },
] as const

export function PaymentProcessing({
  vendorName, amount, billCount, startedAt, budgetMs, awaitingBank,
}: PaymentProcessingProps) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // SMIL ignores prefers-reduced-motion, so the orbits are simply not animated.
  const reduced =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
  const budget = Math.round(budgetMs / 1000)

  return (
    <div className="pay-card pay-proc" role="status" aria-live="polite">
      <div className="pay-proc__stage">
        <svg className="pay-proc__atom" viewBox="0 0 480 480" aria-hidden="true">
          <defs>
            <radialGradient id="payNuc" cx="38%" cy="32%" r="72%">
              <stop offset="0%" stopColor="#EAF5FF" /><stop offset="35%" stopColor="#A9D0F5" />
              <stop offset="68%" stopColor="#4E93DE" /><stop offset="100%" stopColor="#164E90" />
            </radialGradient>
            <radialGradient id="payGlow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#2E7BD6" stopOpacity=".45" />
              <stop offset="100%" stopColor="#2E7BD6" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="payDia" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#7CC3F8" /><stop offset="100%" stopColor="#1462C4" />
            </linearGradient>
            <linearGradient id="payDiaHi" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ffffff" stopOpacity=".5" />
              <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
            </linearGradient>
            <filter id="payLift" x="-70%" y="-70%" width="240%" height="240%">
              <feDropShadow dx="0" dy="6" stdDeviation="9" floodColor="#04101F" floodOpacity=".45" />
            </filter>
          </defs>

          <circle cx="240" cy="240" r="190" fill="url(#payGlow)" className="pay-proc__halo" />

          {/* The three orbit paths, drawn */}
          <g fill="none" stroke="#7FB6EE" strokeOpacity=".4" strokeWidth="1.4">
            <ellipse cx="240" cy="240" rx="212" ry="80" transform="rotate(-32 240 240)" />
            <ellipse cx="240" cy="240" rx="212" ry="80" transform="rotate(28 240 240)" />
            <ellipse cx="240" cy="240" rx="212" ry="80" transform="rotate(90 240 240)" />
          </g>

          <g className="pay-proc__core">
            <circle cx="240" cy="240" r="120" fill="url(#payNuc)" />
            <ellipse cx="202" cy="194" rx="42" ry="27" fill="#ffffff" opacity=".26" />
          </g>

          {/* Electrons: each rides its own orbit, upright throughout */}
          {ORBITS.map(o => (
            <g key={o.key} transform={`rotate(${o.tilt} 240 240)`} filter="url(#payLift)">
              <g>
                <g transform={`rotate(${-o.tilt})`}>
                  <Electron glyph={o.glyph} />
                </g>
                {!reduced && (
                  <animateMotion
                    dur={`${o.dur}s`}
                    begin={`${o.begin}s`}
                    repeatCount="indefinite"
                    path={ORBIT_PATH}
                    calcMode="linear"
                  />
                )}
              </g>
            </g>
          ))}
        </svg>
      </div>

      <h2 className="pay-proc__title">
        {awaitingBank ? 'The bank is processing this transfer' : 'Releasing the payment'}
      </h2>
      <p className="pay-proc__sub">
        {formatMoney(amount, { precise: true })} to {vendorName}
        {billCount > 1 ? ` · ${billCount} bills` : ''}
      </p>
      <p className="pay-proc__meta">
        {elapsed}s elapsed · don’t close this page.
        {' '}After {budget}s it moves to Processing and you can check on it from the receipt.
      </p>
    </div>
  )
}
