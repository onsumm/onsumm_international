/**
 * Payment Hub — every vendor with money owed, in one place, and every action
 * that queue implies performed from here.
 *
 * This replaces the read-only version of this page. That one aggregated
 * `GET /api/disbursements/queue` + stats + the activity feed and then handed
 * every action off to the wizard by row-click, which left four things in the
 * mockup with nothing behind them: the per-row primary action, resuming a
 * payment already sitting on PENDING_OTP, checking a PENDING transfer at the
 * bank, and exporting the queue.
 *
 * What each control now calls (no backend change required):
 *   Pay with OTP      → navigate(payPath(supplier_id))        — the existing wizard
 *   Override…         → in-page panel → POST /api/vendors/{supplier_id}/disbursements/bulk (override)
 *   Enter code        → POST /api/disbursements/{id}/verify-otp  (in-page modal)
 *   Resend code       → POST /api/disbursements/{id}/resend-otp
 *   Check status      → POST /api/disbursements/{id}/refresh-status
 *   Retry (failed)    → navigate(payPath(supplier_id))        — a failed transfer moved no money
 *   Export queue      → client-side CSV of the filtered rows
 *   Refresh           → re-reads queue + stats + recent
 *
 * The guardrail is the backend's, unchanged: a verified vendor (GSTIN +
 * penny-drop) releases on the emailed OTP alone; an unverified beneficiary is
 * only payable as an admin override with a recorded reason, which the wizard's
 * ReviewStep collects and `_resolve_beneficiary` audits. There is no second
 * approver anywhere in this flow.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { loadDisbursalBank, saveDisbursalBank } from './payment/disbursalBank'
import { useNavigate } from 'react-router-dom'
import {
  RefreshCw, ShieldCheck, ShieldAlert, Landmark, Clock, Search, Banknote,
  LayoutList, Columns3, KeyRound, Download, Mail, RotateCcw, Copy, AlertTriangle, XCircle,
  ChevronDown, ChevronRight, Eye, EyeOff, Calendar,
} from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { PATHS, payPath } from '../routes/paths'
import { useJurisdiction } from '../auth/EntitlementsContext'
import { useRecordCards } from '../components/records/recordLinks'
import { downloadAuthed } from '../api/authedFiles'
import {
  fetchDisbursementQueue, fetchDisbursementStats, fetchRecentDisbursements, fetchVendorPaymentProfile, fetchPriorityLabels, fetchBillDetail,
  fetchDisbursalBanks, type DisbursalBank,
  initiateBulkDisbursement, verifyDisbursementOtp, verifyBatchDisbursementOtp, resendDisbursementOtp, refreshDisbursementStatus,
  type PaymentQueueVendor, type DisbursementStats, type RecentDisbursement, type VendorPaymentProfile, type FailedAttempt, type PriorityLabels, type BillDetail,
} from '../api/invoices'
import { amountError, classifyOtpError, isOverrideComplete, RESEND_COOLDOWN_MS, type OtpErrorKind, type OverrideDraft } from './payment/usePaymentFlow'
import { getPayPriority, priorityMeta, DEFAULT_PRIORITY_LABELS, type PayPriority } from '../utils/payPriority'
import { buildPdf, downloadPdf } from './payment/receiptPdf'
import { receiptPdfLines, receiptFileName, type ReceiptData } from './payment/receiptContent'
import {
  Badge, Button, Card, CardBody, Checkbox, DataTable, EmptyState, ErrorState, Input, Modal, PageHeader,
  Skeleton, useToast, useAmountField,
  formatMoney, formatMoneyCompact, formatDate, formatDateTime, formatRelativeDays,
  type Column,
} from '../ui'
import './payment-hub.css'
import './payment.css'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

type Stage = 'ready' | 'otp' | 'processing' | 'blocked'

/**
 * Demo Payments: the backend mails no code and hands it back instead, so the
 * payment is confirmed here and settles at once. Returns false for a real
 * payment, which waits for its emailed code as before.
 */
async function settleDemoPayment(
  res: { disbursement_ids: string[]; demo_otp?: string | null; demo_payment?: boolean },
  vendorName: string, companyId: string | undefined, toast: ReturnType<typeof useToast>,
): Promise<boolean> {
  if (!res.demo_payment || !res.demo_otp) return false
  const { results } = await verifyBatchDisbursementOtp({ disbursement_ids: res.disbursement_ids, otp: res.demo_otp, company_id: companyId })
  const ok = results.filter(r => r.status === 'SUCCESS')
  const paid = ok.reduce((s, r) => s + r.amount, 0)
  if (ok.length === results.length) toast.success(`${ok.length} bill${ok.length === 1 ? '' : 's'} paid (demo)`, `${formatMoney(paid)} to ${vendorName}`)
  else toast.warn(`${ok.length} of ${results.length} bills paid (demo)`, `${formatMoney(paid)} to ${vendorName}`)
  return true
}

function stageOf(v: PaymentQueueVendor): Stage {
  if (v.pending_status === 'PENDING_OTP') return 'otp'
  if (v.pending_status === 'PENDING') return 'processing'
  if (!v.can_pay) return 'blocked'
  return 'ready'
}

/**
 * The OTP's own 2-minute clock, ticked from the page's `now` rather than a
 * per-row timer — `otp_expires_at` is the backend's real expiry (`OTP_TTL_SECONDS`
 * in disbursement.py), so once it passes, "Awaiting OTP" is no longer true and
 * the row should say so without waiting for the admin to open the modal.
 */
function otpCountdown(v: PaymentQueueVendor, now: number): { expired: boolean; label: string } | null {
  if (v.pending_status !== 'PENDING_OTP' || !v.pending_otp_expires_at) return null
  const msLeft = Math.max(0, v.pending_otp_expires_at * 1000 - now)
  const expired = msLeft === 0
  const secs = Math.ceil(msLeft / 1000)
  return { expired, label: expired ? 'Code expired' : `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}` }
}

/** The disbursal bank's mark: ICICI's logo, and a monogram for banks without one on file. */
const BANK_MARKS: Record<string, { text: string; bg: string; fg: string }> = {
  HDFC: { text: 'HDFC', bg: '#004c8f', fg: '#fff' },
  AUBANK: { text: 'AU', bg: '#6d2077', fg: '#f7a823' },
}
function BankBadge({ code, small }: { code?: string | null; small?: boolean }) {
  const mark = code ? BANK_MARKS[code] : undefined
  return (
    <div className={`pmh__remitter-badge${small ? ' pmh__remitter-badge--sm' : ''}`} aria-hidden={small || undefined}>
      {mark
        ? <span className="pmh__bank-mono" style={{ background: mark.bg, color: mark.fg }}>{mark.text}</span>
        : <img src="/brand/icici.jpeg" alt={small ? '' : 'ICICI Bank'} />}
    </div>
  )
}

function StageBadge({ v, now }: { v: PaymentQueueVendor; now: number }) {
  const stage = stageOf(v)
  if (stage === 'ready') return <Badge tone="success" icon={<ShieldCheck size={11} />}>Ready to pay</Badge>
  if (stage === 'otp') {
    const otp = otpCountdown(v, now)
    if (otp?.expired) return <Badge tone="danger" icon={<XCircle size={11} />}>OTP expired</Badge>
    return <Badge tone="warn" icon={<KeyRound size={11} />}>Awaiting OTP{otp ? ` · ${otp.label}` : ''}</Badge>
  }
  if (stage === 'processing') return <Badge tone="warn" icon={<Clock size={11} />}>At the bank</Badge>
  return <Badge tone="danger" icon={<ShieldAlert size={11} />}>Override required</Badge>
}

/**
 * Every failed attempt for a vendor, not just the newest — a vendor can fail
 * more than once (a wrong account the first time, an expired OTP on retry),
 * and each is its own record in the audit trail, so each gets its own line
 * here rather than collapsing to a single "last failure" summary.
 */
function FailedAttempts({ attempts }: { attempts?: FailedAttempt[] }) {
  if (!attempts || attempts.length === 0) return null
  return (
    <div className="pmh__stage-fails">
      {attempts.map(f => (
        <div key={f.disbursement_id} className="pmh__stage-fail">
          <div className="pmh__stage-fail-head">
            <XCircle size={11} className="pmh__stage-fail-icon" aria-hidden="true" />
            <span className="pmh__stage-fail-meta">{formatDateTime(f.failed_at)} · {formatMoney(f.amount)}</span>
          </div>
          <div className="pmh__stage-fail-reason">{f.reason || 'Failed — no reason recorded'}</div>
        </div>
      ))}
    </div>
  )
}

const PRIORITY_GROUPS: { key: NonNullable<PayPriority>; icon: typeof AlertTriangle }[] = [
  { key: 'p0', icon: AlertTriangle },
  { key: 'p1', icon: Clock },
  { key: 'p2', icon: Calendar },
  { key: 'p3', icon: Calendar },
]

/**
 * Bill preview — a card on this same screen, not a navigation away from the
 * hub. Fetches the real bill (`fetchBillDetail`, the same read the Bills page
 * itself uses) rather than re-summarizing queue data that's already on
 * screen. "Open full bill" is kept as a real escape hatch to the actual page
 * (approval chain, editing) for whoever needs more than a look.
 */
function BillPreviewCard({
  invoiceId, onClose, onOpenFull,
}: { invoiceId: string; onClose: () => void; onOpenFull: () => void }) {
  const toast = useToast()
  const [detail, setDetail] = useState<BillDetail | null>(null)
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError('')
    fetchBillDetail(invoiceId)
      .then(d => { if (!cancelled) setDetail(d) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load this bill') })
    return () => { cancelled = true }
  }, [invoiceId])

  const download = async () => {
    if (!detail?.upload_id) return
    setDownloading(true)
    try {
      await downloadAuthed(`${API_BASE}/api/docs/${detail.upload_id}`, detail.original_file || `${detail.id || detail.invoice_id}.pdf`)
    } catch (e) {
      toast.error('Could not download this document', e instanceof Error ? e.message : undefined)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      size="md"
      title={detail?.bill_number || detail?.id || 'Bill preview'}
      description={detail?.vendor.name ?? undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          {detail?.upload_id && (
            <Button variant="secondary" iconLeft={<Download size={13} />} loading={downloading} onClick={download}>
              Download document
            </Button>
          )}
          <Button variant="primary" onClick={onOpenFull}>Open full bill</Button>
        </>
      }
    >
      {error && <div className="pmh__refresh-error" role="alert">{error}</div>}
      {!detail && !error && <Skeleton height={180} />}
      {detail && (
        <div className="pmh__preview">
          <div className="pmh__preview-grid">
            <div className="pmh__preview-row"><span>Vendor</span><b>{detail.vendor.name ?? '—'}</b></div>
            <div className="pmh__preview-row"><span>GSTIN</span><b>{detail.vendor.gstn ?? '—'}</b></div>
            <div className="pmh__preview-row"><span>Invoice date</span><b>{detail.invoice_date ? formatDate(detail.invoice_date) : '—'}</b></div>
            <div className="pmh__preview-row"><span>Due date</span><b>{detail.due_date ? formatDate(detail.due_date) : '—'}</b></div>
            {detail.category && <div className="pmh__preview-row"><span>Category</span><b>{detail.category}</b></div>}
          </div>
          <div className="pmh__preview-amounts">
            <div className="pmh__preview-row"><span>Subtotal</span><b>{formatMoney(detail.subtotal ?? 0, { precise: true })}</b></div>
            <div className="pmh__preview-row"><span>Tax</span><b>{formatMoney(detail.tax_amount ?? 0, { precise: true })}</b></div>
            <div className="pmh__preview-row"><span>Total</span><b>{formatMoney(detail.total_amount ?? 0, { precise: true })}</b></div>
            <div className="pmh__preview-row"><span>Outstanding</span><b>{formatMoney(detail.outstanding_amount ?? 0, { precise: true })}</b></div>
          </div>
          {detail.line_items.length > 0 && (
            <div className="pmh__preview-lines">
              <div className="pmh__preview-lines-head">Line items</div>
              {detail.line_items.map(li => (
                <div key={li.line_number} className="pmh__preview-line">
                  <span>{li.product_name || `Line ${li.line_number}`}</span>
                  <b>{formatMoney(li.line_total ?? 0, { precise: true })}</b>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}

/**
 * The per-vendor row, expanded: every outstanding bill making up
 * payable_amount, grouped by payment priority (P0..P3) — the exact same
 * `getPayPriority` calculation the Bills page uses (utils/payPriority.ts:
 * due date + bill value, admin `manual_priority` override wins), not a
 * separate scheme invented for this panel. "#3 of 4" told you nothing about
 * whether #3 was 90 days overdue or three weeks out; its priority group does.
 *
 * Preview opens a card right on this screen (BillPreviewCard), not a
 * navigation away from the hub — "Open full bill" inside it is the way out
 * to the real page for anyone who needs more.
 *
 * For a vendor that's actually Ready to pay: every bill gets its own editable
 * "pay this run" amount (same ₹1…outstanding validation the wizard's Select
 * step uses, `amountError`) and its own Pay button — click one row's Pay to
 * release just that bill, or check several and use the summary bar's "Pay
 * selected" to release them together under one OTP. Both call
 * `initiateBulkDisbursement` right here, the same endpoint the wizard itself
 * uses. No override fields exist for a Ready vendor, so this needs none. A
 * vendor that's blocked, mid-OTP, or already at the bank shows the same
 * grouped list read-only — override, entering a code, or checking status are
 * the right next step there, not a second way to initiate.
 */
function VendorBills({
  v, isAdmin, companyId, bankProvider, labels, onPaid,
}: { v: PaymentQueueVendor; isAdmin: boolean; companyId?: string; bankProvider?: string; labels: PriorityLabels; onPaid: () => void }) {
  const toast = useToast()
  const bills = v.bills
  const stage = stageOf(v)
  const canBulkPay = isAdmin && stage === 'ready'
  // Blocked isn't "can't pay" — it's "can't pay without an override". Pay on
  // one of these rows opens OverridePanel scoped to just that bill, instead
  // of a direct call the backend would reject for lacking override fields.
  const canOverridePay = isAdmin && stage === 'blocked'
  const meta = priorityMeta(labels)

  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [amounts, setAmounts] = useState<Record<string, number>>(
    () => Object.fromEntries(bills.map(b => [b.invoice_id, b.amount])),
  )
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set())
  const [busyAll, setBusyAll] = useState(false)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const { openBill } = useRecordCards()
  const [overrideBillId, setOverrideBillId] = useState<string | null>(null)

  if (bills.length === 0) return <div className="pmh__expand-empty">No outstanding bills.</div>

  const ranked = bills.map((b, i) => ({
    b, rank: i + 1,
    priority: getPayPriority({ due_date: b.due_date, total_amount: b.total_amount, manual_priority: b.manual_priority }),
  }))
  const groups = PRIORITY_GROUPS
    .map(g => ({ ...g, label: meta[g.key].label, items: ranked.filter(r => r.priority === g.key) }))
    .filter(g => g.items.length > 0)

  const toggle = (id: string) => setSelected(s => {
    const next = new Set(s)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const toggleAll = () => setSelected(s => (s.size === bills.length ? new Set() : new Set(bills.map(b => b.invoice_id))))
  const selectGroup = (key: NonNullable<PayPriority>) => setSelected(new Set(ranked.filter(r => r.priority === key).map(r => r.b.invoice_id)))

  const errors: Record<string, string> = {}
  for (const b of bills) {
    const e = amountError(amounts[b.invoice_id], b.amount)
    if (e) errors[b.invoice_id] = e
  }
  const selectedTotal = [...selected].reduce((s, id) => s + (amounts[id] || 0), 0)
  const selectedValid = [...selected].every(id => !errors[id])
  const canSubmitSelected = canBulkPay && selected.size > 0 && selectedValid

  const payBills = async (ids: string[]) => {
    const items = ids.map(id => ({ invoice_id: id, amount: amounts[id] }))
    const total = items.reduce((s, it) => s + it.amount, 0)
    try {
      const res = await initiateBulkDisbursement(v.supplier_id, {
        bills: items, company_id: companyId, bank_provider: bankProvider,
      })
      if (await settleDemoPayment(res, v.vendor_name, companyId, toast)) { onPaid(); return }
      toast.success(
        'Code sent',
        `A confirmation code was sent to release ${formatMoney(total, { precise: true })} — find it under "Awaiting OTP" once the queue refreshes. Sent to ${res.otp_sent_to}.`,
      )
      onPaid()
    } catch (e) {
      toast.error('Could not start this payment', e instanceof Error ? e.message : undefined)
    }
  }

  const payOne = async (id: string) => {
    setBusyIds(s => new Set(s).add(id))
    await payBills([id])
    setBusyIds(s => { const n = new Set(s); n.delete(id); return n })
  }

  const paySelected = async () => {
    setBusyAll(true)
    await payBills([...selected])
    setSelected(new Set())
    setBusyAll(false)
  }

  return (
    <div className="pmh__expand-panel">
      {/* Same header and column layout for every vendor, one bill or ten,
          Ready to pay or not — only whether "Pay this run"/checkbox/Pay are
          interactive changes below. A vendor that needs an override has no
          working Pay here regardless (the backend rejects it without one),
          so those cells go empty rather than showing a button that would
          just error — the real fix lives in the row's own Override action. */}
      <div className="pmh__expand-head">
        {canBulkPay ? (
          <Checkbox
            label={<span className="sr-only">Select all bills</span>}
            checked={selected.size === bills.length}
            indeterminate={selected.size > 0 && selected.size < bills.length}
            onChange={toggleAll}
          />
        ) : <span aria-hidden="true" />}
        <span className="pmh__expand-head-col pmh__expand-head-bill">Bill</span>
        <span className="pmh__expand-head-col pmh__expand-head-due">Due</span>
        <span className="pmh__expand-head-col pmh__expand-head-out">Outstanding</span>
        <span className="pmh__expand-head-col pmh__expand-head-run">Pay this run</span>
        <span aria-hidden="true" />
        <span aria-hidden="true" />
      </div>

      {groups.map(g => {
        const groupTotal = g.items.reduce((s, r) => s + (amounts[r.b.invoice_id] ?? r.b.amount), 0)
        const Icon = g.icon
        const gm = meta[g.key]
        return (
          <div key={g.key} className="pmh__expand-group">
            <div className="pmh__expand-group-head" style={{ background: gm.bg, color: gm.color }}>
              <Icon size={12} aria-hidden="true" />
              <span className="pmh__expand-group-label">{g.label.toUpperCase()}</span>
              <span className="pmh__expand-group-sub">
                {g.items.length} bill{g.items.length === 1 ? '' : 's'} · {formatMoney(groupTotal, { precise: true })}
              </span>
            </div>
            <ul className="pmh__expand-bills">
              {g.items.map(({ b, rank, priority }) => {
                const sel = selected.has(b.invoice_id)
                const rm = priority ? meta[priority] : null
                return (
                  <li key={b.invoice_id} className={`pmh__expand-bill pmh__expand-bill--editable${sel ? ' pmh__expand-bill--sel' : ''}`}>
                    {canBulkPay ? (
                      <Checkbox
                        label={<span className="sr-only">Select {b.id || 'this bill'}</span>}
                        checked={sel}
                        onChange={() => toggle(b.invoice_id)}
                      />
                    ) : (
                      <span className="pmh__expand-bill-rank" title={rm ? `Priority ${rm.label}` : undefined}
                        style={rm ? { background: rm.color, color: '#fff' } : undefined} aria-hidden="true">
                        {rank}
                      </span>
                    )}
                    <span className="pmh__expand-bill-id">{b.id || b.invoice_id.slice(0, 8)}</span>
                    <span className="pmh__expand-bill-due">
                      {b.due_date ? `${formatRelativeDays(b.due_date) ?? formatDate(b.due_date)}` : 'no due date'}
                      {b.due_date && <span className="pmh__expand-bill-due-abs">{formatDate(b.due_date)}</span>}
                    </span>
                    <b className="pmh__expand-bill-amt">{formatMoney(b.amount, { precise: true })}</b>
                    {canBulkPay || canOverridePay ? (
                      <>
                        <div className="pmh__expand-bill-amtfield">
                          <BillAmountInput
                            value={amounts[b.invoice_id]}
                            invalid={!!errors[b.invoice_id]}
                            label={`Amount to pay for ${b.id || 'this bill'}`}
                            onValue={v => setAmounts(a => ({ ...a, [b.invoice_id]: v }))}
                          />
                          {errors[b.invoice_id] && <div className="pmh__expand-bill-err">{errors[b.invoice_id]}</div>}
                        </div>
                        <Button size="sm" variant="success" disabled={!!errors[b.invoice_id]}
                          loading={busyIds.has(b.invoice_id)}
                          title={canOverridePay ? 'Unverified beneficiary — opens the override form for this bill' : undefined}
                          onClick={() => (canBulkPay ? payOne(b.invoice_id) : setOverrideBillId(b.invoice_id))}>
                          Pay
                        </Button>
                      </>
                    ) : (
                      <>
                        <span className="pmh__expand-bill-run-na" title="Already in progress — use Enter code or Check status on this vendor's row">—</span>
                        <span />
                      </>
                    )}
                    <button type="button" className="pmh__expand-bill-preview" title="Preview this bill"
                      onClick={() => setPreviewId(b.invoice_id)}>
                      <Eye size={13} />
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        )
      })}

      {canBulkPay && (
        <div className="pmh__expand-paybar">
          <span className="pmh__expand-paybar-total">
            {selected.size} of {bills.length} bill{bills.length === 1 ? '' : 's'} selected
            {selected.size > 0 && <> · {formatMoney(selectedTotal, { precise: true })}</>}
          </span>
          <div className="pmh__expand-paybar-actions">
            {selected.size > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
            )}
            {groups.some(g => g.key === 'p0') && (
              <Button variant="secondary" size="sm" onClick={() => selectGroup('p0')}>Select all {meta.p0.label}</Button>
            )}
            <Button variant="success" size="sm" disabled={!canSubmitSelected} loading={busyAll} onClick={paySelected}>
              Pay {selected.size > 0 ? `${selected.size} selected` : 'selected'}
            </Button>
          </div>
        </div>
      )}

      {previewId && (
        <BillPreviewCard
          invoiceId={previewId}
          onClose={() => setPreviewId(null)}
          // The full bill card, over the hub — the payment selection is kept.
          onOpenFull={() => { setPreviewId(null); openBill(previewId) }}
        />
      )}

      {overrideBillId && (
        <OverridePanel
          vendor={v}
          companyId={companyId}
          bankProvider={bankProvider}
          scopedBills={[{ invoice_id: overrideBillId, amount: amounts[overrideBillId] }]}
          onClose={() => setOverrideBillId(null)}
          onDone={onPaid}
        />
      )}
    </div>
  )
}

/** What a row expands to: its failed attempts on the Failed filter, its bills everywhere else. */
function VendorExpanded({
  v, showFailures, isAdmin, companyId, bankProvider, labels, onPaid,
}: { v: PaymentQueueVendor; showFailures: boolean; isAdmin: boolean; companyId?: string; bankProvider?: string; labels: PriorityLabels; onPaid: () => void }) {
  return (
    <div className="pmh__expand">
      {showFailures
        ? <FailedAttempts attempts={v.failed_attempts} />
        : <VendorBills v={v} isAdmin={isAdmin} companyId={companyId} bankProvider={bankProvider} labels={labels} onPaid={onPaid} />}
    </div>
  )
}

const STATUS_TONE: Record<string, 'success' | 'warn' | 'danger' | 'neutral'> = {
  SUCCESS: 'success',
  PENDING: 'warn',
  PENDING_OTP: 'warn',
  FAILED: 'danger',
}

const OTP_FAIL_COPY: Record<OtpErrorKind, { title: string; hint: string; field: string }> = {
  expired: { title: 'This code has expired', hint: 'Codes expire 2 minutes after they’re sent. Resend to get a new one.', field: 'Expired' },
  too_many: { title: 'Too many incorrect attempts', hint: 'This code is locked. Resend to keep trying.', field: 'Locked' },
  wrong: { title: 'That code didn’t match', hint: 'Check the 6-digit code in the email and enter it again.', field: 'Incorrect' },
  other: { title: 'Could not release this payment', hint: 'Something went wrong verifying this code.', field: 'Failed' },
}

function StatTile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'danger' | 'warn' }) {
  return (
    <Card>
      <CardBody>
        <div className="pmh__stat-label">{label}</div>
        <div className={`pmh__stat-value${tone ? ` pmh__stat-value--${tone}` : ''}`}>{value}</div>
        {sub && <div className="pmh__stat-sub">{sub}</div>}
      </CardBody>
    </Card>
  )
}

// 'failed' isn't a Stage — a failed attempt moves no money, so the bill stays
// exactly as ready/blocked as it was; the filter just checks whether the
// vendor's most recent disbursement attempt failed, independent of stage.
type QueueFilter = 'all' | Stage | 'failed'
const FILTERS: { key: QueueFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'ready', label: 'Ready to pay' },
  { key: 'otp', label: 'Awaiting OTP' },
  { key: 'processing', label: 'At the bank' },
  { key: 'blocked', label: 'Override required' },
  { key: 'failed', label: 'Failed' },
]

type LaneKey = 'ready' | 'blocked' | 'otp' | 'released'

// 'released' replaces the old 'processing'/'failed' vendor-queue lanes — it's
// not a queue stage at all, it's the activity feed (recent, already fetched
// for the Activity view) filtered to what's actually happened to a payment:
// paid, still at the bank, or rejected. A vendor mid-OTP still belongs in its
// own queue lane; once a code is entered, its disbursement's fate belongs here.
const LANES: { key: LaneKey; label: string }[] = [
  { key: 'ready', label: 'Ready to pay' },
  { key: 'blocked', label: 'Override required' },
  { key: 'otp', label: 'Awaiting OTP' },
  { key: 'released', label: 'Released' },
]

/**
 * Releasing a payment that is already sitting on PENDING_OTP, without sending
 * the user back through Select → Review to mint a second disbursement row for
 * the same bill. `verify-otp` and `resend-otp` both key off the disbursement id
 * the queue already hands us, so this needs no new endpoint.
 *
 * Both shapes of the queue response are handled. If it carries
 * `pending_otp_expires_at` / `pending_disbursement_ids` (see
 * integration/backend-queue-patch.md) the modal opens with a live countdown and
 * releases a bulk batch whole. Without them it releases the one id it was given
 * and starts a timer only after a resend — the code's age is genuinely unknown
 * before that, and a fabricated countdown is worse than none.
 */
function ReleaseOtpModal({
  vendor, companyId, onClose, onReleased,
}: {
  vendor: PaymentQueueVendor
  companyId?: string
  onClose: () => void
  onReleased: () => void
}) {
  const toast = useToast()
  const ids = useMemo(() => {
    const batch = vendor.pending_disbursement_ids
    return batch && batch.length > 0 ? batch : [vendor.pending_disbursement_id as string]
  }, [vendor])
  const isBulk = ids.length > 1
  const disbursementId = ids[0]
  // What this OTP actually releases — one bill's ad-hoc amount, or a batch's
  // sum — never vendor.payable_amount, which is the vendor's WHOLE
  // outstanding across every bill, most of which this code has nothing to
  // do with. Falls back to it only if the queue hasn't returned pending_amount.
  const releaseAmount = vendor.pending_amount ?? vendor.payable_amount
  const [otp, setOtp] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [sentTo, setSentTo] = useState<string | null>(null)
  const [expiresAt, setExpiresAt] = useState<number | null>(
    () => (vendor.pending_otp_expires_at ? vendor.pending_otp_expires_at * 1000 : null),
  )
  const [resentAt, setResentAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const fieldRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => { fieldRef.current?.querySelector('input')?.focus() }, [])

  const msLeft = expiresAt ? Math.max(0, expiresAt - now) : null
  const expired = msLeft === 0
  const secs = msLeft === null ? 0 : Math.ceil(msLeft / 1000)
  // A batch's rows share one code, so there is nothing to resend individually —
  // the same rule OtpStep applies to a bulk payment in the wizard.
  const canResend = !isBulk && !busy && (resentAt === null || expired || now - resentAt > RESEND_COOLDOWN_MS)
  const kind = error ? classifyOtpError(error) : null

  const verify = async () => {
    setBusy(true)
    setError('')
    try {
      if (isBulk) {
        // One code, one call, one row per bill — batch-verify-otp checks the
        // shared hash once and then makes a bank call per disbursement, so a
        // mixed outcome is normal and each bill needs naming.
        const { results } = await verifyBatchDisbursementOtp({ disbursement_ids: ids, otp, company_id: companyId })
        const ok = results.filter(r => r.status === 'SUCCESS')
        const pending = results.filter(r => r.status === 'PENDING')
        const failed = results.filter(r => r.status === 'FAILED')
        const paid = ok.reduce((s, r) => s + r.amount, 0)
        if (failed.length === 0 && pending.length === 0) {
          toast.success(`${ok.length} bill${ok.length === 1 ? '' : 's'} paid`, `${formatMoney(paid)} to ${vendor.vendor_name}`)
        } else {
          toast.warn(
            `${ok.length} of ${results.length} bills paid`,
            [
              `${formatMoney(paid)} released`,
              pending.length ? `${pending.length} at the bank` : '',
              failed.length ? `${failed.length} failed: ${failed[0].status_message ?? 'rejected by the bank'}` : '',
            ].filter(Boolean).join(' · '),
          )
        }
      } else {
        const res = await verifyDisbursementOtp(disbursementId, { otp, company_id: companyId })
        if (res.status === 'SUCCESS') {
          toast.success('Payment released', `${formatMoney(releaseAmount)} to ${vendor.vendor_name} · RRN ${res.bank_rrn ?? '—'}`)
        } else if (res.status === 'PENDING') {
          toast.warn('At the bank', 'Accepted but not yet confirmed — use Check status to poll it.')
        } else {
          toast.error('Payment failed', res.status_message || 'The bank rejected this transfer. No funds left the account.')
        }
      }
      onReleased()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not verify this code')
    } finally {
      setBusy(false)
    }
  }

  const resend = async () => {
    setBusy(true)
    setError('')
    try {
      const res = await resendDisbursementOtp(disbursementId, companyId)
      setSentTo(res.otp_sent_to)
      setExpiresAt(res.otp_expires_at ? res.otp_expires_at * 1000 : Date.now() + 120_000)
      setResentAt(Date.now())
      setOtp('')
      toast.success('Code sent', `A new 6-digit code is on its way to ${res.otp_sent_to}.`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not resend the code')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      closeOnBackdrop={false}
      size="sm"
      title="Enter the confirmation code"
      description={
        isBulk
          ? `One code releases all ${ids.length} bills for ${vendor.vendor_name} — ${formatMoney(releaseAmount)} over IMPS.`
          : `Releasing ${formatMoney(releaseAmount)} to ${vendor.vendor_name} over IMPS.`
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            variant="secondary"
            iconLeft={<RotateCcw size={13} />}
            onClick={resend}
            disabled={!canResend}
            title={isBulk
              ? 'All bills in this payment share one code — cancel and start it again if it has expired'
              : canResend ? undefined : 'Available shortly, or as soon as the code expires'}
          >
            Resend code
          </Button>
          <Button variant="success" onClick={verify} loading={busy} disabled={otp.length !== 6 || expired}>
            Verify and pay
          </Button>
        </>
      }
    >
      <p className="pmh__otp-sent">
        <Mail size={13} aria-hidden="true" />
        {sentTo
          ? <>Sent to <strong>{sentTo}</strong></>
          : expiresAt !== null
            ? <>A 6-digit code was emailed to the account owner when this payment was started.</>
            : <>A code was already emailed to the account owner when this payment was started. Resend if you no longer have it.</>}
      </p>

      {kind && (
        <div className={`pmh__otp-fail pmh__otp-fail--${kind}`} role="alert">
          {kind === 'wrong' ? <AlertTriangle size={16} /> : kind === 'expired' || kind === 'too_many' ? <ShieldAlert size={16} /> : <XCircle size={16} />}
          <div>
            <div className="pmh__otp-fail-title">{OTP_FAIL_COPY[kind].title}</div>
            <div className="pmh__otp-fail-hint">{OTP_FAIL_COPY[kind].hint}{kind === 'other' && error ? ` (${error})` : ''}</div>
          </div>
        </div>
      )}

      <div ref={fieldRef} className="pmh__otp-field">
        <Input
          label="6-digit code"
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={6}
          value={otp}
          error={error ? OTP_FAIL_COPY[kind ?? 'other'].field : null}
          disabled={busy}
          onChange={e => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
          onKeyDown={e => { if (e.key === 'Enter' && otp.length === 6) verify() }}
        />
      </div>

      {expiresAt !== null && (
        <div className={`pmh__otp-timer${expired ? ' pmh__otp-timer--out' : secs <= 30 ? ' pmh__otp-timer--soon' : ''}`} aria-hidden="true">
          <Clock size={13} />
          {expired ? 'Code expired' : `Expires in ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`}
        </div>
      )}

      <div className="pmh__otp-note">
        <Landmark size={13} aria-hidden="true" />
        <span>
          {isBulk ? `Releases ${ids.length} bills in one go. ` : ''}
          IMPS settles in seconds, 24×7, and cannot be reversed once released.
        </span>
      </div>
    </Modal>
  )
}

/**
 * Pays a blocked vendor in place, without leaving the hub for the wizard.
 *
 * Fetches the same payment-profile the wizard's Select/Review steps read, so
 * the verification detail shown here (GSTIN, bank account) is the real reason
 * this vendor is blocked, not a restatement of the row's two badges. Submitting
 * pays every outstanding bill for the vendor as one bulk override — the same
 * `initiateBulkDisbursement` call the wizard makes, so it lands on the same
 * `disbursement_overrides` + `wf_audit_log` trail and gates on `require_role
 * ("admin")` the same way. There is no separate "approval" step to route this
 * into — the OTP that goes out on submit is the only gate, same as everywhere
 * else in this flow.
 */
function OverridePanel({
  vendor, companyId, bankProvider, scopedBills, onClose, onDone,
}: {
  vendor: PaymentQueueVendor
  companyId?: string
  /** The disbursal bank chosen in the hub. An override is still a payment, so
   *  it must route through the same bank the operator picked — without this it
   *  silently fell back to the remitter account's default. */
  bankProvider?: string
  /** Pay just these bills (e.g. one row's Pay click on a blocked vendor)
   *  instead of every outstanding bill for the vendor. */
  scopedBills?: { invoice_id: string; amount: number }[]
  onClose: () => void
  onDone: () => void
}) {
  const toast = useToast()
  const [profile, setProfile] = useState<VendorPaymentProfile | null>(null)
  const [loadError, setLoadError] = useState('')
  const [override, setOverride] = useState<OverrideDraft>({ account: '', ifsc: '', name: '', reason: '' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchVendorPaymentProfile(vendor.supplier_id, companyId)
      .then(p => { if (!cancelled) setProfile(p) })
      .catch(e => { if (!cancelled) setLoadError(e instanceof Error ? e.message : 'Could not load this vendor') })
    return () => { cancelled = true }
  }, [vendor.supplier_id, companyId])

  const payBills = scopedBills ?? profile?.bills.map(b => ({ invoice_id: b.invoice_id, amount: b.outstanding_amount }))

  const submit = async () => {
    if (!profile || !payBills) return
    setBusy(true)
    try {
      const res = await initiateBulkDisbursement(vendor.supplier_id, {
        bills: payBills,
        company_id: companyId,
        bank_provider: bankProvider,
        override: true,
        override_account_number: override.account,
        override_ifsc: override.ifsc,
        override_beneficiary_name: override.name,
        override_reason: override.reason,
      })
      if (await settleDemoPayment(res, vendor.vendor_name, companyId, toast)) { onDone(); onClose(); return }
      toast.success('Code sent', `A confirmation code was emailed to release this override — find it under "Awaiting OTP" once the queue refreshes. Sent to ${res.otp_sent_to}.`)
      onDone()
      onClose()
    } catch (e) {
      toast.error('Could not start the override', e instanceof Error ? e.message : undefined)
    } finally {
      setBusy(false)
    }
  }

  const complete = isOverrideComplete(override)

  return (
    <Modal
      open
      onClose={onClose}
      closeOnBackdrop={false}
      size="md"
      title={vendor.vendor_name}
      description={vendor.gstin ?? undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="danger" onClick={submit} loading={busy} disabled={!profile || !complete}>
            Override and send code
          </Button>
        </>
      }
    >
      {loadError && <div className="pmh__refresh-error" role="alert">{loadError}</div>}

      <div className="pay-bene__rows" style={{ marginBottom: 'var(--sp-4)' }}>
        <div className="pay-bene__row">
          <span className="pay-bene__key">GSTIN</span>
          <span className="pay-bene__val" style={{ display: 'flex', gap: 'var(--sp-2)', alignItems: 'center' }}>
            <Badge tone={vendor.gstin_verified ? 'success' : 'danger'} icon={vendor.gstin_verified ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}>
              {vendor.gstin_verified ? 'Verified' : `${(vendor.kyb_status || 'unverified').toLowerCase()}`}
            </Badge>
          </span>
        </div>
        <div className="pay-bene__row">
          <span className="pay-bene__key">Bank account</span>
          <span className="pay-bene__val" style={{ display: 'flex', gap: 'var(--sp-2)', alignItems: 'center' }}>
            <Badge tone={vendor.bank_verified ? 'success' : 'danger'} icon={vendor.bank_verified ? <ShieldCheck size={11} /> : <Landmark size={11} />}>
              {vendor.bank_verified ? 'Verified' : 'Unverified'}
            </Badge>
            {!vendor.bank_verified && profile?.bank_accounts?.[0] && (
              <span style={{ fontSize: 12, color: 'var(--ink-500)' }}>
                on file: {profile.bank_accounts[0].bank_name || 'Bank'} · {profile.bank_accounts[0].account_number_masked}
              </span>
            )}
          </span>
        </div>
      </div>

      <div className="pay-warn" style={{ marginBottom: 'var(--sp-3)' }}>
        <AlertTriangle size={13} className="pay-warn__icon" aria-hidden="true" />
        <span>
          Payment can still be released by hand-entering the beneficiary. The override and your reason are
          recorded permanently against every bill in this payment.
        </span>
      </div>

      <div style={{ display: 'grid', gap: 'var(--sp-3)' }}>
        <Input
          label="Beneficiary name"
          hint="As it appears on the bank account, not the invoice"
          value={override.name}
          autoComplete="off"
          onChange={e => setOverride({ ...override, name: e.target.value })}
        />
        <Input
          label="Account number"
          value={override.account}
          inputMode="numeric"
          autoComplete="off"
          onChange={e => setOverride({ ...override, account: e.target.value.replace(/\s/g, '') })}
        />
        <Input
          label="IFSC"
          mono
          value={override.ifsc}
          autoComplete="off"
          onChange={e => setOverride({ ...override, ifsc: e.target.value.toUpperCase().trim() })}
        />
        <Input
          label="Reason for the override"
          required
          hint="Recorded permanently against every bill in this payment"
          value={override.reason}
          autoComplete="off"
          onChange={e => setOverride({ ...override, reason: e.target.value })}
        />
      </div>

      {profile && payBills && (
        <div className="pmh__otp-note" style={{ marginTop: 'var(--sp-4)' }}>
          <Landmark size={13} aria-hidden="true" />
          <span>
            {scopedBills
              ? `Pays this ${payBills.length === 1 ? 'bill' : `${payBills.length} bills`} only — ${formatMoney(payBills.reduce((s, b) => s + b.amount, 0))} in one override.`
              : `Pays all ${payBills.length} outstanding bill${payBills.length === 1 ? '' : 's'} for this vendor — ${formatMoney(vendor.payable_amount)} in one override.`}
          </span>
        </div>
      )}
    </Modal>
  )
}

/**
 * A real receipt for a past payment, built from the Payment Hub's own
 * activity feed — same builder (receiptPdfLines/buildPdf) the wizard's own
 * post-payment receipt uses, so a receipt downloaded from here or from the
 * wizard reads identically. `disbursement_recent` now carries the
 * beneficiary/remitter fields this needs; earlier it only had enough to
 * render the activity row, not to reconstruct a receipt from later.
 */
function recentToReceiptData(r: RecentDisbursement, stats: DisbursementStats | null): ReceiptData {
  return {
    vendorName: r.vendor_name ?? 'Vendor',
    beneficiaryName: r.beneficiary_name ?? r.vendor_name ?? 'Vendor',
    beneficiaryAccount: r.beneficiary_account ?? '—',
    beneficiaryIfsc: r.beneficiary_ifsc,
    beneficiaryBank: r.bank_provider,
    remitterLabel: r.remitter_account_number
      ? `${r.bank_provider || 'Bank'} ···${r.remitter_account_number.slice(-4)}`
      : null,
    results: [{
      disbursement_id: r.disbursement_id,
      invoice_id: r.invoice_id,
      billLabel: r.invoice_number ?? r.invoice_id.slice(0, 8),
      amount: r.amount,
      status: r.status,
      status_message: r.status_message,
      bank_rrn: r.bank_rrn,
      tran_ref_no: r.tran_ref_no,
    }],
    paidAt: new Date(r.updated_at),
    initiatedBy: r.updated_by ?? r.initiated_by ?? undefined,
    overridden: r.is_overridden,
    demo: !!stats?.demo,
  }
}

function toCsv(rows: PaymentQueueVendor[]): string {
  const head = ['Vendor', 'GSTIN', 'GSTIN verified', 'Bank verified', 'Bills', 'Payable', 'Earliest due', 'Stage', 'Failed attempts', 'Failure reasons']
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const body = rows.map(v => [
    v.vendor_name, v.gstin ?? '', v.gstin_verified ? 'yes' : 'no', v.bank_verified ? 'yes' : 'no',
    v.bills_count, v.payable_amount.toFixed(2), v.earliest_due ?? '', stageOf(v),
    v.failed_attempts?.length ?? 0,
    (v.failed_attempts ?? []).map(f => `${formatDateTime(f.failed_at)}: ${f.reason ?? 'Failed'}`).join(' | '),
  ].map(esc).join(','))
  return [head.join(','), ...body].join('\n')
}

/**
 * The hub is the IMPS rail: Indian bank accounts, GSTIN + penny-drop checks,
 * an Indian remitter account. A tenant without the IMPS pack (a company outside
 * India: core/jurisdiction.py) never pays through it, so it gets a pointer to
 * how it does pay instead of a queue that would call every vendor "unverified".
 */
export default function PaymentHubPage() {
  const j = useJurisdiction()
  if (!j.packs.imps) return <NoImpsPayments />
  return <ImpsPaymentHub />
}

function NoImpsPayments() {
  const navigate = useNavigate()
  return (
    <div className="pmh">
      <PageHeader
        title="Payment Hub"
        sub="Bank (IMPS) payouts are for companies registered in India. This company pays vendors in USDC from its own Safe."
      />
      <Card>
        <CardBody>
          <EmptyState
            icon={<Banknote size={20} />}
            title="Pay vendors from the bill or vendor page"
            message="Open an approved bill and choose Pay vendor: payment goes in USDC to the vendor's verified wallet, signed by your Safe's owners. Settled payments and their on-chain matches are in the On-chain ledger."
            action={
              <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                <Button onClick={() => navigate(PATHS.bills)}>Go to bills</Button>
                <Button variant="secondary" onClick={() => navigate(PATHS.onchain)}>Open On-chain ledger</Button>
              </div>
            }
          />
        </CardBody>
      </Card>
    </div>
  )
}

function ImpsPaymentHub() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const toast = useToast()
  const companyId = user?.company_id
  const isAdmin = user?.role === 'admin'

  const [vendors, setVendors] = useState<PaymentQueueVendor[] | null>(null)
  const [stats, setStats] = useState<DisbursementStats | null>(null)
  const [recent, setRecent] = useState<RecentDisbursement[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<QueueFilter>('all')
  const [view, setView] = useState<'table' | 'lanes'>('table')
  const [otpFor, setOtpFor] = useState<PaymentQueueVendor | null>(null)
  const [overrideFor, setOverrideFor] = useState<PaymentQueueVendor | null>(null)
  const [checking, setChecking] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [remitterOpen, setRemitterOpen] = useState(false)
  const [showAccountNumber, setShowAccountNumber] = useState(false)
  const [mainView, setMainView] = useState<'queue' | 'activity'>('queue')
  const [page, setPage] = useState(1)
  const [expandedVendors, setExpandedVendors] = useState<Set<string>>(new Set())
  // Same tenant-configurable label names ("Change priority names") the Bills
  // page uses — fetched once, defaulting to P0..P3 while it loads so nothing
  // renders blank in the meantime.
  const [priorityLabels, setPriorityLabels] = useState<PriorityLabels>(DEFAULT_PRIORITY_LABELS)
  // Which bank moves the money. The list comes from the backend's adapter
  // registry, so a bank whose status codes it cannot read is never offerable.
  // Undefined until loaded, which the API treats as "the remitter's own bank".
  const [banks, setBanks] = useState<DisbursalBank[]>([])
  // Refresh keeps describing the bank that's chosen, not the tenant's first account.
  const bankRef = useRef<string | undefined>(undefined)
  const [bankProvider, setBankProvider] = useState<string | undefined>()
  useEffect(() => { fetchPriorityLabels().then(setPriorityLabels).catch(() => {}) }, [])

  const toggleExpanded = useCallback((supplierId: string) => {
    setExpandedVendors(prev => {
      const next = new Set(prev)
      if (next.has(supplierId)) next.delete(supplierId)
      else next.add(supplierId)
      return next
    })
  }, [])

  const load = useCallback((background = false) => {
    if (background) setRefreshing(true)
    else setLoading(true)
    setError('')
    return Promise.all([
      fetchDisbursementQueue(companyId),
      fetchDisbursementStats(companyId, bankRef.current).catch(() => null),
      fetchRecentDisbursements(12, companyId).catch(() => []),
      fetchDisbursalBanks().catch(() => null),
    ])
      .then(([q, s, r, b]) => {
        setVendors(q); setStats(s); setRecent(r)
        if (b) {
          setBanks(b.banks)
          setBankProvider(prev => prev ?? loadDisbursalBank(companyId) ?? s?.bank_provider ?? b.default)
        }
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the payment hub'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [companyId])

  useEffect(() => { load() }, [load])

  // The card describes the chosen bank's own remitter account and balance.
  const [bankStatsLoading, setBankStatsLoading] = useState(false)
  useEffect(() => {
    bankRef.current = bankProvider ?? undefined
    if (bankProvider) saveDisbursalBank(companyId, bankProvider)
  }, [bankProvider, companyId])
  useEffect(() => {
    if (!bankProvider || !stats || stats.bank_provider === bankProvider) return
    let live = true
    setBankStatsLoading(true)
    fetchDisbursementStats(companyId, bankProvider)
      .then(s => { if (live) setStats(s) })
      .catch(() => {})
      .finally(() => { if (live) setBankStatsLoading(false) })
    return () => { live = false }
  }, [bankProvider, companyId])  // eslint-disable-line react-hooks/exhaustive-deps

  // Only ticks while something is actually counting down, so the page doesn't
  // re-render every second with nothing awaiting a code.
  useEffect(() => {
    if (!(vendors ?? []).some(v => v.pending_status === 'PENDING_OTP')) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [vendors])

  /** Poll one in-flight transfer at the bank. Shared by the table row and the activity feed. */
  const checkStatus = useCallback(async (disbursementId: string, label: string) => {
    setChecking(disbursementId)
    try {
      const res = await refreshDisbursementStatus(disbursementId, companyId)
      if (res.status === 'SUCCESS') toast.success(`${label} paid`, `Bank reference ${res.bank_rrn ?? '—'}`)
      else if (res.status === 'FAILED') toast.error(`${label} failed`, res.status_message || 'The bank rejected this transfer.')
      else toast.warn(`${label} still at the bank`, 'The beneficiary bank has not confirmed credit yet.')
      await load(true)
    } catch (e) {
      toast.error('Could not check this payment', e instanceof Error ? e.message : undefined)
    } finally {
      setChecking(null)
    }
  }, [companyId, load, toast])

  const counts = useMemo(() => {
    const out = { all: vendors?.length ?? 0, ready: 0, otp: 0, processing: 0, blocked: 0, failed: 0 }
    for (const v of vendors ?? []) {
      out[stageOf(v)]++
      if (v.failed_attempts && v.failed_attempts.length > 0) out.failed++
    }
    return out
  }, [vendors])

  const filtered = useMemo(() => {
    let rows = vendors ?? []
    if (filter === 'failed') rows = rows.filter(v => (v.failed_attempts?.length ?? 0) > 0)
    else if (filter !== 'all') rows = rows.filter(v => stageOf(v) === filter)
    const q = search.trim().toLowerCase()
    if (q) rows = rows.filter(v => v.vendor_name.toLowerCase().includes(q) || (v.gstin ?? '').toLowerCase().includes(q))
    return rows
  }, [vendors, filter, search])

  // Selecting Failed used to show every vendor's failure list inline,
  // unconditionally. It still does, functionally — just through the same
  // expand panel every other row uses, auto-opened for exactly the rows this
  // filter surfaces rather than a second rendering path.
  useEffect(() => {
    if (filter === 'failed') setExpandedVendors(new Set(filtered.map(v => v.supplier_id)))
  }, [filter, filtered])

  const PAGE_SIZE = 10
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  // A new filter or search term changes what page 1 even means, so start
  // there rather than showing whatever page number happened to be selected
  // under the old result set.
  useEffect(() => { setPage(1) }, [filter, search])
  // A background refresh can shrink the result set (a payment clears, a row
  // moves stage) and leave `page` pointing past the end.
  useEffect(() => { setPage(p => Math.min(p, totalPages)) }, [totalPages])
  const pageRows = useMemo(
    () => filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [filtered, page],
  )

  const totalPayable = useMemo(() => (vendors ?? []).reduce((s, v) => s + v.payable_amount, 0), [vendors])
  const readyTotal = useMemo(
    () => (vendors ?? []).filter(v => stageOf(v) === 'ready').reduce((s, v) => s + v.payable_amount, 0),
    [vendors],
  )
  const blockedTotal = useMemo(
    () => (vendors ?? []).filter(v => stageOf(v) === 'blocked').reduce((s, v) => s + v.payable_amount, 0),
    [vendors],
  )

  const laned = useMemo(() => {
    const out: Record<'ready' | 'blocked' | 'otp', PaymentQueueVendor[]> = { ready: [], otp: [], blocked: [] }
    for (const v of filtered) {
      const s = stageOf(v)
      // A vendor 'processing' (at the bank) has nothing left to queue on — its
      // disbursement's own fate is what matters now, and that's the Released
      // lane's job, sourced from `recent` rather than the vendor queue.
      if (s === 'ready' || s === 'blocked' || s === 'otp') out[s].push(v)
    }
    return out
  }, [filtered])

  const exportCsv = useCallback(() => {
    const blob = new Blob([toCsv(filtered)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `payment-queue-${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Queue exported', `${filtered.length} vendor${filtered.length === 1 ? '' : 's'} written to CSV.`)
  }, [filtered, toast])

  /**
   * The per-row primary action. One button per stage, because the next step for
   * a ready vendor, one mid-OTP, one at the bank and one that needs an override
   * are four different things — a single row-click could only ever mean one.
   */
  const RowAction = useCallback(({ v }: { v: PaymentQueueVendor }) => {
    const stage = stageOf(v)
    if (stage === 'otp') {
      return (
        <Button size="sm" variant="success" iconLeft={<KeyRound size={12} />} disabled={!isAdmin}
          title={isAdmin ? 'Enter the emailed code to release this payment' : 'Only an admin can release a payment'}
          onClick={() => setOtpFor(v)}>
          Enter code
        </Button>
      )
    }
    if (stage === 'processing') {
      return (
        <Button size="sm" variant="secondary" iconLeft={<RefreshCw size={12} className={checking === v.pending_disbursement_id ? 'pmh__spin' : ''} />}
          loading={checking === v.pending_disbursement_id}
          onClick={() => checkStatus(v.pending_disbursement_id as string, v.vendor_name)}>
          Check status
        </Button>
      )
    }
    if (stage === 'blocked') {
      return (
        <Button size="sm" variant="secondary" iconLeft={<ShieldAlert size={12} />} disabled={!isAdmin}
          onClick={() => setOverrideFor(v)}>
          Override…
        </Button>
      )
    }
    return (
      <Button size="sm" variant="primary" iconLeft={<KeyRound size={12} />} disabled={!isAdmin}
        title={isAdmin ? undefined : 'Only an admin can release a payment'}
        onClick={() => navigate(payPath(v.supplier_id, undefined, undefined, bankProvider))}>
        Pay with OTP
      </Button>
    )
  }, [checkStatus, checking, isAdmin, navigate])

  const columns = useMemo<Column<PaymentQueueVendor>[]>(() => [
    {
      key: 'vendor', header: 'Vendor', pinned: true,
      sortValue: v => v.vendor_name,
      render: v => (
        <button type="button" className="pmh__vendor-toggle" onClick={() => toggleExpanded(v.supplier_id)}
          aria-expanded={expandedVendors.has(v.supplier_id)}
          title={expandedVendors.has(v.supplier_id) ? 'Hide bills' : 'Show bills'}>
          {expandedVendors.has(v.supplier_id) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>
            <div className="pmh__vendor-name">{v.vendor_name}</div>
            {v.gstin && <div className="pmh__vendor-sub">{v.gstin}</div>}
          </span>
        </button>
      ),
    },
    {
      key: 'beneficiary', header: 'Beneficiary account',
      render: v => v.beneficiary_account_masked ? (
        <div>
          <div className="pmh__vendor-name">{v.beneficiary_bank_name || 'Bank'}</div>
          <div className="pmh__vendor-sub">{v.beneficiary_account_masked}</div>
        </div>
      ) : <span className="pmh__vendor-sub">No account on file</span>,
    },
    {
      key: 'verification', header: 'Verification',
      render: v => (
        <div className="pmh__verify">
          <Badge tone={v.gstin_verified ? 'success' : 'danger'} icon={v.gstin_verified ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}>
            GSTIN
          </Badge>
          <Badge tone={v.bank_verified ? 'success' : 'danger'} icon={v.bank_verified ? <ShieldCheck size={11} /> : <Landmark size={11} />}>
            {v.bank_verified ? 'Penny-drop' : 'Bank unverified'}
          </Badge>
        </div>
      ),
    },
    { key: 'bills', header: 'Bills', align: 'right', width: 72, sortValue: v => v.bills_count, render: v => v.bills_count },
    {
      key: 'payable', header: 'Payable', align: 'right', width: 140,
      sortValue: v => v.payable_amount,
      render: v => <b>{formatMoney(v.payable_amount)}</b>,
    },
    {
      // Failure history moved to the row's expand panel (click the vendor
      // name) rather than living under the badge on every tab — the Failed
      // filter now expands into that same panel automatically.
      key: 'stage', header: 'Stage', width: 170,
      render: v => <StageBadge v={v} now={now} />,
    },
    { key: 'action', header: '', width: 158, align: 'right', render: v => <RowAction v={v} /> },
  ], [RowAction, now, expandedVendors, toggleExpanded])

  if (error && !vendors) return <ErrorState title="Could not load the payment hub" message={error} onRetry={() => load()} />

  return (
    <div className="pmh">
      <PageHeader
        title="Payment Hub"
        sub="IMPS disbursement to verified vendors — released with an OTP. An unverified beneficiary needs an admin override first."
        actions={
          <>
            <div className="pmh__view-toggle" role="group" aria-label="Section">
              <button type="button" aria-pressed={mainView === 'queue'}
                className={`pmh__view-btn${mainView === 'queue' ? ' pmh__view-btn--active' : ''}`}
                onClick={() => setMainView('queue')}>
                <LayoutList size={13} /> Queue
              </button>
              <button type="button" aria-pressed={mainView === 'activity'}
                className={`pmh__view-btn${mainView === 'activity' ? ' pmh__view-btn--active' : ''}`}
                onClick={() => setMainView('activity')}>
                <Clock size={13} /> Activity
              </button>
            </div>
            <Button variant="secondary" iconLeft={<Download size={14} />} onClick={exportCsv} disabled={filtered.length === 0}>
              Export queue
            </Button>
            <Button variant="secondary" iconLeft={<RefreshCw size={14} className={refreshing ? 'pmh__spin' : ''} />} onClick={() => load(true)} disabled={refreshing}>
              Refresh
            </Button>
          </>
        }
      />

      {error && vendors && <div className="pmh__refresh-error" role="alert">Could not refresh: {error}</div>}

      {!isAdmin && (
        <div className="pmh__note" role="note">
          You can see everything owed, but only an admin can release a payment or approve an override.
        </div>
      )}

      <Card>
        <CardBody>
          <button
            type="button"
            className={`pmh__remitter${bankStatsLoading ? ' pmh__remitter--loading' : ''}`}
            onClick={() => setRemitterOpen(o => !o)}
            aria-expanded={remitterOpen}
          >
            <BankBadge code={stats?.bank_provider ?? bankProvider} />
            <div className="pmh__remitter-detail">
              <div className="pmh__remitter-bank">{stats ? (stats.bank_name || stats.bank_provider) : <Skeleton width={120} height={14} />}</div>
              <div className="pmh__remitter-account">
                {stats ? `${stats.bank_provider} · ···${stats.remitter_account_number.slice(-4)}` : <Skeleton width={90} height={12} />}
              </div>
            </div>
            <div className="pmh__remitter-balance">
              <div className="pmh__stat-label">Balance</div>
              <div className="pmh__stat-value">{stats ? formatMoney(stats.balance) : <Skeleton width={110} height={22} />}</div>
            </div>
            <ChevronDown size={16} className={`pmh__remitter-chevron${remitterOpen ? ' pmh__remitter-chevron--open' : ''}`} aria-hidden="true" />
          </button>

          {/* Which bank payments go out through: on the card, not tucked in the
              expanded panel, and the card above shows that bank's own account. */}
          {banks.length > 1 && (
            <div className="pmh__banks">
              <span className="pmh__banks-label" id="pmh-banks-label">Pay through</span>
              <div className="pmh__banks-seg" role="radiogroup" aria-labelledby="pmh-banks-label">
                {banks.map(b => (
                  <button key={b.code} type="button" role="radio" aria-checked={bankProvider === b.code}
                    className={`pmh__bank-opt${bankProvider === b.code ? ' pmh__bank-opt--on' : ''}`}
                    onClick={() => setBankProvider(b.code)}>
                    <BankBadge code={b.code} small />{b.label.replace(/ \(Demo\)$/, '')}
                  </button>
                ))}
              </div>
              <span className="pmh__remitter-note">
                Payments you start here go through this bank, from its own account. Each bank reports
                status differently, so this also decides how results are read.
              </span>
            </div>
          )}

          {remitterOpen && stats && (
            <div className="pmh__remitter-expand">
              <div className="pmh__remitter-row">
                <span className="pay-bene__key">Account number</span>
                <span className="pay-bene__val pay-bene__val--mono">
                  {showAccountNumber ? stats.remitter_account_number : `···${stats.remitter_account_number.slice(-4)}`}
                </span>
                <button type="button" className="pmh__remitter-iconbtn" title={showAccountNumber ? 'Hide' : 'Show full number'}
                  onClick={() => setShowAccountNumber(s => !s)}>
                  {showAccountNumber ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
                <button type="button" className="pmh__remitter-iconbtn" title="Copy account number"
                  onClick={() => { navigator.clipboard?.writeText(stats.remitter_account_number); toast.success('Copied', 'Account number copied to clipboard.') }}>
                  <Copy size={13} />
                </button>
              </div>
              <div className="pmh__remitter-row">
                <span className="pay-bene__key">IFSC</span>
                <span className="pay-bene__val pay-bene__val--mono">{stats.ifsc ?? '—'}</span>
                {stats.ifsc && (
                  <button type="button" className="pmh__remitter-iconbtn" title="Copy IFSC"
                    onClick={() => { navigator.clipboard?.writeText(stats.ifsc as string); toast.success('Copied', 'IFSC copied to clipboard.') }}>
                    <Copy size={13} />
                  </button>
                )}
              </div>
              <div className="pmh__remitter-row">
                <span className="pay-bene__key">Provider</span>
                <span className="pay-bene__val">{stats.bank_provider}</span>
              </div>
              <div className="pmh__remitter-row">
                <span className="pay-bene__key">Disbursed to date</span>
                <span className="pay-bene__val">{formatMoney(stats.total_disbursed)} across {stats.successful_payments} payment{stats.successful_payments === 1 ? '' : 's'}</span>
              </div>
            </div>
          )}
        </CardBody>
      </Card>

      <div className="pmh__stats">
        {loading && !stats
          ? Array.from({ length: 4 }, (_, i) => <Card key={i}><CardBody><Skeleton width={70} height={11} /><Skeleton width={110} height={26} /></CardBody></Card>)
          : (
            <>
              <StatTile
                label="Ready to pay"
                value={formatMoney(readyTotal)}
                sub={`${counts.ready} verified vendor${counts.ready === 1 ? '' : 's'} · ${formatMoney(totalPayable)} owed in all`}
              />
              <StatTile
                label="Needs override"
                value={formatMoney(blockedTotal)}
                sub={`${counts.blocked} unverified beneficiar${counts.blocked === 1 ? 'y' : 'ies'}`}
                tone={counts.blocked > 0 ? 'warn' : undefined}
              />
              <StatTile
                label="Processing"
                value={String(counts.otp + counts.processing)}
                sub={`${counts.otp} awaiting OTP · ${counts.processing} at the bank`}
                tone={counts.otp + counts.processing > 0 ? 'warn' : undefined}
              />
              <StatTile
                label="Failed"
                value={String(stats?.failed_payments ?? 0)}
                sub={stats ? `${formatMoneyCompact(stats.total_disbursed)} disbursed to date` : undefined}
                tone={stats && stats.failed_payments > 0 ? 'danger' : undefined}
              />
            </>
          )}
      </div>

      {mainView === 'queue' && (
      <Card>
        <CardBody>
          <div className="pmh__toolbar">
            <div className="pmh__search">
              <Search size={14} aria-hidden="true" />
              <input type="search" placeholder="Search vendor or GSTIN" value={search}
                onChange={e => setSearch(e.target.value)} aria-label="Search vendors" />
            </div>
            <div className="pmh__filters" role="tablist" aria-label="Filter vendors">
              {FILTERS.map(f => (
                <button key={f.key} type="button" role="tab" aria-selected={filter === f.key}
                  className={`pmh__filter${filter === f.key ? ' pmh__filter--active' : ''}`}
                  onClick={() => setFilter(f.key)}>
                  {f.label}
                  <span className="pmh__filter-count">{counts[f.key === 'all' ? 'all' : f.key]}</span>
                </button>
              ))}
            </div>
            <div className="pmh__view-toggle" role="group" aria-label="Layout">
              <button type="button" aria-pressed={view === 'table'}
                className={`pmh__view-btn${view === 'table' ? ' pmh__view-btn--active' : ''}`}
                onClick={() => setView('table')}>
                <LayoutList size={13} /> Table
              </button>
              <button type="button" aria-pressed={view === 'lanes'}
                className={`pmh__view-btn${view === 'lanes' ? ' pmh__view-btn--active' : ''}`}
                onClick={() => setView('lanes')}>
                <Columns3 size={13} /> Lanes
              </button>
            </div>
          </div>

          {!loading && (vendors?.length ?? 0) === 0 ? (
            <EmptyState
              icon={<Banknote size={20} />}
              title="Nothing owed right now"
              message="Every bill is settled, or nothing has been approved for payment yet."
            />
          ) : view === 'table' ? (
            <>
              <DataTable
                rows={pageRows}
                columns={columns}
                rowKey={v => v.supplier_id}
                loading={loading}
                caption="Vendors with money owed"
                empty={<EmptyState title="No vendors match this filter" message="Try a different filter or clear the search." />}
                expandedIds={expandedVendors}
                renderExpanded={v => <VendorExpanded v={v} showFailures={filter === 'failed'} isAdmin={isAdmin} companyId={companyId} bankProvider={bankProvider} labels={priorityLabels} onPaid={() => load(true)} />}
              />
              {filtered.length > 0 && (
                <div className="pmh__pager">
                  <span className="pmh__pager-count">
                    {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filtered.length)} of {filtered.length}
                  </span>
                  <div className="pmh__pager-nav">
                    <Button size="sm" variant="secondary" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1}>
                      Previous
                    </Button>
                    <span className="pmh__pager-page">Page {page} of {totalPages}</span>
                    <Button size="sm" variant="secondary" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="pmh__lanes">
              {LANES.map(lane => (
                <div className="pmh__lane" key={lane.key}>
                  <div className="pmh__lane-head">
                    <span className={`pmh__lane-dot pmh__lane-dot--${lane.key}`} aria-hidden="true" />
                    <span className="pmh__lane-title">{lane.label}</span>
                    <span className="pmh__lane-count">{lane.key === 'released' ? (recent?.length ?? 0) : laned[lane.key].length}</span>
                  </div>
                  <div className="pmh__lane-body">
                    {lane.key === 'released' ? (
                      !recent || recent.length === 0 ? (
                        <div className="pmh__lane-empty">None</div>
                      ) : (
                        recent.map(r => (
                          <div key={r.disbursement_id} className="pmh__lane-card">
                            <div className="pmh__vendor-name">{r.vendor_name ?? 'Vendor'}</div>
                            <div className="pmh__lane-amount">{formatMoney(r.amount)}</div>
                            {r.status === 'SUCCESS' && (
                              <>
                                <div className="pmh__lane-meta">
                                  Paid {formatDateTime(r.updated_at)}{r.bank_rrn ? ` · RRN ${r.bank_rrn}` : ''}
                                </div>
                                <div className="pmh__lane-action">
                                  <Button size="sm" variant="secondary" iconLeft={<Download size={12} />}
                                    onClick={() => {
                                      const data = recentToReceiptData(r, stats)
                                      downloadPdf(buildPdf(receiptPdfLines(data), 'Payment receipt'), receiptFileName(data))
                                    }}>
                                    Download receipt
                                  </Button>
                                </div>
                              </>
                            )}
                            {r.status === 'PENDING' && (
                              <>
                                <div className="pmh__lane-meta">
                                  <Clock size={11} style={{ verticalAlign: -1 }} aria-hidden="true" /> At the bank
                                </div>
                                <div className="pmh__lane-action">
                                  <Button size="sm" variant="secondary" iconLeft={<RefreshCw size={12} className={checking === r.disbursement_id ? 'pmh__spin' : ''} />}
                                    loading={checking === r.disbursement_id}
                                    onClick={() => checkStatus(r.disbursement_id, r.vendor_name ?? 'Payment')}>
                                    Check status
                                  </Button>
                                </div>
                              </>
                            )}
                            {r.status === 'FAILED' && (
                              <>
                                <div className="pmh__lane-meta">
                                  {r.invoice_number ?? r.invoice_id.slice(0, 8)}
                                  {/* bank_rrn only exists once the bank itself assigned one before
                                      rejecting — a gateway-level failure never gets one, so this
                                      falls back to our own tran_ref_no, labelled as such, not as an RRN. */}
                                  {r.bank_rrn ? ` · RRN ${r.bank_rrn}` : r.tran_ref_no ? ` · Ref ${r.tran_ref_no}` : ''}
                                </div>
                                <div className="pmh__lane-fail" title={r.status_message ?? undefined}>
                                  {r.status_message || 'Failed — no reason recorded'}
                                </div>
                                {r.supplier_id && (
                                  <div className="pmh__lane-action">
                                    <Button size="sm" variant="secondary" disabled={!isAdmin}
                                      title={isAdmin ? 'No funds left the account — start this payment again' : 'Only an admin can release a payment'}
                                      onClick={() => navigate(payPath(r.supplier_id as string, r.invoice_id, undefined, bankProvider))}>
                                      Fix &amp; retry
                                    </Button>
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        ))
                      )
                    ) : laned[lane.key].length === 0 ? (
                      <div className="pmh__lane-empty">None</div>
                    ) : (
                      laned[lane.key].map(v => (
                        <div key={v.supplier_id} className="pmh__lane-card">
                          <div className="pmh__vendor-name">{v.vendor_name}</div>
                          {v.gstin && <div className="pmh__vendor-sub">{v.gstin}</div>}
                          {v.beneficiary_account_masked && (
                            <div className="pmh__vendor-sub">
                              {v.beneficiary_bank_name || 'Bank'} ···{v.beneficiary_account_masked.slice(-4)}
                            </div>
                          )}
                          <div className="pmh__verify pmh__lane-verify">
                            <Badge tone={v.gstin_verified ? 'success' : 'danger'} icon={v.gstin_verified ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}>
                              GSTIN
                            </Badge>
                            <Badge tone={v.bank_verified ? 'success' : 'danger'} icon={v.bank_verified ? <ShieldCheck size={11} /> : <Landmark size={11} />}>
                              {v.bank_verified ? 'Penny-drop' : 'Bank unverified'}
                            </Badge>
                          </div>
                          <div className="pmh__lane-amount">{formatMoney(v.payable_amount)}</div>
                          <div className="pmh__lane-meta">
                            {v.bills_count} bill{v.bills_count === 1 ? '' : 's'}
                            {v.earliest_due ? ` · due ${formatRelativeDays(v.earliest_due) ?? formatDate(v.earliest_due)}` : ''}
                          </div>
                          {lane.key === 'otp' && <StageBadge v={v} now={now} />}
                          <div className="pmh__lane-action"><RowAction v={v} /></div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
      )}

      {mainView === 'activity' && (
      <Card>
        <CardBody>
          <h2 className="pmh__section-title">Recent activity</h2>
          {loading && !recent ? (
            <Skeleton height={140} />
          ) : !recent || recent.length === 0 ? (
            <EmptyState title="No payments yet" message="Disbursements will show up here once a vendor is paid." />
          ) : (
            <ul className="pmh__activity">
              {recent.map(r => (
                <li key={r.disbursement_id} className="pmh__activity-row">
                  <Badge tone={STATUS_TONE[r.status] ?? 'neutral'}>{r.status.replace('_', ' ')}</Badge>
                  <div className="pmh__activity-main">
                    <span className="pmh__activity-vendor">{r.vendor_name ?? 'Vendor'}</span>
                    <span className="pmh__activity-meta">
                      {r.invoice_number ?? r.invoice_id.slice(0, 8)}
                      {r.bank_rrn ? ` · RRN ${r.bank_rrn}` : ''}
                      {' · '}{formatDateTime(r.updated_at)}
                      {(r.updated_by ?? r.initiated_by) ? ` · ${r.updated_by ?? r.initiated_by}` : ''}
                      {r.status === 'FAILED' && r.status_message ? ` · ${r.status_message}` : ''}
                    </span>
                  </div>
                  <b className="pmh__activity-amount">{formatMoney(r.amount)}</b>
                  <div className="pmh__activity-action">
                    {r.status === 'PENDING' && (
                      <Button size="sm" variant="secondary" iconLeft={<RefreshCw size={12} />}
                        loading={checking === r.disbursement_id}
                        onClick={() => checkStatus(r.disbursement_id, r.vendor_name ?? 'Payment')}>
                        Check status
                      </Button>
                    )}
                    {r.status === 'FAILED' && r.supplier_id && (
                      <Button size="sm" variant="secondary" disabled={!isAdmin}
                        title={isAdmin ? 'No funds left the account — start this payment again' : 'Only an admin can release a payment'}
                        onClick={() => navigate(payPath(r.supplier_id as string, r.invoice_id, undefined, bankProvider))}>
                        Retry
                      </Button>
                    )}
                    {r.status === 'SUCCESS' && r.bank_rrn && (
                      <Button size="sm" variant="ghost" iconLeft={<Copy size={12} />}
                        onClick={() => {
                          navigator.clipboard?.writeText(r.bank_rrn as string)
                          toast.success('Bank reference copied', r.bank_rrn as string)
                        }}>
                        Copy RRN
                      </Button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
      )}

      {otpFor && otpFor.pending_disbursement_id && (
        <ReleaseOtpModal
          vendor={otpFor}
          companyId={companyId}
          onClose={() => setOtpFor(null)}
          onReleased={() => load(true)}
        />
      )}

      {overrideFor && (
        <OverridePanel
          vendor={overrideFor}
          companyId={companyId}
          bankProvider={bankProvider}
          onClose={() => setOverrideFor(null)}
          onDone={() => load(true)}
        />
      )}
    </div>
  )
}

/** Inline amount box with en-IN separators; a component so the hook can run per row. */
function BillAmountInput({ value, invalid, label, onValue }: {
  value: number
  invalid: boolean
  label: string
  onValue: (v: number) => void
}) {
  const field = useAmountField(value, onValue)
  return (
    <input
      className={`u-input pmh__expand-bill-input${invalid ? ' u-input--invalid' : ''}`}
      aria-label={label}
      {...field}
    />
  )
}
