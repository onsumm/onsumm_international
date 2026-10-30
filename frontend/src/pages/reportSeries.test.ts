/**
 * The dashboard's claims are arithmetic, and a wrong average approval time is
 * worse than none — it gets quoted in a board meeting. These lock the edges the
 * bill list actually produces: missing timestamps, part payments, bills approved
 * after they were already due, and a baseline of zero.
 */
import { describe, it, expect } from 'vitest'
import {
  buildBuckets,
  bucketOf,
  buildPeriods,
  categoryShares,
  activeRoles,
  indexActivity,
  movingAverage,
  delta,
  startOfWeek,
  type SeriesBill,
} from './reportSeries'

const NOW = new Date('2026-08-07T10:00:00')

let seq = 0
const bill = (b: Partial<SeriesBill>): SeriesBill => ({
  invoice_id: `b${++seq}`,
  status: 'APPROVED',
  category: 'Software / SaaS',
  total_amount: 1000,
  amount_paid: null,
  outstanding_amount: null,
  payment_status: 'unpaid',
  payment_updated_at: null,
  invoice_date: null,
  created_at: null,
  due_date: null,
  ...b,
})

/** The activity trail for one bill, as `/api/reports/activity` returns it. */
const act = (
  b: SeriesBill,
  approved_at: string | null,
  extra: { send_backs?: number; role_days?: Record<string, number> } = {},
) =>
  indexActivity([
    { invoice_id: b.invoice_id, approved_at, send_backs: extra.send_backs ?? 0, role_days: extra.role_days ?? {} },
  ])

describe('buckets', () => {
  it('ends on the period containing today, oldest first', () => {
    const m = buildBuckets('month', 6, NOW)
    expect(m).toHaveLength(6)
    expect(m.map(b => b.label)).toEqual(['Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug'])
    expect(new Date(m[5].start).getMonth()).toBe(7) // August
  })

  it('carries the year on a January tick so a 12-month axis is unambiguous', () => {
    const labels = buildBuckets('month', 12, NOW).map(b => b.label)
    expect(labels[0]).toBe('Sep')
    expect(labels).toContain("Jan '26")
  })

  it('cuts weeks on Monday', () => {
    // 2026-08-07 is a Friday; its week starts Monday 2026-08-03.
    expect(startOfWeek(NOW).getDate()).toBe(3)
    const w = buildBuckets('week', 3, NOW)
    expect(w.map(b => b.label)).toEqual(['20 Jul', '27 Jul', '3 Aug'])
    expect(w[2].end - w[2].start).toBe(7 * 86_400_000)
  })

  it('places a timestamp in exactly one bucket and rejects anything outside', () => {
    const b = buildBuckets('month', 3, NOW)
    expect(bucketOf(b, new Date('2026-06-15').getTime())).toBe(0)
    expect(bucketOf(b, b[1].start)).toBe(1) // inclusive lower edge
    expect(bucketOf(b, b[1].end)).toBe(2) // exclusive upper edge
    expect(bucketOf(b, new Date('2020-01-01').getTime())).toBe(-1)
    expect(bucketOf(b, null)).toBe(-1)
  })
})

describe('spend', () => {
  it('counts approved bills on their invoice date, not the upload date', () => {
    const p = buildPeriods(
      [bill({ invoice_date: '2026-06-20', created_at: '2026-08-01', total_amount: 500 })],
      'month',
      3,
      NOW,
    )
    expect(p[0].spend).toBe(500) // June
    expect(p[2].spend).toBe(0) // not August
  })

  it('falls back to the upload date when the invoice has none', () => {
    const p = buildPeriods([bill({ invoice_date: null, created_at: '2026-08-02' })], 'month', 3, NOW)
    expect(p[2].spend).toBe(1000)
  })

  it('ignores bills that are not approved', () => {
    const p = buildPeriods([bill({ status: 'PENDING_FC', invoice_date: '2026-08-02' })], 'month', 3, NOW)
    expect(p[2].spend).toBe(0)
    expect(p[2].billCount).toBe(0)
  })
})

describe('settlement', () => {
  it('splits a part payment by rupee, so the bands add back to what fell due', () => {
    const p = buildPeriods(
      [
        bill({
          due_date: '2026-06-10',
          total_amount: 1000,
          amount_paid: 400,
          outstanding_amount: 600,
          payment_status: 'partial',
          payment_updated_at: '2026-06-05',
        }),
      ],
      'month',
      3,
      NOW,
    )
    const j = p[0]
    expect(j.dueAmt).toBe(1000)
    expect(j.onTimeAmt).toBe(400)
    expect(j.overdueAmt).toBe(600)
    expect(j.onTimeAmt + j.lateAmt + j.overdueAmt + j.openAmt + j.untimedAmt).toBe(j.dueAmt)
  })

  it('calls a payment on the due date on time, and the day after late', () => {
    const on = buildPeriods(
      [bill({ due_date: '2026-06-10', payment_status: 'paid', payment_updated_at: '2026-06-10T23:00:00' })],
      'month',
      3,
      NOW,
    )
    expect(on[0].onTimeAmt).toBe(1000)
    expect(on[0].onTimePct).toBe(100)

    const late = buildPeriods(
      [bill({ due_date: '2026-06-10', payment_status: 'paid', payment_updated_at: '2026-06-11T01:00:00' })],
      'month',
      3,
      NOW,
    )
    expect(late[0].lateAmt).toBe(1000)
    expect(late[0].onTimePct).toBe(0)
  })

  it('keeps an untimed settlement out of the on-time rate rather than guessing', () => {
    const p = buildPeriods(
      [bill({ due_date: '2026-06-10', payment_status: 'paid', payment_updated_at: null })],
      'month',
      3,
      NOW,
    )
    expect(p[0].untimedAmt).toBe(1000)
    expect(p[0].onTimePct).toBeNull()
  })

  it('separates still-overdue from not-yet-due using today', () => {
    const p = buildPeriods(
      [
        bill({ due_date: '2026-06-10', total_amount: 700 }), // past
        bill({ due_date: '2026-08-28', total_amount: 300 }), // future, same window
      ],
      'month',
      3,
      NOW,
    )
    expect(p[0].overdueAmt).toBe(700)
    expect(p[2].openAmt).toBe(300)
    expect(p[2].onTimePct).toBeNull()
  })
})

describe('turnaround, from the activity trail', () => {
  it('averages upload → approval in days, bucketed by when approval happened', () => {
    const a = bill({ created_at: '2026-08-01T00:00:00' })
    const b = bill({ created_at: '2026-08-01T00:00:00' })
    const p = buildPeriods([a, b], 'month', 3, NOW, {
      ...act(a, '2026-08-03T00:00:00'),
      ...act(b, '2026-08-05T00:00:00'),
    })
    expect(p[2].approvedCount).toBe(2)
    expect(p[2].avgApprovalDays).toBe(3) // (2 + 4) / 2
  })

  it('drops a chain row backdated before the upload instead of reporting negative latency', () => {
    const b = bill({ created_at: '2026-08-05T00:00:00' })
    const p = buildPeriods([b], 'month', 3, NOW, act(b, '2026-08-01T00:00:00'))
    expect(p[2].approvedCount).toBe(1)
    expect(p[2].avgApprovalDays).toBeNull()
  })

  it('reports a negative buffer when a bill was approved after it was already due', () => {
    const b = bill({ due_date: '2026-08-01' })
    const p = buildPeriods([b], 'month', 3, NOW, act(b, '2026-08-05'))
    expect(p[2].avgBufferDays).toBe(-4)
    expect(p[2].lateApprovals).toBe(1)
  })

  it('has nothing to say about a period with no approvals', () => {
    const p = buildPeriods([], 'month', 3, NOW)
    expect(p[1].avgApprovalDays).toBeNull()
    expect(p[1].avgBufferDays).toBeNull()
    expect(p[1].reworkPct).toBeNull()
  })

  it('ignores a bill still in the chain, whose last approve is only intermediate', () => {
    const b = bill({ status: 'PENDING_FC', created_at: '2026-08-01' })
    const p = buildPeriods([b], 'month', 3, NOW, act(b, '2026-08-03'))
    expect(p[2].approvedCount).toBe(0)
  })

  it('reports spend but no timing when no activity is supplied at all', () => {
    const p = buildPeriods([bill({ created_at: '2026-08-01', invoice_date: '2026-08-01' })], 'month', 3, NOW)
    expect(p[2].spend).toBe(1000)
    expect(p[2].approvedCount).toBe(0)
    expect(p[2].avgApprovalDays).toBeNull()
  })
})

describe('per-desk waits and rework', () => {
  it('divides each desk total by the period approvals, so the stack sums to turnaround', () => {
    const a = bill({ created_at: '2026-08-01T00:00:00' })
    const b = bill({ created_at: '2026-08-01T00:00:00' })
    const p = buildPeriods([a, b], 'month', 3, NOW, {
      ...act(a, '2026-08-03T00:00:00', { role_days: { Approver: 1, Accountant: 1 } }),
      ...act(b, '2026-08-05T00:00:00', { role_days: { Approver: 3, Accountant: 1 } }),
    })
    expect(p[2].avgRoleDays).toEqual({ Approver: 2, Accountant: 1 })
    const stacked = Object.values(p[2].avgRoleDays).reduce((s, v) => s + v, 0)
    expect(stacked).toBeCloseTo(p[2].avgApprovalDays!)
  })

  it('counts a bill as reworked once however many times it was sent back', () => {
    const a = bill({ created_at: '2026-08-01' })
    const b = bill({ created_at: '2026-08-01' })
    const p = buildPeriods([a, b], 'month', 3, NOW, {
      ...act(a, '2026-08-03', { send_backs: 3 }),
      ...act(b, '2026-08-04'),
    })
    expect(p[2].reworked).toBe(1)
    expect(p[2].reworkPct).toBe(50)
  })

  it('ranks desks over the whole window so a stage keeps its stacking position', () => {
    const a = bill({ created_at: '2026-07-01' })
    const b = bill({ created_at: '2026-08-01' })
    const p = buildPeriods([a, b], 'month', 3, NOW, {
      ...act(a, '2026-07-03', { role_days: { FC: 0.5, Approver: 2 } }),
      ...act(b, '2026-08-03', { role_days: { Approver: 1 } }),
    })
    expect(activeRoles(p)).toEqual(['Approver', 'FC'])
  })

  it('has no desks to report when nothing was approved', () => {
    expect(activeRoles(buildPeriods([], 'month', 3, NOW))).toEqual([])
  })
})

describe('category mix', () => {
  const mixed = () =>
    buildPeriods(
      [
        bill({ invoice_date: '2026-08-02', category: 'Rent', total_amount: 600 }),
        bill({ invoice_date: '2026-08-03', category: 'IT', total_amount: 400 }),
        bill({ invoice_date: '2026-07-02', category: 'Rent', total_amount: 1000 }),
      ],
      'month',
      3,
      NOW,
    )

  it('expresses each period as shares that sum to 100 where there was spend', () => {
    const s = categoryShares(mixed())
    const aug = s.map(x => x.values[2])
    expect(aug.reduce((a, b) => a + b, 0)).toBeCloseTo(100)
    expect(s.find(x => x.label === 'Rent')!.values[2]).toBe(60)
    expect(s.find(x => x.label === 'IT')!.values[2]).toBe(40)
  })

  it('leaves an empty period at zero rather than dividing by it', () => {
    const s = categoryShares(mixed())
    expect(s.every(x => x.values[0] === 0)).toBe(true)
  })

  it('keeps band order stable across periods by ranking on the whole window', () => {
    const s = categoryShares(mixed())
    expect(s.map(x => x.label)).toEqual(['Rent', 'IT'])
  })

  it('rolls a long tail into one Others band so columns still total 100', () => {
    const many = Array.from({ length: 9 }, (_, i) =>
      bill({ invoice_date: '2026-08-02', category: `C${i}`, total_amount: 100 * (9 - i) }),
    )
    const s = categoryShares(buildPeriods(many, 'month', 3, NOW))
    expect(s).toHaveLength(6)
    expect(s[5].label).toBe('Others (4)')
    expect(s.reduce((a, x) => a + x.values[2], 0)).toBeCloseTo(100)
  })

  it('says nothing when there is no spend at all', () => {
    expect(categoryShares(buildPeriods([], 'month', 3, NOW))).toEqual([])
  })
})

describe('trend helpers', () => {
  it('averages over a trailing window that shortens at the start', () => {
    expect(movingAverage([3, 6, 9, 12], 3)).toEqual([3, 4.5, 6, 9])
  })

  it('refuses a percentage change against a zero baseline', () => {
    expect(delta([0, 50]).pct).toBeNull()
    expect(delta([50, 0]).pct).toBe(-100)
    expect(delta([40, 50]).pct).toBeCloseTo(25)
    expect(delta([-2, -4]).pct).toBeCloseTo(-100)
  })
})
