import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Clock, Download, Eye, FileCheck2, Folder, FolderInput, Plus, Receipt, Search, Sparkles, Trash2, Upload, X } from 'lucide-react'
import type { Bill } from '../../api/invoices'
import { classifyVendorDocument, fetchDocumentTypes, uploadVendorDocument, setVendorDocumentType, type DocType, type DocVerdict, type VendorDocument } from '../../api/vendorCard'
import { BillStatus, ClaimsChip, FileIcon, LoadNote, type LoadStatus } from './VendorViews'
import {
  MONTHS_FULL, billAmount, billMoney, billState, currencyOf, dayOf, foreignAmount, isLateInApproval, isOpen, isOverdue, isPaid,
  isUnconverted, monthlyTotals, money, outstandingOf, plural, shortDate, stageOf, unconvertedNote,
} from './vendorStats'

export type { LoadStatus } from './VendorViews'

/**
 * A vendor's documents, filed by type.
 *
 * Bills are built in: every bill for the vendor, straight from the bills (this
 * replaces the old Statements tab). Contracts & IOs are read and checked
 * against the vendor record. Anything else is filed under a type the team
 * names, which is then offered for every vendor.
 */

export type DocFolder = 'recent' | 'bills' | 'contracts' | `t:${string}`
export type BillFilter = 'all' | 'outstanding' | 'overdue' | 'late' | 'review' | 'approval' | 'paid'

/**
 * Each filter is a billState group, so its name always agrees with the Status
 * column: Overdue is the red "Overdue Nd", Late in approval the amber "… · Nd
 * late", and Needs review and In approval are the two steps before approval.
 */
const BILL_FILTERS: { key: BillFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'outstanding', label: 'Unpaid' },
  { key: 'overdue', label: 'Overdue' },
  { key: 'late', label: 'Late in approval' },
  { key: 'review', label: 'Needs review' },
  { key: 'approval', label: 'In approval' },
  { key: 'paid', label: 'Paid' },
]

/** Fewer bills than this read best as one flat list. */
const GROUP_FROM = 8

/** What the upload dialog takes, and the most it sends to be read and stored. */
const ACCEPT = '.pdf,.xls,.xlsx,.csv,.doc,.docx,.jpg,.jpeg,.png,.webp,.gif,.heic'
const MAX_MB = 25

function guessType(name: string): { type: DocType | 'bill'; label?: string } {
  const n = name.toLowerCase()
  if (/invoice|\binv\b|bill/.test(n)) return { type: 'bill' }
  if (/contract|agreement|\bmsa\b|\bio\b|io[-_ ]|insertion|addendum|\bsow\b|\bpo[-_ ]/.test(n)) return { type: 'contract' }
  if (/rate/.test(n)) return { type: 'other', label: 'Rate cards' }
  if (/soa|statement/.test(n)) return { type: 'other', label: 'Statement of account' }
  if (/cert|udyam|msme|cheque|gst[-_ ]?reg/.test(n)) return { type: 'other', label: 'Compliance certificates' }
  return { type: 'other' }
}

function csvCell(v: unknown): string {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Type names that differ only in case are one type; the first spelling seen wins. */
function uniqueTypes(names: string[]): string[] {
  const seen = new Map<string, string>()
  for (const n of names) if (n && !seen.has(n.toLowerCase())) seen.set(n.toLowerCase(), n)
  return [...seen.values()]
}

/** The team's spelling of a type the user typed, so "rate cards" files into "Rate cards". */
const canonicalType = (types: string[], name: string) => types.find(t => t.toLowerCase() === name.toLowerCase()) ?? name

/** "1,25,000", "₹1,25,000" and "125000" all search the same way. */
const digitsOf = (s: string) => s.replace(/[₹,\s]/g, '').replace(/^(rs\.?|inr)/i, '')

export default function VendorDocuments({
  vendorName, supplierId, brandId, bills, docs, now, folder, onFolder, billFilter, onBillFilter,
  currentUser, onReload, onPreviewDoc, onDownloadDoc, onDeleteDoc, onPreviewBill, onOpenBill, onUploadBill, notify, uploadRequest = 0, onUploadRequestDone,
  billsStatus = 'ready', docsStatus = 'ready', onRetry,
}: {
  vendorName: string
  supplierId: string
  brandId?: string | null
  bills: Bill[]
  docs: VendorDocument[] | null
  now: Date
  folder: DocFolder
  onFolder: (f: DocFolder) => void
  billFilter: BillFilter
  onBillFilter: (f: BillFilter) => void
  currentUser: string
  onReload: () => void
  onPreviewDoc: (d: VendorDocument) => void
  onDownloadDoc: (d: VendorDocument) => void
  onDeleteDoc: (d: VendorDocument) => void
  onPreviewBill: (b: Bill) => void
  onOpenBill?: (invoiceId: string) => void
  /** Go to bill upload, where a new bill is read and reviewed. */
  onUploadBill: () => void
  notify: (msg: string, type?: 'error' | 'info') => void
  /** Bumped by the card's Upload button to open the upload dialog. */
  uploadRequest?: number
  /** The request was acted on; clear it so coming back to Documents doesn't open the dialog again. */
  onUploadRequestDone?: () => void
  /** Where the bills fetch is. Loading or failed is never shown as "No bills". */
  billsStatus?: LoadStatus
  /** Where the documents fetch is. `docs` of null also reads as loading. */
  docsStatus?: LoadStatus
  /** Fetch again after a failed load. */
  onRetry?: () => void
}) {
  const [tenantTypes, setTenantTypes] = useState<string[]>([])
  const [upload, setUpload] = useState<{ preset: DocFolder | 'any'; file: File | null } | null>(null)
  const [query, setQuery] = useState('')
  const [moving, setMoving] = useState<VendorDocument | null>(null)

  useEffect(() => {
    if (!uploadRequest) return
    setUpload({ preset: folder === 'contracts' || folder.startsWith('t:') ? folder : 'any', file: null })
    onUploadRequestDone?.()
  }, [uploadRequest])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { fetchDocumentTypes().then(t => setTenantTypes(t.map(x => x.label))).catch(() => {}) }, [docs?.length])

  const docsState: LoadStatus = docsStatus !== 'ready' ? docsStatus : docs ? 'ready' : 'loading'
  const billsReady = billsStatus === 'ready'
  const list = docs ?? []
  const contracts = list.filter(d => d.doc_type === 'contract')
  // Keyed by lower case: "Rate cards" and "rate cards" are one folder.
  const byLabel = useMemo(() => {
    const m = new Map<string, { label: string; docs: VendorDocument[] }>()
    for (const d of list) {
      if (d.doc_type !== 'other' || !d.doc_label) continue
      const k = d.doc_label.toLowerCase()
      const e = m.get(k) ?? { label: d.doc_label, docs: [] }
      e.docs.push(d)
      m.set(k, e)
    }
    return m
  }, [list])
  const labels = [...byLabel.values()].map(e => e.label).sort((a, b) => a.localeCompare(b))
  const allTypes = uniqueTypes([...labels, ...tenantTypes])
  // The open type stays in the list once its last file has moved out, so the
  // body and the selected item never disagree.
  const openLabel = folder.startsWith('t:') ? folder.slice(2) : null
  const navLabels = openLabel && !byLabel.has(openLabel.toLowerCase()) ? [...labels, openLabel] : labels

  const match = (b: Bill, k: BillFilter) => {
    switch (k) {
      case 'all': return true
      case 'paid': return isPaid(b)
      case 'outstanding': return isOpen(b)
      case 'overdue': return isOverdue(b, now)
      case 'late': return isLateInApproval(b, now)
      case 'review': return isOpen(b) && stageOf(b) === 0
      case 'approval': return isOpen(b) && stageOf(b) === 1
    }
  }
  /** Dates carry the year once they're from another year: "Due 3 Mar 2025". */
  const dateOf = (iso: string | null | undefined) => { const d = dayOf(iso); return shortDate(iso, !!d && d.getFullYear() !== now.getFullYear()) }

  const fold = (f: DocFolder, label: string, icon: ReactNode, count?: number) => (
    <button key={f} className={`vc__fold${folder === f ? ' on' : ''}`} aria-current={folder === f ? 'page' : undefined} onClick={() => onFolder(f)}>
      {icon}<span className="vc__ellipsis">{label}</span>{count != null && <span className="vc__cnt">{count}</span>}
    </button>
  )

  const addNew = (preset: DocFolder | 'any') => (
    <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={() => setUpload({ preset, file: null })}><Plus size={14} />Add new</button>
  )
  const docList = (docs: VendorDocument[], empty: string, extra: { showClaims?: boolean; withType?: boolean } = {}) => docsState !== 'ready'
    ? <LoadNote status={docsState} what="documents" onRetry={onRetry} className="vc__box vc__mt14" />
    : <DocList docs={docs} empty={empty} onPreview={onPreviewDoc} onDownload={onDownloadDoc} onDelete={onDeleteDoc} onMove={setMoving} {...extra} />

  let body: ReactNode
  if (folder === 'bills' && !billsReady) {
    body = (
      <>
        <div className="vc__folder-h">
          <div className="vc__grow vc__min0"><div className="vc__folder-t">Bills</div></div>
          <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={onUploadBill}><Upload size={14} />Upload bill</button>
        </div>
        <LoadNote status={billsStatus} what="bills" onRetry={onRetry} className="vc__box vc__mt14" />
      </>
    )
  } else if (folder === 'bills') {
    const q = query.trim().toLowerCase()
    let shown = bills.filter(b => match(b, billFilter))
    if (q) {
      const qd = digitsOf(q)
      const numeric = /^\d+(\.\d+)?$/.test(qd)
      shown = shown.filter(b => `${b.id} ${b.category ?? ''}`.toLowerCase().includes(q)
        || (numeric && [billAmount(b), foreignAmount(b), outstandingOf(b)].some(n => n > 0 && String(Math.round(n * 100) / 100).includes(qd))))
    }

    // A category every bill shares belongs to the vendor, so it's said once in
    // the header rather than on each row.
    const cats = new Set(bills.map(b => b.category || ''))
    const sharedCat = cats.size === 1 ? [...cats][0] : ''
    // Outstanding only earns a column when some bill is part paid; otherwise
    // it repeats Amount on every unpaid row. Decided from all the vendor's
    // bills, so the column doesn't come and go as filters change.
    const partPaid = bills.some(b => { const o = outstandingOf(b); return o > 0 && o !== billAmount(b) })
    const cols = partPaid ? 6 : 5

    // Unpaid views lead with the most overdue; the others read newest first.
    const byDue = billFilter !== 'all' && billFilter !== 'paid'
    const newest = (a: Bill, b: Bill) => (b.invoice_date || '').localeCompare(a.invoice_date || '')
    const sorted = [...shown].sort(byDue ? (a, b) => (a.due_date || '9999').localeCompare(b.due_date || '9999') : newest)
    const months = monthlyTotals(shown, now).filter(m => m.bills.length).reverse()
    // Month headers help a long statement; on a short list they just repeat the row beneath.
    const grouped = !byDue && shown.length >= GROUP_FROM && months.some(m => m.bills.length > 1)
    const undated = shown.filter(b => !dayOf(b.invoice_date))

    const counts = Object.fromEntries(BILL_FILTERS.map(f => [f.key, bills.filter(b => match(b, f.key)).length])) as Record<BillFilter, number>
    // A filter that matches nothing, or exactly what another one does, only adds noise.
    const same = (a: BillFilter, b: BillFilter) => counts[a] === counts[b] && bills.every(x => match(x, a) === match(x, b))
    const filters = BILL_FILTERS.filter(f => f.key === billFilter || f.key === 'all'
      || (counts[f.key] > 0 && counts[f.key] !== counts.all && !(f.key === 'overdue' && counts.overdue === counts.outstanding)
        && !(f.key === 'late' && (same('late', 'approval') || same('late', 'review')))))

    const unpaid = bills.filter(isOpen)
    const owed = unpaid.reduce((t, b) => t + outstandingOf(b), 0)
    const oldest = Math.max(0, ...unpaid.map(b => billState(b, now).late))
    const fx = bills.filter(isUnconverted)

    const exportCsv = () => {
      // Plain words, not codes: "In approval · 40d late", not PENDING_L1.
      const rows = [['Bill no.', 'Bill date', 'Due date', 'Amount (INR)', 'Outstanding (INR)', 'Status', 'Payment', 'Category', 'Currency', 'Amount in currency']]
      for (const b of sorted) {
        const pay = isPaid(b) ? 'Paid' : (b.payment_status || '').toLowerCase() === 'partial' ? 'Part paid' : 'Unpaid'
        const fxb = isUnconverted(b)
        rows.push([b.id, b.invoice_date ?? '', b.due_date ?? '', fxb ? '' : String(billAmount(b)), fxb ? '' : String(outstandingOf(b)),
          billState(b, now).label, pay, b.category ?? '', currencyOf(b), String(foreignAmount(b))])
      }
      const blob = new Blob([rows.map(r => r.map(csvCell).join(',')).join('\n')], { type: 'text/csv' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${vendorName.replace(/[^\w]+/g, '_')}_statement.csv`
      a.click()
      // Revoking straight away can cancel the download in Safari.
      const href = a.href
      window.setTimeout(() => URL.revokeObjectURL(href), 10_000)
    }
    const row = (b: Bill) => {
      const cat = b.category && b.category !== sharedCat ? b.category : ''
      const o = outstandingOf(b)
      return (
        <tr key={b.invoice_id} className="vc__r" onClick={() => onOpenBill?.(b.invoice_id)}>
          <td>
            {/* The bill number is the keyboard way in; the row click is a mouse shortcut. */}
            {onOpenBill
              ? <button type="button" className="vc__mono vc__billno vc__billno-btn" onClick={e => { e.stopPropagation(); onOpenBill(b.invoice_id) }}>{b.id || '—'}</button>
              : <div className="vc__mono vc__billno">{b.id || '—'}</div>}
            <div className="vc__muted vc__xs">{b.invoice_date ? `Billed ${dateOf(b.invoice_date)}` : 'No bill date'}{cat && ` · ${cat}`}</div>
          </td>
          <td>{dateOf(b.due_date)}</td>
          <td><BillStatus bill={b} now={now} /></td>
          <td className={`vc__num vc__strong${isUnconverted(b) ? ' vc__fx' : ''}`}>{billMoney(b)}</td>
          {partPaid && <td className="vc__num">{o ? money(o) : <span className="vc__muted">—</span>}</td>}
          <td className="vc__act">{b.upload_id && <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Preview ${b.id}`} title="Preview" onClick={e => { e.stopPropagation(); onPreviewBill(b) }}><Eye size={14} /></button>}</td>
        </tr>
      )
    }
    const groupRow = (key: string, label: ReactNode, list: Bill[]) => (
      <tr key={key} className="vc__grp">
        <td colSpan={3}>{label}</td>
        <td className="vc__num">{money(list.reduce((t, b) => t + billAmount(b), 0))}</td>
        {partPaid && <td className="vc__num">{money(list.reduce((t, b) => t + outstandingOf(b), 0))}</td>}
        <td />
      </tr>
    )
    const sum = (k: BillFilter, text: string) => <button key={k} className="vc__sumlink" onClick={() => onBillFilter(k)}>{text}</button>
    const shownFx = shown.filter(isUnconverted).length

    body = (
      <>
        <div className="vc__folder-h">
          <div className="vc__grow vc__min0">
            <div className="vc__folder-t">Bills{sharedCat && <span className="vc__folder-tag">{sharedCat}</span>}</div>
            <div className="vc__billsum" data-testid="bills-summary">
              {!bills.length ? 'No bills from this vendor yet.'
                : !unpaid.length ? `All ${bills.length} ${bills.length === 1 ? 'bill' : 'bills'} paid`
                : [
                    sum('outstanding', `${unpaid.length} unpaid · ${money(owed)}`),
                    oldest > 0 && <span key="late" className={oldest > 90 ? 'vc__bad' : undefined}>oldest {oldest}d late</span>,
                    counts.review > 0 && sum('review', `${counts.review} ${counts.review === 1 ? 'needs' : 'need'} review`),
                    counts.approval > 0 && sum('approval', `${counts.approval} waiting on approval`),
                  ].filter(Boolean).flatMap((part, i) => i ? [<span key={`s${i}`} className="vc__sep"> · </span>, part] : [part])}
            </div>
            {fx.length > 0 && <div className="vc__muted vc__xs vc__fxnote" data-testid="bills-fx-note">{unconvertedNote(fx)}</div>}
          </div>
          <button className="vc__btn vc__btn--sm" onClick={exportCsv} disabled={!shown.length} title="Download the bills shown as a CSV"><Download size={14} />Export CSV</button>
          <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={onUploadBill}><Upload size={14} />Upload bill</button>
        </div>
        {bills.length > 0 && (
          <div className="vc__toolbar">
            {filters.length > 1 && (
              <div className="vc__seg" role="group" aria-label="Filter bills">
                {filters.map(f => (
                  <button key={f.key} className={billFilter === f.key ? 'on' : ''} aria-pressed={billFilter === f.key} onClick={() => onBillFilter(f.key)}>{f.label}<span className="vc__c">{counts[f.key]}</span></button>
                ))}
              </div>
            )}
            <label className="vc__search"><Search size={14} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search bill no. or amount" aria-label="Search bills" /></label>
          </div>
        )}
        <div className="vc__tablewrap">
          <table className="vc__stmt">
            <thead><tr><th>Bill</th><th>Due</th><th>Status</th><th className="vc__num">Amount</th>{partPaid && <th className="vc__num">Outstanding</th>}<th aria-label="Preview" /></tr></thead>
            <tbody>
              {grouped
                ? <>
                    {months.map(m => [
                      groupRow(m.ym, <>{MONTHS_FULL[m.month]} {m.year}{m.partial && <span className="vc__muted"> · so far</span>}</>, m.bills),
                      ...[...m.bills].sort(newest).map(row),
                    ])}
                    {undated.length > 0 && [groupRow('undated', 'No bill date', undated), ...undated.map(row)]}
                  </>
                : sorted.map(row)}
              {!shown.length && <tr><td colSpan={cols} className="vc__empty">{bills.length ? 'No bills match.' : 'Bills from this vendor show up here, whether they come by email, upload or the agent.'}</td></tr>}
            </tbody>
            {shown.length > 1 && (
              <tfoot><tr>
                <td colSpan={3}>Total{shownFx > 0 && <span className="vc__muted vc__xs"> · excludes {plural(shownFx, 'unconverted bill')}</span>}</td>
                <td className="vc__num">{money(shown.reduce((t, b) => t + billAmount(b), 0))}</td>
                {partPaid && <td className="vc__num">{money(shown.reduce((t, b) => t + outstandingOf(b), 0))}</td>}
                <td />
              </tr></tfoot>
            )}
          </table>
        </div>
      </>
    )
  } else if (folder === 'contracts') {
    body = (
      <>
        <div className="vc__folder-h"><div className="vc__grow"><div className="vc__folder-t">Contracts &amp; IOs</div><div className="vc__folder-d">Agreements, insertion orders and addenda. Each one is read and its terms checked against this vendor’s details.</div></div>{addNew('contracts')}</div>
        {docList(contracts, 'No contracts yet.', { showClaims: true })}
      </>
    )
  } else if (folder === 'recent') {
    const recentBills = [...bills].filter(b => b.upload_id).sort((a, b) => (b.invoice_date ?? '').localeCompare(a.invoice_date ?? '')).slice(0, 5)
    body = (
      <>
        <div className="vc__folder-h"><div className="vc__grow"><div className="vc__folder-t">Recent</div><div className="vc__folder-d">The latest documents of every type.</div></div>{addNew('any')}</div>
        {docList(list.slice(0, 10), 'No documents uploaded yet.', { withType: true })}
        {billsReady && recentBills.length > 0 && (
          <>
            <div className="vc__subhead">Latest bills</div>
            <div className="vc__box">
              {recentBills.map(b => {
                const info = <><div className="vc__strong vc__ellipsis">{b.id}</div><div className="vc__muted vc__xs">Bill · {shortDate(b.invoice_date, true)} · {billMoney(b)}</div></>
                return (
                  <div key={b.invoice_id} className="vc__docrow">
                    <FileIcon name={b.original_file || 'bill.pdf'} />
                    {onOpenBill
                      ? <button type="button" className="vc__grow vc__min0 vc__docrow-main" onClick={() => onOpenBill(b.invoice_id)} title="Open bill">{info}</button>
                      : <div className="vc__grow vc__min0">{info}</div>}
                    <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Preview ${b.id}`} onClick={() => onPreviewBill(b)}><Eye size={14} /></button>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </>
    )
  } else {
    const label = folder.slice(2)
    body = (
      <>
        <div className="vc__folder-h"><div className="vc__grow"><div className="vc__folder-t">{label}</div><div className="vc__folder-d">A document type your team named. Files are stored and searchable, but not read for values.</div></div>{addNew(folder)}</div>
        {docList(byLabel.get(label.toLowerCase())?.docs ?? [], 'Nothing filed here for this vendor yet.')}
      </>
    )
  }

  // Where Add was clicked is where the file goes, unless the user changes it.
  const preset: Filing | null = !upload ? null
    : upload.preset === 'contracts' ? { kind: 'contract' }
    : typeof upload.preset === 'string' && upload.preset.startsWith('t:') ? { kind: 'existing', label: canonicalType(allTypes, upload.preset.slice(2)) }
    : null

  return (
    <div className="vc__docs">
      <nav className="vc__folders" aria-label="Document types">
        {fold('recent', 'Recent', <Clock size={14} />)}
        {fold('bills', 'Bills', <Receipt size={14} />, billsReady ? bills.length : undefined)}
        {fold('contracts', 'Contracts & IOs', <FileCheck2 size={14} />, docsState === 'ready' ? contracts.length : undefined)}
        {navLabels.map(l => fold(`t:${l}`, l, <Folder size={14} />, byLabel.get(l.toLowerCase())?.docs.length ?? 0))}
        <button className="vc__fold vc__fold--add" onClick={() => setUpload({ preset: 'any', file: null })} title="Name a type when you upload. It’s offered for every vendor."><Plus size={14} />New type</button>
      </nav>
      <div className="vc__min0">{body}</div>

      {upload && (
        <UploadDialog
          supplierId={supplierId} initialFile={upload.file} types={allTypes} vendorName={vendorName} preset={preset}
          onClose={() => setUpload(null)}
          onUploadBill={() => { setUpload(null); onUploadBill() }}
          onUpload={async (file, docType, docLabel) => {
            await uploadVendorDocument(supplierId, file, { docType, docLabel, uploadedBy: currentUser, brandId })
            setUpload(null)
            onReload()
            onFolder(docType === 'contract' ? 'contracts' : `t:${docLabel}`)
            notify(docType === 'contract' ? `${file.name} uploaded. Its terms are being read and checked.` : `Saved to ${docLabel}`, 'info')
          }}
        />
      )}
      {moving && (
        <MoveDialog doc={moving} types={allTypes} onClose={() => setMoving(null)}
          onMove={async (t, l) => {
            const r = await setVendorDocumentType(supplierId, moving.id, t, l)
            setMoving(null)
            onReload()
            notify(t === 'contract' ? (r.queued ? 'Moved to Contracts & IOs. It’s being read now.' : 'Moved to Contracts & IOs.') : `Moved to ${l}`, 'info')
          }} />
      )}
    </div>
  )
}

/** The dialog's drop area, built like Upload invoices': big target, round icon, one clear button. */
function BigDrop({ onFile, onChoose }: { onFile: (f: File, others: number) => void; onChoose: () => void }) {
  const [over, setOver] = useState(false)
  return (
    <div className={`vc__bigdrop${over ? ' is-over' : ''}`} role="button" tabIndex={0} onClick={onChoose}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onChoose() } }}
      onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
      onDrop={e => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f, e.dataTransfer.files.length - 1) }}>
      <div className="vc__bigdrop-ic"><Upload size={22} strokeWidth={1.8} /></div>
      <div className="vc__bigdrop-t">{over ? 'Drop it to read it' : <>Drag & drop a document here, or <span className="vc__bigdrop-lnk">click to upload</span></>}</div>
      <div className="vc__bigdrop-s">We read it and file it for you: contracts and IOs, certificates, rate cards and anything else.</div>
      <button type="button" className="vc__btn vc__btn--primary vc__bigdrop-btn" onClick={e => { e.stopPropagation(); onChoose() }}>Choose file</button>
      <div className="vc__bigdrop-fmts" aria-label="Accepted formats">{['PDF', 'Excel', 'Word', 'Image', `Up to ${MAX_MB} MB`].map(f => <span key={f}>{f}</span>)}</div>
    </div>
  )
}

function DocList({ docs, empty, onPreview, onDownload, onDelete, onMove, showClaims, withType }: {
  docs: VendorDocument[]; empty: string
  onPreview: (d: VendorDocument) => void; onDownload: (d: VendorDocument) => void; onDelete: (d: VendorDocument) => void; onMove: (d: VendorDocument) => void
  showClaims?: boolean; withType?: boolean
}) {
  if (!docs.length) return <div className="vc__box vc__empty vc__mt14">{empty}</div>
  return (
    <div className="vc__box vc__mt14">
      {docs.map(d => (
        <div key={d.id} className="vc__docrow">
          <FileIcon name={d.file_name} />
          <button className="vc__grow vc__min0 vc__docrow-main" onClick={() => onPreview(d)} title="Preview">
            <div className="vc__strong vc__ellipsis">{d.file_name}</div>
            <div className="vc__muted vc__xs vc__docmeta">
              {withType && <span className="vc__chip vc__chip--xs">{d.doc_type === 'contract' ? 'Contracts & IOs' : d.doc_label}</span>}
              {[d.uploaded_by, shortDate(d.uploaded_at, true), d.file_size ? `${Math.max(1, Math.round(d.file_size / 1024))} KB` : null].filter(Boolean).join(' · ')}
              {(showClaims || withType) && d.doc_type === 'contract' && <ClaimsChip status={d.claims_status} />}
            </div>
          </button>
          <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Move ${d.file_name} to another type`} title="Change type" onClick={() => onMove(d)}><FolderInput size={14} /></button>
          <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Download ${d.file_name}`} title="Download" onClick={() => onDownload(d)}><Download size={14} /></button>
          <button className="vc__iconbtn vc__iconbtn--sm vc__iconbtn--danger" aria-label={`Delete ${d.file_name}`} title="Delete" onClick={() => onDelete(d)}><Trash2 size={14} /></button>
        </div>
      ))}
    </div>
  )
}

function TypeOption({ on, icon, title, detail, children, onPick, suggested }: {
  on: boolean; icon: ReactNode; title: string; detail: string; children?: ReactNode; onPick: () => void; suggested?: boolean
}) {
  return (
    <div className={`vc__typeopt${on ? ' on' : ''}`} role="radio" aria-checked={on} tabIndex={0} onClick={onPick} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick() } }}>
      <div className="vc__typeopt-ic">{icon}</div>
      <div className="vc__grow">
        <div className="vc__typeopt-t">{title}{suggested && <span className="vc__chip vc__chip--xs vc__chip--info">Suggested</span>}</div>
        <div className="vc__typeopt-d">{detail}</div>
        {children}
      </div>
    </div>
  )
}

// The same motion as the full-screen PDF viewer (pages/BillPdfFullscreen.tsx).
const FLY_MS = 680
const BLUR_MS = 460
const EASE_OUT = 'cubic-bezier(0.22, 1, 0.36, 1)'
const EASE_IN_OUT = 'cubic-bezier(0.65, 0, 0.35, 1)'
const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Where `to` would sit if it were drawn over `from`: the dialog flies out of the button. */
function flyFrom(from: DOMRect, to: DOMRect): string {
  const s = Math.max(0.04, Math.min(from.width / to.width, from.height / to.height))
  const dx = (from.left + from.width / 2) - (to.left + to.width / 2)
  const dy = (from.top + from.height / 2) - (to.top + to.height / 2)
  return `translate(${dx}px, ${dy}px) scale(${s})`
}

/**
 * A dialog over the whole page, flying out of the button that opened it and
 * back into it on close, the backdrop blurring in as it lands.
 *
 * Rendered at the page root: inside the card, an animated view (each view
 * eases in with a transform) becomes the box that `position: fixed` measures
 * from, so the dimmed backdrop covered only that view and a tall dialog ran
 * off the bottom of the screen.
 */
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function Layer({ busy, onClose, children }: { busy?: boolean; onClose: () => void; children: (close: () => void) => ReactNode }) {
  const backdrop = useRef<HTMLDivElement>(null)
  // What was focused when it opened is what it flies out of (and back to).
  const source = useRef<Element | null>(typeof document !== 'undefined' ? document.activeElement : null)
  const closing = useRef(false)
  const panel = () => backdrop.current?.firstElementChild as HTMLElement | null
  const from = () => {
    const el = source.current
    const r = el && el !== document.body && el.isConnected ? el.getBoundingClientRect() : null
    return r && r.width ? r : null
  }

  useLayoutEffect(() => {
    const b = backdrop.current, p = panel()
    if (!b || !p || reducedMotion() || typeof p.animate !== 'function') return
    const f = from()
    const fly = p.animate(
      [
        { transform: f ? flyFrom(f, p.getBoundingClientRect()) : 'translateY(24px) scale(0.96)', opacity: 0.85 },
        { transform: 'none', opacity: 1 },
      ],
      { duration: FLY_MS, easing: EASE_OUT, fill: 'both' },
    )
    const blur = b.animate(
      [
        { backgroundColor: 'rgb(11 28 48 / 0%)', backdropFilter: 'blur(0px)', WebkitBackdropFilter: 'blur(0px)' },
        { backgroundColor: 'rgb(11 28 48 / 32%)', backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)' },
      ],
      { duration: BLUR_MS, delay: FLY_MS * 0.35, easing: EASE_IN_OUT, fill: 'both' },
    )
    return () => { fly.cancel(); blur.cancel() }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const close = () => {
    if (closing.current) return
    const b = backdrop.current, p = panel()
    if (!b || !p || reducedMotion() || typeof p.animate !== 'function') { onClose(); return }
    closing.current = true
    const f = from()
    b.animate(
      [{ backgroundColor: 'rgb(11 28 48 / 0%)', backdropFilter: 'blur(0px)', WebkitBackdropFilter: 'blur(0px)' }],
      { duration: BLUR_MS * 0.7, easing: EASE_IN_OUT, fill: 'both' },
    )
    p.animate(
      [{ transform: f ? flyFrom(f, p.getBoundingClientRect()) : 'translateY(24px) scale(0.96)', opacity: 0 }],
      { duration: FLY_MS * 0.6, delay: BLUR_MS * 0.15, easing: EASE_IN_OUT, fill: 'both' },
    ).finished.then(onClose, onClose)
  }

  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) { e.stopPropagation(); close() } }
    document.addEventListener('keydown', esc, true)
    return () => document.removeEventListener('keydown', esc, true)
  })

  // A real modal: the page behind doesn't scroll, focus starts inside and Tab
  // stays there, and closing hands focus back to whatever opened it.
  useEffect(() => {
    const p = panel()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const items = () => Array.from(p?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(el => el.offsetParent !== null || el === document.activeElement)
    if (p && !p.contains(document.activeElement)) (items()[0] ?? p).focus({ preventScroll: true })
    const tab = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !p) return
      const list = items()
      if (!list.length) { e.preventDefault(); return }
      const first = list[0], last = list[list.length - 1]
      if (!p.contains(document.activeElement)) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', tab, true)
    const src = source.current as HTMLElement | null
    return () => {
      document.removeEventListener('keydown', tab, true)
      document.body.style.overflow = overflow
      if (src?.isConnected) src.focus?.({ preventScroll: true })
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  return createPortal(
    <div className="vc__mov vc__mov--fly" ref={backdrop} onMouseDown={e => { if (e.target === e.currentTarget && !busy) close() }}>
      {children(close)}
    </div>,
    document.body,
  )
}

/** The team's named types, one click to use. */
function TypeChips({ types, value, onPick }: { types: string[]; value: string; onPick: (t: string) => void }) {
  if (!types.length) return null
  return (
    <div className="vc__typechips" role="group" aria-label="Your types">
      {types.slice(0, 8).map(t => (
        <button key={t} type="button" className={`vc__typechip${t === value ? ' on' : ''}`} aria-pressed={t === value} onClick={() => onPick(t)}>
          <Folder size={11} />{t}
        </button>
      ))}
    </div>
  )
}

type Filing = { kind: 'contract' } | { kind: 'existing'; label: string } | { kind: 'new'; name: string }

const sameFiling = (a: Filing, b: Filing) => a.kind === b.kind
  && (a.kind !== 'existing' || a.label.toLowerCase() === (b as { label: string }).label.toLowerCase())
  && (a.kind !== 'new' || a.name.toLowerCase() === (b as { name: string }).name.toLowerCase())
const filingName = (f: Filing) => f.kind === 'contract' ? 'a contract or IO' : f.kind === 'existing' ? f.label : f.name ? `a new type, “${f.name}”` : 'a new type'

/** Why a file can't be added, or '' when it can. */
function fileProblem(f: File): string {
  const ext = (f.name.split('.').pop() || '').toLowerCase()
  if (!ACCEPT.split(',').includes(`.${ext}`)) return `${f.name} isn’t a PDF, Excel, Word or image file.`
  if (f.size > MAX_MB * 1024 * 1024) return `${f.name} is ${Math.round(f.size / 1024 / 1024)} MB. The limit is ${MAX_MB} MB.`
  return ''
}

/**
 * Add a document: drop it in and it's read to decide where it goes (a contract
 * or IO, one of the team's types, or a new type it suggests a name for). The
 * user can change that, and uploads in one step. Bills aren't added here.
 */
function UploadDialog({ supplierId, initialFile, types, vendorName, preset = null, onClose, onUpload, onUploadBill }: {
  supplierId: string; initialFile: File | null; types: string[]; vendorName: string
  /** Where Add was clicked (Contracts, a type): the starting filing. Reading the file may only suggest another. */
  preset?: Filing | null
  onClose: () => void; onUpload: (f: File, t: DocType, label: string | null) => Promise<void>; onUploadBill: () => void
}) {
  const pick = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(initialFile)
  const [reading, setReading] = useState(false)
  const [verdict, setVerdict] = useState<DocVerdict | null>(null)
  const [filing, setFiling] = useState<Filing | null>(preset)
  const [suggestion, setSuggestion] = useState<Filing | null>(null)
  const [notice, setNotice] = useState('')
  const [isBill, setIsBill] = useState(false)
  const [changing, setChanging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const asked = useRef(0)

  const decide = (v: DocVerdict) => {
    setVerdict(v)
    setIsBill(v.kind === 'bill')
    const read: Filing | null = v.kind === 'contract' ? { kind: 'contract' }
      : v.kind === 'existing' && v.label ? { kind: 'existing', label: v.label }
      : v.kind === 'new' ? { kind: 'new', name: v.suggested_name ?? '' } : null
    if (preset && v.kind !== 'bill') {
      // The user said where it goes by where they clicked; the read is only a suggestion.
      setFiling(preset); setChanging(false)
      setSuggestion(read && !sameFiling(read, preset) ? read : null)
      return
    }
    // Not sure: show the best guess with the choices open, to confirm.
    setChanging(v.sure === false && v.kind !== 'bill')
    setFiling(read)
  }

  const choose = (f: File, others = 0) => {
    const problem = fileProblem(f)
    if (problem) { setError(problem); setNotice(''); return }
    setNotice(others > 0 ? `Only ${f.name} was added. Add the other ${plural(others, 'file')} one at a time.` : '')
    setFile(f); setError(''); setChanging(false); setVerdict(null); setFiling(preset); setSuggestion(null); setIsBill(false)
    const n = ++asked.current
    setReading(true)
    classifyVendorDocument(supplierId, f)
      .then(v => { if (n === asked.current) decide(v) })
      .catch(() => {
        if (n !== asked.current) return
        // Couldn't read it: go by the file name, and let the user say.
        const g = guessType(f.name)
        // A guess from the name, so never presented as sure.
        decide({ kind: g.type === 'bill' ? 'bill' : g.type === 'contract' ? 'contract' : g.label && types.includes(g.label) ? 'existing' : 'new',
          label: g.label, suggested_name: g.label ?? '', title: '', reason: 'Couldn’t read the file, so this goes by its name.', source: 'guess', sure: false })
      })
      .finally(() => { if (n === asked.current) setReading(false) })
  }
  useEffect(() => { if (initialFile) choose(initialFile) }, [])  // eslint-disable-line react-hooks/exhaustive-deps
  // A slow read shouldn't hold the user up: drop it and pick by hand.
  const skipRead = () => { asked.current++; setReading(false); setChanging(true) }

  // Jev was unsure and the user hasn't picked yet. With a preset, the user already has.
  const unsure = !preset && verdict?.sure === false
  const name = filing?.kind === 'new' ? filing.name.trim() : ''
  const ready = !!file && !reading && !isBill && !!filing && (filing.kind !== 'new' || !!name)
  const submit = async () => {
    if (!file || !filing || !ready) return
    setBusy(true); setError('')
    try {
      if (filing.kind === 'contract') await onUpload(file, 'contract', null)
      // An existing type typed in another case files into that type, not a near-duplicate.
      else await onUpload(file, 'other', canonicalType(types, filing.kind === 'existing' ? filing.label : name))
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Upload failed. Try again.')
    } finally { setBusy(false) }
  }

  const where = !filing ? null
    : filing.kind === 'contract' ? { icon: <FileCheck2 size={18} />, title: 'Contracts & IOs', note: 'Its terms are read and checked against this vendor: payment terms, bank, PAN and GST.' }
    : filing.kind === 'existing' ? { icon: <Folder size={18} />, title: filing.label, note: 'One of your types.' }
    : { icon: <Plus size={18} />, title: 'A new type', note: 'Offered for every vendor after this.' }

  return (
    <Layer busy={busy} onClose={onClose}>{close => (
      <div className="vc__modal" role="dialog" aria-modal="true" aria-label="Add a document">
        <div className="vc__modal-h">
          <div className="vc__icotile"><Upload size={16} /></div>
          <div className="vc__grow"><div className="vc__modal-t">Add a document</div><div className="vc__muted vc__xs">{vendorName}</div></div>
          <button className="vc__iconbtn" aria-label="Close" onClick={close} disabled={busy}><X size={16} /></button>
        </div>
        <div className="vc__modal-b">
          <input ref={pick} type="file" hidden accept={ACCEPT} onChange={e => { const f = e.target.files?.[0]; if (f) choose(f); e.target.value = '' }} />
          {file ? (
            <div className="vc__filepill"><FileIcon name={file.name} /><div className="vc__grow vc__min0"><div className="vc__strong vc__ellipsis">{file.name}</div><div className="vc__muted vc__xs">{Math.max(1, Math.round(file.size / 1024))} KB</div></div><button className="vc__lnk" onClick={() => pick.current?.click()} disabled={busy}>Change file</button></div>
          ) : (
            <BigDrop onFile={choose} onChoose={() => pick.current?.click()} />
          )}

          {reading && (
            <div className="vc__dc vc__dc--reading" role="status">
              <span className="vc__dc-ic vc__dc-ic--ai"><Sparkles size={18} /></span>
              <div className="vc__grow">
                <div className="vc__dc-k">Reading the document…</div>
                <span className="vc__skel" style={{ width: '70%' }} /><span className="vc__skel" style={{ width: '45%' }} />
                <button type="button" className="vc__lnk vc__lnk--brand" onClick={skipRead}>I’ll choose</button>
              </div>
            </div>
          )}

          {!reading && isBill && (
            <div className="vc__dc vc__dc--bill" role="status">
              <span className="vc__dc-ic vc__dc-ic--warn"><Receipt size={18} /></span>
              <div className="vc__grow">
                <div className="vc__dc-t">This looks like a bill</div>
                {verdict?.reason && <div className="vc__dc-why">{verdict.reason}</div>}
                <div className="vc__dc-note">Bills are added through bill upload, where they’re read, checked and sent for approval.</div>
                <div className="vc__dc-acts">
                  <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={onUploadBill}><Receipt size={13} />Go to bill upload</button>
                  <button className="vc__lnk" onClick={() => { setIsBill(false); setFiling(preset ?? { kind: 'new', name: '' }); setChanging(!preset) }}>It’s not a bill</button>
                </div>
              </div>
            </div>
          )}

          {!reading && !isBill && where && (
            <div className="vc__dc" key={filing?.kind === 'existing' ? filing.label : filing?.kind}>
              <span className={`vc__dc-ic${filing?.kind === 'contract' ? ' vc__dc-ic--brand' : ''}`}>{where.icon}</span>
              <div className="vc__grow vc__min0">
                <div className={`vc__dc-k${unsure ? ' is-unsure' : ''}`}>
                  {unsure ? 'Not sure. Check this' : !preset && verdict?.source === 'ai' ? <><Sparkles size={12} />Filing it under</> : 'Filing it under'}
                  {!preset && verdict?.confidence != null && <span className="vc__dc-sure" title="How sure Jev is">{Math.round(verdict.confidence * 100)}% sure</span>}
                </div>
                {filing?.kind === 'new' ? (
                  <input className="vc__dc-name" aria-label="Type name" value={filing.name} maxLength={80} placeholder="Name it, e.g. MSME certificates"
                    autoFocus={!filing.name} onChange={e => setFiling({ kind: 'new', name: e.target.value })} />
                ) : <div className="vc__dc-t">{where.title}</div>}
                {(verdict?.title || verdict?.reason) && !suggestion && <div className="vc__dc-why">{[verdict.title, verdict.reason].filter(Boolean).join(' · ')}</div>}
                {suggestion && (
                  <div className="vc__dc-suggest" data-testid="filing-suggestion">
                    <Sparkles size={12} />It reads like {filingName(suggestion)}.{' '}
                    <button type="button" className="vc__lnk vc__lnk--brand" onClick={() => { setFiling(suggestion); setSuggestion(null) }}>File it there</button>
                  </div>
                )}
                <div className="vc__dc-note">{filing?.kind === 'new' && name && types.some(t => t.toLowerCase() === name.toLowerCase()) ? 'Adds to your existing type.' : where.note}</div>
              </div>
              {!changing && <button className="vc__lnk vc__lnk--brand vc__dc-change" onClick={() => setChanging(true)}>Change</button>}
            </div>
          )}

          {!reading && !isBill && file && changing && (
            <div className="vc__dc-pick" role="group" aria-label="File it under">
              <button className={`vc__typechip${filing?.kind === 'contract' ? ' on' : ''}`} aria-pressed={filing?.kind === 'contract'} onClick={() => setFiling({ kind: 'contract' })}><FileCheck2 size={11} />Contract or IO</button>
              {types.slice(0, 10).map(t => (
                <button key={t} className={`vc__typechip${filing?.kind === 'existing' && filing.label === t ? ' on' : ''}`} aria-pressed={filing?.kind === 'existing' && filing.label === t}
                  onClick={() => setFiling({ kind: 'existing', label: t })}><Folder size={11} />{t}</button>
              ))}
              <button className={`vc__typechip${filing?.kind === 'new' ? ' on' : ''}`} aria-pressed={filing?.kind === 'new'}
                onClick={() => setFiling({ kind: 'new', name: verdict?.suggested_name ?? '' })}><Plus size={11} />New type</button>
            </div>
          )}

          {error && <div className="vc__err" role="alert">{error}</div>}
          {notice && <div className="vc__muted vc__xs" role="status">{notice}</div>}
          {!isBill && (
            <div className="vc__dc-billnote"><span><Receipt size={13} />Adding a bill? Bills go through <button className="vc__lnk vc__lnk--brand" onClick={onUploadBill}>bill upload</button></span></div>
          )}
        </div>
        <div className="vc__modal-f">
          <span className="vc__grow" />
          <button className="vc__btn" onClick={close} disabled={busy}>Cancel</button>
          {file && <button className="vc__btn vc__btn--primary" onClick={submit} disabled={busy || !ready}>
            {busy ? 'Uploading…' : !filing || isBill ? 'Upload'
              : filing.kind === 'contract' ? 'Upload and check'
              : filing.kind === 'existing' ? `Upload to ${filing.label}`
              : name ? `Create “${name}” and upload` : 'Upload'}
          </button>}
        </div>
      </div>
    )}</Layer>
  )
}

function MoveDialog({ doc, types, onClose, onMove }: {
  doc: VendorDocument; types: string[]; onClose: () => void; onMove: (t: DocType, label: string | null) => Promise<void>
}) {
  const [type, setType] = useState<DocType>(doc.doc_type === 'contract' ? 'other' : 'contract')
  const [label, setLabel] = useState(doc.doc_type === 'other' ? '' : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const name = label.trim()
  const go = async () => {
    if (type === 'other' && !name) { setError('Name the type.'); return }
    setBusy(true)
    try { await onMove(type, type === 'other' ? canonicalType(types, name) : null) }
    catch (e: unknown) { setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Couldn’t move it. Try again.'); setBusy(false) }
  }
  return (
    <Layer busy={busy} onClose={onClose}>{close => (
      <div className="vc__modal" role="dialog" aria-modal="true" aria-label="Change document type">
        <div className="vc__modal-h">
          <FileIcon name={doc.file_name} />
          <div className="vc__grow vc__min0"><div className="vc__modal-t">Change type</div><div className="vc__muted vc__xs vc__ellipsis">{doc.file_name}</div></div>
          <button className="vc__iconbtn" aria-label="Close" onClick={close} disabled={busy}><X size={16} /></button>
        </div>
        <div className="vc__modal-b">
          <TypeOption on={type === 'contract'} icon={<FileCheck2 size={16} />} title="Contract or IO"
            detail={doc.claims_status === 'SKIPPED' ? 'It will be read and checked against the vendor.' : 'Read and checked against the vendor.'} onPick={() => setType('contract')} />
          <TypeOption on={type === 'other'} icon={<Folder size={16} />} title="Another type" detail="Stored under a named type, not read for values." onPick={() => setType('other')}>
            {type === 'other' && (
              <div className="vc__field" onClick={e => e.stopPropagation()}>
                <input autoFocus list="vc-move-types" value={label} maxLength={80} placeholder="Type name" aria-label="Type name" onChange={e => setLabel(e.target.value)} />
                <datalist id="vc-move-types">{types.filter(t => t !== doc.doc_label).map(t => <option key={t} value={t} />)}</datalist>
                <TypeChips types={types.filter(t => t !== doc.doc_label)} value={name} onPick={setLabel} />
              </div>
            )}
          </TypeOption>
          {error && <div className="vc__err">{error}</div>}
        </div>
        <div className="vc__modal-f">
          <span className="vc__grow" />
          <button className="vc__btn" onClick={close} disabled={busy}>Cancel</button>
          <button className="vc__btn vc__btn--primary" onClick={go} disabled={busy}>{busy ? 'Moving…' : 'Move'}</button>
        </div>
      </div>
    )}</Layer>
  )
}
