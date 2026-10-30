import { Fragment, lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { isAdminPath } from './routes/adminPaths'
import ImpersonationBanner from './components/ImpersonationBanner'
const AdminApp = lazy(() => import('./admin/AdminApp'))
import { EntitlementsProvider, useJurisdiction } from './auth/EntitlementsContext'
import { RequireRole, RequireService } from './auth/guards'
import { can } from './auth/permissions'
import Sidebar from './components/Sidebar'
import MobileNav from './components/MobileNav'
import TopBar from './components/TopBar'
import RecordCardHost from './components/records/RecordCardHost'
import { useRecordCards } from './components/records/recordLinks'
import './components/shell.css'
import LandingApp from './landing/LandingApp'
import LoginPage from './pages/LoginPage'

// Every page below this line is code-split. Eagerly importing them put all
// fourteen — plus react-pdf, which only three of them use — into one 2.3 MB
// entry chunk, so opening /bills downloaded Settings, Reports, Nova and the
// Agent Hub too. LandingApp and LoginPage stay eager above: they are the first
// paint, and splitting them would only add a spinner before the sign-in form.
const UploadPage = lazy(() => import('./pages/UploadPage'))
const ReviewPage = lazy(() => import('./pages/ReviewPage'))
const DonePage = lazy(() => import('./pages/DonePage'))
const BillsPage = lazy(() => import('./pages/BillsPage'))
const PaymentPage = lazy(() => import('./pages/PaymentPage'))
const VendorsPage = lazy(() => import('./pages/VendorsPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const ReportsPage = lazy(() => import('./pages/ReportsPage'))
const DataExtractionPage = lazy(() => import('./pages/DataExtractionPage'))
const IntegrationsPage = lazy(() => import('./pages/IntegrationsPage'))
const NovaPage = lazy(() => import('./pages/NovaPage'))
const AgentHubPage = lazy(() => import('./pages/AgentHubPage'))
const PaymentHubPage = lazy(() => import('./pages/PaymentHubPage'))
// Distinct from ReviewPage above, which is the OCR review step of the upload flow.
const ReviewHubPage = lazy(() => import('./pages/review/ReviewHubPage'))
const ReviewBrowseView = lazy(() => import('./pages/review/BrowseView'))
// Crypto rail (docs/crypto-rail). The vendor portal and /verify are public and
// render outside the shell — see AppRoutes.
const CryptoOnboardingPage = lazy(() => import('./pages/crypto/CryptoOnboardingPage'))
const OnchainPage = lazy(() => import('./pages/crypto/OnchainPage'))
const AuditPage = lazy(() => import('./pages/crypto/AuditPage'))
const VerifyPage = lazy(() => import('./pages/crypto/VerifyPage'))
const VendorPortalPage = lazy(() => import('./pages/crypto/VendorPortalPage'))
import { markDraftSubmitted } from './api/agent'
import { reviewActions } from './pages/review/useReviewStore'
import { fetchNotificationCount, invalidateBillsCache } from './api/invoices'
import { LoadingMark, ToastProvider, setDisplayFormat } from './ui'
import { useDocumentTitle } from './hooks/useDocumentTitle'
import {
  PATHS,
  PUBLIC_PATHS,
  LOGIN_PATH,
  UPLOAD_REVIEW_PATH,
  UPLOAD_DONE_PATH,
  PAY_ROUTE,
  REVIEW_BROWSE_PATH,
  VENDOR_PORTAL_ROUTE,
  VERIFY_PATH,
  isCryptoPublicPath,
  isAppPath,
  isKnownRoute,
  titleForPath,
} from './routes/paths'
import type { InvoicePayload, IngestResult } from './api/invoices'

export interface QueueItem {
  file: File
  data: InvoicePayload
}
export type FileStatus = 'pending' | 'processing' | 'done' | 'error' | 'skipped'
export interface UploadEntry {
  file: File
  status: FileStatus
  result?: InvoicePayload
  error?: string
}

/** Landing page the user drops onto right after signing in. */
function defaultPath(role: string): string {
  if (can(role as Parameters<typeof can>[0], 'payables.view')) return PATHS.bills
  return PATHS.upload
}

// ── Authenticated shell ──────────────────────────────────────────────────────
function AppShell() {
  const { user, activeBrand } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  useDocumentTitle(titleForPath(location.pathname))

  const [uploadQueue, setUploadQueue] = useState<UploadEntry[]>([])
  const [reviewQueue, setReviewQueue] = useState<QueueItem[]>([])
  const [idx, setIdx] = useState(0)
  const [result, setResult] = useState<IngestResult | null>(null)
  // When reviewing an agent-created draft: its id (to mark submitted on done) and
  // the route to return to on Back.
  const [reviewDraftId, setReviewDraftId] = useState<string | undefined>(undefined)
  const [reviewOrigin, setReviewOrigin] = useState<string>(PATHS.upload)

  // Shared "needs your action" count, surfaced on both navs so the badge is
  // visible without opening Bills.
  const [pendingCount, setPendingCount] = useState(0)
  useEffect(() => {
    if (!user) return
    fetchNotificationCount(user.role, user.company_id)
      .then(setPendingCount)
      .catch(() => {})
  }, [user])

  // Review counts are loaded here rather than in the hub so the nav badge is
  // honest on every page, not only once someone has visited /review.
  useEffect(() => {
    if (!user) return
    void reviewActions.loadCounts()
  }, [user, activeBrand?.id])

  const handleReview = (items: QueueItem[], startIdx: number) => {
    setReviewQueue(items)
    setIdx(startIdx)
    navigate(UPLOAD_REVIEW_PATH)
  }

  // Open an agent-created draft in the same ReviewPage editor as the upload flow.
  const handleReviewDraft = (item: QueueItem, draftId: string, origin: string = 'bills') => {
    setReviewQueue([item])
    setIdx(0)
    setReviewDraftId(draftId)
    setReviewOrigin(PATHS[origin as keyof typeof PATHS] ?? PATHS.bills)
    navigate(UPLOAD_REVIEW_PATH)
  }

  const goBack = () => {
    const target = reviewDraftId ? reviewOrigin : PATHS.upload
    setReviewQueue([])
    setIdx(0)
    setReviewDraftId(undefined)
    navigate(target)
  }

  // A single bill was sent for approval. If it was one of several queued bills,
  // drop just that bill and advance to the next one so the rest of the queue
  // survives — only show the "done" screen once the queue is empty.
  const handleBillSent = (sentResult: IngestResult) => {
    // A draft that's been sent for approval is no longer pending — retire it.
    if (reviewDraftId && user?.company_id) {
      markDraftSubmitted(reviewDraftId, user.company_id).catch(() => {})
      setReviewDraftId(undefined)
    }
    if (reviewQueue.length > 1) {
      const newIdx = idx >= reviewQueue.length - 1 ? idx - 1 : idx
      setReviewQueue(prev => prev.filter((_, i) => i !== idx))
      setIdx(newIdx)
      return
    }
    setResult(sentResult)
    navigate(UPLOAD_DONE_PATH)
  }

  const fullReset = () => {
    setUploadQueue([])
    setReviewQueue([])
    setIdx(0)
    setResult(null)
    navigate(PATHS.upload)
  }

  // The bill card opens over the page the user is on (?open=bill:<id>), so the
  // jump from Vendors, the dashboard or the top bar no longer leaves that page.
  // It is still a URL that can be shared or come back to, and Back closes it.
  const { openBill } = useRecordCards()

  // Where an unrecognised in-app URL should land. Honours a deep link the user
  // followed while signed out, provided that route actually exists today.
  const rememberedFrom = (location.state as { from?: string } | null)?.from
  const landingTarget =
    rememberedFrom && isKnownRoute(rememberedFrom) ? rememberedFrom : defaultPath(user?.role ?? '')

  const current = reviewQueue[idx] ?? null
  // Upload is the one page that scrolls at the shell level; the rest own their
  // own scroll containers.
  const scrolls = location.pathname.startsWith(PATHS.upload)

  return (
    <div className="shell">
      <Sidebar pendingCount={pendingCount} />
      <div className="shell__main">
        {/* Renders nothing unless ops is viewing this account. */}
        <ImpersonationBanner />
        <TopBar onGoToBills={() => navigate(PATHS.bills)} onGoToBill={openBill} />
        <div className={`shell__content${scrolls ? ' shell__content--scroll' : ''}`}>
          {/* One boundary for every route below, since they are all code-split.
              The shell — sidebar, top bar, banner — stays mounted across the
              swap, so only the page area shows the mark. */}
          <Suspense
            fallback={
              <div className="shell__route-boot">
                <LoadingMark size={24} />
              </div>
            }
          >
            <Routes>
              <Route
                path={PATHS.dashboard}
                element={
                  <DashboardPage
                    onGoToBills={() => navigate(PATHS.bills)}
                    onGoToUpload={() => navigate(PATHS.upload)}
                    // The bill card, over the dashboard. This used to mount a
                    // second BillsPage inside the dashboard route behind a
                    // hand-rolled "← Back to Dashboard" bar, which gave the bill no
                    // URL and no browser history. The card has both, and Back
                    // closes it back onto the dashboard.
                    onGoToBill={openBill}
                  />
                }
              />

              {/* `/bills/:invoiceId` and `/vendors/:supplierId[/:tab]` render the
                same pages — the record simply opens from the URL, so a bill or a
                vendor's contacts can be linked to, bookmarked and reached with
                Back. */}
              {[PATHS.bills, `${PATHS.bills}/:invoiceId`].map(path => (
                <Route key={path} path={path} element={<BillsPage onReviewDraft={handleReviewDraft} />} />
              ))}
              {[PATHS.vendors, `${PATHS.vendors}/:supplierId`, `${PATHS.vendors}/:supplierId/:tab`].map(
                path => (
                  <Route key={path} path={path} element={<VendorsPage onNavigateToBill={openBill} />} />
                ),
              )}
              {/* Money movement gets a real address — the audit's finding N2: the
                highest-consequence action in the product had no URL, no Back and
                no navigation history. */}
              <Route
                path={PAY_ROUTE}
                element={
                  <RequireService service="disbursement">
                    <PaymentRoute />
                  </RequireService>
                }
              />
              {/* The Payment Hub is a landing page for the same disbursement
                service PAY_ROUTE already guards — it surfaces every vendor
                with money owed and hands off to the existing pay wizard
                (payPath) rather than duplicating it. */}
              <Route
                path={PATHS.payments}
                element={
                  <RequireService service="disbursement">
                    <PaymentHubPage />
                  </RequireService>
                }
              />
              {/* Crypto rail: client onboarding, the on-chain ledger (under the
                  Payment Hub) and the audit proof page. */}
              <Route
                path={PATHS['crypto-onboarding']}
                element={
                  <RequireService service="crypto_payouts">
                    <CryptoOnboardingPage />
                  </RequireService>
                }
              />
              <Route
                path={PATHS.onchain}
                element={
                  <RequireService service="crypto_payouts">
                    <OnchainPage />
                  </RequireService>
                }
              />
              <Route
                path={PATHS.audit}
                element={
                  <RequireService service="audit_anchor">
                    <AuditPage />
                  </RequireService>
                }
              />
              <Route path={PATHS.reports} element={<ReportsPage />} />
              <Route
                path={PATHS['data-extraction']}
                element={
                  <RequireService service="data_extraction">
                    <DataExtractionPage />
                  </RequireService>
                }
              />
              <Route
                path={PATHS.integrations}
                element={
                  <RequireRole roles={['admin']}>
                    <IntegrationsPage />
                  </RequireRole>
                }
              />
              {/* Nova is one customer's reconciliation module. It used to render for
                every tenant that had payables.view. */}
              <Route
                path={PATHS.review}
                element={
                  <RequireService service="review_hub">
                    <ReviewHubPage />
                  </RequireService>
                }
              />
              <Route
                path={`${PATHS.review}/:view`}
                element={
                  <RequireService service="review_hub">
                    <ReviewHubPage />
                  </RequireService>
                }
              />
              {/* A static segment outranks the ":view" param above in React
                  Router's route matching, so this always wins for exactly
                  this path regardless of declaration order — Browse is a
                  different view (the unbounded, filterable table) from the
                  bounded-queue lifecycle tabs ":view" serves. */}
              <Route
                path={REVIEW_BROWSE_PATH}
                element={
                  <RequireService service="review_hub">
                    <ReviewBrowseView />
                  </RequireService>
                }
              />
              <Route
                path={PATHS.nova}
                element={
                  <RequireService service="nova_billing">
                    <NovaPage />
                  </RequireService>
                }
              />
              <Route
                path={PATHS['agent-hub']}
                element={
                  <RequireService service="agent_hub">
                    <AgentHubPage onReviewDraft={handleReviewDraft} />
                  </RequireService>
                }
              />
              {/* The open section is part of the address, so a colleague can be
                sent to /settings/tds rather than to "Settings, then TDS".
                Guarding at the route means an unauthorised user no longer mounts
                the whole page and fires its data loads before hitting a lock. */}
              {[PATHS.settings, `${PATHS.settings}/:section`].map(path => (
                <Route
                  key={path}
                  path={path}
                  element={
                    <RequireRole roles={['admin']}>
                      <SettingsPage />
                    </RequireRole>
                  }
                />
              ))}

              <Route
                path={PATHS.upload}
                element={
                  <UploadPage
                    uploadQueue={uploadQueue}
                    setUploadQueue={setUploadQueue}
                    onReview={handleReview}
                  />
                }
              />
              {/* The review + done steps hold in-memory queue state. Landing on them
                directly (deep link, refresh) has nothing to show — send the user
                back to the start of the flow rather than rendering an empty editor.
                Phase 3 replaces this with server-side draft persistence. */}
              <Route
                path={UPLOAD_REVIEW_PATH}
                element={
                  current ? (
                    <ReviewPage
                      data={current.data}
                      loading={false}
                      error=""
                      file={current.file}
                      queue={reviewQueue}
                      queueIndex={idx}
                      onNav={setIdx}
                      onBack={goBack}
                      onDone={handleBillSent}
                    />
                  ) : (
                    <Navigate to={PATHS.upload} replace />
                  )
                }
              />
              <Route
                path={UPLOAD_DONE_PATH}
                element={
                  result ? (
                    <DonePage
                      result={result}
                      onNew={fullReset}
                      onGoToBills={() => {
                        fullReset()
                        navigate(PATHS.bills)
                      }}
                    />
                  ) : (
                    <Navigate to={PATHS.upload} replace />
                  )
                }
              />

              {/* Signed in but pointed at a public/unknown route: replay the
                remembered deep link, but only if it's a route that exists —
                otherwise we'd bounce back here and loop. */}
              <Route path="*" element={<Navigate to={landingTarget} replace />} />
            </Routes>
          </Suspense>
        </div>
      </div>
      {/* Bill and vendor cards opened by id from any page. */}
      <RecordCardHost />
      <MobileNav pendingCount={pendingCount} />
    </div>
  )
}

/**
 * The payment flow as a page.
 *
 * Reads the vendor from the path and the optional pinned bill from `?bill=`, so
 * a payment in progress is a URL. Closing goes back if there is history to go
 * back to — the user usually arrived from a bill or a vendor and expects to land
 * where they were — and falls back to /bills for a cold deep link.
 */
function PaymentRoute() {
  const { supplierId } = useParams<{ supplierId: string }>()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()

  if (!supplierId) return <Navigate to={PATHS.bills} replace />

  const leave = () => {
    if (location.key !== 'default') navigate(-1)
    else navigate(PATHS.bills)
  }

  return (
    <PaymentPage
      supplierId={supplierId}
      initialInvoiceId={params.get('bill') ?? undefined}
      initialPayoutId={params.get('payout') ?? undefined}
      initialAmount={Number(params.get('amount')) || undefined}
      bankProvider={params.get('via') ?? undefined}
      overlay={false}
      onClose={leave}
      onPaid={() => invalidateBillsCache()}
    />
  )
}

// ── Public (signed-out) routes ───────────────────────────────────────────────
function PublicRoutes() {
  const location = useLocation()
  const navigate = useNavigate()
  useDocumentTitle(titleForPath(location.pathname))

  // The SSO callback can only send the browser to the site root: it is a hard
  // navigation from Google/Microsoft, so a deep link to /login would depend on
  // the CDN's SPA rewrite being in place and 404 before any of this loads if it
  // isn't. The code therefore arrives on the marketing home, which has no idea
  // what to do with it — carry it to the login screen, the only thing that
  // redeems it. Client-side, so the server is never asked for /login.
  const ssoParams = new URLSearchParams(location.search)
  if ((ssoParams.has('sso') || ssoParams.has('sso_error')) && location.pathname !== LOGIN_PATH) {
    return <Navigate to={{ pathname: LOGIN_PATH, search: location.search }} replace />
  }

  return (
    <Routes>
      <Route path={LOGIN_PATH} element={<LoginPage onBack={() => navigate('/')} />} />
      {/* The four marketing pages and every long-form article: all public,
          all rendered inside the same chrome by LandingApp. */}
      {PUBLIC_PATHS.map(p => (
        <Route key={p} path={p} element={<LandingApp onLogin={() => navigate(LOGIN_PATH)} />} />
      ))}
      {/* A deep link into the app while signed out is remembered and replayed
          after login — that's the "approver clicks the emailed bill link" flow. */}
      <Route
        path="*"
        element={
          isAppPath(location.pathname) ? (
            <Navigate to={LOGIN_PATH} replace state={{ from: location.pathname + location.search }} />
          ) : (
            <Navigate to="/" replace />
          )
        }
      />
    </Routes>
  )
}

function AppRoutes() {
  const { user } = useAuth()
  const location = useLocation()

  // Checked before the signed-in fork: a signed-out /admin/tenants would otherwise
  // fall through to PublicRoutes' catch-all, fail isAppPath, and redirect to the
  // marketing home rather than the ops sign-in.
  //
  // Lazy-loaded so the ops panel is never downloaded by a tenant user — defence in
  // depth on top of the server-side identity split.
  // Public crypto-rail pages render without the shell whether or not someone is
  // signed in: a vendor and an auditor have no Onsumm login, and a signed-in
  // user opening a share link should see the same page they would.
  if (isCryptoPublicPath(location.pathname)) {
    return (
      <Suspense
        fallback={
          <div className="adm__boot">
            <LoadingMark size={24} />
          </div>
        }
      >
        <Routes>
          <Route path={VERIFY_PATH} element={<VerifyPage />} />
          <Route path={VENDOR_PORTAL_ROUTE} element={<VendorPortalPage />} />
        </Routes>
      </Suspense>
    )
  }

  if (isAdminPath(location.pathname)) {
    return (
      <Suspense
        fallback={
          <div className="adm__boot">
            <LoadingMark size={24} />
          </div>
        }
      >
        <AdminApp />
      </Suspense>
    )
  }
  return user ? <AppShell /> : <PublicRoutes />
}

/**
 * Points every formatter in ui/format.ts at the signed-in tenant's currency and
 * locale (₹ / en-IN for Indian tenants, as before; $ for a USD-books tenant).
 *
 * Set during render, before any child renders, so the first paint already
 * formats correctly. Keyed on currency + locale so the shell re-renders once if
 * they change after the plan loads: memoised children that only call a pure
 * formatter would otherwise keep showing the previous currency. For an Indian
 * tenant the key never changes, so nothing remounts.
 */
function DisplayFormatSync({ children }: { children: ReactNode }) {
  const j = useJurisdiction()
  setDisplayFormat({ currency: j.base_currency, locale: j.locale })
  return <Fragment key={`${j.base_currency}|${j.locale}`}>{children}</Fragment>
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        {/* Inside ToastProvider: it surfaces a toast when the plan can't be loaded. */}
        <EntitlementsProvider>
          <DisplayFormatSync>
            <AppRoutes />
          </DisplayFormatSync>
        </EntitlementsProvider>
      </ToastProvider>
    </AuthProvider>
  )
}
