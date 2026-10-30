import { describe, it, expect } from 'vitest'
import type { Bill } from '../../api/invoices'
import { aiIntents, billIntents, billsMentioned } from './aiIntents'

const NOW = new Date('2026-10-10T12:00:00')
const bill = (p: Partial<Bill>) => ({
  invoice_id: 'i1', id: 'SGL/0445', status: 'APPROVED', payment_status: 'unpaid', total_amount: 74800,
  outstanding_amount: 74800, due_date: '2026-10-03', invoice_date: '2026-09-03', soft_status: 'active', ...p,
}) as Bill

describe('billIntents', () => {
  it('asks when an overdue bill will be paid, with the amount and how late', () => {
    expect(billIntents(bill({}), NOW, true)[0]).toBe('Tell them when SGL/0445 (₹74,800, 7 days overdue) will be paid')
  })
  // payment_updated_at is when the status was edited, not a paid-on date, so no date is claimed.
  it('confirms a paid bill without claiming a payment date', () => {
    expect(billIntents(bill({ payment_status: 'paid', outstanding_amount: 0, payment_updated_at: '2026-10-05T10:00:00Z' }), NOW, true)[0])
      .toBe('Confirm SGL/0445 (₹74,800) has been paid')
  })
  it('uses what was actually paid after TDS', () => {
    expect(billIntents(bill({ payment_status: 'paid', outstanding_amount: 0, amount_paid: 73304 }), NOW, true)[0])
      .toBe('Confirm SGL/0445 (₹73,304) has been paid')
    expect(billIntents(bill({ payment_status: 'paid', outstanding_amount: 0, amount_paid: null, net_payable: 73304 }), NOW, true)[0])
      .toBe('Confirm SGL/0445 (₹73,304) has been paid')
  })
  it('says a bill in approval is in approval', () => {
    expect(billIntents(bill({ status: 'PENDING', due_date: '2026-10-30' }), NOW, false)[0]).toBe('Let them know SGL/0445 (₹74,800) is in approval')
  })
})

describe('aiIntents', () => {
  it('leads with the attached bill', () => {
    const g = aiIntents({ attached: [bill({})], mentioned: [], now: NOW, hasThread: true })
    expect(g[0].title).toBe('About SGL/0445')
    expect(g[g.length - 1].title).toBe('Something else')
  })
  it('groups several attached bills and calls out the overdue ones', () => {
    const g = aiIntents({ attached: [bill({}), bill({ invoice_id: 'i2', id: 'SGL/0446', due_date: '2026-10-20', total_amount: 69200 })], mentioned: [], now: NOW, hasThread: false })
    expect(g[0].title).toBe('About the 2 attached bills')
    expect(g[0].items[0]).toBe('Send these 2 bills (₹1,44,000) and ask them to confirm they match their records')
    expect(g[0].items).toContain('Tell them when the 1 overdue one will be paid')
  })
  it('suggests answering about bills the conversation mentions', () => {
    const g = aiIntents({ attached: [], mentioned: [bill({})], now: NOW, hasThread: true })
    expect(g[0]).toEqual({ title: 'About bills in this conversation', items: ['Tell them when SGL/0445 (₹74,800, 7 days overdue) will be paid'] })
  })
  it('falls back to general suggestions with no bills in play', () => {
    const g = aiIntents({ attached: [], mentioned: [], now: NOW, hasThread: true })
    expect(g).toHaveLength(1)
    expect(g[0].title).toBe('What should it say?')
    expect(g[0].items[0]).toBe('Reply helpfully to their last message')
  })
})

describe('billsMentioned', () => {
  it('finds bill numbers in text, ignoring case and very short ids', () => {
    const bills = [bill({}), bill({ invoice_id: 'i2', id: 'DN 07' }), bill({ invoice_id: 'i3', id: '12' })]
    expect(billsMentioned(bills, ['When will sgl/0445 be paid? Also dn 07. Ref 12']).map(b => b.invoice_id)).toEqual(['i1', 'i2'])
  })
  it('matches whole bill numbers only, not inside longer numbers', () => {
    const bills = [bill({ invoice_id: 'a', id: '1234' })]
    expect(billsMentioned(bills, ['Call 98123456 or see bill 12345'])).toEqual([])
    expect(billsMentioned(bills, ['Bill #1234, due Friday']).map(b => b.invoice_id)).toEqual(['a'])
  })
})
