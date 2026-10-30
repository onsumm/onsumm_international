/**
 * Un-snoozing a selection.
 *
 * A snooze is the one triage decision the hub could make but not take back in
 * bulk. The Snoozed tab listed 636 findings and offered "Snooze a week" —
 * the action already taken — so the only way to pull something back early was
 * to open each one and reopen it by hand.
 *
 * OPEN is that reverse: the server clears snoozed_until for every non-SNOOZED
 * status, so the interesting part on this side is that the cached finding
 * stops claiming to be asleep at the same moment its status changes.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Finding } from '../../api/review'

const fetchFindings = vi.fn()
const bulkStatus = vi.fn()

vi.mock('../../api/review', () => ({
  fetchFindings: (...a: unknown[]) => fetchFindings(...a),
  bulkStatus: (...a: unknown[]) => bulkStatus(...a),
  fetchFinding: vi.fn(),
  fetchCounts: vi.fn().mockResolvedValue(undefined),
  rescan: vi.fn(),
  runAction: vi.fn(),
  setFindingStatus: vi.fn(),
  undoFinding: vi.fn(),
}))

const { reviewActions, useFinding } = await import('./useReviewStore')

/** The store has no getState; the hooks are the read side. */
const read = (id: string) => renderHook(() => useFinding(id)).result.current

const SNOOZED_QUERY = { scope: 'visible', status: 'snoozed', group: true, limit: 12 } as const

function snoozed(n: number): Finding {
  return {
    finding_id: `s-${n}`, finding_type: 'stale_approval', family: 'approval',
    lane: 'blocking', subject: { type: 'bill', id: `b${n}` }, severity: 'HIGH',
    confidence: 1, verification: 'VERIFIED', is_suspected: false, money_at_risk: null,
    money_currency: 'INR', money_at_risk_inr: null, due_at: null, rank: 1,
    title: 'Bill has been waiting on the same approval step', detail: '',
    evidence: {}, actions: [], deep_link: '', document: null, can_act: true,
    required_capability: 'approve', status: 'SNOOZED', status_reason: null,
    status_by: 'sumit', snoozed_until: '2026-09-26T02:24:00Z',
    detector: 'adapter:stale_approval', source_service: 'approvals', model: null,
    occurrence_count: 1, first_detected_at: '2026-09-18T02:24:00Z',
    last_seen_at: '2026-09-18T02:24:00Z', entities: [],
  }
}

describe('un-snoozing a selection', () => {
  beforeEach(() => {
    reviewActions.reset()
    fetchFindings.mockReset()
    bulkStatus.mockReset()
  })

  it('clears the wake-up time, not just the status', async () => {
    fetchFindings.mockResolvedValue({ items: [snoozed(1), snoozed(2)], next_cursor: null })
    await reviewActions.loadQueue(SNOOZED_QUERY)

    bulkStatus.mockResolvedValue({
      updated: ['s-1'], failed: [], counts: { updated: 1, failed: 0 },
    })
    await reviewActions.bulk({ finding_ids: ['s-1'], status: 'OPEN' })

    const back = read('s-1')
    expect(back?.status).toBe('OPEN')
    // The whole point: a row still carrying "snoozed until Friday" renders as
    // asleep even though it is back in the queue.
    expect(back?.snoozed_until).toBeNull()

    // Untouched members keep theirs.
    expect(read('s-2')?.snoozed_until).toBe('2026-09-26T02:24:00Z')
  })

  it('leaves the wake-up time alone when the batch really is being snoozed', async () => {
    fetchFindings.mockResolvedValue({ items: [snoozed(1)], next_cursor: null })
    await reviewActions.loadQueue(SNOOZED_QUERY)

    bulkStatus.mockResolvedValue({
      updated: ['s-1'], failed: [], counts: { updated: 1, failed: 0 },
    })
    await reviewActions.bulk({ finding_ids: ['s-1'], status: 'SNOOZED', snooze_days: 7 })

    expect(read('s-1')?.snoozed_until).toBe('2026-09-26T02:24:00Z')
  })

  it('expands a grouped snoozed card under the Snoozed tab’s own filters', async () => {
    // Not the open queue's: "× 25" on the Snoozed tab counts 25 snoozed
    // findings, and un-snoozing has to act on exactly those.
    fetchFindings.mockResolvedValue({
      items: Array.from({ length: 25 }, (_, i) => snoozed(i)), next_cursor: null,
    })

    const card: Finding = {
      ...snoozed(0),
      group: { finding_type: 'stale_approval', count: 25, money_at_risk_inr: null },
    }
    const { ids } = await reviewActions.expandForBulk([card], SNOOZED_QUERY)

    expect(ids).toHaveLength(25)
    expect(fetchFindings.mock.calls[0][0]).toMatchObject({
      group: false, finding_type: 'stale_approval', status: 'snoozed',
    })
  })
})
