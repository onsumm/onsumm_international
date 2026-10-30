import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { Bill } from '../../api/invoices'
import { KpiStrip, buildNudges } from './VendorViews'
import { summarize } from './vendorStats'

const NOW = new Date('2026-10-10T12:00:00')

function bill(p: Partial<Bill>): Bill {
  return {
    invoice_id: Math.random().toString(36).slice(2), id: 'DN 07', supplier_id: 'v1', invoice_date: '2026-04-01', due_date: '2026-05-15',
    status: 'APPROVED', total_amount: 100000, currency_code: 'INR', vendor_name: 'A V Industries', vendor_gstn: null, original_file: null,
    upload_id: null, stg_bill_id: null, created_at: null, uploaded_by_name: null, uploaded_by_role: null, category: null, department: null,
    payment_status: 'unpaid', amount_paid: null, outstanding_amount: 100000, payment_updated_by: null, payment_updated_at: null,
    tally_push_pending: false, manual_priority: null, soft_status: 'active', subtotal: null, tax_amount: null, tds_status: null,
    tds_section_code: null, tds_rate: null, tds_amount: null, tds_base_amount: null, net_payable: null, original_amount: null,
    inr_amount_current: null, brand_id: null, cost_center_id: null, cost_center_name: null, fx_agent_note: null, fx_agent_severity: null, ...p,
  }
}

const tile = (bills: Bill[]) => {
  render(<KpiStrip s={summarize(bills, NOW)} now={NOW} inbox={null} onGo={() => {}} />)
  return within(screen.getByText('Next payment due').closest('button')!)
}

describe('Next payment due tile', () => {
  it('says how many are overdue when nothing is upcoming', () => {
    const t = tile([bill({}), bill({ due_date: '2026-06-01' })])
    expect(t.getByText('2 overdue')).toBeInTheDocument()
  })

  it('shows the next due date', () => {
    const t = tile([bill({}), bill({ id: 'DN 09', due_date: '2026-10-12', outstanding_amount: 5000 })])
    expect(t.getByText('12 Oct')).toBeInTheDocument()
  })

  it('shows a dash when nothing is due', () => {
    const t = tile([bill({ payment_status: 'paid', outstanding_amount: 0 })])
    expect(t.getByText('—')).toBeInTheDocument()
  })
})

describe('late bills still in approval (O-M1)', () => {
  const held = () => bill({ id: 'H1', status: 'PENDING_APPROVAL', due_date: '2026-09-01' })

  it('are not counted as overdue', () => {
    const t = tile([held()])
    expect(t.getByText('1 late in approval')).toBeInTheDocument()
    expect(t.queryByText(/overdue/)).not.toBeInTheDocument()
  })

  it('get Chase approval, not Pay vendor', () => {
    const onOpenBill = vi.fn()
    const nudges = buildNudges({
      s: summarize([held()], NOW), now: NOW, inbox: null, contactNames: {}, bankVerified: true, hasPan: true,
      onOpenBill, onPay: () => {}, onOpenThread: () => {}, onAddContact: () => {}, onGoCompliance: () => {},
    })
    expect(nudges.map(n => n.label)).toEqual(['Held in approval'])
    expect(nudges[0].actions.map(a => a.label)).toEqual(['Chase approval'])
    nudges[0].actions[0].run()
    expect(onOpenBill).toHaveBeenCalled()
  })
})

describe('a source that failed to load (O-H1)', () => {
  it('shows a dash and says so, not ₹0', () => {
    render(<KpiStrip s={summarize([], NOW)} now={NOW} inbox={null} onGo={() => {}} failed={{ bills: true, inbox: true }} />)
    expect(screen.getAllByText('Couldn’t load bills')).toHaveLength(3)
    expect(screen.getByText('Couldn’t load emails')).toBeInTheDocument()
    expect(screen.queryByText('₹0')).not.toBeInTheDocument()
    expect(screen.queryByText('Nothing due')).not.toBeInTheDocument()
  })

  it('holds back the nudges that source would have raised', () => {
    const nudges = buildNudges({
      s: summarize([bill({})], NOW), now: NOW, inbox: null, contactNames: {}, bankVerified: false, hasPan: true,
      onOpenBill: () => {}, onOpenThread: () => {}, onAddContact: () => {}, onGoCompliance: () => {},
      failed: { bills: true, bank: true },
    })
    expect(nudges).toEqual([])
  })
})
