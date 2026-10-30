/**
 * What has already been attempted against these bills.
 *
 * Was an always-open block of undifferentiated grey lines — a dozen rows reading
 * "PENDING_OTP · by Vikram Singh", which is a database status shown to a human,
 * and no way to tell the two payments that actually settled from the ten codes
 * that were never entered. Nothing here moves money, so it collapses, and it is
 * closed by default: the header states the outcome, and the rows are for when
 * someone asks "did this already go out?".
 *
 * Abandoned attempts are kept rather than filtered — a bill with eleven of them
 * is telling you something — but they are muted, because no money left the
 * account on those rows.
 */

import { useState } from 'react'
import { ChevronDown, ChevronRight, Check, Clock, X, CircleSlash } from 'lucide-react'
import { Badge, formatMoney, formatDateTime, type BadgeTone } from '../../ui'
import type { Disbursement } from '../../api/invoices'

/** The bank's status codes are not sentences. These are. */
const LOOK: Record<Disbursement['status'], { label: string; tone: BadgeTone; icon: JSX.Element; spent: boolean }> = {
  SUCCESS: { label: 'Settled', tone: 'success', icon: <Check size={11} />, spent: true },
  PENDING: { label: 'Processing', tone: 'warn', icon: <Clock size={11} />, spent: true },
  FAILED: { label: 'Failed', tone: 'danger', icon: <X size={11} />, spent: false },
  PENDING_OTP: { label: 'Code never entered', tone: 'neutral', icon: <CircleSlash size={11} />, spent: false },
}

export function PaymentHistory({ rows }: { rows: Disbursement[] }) {
  const [open, setOpen] = useState(false)
  if (rows.length === 0) return null

  const settled = rows.filter(r => r.status === 'SUCCESS')
  const paid = settled.reduce((sum, r) => sum + r.amount, 0)
  const failed = rows.filter(r => r.status === 'FAILED').length
  const processing = rows.filter(r => r.status === 'PENDING').length
  const abandoned = rows.filter(r => r.status === 'PENDING_OTP').length

  return (
    <section className={`pay-log${open ? ' pay-log--open' : ''}`}>
      <button
        type="button"
        className="pay-log__toggle"
        aria-expanded={open}
        aria-controls="pay-log-body"
        onClick={() => setOpen(o => !o)}
      >
        {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
        <span className="pay-log__title">Earlier payments against this bill</span>

        {/* The answer to the question this section exists for, before it opens. */}
        <span className="pay-log__summary">
          {settled.length > 0 ? (
            <>
              <b>{formatMoney(paid, { precise: true })}</b> settled
              {settled.length > 1 ? ` over ${settled.length} payments` : ''}
            </>
          ) : (
            'Nothing settled yet'
          )}
          {processing > 0 && <> · {processing} processing</>}
          {failed > 0 && <> · {failed} failed</>}
          {abandoned > 0 && <> · {abandoned} abandoned</>}
        </span>
      </button>

      {open && (
        <ol className="pay-log__rows" id="pay-log-body">
          {rows.map(d => {
            const look = LOOK[d.status]
            return (
              <li key={d.disbursement_id} className={`pay-log__row${look.spent ? '' : ' pay-log__row--void'}`}>
                <Badge tone={look.tone} icon={look.icon}>{look.label}</Badge>

                <span className="pay-log__meta">
                  <span className="pay-log__when">{formatDateTime(d.created_at)}</span>
                  {d.bank_rrn && (
                    <span className="pay-log__ref" title="Bank reference (RRN)">{d.bank_rrn}</span>
                  )}
                  {d.initiated_by && <span className="pay-log__who">by {d.initiated_by}</span>}
                  {d.status === 'FAILED' && d.status_message && (
                    <span className="pay-log__why">{d.status_message}</span>
                  )}
                </span>

                <span className="pay-log__amt">{formatMoney(d.amount, { precise: true })}</span>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
