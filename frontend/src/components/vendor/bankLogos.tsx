import { useEffect, useState } from 'react'
import axios from 'axios'
import { Landmark } from 'lucide-react'
import { fetchAuthedObjectUrl } from '../../api/authedFiles'

/**
 * Bank symbols (no wordmark, transparent PNG), picked by IFSC: the first four
 * letters of an IFSC name the bank (HDFC0001721 → HDFC). The images are kept in
 * S3 and served by the API after login (GET /api/bank-logos/{code}.png), never
 * by a public URL. The list of banks that have one is fetched once, and each
 * image once per session. A bank with no symbol keeps the plain bank icon.
 */

let codesP: Promise<Set<string>> | null = null
function availableCodes(): Promise<Set<string>> {
  codesP ??= axios.get('/api/bank-logos')
    .then(r => new Set<string>(r.data?.codes ?? []))
    // A failed list isn't remembered, so the next card tries again.
    .catch(() => { codesP = null; return new Set<string>() })
  return codesP
}

const images = new Map<string, Promise<string | null>>()
const ready = new Map<string, string | null>()

function imageFor(code: string): Promise<string | null> {
  let p = images.get(code)
  if (!p) {
    p = availableCodes()
      .then(have => (have.has(code) ? fetchAuthedObjectUrl(`/api/bank-logos/${code}.png`) : null))
      .catch(() => null)
    p.then(u => ready.set(code, u))
    images.set(code, p)
  }
  return p
}

/** The bank's symbol as an image URL once loaded; null while loading or when there's none. */
export function useBankLogo(code: string | null): string | null {
  const [src, setSrc] = useState<string | null>(() => (code ? ready.get(code) ?? null : null))
  useEffect(() => {
    if (!code) { setSrc(null); return }
    let live = true
    setSrc(ready.get(code) ?? null)
    imageFor(code).then(u => { if (live) setSrc(u) })
    return () => { live = false }
  }, [code])
  return src
}

/** IFSC bank code → the bank's name, shown beside the symbol and used for name matching. */
export const BANK_NAMES: Record<string, string> = {
  SBIN: 'State Bank of India', HDFC: 'HDFC Bank', ICIC: 'ICICI Bank', UTIB: 'Axis Bank', KKBK: 'Kotak Mahindra Bank',
  YESB: 'Yes Bank', IDIB: 'Indian Bank', IOBA: 'Indian Overseas Bank', PUNB: 'Punjab National Bank', BARB: 'Bank of Baroda',
  CNRB: 'Canara Bank', UBIN: 'Union Bank of India', BKID: 'Bank of India', MAHB: 'Bank of Maharashtra',
  CBIN: 'Central Bank of India', UCBA: 'UCO Bank', PSIB: 'Punjab & Sind Bank', INDB: 'IndusInd Bank',
  IDFB: 'IDFC First Bank', FDRL: 'Federal Bank', SIBL: 'South Indian Bank', KARB: 'Karnataka Bank',
  KVBL: 'Karur Vysya Bank', CIUB: 'City Union Bank', TMBL: 'Tamilnad Mercantile Bank', DLXB: 'Dhanlaxmi Bank',
  RATN: 'RBL Bank', BDBL: 'Bandhan Bank', AUBL: 'AU Small Finance Bank', ESFB: 'Equitas Small Finance Bank',
  SCBL: 'Standard Chartered', HSBC: 'HSBC', CITI: 'Citibank', DBSS: 'DBS Bank', DEUT: 'Deutsche Bank',
  IBKL: 'IDBI Bank', JAKA: 'Jammu & Kashmir Bank', CSBK: 'CSB Bank', NTBL: 'Nainital Bank',
  PYTM: 'Paytm Payments Bank', AIRP: 'Airtel Payments Bank', IPOS: 'India Post Payments Bank', COSB: 'Cosmos Bank',
  SRCB: 'Saraswat Bank', ESMF: 'ESAF Small Finance Bank',
}

const squash = (s: string) => s.toLowerCase().replace(/&/g, 'and').replace(/[^a-z]/g, '').replace(/(limited|ltd|bank)$/g, '')

// Longest names first, so "Bank of India" doesn't claim "Central Bank of India".
const BY_NAME = Object.entries(BANK_NAMES)
  .map(([code, name]) => [code, squash(name)] as const)
  .sort((a, b) => b[1].length - a[1].length)

/** The bank's IFSC code, from the IFSC itself or, failing that, its name ("HDFC BANK LTD" → HDFC). */
export function bankCode(ifsc?: string | null, name?: string | null): string | null {
  const fromIfsc = ifsc?.trim().slice(0, 4).toUpperCase()
  if (fromIfsc && /^[A-Z]{4}$/.test(fromIfsc)) return fromIfsc
  const n = name ? squash(name) : ''
  if (!n) return null
  return BY_NAME.find(([, key]) => n === key || n.startsWith(key))?.[0] ?? null
}

/**
 * The bank's symbol on a small white tile (the same neutral backing for every
 * bank, so a dark symbol still shows on the dark card), then its name. In a
 * light list ('line') it's the symbol alone, beside a name shown elsewhere.
 */
export function BankMark({ ifsc, name, iconSize = 14, variant = 'card' }: {
  ifsc?: string | null
  name?: string | null
  iconSize?: number
  variant?: 'card' | 'line'
}) {
  const code = bankCode(ifsc, name)
  const logo = useBankLogo(code)
  const label = name || (code && BANK_NAMES[code]) || code || 'Bank'
  const mark = logo
    ? <span className={`vc__banklogo${variant === 'line' ? ' vc__banklogo--line' : ''}`} aria-hidden="true"><img src={logo} alt="" /></span>
    : <Landmark size={iconSize} aria-hidden="true" />
  return variant === 'line' ? mark : <>{mark}<span>{label}</span></>
}
