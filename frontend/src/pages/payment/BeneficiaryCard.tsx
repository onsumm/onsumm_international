/**
 * Who is about to be paid.
 *
 * This is the answer to the only question that matters before an irreversible
 * transfer, so it gets the account name, the bank, the masked account and the
 * IFSC together in one block. The old page showed the bank line inside a list of
 * badges and never showed `name_at_bank` at all — the field that tells you
 * whether the account belongs to the vendor you think you are paying.
 */

import { Landmark, ShieldCheck, ShieldAlert } from 'lucide-react'
import { Badge } from '../../ui'
import type { VendorPaymentProfile } from '../../api/invoices'
import type { OverrideDraft } from './usePaymentFlow'

export interface BeneficiaryCardProps {
  profile: VendorPaymentProfile
  /** Set when an admin is paying an account other than the vendor's verified one. */
  override?: OverrideDraft | null
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="pay-bene__row">
      <span className="pay-bene__key">{label}</span>
      <span className={`pay-bene__val${mono ? ' pay-bene__val--mono' : ''}`}>{value}</span>
    </div>
  )
}

export function BeneficiaryCard({ profile, override }: BeneficiaryCardProps) {
  const primary = profile.bank_accounts.find(b => b.is_primary) ?? profile.bank_accounts[0]

  if (override) {
    return (
      <div className="pay-bene__rows">
        <Row label="Paying" value={override.name} />
        <Row label="Account" value={override.account} mono />
        <Row label="IFSC" value={override.ifsc} mono />
        <Row label="Vendor" value={profile.vendor_name} />
      </div>
    )
  }

  return (
    <div className="pay-bene__rows">
      <Row label="Paying" value={primary?.name_at_bank || profile.vendor_name} />
      <Row
        label="Account"
        value={primary ? `${primary.bank_name || 'Bank'} · ${primary.account_number_masked}` : 'No bank account on file'}
        mono={!!primary}
      />
      {primary?.ifsc && <Row label="IFSC" value={primary.ifsc} mono />}
      <Row label="GSTIN" value={profile.primary_gstin || '—'} mono={!!profile.primary_gstin} />
      <div className="pay-bene__row">
        <span className="pay-bene__key">Verification</span>
        <span className="pay-bene__val" style={{ display: 'flex', gap: 'var(--sp-2)', flexWrap: 'wrap' }}>
          <Badge
            tone={profile.gstin_verified ? 'success' : 'danger'}
            icon={profile.gstin_verified ? <ShieldCheck size={11} /> : <ShieldAlert size={11} />}
          >
            {profile.gstin_verified ? 'GSTIN verified' : `GSTIN ${(profile.kyb_status || 'unverified').toLowerCase()}`}
          </Badge>
          <Badge
            tone={profile.bank_verified ? 'success' : 'danger'}
            icon={profile.bank_verified ? <ShieldCheck size={11} /> : <Landmark size={11} />}
          >
            {profile.bank_verified ? 'Bank verified' : 'Bank unverified'}
          </Badge>
        </span>
      </div>
    </div>
  )
}
