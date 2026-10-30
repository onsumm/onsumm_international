/**
 * USDC amounts as the backend sends them: strings of integer token units
 * (6 decimals, so "3600000000" is 3,600 USDC). Kept as strings and BigInt so a
 * large payout never passes through a float. The INR formatters in ui/format
 * are deliberately not reused — lakh grouping and a ₹ sign are wrong here.
 */

export const USDC_DECIMALS = 6

export function unitsToBigInt(units: string | number | null | undefined): bigint {
  if (units === null || units === undefined || units === '') return 0n
  try {
    return BigInt(typeof units === 'number' ? Math.trunc(units) : units)
  } catch {
    return 0n
  }
}

/** "3600000000" → "3,600.00". Rounds toward zero at `dp` decimals. */
export function formatUnits(units: string | number | null | undefined, dp = 2, decimals = USDC_DECIMALS): string {
  const v = unitsToBigInt(units)
  const neg = v < 0n
  const abs = neg ? -v : v
  const base = 10n ** BigInt(decimals)
  const whole = abs / base
  const frac = (abs % base).toString().padStart(decimals, '0').slice(0, dp)
  const grouped = whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${neg ? '-' : ''}${grouped}${dp > 0 ? `.${frac}` : ''}`
}

export function formatUsdc(units: string | number | null | undefined, dp = 2): string {
  return `${formatUnits(units, dp)} USDC`
}

/** "3600.5" → "3600500000". Throws on more than 6 decimals or a non-number. */
export function parseUnits(amount: string, decimals = USDC_DECIMALS): string {
  const s = amount.trim()
  if (!/^\d+(\.\d+)?$/.test(s)) throw new Error('Enter a number')
  const [whole, frac = ''] = s.split('.')
  if (frac.length > decimals) throw new Error(`At most ${decimals} decimals`)
  return (BigInt(whole) * 10n ** BigInt(decimals) + BigInt(frac.padEnd(decimals, '0') || '0')).toString()
}

/** Fiat strings from the backend ("3600.00") with grouping and the currency code. */
export function formatFiat(amount: string | number | null | undefined, currency = 'USD'): string {
  if (amount === null || amount === undefined || amount === '') return '—'
  const n = typeof amount === 'number' ? amount : Number(amount)
  if (Number.isNaN(n)) return String(amount)
  return `${currency} ${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function shortAddress(addr: string | null | undefined): string {
  if (!addr) return '—'
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr
}

export function isAddress(addr: string): boolean {
  return /^0x[0-9a-fA-F]{40}$/.test(addr.trim())
}

export const EXPLORER = 'https://sepolia.basescan.org'

export function explorerTx(hash: string): string {
  return `${EXPLORER}/tx/${hash}`
}

export function explorerAddress(addr: string): string {
  return `${EXPLORER}/address/${addr}`
}
