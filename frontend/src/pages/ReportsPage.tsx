/**
 * Reports — spend, aging, vendor, approval and FX analytics.
 *
 * The analysis (the `data` memo) is unchanged from the first cut; the surface is
 * not. Presentation now lives in reports.css and colour is carried by class, so
 * a series declares its hue once and every mark it owns — line, area, bar, dot,
 * monogram, swatch, tint — inherits it through `currentColor`.
 *
 * Interaction follows skills/apple-design-skill.md: press feedback lands on
 * pointer-down, the tab indicator slides so switching reads as one object
 * moving, the trend chart tracks the pointer 1:1 with a live readout, and the
 * legend is a control (click to isolate a category) rather than a caption.
 */
import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { RefreshCw, Download } from 'lucide-react'
import { useBills } from '../hooks/useBills'
import {
  fetchCategories,
  fetchCategoryExpHeads,
  fetchFxReport,
  type Category,
  type FxReportData,
} from '../api/invoices'
import { useAuth } from '../auth/AuthContext'
import { useBaseCurrency } from '../auth/EntitlementsContext'
import { DeltaChip, Empty } from './reportCharts'
import { clamp, cx, fmt, fmtC, fmtDate } from './reportFormat'
import './reports.css'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

const TOP_N = 5

type Slice = { label: string; value: number; cls: string; tail?: number }

/**
 * Top N plus an aggregated tail.
 *
 * A 24-vendor or 13-category list rendered in full buries the five rows that
 * carry the spend under a dozen rounding errors; truncating it silently hides
 * money. Rolling the tail into one "Others" row does neither — the total still
 * adds up, and expanding is one click away. Skipped when the tail is a single
 * row, since "Others (1)" is strictly worse than naming it.
 */
function rollup(entries: [string, number][], n = TOP_N): { slices: Slice[]; tail: number } {
  const all = entries.map(([label, value], i) => ({ label, value, cls: cx(i) }))
  if (all.length <= n + 1) return { slices: all, tail: 0 }
  const tail = entries.slice(n)
  return {
    slices: [
      ...all.slice(0, n),
      {
        label: `Others (${tail.length})`,
        value: tail.reduce((s, [, v]) => s + v, 0),
        cls: 'rp-t--muted',
        tail: tail.length,
      },
    ],
    tail: tail.length,
  }
}

const expand = (entries: [string, number][]): Slice[] =>
  entries.map(([label, value], i) => ({ label, value, cls: cx(i) }))

// ── Meter ─────────────────────────────────────────────────────────────────────
function Meter({ value, max, thin }: { value: number; max: number; thin?: boolean }) {
  const pct = max > 0 ? clamp((value / max) * 100, 0, 100) : 0
  return (
    <div className={`rp__meter${thin ? ' rp__meter--thin' : ''}`}>
      <div className="rp__meter-fill" style={{ width: `${pct}%` }} />
    </div>
  )
}

// ── Donut ─────────────────────────────────────────────────────────────────────
/** Arcs with rounded caps and a gap between them, so adjacent slices stay
 *  distinguishable even when two categories land on similar hues. */
function Donut({ slices, total }: { slices: Slice[]; total: number }) {
  const [active, setActive] = useState<number | null>(null)
  const sum = slices.reduce((s, x) => s + x.value, 0) || 1
  const R = 42
  const C = 2 * Math.PI * R
  const live = slices.map((s, i) => ({ ...s, i })).filter(s => s.value > 0)
  let offset = 0

  return (
    <div className="rp__donut" style={{ width: 108, height: 108 }}>
      <svg viewBox="0 0 108 108" width={108} height={108}>
        <circle cx={54} cy={54} r={R} fill="none" stroke="var(--line-100)" strokeWidth={13} />
        {live.map(seg => {
          const dash = (seg.value / sum) * C
          const gap = live.length > 1 ? Math.min(3, dash * 0.35) : 0
          const rot = (offset / C) * 360 - 90
          offset += dash
          return (
            <circle
              key={seg.label}
              className={`rp__donut-seg ${seg.cls}`}
              cx={54}
              cy={54}
              r={R}
              strokeWidth={active === seg.i ? 17 : 13}
              strokeDasharray={`${Math.max(dash - gap, 0.5)} ${C - Math.max(dash - gap, 0.5)}`}
              opacity={active == null || active === seg.i ? 1 : 0.28}
              style={{ transformOrigin: '54px 54px', transform: `rotate(${rot}deg)` }}
              onPointerEnter={() => setActive(seg.i)}
              onPointerLeave={() => setActive(null)}
            >
              <title>{`${seg.label}: ${fmt(seg.value)}`}</title>
            </circle>
          )
        })}
      </svg>
      <div className="rp__donut-mid">
        <div className="rp__donut-val">{fmtC(active != null ? slices[active].value : total)}</div>
        <div className="rp__donut-lbl">{active != null ? 'selected' : 'total'}</div>
      </div>
    </div>
  )
}

// ── Spark bars (monthly trend) ────────────────────────────────────────────────
function SparkBars({ data, labels }: { data: number[]; labels: string[] }) {
  const max = Math.max(...data, 1)
  return (
    <div className="rp__spark">
      {data.map((v, i) => {
        const now = i === data.length - 1
        const pct = v > 0 ? Math.max((v / max) * 100, 3) : 0
        return (
          <div
            key={labels[i] + i}
            className={`rp__spark-col${now ? ' rp__spark-col--now' : ''} ${now ? 'rp-t--info' : 'rp-t--muted'}`}
            title={`${labels[i]}: ${fmt(v)}`}
          >
            <div className="rp__spark-track">
              <div
                className="rp__spark-bar"
                style={{ height: `${pct}%`, animationDelay: `${i * 55}ms` }}
              />
            </div>
            <div className="rp__spark-amt">{v > 0 ? fmtC(v) : '—'}</div>
            <div className="rp__spark-lbl">{labels[i]}</div>
          </div>
        )
      })}
    </div>
  )
}

// ── Multi-series daily trend ──────────────────────────────────────────────────
type Series = { label: string; values: number[] }

/** Catmull-Rom → cubic Bézier at low tension, clamped to the plot box. Daily
 *  spend is spiky; a looser curve would undershoot below zero and read as
 *  negative spend on days that had none. */
function curve(pts: [number, number][], top: number, bottom: number) {
  if (pts.length < 2) return ''
  const t = 0.16
  let d = `M ${pts[0][0]},${pts[0][1]}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[i + 2] ?? p2
    const c1x = p1[0] + (p2[0] - p0[0]) * t
    const c1y = clamp(p1[1] + (p2[1] - p0[1]) * t, top, bottom)
    const c2x = p2[0] - (p3[0] - p1[0]) * t
    const c2y = clamp(p2[1] - (p3[1] - p1[1]) * t, top, bottom)
    d += ` C ${c1x},${c1y} ${c2x},${c2y} ${p2[0]},${p2[1]}`
  }
  return d
}

function LineChart({
  series,
  dayLabels,
  height = 170,
  hidden,
  focus,
}: {
  series: Series[]
  dayLabels: string[]
  height?: number
  hidden: Set<string>
  focus: string | null
}) {
  const [at, setAt] = useState<number | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  const W = 1000
  const padL = 52
  const padR = 12
  const padT = 12
  const padB = 22
  const plotW = W - padL - padR
  const plotH = height - padT - padB
  const n = dayLabels.length

  const shown = series.filter(s => !hidden.has(s.label))
  const maxV = Math.max(...shown.flatMap(s => s.values), 1)
  const xp = (i: number) => padL + (n > 1 ? (i / (n - 1)) * plotW : plotW / 2)
  const yp = (v: number) => padT + plotH - (v / maxV) * plotH

  if (n < 2) return <div style={{ height }} />

  // Continuous tracking: the readout follows the pointer for the whole sweep,
  // and lands on pointer-down too — no wait for a release that never comes.
  const track = (e: React.PointerEvent<HTMLDivElement>) => {
    const r = boxRef.current?.getBoundingClientRect()
    if (!r || r.width === 0) return
    const x = ((e.clientX - r.left) / r.width) * W
    setAt(clamp(Math.round(((x - padL) / plotW) * (n - 1)), 0, n - 1))
  }

  const ticks = [0, Math.floor(n / 4), Math.floor(n / 2), Math.floor((3 * n) / 4), n - 1]
  const rows =
    at == null
      ? []
      : shown
          .map(s => ({ label: s.label, value: s.values[at] ?? 0, i: series.indexOf(s) }))
          .filter(r => r.value > 0)
          .sort((a, b) => b.value - a.value)
  const dayTotal = rows.reduce((s, r) => s + r.value, 0)
  const tipPct = at == null ? 0 : (xp(at) / W) * 100
  const tipShift = tipPct < 18 ? '0' : tipPct > 82 ? '-100%' : '-50%'

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
        aria-label={`Daily spend across ${shown.length} categories, peak ${fmt(maxV)}`}
      >
        {[0, 0.5, 1].map(f => (
          <g key={f}>
            <line
              className="rp__grid-line"
              x1={padL}
              x2={W - padR}
              y1={padT + plotH * (1 - f)}
              y2={padT + plotH * (1 - f)}
            />
            <text className="rp__axis" x={padL - 8} y={padT + plotH * (1 - f) + 3} textAnchor="end">
              {fmtC(maxV * f)}
            </text>
          </g>
        ))}

        {series.map(s => {
          const i = series.indexOf(s)
          const off = hidden.has(s.label)
          const dim = off || (focus != null && focus !== s.label)
          const pts = s.values.map((v, k) => [xp(k), yp(v)] as [number, number])
          const d = curve(pts, padT, padT + plotH)
          const gid = `rp-g-${i}`
          return (
            <g key={s.label} className={`${cx(i)}${dim ? ' rp__series--dim' : ''}`} aria-hidden={off}>
              <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="currentColor" stopOpacity={0.22} />
                <stop offset="100%" stopColor="currentColor" stopOpacity={0} />
              </linearGradient>
              {!off && (
                <>
                  <path
                    className="rp__area"
                    d={`${d} L ${xp(n - 1)},${padT + plotH} L ${xp(0)},${padT + plotH} Z`}
                    fill={`url(#${gid})`}
                  />
                  <path className="rp__line rp__line--enter" d={d} pathLength={1} />
                </>
              )}
            </g>
          )
        })}

        {at != null && (
          <>
            <line className="rp__crosshair" x1={xp(at)} x2={xp(at)} y1={padT} y2={padT + plotH} />
            {rows.slice(0, 8).map(r => (
              <circle key={r.label} className={`rp__dot ${cx(r.i)}`} cx={xp(at)} cy={yp(r.value)} r={3.5} />
            ))}
          </>
        )}

        {ticks.map(i => (
          <text className="rp__axis" key={i} x={xp(i)} y={height - 4} textAnchor="middle">
            {dayLabels[i]}
          </text>
        ))}
      </svg>

      {at != null && (
        <div className="rp__tip" style={{ left: `${tipPct}%`, transform: `translateX(${tipShift})` }}>
          <div className="rp__tip-day">{dayLabels[at]}</div>
          {rows.length === 0 ? (
            <div className="rp__tip-row">
              <span>No approved spend</span>
            </div>
          ) : (
            <>
              {rows.slice(0, 5).map(r => (
                <div className={`rp__tip-row ${cx(r.i)}`} key={r.label}>
                  <div className="rp__swatch" />
                  <span>{r.label}</span>
                  <span className="rp__tip-val">{fmt(r.value)}</span>
                </div>
              ))}
              {rows.length > 5 && (
                <div className="rp__tip-row">
                  <span>+{rows.length - 5} more</span>
                </div>
              )}
              {rows.length > 1 && (
                <div className="rp__tip-row">
                  <span>Total</span>
                  <span className="rp__tip-val">{fmt(dayTotal)}</span>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** Chart + legend as one unit: the legend owns which series are live, so a
 *  13-category book can be read one category at a time instead of as a knot. */
function CategoryTrend({
  series,
  dayLabels,
  height,
}: {
  series: Series[]
  dayLabels: string[]
  height?: number
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [focus, setFocus] = useState<string | null>(null)

  const toggle = (label: string) =>
    setHidden(prev => {
      const next = new Set(prev)
      if (next.has(label)) next.delete(label)
      else next.add(label)
      return next
    })

  return (
    <>
      <LineChart series={series} dayLabels={dayLabels} height={height} hidden={hidden} focus={focus} />
      {series.length > 0 && (
        <div className="rp__legend">
          {series.map((s, i) => (
            <button
              key={s.label}
              type="button"
              className={`rp__legend-item ${cx(i)}`}
              aria-pressed={!hidden.has(s.label)}
              onPointerEnter={() => setFocus(s.label)}
              onPointerLeave={() => setFocus(null)}
              onFocus={() => setFocus(s.label)}
              onBlur={() => setFocus(null)}
              onClick={() => toggle(s.label)}
            >
              <span className="rp__legend-mark" />
              {s.label}
            </button>
          ))}
          {hidden.size > 0 && (
            <button type="button" className="rp__link" onClick={() => setHidden(new Set())}>
              Show all
            </button>
          )}
          {hidden.size === 0 && <span className="rp__legend-hint">click to hide a category</span>}
        </div>
      )}
    </>
  )
}

// ── Export CSV helper ─────────────────────────────────────────────────────────
function exportCsv(rows: (string | number)[][], filename: string) {
  const csv = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n')
  const a = document.createElement('a')
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
  a.download = filename
  a.click()
}

// ── Small presentational pieces ───────────────────────────────────────────────
function Kpi({
  label,
  value,
  tone,
  sub,
  delta,
  small,
}: {
  label: string
  value: string
  tone: string
  sub?: string
  delta?: React.ReactNode
  small?: boolean
}) {
  return (
    <div className={`rp__kpi ${tone}`}>
      <div className="rp__kpi-lbl">{label}</div>
      <div className={`rp__kpi-val${small ? ' rp__kpi-val--sm' : ''}`}>{value}</div>
      <div className="rp__kpi-sub">
        {delta}
        {sub}
      </div>
    </div>
  )
}

function VendorRow({ slice, max }: { slice: Slice; max: number }) {
  return (
    <div className={`rp__row ${slice.cls}`}>
      <div className="rp__row-top">
        <div className="rp__row-id">
          {/* The rolled-up row is not a vendor, so it does not get a monogram. */}
          <div className="rp__mono">{slice.tail ? '∑' : slice.label.charAt(0).toUpperCase()}</div>
          <span className="rp__row-name" title={slice.label}>
            {slice.label}
          </span>
        </div>
        <span className="rp__row-amt">{fmt(slice.value)}</span>
      </div>
      <Meter value={slice.value} max={max} />
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="rp__panel">
      <div className="rp__kpis">
        {[0, 1, 2, 3].map(i => (
          <div key={i} className="rp__skel" style={{ height: 96 }} />
        ))}
      </div>
      <div className="rp__skel" style={{ height: 260 }} />
      <div className="rp__duo">
        <div className="rp__skel" style={{ height: 210 }} />
        <div className="rp__skel" style={{ height: 210 }} />
      </div>
    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────
export default function ReportsPage() {
  // Was a hard slice(0,6) with no indication data was being withheld. On a
  // ₹100cr book with 400 vendors that hid ~98% of the spend. Now the tail is
  // rolled up rather than dropped, and expanding is one click.
  const [allVendors, setAllVendors] = useState(false)
  const [allCats, setAllCats] = useState(false)
  const { bills, loading, refresh } = useBills()
  const { user } = useAuth()
  // Totals are in the tenant's books currency; a bill is "foreign" only when its
  // currency differs from it (INR for Indian tenants).
  const baseCur = useBaseCurrency()
  const [tab, setTab] = useState<'dashboard' | 'spend' | 'category' | 'aging' | 'approval' | 'vendor' | 'fx'>(
    'dashboard',
  )
  const [apiCategories, setApiCategories] = useState<Category[]>([])
  const [expHeadMap, setExpHeadMap] = useState<Record<string, string>>({})
  const [fxReport, setFxReport] = useState<FxReportData | null>(null)
  const [fxReportLoading, setFxReportLoading] = useState(false)

  useEffect(() => {
    fetchCategories(user?.company_id)
      .then(setApiCategories)
      .catch(() => {})
    fetchCategoryExpHeads(user?.company_id)
      .then(setExpHeadMap)
      .catch(() => {})
  }, [user?.company_id])

  useEffect(() => {
    if (tab === 'fx') {
      setFxReportLoading(true)
      fetchFxReport(user?.company_id)
        .then(setFxReport)
        .catch(() => {})
        .finally(() => setFxReportLoading(false))
    }
  }, [tab, user?.company_id])

  const load = (force = false) => {
    if (force) refresh()
  }

  const data = useMemo(() => {
    const now = new Date()
    now.setHours(0, 0, 0, 0)
    const approved = bills.filter(b => b.status?.toUpperCase() === 'APPROVED')

    // ── Spend by vendor ──
    const vendorMap: Record<string, number> = {}
    approved.forEach(b => {
      const k = b.vendor_name?.trim() || 'Unknown'
      vendorMap[k] = (vendorMap[k] ?? 0) + (b.total_amount ?? 0)
    })
    const byVendor = Object.entries(vendorMap).sort((a, b) => b[1] - a[1])
    const maxVendor = byVendor[0]?.[1] ?? 1

    // ── Exp head mapping (covers both builtin + custom, from /api/category-exp-heads) ──
    const catExpHeadMap = expHeadMap

    // ── Spend by category (include custom categories even with 0 bills) ──
    const catMap: Record<string, number> = {}
    approved.forEach(b => {
      const k = b.category?.trim() || 'Uncategorised'
      catMap[k] = (catMap[k] ?? 0) + (b.total_amount ?? 0)
    })
    apiCategories.forEach(c => {
      if (!(c.name in catMap)) catMap[c.name] = 0
    })
    const byCategory = Object.entries(catMap).sort((a, b) => b[1] - a[1])

    // ── Group by exp head ──
    const expHeadGroups: Record<string, { cats: [string, number][]; total: number }> = {}
    byCategory.forEach(([cat, amt]) => {
      const head = catExpHeadMap[cat] || 'Unassigned'
      if (!expHeadGroups[head]) expHeadGroups[head] = { cats: [], total: 0 }
      expHeadGroups[head].cats.push([cat, amt])
      expHeadGroups[head].total += amt
    })
    const sortedGroups = Object.entries(expHeadGroups).sort(([a], [b]) => {
      if (a === 'Unassigned') return 1
      if (b === 'Unassigned') return -1
      return a.localeCompare(b)
    })
    const totalCatAmt = byCategory.reduce((s, [, v]) => s + v, 0) || 1

    // ── Monthly trend (last 6 months) ──
    const months6 = Array.from({ length: 6 }, (_, i) => {
      const d = new Date(now.getFullYear(), now.getMonth() - 5 + i)
      return { y: d.getFullYear(), mo: d.getMonth(), label: MONTHS[d.getMonth()] }
    })
    const spendByMonth: Record<string, number> = {}
    approved.forEach(b => {
      if (!b.created_at) return
      const d = new Date(b.created_at)
      const k = `${d.getFullYear()}-${d.getMonth()}`
      spendByMonth[k] = (spendByMonth[k] ?? 0) + (b.total_amount ?? 0)
    })
    const trendAmts = months6.map(m => spendByMonth[`${m.y}-${m.mo}`] ?? 0)
    const trendLabels = months6.map(m => m.label)
    const thisMonth = trendAmts[5]
    const lastMonth = trendAmts[4]
    const mom = lastMonth > 0 ? ((thisMonth - lastMonth) / lastMonth) * 100 : null

    // ── Payment aging ──
    const unpaid = approved.filter(b => (b.payment_status ?? 'unpaid') !== 'paid')
    const aging = [
      { label: 'Current (not yet due)', min: -Infinity, max: 0, tone: 'rp-t--success' },
      { label: '1–30 days overdue', min: 1, max: 30, tone: 'rp-t--warn' },
      { label: '31–60 days overdue', min: 31, max: 60, tone: 'rp-t--orange' },
      { label: '61–90 days overdue', min: 61, max: 90, tone: 'rp-t--danger' },
      { label: '90+ days overdue', min: 91, max: Infinity, tone: 'rp-t--crit' },
    ].map(bucket => {
      const rows = unpaid.filter(b => {
        if (!b.due_date) return false
        const days = Math.floor((now.getTime() - new Date(b.due_date).getTime()) / 86400000)
        return days >= bucket.min && days <= bucket.max
      })
      const amt = rows.reduce((s, b) => s + ((b.outstanding_amount ?? b.total_amount) ?? 0), 0)
      return { ...bucket, rows, amt, count: rows.length }
    })
    const agingTotal = aging.reduce((s, a) => s + a.amt, 0)

    // ── Approval turnaround (from chain) ──
    // We approximate: time between bill created_at and latest approval action
    const pendingBills = bills.filter(b => b.status?.toUpperCase().startsWith('PENDING'))
    const avgPendingDays =
      pendingBills.length > 0
        ? pendingBills.reduce((s, b) => {
            if (!b.created_at) return s
            return s + Math.floor((now.getTime() - new Date(b.created_at).getTime()) / 86400000)
          }, 0) / pendingBills.length
        : 0

    // ── Daily category spend (last 30 days) ──
    const days30 = Array.from({ length: 30 }, (_, i) => {
      const d = new Date(now)
      d.setDate(now.getDate() - 29 + i)
      d.setHours(0, 0, 0, 0)
      return d
    })
    const dayLabels = days30.map(d => `${d.getDate()}/${d.getMonth() + 1}`)
    const catDailyMap: Record<string, number[]> = {}
    byCategory.forEach(([cat]) => {
      catDailyMap[cat] = Array(30).fill(0)
    })
    approved.forEach(b => {
      if (!b.created_at) return
      const bd = new Date(b.created_at)
      bd.setHours(0, 0, 0, 0)
      const diff = Math.round((bd.getTime() - days30[0].getTime()) / 86400000)
      if (diff < 0 || diff >= 30) return
      const cat = b.category?.trim() || 'Uncategorised'
      if (!catDailyMap[cat]) catDailyMap[cat] = Array(30).fill(0)
      catDailyMap[cat][diff] += b.total_amount ?? 0
    })
    const catTrendSeries = byCategory.map(([cat]) => ({
      label: cat,
      values: catDailyMap[cat] ?? Array(30).fill(0),
    }))

    const hasFxBills = bills.some(b => b.currency_code && b.currency_code !== baseCur)

    return {
      byVendor,
      maxVendor,
      byCategory,
      totalCatAmt,
      trendAmts,
      trendLabels,
      thisMonth,
      mom,
      aging,
      agingTotal,
      pendingBills,
      avgPendingDays,
      catTrendSeries,
      dayLabels,
      catExpHeadMap,
      sortedGroups,
      totalApproved: approved.length,
      totalApprovedAmt: approved.reduce((s, b) => s + (b.total_amount ?? 0), 0),
      hasFxBills,
    }
  }, [bills, apiCategories, expHeadMap, baseCur])

  const TABS = useMemo(
    () =>
      [
        { key: 'dashboard', label: 'Dashboard' },
        { key: 'spend', label: 'Spend Analysis' },
        { key: 'category', label: 'Category Breakdown' },
        { key: 'aging', label: 'Payment Aging' },
        { key: 'vendor', label: 'Vendor Report' },
        { key: 'approval', label: 'Approval Metrics' },
        // The FX cost report compares booked vs actual rupees paid (RBI/FBIL INR
        // rates), so it exists only for tenants that keep INR books.
        ...(data.hasFxBills && baseCur === 'INR' ? [{ key: 'fx', label: 'Currency & FX' }] : []),
      ] as { key: typeof tab; label: string }[],
    [data.hasFxBills, baseCur],
  )

  // ── Sliding tab indicator ──────────────────────────────────────────────────
  // One indicator that moves, measured from the live DOM so it stays correct
  // when the FX tab appears or the rail is scrolled.
  const tabsRef = useRef<HTMLDivElement>(null)
  const [ind, setInd] = useState({ x: 0, w: 0 })

  useLayoutEffect(() => {
    const rail = tabsRef.current
    if (!rail) return
    const place = () => {
      const el = rail.querySelector<HTMLButtonElement>('[aria-selected="true"]')
      if (!el) return
      setInd({ x: el.offsetLeft, w: el.offsetWidth })
      // Keep the active tab reachable without yanking the page.
      if (el.offsetLeft < rail.scrollLeft || el.offsetLeft + el.offsetWidth > rail.scrollLeft + rail.clientWidth)
        rail.scrollTo({ left: Math.max(0, el.offsetLeft - 16), behavior: 'smooth' })
    }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(rail)
    return () => ro.disconnect()
  }, [tab, TABS])

  const onTabKey = (e: React.KeyboardEvent) => {
    const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0
    if (!dir) return
    e.preventDefault()
    const i = TABS.findIndex(t => t.key === tab)
    const next = TABS[(i + dir + TABS.length) % TABS.length]
    setTab(next.key)
    requestAnimationFrame(() => {
      tabsRef.current?.querySelector<HTMLButtonElement>(`#rp-tab-${next.key}`)?.focus()
    })
  }

  const overdueAmt = data.aging.slice(1).reduce((s, b) => s + b.amt, 0)
  const overdueCount = data.aging.slice(1).reduce((s, b) => s + b.count, 0)

  // Top five plus a rolled-up tail, unless the reader asked for the long list.
  const vendorRoll = rollup(data.byVendor)
  const vendorSlices = allVendors ? expand(data.byVendor) : vendorRoll.slices
  const vendorMax = Math.max(...vendorSlices.map(s => s.value), 1)
  const catRoll = rollup(data.byCategory)
  const catSlices = allCats ? expand(data.byCategory) : catRoll.slices

  const vendorToggle = vendorRoll.tail > 0 && (
    <button type="button" className="rp__link" onClick={() => setAllVendors(v => !v)}>
      {allVendors ? `Show top ${TOP_N}` : `Show all ${data.byVendor.length}`}
    </button>
  )
  const catToggle = catRoll.tail > 0 && (
    <button type="button" className="rp__link" onClick={() => setAllCats(v => !v)}>
      {allCats ? `Show top ${TOP_N}` : `Show all ${data.byCategory.length}`}
    </button>
  )
  const countLabel = (shown: number, total: number, all: boolean) =>
    all ? `all ${total}` : `top ${shown} of ${total}`

  return (
    <div className="rp">
      <div className="rp__scroll">
        {/* Header — scrolls away; the rail is what stays. */}
        <div className="rp__head">
          <div>
            <h1 className="rp__title">Reports</h1>
            <p className="rp__sub">
              {loading
                ? 'Refreshing…'
                : `Based on ${bills.length} bills · ${data.totalApproved} approved · ${fmt(data.totalApprovedAmt)}`}
            </p>
          </div>
          <button className="rp__refresh" onClick={() => load(true)} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'rp__spin' : ''} /> Refresh
          </button>
        </div>

        {/* Tab rail — a translucent material; content passes beneath it. */}
        <div className="rp__rail">
          <div className="rp__tabs" role="tablist" aria-label="Report views" ref={tabsRef}>
            {TABS.map(t => (
              <button
                key={t.key}
                id={`rp-tab-${t.key}`}
                role="tab"
                type="button"
                aria-selected={tab === t.key}
                aria-controls={`rp-panel-${t.key}`}
                tabIndex={tab === t.key ? 0 : -1}
                className="rp__tab"
                onClick={() => setTab(t.key)}
                onKeyDown={onTabKey}
              >
                {t.label}
              </button>
            ))}
            <div className="rp__ind" style={{ width: ind.w, transform: `translateX(${ind.x}px)` }} />
          </div>
        </div>

        <div className="rp__body" role="tabpanel" id={`rp-panel-${tab}`} aria-labelledby={`rp-tab-${tab}`}>
          {loading && bills.length === 0 ? (
            <LoadingSkeleton />
          ) : bills.length === 0 ? (
            <Empty title="No bill data yet" sub="Upload and approve invoices to see reports here." />
          ) : (
            <div className="rp__panel" key={tab}>
              {/* ══ DASHBOARD ══ */}
              {tab === 'dashboard' && (
                <>
                  <div className="rp__kpis">
                    <Kpi
                      label="Total Approved Spend"
                      value={fmt(data.totalApprovedAmt)}
                      tone="rp-t--brand"
                      sub={`${data.totalApproved} approved bills`}
                    />
                    <Kpi
                      label="This Month"
                      value={fmt(data.thisMonth)}
                      tone={data.mom != null && data.mom > 0 ? 'rp-t--danger' : 'rp-t--success'}
                      delta={<DeltaChip pct={data.mom} good="neutral" />}
                      sub={data.mom != null ? 'vs last month' : 'first month of data'}
                    />
                    <Kpi
                      label="Vendors Active"
                      value={String(data.byVendor.length)}
                      tone="rp-t--violet"
                      sub="with approved bills"
                    />
                    <Kpi
                      label="Overdue Outstanding"
                      value={fmt(overdueAmt)}
                      tone="rp-t--danger"
                      sub={`${overdueCount} bills past due`}
                    />
                  </div>

                  {/* Period-over-period progressions live on the Dashboard page,
                      where they answer "how are we doing?". Reports stays the
                      place you come to read the detail behind that answer. */}
                  <div className="rp__card">
                    <div className="rp__card-hdr">
                      <div>
                        <div className="rp__card-title">Category Spend Trend</div>
                        <div className="rp__card-sub">Daily approved spend by category · last 30 days</div>
                      </div>
                      <DeltaChip pct={data.mom} good="neutral" big label="MoM" />
                    </div>
                    <CategoryTrend series={data.catTrendSeries} dayLabels={data.dayLabels} height={180} />
                  </div>


                  <div className="rp__duo">
                    <div className="rp__card">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">Payment Aging Summary</div>
                          <div className="rp__card-sub">{fmt(data.agingTotal)} outstanding across 5 buckets</div>
                        </div>
                      </div>
                      <div>
                        {data.aging.map(b => (
                          <div className={`rp__aging-row ${b.tone}`} key={b.label}>
                            <div className="rp__dotmark" />
                            <div className="rp__aging-lbl">{b.label}</div>
                            <div className={`rp__aging-amt${b.amt > 0 ? '' : ' rp__aging-amt--zero'}`}>
                              {fmt(b.amt)}
                            </div>
                            <div className="rp__aging-n">
                              {b.count} bill{b.count === 1 ? '' : 's'}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="rp__card">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">
                            Vendors by Spend
                            <span className="rp__card-count">
                              {countLabel(TOP_N, data.byVendor.length, allVendors)}
                            </span>
                          </div>
                          <div className="rp__card-sub">Approved spend, largest first</div>
                        </div>
                        {vendorToggle}
                      </div>
                      <div className={`rp__rows${allVendors ? ' rp__rows--scroll' : ''}`}>
                        {vendorSlices.map(s => (
                          <VendorRow key={s.label} slice={s} max={vendorMax} />
                        ))}
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* ══ SPEND ANALYSIS ══ */}
              {tab === 'spend' && (
                <>
                  <div className="rp__kpis">
                    <Kpi
                      label="Total Approved Spend"
                      value={fmt(data.totalApprovedAmt)}
                      tone="rp-t--brand"
                      sub={`${data.totalApproved} bills`}
                    />
                    <Kpi
                      label="This Month"
                      value={fmt(data.thisMonth)}
                      tone={data.mom != null && data.mom > 0 ? 'rp-t--danger' : 'rp-t--success'}
                      delta={<DeltaChip pct={data.mom} good="neutral" />}
                      sub={data.mom != null ? 'vs last month' : 'first month of data'}
                    />
                    <Kpi
                      label="Vendors"
                      value={String(data.byVendor.length)}
                      tone="rp-t--violet"
                      sub="with approved bills"
                    />
                    <Kpi
                      label="Categories"
                      value={String(data.byCategory.length)}
                      tone="rp-t--info"
                      sub="spend categories"
                    />
                  </div>

                  <div className="rp__card">
                    <div className="rp__card-hdr">
                      <div>
                        <div className="rp__card-title">Monthly Spend Trend</div>
                        <div className="rp__card-sub">Approved bills, by upload date · last 6 months</div>
                      </div>
                      <DeltaChip pct={data.mom} good="neutral" big label="MoM" />
                    </div>
                    <SparkBars data={data.trendAmts} labels={data.trendLabels} />
                  </div>

                  <div className="rp__duo">
                    <div className="rp__card">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">
                            Spend by Category
                            <span className="rp__card-count">
                              {countLabel(TOP_N, data.byCategory.length, allCats)}
                            </span>
                          </div>
                          <div className="rp__card-sub">
                            Approved bills breakdown · export covers every category
                          </div>
                        </div>
                        <div className="rp__hdr-actions">
                          {catToggle}
                          <button
                            type="button"
                            className="rp__export"
                          onClick={() =>
                            exportCsv(
                              [
                                ['Exp Head', 'Category', 'Amount', '% Share'],
                                ...data.sortedGroups.flatMap(([head, { cats }]) =>
                                  cats.map(([c, v]) => [
                                    head,
                                    c,
                                    v,
                                    `${((v / data.totalCatAmt) * 100).toFixed(1)}%`,
                                  ]),
                                ),
                              ],
                              'spend_by_category.csv',
                            )
                          }
                          >
                            <Download size={13} /> Export
                          </button>
                        </div>
                      </div>
                      <div style={{ display: 'flex', gap: 'var(--sp-5)', alignItems: 'flex-start' }}>
                        <Donut slices={catSlices} total={data.totalCatAmt} />
                        <div className={`rp__rows${allCats ? ' rp__rows--scroll' : ''}`} style={{ flex: 1, minWidth: 0 }}>
                          {catSlices.map(s => (
                            <div className={`rp__row ${s.cls}`} key={s.label}>
                              <div className="rp__row-top">
                                <div className="rp__row-id">
                                  <div className="rp__sqmark" />
                                  <span className="rp__row-name" title={s.label}>
                                    {s.label}
                                  </span>
                                </div>
                                <div className="rp__row-id">
                                  <span className="rp__row-pct">
                                    {((s.value / data.totalCatAmt) * 100).toFixed(1)}%
                                  </span>
                                  <span className="rp__row-amt">{fmt(s.value)}</span>
                                </div>
                              </div>
                              <Meter value={s.value} max={data.totalCatAmt} thin />
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    <div className="rp__card">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">
                            Top Vendors by Spend
                            <span className="rp__card-count">
                              {countLabel(TOP_N, data.byVendor.length, allVendors)}
                            </span>
                          </div>
                          <div className="rp__card-sub">Share of the largest row</div>
                        </div>
                        {vendorToggle}
                      </div>
                      <div className={`rp__rows${allVendors ? ' rp__rows--scroll' : ''}`}>
                        {vendorSlices.map(s => (
                          <VendorRow key={s.label} slice={s} max={vendorMax} />
                        ))}
                      </div>
                    </div>
                  </div>
                </>
              )}

              {/* ══ CATEGORY BREAKDOWN ══ */}
              {tab === 'category' && (
                <>
                  <div className="rp__card">
                    <div className="rp__card-hdr">
                      <div>
                        <div className="rp__card-title">Daily Spend by Category</div>
                        <div className="rp__card-sub">Approved bills · last 30 days</div>
                      </div>
                    </div>
                    <CategoryTrend series={data.catTrendSeries} dayLabels={data.dayLabels} height={200} />
                  </div>

                  <div className="rp__kpis">
                    {data.sortedGroups.map(([head, { total }]) => (
                      <Kpi
                        key={head}
                        label={head}
                        value={fmt(total)}
                        small
                        tone={head === 'Unassigned' ? 'rp-t--muted' : 'rp-t--indigo'}
                        sub={`${((total / data.totalCatAmt) * 100).toFixed(1)}% of total`}
                      />
                    ))}
                  </div>

                  <div className="rp__card rp__card--flush">
                    <div className="rp__card-hdr">
                      <div>
                        <div className="rp__card-title">Expense Head → Category Breakdown</div>
                        <div className="rp__card-sub">Approved bills only · grouped by expense head</div>
                      </div>
                      <button
                        type="button"
                        className="rp__export"
                        onClick={() =>
                          exportCsv(
                            [
                              ['Exp Head', 'Category', 'Amount', '% Share', 'Bills', 'Avg per Bill'],
                              ...data.sortedGroups.flatMap(([head, { cats }]) =>
                                cats.map(([c, v]) => {
                                  const cb = bills.filter(
                                    b =>
                                      b.status?.toUpperCase() === 'APPROVED' &&
                                      (b.category?.trim() || 'Uncategorised') === c,
                                  )
                                  return [
                                    head,
                                    c,
                                    v,
                                    `${((v / data.totalCatAmt) * 100).toFixed(1)}%`,
                                    cb.length,
                                    cb.length > 0 ? Math.round(v / cb.length) : 0,
                                  ]
                                }),
                              ),
                            ],
                            'category_breakdown.csv',
                          )
                        }
                      >
                        <Download size={13} /> Export
                      </button>
                    </div>
                    <div className="rp__tblwrap rp__tblwrap--tall">
                      <table className="rp__tbl">
                        <thead>
                          <tr>
                            <th>Category</th>
                            <th className="rp__num">Amount</th>
                            <th>% Share</th>
                            <th className="rp__num">Bills</th>
                            <th className="rp__num">Avg per Bill</th>
                            <th>Trend</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.sortedGroups.map(([head, { cats, total: groupTotal }]) => (
                            <React.Fragment key={head}>
                              <tr
                                className={`rp__group ${head === 'Unassigned' ? 'rp-t--muted' : 'rp-t--indigo'}`}
                              >
                                <td colSpan={6}>
                                  <div className="rp__group-row">
                                    <span className="rp__group-tag">{head}</span>
                                    <span className="rp__group-amt">{fmt(groupTotal)}</span>
                                  </div>
                                </td>
                              </tr>
                              {cats.map(([cat, amt]) => {
                                const catBills = bills.filter(
                                  b =>
                                    b.status?.toUpperCase() === 'APPROVED' &&
                                    (b.category?.trim() || 'Uncategorised') === cat,
                                )
                                const avgPerBill = catBills.length > 0 ? amt / catBills.length : 0
                                const i = data.byCategory.findIndex(([c]) => c === cat)
                                return (
                                  <tr className={cx(i)} key={cat}>
                                    <td className="rp__indent">
                                      <div className="rp__row-id">
                                        <div className="rp__sqmark" />
                                        <span className="rp__cell-strong">{cat}</span>
                                      </div>
                                    </td>
                                    <td className="rp__cell-amt">{fmt(amt)}</td>
                                    <td>
                                      <div className="rp__bar-cell">
                                        <Meter value={amt} max={data.totalCatAmt} thin />
                                        <span className="rp__row-pct">
                                          {((amt / data.totalCatAmt) * 100).toFixed(1)}%
                                        </span>
                                      </div>
                                    </td>
                                    <td className="rp__num rp__cell-muted">{catBills.length}</td>
                                    <td className="rp__num">{fmt(avgPerBill)}</td>
                                    <td style={{ minWidth: 90 }}>
                                      <Meter value={amt} max={data.totalCatAmt} />
                                    </td>
                                  </tr>
                                )
                              })}
                            </React.Fragment>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr>
                            <td>Total</td>
                            <td className="rp__num">{fmt(data.totalCatAmt)}</td>
                            <td>100%</td>
                            <td className="rp__num">{data.totalApproved}</td>
                            <td colSpan={2} />
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  </div>
                </>
              )}

              {/* ══ PAYMENT AGING ══ */}
              {tab === 'aging' && (
                <>
                  <div className="rp__buckets">
                    {data.aging.map(b => (
                      <div className={`rp__bucket ${b.tone}`} key={b.label}>
                        <div className="rp__bucket-lbl">
                          <span className="rp__dotmark" />
                          {b.label}
                        </div>
                        <div className="rp__bucket-val">{fmt(b.amt)}</div>
                        <div className="rp__bucket-sub">
                          {b.count} bill{b.count === 1 ? '' : 's'}
                          {data.agingTotal > 0 && b.amt > 0
                            ? ` · ${((b.amt / data.agingTotal) * 100).toFixed(0)}% of outstanding`
                            : ''}
                        </div>
                      </div>
                    ))}
                  </div>

                  {data.aging.slice(1).some(b => b.rows.length > 0) ? (
                    <div className="rp__card rp__card--flush">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">
                            Overdue Bills
                            <span className="rp__card-count">{overdueCount} bills · {fmt(overdueAmt)}</span>
                          </div>
                          <div className="rp__card-sub">
                            Approved bills with outstanding payments past due date
                          </div>
                        </div>
                        <button
                          type="button"
                          className="rp__export"
                          onClick={() =>
                            exportCsv(
                              [
                                ['Vendor', 'Invoice Date', 'Due Date', 'Outstanding', 'Days Overdue'],
                                ...data.aging.slice(1).flatMap(b =>
                                  b.rows.map(r => [
                                    r.vendor_name ?? '',
                                    r.invoice_date ?? '',
                                    r.due_date ?? '',
                                    r.outstanding_amount ?? r.total_amount ?? 0,
                                    Math.floor(
                                      (new Date().getTime() - new Date(r.due_date!).getTime()) / 86400000,
                                    ),
                                  ]),
                                ),
                              ],
                              'overdue_bills.csv',
                            )
                          }
                        >
                          <Download size={13} /> Export
                        </button>
                      </div>
                      <div className="rp__tblwrap rp__tblwrap--tall">
                        <table className="rp__tbl">
                          <thead>
                            <tr>
                              <th>Vendor</th>
                              <th>Due Date</th>
                              <th className="rp__num">Outstanding</th>
                              <th>Days Overdue</th>
                              <th>Payment Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.aging.slice(1).flatMap(bucket =>
                              bucket.rows.map(b => {
                                const days = Math.floor(
                                  (new Date().getTime() - new Date(b.due_date!).getTime()) / 86400000,
                                )
                                return (
                                  <tr key={b.invoice_id}>
                                    <td className="rp__cell-strong">{b.vendor_name || '—'}</td>
                                    <td className="rp__cell-muted">{fmtDate(b.due_date)}</td>
                                    <td className="rp__num">
                                      <span className="rp__pill rp-t--danger">
                                        {fmt(b.outstanding_amount ?? b.total_amount ?? 0)}
                                      </span>
                                    </td>
                                    <td>
                                      <span className={`rp__pill ${bucket.tone}`}>{days}d</span>
                                    </td>
                                    <td className="rp__cell-muted">{b.payment_status ?? 'unpaid'}</td>
                                  </tr>
                                )
                              }),
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : (
                    <div className="rp__card">
                      <Empty title="Nothing overdue" sub="Every approved bill is inside its payment terms." />
                    </div>
                  )}
                </>
              )}

              {/* ══ VENDOR REPORT ══ */}
              {tab === 'vendor' && (
                <div className="rp__card rp__card--flush">
                  <div className="rp__card-hdr">
                    <div>
                      <div className="rp__card-title">
                        Vendor Spend Report
                        <span className="rp__card-count">{data.byVendor.length} vendors</span>
                      </div>
                      <div className="rp__card-sub">All vendors sorted by total approved spend</div>
                    </div>
                    <button
                      type="button"
                      className="rp__export"
                      onClick={() =>
                        exportCsv(
                          [
                            data.hasFxBills
                              ? ['Vendor', `Total Billed (${baseCur})`, 'Bills', 'Outstanding', 'Currencies']
                              : ['Vendor', 'Total Billed', 'Bills', 'Outstanding'],
                            ...data.byVendor.map(([name, amt]) => {
                              const vbills = bills.filter(b => (b.vendor_name?.trim() || 'Unknown') === name)
                              const out = vbills.reduce(
                                (s, b) =>
                                  s +
                                  ((b.payment_status !== 'paid' ? (b.outstanding_amount ?? b.total_amount) : 0) ??
                                    0),
                                0,
                              )
                              const row: (string | number)[] = [name, amt, vbills.length, out]
                              if (data.hasFxBills) {
                                const curs = [
                                  ...new Set(
                                    vbills
                                      .filter(b => b.currency_code && b.currency_code !== baseCur)
                                      .map(b => b.currency_code),
                                  ),
                                ]
                                row.push(curs.length > 0 ? curs.join(', ') : baseCur)
                              }
                              return row
                            }),
                          ],
                          'vendor_report.csv',
                        )
                      }
                    >
                      <Download size={13} /> Export
                    </button>
                  </div>
                  <div className="rp__tblwrap rp__tblwrap--tall">
                    <table className="rp__tbl">
                      <thead>
                        <tr>
                          <th className="rp__rank">#</th>
                          <th>Vendor</th>
                          <th className="rp__num">Total Billed ({baseCur})</th>
                          <th className="rp__num">Bills</th>
                          <th className="rp__num">Outstanding</th>
                          {data.hasFxBills && <th>Currencies</th>}
                          <th>Share</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.byVendor.map(([name, amt], i) => {
                          const vbills = bills.filter(b => (b.vendor_name?.trim() || 'Unknown') === name)
                          const totalAmt = data.byVendor.reduce((s, [, v]) => s + v, 0) || 1
                          const outstanding = vbills.reduce(
                            (s, b) =>
                              s +
                              ((b.payment_status !== 'paid' ? (b.outstanding_amount ?? b.total_amount) : 0) ?? 0),
                            0,
                          )
                          const fxCurrencies = data.hasFxBills
                            ? [
                                ...new Set(
                                  vbills
                                    .filter(b => b.currency_code && b.currency_code !== baseCur)
                                    .map(b => b.currency_code),
                                ),
                              ]
                            : []
                          return (
                            <tr className={cx(i)} key={name}>
                              <td className="rp__rank">{i + 1}</td>
                              <td>
                                <div className="rp__row-id">
                                  <div className="rp__mono rp__mono--lg">{name.charAt(0).toUpperCase()}</div>
                                  <span className="rp__cell-strong">{name}</span>
                                </div>
                              </td>
                              <td className="rp__cell-amt">{fmt(amt)}</td>
                              <td className="rp__num rp__cell-muted">{vbills.length}</td>
                              <td className="rp__num">
                                {outstanding > 0 ? (
                                  <span className="rp__pill rp-t--danger">{fmt(outstanding)}</span>
                                ) : (
                                  <span className="rp__cell-muted">—</span>
                                )}
                              </td>
                              {data.hasFxBills && (
                                <td>
                                  {fxCurrencies.length > 0 ? (
                                    fxCurrencies.map(c => (
                                      <span className="rp__pill rp-t--indigo" key={c}>
                                        {c}
                                      </span>
                                    ))
                                  ) : (
                                    <span className="rp__cell-muted">INR</span>
                                  )}
                                </td>
                              )}
                              <td>
                                <div className="rp__bar-cell">
                                  <Meter value={amt} max={totalAmt} />
                                  <span className="rp__row-pct">{((amt / totalAmt) * 100).toFixed(1)}%</span>
                                </div>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* ══ APPROVAL METRICS ══ */}
              {tab === 'approval' && (
                <>
                  <div className="rp__kpis">
                    <Kpi label="Total Bills" value={String(bills.length)} tone="rp-t--brand" sub="all time" />
                    <Kpi
                      label="Approved"
                      value={String(data.totalApproved)}
                      tone="rp-t--success"
                      sub={`${bills.length > 0 ? ((data.totalApproved / bills.length) * 100).toFixed(0) : 0}% approval rate`}
                    />
                    <Kpi
                      label="Pending Review"
                      value={String(data.pendingBills.length)}
                      tone={data.pendingBills.length > 0 ? 'rp-t--warn' : 'rp-t--muted'}
                      sub={`avg ${data.avgPendingDays.toFixed(1)} days waiting`}
                    />
                  </div>

                  <div className="rp__card">
                    <div className="rp__card-hdr">
                      <div>
                        <div className="rp__card-title">Bill Status Breakdown</div>
                        <div className="rp__card-sub">Every bill in the book, by where it sits</div>
                      </div>
                    </div>
                    {(() => {
                      const statusMap: Record<string, { label: string; tone: string }> = {
                        DRAFT: { label: 'Draft', tone: 'rp-t--muted' },
                        PENDING_APPROVER: { label: 'Pending Approver', tone: 'rp-t--warn' },
                        PENDING_ACCOUNTANT: { label: 'Pending Accountant', tone: 'rp-t--info' },
                        PENDING_FC: { label: 'Pending FC', tone: 'rp-t--violet' },
                        APPROVED: { label: 'Approved', tone: 'rp-t--success' },
                        NEEDS_REVISION: { label: 'Needs Revision', tone: 'rp-t--danger' },
                      }
                      const counts: Record<string, number> = {}
                      bills.forEach(b => {
                        const s = b.status?.toUpperCase() ?? 'DRAFT'
                        counts[s] = (counts[s] ?? 0) + 1
                      })
                      const total = bills.length || 1
                      return (
                        <div className="rp__rows">
                          {Object.entries(counts)
                            .sort((a, b) => b[1] - a[1])
                            .map(([s, cnt]) => {
                              const meta = statusMap[s] ?? { label: s, tone: 'rp-t--muted' }
                              return (
                                <div className={`rp__row ${meta.tone}`} key={s}>
                                  <div className="rp__row-top">
                                    <div className="rp__row-id">
                                      <div className="rp__dotmark" />
                                      <span className="rp__row-name">{meta.label}</span>
                                    </div>
                                    <div className="rp__row-id">
                                      <span className="rp__row-pct">{((cnt / total) * 100).toFixed(0)}%</span>
                                      <span className="rp__row-amt">{cnt}</span>
                                    </div>
                                  </div>
                                  <Meter value={cnt} max={total} />
                                </div>
                              )
                            })}
                        </div>
                      )
                    })()}
                  </div>

                  {data.pendingBills.length > 0 && (
                    <div className="rp__card rp__card--flush">
                      <div className="rp__card-hdr">
                        <div>
                          <div className="rp__card-title">
                            Bills Awaiting Approval
                            <span className="rp__card-count">{data.pendingBills.length} waiting</span>
                          </div>
                          <div className="rp__card-sub">Sorted by longest waiting</div>
                        </div>
                        <button
                          type="button"
                          className="rp__export"
                          onClick={() =>
                            exportCsv(
                              [
                                ['Vendor', 'Status', 'Amount', 'Days Waiting'],
                                ...data.pendingBills.map(b => [
                                  b.vendor_name ?? '',
                                  b.status,
                                  b.total_amount ?? 0,
                                  b.created_at
                                    ? Math.floor((Date.now() - new Date(b.created_at).getTime()) / 86400000)
                                    : 0,
                                ]),
                              ],
                              'pending_approvals.csv',
                            )
                          }
                        >
                          <Download size={13} /> Export
                        </button>
                      </div>
                      <div className="rp__tblwrap rp__tblwrap--tall">
                        <table className="rp__tbl">
                          <thead>
                            <tr>
                              <th>Vendor</th>
                              <th>Status</th>
                              <th className="rp__num">Amount</th>
                              <th>Waiting Since</th>
                              <th>Days</th>
                            </tr>
                          </thead>
                          <tbody>
                            {[...data.pendingBills]
                              .sort(
                                (a, b) =>
                                  new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime(),
                              )
                              .map(b => {
                                const days = b.created_at
                                  ? Math.floor((Date.now() - new Date(b.created_at).getTime()) / 86400000)
                                  : 0
                                const tone = days > 7 ? 'rp-t--danger' : days > 3 ? 'rp-t--warn' : 'rp-t--muted'
                                return (
                                  <tr key={b.invoice_id}>
                                    <td className="rp__cell-strong">{b.vendor_name || '—'}</td>
                                    <td>
                                      <span className="rp__pill rp-t--info">
                                        {b.status.replace('PENDING_', '')}
                                      </span>
                                    </td>
                                    <td className="rp__cell-amt">{fmt(b.total_amount ?? 0)}</td>
                                    <td className="rp__cell-muted">{fmtDate(b.created_at)}</td>
                                    <td>
                                      <span className={`rp__pill ${tone}`}>{days}d</span>
                                    </td>
                                  </tr>
                                )
                              })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              )}

              {/* ══ CURRENCY & FX ══ */}
              {tab === 'fx' &&
                (fxReportLoading ? (
                  <LoadingSkeleton />
                ) : !fxReport ? (
                  <Empty title="No FX data available" sub="Record FX payments to see cost analysis here." />
                ) : (
                  (() => {
                    const r = fxReport
                    const qStart = new Date(r.quarter_start)
                    const qEnd = new Date(r.quarter_end)
                    const qLabel = `${qStart.toLocaleDateString('en-IN', { month: 'short' })} – ${qEnd.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })}`
                    const fxSym = (c: string) =>
                      c === 'USD' ? '$' : c === 'EUR' ? '€' : c === 'GBP' ? '£' : c === 'JPY' ? '¥' : ''
                    const hasCurrencies = r.currencies.length > 0

                    return (
                      <>
                        <div className="rp__deck">
                          <div className="rp__deck-eyebrow">FX Cost Analysis — {qLabel}</div>
                          <div className="rp__deck-grid">
                            <div>
                              <div className="rp__deck-lbl">Booked INR</div>
                              <div className="rp__deck-val">{fmt(r.total_booked_inr)}</div>
                            </div>
                            <div>
                              <div className="rp__deck-lbl">Actual INR Paid</div>
                              <div className="rp__deck-val">{fmt(r.total_actual_inr)}</div>
                            </div>
                            <div>
                              <div className="rp__deck-lbl">Total FX Cost</div>
                              <div
                                className={`rp__deck-val ${r.total_fx_cost > 0 ? 'rp__deck-val--up' : 'rp__deck-val--down'}`}
                              >
                                {r.total_fx_cost > 0 ? '+' : ''}
                                {fmt(r.total_fx_cost)}
                              </div>
                            </div>
                          </div>
                        </div>

                        {hasCurrencies && (
                          <div className="rp__card rp__card--flush">
                            <div className="rp__card-hdr">
                              <div>
                                <div className="rp__card-title">FX Cost by Currency</div>
                                <div className="rp__card-sub">
                                  Payments settled in foreign currencies this quarter
                                </div>
                              </div>
                              <button
                                type="button"
                                className="rp__export"
                                onClick={() =>
                                  exportCsv(
                                    [
                                      [
                                        'Currency',
                                        'Foreign Paid',
                                        'Payments',
                                        'Booked INR',
                                        'Actual INR',
                                        'FX Cost',
                                        'Avg Rate',
                                        'Avg Spread %',
                                      ],
                                      ...r.currencies.map(c => [
                                        c.currency_code,
                                        c.total_foreign,
                                        c.payment_count,
                                        c.booked_inr,
                                        c.actual_inr,
                                        c.fx_cost,
                                        c.avg_effective_rate?.toFixed(4) ?? '',
                                        c.avg_bank_spread_pct?.toFixed(2) ?? '',
                                      ]),
                                      [
                                        'Total',
                                        '',
                                        r.currencies.reduce((s, c) => s + c.payment_count, 0),
                                        r.total_booked_inr,
                                        r.total_actual_inr,
                                        r.total_fx_cost,
                                        '',
                                        '',
                                      ],
                                    ],
                                    `fx_cost_analysis_${r.quarter_start}_${r.quarter_end}.csv`,
                                  )
                                }
                              >
                                <Download size={13} /> Export CSV
                              </button>
                            </div>
                            <div className="rp__tblwrap">
                              <table className="rp__tbl">
                                <thead>
                                  <tr>
                                    <th>Currency</th>
                                    <th className="rp__num">Paid</th>
                                    <th className="rp__num">Payments</th>
                                    <th className="rp__num">Booked INR</th>
                                    <th className="rp__num">Actual INR</th>
                                    <th className="rp__num">FX Cost</th>
                                    <th className="rp__num">Avg Rate</th>
                                    <th className="rp__num">Avg Spread</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {r.currencies.map(c => (
                                    <tr key={c.currency_code}>
                                      <td>
                                        <span className="rp__pill rp-t--indigo">
                                          {fxSym(c.currency_code)} {c.currency_code}
                                        </span>
                                      </td>
                                      <td className="rp__num rp__cell-strong">
                                        {fxSym(c.currency_code)}
                                        {c.total_foreign.toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                                      </td>
                                      <td className="rp__num rp__cell-muted">{c.payment_count}</td>
                                      <td className="rp__num rp__cell-muted">{fmt(c.booked_inr)}</td>
                                      <td className="rp__cell-amt">{fmt(c.actual_inr)}</td>
                                      <td className="rp__num">
                                        {c.fx_cost !== 0 ? (
                                          <span
                                            className={`rp__pill ${c.fx_cost > 0 ? 'rp-t--danger' : 'rp-t--success'}`}
                                          >
                                            {c.fx_cost > 0 ? '+' : ''}
                                            {fmt(c.fx_cost)}
                                          </span>
                                        ) : (
                                          <span className="rp__cell-muted">—</span>
                                        )}
                                      </td>
                                      <td className="rp__num rp__cell-muted">
                                        {c.avg_effective_rate != null ? `₹${c.avg_effective_rate.toFixed(4)}` : '—'}
                                      </td>
                                      <td className="rp__num">
                                        {c.avg_bank_spread_pct != null ? (
                                          <span
                                            className={
                                              c.avg_bank_spread_pct > 1
                                                ? 'rp__pill rp-t--danger'
                                                : 'rp__cell-muted'
                                            }
                                          >
                                            {c.avg_bank_spread_pct.toFixed(2)}%
                                          </span>
                                        ) : (
                                          <span className="rp__cell-muted">—</span>
                                        )}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                                <tfoot>
                                  <tr>
                                    <td>Total</td>
                                    <td />
                                    <td className="rp__num">
                                      {r.currencies.reduce((s, c) => s + c.payment_count, 0)}
                                    </td>
                                    <td className="rp__num">{fmt(r.total_booked_inr)}</td>
                                    <td className="rp__num">{fmt(r.total_actual_inr)}</td>
                                    <td className="rp__num">
                                      {r.total_fx_cost !== 0
                                        ? `${r.total_fx_cost > 0 ? '+' : ''}${fmt(r.total_fx_cost)}`
                                        : '—'}
                                    </td>
                                    <td colSpan={2} />
                                  </tr>
                                </tfoot>
                              </table>
                            </div>
                          </div>
                        )}

                        {r.monthly_trend.length > 0 && (
                          <div className="rp__card">
                            <div className="rp__card-hdr">
                              <div>
                                <div className="rp__card-title">Monthly FX Payment Trend</div>
                                <div className="rp__card-sub">Actual INR settled per month</div>
                              </div>
                            </div>
                            <div className="rp__rows">
                              {r.monthly_trend.map(m => {
                                const maxAmt = Math.max(...r.monthly_trend.map(t => t.actual_inr), 1)
                                const monthLabel = new Date(m.month + '-01').toLocaleDateString('en-IN', {
                                  month: 'long',
                                  year: 'numeric',
                                })
                                return (
                                  <div className="rp__row rp-t--indigo" key={m.month}>
                                    <div className="rp__row-top">
                                      <div className="rp__row-id">
                                        <div className="rp__sqmark" />
                                        <span className="rp__row-name">{monthLabel}</span>
                                      </div>
                                      <div className="rp__row-id">
                                        <span className="rp__row-pct">{m.payment_count} pmts</span>
                                        <span className="rp__row-amt">{fmt(m.actual_inr)}</span>
                                      </div>
                                    </div>
                                    <Meter value={m.actual_inr} max={maxAmt} />
                                  </div>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {hasCurrencies && r.currencies.some(c => c.avg_bank_spread_pct != null) && (
                          <div className="rp__card">
                            <div className="rp__card-hdr">
                              <div>
                                <div className="rp__card-title">Bank Spread by Currency</div>
                                <div className="rp__card-sub">Average bank markup over RBI reference rate</div>
                              </div>
                            </div>
                            <div className="rp__spreads">
                              {r.currencies
                                .filter(c => c.avg_bank_spread_pct != null)
                                .map(c => {
                                  const spread = c.avg_bank_spread_pct!
                                  const isHigh = spread > 1
                                  return (
                                    <div
                                      className={`rp__bucket ${isHigh ? 'rp-t--danger' : 'rp-t--muted'}`}
                                      key={c.currency_code}
                                    >
                                      <div className="rp__bucket-lbl">
                                        {fxSym(c.currency_code)} {c.currency_code}
                                      </div>
                                      <div className="rp__bucket-val">{spread.toFixed(2)}%</div>
                                      <div className="rp__bucket-sub">
                                        avg spread · {c.payment_count} payment
                                        {c.payment_count === 1 ? '' : 's'}
                                      </div>
                                      {isHigh && (
                                        <div className="rp__note">Above 1% — consider negotiating with your bank</div>
                                      )}
                                    </div>
                                  )
                                })}
                            </div>
                          </div>
                        )}

                        {!hasCurrencies && (
                          <div className="rp__card">
                            <Empty
                              title="No FX payments this quarter"
                              sub="Record FX payments on foreign currency bills to see cost analysis here."
                            />
                          </div>
                        )}
                      </>
                    )
                  })()
                ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
