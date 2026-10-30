import type { ReactNode, MouseEvent } from 'react'
import type { VendorTab } from '../../routes/paths'
import { useRecordCards, type RecordRef } from './recordLinks'

/**
 * A link that opens a bill or vendor card over the current page.
 *
 * A real href, so cmd/ctrl-click and "copy link" give a URL that opens the same
 * card; a plain click opens it in place.
 */
function RecordLink({ record, className, title, children }: {
  record: RecordRef
  className?: string
  title?: string
  children: ReactNode
}) {
  const { hrefFor, open } = useRecordCards()
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    e.stopPropagation() // links often sit inside clickable table rows
    open(record)
  }
  return (
    <a href={hrefFor(record)} onClick={onClick} className={className ?? 'rc__link'} title={title}>
      {children}
    </a>
  )
}

export function BillLink({ invoiceId, ...rest }: { invoiceId: string; className?: string; title?: string; children: ReactNode }) {
  return <RecordLink record={{ kind: 'bill', id: invoiceId }} {...rest} />
}

export function VendorLink({ supplierId, tab, ...rest }: { supplierId: string; tab?: VendorTab; className?: string; title?: string; children: ReactNode }) {
  return <RecordLink record={{ kind: 'vendor', id: supplierId, tab }} {...rest} />
}
