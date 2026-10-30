/**
 * TdsBreakdown — visual "how the TDS was computed" panel.
 *
 * Shows the same step-by-step calculation that pipelines/tds_processing.py performs:
 *   Bill amount (with GST) − GST component = Taxable base × TDS rate = TDS deducted,
 * then Net payable. Used both on the OCR review screen (live preview) and on the
 * bill detail (persisted values).
 */

export interface TdsBreakdownData {
  status: 'CALCULATED' | 'NA' | 'PENDING' | string | null
  section_code?: string | null
  rate?: number | null
  base_amount?: number | null
  tax_amount?: number | null
  total_amount?: number | null
  tds_amount?: number | null
  net_payable?: number | null
  pan_status?: string | null
  reason?: string | null
}

import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { PHONE_QUERY, useMediaQuery } from '../hooks/useMediaQuery'

const inr = (v: number | null | undefined) =>
  // Indian TDS is computed and remitted in rupees; shown only with the tds_india pack.
  v == null ? '—' : `₹${Math.round(v).toLocaleString('en-IN')}`

export default function TdsBreakdown({
  data,
  loading = false,
  title = 'TDS Breakdown',
  collapsible = false,
}: {
  data?: TdsBreakdownData | null
  loading?: boolean
  title?: string
  /** On a phone, the header folds the working away (shut by default), since
   *  the bill card there is one long column. Never on a laptop, and off on the
   *  review screen, which is where the computation is being checked. */
  collapsible?: boolean
}) {
  const isPhone = useMediaQuery(PHONE_QUERY)
  const folds = collapsible && isPhone
  const [isOpen, setOpen] = useState(false)
  const open = !folds || isOpen
  const status = (data?.status ?? '').toUpperCase()
  const total = data?.total_amount ?? null
  const tax = data?.tax_amount ?? null
  const base = data?.base_amount ?? null
  const rate = data?.rate ?? null
  const tds = data?.tds_amount ?? null
  const net = data?.net_payable ?? null
  const gstPct = total && tax && total - tax > 0 ? Math.round((tax / (total - tax)) * 100) : null
  const titleText = `${title}${(status === 'CALCULATED' || status === 'DEDUCTED') && data?.section_code ? ` · ${data.section_code}` : ''}`
  const titleStyle = { fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', fontFamily: 'inherit' } as const

  return (
    <div className="tds" style={{ background: '#fafafa', border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: open ? 10 : 0 }}>
        {folds ? (
          <button
            type="button"
            className="tds__toggle"
            aria-expanded={open}
            onClick={() => setOpen(o => !o)}
            style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, padding: 0, background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left', ...titleStyle }}
          >
            <span style={{ flex: 1, minWidth: 0 }}>{titleText}</span>
            <StatusPill status={status} loading={loading} />
            <ChevronDown size={14} aria-hidden="true" style={{ flexShrink: 0, color: '#737781', transform: open ? 'none' : 'rotate(-90deg)', transition: 'transform .15s' }} />
          </button>
        ) : (
          <>
            <div style={titleStyle}>{titleText}</div>
            <StatusPill status={status} loading={loading} />
          </>
        )}
      </div>

      {!open ? null : loading ? (
        <div style={{ fontSize: 12, color: '#9ca3af' }}>Calculating…</div>
      ) : (status === 'CALCULATED' || status === 'DEDUCTED') ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, fontVariantNumeric: 'tabular-nums' }}>
          <Row label="Bill Amount (with GST)" value={inr(total)} />
          {tax != null && tax > 0 && (
            <Row label={`GST component${gstPct ? ` (~${gstPct}%)` : ''}`} value={`− ${inr(tax)}`} muted op />
          )}
          <Row label="Taxable base" value={inr(base)} divider />
          <Row label={`× TDS rate${data?.section_code ? ` (${data.section_code})` : ''}`} value={`× ${rate != null ? `${rate}%` : '—'}`} muted />
          <Row label="TDS deducted" value={inr(tds)} bold divider accent="#dc2626" />
          <Row label="Net Payable" value={inr(net)} bold accent="#059669" />
          {data?.pan_status === 'MISSING_OR_INOPERATIVE' && (
            <div style={{ marginTop: 8, fontSize: 11, color: '#d97706' }}>
              Higher no-PAN rate applied (PAN missing or inoperative).
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 11, color: '#9ca3af' }}>
            TDS is applied on the taxable base only — not on the GST portion.
          </div>
        </div>
      ) : status === 'NA' ? (
        <div style={{ fontSize: 12, color: '#6b7280' }}>
          No TDS applicable.{data?.reason ? ` ${data.reason}` : ''}
        </div>
      ) : status === 'PENDING' ? (
        <div style={{ fontSize: 12, color: '#d97706' }}>
          {data?.reason || 'TDS will be calculated after the vendor category is assigned.'}
        </div>
      ) : status === 'REVIEW_REQUIRED' ? (
        <div style={{ fontSize: 12, color: '#dc2626' }}>
          {data?.reason || 'This bill is backdated beyond the allowed limit. An admin must manually configure TDS for this vendor.'}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: '#dc2626' }}>
          {data?.reason || 'TDS calculation unavailable.'}
        </div>
      )}
    </div>
  )
}

function Row({ label, value, muted, bold, op, divider, accent }: {
  label: string; value: string; muted?: boolean; bold?: boolean; op?: boolean; divider?: boolean; accent?: string
}) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12,
      padding: '5px 0',
      borderTop: divider ? '1px solid #e5e7eb' : undefined,
    }}>
      <span style={{ fontSize: 12, color: muted ? '#9ca3af' : '#374151', fontWeight: bold ? 700 : 500 }}>{label}</span>
      <span style={{
        fontSize: bold ? 14 : 13,
        fontWeight: bold ? 700 : 600,
        color: accent ?? (op ? '#9ca3af' : '#111827'),
      }}>{value}</span>
    </div>
  )
}

function StatusPill({ status, loading }: { status: string; loading: boolean }) {
  const meta: Record<string, { bg: string; color: string; label: string }> = {
    CALCULATED: { bg: '#d1fae5', color: '#059669', label: 'TDS' },
    DEDUCTED:   { bg: '#dbeafe', color: '#1d4ed8', label: 'Deducted' },
    NA:         { bg: '#f3f4f6', color: '#6b7280', label: 'No TDS' },
    PENDING:    { bg: '#fef3c7', color: '#d97706', label: 'Pending' },
  }
  const m = loading
    ? { bg: '#eef2ff', color: '#4f46e5', label: '…' }
    : meta[status] ?? { bg: '#fee2e2', color: '#dc2626', label: status || 'Error' }
  return (
    <span style={{ padding: '2px 8px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: m.bg, color: m.color }}>
      {m.label}
    </span>
  )
}
