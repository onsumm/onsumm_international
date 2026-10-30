/**
 * Locale-aware formatters.
 *
 * Centralised so a non-INR entity or a non-en-IN locale is a config change
 * rather than a hunt through ~40 inline `Intl.NumberFormat` calls — several of
 * which hardcoded `currency: 'INR'` even on the FX screens (audit finding E9).
 */

export const DEFAULT_LOCALE = 'en-IN'
export const DEFAULT_CURRENCY = 'INR'

/**
 * The signed-in tenant's display currency and locale.
 *
 * Set once from the app root (`<DisplayFormatSync>` in App.tsx) from the
 * tenant's jurisdiction (auth/jurisdiction.ts). Every formatter below uses it
 * when the caller names no currency/locale, so an amount with no currency of
 * its own shows in the tenant's books currency: ₹ and en-IN grouping for an
 * Indian tenant (exactly as before), $ for a tenant like Lumen Labs that keeps
 * USD books. An amount that is genuinely in one currency (an IMPS payment is
 * always INR) passes `currency` explicitly and ignores this.
 */
let display = { currency: DEFAULT_CURRENCY, locale: DEFAULT_LOCALE }

export function setDisplayFormat(next: { currency: string; locale: string }): void {
  display = { currency: (next.currency || DEFAULT_CURRENCY).toUpperCase(), locale: next.locale || DEFAULT_LOCALE }
}

export function displayCurrency(): string {
  return display.currency
}

export function displayLocale(): string {
  return display.locale
}

/** Digit grouping for an amount: Indian (1,00,000) only for INR. */
export function localeForCurrency(currency: string): string {
  if (currency === 'INR') return 'en-IN'
  return display.locale === 'en-IN' ? 'en-US' : display.locale
}

const moneyCache = new Map<string, Intl.NumberFormat>()

function moneyFormatter(currency: string, maxFrac: number, locale: string): Intl.NumberFormat {
  const key = `${locale}|${currency}|${maxFrac}`
  let f = moneyCache.get(key)
  if (!f) {
    f = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      maximumFractionDigits: maxFrac,
      minimumFractionDigits: maxFrac === 0 ? 0 : 2,
    })
    moneyCache.set(key, f)
  }
  return f
}

export interface MoneyOptions {
  currency?: string
  locale?: string
  /** Whole rupees for dashboards; paise for anything reconciled or paid. */
  precise?: boolean
  /** Rendered when the value is null/undefined. */
  fallback?: string
}

export function formatMoney(value: number | null | undefined, opts: MoneyOptions = {}): string {
  const { currency = display.currency, precise = false, fallback = '—' } = opts
  const locale = opts.locale ?? localeForCurrency(currency)
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return moneyFormatter(currency, precise ? 2 : 0, locale).format(value)
}

/**
 * Compact form for axis labels and dense tiles: ₹1.2Cr, ₹3.4L, ₹56K for INR;
 * $1.2M, $3.4K, $56 for other currencies (lakh and crore mean nothing there).
 */
export function formatMoneyCompact(value: number | null | undefined, opts: MoneyOptions = {}): string {
  const { currency = display.currency, fallback = '—' } = opts
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (currency !== 'INR') {
    const sym = currencySymbol(currency) || `${currency} `
    if (abs >= 1e9) return `${sign}${sym}${(abs / 1e9).toFixed(abs >= 1e10 ? 0 : 1)}B`
    if (abs >= 1e6) return `${sign}${sym}${(abs / 1e6).toFixed(abs >= 1e7 ? 0 : 1)}M`
    if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(0)}K`
    return `${sign}${sym}${abs.toFixed(0)}`
  }
  const sym = '₹'
  // Indian numbering: crore (10^7) and lakh (10^5) are what finance teams read.
  if (abs >= 1e7) return `${sign}${sym}${(abs / 1e7).toFixed(abs >= 1e8 ? 0 : 1)}Cr`
  if (abs >= 1e5) return `${sign}${sym}${(abs / 1e5).toFixed(abs >= 1e6 ? 0 : 1)}L`
  if (abs >= 1e3) return `${sign}${sym}${(abs / 1e3).toFixed(0)}K`
  return `${sign}${sym}${abs.toFixed(0)}`
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  USD: '$',
  EUR: '€',
  GBP: '£',
  JPY: '¥',
  INR: '₹',
  AED: 'د.إ',
  SGD: 'S$',
}

export function currencySymbol(code: string | null | undefined): string {
  return CURRENCY_SYMBOLS[code ?? ''] ?? ''
}

export function formatDate(
  value: string | Date | null | undefined,
  opts: { locale?: string; fallback?: string; withYear?: boolean } = {},
): string {
  const { locale = display.locale, fallback = '—', withYear = true } = opts
  if (!value) return fallback
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toLocaleDateString(locale, {
    day: '2-digit',
    month: 'short',
    ...(withYear ? { year: 'numeric' } : {}),
  })
}

export function formatDateTime(
  value: string | Date | null | undefined,
  opts: { locale?: string; fallback?: string } = {},
): string {
  const { locale = display.locale, fallback = '—' } = opts
  if (!value) return fallback
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return fallback
  return d.toLocaleString(locale, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** "3 days ago" / "in 5 days". Anchored so it can be tested deterministically. */
export function formatRelativeDays(
  value: string | Date | null | undefined,
  now: Date = new Date(),
): string | null {
  if (!value) return null
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return null
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(d) - startOf(now)) / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return 'tomorrow'
  if (days === -1) return 'yesterday'
  return days > 0 ? `in ${days} days` : `${Math.abs(days)} days ago`
}

/** Turn PENDING_ACCOUNTANT into "Pending Accountant". */
export function humanise(value: string | null | undefined, fallback = '—'): string {
  if (!value) return fallback
  return value
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase())
}
