/**
 * One finding, expanded.
 *
 * The vertical order is fixed and load-bearing: what's wrong → evidence →
 * cluster escape hatch → fix → deep link. The fix is always *below* the
 * evidence, so nobody can click it without having scrolled past why.
 *
 * A drawer rather than a modal because the queue has to stay visible and keep
 * its scroll position — a user triaging twelve findings must not lose their
 * place every time they look at one.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { clickOrigin } from '../../utils/clickOrigin'
import { ExternalLink, FileText, ListTree, Table2, X } from 'lucide-react'

import type { DismissReason, Finding, FindingAction, FindingPreview } from '../../api/review'
import {
  Button,
  IconButton,
  formatDateTime,
  formatMoney,
  useFocusTrap,
  useScrollLock,
  useToast,
} from '../../ui'
import { ActionConfirm } from './ActionConfirm'
import { DismissDialog } from './DismissDialog'
import { EvidenceView } from './EvidenceView'
import { RollupLines, type LineScope } from './RollupLines'
import { rollupKind } from './reviewFormat'
import { reviewActions } from './useReviewStore'
import { ConfidenceBadge, LockChip, SeverityBadge } from './FindingBadges'
import { PreviewPane } from './PreviewPane'
import { severityDisplay } from './reviewFormat'

/** Shape, not problem type — a preview kind the server invents later still gets
 *  a sensible mark. */
const PREVIEW_ICON: Record<string, typeof FileText> = {
  document: FileText,
  timeline: ListTree,
  fields: Table2,
  table: Table2,
}

interface Props {
  finding: Finding
  busy?: boolean
  onClose: () => void
  onStatus: (
    status: 'ACKNOWLEDGED' | 'SNOOZED' | 'RESOLVED' | 'DISMISSED',
    opts?: { reason?: string; snoozeDays?: number; suppressScope?: 'type_tenant_wide' },
  ) => void
  onNavigate: (href: string) => void
  /** Run one executable action — a safe fix, or an approval the caller may make.
   *  `values` carries what the user typed into the action's fields.
   *  Never money: a disbursement is a deep link. */
  onAct: (action: FindingAction, values?: Record<string, string>) => void
  /** A `proposals` evidence panel has just written to the record. The finding
   *  is not closed by that write — the adapter's next scan decides — so this
   *  only asks the caller to refetch. */
  onApplied?: () => void
  /** The queue view the card was opened from. Given, a vendor's failed
   *  payments and every FX item open as one card of lines; omitted (e.g. the
   *  Resolved tab), every finding is its own card. */
  lineScope?: LineScope
}

export function FindingDrawer({
  finding: f, busy, onClose, onStatus, onNavigate, onAct, onApplied, lineScope,
}: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const toast = useToast()
  const [dismissing, setDismissing] = useState(false)
  const [confirming, setConfirming] = useState<FindingAction | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const sev = severityDisplay(f.severity)

  // A finding that carries its own source page still opens with it beside the
  // text — for "did we read this right?" the document *is* the finding, and
  // making the user ask for it would be a step backwards. Everything else opens
  // closed and is one button away.
  const previews = useMemo<FindingPreview[]>(() => {
    const declared = f.previews || []
    if (declared.length || !f.document) return declared
    // Older payloads carry a document but no preview menu.
    return [{ id: 'document', label: 'The bill', kind: 'document', document: f.document }]
  }, [f.previews, f.document])
  const [openPreview, setOpenPreview] = useState<string | null>(null)
  const shown = previews.find(p => p.id === openPreview) || null

  // The action whose form is open, and what has been typed into it. An action
  // with fields is a correction: the stored value is what the finding is
  // complaining about, so it is offered for editing rather than just re-sent.
  const [filling, setFilling] = useState<FindingAction | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})

  useFocusTrap(ref, true, onClose)
  // Same as Modal: the queue behind must not scroll under the drawer.
  useScrollLock(true)

  useEffect(() => {
    setDismissing(false)
    setFilling(null)
    setValues({})
    setOpenPreview(f.document ? 'document' : null)
  // The upload id, not the object: the detail fetch replaces the finding, and
  // depending on the object would put the pane back to the document every time
  // the store re-emits — including after the user chose a different preview.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.finding_id, f.document?.upload_id])

  const navActions = f.actions.filter(a => a.kind === 'navigate' && a.href)
  // An approval leads: when a bill is waiting on this user, deciding it is the
  // reason they opened the finding at all. 'notify' renders and executes
  // identically to 'safe' here — it's already fully server-composed (see
  // api/review.ts::runAction) — so it needs no separate branch, just inclusion.
  const runActions = f.actions.filter(
    a => a.kind === 'approve' || a.kind === 'safe' || a.kind === 'notify',
  )

  // A vendor's failed payments, and every FX item, are one card of lines —
  // each paid, opened or discarded on its own — instead of the generic layout.
  const kind = lineScope ? rollupKind(f) : null
  const [lineIds, setLineIds] = useState<string[]>([])
  const [allBusy, setAllBusy] = useState(false)
  const vendorName = f.entities.find(e => e.type === 'vendor')?.label
  const applyToAll = async (status: 'RESOLVED' | 'SNOOZED' | 'DISMISSED') => {
    if (!lineIds.length) return
    setAllBusy(true)
    try {
      await reviewActions.bulk({
        finding_ids: lineIds, status,
        ...(status === 'SNOOZED' ? { snooze_days: 7 } : {}),
        ...(status === 'DISMISSED' ? { reason: 'not_an_issue' } : {}),
      })
      onApplied?.()
      onClose()
    } finally {
      setAllBusy(false)
    }
  }

  // The card grows out of the row that was clicked, the same gesture as the bill
  // and vendor cards. Read once so a re-render never replays the animation.
  const [originStyle] = useState<CSSProperties>(() => {
    const [dx, dy] = (clickOrigin() ?? '0px 0px').split(' ')
    return { '--rh-dx': dx, '--rh-dy': dy } as CSSProperties
  })

  return (
    <>
      <div className="rh__scrim" onClick={onClose} aria-hidden="true" />
      <div
        // Widened into two panes when the finding carries its source page, so
        // the extracted value and the document sit side by side.
        className={
          `rh__drawer${shown ? ' rh__drawer--split' : ''}` +
          // A document needs every pixel of the pane; a field list does not.
          (shown && shown.kind !== 'document' ? ' rh__drawer--split-text' : '')
        }
        ref={ref}
        style={originStyle}
        role="dialog"
        aria-modal="true"
        aria-labelledby="rh-drawer-title"
      >
        {shown && (
          <PreviewPane finding={f} preview={shown} onClose={() => setOpenPreview(null)} />
        )}
        <div className="rh__pane">
        <header className="rh__drawer-head">
          <div>
            <p className="rh__badges" style={{ marginTop: 0 }}>
              <SeverityBadge sev={sev} />
              <ConfidenceBadge suspected={f.is_suspected} confidence={f.confidence} />
              {!f.can_act && <LockChip capability={f.required_capability} />}
            </p>
            <h2 className="rh__drawer-title" id="rh-drawer-title">
              {kind === 'payment' ? `Payments failed${vendorName ? ` to ${vendorName}` : ''}`
                : kind === 'fx' ? 'FX items to review'
                : f.title}
            </h2>
            {f.money_at_risk_inr && !kind ? (
              <p className="rh__meta" style={{ marginTop: 4 }}>
                <span className="tnum">{formatMoney(f.money_at_risk_inr)} at risk</span>
              </p>
            ) : null}
          </div>
          <IconButton label="Close" icon={<X size={18} />} onClick={onClose} />
        </header>

        <div className="rh__drawer-body">
          {/* Step 1 of the fixed vertical order: what's wrong, in plain
              sentences — the evidence below shows the rest. Section headings
              are omitted deliberately: for a finding this small, four labelled
              sections is more furniture than content. */}
          {kind && lineScope && (
            <RollupLines
              finding={f}
              kind={kind}
              lineScope={lineScope}
              canAct={f.can_act}
              onNavigate={onNavigate}
              onChanged={() => onApplied?.()}
              onEmpty={onClose}
              onLoaded={setLineIds}
            />
          )}

          {!kind && f.detail && <p className="rh__lede">{f.detail}</p>}

          {f.is_suspected && (
            <p className="rh__note">
              Raised by the agent and not confirmed against your data — a prompt to look,
              not a finding of fact.
            </p>
          )}

          {!kind && previews.length > 0 && (
            <div className="rh__previews" role="group" aria-label="Preview">
              {previews.map(p => {
                const Icon = PREVIEW_ICON[p.kind] || FileText
                const on = openPreview === p.id
                return (
                  <button
                    key={p.id}
                    type="button"
                    className={`rh__preview-btn${on ? ' rh__preview-btn--on' : ''}`}
                    aria-pressed={on}
                    onClick={() => setOpenPreview(on ? null : p.id)}
                  >
                    <Icon size={14} aria-hidden="true" />
                    {p.label}
                  </button>
                )
              })}
            </div>
          )}

          {/* Step 2: evidence. */}
          {!kind && Object.keys(f.evidence || {}).length > 0 && (
            <div className="rh__section">
              <EvidenceView evidence={f.evidence} finding={f} onApplied={onApplied} />
            </div>
          )}

          {/* Step 4: fix it here. Never reachable without scrolling past the
              evidence above — the fix's consequences are spelled out in the
              confirmation and, for every action, as a plain-language preview
              right under its button, so the after-state is known before it
              runs, not only after clicking. */}
          {!f.can_act && (
            <p className="rh__note">
              You can see this, but only {f.required_capability} can fix it. Let them know rather
              than approving anything next to it before they have.
            </p>
          )}
          <div className="rh__actions">
            {f.can_act &&
              runActions.map(a => (
                <span key={a.id} className="rh__fix-item">
                  <Button
                    // A conflict's "keep what we already have" is a real
                    // choice but not the change — muted, so the two do not
                    // read as interchangeable.
                    variant={a.emphasis === 'secondary' ? 'secondary' : 'primary'}
                    size="sm"
                    loading={busy}
                    // Fields first, then any confirmation: a correction has to be
                    // typed before there is anything to agree to. An approval
                    // always confirms — the server sends one — so it can never be
                    // committed by a single click in a queue.
                    onClick={() => {
                      if (a.fields?.length) {
                        setValues(Object.fromEntries(
                          a.fields.map(fl => [fl.key, fl.value == null ? '' : String(fl.value)]),
                        ))
                        setFilling(filling?.id === a.id ? null : a)
                      } else if (a.confirm) {
                        setConfirming(a)
                      } else {
                        onAct(a)
                      }
                    }}
                  >
                    {a.label}
                  </Button>
                  {a.preview && !a.fields?.length && (
                    <span className="rh__fix-preview">{a.preview}</span>
                  )}
                </span>
              ))}
          </div>

          {filling && (
            <ActionForm
              action={filling}
              values={values}
              busy={busy}
              onChange={(k, v) => setValues(prev => ({ ...prev, [k]: v }))}
              onCancel={() => setFilling(null)}
              onSubmit={() => {
                const submitted = values
                const a = filling
                setFilling(null)
                if (a.confirm) setConfirming({ ...a, body: { ...a.body, ...submitted } })
                else onAct(a, submitted)
              }}
            />
          )}

          {/* Step 5: or go to the source. Always last, below the fix — a deep
              link is the way out when there is nothing to fix here, never an
              alternative reached before the fix is even seen. */}
          {!kind && navActions.length > 0 && (
            <div className="rh__deeplinks">
              <p className="rh__deeplinks-label">Or go to the source</p>
              <p className="rh__deeplinks-row">
                {navActions.map(a => (
                  <Button
                    key={a.id}
                    variant="ghost"
                    size="sm"
                    iconRight={<ExternalLink size={14} />}
                    onClick={() => a.href && onNavigate(a.href)}
                  >
                    {a.label}
                  </Button>
                ))}
              </p>
            </div>
          )}

          {/* Provenance is a footnote, not a section. It matters when someone
              asks where a finding came from, and never before that. */}
          {!kind && <p className="rh__origin">
            {f.detector}
            {f.model ? ` · ${f.model}` : ''} · first seen {formatDateTime(f.first_detected_at)}
            {f.occurrence_count > 1 ? ` · seen ${f.occurrence_count} times` : ''}
            {f.events && f.events.length > 0 && (
              <>
                {' · '}
                <button
                  type="button"
                  className="rh__origin-more"
                  onClick={() => setShowHistory(v => !v)}
                >
                  {showHistory ? 'hide history' : 'history'}
                </button>
              </>
            )}
          </p>}

          {showHistory && f.events && (
            <ul className="rh__steps">
              {f.events.slice(0, 8).map((e, i) => (
                <li key={i} className="rh__step">
                  <span className="rh__step-label">
                    {e.type.toLowerCase().replace(/_/g, ' ')}
                  </span>
                  <span className="rh__step-actor">
                    {e.actor || 'system'} · {formatDateTime(e.at)}
                    {e.reason ? ` · ${e.reason}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <footer className="rh__drawer-foot">
          {kind && f.can_act ? (
            <>
              {/* Each line has its own Pay / Discard; these act on every line. */}
              <Button variant="secondary" size="sm" loading={allBusy} disabled={!lineIds.length}
                      onClick={() => void applyToAll('RESOLVED')}>
                Mark all resolved
              </Button>
              <span className="rh__foot-spacer" />
              <Button variant="ghost" size="sm" disabled={allBusy || !lineIds.length}
                      onClick={() => void applyToAll('SNOOZED')}>
                Snooze all a week
              </Button>
              <Button variant="ghost" size="sm" disabled={allBusy || !lineIds.length}
                      onClick={() => void applyToAll('DISMISSED')}>
                Discard all
              </Button>
            </>
          ) : f.can_act ? (
            <>
              <Button variant="primary" size="sm" disabled={busy}
                      onClick={() => onStatus('RESOLVED')}>
                Mark resolved
              </Button>
              <Button variant="secondary" size="sm" disabled={busy}
                      onClick={() => onStatus('SNOOZED', { snoozeDays: 7 })}>
                Snooze a week
              </Button>
              <Button variant="ghost" size="sm" disabled={busy}
                      onClick={() => setDismissing(true)}>
                Not an issue
              </Button>
            </>
          ) : (
            // Can see but cannot fix: the actions collapse to a lock chip
            // (shown in the header) plus a way to reach whoever can, never
            // hidden entirely.
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                const link = `${window.location.origin}${window.location.pathname}` +
                  `?f=${encodeURIComponent(f.finding_id)}`
                void navigator.clipboard?.writeText(link).catch(() => {})
                toast.show({
                  tone: 'info',
                  title: `Needs ${f.required_capability}`,
                  message: 'Link to this finding copied — send it to whoever holds that role.',
                })
              }}
            >
              Notify {f.required_capability}
            </Button>
          )}
        </footer>
        </div>
      </div>

      <DismissDialog
        open={dismissing}
        busy={busy}
        onCancel={() => setDismissing(false)}
        onConfirm={(reason: DismissReason) => {
          setDismissing(false)
          onStatus('DISMISSED', {
            reason,
            // "Never show me this kind again" is both the user's mute and the
            // signal that retires a noisy detector.
            suppressScope: reason === 'never_show_type' ? 'type_tenant_wide' : undefined,
          })
        }}
      />

      {confirming && (
        <ActionConfirm
          action={confirming}
          onCancel={() => setConfirming(null)}
          onConfirm={chosen => {
            setConfirming(null)
            onAct(chosen)
          }}
        />
      )}
    </>
  )
}


/**
 * The inputs an action carries, as a form under the button that opened it.
 *
 * Prefilled with what the system holds, because the common case is one wrong
 * character in an otherwise correct value — retyping an account number from
 * scratch to fix an IFSC is how a second mistake gets made. The field's own
 * pattern is checked here so a malformed value is caught where it was typed,
 * not by a 422 from a payment provider.
 */
function ActionForm({
  action, values, busy, onChange, onCancel, onSubmit,
}: {
  action: FindingAction
  values: Record<string, string>
  busy?: boolean
  onChange: (key: string, value: string) => void
  onCancel: () => void
  onSubmit: () => void
}) {
  const problems = action.fields.filter(fl => {
    const v = (values[fl.key] ?? '').trim()
    if (!v) return true
    return fl.pattern ? !new RegExp(fl.pattern).test(v) : false
  })

  return (
    <form
      className="rh__form"
      onSubmit={e => { e.preventDefault(); if (!problems.length) onSubmit() }}
    >
      {action.preview && <p className="rh__note">{action.preview}</p>}
      {action.fields.map(fl => {
        const v = values[fl.key] ?? ''
        const bad = v.trim() !== '' && fl.pattern && !new RegExp(fl.pattern).test(v.trim())
        return (
          <label key={fl.key} className="rh__form-row">
            <span className="rh__form-label">{fl.label}</span>
            <input
              className={`rh__form-input${bad ? ' rh__form-input--bad' : ''}`}
              value={v}
              placeholder={fl.placeholder}
              onChange={e => onChange(fl.key, e.target.value)}
              aria-invalid={bad || undefined}
              aria-describedby={fl.hint ? `rh-hint-${fl.key}` : undefined}
            />
            {(bad || fl.hint) && (
              <span
                id={`rh-hint-${fl.key}`}
                className={`rh__form-hint${bad ? ' rh__form-hint--bad' : ''}`}
              >
                {bad ? `That does not look like a ${fl.label.toLowerCase()}. ${fl.hint}` : fl.hint}
              </span>
            )}
          </label>
        )
      })}
      <div className="rh__form-actions">
        <Button type="submit" variant="primary" size="sm"
                loading={busy} disabled={problems.length > 0}>
          {action.label}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  )
}
