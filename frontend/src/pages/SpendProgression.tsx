/**
 * Spend progression — how the operation is actually trending.
 *
 * A total tells a CFO what they already know. This panel answers the question a
 * ledger cannot: is it getting better or worse? Five progressions on one shared
 * time axis, switchable between monthly and weekly, each figure carrying its
 * change against the previous period rather than standing alone:
 *
 *   1. Spend            — what we committed to.
 *   2. Settlement       — of what fell due, how much was paid on time, late, or
 *                         is still outstanding, plus the on-time rate.
 *   3. Turnaround       — upload → final approval, stacked by the desk that held
 *                         it, with the share of bills that needed rework.
 *   4. Payment buffer   — days left to pay once approved. Below zero means the
 *                         bill was approved after it was already due.
 *   5. Category mix     — share of spend per category, per period.
 *
 * Timing (3 and 4) comes from the activity trail — the same `bill_approval_chain`
 * events the per-bill "View activity" timeline renders — aggregated server-side
 * by /api/reports/activity, so nothing is denormalised onto the bill row and the
 * chart can never disagree with the timeline a user opens to check it.
 *
 * Rendered inside `.rp .rp--embed`, which drops the Reports page shell (full
 * height, own scroller, canvas background) while keeping the scoped tokens the
 * marks colour themselves from.
 */
import { useEffect, useMemo, useState } from 'react'
import { Info } from 'lucide-react'
import { fetchActivityReport, type ActivityRow, type Bill } from '../api/invoices'
import { ChartKeys, ColumnChart, DeltaChip, Empty, Segmented, TrendKpi } from './reportCharts'
import { cx, fmt, fmtC } from './reportFormat'
import {
  activeRoles,
  buildPeriods,
  categoryShares,
  delta,
  indexActivity,
  movingAverage,
  type Grain,
} from './reportSeries'
import './reports.css'

export default function SpendProgression({ bills, companyId }: { bills: Bill[]; companyId?: string }) {
  const [grain, setGrain] = useState<Grain>('month')
  const [activity, setActivity] = useState<ActivityRow[]>([])
  const [activityFailed, setActivityFailed] = useState(false)

  useEffect(() => {
    setActivityFailed(false)
    fetchActivityReport(companyId)
      .then(setActivity)
      .catch(() => setActivityFailed(true))
  }, [companyId])

  // 12 months, or a quarter of weeks — enough to see a trend, few enough that
  // every column keeps a readable width.
  const periodCount = grain === 'month' ? 12 : 13
  const activityIndex = useMemo(() => indexActivity(activity), [activity])
  const periods = useMemo(
    () => buildPeriods(bills, grain, periodCount, new Date(), activityIndex),
    [bills, grain, periodCount, activityIndex],
  )
  const buckets = periods.map(p => p.bucket)
  const grainWord = grain === 'month' ? 'month' : 'week'
  const last = periods[periods.length - 1]

  const spendVals = periods.map(p => p.spend)
  const shares = categoryShares(periods)
  const hasUntimed = periods.some(p => p.untimedAmt > 0)
  // Nothing approved in the window and an unreadable trail are different
  // problems, so they get different messages.
  const hasTiming = periods.some(p => p.avgApprovalDays != null || p.avgBufferDays != null)
  const roles = activeRoles(periods)

  return (
    <div className="rp rp--embed">
      <div className="rp__toolbar">
        <Segmented
          label="Reporting period"
          value={grain}
          onChange={setGrain}
          options={[
            { value: 'month' as Grain, label: 'Monthly' },
            { value: 'week' as Grain, label: 'Weekly' },
          ]}
        />
        <div className="rp__toolbar-note">
          Last {periodCount} {grainWord}s · every figure below is per {grainWord}, against the
          previous one. The final column is the {grainWord} in progress.
        </div>
      </div>

      {/* Five progressions, not five totals: each answers "better or
          worse than last period?" — the question a total cannot. */}
      <div className="rp__kpis">
        <TrendKpi
          label={`Spend this ${grainWord}`}
          value={fmt(last?.spend ?? 0)}
          sub={`${last?.billCount ?? 0} bills`}
          tone="rp-t--brand"
          series={spendVals}
          good="neutral"
        />
        <TrendKpi
          label="Fell overdue"
          value={fmt(last?.overdueAmt ?? 0)}
          sub={`of ${fmtC(last?.dueAmt ?? 0)} due`}
          tone="rp-t--danger"
          series={periods.map(p => p.overdueAmt)}
          good="down"
        />
        <TrendKpi
          label="Settled on time"
          value={last?.onTimePct != null ? `${last.onTimePct.toFixed(0)}%` : '—'}
          sub="of what fell due"
          tone="rp-t--success"
          series={periods.map(p => p.onTimePct)}
          good="up"
        />
        <TrendKpi
          label="Approval turnaround"
          value={last?.avgApprovalDays != null ? `${last.avgApprovalDays.toFixed(1)}d` : '—'}
          sub={`${last?.approvedCount ?? 0} approved`}
          tone="rp-t--info"
          series={periods.map(p => p.avgApprovalDays)}
          good="down"
        />
        <TrendKpi
          label="Buffer before due"
          value={last?.avgBufferDays != null ? `${last.avgBufferDays.toFixed(1)}d` : '—'}
          sub={
            last && last.lateApprovals > 0
              ? `${last.lateApprovals} approved late`
              : 'after final approval'
          }
          tone="rp-t--violet"
          series={periods.map(p => p.avgBufferDays)}
          good="up"
        />
      </div>

      {/* 1 — Spend */}
      <div className="rp__card">
        <div className="rp__card-hdr">
          <div>
            <div className="rp__card-title">Spend by {grainWord}</div>
            <div className="rp__card-sub">
              Approved bills, by invoice date · line is the trailing 3-{grainWord} average
            </div>
          </div>
          <DeltaChip
            pct={delta(spendVals).pct}
            good="neutral"
            big
            label={`vs last ${grainWord}`}
          />
        </div>
        <ColumnChart
          buckets={buckets}
          unit="inr"
          height={230}
          bands={[{ label: 'Approved spend', cls: 'rp-t--brand', values: spendVals }]}
          overlay={{
            label: `Trailing 3-${grainWord} average`,
            cls: 'rp-t--warn',
            values: movingAverage(spendVals, 3),
            unit: 'inr',
            axis: 'left',
          }}
        />
        <ChartKeys
          items={[
            { label: 'Approved spend', cls: 'rp-t--brand' },
            { label: `Trailing 3-${grainWord} average`, cls: 'rp-t--warn', line: true },
          ]}
        />
      </div>

      {/* 2 — Settlement of what fell due */}
      <div className="rp__card">
        <div className="rp__card-hdr">
          <div>
            <div className="rp__card-title">Overdue and settlement by {grainWord}</div>
            <div className="rp__card-sub">
              Every rupee that fell due in the {grainWord}, by how it was actually settled
            </div>
          </div>
        </div>
        <ColumnChart
          buckets={buckets}
          unit="inr"
          height={240}
          bands={[
            { label: 'Settled on time', cls: 'rp-t--success', values: periods.map(p => p.onTimeAmt) },
            { label: 'Settled late', cls: 'rp-t--warn', values: periods.map(p => p.lateAmt) },
            { label: 'Still overdue', cls: 'rp-t--danger', values: periods.map(p => p.overdueAmt) },
            ...(hasUntimed
              ? [
                  {
                    label: 'Settled, date unknown',
                    cls: 'rp-c5',
                    values: periods.map(p => p.untimedAmt),
                  },
                ]
              : []),
            { label: 'Not yet due', cls: 'rp-t--muted', values: periods.map(p => p.openAmt) },
          ]}
          overlay={{
            label: 'Paid on time',
            cls: 'rp-t--info',
            values: periods.map(p => p.onTimePct),
            unit: 'pct',
            axis: 'right',
          }}
        />
        <ChartKeys
          items={[
            { label: 'Settled on time', cls: 'rp-t--success' },
            { label: 'Settled late', cls: 'rp-t--warn' },
            { label: 'Still overdue', cls: 'rp-t--danger' },
            ...(hasUntimed ? [{ label: 'Settled, date unknown', cls: 'rp-c5' }] : []),
            { label: 'Not yet due', cls: 'rp-t--muted' },
            { label: 'Paid on time (right axis)', cls: 'rp-t--info', line: true },
          ]}
        />
      </div>

      {/* 3 & 4 — Our own latency, and the room it leaves finance */}
      {hasTiming ? (
        <div className="rp__duo">
          <div className="rp__card">
            <div className="rp__card-hdr">
              <div>
                <div className="rp__card-title">Approval turnaround by stage</div>
                <div className="rp__card-sub">
                  Column height is average days from upload to final approval · bands show which
                  desk held it
                </div>
              </div>
            </div>
            {/* From the activity trail, so the total is not one opaque
                number: each band is the wait at one desk, and the
                stack adds up to the turnaround above it. */}
            <ColumnChart
              buckets={buckets}
              unit="days"
              height={210}
              bands={
                roles.length > 0
                  ? roles.map((role, i) => ({
                      label: role,
                      cls: cx(i),
                      values: periods.map(p => p.avgRoleDays[role] ?? 0),
                    }))
                  : [
                      {
                        label: 'Avg days to approve',
                        cls: 'rp-t--info',
                        values: periods.map(p => p.avgApprovalDays ?? 0),
                      },
                    ]
              }
              overlay={{
                label: 'Needed rework',
                cls: 'rp-t--danger',
                values: periods.map(p => p.reworkPct),
                unit: 'pct',
                axis: 'right',
              }}
            />
            <ChartKeys
              items={[
                ...(roles.length > 0
                  ? roles.map((role, i) => ({ label: role, cls: cx(i) }))
                  : [{ label: 'Avg days to approve', cls: 'rp-t--info' }]),
                { label: 'Sent back at least once (right axis)', cls: 'rp-t--danger', line: true },
              ]}
            />
          </div>

          <div className="rp__card">
            <div className="rp__card-hdr">
              <div>
                <div className="rp__card-title">Buffer between approval and due date</div>
                <div className="rp__card-sub">
                  Days left to pay once approved · below zero means approved after it was due
                </div>
              </div>
            </div>
            <ColumnChart
              buckets={buckets}
              unit="days"
              height={210}
              bands={[
                {
                  label: 'Avg days of buffer',
                  cls: 'rp-t--violet',
                  values: periods.map(p => p.avgBufferDays ?? 0),
                },
              ]}
            />
          </div>
        </div>
      ) : (
        <div className="rp__hint">
          <Info size={15} />
          <span>
            {activityFailed
              ? 'Turnaround, per-stage waits and rework are read from the activity trail, which could not be loaded. Refresh to try again.'
              : `Turnaround, per-stage waits and rework are read from the activity trail. Nothing in the last ${periodCount} ${grainWord}s has cleared final approval yet, so there is no turnaround to chart.`}
          </span>
        </div>
      )}

      {/* 5 — Mix */}
      <div className="rp__card">
        <div className="rp__card-hdr">
          <div>
            <div className="rp__card-title">Category share of spend over time</div>
            <div className="rp__card-sub">
              Each {grainWord} as 100% · bands ranked over the whole window so the order holds
            </div>
          </div>
        </div>
        {shares.length === 0 ? (
          <Empty title="No approved spend in this window" sub={`Try a wider grain than ${grainWord}ly.`} />
        ) : (
          <>
            <ColumnChart
              buckets={buckets}
              unit="pct"
              height={230}
              domain={[0, 100]}
              bands={shares.map((s, i) => ({
                label: s.label,
                cls: s.label.startsWith('Others') ? 'rp-t--muted' : cx(i),
                values: s.values,
                amounts: s.amounts,
              }))}
            />
            <ChartKeys
              items={shares.map((s, i) => ({
                label: s.label,
                cls: s.label.startsWith('Others') ? 'rp-t--muted' : cx(i),
              }))}
            />
          </>
        )}
      </div>
    </div>
  )
}
