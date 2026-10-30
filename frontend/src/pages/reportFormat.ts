/**
 * Reports — formatting and scale helpers.
 *
 * Split from reportCharts so that file exports only components: a module mixing
 * the two breaks React Fast Refresh, and these are imported by pages that draw
 * no charts at all.
 */

import { displayCurrency, formatDate, formatMoney, localeForCurrency } from '../ui/format'

/** 'inr' is the money unit: amounts in the tenant's books currency (₹ for Indian tenants). */
export type Unit = 'inr' | 'days' | 'pct'

export const fmt = (n: number) => formatMoney(n)

/** Compact form for axis ticks and dense labels, where ₹1,65,38,376 cannot fit. */
export const fmtC = (n: number) => {
  const cur = displayCurrency()
  // INR keeps its Indian compact form (₹1.7Cr, ₹45L) exactly as before.
  if (cur === 'INR') {
    return n === 0 ? '₹0' : `₹${new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 }).format(n)}`
  }
  const locale = localeForCurrency(cur)
  if (n === 0) return formatMoney(0, { currency: cur, locale })
  return new Intl.NumberFormat(locale, {
    style: 'currency', currency: cur, notation: 'compact', maximumFractionDigits: 1,
  }).format(n)
}

export const fmtDate = (s: string | null) => (s ? formatDate(s) : '—')

/** Ten categorical slots, cycled. The class sets `color`; the mark uses currentColor. */
export const cx = (i: number) => `rp-c${(i % 10) + 1}`
export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export const AXIS_FMT: Record<Unit, (n: number) => string> = {
  inr: fmtC,
  days: n => `${Number.isInteger(n) ? n : n.toFixed(1)}d`,
  pct: n => `${Math.round(n)}%`,
}

export const FULL_FMT: Record<Unit, (n: number) => string> = {
  inr: fmt,
  days: n => `${n.toFixed(1)} days`,
  pct: n => `${n.toFixed(1)}%`,
}

/** Round an axis top up to 1/2/2.5/5 × 10ⁿ so ticks land on readable numbers. */
export function niceCeil(v: number) {
  if (v <= 0) return 1
  const p = 10 ** Math.floor(Math.log10(v))
  const f = v / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p
}
