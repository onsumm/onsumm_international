/**
 * BillVerificationCard — tri-state Nova billing verification flag (§16.13 of
 * docs/PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md).
 *
 * One deterministic status flag, not a note — the opposite of the FX layer's
 * free-text advisory. Green / yellow / red, computed server-side by
 * pipelines/billing_reconciliation.py, glanceable, no reading required.
 * Read-only in Phase 1 — the human reads the flag and acts manually.
 */
import { displayLocale } from '../ui/format'

export interface VerificationData {
  verification_flag?: 'GREEN' | 'YELLOW' | 'RED' | null
  verification_detail?: string | null
  verified_at?: string | null
}

const CFG: Record<string, { dot: string; bg: string; border: string; label: string }> = {
  GREEN:  { dot: '#16a34a', bg: '#f0fdf4', border: '#bbf7d0', label: 'Verified' },
  YELLOW: { dot: '#d97706', bg: '#fffbeb', border: '#fde68a', label: 'Not yet verified' },
  RED:    { dot: '#dc2626', bg: '#fef2f2', border: '#fecaca', label: 'Discrepancy' },
}

export default function BillVerificationCard({ data }: { data?: VerificationData | null }) {
  const flag = data?.verification_flag
  if (!flag) return null  // not a Nova-vendor invoice — no card, nothing to show

  const cfg = CFG[flag]
  return (
    <div style={{ background: cfg.bg, border: `1px solid ${cfg.border}`, borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: data.verification_detail ? 4 : 0 }}>
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: cfg.dot, flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Verification
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color: cfg.dot, marginLeft: 'auto' }}>{cfg.label}</span>
      </div>
      {data.verification_detail && (
        <div style={{ fontSize: 13, color: '#374151', marginTop: 4 }}>{data.verification_detail}</div>
      )}
      {data.verified_at && (
        <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 6 }}>
          Checked {new Date(data.verified_at).toLocaleString(displayLocale(), { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
        </div>
      )}
    </div>
  )
}
