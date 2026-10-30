/**
 * Step 2 — the checkpoint before money moves.
 *
 * The old page had no equivalent: "Send OTP to confirm" fired straight from the
 * bill list, and an admin override — paying an account that is *not* the
 * vendor's verified one — needed nothing but filling four inputs and clicking
 * once. The integration plan called for a confirmation interstitial (§6.2); it
 * was never built. This is it, and for an override it asks the admin to type
 * PAY, because that path is both irreversible and permanently audited.
 */

import { useState } from 'react'
import { AlertTriangle, ShieldAlert } from 'lucide-react'
import { Badge, Button, ConfirmDialog, Input, formatMoney } from '../../ui'
import type { VendorPaymentProfile } from '../../api/invoices'
import { BeneficiaryCard } from './BeneficiaryCard'
import { isOverrideComplete, type OverrideDraft, type PayBill } from './usePaymentFlow'

export interface ReviewStepProps {
  profile: VendorPaymentProfile
  selected: PayBill[]
  amounts: Record<string, number>
  total: number
  isAdmin: boolean
  needsOverride: boolean
  useOverride: boolean
  setUseOverride: (v: boolean) => void
  override: OverrideDraft
  setOverride: (next: OverrideDraft) => void
  canSubmit: boolean
  submitting: boolean
  onSubmit: () => void
}

export function ReviewStep({
  profile, selected, amounts, total, isAdmin, needsOverride, useOverride, setUseOverride,
  override, setOverride, canSubmit, submitting, onSubmit,
}: ReviewStepProps) {
  const [confirming, setConfirming] = useState(false)
  const overriding = useOverride && isOverrideComplete(override)

  return (
    <>
      <div className="pay-card">
        <h2 className="pay-card__title">Review this payment</h2>
        <div className="pay-bene">
          <BeneficiaryCard profile={profile} override={overriding ? override : null} />

          <div>
            <div className="pay-bene__total">
              <div className="pay-label">Total to pay</div>
              <div className="pay-bene__amount">{formatMoney(total, { precise: true })}</div>
              <div className="pay-bene__count">
                across {selected.length} bill{selected.length > 1 ? 's' : ''}
              </div>
            </div>
            <div className="pay-warn">
              <AlertTriangle size={13} className="pay-warn__icon" aria-hidden="true" />
              <span>
                A confirmation code goes to the account owner’s email. Once you enter it the transfer is
                released and cannot be undone.
              </span>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 'var(--sp-4)' }}>
          <div className="pay-label">Bills being settled</div>
          {selected.map(b => (
            <div key={b.invoice_id} className="pay-hist">
              <span className="pay-bill__id">{b.id || b.invoice_id.slice(0, 8)}</span>
              <span>{b.due_date ? `due ${new Date(b.due_date).toLocaleDateString('en-IN')}` : 'no due date'}</span>
              <span className="pay-hist__amt">{formatMoney(amounts[b.invoice_id], { precise: true })}</span>
            </div>
          ))}
        </div>
      </div>

      {needsOverride && (
        <div className="pay-card">
          <h2 className="pay-card__title">
            <ShieldAlert size={15} aria-hidden="true" /> This vendor is not fully verified
          </h2>
          <p className="pay__sub" style={{ marginTop: 0 }}>
            {profile.gstin_verified ? '' : 'The GSTIN is not verified. '}
            {profile.bank_verified ? '' : 'No verified bank account is on file. '}
            Payment can still be released, but only as an admin override with the beneficiary details entered
            by hand and a reason recorded in the audit trail.
          </p>

          {!useOverride ? (
            <Button variant="secondary" size="sm" onClick={() => setUseOverride(true)} disabled={!isAdmin}>
              Enter beneficiary details as an override
            </Button>
          ) : (
            <div style={{ display: 'grid', gap: 'var(--sp-3)' }}>
              <Input
                label="Beneficiary account number"
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
                label="Beneficiary name"
                hint="As it appears on the bank account, not the invoice"
                value={override.name}
                autoComplete="off"
                onChange={e => setOverride({ ...override, name: e.target.value })}
              />
              <Input
                label="Reason for the override"
                required
                hint="Recorded permanently against every bill in this payment"
                value={override.reason}
                autoComplete="off"
                onChange={e => setOverride({ ...override, reason: e.target.value })}
              />
              <div>
                <Button variant="ghost" size="sm" onClick={() => setUseOverride(false)}>
                  Cancel override
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="pay-actions">
        {overriding && <Badge tone="warn" icon={<ShieldAlert size={11} />}>Override payment</Badge>}
        <div className="pay-actions__right">
          <Button
            variant="success"
            size="lg"
            disabled={!canSubmit}
            loading={submitting}
            title={canSubmit ? undefined : 'Select at least one valid amount, and complete the override if one is required'}
            onClick={() => setConfirming(true)}
          >
            Send confirmation code
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={confirming}
        onCancel={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false)
          onSubmit()
        }}
        tone={overriding ? 'danger' : 'primary'}
        title={overriding ? 'Release an override payment?' : 'Send the confirmation code?'}
        description={
          overriding
            ? `This pays an account entered by hand, not ${profile.vendor_name}’s verified bank account. The override and your reason are recorded permanently.`
            : `A code will be emailed to release ${formatMoney(total, { precise: true })} to ${profile.vendor_name}. The transfer cannot be reversed once confirmed.`
        }
        confirmLabel={overriding ? 'Override and send code' : 'Send code'}
        requireTyped={overriding ? 'PAY' : undefined}
        impact={[
          { label: 'Beneficiary', value: overriding ? override.name : profile.vendor_name },
          {
            label: 'Account',
            value: overriding
              ? override.account
              : (profile.bank_accounts.find(b => b.is_primary) ?? profile.bank_accounts[0])?.account_number_masked ?? '—',
          },
          { label: 'Total', value: formatMoney(total, { precise: true }) },
        ]}
        items={selected.map(b => `${b.id || b.invoice_id.slice(0, 8)} — ${formatMoney(amounts[b.invoice_id], { precise: true })}`)}
      />
    </>
  )
}
