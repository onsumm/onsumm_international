/**
 * Reports — shared chart primitives.
 *
 * Extracted from ReportsPage so the same marks can be used by the Dashboard's
 * progression panel. Nothing here knows about bills or periods: it takes labelled
 * numbers and draws them, which is why one ColumnChart serves spend (rupees),
 * turnaround (days, stacked by desk) and category mix (percent, fixed domain).
 *
 * Colour arrives through `color` on a wrapper class, so every mark is
 * `currentColor` and a series is recoloured in exactly one place. Styles live in
 * reports.css; a consumer outside the Reports page wraps its content in
 * `.rp .rp--embed` so the scoped custom properties resolve.
 */
import React, { useRef, useState } from 'react'
import { AlertCircle, Minus, TrendingDown, TrendingUp } from 'lucide-react'
import { delta, type Bucket } from './reportSeries'
import { AXIS_FMT, FULL_FMT, clamp, fmtC, niceCeil, type Unit } from './reportFormat'

export function Empty({ title, sub }: { title: string; sub: string }) {
  return (
    <div className="rp__empty">
      <AlertCircle size={34} />
      <div className="rp__empty-title">{title}</div>
      <div className="rp__empty-sub">{sub}</div>
    </div>
  )
}

export type Band = { label: string; cls: string; values: number[]; amounts?: number[] }
export type Overlay = { label: string; cls: string; values: (number | null)[]; unit: Unit; axis: 'left' | 'right' }

/**
 * One column per period, optionally stacked, with an optional overlay line on
 * either axis. Handles negative values against a zero rule, so the payment
 * buffer chart (which can go below zero when bills are approved after they were
 * already due) is the same component as the spend chart.
 */
export function ColumnChart({
  buckets,
  bands,
  overlay,
  unit,
  height = 220,
  domain,
  running = true,
}: {
  buckets: Bucket[]
  bands: Band[]
  overlay?: Overlay
  unit: Unit
  height?: number
  domain?: [number, number]
  running?: boolean
}) {
  const [at, setAt] = useState<number | null>(null)
  // Where the drawing sits inside the box: the SVG keeps its aspect ratio, so on a
  // wide box it is scaled to the height and centred, not stretched edge to edge.
  const [fit, setFit] = useState({ left: 0, scale: 0 })
  const boxRef = useRef<HTMLDivElement>(null)

  const n = buckets.length
  const W = 1000
  const padL = 58
  const padR = overlay?.axis === 'right' ? 48 : 14
  const padT = 14
  const padB = 26
  const plotW = W - padL - padR
  const plotH = height - padT - padB
  const bandW = plotW / Math.max(n, 1)
  const colW = Math.min(bandW * 0.58, 44)

  const stackUp = buckets.map((_, i) => bands.reduce((s, b) => s + Math.max(b.values[i] ?? 0, 0), 0))
  const stackDown = buckets.map((_, i) => bands.reduce((s, b) => s + Math.min(b.values[i] ?? 0, 0), 0))
  const onLeft = overlay?.axis === 'left' ? overlay.values.filter((v): v is number => v != null) : []
  const minNeg = Math.min(...stackDown, 0)
  const hi = domain ? domain[1] : niceCeil(Math.max(...stackUp, ...onLeft, 0))
  const lo = domain ? domain[0] : minNeg < 0 ? -niceCeil(-minNeg) : 0

  const y = (v: number) => padT + plotH - ((v - lo) / (hi - lo || 1)) * plotH
  const colX = (i: number) => padL + bandW * i + bandW / 2
  const zeroY = y(0)

  // Continuous tracking, and it lands on pointer-down too.
  const track = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = boxRef.current?.getBoundingClientRect()
    if (!r || r.width === 0) return
    // viewBox is W × height and the SVG is drawn `height` px tall, so the
    // vertical fit is 1:1 and caps the scale once the box is wider than W.
    const scale = Math.min(r.width / W, 1)
    const left = (r.width - W * scale) / 2
    const i = Math.floor(((e.clientX - r.left - left) / scale - padL) / bandW)
    setFit({ left, scale })
    setAt(i >= 0 && i < n ? i : null)
  }

  const ticks = [0, 1, 2, 3].map(k => lo + ((hi - lo) * k) / 3)
  // A 13-week axis cannot fit 13 labels. Thin from the right so the newest
  // period is always the one that keeps its label.
  const showTick = (i: number) => n <= 8 || (n - 1 - i) % 2 === 0
  const tipX = at == null ? 0 : fit.left + colX(at) * fit.scale
  const boxW = boxRef.current?.clientWidth || 1
  const tipShift = tipX < boxW * 0.18 ? '0' : tipX > boxW * 0.82 ? '-100%' : '-50%'
  const rowsAt = (i: number) => bands.map((b, bi) => ({ ...b, value: b.values[i] ?? 0, bi })).filter(r => r.value !== 0)

  return (
    <div
      className="rp__chart"
      ref={boxRef}
      style={{ height }}
      onPointerMove={track}
      onPointerDown={track}
      onPointerLeave={() => setAt(null)}
    >
      <svg
        viewBox={`0 0 ${W} ${height}`}
        style={{ height }}
        role="img"
        aria-label={`${bands.map(b => b.label).join(', ')} across ${n} periods`}
      >
        {ticks.map(t => (
          <g key={t}>
            <line className="rp__grid-line" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text className="rp__axis" x={padL - 10} y={y(t) + 3} textAnchor="end">
              {AXIS_FMT[unit](t)}
            </text>
          </g>
        ))}
        {lo < 0 && <line className="rp__zero" x1={padL} x2={W - padR} y1={zeroY} y2={zeroY} />}

        {buckets.map((b, i) => {
          const isRunning = running && i === n - 1
          let up = 0
          let down = 0
          return (
            <g key={b.key}>
              {bands.map(bd => {
                const v = bd.values[i] ?? 0
                if (v === 0) return null
                let top: number
                let bottom: number
                if (v > 0) {
                  top = y(up + v)
                  bottom = y(up)
                  up += v
                } else {
                  top = y(down)
                  bottom = y(down + v)
                  down += v
                }
                return (
                  <rect
                    key={bd.label}
                    className={`rp__col ${bd.cls}${isRunning ? ' rp__col--running' : ''}${
                      at != null && at !== i ? ' rp__col--muted' : ''
                    }`}
                    x={colX(i) - colW / 2}
                    y={Math.min(top, bottom)}
                    width={colW}
                    height={Math.max(Math.abs(bottom - top), 1.5)}
                    rx={bands.length > 1 ? 0 : 3}
                    style={{ transformOrigin: `center ${zeroY}px`, animationDelay: `${i * 28}ms` }}
                  />
                )
              })}
            </g>
          )
        })}

        {overlay &&
          (() => {
            const oy = (v: number) =>
              overlay.axis === 'right' ? padT + plotH - (clamp(v, 0, 100) / 100) * plotH : y(v)
            // A gap in the data is drawn as a gap: a period with nothing to
            // average is not a period that averaged zero.
            const segs: [number, number][][] = []
            overlay.values.forEach((v, i) => {
              if (v == null) return
              const pt: [number, number] = [colX(i), oy(v)]
              const last = segs[segs.length - 1]
              if (last && overlay.values[i - 1] != null) last.push(pt)
              else segs.push([pt])
            })
            return (
              <g className={overlay.cls}>
                {segs.map((s, si) => (
                  <polyline key={si} className="rp__overlay" points={s.map(p => p.join(',')).join(' ')} />
                ))}
                {segs.flat().map((p, pi) => (
                  <circle key={pi} className="rp__overlay-dot" cx={p[0]} cy={p[1]} r={3} />
                ))}
                {overlay.axis === 'right' &&
                  [0, 50, 100].map(t => (
                    <text className="rp__axis" key={t} x={W - padR + 10} y={oy(t) + 3} textAnchor="start">
                      {AXIS_FMT[overlay.unit](t)}
                    </text>
                  ))}
              </g>
            )
          })()}

        {buckets.map((b, i) =>
          showTick(i) ? (
            <text className="rp__axis" key={b.key} x={colX(i)} y={height - 6} textAnchor="middle">
              {b.label}
            </text>
          ) : null,
        )}
      </svg>

      {at != null && (
        <div className="rp__tip" style={{ left: tipX, transform: `translateX(${tipShift})` }}>
          <div className="rp__tip-day">
            {buckets[at].full}
            {running && at === n - 1 ? ' · to date' : ''}
          </div>
          {rowsAt(at).length === 0 ? (
            <div className="rp__tip-row">
              <span>Nothing recorded</span>
            </div>
          ) : (
            rowsAt(at).map(r => (
              <div className={`rp__tip-row ${r.cls}`} key={r.label}>
                <div className="rp__swatch" />
                <span>{r.label}</span>
                <span className="rp__tip-val">
                  {FULL_FMT[unit](r.value)}
                  {r.amounts ? ` · ${fmtC(r.amounts[at])}` : ''}
                </span>
              </div>
            ))
          )}
          {bands.length > 1 && rowsAt(at).length > 1 && unit !== 'pct' && (
            <div className="rp__tip-row">
              <span>Total</span>
              <span className="rp__tip-val">{FULL_FMT[unit](rowsAt(at).reduce((s, r) => s + r.value, 0))}</span>
            </div>
          )}
          {overlay && overlay.values[at] != null && (
            <div className={`rp__tip-row ${overlay.cls}`}>
              <div className="rp__legend-mark" />
              <span>{overlay.label}</span>
              <span className="rp__tip-val">{FULL_FMT[overlay.unit](overlay.values[at]!)}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 30px of history inside a KPI tile: the number, plus where it came from. */
export function Sparkline({ values }: { values: (number | null)[] }) {
  const defined = values.map((v, i) => [i, v] as [number, number | null]).filter(([, v]) => v != null)
  if (defined.length < 2) return null
  const W = 100
  const H = 30
  const nums = defined.map(([, v]) => v as number)
  const hi = Math.max(...nums)
  const lo = Math.min(...nums, 0)
  const x = (i: number) => (values.length > 1 ? (i / (values.length - 1)) * W : W / 2)
  const y = (v: number) => H - 3 - ((v - lo) / (hi - lo || 1)) * (H - 8)
  const pts = defined.map(([i, v]) => [x(i), y(v as number)] as [number, number])
  const line = pts.map(p => p.join(',')).join(' ')
  const last = pts[pts.length - 1]
  return (
    <div className="rp__kpi-spark">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        <polygon
          className="rp__spark-area"
          points={`${pts[0][0]},${H} ${line} ${last[0]},${H}`}
          vectorEffect="non-scaling-stroke"
        />
        <polyline className="rp__spark-line" points={line} vectorEffect="non-scaling-stroke" />
        <circle className="rp__spark-tip" cx={last[0]} cy={last[1]} r={2.5} />
      </svg>
    </div>
  )
}

/**
 * Direction of travel, coloured by whether that direction is good.
 *
 * Spend rising is business volume, not a failure, so it is deliberately neutral.
 * Overdue and approval latency rising is bad; on-time rate and payment buffer
 * rising is good. Colouring every increase red is how a dashboard teaches people
 * to ignore it.
 */
export function DeltaChip({
  pct,
  good,
  big,
  label,
}: {
  pct: number | null
  good: 'up' | 'down' | 'neutral'
  big?: boolean
  label?: string
}) {
  const base = big ? 'rp__trend' : 'rp__delta'
  const size = big ? 13 : 11
  if (pct == null || !Number.isFinite(pct)) return <span className={`${base} rp-t--muted`}>no baseline</span>
  if (Math.abs(pct) < 0.5)
    return (
      <span className={`${base} rp-t--muted`}>
        <Minus size={size} /> flat {label}
      </span>
    )
  const up = pct > 0
  const tone =
    good === 'neutral' ? 'rp-t--muted' : up === (good === 'up') ? 'rp-t--success' : 'rp-t--danger'
  return (
    <span className={`${base} ${tone}`}>
      {up ? <TrendingUp size={size} /> : <TrendingDown size={size} />}
      {Math.abs(pct) >= 999 ? '999+' : Math.abs(pct).toFixed(big ? 1 : 0)}% {label}
    </span>
  )
}

export function TrendKpi({
  label,
  value,
  sub,
  tone,
  series,
  good,
}: {
  label: string
  value: string
  sub: string
  tone: string
  series: (number | null)[]
  good: 'up' | 'down' | 'neutral'
}) {
  return (
    <div className={`rp__kpi ${tone}`}>
      <div className="rp__kpi-lbl">{label}</div>
      <div className="rp__kpi-val rp__kpi-val--sm">{value}</div>
      <div className="rp__kpi-sub">
        <DeltaChip pct={delta(series).pct} good={good} />
        {sub}
      </div>
      <Sparkline values={series} />
    </div>
  )
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (v: T) => void
  label: string
}) {
  const i = Math.max(
    0,
    options.findIndex(o => o.value === value),
  )
  return (
    <div className="rp__seg" role="group" aria-label={label}>
      <div
        className="rp__seg-thumb"
        style={{ width: `calc((100% - 6px) / ${options.length})`, transform: `translateX(${i * 100}%)` }}
      />
      {options.map(o => (
        <button
          key={o.value}
          type="button"
          className="rp__seg-btn"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Key for bands whose sum is the point — hiding one would break the reading. */
export function ChartKeys({ items }: { items: { label: string; cls: string; line?: boolean }[] }) {
  return (
    <div className="rp__keys">
      {items.map(k => (
        <span className={`rp__key ${k.cls}`} key={k.label}>
          <span className={`rp__key-mark${k.line ? ' rp__key-mark--line' : ''}`} />
          {k.label}
        </span>
      ))}
    </div>
  )
}

