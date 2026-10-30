/**
 * The USDC rail inside the payment page (05_USER_FLOWS.md, flow 5).
 *
 *   1. Choose the bill and amount; the withholding preview and policy result
 *      are shown before anything is created.
 *   2. Prepare: the backend freezes the FX snapshot, works out withholding and
 *      emails the release OTP — the same gate IMPS uses (OtpStep is reused).
 *   3. Sign: each Safe owner signs the Safe transaction in their own wallet
 *      (eth_signTypedData_v4). Onsumm only collects signatures; when the
 *      threshold is met a gas-only relayer submits it.
 *   4. Watch: PROPOSED → PARTIALLY_SIGNED → READY → SUBMITTED → CONFIRMING →
 *      CONFIRMED, polled every 3 seconds, ending on a receipt with the tx hash.
 *
 * The IMPS path in usePaymentFlow is untouched; this component replaces the
 * page body only while the USDC rail is selected.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, ExternalLink, Link2, PenLine, RefreshCw, ShieldCheck, Wallet, XCircle } from 'lucide-react'
import {
  apiError, cancelCryptoPayout, createUsdcPayout, fetchCryptoStatus, fetchPendingUsdcPayouts, fetchSafeTx, fetchWithholdingPreview, keepOnlyCryptoPayout,
  postSafeSignature, reproposeCryptoPayout, verifyUsdcOtp, type CryptoStatus, type PayoutPreview, type PendingUsdcPayout, type RailPolicy, type SafeTxView,
  type SupplierWallet, type TreasuryWallet, type WithholdingPreview,
} from '../../api/crypto'
import type { Vendor } from '../../api/invoices'
import { BillPicker, PayeeCard, PayFooterSummary, PaymentSummary, UsdcStepper } from './UsdcPayParts'
import { preflight } from './usdcPay'
import { Badge, Button, Input, useToast } from '../../ui'
import { AddressLink, Note, RefusalList, StatusBadge, TxLink } from '../../components/crypto/common'
import { chooseAccount, connectedAccounts, connectWallet, ensureBaseSepolia, hasWallet, signTypedData, walletErrorMessage } from '../../web3/eip1193'
import { explorerAddress, formatFiat, formatUsdc } from '../../web3/units'
import { resendDisbursementOtp } from '../../api/invoices'
import { OtpStep } from './OtpStep'
import type { PayBill } from './usePaymentFlow'
import '../crypto/crypto.css'

type CryptoStep = 'select' | 'verify' | 'sign' | 'receipt'

const TIMELINE = [
  { key: 'PROPOSED', label: 'Safe transaction proposed' },
  { key: 'PARTIALLY_SIGNED', label: 'Collecting owner signatures' },
  { key: 'READY', label: 'Threshold met' },
  { key: 'SUBMITTED', label: 'Submitted by the relayer' },
  { key: 'CONFIRMING', label: 'Confirming on Base Sepolia' },
  { key: 'CONFIRMED', label: 'Confirmed and reconciled' },
] as const

const FAILED_SUBS = new Set(['REJECTED', 'EXPIRED', 'REVERTED', 'REPLACED'])
const POLL_MS = 3_000

function Timeline({ status }: { status: CryptoStatus | null }) {
  const sub = status?.sub_status ?? 'PROPOSED'
  const failed = FAILED_SUBS.has(sub) || status?.status === 'FAILED'
  const idx = TIMELINE.findIndex(t => t.key === sub)
  return (
    <ol className="cr-timeline" aria-label="Payout progress">
      {TIMELINE.map((t, i) => {
        const cls = failed ? (i <= Math.max(idx, 0) ? 'is-done' : '') : i < idx || sub === 'CONFIRMED' ? 'is-done' : i === idx ? 'is-current' : ''
        return (
          <li key={t.key} className={cls}>
            <span className="dot" aria-hidden="true" />
            {t.label}
            {t.key === 'PARTIALLY_SIGNED' && status && ` (${status.signatures.length} of ${status.threshold})`}
            {t.key === 'CONFIRMING' && status && (sub === 'CONFIRMING' || sub === 'SUBMITTED') && ` (${status.confirmations} of ${status.confirmations_target})`}
          </li>
        )
      })}
      {failed && (
        <li className="is-failed">
          <span className="dot" aria-hidden="true" />
          {sub.replace(/_/g, ' ').toLowerCase()} — {status?.failure_reason ?? 'payout stopped'}
        </li>
      )}
    </ol>
  )
}

function PreviewCard({ p }: { p: PayoutPreview }) {
  return (
    <div className="pay-card">
      <h2 className="pay-card__title">USDC payout</h2>
      <dl className="cr-kv">
        <dt>Bill</dt><dd>{p.bill.invoice_number || p.bill.invoice_id.slice(0, 8)} · billed {formatFiat(p.bill.billed, p.bill.currency)} · paying {formatFiat(p.bill.approved, p.bill.currency)}</dd>
        <dt>Gross</dt><dd>{formatUsdc(p.gross_units)}</dd>
        <dt>Withholding</dt>
        <dd className="cr-row">
          {formatUsdc(p.withholding.amount_units)}
          <Badge tone="neutral">{p.withholding.rule_pack}{p.withholding.rule_code ? ` · ${p.withholding.rule_code}` : ''} · {Number(p.withholding.rate) * 100}%</Badge>
          {p.withholding.requires_signoff && <Badge tone="warn">Requires adviser sign-off</Badge>}
        </dd>
        <dt>Net to vendor</dt><dd><strong>{formatUsdc(p.net_units)}</strong></dd>
        <dt>Network fee</dt><dd>{p.estimated_gas_fee ?? '—'} <span className="cr-muted">(paid by the relayer)</span></dd>
        <dt>To</dt><dd className="cr-row">{p.to.supplier} <AddressLink address={p.to.address} href={explorerAddress(p.to.address)} /> <span className="cr-muted">{p.to.chain}</span></dd>
        {p.tax_wallet && (<><dt>Tax wallet</dt><dd><AddressLink address={p.tax_wallet} href={explorerAddress(p.tax_wallet)} /></dd></>)}
        <dt>From</dt><dd className="cr-row">Safe <AddressLink address={p.from.safe} href={explorerAddress(p.from.safe)} /> <span className="cr-muted">{p.from.threshold} of {p.from.owners} signatures</span></dd>
        <dt>Rate</dt><dd className="cr-muted">1 USDC = {p.quote.token_usd} USD · 1 USD = {p.quote.usd_inr} INR (reference)</dd>
      </dl>
    </div>
  )
}

/**
 * A link that opens this payment page straight on a payout's signing step.
 * Safe owners sign on their own devices, so the payout has to be reachable from
 * any browser: the link carries the payout id, and the signed-in user (any user
 * of the tenant) lands on the same Safe transaction.
 */
function signingLink(disbursementId: string, invoiceId?: string): string {
  const u = new URL(window.location.href)
  u.searchParams.set('payout', disbursementId)
  if (invoiceId) u.searchParams.set('bill', invoiceId)
  return u.toString()
}

function SafeSignStep({ disbursementId, onDone }: { disbursementId: string; onDone: (s: CryptoStatus) => void }) {
  const toast = useToast()
  const [safeTx, setSafeTx] = useState<SafeTxView | null>(null)
  const [status, setStatus] = useState<CryptoStatus | null>(null)
  const [account, setAccount] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [cancelling, setCancelling] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [cancelReason, setCancelReason] = useState('')
  const [rebuilding, setRebuilding] = useState(false)
  const [keeping, setKeeping] = useState(false)
  const doneRef = useRef(false)

  const loadSafeTx = useCallback(() => {
    fetchSafeTx(disbursementId).then(setSafeTx).catch(e => setErr(apiError(e).message))
  }, [disbursementId])
  useEffect(loadSafeTx, [loadSafeTx])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s = await fetchCryptoStatus(disbursementId)
        if (!alive) return
        setStatus(s)
        if (!doneRef.current && (s.status === 'SUCCESS' || s.status === 'FAILED')) {
          doneRef.current = true
          onDone(s)
        }
      } catch {
        /* the next tick retries; a transient error must not end the watch */
      }
    }
    void tick()
    const t = setInterval(() => void tick(), POLL_MS)
    return () => { alive = false; clearInterval(t) }
  }, [disbursementId, onDone])

  const signers = new Set((status?.signatures ?? safeTx?.signatures ?? []).map(s => s.signer.toLowerCase()))
  const threshold = status?.threshold ?? safeTx?.threshold ?? 0
  const alreadySigned = !!account && signers.has(account.toLowerCase())
  const canSign = !!safeTx && ['PROPOSED', 'PARTIALLY_SIGNED'].includes(status?.sub_status ?? 'PROPOSED')

  const owners = useMemo(() => new Set((safeTx?.owners ?? []).map(o => o.toLowerCase())), [safeTx])
  const remaining = Math.max(0, threshold - signers.size)

  /**
   * One click collects every signature still needed. Each Safe owner connected
   * to this site signs in turn (the wallet shows one confirmation per owner, with
   * no account switching). If too few owners are connected, the wallet's account
   * picker opens once so the user can tick the rest.
   */
  const sign = async () => {
    if (!safeTx) return
    setBusy(true)
    setErr('')
    const done = new Set(signers)
    const need = () => Math.max(0, threshold - done.size)
    const unsignedOwners = async () => (await connectedAccounts())
      .filter(a => (!owners.size || owners.has(a.toLowerCase())) && !done.has(a.toLowerCase()))
    try {
      await connectWallet()
      await ensureBaseSepolia()
      let queue = await unsignedOwners()
      if (queue.length < need()) {
        toast.warn('Pick the Safe owners', 'Tick every owner account in the wallet so all can sign in one go.')
        await chooseAccount()
        queue = await unsignedOwners()
      }
      if (!queue.length) {
        throw new Error('None of the connected accounts is a Safe owner that still has to sign. Connect an owner account in the wallet.')
      }
      for (const addr of queue.slice(0, need())) {
        setAccount(addr)
        const signature = await signTypedData(addr, safeTx.typed_data)
        const res = await postSafeSignature(disbursementId, addr, signature)
        done.add(addr.toLowerCase())
        toast.success('Signature added', `${res.signatures} of ${res.threshold}${res.tx_hash ? ' — settling now' : ''}`)
      }
      if (need() > 0) {
        setErr(`${need()} more owner signature${need() === 1 ? '' : 's'} needed. Connect the other Safe owner in the wallet and click Sign again, or send them the signing link.`)
      }
      loadSafeTx()
    } catch (e) {
      const info = apiError(e)
      setErr(info.status ? (info.codes.length ? `${info.codes.join(', ')} — ${info.message}` : info.message) : walletErrorMessage(e))
      loadSafeTx()
    } finally {
      setBusy(false)
    }
  }

  // The next owner's account is usually not connected to this site yet, so open
  // the wallet's account picker rather than reusing the connected one.
  const switchAccount = async () => {
    setErr('')
    try {
      const chosen = await chooseAccount()
      // MetaMask returns its *selected* account first even when the user ticks
      // several in the picker; prefer a connected account that has not signed yet.
      const connected = await connectedAccounts()
      setAccount(connected.find(a => !signers.has(a.toLowerCase())) ?? chosen)
    } catch (e) {
      setErr(walletErrorMessage(e))
    }
  }

  const copyLink = async () => {
    const link = signingLink(disbursementId, status?.invoice_id)
    try {
      await navigator.clipboard.writeText(link)
      toast.success('Signing link copied', 'Open it in the browser where the next Safe owner\'s wallet is.')
    } catch {
      window.prompt('Copy this signing link', link)
    }
  }

  const rebuild = async () => {
    setRebuilding(true)
    setErr('')
    try {
      const r = await reproposeCryptoPayout(disbursementId)
      toast.success('Payout rebuilt', `Now at Safe nonce ${r.nonce}. Owners sign again.`)
      loadSafeTx()
    } catch (e) {
      setErr(apiError(e).message)
    } finally {
      setRebuilding(false)
    }
  }

  const keepOnly = async () => {
    const ok = window.confirm(
      'Keep only this payout?\n\n' +
      '• Every other unsent USDC payout for this vendor is cancelled (nothing has been sent for them).\n' +
      '• This payout moves to the front of the Safe queue. Its transaction changes, so any signatures ' +
      'already collected for it are cleared and the owners sign once more.')
    if (!ok) return
    setKeeping(true)
    setErr('')
    try {
      const r = await keepOnlyCryptoPayout(disbursementId)
      toast.success('Only this payout is left',
        `${r.cancelled.length} other payout${r.cancelled.length === 1 ? '' : 's'} cancelled` +
        (r.signatures_cleared ? `; now at Safe nonce ${r.nonce}, so owners sign again.` : '.'))
      loadSafeTx()
    } catch (e) {
      setErr(apiError(e).message)
    } finally {
      setKeeping(false)
    }
  }

  const cancel = async () => {
    if (!cancelReason.trim()) return
    setCancelling(true)
    try {
      const s = await cancelCryptoPayout(disbursementId, cancelReason.trim())
      setStatus(s)
      setCancelOpen(false)
    } catch (e) {
      toast.error('Could not cancel', apiError(e).message)
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="pay-card">
      <div className="cr-row cr-row--between">
        <h2 className="pay-card__title" style={{ margin: 0 }}>Sign with the Safe</h2>
        {status && <StatusBadge status={status.sub_status} />}
      </div>
      <div className="cr-stack" style={{ marginTop: 'var(--sp-3)' }}>
        <Note>
          One click collects every signature: each Safe owner connected to this wallet confirms in turn. When {threshold || 'enough'} owners
          have signed, the payout is submitted automatically. Owners on another device can use the signing link.
        </Note>
        {safeTx && (
          <dl className="cr-kv">
            <dt>Safe</dt><dd><AddressLink address={safeTx.safe} href={explorerAddress(safeTx.safe)} /></dd>
            <dt>Safe tx hash</dt><dd className="cr-mono">{safeTx.safe_tx_hash}</dd>
            <dt>Nonce</dt><dd>{safeTx.nonce}</dd>
            <dt>Signatures</dt>
            <dd className="cr-stack" style={{ gap: 2 }}>
              <span>{signers.size} of {threshold}</span>
              {(status?.signatures ?? safeTx.signatures).map(s => (
                <span key={s.signer} className="cr-mono"><CheckCircle2 size={11} aria-hidden="true" /> {s.signer}</span>
              ))}
            </dd>
          </dl>
        )}
        {safeTx?.waiting_for && (
          <Note tone="warn">
            The Safe runs transactions strictly in order. An earlier payout (nonce {safeTx.waiting_for.nonce},{' '}
            {safeTx.waiting_for.amount}) must be signed or cancelled first, or this one cannot go through.{' '}
            <a href={signingLink(safeTx.waiting_for.disbursement_id, safeTx.waiting_for.invoice_id)}>Open that payout</a>
            {' '}or keep only this one.
            <div style={{ marginTop: 8 }}>
              <Button size="sm" variant="secondary" loading={keeping} onClick={() => void keepOnly()}>
                Keep only this payout
              </Button>
            </div>
          </Note>
        )}
        {safeTx?.nonce_gap && (
          <Note tone="warn">
            {safeTx.demo_settlement ? (
              <>
                Demo settlement never broadcasts, so the Safe stays at nonce {safeTx.safe_nonce_onchain} and every payout must be signed
                at that nonce. This one was signed at nonce {safeTx.nonce}: rebuild it at nonce {safeTx.next_free_nonce} and sign again.
              </>
            ) : (
              <>
                This payout is at Safe nonce {safeTx.nonce}, but the Safe is at {safeTx.safe_nonce_onchain} and no other payout holds
                the nonces in between (an earlier one was cancelled). Rebuild it at nonce {safeTx.next_free_nonce} so it can go through;
                any signatures already collected are cleared.
              </>
            )}
            <div style={{ marginTop: 8 }}>
              <Button size="sm" variant="secondary" iconLeft={<RefreshCw size={13} />} loading={rebuilding} onClick={() => void rebuild()}>
                Rebuild at nonce {safeTx.next_free_nonce}
              </Button>
            </div>
          </Note>
        )}
        {!hasWallet() && <Note tone="warn">No browser wallet found. Each Safe owner needs MetaMask or Rabby to sign.</Note>}
        {account && <span className="cr-muted">Connected as <span className="cr-mono">{account}</span>{alreadySigned && remaining > 0 ? ' — already signed. Click Sign again with the next owner connected, or use another account.' : ''}</span>}
        <div className="cr-row">
          <Button variant="primary" size="lg" iconLeft={account ? <PenLine size={15} /> : <Wallet size={15} />} loading={busy}
            disabled={!canSign || !hasWallet() || remaining === 0} onClick={() => void sign()}>
            {remaining > 1 ? `Sign as all ${remaining} owners` : 'Sign and release'}
          </Button>
          {account && (
            <Button variant={alreadySigned ? 'secondary' : 'ghost'} onClick={() => void switchAccount()}
              title="Opens the wallet's account picker: choose the next Safe owner">
              Use another account
            </Button>
          )}
          <span className="cr-grow" />
          {canSign && (
            <Button variant="ghost" iconLeft={<Link2 size={14} />} onClick={() => void copyLink()}
              title="For an owner whose wallet is in another browser or on another device">
              Copy signing link
            </Button>
          )}
          {canSign && <Button variant="ghost" onClick={() => setCancelOpen(o => !o)}>{cancelOpen ? 'Keep payout' : 'Cancel payout'}</Button>}
        </div>
        {cancelOpen && canSign && (
          <div className="cr-form" style={{ alignItems: 'end' }}>
            <Input label="Why cancel this payout?" hint="Recorded in the audit log; nothing has been sent yet" value={cancelReason} onChange={e => setCancelReason(e.target.value)} />
            <div>
              <Button variant="danger" disabled={!cancelReason.trim()} loading={cancelling} onClick={() => void cancel()}>Cancel payout</Button>
            </div>
          </div>
        )}
        {err && <Note tone="danger">{err}</Note>}
        <Timeline status={status} />
        {status?.tx_hash && <span className="cr-row">Transaction <TxLink hash={status.tx_hash} href={status.explorer} simulated={status.simulated} /></span>}
      </div>
    </div>
  )
}

function CryptoReceipt({ status, onDone, onPayMore }: { status: CryptoStatus; onDone: () => void; onPayMore: () => void }) {
  const ok = status.status === 'SUCCESS'
  return (
    <div className="pay-card">
      <div className="cr-row">
        {ok ? <CheckCircle2 size={22} color="var(--brand-600)" aria-hidden="true" /> : <XCircle size={22} color="var(--danger-600)" aria-hidden="true" />}
        <h2 className="pay-card__title" style={{ margin: 0 }}>{ok ? 'Paid and reconciled' : 'Payout did not complete'}</h2>
      </div>
      <dl className="cr-kv" style={{ marginTop: 'var(--sp-3)' }}>
        <dt>Bill amount</dt><dd>{formatFiat(status.amount, status.currency)}</dd>
        <dt>Sent to vendor</dt><dd>{formatUsdc(status.net_units)}</dd>
        {status.withholding_units !== '0' && (<><dt>Withheld</dt><dd>{formatUsdc(status.withholding_units)}</dd></>)}
        <dt>Transaction</dt><dd><TxLink hash={status.tx_hash} href={status.explorer} simulated={status.simulated} /></dd>
        {status.block_number != null && (<><dt>Block</dt><dd>{status.block_number} · {status.confirmations} confirmations</dd></>)}
        {status.transfers.length > 0 && (
          <>
            <dt>Matched on-chain</dt>
            <dd className="cr-stack" style={{ gap: 2 }}>
              {status.transfers.map(t => (
                <span key={`${t.tx_hash}-${t.log_index}`} className="cr-row">
                  <Badge tone={t.status === 'MATCHED' ? 'success' : 'warn'}>{t.match_type ?? '—'}</Badge>
                  {formatUsdc(t.amount_units)} → <AddressLink address={t.to_address} href={explorerAddress(t.to_address)} />
                </span>
              ))}
            </dd>
          </>
        )}
        {!ok && (<><dt>Reason</dt><dd>{status.failure_reason ?? status.sub_status}</dd></>)}
      </dl>
      <div className="pay-actions" style={{ marginTop: 'var(--sp-4)' }}>
        <div className="pay-actions__right cr-row">
          {status.explorer && (
            <a href={status.explorer} target="_blank" rel="noreferrer" className="u-btn u-btn--secondary u-btn--md">
              View on explorer <ExternalLink size={13} aria-hidden="true" />
            </a>
          )}
          <Button onClick={onPayMore}>Pay another bill</Button>
          <Button variant="primary" onClick={onDone}>Done</Button>
        </div>
      </div>
    </div>
  )
}

export interface CryptoPayFlowProps {
  bills: PayBill[]
  initialInvoiceId?: string
  isAdmin: boolean
  onPaid: () => void
  onClose: () => void
  /** Open straight on this payout's signing step (from a signing link). */
  resumePayoutId?: string
  vendorName: string
  vendor: Vendor | null
  wallet: SupplierWallet | null
  treasury: TreasuryWallet | null
  railPolicy: RailPolicy | null
  /** The bills' currency (the tenant's base currency). */
  currency: string
  /** Re-read the treasury: a payout was prepared, settled or cancelled. */
  onTreasuryChange?: () => void
}

export default function CryptoPayFlow({
  bills, initialInvoiceId, isAdmin, onPaid, onClose, resumePayoutId,
  vendorName, vendor, wallet, treasury, railPolicy, currency, onTreasuryChange,
}: CryptoPayFlowProps) {
  const toast = useToast()
  const [resumeId, setResumeId] = useState<string | undefined>(resumePayoutId)
  const [step, setStep] = useState<CryptoStep>(resumePayoutId ? 'sign' : 'select')
  const [invoiceId, setInvoiceId] = useState(initialInvoiceId ?? bills[0]?.invoice_id ?? '')
  const bill = useMemo(() => bills.find(b => b.invoice_id === invoiceId), [bills, invoiceId])
  const [amount, setAmount] = useState(() => (bill ? String(bill.outstanding_amount) : ''))
  const [preview, setPreview] = useState<WithholdingPreview | null>(null)
  const [previewErr, setPreviewErr] = useState<{ codes: string[]; message: string } | null>(null)
  const [payout, setPayout] = useState<PayoutPreview | null>(null)
  const [refusal, setRefusal] = useState<{ codes: string[]; messages: string[] } | null>(null)
  const [preparing, setPreparing] = useState(false)
  const [otp, setOtp] = useState('')
  const [otpError, setOtpError] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [final, setFinal] = useState<CryptoStatus | null>(null)
  // Payouts for these bills already waiting on the Safe. Each new payout takes the
  // next Safe nonce and has to wait behind these, so they are offered first.
  const [pending, setPending] = useState<PendingUsdcPayout[]>([])
  useEffect(() => {
    if (step !== 'select') return
    const ids = new Set(bills.map(b => b.invoice_id))
    fetchPendingUsdcPayouts()
      .then(rows => setPending(rows.filter(r => ids.has(r.invoice_id))))
      .catch(() => setPending([]))
  }, [bills, step])

  useEffect(() => {
    if (bill) setAmount(String(bill.outstanding_amount))
  }, [bill])

  // Preparing reserves funds and settling spends them: keep the sidebar in step.
  useEffect(() => { onTreasuryChange?.() }, [step, onTreasuryChange])

  const amountNum = Number(amount)
  const amountErr = !amount ? 'Enter an amount' : Number.isNaN(amountNum) || amountNum <= 0 ? 'Enter a positive amount'
    : bill && amountNum - bill.outstanding_amount > 0.005 ? 'More than the outstanding amount' : null

  // Withholding and policy, shown before anything is created.
  useEffect(() => {
    if (!invoiceId || amountErr) { setPreview(null); return }
    let alive = true
    const t = setTimeout(() => {
      fetchWithholdingPreview(invoiceId, amountNum.toFixed(2))
        .then(p => { if (alive) { setPreview(p); setPreviewErr(null) } })
        .catch(e => { if (alive) { const i = apiError(e); setPreview(null); setPreviewErr({ codes: i.codes, message: i.message }) } })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [invoiceId, amountNum, amountErr])

  // What the page can check itself before the backend does (UsdcPayParts.preflight).
  const amountUsd = preview?.amount_usd != null ? Number(preview.amount_usd) : null
  const checks = useMemo(
    () => preflight({ preview, treasury, policy: railPolicy, wallet, amountUsd }),
    [preview, treasury, railPolicy, wallet, amountUsd],
  )
  const blocked = !!preview && checks.some(c => !c.ok)
  // The most the treasury can pay, in bill currency, at the preview's rate.
  const maxPayable = useMemo(() => {
    const avail = treasury?.available_units ?? treasury?.usdc_balance_units
    if (!preview?.gross_units || avail == null || !(amountNum > 0)) return null
    const perUnit = Number(preview.gross_units) / amountNum
    if (!(perUnit > 0)) return null
    return Math.floor((Number(avail) / perUnit) * 100) / 100
  }, [preview, treasury, amountNum])

  const prepare = async () => {
    setPreparing(true)
    setRefusal(null)
    try {
      const p = await createUsdcPayout(invoiceId, amountNum.toFixed(2))
      setPayout(p)
      if (!p.policy.allowed) {
        setRefusal({ codes: p.policy.codes, messages: p.policy.messages })
        return
      }
      setOtp('')
      setOtpError('')
      setStep('verify')
    } catch (e) {
      const info = apiError(e)
      if (info.codes.length) setRefusal({ codes: info.codes, messages: info.message ? [info.message] : [] })
      else toast.error('Could not prepare the payout', info.message)
    } finally {
      setPreparing(false)
    }
  }

  // The release OTP lives on the same fact_disbursements row as an IMPS payment,
  // so the existing resend route regenerates it.
  const resend = async () => {
    if (!payout) return
    try {
      const r = await resendDisbursementOtp(payout.disbursement_id)
      setPayout(p => (p ? { ...p, otp_sent_to: r.otp_sent_to, otp_expires_at: r.otp_expires_at ?? p.otp_expires_at } : p))
      setOtpError('')
      toast.success('New code sent', r.otp_sent_to ? `Sent to ${r.otp_sent_to}` : undefined)
    } catch (e) {
      toast.error('Could not resend the code', apiError(e).message)
    }
  }

  const verify = async (code: string) => {
    if (!payout) return
    setVerifying(true)
    setOtpError('')
    try {
      await verifyUsdcOtp(payout.disbursement_id, code)
      setStep('sign')
    } catch (e) {
      setOtpError(apiError(e).message)
    } finally {
      setVerifying(false)
    }
  }

  const onDone = useCallback((s: CryptoStatus) => {
    setFinal(s)
    setStep('receipt')
    if (s.status === 'SUCCESS') onPaid()
  }, [onPaid])

  const restart = () => {
    setStep('select')
    setPayout(null)
    setResumeId(undefined)
    setFinal(null)
    setRefusal(null)
  }

  if (step === 'receipt' && final) return <><UsdcStepper step="receipt" /><CryptoReceipt status={final} onDone={onClose} onPayMore={restart} /></>

  // A payout opened from a signing link has no preview in this browser: just the signing step.
  if (step === 'sign' && (payout || resumeId)) {
    return (
      <>
        <UsdcStepper step="sign" />
        {payout && <PreviewCard p={payout} />}
        <SafeSignStep disbursementId={payout?.disbursement_id ?? resumeId!} onDone={onDone} />
      </>
    )
  }

  if (!bills.length) {
    return <div className="pay-card"><Note>No outstanding bills for this vendor.</Note></div>
  }

  if (step === 'verify' && payout) {
    return (
      <>
        <UsdcStepper step="verify" />
        <PreviewCard p={payout} />
        <OtpStep
          sentTo={payout.otp_sent_to ?? ''}
          expiresAt={payout.otp_expires_at ? payout.otp_expires_at * 1000 : Date.now() + (payout.otp_ttl_seconds ?? 120) * 1000}
          demoOtp={payout.demo_otp ?? null}
          isBulk={false}
          otp={otp}
          setOtp={setOtp}
          error={otpError}
          submitting={verifying}
          onVerify={c => void verify(c)}
          onResend={() => void resend()}
          onRestart={restart}
        />
      </>
    )
  }

  const policy = preview?.policy
  return (
    <>
      <UsdcStepper step="select" />
      {pending.length > 0 && (
        <div className="pay-card">
          <h2 className="pay-card__title">Waiting for Safe signatures</h2>
          <Note tone="warn">
            Finish or cancel these first: the Safe runs payouts in order.
          </Note>
          <ul className="cr-stack" style={{ listStyle: 'none', padding: 0, margin: 'var(--sp-3) 0 0' }}>
            {pending.map((p, i) => (
              <li key={p.disbursement_id} className="cr-row cr-row--between">
                <span>
                  <strong>{p.invoice_number || p.invoice_id.slice(0, 8)}</strong> · {p.amount} {p.currency || ''} · nonce {p.nonce ?? '—'}
                  {' · '}{p.signatures} of {p.threshold ?? '?'} signatures{i === 0 ? ' · goes first' : ''}
                </span>
                <Button size="sm" variant={i === 0 ? 'primary' : 'ghost'}
                  onClick={() => { setResumeId(p.disbursement_id); setStep('sign') }}>
                  Open
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <PayeeCard name={vendorName} vendor={vendor} wallet={wallet} bills={bills} currency={currency} />

      <div className="pay-card">
        <h2 className="pay-card__title">
          What to pay
          <span className="up-count">{bills.length} open</span>
        </h2>
        <BillPicker bills={bills} value={invoiceId} onChange={setInvoiceId} currency={currency} />
        <div className="up-amount">
          <label className="up-amount__field">
            <span className="up-amount__cur">{currency}</span>
            <input className="up-amount__input" type="number" min={0} step="0.01" value={amount} aria-label={`Amount in ${currency}`}
              aria-invalid={!!amountErr || undefined} onChange={e => setAmount(e.target.value)} />
          </label>
          {bill && (
            <div className="up-chips" role="group" aria-label="Quick amounts">
              <button type="button" className="up-chip" aria-pressed={Math.abs(amountNum - bill.outstanding_amount) < 0.005}
                onClick={() => setAmount(String(bill.outstanding_amount))}>Full</button>
              <button type="button" className="up-chip" aria-pressed={Math.abs(amountNum - bill.outstanding_amount / 2) < 0.005}
                onClick={() => setAmount((Math.round(bill.outstanding_amount * 50) / 100).toFixed(2))}>Half</button>
              {maxPayable != null && maxPayable > 0 && maxPayable < bill.outstanding_amount && (
                <button type="button" className="up-chip" aria-pressed={Math.abs(amountNum - maxPayable) < 0.005}
                  title="The most the treasury can pay right now" onClick={() => setAmount(maxPayable.toFixed(2))}>
                  Max {formatFiat(maxPayable, currency)}
                </button>
              )}
            </div>
          )}
        </div>
        {amountErr && <div className="up-amount__err" role="alert">{amountErr}</div>}
      </div>

      {preview && (
        <PaymentSummary preview={preview} amount={amountNum} currency={currency} wallet={wallet} treasury={treasury}
          checks={checks} partialLeft={bill ? bill.outstanding_amount - amountNum : 0}
          maxPayable={maxPayable} onUseMax={maxPayable ? () => setAmount(maxPayable.toFixed(2)) : undefined} />
      )}
      {preview && (
        <>
          {policy && !policy.allowed && <RefusalList codes={policy.codes} messages={policy.messages} />}
          {preview.alternative && (
            <Note tone="warn">
              On the bank rail instead: <strong>{preview.alternative.rule_pack}</strong>
              {preview.alternative.rule_code ? ` (${preview.alternative.rule_code})` : ''} at {Number(preview.alternative.rate) * 100}% —{' '}
              {formatFiat(preview.alternative.amount, preview.alternative.currency)} withheld. {preview.alternative.note}
            </Note>
          )}
        </>
      )}
      {previewErr && <RefusalList codes={previewErr.codes} messages={previewErr.codes.length ? [] : [previewErr.message]} title="The USDC rail is not available for this bill" />}
      {refusal && <RefusalList codes={refusal.codes} messages={refusal.messages} />}
      {payout && !payout.policy.allowed && <PreviewCard p={payout} />}

      <div className="pay-actions">
        <PayFooterSummary net={preview?.net_units} vendor={vendorName} threshold={treasury?.safe_threshold} owners={treasury?.safe_owners.length} />
        <div className="pay-actions__right">
          <Button
            variant="primary"
            size="lg"
            iconLeft={<ShieldCheck size={15} />}
            iconRight={<ArrowRight size={15} />}
            loading={preparing}
            disabled={!isAdmin || !!amountErr || !bill || (policy ? !policy.allowed : false) || blocked}
            title={isAdmin ? undefined : 'Only an admin can release a payment'}
            onClick={() => void prepare()}
          >
            Prepare USDC payout
          </Button>
        </div>
      </div>
    </>
  )
}
