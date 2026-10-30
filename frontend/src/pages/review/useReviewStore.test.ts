/**
 * What happens to a row after you deal with it.
 *
 * The queue is frozen on load on purpose, so nothing re-sorts under a user who
 * is working down it. That same freeze is why clearing a finding has to take it
 * out of the list explicitly — before this, approving a bill or resolving a
 * finding left the row sitting there until a manual refresh, which reads as the
 * action not having worked.
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Finding } from '../../api/review'

const api = vi.hoisted(() => ({
  fetchFindings: vi.fn(),
  fetchCounts: vi.fn(),
  fetchFinding: vi.fn(),
  setFindingStatus: vi.fn(),
  undoFinding: vi.fn(),
  bulkStatus: vi.fn(),
  runAction: vi.fn(),
  rescan: vi.fn(),
}))

vi.mock('../../api/review', async importOriginal => {
  const actual = await importOriginal<typeof import('../../api/review')>()
  return { ...actual, ...api }
})

const { reviewActions, useQueue } = await import('./useReviewStore')

function finding(id: string, over: Partial<Finding> = {}): Finding {
  return {
    finding_id: id, finding_type: 'approval_stalled', family: 'approval',
    lane: 'blocking', subject: { type: 'bill', id: `bill-${id}` },
    severity: 'HIGH', confidence: 1, verification: 'VERIFIED', is_suspected: false,
    money_at_risk: 100, money_currency: 'INR', money_at_risk_inr: 100,
    due_at: null, rank: 1, title: 'Bill has been waiting', detail: '',
    evidence: {}, actions: [], deep_link: '', document: null, can_act: true,
    required_capability: 'approve', status: 'OPEN', status_reason: null,
    status_by: null, snoozed_until: null, detector: 'adapter:approval_stalls',
    source_service: 'approvals', model: null, occurrence_count: 1,
    first_detected_at: '2026-09-01T00:00:00Z', last_seen_at: '2026-09-01T00:00:00Z',
    entities: [], ...over,
  }
}

/** The store is module state; every test starts from a known queue. */
async function load(items: Finding[]) {
  api.fetchFindings.mockResolvedValue({ items, next_cursor: null, scope: 'actionable', lane: null })
  api.fetchCounts.mockResolvedValue({
    lanes: {}, by_severity: {}, total_actionable: items.length, total_visible: items.length,
    money_at_risk_inr: 0, blocking: 0, snoozed: 0, resolved_this_week: 0,
    protected_this_week_inr: 0, last_scan_at: null, my_capabilities: [], role: 'admin',
  })
  await act(async () => { await reviewActions.loadQueue({}) })
}

/** The queue as the page sees it: through the hook, in a render. */
function queue() {
  return renderHook(() => useQueue()).result.current
}

function ids() {
  return queue().items.map(f => f.finding_id)
}

beforeEach(() => {
  vi.clearAllMocks()
  reviewActions.reset()
})

describe('reading what actually happened', () => {
  const ACTION = {
    id: 'verify_bank', label: 'Check and verify', kind: 'safe' as const,
    endpoint: '/api/vendors/v1/verify-bank', method: 'POST', fields: [],
    preview: '', capability: 'verify', destructive: false, href: null,
    body: {}, confirm: null,
  }

  it('keeps the row when the endpoint only queued the work', async () => {
    // /gstins/primary/reverify resets the vendor and notifies the GST worker.
    // Its 200 means "asked", and the vendor is still unverified.
    await load([finding('a'), finding('b')])
    api.runAction.mockResolvedValue({ id: null, status: 'UNVERIFIED' })

    const res = await act(async () =>
      reviewActions.act(finding('a'), { ...ACTION, queued: 'Queued with the GST portal.' }))

    expect(ids()).toEqual(['a', 'b'])
    expect((await res)?.queued).toBe(true)
  })

  it('keeps the row when the response says the problem is still there', async () => {
    // 200 with persisted=false: the bank said yes but nothing was stored, so
    // the account on file is still unverified.
    await load([finding('a'), finding('b')])
    api.runAction.mockResolvedValue({ verified: true, persisted: false })

    // No act() wrapper: the call rejects, and a rejected render pass leaves
    // renderHook with nothing to read.
    await expect(
      reviewActions.act(finding('a'),
        { ...ACTION, success: { verified: true, persisted: true } }),
    ).rejects.toThrow(/still there/)

    expect(ids()).toEqual(['a', 'b'])
  })

  it('clears the row when the response confirms the fix', async () => {
    await load([finding('a'), finding('b')])
    api.runAction.mockResolvedValue({ verified: true, persisted: true })

    await act(async () => {
      await reviewActions.act(finding('a'),
        { ...ACTION, success: { verified: true, persisted: true } })
    })

    expect(ids()).toEqual(['b'])
  })
})

describe('clearing a finding', () => {
  it('takes the approved bill out of the list without touching the others', async () => {
    await load([finding('a'), finding('b'), finding('c')])
    api.runAction.mockResolvedValue({ status: 'ok', message: 'Approved.' })

    await act(async () => { await reviewActions.act(finding('b'), {
      id: 'approve_bill', label: 'Approve', kind: 'approve',
      endpoint: '/api/review/act/approve-bill', method: 'POST', fields: [],
      preview: '', capability: 'approve', destructive: false, href: null,
      body: {}, confirm: null,
    }) })

    expect(ids()).toEqual(['a', 'c'])
  })

  it('keeps a grouped card and drops its count by one', async () => {
    // The card stands for every finding of its type. Removing it because one
    // member was approved would take the other 24 off the screen.
    await load([finding('g', {
      group: { finding_type: 'approval_stalled', count: 25, money_at_risk_inr: 2500 },
    })])
    api.runAction.mockResolvedValue({ status: 'ok' })

    await act(async () => { await reviewActions.act(finding('g'), {
      id: 'approve_bill', label: 'Approve', kind: 'approve',
      endpoint: '/api/review/act/approve-bill', method: 'POST', fields: [],
      preview: '', capability: 'approve', destructive: false, href: null,
      body: {}, confirm: null,
    }) })

    const items = queue().items
    expect(items).toHaveLength(1)
    expect(items[0].group?.count).toBe(24)
    expect(items[0].group?.money_at_risk_inr).toBe(2400)
  })

  it.each([
    ['RESOLVED', true],
    ['DISMISSED', true],
    ['SNOOZED', true],
    ['ACKNOWLEDGED', false],
  ] as const)('%s leaves the queue: %s', async (status, shouldLeave) => {
    await load([finding('a'), finding('b')])
    api.setFindingStatus.mockResolvedValue({
      finding: finding('a', { status }), undo_token: null, undo_expires_in: null,
    })

    await act(async () => { await reviewActions.setStatus('a', status) })

    expect(ids()).toEqual(shouldLeave ? ['b'] : ['a', 'b'])
  })

  it('undo puts the row back where the user left it', async () => {
    await load([finding('a'), finding('b'), finding('c')])
    api.setFindingStatus.mockResolvedValue({
      finding: finding('b', { status: 'RESOLVED' }),
      undo_token: 'tok', undo_expires_in: 30,
    })
    await act(async () => { await reviewActions.setStatus('b', 'RESOLVED') })
    expect(ids()).toEqual(['a', 'c'])

    api.undoFinding.mockResolvedValue({ finding: finding('b', { status: 'OPEN' }) })
    await act(async () => { await reviewActions.undoLast() })

    // Back in the middle, not at the top: the user's place in the queue is the
    // thing undo is protecting.
    expect(ids()).toEqual(['a', 'b', 'c'])
  })
})
