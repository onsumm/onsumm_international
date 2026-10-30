/**
 * The lines of a rolled-up finding card: a vendor's failed payments, or every
 * FX item.
 *
 * Each line is one or more real findings underneath, and is dealt with on its
 * own — paid again, opened, or discarded. The card (and the queue's count) only
 * goes away once the last line has been. The server decides what rolls up
 * (ROLLUP_KEY_SQL); this only lays the lines out.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, ExternalLink } from 'lucide-react'
import { fetchRollup, type Finding } from '../../api/review'
import { Button, LoadingMark, formatDateTime, formatMoney } from '../../ui'
import { payPath } from '../../routes/paths'
import { explainFailure } from './explainFailure'
import type { RollupKind } from './reviewFormat'
import { reviewActions } from './useReviewStore'
import { displayCurrency } from '../../ui/format'

export interface LineScope { status: 'open' | 'snoozed'; scope: 'actionable' | 'visible' }

interface Fields { label: string; value: unknown }

function field(f: Finding, label: string): string | null {
  const v = (f.evidence as { fields?: Fields[] } | undefined)?.fields?.find(x => x.label === label)?.value
  return v == null || v === '—' ? null : String(v)
}

interface Line {
  key: string
  ids: string[]
  label: string
  sub: string | null
  amount: number | null
  currency: string
  note: string | null
  hint: string | null
  payHref: string | null
  openHref: string | null
}

/** A bill paid and failed twice is one thing to pay again, not two. */
function paymentLines(items: Finding[]): Line[] {
  const byBill = new Map<string, Finding[]>()
  for (const f of items) {
    const bill = f.entities.find(e => e.type === 'bill' || e.type === 'invoice')
    const k = bill?.id ?? f.finding_id
    byBill.set(k, [...(byBill.get(k) ?? []), f])
  }
  return [...byBill.entries()].map(([k, fs]) => {
    const at = (f: Finding) => field(f, 'Attempted at') ?? f.first_detected_at
    const latest = [...fs].sort((a, b) => (at(b) ?? '').localeCompare(at(a) ?? ''))[0]
    const bill = latest.entities.find(e => e.type === 'bill' || e.type === 'invoice')
    const vendor = latest.entities.find(e => e.type === 'vendor')
    const amount = latest.money_at_risk ?? latest.money_at_risk_inr
    const by = field(latest, 'Attempted by')
    const when = at(latest)
    const { reason, hint } = explainFailure(field(latest, 'Reason') || latest.detail)
    return {
      key: k,
      ids: fs.map(f => f.finding_id),
      label: bill?.label ? `Bill ${bill.label}` : 'Payment',
      sub: [
        when ? `Attempted ${formatDateTime(when)}` : null,
        by ? `by ${by}` : null,
        fs.length > 1 ? `· ${fs.length} attempts` : null,
      ].filter(Boolean).join(' ') || null,
      amount,
      currency: latest.money_currency || displayCurrency(),
      note: reason,
      hint,
      payHref: vendor ? payPath(vendor.id, bill?.id, amount ?? undefined) : null,
      openHref: null,
    }
  })
}

function fxLines(items: Finding[]): Line[] {
  return items.map(f => {
    const vendor = f.entities.find(e => e.type === 'vendor' && e.label)
    const bill = f.entities.find(e => (e.type === 'bill' || e.type === 'invoice') && e.label)
    return {
      key: f.finding_id,
      ids: [f.finding_id],
      label: f.title,
      sub: [vendor?.label, bill?.label && `Bill ${bill.label}`].filter(Boolean).join(' · ') || null,
      amount: f.money_at_risk_inr,
      currency: 'INR',
      note: null,
      hint: null,
      payHref: null,
      openHref: f.deep_link || null,
    }
  })
}

export function RollupLines({ finding, kind, lineScope, canAct, onNavigate, onChanged, onEmpty, onLoaded }: {
  finding: Finding
  kind: RollupKind
  lineScope: LineScope
  canAct: boolean
  onNavigate: (href: string) => void
  /** A line was dealt with: the queue and its counts need a reload. */
  onChanged: () => void
  /** The last line is gone; the card has nothing left to show. */
  onEmpty: () => void
  /** Every open finding in the card, for the footer's apply-to-all actions. */
  onLoaded: (ids: string[]) => void
}) {
  const [lines, setLines] = useState<Line[] | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { items } = await fetchRollup(finding.finding_id, lineScope)
      const next = kind === 'payment' ? paymentLines(items) : fxLines(items)
      setLines(next)
      onLoaded(items.map(f => f.finding_id))
      return next
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load these items')
      return null
    }
  }, [finding.finding_id, kind, lineScope.status, lineScope.scope])

  useEffect(() => { void load() }, [load])

  const discard = async (line: Line) => {
    setBusy(line.key)
    try {
      await reviewActions.bulk({ finding_ids: line.ids, status: 'DISMISSED', reason: 'not_an_issue' })
      onChanged()
      const next = await load()
      if (next && next.length === 0) onEmpty()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not discard this line')
    } finally {
      setBusy(null)
    }
  }

  if (error) return <p className="rh__row-error" role="alert">{error}</p>
  if (!lines) return <div className="rh__rl-loading"><LoadingMark size={18} /></div>

  return (
    <ul className="rh__rl" aria-label={kind === 'payment' ? 'Failed payments' : 'FX items'}>
      {lines.map(line => (
        <li key={line.key} className="rh__rl-line">
          <div className="rh__rl-main">
            <div className="rh__rl-top">
              <span className="rh__rl-label">{line.label}</span>
              {line.amount != null && (
                <span className="rh__rl-amount tnum">
                  {formatMoney(line.amount, { currency: line.currency, precise: kind === 'payment' })}
                </span>
              )}
            </div>
            {line.sub && <p className="rh__rl-sub">{line.sub}</p>}
            {line.note && (
              <p className="rh__rl-note">
                <AlertCircle size={13} aria-hidden="true" />
                <span>{line.note}{line.hint ? ` ${line.hint}` : ''}</span>
              </p>
            )}
          </div>
          {canAct && (
            <div className="rh__rl-actions">
              {line.payHref && (
                <Button variant="success" size="sm" disabled={!!busy}
                        onClick={() => onNavigate(line.payHref!)}>
                  Pay now
                </Button>
              )}
              {line.openHref && (
                <Button variant="secondary" size="sm" disabled={!!busy}
                        iconRight={<ExternalLink size={13} />}
                        onClick={() => onNavigate(line.openHref!)}>
                  Open
                </Button>
              )}
              <Button variant="ghost" size="sm" loading={busy === line.key} disabled={!!busy && busy !== line.key}
                      onClick={() => void discard(line)}>
                Discard
              </Button>
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}
