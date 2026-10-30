/**
 * The subject, opened beside the finding rather than instead of it.
 *
 * "Who is sitting on this?", "what is this bill?" and "does the paper say what
 * we think it says?" are the three questions a stalled bill raises, and all
 * three used to be answered by leaving for the Bills screen — which ends the
 * triage session, takes the queue and the scroll position with it, and in
 * practice the visit too.
 *
 * Like everything else in this folder it dispatches on *shape*, never on
 * problem type: the server names a preview and its kind, `document` renders the
 * stored page and anything else renders through EvidenceView, so a preview
 * added server-side tomorrow needs no change here.
 */
import { useEffect, useState } from 'react'
import { FileWarning, X } from 'lucide-react'

import { type Finding, type FindingPreview, fetchFindingPreview } from '../../api/review'
import { useAuth } from '../../auth/AuthContext'
import { ApprovalStepper, type ChainRow } from '../../components/ApprovalStepper'
import { IconButton, LoadingMark } from '../../ui'
import { DocumentPane } from './DocumentPane'
import { EvidenceView } from './EvidenceView'

interface Props {
  finding: Finding
  preview: FindingPreview
  onClose: () => void
}

export function PreviewPane({ finding, preview, onClose }: Props) {
  if (preview.kind === 'document' && preview.document) {
    // Somewhere to go when the page itself will not render. The bill is the
    // one place the document is certain to be reachable from.
    const bill = finding.entities.find(e => e.type === 'bill' || e.type === 'invoice')
    return (
      <DocumentPane
        document={preview.document}
        onClose={onClose}
        billId={bill?.id ?? null}
      />
    )
  }
  return <FetchedPreview finding={finding} preview={preview} onClose={onClose} />
}

function FetchedPreview({ finding, preview, onClose }: Props) {
  const [evidence, setEvidence] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState('')

  // Re-fetched whenever the finding or the preview changes, and never cached:
  // the reason to show the chain here is that it is the chain as of now. A
  // stale approval is exactly the thing this is meant to settle.
  useEffect(() => {
    let live = true
    setEvidence(null)
    setError('')
    fetchFindingPreview(finding.finding_id, preview.id)
      .then(r => { if (live) setEvidence(r.evidence) })
      .catch(e => { if (live) setError(e instanceof Error ? e.message : 'Could not load') })
    return () => { live = false }
  }, [finding.finding_id, preview.id])

  return (
    <aside className="rh__doc" aria-label={preview.label}>
      <header className="rh__doc-head">
        <span className="rh__doc-name">{preview.label}</span>
        <IconButton label="Close the preview" icon={<X size={15} />} onClick={onClose} />
      </header>

      <div className="rh__doc-body rh__doc-body--scroll">
        {!evidence && !error && (
          <div className="rh__doc-state"><LoadingMark /></div>
        )}
        {error && (
          <div className="rh__doc-state">
            <FileWarning size={20} aria-hidden="true" />
            <p className="rh__note">{error}</p>
          </div>
        )}
        {evidence && (
          <div className="rh__doc-content">
            {evidence.kind === 'approval_chain'
              ? <ApprovalChainPreview evidence={evidence} />
              : <EvidenceView evidence={evidence} />}
          </div>
        )}
      </div>
    </aside>
  )
}


/**
 * The chain drawn exactly as the Bills screen draws it.
 *
 * The same component, not a copy: a second stepper would answer "where is this
 * bill and who has it" in its own slightly different way, and two answers to
 * that question is worse than one in the wrong place. The activity list under
 * it is the audit trail — who acted, when, and what they wrote.
 */
function ApprovalChainPreview({ evidence }: { evidence: Record<string, unknown> }) {
  const { user } = useAuth()
  const chain = (Array.isArray(evidence.chain) ? evidence.chain : []) as ChainRow[]
  const status = typeof evidence.status === 'string' ? evidence.status : ''
  const department = typeof evidence.department === 'string' ? evidence.department : null
  const activity = evidence.activity as Record<string, unknown> | undefined

  return (
    <>
      <ApprovalStepper
        chain={chain}
        status={status}
        companyId={user?.company_id}
        department={department}
        // The stepper's own button toggles the Bills modal's history panel.
        // Here the activity is already below it, so there is nothing to toggle.
        onViewChain={undefined}
      />
      {activity && <EvidenceView evidence={activity} />}
    </>
  )
}
