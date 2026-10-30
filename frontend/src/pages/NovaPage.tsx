import { useState } from 'react'
import NovaOwnershipPanel from '../components/NovaOwnershipPanel'
import NovaRawExplorer from '../components/NovaRawExplorer'

// Nova ownership & rate certification (services/nova_reconciliation/,
// docs/NOVA_RECONCILIATION_LOGIC.md). The older invoice-vs-Nova-ledger view that
// used to share this page (pipelines/billing_reconciliation.py, /api/billing/*) has
// been retired — this is the one Nova reconciliation system now.
//
// Two subpages, deliberately separate: Reconciliation is what we concluded about a
// bill; Raw API is what Nova actually said. Keeping them apart is what makes it
// possible to tell a Nova data problem from a reconciliation problem.

type Tab = 'reconciliation' | 'raw'

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'reconciliation', label: 'Reconciliation', hint: 'Ownership & rate certification per bill' },
  { id: 'raw', label: 'Raw Nova API', hint: 'Unreconciled Nova output by brand, vendor and month' },
]

export default function NovaPage() {
  const [tab, setTab] = useState<Tab>('reconciliation')

  return (
    <div style={{ padding: 24, overflowY: 'auto', height: '100%', background: '#f4f7f8' }}>
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>
        <h1 style={{ fontSize: 18, fontWeight: 800, color: '#0f172a', margin: '0 0 12px', letterSpacing: '-0.03em' }}>Nova</h1>

        <div role="tablist" style={{ display: 'flex', gap: 4, borderBottom: '1px solid #e2e8f0' }}>
          {TABS.map(t => {
            const active = tab === t.id
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                title={t.hint}
                onClick={() => setTab(t.id)}
                style={{
                  padding: '9px 14px', fontSize: 13, fontWeight: active ? 700 : 600,
                  color: active ? '#4338ca' : '#64748b', background: 'none', border: 'none',
                  borderBottom: `2px solid ${active ? '#4f46e5' : 'transparent'}`,
                  marginBottom: -1, cursor: 'pointer',
                }}>
                {t.label}
              </button>
            )
          })}
        </div>

        {tab === 'reconciliation' ? <NovaOwnershipPanel /> : <NovaRawExplorer />}
      </div>
    </div>
  )
}
