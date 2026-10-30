/**
 * Browse — /review/browse
 *
 * Every open finding this user can see, in one filterable table. Design brief
 * §4 artboard 8, §9 "a table is the escape hatch, not the default view": the
 * bounded queue at /review renders at most twelve cards because each finding
 * is a decision, not a row, but a user with 400 open findings still needs a
 * real way to see all 400 — this is it, reached from the queue's "Show
 * everything" line once its scope (visible) outgrows what it renders
 * (actionable).
 *
 * Built to the same big-filterable-table pattern BillsPage already uses:
 * Excel-style column filters (ui/ColumnFilter.tsx) over rows already in
 * memory, and a hand-rolled `<table>` rather than the DataTable primitive, to
 * match BillsPage's own header/row markup instead of introducing a second
 * table convention.
 *
 * Not DOM-virtualized. Nothing in this codebase is — BillsPage renders its own
 * few-hundred-row table the same way, and there is no windowing library in
 * package.json — so this follows the same convention rather than adding a new
 * dependency for one page. Column filtering is client-side over whatever has
 * loaded (exactly what useColumnFilters already assumes everywhere it is
 * used), so pages are fetched in batches of 100 with an explicit "Load more"
 * button — the design brief's own anti-pattern list rules out infinite scroll.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, RefreshCw } from 'lucide-react'

import { fetchFindings, type Finding, type FindingAction } from '../../api/review'
import { findingPath, reviewPath } from '../../routes/paths'
import {
  Button,
  EmptyState,
  ErrorState,
  FilterStatus,
  FilterTh,
  SkeletonRows,
  formatMoneyCompact,
  useColumnFilters,
  useToast,
  type FilterField,
} from '../../ui'
import { ActionConfirm } from './ActionConfirm'
import { OverdueBadge, SeverityBadge } from './FindingBadges'
import { ageLabel, dueLabel, primaryAction, severityDisplay } from './reviewFormat'
import { reviewActions, useReviewCounts } from './useReviewStore'
import './review.css'

const PAGE = 100

/** The record a finding is about, in the user's words — same fallback FindingTable uses. */
function subjectLabel(f: Finding): string {
  const named = f.entities.find(e => e.label && e.role === 'subject') ?? f.entities.find(e => e.label)
  return named?.label ?? f.subject.id?.slice(0, 8) ?? '—'
}

const FIELDS: FilterField<Finding>[] = [
  { key: 'severity', label: 'Severity', kind: 'enum', value: f => severityDisplay(f.severity).label },
  { key: 'lane', label: 'Lane', kind: 'enum', value: f => f.lane },
  { key: 'subject', label: 'Record', kind: 'text', value: f => subjectLabel(f) },
  { key: 'title', label: 'Finding', kind: 'text', value: f => f.title },
  { key: 'money', label: 'At risk', kind: 'number', value: f => f.money_at_risk_inr ?? undefined },
  { key: 'age', label: 'First seen', kind: 'date', value: f => f.first_detected_at },
  {
    key: 'can_act', label: 'Can I fix it', kind: 'enum',
    value: f => (f.can_act ? 'Yes' : `Needs ${f.required_capability}`),
  },
]

export default function BrowseView() {
  const navigate = useNavigate()
  const toast = useToast()
  const counts = useReviewCounts()

  const [rows, setRows] = useState<Finding[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)

  const colFilters = useColumnFilters(FIELDS)

  const load = async (nextCursor: string | null) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchFindings({
        scope: 'visible', status: 'open', group: false, limit: PAGE,
        cursor: nextCursor ?? undefined,
      })
      setRows(prev => (nextCursor ? [...prev, ...res.items] : res.items))
      setCursor(res.next_cursor)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load findings')
    } finally {
      setLoading(false)
    }
  }

  // Runs once — every open finding this user can see, loaded here rather than
  // tied to the shared queue store, so paging through Browse never disturbs
  // the bounded queue's frozen order back at /review. `load` itself is stable
  // across renders (it closes over nothing but setState calls), so there is
  // no missing-dependency warning to suppress here.
  useEffect(() => {
    void load(null)
  }, [])

  const filtered = colFilters.apply(rows)

  const allOn = filtered.length > 0 && filtered.every(f => selected.has(f.finding_id))
  const someOn = !allOn && filtered.some(f => selected.has(f.finding_id))
  const toggleAll = () => {
    setSelected(prev => {
      const next = new Set(prev)
      if (allOn) filtered.forEach(f => next.delete(f.finding_id))
      else filtered.forEach(f => next.add(f.finding_id))
      return next
    })
  }
  const toggleOne = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const runBulk = async (status: 'SNOOZED' | 'DISMISSED') => {
    const ids = [...selected]
    if (!ids.length) return
    setBulkBusy(true)
    try {
      const res = await reviewActions.bulk({
        finding_ids: ids,
        status,
        reason: status === 'DISMISSED' ? 'not_an_issue' : undefined,
        snooze_days: status === 'SNOOZED' ? 7 : undefined,
      })
      toast.success(
        `${res.counts.updated} updated${res.counts.failed ? `, ${res.counts.failed} did not go through` : ''}.`,
      )
      const done = new Set(res.updated)
      setRows(prev => prev.filter(f => !done.has(f.finding_id)))
      setSelected(new Set())
    } catch (e) {
      // call() already throws with the server's `detail`; swallowing it is why
      // a "uuid = text" 500 read as an unexplained refusal.
      toast.error(
        e instanceof Error && e.message
          ? `That bulk action did not go through: ${e.message}`
          : 'That bulk action did not go through.',
      )
    } finally {
      setBulkBusy(false)
    }
  }

  // Same rule as the queue (ReviewHubPage::handleAct): inputs go to the
  // finding's own page, where there is somewhere to type them; a confirmation
  // is shown before anything is written; only an action with neither runs on
  // the click. This table offers a suspected finding's action now too, so the
  // two surfaces agree on what approving from a row costs.
  const [confirming, setConfirming] = useState<{ finding: Finding; action: FindingAction } | null>(null)

  const runFix = async (f: Finding, action: FindingAction) => {
    try {
      await reviewActions.act(f, action)
      toast.success('Done.')
      setRows(prev => prev.filter(r => r.finding_id !== f.finding_id))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'That did not go through.')
    }
  }

  const startFix = (f: Finding) => {
    const action = primaryAction(f)
    if (!action) return
    if (action.fields?.length) {
      navigate(findingPath(f.finding_id))
      return
    }
    if (action.confirm) {
      setConfirming({ finding: f, action })
      return
    }
    void runFix(f, action)
  }

  return (
    <div className="rh rh--browse">
      <header className="rh__header">
        <div>
          <button type="button" className="rh__scope-more rh__browse-back" onClick={() => navigate(reviewPath())}>
            <ArrowLeft size={14} aria-hidden="true" /> Review Hub
          </button>
          <h1 className="rh__title" style={{ marginTop: 'var(--sp-2)' }}>Browse</h1>
          <p className="rh__sub">
            Every open finding you can see{counts ? ` · ${counts.total_visible} total` : ''} — filter any
            column, like a sheet.
          </p>
        </div>
        <Button variant="secondary" size="sm" loading={loading && rows.length === 0} onClick={() => void load(null)}>
          <RefreshCw size={14} aria-hidden="true" /> Refresh
        </Button>
      </header>

      {colFilters.count > 0 && (
        <div className="rh__browse-filterbar">
          <FilterStatus state={colFilters} total={rows.length} shown={filtered.length} unit="findings" />
        </div>
      )}

      <div className="rh__wrap">
        {loading && rows.length === 0 && (
          <div className="rh__state">
            <SkeletonRows rows={8} />
          </div>
        )}

        {error && rows.length === 0 && (
          <div className="rh__state">
            <ErrorState title="Could not load findings" message={error} onRetry={() => void load(null)} />
          </div>
        )}

        {!loading && !error && rows.length === 0 && (
          <div className="rh__state">
            <EmptyState title="Nothing to browse" message="No open findings are visible to you right now." />
          </div>
        )}

        {rows.length > 0 && (
          <div className="rh__browse-tablewrap">
            <table className="rh__browse-table">
              <caption className="sr-only">Every open finding you can see ({filtered.length} of {rows.length})</caption>
              <thead>
                <tr>
                  <th className="rh__browse-th-check">
                    <input
                      type="checkbox"
                      aria-label="Select all"
                      checked={allOn}
                      ref={el => { if (el) el.indeterminate = someOn }}
                      onChange={toggleAll}
                    />
                  </th>
                  <FilterTh state={colFilters} fieldKey="severity" rows={rows}>Severity</FilterTh>
                  <FilterTh state={colFilters} fieldKey="title" rows={rows}>Finding</FilterTh>
                  <FilterTh state={colFilters} fieldKey="subject" rows={rows}>Record</FilterTh>
                  <FilterTh state={colFilters} fieldKey="lane" rows={rows}>Lane</FilterTh>
                  <FilterTh state={colFilters} fieldKey="money" rows={rows} align="right">At risk</FilterTh>
                  <FilterTh state={colFilters} fieldKey="age" rows={rows}>Age</FilterTh>
                  <FilterTh state={colFilters} fieldKey="can_act" rows={rows}>Can I fix it</FilterTh>
                  <th style={{ width: 1 }} />
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 && colFilters.count > 0 && (
                  <tr>
                    <td colSpan={9} style={{ textAlign: 'center', padding: 'var(--sp-6) 0' }}>
                      No findings match these filters.{' '}
                      <button type="button" className="rh__scope-more" onClick={colFilters.clearAll}>
                        Clear {colFilters.count} filter{colFilters.count > 1 ? 's' : ''}
                      </button>
                    </td>
                  </tr>
                )}
                {filtered.map(f => {
                  const sev = severityDisplay(f.severity)
                  const due = dueLabel(f.due_at)
                  const action = primaryAction(f)
                  return (
                    <tr
                      key={f.finding_id}
                      className={`rh__browse-row${selected.has(f.finding_id) ? ' rh__browse-row--checked' : ''}`}
                      onClick={() => navigate(findingPath(f.finding_id))}
                    >
                      <td className="rh__browse-td-check" onClick={e => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={`Select ${f.title}`}
                          checked={selected.has(f.finding_id)}
                          onChange={() => toggleOne(f.finding_id)}
                        />
                      </td>
                      <td><SeverityBadge sev={sev} /></td>
                      <td className="rh__browse-title">{f.title}</td>
                      <td>{subjectLabel(f)}</td>
                      <td className="rh__browse-lane">{f.lane}</td>
                      <td style={{ textAlign: 'right' }} className="tnum">
                        {f.money_at_risk_inr ? formatMoneyCompact(f.money_at_risk_inr) : null}
                      </td>
                      <td>{due ? <OverdueBadge label={due} /> : ageLabel(f.first_detected_at)}</td>
                      <td>{f.can_act ? 'Yes' : `Needs ${f.required_capability}`}</td>
                      <td onClick={e => e.stopPropagation()}>
                        {action && f.can_act && (
                          <Button size="sm" variant="secondary" onClick={() => startFix(f)}>
                            {action.label}
                          </Button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {cursor && (
          <div className="rh__more">
            <Button variant="secondary" size="sm" loading={loading} onClick={() => void load(cursor)}>
              Load {PAGE} more
            </Button>
          </div>
        )}

        {selected.size > 0 && (
          <div className="rh__bulkbar">
            <span className="rh__bulkbar-count">{selected.size} selected</span>
            <span className="rh__bulkbar-actions">
              <Button size="sm" variant="secondary" loading={bulkBusy} onClick={() => void runBulk('SNOOZED')}>
                Snooze a week
              </Button>
              <Button size="sm" variant="ghost" loading={bulkBusy} onClick={() => void runBulk('DISMISSED')}>
                Dismiss
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear</Button>
            </span>
          </div>
        )}

        {confirming && (
          <ActionConfirm
            action={confirming.action}
            onCancel={() => setConfirming(null)}
            onConfirm={action => {
              const { finding } = confirming
              setConfirming(null)
              void runFix(finding, action)
            }}
          />
        )}
      </div>
    </div>
  )
}
