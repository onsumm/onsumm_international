/**
 * The Review Hub's KPI strip.
 *
 * Four tiles, built to the same spec as the Reports page's own (.rp__kpi):
 * hairline in the tile's tone, corner bloom from the same colour, label in
 * small caps, number in display type. The one departure is that the number
 * itself stays --ink-900 rather than taking the tone. Reports tints its
 * values because each tile there *is* the metric; here the tone belongs to
 * the qualifier underneath — "blocked from payment", "past due" — which is
 * the part that changes behaviour, and four differently coloured numbers in
 * a row would spend the page's accent budget before the queue below it gets
 * any (see the restraint note at the top of review.css).
 *
 * Every number arrives from GET /api/review/counts, which is the same call the
 * nav badge and the lane row read. One source, so the strip can never disagree
 * with the queue underneath it.
 */
import type { ReactNode } from 'react'

import type { ReviewCounts } from '../../api/review'
import { formatMoney } from '../../ui'
import {
  blockedLine, completedLine, dueLine, formatHours, hoursSaved,
} from './reviewFormat'

function Kpi({ label, value, unit, sub, tone }: {
  label: string
  value: ReactNode
  unit?: string
  sub?: string | null
  tone: string
}) {
  return (
    <div className={`rh__kpi ${tone}`}>
      <div className="rh__kpi-lbl">{label}</div>
      <div className="rh__kpi-line">
        <span className="rh__kpi-val tnum">{value}</span>
        {unit && <span className="rh__kpi-unit">{unit}</span>}
      </div>
      {/* Reserved whether or not there is a line to put in it: without this the
          tiles change height as the numbers move, and the whole queue below
          them shifts on a 45-second counts poll. */}
      <div className="rh__kpi-sub">{sub ?? '\u00a0'}</div>
    </div>
  )
}

export function KpiStrip({ counts }: { counts: ReviewCounts | null }) {
  if (!counts) {
    return (
      <div className="rh__kpis" aria-hidden="true">
        {[0, 1, 2, 3].map(i => <div key={i} className="rh__kpi rh__kpi--skel" />)}
      </div>
    )
  }

  const closed = counts.resolved_this_month ?? 0
  const accepted = counts.accepted_this_month ?? 0
  const today = counts.resolved_today ?? 0

  return (
    <div className="rh__kpis">
      <Kpi
        label="Under review"
        tone="rh-t--danger"
        value={formatMoney(counts.money_at_risk_inr)}
        sub={blockedLine(counts.lanes?.blocking?.money_inr ?? 0, formatMoney)}
      />
      <Kpi
        label="To do"
        tone="rh-t--warn"
        value={counts.total_actionable}
        /* Not "assigned to you": nothing in the hub is assigned. This is the
           set your capabilities let you actually close. */
        unit={counts.total_actionable === 1 ? 'task you can act on' : 'tasks you can act on'}
        sub={dueLine(counts.due_soon ?? 0, counts.overdue ?? 0, counts.total_actionable)}
      />
      <Kpi
        label="Completed"
        tone="rh-t--success"
        value={closed}
        unit="this month"
        sub={completedLine(today, accepted, closed)}
      />
      <Kpi
        label="Estimated hours saved"
        tone="rh-t--brand"
        value={<>{formatHours(hoursSaved(closed))}<span className="rh__kpi-suffix">h</span></>}
        unit="this month"
        sub="Via Onsumm"
      />
    </div>
  )
}
