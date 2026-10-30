/**
 * The pieces of the USDC rail's first step (CryptoPayFlow), laid out the way a
 * payer reads a payment: who receives it, which bill it settles, and what will
 * happen to the money — with every pre-flight check visible before anything is
 * created, not discovered as a refusal afterwards.
 */

import { AlertTriangle, Check as CheckIcon, FileText, ShieldCheck, Wallet, XCircle } from 'lucide-react'
import { Badge, Button } from '../../ui'
import type { SupplierWallet, TreasuryWallet, WithholdingPreview } from '../../api/crypto'
import type { Vendor } from '../../api/invoices'
import { AddressLink, CopyButton } from '../../components/crypto/common'
import { explorerAddress, formatFiat, formatUnits, formatUsdc, unitsToBigInt } from '../../web3/units'
import { chainLabel, type Check } from './usdcPay'
import type { PayBill } from './usePaymentFlow'
import './usdc-pay.css'

// ── stepper ──────────────────────────────────────────────────────────────────

const STEPS = [
  { key: 'select', label: 'Bill & amount' },
  { key: 'verify', label: 'Release code' },
  { key: 'sign', label: 'Safe signatures' },
  { key: 'receipt', label: 'Settled' },
] as const

export function UsdcStepper({ step }: { step: (typeof STEPS)[number]['key'] }) {
  const at = STEPS.findIndex(s => s.key === step)
  return (
    <>
      <ol className="pay__steps" aria-label="Payment steps">
        {STEPS.map((s, i) => {
          const state = i < at ? 'done' : i === at ? 'current' : 'todo'
          return (
            <li key={s.key} className="pay__step-wrap" style={{ display: 'contents' }}>
              <span className={`pay__step${state === 'done' ? ' pay__step--done' : state === 'current' ? ' pay__step--current' : ''}`}
                aria-current={state === 'current' ? 'step' : undefined}>
                <span className="pay__step-num" aria-hidden="true">{i + 1}</span>
                {s.label}
              </span>
              {i < STEPS.length - 1 && <span className="pay__step-sep" aria-hidden="true" />}
            </li>
          )
        })}
      </ol>
      <div className="pay__steps-compact">Step {at + 1} of {STEPS.length} · {STEPS[at]?.label}</div>
    </>
  )
}

// ── payee ────────────────────────────────────────────────────────────────────

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]?.toUpperCase()).join('') || '?'
}

function date(d: string | null | undefined, year = false): string {
  if (!d) return '—'
  const t = new Date(d)
  return Number.isNaN(t.getTime()) ? d : t.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...(year ? { year: 'numeric' } : {}) })
}

/** A compact fact: green tick when it holds, amber when it does not. */
function Fact({ ok, children, title }: { ok: boolean; children: React.ReactNode; title?: string }) {
  return (
    <span className={`up-fact${ok ? '' : ' up-fact--warn'}`} title={title}>
      {ok ? <CheckIcon size={12} aria-hidden="true" /> : <AlertTriangle size={12} aria-hidden="true" />}
      {children}
    </span>
  )
}

export function PayeeCard({ name, vendor, wallet, bills, currency }: {
  name: string
  vendor: Vendor | null
  wallet: SupplierWallet | null
  bills: PayBill[]
  currency: string
}) {
  const v = vendor?.verification
  const docs = Object.keys(vendor?.tax_docs ?? {}).filter(k => vendor?.tax_docs?.[k])
  const outstanding = bills.reduce((s, b) => s + (b.outstanding_amount || 0), 0)
  const meta = [
    vendor?.country_code && `${vendor.country_code}${vendor.tax_residency && vendor.tax_residency !== vendor.country_code ? ` · tax ${vendor.tax_residency}` : ''}`,
    vendor?.payment_terms_days ? `Net ${vendor.payment_terms_days}` : null,
    vendor?.email,
  ].filter(Boolean)
  const screened = !!wallet?.screening_status && ['CLEAR', 'PASSED', 'OK'].includes(wallet.screening_status.toUpperCase())
  return (
    <div className="pay-card up-payee">
      <div className="up-payee__head">
        <span className="up-avatar" aria-hidden="true">{initials(name)}</span>
        <div className="up-payee__id">
          <div className="up-payee__name">
            {name}
            {v && <Badge tone={v.status === 'VERIFIED' ? 'success' : v.status === 'PENDING' ? 'warn' : 'neutral'}
              icon={v.status === 'VERIFIED' ? <ShieldCheck size={11} /> : undefined}>{v.label}</Badge>}
          </div>
          {meta.length > 0 && <div className="up-payee__meta">{meta.join(' · ')}</div>}
        </div>
        <div className="up-payee__due">
          <strong>{formatFiat(outstanding, currency)}</strong>
          <span>due · {bills.length} bill{bills.length === 1 ? '' : 's'}</span>
        </div>
      </div>

      <div className="up-payee__wallet">
        <Wallet size={14} aria-hidden="true" className="up-payee__wicon" />
        {wallet ? (
          <>
            <AddressLink address={wallet.address} href={wallet.explorer ?? explorerAddress(wallet.address)} />
            <span className="up-payee__net">{wallet.asset || 'USDC'} · {chainLabel(wallet.chain)}</span>
            <CopyButton text={wallet.address} />
          </>
        ) : <span className="cr-muted">No approved wallet</span>}
        <span className="up-payee__facts">
          <Fact ok={wallet?.status === 'ACTIVE'}>{wallet?.status === 'ACTIVE' ? 'Active' : (wallet?.status ?? 'No wallet').toLowerCase()}</Fact>
          <Fact ok={!!wallet?.verified_at} title="The vendor signed a message with this wallet">
            {wallet?.verified_at ? `Owner proved ${date(wallet.verified_at)}` : 'Ownership not proved'}
          </Fact>
          <Fact ok={screened}>{screened ? 'Screened' : 'Not screened'}</Fact>
          <Fact ok={docs.length > 0} title={docs.join(', ')}>
            <FileText size={11} aria-hidden="true" style={{ display: 'none' }} />
            {docs.length ? docs.join(', ') : 'No tax docs'}
          </Fact>
        </span>
      </div>
    </div>
  )
}

// ── bill picker ──────────────────────────────────────────────────────────────

function daysPast(due: string | null): number {
  if (!due) return 0
  const t = new Date(due).getTime()
  if (Number.isNaN(t)) return 0
  return Math.floor((Date.now() - t) / 86_400_000)
}

function daysLeftLabel(due: string | null): string {
  if (!due) return ''
  const t = new Date(due).getTime()
  if (Number.isNaN(t)) return ''
  const d = Math.ceil((t - Date.now()) / 86_400_000)
  return d === 0 ? 'due today' : d > 0 ? `due in ${d}d` : ''
}

export function BillPicker({ bills, value, onChange, currency }: {
  bills: PayBill[]
  value: string
  onChange: (invoiceId: string) => void
  currency: string
}) {
  return (
    <div className="up-bills" role="radiogroup" aria-label="Bill to pay">
      {bills.map(b => {
        const on = b.invoice_id === value
        const late = daysPast(b.due_date)
        const paid = Math.max(0, (b.total_amount || 0) - (b.outstanding_amount || 0))
        return (
          <button key={b.invoice_id} type="button" role="radio" aria-checked={on}
            className={`up-bill${on ? ' up-bill--on' : ''}`} onClick={() => onChange(b.invoice_id)}
            title={`Issued ${date(b.invoice_date, true)} · Due ${date(b.due_date, true)}`}>
            <span className="up-bill__radio" aria-hidden="true" />
            <span className="up-bill__no">{b.id || b.invoice_id.slice(0, 8)}</span>
            <span className="up-bill__meta">
              {late > 0 ? <Badge tone="danger">{late}d overdue</Badge> : <span>{daysLeftLabel(b.due_date) || `due ${date(b.due_date)}`}</span>}
              {paid > 0.005 && <Badge tone="info">{formatFiat(paid, currency)} paid</Badge>}
            </span>
            <span className="up-bill__amt">
              <strong>{formatFiat(b.outstanding_amount, currency)}</strong>
              {paid > 0.005 && <span className="up-bill__of">of {formatFiat(b.total_amount, currency)}</span>}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// ── summary and pre-flight checks ───────────────────────────────────────────

export function PaymentSummary({ preview, amount, currency, wallet, treasury, checks, partialLeft, maxPayable, onUseMax }: {
  preview: WithholdingPreview
  amount: number
  currency: string
  wallet: SupplierWallet | null
  treasury: TreasuryWallet | null
  checks: Check[]
  partialLeft: number
  /** The most the treasury can pay, in bill currency; offered when the balance falls short. */
  maxPayable?: number | null
  onUseMax?: () => void
}) {
  const demo = treasury?.settlement_mode === 'demo'
  const withheld = unitsToBigInt(preview.amount_units)
  const failed = checks.filter(c => !c.ok)
  const passed = checks.filter(c => c.ok)
  const rate = preview.token_usd && Number(preview.token_usd) !== 1 ? ` @ ${Number(preview.token_usd).toFixed(4)}` : ''
  return (
    <div className="pay-card">
      <div className="up-sum">
        <dl className="up-sum__rows">
          <div><dt>Bill amount</dt><dd>{formatFiat(amount, currency)}</dd></div>
          {preview.gross_units && <div><dt>In USDC{rate}</dt><dd>{formatUsdc(preview.gross_units)}</dd></div>}
          <div>
            <dt>
              Withholding{' '}
              <span className="up-sum__rule" title={`${preview.rule_pack}${preview.rule_code ? ` · ${preview.rule_code}` : ''}`}>
                {Number(preview.rate) * 100}% · {preview.rule_pack}
              </span>
            </dt>
            <dd>{withheld > 0n ? `−${formatUsdc(preview.amount_units)}` : '—'}</dd>
          </div>
          <div><dt>Network fee</dt><dd>{demo ? 'None (demo)' : 'Paid by relayer'}</dd></div>
        </dl>
        <div className="up-sum__net">
          <span className="up-sum__net-k">Vendor receives</span>
          <span className="up-sum__net-v">{formatUnits(preview.net_units)} <small>USDC</small></span>
          <span className="up-sum__net-to">to <AddressLink address={wallet?.address} href={wallet ? explorerAddress(wallet.address) : null} /></span>
          {partialLeft > 0.005 && <span className="up-sum__partial">Part payment · {formatFiat(partialLeft, currency)} left on the bill</span>}
        </div>
      </div>

      {failed.length > 0 && (
        <div className="up-block" role="alert">
          <XCircle size={16} aria-hidden="true" />
          <div className="up-block__body">
            {failed.map(c => (
              <div key={c.label}><strong>{c.label}</strong>{c.detail && <span> — {c.detail}</span>}</div>
            ))}
          </div>
          {maxPayable != null && maxPayable > 0 && onUseMax && failed.some(c => c.key === 'balance') && (
            <Button size="sm" variant="secondary" onClick={onUseMax}>Pay {formatFiat(maxPayable, currency)} instead</Button>
          )}
        </div>
      )}
      <div className="up-checks" aria-label="Pre-flight checks">
        {passed.map(c => (
          <span key={c.label} className={`up-check${c.warn ? ' up-check--warn' : ''}`} title={c.detail}>
            {c.warn ? <AlertTriangle size={12} aria-hidden="true" /> : <CheckIcon size={12} aria-hidden="true" />}
            {c.short ?? c.label}
          </span>
        ))}
      </div>
    </div>
  )
}

export function PayFooterSummary({ net, vendor, threshold, owners }: { net?: string | null; vendor: string; threshold?: number | null; owners?: number }) {
  return (
    <span className="up-hint">
      <span>{net ? <>Paying <strong>{formatUsdc(net)}</strong> to {vendor}</> : <>Choose a bill and amount</>}</span>
      <span className="up-hint__sub">Next: release code, then {threshold ?? 2} of {owners ?? threshold ?? 2} owners sign</span>
    </span>
  )
}
