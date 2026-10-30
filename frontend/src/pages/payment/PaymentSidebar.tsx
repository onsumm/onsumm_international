/**
 * The payment sidebar — everything true of the payment rather than of a step.
 *
 * Today that is one thing: which account the money leaves. It used to be a
 * full-width strip at the foot of the page, so on Verify and Receipt — the two
 * steps where money actually moves — the paying account was off screen.
 *
 * Built as a sectioned panel, not a card, because it is meant to grow: more
 * remitter accounts arrive as further `pay-side__acct` rows inside the same
 * section (the markup is already a list with a selected row), and anything else
 * that belongs to the whole payment arrives as another `<section>`. Nothing here
 * assumes a single account except the data — `DisbursementStats` returns one.
 */

import type { ReactNode } from 'react'
import { formatMoney, formatMoneyCompact, Badge } from '../../ui'
import type { DisbursalBank, DisbursementStats } from '../../api/invoices'

function Row({ label, value, title }: { label: string; value: ReactNode; title?: string }) {
  return (
    <div className="pay-side__row">
      <span className="pay-side__key">{label}</span>
      <span className="pay-side__val" title={title}>{value}</span>
    </div>
  )
}

export function PaymentSidebar({ stats, banks = [], bank, onBank, locked }: {
  stats: DisbursementStats | null
  /** Banks the payment can go through; with more than one, a switch is shown. */
  banks?: DisbursalBank[]
  bank?: string
  onBank?: (code: string) => void
  /** Once the OTP has gone out the bank is part of the payment: shown, not changeable. */
  locked?: boolean
}) {
  if (!stats) return null
  const current = bank ?? stats.bank_provider

  return (
    <aside className="pay__side" aria-label="Payment details">
      <section className="pay-side__sec">
        <h2 className="pay-side__head">
          Paying from
          {stats.demo && <Badge tone="warn">DEMO</Badge>}
        </h2>

        {banks.length > 1 && onBank && (
          <div className="pay-side__banks" role="radiogroup" aria-label="Pay through">
            {banks.map(b => (
              <button key={b.code} type="button" role="radio" aria-checked={current === b.code} disabled={locked}
                className={`pay-side__bank${current === b.code ? ' pay-side__bank--on' : ''}`}
                title={locked && current !== b.code ? 'The confirmation code was sent for this bank' : undefined}
                onClick={() => onBank(b.code)}>
                {b.label.replace(/ \(Demo\)$/, '').replace(/ Small Finance Bank$/, ' Bank')}
              </button>
            ))}
          </div>
        )}

        {/* The chosen bank's own remitter account. */}
        <ul className="pay-side__accts">
          <li className="pay-side__acct pay-side__acct--sel">
            <span className="pay-side__acct-bank">{stats.bank_name || stats.bank_provider}</span>
            <span className="pay-side__acct-num">···{stats.remitter_account_number.slice(-4)}</span>
            <span className="pay-side__acct-bal" title={formatMoney(stats.balance, { precise: true, currency: 'INR' })}>
              {formatMoney(stats.balance, { currency: 'INR' })}
            </span>
          </li>
        </ul>
      </section>

      <section className="pay-side__sec">
        <h2 className="pay-side__head">This account so far</h2>
        <div className="pay-side__rows">
          <Row label="Disbursed" value={formatMoneyCompact(stats.total_disbursed, { currency: 'INR' })} />
          <Row label="Payments" value={stats.total_transactions} />
        </div>
        <div className="pay-side__tally">
          <span><b className="pay-side__ok">{stats.successful_payments}</b> succeeded</span>
          <span><b className="pay-side__fail">{stats.failed_payments}</b> failed</span>
          <span><b className="pay-side__wait">{stats.pending_payments}</b> processing</span>
        </div>
      </section>
    </aside>
  )
}
