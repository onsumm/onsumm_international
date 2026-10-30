import { describe, expect, it } from 'vitest'
import type { Bill } from '../../api/invoices'
import { billAmount, billMoney, billState, compactINR, forecast, isOpen, isUnconverted, monthlyTotals, paymentBuckets, stageOf, summarize } from './vendorStats'

const TODAY = new Date('2026-10-10T12:00:00')

function bill(p: Partial<Bill>): Bill {
  return {
    invoice_id: Math.random().toString(36), id: 'INV', supplier_id: 's1', invoice_date: '2026-09-01', due_date: '2026-10-01',
    status: 'APPROVED', total_amount: 1000, currency_code: 'INR', vendor_name: 'Acme', vendor_gstn: null, original_file: null,
    upload_id: null, stg_bill_id: null, created_at: null, uploaded_by_name: null, uploaded_by_role: null, category: null,
    department: null, payment_status: 'unpaid', amount_paid: null, outstanding_amount: null, payment_updated_by: null,
    payment_updated_at: null, tally_push_pending: false, manual_priority: null, soft_status: 'active', subtotal: null,
    tax_amount: null, tds_status: null, tds_section_code: null, tds_rate: null, tds_amount: null, tds_base_amount: null,
    net_payable: null, original_amount: null, inr_amount_current: null, brand_id: null, cost_center_id: null,
    cost_center_name: null, fx_agent_note: null, fx_agent_severity: null, ...p,
  }
}

describe('monthlyTotals', () => {
  it('groups by bill date and fills empty months with zero', () => {
    const s = monthlyTotals([
      bill({ invoice_date: '2026-07-05', total_amount: 100 }),
      bill({ invoice_date: '2026-07-20', total_amount: 50 }),
      bill({ invoice_date: '2026-09-02', total_amount: 30 }),
    ], TODAY)
    expect(s.map(m => [m.ym, m.total])).toEqual([['2026-07', 150], ['2026-08', 0], ['2026-09', 30], ['2026-10', 0]])
    expect(s[s.length - 1].partial).toBe(true)
  })
})

describe('forecast', () => {
  const months = (vals: number[]) => monthlyTotals(
    vals.map((v, i) => bill({ invoice_date: `2026-${String(i + 1).padStart(2, '0')}-10`, total_amount: v })),
    new Date('2026-12-15T12:00:00'),
  ).slice(0, vals.length)

  it('needs three full months', () => {
    expect(forecast(months([100, 120])).ok).toBe(false)
  })
  it('uses a weighted average with 3–5 months', () => {
    const f = forecast(months([100, 100, 200]))
    expect(f.ok).toBe(true)
    expect(f.expected![0]).toBeCloseTo((100 + 200 + 600) / 6)
    expect(f.confidence).toBe('Low')
  })
  it('follows a trend with 6+ months', () => {
    const f = forecast(months([100, 110, 120, 130, 140, 150, 160]))
    expect(f.expected![0]).toBeGreaterThan(160)
    expect(f.expected![1]).toBeGreaterThan(f.expected![0])
  })

  // Real series run up to "today" (Oct 2026 here), so September is the last full month.
  const TODAY_OCT = new Date('2026-10-10T12:00:00')
  const billedIn = (yms: string[]) => monthlyTotals(yms.map(ym => bill({ invoice_date: `${ym}-10`, total_amount: 100 })), TODAY_OCT)

  it('does not forecast a vendor who stopped billing, however much history there is', () => {
    const f = forecast(billedIn(['2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06']))
    expect(f.ok).toBe(false)
    expect(f.reason).toBe('stopped')
    expect(f.lastBilled).toBe('2026-06')
  })

  it('does not forecast across a recent gap', () => {
    // Billed every month but August.
    const f = forecast(billedIn(['2026-03', '2026-04', '2026-05', '2026-06', '2026-07', '2026-09']))
    expect(f.ok).toBe(false)
    expect(f.reason).toBe('short')
    expect(f.months).toBe(1)
    expect(f.since).toBe('2026-09')
  })

  it('forecasts once the last 3 months each have bills, from that run only', () => {
    // An old gap (March) doesn't matter: April–September is unbroken.
    const f = forecast(billedIn(['2026-01', '2026-02', '2026-04', '2026-05', '2026-06', '2026-07', '2026-08', '2026-09']))
    expect(f.ok).toBe(true)
    expect(f.months).toBe(6)
    expect(f.since).toBe('2026-04')
  })

  it('counts this month only once it is over', () => {
    // July, August, October: October is still in progress, so only 0 months in a row end in September.
    const f = forecast(billedIn(['2026-07', '2026-08', '2026-10']))
    expect(f.ok).toBe(false)
  })
})

describe('stages and buckets', () => {
  it('maps statuses to stages', () => {
    expect(stageOf(bill({ status: 'DRAFT' }))).toBe(0)
    expect(stageOf(bill({ status: 'PENDING_FINANCE' }))).toBe(1)
    expect(stageOf(bill({ status: 'APPROVED' }))).toBe(2)
    expect(stageOf(bill({ status: 'APPROVED', payment_status: 'paid' }))).toBe(3)
  })
  it('splits open bills by when they are due', () => {
    const b = paymentBuckets([
      bill({ due_date: '2026-10-01' }),
      bill({ due_date: '2026-10-12' }),
      bill({ due_date: '2026-11-30' }),
      bill({ due_date: '2026-10-20', status: 'PENDING' }),
    ], TODAY)
    expect(b.map(x => x.bills.length)).toEqual([1, 1, 1, 1])
  })
})

describe('summarize', () => {
  it('ignores deleted and rejected bills and finds the next due date', () => {
    const s = summarize([
      bill({ due_date: '2026-10-01', total_amount: 500 }),
      bill({ due_date: '2026-10-15', total_amount: 300 }),
      bill({ status: 'REJECTED', total_amount: 9999 }),
      bill({ soft_status: 'deleted', total_amount: 9999 }),
      bill({ payment_status: 'paid', invoice_date: '2026-08-01', payment_updated_at: '2026-09-10T10:00:00' }),
    ], TODAY)
    expect(s.outstanding).toBe(800)
    expect(s.overdue).toHaveLength(1)
    expect(s.nextDue?.due_date).toBe('2026-10-15')
    expect(s.daysToPay).toBe(40)
  })
})

describe('compactINR', () => {
  it('uses Indian units', () => {
    expect(compactINR(288000)).toBe('₹2.88L')
    expect(compactINR(94000)).toBe('₹94K')
    expect(compactINR(12000000)).toBe('₹1.2Cr')
  })
})

describe('billState', () => {
  const now = new Date('2026-10-22T12:00:00')
  const b = (p: Partial<Bill>) => ({ status: 'APPROVED', payment_status: 'unpaid', due_date: '2026-11-01', ...p }) as Bill

  it('calls a late bill still in approval our hold-up, not an overdue payment', () => {
    expect(billState(b({ status: 'PENDING_APPROVAL', due_date: '2026-07-03' }), now))
      .toEqual({ tone: 'warn', label: 'In approval · 111d late', late: 111, blocked: true })
    expect(billState(b({ status: 'EXTRACTED', due_date: '2026-10-20' }), now).label).toBe('Needs review · 2d late')
  })

  it('keeps red for approved bills past their due date', () => {
    expect(billState(b({ due_date: '2026-10-12' }), now)).toEqual({ tone: 'bad', label: 'Overdue 10d', late: 10, blocked: false })
  })

  it('reads in-time, part-paid, undated and paid bills plainly', () => {
    expect(billState(b({ status: 'PENDING_APPROVAL' }), now).label).toBe('In approval')
    expect(billState(b({ payment_status: 'partial' }), now).tone).toBe('info')
    expect(billState(b({ due_date: null }), now).label).toBe('No due date')
    expect(billState(b({ due_date: '2026-11-01' }), now).label).toBe('Due 1 Nov')
    expect(billState(b({ payment_status: 'paid', due_date: '2026-01-01' }), now)).toEqual({ tone: 'ok', label: 'Paid', late: 0, blocked: false })
  })
})

describe('one meaning of overdue (O-M1)', () => {
  const s = summarize([
    bill({ id: 'APP', due_date: '2026-09-20', total_amount: 100 }),
    bill({ id: 'HELD', due_date: '2026-09-01', status: 'PENDING_APPROVAL', total_amount: 200 }),
    bill({ id: 'NEW', due_date: '2026-10-20', status: 'PENDING_APPROVAL', total_amount: 400 }),
  ], TODAY)

  it('calls only approved, unpaid, past-due bills overdue', () => {
    expect(s.overdue.map(b => b.id)).toEqual(['APP'])
    expect(s.lateInApproval.map(b => b.id)).toEqual(['HELD'])
  })

  it('keeps late unapproved bills under Awaiting approval, and the buckets add up', () => {
    const [overdue, , , approval] = s.buckets
    expect(overdue.bills.map(b => b.id)).toEqual(['APP'])
    expect(approval.bills.map(b => b.id).sort()).toEqual(['HELD', 'NEW'])
    expect(s.buckets.reduce((t, b) => t + b.amount, 0)).toBe(s.outstanding)
  })
})

describe('foreign-currency bills (B-H1)', () => {
  const usd = bill({ currency_code: 'USD', total_amount: 5000, original_amount: null, inr_amount_current: null })

  it('never counts an unconverted bill as rupees', () => {
    expect(isUnconverted(usd)).toBe(true)
    expect(billAmount(usd)).toBe(0)
    expect(billMoney(usd)).toBe('USD 5,000 · not converted')
    const s = summarize([usd, bill({ total_amount: 1000 })], TODAY)
    expect(s.outstanding).toBe(1000)
    expect(s.unconverted).toEqual([usd])
    // Still unpaid, so still listed.
    expect(isOpen(usd)).toBe(true)
    expect(s.open).toContain(usd)
  })

  it('counts a converted one in rupees', () => {
    expect(billAmount(bill({ currency_code: 'USD', total_amount: 5000, inr_amount_current: 420000 }))).toBe(420000)
    // Converted in place: total_amount rewritten in ₹, the invoice figure kept in original_amount.
    const moved = bill({ currency_code: 'USD', total_amount: 415000, original_amount: 5000 })
    expect(isUnconverted(moved)).toBe(false)
    expect(billAmount(moved)).toBe(415000)
  })
})

describe('spend numbers', () => {
  it('says "first bills this month" rather than averaging to ₹0', () => {
    const s = summarize([bill({ invoice_date: '2026-10-02' })], TODAY)
    expect(s.avgMonths).toBe(0)
    expect(s.bills12).toBe(1)
  })

  it('counts a bill this month as billing, so the vendor has not stopped (SP-M1)', () => {
    const f = forecast(monthlyTotals([bill({ invoice_date: '2026-06-10' }), bill({ invoice_date: '2026-10-03' })], TODAY))
    expect(f.lastBilled).toBe('2026-10')
  })

  it('only claims a track record when one was checked (SP-M3)', () => {
    const months = (vals: number[]) => monthlyTotals(
      vals.map((v, i) => bill({ invoice_date: `2026-${String(i + 1).padStart(2, '0')}-10`, total_amount: v })),
      new Date('2026-12-15T12:00:00'),
    ).slice(0, vals.length)
    expect(forecast(months([100, 110, 120])).backtested).toBe(false)
    expect(forecast(months([100, 110, 120, 130, 140, 150, 160])).backtested).toBe(true)
  })

  it('keeps half steps on axis labels (SP-L1)', () => {
    expect(compactINR(2500)).toBe('₹2.5K')
    expect(compactINR(7500)).toBe('₹7.5K')
  })
})
