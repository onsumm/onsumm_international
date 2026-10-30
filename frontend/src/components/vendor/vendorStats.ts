import type { Bill } from '../../api/invoices'

/**
 * Numbers the vendor card shows, worked out from the vendor's bills.
 *
 * Pure: no React, no fetching, so the card and its tests read the same thing.
 * A bill counts in the month of its bill date (invoice_date), not when it was
 * uploaded, because "what did they bill us in July" is the question asked.
 */

export const STAGES = ['Captured', 'In approval', 'Approved', 'Paid'] as const

const DAY = 864e5

// ── Books currency ───────────────────────────────────────────────────────────
// The currency the tenant keeps its books in: INR for Indian tenants, USD for
// one like Lumen Labs. Every sum and "converted" check below is relative to it.
// Module-level so these helpers stay pure and their many call sites unchanged;
// the vendor screens set it from useJurisdiction() as they render.
let BOOKS = 'INR'
let BOOKS_LOCALE = 'en-IN'
let booksFmt: Intl.NumberFormat | null = null

export function setBooksCurrency(code: string | null | undefined, locale?: string | null): void {
  const c = (code || 'INR').trim().toUpperCase() || 'INR'
  const l = c === 'INR' ? 'en-IN' : (locale && locale !== 'en-IN' ? locale : 'en-US')
  if (c === BOOKS && l === BOOKS_LOCALE) return
  BOOKS = c
  BOOKS_LOCALE = l
  booksFmt = null
}

export function booksCurrency(): string {
  return BOOKS
}

/** Bills that are still real: not deleted, rejected or cancelled. */
export function liveBills(bills: Bill[]): Bill[] {
  return bills.filter(b => {
    if (b.soft_status && b.soft_status !== 'active') return false
    const s = (b.status || '').toUpperCase()
    return s !== 'REJECTED' && s !== 'CANCELLED'
  })
}

/** The bill's currency, upper-cased; a blank code is in the books currency. */
export function currencyOf(b: Bill): string {
  return (b.currency_code || BOOKS).trim().toUpperCase() || BOOKS
}

/**
 * A bill in a currency other than the books currency whose books-currency
 * value we don't know yet. Once the pipeline converts one it either fills
 * inr_amount_current or rewrites total_amount in the books currency and keeps
 * the invoice figure in original_amount; with neither, total_amount is still in
 * the bill's own currency and summing it would be wrong.
 */
export function isUnconverted(b: Bill): boolean {
  if (currencyOf(b) === BOOKS || b.inr_amount_current != null) return false
  return !(b.original_amount != null && b.total_amount != null && Number(b.original_amount) !== Number(b.total_amount))
}

/** The bill's value in the books currency, for sums. 0 for an unconverted foreign bill: it's left out, never counted at face value. */
export function billAmount(b: Bill): number {
  if (isUnconverted(b)) return 0
  return Number(b.inr_amount_current ?? b.total_amount ?? 0) || 0
}

/** The amount as written on the bill, in its own currency. */
export function foreignAmount(b: Bill): number {
  return Number(b.original_amount ?? b.total_amount ?? 0) || 0
}

export function isPaid(b: Bill): boolean {
  return (b.payment_status || '').toLowerCase() === 'paid' || (b.status || '').toUpperCase() === 'PAID'
}

export function outstandingOf(b: Bill): number {
  if (isPaid(b) || isUnconverted(b)) return 0
  return b.outstanding_amount != null ? Number(b.outstanding_amount) : billAmount(b)
}

/** Still to be paid. An unconverted foreign bill has no books-currency outstanding but is open all the same. */
export function isOpen(b: Bill): boolean {
  if (isPaid(b)) return false
  return isUnconverted(b) ? foreignAmount(b) > 0 : outstandingOf(b) > 0
}

/** Where a bill is on its way to being paid: an index into STAGES. */
export function stageOf(b: Bill): number {
  if (isPaid(b)) return 3
  const s = (b.status || '').toUpperCase()
  if (s === 'APPROVED') return 2
  if (s.startsWith('PENDING') || s === 'NEEDS_REVISION') return 1
  return 0
}

export interface BillState {
  tone: 'ok' | 'bad' | 'warn' | 'info' | 'plain'
  label: string
  /** Days past the due date, 0 when not late. */
  late: number
  /** Late and still with us (review or approval), not with payments. */
  blocked: boolean
}

/** Approved, unpaid and past due: a payment we've missed. The only bills called "overdue". */
export const isOverdue = (b: Bill, now: Date) => billState(b, now).tone === 'bad'
/** Past due but still in review or approval: held up on our side, not payable yet. */
export const isLateInApproval = (b: Bill, now: Date) => billState(b, now).blocked

/**
 * One line on where a bill is and who has to act. A late bill that hasn't
 * been approved is our hold-up, not a missed payment, so it reads as "In
 * approval · 111d late" in amber rather than a red "overdue".
 */
export function billState(b: Bill, now: Date): BillState {
  if (isPaid(b)) return { tone: 'ok', label: 'Paid', late: 0, blocked: false }
  const due = dayOf(b.due_date)
  const late = due && due < now ? daysBetween(due, now) : 0
  const st = stageOf(b)
  if (st < 2) {
    const step = st === 0 ? 'Needs review' : 'In approval'
    return { tone: 'warn', label: late ? `${step} · ${late}d late` : step, late, blocked: late > 0 }
  }
  if (late) return { tone: 'bad', label: `Overdue ${late}d`, late, blocked: false }
  if ((b.payment_status || '').toLowerCase() === 'partial') return { tone: 'info', label: 'Part paid', late: 0, blocked: false }
  if (!due) return { tone: 'plain', label: 'No due date', late: 0, blocked: false }
  return { tone: 'plain', label: `Due ${shortDate(b.due_date)}`, late: 0, blocked: false }
}

/** Local calendar date at noon, so day arithmetic never trips on time zones. */
export function dayOf(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const d = new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso)
  if (Number.isNaN(d.getTime())) return null
  d.setHours(12, 0, 0, 0)
  return d
}

export function daysBetween(a: Date, b: Date): number {
  return Math.round((b.getTime() - a.getTime()) / DAY)
}

const ymOf = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`

export interface MonthTotal {
  ym: string
  year: number
  /** 0–11 */
  month: number
  bills: Bill[]
  total: number
  paid: number
  outstanding: number
  /** The month we are in, so its total is still growing. */
  partial: boolean
}

/**
 * One entry per month from the vendor's first bill to this month, gaps
 * included as zero: a month with no bills is information, not a missing bar.
 */
export function monthlyTotals(bills: Bill[], today: Date): MonthTotal[] {
  const dated = bills.map(b => ({ b, d: dayOf(b.invoice_date) })).filter((x): x is { b: Bill; d: Date } => !!x.d)
  if (!dated.length) return []
  const first = dated.reduce((m, x) => (x.d < m ? x.d : m), dated[0].d)
  const out: MonthTotal[] = []
  const cur = new Date(first.getFullYear(), first.getMonth(), 1, 12)
  const end = new Date(today.getFullYear(), today.getMonth(), 1, 12)
  const nowYm = ymOf(today)
  while (cur <= end) {
    const ym = ymOf(cur)
    const bs = dated.filter(x => ymOf(x.d) === ym).map(x => x.b)
    out.push({
      ym, year: cur.getFullYear(), month: cur.getMonth(), bills: bs,
      total: bs.reduce((s, b) => s + billAmount(b), 0),
      paid: bs.filter(isPaid).reduce((s, b) => s + billAmount(b), 0),
      outstanding: bs.reduce((s, b) => s + outstandingOf(b), 0),
      partial: ym === nowYm,
    })
    cur.setMonth(cur.getMonth() + 1)
  }
  return out
}

export interface Forecast {
  ok: boolean
  /** Consecutive full months, ending last month, that each had bills. */
  months: number
  /** Without a forecast: 'stopped' when last month had no bills at all, else 'short'. */
  reason?: 'short' | 'stopped'
  /** First month (ym) of that unbroken run. */
  since?: string
  /** The latest month (ym) that had any bills. */
  lastBilled?: string
  /** Expected total for [this month, next month]. */
  expected?: [number, number]
  /** Half-width of the ~80% likely range for each. */
  band?: [number, number]
  method?: string
  confidence?: 'Low' | 'Medium' | 'High'
  /** Typical past error, in percent. */
  within?: number
  /**
   * True when `within` comes from checking the model against months it hadn't
   * seen. With 3–5 months there's nothing to check against, so `within` is
   * only the width of the likely range and mustn't be read as a track record.
   */
  backtested?: boolean
}

/** How many months in a row, ending last month, need bills before we forecast. */
export const FORECAST_MIN_MONTHS = 3

const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0)
const sd = (a: number[]) => { const m = mean(a); return Math.sqrt(mean(a.map(x => (x - m) ** 2))) }

/**
 * Expected billing for this month and next.
 *
 * Only forecasts from recent, unbroken history: each of the last 3 full months
 * (the 3 before this one) must have bills. A vendor who billed for six months
 * and then stopped, or skipped a month recently, gets no forecast; projecting
 * from an old or broken run would be a guess dressed up as a number.
 *
 * The model uses only that unbroken run. 3–5 months: a weighted average of
 * the last three (3:2:1, recent months count more). 6+: Holt's linear
 * smoothing, which follows a trend. The likely range is ±1.28 standard
 * deviations of the method's past errors (about 80%), widened for the month
 * further out.
 */
export function forecast(series: MonthTotal[]): Forecast {
  const complete = series.filter(m => !m.partial)
  let start = complete.length
  while (start > 0 && complete[start - 1].bills.length > 0) start--
  const run = complete.slice(start)
  const n = run.length
  // This month counts here: a bill on the 3rd means they haven't stopped, even
  // though the month isn't over and so can't start a run.
  const lastBilled = [...series].reverse().find(m => m.bills.length > 0)?.ym
  if (n < FORECAST_MIN_MONTHS) {
    return { ok: false, months: n, reason: n === 0 ? 'stopped' : 'short', since: run[0]?.ym, lastBilled }
  }
  const ys = run.map(m => m.total)
  const since = run[0].ym
  if (n < 6) {
    const [a, b, c] = ys.slice(-3)
    const f = (a + 2 * b + 3 * c) / 6
    const w = Math.max(1.28 * sd(ys.slice(-3)), 0.08 * f)
    return {
      ok: true, months: n, since, lastBilled, expected: [f, f], band: [w, w * Math.SQRT2],
      method: 'Weighted average of the last 3 months, recent months counting more',
      confidence: 'Low', within: f ? Math.round((w / f) * 100) : 0, backtested: false,
    }
  }
  const A = 0.5, B = 0.3
  let level = ys[0], trend = ys[1] - ys[0]
  const errs: number[] = [], pct: number[] = []
  for (let t = 1; t < n; t++) {
    const pred = level + trend
    if (t >= 2) {
      errs.push(ys[t] - pred)
      if (ys[t]) pct.push(Math.abs(ys[t] - pred) / ys[t])
    }
    const nl = A * ys[t] + (1 - A) * (level + trend)
    trend = B * (nl - level) + (1 - B) * trend
    level = nl
  }
  const rmse = Math.sqrt(mean(errs.map(e => e * e)))
  const mape = mean(pct) * 100
  const f1 = Math.max(0, level + trend), f2 = Math.max(0, level + 2 * trend)
  return {
    ok: true, months: n, since, lastBilled, expected: [f1, f2], band: [1.28 * rmse, 1.28 * rmse * Math.SQRT2],
    method: `Trend model (Holt’s linear smoothing) over the last ${n} months`,
    confidence: mape < 8 ? 'High' : mape < 15 ? 'Medium' : 'Low', within: Math.round(mape), backtested: pct.length > 0,
  }
}

export interface Bucket {
  key: 'overdue' | 'week' | 'later' | 'approval'
  label: string
  bills: Bill[]
  amount: number
}

/**
 * Open bills grouped by when the money goes out. Every open bill lands in
 * exactly one bucket, so the buckets add up to the outstanding total. Overdue
 * is billState's: approved and past due. A late bill still in approval can't
 * be paid yet, so it waits under Awaiting approval with the rest.
 */
export function paymentBuckets(open: Bill[], today: Date): Bucket[] {
  const due = (b: Bill) => dayOf(b.due_date)
  const make = (key: Bucket['key'], label: string, bills: Bill[]): Bucket =>
    ({ key, label, bills, amount: bills.reduce((s, b) => s + outstandingOf(b), 0) })
  const approved = open.filter(b => stageOf(b) >= 2 && !isOverdue(b, today))
  return [
    make('overdue', 'Overdue', open.filter(b => isOverdue(b, today))),
    make('week', 'Due in 7 days', approved.filter(b => !!due(b) && daysBetween(today, due(b)!) <= 7)),
    make('later', 'Due later', approved.filter(b => !due(b) || daysBetween(today, due(b)!) > 7)),
    make('approval', 'Awaiting approval', open.filter(b => stageOf(b) < 2)),
  ]
}

export interface VendorSummary {
  bills: Bill[]
  series: MonthTotal[]
  forecast: Forecast
  open: Bill[]
  /** Approved, unpaid and past due, oldest first. */
  overdue: Bill[]
  /** Past due but still in review or approval, oldest first. */
  lateInApproval: Bill[]
  /** Foreign-currency bills with no ₹ value yet: listed, but left out of every total. */
  unconverted: Bill[]
  nextDue: Bill | null
  paid: Bill[]
  buckets: Bucket[]
  outstanding: number
  /** Billed over the last 12 months, this month included. */
  billed12: number
  /** Bills dated in the last 12 months, this month included. */
  bills12: number
  /** Average over the last 12 full months that the vendor has been billing; 0 when there are none yet. */
  avgMonth: number
  /** How many full months avgMonth is over: 0 for a vendor whose first bill is this month. */
  avgMonths: number
  /** Last 3 full months vs the 3 before, in percent; null without 6 months. */
  quarterDelta: number | null
  /** Average days from bill date to paid, over paid bills with a payment date. */
  daysToPay: number | null
  billsPerMonth: number
}

export function summarize(allBills: Bill[], today: Date): VendorSummary {
  const bills = liveBills(allBills)
  const series = monthlyTotals(bills, today)
  const complete = series.filter(m => !m.partial)
  const open = bills.filter(isOpen)
    .sort((a, b) => (dayOf(a.due_date)?.getTime() ?? Infinity) - (dayOf(b.due_date)?.getTime() ?? Infinity))
  const overdue = open.filter(b => isOverdue(b, today))
  const avgOver = complete.slice(-12)
  const nextDue = open.find(b => { const d = dayOf(b.due_date); return !!d && d >= today }) ?? null
  const paid = bills.filter(isPaid)
  const last3 = complete.slice(-3), prev3 = complete.slice(-6, -3)
  const sum = (ms: MonthTotal[]) => ms.reduce((s, m) => s + m.total, 0)
  const paidDays = paid
    .map(b => { const a = dayOf(b.invoice_date), p = dayOf(b.payment_updated_at); return a && p ? daysBetween(a, p) : null })
    .filter((x): x is number => x != null && x >= 0)
  return {
    bills, series, forecast: forecast(series), open, overdue, nextDue, paid,
    lateInApproval: open.filter(b => isLateInApproval(b, today)),
    unconverted: bills.filter(isUnconverted),
    buckets: paymentBuckets(open, today),
    outstanding: open.reduce((s, b) => s + outstandingOf(b), 0),
    billed12: sum(series.slice(-12)),
    bills12: series.slice(-12).reduce((n, m) => n + m.bills.length, 0),
    avgMonth: mean(avgOver.map(m => m.total)),
    avgMonths: avgOver.length,
    quarterDelta: prev3.length === 3 && sum(prev3) > 0 ? (sum(last3) / sum(prev3) - 1) * 100 : null,
    daysToPay: paidDays.length ? Math.round(mean(paidDays)) : null,
    billsPerMonth: mean(last3.map(m => m.bills.length)),
  }
}

// ── Formatting ───────────────────────────────────────────────────────────────
/** An amount in the books currency, whole units: "₹4,20,000" or "$4,200". */
export const money = (n: number) => {
  booksFmt ??= new Intl.NumberFormat(BOOKS_LOCALE, { style: 'currency', currency: BOOKS, maximumFractionDigits: 0 })
  return booksFmt.format(Math.round(n))
}

/** An amount in any currency by its code, e.g. "USD 5,000", so it can't be mistaken for rupees. */
export function moneyIn(n: number, code: string): string {
  try {
    return new Intl.NumberFormat(BOOKS_LOCALE, { style: 'currency', currency: code, currencyDisplay: 'code', minimumFractionDigits: 0, maximumFractionDigits: 2 })
      .format(n).replace(/\u00a0/g, ' ')
  } catch {
    // A code Intl doesn't know.
    return `${code} ${new Intl.NumberFormat(BOOKS_LOCALE, { maximumFractionDigits: 2 }).format(n)}`
  }
}

/** What a bill's amount reads as: the books currency when we know it, else its own currency, marked as not converted. */
export function billMoney(b: Bill, amount = billAmount(b)): string {
  return isUnconverted(b) ? `${moneyIn(foreignAmount(b), currencyOf(b))} · not converted` : money(amount)
}

/** "1 bill", "3 bills". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** "Bills in USD", "Bills in USD and EUR": the note that some bills aren't in a total. */
export function unconvertedNote(bills: Bill[]): string {
  if (!bills.length) return ''
  const codes = [...new Set(bills.map(currencyOf))]
  const list = codes.length > 1 ? `${codes.slice(0, -1).join(', ')} and ${codes[codes.length - 1]}` : codes[0]
  return `${plural(bills.length, 'bill')} in ${list} not converted to ${BOOKS === 'INR' ? '₹' : BOOKS}, so not in the totals`
}

/**
 * ₹2.88L, ₹94K, ₹1.2Cr — Indian units, for chart axes and tiles. For a tenant
 * whose books aren't in INR: $2.9M, $94K, $1.2B.
 */
export function compactINR(n: number, dp = 2): string {
  const a = Math.abs(n)
  const trim = (s: string) => s.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
  if (BOOKS !== 'INR') {
    const sym = new Intl.NumberFormat(BOOKS_LOCALE, { style: 'currency', currency: BOOKS, maximumFractionDigits: 0 })
      .formatToParts(0).find(p => p.type === 'currency')?.value ?? `${BOOKS} `
    if (a >= 1e9) return `${sym}${trim((n / 1e9).toFixed(dp))}B`
    if (a >= 1e6) return `${sym}${trim((n / 1e6).toFixed(dp))}M`
    if (a >= 1e3) return `${sym}${trim((n / 1e3).toFixed(1))}K`
    return `${sym}${Math.round(n)}`
  }
  if (a >= 1e7) return `₹${trim((n / 1e7).toFixed(dp))}Cr`
  if (a >= 1e5) return `₹${trim((n / 1e5).toFixed(dp))}L`
  // One decimal at most, so ₹2.5K and ₹7.5K axis steps don't round to ₹3K and ₹8K.
  if (a >= 1e3) return `₹${trim((n / 1e3).toFixed(1))}K`
  return `₹${Math.round(n)}`
}

export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const MONTHS_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

export function shortDate(iso: string | null | undefined, withYear = false): string {
  const d = dayOf(iso)
  if (!d) return '—'
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${withYear ? ` ${d.getFullYear()}` : ''}`
}

/** '2026-07' → 'July 2026' (or 'Jul 2026' short). */
export function monthLabel(ym: string | null | undefined, short = false): string {
  if (!ym) return ''
  const [y, m] = ym.split('-').map(Number)
  return `${(short ? MONTHS : MONTHS_FULL)[m - 1]} ${y}`
}
