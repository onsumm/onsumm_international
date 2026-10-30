/**
 * Which country's rules apply to the signed-in tenant (backend:
 * core/jurisdiction.py, served with /api/tenant/entitlements).
 *
 * The app was built for Indian companies paying in rupees, so GSTIN, PAN, TDS,
 * IFSC bank accounts, IMPS, `en-IN` formatting and the ₹ sign were assumed on
 * every screen. Screens ask this instead:
 *
 *   j.packs.gst        show GSTIN fields, GST verification, CGST/SGST/IGST
 *   j.packs.pan        show PAN
 *   j.packs.tds_india  show Indian TDS sections and deductions
 *   j.packs.bank_india show IFSC bank accounts and penny-drop verification
 *   j.packs.imps       offer the IMPS rail
 *   j.packs.msme       show MSME / Udyam rules
 *   formatTenantMoney / formatTenantDate   currency and locale for amounts and dates
 *
 * Unknown or not-yet-loaded is India, which is what every tenant was before
 * this existed — an Indian tenant never sees a difference.
 */
import { formatMoney } from '../ui/format'

export type PackKey = 'gst' | 'pan' | 'tds_india' | 'bank_india' | 'imps' | 'msme'

export interface Jurisdiction {
  profile: 'india' | 'international' | string
  country_code: string
  base_currency: string
  is_resident_india: boolean
  locale: string
  timezone: string
  fiscal_year_start_month: number
  tax_id_label: string
  indirect_tax_label: string
  packs: Record<PackKey, boolean>
  rails: string[]
}

export const INDIA_JURISDICTION: Jurisdiction = {
  profile: 'india',
  country_code: 'IN',
  base_currency: 'INR',
  is_resident_india: true,
  locale: 'en-IN',
  timezone: 'Asia/Kolkata',
  fiscal_year_start_month: 4,
  tax_id_label: 'GSTIN',
  indirect_tax_label: 'GST',
  packs: { gst: true, pan: true, tds_india: true, bank_india: true, imps: true, msme: true },
  rails: ['IMPS'],
}

export function normalizeJurisdiction(raw: unknown): Jurisdiction {
  if (!raw || typeof raw !== 'object') return INDIA_JURISDICTION
  const o = raw as Record<string, unknown>
  const packsRaw = (o.packs ?? {}) as Record<string, unknown>
  const packs = { ...INDIA_JURISDICTION.packs }
  for (const k of Object.keys(packs) as PackKey[]) {
    if (typeof packsRaw[k] === 'boolean') packs[k] = packsRaw[k] as boolean
  }
  const str = (v: unknown, d: string) => (typeof v === 'string' && v ? v : d)
  return {
    profile: str(o.profile, 'india'),
    country_code: str(o.country_code, 'IN').toUpperCase(),
    base_currency: str(o.base_currency, 'INR').toUpperCase(),
    is_resident_india: o.is_resident_india !== false,
    locale: str(o.locale, 'en-IN'),
    timezone: str(o.timezone, 'Asia/Kolkata'),
    fiscal_year_start_month: typeof o.fiscal_year_start_month === 'number' ? o.fiscal_year_start_month : 4,
    tax_id_label: str(o.tax_id_label, 'GSTIN'),
    indirect_tax_label: str(o.indirect_tax_label, 'GST'),
    packs,
    rails: Array.isArray(o.rails) ? (o.rails as unknown[]).map(String) : ['IMPS'],
  }
}

/** Locale to group digits with: Indian grouping (1,00,000) only for INR. */
export function moneyLocale(currency: string, j: Jurisdiction = INDIA_JURISDICTION): string {
  if (currency === 'INR') return 'en-IN'
  return j.locale === 'en-IN' ? 'en-US' : j.locale
}

/**
 * An amount in the tenant's books currency (or `currency` when the amount is
 * in a specific one, e.g. a bill's original currency).
 */
export function formatTenantMoney(
  value: number | null | undefined,
  j: Jurisdiction = INDIA_JURISDICTION,
  opts: { currency?: string; precise?: boolean; fallback?: string } = {},
): string {
  const currency = (opts.currency || j.base_currency || 'INR').toUpperCase()
  return formatMoney(value, {
    currency, locale: moneyLocale(currency, j), precise: opts.precise ?? true, fallback: opts.fallback,
  })
}

/** "26 Oct 2026" in the tenant's locale. */
export function formatTenantDate(
  value: string | number | Date | null | undefined,
  j: Jurisdiction = INDIA_JURISDICTION,
  opts: Intl.DateTimeFormatOptions = { day: '2-digit', month: 'short', year: 'numeric' },
): string {
  if (value == null || value === '') return '—'
  const d = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(j.locale, opts)
}
