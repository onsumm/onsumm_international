/**
 * The CGST/SGST/IGST split, on the screen where the money is approved.
 *
 * The split was read at upload, shown on the review screen, then added up and
 * thrown away — so the Bills page showed one "GST Amount" and anyone who
 * needed to know how it divided went back to the PDF.
 *
 * What is pinned here is the shape as much as the numbers: the bill detail
 * lays the split out as the review screen does, same labels in the same order,
 * with an absent component keeping its slot. Two screens showing one invoice's
 * tax two different ways is what made a correction made at upload hard to find
 * again after posting.
 *
 * The case worth pinning hardest is the stale one. The split is written once,
 * at ingest; PUT /api/bills can change the tax total afterwards and has no way
 * to know how the new figure divides. Showing a split that no longer adds up,
 * silently, would be worse than showing nothing.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import GstBreakdown, { type GstBreakdownData } from './GstBreakdown'

const intra: GstBreakdownData = {
  cgst: 432.11, sgst: 432.11, igst: null, cess: null,
  cgst_rate: 9, sgst_rate: 9, igst_rate: null,
  taxable_amount: 4801.2, place_of_supply: 'Haryana (06)', components_total: 864.22,
  reconciles: true, is_interstate: false,
}

/** The value rendered under a given label, as the grid pairs them. */
const valueOf = (label: string) =>
  screen.getByText(label).parentElement!.textContent!.replace(label, '')

describe('GstBreakdown', () => {
  it('shows each component and its rate in the review screen’s own fields', () => {
    render(<GstBreakdown data={intra} taxAmount={864.22} />)

    expect(valueOf('Taxable Amt')).toBe('₹4,801.20')
    expect(valueOf('CGST Rate %')).toBe('9')
    expect(valueOf('CGST Amount')).toBe('₹432.11')
    expect(valueOf('SGST Rate %')).toBe('9')
    expect(valueOf('SGST Amount')).toBe('₹432.11')
    expect(valueOf('Total Tax')).toBe('₹864.22')
    // What the invoice printed, not what we inferred from an address — it is
    // the field that decides whether this split is the right one.
    expect(valueOf('Place of Supply')).toBe('Haryana (06)')
  })

  it('lays the fields out in the order the review screen uses', () => {
    const { container } = render(<GstBreakdown data={intra} taxAmount={864.22} />)
    expect([...container.querySelectorAll('.bm__lbl')].map(n => n.textContent)).toEqual([
      'Taxable Amt', 'CGST Rate %', 'CGST Amount',
      'SGST Rate %', 'SGST Amount', 'IGST Rate %',
      'IGST Amount', 'Cess Amount', 'Total Tax',
      'Place of Supply',
    ])
  })

  it('keeps an absent component’s slot rather than closing the gap', () => {
    render(<GstBreakdown data={intra} taxAmount={864.22} />)
    // Where a field sits carries meaning on both screens, so IGST stays put
    // and shows a dash instead of shifting Cess up into its place.
    expect(valueOf('IGST Rate %')).toBe('—')
    expect(valueOf('IGST Amount')).toBe('—')
    expect(valueOf('Cess Amount')).toBe('—')
  })

  it('states intra- or inter-state rather than leaving it to be inferred', () => {
    render(<GstBreakdown data={intra} taxAmount={864.22} />)
    expect(screen.getByText('Intra-state')).toBeInTheDocument()
  })

  it('reads an IGST-only bill as inter-state', () => {
    render(<GstBreakdown
      data={{ ...intra, cgst: null, sgst: null, igst: 864.22, igst_rate: 18, is_interstate: true }}
      taxAmount={864.22}
    />)
    expect(screen.getByText('Inter-state')).toBeInTheDocument()
    expect(valueOf('IGST Rate %')).toBe('18')
    expect(valueOf('IGST Amount')).toBe('₹864.22')
    expect(valueOf('CGST Amount')).toBe('—')
  })

  it('titles the section as the review screen does', () => {
    const { container } = render(<GstBreakdown data={intra} taxAmount={864.22} />)
    expect(within(container.querySelector('.bm__section') as HTMLElement)
      .getByText('Tax Breakdown')).toBeInTheDocument()
  })

  it('says so when the bill total has been edited away from the split', () => {
    render(<GstBreakdown data={{ ...intra, reconciles: false }} taxAmount={1000} />)
    expect(screen.getByText(/what the invoice stated/)).toBeInTheDocument()
    expect(screen.getByText(/₹1,000.00/)).toBeInTheDocument()
  })

  it('stays silent on a bill that predates the split being kept', () => {
    const { container } = render(<GstBreakdown data={null} taxAmount={864.22} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when every component is zero', () => {
    const { container } = render(<GstBreakdown
      data={{ cgst: 0, sgst: 0, igst: 0, cess: 0, components_total: 0, reconciles: true }}
      taxAmount={0}
    />)
    expect(container).toBeEmptyDOMElement()
  })

  it('does not print a rate it does not have', () => {
    render(<GstBreakdown
      data={{ ...intra, cgst_rate: null, sgst_rate: null }}
      taxAmount={864.22}
    />)
    expect(valueOf('CGST Rate %')).toBe('—')
    expect(valueOf('CGST Amount')).toBe('₹432.11')
  })
})
