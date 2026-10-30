/**
 * Cluster drill-in — every member of one finding's cluster as a selectable
 * table, with a floating bulk-action bar. Design brief §4 artboard 5, reached
 * from the drawer's "N others like this" escape hatch (§7).
 *
 * Lives inside ReviewHubPage's `.rh__wrap`, in the same slot the finding-type
 * drill (FindingTable) already occupies, so opening a member reuses the
 * page's existing `?f=` drawer wiring rather than a second one — this
 * component never renders a drawer or a modal of its own.
 *
 * Members that share the finding's `condition_hash` are the same problem with
 * the same fix and are pre-selected; members that differ are auto-deselected
 * with a banner explaining why, per the design brief's homogeneity gate (see
 * the comment on `cluster.homogeneous` in review_hub.py) — a one-click bulk
 * write across records that quietly differ is the fastest way to lose a
 * finance team's trust.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import type { Finding } from '../../api/review'
import { Badge, Button, ConfirmDialog, formatMoneyCompact, useToast } from '../../ui'
import { primaryAction } from './reviewFormat'
import { reviewActions } from './useReviewStore'

interface Member {
  id: string
  label: string
  money: number | null
  differs: boolean
}

interface Props {
  finding: Finding
  onOpenMember: (id: string) => void
  onBack: () => void
}

export function ClusterDrillIn({ finding: f, onOpenMember, onBack }: Props) {
  const cluster = f.cluster
  const toast = useToast()

  // The primary finding is a member of its own cluster — the sample the
  // server sends never repeats it (see GET /findings/{id}). Folding it back in
  // here means "select all" really means all of them, not all-but-the-one the
  // user opened.
  const members: Member[] = useMemo(() => {
    const self: Member = { id: f.finding_id, label: f.title, money: f.money_at_risk_inr, differs: false }
    const rest = (cluster?.sample ?? []).map(s => ({
      id: s.finding_id,
      // The sample carries no entity labels (see api/review.ts) — the record's
      // own title is the best identifier available without a second request
      // per row, same fallback FindingTable uses for a record with no label.
      label: s.title,
      money: s.money_at_risk_inr,
      differs: s.differs,
    }))
    return [self, ...rest]
  }, [f, cluster])

  const differing = members.filter(m => m.differs)
  const action = primaryAction(f)

  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(members.filter(m => !m.differs).map(m => m.id)),
  )
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<Map<string, string>>(new Map())
  const [done, setDone] = useState<Set<string>>(new Set())

  const toggle = (id: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const selectable = members.filter(m => !done.has(m.id))
  const allOn = selectable.length > 0 && selectable.every(m => selected.has(m.id))
  const toggleAll = () => setSelected(allOn ? new Set() : new Set(selectable.map(m => m.id)))

  const selectedMembers = members.filter(m => selected.has(m.id) && !done.has(m.id))
  const selectedMoney = selectedMembers.reduce((sum, m) => sum + (m.money ?? 0), 0)
  const remaining = members.length - done.size

  const runBulk = async () => {
    const ids = selectedMembers.map(m => m.id)
    setBusy(true)
    try {
      const results = await reviewActions.runClusterFix(ids, action?.id)
      const okIds = results.filter(r => r.ok).map(r => r.id)
      setFailed(prev => {
        const next = new Map(prev)
        for (const r of results) {
          if (r.ok) next.delete(r.id)
          else next.set(r.id, r.error || 'That did not go through.')
        }
        return next
      })
      setDone(prev => new Set([...prev, ...okIds]))
      setSelected(new Set())
      setConfirming(false)
      const failedCount = results.length - okIds.length
      if (okIds.length) toast.success(`${okIds.length} of ${results.length} fixed.`)
      if (failedCount > 0) toast.error(`${failedCount} did not go through — see the table.`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rh__cluster">
      <div className="rh__drill">
        <div>
          <button type="button" className="rh__scope-more" onClick={onBack} style={{ marginBottom: 'var(--sp-1)' }}>
            ← Back to the finding
          </button>
          <p className="rh__drill-title">{f.title}</p>
          <p className="rh__drill-sub">
            {remaining} record{remaining === 1 ? '' : 's'}
            {cluster?.money_at_risk_inr ? ` · ${formatMoneyCompact(cluster.money_at_risk_inr)} at risk` : ''}
          </p>
        </div>
      </div>

      {differing.length > 0 && (
        <p className="rh__cluster-banner">
          <AlertTriangle size={14} aria-hidden="true" />
          {differing.length} differ{differing.length === 1 ? 's' : ''} from the rest and{' '}
          {differing.length === 1 ? 'is' : 'are'} excluded — review separately.
        </p>
      )}

      <div className="rh__tablewrap">
        <table className="rh__table rh__cluster-table">
          <caption className="sr-only">Every member of this cluster ({members.length})</caption>
          <thead>
            <tr>
              <th style={{ width: 36 }}>
                <input type="checkbox" aria-label="Select all" checked={allOn} onChange={toggleAll} />
              </th>
              <th>Record</th>
              <th style={{ textAlign: 'right' }}>At risk</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {members.map(m => {
              const isDone = done.has(m.id)
              const err = failed.get(m.id)
              return (
                <tr key={m.id} className={`rh__cluster-row${selected.has(m.id) ? ' rh__cluster-row--selected' : ''}`}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${m.label}`}
                      checked={selected.has(m.id)}
                      disabled={isDone}
                      onChange={() => toggle(m.id)}
                    />
                  </td>
                  <td>
                    <button type="button" className="rh__cluster-open" onClick={() => onOpenMember(m.id)}>
                      {m.label}
                    </button>
                    {err && <p className="rh__row-error">{err}</p>}
                  </td>
                  <td style={{ textAlign: 'right' }} className="tnum">
                    {m.money ? formatMoneyCompact(m.money) : null}
                  </td>
                  <td>
                    {isDone ? (
                      <Badge tone="success">Fixed</Badge>
                    ) : m.differs ? (
                      <Badge tone="warn">Differs</Badge>
                    ) : (
                      <Badge tone="neutral">Same problem</Badge>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {selectedMembers.length > 0 && (
        <div className="rh__bulkbar">
          <span className="rh__bulkbar-count">
            {selectedMembers.length} selected
            {selectedMoney > 0 && ` · ${formatMoneyCompact(selectedMoney)}`}
          </span>
          <span className="rh__bulkbar-actions">
            {action ? (
              <Button size="sm" variant="primary" loading={busy} onClick={() => setConfirming(true)}>
                {action.label} · {selectedMembers.length}
              </Button>
            ) : (
              <span className="rh__note" style={{ margin: 0 }}>
                No one-click fix for this kind — open each record.
              </span>
            )}
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </span>
        </div>
      )}

      {action && (
        <ConfirmDialog
          open={confirming}
          onCancel={() => setConfirming(false)}
          onConfirm={runBulk}
          title={`${action.label} across ${selectedMembers.length} record${selectedMembers.length === 1 ? '' : 's'}`}
          description={action.preview || "Each record gets its own version of this fix — not a copy of one value."}
          impact={[
            { label: 'Records', value: selectedMembers.length },
            ...(selectedMoney > 0 ? [{ label: 'Money at risk', value: formatMoneyCompact(selectedMoney) }] : []),
            ...(differing.length > 0 ? [{ label: 'Excluded (differ)', value: differing.length }] : []),
          ]}
          items={selectedMembers.map(m => m.label)}
          confirmLabel={action.label}
          requireTyped={selectedMembers.length > 1 ? 'FIX' : undefined}
          tone={action.destructive ? 'danger' : 'primary'}
        />
      )}
    </div>
  )
}
