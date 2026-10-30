import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { PHONE_QUERY, useMediaQuery } from '../hooks/useMediaQuery'

/**
 * A titled group of bill-detail fields.
 *
 * On a laptop it is the plain section divider it always was: the fields are
 * cells of the surrounding `.bm__grid` and nothing folds. On a phone the bill
 * card is one long column, so the heading becomes a toggle and groups an
 * approver rarely needs (`closedOnPhone`) start folded — the card opens on the
 * amount, the vendor and the decision.
 */
export default function BillSection({
  title,
  aside,
  closedOnPhone = false,
  forceOpen = false,
  children,
}: {
  title: string
  /** Shown beside the title, e.g. an "Inter-state" chip. */
  aside?: ReactNode
  closedOnPhone?: boolean
  /** Editing: every input has to stay reachable. */
  forceOpen?: boolean
  children: ReactNode
}) {
  const isPhone = useMediaQuery(PHONE_QUERY)
  const [open, setOpen] = useState(() => !(closedOnPhone && isPhone))
  const shown = open || forceOpen

  if (!isPhone) return (
    <>
      <div className="bm__section" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <span>{title}</span>
        {aside}
      </div>
      {children}
    </>
  )

  return (
    <div className={`bm__group${shown ? '' : ' bm__group--closed'}`}>
      <button
        type="button"
        className="bm__section bm__group-head"
        aria-expanded={shown}
        disabled={forceOpen}
        onClick={() => setOpen(o => !o)}
      >
        <span className="bm__group-title">{title}</span>
        {aside}
        <ChevronDown size={14} className="bm__group-chev" aria-hidden="true" />
      </button>
      <div className="bm__group-body">{children}</div>
    </div>
  )
}
