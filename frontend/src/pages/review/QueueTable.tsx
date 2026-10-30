/**
 * The queue, one row per finding.
 *
 * This table used to collapse findings by kind: one row reading "Vendor has no
 * bank account on file × 224", with "See all 224" to drill in. That answered
 * "what kinds of problem do we have" — a good question, and the wrong first
 * one. Someone working the queue is holding "which vendor, on which bill, for
 * how much, and is it mine to fix", and a collapsed row answers none of those
 * without a second click. The columns below are those questions in order.
 *
 * Sorting is the server's, not this component's. A client-side sort of the 25
 * rows on screen looks identical and means something completely different —
 * "the biggest of these 25" rather than "the biggest of 636" — and the column
 * it is spelled on gives no hint which one you got. Clicking a header changes
 * the URL, which re-queries.
 *
 * Built to the same table system as BillsPage and Browse: sticky translucent
 * head, select-all checkbox, hover/checked row states, `u-xl-th` headers.
 *
 * Keyboard triage (j/k/o/e/s/d/x in ReviewHubPage) still works unmodified: it
 * looks up `#rh-open-{finding_id}` to move DOM focus, and this table still
 * renders that id on the row's title button.
 */
import type { ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronsUpDown, Lock, ThumbsDown, ThumbsUp } from 'lucide-react'

import type { Finding, SortDir, SortKey } from '../../api/review'
import { Button, formatMoney } from '../../ui'
import {
  ageLabel, dueLabel, familyDisplay, primaryAction, priorityDisplay, rollupKind, rowMoney, rowTitle, statusDisplay, vendorCell,
} from './reviewFormat'
import { currencySymbol, displayCurrency } from '../../ui/format'

interface Props {
  findings: Finding[]
  busy: Set<string>
  selectedIds: Set<string>
  rowErrors: Map<string, string>
  sort: SortKey
  direction: SortDir
  onSort: (key: SortKey) => void
  onOpen: (id: string) => void
  onAct: (finding: Finding) => void
  onToggleSelect: (id: string) => void
  onSelectAll: () => void
  onNotify: (finding: Finding) => void
  onFeedback: (finding: Finding, good: boolean) => void
  feedbackBusyId: string | null
  entityHref: (type: string, id: string) => string | null
  /** Opens the record's card over the hub. Without it the link navigates to entityHref. */
  onOpenEntity?: (type: string, id: string) => void
}

/**
 * A sortable header.
 *
 * Shares `u-xl-th` with the column-filter headers on Bills, Vendors and
 * Browse, so a header behaves like a header everywhere in the app. The glyph
 * is always rendered, at low contrast until the column is the active one:
 * a sort arrow that appears on hover is a sort nobody finds.
 */
function SortTh({
  column, sort, direction, onSort, align, children,
}: {
  column: SortKey
  sort: SortKey
  direction: SortDir
  onSort: (key: SortKey) => void
  align?: 'right'
  children: ReactNode
}) {
  const on = sort === column
  const Icon = !on ? ChevronsUpDown : direction === 'asc' ? ArrowUp : ArrowDown
  return (
    <th
      className={`u-xl-th${on ? ' u-xl-th--sorted' : ''}`}
      style={{ textAlign: align }}
      aria-sort={on ? (direction === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        className="u-xl-th__btn rh__sort-btn"
        title={
          on
            ? `Sorted ${direction === 'asc' ? 'ascending' : 'descending'} — click to reverse`
            : 'Click to sort by this column'
        }
        onClick={() => onSort(column)}
      >
        <span>{children}</span>
        <Icon size={12} className={`rh__sort-icon${on ? ' rh__sort-icon--on' : ''}`} aria-hidden="true" />
      </button>
    </th>
  )
}

export function QueueTable({
  findings, busy, selectedIds, rowErrors, sort, direction, onSort,
  onOpen, onAct, onToggleSelect, onSelectAll, onNotify, onFeedback, feedbackBusyId,
  entityHref,
  onOpenEntity,
}: Props) {
  const allOn = findings.length > 0 && findings.every(f => selectedIds.has(f.finding_id))
  const someOn = !allOn && findings.some(f => selectedIds.has(f.finding_id))

  return (
    <div className="rh__browse-tablewrap">
      <table className="rh__browse-table rh__queue-table">
        <caption className="sr-only">Findings that need you ({findings.length})</caption>
        <thead>
          <tr>
            <th className="rh__browse-th-check">
              <input
                type="checkbox"
                aria-label="Select all"
                checked={allOn}
                ref={el => { if (el) el.indeterminate = someOn }}
                onChange={onSelectAll}
              />
            </th>
            <SortTh column="type" sort={sort} direction={direction} onSort={onSort}>Type</SortTh>
            <SortTh column="vendor" sort={sort} direction={direction} onSort={onSort}>Vendor</SortTh>
            {/* Not sortable: alphabetical by sentence is not an order anyone
                wants a queue in, and the column already sorts by rank. */}
            <th>Finding</th>
            <SortTh column="money" sort={sort} direction={direction} onSort={onSort} align="right">
              {currencySymbol(displayCurrency()) || displayCurrency()} at risk
            </SortTh>
            <SortTh column="status" sort={sort} direction={direction} onSort={onSort}>Status</SortTh>
            <SortTh column="priority" sort={sort} direction={direction} onSort={onSort}>Priority</SortTh>
            <SortTh column="age" sort={sort} direction={direction} onSort={onSort}>Age</SortTh>
            <th style={{ width: 1 }} />
          </tr>
        </thead>
        <tbody>
          {findings.map(f => {
            const fam = familyDisplay(f.family)
            const pri = priorityDisplay(f.severity)
            const st = statusDisplay(f)
            // Every FX item is one card, across vendors — no single name to show.
            const fxAcross = rollupKind(f) === 'fx' && (f.rollup?.count ?? 1) > 1
            const who = fxAcross ? { name: 'Across vendors', ref: null } : vendorCell(f)
            const due = dueLabel(f.due_at)
            const action = primaryAction(f)
            const error = rowErrors.get(f.finding_id) ?? null
            const isBusy = busy.has(f.finding_id)
            const selected = selectedIds.has(f.finding_id)
            const vendorEntity = fxAcross ? undefined : f.entities.find(e => e.type === 'vendor' && e.label)
            const vendorLink = vendorEntity ? entityHref(vendorEntity.type, vendorEntity.id) : null

            return (
              <tr
                key={f.finding_id}
                className={
                  `rh__browse-row${selected ? ' rh__browse-row--checked' : ''}` +
                  (st.tone === 'blocked' ? ' rh__browse-row--locked' : '')
                }
                aria-busy={isBusy || undefined}
                onClick={() => onOpen(f.finding_id)}
              >
                <td className="rh__browse-td-check" onClick={e => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    aria-label={`Select ${f.title}`}
                    checked={selected}
                    onChange={() => onToggleSelect(f.finding_id)}
                  />
                </td>

                <td>
                  <span className={`rh__type rh__type--c${fam.cat}`}>{fam.label}</span>
                </td>

                <td className="rh__who">
                  {who.name ? (
                    vendorLink ? (
                      <a
                        className="rh__who-name"
                        href={vendorLink}
                        onClick={e => {
                          e.stopPropagation()
                          // Plain click opens the vendor card here; modified clicks
                          // keep the browser's new-tab behaviour.
                          if (!onOpenEntity || !vendorEntity || e.metaKey || e.ctrlKey || e.shiftKey) return
                          e.preventDefault()
                          onOpenEntity(vendorEntity.type, vendorEntity.id)
                        }}
                      >
                        {who.name}
                      </a>
                    ) : (
                      <span className="rh__who-name">{who.name}</span>
                    )
                  ) : (
                    <span className="rh__dot">—</span>
                  )}
                  {who.ref && <span className="rh__who-ref">{who.ref}</span>}
                </td>

                <td className="rh__browse-title">
                  <button
                    type="button"
                    id={`rh-open-${f.finding_id}`}
                    className="rh__cluster-open rh__finding-text"
                    title={rowTitle(f)}
                    onClick={e => { e.stopPropagation(); onOpen(f.finding_id) }}
                  >
                    {st.tone === 'blocked' && <Lock size={12} aria-hidden="true" />}
                    {rowTitle(f)}
                  </button>
                  {error && <p className="rh__row-error" role="alert">{error}</p>}
                </td>

                <td style={{ textAlign: 'right' }} className="tnum">
                  {rowMoney(f) ? formatMoney(rowMoney(f)) : <span className="rh__dot">—</span>}
                </td>

                <td>
                  <span className={`rh__state rh__state--${st.tone}`}>
                    {st.tone === 'pending' && <span className="rh__state-dot" aria-hidden="true" />}
                    {st.label}
                  </span>
                  {/* Overdue is not a status, it is a deadline that has passed
                      — it rides under the status rather than replacing it. */}
                  {due && <span className="rh__state-due">{due}</span>}
                </td>

                <td>
                  <span className={`rh__pri rh__pri--${pri.tone}`}>{pri.label}</span>
                </td>

                <td className="rh__age">{ageLabel(f.first_detected_at)}</td>

                <td onClick={e => e.stopPropagation()}>
                  {!f.can_act ? (
                    <Button size="sm" variant="ghost" onClick={() => onNotify(f)}>
                      Notify
                    </Button>
                  ) : f.is_suspected ? (
                    <span className="rh__act-group">
                      {/* The fix leads, then the way to check it. A suspected
                          finding is still the agent's proposal — but it is one
                          the user can accept from here, behind the same
                          confirmation the drawer shows, rather than having to
                          find the drawer first to discover an approve button
                          exists at all. */}
                      {action && (
                        <Button size="sm" variant="primary" onClick={() => onAct(f)}>
                          {action.label}
                        </Button>
                      )}
                      <Button size="sm" variant="secondary" onClick={() => onOpen(f.finding_id)}>
                        Open &amp; check
                      </Button>
                      <span className="rh__feedback" role="group" aria-label="Was this a real problem?">
                        <button
                          type="button"
                          className="rh__feedback-btn"
                          disabled={feedbackBusyId === f.finding_id}
                          aria-label="Yes, this is a real problem"
                          onClick={() => onFeedback(f, true)}
                        >
                          <ThumbsUp size={13} aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          className="rh__feedback-btn"
                          disabled={feedbackBusyId === f.finding_id}
                          aria-label="No, this is not a problem"
                          onClick={() => onFeedback(f, false)}
                        >
                          <ThumbsDown size={13} aria-hidden="true" />
                        </button>
                      </span>
                    </span>
                  ) : action ? (
                    <Button size="sm" variant="secondary" onClick={() => onAct(f)}>
                      {action.label}
                    </Button>
                  ) : null}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
