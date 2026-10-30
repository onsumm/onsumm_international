/**
 * The source page, shown beside the finding.
 *
 * For anything that turns on "did we read this right?" — a misread GSTIN, a
 * line with no quantity — the user's real question is *what does the invoice
 * actually say*. Sending them to the Bills screen to answer it ends the triage
 * session and usually the visit. So the page comes here.
 *
 * Served by GET /api/docs/{upload_id} through useAuthedObjectUrl, which fetches
 * with the bearer header and hands the browser a blob: URL — an <embed src>
 * cannot set an Authorization header, and putting the token in the query string
 * leaks a 60-minute credential into history, logs and the Referer header (see
 * api/authedFiles.ts).
 *
 * Naming what to look for isn't enough on its own — a printed A4 page has
 * hundreds of characters and the eye has no reason to land on the right 15 of
 * them. So the page is rendered with pdf.js (via react-pdf, already used by
 * UploadPage/ReviewPage/BillsPage) instead of a bare <iframe>: its text layer
 * lets us find the highlighted value ourselves, jump to the page it's actually
 * on, and mark it, rather than leaving the user to re-read the whole invoice.
 */
import { useEffect, useRef, useState } from 'react'
import { FileWarning, Search, X } from 'lucide-react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

import type { Finding } from '../../api/review'
import { useAuthedObjectUrl } from '../../api/authedFiles'
import { IconButton, LoadingMark } from '../../ui'
import { BillLink } from '../../components/records/RecordLink'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

// GSTINs and invoice numbers get OCR'd and typeset with inconsistent spacing;
// comparing on letters and digits alone is what makes the match reliable.
const normalize = (value: string) => value.replace(/[^a-zA-Z0-9]+/g, '').toUpperCase()

async function findHighlightPage(pdf: any, needle: string, startPage?: number): Promise<number | null> {
  const target = normalize(needle)
  if (!target) return null

  const order: number[] = []
  if (startPage) order.push(startPage)
  for (let i = 1; i <= pdf.numPages; i++) {
    if (i !== startPage) order.push(i)
  }

  for (const pageNum of order) {
    const page = await pdf.getPage(pageNum)
    const content = await page.getTextContent()
    const text = normalize(
      content.items.map((item: any) => (typeof item.str === 'string' ? item.str : '')).join(' '),
    )
    if (text.includes(target)) return pageNum
  }
  return null
}

/**
 * Why this does not say "it may have been removed since this bill was
 * processed": that was asserted for every failure, and the failure it was
 * asserted for most often was a deployed build fetching the document from the
 * frontend's own origin (see withApiBase in api/authedFiles.ts). A removed
 * document and a misrouted request are indistinguishable from here, so the
 * message names what is true — it did not load — and offers the way to check.
 */
function DocFailed({ billId }: { billId: string | null }) {
  return (
    <div className="rh__doc-state">
      <FileWarning size={20} aria-hidden="true" />
      <p className="rh__note">
        The original document could not be loaded. It may have been removed, or
        it may not be reachable from here right now.
      </p>
      {billId && (
        // The bill card, over the hub — the finding stays open behind it.
        <BillLink invoiceId={billId} className="rh__scope-more">
          Open the bill instead
        </BillLink>
      )}
    </div>
  )
}

export function DocumentPane(
  { document: doc, onClose, billId = null }: {
    document: NonNullable<Finding['document']>
    onClose?: () => void
    /** The bill to offer when the preview cannot be shown at all. */
    billId?: string | null
  },
) {
  const file = useAuthedObjectUrl(`/api/docs/${encodeURIComponent(doc.upload_id)}`)
  const bodyRef = useRef<HTMLDivElement | null>(null)
  const [pdf, setPdf] = useState<any>(null)
  const [pageNum, setPageNum] = useState(doc.page || 1)
  const [pageWidth, setPageWidth] = useState(640)
  const [found, setFound] = useState<boolean | null>(null)

  useEffect(() => {
    setPageNum(doc.page || 1)
    setFound(null)
  }, [doc.upload_id, doc.highlight, doc.page])

  useEffect(() => {
    const el = bodyRef.current
    if (!el) return
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width
      if (w) setPageWidth(Math.max(320, Math.floor(w) - 32))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (!pdf || !doc.highlight) return
    let cancelled = false
    findHighlightPage(pdf, doc.highlight, doc.page).then((page) => {
      if (cancelled) return
      if (page) setPageNum(page)
      else setFound(false)
    })
    return () => {
      cancelled = true
    }
  }, [pdf, doc.highlight, doc.page])

  const handleTextLayerRendered = () => {
    if (!doc.highlight || !bodyRef.current) return
    const needle = normalize(doc.highlight)
    const spans = bodyRef.current.querySelectorAll<HTMLSpanElement>('.react-pdf__Page span')
    let matched: HTMLSpanElement | null = null
    spans.forEach((span) => {
      const text = normalize(span.textContent || '')
      if (text && text.includes(needle)) {
        span.classList.add('rh__doc-highlight')
        if (!matched) matched = span
      }
    })
    if (matched) {
      setFound(true)
      ;(matched as HTMLSpanElement).scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' })
    }
  }

  return (
    <aside className="rh__doc" aria-label="The source document">
      <header className="rh__doc-head">
        <span className="rh__doc-name">{doc.label || 'Source document'}</span>
        {doc.highlight && (
          <span className={`rh__doc-hint${found === false ? ' rh__doc-hint--miss' : ''}`}>
            <Search size={12} aria-hidden="true" />
            {found === false ? "not found on the page — " : ''}
            look for <span className="rh__mono">{doc.highlight}</span>
          </span>
        )}
        {onClose && (
          <IconButton label="Close the preview" icon={<X size={15} />} onClick={onClose} />
        )}
      </header>

      <div className="rh__doc-body rh__doc-body--scroll" ref={bodyRef}>
        {file.loading && (
          <div className="rh__doc-state">
            <LoadingMark />
          </div>
        )}

        {file.error && <DocFailed billId={billId} />}

        {file.url && (
          <Document
            key={file.url}
            file={file.url}
            onLoadSuccess={setPdf}
            className="rh__doc-pdf"
            loading={
              <div className="rh__doc-state">
                <LoadingMark />
              </div>
            }
            error={<DocFailed billId={billId} />}
          >
            <Page
              pageNumber={pageNum}
              width={pageWidth}
              renderAnnotationLayer={false}
              renderTextLayer
              onRenderTextLayerSuccess={handleTextLayerRendered}
            />
          </Document>
        )}
      </div>
    </aside>
  )
}
