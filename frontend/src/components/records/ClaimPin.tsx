import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, Check, X } from 'lucide-react'
import { fetchFinding, runAction, setFindingStatus, type Finding, type FindingAction } from '../../api/review'
import { useFixHosts } from './CorrectionPins'
import { REVIEW_CHANGED_EVENT } from './reviewEvents'
import { CLAIM_CONFIG, type ClaimRow } from './claimFields'

/**
 * A value a vendor contract states, built into the vendor card's field.
 *
 * The same in-field box as a bill's corrections — what is on file struck
 * through, what the contract says as a chip — but this is the agent's reading
 * of a document, not a confirmed fact, so nothing changes on one click: ✓
 * asks first ("This came from a contract and hasn't been checked…"), ✕ keeps
 * what is on file and discards the contract's claim. Either way the review
 * item closes and the card reloads.
 */



export default function ClaimPin({ findingId, onApplied, onDone }: {
  findingId: string
  /** The vendor was changed; the card needs to read it again. */
  onApplied: () => void
  onDone: () => void
}) {
  const [finding, setFinding] = useState<Finding | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    fetchFinding(findingId).then(f => live && setFinding(f)).catch(() => {})
    return () => { live = false }
  }, [findingId])

  const cfg = finding ? CLAIM_CONFIG[finding.finding_type] : undefined
  const field = cfg?.field
  const hosts = useFixHosts(field ? [field] : [])
  const h = field ? hosts[field] : null

  // The field's own value steps aside for the box while it is up.
  useEffect(() => {
    if (h) h.anchor.dataset.fixState = 'pending'
  }, [h])

  // Straight to the field, in the middle of the card.
  useEffect(() => {
    if (!h) return
    const t = window.setTimeout(() => h.anchor.scrollIntoView({ block: 'center', behavior: 'smooth' }), 450)
    return () => window.clearTimeout(t)
  }, [h])

  if (!finding || !cfg || !field || !h) return null

  const row = ((finding.evidence as { rows?: ClaimRow[] })?.rows ?? [])[0]
  const useIt = finding.actions.find(a => a.id === cfg.useAction)
  const keep = cfg.keepAction ? finding.actions.find(a => a.id === cfg.keepAction) : undefined
  if (!row || !useIt) return null
  const current = cfg.current(row)
  const proposed = cfg.proposed(row)

  const run = async (action: FindingAction | undefined, changed: boolean) => {
    setBusy(true); setError('')
    try {
      if (action) await runAction(finding, action)
      // Kept as it is with no action of its own to say so: closed as "not an
      // issue", so the same thing is not raised again.
      await setFindingStatus(finding.finding_id, changed || action
        ? { status: 'RESOLVED' }
        : { status: 'DISMISSED', reason: 'not_an_issue', suppress_scope: 'exact_key' }).catch(() => {})
      window.dispatchEvent(new CustomEvent(REVIEW_CHANGED_EVENT))
      if (changed) onApplied()
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save this change')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {createPortal(
        <span className="rc__fix-acts">
          <button type="button" className="rc__fix-btn rc__fix-btn--use" disabled={busy}
                  onClick={() => (cfg.suspected ? setConfirming(true) : void run(useIt, true))}
                  aria-label={`Change ${row.label} to ${proposed}`}
                  title={`Change to ${proposed}`}>
            <Check size={13} strokeWidth={3} />
          </button>
          <button type="button" className="rc__fix-btn rc__fix-btn--keep" disabled={busy}
                  onClick={() => void run(keep, false)} aria-label={`Keep ${current}`}
                  title={`Keep ${current}`}>
            <X size={13} strokeWidth={3} />
          </button>
        </span>,
        h.actions,
      )}
      {createPortal(
        <div className="rc__fix-box rc__fix-box--pending">
          <div className="rc__fix-old">{current || 'Not set'}</div>
          <div className="rc__fix-line">
            <span className="rc__fix-new">{proposed}</span>
          </div>
          <p className="rc__claim-note">
            {cfg.suspected && <AlertTriangle size={12} aria-hidden="true" />}
            {cfg.note(row)}
          </p>
          {confirming && (
            <div className="rc__claim-confirm" role="alertdialog" aria-label="Confirm change">
              <p>
                This came from a contract and hasn’t been checked. Change {row.label} from{' '}
                <b>{current || 'not set'}</b> to <b>{proposed}</b>?
              </p>
              <div className="rc__claim-confirm-acts">
                <button type="button" className="rc__claim-yes" disabled={busy}
                        onClick={() => void run(useIt, true)}>
                  {busy ? 'Saving…' : 'Yes, change it'}
                </button>
                <button type="button" className="rc__claim-no" disabled={busy}
                        onClick={() => setConfirming(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
          {error && <p className="rc__claim-err" role="alert">{error}</p>}
        </div>,
        h.box,
      )}
    </>
  )
}
