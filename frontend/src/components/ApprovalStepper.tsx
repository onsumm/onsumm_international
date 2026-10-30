/**
 * The approval stepper — where a bill is and who has it.
 *
 * Lifted out of BillsPage unchanged so the Review Hub can show the same chain
 * in its drawer. Importing it from BillsPage instead would have pulled that
 * module's whole chunk — react-pdf included — into the review bundle, which is
 * the split App.tsx goes out of its way to preserve.
 *
 * The chain-config cache is module state shared with `prefetchChains`, so both
 * live here: a second copy in another module would mean two caches and a
 * stepper that renders a skeleton for a chain the page has already fetched.
 */
import React, { useEffect, useState } from 'react'
import { Check, CornerDownLeft } from 'lucide-react'

import { fetchChainConfig, type ChainEntry } from '../api/invoices'
import {
  _chainCache, buildStatusActiveStep, chainCacheKey, stepStateFromChain,
} from './approvalChain'
import './approvalStepper.css'

/** One audit row. Re-exported so callers need not reach into the bills API for
 *  the shape of this component's own input. */
export type ChainRow = ChainEntry

export function ApprovalStepper({ chain, status, onViewChain, companyId, department }: {
  chain: ChainEntry[]; status: string; onViewChain?: () => void
  companyId?: string; department?: string | null
}) {
  const cacheKey = chainCacheKey(companyId, department)
  const [apiSteps, setApiSteps] = useState<{ id: number; label: string; role: string; assignees?: string[] }[] | null>(
    _chainCache.get(cacheKey) ?? null
  )

  useEffect(() => {
    const cached = _chainCache.get(cacheKey)
    if (cached) { setApiSteps(cached); return }
    fetchChainConfig(companyId, department ?? undefined).then(steps => {
      if (steps.length > 0) {
        const mapped = steps.map((s, i) => ({ id: i + 1, label: s.label, role: s.role, assignees: s.assignees ?? [] }))
        _chainCache.set(cacheKey, mapped)
        setApiSteps(mapped)
      }
    }).catch(() => {})
  // cacheKey is derived from both, so listing them again would only re-run the
  // effect for a key that has not changed.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cacheKey])

  // Skeleton while chain is loading
  if (!apiSteps) return (
    <div className="bm__stepper-skel" style={{ padding: '20px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
        {[1, 2, 3, 4].map((_, i) => (
          <React.Fragment key={i}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, minWidth: 80 }}>
              <div className="bp__skel" style={{ width: 28, height: 28, borderRadius: '50%' }} />
              <div className="bp__skel" style={{ width: 64, height: 10 }} />
              <div className="bp__skel" style={{ width: 48, height: 8 }} />
            </div>
            {i < 3 && <div className="bp__skel" style={{ flex: 1, height: 3, borderRadius: 2, margin: '0 4px', marginBottom: 28 }} />}
          </React.Fragment>
        ))}
      </div>
    </div>
  )

  const chainSteps       = apiSteps
  const statusActiveStep = buildStatusActiveStep(apiSteps)

  const stepMeta = chainSteps.map(step => {
    const state         = stepStateFromChain(step.id, chain, status, statusActiveStep)
    const isDone        = state === 'approved' || state === 'submitted'
    const sendBackCount = chain.filter(c => c.step === step.id && c.action === 'send_back').length
    return { state, isDone, sendBackCount, hadRevision: sendBackCount > 0 }
  })

  // Detect the most recent send-back: origin step and destination step
  const lastSendBack = [...chain].reverse().find(c => c.action === 'send_back')
  const activeStepId = statusActiveStep[status] ?? 0
  // A connector is "skipped" if it falls in the jump zone between where the send-back
  // landed (target_step) and where it originated — regardless of current activeStepId.
  const isConnectorSkipped = (fromStepId: number, toStepId: number): boolean => {
    if (!lastSendBack) return false
    const sendBackOrigin = lastSendBack.step  // step that sent it back
    // target_step is stored in DB; fall back to activeStepId for older entries
    const landedAt = lastSendBack.target_step != null ? lastSendBack.target_step : activeStepId
    // connector is skipped if it lies strictly between landedAt and sendBackOrigin
    return landedAt <= fromStepId && toStepId <= sendBackOrigin && sendBackOrigin > landedAt
  }

  return (
    <div className="bm__stepper-wrap">
      <div className="bm__stepper">
        {(() => {
          return chainSteps.map((step, i) => {
            const { state, isDone, sendBackCount, hadRevision } = stepMeta[i]
            const isActive   = statusActiveStep[status] === step.id

            const actorEntry = chain.filter(c => c.step === step.id && (c.action === 'approve' || c.action === 'approved' || c.action === 'submit' || c.action === 'submitted')).slice(-1)[0]
              ?? chain.filter(c => c.step === step.id && c.action === 'send_back').slice(-1)[0]

            let dot = <span className="bm__dot bm__dot--idle" />
            if (isDone)                    dot = <span className="bm__dot bm__dot--ok"><Check size={9} strokeWidth={3} /></span>
            else if (state === 'sent_back') dot = <span className="bm__dot bm__dot--back"><CornerDownLeft size={9} strokeWidth={2.5} /></span>
            else if (isActive)             dot = <span className="bm__dot bm__dot--active" />

            return (
              <React.Fragment key={step.id}>
                <div className="bm__step">
                  <div style={{ position: 'relative', display: 'inline-flex' }}>
                    {dot}
                    {hadRevision && (
                      <span title={`Sent back ${sendBackCount}×`} style={{
                        position: 'absolute', top: -4, right: -5,
                        background: '#f59e0b', color: '#fff',
                        borderRadius: '50%', width: 13, height: 13,
                        fontSize: 8, fontWeight: 700,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        border: '1.5px solid #fff', lineHeight: 1,
                      }}>{sendBackCount}</span>
                    )}
                  </div>
                  <span className={`bm__step-lbl ${isActive ? 'bm__step-lbl--active' : ''} ${state === 'sent_back' ? 'bm__step-lbl--back' : ''} ${isDone ? 'bm__step-lbl--done' : ''}`}>
                    {step.label}
                  </span>
                  {actorEntry?.actor_name ? (
                    // Already acted — show who did it
                    <span className="bm__step-actor">
                      <span className="bm__step-actor-name">{actorEntry.actor_name}</span>
                      {actorEntry.actor_role && <span className="bm__step-actor-role">{actorEntry.actor_role.replace(/_/g, ' ')}</span>}
                    </span>
                  ) : (step.assignees ?? []).length > 0 && !isDone ? (
                    // Pending/idle step with specific assignees — show who will approve
                    <span className="bm__step-actor">
                      {(step.assignees ?? []).map((name, ni) => (
                        <span key={ni} className="bm__step-actor-name" style={{ color: isActive ? '#1d4ed8' : 'var(--ink-500)' }}>
                          {name}{ni < (step.assignees ?? []).length - 1 ? ', ' : ''}
                        </span>
                      ))}
                      <span className="bm__step-actor-role" style={{ color: isActive ? '#93c5fd' : '#d1d5db' }}>
                        {step.role.replace(/_/g, ' ')}
                      </span>
                    </span>
                  ) : null}
                </div>
                {i < chainSteps.length - 1 && (() => {
                  const nextStep = chainSteps[i + 1]
                  const skipped = isConnectorSkipped(step.id, nextStep.id)
                  if (skipped) {
                    return (
                      <div style={{ flex: 1, display: 'flex', alignItems: 'center', marginTop: 11, alignSelf: 'flex-start', minWidth: 12, position: 'relative' }}
                        title="Sent back and re-approved (skipped)">
                        {/* Left arrow: send-back direction */}
                        <svg width="8" height="10" viewBox="0 0 8 10" style={{ flexShrink: 0 }}>
                          <path d="M7 1L1 5L7 9" stroke="#f97316" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
                        </svg>
                        {/* Dashed line */}
                        <div style={{
                          flex: 1,
                          height: 3,
                          backgroundImage: 'repeating-linear-gradient(90deg, #f97316 0, #f97316 6px, transparent 6px, transparent 12px)',
                          backgroundSize: '12px 3px',
                          backgroundRepeat: 'repeat-x',
                          backgroundPosition: 'center',
                        }} />
                        {/* Right arrow: re-approval direction */}
                        <svg width="8" height="10" viewBox="0 0 8 10" style={{ flexShrink: 0 }}>
                          <path d="M1 1L7 5L1 9" stroke="#f97316" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
                        </svg>
                      </div>
                    )
                  }
                  return (
                    <div
                      className={`bm__connector ${isDone ? 'bm__connector--done' : ''}`}
                    />
                  )
                })()}
              </React.Fragment>
            )
          })
        })()}
        {/* Done node */}
        <div className={`bm__connector ${status === 'APPROVED' ? 'bm__connector--done' : ''}`} />
        <div className="bm__step">
          <span className={`bm__dot ${status === 'APPROVED' ? 'bm__dot--ok' : 'bm__dot--idle'}`}>
            {status === 'APPROVED' && <Check size={9} strokeWidth={3} />}
          </span>
          <span className={`bm__step-lbl ${status === 'APPROVED' ? 'bm__step-lbl--done bm__step-lbl--active' : ''}`}>Done</span>
        </div>
      </div>
      {/* Send-back notice — shown when the last action was a send_back */}
      {(() => {
        const lastAct = [...chain].reverse().find(c => c.action !== 'priority_change')
        if (!lastAct || lastAct.action !== 'send_back') return null
        // Find which step the bill is now waiting at
        const activeStepId = statusActiveStep[status] ?? 0
        const targetStep = chainSteps.find(s => s.id === activeStepId)
        const targetNames = targetStep?.assignees?.length
          ? targetStep.assignees.join(', ')
          : targetStep?.label ?? status.replace('PENDING_', '').replace(/_/g, ' ').toLowerCase()
        const fromStep = chainSteps.find(s => s.id === lastAct.step)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '6px 0 2px', padding: '7px 12px', borderRadius: 8, background: '#fff7ed', border: '1px solid #fed7aa' }}>
            <CornerDownLeft size={13} color="#ea580c" style={{ flexShrink: 0 }} />
            <span style={{ fontSize: 12, color: '#9a3412' }}>
              <b>{lastAct.actor_name}</b> sent back from <b>{fromStep?.label ?? 'step ' + lastAct.step}</b> → now waiting for <b>{targetNames}</b>
              {lastAct.comment ? <span style={{ color: '#c2410c' }}> — "{lastAct.comment}"</span> : null}
            </span>
          </div>
        )
      })()}

      {chain.length > 0 && onViewChain && (
        <button className="bm__view-chain" onClick={onViewChain}>
          View activity ({chain.length})
        </button>
      )}
    </div>
  )
}
