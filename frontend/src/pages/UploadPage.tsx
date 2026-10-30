import { useCallback, useState, useEffect, useMemo, useRef } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { useDropzone } from 'react-dropzone'
import { Document, Page, pdfjs } from 'react-pdf'
import {
  Upload, CheckCircle2, Zap,
  FileText, FileX, X, AlertCircle, Bot,
} from 'lucide-react'
import { GridGlow } from '../components/GridGlow'
import { ConfirmDialog, formatMoney } from '../ui'
import { LoadingMark } from '../ui'
import { ocrInvoice } from '../api/invoices'
import { useJurisdiction } from '../auth/EntitlementsContext'

// Same worker wiring as the Review and Bills viewers.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()
import type { InvoicePayload } from '../api/invoices'

// Mirrors MAX_PDF_PAGES in config.py; the server rejects longer files anyway,
// and checking here just says so before the upload instead of after it.
const MAX_PDF_PAGES = 20

/** Page count read from the PDF itself, or null if pdf.js cannot open it. */
async function countPdfPages(file: File): Promise<number | null> {
  try {
    const task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
    const doc = await task.promise
    const n = doc.numPages
    void task.destroy()
    return n
  } catch {
    return null
  }
}

// skipped: looked at and not an invoice. Not a failure, so it isn't shown as one.
type FileStatus = 'pending' | 'processing' | 'done' | 'error' | 'skipped'
interface QueueEntry {
  file: File
  status: FileStatus
  result?: InvoicePayload
  error?: string
}

// The progress copy names Indian invoice fields only where GST applies.
const PARSE_FIELDS_INTL = [
  { field: 'Document Layout',   detail: 'Analyzing page structure, headers & tables' },
  { field: 'Vendor Details',    detail: 'Identifying supplier name, address & tax ID' },
  { field: 'Invoice Reference', detail: 'Locating invoice number, PO number & date' },
  { field: 'Tax Breakdown',     detail: 'Extracting VAT / sales tax and totals' },
  { field: 'Line Items',        detail: 'Parsing item descriptions, codes, qty & rates' },
  { field: 'Amount Totals',     detail: 'Verifying sub-total, tax & grand total math' },
  { field: 'COA Mapping',       detail: 'Matching expense heads to Chart of Accounts' },
]

const PARSE_FIELDS = [
  { field: 'Document Layout',   detail: 'Analyzing page structure, headers & tables' },
  { field: 'Vendor Details',    detail: 'Identifying supplier name, address & GSTIN' },
  { field: 'Invoice Reference', detail: 'Locating invoice number, PO number & date' },
  { field: 'Tax Breakdown',     detail: 'Extracting CGST, SGST, IGST & cess values' },
  { field: 'Line Items',        detail: 'Parsing item descriptions, HSN, qty & rates' },
  { field: 'Amount Totals',     detail: 'Verifying sub-total, tax & grand total math' },
  { field: 'COA Mapping',       detail: 'Matching expense heads to Chart of Accounts' },
]

/**
 * The OCR response is an untyped bag (`InvoicePayload = Record<string, unknown>`),
 * so the two fields worth showing before someone discards an extraction are read
 * defensively rather than asserted.
 */
function extractedSummary(result?: InvoicePayload): { vendor: string; amount: number | null } {
  const vendor = (result?.vendor as { name?: unknown } | undefined)?.name
  const total = result?.total_amount
  return {
    vendor: typeof vendor === 'string' && vendor.trim() ? vendor : 'Not identified',
    amount: typeof total === 'number' ? total : null,
  }
}

import type { QueueItem } from '../App'

interface Props {
  uploadQueue: QueueEntry[]
  setUploadQueue: Dispatch<SetStateAction<QueueEntry[]>>
  onReview: (items: QueueItem[], startIdx: number) => void
}

export default function UploadPage({ uploadQueue: queue, setUploadQueue: setQueue, onReview }: Props) {
  const [parseStage, setParseStage] = useState(0)
  const parseFields = useJurisdiction().packs.gst ? PARSE_FIELDS : PARSE_FIELDS_INTL

  const updateEntry = (file: File, patch: Partial<QueueEntry>) =>
    setQueue(q => q.map(e => e.file === file ? { ...e, ...patch } : e))

  // Files turned away for length never join the queue: they were never extracted,
  // so counting them as failed extractions would misreport the batch.
  const [tooLong, setTooLong] = useState<{ name: string; pages: number }[]>([])

  const onDrop = useCallback(async (dropped: File[]) => {
    const counts = await Promise.all(dropped.map(countPdfPages))
    const acc = dropped.filter((_, i) => (counts[i] ?? 0) <= MAX_PDF_PAGES)
    setTooLong(dropped.flatMap((f, i) =>
      (counts[i] ?? 0) > MAX_PDF_PAGES ? [{ name: f.name, pages: counts[i]! }] : []))
    const newEntries: QueueEntry[] = acc.map(f => ({ file: f, status: 'pending' }))
    setQueue(q => {
      const existing = new Set(q.map(e => e.file.name))
      return [...q, ...newEntries.filter(e => !existing.has(e.file.name))]
    })
  }, [])

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    // Photos and screenshots of bills too; the server turns them into a one-page PDF.
    onDrop, accept: { 'application/pdf': ['.pdf'], 'image/jpeg': ['.jpg', '.jpeg'], 'image/png': ['.png'], 'image/webp': ['.webp'] },
    maxSize: 50 * 1024 * 1024, multiple: true,
    noClick: false,
  })

  const removeEntry = (file: File) =>
    setQueue(q => q.filter(e => e.file !== file))

  /**
   * Removing a file that has already been extracted throws away the extraction —
   * the only copy of it, since nothing is persisted until the invoice is
   * reviewed and submitted. That gets a confirmation. A failed or not-yet-run
   * file has nothing to lose, so it just goes.
   */
  const [pendingRemoval, setPendingRemoval] = useState<QueueEntry | null>(null)

  const requestRemove = (entry: QueueEntry) => {
    if (entry.status === 'done') setPendingRemoval(entry)
    else removeEntry(entry.file)
  }

  const handleProcess = async () => {
    const pending = queue.filter(e => e.status === 'pending')
    if (!pending.length) return
    pending.forEach(e => updateEntry(e.file, { status: 'processing' }))
    await Promise.all(pending.map(async ({ file }) => {
      try {
        const result = await ocrInvoice(file)
        if (typeof result._not_invoice === 'string') updateEntry(file, { status: 'skipped', error: result._not_invoice })
        else updateEntry(file, { status: 'done', result })
      } catch (err: unknown) {
        const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
          || (err as { message?: string })?.message || 'OCR failed'
        updateEntry(file, { status: 'error', error: msg })
      }
    }))
  }

  // The page under the scan beam is the actual first page of the file being
  // extracted — a fake wireframe of a document made the whole animation feel
  // like a loading spinner pretending to be work.
  const scanningFile = queue.find(e => e.status === 'processing')?.file ?? null
  // Memoised: a fresh object URL on every render makes react-pdf treat it as a
  // new document and reload the PDF on each parse-stage tick.
  const scanUrl = useMemo(() => (scanningFile ? URL.createObjectURL(scanningFile) : null), [scanningFile])
  useEffect(() => () => { if (scanUrl) URL.revokeObjectURL(scanUrl) }, [scanUrl])
  const scanIsImage = !!scanningFile?.type.startsWith('image/')
  const [scanPageFailed, setScanPageFailed] = useState(false)
  useEffect(() => { setScanPageFailed(false) }, [scanUrl])

  /**
   * The page is rendered to fit its frame rather than at a fixed 300px: the
   * viewer is now the right half of the panel, so it should use the space it
   * has. Fitting needs both the frame box (measured — the frame is a fixed
   * height in CSS, so this does not feed back into layout) and the page's own
   * aspect ratio, which only pdf.js can tell us.
   */
  const [frame, setFrame] = useState({ w: 0, h: 0 })
  const [pageAspect, setPageAspect] = useState<number | null>(null)
  useEffect(() => { setPageAspect(null) }, [scanUrl])

  // A ref callback rather than useRef + effect: the frame mounts and unmounts
  // with the extraction panel, and this way the observer follows it without
  // having to name that condition twice.
  const observer = useRef<ResizeObserver | null>(null)
  const frameRef = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect()
    if (!el) return
    observer.current = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setFrame({ w: Math.floor(width), h: Math.floor(height) })
    })
    observer.current.observe(el)
  }, [])

  // Fit to the frame: width-bound for a wide page, height-bound for a tall one.
  const fitWidth = frame.w > 0
    ? (pageAspect ? Math.max(80, Math.min(frame.w, Math.floor(frame.h * pageAspect))) : frame.w)
    : 0

  const total           = queue.length
  const doneCount       = queue.filter(e => e.status === 'done').length
  const errorCount      = queue.filter(e => e.status === 'error').length
  const skippedCount    = queue.filter(e => e.status === 'skipped').length
  const processingCount = queue.filter(e => e.status === 'processing').length
  const pendingCount    = queue.filter(e => e.status === 'pending').length
  const finishedCount   = doneCount + errorCount + skippedCount
  const allFinished     = total > 0 && processingCount === 0 && pendingCount === 0
  const anyProcessing   = processingCount > 0
  const progressPct     = total > 0 ? Math.round((finishedCount / total) * 100) : 0

  /**
   * A clean batch hands itself straight to the review editor.
   *
   * The "Extraction Complete · Review & Submit All" card was a dead stop: the
   * only sensible next action was already known, so it made the user click to be
   * told what they had just watched happen. The queue is cleared as we go —
   * those files now live in the review queue, and leaving them behind would
   * bounce the user back here → straight to review again on every Back.
   *
   * Anything that failed keeps the summary card, because then the outcome is not
   * uniform and the user has a real decision: review what worked, or retry what
   * did not.
   */
  useEffect(() => {
    if (!allFinished || anyProcessing || errorCount > 0 || skippedCount > 0 || doneCount === 0) return
    const done = queue.filter(e => e.status === 'done' && e.result)
    if (!done.length) return
    setQueue([])
    onReview(done.map(e => ({ file: e.file, data: e.result! })), 0)
  }, [allFinished, anyProcessing, errorCount, skippedCount, doneCount, queue, onReview, setQueue])

  // Cycle parse stages during processing
  useEffect(() => {
    if (!anyProcessing) return
    setParseStage(0)
    const iv = setInterval(() => {
      setParseStage(s => (s + 1) % PARSE_FIELDS.length)  // both lists are the same length
    }, 2200)
    return () => clearInterval(iv)
  }, [anyProcessing])

  return (
    <div className="up">

      <GridGlow />

      {/* One thing on this page: put invoices in. The Agent Network panel, the
          Key Extraction Fields explainer and the four alternate-source tabs
          (URL / Google Drive / Dropbox / OneDrive) are gone — the tabs were dead
          controls that only moved an underline, and the rest was product
          narrative that a user who is already inside the app has read. */}
      <div className={`up__grid${anyProcessing ? ' up__grid--busy' : ''}`}>

        {/* Processing: Live Extraction View */}
        {anyProcessing && (
          <div className="up__card up__card--p up__live">
            <h1 className="up__title up__title--live">Extracting invoices</h1>

            {/* Two columns: what the agent is doing on the left, the page it is
                reading on the right at the size the frame allows. The document
                used to sit above the stage list at a fixed 300px, which left the
                invoice too small to read and pushed the stages down the page. */}
            <div className="up__live-body">

              {/* ── Left: agent status, stages, progress ── */}
              <div className="up__live-side">
                <div className="up__agents-badge">
                  <Bot size={14} />
                  <span>{processingCount} Agent{processingCount !== 1 ? 's' : ''} Deployed</span>
                  <div className="up__agents-dots">
                    {Array.from({ length: Math.min(processingCount, 8) }).map((_, i) => (
                      <div key={i} className="up__agent-dot" style={{ animationDelay: `${i * 0.15}s` }} />
                    ))}
                  </div>
                </div>

                <div className="up__stages">
                  <div className="up__stages-title">Live Extraction</div>
                  {parseFields.map((pf, i) => {
                    const isDone = i < parseStage
                    const isActive = i === parseStage
                    return (
                      <div key={pf.field} className={`up__stage ${isDone ? 'up__stage--done' : ''} ${isActive ? 'up__stage--active' : ''}`}>
                        <div className="up__stage-indicator">
                          {isDone ? <CheckCircle2 size={13} /> : isActive ? <LoadingMark size={15} label={`${pf.field} in progress`} /> : <div className="up__stage-num">{i + 1}</div>}
                        </div>
                        <div className="up__stage-info">
                          <span className="up__stage-field">{pf.field}</span>
                          {isActive && <span className="up__stage-detail">{pf.detail}</span>}
                        </div>
                      </div>
                    )
                  })}
                </div>

                {total > 0 && (
                  <div className="up__prog">
                    <div className="up__prog-meta">
                      <span>{finishedCount} of {total} invoices extracted</span>
                      <span className="up__prog-pct">{progressPct}%</span>
                    </div>
                    <div className="up__prog-bar"><div className="up__prog-fill" style={{ width: `${progressPct}%` }} /></div>
                  </div>
                )}
              </div>

              {/* ── Right: the real page being read, scanned in place ── */}
              <div className="up__doc">
                <div
                  ref={frameRef}
                  className={`up__doc-inner${scanUrl && !scanPageFailed ? ' up__doc-inner--pdf' : ''}`}
                >
                  {scanUrl && !scanPageFailed && scanIsImage ? (
                    <img src={scanUrl} alt="" className="up__doc-img" onError={() => setScanPageFailed(true)} />
                  ) : scanUrl && !scanPageFailed ? (
                    <Document
                      file={scanUrl}
                      loading={<div className="up__doc-placeholder" />}
                      // An unrenderable PDF must not take the animation down with
                      // it — fall back to the wireframe.
                      onLoadError={() => setScanPageFailed(true)}
                      error={<div className="up__doc-placeholder" />}
                    >
                      {/* Held back until the frame has been measured: rendering at
                          a guessed width only to re-render at the real one makes
                          the page visibly jump. */}
                      {fitWidth > 0 && (
                        <Page
                          pageNumber={1}
                          width={fitWidth}
                          renderTextLayer={false}
                          renderAnnotationLayer={false}
                          onLoadSuccess={p => setPageAspect(p.width / p.height)}
                          onRenderError={() => setScanPageFailed(true)}
                          loading={<div className="up__doc-placeholder" />}
                        />
                      )}
                    </Document>
                  ) : (
                    /* Fallback wireframe: no file in flight yet, or a PDF pdf.js
                       cannot draw. */
                    <>
                      <div className="up__doc-hdr-block" />
                      <div className="up__doc-hdr-sub" />
                      <div className="up__doc-gap" />
                      <div className="up__doc-line" style={{ width: '70%' }} />
                      <div className="up__doc-line" style={{ width: '55%' }} />
                      <div className="up__doc-line" style={{ width: '85%' }} />
                      <div className="up__doc-gap" />
                      <div className="up__doc-tbl">
                        <div className="up__doc-tbl-hdr" />
                        <div className="up__doc-tbl-row"><div /><div /><div /></div>
                        <div className="up__doc-tbl-row"><div /><div /><div /></div>
                        <div className="up__doc-tbl-row"><div /><div /><div /></div>
                      </div>
                      <div className="up__doc-gap" />
                      <div className="up__doc-line" style={{ width: '40%', marginLeft: 'auto' }} />
                      <div className="up__doc-line up__doc-line--bold" style={{ width: '35%', marginLeft: 'auto' }} />
                    </>
                  )}

                  {/* Scan beam */}
                  <div className="up__scan-beam" />
                  {/* Highlight overlay for active field */}
                  <div className="up__doc-highlight" style={{ top: `${12 + parseStage * 11}%`, opacity: 1 }} />
                </div>
                {scanningFile && (
                  <div className="up__doc-caption" title={scanningFile.name}>{scanningFile.name}</div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Done state */}
        {/* Only reached when something failed — a clean batch has already been
            handed to the review editor. */}
        {allFinished && !anyProcessing && (errorCount > 0 || skippedCount > 0) && (
          <div className="up__card up__card--p">
            <div className="up__done-head">
              <CheckCircle2 size={20} style={{ color: '#059669' }} />
              <div>
                <div className="up__card-title">{errorCount > 0 ? 'Extraction finished with errors' : 'Extraction finished'}</div>
                <div className="up__card-sub">
                  {doneCount} ready for review
                  {skippedCount > 0 && ` · ${skippedCount} not an invoice`}
                  {errorCount > 0 && ` · ${errorCount} failed`}
                </div>
              </div>
            </div>
            <div className="up__done-summary">
              <div className="up__done-stat">
                <span className="up__done-stat-val" style={{ color: '#059669' }}>{doneCount}</span>
                <span className="up__done-stat-label">Extracted</span>
              </div>
              {skippedCount > 0 && (
                <div className="up__done-stat">
                  <span className="up__done-stat-val" style={{ color: '#6b7280' }}>{skippedCount}</span>
                  <span className="up__done-stat-label">Not invoices</span>
                </div>
              )}
              {errorCount > 0 && (
                <div className="up__done-stat">
                  <span className="up__done-stat-val" style={{ color: '#dc2626' }}>{errorCount}</span>
                  <span className="up__done-stat-label">Failed</span>
                </div>
              )}
              <div className="up__done-stat">
                <span className="up__done-stat-val" style={{ color: '#004080' }}>{finishedCount}</span>
                <span className="up__done-stat-label">Agents Used</span>
              </div>
            </div>
            {doneCount > 0 && (
              <button className="up__review-all" onClick={() => {
                const done = queue.filter(e => e.status === 'done' && e.result)
                onReview(done.map(e => ({ file: e.file, data: e.result! })), 0)
              }}>
                <CheckCircle2 size={14} /> Review & Submit All Extracted Invoices
              </button>
            )}
          </div>
        )}

        <div className="up__card">
          <div className="up__body">
            {/* Hidden while agents are running: nobody is dropping a file into
                a page that is mid-extraction, and it pushes the progress view
                off-centre. */}
            {!anyProcessing && (
              <>
                <h1 className="up__title">Upload invoices</h1>
                <div {...getRootProps()} className={`up__dz ${isDragActive ? 'up__dz--over' : ''}`}>
                  <input {...getInputProps()} />
                  <div className="up__dz-circle">
                    <Upload size={22} strokeWidth={1.8} />
                  </div>
                  <div className="up__dz-t1">Drag & drop Bills here, or <span className="up__dz-link">Click to upload</span></div>
              
                  <button type="button" className="up__dz-btn" onClick={e => { e.stopPropagation(); open() }}>
                    Choose Files
                  </button>
                </div>
                {tooLong.length > 0 && (
                  <div className="up__too-long" role="alert">
                    <AlertCircle size={14} />
                    <div>
                      {tooLong.map(t => (
                        <div key={t.name}>
                          <strong>{t.name}</strong> has {t.pages} pages — the limit is {MAX_PDF_PAGES}.
                        </div>
                      ))}
                      <div className="up__too-long-hint">Split it and upload the pages you need.</div>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* File Queue */}
            {queue.length > 0 && (
              <div className="up__fq">
                <div className="up__fq-head">
                  <span>{queue.length} file{queue.length !== 1 ? 's' : ''} queued</span>
                  {anyProcessing && (
                    <span className="up__fq-proc">
                      <Bot size={12} /> {processingCount} agent{processingCount !== 1 ? 's' : ''} active
                    </span>
                  )}
                </div>
                {queue.map(({ file: f, status, error, result }) => (
                  <div key={f.name} className={`up__fq-row up__fq-row--${status}`}>
                    <div className="up__fq-icon">
                      {status === 'processing' && <LoadingMark size={16} label="Agent extracting" />}
                      {status === 'done'       && <CheckCircle2 size={14} />}
                      {status === 'error'      && <AlertCircle size={14} />}
                      {status === 'skipped'    && <FileX size={14} />}
                      {status === 'pending'    && <FileText size={14} />}
                    </div>
                    <div className="up__fq-info">
                      <span className="up__fq-name" title={f.name}>{f.name}</span>
                      <span className="up__fq-size">
                        {(f.size / 1024 / 1024).toFixed(2)} MB
                        {status === 'processing' && <span className="up__fq-agent"> · Agent extracting…</span>}
                        {error && <span className={status === 'skipped' ? 'up__fq-skip' : 'up__fq-err'}> · {error}</span>}
                      </span>
                    </div>
                    <div className="up__fq-actions">
                      {status === 'done' && result && (
                        <button className="up__btn-review" onClick={() => {
                          const done = queue.filter(e => e.status === 'done' && e.result)
                          const startIdx = done.findIndex(e => e.file === f)
                          onReview(done.map(e => ({ file: e.file, data: e.result! })), Math.max(0, startIdx))
                        }}>Review</button>
                      )}
                      {status !== 'processing' && (
                        <button
                          className="up__btn-rm"
                          // Icon-only, so it needs a real accessible name.
                          aria-label={`Remove ${f.name}`}
                          title={`Remove ${f.name}`}
                          onClick={() => requestRemove({ file: f, status, error, result })}
                        >
                          <X size={12} />
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {pendingCount > 0 && (
              <button className="up__extract-btn" onClick={handleProcess}>
                <Zap size={15} />
                {/* One file does not need to be told it is being processed in
                    parallel with itself. */}
                {pendingCount === 1 ? 'Extract' : `Extract ${pendingCount} invoices in parallel`}
              </button>
            )}
          </div>
        </div>

      </div>

      <ConfirmDialog
        open={!!pendingRemoval}
        onCancel={() => setPendingRemoval(null)}
        onConfirm={() => {
          if (pendingRemoval) removeEntry(pendingRemoval.file)
          setPendingRemoval(null)
        }}
        tone="danger"
        title="Remove this extracted invoice?"
        description="The extracted data is discarded. Nothing has been saved yet, so you would have to upload the file and run extraction again."
        confirmLabel="Remove"
        cancelLabel="Keep it"
        impact={
          pendingRemoval
            ? [
                { label: 'File', value: pendingRemoval.file.name },
                { label: 'Vendor', value: extractedSummary(pendingRemoval.result).vendor },
                { label: 'Amount', value: formatMoney(extractedSummary(pendingRemoval.result).amount) },
              ]
            : undefined
        }
      />

      {/* ── Styles ── */}
      <style>{`
        .up {
          padding: 24px;
          background: #f8f9ff;
          min-height: 100%;
          overflow-y: auto;
          box-sizing: border-box;
          /* Positioning context for the GridGlow background layer. */
          position: relative;
          font-family: 'Inter', sans-serif;
          /* One card, centred on both axes. "safe center" keeps a long file
             queue scrollable from the top instead of clipping above the fold. */
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: safe center;
          gap: 16px;
        }

        .up__title { font-size: 16px; font-weight: 700; color: #0f172a; margin: 0; text-align: center; }

        /* Grid */
        .up__grid {
          display: flex;
          flex-direction: column;
          gap: 14px;
          width: 100%;
          max-width: 460px;
          transition: max-width 220ms cubic-bezier(0.2, 0, 0.2, 1);
          /* Above the GridGlow layer. */
          position: relative;
          z-index: 1;
        }

        /* The extraction view needs more room than a dropzone does — two columns
           and a readable page. */
        .up__grid--busy { max-width: 980px; }

        /* Cards */
        .up__card {
          background: white; border: 1px solid #e2e8f0; border-radius: 14px;
          box-shadow: 0 1px 4px rgba(0,0,0,0.05); overflow: hidden;
        }
        .up__card--p { padding: 20px; }
        .up__card-title { font-size: 15px; font-weight: 700; color: #0f172a; }
        .up__card-sub   { font-size: 12px; color: #5b6473; margin-top: 3px; }

        /* Body */
        .up__body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }

        /* Dropzone */
        .up__dz {
          border: 1.5px dashed #c3c6d2; border-radius: 12px;
          padding: 20px 16px; text-align: center; cursor: pointer;
          display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
          transition: border-color 0.15s, background 0.15s; background: #fafbfd;
        }
        .up__dz:hover, .up__dz--over { border-color: #004080; background: #f0f4ff; }
        .up__dz-circle {
          width: 56px; height: 56px; border-radius: 50%;
          background: #eff4ff; color: #004080;
          display: flex; align-items: center; justify-content: center;
        }
        .up__dz--over .up__dz-circle { background: #dbeafe; }
        .up__dz-t1 { font-size: 14px; font-weight: 500; color: #374151; }
        .up__dz-link { color: #004080; font-weight: 600; }
        .up__dz-t2 { font-size: 12px; color: #9ca3af; }
        .up__dz-btn {
          margin-top: 4px; padding: 9px 24px; background: #004080; color: white;
          border: none; border-radius: 6px; font-size: 13px; font-weight: 600;
          cursor: pointer; font-family: 'Inter', sans-serif; transition: background 0.12s;
        }
        .up__dz-btn:hover { background: #002a58; }
        .up__too-long {
          display: flex; gap: 8px; align-items: flex-start; margin-top: 12px;
          padding: 10px 12px; border: 1px solid #fecaca; border-radius: 8px;
          background: #fef2f2; color: #b91c1c; font-size: 13px; line-height: 1.45;
          overflow-wrap: anywhere;
        }
        .up__too-long svg { flex-shrink: 0; margin-top: 2px; }
        .up__too-long-hint { color: #7f1d1d; opacity: .8; }

        /* File Queue */
        .up__fq { display: flex; flex-direction: column; gap: 8px; }
        .up__fq-head {
          display: flex; align-items: center; justify-content: space-between;
          font-size: 12px; font-weight: 600; color: #374151;
        }
        .up__fq-proc {
          display: flex; align-items: center; gap: 5px;
          font-size: 11px; font-weight: 600; color: #004080;
        }
        .up__fq-row {
          display: flex; align-items: center; gap: 10px;
          padding: 9px 12px; border-radius: 8px;
          border: 1px solid #e2e8f0; background: #f8f9ff;
        }
        .up__fq-row--processing { border-color: #bfdbfe; background: #eff6ff; }
        .up__fq-row--processing .up__fq-icon { color: #004080; }
        .up__fq-row--done { border-color: #a7f3d0; background: #f0fdf4; }
        .up__fq-row--done .up__fq-icon { color: #059669; }
        .up__fq-row--error { border-color: #fecaca; background: #fef2f2; }
        .up__fq-row--error .up__fq-icon { color: #dc2626; }
        .up__fq-row--skipped .up__fq-icon, .up__fq-skip { color: #6b7280; }

        .up__fq-icon { flex-shrink: 0; display: flex; color: #9ca3af; }
        .up__fq-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
        .up__fq-name { font-size: 12px; font-weight: 600; color: #0f172a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .up__fq-size { font-size: 11px; color: #9ca3af; }
        .up__fq-agent { color: #004080; font-weight: 600; }
        .up__fq-err   { color: #dc2626; }
        .up__fq-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
        .up__btn-review {
          padding: 4px 12px; background: #004080; color: #fff;
          border: none; border-radius: 4px; font-size: 11px; font-weight: 600;
          cursor: pointer; font-family: 'Inter', sans-serif;
        }
        .up__btn-review:hover { background: #002a58; }
        .up__btn-rm {
          width: 22px; height: 22px; border-radius: 4px;
          border: 1px solid #e2e8f0; background: #fff; color: #9ca3af;
          display: flex; align-items: center; justify-content: center; cursor: pointer;
        }
        .up__btn-rm:hover { background: #fef2f2; color: #dc2626; border-color: #fecaca; }

        .up__extract-btn {
          display: inline-flex; align-items: center; gap: 7px;
          padding: 11px 22px; background: #004080; color: #fff;
          border: none; border-radius: 6px;
          font-size: 13px; font-weight: 600; cursor: pointer;
          font-family: 'Inter', sans-serif; width: fit-content; transition: background 0.12s;
        }
        .up__extract-btn:hover { background: #002a58; }

        /* ── Right column ── */




        /* ── Live Extraction Panel ── */
        .up__live { border-color: #bfdbfe; }
        .up__title--live { text-align: left; margin-bottom: 16px; }

        /* The document column is given the larger share: it is the thing being
           read. Both tracks are minmax(0, …) so a wide page cannot blow the
           grid out past the card. */
        .up__live-body {
          display: grid;
          grid-template-columns: minmax(0, 0.85fr) minmax(0, 1.15fr);
          gap: 20px;
          align-items: stretch;
        }
        .up__live-side { display: flex; flex-direction: column; min-width: 0; }

        /* Under ~880px there is no room for side-by-side; the page goes first
           because it is the anchor. */
        @media (max-width: 880px) {
          .up__live-body { grid-template-columns: minmax(0, 1fr); }
          .up__doc { order: -1; }
        }

        .up__agents-badge {
          display: flex; align-items: center; gap: 8px;
          padding: 8px 14px; border-radius: 10px;
          background: #eff4ff; border: 1px solid #bfdbfe;
          font-size: 12px; font-weight: 700; color: #004080;
          margin-bottom: 14px;
        }
        .up__agents-dots { display: flex; gap: 4px; margin-left: auto; }
        .up__agent-dot {
          width: 8px; height: 8px; border-radius: 50%; background: #004080;
          animation: agent-pulse 1.2s ease-in-out infinite;
        }
        @keyframes agent-pulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50%      { opacity: 1;   transform: scale(1.1); }
        }

        /* Document scan */
        .up__doc {
          background: #f8f9ff; border: 1px solid #e2e8f0; border-radius: 10px;
          padding: 6px; display: flex; flex-direction: column; min-width: 0;
        }
        /* A fixed-height frame, so the page is fitted to the panel instead of the
           panel being sized by the page — and so measuring the frame to compute
           that fit cannot feed back into layout. */
        .up__doc-inner {
          background: white; border-radius: 6px;
          position: relative; overflow: hidden;
          height: min(56vh, 520px);
          padding: 16px 18px;
          display: flex; flex-direction: column; gap: 0;
        }
        /* The real page renders edge to edge and centred — the wireframe's padding
           existed to fake margins the document already has. */
        .up__doc-inner--pdf {
          padding: 0;
          flex-direction: row; align-items: center; justify-content: center;
        }
        .up__doc-img { display: block; max-width: 100%; max-height: 100%; object-fit: contain; }
        .up__doc-inner .react-pdf__Page canvas {
          display: block; max-width: 100%; max-height: 100%; height: auto;
        }
        .up__doc-placeholder {
          width: 100%; height: 100%; min-height: 120px; background: #f8fafc;
        }
        /* Which file the agent currently has open. */
        .up__doc-caption {
          font-size: 10.5px; color: #6b7280; font-weight: 600;
          padding: 6px 4px 2px; text-align: center;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        }
        .up__doc-hdr-block {
          width: 50%; height: 10px; background: #e2e8f0; border-radius: 3px; margin-bottom: 6px;
        }
        .up__doc-hdr-sub {
          width: 30%; height: 6px; background: #f1f5f9; border-radius: 3px; margin-bottom: 0;
        }
        .up__doc-gap { height: 12px; }
        .up__doc-line {
          height: 5px; background: #f1f5f9; border-radius: 2px; margin-bottom: 6px;
        }
        .up__doc-line--bold { height: 7px; background: #e2e8f0; }

        .up__doc-tbl { display: flex; flex-direction: column; gap: 0; }
        .up__doc-tbl-hdr {
          height: 6px; background: #e2e8f0; border-radius: 2px; margin-bottom: 4px;
        }
        .up__doc-tbl-row {
          display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 6px; margin-bottom: 4px;
        }
        .up__doc-tbl-row > div { height: 5px; background: #f1f5f9; border-radius: 2px; }

        /* Scan beam */
        .up__scan-beam {
          position: absolute; left: 0; right: 0; height: 2px;
          background: linear-gradient(90deg, transparent 0%, #004080 30%, #3b82f6 50%, #004080 70%, transparent 100%);
          box-shadow: 0 0 12px rgba(0, 64, 128, 0.4), 0 0 4px rgba(0, 64, 128, 0.2);
          animation: scan-sweep 2.5s ease-in-out infinite;
          opacity: 0.9;
        }
        @keyframes scan-sweep {
          0%   { top: 5%;  opacity: 0; }
          5%   { opacity: 0.9; }
          95%  { opacity: 0.9; }
          100% { top: 92%; opacity: 0; }
        }

        /* Highlight overlay */
        .up__doc-highlight {
          position: absolute; left: 8px; right: 8px; height: 14px;
          background: rgba(0, 64, 128, 0.06); border-left: 2px solid #004080;
          border-radius: 2px; transition: top 0.4s ease, opacity 0.3s;
        }

        /* Extraction stages — takes the slack in the left column so the progress
           bar sits level with the bottom of the page. */
        .up__stages { margin-bottom: 16px; flex: 1; }
        .up__stages-title {
          font-size: 11px; font-weight: 700; color: #94a3b8;
          text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px;
        }
        .up__stage {
          display: flex; align-items: center; gap: 10px;
          padding: 5px 0; transition: opacity 0.2s;
        }
        .up__stage:not(.up__stage--done):not(.up__stage--active) { opacity: 0.4; }

        .up__stage-indicator {
          width: 22px; height: 22px; border-radius: 50%;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; font-size: 10px; color: #9ca3af;
        }
        .up__stage--done .up__stage-indicator { color: #059669; }
        .up__stage--active .up__stage-indicator { color: #004080; }

        .up__stage-num {
          width: 18px; height: 18px; border-radius: 50%;
          background: #f1f5f9; display: flex; align-items: center; justify-content: center;
          font-size: 9px; font-weight: 700; color: #9ca3af;
        }

        .up__stage-info { min-width: 0; }
        .up__stage-field {
          font-size: 12px; font-weight: 600; color: #374151; display: block;
        }
        .up__stage--done .up__stage-field { color: #059669; }
        .up__stage--active .up__stage-field { color: #004080; }

        .up__stage-detail {
          font-size: 10.5px; color: #004080; display: block; margin-top: 1px;
          animation: stage-fade 0.3s ease;
        }
        @keyframes stage-fade {
          from { opacity: 0; transform: translateY(-4px); }
          to   { opacity: 1; transform: translateY(0); }
        }

        /* Progress */
        .up__prog { padding-top: 14px; border-top: 1px solid #f0f0f5; }
        .up__prog-meta {
          display: flex; justify-content: space-between;
          font-size: 11px; color: #6b7280; margin-bottom: 6px;
        }
        .up__prog-pct { font-weight: 700; color: #004080; }
        .up__prog-bar { height: 6px; background: #e2e8f0; border-radius: 99px; overflow: hidden; }
        .up__prog-fill { height: 100%; background: #004080; border-radius: 99px; transition: width 0.3s; }

        /* Done state */
        .up__done-head { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
        .up__done-summary {
          display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
          margin-bottom: 16px;
        }
        .up__done-stat {
          text-align: center; padding: 12px; border-radius: 10px;
          background: #f8f9ff; border: 1px solid #e2e8f0;
        }
        .up__done-stat-val { display: block; font-size: 24px; font-weight: 800; line-height: 1; }
        .up__done-stat-label { display: block; font-size: 10px; color: #9ca3af; margin-top: 4px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.04em; }

        .up__review-all {
          display: flex; align-items: center; justify-content: center; gap: 7px;
          width: 100%; padding: 10px; background: #004080; color: #fff;
          border: none; border-radius: 8px; font-size: 13px; font-weight: 600;
          cursor: pointer; font-family: 'Inter', sans-serif; transition: background 0.12s;
        }
        .up__review-all:hover { background: #002a58; }

      `}</style>
    </div>
  )
}
