import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { MONTHS, MONTHS_FULL, compactINR, money, type Forecast, type MonthTotal } from './vendorStats'

/**
 * Monthly billed amounts, with this month's running total inside its expected
 * total and next month as a forecast bar. Bars only: each month is a total, and
 * the chart's job is comparing them.
 *
 * Colours come from classes in vendorCard.css (tokens), never from attributes,
 * so the chart follows the app's palette.
 */

interface Col {
  kind: 'actual' | 'partial' | 'forecast'
  ym: string
  year: number
  month: number
  value: number
  m?: MonthTotal
  prev?: MonthTotal
  expected?: number
  band?: number
}

function niceStep(raw: number): number {
  const p = 10 ** Math.floor(Math.log10(raw || 1)), n = raw / p
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p
}

function barPath(x: number, yTop: number, w: number, yBase: number, r = 5): string {
  const h = yBase - yTop
  if (h <= 0.5) return ''
  r = Math.min(r, h, w / 2)
  return `M${x},${yBase}V${yTop + r}Q${x},${yTop} ${x + r},${yTop}H${x + w - r}Q${x + w},${yTop} ${x + w},${yTop + r}V${yBase}Z`
}

export default function SpendChart({ series, forecast, months = 12, height = 240, selected, onSelect, billsPerMonth, average }: {
  series: MonthTotal[]
  forecast: Forecast
  months?: number
  height?: number
  /** A month (ym) to emphasise; the others recede. */
  selected?: string | null
  onSelect?: (ym: string) => void
  billsPerMonth?: number
  /**
   * The monthly average to draw, so the dashed line is the headline's figure.
   * Without it, the average of the months shown (which moves with 6M/24M).
   */
  average?: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)
  const uid = useId().replace(/:/g, '')
  // Bars grow in once, when the chart first has a size.
  const [animate] = useState(() => !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)

  useEffect(() => {
    if (!host.current) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)))
    ro.observe(host.current)
    return () => ro.disconnect()
  }, [])

  const fc = forecast
  let cols: Col[] = series.map((m, i) => ({
    kind: m.partial ? 'partial' : 'actual', ym: m.ym, year: m.year, month: m.month, value: m.total, m, prev: series[i - 1],
    expected: m.partial && fc.ok ? fc.expected![0] : undefined, band: m.partial && fc.ok ? fc.band![0] : undefined,
  }))
  cols = cols.slice(-months)
  if (fc.ok && series.length) {
    const last = series[series.length - 1]
    const next = new Date(last.year, last.month + 1, 1)
    cols.push({ kind: 'forecast', ym: `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`, year: next.getFullYear(), month: next.getMonth(), value: fc.expected![1], band: fc.band![1] })
  }

  const W = Math.max(260, width), H = height
  const padL = 44, padR = W < 480 ? 8 : 58, padT = 26, padB = 28
  const iw = W - padL - padR, ih = H - padT - padB
  const top = Math.max(1, ...cols.map(c => Math.max(c.value, c.expected != null ? c.expected + (c.band ?? 0) : 0, c.kind === 'forecast' ? c.value + (c.band ?? 0) : 0)))
  const step = niceStep(top / 4), yMax = Math.ceil((top * 1.06) / step) * step
  const y = (v: number) => padT + ih - (v / yMax) * ih
  const base = y(0)
  const cw = cols.length ? iw / cols.length : iw
  const bw = Math.min(30, cw * 0.54)
  const actuals = cols.filter(c => c.kind === 'actual')
  const avg = average ?? (actuals.length ? actuals.reduce((s, c) => s + c.value, 0) / actuals.length : 0)
  const lastActual = actuals[actuals.length - 1]
  const everyOther = cw < 32
  const ticks: number[] = []
  for (let v = 0; v <= yMax + 1; v += step) ticks.push(v)

  const whisker = (cx: number, hi: number, lo: number) => (
    <g className="vcs__wh" pointerEvents="none">
      <line x1={cx} x2={cx} y1={y(hi)} y2={y(Math.max(0, lo))} />
      <line x1={cx - 5} x2={cx + 5} y1={y(hi)} y2={y(hi)} />
      <line x1={cx - 5} x2={cx + 5} y1={y(Math.max(0, lo))} y2={y(Math.max(0, lo))} />
    </g>
  )

  const tip = hover != null ? cols[hover] : null

  // Keyboard: arrows walk the months (showing the tooltip), Enter picks one.
  const onKey = (e: KeyboardEvent) => {
    if (!cols.length) return
    const at = hover ?? cols.length - 1
    const go = (i: number) => { e.preventDefault(); setHover(Math.max(0, Math.min(cols.length - 1, i))) }
    if (e.key === 'ArrowRight') go(hover == null ? at : at + 1)
    else if (e.key === 'ArrowLeft') go(hover == null ? at : at - 1)
    else if (e.key === 'Home') go(0)
    else if (e.key === 'End') go(cols.length - 1)
    else if ((e.key === 'Enter' || e.key === ' ') && hover != null) { e.preventDefault(); if (cols[hover].kind !== 'forecast') onSelect?.(cols[hover].ym) }
    else if (e.key === 'Escape') setHover(null)
  }
  // A label that says something: the range, the peak and the latest month.
  const peak = actuals.reduce<Col | null>((m, c) => (!m || c.value > m.value ? c : m), null)
  const label = cols.length
    ? `Billed per month, ${MONTHS[cols[0].month]} ${cols[0].year} to ${MONTHS[cols[cols.length - 1].month]} ${cols[cols.length - 1].year}.`
      + (peak ? ` Highest ${money(peak.value)} in ${MONTHS_FULL[peak.month]} ${peak.year}.` : '')
      + (lastActual ? ` Latest full month ${money(lastActual.value)}.` : '')
      + (fc.ok ? ` Next month forecast about ${money(fc.expected![1])}.` : '')
      + (actuals.length ? ` Monthly average ${money(avg)}.` : '')
      + (onSelect ? ' Use the arrow keys to read each month, Enter to pick it.' : '')
    : 'Billed per month'
  const spoken = tip ? `${MONTHS_FULL[tip.month]} ${tip.year}: ${tip.kind === 'forecast' ? 'forecast about ' : tip.kind === 'partial' ? 'so far ' : ''}${money(tip.value)}` : ''
  const tipTop = tip ? Math.min(y(tip.value), tip.expected != null ? y(tip.expected + (tip.band ?? 0)) : Infinity, tip.kind === 'forecast' ? y(tip.value + (tip.band ?? 0)) : Infinity) : 0

  return (
    <div className="vcs" ref={host}>
      <span className="vc__sr" aria-live="polite">{spoken}</span>
      {width > 0 && (
        <svg width={W} height={H} role="img" aria-label={label} tabIndex={0}
             onKeyDown={onKey} onBlur={() => setHover(null)}>
          <defs>
            <linearGradient id={`g${uid}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="vcs__stop1" /><stop offset="1" className="vcs__stop2" /></linearGradient>
            <linearGradient id={`d${uid}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" className="vcs__dim1" /><stop offset="1" className="vcs__dim2" /></linearGradient>
            <pattern id={`h${uid}`} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="5" height="5" className="vcs__hatchbg" /><line x1="0" y1="0" x2="0" y2="5" className="vcs__hatch" strokeWidth="1.6" />
            </pattern>
          </defs>
          {ticks.map(v => (
            <g key={v}>
              <line className={v === 0 ? 'vcs__base' : 'vcs__grid'} x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} />
              <text className="vcs__ax" x={padL - 10} y={y(v) + 4} textAnchor="end">{v === 0 ? '0' : compactINR(v, 2)}</text>
            </g>
          ))}
          {cols.map((c, i) => {
            const cx = padL + cw * i + cw / 2, x = cx - bw / 2
            const dim = selected != null && c.ym !== selected
            const grow = animate ? { className: 'vcs__grow', style: { animationDelay: `${80 + i * 40}ms` } } : {}
            const labelled = selected != null ? c.ym === selected : c === lastActual
            const showLbl = !everyOther || i % 2 === (cols.length - 1) % 2
            const yr = (c.month === 0 || i === 0) && cw >= 44 ? ` ’${String(c.year).slice(2)}` : ''
            return (
              <g key={c.ym} className={`vcs__col${hover === i ? ' is-hover' : ''}`}
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                 // A tap has no hover: it shows the month's numbers as well as picking it.
                 onClick={() => { setHover(i); if (c.kind !== 'forecast') onSelect?.(c.ym) }}>
                <rect className="vcs__hl" x={padL + cw * i + 2} y={padT - 10} width={Math.max(0, cw - 4)} height={ih + 10} rx={8} />
                {c.kind === 'actual' && <path {...grow} d={barPath(x, y(c.value), bw, base)} fill={`url(#${dim ? 'd' : 'g'}${uid})`} />}
                {c.kind === 'partial' && (
                  <>
                    {c.expected != null && <path {...grow} className={`vcs__fc ${grow.className ?? ''}`} d={barPath(x, y(c.expected), bw, base)} fill={`url(#h${uid})`} />}
                    <path {...grow} d={barPath(x + (c.expected != null ? 3 : 0), y(c.value), bw - (c.expected != null ? 6 : 0), base, c.expected != null ? 3 : 5)}
                          fill={`url(#${dim ? 'd' : 'g'}${uid})`} opacity={c.expected != null ? 1 : 0.6} />
                    {c.expected != null && whisker(cx, c.expected + (c.band ?? 0), c.expected - (c.band ?? 0))}
                  </>
                )}
                {c.kind === 'forecast' && (
                  <>
                    <path {...grow} className={`vcs__fc ${grow.className ?? ''}`} d={barPath(x, y(c.value), bw, base)} fill={`url(#h${uid})`} />
                    {whisker(cx, c.value + (c.band ?? 0), c.value - (c.band ?? 0))}
                  </>
                )}
                {W >= 520 && labelled && c.kind === 'actual' && (
                  <text className="vcs__val" x={cx} y={y(c.value) - 8} textAnchor="middle">{compactINR(c.value, 1)}</text>
                )}
                {W >= 520 && c.kind === 'partial' && (
                  <text className="vcs__val vcs__val--mute" x={cx} y={y(c.expected != null ? c.expected + (c.band ?? 0) : c.value) - 8} textAnchor="middle">
                    {c.expected != null ? 'so far' : `${compactINR(c.value, 1)} so far`}
                  </text>
                )}
                {c.kind === 'forecast' && (
                  <text className="vcs__val vcs__val--fc" x={cx} y={y(c.value + (c.band ?? 0)) - 8} textAnchor="middle">~{compactINR(c.value, 1)}</text>
                )}
                {showLbl && (
                  <text className={`vcs__ax${c.kind === 'forecast' ? ' vcs__ax--fc' : ''}`} x={cx} y={H - 8} textAnchor="middle">{MONTHS[c.month]}{yr}</text>
                )}
                <rect className="vcs__hit" x={padL + cw * i} y={padT - 10} width={cw} height={ih + padB + 10} />
              </g>
            )
          })}
          {actuals.length > 0 && (
            <>
              <line className="vcs__avg" x1={padL} x2={W - padR} y1={y(avg)} y2={y(avg)} pointerEvents="none" />
              {padR > 40 && (
                <g className="vcs__avgpill" pointerEvents="none">
                  <rect x={W - padR + 6} y={y(avg) - 10} width={50} height={20} rx={10} />
                  <text x={W - padR + 31} y={y(avg) + 4} textAnchor="middle">{compactINR(avg, 1)}</text>
                </g>
              )}
            </>
          )}
        </svg>
      )}
      {tip && (
        <div className="vcs__tip" style={{ left: Math.min(Math.max(padL + cw * hover! + cw / 2, 100), W - 100), top: Math.max(tipTop - 12, 0) }}>
          <b>{MONTHS_FULL[tip.month]} {tip.year}</b>
          {tip.kind === 'forecast' ? (
            <>
              <span className="m"> · forecast</span>
              <div className="r"><span className="m">Expected</span><span>{money(tip.value)}</span></div>
              <div className="r"><span className="m">Likely range</span><span>{compactINR(Math.max(0, tip.value - (tip.band ?? 0)), 1)} – {compactINR(tip.value + (tip.band ?? 0), 1)}</span></div>
              {billsPerMonth ? <div className="r"><span className="m">Bills expected</span><span>~{Math.round(billsPerMonth)}</span></div> : null}
            </>
          ) : (
            <>
              {tip.kind === 'partial' && <span className="m"> · in progress</span>}
              <div className="r"><span className="m">{tip.kind === 'partial' ? 'Billed so far' : 'Billed'}</span><span>{money(tip.value)}</span></div>
              <div className="r"><span className="m">Bills</span><span>{tip.m!.bills.length}</span></div>
              <div className="r"><span className="m">Paid · Open</span><span>{compactINR(tip.m!.paid)} · {compactINR(tip.m!.outstanding)}</span></div>
              {tip.kind === 'actual' && tip.prev && tip.prev.total > 0 && (
                <div className="r"><span className="m">vs {MONTHS[tip.prev.month]}</span><span>{tip.value >= tip.prev.total ? '+' : ''}{((tip.value / tip.prev.total - 1) * 100).toFixed(1)}%</span></div>
              )}
              {tip.expected != null && <div className="r"><span className="m">Expected by month end</span><span>{compactINR(tip.expected, 1)}</span></div>}
            </>
          )}
        </div>
      )}
    </div>
  )
}
