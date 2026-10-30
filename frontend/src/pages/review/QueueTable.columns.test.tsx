/**
 * The queue, one row per finding.
 *
 * What these pin is the reading of a row, because that is what changed: the
 * table used to answer "what kinds of problem do we have" (one row, "× 224")
 * and now answers "which vendor, on which bill, for how much, and is it mine".
 *
 * The sort assertions matter more than they look. Clicking a header here must
 * re-query, not re-order what is on screen — sorting 25 of 636 rows locally
 * produces a table that is indistinguishable from a correct one and means
 * something completely different.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Finding } from '../../api/review'
import { QueueTable } from './QueueTable'

function finding(over: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f1', finding_type: 'unit_price_variance', family: 'reconciliation',
    lane: 'money', subject: { type: 'bill', id: 'b1' }, severity: 'CRITICAL',
    confidence: 1, verification: 'VERIFIED', is_suspected: false, money_at_risk: 38400,
    money_currency: 'INR', money_at_risk_inr: 38400, due_at: null, rank: 0.9,
    title: 'Image Liveliness billed ₹7.16 vs ₹6.00 agreed', detail: '',
    evidence: {}, actions: [], deep_link: '/bills/b1', document: null,
    can_act: true, required_capability: 'edit', status: 'OPEN',
    status_reason: null, status_by: null, snoozed_until: null,
    detector: 'adapter:recon', source_service: 'nova', model: null,
    occurrence_count: 1, first_detected_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    entities: [
      { type: 'vendor', id: 'v1', label: 'Signzy', role: 'subject' },
      { type: 'bill', id: 'b1', label: 'INV-2231', role: 'related' },
    ],
    ...over,
  }
}

function renderTable(findings: Finding[], over = {}) {
  const props = {
    findings, busy: new Set<string>(), selectedIds: new Set<string>(),
    rowErrors: new Map<string, string>(),
    sort: 'rank' as const, direction: 'desc' as const, onSort: vi.fn(),
    onOpen: vi.fn(), onAct: vi.fn(), onToggleSelect: vi.fn(), onSelectAll: vi.fn(),
    onNotify: vi.fn(), onFeedback: vi.fn(), feedbackBusyId: null,
    entityHref: (t: string, id: string) => (t === 'vendor' ? `/vendors/${id}` : null),
    ...over,
  }
  render(<QueueTable {...props} />)
  return props
}

const row = () => screen.getAllByRole('row')[1]

describe('a row answers the four questions in one line', () => {
  it('names the type, the vendor, the bill, the exposure and the priority', () => {
    renderTable([finding()])
    const cells = within(row())

    expect(cells.getByText('Recon')).toBeInTheDocument()
    expect(cells.getByText('Signzy')).toBeInTheDocument()
    expect(cells.getByText('INV-2231')).toBeInTheDocument()
    // Full rupees, grouped the Indian way — a queue is read, not eyeballed,
    // so ₹38.4K would be the wrong trade here.
    expect(cells.getByText('₹38,400')).toBeInTheDocument()
    expect(cells.getByText('Open')).toBeInTheDocument()
    expect(cells.getByText('P0')).toBeInTheDocument()
  })

  it('links the vendor out without opening the drawer on the way', () => {
    const { onOpen } = renderTable([finding()])
    const link = within(row()).getByRole('link', { name: 'Signzy' })
    expect(link).toHaveAttribute('href', '/vendors/v1')

    fireEvent.click(link)
    // The row is clickable; the link inside it must not be both.
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('opens the finding when the row itself is clicked', () => {
    const { onOpen } = renderTable([finding()])
    fireEvent.click(row())
    expect(onOpen).toHaveBeenCalledWith('f1')
  })

  it('marks a finding this user cannot fix, and says so more than one way', () => {
    renderTable([finding({ can_act: false })])
    // Dimming alone would be invisible to anyone who cannot see the contrast
    // difference, so the word and the lock carry it too.
    expect(row().className).toContain('rh__browse-row--locked')
    expect(within(row()).getByText('Blocked')).toBeInTheDocument()
  })

  it('shows a dash, not a zero, where there is nothing at risk', () => {
    renderTable([finding({ money_at_risk_inr: null })])
    expect(within(row()).getByText('—')).toBeInTheDocument()
    expect(within(row()).queryByText('₹0')).not.toBeInTheDocument()
  })

  it('never collapses two findings of the same kind into one row', () => {
    renderTable([finding(), finding({ finding_id: 'f2', title: 'Two bills for Aug' })])
    expect(screen.getAllByRole('row')).toHaveLength(3)   // head + two findings
    expect(screen.queryByText(/× 2/)).not.toBeInTheDocument()
  })
})

describe('sorting is asked of the server, never faked locally', () => {
  it('reports the column rather than reordering what is on screen', () => {
    const rows = [
      finding({ finding_id: 'a', money_at_risk_inr: 100 }),
      finding({ finding_id: 'b', money_at_risk_inr: 900 }),
    ]
    const { onSort } = renderTable(rows)

    fireEvent.click(screen.getByRole('button', { name: /at risk/i }))

    expect(onSort).toHaveBeenCalledWith('money')
    // Still in the order the server handed them over — this component sorted
    // nothing, which is the whole point.
    const [, first, second] = screen.getAllByRole('row')
    expect(within(first).getByText('₹100')).toBeInTheDocument()
    expect(within(second).getByText('₹900')).toBeInTheDocument()
  })

  it('tells assistive tech which column is live and which way it runs', () => {
    renderTable([finding()], { sort: 'money', direction: 'asc' })
    const headers = screen.getAllByRole('columnheader')
    const money = headers.find(h => h.textContent?.includes('at risk'))!
    expect(money).toHaveAttribute('aria-sort', 'ascending')

    const priority = headers.find(h => h.textContent?.includes('Priority'))!
    expect(priority).toHaveAttribute('aria-sort', 'none')
  })

  it('leaves the finding text unsortable — alphabetical by sentence is not an order', () => {
    renderTable([finding()])
    const findingTh = screen.getAllByRole('columnheader').find(h => h.textContent === 'Finding')!
    expect(within(findingTh).queryByRole('button')).toBeNull()
  })
})
