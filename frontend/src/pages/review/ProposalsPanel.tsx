/**
 * Several proposed corrections to one bill, each accepted on its own.
 *
 * Every other evidence kind in this folder describes a problem and hands the
 * decision to a button underneath. This one *is* the decision: each row shows
 * what the bill says today struck through, and what we think it should say
 * above it, already ticked. Untick what you disagree with, apply the rest.
 *
 * Two things the shape is protecting:
 *
 *   · Partial acceptance. A bill can be wrong about its category and right
 *     about its cost centre, and someone who has to take all four or none
 *     will take none. The panel never proposes an all-or-nothing choice, and
 *     the server applies each row under its own savepoint so one failure
 *     costs only itself.
 *
 *   · The difference between fixing our record and fixing the problem. A
 *     wrongly-charged GST split carries a caveat, shown on the row rather
 *     than in a footnote: accepting it tidies our books and leaves the input
 *     credit where it was, because only a reissued invoice moves that.
 *
 * Applied rows stay on screen, ticked and locked, until the next scan drops
 * them — the queue's own "did that work?" answer, rather than a row vanishing
 * mid-review.
 */
import { useMemo, useState } from 'react'
import { AlertTriangle, Check, Info } from 'lucide-react'

import { applyBillCoding, type Finding } from '../../api/review'
import { Button, useToast } from '../../ui'

export interface Proposal {
  id: string
  group?: string
  label: string
  current?: string | null
  proposed: string
  why?: string
  writable?: boolean
  caveat?: string
}

export function ProposalsPanel({
  proposals, finding, onApplied,
}: {
  proposals: Proposal[]
  finding: Finding
  /** Refetch the finding — the server does not close it, the next scan does. */
  onApplied?: () => void
}) {
  const toast = useToast()
  const writable = useMemo(
    () => proposals.filter(p => p.writable !== false),
    [proposals],
  )
  // Everything we can write starts ticked: the proposals are the vendor's own
  // established habit or a rule about state codes, so agreement is the common
  // case and the work should be unticking the exception.
  const [picked, setPicked] = useState<Set<string>>(
    () => new Set(writable.map(p => p.id)),
  )
  const [done, setDone] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)

  const toggle = (id: string) =>
    setPicked(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const selected = [...picked].filter(id => !done.has(id))

  const apply = async () => {
    if (selected.length === 0) return
    setBusy(true)
    try {
      const res = await applyBillCoding({
        finding_id: finding.finding_id,
        invoice_id: finding.subject.id ?? '',
        accept: selected,
      })
      setDone(prev => new Set([...prev, ...res.applied]))
      if (res.failed.length) {
        toast.error(
          `${res.counts.applied} applied, ${res.counts.failed} did not: ` +
          res.failed.map(f => `${f.id} — ${f.error}`).join('; '),
        )
      } else {
        toast.success(
          `${res.counts.applied} correction${res.counts.applied === 1 ? '' : 's'} applied.`,
        )
      }
      onApplied?.()
    } catch (e) {
      toast.error(
        e instanceof Error && e.message
          ? `That did not go through: ${e.message}`
          : 'That did not go through.',
      )
    } finally {
      setBusy(false)
    }
  }

  // Grouped so four unrelated kinds of wrong do not read as one list.
  const groups = useMemo(() => {
    const out = new Map<string, Proposal[]>()
    for (const p of proposals) {
      const key = p.group || 'Corrections'
      out.set(key, [...(out.get(key) ?? []), p])
    }
    return [...out.entries()]
  }, [proposals])

  return (
    <div className="rh__prop">
      {groups.map(([group, rows]) => (
        <div key={group} className="rh__prop-group">
          <p className="rh__prop-grouphead">{group}</p>
          {rows.map(p => {
            const applied = done.has(p.id)
            const on = applied || picked.has(p.id)
            const canWrite = p.writable !== false
            return (
              <div
                key={p.id}
                className={
                  'rh__prop-row' +
                  (on ? ' rh__prop-row--on' : '') +
                  (applied ? ' rh__prop-row--done' : '')
                }
              >
                <button
                  type="button"
                  className="rh__prop-tick"
                  role="checkbox"
                  aria-checked={on}
                  aria-label={`Apply: ${p.label} becomes ${p.proposed}`}
                  disabled={!canWrite || applied || busy}
                  onClick={() => toggle(p.id)}
                >
                  {on && <Check size={12} strokeWidth={3} aria-hidden="true" />}
                </button>

                <div className="rh__prop-body">
                  <p className="rh__prop-label">{p.label}</p>
                  {/* Proposed on top, current struck through beneath: the
                      reader's eye should land on what it would become, with
                      what it is still legible under it. */}
                  <p className="rh__prop-to">{p.proposed}</p>
                  {p.current
                    ? <p className="rh__prop-from"><s>{p.current}</s></p>
                    : <p className="rh__prop-from rh__prop-from--empty">not set</p>}
                  {p.why && <p className="rh__prop-why">{p.why}</p>}
                  {p.caveat && (
                    <p className="rh__prop-caveat">
                      <AlertTriangle size={12} aria-hidden="true" />
                      {p.caveat}
                    </p>
                  )}
                  {!canWrite && (
                    <p className="rh__prop-why">
                      <Info size={12} aria-hidden="true" /> Not something we can write.
                    </p>
                  )}
                </div>

                {applied && <span className="rh__prop-done">Applied</span>}
              </div>
            )
          })}
        </div>
      ))}

      <div className="rh__prop-foot">
        <span className="rh__prop-count">
          {selected.length === 0
            ? done.size > 0 ? 'Nothing left selected' : 'Nothing selected'
            : `${selected.length} of ${writable.length} selected`}
        </span>
        <Button
          size="sm"
          variant="primary"
          loading={busy}
          disabled={selected.length === 0}
          onClick={() => void apply()}
        >
          Apply {selected.length || ''}
        </Button>
      </div>
    </div>
  )
}
