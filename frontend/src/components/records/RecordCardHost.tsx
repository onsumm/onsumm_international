import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { setReviewContext } from '../../api/reviewContext'
import GuideSpot from './GuideSpot'
import { useRecordCards } from './recordLinks'
import RecordCardStatus from './RecordCardStatus'
import './records.css'

// The cards are the Bills and Vendors pages' own modals, so importing them here
// eagerly would pull both pages into the shell bundle. They load on first open.
const BillCard = lazy(() => import('./BillCard'))
const VendorCard = lazy(() => import('./VendorCard'))

/**
 * Renders whichever card `?open=` names, over the current page.
 *
 * Mounted once in the app shell. Its own stacking layer sits above the pages'
 * own cards, so a vendor opened from the Bills page's bill modal is not hidden
 * behind it.
 */
export default function RecordCardHost() {
  const { current, guide, corrections, prefill, claim, findingId, open, close, openBill } = useRecordCards()
  // While a card opened from a Review finding is up, every change it makes is
  // tagged with that finding for the audit log.
  useEffect(() => {
    setReviewContext(current && findingId ? { findingId, surface: `${current.kind}-card` } : null)
    return () => setReviewContext(null)
  }, [current?.kind, current?.id, findingId])
  // "Got it" (or using the control) ends the guide for this visit to the card,
  // tab switches included. Opening the card again is a new visit with its own
  // openId, so a problem that was waved off but never fixed is pointed at again.
  const [doneGuides, setDoneGuides] = useState<Set<string>>(() => new Set())
  const guideKey = current && guide
    ? `${current.kind}:${current.id}:${guide.openId ?? guide.targets.join('|')}`
    : null
  const finishGuide = useCallback(() => {
    if (guideKey) setDoneGuides(prev => new Set(prev).add(guideKey))
  }, [guideKey])
  if (!current) return null

  return (
    <div className="rc__layer">
      <Suspense fallback={<RecordCardStatus label={current.kind} onClose={close} />}>
        {current.kind === 'bill' ? (
          <BillCard key={current.id} invoiceId={current.id} onClose={close}
                    correctionsFor={corrections?.findingId} />
        ) : (
          <VendorCard
            key={current.id}
            supplierId={current.id}
            tab={current.tab ?? 'details'}
            // A tab switch replaces the entry, so Back closes the card instead of
            // walking back through every tab that was looked at.
            onTabChange={tab => open({ kind: 'vendor', id: current.id, tab }, { replace: true })}
            onClose={close}
            onOpenBill={id => openBill(id)}
            prefill={prefill ?? undefined}
            claimFor={claim?.findingId}
          />
        )}
      </Suspense>
      {guide && guideKey && !doneGuides.has(guideKey) && (
        <GuideSpot key={guideKey} guide={guide} onDone={finishGuide} />
      )}
    </div>
  )
}
