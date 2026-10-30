/**
 * Step 1 — which bills, and how much of each.
 *
 * Each row now shows the billed total next to the outstanding, so a part-paid
 * bill is obvious before you decide the amount, and the amount field validates
 * against ₹1…outstanding at the field rather than losing the submission to a
 * server 400. Both `invoice_date` and `total_amount` were already in the
 * payment-profile response and previously unused.
 */

import { Checkbox, Input, EmptyState, Button, formatMoney, formatDate, useAmountField } from '../../ui'
import type { PayBill } from './usePaymentFlow'

export interface BillSelectStepProps {
  bills: PayBill[]
  picked: Set<string>
  amounts: Record<string, number>
  amountErrors: Record<string, string>
  total: number
  onToggle: (bill: PayBill) => void
  onAmount: (invoiceId: string, value: number) => void
  onSelectAll: () => void
  disabled?: boolean
}

export function BillSelectStep({
  bills, picked, amounts, amountErrors, total, onToggle, onAmount, onSelectAll, disabled,
}: BillSelectStepProps) {
  if (bills.length === 0) {
    return (
      <EmptyState
        title="Nothing outstanding"
        message="This vendor has no unpaid bills, so there is nothing to disburse."
      />
    )
  }

  const allPicked = picked.size === bills.length

  return (
    <div className="pay-card">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-3)' }}>
        <h2 className="pay-card__title" style={{ margin: 0 }}>
          Outstanding bills
        </h2>
        <Button variant="ghost" size="sm" onClick={onSelectAll} disabled={disabled}>
          {allPicked ? 'Clear all' : 'Select all'}
        </Button>
      </div>
      <p className="pay__sub" style={{ margin: '0 0 var(--sp-3)' }}>
        Selecting more than one pays them together under a single confirmation code.
      </p>

      <div className="pay-bills">
        {bills.map(b => {
          const sel = picked.has(b.invoice_id)
          const partly = b.total_amount > b.outstanding_amount
          const overdue = !!b.due_date && new Date(b.due_date) < new Date()
          return (
            <div key={b.invoice_id} className={`pay-bill${sel ? ' pay-bill--sel' : ''}`}>
              <div style={{ minWidth: 0 }}>
                <Checkbox
                  label={<span className="pay-bill__id">{b.id || b.invoice_id.slice(0, 8)}</span>}
                  checked={sel}
                  disabled={disabled}
                  onChange={() => onToggle(b)}
                />
                <div className="pay-bill__meta">
                  {b.invoice_date && <>Invoiced {formatDate(b.invoice_date)} · </>}
                  Due{' '}
                  <span className={overdue ? 'pay-bill__due--overdue' : undefined}>
                    {b.due_date ? formatDate(b.due_date) : '—'}
                    {overdue ? ' (overdue)' : ''}
                  </span>
                  {partly && <> · billed {formatMoney(b.total_amount)}</>}
                </div>
              </div>

              <div className="pay-bill__amt">
                {sel ? (
                  <BillAmount
                    value={amounts[b.invoice_id]}
                    error={amountErrors[b.invoice_id] ?? null}
                    hint={`Outstanding ${formatMoney(b.outstanding_amount, { precise: true })}`}
                    disabled={disabled}
                    onValue={v => onAmount(b.invoice_id, v)}
                  />
                ) : (
                  <div className="pay-bill__out">{formatMoney(b.outstanding_amount)}</div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {picked.size > 0 && (
        <div className="pay-total">
          <span>
            {picked.size} bill{picked.size > 1 ? 's' : ''}
          </span>
          <span>{formatMoney(total, { precise: true })}</span>
        </div>
      )}
    </div>
  )
}

function BillAmount({ value, onValue, error, hint, disabled }: {
  value: number
  onValue: (v: number) => void
  error: string | null
  hint: string
  disabled?: boolean
}) {
  const field = useAmountField(value, onValue)
  return <Input label="Amount to pay" error={error} hint={hint} disabled={disabled} {...field} />
}
