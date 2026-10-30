import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { VendorActivityEvent, VendorActivityPage } from '../../api/vendorCard'

const api = vi.hoisted(() => ({ fetchVendorActivity: vi.fn() }))
vi.mock('../../api/vendorCard', () => api)

import VendorActivity from './VendorActivity'

// Local times, so the day groups and clock read the same in any timezone.
const NOW = new Date(2026, 9, 25, 12)
const at = (day: number, h: number, m: number, year = 2026) => new Date(year, 9, day, h, m).toISOString()

function ev(p: Partial<VendorActivityEvent>): VendorActivityEvent {
  return {
    id: Math.random().toString(36).slice(2), at: at(25, 10, 31), kind: 'bill', action: 'bill_received',
    title: 'Bill received', detail: null, actor: null, actor_role: null, invoice_id: null, bill_no: null,
    amount: null, changes: null, ...p,
  }
}

const EVENTS: VendorActivityEvent[] = [
  ev({ id: 'e1', at: at(25, 10, 31), kind: 'bill', title: 'Bill SGL/0445 received by email', invoice_id: 'inv-445', bill_no: 'SGL/0445' }),
  ev({ id: 'e2', at: at(24, 16, 5), kind: 'change', action: 'vendor_updated', title: 'Vendor details updated', actor: 'Ananya', actor_role: 'Finance',
    changes: [{ field: 'Payment terms', from: '30 days', to: '45 days' }, { field: 'Address', from: null, to: 'Plot 14, MIDC Industrial Area, Andheri East, Mumbai 400093' }] }),
  ev({ id: 'e3', at: at(20, 9, 0), kind: 'payment', action: 'payment_failed', title: 'Payment of ₹50,000 failed', detail: 'The bank rejected the IFSC.' }),
]

const page = (p: Partial<VendorActivityPage> = {}): VendorActivityPage => ({ events: EVENTS, next_before: null, partial: false, ...p })

function show(onOpenBill = vi.fn()) {
  render(<VendorActivity supplierId="v1" onOpenBill={onOpenBill} now={NOW} />)
  return onOpenBill
}

beforeEach(() => { api.fetchVendorActivity.mockReset() })

describe('vendor activity', () => {
  it('groups events by day, with who did it and what changed', async () => {
    api.fetchVendorActivity.mockResolvedValue(page())
    show()
    const today = await screen.findByRole('region', { name: 'Today' })
    expect(within(today).getByRole('listitem')).toHaveTextContent('Bill SGL/0445 received by email')
    expect(within(today).getByText('System')).toBeInTheDocument()
    expect(within(today).getByText('10:31')).toBeInTheDocument()

    const yesterday = screen.getByRole('region', { name: 'Yesterday' })
    expect(within(yesterday).getByText('by Ananya · Finance')).toBeInTheDocument()
    expect(within(yesterday).getByText('Payment terms:').parentElement).toHaveTextContent('Payment terms: 30 days → 45 days')
    // A long value is cut, with the whole of it on hover.
    const addr = within(yesterday).getByTitle('Plot 14, MIDC Industrial Area, Andheri East, Mumbai 400093')
    expect(addr.textContent).toMatch(/…$/)

    expect(screen.getByRole('heading', { name: 'Tue 20 Oct' })).toBeInTheDocument()
    expect(screen.getByText('The bank rejected the IFSC.')).toBeInTheDocument()
    expect(api.fetchVendorActivity).toHaveBeenCalledWith('v1')
  })

  it('shows the year for another year’s day', async () => {
    api.fetchVendorActivity.mockResolvedValue(page({ events: [ev({ at: at(20, 9, 0, 2025) })] }))
    show()
    expect(await screen.findByRole('heading', { name: 'Mon 20 Oct 2025' })).toBeInTheDocument()
  })

  it('filters by kind and hides empty chips', async () => {
    api.fetchVendorActivity.mockResolvedValue(page())
    show()
    const all = await screen.findByRole('button', { name: /All/ })
    expect(all).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('button', { name: /Compliance/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Emails/ })).not.toBeInTheDocument()

    const payments = screen.getByRole('button', { name: /Payments/ })
    expect(payments).toHaveTextContent('1')
    await userEvent.click(payments)
    expect(payments).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Payment of ₹50,000 failed')).toBeInTheDocument()
    expect(screen.queryByText('Vendor details updated')).not.toBeInTheDocument()
  })

  it('searches by bill number or person', async () => {
    api.fetchVendorActivity.mockResolvedValue(page())
    show()
    await userEvent.type(await screen.findByRole('textbox', { name: 'Search activity' }), 'ananya')
    expect(screen.getByText('Vendor details updated')).toBeInTheDocument()
    expect(screen.queryByText('Payment of ₹50,000 failed')).not.toBeInTheDocument()
  })

  it('opens the bill from its number', async () => {
    api.fetchVendorActivity.mockResolvedValue(page())
    const onOpenBill = show()
    await userEvent.click(await screen.findByRole('button', { name: 'SGL/0445' }))
    expect(onOpenBill).toHaveBeenCalledWith('inv-445')
  })

  it('loads older events and adds them below', async () => {
    api.fetchVendorActivity
      .mockResolvedValueOnce(page({ next_before: '2026-10-20T00:00:00+05:30' }))
      .mockResolvedValueOnce(page({ events: [ev({ id: 'old', at: at(2, 11, 0), title: 'Bank account verified', kind: 'compliance', action: 'bank_verified' })] }))
    show()
    await userEvent.click(await screen.findByRole('button', { name: 'Load older' }))
    expect(api.fetchVendorActivity).toHaveBeenLastCalledWith('v1', { before: '2026-10-20T00:00:00+05:30' })
    expect(await screen.findByText('Bank account verified')).toBeInTheDocument()
    expect(screen.getByText('Vendor details updated')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load older' })).not.toBeInTheDocument()
  })

  it('says when it failed, and Retry asks again', async () => {
    api.fetchVendorActivity.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce(page())
    show()
    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t load activity')
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Vendor details updated')).toBeInTheDocument()
    expect(api.fetchVendorActivity).toHaveBeenCalledTimes(2)
  })

  it('notes when some history is missing', async () => {
    api.fetchVendorActivity.mockResolvedValue(page({ partial: true }))
    show()
    expect(await screen.findByText('Some older history couldn’t be loaded.')).toBeInTheDocument()
  })

  it('says so when there is nothing yet', async () => {
    api.fetchVendorActivity.mockResolvedValue(page({ events: [] }))
    show()
    expect(await screen.findByText('No activity recorded for this vendor yet.')).toBeInTheDocument()
  })
})

describe('collapseBillPairs', () => {
  it('reads a bill received and sent for approval moments apart as one event', async () => {
    const { collapseBillPairs } = await import('./VendorActivity')
    const base = { kind: 'bill' as const, detail: null, actor: 'Javed', actor_role: 'approver', invoice_id: 'i1', bill_no: 'DL/1', amount: 100, changes: null }
    const out = collapseBillPairs([
      { ...base, id: 's', at: '2026-06-25T15:24:30Z', kind: 'approval', action: 'bill_submitted', title: 'Submitted bill DL/1 for approval', detail: 'Invoice summary' },
      { ...base, id: 'r', at: '2026-06-25T15:24:00Z', action: 'bill_received', title: 'Received bill DL/1 · ₹100' },
      { ...base, id: 'x', at: '2026-06-20T10:00:00Z', action: 'bill_received', title: 'Received bill DL/2', invoice_id: 'i2', bill_no: 'DL/2' },
    ])
    expect(out.map(e => e.title)).toEqual(['Received bill DL/1 · ₹100 and sent it for approval', 'Received bill DL/2'])
    expect(out[0].detail).toBe('Invoice summary')
  })
})

