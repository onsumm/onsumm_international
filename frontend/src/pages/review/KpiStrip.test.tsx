/**
 * The KPI strip.
 *
 * Two things are worth pinning down here and nowhere else: that a tile with a
 * zero behind it says something rather than printing a nought, and that the
 * strip survives a counts payload from a backend that predates it — a frontend
 * deploy lands first often enough that "renders NaN for a day" is a real
 * outcome, not a hypothetical one.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ReviewCounts } from '../../api/review'
import { KpiStrip } from './KpiStrip'

function counts(over: Partial<ReviewCounts> = {}): ReviewCounts {
  return {
    lanes: { blocking: { actionable: 3, visible: 3, money_inr: 421900 } },
    by_severity: {},
    total_actionable: 12,
    total_visible: 48,
    money_at_risk_inr: 1842350,
    blocking: 3,
    snoozed: 0,
    resolved_this_week: 31,
    protected_this_week_inr: 0,
    resolved_this_month: 131,
    resolved_today: 14,
    accepted_this_month: 119,
    due_soon: 7,
    overdue: 2,
    last_scan_at: new Date().toISOString(),
    my_capabilities: ['edit'],
    role: 'admin',
    ...over,
  }
}

describe('KpiStrip', () => {
  it('states exposure, workload, throughput and value', () => {
    render(<KpiStrip counts={counts()} />)

    expect(screen.getByText('₹18,42,350')).toBeInTheDocument()
    expect(screen.getByText('₹4,21,900 blocked from payment')).toBeInTheDocument()

    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('tasks you can act on')).toBeInTheDocument()
    expect(screen.getByText('7 due within 3 days, 2 past due')).toBeInTheDocument()

    expect(screen.getByText('131')).toBeInTheDocument()
    expect(screen.getByText('14 today, 91% accepted as proposed')).toBeInTheDocument()

    // 131 × 17 minutes ≈ 37h, credited to Onsumm.
    expect(screen.getByText('37')).toBeInTheDocument()
    expect(screen.getByText('Via Onsumm')).toBeInTheDocument()
  })

  it('says what a zero means instead of showing a row of noughts', () => {
    render(<KpiStrip counts={counts({
      lanes: { blocking: { actionable: 0, visible: 0, money_inr: 0 } },
      total_actionable: 0, due_soon: 0, overdue: 0,
      resolved_this_month: 0, resolved_today: 0, accepted_this_month: 0,
    })} />)

    expect(screen.queryByText(/blocked from payment/)).not.toBeInTheDocument()
    expect(screen.getByText('Your queue is clear')).toBeInTheDocument()
    expect(screen.getByText('Nothing closed yet this month')).toBeInTheDocument()
  })

  it('falls back rather than rendering NaN against an older backend', () => {
    const stale = counts()
    delete stale.resolved_this_month
    delete stale.resolved_today
    delete stale.accepted_this_month
    delete stale.due_soon
    delete stale.overdue

    render(<KpiStrip counts={stale} />)

    expect(screen.getByText('₹18,42,350')).toBeInTheDocument()
    expect(screen.getByText('Nothing closed yet this month')).toBeInTheDocument()
    expect(screen.getByText('None due in the next 3 days')).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })

  it('holds the row open while the counts are still in flight', () => {
    const { container } = render(<KpiStrip counts={null} />)
    expect(container.querySelectorAll('.rh__kpi--skel')).toHaveLength(4)
  })
})
