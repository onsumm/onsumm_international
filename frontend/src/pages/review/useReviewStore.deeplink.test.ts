/**
 * A `?f=<id>` deep link must open the drawer for a finding that is nowhere on
 * the queue's first page.
 *
 * ReviewHubPage fires loadQueue and openFinding from two separate effects on
 * the same render, so they race. The list query is the slow one (grouped, over
 * every open finding); the fetch by id is fast. loadQueue then replaced `byId`
 * wholesale, so the finding openFinding had just put there was evicted before
 * the drawer could read it — a deep link only worked for rows that happened to
 * land on page one, or after enough "Load more" clicks to pull the real row in.
 *
 * Read through useFinding, which is the hook the drawer itself uses.
 */
import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const fetchFindings = vi.fn()
const fetchFinding = vi.fn()

vi.mock('../../api/review', () => ({
  fetchFindings: (...a: unknown[]) => fetchFindings(...a),
  fetchFinding: (...a: unknown[]) => fetchFinding(...a),
  fetchCounts: vi.fn(),
  bulkStatus: vi.fn(),
  rescan: vi.fn(),
  runAction: vi.fn(),
  setFindingStatus: vi.fn(),
  undoFinding: vi.fn(),
}))

const { reviewActions, useFinding } = await import('./useReviewStore')

const DEEP_ID = '50b4161a-db45-4e7a-b602-e2ee652cddc9'
const QUERY = { scope: 'actionable', status: 'open', group: true } as const

function finding(id: string, type = 'payment_term_conflict') {
  return {
    finding_id: id, finding_type: type, family: 'vendor_identity', lane: 'compliance',
    subject: { type: 'vendor', id: '04a74f0a-5ddd-4e22-ae33-ad7aaa000000' },
    severity: 'MEDIUM', confidence: 1, verification: 'VERIFIED', is_suspected: false,
    title: 'A vendor contract states a different payment term than what is on file',
    detail: '', status: 'OPEN', actions: [], entities: [],
  }
}

/** What the drawer would see for this id, right now. */
const seen = (id: string) => renderHook(() => useFinding(id)).result.current

describe('a deep-linked finding survives a concurrent queue load', () => {
  beforeEach(() => {
    reviewActions.reset()
    fetchFindings.mockReset()
    fetchFinding.mockReset()
  })

  it('is still there when the slow list query lands after the fetch by id', async () => {
    // The list resolves LAST and does not contain the deep-linked finding —
    // the real case: 635 findings, this one ranked 0.29, far off page one.
    fetchFindings.mockImplementation(
      () => new Promise(res => setTimeout(() => res({
        items: [finding('aaaaaaaa-0000-0000-0000-000000000001', 'stale_approval'),
                finding('bbbbbbbb-0000-0000-0000-000000000002', 'vendor_no_bank')],
        next_cursor: 'cursor-1',
      }), 20)),
    )
    fetchFinding.mockResolvedValue(finding(DEEP_ID))

    // Same firing order as ReviewHubPage's two effects.
    const queue = reviewActions.loadQueue(QUERY)
    const open = reviewActions.openFinding(DEEP_ID)
    await Promise.all([queue, open])

    expect(seen(DEEP_ID)?.finding_id).toBe(DEEP_ID)
  })

  it('is still there in the other race order (list first, then fetch by id)', async () => {
    fetchFindings.mockResolvedValue({
      items: [finding('aaaaaaaa-0000-0000-0000-000000000001', 'stale_approval')],
      next_cursor: null,
    })
    fetchFinding.mockResolvedValue(finding(DEEP_ID))

    await reviewActions.loadQueue(QUERY)
    await reviewActions.openFinding(DEEP_ID)

    expect(seen(DEEP_ID)?.finding_id).toBe(DEEP_ID)
  })

  it('survives a later queue reload (lane switch, poll, brand-unrelated requery)', async () => {
    fetchFindings.mockResolvedValue({ items: [], next_cursor: null })
    fetchFinding.mockResolvedValue(finding(DEEP_ID))

    await reviewActions.openFinding(DEEP_ID)
    await reviewActions.loadQueue(QUERY)
    await reviewActions.loadQueue({ ...QUERY, scope: 'visible' })

    expect(seen(DEEP_ID)?.finding_id).toBe(DEEP_ID)
  })

  it('does not keep the queue list itself growing across reloads', async () => {
    fetchFindings.mockResolvedValue({
      items: [finding('cccccccc-0000-0000-0000-000000000003', 'other')],
      next_cursor: null,
    })
    await reviewActions.loadQueue(QUERY)
    fetchFindings.mockResolvedValue({ items: [], next_cursor: null })
    await reviewActions.loadQueue(QUERY)

    // Nobody opened this one, so the second load evicts it as before.
    expect(seen('cccccccc-0000-0000-0000-000000000003')).toBeUndefined()
  })

  it('reset still clears it, so brand switching cannot leak findings', async () => {
    fetchFinding.mockResolvedValue(finding(DEEP_ID))
    await reviewActions.openFinding(DEEP_ID)
    expect(seen(DEEP_ID)).toBeDefined()

    reviewActions.reset()
    expect(seen(DEEP_ID)).toBeUndefined()
  })
})
