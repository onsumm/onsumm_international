/**
 * Vendor payment — Select → Review → Verify → Receipt.
 *
 * Replaces PaymentGatewayPage, which was 470 lines of inline styles with no
 * loading state, no retry on a failed load, no keyboard handling, no aria-live,
 * no media queries, a 120-second OTP whose expiry was never shown, and a
 * PENDING bank result that could not be resolved from the UI at all. Everything
 * visual here comes from ui/ and payment.css so a fix lands once.
 *
 * Still mounted as an overlay from Bills and Vendors (`overlay`); the route move
 * is the next slice, and only the wrapper changes when it lands.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Info } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { useBaseCurrency, useJurisdiction, useService } from '../auth/EntitlementsContext'
import { fetchRailPolicy, fetchSupplierWallets, fetchTreasuryWallets, type RailPolicy, type SupplierWallet, type TreasuryWallet } from '../api/crypto'
import { shortAddress } from '../web3/units'
import { fetchVendor, refreshDisbursementStatus, type Vendor } from '../api/invoices'
import {
  Badge, Button, ConfirmDialog, ErrorState, Skeleton, formatMoney, useToast,
} from '../ui'
import { useDocumentTitle } from '../hooks/useDocumentTitle'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { usePaymentFlow, summariseResults, SETTLE_BUDGET_MS, type PayResult, type PayStep } from './payment/usePaymentFlow'
import { PaymentProcessing } from './payment/PaymentProcessing'
import { BillSelectStep } from './payment/BillSelectStep'
import { ReviewStep } from './payment/ReviewStep'
import { OtpStep } from './payment/OtpStep'
import { ReceiptStep } from './payment/ReceiptStep'
import { BeneficiaryCard } from './payment/BeneficiaryCard'
import { PaymentSidebar } from './payment/PaymentSidebar'
import { PaymentHistory } from './payment/PaymentHistory'
import CryptoPayFlow from './payment/CryptoPayFlow'
import { UsdcTreasurySidebar } from './payment/UsdcTreasurySidebar'
import './payment.css'

export interface PaymentPageProps {
  supplierId: string
  /** Set by the Bills entry points: pay exactly this bill, skip selection. */
  initialInvoiceId?: string
  /** A USDC payout to open on its signing step (from a signing link). */
  initialPayoutId?: string
  /** Pre-fills the pinned bill's amount (e.g. retrying a failed payment). */
  initialAmount?: number
  /** The disbursal bank chosen in the Payment Hub; omitted, the remitter account's own. */
  bankProvider?: string
  onClose: () => void
  onPaid: () => void
  /** Slice 1 renders over the calling page; the route drops this. */
  overlay?: boolean
}

export default function PaymentPage({ supplierId, initialInvoiceId, initialPayoutId, initialAmount, bankProvider, onClose, onPaid, overlay = true }: PaymentPageProps) {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = user?.role === 'admin'

  const flow = usePaymentFlow({
    supplierId,
    initialInvoiceId,
    initialAmount,
    bankProvider,
    companyId: user?.company_id,
    actorName: user?.name,
    actorRole: user?.role,
    isAdmin,
    onPaid,
  })

  // The USDC rail (docs/crypto-rail): offered only to a tenant licensed for
  // crypto_payouts, and only once the vendor has an approved (ACTIVE) wallet.
  // IMPS stays the default and its flow below is unchanged.
  const cryptoOn = useService('crypto_payouts')
  // Which rails exist for this tenant (auth/jurisdiction.ts): IMPS only where the
  // imps pack applies (India), USDC only where the jurisdiction offers it and the
  // tenant is licensed. With one rail there is nothing to choose.
  const jur = useJurisdiction()
  const impsAllowed = jur.packs.imps
  const usdcAllowed = cryptoOn && jur.rails.includes('USDC')
  // A signing link is always for a USDC payout.
  const [rail, setRail] = useState<'IMPS' | 'USDC'>(initialPayoutId ? 'USDC' : 'IMPS')
  useEffect(() => {
    if (!impsAllowed && usdcAllowed && rail === 'IMPS') setRail('USDC')
    if (impsAllowed && !usdcAllowed && rail === 'USDC') setRail('IMPS')
  }, [impsAllowed, usdcAllowed, rail])
  const [usdcWallet, setUsdcWallet] = useState<SupplierWallet | null>(null)
  useEffect(() => {
    if (!cryptoOn) return
    fetchSupplierWallets(supplierId)
      .then(ws => setUsdcWallet(ws.find(w => w.status === 'ACTIVE') ?? null))
      .catch(() => setUsdcWallet(null))
  }, [cryptoOn, supplierId])
  // The USDC rail's sidebar and payee card: the treasury Safe (balance, owners),
  // the company's limits, and the vendor's own record.
  const baseCurrency = useBaseCurrency()
  const [treasury, setTreasury] = useState<TreasuryWallet | null>(null)
  const [railPolicy, setRailPolicy] = useState<RailPolicy | null>(null)
  const [treasuryLoading, setTreasuryLoading] = useState(false)
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const loadTreasury = useCallback(() => {
    if (!usdcAllowed) return
    setTreasuryLoading(true)
    fetchTreasuryWallets()
      .then(ws => setTreasury(ws.find(w => w.role === 'TREASURY' && w.status === 'ACTIVE') ?? ws.find(w => w.role === 'TREASURY') ?? null))
      .catch(() => setTreasury(null))
      .finally(() => setTreasuryLoading(false))
  }, [usdcAllowed])
  useEffect(() => {
    if (!usdcAllowed) return
    loadTreasury()
    fetchRailPolicy().then(setRailPolicy).catch(() => setRailPolicy(null))
    fetchVendor(supplierId).then(setVendor).catch(() => setVendor(null))
  }, [usdcAllowed, supplierId, loadTreasury])
  const showRailPicker = impsAllowed && usdcAllowed && !flow.pending && (flow.step === 'select' || flow.step === 'review') && !!flow.profile

  const [leaving, setLeaving] = useState(false)
  const [checking, setChecking] = useState<Record<string, boolean>>({})
  const [live, setLive] = useState('')
  const stepRef = useRef<HTMLDivElement>(null)
  const paidAtRef = useRef<Date | null>(null)

  useDocumentTitle(flow.profile ? `Pay ${flow.profile.vendor_name} · Onsumm` : 'Pay vendor · Onsumm')
  useUnsavedGuard(flow.dirty)

  const stepIndex = flow.steps.findIndex(s => s.key === flow.step)
  const stepLabel = flow.steps[stepIndex]?.label ?? ''

  // Move focus to the new step and announce it, so a keyboard or screen-reader
  // user is not left on a button that no longer exists.
  useEffect(() => {
    stepRef.current?.focus()
    setLive(`Step ${stepIndex + 1} of ${flow.steps.length}: ${stepLabel}`)
  }, [flow.step, stepIndex, stepLabel, flow.steps.length])

  useEffect(() => {
    if (flow.results) setLive(summariseResults(flow.results).label)
  }, [flow.results])

  const requestLeave = useCallback(() => {
    if (flow.pending) setLeaving(true)
    else onClose()
  }, [flow.pending, onClose])

  // Escape steps back through the flow rather than dropping the whole payment,
  // and never silently abandons rows that are awaiting a code.
  const { pending, step, pinned, setStep } = flow
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (pending) setLeaving(true)
      else if (step === 'review' && !pinned) setStep('select')
      else if (step !== 'receipt') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [pending, step, pinned, setStep, onClose])

  const checkStatus = useCallback(
    async (r: PayResult) => {
      setChecking(c => ({ ...c, [r.disbursement_id]: true }))
      try {
        const res = await refreshDisbursementStatus(r.disbursement_id, user?.company_id)
        const ref = res.bank_rrn ?? r.bank_rrn
        // Patch the row first — the card is the answer, the toast is only the
        // notification. Reporting success while the card still says "processing"
        // is worse than not reporting it at all.
        flow.applyResultStatus(r.disbursement_id, res.status, res.bank_rrn, res.status_message ?? null)
        if (res.status === 'SUCCESS') {
          toast.success('Payment confirmed', `${r.billLabel} settled${ref ? ` — reference ${ref}` : ''}`)
          onPaid()
        } else if (res.status === 'PENDING') {
          // The sweeper keeps enquiring server-side, so this is no longer a
          // request for the user to come back and poll by hand.
          toast.warn('Still processing', 'The bank has not confirmed this transfer yet. We will keep checking and update it automatically.')
        } else {
          toast.error('Payment failed', r.status_message ?? 'The bank rejected this transfer.')
        }
        flow.reload()
      } catch {
        toast.error('Could not check status', 'The status service did not respond.')
      } finally {
        setChecking(c => ({ ...c, [r.disbursement_id]: false }))
      }
    },
    [user?.company_id, toast, onPaid, flow],
  )

  // No auto-poll here: the wait happens up front on the settling screen, under a
  // single budget. A row that arrives still processing has already been waited
  // for, so resolving it is an explicit Check status rather than a background
  // loop the user cannot see.

  const body = useMemo(() => {
    if (flow.loading) {
      return (
        <div className="pay-card pay-skel">
          <Skeleton width="40%" height={18} />
          <Skeleton width="70%" />
          <Skeleton width="55%" />
          <Skeleton height={64} radius={8} />
          <Skeleton height={64} radius={8} />
        </div>
      )
    }

    if (flow.profileErr || !flow.profile) {
      return (
        <div className="pay-card">
          <ErrorState
            title="Could not load this vendor"
            message={flow.profileErr || 'No payment profile came back for this vendor.'}
            onRetry={flow.reload}
          />
        </div>
      )
    }

    if (flow.step === 'receipt' && flow.results) {
      const primaryBank = flow.profile.bank_accounts.find(b => b.is_primary) ?? flow.profile.bank_accounts[0]
      const overridden = flow.useOverride
      return (
        <ReceiptStep
          results={flow.results}
          receipt={{
            vendorName: flow.profile.vendor_name,
            beneficiaryName: overridden ? flow.override.name : primaryBank?.name_at_bank || flow.profile.vendor_name,
            beneficiaryAccount: overridden ? flow.override.account : primaryBank?.account_number_masked ?? '—',
            beneficiaryIfsc: overridden ? flow.override.ifsc : primaryBank?.ifsc ?? null,
            beneficiaryBank: overridden ? null : primaryBank?.bank_name ?? null,
            remitterLabel: flow.stats
              ? `${flow.stats.bank_name || flow.stats.bank_provider} ···${flow.stats.remitter_account_number.slice(-4)}`
              : null,
            results: flow.results,
            // Stamped once, when the receipt is first shown, so a preview opened
            // ten minutes later still reads the time the payment was released.
            paidAt: paidAtRef.current ?? (paidAtRef.current = new Date()),
            initiatedBy: user?.name,
            overridden,
            demo: !!flow.stats?.demo,
          }}
          checking={checking}
          onCheckStatus={checkStatus}
          onPayMore={flow.restart}
          onDone={onClose}
        />
      )
    }

    // Between "code accepted" and "the bank has decided" — the Onsumm mark in
    // motion, held until settled or the budget expires.
    if (flow.settling) {
      return (
        <PaymentProcessing
          vendorName={flow.profile.vendor_name}
          amount={flow.total}
          billCount={flow.selected.length}
          startedAt={flow.settling.startedAt}
          budgetMs={SETTLE_BUDGET_MS}
          awaitingBank={flow.settling.awaitingBank}
        />
      )
    }

    if (flow.step === 'verify' && flow.pending) {
      return (
        <OtpStep
          sentTo={flow.pending.sentTo}
          expiresAt={flow.pending.expiresAt}
          demoOtp={flow.pending.demoOtp}
          isBulk={flow.pending.ids.length > 1}
          otp={flow.otp}
          setOtp={flow.setOtp}
          error={flow.otpError}
          submitting={flow.submitting}
          onVerify={flow.verify}
          onResend={flow.resend}
          onRestart={() => setLeaving(true)}
        />
      )
    }

    if (flow.step === 'review') {
      return (
        <ReviewStep
          profile={flow.profile}
          selected={flow.selected}
          amounts={flow.amounts}
          total={flow.total}
          isAdmin={isAdmin}
          needsOverride={flow.needsOverride}
          useOverride={flow.useOverride}
          setUseOverride={flow.setUseOverride}
          override={flow.override}
          setOverride={flow.setOverride}
          canSubmit={flow.canSubmit}
          submitting={flow.submitting}
          onSubmit={flow.initiate}
        />
      )
    }

    return (
      <>
        <div className="pay-card">
          <h2 className="pay-card__title">Beneficiary</h2>
          <BeneficiaryCard profile={flow.profile} />
        </div>
        <BillSelectStep
          bills={flow.bills}
          picked={flow.picked}
          amounts={flow.amounts}
          amountErrors={flow.amountErrors}
          total={flow.total}
          onToggle={flow.toggleBill}
          onAmount={flow.setAmount}
          onSelectAll={flow.selectAll}
        />
        <div className="pay-actions">
          <div className="pay-actions__right">
            <Button
              variant="primary"
              size="lg"
              disabled={!flow.selectionValid}
              title={flow.selectionValid ? undefined : 'Select at least one bill and enter a valid amount'}
              onClick={() => flow.setStep('review')}
            >
              Review payment
            </Button>
          </div>
        </div>
      </>
    )
  }, [flow, checking, checkStatus, isAdmin, onClose, user?.name])

  return (
    <div className={overlay ? 'pay pay--overlay' : 'pay'}>
      {/* Docked at the far left edge, outside the scrolling column, so it holds
          through all four steps and has the room below it that the next sections
          (more remitter accounts, account selection) will occupy. */}
      {/* The remitter bank accounts (ICICI / HDFC / AU) exist only for IMPS. A tenant
          that pays over USDC alone has no bank to show, and its balance would be a
          rupee figure. */}
      {rail === 'USDC' && usdcAllowed ? (
        <UsdcTreasurySidebar wallet={treasury} policy={railPolicy} loading={treasuryLoading} onRefresh={loadTreasury} />
      ) : impsAllowed && (
        <PaymentSidebar stats={flow.stats} banks={flow.banks} bank={flow.bank} onBank={flow.setBank} locked={flow.bankLocked} />
      )}

      <div className="pay__scroll">
        <div className="pay__inner">
          <div className="pay__head">
            <div style={{ minWidth: 0 }}>
              <Button variant="ghost" size="sm" iconLeft={<ArrowLeft size={14} />} onClick={requestLeave}>
                Back
              </Button>
              <h1 className="pay__title">Pay vendor</h1>
              <p className="pay__sub">
                {flow.profile?.vendor_name ?? 'Loading vendor…'}
                {rail === 'IMPS' && flow.selected.length > 0 && flow.step !== 'receipt' && (
                  <> · {formatMoney(flow.total, { precise: true })} selected</>
                )}
              </p>
            </div>
            <div className="pay__head-actions">
              {rail === 'IMPS' && impsAllowed && flow.stats?.demo && <Badge tone="warn">DEMO — no real funds move</Badge>}
              {rail === 'USDC' && treasury?.settlement_mode === 'demo' && <Badge tone="warn">DEMO settlement — no funds move</Badge>}
            </div>
          </div>

          {rail === 'IMPS' && (<>
          <ol className="pay__steps">
            {flow.steps.map((s, i) => {
              const state = i < stepIndex ? 'done' : i === stepIndex ? 'current' : 'todo'
              return (
                <li key={s.key} className="pay__step-wrap" style={{ display: 'contents' }}>
                  <span
                    className={`pay__step${state === 'done' ? ' pay__step--done' : state === 'current' ? ' pay__step--current' : ''}`}
                    aria-current={state === 'current' ? 'step' : undefined}
                  >
                    <span className="pay__step-num" aria-hidden="true">{i + 1}</span>
                    {s.label}
                  </span>
                  {i < flow.steps.length - 1 && <span className="pay__step-sep" aria-hidden="true" />}
                </li>
              )
            })}
          </ol>
          <div className="pay__steps-compact">
            Step {stepIndex + 1} of {flow.steps.length} · {stepLabel}
          </div>
          </>)}

          <div aria-live="polite" className="sr-only">{live}</div>

          {!isAdmin && (
            <div className="pay-warn" style={{ marginBottom: 'var(--sp-4)' }}>
              <Info size={13} className="pay-warn__icon" aria-hidden="true" />
              <span>
                Only an admin can release a payment. You can review the vendor’s details and outstanding bills
                here, but the payment actions are unavailable for your role.
              </span>
            </div>
          )}

          {flow.error && (
            <div className="pay-warn pay-warn--danger" role="alert" style={{ marginBottom: 'var(--sp-4)' }}>
              <Info size={13} className="pay-warn__icon" aria-hidden="true" />
              <span>{flow.error}</span>
            </div>
          )}

          {!impsAllowed && !usdcAllowed && (
            <div className="pay-warn" role="status">
              <Info size={13} className="pay-warn__icon" aria-hidden="true" />
              <span>No payment rail is enabled for your company. IMPS is for Indian companies; USDC needs the Crypto Payouts licence.</span>
            </div>
          )}
          {!impsAllowed && usdcAllowed && !usdcWallet && (
            <div className="pay-warn" role="status">
              <Info size={13} className="pay-warn__icon" aria-hidden="true" />
              <span>This vendor has no approved USDC wallet yet. Send a wallet invite from the vendor card (Compliance → Crypto wallets), then approve the wallet once its cooling period ends.</span>
            </div>
          )}
          {showRailPicker && (
            <div className="cr-rail" role="group" aria-label="Payment rail">
              <button type="button" className="cr-rail__opt" aria-pressed={rail === 'IMPS'} onClick={() => setRail('IMPS')}>
                <div className="cr-rail__t">Bank · IMPS</div>
                <div className="cr-rail__d">INR to the vendor’s verified bank account</div>
              </button>
              <button
                type="button"
                className="cr-rail__opt"
                aria-pressed={rail === 'USDC'}
                disabled={!usdcWallet}
                title={usdcWallet ? undefined : 'The vendor has no approved USDC wallet yet'}
                onClick={() => setRail('USDC')}
              >
                <div className="cr-rail__t">USDC · Base Sepolia</div>
                <div className="cr-rail__d">
                  {usdcWallet ? `To ${shortAddress(usdcWallet.address)} from the company Safe` : 'No approved wallet for this vendor'}
                </div>
              </button>
            </div>
          )}

          {/* tabIndex -1 so focus can land here on a step change without adding a tab stop */}
          <div ref={stepRef} tabIndex={-1} style={{ outline: 'none' }}>
            {!impsAllowed && !usdcAllowed ? null : rail === 'USDC' && flow.profile ? (
              <CryptoPayFlow
                bills={flow.bills}
                initialInvoiceId={initialInvoiceId}
                resumePayoutId={initialPayoutId}
                isAdmin={isAdmin}
                onPaid={onPaid}
                onClose={onClose}
                vendorName={flow.profile.vendor_name}
                vendor={vendor}
                wallet={usdcWallet}
                treasury={treasury}
                railPolicy={railPolicy}
                currency={baseCurrency}
                onTreasuryChange={loadTreasury}
              />
            ) : (
              body
            )}
          </div>

          {rail === 'IMPS' && flow.step !== 'receipt' && <PaymentHistory rows={flow.history} />}
        </div>
      </div>

      <ConfirmDialog
        open={leaving}
        onCancel={() => setLeaving(false)}
        onConfirm={() => {
          setLeaving(false)
          // The disbursement rows stay PENDING_OTP server-side and expire on
          // their own; nothing has been sent to the bank yet.
          flow.restart()
          if (flow.step === 'verify') return
          onClose()
        }}
        tone="danger"
        title="Abandon this payment?"
        description="The confirmation code becomes void and nothing is sent to the bank. You can start the payment again afterwards."
        confirmLabel="Abandon payment"
        cancelLabel="Keep going"
        impact={[
          { label: 'Vendor', value: flow.profile?.vendor_name ?? '—' },
          { label: 'Total', value: formatMoney(flow.total, { precise: true }) },
        ]}
      />
    </div>
  )
}

export type { PayStep }
