/**
 * A suspected finding's fix, offered from the row.
 *
 * Findings the Communication Agent raises off a contract are UNVERIFIED by
 * construction — master_data_conflict.py only reaches VERIFIED once a human
 * has approved the reading — so "suspected" is the normal state for the PAN,
 * payment-terms and new-contact findings a user triages most. While the queue
 * withheld the action on those, the hub looked like it had no approve button
 * at all, and the only way to find one was to guess that the drawer had it.
 *
 * The fix now leads the row. What it must not become is a one-click write:
 * ReviewHubPage::handleAct still routes it through the same confirmation the
 * drawer shows, which is what these tests pin from the row's side — the row
 * reports the intent, it does not perform the write.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Finding, FindingAction } from '../../api/review'
import { QueueTable } from './QueueTable'

const KEEP_NEW: FindingAction = {
  id: 'keep_new', label: "Keep the contract's pan (AACCA2775M)", kind: 'safe',
  endpoint: '/api/vendors/v1', method: 'PATCH', fields: [], preview: '',
  capability: 'edit', destructive: false, href: null,
  body: { pan: 'AACCA2775M' },
  confirm: {
    title: "Update 3DOT14 MOBITECH LIMITED's pan",
    change: { label: 'PAN', from: 'AABCZ6304N', to: 'AACCA2775M' },
  },
}

function finding(over: Partial<Finding> = {}): Finding {
  return {
    finding_id: 'f1', finding_type: 'vendor_pan_conflict', family: 'vendor_identity',
    lane: 'compliance', subject: { type: 'vendor', id: 'v1' }, severity: 'MEDIUM',
    confidence: 1, verification: 'UNVERIFIED', is_suspected: true, money_at_risk: null,
    money_currency: 'INR', money_at_risk_inr: null, due_at: null, rank: 0.5,
    title: "A vendor contract states a different pan than what's on file",
    detail: 'Nothing has been changed yet.',
    evidence: {}, actions: [KEEP_NEW], deep_link: '/vendors/v1', document: null,
    can_act: true, required_capability: 'edit', status: 'OPEN',
    status_reason: null, status_by: null, snoozed_until: null,
    detector: 'adapter:master_data_conflict', source_service: 'vendors', model: null,
    occurrence_count: 1, first_detected_at: new Date().toISOString(),
    last_seen_at: new Date().toISOString(),
    entities: [{ type: 'vendor', id: 'v1', label: '3DOT14 MOBITECH LIMITED', role: 'subject' }],
    ...over,
  }
}

function renderTable(f: Finding, over: Partial<Parameters<typeof QueueTable>[0]> = {}) {
  const props = {
    findings: [f], busy: new Set<string>(), selectedIds: new Set<string>(),
    rowErrors: new Map<string, string>(),
    sort: 'rank' as const, direction: 'desc' as const, onSort: vi.fn(),
    onOpen: vi.fn(), onAct: vi.fn(), onToggleSelect: vi.fn(),
    onSelectAll: vi.fn(), onNotify: vi.fn(), onFeedback: vi.fn(),
    feedbackBusyId: null, entityHref: () => null,
    ...over,
  }
  render(<QueueTable {...props} />)
  return props
}

describe('a suspected finding offers its fix in the row', () => {
  it('shows the action label, not only a way to open the finding', () => {
    renderTable(finding())
    expect(screen.getByRole('button', { name: KEEP_NEW.label })).toBeTruthy()
  })

  it('puts the fix before "Open & check"', () => {
    renderTable(finding())
    const row = screen.getAllByRole('row')[1]
    const labels = within(row).getAllByRole('button').map(b => b.textContent?.trim())
    const fix = labels.indexOf(KEEP_NEW.label)
    const check = labels.findIndex(l => l === 'Open & check')
    expect(fix).toBeGreaterThanOrEqual(0)
    expect(check).toBeGreaterThan(fix)
  })

  it('still offers the way to check it, and the feedback pair', () => {
    renderTable(finding())
    expect(screen.getByRole('button', { name: 'Open & check' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Yes, this is a real problem' })).toBeTruthy()
  })

  it('reports the click as an intent to act — the page decides how to confirm', () => {
    const props = renderTable(finding())
    fireEvent.click(screen.getByRole('button', { name: KEEP_NEW.label }))
    expect(props.onAct).toHaveBeenCalledTimes(1)
    expect(props.onAct).toHaveBeenCalledWith(expect.objectContaining({ finding_id: 'f1' }))
    // Opening is a separate affordance; approving must not also open.
    expect(props.onOpen).not.toHaveBeenCalled()
  })

  it('offers nothing to act on when the finding carries no safe action', () => {
    renderTable(finding({ actions: [] }))
    expect(screen.getByRole('button', { name: 'Open & check' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: KEEP_NEW.label })).toBeNull()
  })

  it('leaves a finding this user cannot act on as a notify, fix or no fix', () => {
    const props = renderTable(finding({ can_act: false }))
    expect(screen.queryByRole('button', { name: KEEP_NEW.label })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Notify' }))
    expect(props.onNotify).toHaveBeenCalledTimes(1)
  })
})
