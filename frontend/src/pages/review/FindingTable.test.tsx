/**
 * The drilled-in view.
 *
 * The point of this component is subtraction: once the user has drilled into a
 * kind, the kind is established. Fifty rows each repeating "Vendor bank account
 * has never been verified" is the same duplication the grouped queue exists to
 * remove, moved one level down.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Finding } from '../../api/review'
import { FindingTable } from './FindingTable'

const TITLE = 'Vendor bank account has never been verified'

function finding(over: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f1', finding_type: 'vendor_bank_unverified', family: 'vendor_identity',
    lane: 'compliance', subject: { type: 'vendor', id: 'v1' }, severity: 'HIGH',
    confidence: 1, verification: 'VERIFIED', is_suspected: false, money_at_risk: null,
    money_currency: 'INR', money_at_risk_inr: 849800, due_at: null, rank: 0.5,
    title: TITLE,
    detail: 'Payment is blocked until the account passes a penny-drop check.',
    evidence: {}, actions: [], deep_link: '/vendors/v1', document: null,
    can_act: true, required_capability: 'verify', status: 'OPEN',
    status_reason: null, status_by: null, snoozed_until: null,
    detector: 'adapter:vendor_identity', source_service: 'kyc_verification', model: null,
    occurrence_count: 1, first_detected_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    entities: [{ type: 'vendor', id: 'v1', label: 'Unifinz Capital India Limited', role: 'subject' }],
    ...over,
  }
}

const rows = [
  finding({ finding_id: 'a', entities: [{ type: 'vendor', id: 'v1', label: 'Unifinz Capital India Limited', role: 'subject' }] }),
  finding({ finding_id: 'b', entities: [{ type: 'vendor', id: 'v2', label: 'A V INDUSTRIES', role: 'subject' }] }),
  finding({ finding_id: 'c', entities: [{ type: 'vendor', id: 'v3', label: 'UVL TECHNOLOGIES PRIVATE LIMITED', role: 'subject' }] }),
]

describe('the drilled-in table does not repeat the finding', () => {
  it('never renders the shared title, not even once per row', () => {
    // The title belongs to the heading above the table, which is the page's job.
    const { container } = render(<FindingTable findings={rows} onOpen={() => {}} />)
    expect(container.textContent).not.toContain(TITLE)
  })

  it('renders one row per record', () => {
    render(<FindingTable findings={rows} onOpen={() => {}} />)
    const body = screen.getAllByRole('rowgroup')[1]
    expect(within(body).getAllByRole('row')).toHaveLength(3)
  })

  it('names the record each row is about', () => {
    render(<FindingTable findings={rows} onOpen={() => {}} />)
    expect(screen.getByText('Unifinz Capital India Limited')).toBeTruthy()
    expect(screen.getByText('A V INDUSTRIES')).toBeTruthy()
  })

  it('falls back to an id when a record has no label', () => {
    render(
      <FindingTable
        findings={[finding({ entities: [], subject: { type: 'vendor', id: 'abcdef1234' } })]}
        onOpen={() => {}}
      />,
    )
    expect(screen.getByText('abcdef12')).toBeTruthy()
  })

  it('opens the finding when a row is clicked', () => {
    const onOpen = vi.fn()
    render(<FindingTable findings={rows} onOpen={onOpen} />)
    screen.getByText('A V INDUSTRIES').click()
    expect(onOpen).toHaveBeenCalledWith('b')
  })

  it('keeps the row action separate from opening the row', () => {
    // Without stopPropagation the click would both fix and open.
    const onOpen = vi.fn()
    const onAct = vi.fn()
    const withFix = finding({
      finding_id: 'd',
      actions: [{
        id: 'verify_bank', label: 'Run verification now', kind: 'safe',
        endpoint: '/x', method: 'POST', fields: [], preview: '', capability: 'verify',
        destructive: false, href: null, body: {}, confirm: null,
      }],
    })
    render(<FindingTable findings={[withFix]} onOpen={onOpen} onAct={onAct} />)
    screen.getByRole('button', { name: 'Run verification now' }).click()
    expect(onAct).toHaveBeenCalledOnce()
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('offers no action on a finding the user cannot act on', () => {
    const locked = finding({ can_act: false })
    render(<FindingTable findings={[locked]} onOpen={() => {}} onAct={() => {}} />)
    expect(screen.queryByRole('button', { name: /verification/i })).toBeNull()
  })

  it('renders an empty set without throwing', () => {
    expect(() => render(<FindingTable findings={[]} onOpen={() => {}} />)).not.toThrow()
  })
})
