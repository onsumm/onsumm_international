/**
 * Bulk-acting on a grouped queue row has to cover the whole group.
 *
 * The queue is grouped: a row reads "Bill has been waiting on the same
 * approval step × 16", "16 records", "See all 16" — and the finding_id it
 * carries is ONE representative. The bulk bar sent that single id, so
 * "1 selected → Snooze a week" snoozed 1 of the 16 and left the card sitting
 * there. Expansion re-queries each group under the queue's own filters, so
 * the count acted on is the count the card claims.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Finding } from '../../api/review'

const fetchFindings = vi.fn()

vi.mock('../../api/review', () => ({
  fetchFindings: (...a: unknown[]) => fetchFindings(...a),
  fetchFinding: vi.fn(),
  fetchCounts: vi.fn(),
  bulkStatus: vi.fn(),
  rescan: vi.fn(),
  runAction: vi.fn(),
  setFindingStatus: vi.fn(),
  undoFinding: vi.fn(),
}))

const { reviewActions } = await import('./useReviewStore')

const QUERY = { scope: 'actionable', status: 'open', group: true, limit: 12 } as const

function member(n: number, type = 'stale_approval'): Finding {
  return {
    finding_id: `m${type}-${n}`, finding_type: type, family: 'approval', lane: 'blocking',
    subject: { type: 'bill', id: `b${n}` }, severity: 'HIGH', confidence: 1,
    verification: 'VERIFIED', is_suspected: false, money_at_risk: null,
    money_currency: 'INR', money_at_risk_inr: null, due_at: null, rank: 1,
    title: 'Bill has been waiting on the same approval step', detail: '',
    evidence: {}, actions: [], deep_link: '', document: null, can_act: true,
    required_capability: 'approve', status: 'OPEN', status_reason: null,
    status_by: null, snoozed_until: null, detector: 'adapter:stale_approval',
    source_service: 'approvals', model: null, occurrence_count: 1,
    first_detected_at: '2026-09-18T02:24:00Z', last_seen_at: '2026-09-18T02:24:00Z',
    entities: [],
  }
}

/** A grouped card: one representative id, plus the size of what it stands for. */
function card(type: string, count: number): Finding {
  return { ...member(0, type), group: { finding_type: type, count, money_at_risk_inr: null } }
}

describe('a grouped row expands to every finding it stands for', () => {
  beforeEach(() => {
    reviewActions.reset()
    fetchFindings.mockReset()
  })

  it('turns one selected card into all 16 of its members', async () => {
    fetchFindings.mockResolvedValue({
      items: Array.from({ length: 16 }, (_, i) => member(i)), next_cursor: null,
    })

    const { ids, truncated } = await reviewActions.expandForBulk([card('stale_approval', 16)], QUERY)

    expect(ids).toHaveLength(16)
    expect(truncated).toBe(false)
    // Re-queried ungrouped, for that kind, under the queue's own filters —
    // not by-type across everything merely visible.
    const [arg] = fetchFindings.mock.calls[0]
    expect(arg).toMatchObject({
      group: false, finding_type: 'stale_approval', scope: 'actionable', status: 'open',
    })
  })

  it('pages through a group bigger than one page (the 224-member card)', async () => {
    fetchFindings
      .mockResolvedValueOnce({
        items: Array.from({ length: 200 }, (_, i) => member(i, 'vendor_no_bank')),
        next_cursor: 'page-2',
      })
      .mockResolvedValueOnce({
        items: Array.from({ length: 24 }, (_, i) => member(200 + i, 'vendor_no_bank')),
        next_cursor: null,
      })

    const { ids, truncated } = await reviewActions.expandForBulk(
      [card('vendor_no_bank', 224)], QUERY,
    )

    expect(ids).toHaveLength(224)
    expect(truncated).toBe(false)
    expect(fetchFindings.mock.calls[1][0]).toMatchObject({ cursor: 'page-2' })
  })

  it('caps at the endpoint limit and says so rather than silently dropping', async () => {
    // A group far past the 500-id cap: a fresh full page every time, so the
    // cap is what stops it rather than running out of members.
    let n = 0
    fetchFindings.mockImplementation(() => Promise.resolve({
      items: Array.from({ length: 200 }, () => member(n++, 'huge')),
      next_cursor: 'more',
    }))

    const { ids, truncated } = await reviewActions.expandForBulk([card('huge', 5000)], QUERY)

    expect(ids).toHaveLength(500)
    expect(truncated).toBe(true)
  })

  it('cannot spin forever when the server keeps returning the same page', async () => {
    // Found by this suite hanging: the loop is driven by the server's cursor,
    // and a cursor that does not advance added no new ids, never reached the
    // cap, and never stopped.
    const stuck = {
      items: Array.from({ length: 200 }, (_, i) => member(i, 'stuck')),
      next_cursor: 'never-advances',
    }
    fetchFindings.mockResolvedValue(stuck)

    const { ids } = await reviewActions.expandForBulk([card('stuck', 5000)], QUERY)

    expect(ids).toHaveLength(200)          // the one page it really got
    expect(fetchFindings.mock.calls.length).toBeLessThanOrEqual(4)
  })

  it('leaves an ungrouped row exactly as it is', async () => {
    const single = member(1)

    const { ids, truncated } = await reviewActions.expandForBulk([single], QUERY)

    expect(ids).toEqual([single.finding_id])
    expect(truncated).toBe(false)
    expect(fetchFindings).not.toHaveBeenCalled()  // nothing to expand
  })

  it('treats a group of one as itself, without a round trip', async () => {
    const { ids } = await reviewActions.expandForBulk([card('lonely', 1)], QUERY)

    expect(ids).toHaveLength(1)
    expect(fetchFindings).not.toHaveBeenCalled()
  })

  it('expands several selected cards and never repeats an id', async () => {
    fetchFindings
      .mockResolvedValueOnce({ items: [member(1, 'a'), member(2, 'a')], next_cursor: null })
      // The second group's page happens to include a finding already seen.
      .mockResolvedValueOnce({ items: [member(3, 'b'), member(1, 'a')], next_cursor: null })

    const { ids } = await reviewActions.expandForBulk(
      [card('a', 2), card('b', 2)], QUERY,
    )

    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toHaveLength(3)
  })
})
