import { useLocation, useNavigate } from 'react-router-dom'
import { VENDOR_TABS, type VendorTab } from '../../routes/paths'

/**
 * Bill and vendor cards that open over whatever page you are on.
 *
 * The open card is a query param, not a route: `?open=bill:<invoice_id>` or
 * `?open=vendor:<supplier_id>[:<tab>]`. Any page can link to a record without
 * sending the user to Bills or Vendors first, the link survives a refresh, and
 * Back closes the card and leaves the page underneath where it was.
 *
 * Not `?bill=` — the payment route already uses that to pin a bill.
 */
export const RECORD_PARAM = 'open'

export type RecordRef =
  | { kind: 'bill'; id: string }
  | { kind: 'vendor'; id: string; tab?: VendorTab }

export function formatRecord(ref: RecordRef): string {
  // 'details' is the landing tab, so it stays out of the URL — mirrors vendorPath().
  if (ref.kind === 'vendor' && ref.tab && ref.tab !== 'details') return `vendor:${ref.id}:${ref.tab}`
  return `${ref.kind}:${ref.id}`
}

export function parseRecord(value: string | null): RecordRef | null {
  if (!value) return null
  const [kind, id, tab, ...rest] = value.split(':')
  if (!id || rest.length) return null
  if (kind === 'bill') return tab === undefined ? { kind, id } : null
  if (kind === 'vendor') {
    // An unknown tab still opens the vendor, on its landing tab.
    return VENDOR_TABS.includes(tab as VendorTab) ? { kind, id, tab: tab as VendorTab } : { kind, id }
  }
  return null
}

/** `search` with the open card set to `ref`, or removed when `ref` is null. Other params are kept. */
export function withRecord(search: string, ref: RecordRef | null): string {
  const params = new URLSearchParams(search)
  if (ref) params.set(RECORD_PARAM, formatRecord(ref))
  else params.delete(RECORD_PARAM)
  // ':' is legal in a query string; leaving it unescaped keeps the link readable.
  const q = params.toString().replace(/%3A/gi, ':')
  return q ? `?${q}` : ''
}

/**
 * Points at the control that fixes what the card was opened for — "+ Add
 * Account", "Retry Verification". `targets` are `data-guide` keys tried in
 * order, so a missing control can fall back to the next best one.
 */
export interface CardGuide {
  targets: string[]
  message: string
  /** What to say when the guide lands on a fallback target instead — pointing
   *  at "+ Add Account" with "verify this account" when there is none is wrong. */
  messages?: Record<string, string>
  /** One per opening of the card, so dismissing the guide lasts for that
   *  visit (tab switches included) but not the next one. Set by open(). */
  openId?: string
}

/** Open a bill card with a review finding's suggested corrections on it. */
export interface CardCorrections { findingId: string }

/** Open a vendor card with a form already filled in from a review finding —
 *  the user only checks it and saves. `findingId` is closed once they do. */
export interface CardPrefill {
  findingId: string
  contact?: { name: string; role: string; email: string; phone: string }
}

/** Open a vendor card with a contract's claimed value built into its field. */
export interface CardClaim { findingId: string }

interface CardState {
  recordCard?: boolean
  /** The Review finding this card was opened for — its changes are audited
   *  against it (api/reviewContext.ts). */
  findingId?: string
  guide?: CardGuide
  corrections?: CardCorrections
  prefill?: CardPrefill
  claim?: CardClaim
}

/**
 * Open, close and link to record cards from any component inside the router.
 *
 * Cards are rendered once, by <RecordCardHost /> in the app shell, so a caller
 * only says which record — it never mounts a card itself.
 */
export function useRecordCards() {
  const navigate = useNavigate()
  const location = useLocation()
  const current = parseRecord(new URLSearchParams(location.search).get(RECORD_PARAM))
  const cardState = location.state as CardState | null
  const pushedHere = cardState?.recordCard === true

  const hrefFor = (ref: RecordRef | null) => `${location.pathname}${withRecord(location.search, ref)}`

  const open = (ref: RecordRef, opts: {
    replace?: boolean; guide?: CardGuide; corrections?: CardCorrections; prefill?: CardPrefill; claim?: CardClaim
    findingId?: string
  } = {}) =>
    navigate(hrefFor(ref), {
      replace: opts.replace,
      // A replace (e.g. switching a vendor tab) keeps the marker of the entry it
      // replaces, so close() still knows whether there is a page to go back to.
      // The guide rides in history state, not the URL: a shared link opens the
      // card, not someone else's walkthrough.
      state: opts.replace ? location.state : ({
        recordCard: true,
        findingId: opts.findingId,
        guide: opts.guide && { ...opts.guide, openId: `${Date.now()}-${Math.random().toString(36).slice(2)}` },
        corrections: opts.corrections,
        prefill: opts.prefill,
        claim: opts.claim,
      } satisfies CardState),
    })

  const close = () => {
    // Opened in-app: step back, so Close and Back do the same thing. Opened from
    // a pasted link: there is nothing behind it in this app, so drop the param.
    if (pushedHere) navigate(-1)
    else navigate(hrefFor(null), { replace: true })
  }

  return {
    current,
    guide: cardState?.guide ?? null,
    corrections: cardState?.corrections ?? null,
    prefill: cardState?.prefill ?? null,
    claim: cardState?.claim ?? null,
    findingId: cardState?.findingId ?? null,
    hrefFor,
    open,
    close,
    openBill: (invoiceId: string, opts: { guide?: CardGuide; corrections?: CardCorrections; findingId?: string } = {}) =>
      open({ kind: 'bill', id: invoiceId }, opts),
    openVendor: (supplierId: string, tab?: VendorTab,
                 opts: { guide?: CardGuide; prefill?: CardPrefill; claim?: CardClaim; findingId?: string } = {}) =>
      open({ kind: 'vendor', id: supplierId, tab }, opts),
  }
}
