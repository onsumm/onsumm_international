import { useState, useEffect, useCallback, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import {
  getDashboardSummary, getReconciliation,
  type DashboardSummary, type ReconcileResult, type RollupStatus,
  type CertificationStatus, type RateStatus, type MatchStatus, type QtyStatus,
} from '../api/novaReconciliation'

// Nova reconciliation view, backed by the standalone Nova Reconciliation Service
// (services/nova_reconciliation/): certification (is each line item allowed to be
// billed against this entity/cost centre, per lendix/nova_vendor_mapping.json),
// invoice-vs-Nova matching, and Nova rate/self-consistency checks.
// Design: docs/NOVA_RECONCILIATION_LOGIC.md. Contract: docs/nova-reconciliation-service.openapi.yaml.

const ROLLUP_COLOR: Record<RollupStatus, string> = { RED: '#dc2626', YELLOW: '#d97706', GREEN: '#16a34a' }
const ROLLUP_BG: Record<RollupStatus, string> = { RED: '#fef2f2', YELLOW: '#fffbeb', GREEN: '#f0fdf4' }

// Cost-centre attribution (NOVA_RECONCILIATION_LOGIC §4.4) — only shown when the line
// is NOT plain OWN, since "this bill's own cost centre" is the unremarkable default.
const ATTRIBUTION_LABEL: Record<string, string> = {
  SIBLING_OWNED: 'OTHER COST CENTRE',
  SHARED: 'SHARED',
  UNATTRIBUTED: 'UNATTRIBUTED',
  OWN: 'OWN',
}
const ATTRIBUTION_COLOR: Record<string, string> = {
  SIBLING_OWNED: '#7c3aed',  // attributed away from this bill — informational, not a failure
  SHARED: '#0891b2',
  UNATTRIBUTED: '#dc2626',
  OWN: '#16a34a',
}

const CERT_COLOR: Record<CertificationStatus, string> = {
  CERTIFIED: '#16a34a', UNCERTIFIED: '#dc2626', AMBIGUOUS: '#d97706',
  UNRESOLVED_MODULE: '#64748b', PENDING_EXTENDED_LOOKUP: '#d97706', NOVA_LOGIC_MISSING: '#64748b',
}
const RATE_COLOR: Record<NonNullable<RateStatus>, string> = {
  RATE_OK: '#16a34a', RATE_MISMATCH: '#dc2626', ZERO_VOLUME: '#64748b', NOT_AUTO_COMPARABLE: '#64748b',
}
const MATCH_COLOR: Record<NonNullable<MatchStatus>, string> = {
  MATCHED: '#16a34a', MISSING_IN_INVOICE: '#d97706', EXTRA_IN_INVOICE: '#dc2626',
}
const QTY_COLOR: Record<NonNullable<QtyStatus>, string> = { QTY_OK: '#16a34a', QTY_MISMATCH: '#dc2626' }

const CERT_REASON: Partial<Record<CertificationStatus, string>> = {
  UNCERTIFIED: 'not in this cost centre’s certified rate card',
  NOVA_LOGIC_MISSING: 'rate card lists this service, but its Nova module mapping isn’t confirmed yet',
  AMBIGUOUS: 'certified in more than one scope — needs manual resolution',
  UNRESOLVED_MODULE: 'couldn’t match this line to any known service',
  PENDING_EXTENDED_LOOKUP: 'waiting on a second Nova pull for the owning cost centre',
}
const certReason = (status: CertificationStatus) => CERT_REASON[status]

// Nova billing data is an Indian lender's usage billing, always in rupees.
const inr = (n: number | null) => n == null ? '—' : `₹${n.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
const pct = (n: number | null) => n == null ? '—' : `${n}%`
const monthLabel = (ym: string) => {
  const [y, m] = ym.split('-')
  return new Date(Number(y), Number(m) - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
}

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{ fontSize: 10, fontWeight: 800, color: '#fff', background: color, padding: '4px 8px', borderRadius: 999, textTransform: 'uppercase', letterSpacing: '0.02em', whiteSpace: 'nowrap' }}>
      {label.replace(/_/g, ' ')}
    </span>
  )
}

export default function NovaOwnershipPanel() {
  const { user } = useAuth()
  const ownerTenantId = user?.company_id ?? ''

  // Unfiltered fetch — the source of truth for which vendors/months actually have
  // data, so the filters below only ever offer choices that produce a result.
  const [allSummary, setAllSummary] = useState<DashboardSummary | null>(null)
  const [vendor, setVendor] = useState('')
  const [period, setPeriod] = useState('')
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [selectedBillId, setSelectedBillId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ReconcileResult | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (!ownerTenantId) return
    getDashboardSummary(ownerTenantId).then(setAllSummary).catch(() => { /* filters just stay empty */ })
  }, [ownerTenantId])

  const vendors = useMemo(
    () => Array.from(new Set((allSummary?.by_vendor ?? []).map(r => r.vendor))).sort(),
    [allSummary],
  )
  const months = useMemo(() => {
    const rows = vendor ? (allSummary?.by_vendor ?? []).filter(r => r.vendor === vendor) : (allSummary?.by_vendor ?? [])
    return Array.from(new Set(rows.map(r => r.month))).sort().reverse()
  }, [allSummary, vendor])

  const loadSummary = useCallback(() => {
    if (!ownerTenantId) return
    setLoading(true)
    setError(null)
    getDashboardSummary(ownerTenantId, { vendor: vendor || undefined, period: period || undefined })
      .then(setSummary)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load Nova ownership summary'))
      .finally(() => setLoading(false))
  }, [ownerTenantId, vendor, period])

  useEffect(() => { loadSummary() }, [loadSummary])

  useEffect(() => {
    if (!selectedBillId) { setDetail(null); return }
    setDetailLoading(true)
    getReconciliation(selectedBillId)
      .then(setDetail)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load reconciliation detail'))
      .finally(() => setDetailLoading(false))
  }, [selectedBillId])

  if (!ownerTenantId) {
    return <div style={{ padding: 24, color: '#94a3b8' }}>Sign in to view ownership certification.</div>
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', padding: '14px 16px', borderRadius: 14, border: '1px solid #e5e7eb', background: '#f9fafb', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: '#475467', fontWeight: 600 }}>Vendor:</label>
        <select
          value={vendor}
          onChange={e => { setVendor(e.target.value); setPeriod(''); setSelectedBillId(null) }}
          style={{ padding: '8px 12px', borderRadius: 10, border: '1px solid #dfe3ea', fontSize: 13, color: '#0f172a', background: '#fff', minWidth: 160 }}
        >
          <option value="">All vendors</option>
          {vendors.map(v => <option key={v} value={v}>{v}</option>)}
        </select>

        <label style={{ fontSize: 13, color: '#475467', fontWeight: 600 }}>Service month:</label>
        <select
          value={period}
          onChange={e => { setPeriod(e.target.value); setSelectedBillId(null) }}
          disabled={months.length === 0}
          style={{ padding: '8px 12px', borderRadius: 10, border: '1px solid #dfe3ea', fontSize: 13, color: '#0f172a', background: '#fff', minWidth: 160 }}
        >
          <option value="">{months.length === 0 ? 'No data yet' : 'Latest per vendor'}</option>
          {months.map(m => <option key={m} value={m}>{monthLabel(m)}</option>)}
        </select>

        {(vendor || period) && (
          <button onClick={() => { setVendor(''); setPeriod(''); setSelectedBillId(null) }} style={{ background: 'none', border: 'none', color: '#2563eb', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
            clear filters
          </button>
        )}
      </div>

      {error && <div style={{ padding: '12px 14px', marginBottom: 16, background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 12, color: '#991b1b', fontSize: 13, fontWeight: 600 }}>{error}</div>}

      {loading && <div style={{ padding: 40, textAlign: 'center', color: '#94a3b8' }}><Loader2 size={20} /> Loading…</div>}

      {!loading && summary && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 12, marginBottom: 12 }}>
            <Tile label="Certified" value={String(summary.totals.certified)} color="#16a34a" />
            <Tile label="Uncertified" value={String(summary.totals.uncertified)} color={summary.totals.uncertified ? '#dc2626' : undefined} />
            <Tile label="Ambiguous" value={String(summary.totals.ambiguous)} color={summary.totals.ambiguous ? '#d97706' : undefined} />
            <Tile label="Unresolved module" value={String(summary.totals.unresolved_module)} color={summary.totals.unresolved_module ? '#d97706' : undefined} />
            <Tile label="Pending lookup" value={String(summary.totals.pending_extended_lookup)} color={summary.totals.pending_extended_lookup ? '#d97706' : undefined} />
            <Tile label="Nova logic missing" value={String(summary.totals.nova_logic_missing)} color={summary.totals.nova_logic_missing ? '#64748b' : undefined} />
            <Tile label="Rate mismatches" value={String(summary.totals.rate_mismatch)} color={summary.totals.rate_mismatch ? '#dc2626' : undefined} />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: 12, marginBottom: 20 }}>
            <Tile label="Qty mismatches (2% tol.)" value={String(summary.totals.qty_mismatch)} color={summary.totals.qty_mismatch ? '#dc2626' : undefined} />
            <Tile label="Missing in invoice" value={String(summary.totals.missing_in_invoice)} color={summary.totals.missing_in_invoice ? '#d97706' : undefined} />
            <Tile label="Extra in invoice" value={String(summary.totals.extra_in_invoice)} color={summary.totals.extra_in_invoice ? '#dc2626' : undefined} />
            <Tile label="Nova self-inconsistent" value={String(summary.totals.self_inconsistent)} color={summary.totals.self_inconsistent ? '#dc2626' : undefined} />
            <Tile label="Variance" value={inr(summary.totals.variance_amount_rupees)} color={summary.totals.variance_amount_rupees ? '#dc2626' : '#16a34a'} />
          </div>

          {summary.by_vendor.length === 0 && (
            <div style={{ padding: 24, textAlign: 'center', color: '#94a3b8', border: '1px solid #e2e8f0', borderRadius: 8 }}>
              {vendor || period
                ? 'No runs match this filter — try clearing it.'
                : 'No ownership reconciliation runs yet for this tenant — this populates once a bill has been posted to POST /v1/reconcile.'}
            </div>
          )}

          {summary.by_vendor.length > 0 && (
            <div style={{ border: '1px solid #e2e8f0', borderRadius: 14, overflow: 'hidden', background: '#fff', marginBottom: 20 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', textAlign: 'left' }}>
                    <th style={th}></th>
                    <th style={th}>Vendor</th>
                    <th style={th}>Service month</th>
                    <th style={th}>Bill</th>
                    <th style={th}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.by_vendor.map(row => (
                    <tr
                      key={row.run_id}
                      onClick={() => setSelectedBillId(row.bill_id)}
                      style={{
                        borderBottom: '1px solid #f1f5f9', cursor: 'pointer',
                        background: selectedBillId === row.bill_id ? '#f8fafc' : '#fff',
                      }}
                    >
                      <td style={td}><span style={{ width: 8, height: 8, borderRadius: '50%', display: 'inline-block', background: ROLLUP_COLOR[row.rollup_status] }} /></td>
                      <td style={{ ...td, color: '#0f172a', fontWeight: 600 }}>{row.vendor}</td>
                      <td style={{ ...td, color: '#475467' }}>{monthLabel(row.month)}</td>
                      <td style={{ ...td, color: '#475467', fontFamily: 'monospace', fontSize: 11 }}>{row.bill_id}</td>
                      <td style={td}><Badge label={row.rollup_status} color={ROLLUP_COLOR[row.rollup_status]} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {detailLoading && <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8' }}><Loader2 size={18} /> Loading lines…</div>}

          {!detailLoading && detail && (() => {
            // Nova/Invoice totals in this strip are scoped to services actually
            // present on this bill (certified + MATCHED) — not Nova's full-month
            // total across every module it billed, most of which this document
            // never claimed. detail.run.total_cost_rupees is that full-month
            // figure; it stays in the run record for the dashboard rollup, but
            // isn't what belongs in a per-bill "what does this invoice's Nova
            // usage actually cost" comparison.
            const matched = detail.lines.filter(l => l.certification_status === 'CERTIFIED' && l.match_status === 'MATCHED')
            const novaTotal = matched.reduce((sum, l) => sum + (l.total_cost_rupees ?? 0), 0)
            const invoiceTotal = matched.reduce((sum, l) => sum + (l.invoice_amount ?? 0), 0)
            const variance = Math.round((invoiceTotal - novaTotal) * 100) / 100
            return (
            <div style={{ border: '1px solid #e2e8f0', borderRadius: 14, overflow: 'hidden', background: '#fff' }}>
              <div style={{ padding: '10px 14px', borderBottom: '1px solid #e2e8f0', fontSize: 12, color: '#475467', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                <span><strong style={{ color: '#0f172a' }}>{detail.run.bill_id}</strong></span>
                <span>Nova total (services on this bill): <strong>{inr(novaTotal)}</strong></span>
                <span>Invoice total (same services): <strong>{inr(invoiceTotal)}</strong></span>
                <span>Variance: <strong style={{ color: variance ? '#dc2626' : '#16a34a' }}>{inr(variance)}</strong></span>
              </div>
              <div style={{ padding: '6px 14px', fontSize: 11, color: '#94a3b8', borderBottom: '1px solid #f1f5f9' }}>
                Every line item on this bill, whatever its outcome — nothing on the invoice is hidden. Rows with no invoice line (Nova has usage this bill never billed) aren't shown here; they're counted in Missing in invoice above.
              </div>
              <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 1050 }}>
                <thead>
                  <tr style={{ background: '#f8fafc', textAlign: 'left' }}>
                    <th style={th}>Line item</th>
                    <th style={th}>Nova module</th>
                    <th style={th}>Canonical service</th>
                    <th style={th}>Cost centre</th>
                    <th style={th}>Certification</th>
                    <th style={th}>Match</th>
                    <th style={th}>Rate</th>
                    <th style={th}>Qty (2% tol.)</th>
                    <th style={th}>Bill value</th>
                    <th style={th}>Nova value</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.lines.filter(l => l.invoice_description != null).map(l => (
                    <tr key={l.line_id} style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ ...td, color: '#475467' }}>{l.invoice_description ?? '—'}</td>
                      <td style={{ ...td, color: '#0f172a', fontWeight: 600 }}>{l.module_slug ?? '—'}</td>
                      <td style={{ ...td, color: '#475467' }}>{l.canonical_service ?? <span style={{ color: '#dc2626' }}>unresolved</span>}</td>
                      <td style={{ ...td, color: '#475467' }}>
                        {l.cost_centre ?? '—'}
                        {l.cost_centre_attribution && l.cost_centre_attribution !== 'OWN' && (
                          <div style={{ marginTop: 2 }}>
                            <Badge label={ATTRIBUTION_LABEL[l.cost_centre_attribution]} color={ATTRIBUTION_COLOR[l.cost_centre_attribution]} />
                          </div>
                        )}
                      </td>
                      <td style={td}>
                        <Badge label={l.certification_status} color={CERT_COLOR[l.certification_status]} />
                        {certReason(l.certification_status) && (
                          <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2, maxWidth: 180 }}>{certReason(l.certification_status)}</div>
                        )}
                      </td>
                      <td style={td}>{l.match_status && <Badge label={l.match_status} color={MATCH_COLOR[l.match_status]} />}</td>
                      <td style={td}>
                        {l.rate_status && <Badge label={l.rate_status} color={RATE_COLOR[l.rate_status]} />}
                        {l.rate_status === 'RATE_MISMATCH' && <div style={{ fontSize: 10, color: '#dc2626', marginTop: 2 }}>{pct(l.rate_variance_pct)}</div>}
                      </td>
                      <td style={td}>
                        {l.qty_status && <Badge label={l.qty_status} color={QTY_COLOR[l.qty_status]} />}
                        {l.qty_status === 'QTY_MISMATCH' && <div style={{ fontSize: 10, color: '#dc2626', marginTop: 2 }}>{pct(l.qty_variance_pct)}</div>}
                        {l.combined_brand_check && <div style={{ fontSize: 10, color: '#64748b', marginTop: 2, maxWidth: 200 }}>{l.combined_brand_check}</div>}
                      </td>
                      <td style={{ ...td, color: '#475467' }}>{l.invoice_amount != null ? inr(l.invoice_amount) : '—'}</td>
                      <td style={{ ...td, color: '#475467' }}>{l.total_cost_rupees != null ? inr(l.total_cost_rupees) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </div>
            )
          })()}
        </>
      )}
    </div>
  )
}

function Tile({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ border: '1px solid #e2e8f0', borderRadius: 14, padding: '14px 16px', background: '#fff', boxShadow: '0 4px 12px rgba(15,23,42,0.03)' }}>
      <div style={{ fontSize: 10.5, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 800, color: color ?? '#0f172a', marginTop: 6, letterSpacing: '-0.02em' }}>{value}</div>
    </div>
  )
}

const th: React.CSSProperties = { padding: '10px 12px', fontWeight: 700, color: '#475467', borderBottom: '1px solid #e2e8f0', letterSpacing: '0.02em', fontSize: 10.5, textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '10px 12px', fontSize: 12, verticalAlign: 'middle' }
