/**
 * Single source of truth for the app's URL structure.
 *
 * Before this existed, navigation was a `useState<NavPage>` in App.tsx: no
 * shareable links, no browser Back, and a refresh dropped the user back on the
 * role-default page. Every screen now has a real address.
 */

import { ARTICLES, ARTICLE_PATHS } from '../content'

export type NavPage =
  | 'upload'
  | 'bills'
  | 'vendors'
  | 'settings'
  | 'dashboard'
  | 'reports'
  | 'integrations'
  | 'data-extraction'
  | 'nova'
  | 'agent-hub'
  | 'payments'
  | 'review'
  | 'crypto-onboarding'
  | 'onchain'
  | 'audit'

/** Canonical path for each navigable page. */
export const PATHS: Record<NavPage, string> = {
  upload:            '/upload',
  bills:             '/bills',
  dashboard:         '/dashboard',
  vendors:           '/vendors',
  reports:           '/reports',
  'data-extraction': '/data-extraction',
  nova:              '/nova',
  review:            '/review',
  'agent-hub':       '/agent-hub',
  integrations:      '/integrations',
  settings:          '/settings',
  payments:          '/payments',
  // Crypto rail (docs/crypto-rail). The on-chain ledger nests under the
  // Payment Hub; pageForPath's longest-prefix match keeps it its own nav item.
  'crypto-onboarding': '/crypto/onboarding',
  onchain:           '/payments/onchain',
  audit:             '/audit',
}

/**
 * Public crypto-rail pages, outside the signed-in shell: the vendor's wallet
 * portal (authenticated by its own signed link token) and the audit proof
 * checker. Both work signed in or out — an auditor has no Onsumm login.
 */
export const VERIFY_PATH = '/verify'
export const VENDOR_PORTAL_ROUTE = '/vendor-portal/:token'

export function isCryptoPublicPath(pathname: string): boolean {
  const path = pathname.split('?')[0]
  return path === VERIFY_PATH || /^\/vendor-portal\/[^/]+$/.test(path)
}

/** Sub-steps of the upload flow. */
export const UPLOAD_REVIEW_PATH = '/upload/review'
export const UPLOAD_DONE_PATH   = '/upload/done'

/**
 * Records that are worth linking to.
 *
 * Opening a bill or a vendor used to be `useState` inside the page: the panel
 * covered the screen while the address bar still read /bills, so a link to "this
 * vendor's contacts" could not be sent, bookmarked, or reached with Back.
 */
/**
 * Vendor card views. 'details' is the landing Overview. The card shows six:
 * details, spend, documents, conversations, activity, compliance. The older keys stay
 * valid so links already sent out (and Review's guides) still land in the
 * right place: contacts → conversations, bank/gstins/tds → compliance at that
 * section, statements → documents' Bills.
 */
export const VENDOR_TABS = ['details', 'spend', 'documents', 'conversations', 'activity', 'compliance', 'contacts', 'bank', 'gstins', 'tds', 'statements'] as const
export type VendorTab = (typeof VENDOR_TABS)[number]

export function billPath(invoiceId: string): string {
  return `${PATHS.bills}/${invoiceId}`
}

/**
 * Settings sections, so a colleague can be sent straight to the one being
 * discussed instead of "Settings, then the fourth item down".
 */
/**
 * Review Hub views.
 *
 * A finding is a query param (`?f=`), not a path segment, because the drawer
 * overlays a list whose scroll position, filters and selection have to survive
 * being opened and closed — and the same drawer opens from the queue, from
 * Browse, from the bell and from a per-record strip. This matches the existing
 * `payPath(sup, bill)` precedent rather than inventing a second convention.
 */
// 'browse' is a separate, static route (below) rather than a member of this
// set: these three are lifecycle *statuses* of the same bounded queue, and the
// Lifecycle tabs in ReviewHubPage render one button per entry here. Browse is
// a different view entirely — the unbounded, filterable table (design brief
// artboard 8) — so it stays out of this list and off those tabs.
export const REVIEW_VIEWS = ['queue', 'snoozed', 'resolved', 'activity'] as const
export type ReviewView = (typeof REVIEW_VIEWS)[number]

/** 'queue' is the landing view, so it stays out of the URL. */
export function reviewPath(view?: ReviewView): string {
  return view && view !== 'queue' ? `${PATHS.review}/${view}` : PATHS.review
}

export function findingPath(findingId: string, view?: ReviewView): string {
  return `${reviewPath(view)}?f=${encodeURIComponent(findingId)}`
}

/**
 * Browse — the full, unbounded table every open finding lives in, virtualised
 * and filterable (design brief artboard 8). What the bounded queue's
 * "Show everything" line escapes to, rather than widening the queue's own
 * filter in place.
 */
export const REVIEW_BROWSE_PATH = `${PATHS.review}/browse`

export function reviewBrowsePath(): string {
  return REVIEW_BROWSE_PATH
}

/**
 * Deep-link a page at a specific field, with a trail back to the hub.
 *
 * `focus` is read by useFocusTarget(), which scrolls to [data-focus-key] and
 * pulses the focus ring. `from` lets the destination render a "back to Review
 * Hub" bar — without it every deep link ends the triage session.
 */
export function focusPath(target: string, focus?: string, findingId?: string): string {
  const [base, existing] = target.split('?')
  const q = new URLSearchParams(existing || '')
  if (focus) q.set('focus', focus)
  if (findingId) q.set('from', `review:${findingId}`)
  const qs = q.toString()
  return qs ? `${base}?${qs}` : base
}

export const SETTINGS_SECTIONS = [
  'approval-chain', 'roles', 'team', 'charges', 'categories', 'coa', 'tds', 'brands', 'cost-centers', 'fx',
  'demo-payments', 'country',
] as const
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]

export function settingsPath(section?: SettingsSection): string {
  // 'approval-chain' is the landing section, so it stays out of the URL.
  return section && section !== 'approval-chain' ? `${PATHS.settings}/${section}` : PATHS.settings
}

/**
 * The payment flow — the highest-consequence screen in the product, and the one
 * the audit singled out for having no address at all (finding N2, task 5.8).
 * `?bill=` pins a single bill; without it the vendor's whole outstanding list is
 * offered.
 */
export const PAY_ROUTE = `${PATHS.bills}/pay/:supplierId`

/**
 * The payment flow for a vendor, optionally pinned to one bill. `amount`
 * pre-fills that bill's payment (a retry of a failed transfer pays what failed);
 * the flow caps it at the bill's outstanding.
 */
export function payPath(supplierId: string, invoiceId?: string, amount?: number, bank?: string): string {
  const q = new URLSearchParams()
  if (invoiceId) q.set('bill', invoiceId)
  if (invoiceId && amount && amount > 0) q.set('amount', String(amount))
  // The disbursal bank chosen in the Payment Hub: the payment goes through it.
  if (bank) q.set('via', bank)
  const qs = q.toString()
  return `${PATHS.bills}/pay/${supplierId}${qs ? `?${qs}` : ''}`
}

export function vendorPath(supplierId: string, tab?: VendorTab): string {
  // 'details' is the default tab, so it stays out of the URL.
  return tab && tab !== 'details'
    ? `${PATHS.vendors}/${supplierId}/${tab}`
    : `${PATHS.vendors}/${supplierId}`
}

/** Route patterns registered with a param, matched by regex for isKnownRoute. */
const PARAM_ROUTES: RegExp[] = [
  new RegExp(`^${PATHS.bills}/pay/[^/]+$`),
  // Anything but the /pay/ prefix above, which is a flow rather than a record.
  new RegExp(`^${PATHS.bills}/(?!pay/)[^/]+$`),
  new RegExp(`^${PATHS.vendors}/[^/]+(/(${VENDOR_TABS.join('|')}))?$`),
  new RegExp(`^${PATHS.settings}/(${SETTINGS_SECTIONS.join('|')})$`),
  new RegExp(`^${PATHS.review}/(${REVIEW_VIEWS.filter(v => v !== 'queue').join('|')})$`),
]

/** Public marketing routes, available signed-out. */
export const LANDING_PATHS = ['/', '/product', '/pricing', '/contact'] as const

/**
 * Every route that is public, marketing pages and long-form articles together.
 * This is the list the router, the prerenderer and the sitemap all read, so a
 * new article cannot end up rendered but unlisted, or listed but unrendered.
 */
export const PUBLIC_PATHS: readonly string[] = [...LANDING_PATHS, ...ARTICLE_PATHS]

export const LOGIN_PATH = '/login'

/**
 * Browser tab titles. Distinct per page so a user with six tabs open can tell
 * them apart — previously every tab read "Onsum — Invoice Processing".
 */
const TITLES: Record<string, string> = {
  [PATHS.upload]:            'Upload Invoices',
  [UPLOAD_REVIEW_PATH]:      'Review Invoice',
  [UPLOAD_DONE_PATH]:        'Invoice Submitted',
  [PATHS.bills]:             'Bills',
  [PATHS.dashboard]:         'Dashboard',
  [PATHS.vendors]:           'Vendors',
  [PATHS.reports]:           'Reports',
  [PATHS['data-extraction']]:'Data Extraction',
  [PATHS.nova]:              'Nova',
  [PATHS.review]:            'Review Hub',
  [REVIEW_BROWSE_PATH]:      'Browse — Review Hub',
  [PATHS['agent-hub']]:      'Agent Hub',
  [PATHS.payments]:          'Payment Hub',
  [PATHS.integrations]:      'Integrations',
  [PATHS.settings]:          'Settings',
  [PATHS['crypto-onboarding']]: 'Crypto Rail Setup',
  [PATHS.onchain]:           'On-chain Ledger',
  [PATHS.audit]:             'Audit Proof',
  [VERIFY_PATH]:             'Verify an Audit Proof',
  [LOGIN_PATH]:              'Sign in',
  // The four public marketing routes carry the terms we want to rank for, not
  // just a section name: these titles are the headline of the search result and
  // the first line an answer engine reads. Kept under ~60 characters including
  // the ` · Onsumm` suffix so Google shows them whole.
  '/':                       'AP Automation for Indian SMEs — Tally & Zoho Books',
  '/product':                'Invoice OCR, Approval Workflows, GST & TDS',
  '/pricing':                'AP Automation Pricing for Indian SMEs',
  '/contact':                'Contact & Demo — AP Automation India',
  // Long-form pages title themselves from src/content, so the tab, the <title>
  // and the H1 cannot disagree about what the page is.
  ...Object.fromEntries(ARTICLES.map(a => [a.path, a.title])),
}

export const APP_NAME = 'Onsumm'

/** Tab title for a path, longest-prefix matched so nested routes inherit. */
export function titleForPath(pathname: string): string {
  const exact = TITLES[pathname]
  if (exact) return `${exact} · ${APP_NAME}`
  const prefix = Object.keys(TITLES)
    .filter(p => p !== '/' && pathname.startsWith(p))
    .sort((a, b) => b.length - a.length)[0]
  return prefix ? `${TITLES[prefix]} · ${APP_NAME}` : APP_NAME
}

const APP_PATH_LIST = Object.values(PATHS)

/**
 * Routes that actually have an <Route> registered today.
 *
 * `isAppPath` is deliberately looser — it also matches not-yet-built nested
 * routes like /bills/:id so a signed-out deep link is still remembered rather
 * than dropped. Before *replaying* a remembered path we need the strict check:
 * navigating to an unregistered path would fall through to the catch-all, which
 * would redirect to it again, and loop forever.
 */
const EXACT_ROUTES = new Set<string>([
  ...APP_PATH_LIST,
  UPLOAD_REVIEW_PATH,
  UPLOAD_DONE_PATH,
  REVIEW_BROWSE_PATH,
])

export function isKnownRoute(pathname: string): boolean {
  const path = pathname.split('?')[0]
  return EXACT_ROUTES.has(path) || PARAM_ROUTES.some(re => re.test(path))
}

/**
 * True when the path belongs to the authenticated app (vs. marketing pages).
 *
 * The query string is stripped first, matching isKnownRoute. Without that, any
 * link carrying params — `/bills/pay/x?bill=y`, or a Review Hub link to one
 * finding, `?f=` — failed this check, so a signed-out user clicking it from an
 * email lost their destination at the login redirect.
 */
export function isAppPath(pathname: string): boolean {
  const path = pathname.split('?')[0]
  return APP_PATH_LIST.some(p => path === p || path.startsWith(`${p}/`))
}

/** Which nav item should render as active for the current URL. */
export function pageForPath(pathname: string): NavPage | null {
  const entry = (Object.entries(PATHS) as [NavPage, string][])
    .filter(([, p]) => pathname === p || pathname.startsWith(`${p}/`))
    .sort((a, b) => b[1].length - a[1].length)[0]
  return entry ? entry[0] : null
}
