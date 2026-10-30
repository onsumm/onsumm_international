// Raw Nova API explorer — NOVA_RECONCILIATION_LOGIC.md §2.2.
//
// Shows exactly what Nova returned for one (brand, vendor, month), with no
// reconciliation applied. The point is to separate "what did Nova say?" from "what did
// we conclude?": when a bill looks wrong, that's the first fork to resolve, and it
// otherwise means reading service logs.
//
// Brand is a real input, not a filter. Each cost centre holds its own Nova API key
// (§0.5), so a different brand is a different key and therefore a different ledger —
// not a subset of one response.
import { useEffect, useState } from 'react'
import { RefreshCw, AlertCircle, Search } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import {
  getNovaOptions, getNovaRaw,
  type NovaOptions, type NovaRawResponse, type NovaRawModule,
} from '../api/novaReconciliation'

const CERT_COLOR: Record<string, string> = {
  CERTIFIED: '#16a34a',
  UNCERTIFIED: '#dc2626',
  NOVA_LOGIC_MISSING: '#64748b',
  UNRESOLVED_MODULE: '#d97706',
  AMBIGUOUS: '#dc2626',
  PENDING_EXTENDED_LOOKUP: '#d97706',
}

const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 700, color: '#64748b',
  textTransform: 'uppercase', letterSpacing: '.04em', borderBottom: '1px solid #e2e8f0', whiteSpace: 'nowrap',
}
const td: React.CSSProperties = { padding: '8px 10px', fontSize: 12, whiteSpace: 'nowrap' }
const num: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

const inr = (n: number) =>
  // Nova billing data is an Indian lender's usage billing, always in rupees.
  `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
// Nova returns floats like 0.7899999999999999 — that's binary representation, not a real
// rate difference, so it's rounded for display rather than shown raw.
const rate = (n: number | null) => (n == null ? '—' : `₹${Number(n.toFixed(4))}`)
const int = (n: number) => n.toLocaleString('en-IN')

function currentMonth(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function NovaRawExplorer() {
  const { user } = useAuth()
  const tenantId = user?.company_id ?? ''

  const [options, setOptions] = useState<NovaOptions | null>(null)
  const [brand, setBrand] = useState('')
  const [vendor, setVendor] = useState('')
  const [month, setMonth] = useState(currentMonth())

  const [data, setData] = useState<NovaRawResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (!tenantId) return
    getNovaOptions(tenantId)
      .then(o => {
        setOptions(o)
        // Preselect the only sensible defaults rather than making the user pick twice
        // before anything can be fetched.
        setBrand(b => b || o.brands[0] || '')
        setVendor(v => v || o.vendors[0] || '')
      })
      .catch(e => setErr(e?.message || 'Could not load brand/vendor options'))
  }, [tenantId])

  const fetchRaw = () => {
    if (!tenantId || !vendor || !month) return
    setLoading(true); setErr(''); setData(null)
    getNovaRaw(tenantId, vendor, month, brand || undefined)
      .then(setData)
      .catch(e => setErr(e?.message || 'Nova call failed'))
      .finally(() => setLoading(false))
  }

  const card: React.CSSProperties = {
    background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12, overflow: 'hidden',
  }
  const sel: React.CSSProperties = {
    padding: '7px 10px', fontSize: 13, borderRadius: 8, border: '1px solid #cbd5e1',
    background: '#fff', color: '#0f172a', minWidth: 150,
  }

  return (
    <div style={{ ...card, marginTop: 16 }}>
      <div style={{ padding: '12px 16px', borderBottom: '1px solid #f1f5f9' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: '#0f172a' }}>Raw Nova API</div>
        <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
          Exactly what <code>GET /billing/monthly</code> returned — no reconciliation applied.
          Each brand uses its own API key, so changing brand queries a different ledger.
        </div>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end', padding: '12px 16px',
                    borderBottom: '1px solid #f1f5f9', background: '#f8fafc' }}>
        <label style={{ fontSize: 11, fontWeight: 600, color: '#64748b' }}>
          <div style={{ marginBottom: 4 }}>BRAND / COST CENTRE</div>
          <select style={sel} value={brand} onChange={e => setBrand(e.target.value)}>
            {(options?.brands ?? []).map(b => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, fontWeight: 600, color: '#64748b' }}>
          <div style={{ marginBottom: 4 }}>VENDOR</div>
          <select style={sel} value={vendor} onChange={e => setVendor(e.target.value)}>
            {(options?.vendors ?? []).map(v => <option key={v} value={v}>{v}</option>)}
          </select>
        </label>
        <label style={{ fontSize: 11, fontWeight: 600, color: '#64748b' }}>
          <div style={{ marginBottom: 4 }}>MONTH</div>
          <input type="month" style={sel} value={month} onChange={e => setMonth(e.target.value)} />
        </label>
        <button
          onClick={fetchRaw}
          disabled={loading || !vendor || !month}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 14px', fontSize: 13,
                   fontWeight: 600, borderRadius: 8, border: '1px solid #4338ca', background: '#4f46e5',
                   color: '#fff', cursor: loading ? 'default' : 'pointer', opacity: loading ? 0.6 : 1 }}>
          {loading ? <RefreshCw size={13} /> : <Search size={13} />} {loading ? 'Calling Nova…' : 'Fetch'}
        </button>
        {options?.registered_entity && (
          <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 'auto' }}>
            {options.registered_entity} · {options.entity_type}
          </span>
        )}
      </div>

      {err && (
        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '12px 16px',
                      color: '#b91c1c', fontSize: 13, background: '#fef2f2' }}>
          <AlertCircle size={14} style={{ marginTop: 2, flexShrink: 0 }} /> {err}
        </div>
      )}

      {data && (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, padding: '10px 16px', fontSize: 12,
                        color: '#475467', borderBottom: '1px solid #f1f5f9' }}>
            <span>Modules <b>{data.modules.length}</b></span>
            <span>Billable ops <b>{int(data.totals?.totalBillableOperations ?? 0)}</b></span>
            <span>Total <b>{inr(data.totals?.totalCostRupees ?? 0)}</b></span>
            {data.timestamp && <span style={{ color: '#94a3b8' }}>as of {new Date(data.timestamp).toLocaleString()}</span>}
            {data.request.key_scope === 'tenant-default' && (
              // Not this brand's own ledger — say so rather than let the brand dropdown imply it.
              <span style={{ color: '#b45309', fontWeight: 600 }}>
                using the tenant-wide default key, not {data.request.brand}'s own
              </span>
            )}
            {data.reference_id && (
              <span style={{ color: '#94a3b8', fontFamily: 'ui-monospace, monospace' }}>
                ref {data.reference_id.slice(0, 8)}
              </span>
            )}
          </div>

          {/* Wide table: scrolls inside its own container so the page never scrolls sideways. */}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980 }}>
              <thead>
                <tr>
                  <th style={th}>Module</th>
                  <th style={th}>Canonical service</th>
                  <th style={th}>Certification</th>
                  <th style={{ ...th, textAlign: 'right' }}>API calls</th>
                  <th style={{ ...th, textAlign: 'right' }}>Billable ops</th>
                  <th style={{ ...th, textAlign: 'right' }}>Nova rate</th>
                  <th style={{ ...th, textAlign: 'right' }}>Certified rate</th>
                  <th style={{ ...th, textAlign: 'right' }}>Rate var</th>
                  <th style={{ ...th, textAlign: 'right' }}>Total cost</th>
                  <th style={th}>Nova self-check</th>
                </tr>
              </thead>
              <tbody>
                {data.modules.map((m: NovaRawModule) => {
                  const over = m.rate_variance_pct != null && m.rate_variance_pct > m.tolerance_pct
                  return (
                    <tr key={m.module} style={{ borderBottom: '1px solid #f8fafc' }}>
                      <td style={{ ...td, fontFamily: 'ui-monospace, monospace', color: '#0f172a', fontWeight: 600 }}>{m.module}</td>
                      <td style={{ ...td, color: '#475467' }}>
                        {m.canonical_service ?? <span style={{ color: '#d97706' }}>unresolved</span>}
                      </td>
                      <td style={td}>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999,
                                       color: '#fff', background: CERT_COLOR[m.certification ?? ''] ?? '#64748b' }}>
                          {m.certification}
                        </span>
                      </td>
                      <td style={num}>{int(m.apiCallCount)}</td>
                      <td style={num}>{int(m.billableOperations)}</td>
                      <td style={num}>{rate(m.costPerOperationRupees)}</td>
                      <td style={{ ...num, color: '#94a3b8' }}>{rate(m.certified_rate)}</td>
                      <td style={{ ...num, color: over ? '#dc2626' : '#94a3b8', fontWeight: over ? 700 : 400 }}>
                        {m.rate_variance_pct == null ? '—' : `${m.rate_variance_pct}%`}
                      </td>
                      <td style={{ ...num, color: '#0f172a', fontWeight: 600 }}>{inr(m.totalCostRupees)}</td>
                      <td style={td}>
                        {m.self_consistent == null ? '—'
                          : m.self_consistent ? <span style={{ color: '#16a34a' }}>ops × rate = cost</span>
                          : <span style={{ color: '#dc2626', fontWeight: 600 }}>inconsistent</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <div style={{ padding: '10px 16px', fontSize: 11, color: '#94a3b8', borderTop: '1px solid #f1f5f9' }}>
            Nova's sandbox figures drift between calls for the same vendor/month — this is a
            snapshot as of the timestamp above, not a stable ledger.
          </div>
        </>
      )}

      {!data && !err && !loading && (
        <div style={{ padding: '22px 16px', fontSize: 13, color: '#94a3b8' }}>
          Pick a brand, vendor and month, then Fetch.
        </div>
      )}
    </div>
  )
}
