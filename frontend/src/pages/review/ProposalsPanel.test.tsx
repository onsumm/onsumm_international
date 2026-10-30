/**
 * Several corrections to one bill, each accepted on its own.
 *
 * Partial acceptance is the whole reason the four are bundled: a bill can be
 * wrong about its category and right about its cost centre, and someone forced
 * to take all four or none will take none. So what these pin is that the panel
 * sends exactly what is ticked — never more.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const applyBillCoding = vi.fn()
vi.mock('../../api/review', () => ({ applyBillCoding: (...a: unknown[]) => applyBillCoding(...a) }))
vi.mock('../../ui', async () => {
  const React = await import('react')
  return {
    useToast: () => ({ success: vi.fn(), error: vi.fn() }),
    Button: ({ children, onClick, disabled }: Record<string, unknown>) =>
      React.createElement('button', { onClick, disabled } as never, children as never),
  }
})

const { ProposalsPanel } = await import('./ProposalsPanel')

const FINDING = {
  finding_id: 'f1', subject: { type: 'bill', id: 'b1' },
} as never

const PROPOSALS = [
  { id: 'category', group: 'Coding', label: 'Category',
    current: 'Telecom', proposed: 'Software subscriptions',
    why: '11 of this vendor’s last 12 bills', writable: true },
  { id: 'cost_centre', group: 'Coding', label: 'Cost centre',
    current: 'CC-01 G&A', proposed: 'CC-07 Support', writable: true },
  { id: 'gst_split', group: 'GST', label: 'Tax charged',
    current: 'CGST + SGST  ₹4,07,871.85', proposed: 'IGST @ 18%  ₹4,07,871.85',
    why: 'inter-state supply (27 → 07)', writable: true,
    caveat: 'The invoice itself charges CGST+SGST. Recording IGST here fixes our books; the input credit needs a revised invoice from the vendor.' },
]

const ticks = () => screen.getAllByRole('checkbox')
const applyBtn = () => screen.getByRole('button', { name: /^Apply/ })

describe('ProposalsPanel', () => {
  beforeEach(() => {
    applyBillCoding.mockReset()
    applyBillCoding.mockResolvedValue({
      applied: [], failed: [], counts: { applied: 0, failed: 0 },
    })
  })

  it('shows what it would become above what it is, struck through', () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)

    expect(screen.getByText('Software subscriptions')).toBeInTheDocument()
    const old = screen.getByText('Telecom')
    expect(old.tagName).toBe('S')
  })

  it('starts with everything writable ticked, because agreement is the common case', () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    expect(ticks().every(t => t.getAttribute('aria-checked') === 'true')).toBe(true)
    expect(screen.getByText('3 of 3 selected')).toBeInTheDocument()
  })

  it('sends only what is still ticked', async () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)

    fireEvent.click(ticks()[1])            // untick the cost centre
    expect(screen.getByText('2 of 3 selected')).toBeInTheDocument()

    fireEvent.click(applyBtn())
    await waitFor(() => expect(applyBillCoding).toHaveBeenCalledOnce())
    expect(applyBillCoding.mock.calls[0][0]).toEqual({
      finding_id: 'f1', invoice_id: 'b1', accept: ['category', 'gst_split'],
    })
  })

  it('can send just one', async () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    fireEvent.click(ticks()[0])
    fireEvent.click(ticks()[1])

    fireEvent.click(applyBtn())
    await waitFor(() => expect(applyBillCoding).toHaveBeenCalledOnce())
    expect(applyBillCoding.mock.calls[0][0].accept).toEqual(['gst_split'])
  })

  it('will not send an empty selection', () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    ticks().forEach(t => fireEvent.click(t))

    expect(screen.getByText('Nothing selected')).toBeInTheDocument()
    expect(applyBtn()).toBeDisabled()
  })

  it('says on the row that accepting the GST fix does not free the credit', () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    expect(screen.getByText(/needs a revised invoice from the vendor/)).toBeInTheDocument()
  })

  it('locks what was applied and leaves the rest selectable', async () => {
    applyBillCoding.mockResolvedValue({
      applied: ['category'], failed: [], counts: { applied: 1, failed: 0 },
    })
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    fireEvent.click(ticks()[1])
    fireEvent.click(ticks()[2])

    fireEvent.click(applyBtn())
    await waitFor(() => expect(screen.getByText('Applied')).toBeInTheDocument())

    // Applied rows stay on screen rather than vanishing mid-review, and cannot
    // be sent twice.
    expect(ticks()[0]).toBeDisabled()
    expect(screen.getByText('Nothing left selected')).toBeInTheDocument()
  })

  it('offers no tick for something we cannot write', () => {
    render(<ProposalsPanel
      proposals={[{ ...PROPOSALS[0], writable: false }]}
      finding={FINDING}
    />)
    expect(ticks()[0]).toBeDisabled()
    expect(screen.getByText(/Not something we can write/)).toBeInTheDocument()
  })

  it('groups unrelated kinds of wrong rather than running them together', () => {
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} />)
    expect(screen.getByText('Coding')).toBeInTheDocument()
    expect(screen.getByText('GST')).toBeInTheDocument()
  })

  it('tells the caller to refetch, since the finding closes on the next scan', async () => {
    const onApplied = vi.fn()
    applyBillCoding.mockResolvedValue({
      applied: ['category'], failed: [], counts: { applied: 1, failed: 0 },
    })
    render(<ProposalsPanel proposals={PROPOSALS} finding={FINDING} onApplied={onApplied} />)

    fireEvent.click(applyBtn())
    await waitFor(() => expect(onApplied).toHaveBeenCalledOnce())
  })
})
