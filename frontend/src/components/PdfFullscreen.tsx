import { useRef, useState } from 'react'
import { Document, pdfjs } from 'react-pdf'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { useAuthedObjectUrl } from '../api/authedFiles'
import { BillPdfFullscreen } from '../pages/BillPdfFullscreen'
import '../pages/billPdfFullscreen.css'

// Same worker the bill and vendor pages point react-pdf at; set here too since
// this can be the first PDF a screen opens. Unconditionally: react-pdf ships a
// default ('pdf.worker.mjs') that the browser can't resolve, so "only if unset"
// never runs.
pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

/**
 * Any PDF, opened in the bill's full-screen viewer — the one view every
 * "just looking at the PDF" screen uses, so zoom, pinch, paging and Esc work
 * the same everywhere.
 *
 * Takes a URL (fetched with the login token) rather than a parsed document,
 * and flies out of whatever had focus when it opened: the row or button that
 * was clicked.
 */
export default function PdfFullscreen({ url, name, fromRect, onClose }: {
  /** A protected API path or absolute URL; a blob: URL is used as is; null while the caller is still fetching it. */
  url: string | null
  /** Where it was opened from, when that isn't the focused element (a clicked row). */
  fromRect?: DOMRect | null
  /** The file name, for the screen reader label. */
  name?: string
  onClose: () => void
}) {
  const isBlob = !!url?.startsWith('blob:')
  const authed = useAuthedObjectUrl(url && !isBlob ? url : null)
  const src = isBlob ? url : authed.url
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null)
  const [failed, setFailed] = useState(false)
  const [pageNum, setPageNum] = useState(1)
  // Captured on open, before focus moves into the viewer.
  const origin = useRef<HTMLElement | null>(
    fromRect
      ? ({ getBoundingClientRect: () => fromRect } as HTMLElement)
      : typeof document !== 'undefined' && document.activeElement instanceof HTMLElement && document.activeElement !== document.body
        ? document.activeElement : null,
  )

  // The viewer opens at once; the page fades in when it's ready, so loading
  // time reads as part of the opening rather than a spinner in front of it.
  return (
    <>
      {/* Loaded the same way the bill pane loads its PDF (react-pdf's Document),
          which also frees the document when the viewer closes. Renders nothing. */}
      {src && (
        <div hidden>
          <Document file={src} loading={null} error={null} noData={null}
                    onLoadSuccess={setPdf} onLoadError={() => setFailed(true)} />
        </div>
      )}
      <BillPdfFullscreen
        pdf={pdf} sourceRef={origin as React.RefObject<HTMLElement>} pageNum={pageNum} numPages={pdf?.numPages ?? 0}
        onPage={setPageNum} onClose={onClose} error={failed || authed.error}
        label={name ? `${name}, full screen` : 'Document, full screen'}
      />
    </>
  )
}
