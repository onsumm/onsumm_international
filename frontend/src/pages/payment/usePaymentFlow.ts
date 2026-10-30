/**
 * The vendor-payment state machine.
 *
 * Kept out of the view so the rules that decide whether money can move — is the
 * amount inside the outstanding, is an override complete, is this OTP still
 * live — are testable without rendering anything. The old page interleaved all
 * of it with JSX, so none of it was covered.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import { loadDisbursalBank, saveDisbursalBank } from './disbursalBank'
import {
  fetchDisbursalBanks, type DisbursalBank,
  fetchDisbursementStats, fetchDisbursements, fetchVendorPaymentProfile,
  initiateDisbursement, initiateBulkDisbursement, verifyDisbursementOtp, verifyBatchDisbursementOtp,
  resendDisbursementOtp, refreshDisbursementStatus,
  type Disbursement, type DisbursementStats, type VendorPaymentProfile,
} from '../../api/invoices'

export type PayStep = 'select' | 'review' | 'verify' | 'receipt'

export type PayBill = VendorPaymentProfile['bills'][number]

/** One row of the receipt, normalised so single and bulk share a render path. */
export interface PayResult {
  disbursement_id: string
  invoice_id: string
  billLabel: string
  amount: number
  status: string
  status_message: string | null
  bank_rrn: string | null
  tran_ref_no?: string | null
}

export interface OverrideDraft {
  account: string
  ifsc: string
  name: string
  reason: string
}

/** Mirrors OTP_TTL_SECONDS in disbursement_api.py. */
export const OTP_TTL_MS = 120_000
/** How long after sending before Resend becomes available. */
export const RESEND_COOLDOWN_MS = 30_000
/**
 * How long to hold the settling screen before reporting a payment as still
 * processing. The bank can leave a transfer in flight indefinitely; a minute is
 * long enough that most resolve on screen, and short enough that nobody is left
 * staring at an animation with no way forward.
 */
export const SETTLE_BUDGET_MS = 60_000
/**
 * Gap between status checks while the bank has the transfer, and how it grows.
 *
 * A flat 3s gap meant ~20 enquiries in the budget for a payment that had not
 * moved — against a bank whose integration document asks for one enquiry every
 * five minutes. Backing off keeps the first few checks quick, so a transfer
 * that settles in seconds still lands on screen immediately, while one that is
 * genuinely stuck is asked about 8 times instead of 20.
 *
 * Nothing is lost by easing off: since the disbursement sweeper
 * (app/domain/payments/sweeper.py) reconciles PENDING payments server-side,
 * this loop is a convenience for the person watching, no longer the only thing
 * that will ever ask the bank.
 */
const SETTLE_POLL_MS = 3_000
const SETTLE_POLL_FACTOR = 1.5
const SETTLE_POLL_MAX_MS = 15_000
const MIN_AMOUNT = 1

// ── Pure rules (unit-tested in paymentFlow.test.ts) ──────────────────────────

/**
 * The backend rejects anything outside ₹1…outstanding with a 400. Checking the
 * same bounds here means the user is told at the field instead of losing the
 * whole submission to a toast.
 */
export function amountError(value: number | null | undefined, outstanding: number): string | null {
  if (value === null || value === undefined || Number.isNaN(value)) return 'Enter an amount'
  if (value < MIN_AMOUNT) return `Minimum ₹${MIN_AMOUNT}`
  // Rounding guard: an outstanding of 59000.0000 must accept exactly 59000.
  if (value - outstanding > 0.005) return 'More than the outstanding amount'
  return null
}

export function isOverrideComplete(o: OverrideDraft): boolean {
  return !!(o.account.trim() && o.ifsc.trim() && o.name.trim() && o.reason.trim())
}

export function summariseResults(rows: PayResult[]): { ok: number; wait: number; fail: number; label: string } {
  const ok = rows.filter(r => r.status === 'SUCCESS').length
  const wait = rows.filter(r => r.status === 'PENDING').length
  const fail = rows.filter(r => r.status !== 'SUCCESS' && r.status !== 'PENDING').length
  const parts = [
    ok ? `${ok} succeeded` : '',
    wait ? `${wait} processing` : '',
    fail ? `${fail} failed` : '',
  ].filter(Boolean)
  return { ok, wait, fail, label: parts.join(' · ') || 'No payments' }
}

/** Which steps exist. A pinned bill has nothing to select, so Select is dropped. */
export function stepsFor(pinned: boolean): { key: PayStep; label: string }[] {
  const all: { key: PayStep; label: string }[] = [
    { key: 'select', label: 'Select bills' },
    { key: 'review', label: 'Review' },
    { key: 'verify', label: 'Verify' },
    { key: 'receipt', label: 'Receipt' },
  ]
  return pinned ? all.filter(s => s.key !== 'select') : all
}

function errMsg(e: unknown, fallback: string): string {
  return axios.isAxiosError(e) ? (e.response?.data?.detail ?? e.message) : fallback
}

/** The backend's way of saying these disbursement rows have already been used. */
function isStale(message: string): boolean {
  return message.toLowerCase().includes('no longer awaiting otp')
}

export type OtpErrorKind = 'expired' | 'wrong' | 'too_many' | 'other'

/**
 * The backend reports OTP-stage failures as one of a few fixed strings (see
 * `disbursement.py`'s `verify_otp`/`verify_batch_otp`) — "expired", "too many
 * attempts", or "incorrect" — plus whatever else could reach this step (a
 * network error, an unexpected 500). The OTP step used to show all of these
 * identically as a plain red line under the field; classifying the message
 * lets it show the right next action (resend vs. just retry) instead of one
 * generic "verification failed".
 */
export function classifyOtpError(message: string): OtpErrorKind {
  const m = message.toLowerCase()
  if (m.includes('expired')) return 'expired'
  if (m.includes('too many attempts')) return 'too_many'
  if (m.includes('incorrect')) return 'wrong'
  return 'other'
}

// ── Hook ─────────────────────────────────────────────────────────────────────

export interface PaymentFlowArgs {
  supplierId: string
  initialInvoiceId?: string
  /** The pinned bill's starting amount; capped at its outstanding. */
  initialAmount?: number
  /** Which bank moves the money: shown as "Paying from" and sent with the payment. */
  bankProvider?: string
  companyId?: string
  actorName?: string
  actorRole?: string
  isAdmin: boolean
  onPaid: () => void
}

export function usePaymentFlow({
  supplierId, initialInvoiceId, initialAmount, bankProvider, companyId, actorName, actorRole, isAdmin, onPaid,
}: PaymentFlowArgs) {
  const pinned = !!initialInvoiceId

  const [profile, setProfile] = useState<VendorPaymentProfile | null>(null)
  const [profileErr, setProfileErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [stats, setStats] = useState<DisbursementStats | null>(null)
  const [history, setHistory] = useState<Disbursement[]>([])

  const [step, setStep] = useState<PayStep>(pinned ? 'review' : 'select')
  const [amounts, setAmounts] = useState<Record<string, number>>({})
  const [picked, setPicked] = useState<Set<string>>(new Set())

  const [override, setOverride] = useState<OverrideDraft>({ account: '', ifsc: '', name: '', reason: '' })
  const [useOverride, setUseOverride] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  // `auto`: a Demo Payments tenant. The code is confirmed for the user, so Pay goes straight to paid.
  const [pending, setPending] = useState<{ ids: string[]; sentTo: string; demoOtp: string | null; expiresAt: number; auto?: boolean } | null>(null)
  const [otp, setOtp] = useState('')
  const [otpError, setOtpError] = useState('')
  const [results, setResults] = useState<PayResult[] | null>(null)
  /** Set from OTP acceptance until the bank has decided (or the budget runs out). */
  const [settling, setSettling] = useState<{ startedAt: number; awaitingBank: boolean } | null>(null)

  const loadProfile = useCallback(() => {
    setLoading(true)
    setProfileErr('')
    return fetchVendorPaymentProfile(supplierId, companyId)
      .then(p => {
        setProfile(p)
        // A pinned bill starts fully selected at its outstanding — the caller
        // already said which bill and the user's next question is "how much".
        // A caller-supplied amount (a retry pays what failed) wins, but never
        // above what is still owed.
        if (initialInvoiceId) {
          const b = p.bills.find(x => x.invoice_id === initialInvoiceId)
          if (b) {
            setPicked(new Set([b.invoice_id]))
            const start = initialAmount && initialAmount > 0
              ? Math.min(initialAmount, b.outstanding_amount)
              : b.outstanding_amount
            setAmounts({ [b.invoice_id]: start })
          }
        }
      })
      .catch(e => setProfileErr(errMsg(e, 'Could not load this vendor’s payment details')))
      .finally(() => setLoading(false))
  }, [supplierId, companyId, initialInvoiceId, initialAmount])

  // Stats drive a context strip only, so a failure must never block the page —
  // but it also must not vanish silently the way `.catch(() => {})` did.
  // Which bank moves the money: the one the link names, else the one last chosen
  // in this browser, else the remitter account's own. Changeable until the OTP goes out.
  const [bank, setBankState] = useState<string | undefined>(() => bankProvider ?? loadDisbursalBank(companyId))
  const [banks, setBanks] = useState<DisbursalBank[]>([])
  useEffect(() => { fetchDisbursalBanks().then(r => setBanks(r.banks)).catch(() => setBanks([])) }, [])
  const setBank = useCallback((code: string) => { setBankState(code); saveDisbursalBank(companyId, code) }, [companyId])

  const loadStats = useCallback(
    () => fetchDisbursementStats(companyId, bank).then(setStats).catch(() => setStats(null)),
    [companyId, bank],
  )

  useEffect(() => { loadProfile(); loadStats() }, [loadProfile, loadStats])

  // Past payments for the one bill in focus.
  useEffect(() => {
    const ids = [...picked]
    if (ids.length === 1) fetchDisbursements(ids[0], companyId).then(setHistory).catch(() => setHistory([]))
    else setHistory([])
  }, [picked, companyId])

  const bills = useMemo(
    () => (initialInvoiceId ? (profile?.bills ?? []).filter(b => b.invoice_id === initialInvoiceId) : profile?.bills ?? []),
    [profile, initialInvoiceId],
  )
  const selected = useMemo(() => bills.filter(b => picked.has(b.invoice_id)), [bills, picked])
  const total = useMemo(() => selected.reduce((s, b) => s + (amounts[b.invoice_id] ?? 0), 0), [selected, amounts])
  const amountErrors = useMemo(() => {
    const out: Record<string, string> = {}
    for (const b of selected) {
      const e = amountError(amounts[b.invoice_id], b.outstanding_amount)
      if (e) out[b.invoice_id] = e
    }
    return out
  }, [selected, amounts])

  const canPay = !!profile?.can_pay
  const needsOverride = !!profile && !canPay
  const isBulk = selected.length > 1
  const selectionValid = selected.length > 0 && Object.keys(amountErrors).length === 0
  const overrideReady = !needsOverride || (useOverride && isOverrideComplete(override))
  const canSubmit = isAdmin && selectionValid && total > 0 && overrideReady && !submitting

  const toggleBill = useCallback((b: PayBill) => {
    setPicked(prev => {
      const next = new Set(prev)
      if (next.has(b.invoice_id)) next.delete(b.invoice_id)
      else {
        next.add(b.invoice_id)
        setAmounts(a => (b.invoice_id in a ? a : { ...a, [b.invoice_id]: b.outstanding_amount }))
      }
      return next
    })
  }, [])

  const setAmount = useCallback((invoiceId: string, value: number) => {
    setAmounts(a => ({ ...a, [invoiceId]: value }))
  }, [])

  const selectAll = useCallback(() => {
    setPicked(prev => {
      if (prev.size === bills.length) return new Set()
      setAmounts(a => {
        const next = { ...a }
        for (const b of bills) if (!(b.invoice_id in next)) next[b.invoice_id] = b.outstanding_amount
        return next
      })
      return new Set(bills.map(b => b.invoice_id))
    })
  }, [bills])

  /** Sends the OTP and moves to Verify. Only called after the review step. */
  const initiate = useCallback(async () => {
    if (!canSubmit) return
    setError('')
    setSubmitting(true)
    const overrideFields = useOverride
      ? {
          override: true,
          override_account_number: override.account.trim(),
          override_ifsc: override.ifsc.trim(),
          override_beneficiary_name: override.name.trim(),
          override_reason: override.reason.trim(),
        }
      : {}
    try {
      const common = {
        company_id: companyId, actor_name: actorName, actor_role: actorRole,
        ...(bank ? { bank_provider: bank } : {}), ...overrideFields,
      }
      const res = isBulk
        ? await initiateBulkDisbursement(supplierId, {
            bills: selected.map(b => ({ invoice_id: b.invoice_id, amount: amounts[b.invoice_id] })),
            ...common,
          })
        : await initiateDisbursement(selected[0].invoice_id, { amount: amounts[selected[0].invoice_id], ...common })
      const ids = 'disbursement_ids' in res ? res.disbursement_ids : [res.disbursement_id]
      setPending({
        ids,
        sentTo: res.otp_sent_to,
        demoOtp: res.demo_otp ?? null,
        auto: !!res.demo_payment && !!res.demo_otp,
        // Prefer the server's expiry; fall back to the known TTL so the
        // countdown still means something against an older backend.
        expiresAt: res.otp_expires_at ? res.otp_expires_at * 1000 : Date.now() + OTP_TTL_MS,
      })
      setOtp('')
      setOtpError('')
      setStep('verify')
    } catch (e) {
      setError(errMsg(e, 'Could not start this payment'))
    } finally {
      setSubmitting(false)
    }
  }, [canSubmit, useOverride, override, companyId, actorName, actorRole, isBulk, selected, amounts, supplierId, bank])

  /**
   * Waits for the bank, then reports.
   *
   * A mock IMPS call can come back PENDING, and until now that landed straight on
   * the receipt as an amber card the user had to poll by hand. Instead the
   * settling screen stays up and the rows are polled here, in one place, under a
   * single budget — so "processing" is a real outcome reached after waiting, not
   * an immediate shrug.
   */
  const settle = useCallback(async (rows: PayResult[], startedAt: number): Promise<PayResult[]> => {
    let current = rows
    let gap = SETTLE_POLL_MS
    while (current.some(r => r.status === 'PENDING') && Date.now() - startedAt < SETTLE_BUDGET_MS) {
      setSettling({ startedAt, awaitingBank: true })
      await new Promise(res => setTimeout(res, gap))
      gap = Math.min(gap * SETTLE_POLL_FACTOR, SETTLE_POLL_MAX_MS)
      const checked = await Promise.all(
        current.map(async r => {
          if (r.status !== 'PENDING') return r
          try {
            const res = await refreshDisbursementStatus(r.disbursement_id, companyId)
            return {
              ...r,
              status: res.status,
              bank_rrn: res.bank_rrn ?? r.bank_rrn,
              status_message: res.status_message ?? r.status_message,
            }
          } catch {
            return r // transient — try again next tick, or give up at the budget
          }
        }),
      )
      current = checked
    }
    return current
  }, [companyId])

  const verify = useCallback(async (code: string) => {
    if (!pending || submitting) return
    setOtpError('')
    setSubmitting(true)
    const startedAt = Date.now()
    try {
      let rows: PayResult[]
      if (pending.ids.length > 1) {
        const res = await verifyBatchDisbursementOtp({ disbursement_ids: pending.ids, otp: code, company_id: companyId })
        rows = res.results.map(r => ({
          disbursement_id: r.disbursement_id,
          invoice_id: r.invoice_id,
          billLabel: bills.find(b => b.invoice_id === r.invoice_id)?.id ?? r.invoice_id.slice(0, 8),
          amount: r.amount,
          status: r.status,
          status_message: r.status_message,
          bank_rrn: r.bank_rrn,
        }))
      } else {
        const res = await verifyDisbursementOtp(pending.ids[0], { otp: code, company_id: companyId })
        const b = selected[0]
        rows = [{
          disbursement_id: res.disbursement_id,
          invoice_id: b?.invoice_id ?? '',
          billLabel: b?.id ?? '',
          amount: amounts[b?.invoice_id ?? ''] ?? 0,
          status: res.status,
          status_message: res.status_message,
          bank_rrn: res.bank_rrn,
          tran_ref_no: res.tran_ref_no,
        }]
      }

      // The code was accepted: the OTP form has done its job and the wait begins.
      setPending(null)
      setSettling({ startedAt, awaitingBank: rows.some(r => r.status === 'PENDING') })
      const finalRows = await settle(rows, startedAt)

      setResults(finalRows)
      setSettling(null)
      setStep('receipt')
      loadStats()
      loadProfile()
      if (finalRows.some(r => r.status === 'SUCCESS')) onPaid()
    } catch (e) {
      const msg = errMsg(e, 'Could not verify this code')
      setSettling(null)
      // Rows already consumed (another tab, a stale resume): there is nothing
      // left to confirm, so send the user back to a clean selection.
      if (isStale(msg)) {
        setPending(null)
        setOtp('')
        setStep(pinned ? 'review' : 'select')
        setError('That payment was already completed or cancelled. Start again.')
      } else {
        setOtpError(msg)
      }
    } finally {
      setSubmitting(false)
    }
  }, [pending, submitting, companyId, bills, selected, amounts, settle, loadStats, loadProfile, onPaid, pinned])

  // Demo payments: confirm the code as soon as it is issued.
  useEffect(() => {
    if (step !== 'verify' || !pending?.auto || !pending.demoOtp || submitting) return
    // Once only: a failed confirm falls back to the normal code screen, not a retry loop.
    setPending(p => (p ? { ...p, auto: false } : p))
    void verify(pending.demoOtp)
  }, [step, pending, submitting, verify])

  const resend = useCallback(async () => {
    // A bulk batch shares one OTP hash across its rows; reissuing for one row
    // would desync the rest, so the backend has no bulk resend.
    if (!pending || pending.ids.length > 1) return
    setOtpError('')
    try {
      const res = await resendDisbursementOtp(pending.ids[0], companyId)
      setPending(p => (p ? {
        ...p,
        sentTo: res.otp_sent_to,
        expiresAt: res.otp_expires_at ? res.otp_expires_at * 1000 : Date.now() + OTP_TTL_MS,
      } : p))
      setOtp('')
    } catch (e) {
      setOtpError(errMsg(e, 'Could not resend the code'))
    }
  }, [pending, companyId])

  /**
   * Patches one receipt row after a status re-check.
   *
   * Without this the refresh was invisible: the toast said "Payment confirmed"
   * while the card underneath still read "Processing at the bank", because the
   * results array was never touched. The old `bank_rrn` is kept when the status
   * call returns none, and the stale in-flight message is cleared.
   */
  const applyResultStatus = useCallback(
    (disbursementId: string, status: string, bankRrn: string | null, statusMessage: string | null = null) => {
      setResults(rows =>
        rows
          ? rows.map(r =>
              r.disbursement_id === disbursementId
                ? { ...r, status, bank_rrn: bankRrn ?? r.bank_rrn, status_message: statusMessage }
                : r,
            )
          : rows,
      )
    },
    [],
  )

  /** Abandons the pending rows and returns to the start for another payment. */
  const restart = useCallback(() => {
    setPending(null)
    setSettling(null)
    setResults(null)
    setOtp('')
    setOtpError('')
    setError('')
    setStep(pinned ? 'review' : 'select')
  }, [pinned])

  const goBack = useCallback(() => {
    setError('')
    if (step === 'review' && !pinned) setStep('select')
    else if (step === 'verify') restart()
  }, [step, pinned, restart])

  // Leaving with rows awaiting an OTP abandons them; warn on reload/close.
  const dirty = !!pending || (step !== 'receipt' && selected.length > 0)

  const otpSubmittedRef = useRef('')
  /** Auto-submit on the sixth digit, but never resubmit the same rejected code. */
  useEffect(() => {
    if (step !== 'verify' || otp.length !== 6 || submitting) return
    if (otpSubmittedRef.current === otp) return
    otpSubmittedRef.current = otp
    void verify(otp)
  }, [otp, step, submitting, verify])

  return {
    // data
    profile, profileErr, loading, stats, history, bills, selected, total, amountErrors,
    // disbursal bank (fixed once the OTP has gone out)
    bank, banks, setBank, bankLocked: step === 'verify' || step === 'receipt',
    // flags
    pinned, canPay, needsOverride, isBulk, isAdmin, canSubmit, selectionValid, dirty,
    // step
    step, setStep, steps: stepsFor(pinned), goBack, restart,
    // selection
    picked, toggleBill, setAmount, selectAll, amounts,
    // override
    override, setOverride, useOverride, setUseOverride,
    // submission
    submitting, error, setError, pending, otp, setOtp, otpError, results, settling,
    initiate, verify, resend, applyResultStatus,
    // retries
    reload: loadProfile,
  }
}
