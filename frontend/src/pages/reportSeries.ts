/**
 * Reports — period series.
 *
 * The first Reports build answered "what is the total?". A CFO already knows the
 * total; what they cannot see from a ledger is whether the operation is getting
 * better or worse. This module turns the bill list into per-period metrics so
 * every number on the dashboard can be shown as a trend with a delta against
 * the previous period.
 *
 * Five progressions, each on the same time axis and each switchable between
 * monthly and weekly grain:
 *
 *   1. Spend            — what we committed to, by invoice date.
 *   2. Settlement       — of the money that FELL DUE in the period, how much was
 *                         paid on time, paid late, or is still outstanding.
 *   3. Approval time    — upload → final approval, in days. Our own latency.
 *   4. Payment buffer   — final approval → due date, in days. How much room the
 *                         finance team is left with. Negative means the bill was
 *                         approved after it was already due.
 *   5. Category mix     — share of spend per category, per period.
 *
 * Pure functions over a structural subset of `Bill`, so the arithmetic is unit
 * tested without a network, a clock or a React tree (see reportSeries.test.ts).
 */

export const DAY = 86_400_000

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export type Grain = 'month' | 'week'

/** Bills-list fields the series need. `Bill` satisfies this structurally. */
export interface SeriesBill {
  invoice_id: string
  status: string
  category: string | null
  total_amount: number | null
  amount_paid: number | null
  outstanding_amount: number | null
  payment_status: string | null
  payment_updated_at: string | null
  invoice_date: string | null
  created_at: string | null
  due_date: string | null
}

/**
 * Lifecycle facts for one bill, as distilled from the activity trail.
 *
 * Timing lives here rather than on the bill row because the activity trail is
 * already the system of record for it: every approval, send-back and the desk it
 * happened at is a row in the chain the "View activity" timeline renders. Reports
 * reads that source instead of a second copy denormalised onto the bill.
 */
export interface Lifecycle {
  approved_at: string | null
  send_backs: number
  /** Days the bill waited at each desk before that desk acted. */
  role_days: Record<string, number>
}

export type ActivityIndex = Record<string, Lifecycle>

/** Index activity rows by bill, for the join in `buildPeriods`. */
export function indexActivity(rows: { invoice_id: string; approved_at: string | null; send_backs: number; role_days: Record<string, number> }[]): ActivityIndex {
  const out: ActivityIndex = {}
  for (const r of rows)
    out[r.invoice_id] = {
      approved_at: r.approved_at,
      send_backs: r.send_backs ?? 0,
      role_days: r.role_days ?? {},
    }
  return out
}

export interface Bucket {
  key: string
  /** Axis label — short enough for a 26-tick axis. */
  label: string
  /** Full label for tooltips and screen readers. */
  full: string
  start: number
  /** Exclusive. */
  end: number
}

export interface Period {
  bucket: Bucket

  /** Approved spend committed in the period, by invoice date. */
  spend: number
  billCount: number

  /** Money that fell due in this period, split by how it was actually settled. */
  dueAmt: number
  onTimeAmt: number
  lateAmt: number
  overdueAmt: number
  openAmt: number
  /** Settled, but with no payment timestamp to judge — excluded from the rate. */
  untimedAmt: number
  /** onTime / (onTime + late + overdue). Null when nothing judgeable fell due. */
  onTimePct: number | null

  /** Bills that reached final approval in this period. */
  approvedCount: number
  /** Mean upload → approval, in days. Null when nothing was approved. */
  avgApprovalDays: number | null
  /** Mean approval → due date, in days. Negative = approved after it was due. */
  avgBufferDays: number | null
  /** Bills approved in the period that were already past due when approved. */
  lateApprovals: number
  /** Mean days waited per desk, over bills approved in the period. */
  avgRoleDays: Record<string, number>
  /** Bills approved in the period that were sent back at least once. */
  reworked: number
  /** Share of the period's approvals that needed rework, 0–100. */
  reworkPct: number | null

  byCategory: Record<string, number>
}

const dayFloor = (t: number) => {
  const d = new Date(t)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

const ts = (s: string | null | undefined): number | null => {
  if (!s) return null
  const t = new Date(s).getTime()
  return Number.isFinite(t) ? t : null
}

/** Monday-based week start, matching how finance teams cut a working week. */
export function startOfWeek(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7))
  return x
}

/**
 * `count` contiguous buckets ending with the one containing `now`, oldest first.
 * The current bucket is included and partial — a month-to-date column that reads
 * as a collapse is a reporting artefact, so callers should label it as running.
 */
export function buildBuckets(grain: Grain, count: number, now: Date): Bucket[] {
  const out: Bucket[] = []
  if (grain === 'month') {
    for (let i = count - 1; i >= 0; i--) {
      const s = new Date(now.getFullYear(), now.getMonth() - i, 1)
      const e = new Date(now.getFullYear(), now.getMonth() - i + 1, 1)
      const yy = String(s.getFullYear()).slice(2)
      out.push({
        key: `${s.getFullYear()}-${String(s.getMonth() + 1).padStart(2, '0')}`,
        // A January tick carries the year, so a 12-month axis is unambiguous
        // without repeating "'26" on every column.
        label: s.getMonth() === 0 ? `${MONTHS[s.getMonth()]} '${yy}` : MONTHS[s.getMonth()],
        full: `${MONTHS[s.getMonth()]} 20${yy}`,
        start: s.getTime(),
        end: e.getTime(),
      })
    }
    return out
  }
  const base = startOfWeek(now)
  for (let i = count - 1; i >= 0; i--) {
    const s = new Date(base)
    s.setDate(base.getDate() - i * 7)
    const e = new Date(s)
    e.setDate(s.getDate() + 7)
    const last = new Date(e.getTime() - DAY)
    out.push({
      key: `${s.getFullYear()}-W${s.getMonth() + 1}-${s.getDate()}`,
      label: `${s.getDate()} ${MONTHS[s.getMonth()]}`,
      full: `${s.getDate()} ${MONTHS[s.getMonth()]} – ${last.getDate()} ${MONTHS[last.getMonth()]}`,
      start: s.getTime(),
      end: e.getTime(),
    })
  }
  return out
}

/** Index of the bucket containing `t`, or -1. Buckets are contiguous and sorted. */
export function bucketOf(buckets: Bucket[], t: number | null): number {
  if (t == null || buckets.length === 0) return -1
  if (t < buckets[0].start || t >= buckets[buckets.length - 1].end) return -1
  let lo = 0
  let hi = buckets.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (t < buckets[mid].start) hi = mid - 1
    else if (t >= buckets[mid].end) lo = mid + 1
    else return mid
  }
  return -1
}

const empty = (bucket: Bucket): Period => ({
  bucket,
  spend: 0,
  billCount: 0,
  dueAmt: 0,
  onTimeAmt: 0,
  lateAmt: 0,
  overdueAmt: 0,
  openAmt: 0,
  untimedAmt: 0,
  onTimePct: null,
  approvedCount: 0,
  avgApprovalDays: null,
  avgBufferDays: null,
  lateApprovals: 0,
  avgRoleDays: {},
  reworked: 0,
  reworkPct: null,
  byCategory: {},
})

export function buildPeriods(
  bills: SeriesBill[],
  grain: Grain,
  count: number,
  now: Date = new Date(),
  activity: ActivityIndex = {},
): Period[] {
  const buckets = buildBuckets(grain, count, now)
  const periods = buckets.map(empty)
  const today = dayFloor(now.getTime())

  // Approval, buffer and per-desk waits are means, so they need accumulators.
  const approvalDays: number[][] = buckets.map(() => [])
  const bufferDays: number[][] = buckets.map(() => [])
  const roleTotals: Record<string, number>[] = buckets.map(() => ({}))

  for (const b of bills) {
    const approved = b.status?.toUpperCase() === 'APPROVED'

    // ── 1. Spend and category mix, anchored on the invoice date ──────────────
    // The economic date, not the day someone got round to uploading it. Falls
    // back to created_at because a scanned bill can arrive without one.
    if (approved) {
      const i = bucketOf(buckets, ts(b.invoice_date) ?? ts(b.created_at))
      if (i >= 0) {
        const amt = b.total_amount ?? 0
        periods[i].spend += amt
        periods[i].billCount += 1
        const cat = b.category?.trim() || 'Uncategorised'
        periods[i].byCategory[cat] = (periods[i].byCategory[cat] ?? 0) + amt
      }
    }

    // ── 2. Settlement of what fell due, anchored on the due date ─────────────
    // Split by rupee, not by bill: a part-paid invoice contributes its paid
    // portion to on-time/late and its outstanding portion to overdue/open, so
    // the four bands always add back up to what fell due.
    if (approved && b.due_date) {
      const i = bucketOf(buckets, ts(b.due_date))
      if (i >= 0) {
        const p = periods[i]
        const total = b.total_amount ?? 0
        const paid = b.amount_paid ?? (b.payment_status === 'paid' ? total : 0)
        const out = b.outstanding_amount ?? Math.max(total - paid, 0)
        const due = dayFloor(ts(b.due_date)!)
        p.dueAmt += total
        if (paid > 0) {
          const settled = ts(b.payment_updated_at)
          if (settled == null) p.untimedAmt += paid
          else if (dayFloor(settled) <= due) p.onTimeAmt += paid
          else p.lateAmt += paid
        }
        if (out > 0) {
          if (due < today) p.overdueAmt += out
          else p.openAmt += out
        }
      }
    }

    // ── 3 & 4. Turnaround and per-desk waits, from the activity trail ────────
    // Bucketed by when approval actually happened, not by when the bill arrived,
    // so a period reports the work it actually cleared.
    const life = activity[b.invoice_id]
    const appr = approved ? ts(life?.approved_at) : null
    if (appr != null) {
      const i = bucketOf(buckets, appr)
      if (i >= 0) {
        const p = periods[i]
        p.approvedCount += 1
        if ((life?.send_backs ?? 0) > 0) p.reworked += 1
        const created = ts(b.created_at)
        // Guard against a chain row backdated before the upload: a negative
        // turnaround is a data fault, not a fast approval.
        if (created != null && appr >= created) approvalDays[i].push((appr - created) / DAY)
        const due = ts(b.due_date)
        if (due != null) {
          const buffer = (dayFloor(due) - dayFloor(appr)) / DAY
          bufferDays[i].push(buffer)
          if (buffer < 0) p.lateApprovals += 1
        }
        for (const [role, days] of Object.entries(life?.role_days ?? {}))
          roleTotals[i][role] = (roleTotals[i][role] ?? 0) + days
      }
    }
  }

  const mean = (xs: number[]) => (xs.length === 0 ? null : xs.reduce((s, x) => s + x, 0) / xs.length)

  periods.forEach((p, i) => {
    p.avgApprovalDays = mean(approvalDays[i])
    p.avgBufferDays = mean(bufferDays[i])
    const judged = p.onTimeAmt + p.lateAmt + p.overdueAmt
    p.onTimePct = judged > 0 ? (p.onTimeAmt / judged) * 100 : null
    // Per-desk means share the approval count as denominator, so the stacked
    // waits add up to the period's average turnaround rather than to something
    // taller than it.
    if (p.approvedCount > 0) {
      p.reworkPct = (p.reworked / p.approvedCount) * 100
      for (const [role, total] of Object.entries(roleTotals[i]))
        p.avgRoleDays[role] = total / p.approvedCount
    }
  })

  return periods
}

/**
 * Desks that appear in the window, busiest first.
 *
 * Ranked over the whole window so a stage keeps its colour and stacking position
 * from column to column — the same reason `categoryShares` ranks globally.
 */
export function activeRoles(periods: Period[]): string[] {
  const totals: Record<string, number> = {}
  for (const p of periods)
    for (const [role, days] of Object.entries(p.avgRoleDays)) totals[role] = (totals[role] ?? 0) + days
  return Object.entries(totals)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([role]) => role)
}

/** Trailing mean over `w` periods — the trend under a noisy bar. */
export function movingAverage(values: number[], w = 3): number[] {
  return values.map((_, i) => {
    const from = Math.max(0, i - w + 1)
    const win = values.slice(from, i + 1)
    return win.reduce((s, x) => s + x, 0) / win.length
  })
}

export interface ShareSeries {
  label: string
  /** Share of the period's spend, 0–100. */
  values: number[]
  /** Absolute amount per period, for the readout. */
  amounts: number[]
}

/**
 * Category mix over time, as shares of each period's spend.
 *
 * Ranked by total spend across the whole window rather than per period, so a
 * band keeps its colour and its stacking position from column to column — a mix
 * chart that reshuffles its own order cannot be read as a trend. The tail is
 * rolled into one "Others" band; it is never dropped, so every column still
 * sums to 100%.
 */
export function categoryShares(periods: Period[], topN = 5): ShareSeries[] {
  const totals: Record<string, number> = {}
  for (const p of periods)
    for (const [cat, amt] of Object.entries(p.byCategory)) totals[cat] = (totals[cat] ?? 0) + amt

  const ranked = Object.entries(totals)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
  if (ranked.length === 0) return []

  const head = ranked.slice(0, topN).map(([cat]) => cat)
  const hasTail = ranked.length > topN + 1
  const labels = hasTail ? [...head, `Others (${ranked.length - topN})`] : ranked.map(([c]) => c)
  const named = hasTail ? head : labels

  return labels.map((label, li) => {
    const amounts = periods.map(p => {
      if (hasTail && li === labels.length - 1)
        return Object.entries(p.byCategory).reduce(
          (s, [cat, amt]) => (named.includes(cat) ? s : s + amt),
          0,
        )
      return p.byCategory[label] ?? 0
    })
    return {
      label,
      amounts,
      values: amounts.map((amt, i) => (periods[i].spend > 0 ? (amt / periods[i].spend) * 100 : 0)),
    }
  })
}

/**
 * Change from the previous period to the last one.
 *
 * `pct` is null when the baseline is zero — a jump from nothing is not an
 * infinite improvement, and printing "+∞%" is worse than printing nothing.
 */
export function delta(values: (number | null)[]): { curr: number | null; prev: number | null; pct: number | null } {
  const curr = values[values.length - 1] ?? null
  const prev = values[values.length - 2] ?? null
  const pct = prev != null && prev !== 0 && curr != null ? ((curr - prev) / Math.abs(prev)) * 100 : null
  return { curr, prev, pct }
}
