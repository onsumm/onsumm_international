import React, { useEffect, useState, useRef, memo, Fragment, lazy, Suspense } from 'react'
import TdsBreakdown from '../components/TdsBreakdown'
import BillSection from '../components/BillSection'
import GstBreakdown from '../components/GstBreakdown'
import BillVerificationCard from '../components/BillVerificationCard'
import { VendorLink } from '../components/records/RecordLink'
import { useJurisdiction, useService } from '../auth/EntitlementsContext'
import { formatTenantMoney } from '../auth/jurisdiction'
import { fetchBillReconciliation, reconcileAction, type BillReconciliation } from '../api/billing'
import { FileText, RefreshCw, AlertCircle, X, ZoomIn, ZoomOut, RotateCcw, RotateCwSquare, ChevronLeft, ChevronRight, Check, CornerDownLeft, Send, Pencil, ChevronDown, Download, Info, Shield, Trash2, Bot, AlertTriangle, Zap, Search, PauseCircle, ArrowUpCircle, Layers, Plus, Maximize2 } from 'lucide-react'
import { useAuthedObjectUrl, downloadAuthed, fetchAuthedBlob } from '../api/authedFiles'
import {
  Button, ConfirmDialog, PdfLoading, formatMoney, LoadMore, Pager, useInfiniteRows, usePagedRows,
  useColumnFilters, FilterTh, FilterStatus, type FilterField,
} from '../ui'
import { fetchAgentDrafts, fetchAgentDraft, fetchAgentDraftFile, discardDraft, type AgentDraft } from '../api/agent'

import { getAllCategories, addCustomCategory, syncCategoriesFromApi } from '../utils/categories'
import { gstinChecksumError, isValidGstinChecksum } from '../utils/gstin'
const API_BASE = import.meta.env.VITE_API_URL ?? ''
import { Document, Page, pdfjs } from 'react-pdf'
import type { PDFDocumentProxy } from 'pdfjs-dist'
import { ApprovalStepper } from '../components/ApprovalStepper'
import { _chainCache, chainCacheKey, prefetchChains } from '../components/approvalChain'
import { fetchBills, fetchBillDetail, billAction, updateBill, updatePaymentStatus, fetchNotificationCount, invalidateDetailCache, invalidateBillsCache, bulkBillAction, fetchChainConfig, fetchDepartments, recordFxPayment, fetchFxPayments, fetchFxAgentNote, updateVendor, fetchVendorCategories, fetchTdsSections, previewTds, createVendorOverride, fetchDisbursements, fetchVendorPanDuplicates, mergeVendors, deferVendorDedup, invalidateVendorsCache, fetchPriorityLabels, updatePriorityLabels, type Bill, type BillDetail, type ChainEntry, type BulkActionResult, type BillAttachment, type FxPayment, type FxAgentNote, type VendorCategory, type TdsSection, type CatchupSummary, type Disbursement, type PanDuplicate, type PriorityLabels } from '../api/invoices'
import axios from 'axios'
import { useAuth } from '../auth/AuthContext'
import { getChain } from '../utils/chainConfig'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { PATHS, billPath, payPath } from '../routes/paths'
import './bills.css'
import { BillPdfFullscreen } from './BillPdfFullscreen'
import PdfFullscreen from '../components/PdfFullscreen'
import { usePinchZoom } from './usePinchZoom'
import { PHONE_QUERY, useMediaQuery } from '../hooks/useMediaQuery'
import BillCryptoChecks from '../components/crypto/BillCryptoChecks'

// Loaded when an attachment preview is opened, not on first paint: this is the
// one component that pulls in the ~400 kB xlsx parser.
const ExcelPreview = lazy(() => import('../components/ExcelPreview').then(m => ({ default: m.ExcelPreview })))

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  DRAFT:      { bg: '#edf2f7', color: '#475467', label: 'Draft' },
  PENDING:    { bg: '#fff3d6', color: '#9a5b00', label: 'Pending' },
  APPROVED:   { bg: '#def7ea', color: '#1d7a5f', label: 'Approved' },
  PAID:       { bg: '#def7ea', color: '#1d7a5f', label: 'Paid' },
  REJECTED:   { bg: '#fde8e8', color: '#b42318', label: 'Rejected' },
  CANCELLED:  { bg: '#fde8e8', color: '#b42318', label: 'Cancelled' },
}

function StatusBadge({ status, paymentStatus, statusLabel }: { status: string; paymentStatus?: string | null; statusLabel?: string | null }) {
  const key = status?.toUpperCase()
  const effectiveKey = (key === 'APPROVED' && paymentStatus === 'paid') ? 'PAID' : key
  const base = STATUS_STYLE[effectiveKey] ?? { bg: '#f3f4f6', color: '#6b7280', label: status }
  // Use chain-derived label for pending steps instead of raw status key
  const isPending = key?.startsWith('PENDING')
  const label = isPending && statusLabel ? `Pending: ${statusLabel}` : base.label
  const s = { ...base, label }
  return (
    <span style={{
      display: 'inline-block', padding: '4px 10px', borderRadius: 999,
      background: s.bg, color: s.color, fontSize: 11, fontWeight: 700,
      whiteSpace: 'nowrap', verticalAlign: 'middle', cursor: 'default',
      border: '1px solid rgba(15, 23, 42, 0.04)', letterSpacing: '0.01em',
    }}>{s.label}</span>
  )
}

/** Money in a tenant's books currency: Indian grouping for INR, international otherwise. */
function tenantMoney(v: number | null | undefined, currency: string, precise = true): string {
  return formatTenantMoney(v, undefined, { currency, precise })
}

function fmt(val: string | null, type: 'date' | 'amount' | 'text', currency = 'INR') {
  if (!val) return <span style={{ color: '#d1d5db' }}>—</span>
  if (type === 'date') {
    return new Date(val as string).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
  }
  if (type === 'amount') {
    return tenantMoney(val as unknown as number, currency)
  }
  return val
}


// ── Stable PDF viewer — memoized so typing in comments never reloads the PDF ─
const PdfViewer = memo(function PdfViewer({
  doc, pageNum, pdfW, zoom, rotate = 0, onLoaded, onError, onRendered, pdfRef,
}: {
  doc: { url: string | null; error: boolean }
  pageNum: number
  pdfW: number
  zoom: number
  /** Degrees clockwise: 0, 90, 180 or 270. Scanned bills are often sideways. */
  rotate?: number
  onLoaded: (pdf: PDFDocumentProxy) => void
  onError: () => void
  onRendered: () => void
  pdfRef: React.RefObject<HTMLDivElement>
}) {
  useEffect(() => { if (doc.error) onError() }, [doc.error, onError])

  return (
    <div className="bm__pdfbody" ref={pdfRef}>
      {doc.url ? (
        <Document
          file={doc.url}
          onLoadSuccess={onLoaded}
          onLoadError={onError}
          loading={<PdfLoading stage="rendering" />}
          error={null}
        >
          <Page pageNumber={pageNum} rotate={rotate} width={Math.max(200, pdfW + zoom)} renderAnnotationLayer={false} renderTextLayer={false} onRenderSuccess={onRendered} />
        </Document>
      ) : (
        doc.error
          ? <div style={{ color: 'var(--ink-500)', fontSize: 13, marginTop: 60, textAlign: 'center' }}>Could not load this document.</div>
          : <PdfLoading stage="fetching" />
      )}
    </div>
  )
})

// ── Export PDF button ────────────────────────────────────────────────────────
function ExportPdfButton({ invoiceId, billId, companyId }: { invoiceId: string; billId?: string; companyId?: string }) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(false)

  const handleExport = async () => {
    setLoading(true)
    setErr(false)
    try {
      const url = `${import.meta.env.VITE_API_URL ?? ''}/api/bills/${invoiceId}/export-pdf?company_id=${companyId ?? ''}`
      const res = await fetch(url)
      if (!res.ok) throw new Error('failed')
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `bill_${(billId || invoiceId.slice(0, 8)).replace(/\//g, '-')}.pdf`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch {
      setErr(true)
      setTimeout(() => setErr(false), 3000)
    } finally {
      setLoading(false)
    }
  }

  return (
    <button
      onClick={handleExport}
      disabled={loading}
      aria-label={loading ? 'Exporting PDF' : err ? 'Export failed' : 'Export PDF'}
      className="bm__edit-btn"
      style={{
        display: 'flex', alignItems: 'center', gap: 5,
        padding: '6px 14px',
        background: err ? '#fff5f5' : '#eff6ff',
        border: `1px solid ${err ? '#fca5a5' : '#bfdbfe'}`,
        color: err ? '#dc2626' : '#2563eb',
        borderRadius: 8, fontSize: 12, fontWeight: 600,
        cursor: loading ? 'wait' : 'pointer',
        fontFamily: 'inherit', opacity: loading ? 0.7 : 1,
        transition: 'all 0.15s',
      }}
    >
      {loading
        ? <><RefreshCw size={13} style={{ animation: 'bp-spin 1s linear infinite' }} /> <span className="bm__btn-text">Exporting…</span></>
        : err
          ? <>&#x26A0; <span className="bm__btn-text">Export failed</span></>
          : <><Download size={13} /> <span className="bm__btn-text">Export PDF</span></>}
    </button>
  )
}

// Field helpers — kept at module scope so they retain a stable component
// identity across BillModal re-renders. Defining them inside BillModal made
// every keystroke remount the <input>, dropping focus after one character.
function EField({ k, label, type = 'text', editData, setEditData, checksum }: {
  k: string; label: string; type?: string;
  editData: Record<string, string>;
  setEditData: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  checksum?: boolean;
}) {
  const val = editData[k] ?? ''
  const err = checksum ? gstinChecksumError(val) : null
  const ok  = checksum ? isValidGstinChecksum(val) : false
  return (
    <div>
      <div className="bm__lbl">{label}</div>
      <div className={`bm__field${err ? ' bm__field--err' : ''}`}>
        <input className={`bm__input${err ? ' bm__input--err' : ''}`} type={type} value={val}
               aria-invalid={err ? true : undefined}
               onChange={e => setEditData(p => ({ ...p, [k]: e.target.value }))} />
        {err && <span className="bm__warn"><AlertTriangle size={13} /></span>}
        {err && <span className="bm__tip" role="tooltip">{err}</span>}
        {ok && !err && <span className="bm__badge" aria-label="Valid GSTIN checksum"><Check size={11} strokeWidth={3} /></span>}
      </div>
    </div>
  )
}

// `fix` names the field for a Review correction pinned to it (CorrectionPins).
function Field({ label, value, mono, wide, checksum, fix }: { label: string; value: React.ReactNode; mono?: boolean; wide?: boolean; checksum?: boolean; fix?: string }) {
  const str = typeof value === 'string' ? value : ''
  const err = checksum ? gstinChecksumError(str) : null
  const ok  = checksum ? isValidGstinChecksum(str) : false
  return (
    <div className="bm__kv" style={{ gridColumn: wide ? '1 / -1' : undefined }} data-fix={fix}>
      <div className="bm__lbl">{label}</div>
      <div className={`bm__field${err ? ' bm__field--err' : ''}`} style={{ display: 'inline-flex', alignItems: 'center', maxWidth: '100%' }}>
        <div className={`bm__val${err ? ' bm__val--err' : ''}`} style={mono ? { fontFamily: 'monospace', fontSize: 12 } : {}}>{value ?? '—'}</div>
        {err && <span className="bm__warn bm__warn--inline"><AlertTriangle size={13} /></span>}
        {err && <span className="bm__tip" role="tooltip">{err}</span>}
        {ok && !err && <span className="bm__badge bm__badge--inline" aria-label="Valid GSTIN checksum"><Check size={11} strokeWidth={3} /></span>}
      </div>
    </div>
  )
}

function SectionHead({ title }: { title: string }) {
  return <div style={{ gridColumn: '1 / -1' }} className="bm__section">{title}</div>
}

// ── Extracted coding, in the Review Hub's claims-panel shape ─────────────────
// Deliberately the same three columns that panel uses — LABEL | value | state
// badge — and the same tokens, so a vendor's extracted-terms panel and a bill's
// approval screen are recognisably the same surface rather than two house
// styles. See ClaimSection/ClaimRow in VendorsPage.tsx.

// On a phone it folds like BillSection, shut by default; `aside` stands in for
// the rows while folded — the bank's verified state must not hide behind a tap.
// On a laptop the heading is a plain label and nothing folds.
function CodingSection({ title, aside, children }: { title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  const isPhone = useMediaQuery(PHONE_QUERY)
  const [open, setOpen] = useState(false)
  const shown = !isPhone || open
  return (
    <tbody className={shown ? undefined : 'bm__coding-sec--closed'}>
      <tr>
        <th colSpan={3} style={{ textAlign: 'left', padding: 'var(--sp-3) var(--sp-3) var(--sp-1)', fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-bold)', color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid var(--line-200)' }}>
          {isPhone ? (
            <button type="button" className="bm__coding-head" aria-expanded={open} onClick={() => setOpen(o => !o)}>
              <span className="bm__group-title">{title}</span>
              {!open && aside}
              <ChevronDown size={14} className="bm__group-chev" aria-hidden="true" />
            </button>
          ) : title}
        </th>
      </tr>
      {shown && children}
    </tbody>
  )
}

function CodingRow({ label, value, sub, badge, mono, fix }: {
  label: string; value: React.ReactNode; sub?: string | null
  badge?: React.ReactNode; mono?: boolean; fix?: string
}) {
  return (
    <tr data-fix={fix}>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top', color: 'var(--ink-500)', fontSize: 'var(--fs-2xs)', textTransform: 'uppercase', letterSpacing: '0.03em', whiteSpace: 'nowrap' }}>
        {label}
      </td>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top' }}>
        <div style={{ fontSize: 'var(--fs-sm)', fontWeight: 'var(--fw-semibold)', color: 'var(--ink-900)', ...(mono ? { fontFamily: 'monospace' } : {}) }}>
          {value ?? <span style={{ color: 'var(--ink-400, #9ca3af)', fontWeight: 400 }}>Not set</span>}
        </div>
        {sub && <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)', marginTop: 2 }}>{sub}</div>}
      </td>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top', textAlign: 'right', whiteSpace: 'nowrap' }}>
        {badge}
      </td>
    </tr>
  )
}

function CodingBadge({ tone, children }: { tone: 'ok' | 'warn'; children: React.ReactNode }) {
  const c = tone === 'ok'
    ? { color: 'var(--success-700)', bg: '#ecfdf5', border: '#a7f3d0' }
    : { color: 'var(--warn-700)', bg: '#fffbeb', border: '#fde68a' }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: c.color, background: c.bg, border: `1px solid ${c.border}`, borderRadius: 999, padding: '1px 8px' }}>
      {children}
    </span>
  )
}

function ExtractedCoding({ detail, sapEnabled }: { detail: BillDetail; sapEnabled: boolean }) {
  const bank = detail.vendor.bank
  // Ledger is stored per line, not per bill — so it is listed per line, named
  // by the line it codes rather than rolled into one value that would be a
  // lie the moment two lines differ.
  const ledgerLines = detail.line_items.filter(li => li.ledger_name)
  const nothingToShow =
    !detail.category && !detail.department && !detail.cost_center_name &&
    !bank && ledgerLines.length === 0 && !detail.vendor_sap_code
  if (nothingToShow) return null

  const bankBadge = bank?.verified
    ? <CodingBadge tone="ok"><Check size={11} strokeWidth={3} /> Verified</CodingBadge>
    // Not a quiet omission: this account has never been penny-dropped, and
    // the approver is the last person who can catch that before the money moves.
    : <CodingBadge tone="warn"><AlertTriangle size={11} /> Unverified</CodingBadge>

  return (
    <div style={{ gridColumn: '1 / -1', marginTop: 'var(--sp-4)' }}>
      <table className="bm__coding" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <CodingSection title="Accounting">
          <CodingRow label="Category" value={detail.category} fix="category" />
          <CodingRow label="Department" value={detail.department} fix="department" />
          {detail.cost_center_name && <CodingRow label="Cost Center" value={detail.cost_center_name} fix="cost_centre" />}
          {sapEnabled && detail.vendor_sap_code && (
            <CodingRow label="Vendor SAP Code" value={detail.vendor_sap_code} mono />
          )}
        </CodingSection>

        {ledgerLines.length > 0 && (
          <CodingSection title="Ledger Mapping">
            {ledgerLines.map(li => (
              <CodingRow
                key={li.line_number}
                label={`Line ${li.line_number}`}
                value={li.ledger_name}
                sub={li.product_name || undefined}
                badge={li.ledger_code
                  ? <span style={{ fontFamily: 'monospace', fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)' }}>{li.ledger_code}</span>
                  : undefined}
              />
            ))}
          </CodingSection>
        )}

        {bank && (
          <CodingSection title="Payee Bank Account" aside={bankBadge}>
            <CodingRow
              label="Account"
              value={bank.account_number}
              mono
              sub={bank.name_at_bank ? `Name at bank: ${bank.name_at_bank}` : undefined}
              badge={bankBadge}
            />
            {bank.ifsc && <CodingRow label="IFSC" value={bank.ifsc} mono />}
            {bank.bank_name && <CodingRow label="Bank" value={bank.bank_name} />}
          </CodingSection>
        )}
      </table>
    </div>
  )
}

// ── Nova reconciliation panel (below the approval chain) ──────────────────────
const _rupee = (v: number | null | undefined) =>
  v == null ? '—' : `₹${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const _monthLabel = (m?: string) => {
  const x = /^(\d{4})-(\d{2})$/.exec(m ?? '')
  return x ? new Date(+x[1], +x[2] - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' }) : (m ?? '')
}

// Which Nova module an invoice line was matched to. That mapping is the entire basis of
// the row's comparison, so it reads as a labelled chip instead of grey micro-text that gets
// lost under the description. Shared by the panel table and the drill-down popup.
function NovaModuleChip({ module }: { module: string }) {
  return (
    <div style={{ paddingLeft: 14, marginTop: 3 }}>
      <span title={`Matched to Nova module \u201c${module}\u201d`}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600,
          color: '#0f766e', background: '#f0fdfa', border: '1px solid #99f6e4',
          borderRadius: 4, padding: '1px 6px', lineHeight: 1.5 }}>
        <span style={{ color: '#14b8a6', fontWeight: 700 }}>&rarr;</span> {module}
      </span>
    </div>
  )
}

function ReconciliationPanel({ invoiceId, actorRole, actorName, accepted, onActed }: {
  invoiceId: string; actorRole?: string; actorName?: string; accepted?: boolean; onActed?: () => void
}) {
  const [data, setData] = useState<BillReconciliation | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  const [showMismatchPopup, setShowMismatchPopup] = useState(false)
  const [showApproveConfirm, setShowApproveConfirm] = useState(false)
  const [reconComment, setReconComment] = useState('')
  const [acting, setActing] = useState(false)
  const [actionMsg, setActionMsg] = useState('')
  const [actionErr, setActionErr] = useState('')

  // Off by default: the per-cost-centre view is the one cost allocation depends on.
  // Turning this on asks "does this bill add up if every brand is treated as one pool?",
  // which is how some vendors actually bill an entity (§4.4).
  const [combined, setCombined] = useState(false)

  const load = (refresh = false, useCombined = combined) => {
    setLoading(true); setErr('')
    fetchBillReconciliation(invoiceId, refresh, useCombined)
      .then(setData)
      .catch(e => setErr(e?.message || 'Failed to reconcile'))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load(false, combined); /* eslint-disable-next-line */ }, [invoiceId, combined])

  const wrap: React.CSSProperties = { margin: '14px 0 4px', border: '1px solid #e5e7eb', borderRadius: 10, overflow: 'hidden' }
  const head: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', fontWeight: 700, fontSize: 13 }
  const btn: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', fontSize: 12, fontWeight: 600, borderRadius: 6, border: '1px solid #cbd5e1', background: '#fff', color: '#334155', cursor: 'pointer' }

  if (loading && !data) return (
    <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#f8fafc', color: '#64748b' }}>
      <RefreshCw size={14} /> Checking against Nova… this can take a moment</div></div>
  )
  if (err) return (
    <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#f8fafc', color: '#64748b', justifyContent: 'space-between' }}>
      <span>Reconciliation couldn’t complete</span>
      <button style={btn} onClick={() => load(true)}>Retry</button></div></div>
  )
  if (!data) return null

  // Most vendors are not Nova vendors, so "no reconciliation applies" is the
  // ordinary case, not a finding. Announcing it on every one of those bills is a
  // panel that only ever says nothing happened.
  if (data.status === 'NOT_APPLICABLE') return null
  // Queued but not yet swept — nothing is wrong, so this reads as neutral, not a warning.
  if (data.status === 'PENDING')
    return <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#f8fafc', color: '#64748b' }}>
      Nova Reconciliation — {data.reason || 'queued, not reconciled yet'}</div></div>
  // Two sibling cost centres certify this vendor identically and the bill names neither,
  // so reconciling would mean guessing whose usage to check against. Says what to do
  // rather than only that it failed.
  if (data.status === 'NEEDS_COST_CENTRE')
    return <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#fffbeb', color: '#b45309', display: 'block' }}>
      <div>⚠ Nova Reconciliation — needs a cost centre</div>
      <div style={{ fontWeight: 500, fontSize: 12, marginTop: 4 }}>
        {data.reason} · Set this bill's cost centre and it reconciles on the next sweep.
      </div></div></div>
  if (data.status === 'NEEDS_MONTH' || data.status === 'BLOCKED')
    return <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#fffbeb', color: '#b45309' }}>⚠ Nova Reconciliation — {data.reason}</div></div>
  if (data.status === 'NOVA_UNAVAILABLE')
    return <div className="bm__recon" style={wrap}><div style={{ ...head, background: '#f8fafc', color: '#64748b', justifyContent: 'space-between' }}>
      <span>Nova check — couldn’t reach Nova</span>
      <button style={btn} onClick={() => load(true)}>Re-run</button></div></div>

  // "Green till 5%": once the overall variance is within tolerance the panel reads
  // green even if individual lines exceed 5% — those are surfaced via the ⓘ button below.
  const effFlag = data.within_tolerance ? 'GREEN' : (data.flag || 'GREEN')
  const flagColor = effFlag === 'RED' ? { bg: '#fef2f2', fg: '#b91c1c', bd: '#fecaca' }
    : effFlag === 'YELLOW' ? { bg: '#fffbeb', fg: '#b45309', bd: '#fde68a' }
    : { bg: '#f0fdf4', fg: '#15803d', bd: '#bbf7d0' }
  const dot = (f: string) => ({ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
    background: f === 'RED' ? '#dc2626' : f === 'YELLOW' ? '#d97706' : '#16a34a' })

  const mismatches = (data.modules ?? []).filter(m => m.flag === 'RED' || m.flag === 'YELLOW')
  // Part of the bill had nothing on Nova's side to compare against, so the headline %
  // is measured over a subset of the bill and has to say which subset.
  const hasUnmatched = (data.unmatched_invoice_amount ?? 0) > 0
  // Only offer the drill-down when the header is green, lines still differ, and it
  // hasn't already been accepted/overridden — once accepted it shouldn't reappear.
  const hasHiddenMismatch = !!data.within_tolerance && mismatches.length > 0 && !accepted
  const showAcceptedBadge = !!data.within_tolerance && mismatches.length > 0 && !!accepted

  const decide = async (decision: 'approve' | 'reject') => {
    if (!actorRole || !actorName) { setActionErr('You must be signed in to act.'); return }
    setActing(true); setActionErr(''); setActionMsg('')
    try {
      // Record the reviewer's reason and the agent's note together, so the agent's
      // explanation is captured in the activity log alongside the decision.
      const parts: string[] = []
      if (reconComment.trim()) parts.push(reconComment.trim())
      if (data.agent_note) parts.push(`Agent note: ${data.agent_note}`)
      const comment = parts.join('\n\n') || undefined
      const res = await reconcileAction(invoiceId, { decision, actor_role: actorRole, actor_name: actorName, comment })
      setActionMsg(decision === 'approve'
        ? 'Discrepancies accepted — recorded in the activity log.'
        : res.advanced ? 'Rejected — carried forward to the next approver.' : 'Rejection recorded in the activity log.')
      setReconComment('')
      setShowApproveConfirm(false)
      onActed?.()  // refresh bill detail so the stepper / activity feed reflect the change
      setTimeout(() => setShowMismatchPopup(false), 1200)
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : 'Action failed')
    } finally { setActing(false) }
  }

  return (
    <div className="bm__recon" style={wrap}>
      <div style={{ ...head, background: flagColor.bg, color: flagColor.fg, borderBottom: collapsed ? 'none' : `1px solid ${flagColor.bd}`, justifyContent: 'space-between', cursor: 'pointer' }}
           onClick={() => setCollapsed(c => !c)} title={collapsed ? 'Expand' : 'Collapse'}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ChevronDown size={14} style={{ transform: collapsed ? 'rotate(-90deg)' : 'none', transition: 'transform .15s', flexShrink: 0 }} />
          {effFlag === 'GREEN' ? '✓' : '✗'} Nova Reconciliation — {data.vendor} · {_monthLabel(data.month)}
          {hasHiddenMismatch && (
            <button
              type="button"
              onClick={e => { e.stopPropagation(); setShowMismatchPopup(true) }}
              title={`Within tolerance, but ${mismatches.length} line${mismatches.length > 1 ? 's' : ''} differ — click for details`}
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20,
                borderRadius: '50%', border: '1px solid #fcd34d', background: '#fef3c7', color: '#b45309',
                cursor: 'pointer', flexShrink: 0, padding: 0 }}>
              <Info size={13} />
            </button>
          )}
          {showAcceptedBadge && (
            <span title="Line-level flags were reviewed and accepted"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600,
                color: '#15803d', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 999, padding: '1px 8px' }}>
              <Check size={11} /> Flags accepted
            </span>
          )}
          {collapsed && (
            <span style={{ fontWeight: 600, fontSize: 12 }}>
              · {data.within_tolerance ? 'within' : 'off by'} {data.variance_pct != null ? `${data.variance_pct}%` : '—'}
            </span>
          )}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }} onClick={e => e.stopPropagation()}>
          <button
            style={{ ...btn, opacity: loading ? 0.6 : 1,
              ...(combined ? { background: '#eef2ff', borderColor: '#a5b4fc', color: '#4338ca' } : {}) }}
            onClick={() => setCombined(c => !c)}
            disabled={loading}
            title={combined
              ? 'Showing every cost centre summed — switch back to this bill\'s own cost centre'
              : 'Reconcile against every brand/cost centre of this entity, summed'}>
            <Layers size={12} /> {combined ? 'Combined: all brands' : 'Compare all brands combined'}
          </button>
          <button style={{ ...btn, opacity: loading ? 0.6 : 1 }} onClick={() => load(true)} disabled={loading} title="Re-pull Nova & recompute">
            <RefreshCw size={12} /> Re-run
          </button>
        </span>
      </div>
      {!collapsed && (
      <div style={{ padding: '10px 14px', fontSize: 13 }}>
        {data.combined && (
          <div style={{ marginBottom: 10, padding: '7px 10px', borderRadius: 8, background: '#eef2ff',
                        border: '1px solid #c7d2fe', color: '#3730a3', fontSize: 12 }}>
            Summed across {data.combined_over?.length ?? 0} cost centre
            {(data.combined_over?.length ?? 0) === 1 ? '' : 's'}
            {data.combined_over?.length ? `: ${data.combined_over.join(' + ')}` : ''}
            {data.combined_missing?.length ? (
              // A partial sum is worse than no sum if it looks complete.
              <span style={{ color: '#b45309' }}>
                {' '}· excluded (no usable Nova key): {data.combined_missing.join(', ')}
              </span>
            ) : null}
          </div>
        )}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'baseline', marginBottom: hasUnmatched ? 4 : 10 }}>
          <span>Invoice <b>{_rupee(data.total_invoice)}</b></span>
          {/* When part of the bill has no Nova counterpart, the figures being compared are
              not the bill's totals — say so inline rather than letting the % look like it
              covered everything. */}
          {hasUnmatched && (
            <span style={{ color: '#64748b' }}>compared <b>{_rupee(data.comparable_invoice)}</b></span>
          )}
          <span style={{ color: '#94a3b8' }}>vs Nova <b>{_rupee(data.total_nova)}</b></span>
          <span style={{ fontWeight: 700, color: flagColor.fg }}>
            {data.within_tolerance ? '✓ Within' : '✗ Off by'} {data.variance_pct != null ? `${data.variance_pct}%` : '—'}
            {data.variance_amount != null && ` (${_rupee(Math.abs(data.variance_amount))} ${(data.variance_amount ?? 0) >= 0 ? 'higher' : 'lower'})`}
          </span>
          <span style={{ marginLeft: 'auto', fontSize: 11, color: '#94a3b8' }}>tolerance {data.tolerance_pct}%</span>
        </div>
        {hasUnmatched && (
          <div style={{ marginBottom: 10, fontSize: 11.5, color: '#b45309' }}>
            {_rupee(data.unmatched_invoice_amount)} across {data.unmatched_count} line
            {(data.unmatched_count ?? 0) === 1 ? '' : 's'} has no Nova figure to compare against —
            excluded from the % above rather than counted as a difference.
          </div>
        )}
        <div style={{ overflowX: 'auto', overflowY: 'auto', maxHeight: 320 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ textAlign: 'left', color: '#64748b', borderBottom: '1px solid #e5e7eb' }}>
                <th style={{ padding: '6px 8px' }}>Line / Service</th>
                <th style={{ padding: '6px 8px', textAlign: 'right' }}>Invoice</th>
                <th style={{ padding: '6px 8px', textAlign: 'right' }}>Nova</th>
                <th style={{ padding: '6px 8px', textAlign: 'right' }}>Difference</th>
                <th style={{ padding: '6px 8px' }}>Note</th>
              </tr>
            </thead>
            <tbody>
              {(data.modules ?? []).map((m, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #f1f5f9' }}>
                  <td style={{ padding: '6px 8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={dot(m.flag)} />
                      <span>{m.invoice_description || m.nova_module || '—'}</span>
                      {m.match_source === 'ai' && <span title="Matched to a Nova service by AI" style={{ fontSize: 9, fontWeight: 700, color: '#7c3aed', background: '#f5f3ff', border: '1px solid #ddd6fe', borderRadius: 4, padding: '0 4px' }}>AI</span>}
                    </div>
                    {m.nova_module && m.invoice_description && <NovaModuleChip module={m.nova_module} />}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{_rupee(m.invoice_amount)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#64748b' }}>{_rupee(m.nova_cost)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: m.flag === 'RED' ? 700 : 400, color: m.flag === 'RED' ? '#b91c1c' : '#334155' }}>
                    {m.variance_amount == null ? '—' : _rupee(m.variance_amount)}{m.variance_pct != null && m.flag === 'RED' ? ` (${m.variance_pct}%)` : ''}
                  </td>
                  <td style={{ padding: '6px 8px', color: m.flag === 'RED' ? '#b91c1c' : m.flag === 'YELLOW' ? '#b45309' : '#64748b' }}>{m.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {(data.snapshot_fetched_at || data.computed_at) && (
          <div style={{ fontSize: 10.5, color: '#94a3b8', marginTop: 8 }}>
            {data.snapshot_fetched_at && <>Nova data as of {new Date(data.snapshot_fetched_at).toLocaleString('en-IN')}</>}
            {data.computed_at && <> · saved result from {new Date(data.computed_at).toLocaleString('en-IN')} — Re-run to refresh</>}
          </div>
        )}
      </div>
      )}

      {showMismatchPopup && (
        <div onClick={() => setShowMismatchPopup(false)}
             style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000,
               display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()}
               style={{ background: '#fff', borderRadius: 12, width: 'min(680px, 100%)', maxHeight: '80vh',
                 overflow: 'hidden', boxShadow: '0 20px 50px rgba(0,0,0,0.25)', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10,
              padding: '14px 18px', borderBottom: '1px solid #f1f5f9' }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14, color: '#0f172a' }}>
                  {mismatches.length} line{mismatches.length > 1 ? 's' : ''} {hasUnmatched ? 'to check' : 'differ from Nova'}
                </div>
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                  Overall is within tolerance ({data.variance_pct}% ≤ {data.tolerance_pct}%)
                  {hasUnmatched
                    ? ' over the lines Nova has a figure for. These still need a look — a line with no Nova figure was not compared at all, so it is neither agreement nor a difference.'
                    : ', but these lines are off.'}
                </div>
              </div>
              <button onClick={() => setShowMismatchPopup(false)} title="Close"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 28, height: 28,
                  borderRadius: 6, border: '1px solid #e5e7eb', background: '#fff', color: '#64748b', cursor: 'pointer', flexShrink: 0 }}>
                <X size={16} />
              </button>
            </div>
            <div style={{ overflow: 'auto', padding: '6px 18px 16px' }}>
              {data.agent_note && (
                <div style={{ display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 6, margin: '8px 0 12px',
                  padding: '8px 12px', background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 8, fontSize: 12 }}>
                  <span style={{ fontWeight: 700, color: '#92400e', whiteSpace: 'nowrap' }}>Agent Note</span>
                  <span style={{ color: '#78350f', fontStyle: 'italic', flex: 1 }}>{data.agent_note}</span>
                </div>
              )}
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: 'left', color: '#64748b', borderBottom: '1px solid #e5e7eb' }}>
                    <th style={{ padding: '8px 8px' }}>Line / Service</th>
                    <th style={{ padding: '8px 8px', textAlign: 'right' }}>Invoice</th>
                    <th style={{ padding: '8px 8px', textAlign: 'right' }}>Nova</th>
                    <th style={{ padding: '8px 8px', textAlign: 'right' }}>Difference</th>
                    <th style={{ padding: '8px 8px' }}>Note</th>
                  </tr>
                </thead>
                <tbody>
                  {mismatches.map((m, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid #f1f5f9' }}>
                      <td style={{ padding: '8px 8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={dot(m.flag)} />
                          <span>{m.invoice_description || m.nova_module || '—'}</span>
                        </div>
                        {m.nova_module && m.invoice_description && <NovaModuleChip module={m.nova_module} />}
                      </td>
                      <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{_rupee(m.invoice_amount)}</td>
                      <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: '#64748b' }}>{_rupee(m.nova_cost)}</td>
                      <td style={{ padding: '8px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 700, color: m.flag === 'YELLOW' ? '#b45309' : '#b91c1c' }}>
                        {m.variance_amount == null ? '—' : _rupee(m.variance_amount)}{m.variance_pct != null ? ` (${m.variance_pct}%)` : ''}
                      </td>
                      <td style={{ padding: '8px 8px', color: m.flag === 'YELLOW' ? '#b45309' : '#b91c1c' }}>{m.note}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ borderTop: '1px solid #f1f5f9', padding: '12px 18px 16px', background: '#fafafa' }}>
              {actionErr && <div style={{ fontSize: 12, color: '#b91c1c', marginBottom: 8 }}>{actionErr}</div>}
              {actionMsg && <div style={{ fontSize: 12, color: '#15803d', marginBottom: 8 }}>{actionMsg}</div>}
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
                <button
                  type="button"
                  onClick={() => decide('reject')}
                  disabled={acting}
                  title="Reject these discrepancies — carry the bill forward to the next approver"
                  style={{ padding: '8px 16px', fontSize: 13, fontWeight: 600, borderRadius: 8, cursor: acting ? 'default' : 'pointer',
                    border: '1px solid #fecaca', background: '#fff', color: '#b91c1c', opacity: acting ? 0.6 : 1 }}>
                  Reject
                </button>
                <button
                  type="button"
                  onClick={() => { setActionErr(''); setActionMsg(''); setShowApproveConfirm(true) }}
                  disabled={acting}
                  title="Accept these discrepancies — record in the activity log"
                  style={{ padding: '8px 16px', fontSize: 13, fontWeight: 700, borderRadius: 8, cursor: acting ? 'default' : 'pointer',
                    border: '1px solid #16a34a', background: '#16a34a', color: '#fff', opacity: acting ? 0.6 : 1 }}>
                  Approve
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showApproveConfirm && (
        <div onClick={() => !acting && setShowApproveConfirm(false)}
             style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.5)', zIndex: 1100,
               display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()}
               style={{ background: '#fff', borderRadius: 12, width: 'min(620px, 100%)', maxHeight: '85vh',
                 overflow: 'auto', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
            <div style={{ padding: '16px 20px 0' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, fontSize: 15, color: '#0f172a' }}>
                <Shield size={18} style={{ color: '#e11d48', flexShrink: 0 }} />
                Override this reconciliation
              </div>
              <div style={{ fontSize: 12.5, color: '#64748b', marginTop: 4, lineHeight: 1.5 }}>
                Clears <b style={{ color: '#334155' }}>every open flag at once</b> — for when you know Nova is wrong for this vendor/month. <b style={{ color: '#334155' }}>Logged to the audit trail.</b> The agent attaches the facts; you give the reason.
              </div>
            </div>

            <div style={{ margin: '14px 20px', padding: '12px 14px', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <span style={{ fontSize: 9, fontWeight: 700, color: '#7c3aed', background: '#f5f3ff', border: '1px solid #ddd6fe', borderRadius: 4, padding: '1px 5px' }}>AI</span>
                <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: '#7c3aed' }}>Context the agent attaches for the record</span>
              </div>
              <div style={{ fontSize: 12.5, color: '#475569', lineHeight: 1.5, marginBottom: 8 }}>
                Overriding <b style={{ color: '#334155' }}>{mismatches.length} open flag{mismatches.length > 1 ? 's' : ''}</b> on {data.vendor} · {_monthLabel(data.month)} — the aggregate was within {data.variance_pct}%, but these line-level flags were unresolved:
              </div>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: '#475569', lineHeight: 1.6 }}>
                {mismatches.map((m, i) => {
                  const name = m.invoice_description || m.nova_module || '—'
                  const diff = m.variance_amount ?? 0
                  let fact: React.ReactNode
                  if (m.note === 'Needs mapping')
                    fact = <>Unmapped line — {name} ({_rupee(m.invoice_amount)}, no Nova counterpart)</>
                  else if (m.nova_cost != null)
                    fact = <><b style={{ color: '#b91c1c' }}>{_rupee(Math.abs(diff))}</b> {diff >= 0 ? 'over-bill' : 'under-bill'} — {name} ({_rupee(m.invoice_amount)} vs Nova {_rupee(m.nova_cost)}{m.variance_pct != null ? `, ${m.variance_pct}%` : ''})</>
                  else
                    fact = <>{m.note} — {name} ({_rupee(m.invoice_amount)})</>
                  return <li key={i}>{fact}</li>
                })}
              </ul>
            </div>

            <div style={{ padding: '0 20px' }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: '#334155' }}>Your reason for the override (required)</label>
              <textarea
                value={reconComment}
                onChange={e => setReconComment(e.target.value)}
                disabled={acting}
                rows={3}
                placeholder={'Why is the invoice right and Nova wrong? e.g. "Nova under-counts eKYC billableOps for avinashcapital_prod_v3; confirmed the invoice is correct with vendor ops on 14 Jul, Nova ledger fix pending."'}
                style={{ width: '100%', boxSizing: 'border-box', marginTop: 6, padding: '8px 10px', fontSize: 12.5,
                  borderRadius: 8, border: '1px solid #e5e7eb', resize: 'vertical', outline: 'none' }} />
              {actionErr && <div style={{ fontSize: 12, color: '#b91c1c', marginTop: 6 }}>{actionErr}</div>}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '14px 20px 18px', marginTop: 8 }}>
              <button type="button" onClick={() => decide('approve')} disabled={acting || !reconComment.trim()}
                style={{ padding: '9px 18px', fontSize: 13, fontWeight: 700, borderRadius: 8,
                  cursor: (acting || !reconComment.trim()) ? 'default' : 'pointer',
                  border: '1px solid #4f46e5', background: '#4f46e5', color: '#fff', opacity: (acting || !reconComment.trim()) ? 0.5 : 1 }}>
                {acting ? 'Overriding…' : 'Override & clear all'}
              </button>
              <button type="button" onClick={() => setShowApproveConfirm(false)} disabled={acting}
                style={{ padding: '9px 18px', fontSize: 13, fontWeight: 600, borderRadius: 8, cursor: 'pointer',
                  border: '1px solid #cbd5e1', background: '#fff', color: '#334155' }}>
                Cancel
              </button>
              <span style={{ marginLeft: 'auto', fontSize: 11, color: '#94a3b8' }}>Saved to audit as an override, not "reconciled".</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Full-screen modal ────────────────────────────────────────────────────────
// Exported for components/records/BillCard, which opens it by id over any page.
export function BillModal({ bill, origin, cachedDetail, labels = DEFAULT_PRIORITY_LABELS, onClose, onRefresh, onPay, refreshKey = 0 }: {
  bill: Bill; origin: string; cachedDetail: BillDetail | null
  labels?: PriorityLabels; onClose: () => void; onRefresh: () => void; onPay: (bill: Bill) => void
  /** Bump to re-read the bill after an outside write (an accepted correction). */
  refreshKey?: number
}) {
  const { user } = useAuth()
  const [detail, setDetail]       = useState<BillDetail | null>(cachedDetail)
  const [detailErr, setDetailErr] = useState('')
  // Licensed services, resolved once per session by EntitlementsProvider rather
  // than re-fetched by each page that needs a flag.
  const sapEnabled = useService('sap_matching')
  const novaOn = useService('nova_billing')
  const payEnabled = useService('disbursement')
  const cryptoOn = useService('crypto_payouts')
  // The tenant's books currency. A bill is foreign only when its currency differs
  // from this (INR for Indian tenants; USD for a tenant like Lumen Labs).
  // Which country's rules apply (auth/jurisdiction.ts): books currency, tax
  // label, and whether GST / TDS / IMPS exist for this tenant at all.
  const jur = useJurisdiction()
  const baseCur = jur.base_currency
  const gstOn = jur.packs.gst, tdsOn = jur.packs.tds_india, impsOn = jur.packs.imps
  const usdcRail = jur.rails.includes('USDC') && cryptoOn
  const [numPages, setNumPages]   = useState(0)
  const [pageNum, setPageNum]     = useState(1)
  const [zoom, setZoom]           = useState(0)
  const [rotation, setRotation]   = useState(0)
  const [pdfLoadErr, setPdfLoadErr] = useState(false)
  const pdfRef = useRef<HTMLDivElement>(null)
  const [pdfW, setPdfW]           = useState(0)
  const pdfPaneRef = useRef<HTMLDivElement>(null)
  const [pdfFull, setPdfFull]     = useState(false)
  // Phones keep the document off the card: a header button opens it full
  // screen, flying out of that button instead of out of the (hidden) pane.
  const isPhone = useMediaQuery(PHONE_QUERY)
  const pdfBtnRef = useRef<HTMLButtonElement>(null)
  // The parsed document from the side pane, handed to the full-screen view so it
  // draws straight away instead of reading the file a second time.
  const [pdfProxy, setPdfProxy]   = useState<PDFDocumentProxy | null>(null)

  // action panel
  const [comment, setComment]     = useState('')
  const [actionErr, setActionErr] = useState('')
  const [actioning, setActioning] = useState(false)
  const [showSendBackPicker, setShowSendBackPicker] = useState(false)
  const [sendBackTarget, setSendBackTarget] = useState<string>('')

  // edit mode
  const [editing, setEditing]     = useState(false)
  const [editData, setEditData]   = useState<Record<string, string>>({})
  // `_uid` is a client-only React key. line_number cannot serve as one: every
  // line added here carries 0 until the server assigns a real number, so two new
  // rows would share a key and React would reuse the wrong input.
  type EditLine = { _uid: string; line_number: number; product_name: string; hsn_code: string; sap_item_code: string; quantity: string; unit: string; unit_price: string; tax_rate_percent: string; line_total: string; rate_is_percent: boolean }
  const [editLineItems, setEditLineItems] = useState<EditLine[]>([])
  const [saving, setSaving]       = useState(false)
  // PAN-duplicate merge prompt (shown after a GSTIN correction reveals another
  // vendor sharing the same PAN)
  const [mergeCandidate, setMergeCandidate] = useState<PanDuplicate | null>(null)
  const [merging, setMerging]     = useState(false)
  const [mergeErr, setMergeErr]   = useState('')
  const [showChain, setShowChain] = useState(false)
  const [bmCategories, setBmCategories]         = useState<string[]>(getAllCategories)
  const [bmAddingCategory, setBmAddingCategory] = useState(false)
  useEffect(() => { syncCategoriesFromApi(user?.company_id).then(setBmCategories) }, [user?.company_id])
  const [bmNewCategory, setBmNewCategory]       = useState('')
  const [bmDepartments, setBmDepartments]       = useState<string[]>([])

  useEffect(() => {
    if (user?.company_id) fetchDepartments(user.company_id).then(setBmDepartments).catch(() => {})
  }, [user?.company_id])
  const [prioritySaving, setPrioritySaving]     = useState(false)

  // FX payment recording
  const [fxPayments, setFxPayments]             = useState<FxPayment[]>([])
  const [fxPayLoading, setFxPayLoading]         = useState(false)
  const [showFxPayForm, setShowFxPayForm]       = useState(false)
  const [fxPayFormData, setFxPayFormData]       = useState({ actual_inr_paid: '', bank_name: '', reference_no: '', notes: '' })
  const [fxPaySaving, setFxPaySaving]           = useState(false)
  const [fxPayError, setFxPayError]             = useState('')

  // FX agent note
  const [agentNote, setAgentNote]               = useState<FxAgentNote | null>(null)

  // Vendor disbursement (IMPS demo) — read-only history; the actual pay flow
  // lives on the dedicated payment page (PaymentPage.tsx), opened via onPay.
  const [disbursements, setDisbursements] = useState<Disbursement[]>([])
  useEffect(() => {
    fetchDisbursements(bill.invoice_id, user?.company_id).then(setDisbursements).catch(() => {})
  }, [bill.invoice_id])

  // attachments
  const [uploading, setUploading]   = useState(false)
  const [previewAttach, setPreviewAttach] = useState<{ url: string; blobUrl?: string; blobData?: ArrayBuffer; name: string; rect: DOMRect; attachmentId?: string } | null>(null)
  const [attachEditHistory, setAttachEditHistory] = useState<{ id: string; edited_by: string | null; edited_at: string | null; diff: { sheet: string; row: number; col: number; header: string; oldValue: string; newValue: string }[] }[]>([])
  const [showAttachHistory, setShowAttachHistory] = useState(false)
  const [attachHistoryLoading, setAttachHistoryLoading] = useState(false)
  const [expandedDiff, setExpandedDiff] = useState<number | null>(null)
  const attachInputRef = useRef<HTMLInputElement>(null)

  const handleAttachUpload = async (files: FileList | null) => {
    if (!files || !files.length) return
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const fd = new FormData()
        fd.append('file', file)
        fd.append('uploaded_by', user?.name ?? '')
        fd.append('company_id', user?.company_id ?? '')
        await fetch(`${API_BASE}/api/bills/${bill.invoice_id}/attachments`, { method: 'POST', body: fd })
      }
      reload()
    } finally {
      setUploading(false)
      if (attachInputRef.current) attachInputRef.current.value = ''
    }
  }

  const handleAttachDelete = async (attachmentId: string) => {
    await fetch(`${API_BASE}/api/bills/${bill.invoice_id}/attachments/${attachmentId}`, { method: 'DELETE' })
    reload()
  }

  useEffect(() => {
    setDetailErr('')
    // Always fetch fresh so any external change (email-link approval/revision) is reflected immediately.
    // cachedDetail is shown instantly as a placeholder while the request is in flight.
    invalidateDetailCache(bill.invoice_id)
    fetchBillDetail(bill.invoice_id)
      .then(setDetail)
      .catch(e => setDetailErr(e instanceof Error ? e.message : 'Failed to load'))
  }, [bill.invoice_id])

  // Load FX payment history whenever bill changes (non-INR bills only)
  useEffect(() => {
    const cur = bill.currency_code || 'INR'
    if (cur === 'INR') return
    setFxPayLoading(true)
    fetchFxPayments(bill.invoice_id, user?.company_id)
      .then(setFxPayments)
      .catch(() => {})
      .finally(() => setFxPayLoading(false))
    // Also fetch agent note
    fetchFxAgentNote(bill.invoice_id).then(setAgentNote).catch(() => {})
  }, [bill.invoice_id, bill.currency_code, user?.company_id])


  const overlayRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // A record card opened on top of this one (e.g. its vendor) takes the
      // Escape — closing both at once would lose the bill underneath.
      const layer = document.querySelector('.rc__layer')
      if (layer && !layer.contains(overlayRef.current)) return
      onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  useEffect(() => {
    const el = pdfRef.current; if (!el) return
    const ro = new ResizeObserver(() => {
      const w = el.clientWidth
      setPdfW(prev => (prev === w ? prev : w))   // only update when width actually changes
    })
    ro.observe(el); setPdfW(el.clientWidth)
    return () => ro.disconnect()
  }, [])

  const reload = () => {
    invalidateDetailCache(bill.invoice_id)
    invalidateBillsCache()
    fetchBillDetail(bill.invoice_id).then(d => { setDetail(d); onRefresh() }).catch(() => {})
  }

  // A write from outside the modal (a correction accepted on this card) — read
  // the bill again so its fields show the new values.
  useEffect(() => { if (refreshKey) reload() }, [refreshKey])

  const doAction = async (action: 'submit' | 'approve' | 'send_back', targetStatus?: string) => {
    if (!user) return
    if (action === 'send_back' && !comment.trim()) { setActionErr('Please add a note explaining what needs to be corrected.'); return }
    setActioning(true); setActionErr('')
    try {
      await billAction(bill.invoice_id, { action, actor_role: user.role, actor_name: user.name, comment: comment || undefined, target_status: targetStatus })
      setComment('')
      setShowSendBackPicker(false)
      setSendBackTarget('')
      reload()
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : 'Action failed')
    } finally { setActioning(false) }
  }

  const doSaveEdit = async () => {
    if (!detail) return
    setSaving(true)
    // Only send fields that actually changed vs the loaded detail
    const orig = {
      vendor_name:     detail.vendor.name     ?? '',
      vendor_gstn:     detail.vendor.gstn     ?? '',
      invoice_date:    detail.invoice_date?.slice(0, 10) ?? '',
      due_date:        detail.due_date?.slice(0, 10)     ?? '',
      subtotal:        String(detail.subtotal        ?? ''),
      tax_amount:      String(detail.tax_amount      ?? ''),
      total_amount:    String(detail.total_amount    ?? ''),
      discount_amount: String(detail.discount_amount ?? ''),
      category:        detail.category ?? '',
      department:      detail.department ?? '',
    }
    const diff: Record<string, unknown> = {}
    if (editData.vendor_name     !== orig.vendor_name)     diff.vendor_name     = editData.vendor_name     || undefined
    if (editData.vendor_gstn     !== orig.vendor_gstn)     diff.vendor_gstn     = editData.vendor_gstn     || undefined
    if (editData.invoice_date    !== orig.invoice_date)    diff.invoice_date    = editData.invoice_date    || undefined
    if (editData.due_date        !== orig.due_date)        diff.due_date        = editData.due_date        || undefined
    if (editData.subtotal        !== orig.subtotal)        diff.subtotal        = editData.subtotal        ? parseFloat(editData.subtotal)        : undefined
    if (editData.tax_amount      !== orig.tax_amount)      diff.tax_amount      = editData.tax_amount      ? parseFloat(editData.tax_amount)      : undefined
    if (editData.total_amount    !== orig.total_amount)    diff.total_amount    = editData.total_amount    ? parseFloat(editData.total_amount)    : undefined
    if (editData.discount_amount !== orig.discount_amount) diff.discount_amount = editData.discount_amount ? parseFloat(editData.discount_amount) : undefined
    if (editData.category        !== orig.category)        diff.category        = editData.category ?? ''
    if (editData.department      !== orig.department)      diff.department      = editData.department ?? ''
    // Sent unconditionally, including as an empty array. The server treats this
    // field as the authoritative full set, so `undefined` would mean "leave the
    // lines alone" — and deleting the last remaining line would silently not
    // persist. New rows carry line_number 0 and the server inserts them.
    const lineItems = editLineItems.map(li => ({
          line_number:       li.line_number,
          product_name:      li.product_name   || undefined,
          hsn_code:          li.hsn_code        || undefined,
          quantity:          li.quantity        !== '' ? parseFloat(li.quantity)        : null,
          unit:              li.unit             || undefined,
          unit_price:        li.unit_price       !== '' ? parseFloat(li.unit_price)       : null,
          tax_rate_percent:  li.tax_rate_percent !== '' ? parseFloat(li.tax_rate_percent) : null,
          line_total:        li.line_total       !== '' ? parseFloat(li.line_total)       : null,
          rate_is_percent:   li.rate_is_percent,
        }))
    try {
      await updateBill(bill.invoice_id, { ...diff, line_items: lineItems, actor_name: user?.name, actor_role: user?.role })
      setEditing(false); reload()
      // If the GSTIN was corrected, another vendor may share the now-correct PAN
      // (e.g. a duplicate created earlier from an OCR-scrambled GSTIN). Offer to merge.
      if ('vendor_gstn' in diff && detail.supplier_id) {
        try {
          const res = await fetchVendorPanDuplicates(detail.supplier_id, user?.company_id)
          if (res.duplicates.length > 0) { setMergeErr(''); setMergeCandidate(res.duplicates[0]) }
        } catch { /* non-fatal — save already succeeded */ }
      }
    } catch (e: unknown) {
      setActionErr(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  const doMerge = async () => {
    if (!detail?.supplier_id || !mergeCandidate) return
    setMerging(true); setMergeErr('')
    try {
      await mergeVendors(detail.supplier_id, mergeCandidate.supplier_id, { actor_name: user?.name, actor_role: user?.role })
      invalidateVendorsCache(user?.company_id)
      invalidateBillsCache()
      setMergeCandidate(null)
      reload()
    } catch (e: unknown) {
      setMergeErr(e instanceof Error ? e.message : 'Merge failed')
    } finally { setMerging(false) }
  }

  // User chose to keep the duplicate: flag this vendor so the Vendors list shows
  // a green "Deduplicate" shortcut to merge it later.
  const keepDuplicated = async () => {
    if (!detail?.supplier_id) { setMergeCandidate(null); return }
    setMerging(true); setMergeErr('')
    try {
      await deferVendorDedup(detail.supplier_id, true)
      invalidateVendorsCache(user?.company_id)
      setMergeCandidate(null)
    } catch (e: unknown) {
      setMergeErr(e instanceof Error ? e.message : 'Failed to save choice')
    } finally { setMerging(false) }
  }

  const startEdit = () => {
    if (!detail) return
    setEditData({
      vendor_name:     detail.vendor.name || '',
      vendor_gstn:     detail.vendor.gstn || '',
      invoice_date:    detail.invoice_date?.slice(0, 10) || '',
      due_date:        detail.due_date?.slice(0, 10) || '',
      subtotal:        String(detail.subtotal ?? ''),
      tax_amount:      String(detail.tax_amount ?? ''),
      total_amount:    String(detail.total_amount ?? ''),
      discount_amount: String(detail.discount_amount ?? ''),
      category:        detail.category || '',
      department:      detail.department || '',
    })
    setEditLineItems(detail.line_items.map(li => ({
      _uid:              `db-${li.line_number}`,
      line_number:       li.line_number,
      product_name:      li.product_name ?? '',
      hsn_code:          li.hsn_code ?? '',
      sap_item_code:     li.sap_item_code ?? '',
      quantity:          li.quantity != null ? String(li.quantity) : '',
      unit:              li.unit ?? '',
      unit_price:        li.unit_price != null ? String(li.unit_price) : '',
      tax_rate_percent:  li.tax_rate_percent != null ? String(li.tax_rate_percent) : '',
      line_total:        li.line_total != null ? String(li.line_total) : '',
      rate_is_percent:   !!li.rate_is_percent,
    })))
    setEditing(true)
  }

  // line_number 0 tells the server this row is new; it assigns the real number on
  // save. Removing a row simply drops it from the payload, which the server reads
  // as a delete — see the note on BillUpdateRequest.line_items.
  const addEditLine = () => setEditLineItems(prev => [...prev, {
    _uid: `new-${Date.now()}-${prev.length}`,
    line_number: 0, product_name: '', hsn_code: '', sap_item_code: '',
    quantity: '', unit: '', unit_price: '', tax_rate_percent: '', line_total: '',
    rate_is_percent: false,
  }])
  const removeEditLine = (uid: string) =>
    setEditLineItems(prev => prev.filter(r => r._uid !== uid))

  /** Keep a percentage line's Total in step with base x rate% (+ tax).
   *
   * Only percentage lines are recomputed. On an ordinary line the Total stays
   * whatever the invoice printed and the user typed — qty x rate does not always
   * equal the amount there (a discount, or a rate quoted per a different unit),
   * and silently overwriting it would fight the document. A percentage line has
   * no such ambiguity, and nobody should have to work out 4% of 1,00,000 by hand.
   *
   * base x rate/100 is the PRE-TAX amount; Total is stored tax-inclusive, which
   * is the same order the server applies (routers/bills.py). */
  const recalc = (r: EditLine): EditLine => {
    if (!r.rate_is_percent) return r
    const base = parseFloat(r.quantity)
    const pct  = parseFloat(r.unit_price)
    if (!Number.isFinite(base) || !Number.isFinite(pct)) return r
    const tax = parseFloat(r.tax_rate_percent)
    const net = base * pct / 100
    const total = net * (1 + (Number.isFinite(tax) ? tax : 0) / 100)
    return { ...r, line_total: String(Math.round(total * 100) / 100) }
  }

  const cur = detail?.currency_code || bill.currency_code || baseCur
  const isForeign = cur !== baseCur
  const fxSymbol = cur === 'USD' ? '$' : cur === 'EUR' ? '€' : cur === 'GBP' ? '£' : cur === 'JPY' ? '¥' : ''
  // inr_amount_current is set by pipeline after FX conversion; until then total_amount is still in foreign currency
  const pipelineConverted = isForeign && (detail?.inr_amount_current != null)
  // Amounts are stored in the tenant's books currency (converted by the FX
  // pipeline for foreign bills), so they format in that currency.
  const fmtAmt = (v: number | null | undefined) => formatTenantMoney(v, jur)
  const fmtForeign = (v: number | null | undefined) =>
    v != null ? `${fxSymbol}${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(v)} ${cur}` : null
  const fmtOriginalCur = (v: number | null | undefined) => {
    if (v == null) return '—'
    return new Intl.NumberFormat('en-IN', { style: 'currency', currency: cur, maximumFractionDigits: 2 }).format(v)
  }
  const fmtDate = (v: string | null | undefined) =>
    v ? new Date(v).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'

  // "2026-05" → "May 2026"; leave anything unexpected as-is.
  const fmtServiceMonth = (v: string | null | undefined) => {
    const m = /^(\d{4})-(\d{2})$/.exec(v ?? '')
    if (!m) return v || '—'
    return new Date(Number(m[1]), Number(m[2]) - 1, 1).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })
  }

  const status      = (detail?.status || bill.status)?.toUpperCase()
  const statusLabel = detail?.status_label ?? bill.status_label
  const paymentStatus = (detail?.payment_status ?? bill.payment_status ?? '').toLowerCase()
  const isPaid      = paymentStatus === 'paid'
  // Same rule as the list's StatusBadge: an approved bill that's been paid reads "Paid".
  const badgeKey    = status === 'APPROVED' && isPaid ? 'PAID' : status
  const badge       = STATUS_STYLE[badgeKey]
    ? { ...STATUS_STYLE[badgeKey], label: statusLabel && status?.startsWith('PENDING') ? `Pending: ${statusLabel}` : STATUS_STYLE[badgeKey].label }
    : { bg: '#f3f4f6', color: '#6b7280', label: statusLabel ? `Pending: ${statusLabel}` : status }
  // A paid bill isn't overdue, whatever its due date.
  const isOverdue = !isPaid && (detail?.due_date || bill.due_date) && new Date((detail?.due_date || bill.due_date)!) < new Date()
  const uploadId  = pdfLoadErr ? null : (detail?.upload_id || bill.upload_id)
  // Fetched here rather than inside PdfViewer so the full-screen view can reuse
  // the same blob. It goes out with the bearer header and reaches react-pdf as an
  // opaque blob URL — the token never travels in the URL. See api/authedFiles.ts.
  const pdfDoc = useAuthedObjectUrl(uploadId ? `${API_BASE}/api/docs/${uploadId}` : null)
  // Trackpad pinch on the side pane. Same width the PdfViewer renders at.
  const onPdfRendered = usePinchZoom(pdfRef, Math.max(200, pdfW + zoom), w => setZoom(w - pdfW), uploadId)
  const role      = user?.role ?? ''

  // which actions can this role take right now?
  // The person assigned to the submit step may not have role 'member' (e.g. an
  // approver configured as the submitter in a custom per-department chain). Whoever
  // originally submitted the bill can always resubmit it after a send-back, regardless
  // of role — match on their name from the chain's submit entry.
  const submitterName = (detail?.chain?.find(c => c.action === 'submit' || c.action === 'submitted')?.actor_name ?? '').trim()
  const isSubmitter   = submitterName !== '' && submitterName === (user?.name ?? '').trim()
  const canSubmit   = (status === 'DRAFT' || status === 'NEEDS_REVISION') && (role === 'member' || role === 'admin' || isSubmitter)
  const allowedRoles: string[]         = (detail?.allowed_roles     ?? bill.allowed_roles     ?? []) as string[]
  const allowedNames: string[] | null  = (detail?.allowed_usernames ?? bill.allowed_usernames ?? null) as string[] | null
  const nameOk      = !allowedNames || allowedNames.length === 0 || allowedNames.includes(user?.name ?? '') || role === 'admin'
  const canApprove  = allowedRoles.includes(role) && nameOk
  const canSendBack = canApprove
  // Editable when DRAFT/NEEDS_REVISION (member/submitter fixes) OR when sent back to an intermediate approver
  const canEdit     = (status === 'DRAFT' || status === 'NEEDS_REVISION') && (role === 'member' || role === 'admin' || isSubmitter)
                   || canApprove
  const hasAction   = canSubmit || canApprove || canSendBack

  return (
    <div className="bm__overlay" ref={overlayRef} onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      {mergeCandidate && (
        <div className="bm__merge-backdrop" onClick={e => { if (e.target === e.currentTarget && !merging) setMergeCandidate(null) }}>
          <div className="bm__merge-dialog">
            <div className="bm__merge-title">Merge duplicate vendor?</div>
            <div className="bm__merge-body">
              Another vendor with the same PAN <b>{mergeCandidate.pan}</b> already exists.
              This bill's vendor will be <b>merged into</b> it — all bills and history move to
              the existing record, and the duplicate is deactivated.
              <div className="bm__merge-target">
                <div className="bm__merge-target-name">{mergeCandidate.name || 'Unnamed vendor'}</div>
                <div className="bm__merge-target-meta">
                  <span style={{ fontFamily: 'monospace' }}>{mergeCandidate.gstn || '—'}</span>
                  <span>· {mergeCandidate.bill_count} bill{mergeCandidate.bill_count === 1 ? '' : 's'}</span>
                  {mergeCandidate.status && <span>· {mergeCandidate.status}</span>}
                </div>
              </div>
            </div>
            {mergeErr && <div className="bm__merge-err">{mergeErr}</div>}
            <div className="bm__merge-actions">
              <button className="bm__merge-keep" disabled={merging} onClick={keepDuplicated}>Keep duplicated</button>
              <button className="bm__merge-confirm" disabled={merging} onClick={doMerge}>
                {merging ? 'Deduplicating…' : 'Deduplicate vendor'}
              </button>
            </div>
          </div>
        </div>
      )}
      <div className="bm__card" style={(() => { const [dx, dy] = origin.split(' '); return { '--bm-dx': dx, '--bm-dy': dy } as React.CSSProperties })()}>

        {/* ── Left: details ── */}
        <div className="bm__left">

          {/* Header */}
          <div className="bm__hdr">
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                <span style={{ padding: '3px 12px', borderRadius: 12, background: badge.bg, color: badge.color, fontSize: 12, fontWeight: 700 }}>{badge.label}</span>
                {editing && <span style={{ fontSize: 11, color: '#2563eb', fontWeight: 600 }}>Editing…</span>}
              </div>
              <div className="bm__vendor">
                {(() => {
                  const name = detail?.vendor?.name || bill.vendor_name || 'Unknown Vendor'
                  const supplierId = detail?.supplier_id ?? bill.supplier_id
                  // Opens the vendor card over this bill — no trip to the Vendors page.
                  return supplierId ? <VendorLink supplierId={supplierId} title="Open vendor">{name}</VendorLink> : name
                })()}
              </div>
              {(detail?.vendor?.gstn || bill.vendor_gstn) && <div className="bm__gstn">{detail?.vendor?.gstn || bill.vendor_gstn}</div>}
            </div>
            <div className="bm__hdr-actions" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {isPhone && uploadId && (
                <button
                  ref={pdfBtnRef}
                  type="button"
                  className="bm__pdf-btn"
                  onClick={() => setPdfFull(true)}
                  disabled={!pdfProxy}
                  aria-label="View bill PDF"
                >
                  <FileText size={14} aria-hidden="true" /> {pdfProxy ? 'PDF' : '…'}
                </button>
              )}
              {canEdit && !editing && <button className="bm__edit-btn" onClick={startEdit} aria-label="Edit bill"><Pencil size={12} /> <span className="bm__btn-text">Edit</span></button>}
              <ExportPdfButton invoiceId={bill.invoice_id} billId={bill.id} companyId={user?.company_id} />
              <button className="bm__close" onClick={onClose}><X size={18} /></button>
            </div>
          </div>

          {/* Everything below the header scrolls together, the approval chain included. */}
          <div className="bm__scroll">
          {/* Approval stepper */}
          {detail && <ApprovalStepper chain={detail.chain} status={status} onViewChain={() => setShowChain(s => !s)} companyId={user?.company_id} department={detail.department} />}

          {/* Nova reconciliation — shown below the approval chain */}
          {novaOn && detail && <ReconciliationPanel invoiceId={detail.invoice_id} actorRole={user?.role} actorName={user?.name} accepted={detail.chain.some(c => c.action === 'recon_approve')} onActed={reload} />}

          {/* Full activity timeline — toggled by "View activity" */}
          {detail && showChain && detail.chain.length > 0 && (
            <div className="bm__history">
              {[...detail.chain].reverse().map((c, idx) => {
                const isSendBack    = c.action === 'send_back'
                const isApproved    = c.action === 'approve' || c.action === 'approved'
                const isPayment     = c.action?.startsWith('payment_')
                const isEdited      = c.action === 'edited'
                const isSubmit      = c.action === 'submit' || c.action === 'submitted'
                const isPriority    = c.action === 'priority_change'
                const isReconApprove = c.action === 'recon_approve'
                const isReconReject  = c.action === 'recon_reject'
                const payStatus     = c.action?.replace('payment_', '') as 'paid' | 'partial' | 'unpaid'
                const PAY_COLOR: Record<string,string> = { paid: '#059669', partial: '#d97706', unpaid: '#dc2626' }
                const PAY_BG: Record<string,string>    = { paid: '#d1fae5', partial: '#fef3c7', unpaid: '#fee2e2' }
                const dotColor   = isPriority ? '#7c3aed' : isEdited ? '#7c3aed' : isSendBack ? '#ef4444' : isReconReject ? '#d97706' : isReconApprove ? '#10b981' : isApproved ? '#10b981' : isPayment ? (PAY_COLOR[payStatus] ?? '#6b7280') : '#2563eb'
                const dotBg      = isPriority ? '#ede9fe' : isEdited ? '#ede9fe' : isSendBack ? '#fee2e2' : isReconReject ? '#fef3c7' : isReconApprove ? '#d1fae5' : isApproved ? '#d1fae5' : isPayment ? (PAY_BG[payStatus] ?? '#f3f4f6') : '#eff6ff'
                const verb       = isPriority ? 'changed priority' : isEdited ? 'edited bill' : isSendBack ? 'sent back' : isReconReject ? 'rejected reconciliation' : isReconApprove ? 'accepted reconciliation' : isApproved ? 'approved' : isSubmit ? 'submitted' : isPayment ? `marked ${payStatus}` : c.action
                const diffs      = isEdited && c.comment ? c.comment.split(';').map(s => s.trim()).filter(Boolean) : []
                const isExpanded = expandedDiff === c.id
                return (
                  <div key={c.id} className="bm__tl-row">
                    <div className="bm__tl-left">
                      <div className="bm__tl-dot" style={{ background: dotBg, color: dotColor }}>
                        {isPriority ? <span style={{ fontSize: 9, fontWeight: 700 }}>P</span> : isEdited ? <Pencil size={9} /> : isSendBack ? <CornerDownLeft size={9} /> : isReconReject ? <Info size={9} /> : isReconApprove ? <Check size={9} /> : isApproved ? <Check size={9} /> : isSubmit ? <Send size={9} /> : <span style={{ fontSize: 9 }}>₹</span>}
                      </div>
                      {idx < detail.chain.length - 1 && <div className="bm__tl-line" />}
                    </div>
                    <div className="bm__tl-body">
                      <div className="bm__tl-headline">
                        <span className="bm__tl-actor">{c.actor_name || 'System'}</span>
                        {c.actor_role && <span className="bm__tl-role">{c.actor_role.replace(/_/g, ' ')}</span>}
                        <span className="bm__tl-verb" style={{ color: dotColor }}>{verb}</span>
                        {isSendBack && (() => {
                          const ck = chainCacheKey(user?.company_id, detail.department)
                          const steps = _chainCache.get(ck) ?? []
                          // Use stored target_step if available; fall back to step-1
                          const targetStepId = c.target_step ?? null
                          const targetStep = targetStepId != null
                            ? steps.find(s => s.id === targetStepId) ?? null
                            : (steps.findIndex(s => s.id === c.step) - 1 >= 0 ? steps[steps.findIndex(s => s.id === c.step) - 1] : null)
                          // For the name: prefer last approver at that step from chain history, then assignees
                          const actedName = targetStep
                            ? (detail.chain ?? []).filter(ce => ce.step === targetStep.id && (ce.action === 'approve' || ce.action === 'approved' || ce.action === 'submit' || ce.action === 'submitted')).slice(-1)[0]?.actor_name ?? null
                            : null
                          const configAssignees = (targetStep as { assignees?: string[] } | null)?.assignees ?? []
                          const targetLabel = targetStep
                            ? (actedName ?? (configAssignees.length > 0 ? configAssignees[0] : targetStep.label))
                            : 'Submitter'
                          return <span style={{ fontSize: 11, color: '#9a3412', background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 5, padding: '1px 7px', marginLeft: 2 }}>→ {targetLabel}</span>
                        })()}
                      </div>
                      {isEdited && diffs.length > 0 && (
                        <button className="bm__tl-diff-toggle" onClick={() => setExpandedDiff(isExpanded ? null : c.id)}>
                          {isExpanded ? '▴ hide changes' : `▾ ${diffs.length} field${diffs.length > 1 ? 's' : ''} changed`}
                        </button>
                      )}
                      {isExpanded && (
                        <div className="bm__tl-diffs">
                          {diffs.map((d, i) => {
                            const [field, change] = d.split(':').map(s => s.trim())
                            const [oldVal, newVal] = (change ?? d).split('→').map(s => s.trim())
                            return (
                              <div key={i} className="bm__tl-diff-row">
                                <span className="bm__tl-diff-field">{field}</span>
                                <span className="bm__tl-diff-old">{oldVal}</span>
                                <span className="bm__tl-diff-arrow">→</span>
                                <span className="bm__tl-diff-new">{newVal}</span>
                              </div>
                            )
                          })}
                        </div>
                      )}
                      {!isEdited && c.comment && <div className="bm__tl-comment" style={{ whiteSpace: 'pre-wrap' }}>"{c.comment}"</div>}
                      <div className="bm__tl-ts">{c.created_at ? new Date(c.created_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

            {detailErr && <div style={{ padding: '12px 20px', color: '#ef4444', fontSize: 13 }}>{detailErr}</div>}
            {!detail && !detailErr && <div style={{ padding: '24px 20px', color: 'var(--ink-500)', fontSize: 13 }}>Loading details…</div>}

            {detail && (
              <>
              <div className="bm__body">
                <div className="bm__hero">
                  <div className="bm__hero-lbl">Requested Amount (incl. {jur.indirect_tax_label})</div>
                  <div className="bm__hero-val">{fmtAmt(detail.total_amount)}</div>
                  {isForeign && pipelineConverted && detail.original_amount != null && (
                    <div style={{ fontSize: 12, color: '#6366f1', fontWeight: 500, marginTop: 2 }}>
                      {fmtForeign(detail.original_amount)}
                    </div>
                  )}
                </div>

                <div className="bm__grid">
                  <BillSection title="Vendor Details" forceOpen={editing}>
                  {editing ? (
                    <>
                      <EField k="vendor_name" label="Vendor Name" editData={editData} setEditData={setEditData} /><EField k="vendor_gstn" label={gstOn ? 'Vendor GST / PAN' : 'Vendor tax ID'} editData={editData} setEditData={setEditData} checksum={gstOn} />
                    </>
                  ) : (
                    <>
                      <Field label="Vendor Name" value={detail.vendor.name} wide />
                      <Field label={gstOn ? 'Vendor GST / PAN' : 'Vendor tax ID'} value={detail.vendor.gstn} mono checksum={gstOn} />
                      <Field label="Vendor Email" value={detail.vendor.email} />
                    </>
                  )}
                  </BillSection>

                  <BillSection title="Buyer Details" closedOnPhone>
                  <Field label="Buyer Name" value={detail.buyer.name} />
                  {detail.buyer.brand && <Field label="Brand Name" value={detail.buyer.brand} />}
                  {gstOn && <Field label="Buyer GSTIN" value={detail.buyer.gstn} mono checksum />}
                  </BillSection>

                  <BillSection title="Bill Summary" forceOpen={editing}>
                  <Field label="Bill Number" value={detail.bill_number || detail.id} mono wide />
                  {editing ? (
                    <>
                      <EField k="invoice_date" label="Bill Date" type="date" editData={editData} setEditData={setEditData} />
                      <EField k="due_date" label="Due Date" type="date" editData={editData} setEditData={setEditData} />
                      <EField k="subtotal" label="Subtotal" type="number" editData={editData} setEditData={setEditData} />
                      <EField k="discount_amount" label="Discount" type="number" editData={editData} setEditData={setEditData} />
                      <EField k="tax_amount" label={gstOn && detail.vendor?.gstn ? "GST Amount" : `${jur.indirect_tax_label} Amount`} type="number" editData={editData} setEditData={setEditData} />
                      <EField k="total_amount" label="Total Amount" type="number" editData={editData} setEditData={setEditData} />
                      <div style={{ gridColumn: '1 / -1' }}>
                        <div className="bm__lbl">Category</div>
                        {bmAddingCategory ? (
                          <div style={{ display: 'flex', gap: 4 }}>
                            <input
                              className="bm__input"
                              autoFocus
                              placeholder="New category name…"
                              value={bmNewCategory}
                              onChange={e => setBmNewCategory(e.target.value)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') {
                                  const name = bmNewCategory.trim()
                                  if (name) {
                                    addCustomCategory(name)
                                    setBmCategories(getAllCategories())
                                    setEditData(p => ({ ...p, category: name }))
                                  }
                                  setBmAddingCategory(false); setBmNewCategory('')
                                } else if (e.key === 'Escape') {
                                  setBmAddingCategory(false); setBmNewCategory('')
                                }
                              }}
                              style={{ flex: 1 }}
                            />
                            <button type="button" style={{ padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', background: '#2563eb', color: '#fff', fontSize: 12, cursor: 'pointer' }}
                              onClick={() => {
                                const name = bmNewCategory.trim()
                                if (name) { addCustomCategory(name); setBmCategories(getAllCategories()); setEditData(p => ({ ...p, category: name })) }
                                setBmAddingCategory(false); setBmNewCategory('')
                              }}>Add</button>
                            <button type="button" style={{ padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', background: '#f3f4f6', fontSize: 12, cursor: 'pointer' }}
                              onClick={() => { setBmAddingCategory(false); setBmNewCategory('') }}>✕</button>
                          </div>
                        ) : (
                          <div style={{ position: 'relative' }}>
                            <select
                              className="bm__input"
                              style={{ appearance: 'none', paddingRight: 28, cursor: 'pointer' }}
                              value={editData.category ?? ''}
                              onChange={e => {
                                if (e.target.value === '__add__') { setBmAddingCategory(true) }
                                else { setEditData(p => ({ ...p, category: e.target.value })) }
                              }}
                            >
                              <option value="">Select category…</option>
                              {bmCategories.map(c => <option key={c} value={c}>{c}</option>)}
                              {user?.role === 'admin' && <option value="__add__">+ Add custom category…</option>}
                            </select>
                            <ChevronDown size={13} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: '#6b7280' }} />
                          </div>
                        )}
                      </div>
                      {user?.role === 'admin' && (
                        <div style={{ gridColumn: '1 / -1' }}>
                          <div className="bm__lbl">Department</div>
                          <div style={{ position: 'relative' }}>
                            <select
                              className="bm__input"
                              style={{ appearance: 'none', paddingRight: 28, cursor: 'pointer' }}
                              value={editData.department ?? ''}
                              onChange={e => setEditData(p => ({ ...p, department: e.target.value }))}
                            >
                              <option value="">— No department —</option>
                              {bmDepartments.map(d => <option key={d} value={d}>{d}</option>)}
                            </select>
                            <ChevronDown size={13} style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none', color: '#6b7280' }} />
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <Field label="Bill Date" value={fmtDate(detail.invoice_date)} />
                      {detail.service_month && <Field label="Service Month" value={fmtServiceMonth(detail.service_month)} />}
                      <Field label="Due Date" fix="due_date" value={<span style={{ color: isOverdue ? '#ef4444' : undefined, fontWeight: isOverdue ? 600 : undefined }}>{fmtDate(detail.due_date)}</span>} />
                      {detail.payment_terms_days != null && <Field label="Payment Terms" fix="payment_terms" value={detail.payment_terms_days === 0 ? 'Due on receipt' : `Net ${detail.payment_terms_days} days`} />}
                      <Field label="Subtotal" value={fmtAmt(detail.subtotal)} />
                      {(detail.discount_amount ?? 0) > 0 && <Field label="Discount" value={fmtAmt(detail.discount_amount)} />}
                      <Field label={gstOn && detail.vendor?.gstn ? "GST Amount" : `${jur.indirect_tax_label} Amount`} fix="gst_split" value={fmtAmt(detail.tax_amount)} />
                      <Field label="Total Amount" value={<strong style={{ fontSize: 15, color: '#111827' }}>{fmtAmt(detail.total_amount)}</strong>} />
                      {detail.currency_code && detail.currency_code !== baseCur && detail.original_amount != null && (
                        <div style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: '#eef2ff', borderRadius: 8, border: '1px solid #c7d2fe', fontSize: 12 }}>
                          <span style={{ fontSize: 16 }}>🌐</span>
                          <span style={{ color: '#4338ca', fontWeight: 600 }}>
                            {detail.currency_code === 'USD' ? '$' : detail.currency_code === 'EUR' ? '€' : detail.currency_code === 'GBP' ? '£' : detail.currency_code === 'JPY' ? '¥' : ''}{detail.original_amount.toLocaleString('en-IN')} {detail.currency_code}
                          </span>
                          {detail.total_amount != null && (
                            <span style={{ color: '#6b7280' }}>→ {fmtAmt(detail.total_amount)} <span style={{ fontSize: 10, color: 'var(--ink-500)' }}>(converted)</span></span>
                          )}
                          {(detail as any).fx_gain_loss != null && (detail as any).fx_gain_loss !== 0 && (
                            <span style={{ marginLeft: 'auto', color: (detail as any).fx_gain_loss > 0 ? '#059669' : '#dc2626', fontWeight: 600, fontSize: 11 }}>
                              {(detail as any).fx_gain_loss > 0 ? '▲' : '▼'} {fmtAmt(Math.abs((detail as any).fx_gain_loss))} FX {(detail as any).fx_gain_loss > 0 ? 'gain' : 'loss'}
                            </span>
                          )}
                        </div>
                      )}
                      {/* FX agent note — shown when agent has a non-obvious insight */}
                      {detail.currency_code && detail.currency_code !== baseCur && agentNote && agentNote.severity !== 'NONE' && agentNote.note_text && (
                        <div style={{
                          gridColumn: '1 / -1', display: 'flex', alignItems: 'flex-start', gap: 10,
                          padding: '10px 14px', borderRadius: 8, fontSize: 12, lineHeight: 1.5,
                          background: agentNote.severity === 'WARNING' ? '#fffbeb' : agentNote.severity === 'OPPORTUNITY' ? '#f0fdf4' : '#f8fafc',
                          border: `1px solid ${agentNote.severity === 'WARNING' ? '#fde68a' : agentNote.severity === 'OPPORTUNITY' ? '#a7f3d0' : '#e2e8f0'}`,
                        }}>
                          <span style={{ fontSize: 14, flexShrink: 0, marginTop: 1 }}>
                            {agentNote.severity === 'WARNING' ? '⚠️' : agentNote.severity === 'OPPORTUNITY' ? '💡' : 'ℹ️'}
                          </span>
                          <div>
                            <div style={{
                              color: agentNote.severity === 'WARNING' ? '#92400e' : agentNote.severity === 'OPPORTUNITY' ? '#065f46' : '#475569',
                              fontWeight: 500,
                            }}>
                              {agentNote.confidence != null && agentNote.confidence < 0.7 && <span style={{ color: 'var(--ink-500)', fontStyle: 'italic' }}>Estimated: </span>}
                              {agentNote.note_text}
                            </div>
                            {agentNote.created_at && (
                              <div style={{ fontSize: 10, color: 'var(--ink-500)', marginTop: 3 }}>
                                AI insight · {new Date(agentNote.created_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' })}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      {/* FX settlement info — shown after actual payment is recorded */}
                      {detail.currency_code && detail.currency_code !== baseCur && (detail as any).fx_effective_rate != null && (
                        <div style={{ gridColumn: '1 / -1', padding: '8px 12px', background: '#f0fdf4', borderRadius: 8, border: '1px solid #a7f3d0', fontSize: 12 }}>
                          <div style={{ fontWeight: 600, color: '#059669', marginBottom: 6 }}>FX Settlement</div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
                            <div>
                              <div style={{ color: '#6b7280', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Actual {baseCur} Paid</div>
                              <div style={{ fontWeight: 600, color: '#111827' }}>{(detail as any).fx_actual_inr != null ? fmtAmt((detail as any).fx_actual_inr) : '—'}</div>
                            </div>
                            <div>
                              <div style={{ color: '#6b7280', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Effective Rate</div>
                              <div style={{ fontWeight: 600, color: '#111827' }}>{(detail as any).fx_effective_rate?.toFixed(4)} {baseCur}/{detail.currency_code}</div>
                            </div>
                            <div>
                              <div style={{ color: '#6b7280', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>FX Cost</div>
                              <div style={{ fontWeight: 600, color: (detail as any).fx_realized_cost > 0 ? '#dc2626' : '#059669' }}>
                                {(detail as any).fx_realized_cost != null ? fmtAmt(Math.abs((detail as any).fx_realized_cost)) : '—'}
                                {(detail as any).fx_realized_cost > 0 ? ' extra' : (detail as any).fx_realized_cost < 0 ? ' saved' : ''}
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                      <Field label="Amount Paid" value={fmtAmt(detail.amount_paid)} />
                      <Field label="Outstanding" value={<span style={{ color: (detail.outstanding_amount ?? 0) > 0 ? '#ef4444' : '#059669', fontWeight: 600 }}>{fmtAmt(detail.outstanding_amount)}</span>} />
                    </>
                  )}
                  </BillSection>

                  {/* A section of its own, laid out exactly as the review
                      screen's Tax Breakdown: same fields, same order, same
                      three columns. The split used to be summarised in a
                      panel shaped like nothing else on either screen, so
                      checking an upload correction against the posted bill
                      meant re-reading it. Renders nothing on a bill that
                      predates the split being kept. Outside Bill Summary so
                      the two fold independently. */}
                  {!editing && <GstBreakdown data={detail.tax_breakdown} taxAmount={detail.tax_amount} />}

                  {(editing || detail.line_items.length > 0) && (
                    <>
                      <SectionHead title="Line Items" />
                      <div style={{ gridColumn: '1 / -1', overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                          <thead><tr style={{ background: '#f9fafb' }}>
                            {['#', 'Description', 'HSN', ...(sapEnabled ? ['SAP Code'] : []), 'Qty', 'Unit', 'Rate', 'Tax%', 'Total', ...(editing ? [''] : [])].map((h, hi) => (
                              <th key={h || `sp-${hi}`} style={{ padding: '6px 10px', fontWeight: 600, color: '#6b7280', textAlign: h === '#' ? 'center' : 'left', borderBottom: '1px solid #e5e7eb', whiteSpace: 'nowrap', fontSize: 11 }}>{h}</th>
                            ))}
                          </tr></thead>
                          <tbody>
                            {editing ? editLineItems.map((li, idx) => {
                              const upd = (k: keyof typeof li, v: string) => setEditLineItems(prev => prev.map((r, i) => i === idx ? recalc({ ...r, [k]: v }) : r))
                              // Typing a "%" in Rate switches the line to a
                              // percentage of the Qty column — "Commission @ 4% on
                              // 1,00,000". Stripped here so only the number is stored.
                              const updRate = (raw: string) => {
                                const pct = raw.trim().endsWith('%')
                                const num = raw.replace('%', '')
                                setEditLineItems(prev => prev.map((r, i) => i === idx
                                  ? recalc({ ...r, unit_price: num, rate_is_percent: pct })
                                  : r))
                              }
                              const liInp: React.CSSProperties = { width: '100%', border: '1px solid #e5e7eb', borderRadius: 4, padding: '3px 6px', fontSize: 12, outline: 'none', boxSizing: 'border-box' }
                              return (
                                <tr key={li._uid} style={{ borderBottom: '1px solid #f3f4f6' }}>
                                  <td style={{ padding: '4px 8px', textAlign: 'center', color: 'var(--ink-500)' }}>{li.line_number || '+'}</td>
                                  <td style={{ padding: '4px 6px', minWidth: 140 }}><input style={liInp} value={li.product_name} onChange={e => upd('product_name', e.target.value)} /></td>
                                  <td style={{ padding: '4px 6px', minWidth: 80 }}><input style={liInp} value={li.hsn_code} onChange={e => upd('hsn_code', e.target.value)} /></td>
                                  {sapEnabled && <td style={{ padding: '4px 6px', minWidth: 90, color: 'var(--ink-500)', fontFamily: 'monospace', fontSize: 11 }} title="Auto-mapped from SAP catalog">{li.sap_item_code || '—'}</td>}
                                  <td style={{ padding: '4px 6px', minWidth: 60 }}><input style={{ ...liInp, textAlign: 'right' }} type="number" value={li.quantity} onChange={e => upd('quantity', e.target.value)} /></td>
                                  <td style={{ padding: '4px 6px', minWidth: 50 }}><input style={liInp} value={li.unit} onChange={e => upd('unit', e.target.value)} /></td>
                                  <td style={{ padding: '4px 6px', minWidth: 108 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                      <input
                                        style={{ ...liInp, textAlign: 'right', flex: 1, minWidth: 0 }}
                                        type="text" inputMode="decimal"
                                        value={li.rate_is_percent ? `${li.unit_price}%` : li.unit_price}
                                        onChange={e => updRate(e.target.value)} />
                                      {/* The visible affordance. Typing a trailing "%" does the
                                          same thing, but nobody discovers a convention that is
                                          only described in a tooltip. */}
                                      <button
                                        type="button"
                                        onClick={() => setEditLineItems(prev => prev.map((r, i) =>
                                          i === idx ? recalc({ ...r, rate_is_percent: !r.rate_is_percent }) : r))}
                                        title={li.rate_is_percent
                                          ? 'Rate is a percentage of Qty. Click for a per-unit rate.'
                                          : 'Rate is per unit. Click to make it a percentage of Qty.'}
                                        aria-pressed={li.rate_is_percent}
                                        aria-label="Toggle between a per-unit rate and a percentage of Qty"
                                        style={{
                                          width: 24, height: 24, flex: '0 0 auto', borderRadius: 5, cursor: 'pointer',
                                          fontSize: 12, fontWeight: 700, lineHeight: 1, fontFamily: 'inherit',
                                          border: `1px solid ${li.rate_is_percent ? '#2563eb' : '#e5e7eb'}`,
                                          background: li.rate_is_percent ? '#eff6ff' : '#fff',
                                          color: li.rate_is_percent ? '#2563eb' : '#9ca3af',
                                        }}
                                      >{li.rate_is_percent ? '%' : '\u20b9'}</button>
                                    </div>
                                  </td>
                                  <td style={{ padding: '4px 6px', minWidth: 60 }}><input style={{ ...liInp, textAlign: 'right' }} type="number" value={li.tax_rate_percent} onChange={e => upd('tax_rate_percent', e.target.value)} /></td>
                                  <td style={{ padding: '4px 6px', minWidth: 80 }}><input style={{ ...liInp, textAlign: 'right' }} type="number" value={li.line_total} onChange={e => upd('line_total', e.target.value)} /></td>
                                  <td style={{ padding: '4px 6px', width: 32 }}>
                                    <button
                                      type="button"
                                      onClick={() => removeEditLine(li._uid)}
                                      title="Remove this line"
                                      aria-label={`Remove line ${li.line_number || 'new'}`}
                                      style={{ width: 24, height: 24, borderRadius: 5, border: '1px solid #e5e7eb', background: 'none', color: '#9ca3af', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
                                    ><Trash2 size={12} /></button>
                                  </td>
                                </tr>
                              )
                            }) : detail.line_items.map(li => (
                              <tr key={li.line_number} style={{ borderBottom: '1px solid #f3f4f6' }}>
                                <td style={{ padding: '6px 10px', textAlign: 'center', color: 'var(--ink-500)' }}>{li.line_number}</td>
                                <td style={{ padding: '6px 10px', color: '#111827', fontWeight: 500 }}>{li.product_name || '—'}</td>
                                <td style={{ padding: '6px 10px', color: 'var(--ink-500)', fontFamily: 'monospace', fontSize: 11 }}>{li.hsn_code || '—'}</td>
                                {sapEnabled && (
                                <td style={{ padding: '6px 10px', fontFamily: 'monospace', fontSize: 11 }} title={li.sap_material_desc ? `${li.sap_material_desc}${li.sap_map_score != null ? ` (score ${(li.sap_map_score * 100).toFixed(0)}%)` : ''}` : 'Not yet mapped'}>
                                  {li.sap_item_code
                                    ? <span style={{ color: li.sap_map_status === 'REVIEW' ? '#d97706' : '#059669', fontWeight: 600 }}>{li.sap_item_code}</span>
                                    : <span style={{ color: '#d1d5db' }}>—</span>}
                                </td>
                                )}
                                <td style={{ padding: '6px 10px', textAlign: 'right' }}>{li.quantity ?? '—'}</td>
                                <td style={{ padding: '6px 10px' }}>{li.unit || '—'}</td>
                                <td style={{ padding: '6px 10px', textAlign: 'right' }} title={li.rate_is_percent ? `${li.unit_price}% of ${li.quantity}` : undefined}>
                                  {li.unit_price == null ? '—'
                                    : li.rate_is_percent ? `${li.unit_price}%`
                                    : fmtOriginalCur(li.unit_price)}
                                </td>
                                <td style={{ padding: '6px 10px', textAlign: 'right' }}>{li.tax_rate_percent != null ? `${li.tax_rate_percent}%` : '—'}</td>
                                <td style={{ padding: '6px 10px', textAlign: 'right', fontWeight: 600 }}>{fmtOriginalCur(li.line_total)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      {editing && (
                        <div style={{ gridColumn: '1 / -1', marginTop: 6 }}>
                          <button
                            type="button"
                            onClick={addEditLine}
                            style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '7px 12px', border: '1px dashed #2563eb', borderRadius: 6, background: '#eff6ff', color: '#2563eb', fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
                          ><Plus size={13} aria-hidden="true" /> Add line item</button>
                          {editLineItems.length === 0 && (
                            <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--ink-500)' }}>
                              This bill has no line items yet.
                            </span>
                          )}
                        </div>
                      )}
                    </>
                  )}

                  {/* ── TDS breakdown (below line items) ── */}
                  {!editing && tdsOn && detail.tds_status && (
                    <div style={{ gridColumn: '1 / -1', marginTop: 4 }}>
                      <TdsBlockWithFlag
                        detail={detail}
                        billId={bill.invoice_id}
                        user={user}
                        onDetailUpdate={(updates) => setDetail((d: BillDetail | null) => d ? { ...d, ...updates } : d)}
                      />
                    </div>
                  )}

                  {/* What review decided about this bill, rather than what the
                      document said: how it was coded and where it pays. Built
                      in the Review Hub's own claims-panel shape — label,
                      value, state badge — so the two screens read as one
                      product. Near the bottom because it is what you check
                      last, after the amount and the lines. */}
                  {!editing && <ExtractedCoding detail={detail} sapEnabled={sapEnabled} />}
                </div>

                {/* ── Attachments ── */}
                <div className="bm__attach-section">
                  <div className="bm__section" style={{ gridColumn: undefined, marginBottom: 10 }}>Supporting Documents</div>
                  <div className="bm__attach-list">
                    {(detail.attachments ?? []).length === 0 && (
                      <div className="bm__attach-empty">No attachments yet</div>
                    )}
                    {(detail.attachments ?? []).map((a: BillAttachment) => {
                      const url = `${API_BASE}/api/bills/${bill.invoice_id}/attachments/${a.id}/download`
                      const isImage = /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(a.file_name)
                      return (
                        <div key={a.id} className="bm__attach-row">
                          <FileText size={14} style={{ color: '#6b7280', flexShrink: 0 }} />
                          <div className="bm__attach-info" style={{ cursor: 'pointer' }}
                            onClick={async e => {
                              const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                              const isXls = /\.(xlsx?|csv)$/i.test(a.file_name)
                              const previewUrl = `${url}?preview=true`
                              setPreviewAttach({ url, name: a.file_name, rect, attachmentId: a.id })
                              try {
                                const blob = await fetchAuthedBlob(previewUrl)
                                if (isXls) {
                                  const blobData = await blob.arrayBuffer()
                                  setPreviewAttach(prev => prev ? { ...prev, blobData } : prev)
                                } else {
                                  const blobUrl = URL.createObjectURL(blob)
                                  setPreviewAttach(prev => prev ? { ...prev, blobUrl } : prev)
                                }
                              } catch { /* fallback */ }
                            }}>
                            <span className="bm__attach-name" style={{ textDecoration: 'underline', color: '#2563eb', cursor: 'pointer' }}>{a.file_name}</span>
                            <span className="bm__attach-meta">
                              {a.file_size ? `${(a.file_size / 1024).toFixed(0)} KB · ` : ''}
                              {a.uploaded_by || 'Unknown'} · {a.uploaded_at ? new Date(a.uploaded_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : ''}
                            </span>
                          </div>
                          <button
                            onClick={() => downloadAuthed(url, a.file_name)}
                            className="bm__attach-dl"
                            title={`Download ${a.file_name}`}
                            aria-label={`Download ${a.file_name}`}>
                            <Download size={13} aria-hidden="true" />
                          </button>
                          <button className="bm__attach-del" onClick={() => handleAttachDelete(a.id)} title="Delete"><X size={12} /></button>
                        </div>
                      )
                    })}
                  </div>
                  <div className="bm__attach-upload">
                    <input ref={attachInputRef} type="file" multiple style={{ display: 'none' }} onChange={e => handleAttachUpload(e.target.files)} />
                    <button className="bm__attach-btn" onClick={() => attachInputRef.current?.click()} disabled={uploading}>
                      {uploading ? 'Uploading…' : '+ Attach files'}
                    </button>
                  </div>
                </div>
              </div>

              </>
            )}
          </div>

          {/* ── Action panel (sticky footer) ── */}
          {(hasAction || editing) && (
            <div className="bm__actions">
              {actionErr && <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 8 }}>{actionErr}</div>}

              {editing ? (
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="bm__btn bm__btn--secondary" onClick={() => setEditing(false)} disabled={saving}>Cancel</button>
                  <button className="bm__btn bm__btn--primary" onClick={doSaveEdit} disabled={saving} style={{ flex: 1 }}>
                    {saving ? 'Saving…' : 'Save Changes'}
                  </button>
                </div>
              ) : (
                <>
                  {(canSubmit || canApprove || canSendBack) && (
                    <textarea
                      className="bm__comment"
                      placeholder={
                        canSendBack ? 'Add a note (required for Send Back)…'
                        : canSubmit  ? 'Add a note to this submission (optional)…'
                        : 'Optional note…'
                      }
                      value={comment}
                      onChange={e => setComment(e.target.value)}
                      rows={2}
                    />
                  )}
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    {canSubmit && (
                      <button className="bm__btn bm__btn--primary" style={{ flex: 1 }} disabled={actioning} onClick={() => doAction('submit')}>
                        <Send size={13} /> {status === 'DRAFT' ? 'Submit for Approval' : 'Resubmit'}
                      </button>
                    )}
                    {canApprove && (
                      <button className="bm__btn bm__btn--approve" style={{ flex: 1 }} disabled={actioning} data-guide="bill.approve" onClick={() => doAction('approve')}>
                        <Check size={13} /> Approve
                      </button>
                    )}
                    {canSendBack && !showSendBackPicker && (
                      <button className="bm__btn bm__btn--sendback" disabled={actioning} onClick={() => {
                        setShowSendBackPicker(true)
                        setSendBackTarget('')
                      }}>
                        <CornerDownLeft size={13} /> Send Back
                      </button>
                    )}
                  </div>

                  {/* Send-back step picker */}
                  {showSendBackPicker && canSendBack && (() => {
                    const cacheKey = chainCacheKey(user?.company_id, detail?.department)
                    const chainSteps = _chainCache.get(cacheKey) ?? []
                    const statusKeys = ['PENDING_APPROVER', 'PENDING_ACCOUNTANT', 'PENDING_FC']
                    // Get who actually submitted this bill from chain history
                    const submitterEntry = detail?.chain?.find(c => c.action === 'submit' || c.action === 'submitted')
                    const submitterName = submitterEntry?.actor_name ?? ''
                    const submitterRole = submitterEntry?.actor_role ?? ''
                    const allStatuses: { label: string; role: string; names: string[]; value: string }[] = [
                      { label: 'Submitter', role: submitterRole, names: submitterName ? [submitterName] : [], value: 'NEEDS_REVISION' },
                    ]
                    const approvalSteps = chainSteps.filter((_, i) => i > 0)
                    approvalSteps.forEach((step, i) => {
                      const s = step as { id: number; label: string; role: string; assignees?: string[] }
                      const statusVal = statusKeys[i] ?? `PENDING_STEP_${i + 1}`
                      // Prefer configured assignees; fall back to who actually acted at this step
                      const configNames = s.assignees ?? []
                      const actedNames = (detail?.chain ?? [])
                        .filter(c => c.step === s.id && (c.action === 'approve' || c.action === 'approved'))
                        .map(c => c.actor_name).filter(Boolean) as string[]
                      const names = configNames.length > 0 ? configNames : actedNames.length > 0 ? [actedNames[actedNames.length - 1]] : []
                      allStatuses.push({ label: s.label, role: s.role, names, value: statusVal })
                    })
                    const currentIdx = allStatuses.findIndex(s => s.value === status)
                    const targets = currentIdx > 0 ? allStatuses.slice(0, currentIdx) : allStatuses

                    return (
                      <div style={{ marginTop: 10, background: '#fff7ed', border: '1px solid #fed7aa', borderRadius: 10, padding: '12px 14px' }}>
                        <div style={{ fontSize: 11, fontWeight: 700, color: '#c2410c', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
                          <CornerDownLeft size={11} style={{ display: 'inline', marginRight: 4 }} />
                          Send back to which step?
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
                          {targets.map(t => (
                            <label key={t.value} style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', padding: '8px 12px', borderRadius: 8, background: sendBackTarget === t.value ? '#ffedd5' : '#fff', border: `1px solid ${sendBackTarget === t.value ? '#fb923c' : '#fed7aa'}`, transition: 'all 0.12s' }}>
                              <input
                                type="radio"
                                name="sendback-target"
                                value={t.value}
                                checked={sendBackTarget === t.value}
                                onChange={() => setSendBackTarget(t.value)}
                                style={{ accentColor: '#ea580c', flexShrink: 0 }}
                              />
                              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                  <span style={{ fontSize: 13, fontWeight: 600, color: '#1f2937' }}>{t.label}</span>
                                  {t.role && <span style={{ fontSize: 10, color: 'var(--ink-500)', background: '#f3f4f6', borderRadius: 4, padding: '1px 5px', textTransform: 'capitalize' }}>{t.role.replace(/_/g, ' ')}</span>}
                                </div>
                                {t.names.length > 0 && (
                                  <span style={{ fontSize: 11, color: '#6b7280' }}>{t.names.join(', ')}</span>
                                )}
                              </div>
                            </label>
                          ))}
                        </div>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button
                            className="bm__btn bm__btn--sendback"
                            style={{ flex: 1 }}
                            disabled={actioning || !sendBackTarget}
                            onClick={() => doAction('send_back', sendBackTarget || undefined)}
                          >
                            <CornerDownLeft size={13} /> Confirm Send Back
                          </button>
                          <button
                            onClick={() => { setShowSendBackPicker(false); setSendBackTarget('') }}
                            style={{ padding: '7px 14px', borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff', color: '#6b7280', fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )
                  })()}
                </>
              )}
            </div>
          )}
        </div>

        {/* ── Right: PDF or No-PDF summary ── */}
        {uploadId ? (
          <div className="bm__right" ref={pdfPaneRef}>
            <div className="bm__pdfbar">
              <div style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <button className="bm__pctrl" onClick={() => setPdfFull(true)} disabled={!pdfProxy} title="Full screen" aria-label="View full screen"><Maximize2 size={14} /></button>
                <div style={{ width: 1, height: 16, background: '#e5e7eb', margin: '0 4px' }} />
                <button className="bm__pctrl" onClick={() => setZoom(z => z + 80)}><ZoomIn size={14} /></button>
                <button className="bm__pctrl" onClick={() => setZoom(z => z - 80)}><ZoomOut size={14} /></button>
                <div style={{ width: 1, height: 16, background: '#e5e7eb', margin: '0 4px' }} />
                {/* A page-with-arrow icon, so it can't be read as "refresh". */}
                <button className="bm__pctrl" onClick={() => setRotation(r => (r + 90) % 360)} title="Rotate page" aria-label="Rotate page"><RotateCwSquare size={15} /></button>
                <div style={{ width: 1, height: 16, background: 'var(--line-200)', margin: '0 4px' }} />
                <button className="bm__pctrl" style={{ fontSize: 12, fontWeight: 600 }} onClick={() => { setZoom(0); setRotation(0); setPageNum(1) }} title="Reset zoom and rotation" disabled={zoom === 0 && rotation === 0 && pageNum === 1}>Reset</button>
              </div>
              {numPages > 0 && <span style={{ fontSize: 12, color: '#6b7280' }}>{`Page ${pageNum} of ${numPages}`}</span>}
              {numPages > 1 && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <button className="bm__pctrl" disabled={pageNum <= 1} onClick={() => setPageNum(p => p - 1)}><ChevronLeft size={14} /></button>
                  <button className="bm__pctrl" disabled={pageNum >= numPages} onClick={() => setPageNum(p => p + 1)}><ChevronRight size={14} /></button>
                </div>
              )}
            </div>
            <PdfViewer
              doc={pdfDoc}
              pageNum={pageNum}
              pdfW={pdfW}
              zoom={zoom}
              rotate={rotation}
              onLoaded={pdf => { setPdfProxy(pdf); setNumPages(pdf.numPages); setPageNum(1) }}
              onError={() => setPdfLoadErr(true)}
              onRendered={onPdfRendered}
              pdfRef={pdfRef}
            />
            {pdfFull && pdfProxy && (
              <BillPdfFullscreen
                pdf={pdfProxy}
                rotate={rotation}
                sourceRef={isPhone ? pdfBtnRef : pdfPaneRef}
                pageNum={pageNum}
                numPages={numPages}
                onPage={setPageNum}
                onClose={() => setPdfFull(false)}
              />
            )}
          </div>
        ) : (
          /* ── No PDF: rich summary panel ── */
          <div className="bm__right bm__right--nopdf">
            <div style={{ padding: '28px 28px 12px', borderBottom: '1px solid #e5e7eb' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <div style={{ width: 36, height: 36, borderRadius: 10, background: '#f3f4f6', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <FileText size={18} color="#5b6473" />
                </div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: '#111827' }}>No Document Attached</div>
                  <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>Bill entered manually</div>
                </div>
              </div>
            </div>

            <div style={{ padding: '20px 28px', display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto', flex: 1 }}>

              {/* Amount card */}
              <div style={{ background: 'linear-gradient(135deg, #1e40af 0%, #3b82f6 100%)', borderRadius: 14, padding: '20px 22px', color: '#fff' }}>
                <div style={{ fontSize: 11, fontWeight: 600, opacity: 0.75, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>Total Amount</div>
                <div style={{ fontSize: 34, fontWeight: 800, letterSpacing: '-1px', marginBottom: 10 }}>
                  {detail?.total_amount != null ? tenantMoney(detail.total_amount, baseCur, false) : '—'}
                </div>
                <div style={{ display: 'flex', gap: 16 }}>
                  {[
                    { label: 'Subtotal', val: detail?.subtotal },
                    { label: 'Tax', val: detail?.tax_amount },
                    { label: 'Discount', val: detail?.discount_amount },
                  ].map(r => r.val != null ? (
                    <div key={r.label}>
                      <div style={{ fontSize: 10, opacity: 0.65, marginBottom: 2 }}>{r.label}</div>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>
                        {tenantMoney(r.val, baseCur, false)}
                      </div>
                    </div>
                  ) : null)}
                </div>
              </div>

              {/* Key details grid */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {[
                  { label: 'Invoice Date', val: detail?.invoice_date ? new Date(detail.invoice_date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—' },
                  { label: 'Due Date', val: detail?.due_date ? new Date(detail.due_date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—' },
                  { label: 'Invoice No.', val: detail?.bill_number || '—' },
                  { label: 'Category', val: detail?.category || '—' },
                  { label: 'Department', val: detail?.department || '—' },
                  { label: 'Payment Terms', val: detail?.payment_terms_days != null ? (detail.payment_terms_days === 0 ? 'Due on receipt' : `${detail.payment_terms_days} days`) : '—' },
                  { label: 'Currency', val: detail?.currency_code || 'INR' },
                ].map(r => (
                  <div key={r.label} style={{ background: '#f9fafb', borderRadius: 10, padding: '12px 14px', border: '1px solid #f3f4f6' }}>
                    <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 4 }}>{r.label}</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{r.val}</div>
                  </div>
                ))}
              </div>

              {/* Vendor + Buyer */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {[
                  { title: 'Vendor', name: detail?.vendor?.name, gstn: detail?.vendor?.gstn, email: detail?.vendor?.email },
                  { title: 'Buyer', name: detail?.buyer?.name, gstn: detail?.buyer?.gstn, email: null },
                ].map(p => (
                  <div key={p.title} style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 14px' }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>{p.title}</div>
                    <div style={{ fontSize: 13, fontWeight: 700, color: '#111827', marginBottom: 3 }}>{p.name || '—'}</div>
                    {p.gstn && <div style={{ fontSize: 11, color: 'var(--ink-500)', fontFamily: 'monospace', marginBottom: 2 }}>{p.gstn}</div>}
                    {p.email && <div style={{ fontSize: 11, color: '#6b7280' }}>{p.email}</div>}
                  </div>
                ))}
              </div>

              {/* Admin priority override */}
              {user?.role === 'admin' && detail?.status === 'APPROVED' && (
                <div style={{ background: '#faf5ff', border: '1px solid #e9d5ff', borderRadius: 10, padding: '12px 16px' }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#7c3aed', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
                    📌 Payment Priority
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    {(['', 'p0', 'p1', 'p2', 'p3'] as const).map(val => {
                      const isAuto = val === ''
                      const meta = isAuto ? null : priorityMeta(labels)[val as NonNullable<PayPriority>]
                      const isSelected = (detail?.manual_priority ?? '') === val
                      return (
                        <button
                          key={val}
                          disabled={prioritySaving}
                          onClick={async () => {
                            if (isSelected) return
                            setPrioritySaving(true)
                            try {
                              const prevPriority = detail?.manual_priority || 'auto'
                              const newPriority  = val || 'auto'
                              await updateBill(detail!.invoice_id, { manual_priority: val || null, actor_name: user?.name, actor_role: user?.role })
                              // Log the priority change in the activity trail
                              await billAction(detail!.invoice_id, {
                                action: 'priority_change',
                                actor_role: user?.role ?? '',
                                actor_name: user?.name ?? '',
                                comment: `Priority changed from ${prevPriority.toUpperCase()} to ${newPriority.toUpperCase()}`,
                              }).catch(() => {})
                              invalidateDetailCache(detail!.invoice_id)
                              invalidateBillsCache(user?.company_id)
                              onRefresh?.()
                            } finally { setPrioritySaving(false) }
                          }}
                          style={{
                            padding: '4px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                            cursor: isSelected ? 'default' : 'pointer',
                            border: isSelected ? '2px solid #7c3aed' : '1.5px solid #e5e7eb',
                            background: isSelected ? (meta ? meta.bg : '#f3e8ff') : '#fff',
                            color: isSelected ? (meta ? meta.color : '#7c3aed') : '#6b7280',
                            boxShadow: isSelected ? '0 0 0 2px #e9d5ff' : 'none',
                            transition: 'all 0.15s',
                            opacity: prioritySaving ? 0.5 : 1,
                          }}
                        >
                          {isAuto ? 'Auto' : meta!.label}
                        </button>
                      )
                    })}
                    {prioritySaving && <span style={{ fontSize: 11, color: 'var(--ink-500)' }}>Saving…</span>}
                    {detail?.manual_priority && (
                      <span style={{ fontSize: 11, color: '#7c3aed', marginLeft: 4 }}>
                        Override active · <span style={{ color: 'var(--ink-500)' }}>click Auto to reset</span>
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Payment status */}
              {detail?.total_amount != null && (
                <div style={{ background: '#f0fdf4', border: '1px solid #a7f3d0', borderRadius: 10, padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: '#059669', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>Payment Status</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: '#111827' }}>
                      {detail.amount_paid != null ? `${fmtAmt(detail.amount_paid)} paid` : 'Unpaid'}
                    </div>
                  </div>
                  {detail.outstanding_amount != null && detail.outstanding_amount > 0 && (
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 11, color: 'var(--ink-500)', marginBottom: 2 }}>Outstanding</div>
                      <div style={{ fontSize: 14, fontWeight: 700, color: '#dc2626' }}>{fmtAmt(detail.outstanding_amount)}</div>
                    </div>
                  )}
                </div>
              )}

              {/* Crypto rail checks: wallet on the invoice vs the verified wallet, and
                  invoice lines vs the vendor's contract rates (docs/crypto-rail). */}
              {cryptoOn && <BillCryptoChecks invoiceId={bill.invoice_id} />}

              {/* Vendor disbursement (IMPS demo) — hands off to the dedicated Payment Gateway page. Only an admin may initiate a payment. */}
              {payEnabled && (impsOn || usdcRail) && detail?.outstanding_amount != null && detail.outstanding_amount > 0 && (
                <div style={{ background: '#fffbeb', border: '1px solid #fde68a', borderRadius: 10, padding: '12px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#92400e', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      {impsOn ? 'Pay via Bank · Demo IMPS' : 'Pay vendor · USDC'}
                    </div>
                    {user?.role === 'admin' ? (
                      <button
                        onClick={() => onPay(bill)}
                        style={{ fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 6, border: 'none', background: '#059669', color: '#fff', cursor: 'pointer', fontFamily: 'inherit' }}
                      >
                        Pay via Payment Gateway →
                      </button>
                    ) : (
                      <span style={{ fontSize: 11, color: '#92400e' }}>Admin only</span>
                    )}
                  </div>

                  {/* Payment history */}
                  {disbursements.length > 0 && (
                    <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
                      {disbursements.map(d => (
                        <div key={d.disbursement_id} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '4px 12px', padding: '6px 10px', background: '#fff', borderRadius: 8, border: '1px solid #fde68a', fontSize: 11 }}>
                          <div>{new Date(d.created_at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })}</div>
                          <div style={{ fontWeight: 600 }}>{tenantMoney(d.amount, (d as { currency_code?: string }).currency_code || baseCur)}</div>
                          <div style={{
                            color: d.status === 'SUCCESS' ? '#059669' : d.status === 'FAILED' ? '#dc2626' : '#d97706',
                            fontWeight: 600,
                          }}>{d.status}{d.bank_rrn ? ` · ${d.bank_rrn}` : ''}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* FX payment recording & history (non-INR bills only) */}
              {isForeign && detail?.original_amount != null && (
                <div style={{ background: '#f8fafc', border: '1px solid #e0e7ff', borderRadius: 10, padding: '12px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#4338ca', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      FX Payment · {cur}
                    </div>
                    {status !== 'PAID' && (
                      <button
                        onClick={() => { setShowFxPayForm(p => !p); setFxPayError('') }}
                        style={{ fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6, border: '1px solid #a5b4fc', background: showFxPayForm ? '#e0e7ff' : '#fff', color: '#4338ca', cursor: 'pointer', fontFamily: 'inherit' }}
                      >
                        {showFxPayForm ? 'Cancel' : '+ Record Payment'}
                      </button>
                    )}
                  </div>

                  {/* Record FX payment form */}
                  {showFxPayForm && (
                    <div style={{ display: 'grid', gap: 8, marginBottom: 12, padding: '10px 12px', background: '#fff', borderRadius: 8, border: '1px solid #e0e7ff' }}>
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
                          Actual INR Paid <span style={{ color: '#dc2626' }}>*</span>
                        </div>
                        <input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder={`e.g. ${detail.total_amount != null ? Math.round(detail.total_amount).toLocaleString('en-IN') : '0'}`}
                          value={fxPayFormData.actual_inr_paid}
                          onChange={e => setFxPayFormData(p => ({ ...p, actual_inr_paid: e.target.value }))}
                          style={{ width: '100%', border: '1px solid #c7d2fe', borderRadius: 6, padding: '6px 10px', fontSize: 13, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }}
                        />
                        {fxPayFormData.actual_inr_paid && detail.original_amount != null && parseFloat(fxPayFormData.actual_inr_paid) > 0 && (
                          <div style={{ fontSize: 11, color: '#6b7280', marginTop: 3 }}>
                            Effective rate: {(parseFloat(fxPayFormData.actual_inr_paid) / detail.original_amount).toFixed(4)} INR/{cur}
                          </div>
                        )}
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                        <div>
                          <div style={{ fontSize: 10, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>Bank Name</div>
                          <input
                            type="text"
                            placeholder="e.g. HDFC Bank"
                            value={fxPayFormData.bank_name}
                            onChange={e => setFxPayFormData(p => ({ ...p, bank_name: e.target.value }))}
                            style={{ width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '6px 10px', fontSize: 12, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }}
                          />
                        </div>
                        <div>
                          <div style={{ fontSize: 10, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>Bank Reference</div>
                          <input
                            type="text"
                            placeholder="TT/SWIFT ref"
                            value={fxPayFormData.reference_no}
                            onChange={e => setFxPayFormData(p => ({ ...p, reference_no: e.target.value }))}
                            style={{ width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '6px 10px', fontSize: 12, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }}
                          />
                        </div>
                      </div>
                      <div>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>Notes</div>
                        <input
                          type="text"
                          placeholder="Optional notes"
                          value={fxPayFormData.notes}
                          onChange={e => setFxPayFormData(p => ({ ...p, notes: e.target.value }))}
                          style={{ width: '100%', border: '1px solid #e5e7eb', borderRadius: 6, padding: '6px 10px', fontSize: 12, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit' }}
                        />
                      </div>
                      {fxPayError && <div style={{ fontSize: 11, color: '#dc2626' }}>{fxPayError}</div>}
                      <button
                        disabled={fxPaySaving || !fxPayFormData.actual_inr_paid}
                        onClick={async () => {
                          const amt = parseFloat(fxPayFormData.actual_inr_paid)
                          if (!amt || amt <= 0) { setFxPayError('Enter a valid INR amount'); return }
                          setFxPaySaving(true); setFxPayError('')
                          try {
                            await recordFxPayment(bill.invoice_id, {
                              actual_inr_paid: amt,
                              bank_name: fxPayFormData.bank_name || undefined,
                              bank_reference: fxPayFormData.reference_no || undefined,
                              notes: fxPayFormData.notes || undefined,
                              company_id: user?.company_id,
                            })
                            setShowFxPayForm(false)
                            setFxPayFormData({ actual_inr_paid: '', bank_name: '', reference_no: '', notes: '' })
                            // Refresh payments list and detail
                            const [payments] = await Promise.all([
                              fetchFxPayments(bill.invoice_id, user?.company_id),
                              fetchBillDetail(bill.invoice_id).then(setDetail).catch(() => {}),
                            ])
                            setFxPayments(payments)
                            invalidateDetailCache(bill.invoice_id)
                            onRefresh()
                          } catch (e) {
                            setFxPayError(e instanceof Error ? e.message : 'Failed to record payment')
                          } finally { setFxPaySaving(false) }
                        }}
                        style={{ padding: '7px 16px', borderRadius: 7, border: 'none', background: fxPaySaving ? '#a5b4fc' : '#4338ca', color: '#fff', fontSize: 12, fontWeight: 700, cursor: fxPaySaving ? 'default' : 'pointer', fontFamily: 'inherit', alignSelf: 'flex-start' }}
                      >
                        {fxPaySaving ? 'Saving…' : 'Record FX Payment'}
                      </button>
                    </div>
                  )}

                  {/* FX payment history list */}
                  {fxPayLoading ? (
                    <div style={{ fontSize: 12, color: 'var(--ink-500)', padding: '4px 0' }}>Loading payment history…</div>
                  ) : fxPayments.length === 0 ? (
                    <div style={{ fontSize: 12, color: 'var(--ink-500)' }}>No FX payments recorded yet.</div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                      {fxPayments.map((p, i) => (
                        <div key={p.payment_id ?? i} style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '4px 12px', padding: '8px 10px', background: '#fff', borderRadius: 8, border: '1px solid #e0e7ff', fontSize: 11 }}>
                          <div>
                            <div style={{ color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.04em', fontSize: 10 }}>Date</div>
                            <div style={{ fontWeight: 600, color: '#111827' }}>{p.payment_date ? new Date(p.payment_date).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.04em', fontSize: 10 }}>INR Paid</div>
                            <div style={{ fontWeight: 600, color: '#111827' }}>₹{(p.actual_inr_paid ?? 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                          </div>
                          <div>
                            <div style={{ color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.04em', fontSize: 10 }}>Eff. Rate</div>
                            <div style={{ fontWeight: 600, color: '#111827' }}>{p.effective_rate?.toFixed(4) ?? '—'} INR/{cur}</div>
                          </div>
                          {(p.bank_spread != null) && (
                            <div style={{ gridColumn: '1 / -1' }}>
                              <span style={{ color: 'var(--ink-500)' }}>Bank spread: </span>
                              <span style={{ color: p.bank_spread > 0 ? '#dc2626' : '#059669', fontWeight: 600 }}>
                                {p.bank_spread > 0 ? '+' : ''}{p.bank_spread.toFixed(4)}
                                {p.bank_spread_pct != null ? ` (${p.bank_spread_pct.toFixed(2)}%)` : ''}
                              </span>
                              {p.bank_name && <span style={{ color: 'var(--ink-500)' }}> · {p.bank_name}</span>}
                            </div>
                          )}
                          {p.bank_reference && (
                            <div style={{ gridColumn: '1 / -1', color: '#6b7280' }}>Ref: {p.bank_reference}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Admin: Soft-delete / Hold */}
              {user?.role === 'admin' && (
                <div style={{ background: '#fafafa', border: '1px solid #e5e7eb', borderRadius: 10, padding: '12px 16px' }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>Admin Actions</div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {bill.soft_status !== 'on_hold' && bill.soft_status !== 'deleted' && (
                      <>
                        <button
                          onClick={async () => {
                            if (!window.confirm('Put this bill on hold? It will be hidden from normal views but can be restored.')) return
                            await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/bills/${bill.invoice_id}/soft-status?status=on_hold&company_id=${user?.company_id ?? ''}`, { method: 'PATCH' })
                            onRefresh()
                            onClose()
                          }}
                          style={{ display:'flex', alignItems:'center', gap:5, padding:'7px 14px', borderRadius:7, border:'1px solid #fde68a', background:'#fffbeb', color:'#d97706', fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}
                        >
                          <PauseCircle size={13} aria-hidden="true" /> Put on Hold
                        </button>
                        <button
                          onClick={async () => {
                            if (!window.confirm('delete this bill? It will be hidden from normal views. A bill with the same ID can be re-uploaded.')) return
                            await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/bills/${bill.invoice_id}/soft-status?status=deleted&company_id=${user?.company_id ?? ''}`, { method: 'PATCH' })
                            onRefresh()
                            onClose()
                          }}
                          style={{ display:'flex', alignItems:'center', gap:5, padding:'7px 14px', borderRadius:7, border:'1px solid #fca5a5', background:'#fff5f5', color:'#dc2626', fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}
                        >
                          <Trash2 size={13} aria-hidden="true" /> Soft Delete
                        </button>
                      </>
                    )}
                    {(bill.soft_status === 'on_hold' || bill.soft_status === 'deleted') && (
                      <button
                        onClick={async () => {
                          await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/bills/${bill.invoice_id}/soft-status?status=active&company_id=${user?.company_id ?? ''}`, { method: 'PATCH' })
                          onRefresh()
                          onClose()
                        }}
                        style={{ display:'flex', alignItems:'center', gap:5, padding:'7px 14px', borderRadius:7, border:'1px solid #a7f3d0', background:'#f0fdf4', color:'#059669', fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}
                      >
                        <RotateCcw size={14} aria-hidden="true" /> Restore to Active
                      </button>
                    )}
                    <span style={{ fontSize:11, color:'var(--ink-500)', alignSelf:'center' }}>
                      Status: <strong>{bill.soft_status === 'on_hold' ? 'On Hold' : bill.soft_status === 'deleted' ? 'Deleted' : 'Active'}</strong>
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Attachment image preview popover ── */}
      {previewAttach && (() => {
        const isImage = /\.(jpg|jpeg|png|gif|webp|svg)$/i.test(previewAttach.name)
        const isPdf   = /\.pdf$/i.test(previewAttach.name)
        const isExcel = /\.(xlsx?|csv)$/i.test(previewAttach.name)
        const viewUrl = previewAttach.blobUrl ?? previewAttach.url
        const closePreview = () => {
          if (previewAttach.blobUrl) URL.revokeObjectURL(previewAttach.blobUrl)
          setPreviewAttach(null)
          setShowAttachHistory(false)
          setAttachEditHistory([])
        }
        // A PDF is only being looked at: the same full-screen viewer as the bill.
        if (isPdf) {
          return <PdfFullscreen url={previewAttach.blobUrl ?? null} name={previewAttach.name} fromRect={previewAttach.rect} onClose={closePreview} />
        }
        const loadAttachHistory = async () => {
          if (!previewAttach.attachmentId) return
          setAttachHistoryLoading(true)
          try {
            const res = await fetch(`${API_BASE}/api/bills/${bill.invoice_id}/attachments/${previewAttach.attachmentId}/edits`)
            const json = await res.json()
            setAttachEditHistory(json.edits ?? [])
          } catch { /* ignore */ }
          setAttachHistoryLoading(false)
        }
        const toggleAttachHistory = () => {
          setShowAttachHistory(p => { if (!p) loadAttachHistory(); return !p })
        }
        const handleAttachSave = async (file: File, diff: import('../components/ExcelPreview').CellDiff[]) => {
          if (!previewAttach.attachmentId) return
          const form = new FormData()
          form.append('file', file)
          form.append('edited_by', user?.name ?? '')
          form.append('diff', JSON.stringify(diff))
          const res = await fetch(`${API_BASE}/api/bills/${bill.invoice_id}/attachments/${previewAttach.attachmentId}`, {
            method: 'PUT', body: form,
          })
          if (!res.ok) throw new Error('Save failed')
          if (showAttachHistory) loadAttachHistory()
        }
        return (
          <div onClick={closePreview} style={{ position: 'fixed', inset: 0, zIndex: 9999, background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div onClick={e => e.stopPropagation()} style={{ background: '#fff', borderRadius: 12, overflow: 'hidden', width: (isPdf || isExcel) ? '90vw' : 'auto', maxWidth: 1400, maxHeight: '92vh', display: 'flex', flexDirection: 'column', boxShadow: '0 24px 60px rgba(0,0,0,0.45)' }}>
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 18px', borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <FileText size={16} color="#4f46e5" />
                  <span style={{ fontSize: 13, fontWeight: 600, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 400 }}>{previewAttach.name}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  {isExcel && previewAttach.attachmentId && (
                    <button
                      onClick={toggleAttachHistory}
                      style={{ fontSize: 12, padding: '4px 10px', border: '1px solid #e5e7eb', borderRadius: 6, background: showAttachHistory ? '#eff6ff' : '#fff', color: showAttachHistory ? '#1d4ed8' : '#6b7280', cursor: 'pointer', fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 5 }}
                    >
                      <Download size={12} style={{ transform: 'rotate(180deg)' }} /> Edit History
                    </button>
                  )}
                  <a href={previewAttach.url} download={previewAttach.name} style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 12px', borderRadius: 7, border: '1px solid #bfdbfe', background: '#eff6ff', color: '#2563eb', fontSize: 12, fontWeight: 600, textDecoration: 'none' }}>
                    <Download size={12} /> Download
                  </a>
                  <button onClick={closePreview} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6b7280', padding: 4, borderRadius: 6, display: 'flex' }}><X size={16} /></button>
                </div>
              </div>
              {/* Body */}
              <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
                {/* Main content */}
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                  {isPdf && !previewAttach.blobUrl && (
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh', color: 'var(--ink-500)', fontSize: 13 }}>
                      <RefreshCw size={16} style={{ animation: 'bp-spin 1s linear infinite', marginRight: 8 }} /> Loading…
                    </div>
                  )}
                  {isPdf && previewAttach.blobUrl && (
                    <iframe src={`${viewUrl}#toolbar=0&navpanes=0&scrollbar=0`} style={{ flex: 1, width: '100%', minHeight: '78vh', border: 'none' }} title={previewAttach.name} />
                  )}
                  {isImage && (
                    <div style={{ flex: 1, overflow: 'auto', background: '#f3f4f6', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
                      <img src={viewUrl} alt={previewAttach.name} style={{ maxWidth: '100%', maxHeight: '72vh', objectFit: 'contain', borderRadius: 4 }} />
                    </div>
                  )}
                  {isExcel && (
                    previewAttach.blobData
                      ? <Suspense fallback={<div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--ink-500)', fontSize: 13, gap: 8 }}><RefreshCw size={14} style={{ animation: 'bp-spin 1s linear infinite' }} /> Loading…</div>}>
                          <ExcelPreview blobData={previewAttach.blobData} editable={!!previewAttach.attachmentId} onSave={previewAttach.attachmentId ? handleAttachSave : undefined} />
                        </Suspense>
                      : <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1, color: 'var(--ink-500)', fontSize: 13, gap: 8 }}><RefreshCw size={14} style={{ animation: 'bp-spin 1s linear infinite' }} /> Loading…</div>
                  )}
                  {!isPdf && !isImage && !isExcel && (
                    <div style={{ padding: 40, textAlign: 'center', color: '#6b7280', fontSize: 13 }}>
                      Preview not available for this file type.<br />
                      <a href={previewAttach.url} download={previewAttach.name} style={{ color: '#2563eb', marginTop: 8, display: 'inline-block' }}>Download to view</a>
                    </div>
                  )}
                </div>
                {/* Edit history panel */}
                {isExcel && showAttachHistory && (
                  <div style={{ width: 320, borderLeft: '1px solid #e5e7eb', background: '#fafafa', display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
                    <div style={{ padding: '12px 16px', fontWeight: 600, fontSize: 13, color: '#374151', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>Edit History</div>
                    <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
                      {attachHistoryLoading ? (
                        <div style={{ color: 'var(--ink-500)', fontSize: 12, padding: 8, display: 'flex', alignItems: 'center', gap: 6 }}><RefreshCw size={12} style={{ animation: 'bp-spin 0.8s linear infinite' }} /> Loading…</div>
                      ) : attachEditHistory.length === 0 ? (
                        <div style={{ color: 'var(--ink-500)', fontSize: 12, padding: 8 }}>No edits recorded yet.</div>
                      ) : attachEditHistory.map(entry => (
                        <div key={entry.id} style={{ marginBottom: 14, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'hidden' }}>
                          <div style={{ padding: '8px 12px', borderBottom: '1px solid #f3f4f6', background: '#f9fafb' }}>
                            <div style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>{entry.edited_by || 'Unknown'}</div>
                            <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>{entry.edited_at ? new Date(entry.edited_at).toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}</div>
                          </div>
                          <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
                            {entry.diff.length === 0 ? (
                              <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>No cell changes recorded</div>
                            ) : entry.diff.map((d, i) => (
                              <div key={i} style={{ fontSize: 11 }}>
                                <span style={{ color: '#6b7280' }}>{d.sheet} · Row {d.row} · </span>
                                <span style={{ fontWeight: 600, color: '#374151' }}>{d.header}</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginTop: 2 }}>
                                  <span style={{ background: '#fee2e2', color: '#b91c1c', padding: '1px 6px', borderRadius: 4, textDecoration: 'line-through', maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.oldValue}>{d.oldValue || '(empty)'}</span>
                                  <span style={{ color: 'var(--ink-500)' }}>→</span>
                                  <span style={{ background: '#dcfce7', color: '#15803d', padding: '1px 6px', borderRadius: 4, maxWidth: 100, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.newValue}>{d.newValue || '(empty)'}</span>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}

// ── Payment priority ─────────────────────────────────────────────────────────
// Moved to utils/payPriority.ts so the Payment Hub can share the exact same
// calculation instead of running its own. Re-exported here so every existing
// reference in this file keeps working unchanged.
import { PRIORITY_META, DEFAULT_PRIORITY_LABELS, priorityMeta, getPayPriority, type PayPriority } from '../utils/payPriority'
export type { PayPriority }

/**
 * Every column, as something the user can filter on.
 *
 * The order here is the order of the "Add filter" list, so it follows the table
 * left to right; `value` returns the same fact the cell shows, and `format`
 * gives an enum option the label the cell prints — a checkbox reading
 * `PENDING_L1` next to a badge reading "Pending: Finance" is two names for one
 * thing. Columns that are only buttons (Doc, Tally) carry no filter.
 */
const BILL_FILTER_FIELDS: FilterField<Bill>[] = [
  {
    key: 'priority', label: 'Priority', kind: 'enum',
    value: b => getPayPriority(b) ?? '',
    format: p => (p in PRIORITY_META ? PRIORITY_META[p as NonNullable<PayPriority>].label : p),
  },
  { key: 'vendor_name', label: 'Vendor', kind: 'text', value: b => b.vendor_name },
  { key: 'vendor_gstn', label: 'Vendor GSTIN', kind: 'text', value: b => b.vendor_gstn },
  { key: 'total_amount', label: 'Bill Amount', kind: 'number', value: b => b.total_amount },
  { key: 'uploaded_by_name', label: 'Uploaded By', kind: 'enum', value: b => b.uploaded_by_name },
  {
    key: 'status', label: 'Status', kind: 'enum',
    value: b => b.status,
    format: s => {
      const k = s.toUpperCase()
      if (STATUS_STYLE[k]) return STATUS_STYLE[k].label
      // PENDING_L1 → "Pending L1": the same reading as the badge on the row.
      return k.replace('PENDING_', 'Pending ').replace(/_/g, ' ')
    },
  },
  { key: 'invoice_date', label: 'Invoice Date', kind: 'date', value: b => b.invoice_date },
  { key: 'due_date', label: 'Due Date', kind: 'date', value: b => b.due_date },
  { key: 'id', label: 'ID', kind: 'text', value: b => b.id },
  { key: 'category', label: 'Category', kind: 'enum', value: b => b.category },
  { key: 'department', label: 'Department', kind: 'enum', value: b => b.department },
  { key: 'cost_center_name', label: 'Cost Center', kind: 'enum', value: b => b.cost_center_name },
  {
    key: 'tds_status', label: 'TDS', kind: 'enum',
    value: b => b.tds_status?.toUpperCase() ?? '',
    format: s => TDS_STATUS_META[s]?.label ?? s,
  },
  { key: 'net_payable', label: 'Net Payable', kind: 'number', value: b => b.net_payable },
  {
    key: 'payment_status', label: 'Payment', kind: 'enum',
    value: b => b.payment_status ?? 'unpaid',
    format: s => s.charAt(0).toUpperCase() + s.slice(1),
  },
  { key: 'currency_code', label: 'Currency', kind: 'enum', value: b => b.currency_code },
]

const TDS_STATUS_META: Record<string, { bg: string; color: string; label: string }> = {
  CALCULATED: { bg: '#d1fae5', color: '#059669', label: 'TDS' },
  NA:         { bg: '#f3f4f6', color: 'var(--ink-500)', label: 'N/A' },
  PENDING:    { bg: '#fef3c7', color: '#d97706', label: '...'  },
  ERROR:      { bg: '#fee2e2', color: '#dc2626', label: 'ERR' },
}

function TdsBadge({ bill }: { bill: Bill }) {
  const s = bill.tds_status?.toUpperCase()
  if (!s || s === 'NA') {
    return <span style={{ color: '#d1d5db', fontSize: 12 }}>—</span>
  }
  const meta = TDS_STATUS_META[s] ?? { bg: '#f3f4f6', color: '#6b7280', label: s }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{
        display: 'inline-block', padding: '1px 7px', borderRadius: 20,
        fontSize: 11, fontWeight: 700,
        background: meta.bg, color: meta.color,
      }}>
        {meta.label}
        {s === 'CALCULATED' && bill.tds_section_code ? ` · ${bill.tds_section_code}` : ''}
      </span>
      {s === 'CALCULATED' && bill.tds_amount != null && (
        <span style={{ fontSize: 11, color: '#374151', fontVariantNumeric: 'tabular-nums', paddingLeft: 2 }}>
          ₹{Math.round(bill.tds_amount).toLocaleString('en-IN')}
          {bill.tds_rate ? ` @ ${bill.tds_rate}%` : ''}
        </span>
      )}
    </div>
  )
}

function TdsBlockWithFlag({ detail, billId, user, onDetailUpdate }: {
  detail: BillDetail; billId: string; user: any; onDetailUpdate: (u: Partial<BillDetail>) => void
}) {
  const s = detail.tds_status?.toUpperCase()
  // Read the licence here rather than threading a prop down from BillModal.
  const novaOn = useService('nova_billing')
  const canSetTds = user?.role === 'admin' || user?.role === 'finance_controller'
  const isNa = s === 'NA'
  const isReview = s === 'REVIEW_REQUIRED'
  const isCalculated = s === 'CALCULATED' || s === 'DEDUCTED'
  const needsSetup = (isNa && !detail.vendor_category_id) || isReview
  const hasCategory = !!detail.vendor_category_id

  const [showSetup, setShowSetup] = useState(false)
  const [step, setStep] = useState<1 | 2>(1)
  const [categories, setCategories] = useState<VendorCategory[]>([])
  const [sections, setSections] = useState<TdsSection[]>([])
  const [dataLoading, setDataLoading] = useState(false)
  const [selectedCat, setSelectedCat] = useState('')
  const [saving, setSaving] = useState(false)
  const [livePreview, setLivePreview] = useState<any>(null)

  // Step 2 override fields
  const [ovMode, setOvMode] = useState('cumulative')
  const [ovSection, setOvSection] = useState('')
  const [ovRate, setOvRate] = useState('')
  const [ovCert, setOvCert] = useState('')
  const [ovFrom, setOvFrom] = useState('')
  const [ovTo, setOvTo] = useState('')
  const [ovReason, setOvReason] = useState('')
  // Override is created whenever user saves from Step 2

  const openSetup = async () => {
    setShowSetup(true)
    setStep(1)
    setDataLoading(true)
    try {
      const [cats, secs] = await Promise.all([
        fetchVendorCategories(user?.company_id),
        fetchTdsSections(user?.company_id),
      ])
      setCategories(cats.filter((c: any) => c.is_active !== false))
      setSections(secs.filter((s: any) => s.is_active))
    } catch {} finally { setDataLoading(false) }
  }

  const handleCatSelect = async (catId: string) => {
    setSelectedCat(catId)
    if (!catId) { setLivePreview(null); return }
    try {
      const preview = await previewTds({
        company_id: user?.company_id,
        vendor_name: detail.vendor?.name ?? undefined,
        vendor_gstin: detail.vendor?.gstn ?? undefined,
        subtotal: detail.subtotal ?? undefined,
        tax_amount: detail.tax_amount ?? undefined,
        total_amount: detail.total_amount ?? undefined,
        bill_date: detail.invoice_date ?? undefined,
      })
      setLivePreview(preview)
    } catch { setLivePreview(null) }
  }

  const [saveErr, setSaveErr] = useState('')

  const handleSave = async () => {
    if (!selectedCat) { setSaveErr('Select a TDS category first'); return }
    if (!detail.supplier_id) { setSaveErr('Vendor not linked — cannot save TDS settings'); return }
    setSaving(true)
    setSaveErr('')
    try {
      // 1. Assign category
      await updateVendor(detail.supplier_id, { vendor_category_id: selectedCat }, user?.company_id)

      // 2. Create override if on Step 2 (user configured override settings)
      if (step === 2) {
        await createVendorOverride({
          supplier_id: detail.supplier_id!,
          tds_section_id: ovSection || undefined,
          tds_mode: ovMode,
          override_rate: ovRate ? parseFloat(ovRate) : undefined,
          certificate_no: ovCert || undefined,
          effective_from: ovFrom || undefined,
          valid_to: ovTo || undefined,
          mode_reason: ovReason || undefined,
        })
      }

      // 3. Trigger recalc
      await axios.post(`/api/bills/${billId}/recalc-tds`, {
        company_id: user?.company_id,
      })

      // 4. Get fresh preview for immediate UI feedback (pipeline may not have run yet)
      const preview = await previewTds({
        company_id: user?.company_id,
        vendor_name: detail.vendor?.name ?? undefined,
        vendor_gstin: detail.vendor?.gstn ?? undefined,
        subtotal: detail.subtotal ?? undefined,
        tax_amount: detail.tax_amount ?? undefined,
        total_amount: detail.total_amount ?? undefined,
        bill_date: detail.invoice_date ?? undefined,
      })

      invalidateDetailCache(billId)

      // Use preview for immediate display; pipeline will persist the real values
      if (preview.status === 'CALCULATED') {
        onDetailUpdate({
          tds_status: 'CALCULATED',
          tds_section_code: preview.section_code ?? preview.return_code,
          tds_rate: preview.rate,
          tds_amount: preview.tds_amount,
          tds_base_amount: preview.base_amount,
          net_payable: preview.net_payable,
          vendor_category_id: selectedCat,
        })
      } else {
        onDetailUpdate({
          tds_status: preview.status || 'PENDING',
          vendor_category_id: selectedCat,
        })
      }
      setShowSetup(false)
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || 'Save failed'
      setSaveErr(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally { setSaving(false) }
  }

  const closeSetup = () => {
    setShowSetup(false)
    setStep(1)
    setSelectedCat('')
    setLivePreview(null)
    setOvMode('cumulative')
    setOvSection('')
    setOvRate('')
    setOvCert('')
    setOvFrom('')
    setOvTo('')
    setOvReason('')
  }

  const pills: Record<string, { bg: string; color: string; label: string }> = {
    CALCULATED:      { bg: '#d1fae5', color: '#059669', label: 'TDS applied' },
    DEDUCTED:        { bg: '#d1fae5', color: '#059669', label: 'TDS deducted' },
    NA:              { bg: '#f3f4f6', color: 'var(--ink-500)', label: 'TDS not applicable' },
    PENDING:         { bg: '#fef3c7', color: '#d97706', label: 'TDS pending' },
    REVIEW_REQUIRED: { bg: '#fee2e2', color: '#dc2626', label: 'Review required' },
  }
  const pill = pills[s ?? ''] ?? { bg: '#f3f4f6', color: '#6b7280', label: s || '' }

  const modeOpts = [
    { value: 'cumulative', label: 'Cumulative (default)', hint: 'Deduct after FY total crosses threshold' },
    { value: 'always_deduct', label: 'Always deduct', hint: 'From first bill — vendor will cross threshold' },
    { value: 'per_bill_threshold', label: 'Per-bill threshold', hint: 'Only if this bill crosses the threshold' },
  ]

  return (
    <>
      {/* Status pill row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        {(needsSetup || isReview) && canSetTds ? (
          <button onClick={openSetup} disabled={showSetup}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '4px 12px', borderRadius: 20, fontSize: 11, fontWeight: 700,
              background: showSetup ? '#fef3c7' : isReview ? '#fee2e2' : '#f3f4f6',
              color: showSetup ? '#d97706' : isReview ? '#dc2626' : 'var(--ink-500)',
              border: `1px dashed ${showSetup ? '#d97706' : isReview ? '#fca5a5' : '#d1d5db'}`,
              cursor: showSetup ? 'default' : 'pointer', transition: 'all 0.15s',
            }}>
            {isReview ? 'Review required — click to configure TDS' : 'TDS not applicable — click to configure TDS'}
          </button>
        ) : (
          <>
            <span style={{ padding: '3px 10px', borderRadius: 20, fontSize: 11, fontWeight: 700, background: pill.bg, color: pill.color }}>
              {pill.label}
            </span>
            {canSetTds && !showSetup && (
              <button onClick={openSetup}
                style={{ fontSize: 11, color: '#6b7280', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', padding: 0 }}>
                Reconfigure
              </button>
            )}
          </>
        )}
        {detail.tds_section_code && (
          <span style={{ fontSize: 12, color: '#6b7280' }}>
            {detail.tds_section_code}
            {detail.tds_rate != null && ` @ ${detail.tds_rate}%`}
          </span>
        )}
      </div>


      {/* ── Full TDS Setup Panel ── */}
      {showSetup && (
        <div style={{ background: '#fffbeb', border: '1px solid #f59e0b', borderRadius: 12, padding: '16px 18px', marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#92400e' }}>
              Configure TDS for {detail.vendor?.name || 'this vendor'}
            </div>
            <button onClick={closeSetup} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-500)' }}>
              <X size={16} />
            </button>
          </div>

          {/* Step indicators */}
          <div style={{ display: 'flex', gap: 0, marginBottom: 14 }}>
            <button onClick={() => setStep(1)} style={{
              flex: 1, padding: '6px 0', fontSize: 12, fontWeight: 600, cursor: 'pointer',
              background: step === 1 ? '#fff' : 'transparent', color: step === 1 ? '#92400e' : 'var(--ink-500)',
              border: 'none', borderBottom: step === 1 ? '2px solid #f59e0b' : '2px solid #e5e7eb',
            }}>1. TDS Category</button>
            <button onClick={() => selectedCat ? setStep(2) : null} style={{
              flex: 1, padding: '6px 0', fontSize: 12, fontWeight: 600, cursor: selectedCat ? 'pointer' : 'default',
              background: step === 2 ? '#fff' : 'transparent', color: step === 2 ? '#92400e' : 'var(--ink-500)',
              border: 'none', borderBottom: step === 2 ? '2px solid #f59e0b' : '2px solid #e5e7eb',
              opacity: selectedCat ? 1 : 0.5,
            }}>2. Override Settings (optional)</button>
          </div>

          {dataLoading ? (
            <div style={{ fontSize: 12, color: 'var(--ink-500)', padding: '12px 0' }}>Loading...</div>
          ) : step === 1 ? (
            /* ── Step 1: Category ── */
            <>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>TDS CATEGORY</div>
              <select style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', background: '#fff', marginBottom: 10 }}
                value={selectedCat} onChange={e => handleCatSelect(e.target.value)}>
                <option value="">-- Select TDS category --</option>
                {categories.map(c => (
                  <option key={c.category_id} value={c.category_id}>
                    {c.name}{c.tds_rate != null ? ` (${c.tds_rate}% - ${c.section_label || c.section_code || ''})` : ''}
                  </option>
                ))}
              </select>

              {livePreview && livePreview.status === 'CALCULATED' && (
                <div style={{ background: '#f0fdf4', border: '1px solid #a7f3d0', borderRadius: 8, padding: '8px 12px', marginBottom: 10 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#059669', marginBottom: 4 }}>Live TDS Preview</div>
                  <div style={{ display: 'flex', gap: 16, fontSize: 12, color: '#374151', flexWrap: 'wrap' }}>
                    <span>Base: ₹{Math.round(livePreview.base_amount).toLocaleString('en-IN')}</span>
                    <span>Rate: {livePreview.rate}%</span>
                    <span style={{ fontWeight: 700, color: '#dc2626' }}>TDS: ₹{Math.round(livePreview.tds_amount).toLocaleString('en-IN')}</span>
                    <span style={{ fontWeight: 700, color: '#059669' }}>Net: ₹{Math.round(livePreview.net_payable).toLocaleString('en-IN')}</span>
                  </div>
                </div>
              )}
              {livePreview && livePreview.status === 'NA' && (
                <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 10 }}>
                  {livePreview.reason || 'Below threshold — TDS not applicable yet.'}
                </div>
              )}

              {saveErr && <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 8, padding: '6px 10px', background: '#fee2e2', borderRadius: 6 }}>{saveErr}</div>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={handleSave} disabled={!selectedCat || saving}
                  style={{ padding: '8px 18px', borderRadius: 8, background: selectedCat ? '#059669' : '#d1d5db', color: '#fff', border: 'none', fontSize: 13, fontWeight: 600, cursor: selectedCat ? 'pointer' : 'not-allowed', fontFamily: 'inherit' }}>
                  {saving ? 'Saving...' : 'Assign & Calculate TDS'}
                </button>
                {selectedCat && (
                  <button onClick={() => setStep(2)}
                    style={{ padding: '8px 14px', borderRadius: 8, background: '#fff', color: '#374151', border: '1px solid #d1d5db', fontSize: 12, fontWeight: 500, cursor: 'pointer', fontFamily: 'inherit' }}>
                    Configure override settings →
                  </button>
                )}
                <button onClick={closeSetup}
                  style={{ padding: '8px 12px', borderRadius: 8, background: '#fff', color: '#6b7280', border: '1px solid #e5e7eb', fontSize: 12, cursor: 'pointer', fontFamily: 'inherit' }}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            /* ── Step 2: Override Settings ── */
            <>
              {/* TDS Section override */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>TDS SECTION (override category default)</div>
                <select style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', background: '#fff' }}
                  value={ovSection} onChange={e => setOvSection(e.target.value)}>
                  <option value="">— Use category default —</option>
                  {sections.map(sec => (
                    <option key={sec.tds_section_id} value={sec.tds_section_id}>
                      {sec.old_section_label || sec.return_code} · {sec.nature_of_payment} ({sec.default_rate}%)
                    </option>
                  ))}
                </select>
              </div>

              {/* Deduction Mode */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 6, letterSpacing: 0.5 }}>DEDUCTION MODE</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {modeOpts.map(m => {
                    const sel = ovMode === m.value
                    return (
                      <label key={m.value} style={{
                        display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 12px', borderRadius: 8,
                        border: `1.5px solid ${sel ? '#3b82f6' : '#e5e7eb'}`,
                        background: sel ? '#eff6ff' : '#fff', cursor: 'pointer',
                      }}>
                        <input type="radio" name="bill_tds_mode" checked={sel} onChange={() => setOvMode(m.value)} style={{ marginTop: 2 }} />
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{m.label}</div>
                          <div style={{ fontSize: 11, color: '#6b7280' }}>{m.hint}</div>
                        </div>
                      </label>
                    )
                  })}
                </div>
              </div>

              {/* Rate + Certificate */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>RATE OVERRIDE %</div>
                  <input type="number" step="0.1" placeholder="Section default"
                    style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }}
                    value={ovRate} onChange={e => setOvRate(e.target.value)} />
                </div>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>CERTIFICATE NO.</div>
                  <input placeholder="Sec 197"
                    style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }}
                    value={ovCert} onChange={e => setOvCert(e.target.value)} />
                </div>
              </div>

              {/* Dates */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>EFFECTIVE FROM</div>
                  <input type="date" style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }}
                    value={ovFrom} onChange={e => setOvFrom(e.target.value)} />
                </div>
                <div>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>VALID TO (OPTIONAL)</div>
                  <input type="date" style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }}
                    value={ovTo} onChange={e => setOvTo(e.target.value)} />
                </div>
              </div>

              {/* Reason */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>REASON / NOTE</div>
                <input placeholder="e.g. Annual contract on record"
                  style={{ width: '100%', padding: '7px 10px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }}
                  value={ovReason} onChange={e => setOvReason(e.target.value)} />
              </div>

              {saveErr && <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 8, padding: '6px 10px', background: '#fee2e2', borderRadius: 6 }}>{saveErr}</div>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={handleSave} disabled={saving}
                  style={{ padding: '8px 18px', borderRadius: 8, background: '#059669', color: '#fff', border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
                  {saving ? 'Saving...' : 'Save Category + Override & Calculate TDS'}
                </button>
                <button onClick={() => setStep(1)}
                  style={{ padding: '8px 12px', borderRadius: 8, background: '#fff', color: '#6b7280', border: '1px solid #e5e7eb', fontSize: 12, cursor: 'pointer', fontFamily: 'inherit' }}>
                  ← Back
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Nova billing verification flag (renders nothing if this isn't a Nova-vendor invoice) */}
      {novaOn && <BillVerificationCard data={detail} />}

      {/* TDS breakdown when calculated */}
      {s !== 'NA' && s !== undefined && (
        <TdsBreakdown
          collapsible
          data={{
            status: detail.tds_status,
            section_code: detail.tds_section_code,
            rate: detail.tds_rate,
            base_amount: detail.tds_base_amount,
            tax_amount: detail.tax_amount,
            total_amount: detail.total_amount,
            tds_amount: detail.tds_amount,
            net_payable: detail.net_payable,
          }}
        />
      )}

      {/* Manual TDS override (admin / finance_controller) */}
      {canSetTds && (s === 'CALCULATED' || s === 'DEDUCTED' || s === 'REVIEW_REQUIRED') && (
        <TdsManualOverride
          billId={billId}
          currentAmount={detail.tds_amount}
          currentRate={detail.tds_rate}
          currentBase={detail.tds_base_amount}
          sectionCode={detail.tds_section_code}
          totalAmount={detail.total_amount}
          companyId={user?.company_id}
          onSaved={(newAmt) => {
            onDetailUpdate({ tds_amount: newAmt, tds_status: 'DEDUCTED', net_payable: (detail.total_amount ?? 0) - newAmt })
          }}
        />
      )}
    </>
  )
}

function TdsManualOverride({ billId, currentAmount, currentRate, currentBase, sectionCode, totalAmount, companyId, onSaved }: {
  billId: string; currentAmount: number | null; currentRate: number | null; currentBase: number | null
  sectionCode: string | null; totalAmount: number | null; companyId?: string; onSaved: (newAmt: number) => void
}) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'amount' | 'rate'>('amount')
  const [amt, setAmt] = useState(String(Math.round(currentAmount ?? 0)))
  const [rate, setRate] = useState(String(currentRate ?? ''))
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const base = currentBase ?? totalAmount ?? 0
  const computedFromRate = rate ? Math.round(base * parseFloat(rate) / 100) : null
  const finalAmt = mode === 'rate' && computedFromRate != null ? computedFromRate : (parseFloat(amt) || 0)
  const netPayable = (totalAmount ?? 0) - finalAmt

  if (!open) {
    return (
      <button onClick={() => { setOpen(true); setAmt(String(Math.round(currentAmount ?? 0))); setRate(String(currentRate ?? '')) }}
        style={{
          marginTop: 10, display: 'flex', alignItems: 'center', gap: 6,
          padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
          background: '#fff', color: '#4f46e5', border: '1px solid #c7d2fe',
          cursor: 'pointer', fontFamily: 'inherit', transition: 'all 0.15s',
        }}>
        <Pencil size={12} /> Modify TDS on this bill
      </button>
    )
  }

  return (
    <div style={{ marginTop: 10, background: '#eef2ff', border: '1px solid #c7d2fe', borderRadius: 10, padding: '14px 16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#3730a3' }}>Modify TDS Deduction</div>
        <button onClick={() => { setOpen(false); setErr('') }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-500)', padding: 2 }}>
          <X size={14} />
        </button>
      </div>

      {sectionCode && (
        <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 10 }}>
          Current: {sectionCode} @ {currentRate ?? '—'}% on base ₹{Math.round(base).toLocaleString('en-IN')} = ₹{Math.round(currentAmount ?? 0).toLocaleString('en-IN')}
        </div>
      )}

      {/* Mode toggle */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 12, borderRadius: 8, overflow: 'hidden', border: '1px solid #c7d2fe' }}>
        <button onClick={() => setMode('amount')} style={{
          flex: 1, padding: '6px 0', fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
          background: mode === 'amount' ? '#4f46e5' : '#fff', color: mode === 'amount' ? '#fff' : '#6b7280',
        }}>Enter amount</button>
        <button onClick={() => setMode('rate')} style={{
          flex: 1, padding: '6px 0', fontSize: 12, fontWeight: 600, cursor: 'pointer', border: 'none',
          background: mode === 'rate' ? '#4f46e5' : '#fff', color: mode === 'rate' ? '#fff' : '#6b7280',
        }}>Enter rate %</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
        {mode === 'amount' ? (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#4338ca', marginBottom: 4 }}>TDS AMOUNT (₹)</div>
            <input type="number" value={amt} onChange={e => setAmt(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid #c7d2fe', fontSize: 14, fontWeight: 600, fontFamily: 'inherit', boxSizing: 'border-box' }} />
          </div>
        ) : (
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#4338ca', marginBottom: 4 }}>TDS RATE (%)</div>
            <input type="number" step="0.1" value={rate} onChange={e => setRate(e.target.value)}
              placeholder={String(currentRate ?? '')}
              style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid #c7d2fe', fontSize: 14, fontWeight: 600, fontFamily: 'inherit', boxSizing: 'border-box' }} />
          </div>
        )}
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: '#4338ca', marginBottom: 4 }}>REASON</div>
          <input value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. Sec 197 certificate"
            style={{ width: '100%', padding: '8px 10px', borderRadius: 8, border: '1px solid #c7d2fe', fontSize: 13, fontFamily: 'inherit', boxSizing: 'border-box' }} />
        </div>
      </div>

      {/* Live preview */}
      <div style={{ background: '#fff', borderRadius: 8, padding: '8px 12px', marginBottom: 10, display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
        <span style={{ color: '#6b7280' }}>Base: ₹{Math.round(base).toLocaleString('en-IN')}</span>
        <span style={{ color: '#dc2626', fontWeight: 600 }}>TDS: ₹{finalAmt.toLocaleString('en-IN')}</span>
        <span style={{ color: '#059669', fontWeight: 700 }}>Net: ₹{Math.round(netPayable).toLocaleString('en-IN')}</span>
      </div>

      {err && <div style={{ color: '#dc2626', fontSize: 12, marginBottom: 8 }}>{err}</div>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button disabled={saving} onClick={async () => {
          setSaving(true); setErr('')
          try {
            await axios.post(`/api/bills/${billId}/tds-override`, { tds_amount: finalAmt, reason, company_id: companyId })
            onSaved(finalAmt)
            setOpen(false)
          } catch (e: any) {
            setErr(e?.response?.data?.detail || 'Save failed')
          } finally { setSaving(false) }
        }} style={{
          flex: 1, padding: '8px 0', borderRadius: 8, background: '#4f46e5', color: '#fff',
          border: 'none', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
        }}>
          {saving ? 'Saving...' : 'Save Override'}
        </button>
        <button onClick={() => { setOpen(false); setErr('') }}
          style={{ padding: '8px 14px', borderRadius: 8, background: '#fff', color: '#6b7280', border: '1px solid #e5e7eb', fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function PriorityBadge({ bill, isAdmin, userName, userRole, companyId, labels = DEFAULT_PRIORITY_LABELS, onLabelsSaved, onRefresh }: {
  bill: Bill; isAdmin?: boolean; userName?: string; userRole?: string; companyId?: string; labels?: PriorityLabels; onLabelsSaved?: (labels: PriorityLabels) => void; onRefresh?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [localPriority, setLocalPriority] = useState<string | null>(bill.manual_priority ?? null)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => { setLocalPriority(bill.manual_priority ?? null) }, [bill.manual_priority])

  // Compute priority using localPriority (optimistic) instead of bill.manual_priority
  const effectiveBill = { ...bill, manual_priority: localPriority }
  const p = getPayPriority(effectiveBill as Bill)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handlePick = (val: string) => {
    setOpen(false)
    const prev = localPriority || 'auto'
    const next = val || 'auto'
    setLocalPriority(val || null)  // instant UI update — no waiting
    // Invalidate caches immediately so a page refresh fetches fresh data
    invalidateBillsCache(companyId)
    invalidateDetailCache(bill.invoice_id)
    // Save in background, don't block the UI
    updateBill(bill.invoice_id, { manual_priority: val || null, actor_name: userName ?? '', actor_role: userRole ?? '' })
      .then(() => {
        billAction(bill.invoice_id, {
          action: 'priority_change', actor_role: userRole ?? 'admin', actor_name: userName ?? 'Admin',
          comment: `Priority changed from ${prev.toUpperCase()} to ${next.toUpperCase()}`,
        }).catch(() => {})
      })
      .catch(() => setLocalPriority(bill.manual_priority ?? null))  // rollback on error
  }

  const today = new Date(); today.setHours(0, 0, 0, 0)
  const days  = bill.due_date ? Math.round((new Date(bill.due_date).getTime() - today.getTime()) / 86400000) : null
  const isManual = !!localPriority && localPriority in PRIORITY_META && bill.payment_status !== 'paid'

  const isPaid = bill.payment_status === 'paid' || p === 'done'

  if (!p) {
    if (!isAdmin || isPaid) return <span style={{ color: '#d1d5db' }}>—</span>
    return (
      <div ref={ref} style={{ position: 'relative' }}>
        <button onClick={() => setOpen(o => !o)} style={{ background: 'none', border: '1px dashed #d1d5db', borderRadius: 6, padding: '2px 8px', cursor: 'pointer', color: 'var(--ink-500)', fontSize: 11 }}>
          Set
        </button>
        {open && <PriorityPicker current="" onPick={handlePick} labels={labels} isAdmin={isAdmin} onLabelsSaved={onLabelsSaved} />}
      </div>
    )
  }

  const m = priorityMeta(labels)[p]
  const canEdit = isAdmin && !isPaid
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
        <span
          onClick={() => canEdit && setOpen(o => !o)}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, justifyContent: 'center',
            fontSize: 11, fontWeight: 800, padding: '3px 10px', borderRadius: 8,
            background: m.bg, color: m.color, border: `1px solid ${m.border}`,
            whiteSpace: 'nowrap', letterSpacing: '0.02em', minWidth: 32,
            cursor: canEdit ? 'pointer' : 'default',
            opacity: 1,
          }}
        >
          {isManual && <span title="Admin override" style={{ fontSize: 10 }}>📌</span>}
          {m.label}
          {canEdit && <span style={{ fontSize: 9, marginLeft: 1 }}>▾</span>}
        </span>
        {open && <PriorityPicker current={localPriority ?? ''} onPick={handlePick} labels={labels} isAdmin={isAdmin} onLabelsSaved={onLabelsSaved} />}
      </div>
      {days !== null && p !== 'done' && (
        <span style={{ fontSize: 10, color: days <= 0 ? '#dc2626' : days <= 3 ? '#2563eb' : 'var(--ink-500)', fontWeight: 600, paddingLeft: 2 }}>
          {days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? 'Due today' : `${days}d left`}
        </span>
      )}
    </div>
  )
}

function PriorityPicker({ current, onPick, labels, isAdmin, onLabelsSaved }: { current: string; onPick: (v: string) => void; labels: PriorityLabels; isAdmin?: boolean; onLabelsSaved?: (labels: PriorityLabels) => void }) {
  const [namingOpen, setNamingOpen] = useState(false)
  const meta = priorityMeta(labels)
  const options: { val: string; label: string; color: string; bg: string; border: string }[] = [
    { val: '', label: 'Auto', color: '#6b7280', bg: '#f9fafb', border: '#e5e7eb' },
    ...(['p0','p1','p2','p3'] as const).map(v => ({ val: v, ...meta[v] })),
  ]
  return (
    <div style={{
      position: 'absolute', top: '110%', left: 0, zIndex: 200,
      background: '#fff', borderRadius: 8, boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
      border: '1px solid #e5e7eb', padding: 6, minWidth: 100,
    }}>
      {options.map(o => (
        <button key={o.val} onClick={() => onPick(o.val)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8, width: '100%',
            padding: '5px 8px', borderRadius: 5, border: 'none', cursor: 'pointer',
            background: current === o.val ? o.bg : 'transparent',
            fontWeight: current === o.val ? 700 : 500, fontSize: 12, color: o.color,
          }}
        >
          <span style={{ width: 28, flexShrink: 0, textAlign: 'center', fontWeight: 800, fontSize: 11, padding: '2px 6px', borderRadius: 5, background: o.bg, border: `1px solid ${o.border}`, color: o.color }}>
            {o.val ? o.val.toUpperCase() : '~'}
          </span>
          {o.val ? o.label : 'Auto (calculated)'}
          {current === o.val && <span style={{ marginLeft: 'auto', fontSize: 10 }}>✓</span>}
        </button>
      ))}
      {isAdmin && <>
        <div style={{ height: 1, background: '#e5e7eb', margin: '5px 2px' }} />
        <button onClick={() => setNamingOpen(true)} style={{ width: '100%', padding: '6px 8px', border: 0, borderRadius: 5, background: 'transparent', color: '#4f46e5', cursor: 'pointer', fontSize: 11, fontWeight: 700, textAlign: 'left' }}>
          Change priority names
        </button>
      </>}
      {namingOpen && <PriorityNamingModal labels={labels} onClose={() => setNamingOpen(false)} onSaved={next => { onLabelsSaved?.(next); setNamingOpen(false) }} />}
    </div>
  )
}

function PriorityNamingModal({ labels, onClose, onSaved }: { labels: PriorityLabels; onClose: () => void; onSaved: (labels: PriorityLabels) => void }) {
  // Keep inputs empty initially so the suggested name reads as a true, faint
  // placeholder rather than a value the admin must delete. Empty fields retain
  // their existing tenant label on save.
  const [draft, setDraft] = useState<PriorityLabels>({ p0: '', p1: '', p2: '', p3: '' })
  const inputRefs = useRef<Partial<Record<keyof PriorityLabels, HTMLInputElement>>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const suggestions: Record<keyof PriorityLabels, string> = { p0: 'Urgent', p1: 'High', p2: 'Medium', p3: 'Low' }
  const save = async () => {
    setSaving(true); setError('')
    const next = (Object.keys(labels) as (keyof PriorityLabels)[]).reduce((result, key) => {
      result[key] = draft[key].trim() || labels[key]
      return result
    }, {} as PriorityLabels)
    try { onSaved(await updatePriorityLabels(next)) }
    catch (e: any) { setError(e?.response?.data?.detail ?? 'Could not save priority names') }
    finally { setSaving(false) }
  }
  return (
    <div role="dialog" aria-modal="true" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }} style={{ position: 'fixed', inset: 0, zIndex: 500, display: 'grid', placeItems: 'center', background: 'rgba(15,23,42,.38)', animation: 'priorityFadeIn .18s ease-out' }}>
      <div style={{ width: 'min(420px, calc(100vw - 32px))', background: '#fff', borderRadius: 16, boxShadow: '0 24px 70px rgba(15,23,42,.25)', padding: 22, animation: 'priorityCardIn .22s cubic-bezier(.2,.8,.2,1)' }}>
        <div style={{ fontSize: 17, fontWeight: 800, color: '#172033' }}>Change priority names</div>
        <p style={{ fontSize: 12, lineHeight: 1.5, color: '#64748b', margin: '5px 0 16px' }}> Priority order and auto-calculation stay the same.</p>
        {(['p0', 'p1', 'p2', 'p3'] as const).map(key => (
          <label key={key} style={{ display: 'grid', gridTemplateColumns: '42px 1fr', gap: 10, alignItems: 'center', marginBottom: 10 }}>
            <span style={{ fontSize: 12, fontWeight: 800, color: PRIORITY_META[key].color }}>{key.toUpperCase()}</span>
            <span>
              <input
                ref={node => { inputRefs.current[key] = node ?? undefined }}
                value={draft[key]}
                maxLength={48}
                onFocus={() => {
                  if (draft[key]) return
                  // A click turns the faint placeholder into an actual value;
                  // selecting it lets the next keystroke replace it naturally.
                  setDraft(current => ({ ...current, [key]: suggestions[key] }))
                  window.requestAnimationFrame(() => inputRefs.current[key]?.select())
                }}
                onChange={e => setDraft({ ...draft, [key]: e.target.value })}
                placeholder={suggestions[key]}
                style={{ width: '100%', boxSizing: 'border-box', border: '1px solid #dbe3ef', borderRadius: 8, padding: '8px 9px', font: 'inherit', fontSize: 13 }}
              />
            </span>
          </label>
        ))}
        {error && <div style={{ color: '#b91c1c', fontSize: 12, marginTop: 4 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
          <button onClick={onClose} disabled={saving} style={{ padding: '8px 12px', borderRadius: 8, border: '1px solid #dbe3ef', background: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>Cancel</button>
          <button onClick={save} disabled={saving} style={{ padding: '8px 12px', borderRadius: 8, border: 0, background: '#4f46e5', color: '#fff', fontWeight: 700, fontSize: 12, cursor: 'pointer' }}>{saving ? 'Saving…' : 'Save names'}</button>
        </div>
      </div>
    </div>
  )
}

// ── Payment status cell with popover ─────────────────────────────────────────
const PAY_META = {
  unpaid:  { label: 'Unpaid',   bg: '#fef2f2', color: '#dc2626', border: '#fecaca', dot: '#ef4444' },
  partial: { label: 'Partial',  bg: '#fffbeb', color: '#d97706', border: '#fde68a', dot: '#f59e0b' },
  paid:    { label: 'Paid',     bg: '#f0fdf4', color: '#059669', border: '#a7f3d0', dot: '#10b981' },
}

function PaymentCell({ bill, onUpdate }: { bill: Bill; onUpdate: (id: string, status: string, amount?: number) => Promise<void> }) {
  const [open, setOpen]       = useState(false)
  const [partialAmt, setPartialAmt] = useState('')
  const [saving, setSaving]   = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const ps  = (bill.payment_status ?? 'unpaid') as keyof typeof PAY_META
  const meta = PAY_META[ps] ?? PAY_META.unpaid
  const isApproved = bill.status?.toUpperCase() === 'APPROVED'

  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])

  async function apply(status: string, amount?: number) {
    setSaving(true)
    try { await onUpdate(bill.invoice_id, status, amount) } catch { /* ignore */ }
    setSaving(false)
    setOpen(false)
    setPartialAmt('')
  }

  const fmtDate = (d: string) => new Date(d).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
  const fmtInr  = (n: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(n)

  return (
    <div ref={ref} style={{ position: 'relative' }} onClick={e => e.stopPropagation()}>
      <div style={{ position: 'relative', display: 'inline-block' }} className="bp__tip-wrap">
        <button
          onClick={() => isApproved && setOpen(o => !o)}
          disabled={!isApproved}
          title={!isApproved ? 'Bill must be approved before marking payment' : undefined}
          style={{
            display: 'flex', alignItems: 'center', gap: 4, padding: '2px 7px',
            borderRadius: 8, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
            ...(isApproved
              ? { border: `1px solid ${meta.border}`, background: meta.bg, color: meta.color, cursor: 'pointer' }
              : { border: '1px solid #e5e7eb', background: '#f9fafb', color: 'var(--ink-500)', cursor: 'not-allowed' }
            ),
          }}
        >
          <span style={{
            width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
            background: isApproved ? meta.dot : '#d1d5db',
          }} />
          {meta.label}
          {ps === 'partial' && bill.amount_paid != null && (
            <span style={{ opacity: 0.7 }}>· {fmtInr(bill.amount_paid)}</span>
          )}
        </button>
        {bill.payment_updated_by && (
          <span className="bp__tip">
            by {bill.payment_updated_by}{bill.payment_updated_at ? ' · ' + fmtDate(bill.payment_updated_at) : ''}
          </span>
        )}
      </div>

      {open && isApproved && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 2000,
          background: 'white', border: '1px solid #e5e7eb', borderRadius: 10,
          boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 10, minWidth: 180,
        }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--ink-500)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Mark as
          </div>
          {(['unpaid', 'paid'] as const).map(s => (
            <button key={s} onClick={() => apply(s)}
              style={{
                display: 'flex', alignItems: 'center', gap: 7, width: '100%',
                padding: '7px 8px', borderRadius: 7, border: 'none', cursor: 'pointer',
                background: ps === s ? PAY_META[s].bg : 'transparent',
                color: PAY_META[s].color, fontSize: 13, fontWeight: ps === s ? 700 : 500,
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: PAY_META[s].dot }} />
              {PAY_META[s].label}
              {saving && ps === s && <RefreshCw size={10} style={{ marginLeft: 'auto', animation: 'bp-spin 1s linear infinite' }} />}
            </button>
          ))}

          {/* Partial paid */}
          <div style={{ borderTop: '1px solid #f3f4f6', marginTop: 6, paddingTop: 6 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: '#d97706', marginBottom: 5, display: 'flex', alignItems: 'center', gap: 5 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b' }} />
              Partially Paid
            </div>
            <div style={{ display: 'flex', gap: 5 }}>
              <input
                type="number" min={0} placeholder="Amount paid"
                value={partialAmt}
                onChange={e => setPartialAmt(e.target.value)}
                style={{
                  flex: 1, padding: '5px 8px', borderRadius: 6, border: '1px solid #e5e7eb',
                  fontSize: 12, outline: 'none', minWidth: 0,
                }}
                onKeyDown={e => e.key === 'Enter' && partialAmt && apply('partial', parseFloat(partialAmt))}
              />
              <button
                disabled={!partialAmt || saving}
                onClick={() => apply('partial', parseFloat(partialAmt))}
                style={{
                  padding: '5px 10px', borderRadius: 6, border: 'none',
                  background: partialAmt ? '#f59e0b' : '#e5e7eb',
                  color: partialAmt ? 'white' : 'var(--ink-500)',
                  fontSize: 12, fontWeight: 600, cursor: partialAmt ? 'pointer' : 'default',
                }}
              >
                Set
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export interface BillsPageProps {
  initialBillId?: string
  onBillOpened?: () => void
  onReviewDraft?: (item: { file: File; data: Record<string, unknown> }, draftId: string, origin?: string) => void
}

export default function BillsPage({ initialBillId, onBillOpened, onReviewDraft }: BillsPageProps = {}) {
  const { user, activeBrand } = useAuth()
  // Vendor payments are a licensed module. Hiding the entry points avoids dead
  // ends — the route and the endpoints behind it refuse regardless.
  const payEnabled = useService('disbursement')
  const listJur = useJurisdiction()
  const baseCur = listJur.base_currency  // amounts in the list are in the tenant's books currency
  const listTds = listJur.packs.tds_india  // TDS / Net Payable columns exist only where Indian TDS applies
  const [priorityLabels, setPriorityLabels] = useState<PriorityLabels>(DEFAULT_PRIORITY_LABELS)
  const [bills, setBills]           = useState<Bill[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState('')
  const [selected, setSelected]     = useState<Bill | null>(null)
  // An open bill belongs in the URL: /bills/<invoice_id> is linkable and Back
  // closes it. The page is also embedded under /dashboard, where changing the
  // address would tear that view down — so URL syncing only happens when this
  // page is the one the address bar is pointing at.
  const navigate = useNavigate()
  const location = useLocation()
  const { invoiceId: routeInvoiceId } = useParams<{ invoiceId?: string }>()
  const routed = location.pathname === PATHS.bills || location.pathname.startsWith(`${PATHS.bills}/`)
  const [origin, setOrigin]         = useState('center center')
  const [notifCount, setNotifCount] = useState(0)
  const [sortBy, setSortBy]         = useState<'default' | 'priority'>('priority')
  const [search, setSearch]               = useState('')
  // Per-column filters, from the funnels in the headers and the bar above the
  // table — the only filtering on this page besides the tab and the search box.
  const colFilters = useColumnFilters(BILL_FILTER_FIELDS)
  const [activeTab, setActiveTab] = useState<'all' | 'my_approval' | 'my_uploaded' | 'approved' | 'on_hold' | 'deleted' | 'draft'>('all')

  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set())
  const [bulkLoading, setBulkLoading] = useState(false)
  const [bulkToast, setBulkToast]   = useState<{ succeeded: number; failed: number; action: string } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const exportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!user?.company_id) { setPriorityLabels(DEFAULT_PRIORITY_LABELS); return }
    fetchPriorityLabels().then(setPriorityLabels).catch(() => setPriorityLabels(DEFAULT_PRIORITY_LABELS))
  }, [user?.company_id])

  // In-memory prefetch cache: invoice_id → BillDetail
  const detailCache = useRef<Map<string, BillDetail>>(new Map())

  const openBill = (bill: Bill, e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const cx = r.left + r.width / 2
    const cy = r.top + r.height / 2
    const dx = cx - window.innerWidth  / 2
    const dy = cy - window.innerHeight / 2
    setOrigin(`${dx.toFixed(1)}px ${dy.toFixed(1)}px`)
    if (routed) navigate(billPath(bill.invoice_id))
    else setSelected(bill)
  }

  const [archivedBills, setArchivedBills] = useState<typeof bills>([])
  const [archivedLoading, setArchivedLoading] = useState(false)

  const loadArchived = async (softStatus: 'deleted' | 'on_hold') => {
    setArchivedLoading(true)
    try {
      const list = await fetchBills(true, user?.company_id, undefined, undefined, undefined, activeBrand?.id, softStatus)
      setArchivedBills(list)
    } catch { /* silent */ } finally { setArchivedLoading(false) }
  }

  useEffect(() => {
    if (activeTab === 'deleted') loadArchived('deleted')
    else if (activeTab === 'on_hold') loadArchived('on_hold')
    else if (activeTab === 'draft') loadDrafts()
  }, [activeTab, activeBrand?.id])

  // ── Agent-created draft bills (pending review — not yet ingested) ────────────
  const [draftBills, setDraftBills] = useState<AgentDraft[]>([])
  const [draftsLoading, setDraftsLoading] = useState(false)
  const [draftOpening, setDraftOpening] = useState<string | null>(null)

  const loadDrafts = async () => {
    if (!user?.company_id) return
    setDraftsLoading(true)
    try { setDraftBills(await fetchAgentDrafts(user.company_id)) }
    catch { /* silent */ } finally { setDraftsLoading(false) }
  }

  const openDraft = async (draft: AgentDraft) => {
    if (!user?.company_id || !onReviewDraft) return
    setDraftOpening(draft.id)
    try {
      const [detail, file] = await Promise.all([
        fetchAgentDraft(draft.id, user.company_id),
        fetchAgentDraftFile(draft.id, user.company_id, draft.source_file),
      ])
      onReviewDraft({ file, data: detail.payload }, draft.id, 'bills')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to open draft')
    } finally { setDraftOpening(null) }
  }

  const handleDiscardDraft = async (draft: AgentDraft) => {
    if (!user?.company_id) return
    if (!window.confirm('Discard this draft bill? The agent will not re-create it.')) return
    try {
      await discardDraft(draft.id, user.company_id)
      setDraftBills(prev => prev.filter(d => d.id !== draft.id))
    } catch { /* silent */ }
  }

  const load = async (forceRefresh = false) => {
    setLoading(true); setError('')
    try {
      const list = await fetchBills(forceRefresh, user?.company_id, fresh => setBills(fresh), user?.role, user?.name, activeBrand?.id)
      setBills(list)
      // Pre-fetch all dept chains so stepper renders instantly when a bill is opened
      if (user?.company_id) {
        const depts = [...new Set(list.map(b => b.department).filter(Boolean) as string[])]
        prefetchChains(user.company_id, depts)
      }
      // Detail pre-fetch removed — fetch on demand when user opens a bill
    }
    catch (e: unknown) { setError(e instanceof Error ? e.message : 'Failed to load bills') }
    finally { setLoading(false) }
  }

  // Open whatever the URL points at (or what a caller asked for), once the list
  // it has to be found in has arrived.
  useEffect(() => {
    const wanted = routeInvoiceId ?? initialBillId
    if (!wanted || bills.length === 0) return
    if (selected?.invoice_id === wanted) return
    const bill = bills.find(b => b.invoice_id === wanted)
    if (bill) {
      setOrigin('center center')
      setSelected(bill)
      if (!routeInvoiceId) onBillOpened?.()
    }
  }, [routeInvoiceId, initialBillId, bills, selected])

  // Back, or a link to /bills, closes the panel.
  useEffect(() => {
    if (routed && !routeInvoiceId && selected) setSelected(null)
  }, [routed, routeInvoiceId, selected])

  // notification count + toasts for pending bills
  useEffect(() => {
    if (!user) return
    fetchNotificationCount(user.role, user.company_id).then(setNotifCount).catch(() => {})
  }, [user, bills])

  useEffect(() => { if (user !== undefined) load() }, [user?.company_id, activeBrand?.id])

  useEffect(() => {
    if (!exportOpen) return
    const h = (e: MouseEvent) => {
      if (exportRef.current && !exportRef.current.contains(e.target as Node)) setExportOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [exportOpen])

  const [showExportPreview, setShowExportPreview] = useState(false)
  const ALL_EXPORT_COLS = [
    'Invoice No.', 'Vendor', 'Vendor GSTN',
    'Invoice Date', 'Due Date', 'Category', 'Department', 'Cost Center',
    'Subtotal (₹)', 'GST / Tax Amount (₹)', 'Total Amount (₹)',
    'TDS Section', 'TDS Rate (%)', 'TDS Amount (₹)',
    'Net Payable (₹)', 'Amount Paid (₹)', 'Outstanding (₹)',
    'Currency', 'Status', 'Payment Status',
    'Uploaded By', 'Uploaded By Role',
  ]
  const [exportColsVisible, setExportColsVisible] = useState<Set<string>>(() => new Set(ALL_EXPORT_COLS))
  const [showColPicker, setShowColPicker] = useState(false)

  const buildExportData = () => {
    const rows = displayBills
    const fmt = (n: number | null) => n != null ? n.toFixed(2) : ''
    const fmtDate = (d: string | null) => d ? d.slice(0, 10) : ''
    const statusLabel = (s: string) => s.replace('PENDING_', '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase())
    const payLabel = (s: string | null) => s ? s.charAt(0).toUpperCase() + s.slice(1) : ''
    // All columns in order; filtered by exportColsVisible
    const allColValues = (b: Bill): Record<string, string> => ({
      'Invoice No.': b.id ?? '',
      'Vendor': b.vendor_name ?? '',
      'Vendor GSTN': b.vendor_gstn ?? '',
      'Invoice Date': fmtDate(b.invoice_date),
      'Due Date': fmtDate(b.due_date),
      'Category': b.category ?? '',
      'Department': b.department ?? '',
      'Cost Center': b.cost_center_name ?? '',
      'Subtotal (₹)': fmt(b.subtotal),
      'GST / Tax Amount (₹)': fmt(b.tax_amount),
      'Total Amount (₹)': fmt(b.total_amount),
      'TDS Section': b.tds_status?.toUpperCase() === 'CALCULATED' ? (b.tds_section_code ?? '') : '',
      'TDS Rate (%)': b.tds_status?.toUpperCase() === 'CALCULATED' ? (b.tds_rate != null ? String(b.tds_rate) : '') : '',
      'TDS Amount (₹)': b.tds_status?.toUpperCase() === 'CALCULATED' ? fmt(b.tds_amount) : '',
      'Net Payable (₹)': fmt(b.net_payable),
      'Amount Paid (₹)': fmt(b.amount_paid),
      'Outstanding (₹)': fmt(b.outstanding_amount),
      'Currency': b.currency_code ?? 'INR',
      'Status': statusLabel(b.status),
      'Payment Status': payLabel(b.payment_status),
      'Uploaded By': b.uploaded_by_name ?? '',
      'Uploaded By Role': b.uploaded_by_role ?? '',
    })
    // Raw values for the columns a spreadsheet should treat as numbers or dates.
    // allColValues() above formats everything to a string for the CSV path; a
    // workbook built from those strings can't sum a column or sort by date, so
    // the xlsx path reads these instead and lets the cell keep its real type.
    const NUMERIC_COLS = new Set([
      'Subtotal (₹)', 'GST / Tax Amount (₹)', 'Total Amount (₹)', 'TDS Rate (%)',
      'TDS Amount (₹)', 'Net Payable (₹)', 'Amount Paid (₹)', 'Outstanding (₹)',
    ])
    const DATE_COLS = new Set(['Invoice Date', 'Due Date'])
    const typedValue = (col: string, display: string): string | number | Date => {
      if (!display) return ''
      if (NUMERIC_COLS.has(col)) {
        const n = Number(display)
        return Number.isFinite(n) ? n : display
      }
      if (DATE_COLS.has(col)) {
        const d = new Date(`${display}T00:00:00`)
        return Number.isNaN(d.getTime()) ? display : d
      }
      return display
    }

    const headers = ALL_EXPORT_COLS.filter(h => exportColsVisible.has(h))
    const dataRows = rows.map(b => { const vals = allColValues(b); return headers.map(h => vals[h]) })
    const typedRows = rows.map(b => {
      const vals = allColValues(b)
      return headers.map(h => typedValue(h, vals[h]))
    })
    const brandLabel = activeBrand?.name ? `_${activeBrand.name.replace(/\s+/g, '_')}` : ''
    const fileName = `Bills${brandLabel}_${new Date().toISOString().slice(0, 10)}`
    return { headers, dataRows, typedRows, fileName }
  }

  const doExportDownload = async (format: 'xlsx' | 'csv') => {
    const { headers, dataRows, typedRows, fileName } = buildExportData()

    if (format === 'csv') {
      const escape = (v: string) => `"${String(v).replace(/"/g, '""')}"`
      const lines = [headers.map(escape).join(','), ...dataRows.map(r => r.map(escape).join(','))]
      const blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a'); a.href = url; a.download = `${fileName}.csv`; a.click()
      URL.revokeObjectURL(url)
      return
    }

    // A real workbook, not a CSV wearing an .xls extension. The previous version
    // shipped the same comma-separated text with a BOM and an Excel MIME type,
    // so every amount arrived as left-aligned text: no column totals, and dates
    // sorted alphabetically.
    //
    // Imported on click rather than at the top of the file. The library is ~400 kB
    // and only this handler and the attachment preview need it, so paying for it
    // on first paint made every page load slower for a button most sessions never
    // press.
    const XLSX = await import('xlsx')
    const ws = XLSX.utils.aoa_to_sheet([headers, ...typedRows], { cellDates: true })
    ws['!cols'] = headers.map(h => ({ wch: Math.max(12, h.length + 2) }))
    ws['!freeze'] = { xSplit: '0', ySplit: '1' }
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Bills')
    XLSX.writeFile(wb, `${fileName}.xlsx`, { compression: true })
  }

  // The preview dialog carries the chosen format through to the download, so
  // "Excel" and "CSV" are no longer two buttons with one behaviour.
  const [exportFormat, setExportFormat] = useState<'xlsx' | 'csv'>('xlsx')
  const exportBills = (format: 'xlsx' | 'csv') => {
    setExportFormat(format)
    setExportOpen(false)
    setShowExportPreview(true)
  }

  const filteredBills = bills.filter(b => {
    // Tab filter
    if (activeTab === 'my_approval') {
      const roleOk = (b.allowed_roles ?? []).includes(user?.role ?? '')
      const names  = b.allowed_usernames
      const nameOk = !names || names.length === 0 || names.includes(user?.name ?? '') || user?.role === 'admin'
      if (!(roleOk && nameOk)) return false
    }
    if (activeTab === 'my_uploaded') {
      if (b.uploaded_by_name !== user?.name) return false
    }
    if (activeTab === 'approved') {
      if (b.status?.toUpperCase() !== 'APPROVED') return false
    }
    // Members only see their own uploaded bills
    if (user?.role === 'member' && b.uploaded_by_name !== user?.name) return false
    // Vendor name search
    if (search) {
      const q = search.toLowerCase()
      if (!(b.vendor_name?.toLowerCase().includes(q) || b.id?.toLowerCase().includes(q))) return false
    }
    return true
  })

  const isArchivedTab = activeTab === 'on_hold' || activeTab === 'deleted'
  // Everything the tab, and the search box already decided. The
  // column filters run last, on this set — which is also the set their value
  // lists and counts are drawn from, so "Approved · 12" means twelve rows in the
  // table you are looking at, not twelve somewhere in the company.
  const tabBills = isArchivedTab
    ? archivedBills
    : sortBy === 'priority'
    ? [...filteredBills].sort((a, b) => {
        const pa = getPayPriority(a)
        const pb = getPayPriority(b)
        const ra = pa ? PRIORITY_META[pa].rank : 99
        const rb = pb ? PRIORITY_META[pb].rank : 99
        if (ra !== rb) return ra - rb
        const da = a.due_date ? new Date(a.due_date).getTime() : Infinity
        const db = b.due_date ? new Date(b.due_date).getTime() : Infinity
        return da - db
      })
    : filteredBills
  const displayBills = colFilters.apply(tabBills)
  // Paged at the foot of the table; back to page 1 whenever the tab, the
  // search or any filter changes what is listed.
  const billsPage = usePagedRows(displayBills, 'bills', [
    activeTab, search, sortBy, colFilters.count, displayBills.length,
  ])
  // Phones scroll instead: 50 rows at a time, more as the end comes into view.
  const isPhone = useMediaQuery(PHONE_QUERY)
  const billsFeed = useInfiniteRows(displayBills, [
    activeTab, search, sortBy, colFilters.count, displayBills.length,
  ])
  const shownBills = isPhone ? billsFeed.rows : billsPage.pageRows

  async function handlePaymentUpdate(invoiceId: string, status: string, amount?: number) {
    await updatePaymentStatus(invoiceId, {
      payment_status: status,
      ...(amount != null ? { amount_paid: amount } : {}),
      actor_name: user?.name,
      actor_role: user?.role,
    })
    invalidateBillsCache(user?.company_id)
    const now = new Date().toISOString()
    setBills(prev => prev.map(b =>
      b.invoice_id === invoiceId
        ? { ...b, payment_status: status, amount_paid: amount ?? (status === 'paid' ? b.total_amount : b.amount_paid),
            payment_updated_by: user?.name ?? null, payment_updated_at: now }
        : b
    ))
  }

  const allPageIds = displayBills.map(b => b.invoice_id)
  const allChecked = allPageIds.length > 0 && allPageIds.every(id => checkedIds.has(id))
  const someChecked = !allChecked && allPageIds.some(id => checkedIds.has(id))

  const toggleAll = () => {
    if (allChecked) {
      setCheckedIds(prev => { const n = new Set(prev); allPageIds.forEach(id => n.delete(id)); return n })
    } else {
      setCheckedIds(prev => { const n = new Set(prev); allPageIds.forEach(id => n.add(id)); return n })
    }
  }

  const toggleOne = (id: string) => {
    setCheckedIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  const handleBulkAction = async (action: 'APPROVE' | 'PUSH_TALLY' | 'MARK_PAID') => {
    const ids = [...checkedIds]
    if (!ids.length || !user) return
    setBulkLoading(true)
    try {
      const res = await bulkBillAction({ bill_ids: ids, action, actor_name: user.name, actor_role: user.role, company_id: user.company_id })
      setBulkToast({ succeeded: res.succeeded, failed: res.failed, action })
      setCheckedIds(new Set())
      await load(true)
      setTimeout(() => setBulkToast(null), 5000)
    } catch {
      setBulkToast({ succeeded: 0, failed: ids.length, action })
      setTimeout(() => setBulkToast(null), 5000)
    } finally {
      setBulkLoading(false)
    }
  }

  // Staged so a ConfirmDialog can show exactly which bills and how much money is
  // affected. `window.confirm` could show neither, and was guarding the soft
  // deletion of an arbitrary number of payables.
  const [pendingSoftStatus, setPendingSoftStatus] = useState<'on_hold' | 'deleted' | 'active' | null>(null)

  const handleBulkSoftStatus = async (status: 'on_hold' | 'deleted' | 'active') => {
    const ids = [...checkedIds]
    if (!ids.length || !user) return
    setPendingSoftStatus(null)
    setBulkLoading(true)
    let succeeded = 0, failed = 0
    await Promise.all(ids.map(async id => {
      try {
        const res = await fetch(
          `${import.meta.env.VITE_API_URL ?? ''}/api/bills/${id}/soft-status?status=${status}&company_id=${user.company_id ?? ''}`,
          { method: 'PATCH' }
        )
        res.ok ? succeeded++ : failed++
      } catch { failed++ }
    }))
    setBulkToast({ succeeded, failed, action: 'APPROVE' })
    setCheckedIds(new Set())
    if (isArchivedTab) loadArchived(activeTab as 'on_hold' | 'deleted')
    else await load(true)
    setTimeout(() => setBulkToast(null), 5000)
    setBulkLoading(false)
  }

  const selectedBills = displayBills.filter(b => checkedIds.has(b.invoice_id))
  const approvableCount = selectedBills.filter(b => {
    const roleOk = (b.allowed_roles ?? []).includes(user?.role ?? '')
    const names  = b.allowed_usernames
    const nameOk = !names || names.length === 0 || names.includes(user?.name ?? '') || user?.role === 'admin'
    return roleOk && nameOk
  }).length
  const canBulkApprove = approvableCount > 0
  const approvedSelectedCount = selectedBills.filter(b => b.status === 'APPROVED').length
  const canBulkFinance = user?.role === 'admin' && approvedSelectedCount > 0
  // Same Pay Now gate/behavior as the row-level button — only offered here when the
  // single selected bill actually qualifies, so this is the same functionality in a
  // second place (bottom bulk bar), not a separate payment path.
  const payableSelectedBill = selectedBills.length === 1 && user?.role === 'admin'
    && selectedBills[0].status === 'APPROVED' && selectedBills[0].supplier_id
    && selectedBills[0].payment_status !== 'paid'
    && (selectedBills[0].outstanding_amount ?? selectedBills[0].total_amount ?? 0) > 0
    ? selectedBills[0] : null

  return (
    <>
      <div className="bp">
        <div className="bp__header">
          {/* Left: title */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <h1 className="bp__title" style={{ margin: 0 }}>Bills</h1>
            {notifCount > 0 && (
              <span className="bp__badge" title={`${notifCount} bill${notifCount > 1 ? 's' : ''} need your action`}>
                {notifCount}
              </span>
            )}
            {/* Count must describe the rows actually on screen. It previously
                read `filteredBills` (active bills) even on the On Hold and
                Deleted tabs, so the header could claim 312 bills above a table
                of 4. */}
            <span className="bp__sub" style={{ margin: 0 }}>
              {loading ? 'Loading…' : (() => {
                const shown = displayBills.length
                const total = isArchivedTab ? archivedBills.length : bills.length
                const filtered = shown !== total ? `${shown} / ${total}` : `${shown}`
                return `${filtered} bill${total !== 1 ? 's' : ''}`
              })()}
            </span>
          </div>

          {/* Right: search + sort + refresh */}
          <div className="bp__toolbar" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {/* Search */}
            <div className="bp__search" style={{ position: 'relative' }}>
              <input
                value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Search vendor, ID…"
                style={{ padding: '9px 12px 9px 32px', borderRadius: 10, border: '1px solid #dfe6f0', fontSize: 13, width: 190, outline: 'none', background: '#ffffff', color: '#111827', boxShadow: 'inset 0 1px 2px rgba(15, 23, 42, 0.02)' }}
              />
              <span style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: '#8a94a6', pointerEvents: 'none', fontSize: 14, display: 'flex' }}><Search size={14} aria-hidden="true" /></span>
              {search && <button onClick={() => setSearch('')} style={{ position: 'absolute', right: 9, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#8a94a6', fontSize: 14, lineHeight: 1, padding: 0 }}>×</button>}
            </div>

            <div className="bp__toolbar-sep" style={{ width: 1, height: 20, background: '#e5e7eb', flexShrink: 0 }} />

            <button
              onClick={() => setSortBy(s => s === 'priority' ? 'default' : 'priority')}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px',
                borderRadius: 10, border: '1px solid', fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
                // Active state mirrors the Filters button so "this control is on"
                // reads the same way across the toolbar.
                background:  sortBy === 'priority' ? '#eef6ff' : '#ffffff',
                color:       sortBy === 'priority' ? '#1d4ed8' : '#374151',
                borderColor: sortBy === 'priority' ? '#bfd5ff' : '#dfe6f0',
                boxShadow: '0 1px 2px rgba(15, 23, 42, 0.02)',
              }}
            >
              <Zap size={14} aria-hidden="true" style={{ color: sortBy === 'priority' ? '#1d4ed8' : '#6b7280' }} />
              {sortBy === 'priority' ? 'Sorted by Priority' : 'Sort by Priority'}
            </button>
            {/* Export dropdown */}
            <div ref={exportRef} style={{ position: 'relative' }}>
              <button
                onClick={() => setExportOpen(o => !o)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '9px 14px',
                  borderRadius: 10, border: '1px solid #dfe6f0', fontSize: 13, fontWeight: 600,
                  cursor: 'pointer', background: exportOpen ? '#edf8f1' : '#ffffff',
                  color: exportOpen ? '#177a4a' : '#374151', whiteSpace: 'nowrap',
                  boxShadow: '0 1px 2px rgba(15, 23, 42, 0.02)',
                }}
              >
                <Download size={14} aria-hidden="true" /> Export
              </button>
              {exportOpen && (
                <div style={{
                  position: 'absolute', top: 'calc(100% + 8px)', right: 0,
                  background: '#ffffff', border: '1px solid #e5ecf5', borderRadius: 12,
                  boxShadow: '0 14px 32px rgba(15, 23, 42, 0.08)', zIndex: 200, minWidth: 210, overflow: 'hidden',
                }}>
                  <div style={{ padding: '10px 14px 8px', fontSize: 11, fontWeight: 700, color: '#667085', textTransform: 'uppercase', letterSpacing: '.08em', borderBottom: '1px solid #edf2f7', background: '#f8fafc' }}>
                    Export {displayBills.length} bills
                  </div>
                  <button onClick={() => exportBills('xlsx')} style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '12px 14px', background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, fontWeight: 600, color: '#1f2937', textAlign: 'left' }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#f3f8ff')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}>
                    <span style={{ width: 18, height: 18, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6, background: '#e9f7ee', color: '#127a4a', fontSize: 12, fontWeight: 800 }}>X</span>
                    Excel (.xls)
                  </button>
                  <button onClick={() => exportBills('csv')} style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '12px 14px', background: 'none', border: 'none', cursor: 'pointer', fontSize: 14, fontWeight: 600, color: '#1f2937', textAlign: 'left' }}
                    onMouseEnter={e => (e.currentTarget.style.background = '#f3f8ff')}
                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}>
                    <span style={{ width: 18, height: 18, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 6, background: '#f3f4f6', color: '#4b5563', fontSize: 12, fontWeight: 800 }}>C</span>
                    CSV (.csv)
                  </button>
                </div>
              )}
            </div>

            <button className="bp__refresh" onClick={() => load(true)} disabled={loading}>
              <RefreshCw size={15} className={loading ? 'bp__spin' : ''} />
              Refresh
            </button>
          </div>
        </div>

        {/* Tabs */}
        <div className="bp__tabs" style={{ display: 'flex', gap: 0, borderBottom: '1px solid #e5e7eb', marginBottom: 0, background: 'rgba(255,255,255,0.12)' }}>
          {([
            { key: 'all',         label: 'All Bills',     color: '#111827' },
            { key: 'my_approval', label: 'My Approvals',  color: '#111827' },
            { key: 'my_uploaded', label: 'My Uploaded',   color: '#111827' },
            { key: 'approved',    label: 'All Approved',  color: '#111827' },
            ...(user?.role === 'admin' ? [
              { key: 'on_hold',   label: 'On Hold',       color: '#b45309' },
              { key: 'deleted',   label: 'Deleted',       color: '#b42318' },
            ] : []),
            { key: 'draft',       label: 'Draft',         color: '#5b5bd6' },
          ] as const).map(tab => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key as typeof activeTab)}
              style={{
                padding: '14px 18px 12px', fontSize: 14, fontWeight: 600, border: 'none', background: 'none', cursor: 'pointer',
                color: activeTab === tab.key ? tab.color : '#6b7280',
                borderBottom: activeTab === tab.key ? `3px solid ${activeTab === tab.key && (tab.key === 'on_hold' || tab.key === 'deleted' || tab.key === 'draft') ? (tab.key === 'on_hold' ? '#d97706' : tab.key === 'deleted' ? '#ef4444' : '#7c3aed') : '#111827'}` : '3px solid transparent',
                marginBottom: -1, transition: 'color 0.15s, border-color 0.15s',
                fontFamily: 'Inter, sans-serif',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {error && (
          <div className="bp__error"><AlertCircle size={16} />{error}</div>
        )}

        {activeTab === 'draft' ? (
          <div className="bp__wrap">
            <table className="bp__table">
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Invoice No.</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                  <th>From</th>
                  <th>Subject</th>
                  <th>Received</th>
                  <th style={{ textAlign: 'center' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {draftsLoading && (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '40px 0', color: 'var(--ink-500)' }}>Loading drafts…</td></tr>
                )}
                {!draftsLoading && draftBills.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: '48px 0', color: 'var(--ink-500)' }}>
                    <Bot size={20} style={{ opacity: 0.5, marginBottom: 6 }} /><br />
                    No draft bills yet. When the Vendor Bill Agent detects an invoice emailed by a watched contact, it lands here for review.
                  </td></tr>
                )}
                {!draftsLoading && draftBills.map(d => (
                  <Fragment key={d.id}>
                  <tr className="bp__row" style={{ cursor: 'pointer', borderBottom: d.suggestion ? 'none' : undefined }} onClick={() => openDraft(d)}>
                    <td><div className="bp__vendor">{d.vendor_name ?? <span style={{ color: '#d1d5db' }}>Unknown</span>}</div></td>
                    <td>
                      {d.invoice_number ?? <span style={{ color: '#d1d5db' }}>—</span>}
                      {d.analysis === 'correction' && (
                        <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 700, padding: '1px 6px', borderRadius: 4, background: '#fef3c7', color: '#b45309' }}>CORRECTION</span>
                      )}
                    </td>
                    <td style={{ textAlign: 'right' }}>{d.total_amount != null ? `${d.currency} ${Number(d.total_amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}</td>
                    <td style={{ fontSize: 12, color: '#6b7280' }}>{d.from_email ?? '—'}</td>
                    <td style={{ fontSize: 12, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.subject ?? ''}>{d.subject ?? '—'}</td>
                    <td style={{ fontSize: 12, color: '#6b7280' }}>{new Date(d.created_at).toLocaleDateString()}</td>
                    <td style={{ textAlign: 'center' }} onClick={e => e.stopPropagation()}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'center' }}>
                        <button onClick={() => openDraft(d)} disabled={draftOpening === d.id}
                          style={{ fontSize: 11, fontWeight: 600, padding: '4px 10px', borderRadius: 6, border: 'none', background: '#7c3aed', color: '#fff', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                          {draftOpening === d.id ? 'Opening…' : 'Review'}
                        </button>
                        <button onClick={() => handleDiscardDraft(d)} title="Discard draft"
                          style={{ fontSize: 11, padding: '4px 8px', borderRadius: 6, border: '1px solid #fca5a5', background: '#fff', color: '#dc2626', cursor: 'pointer' }}>
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {d.suggestion && (
                    <tr>
                      <td colSpan={7} style={{ padding: '6px 12px 10px', background: '#fffbeb' }}>
                        <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12, color: '#92400e' }}>
                          <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
                          <span><b>Agent suggestion:</b> {d.suggestion}</span>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
        <>
        {/* The sheet's status bar: which columns are filtering, and what it cost.
            Only present while something is filtering. */}
        {colFilters.count > 0 && (
          <div className="bp__filterbar">
            <FilterStatus state={colFilters} total={tabBills.length} shown={displayBills.length} unit="bills" />
          </div>
        )}
        <div className={`bp__wrap${billsPage.total > 0 && !isPhone ? ' bp__wrap--paged' : ''}`}>
          <table className="bp__table bp__table--bills">
            <thead>
              <tr>
                <th className="bp__th-check">
                  <input type="checkbox" className="bp__chk" checked={allChecked} ref={el => { if (el) el.indeterminate = someChecked }} onChange={toggleAll} />
                </th>
                {/* Header = control: click it for the AutoFilter dropdown. */}
                <FilterTh state={colFilters} fieldKey="priority" rows={tabBills}>Priority</FilterTh>
                <FilterTh state={colFilters} fieldKey="vendor_name" rows={tabBills}>Vendor</FilterTh>
                <FilterTh state={colFilters} fieldKey="total_amount" rows={tabBills} align="right">Bill Amount</FilterTh>
                <FilterTh state={colFilters} fieldKey="uploaded_by_name" rows={tabBills}>Uploaded By</FilterTh>
                <FilterTh state={colFilters} fieldKey="status" rows={tabBills}>Status</FilterTh>
                <FilterTh state={colFilters} fieldKey="invoice_date" rows={tabBills}>Invoice Date</FilterTh>
                <FilterTh state={colFilters} fieldKey="due_date" rows={tabBills}>Due Date</FilterTh>
                <FilterTh state={colFilters} fieldKey="id" rows={tabBills}>ID</FilterTh>
                <FilterTh state={colFilters} fieldKey="category" rows={tabBills}>Category</FilterTh>
                {listTds && <FilterTh state={colFilters} fieldKey="tds_status" rows={tabBills}>TDS</FilterTh>}
                {listTds && <FilterTh state={colFilters} fieldKey="net_payable" rows={tabBills} align="right">Net Payable</FilterTh>}
                <th style={{ textAlign: 'center' }}>Doc</th>
                <FilterTh state={colFilters} fieldKey="payment_status" rows={tabBills}>Payment</FilterTh>
                <th style={{ textAlign: 'center' }}>Tally</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={15} style={{ textAlign: 'center', padding: '40px 0', color: 'var(--ink-500)' }}>Loading bills…</td></tr>
              )}
              {!loading && bills.length === 0 && !error && (
                <tr><td colSpan={15} style={{ textAlign: 'center', padding: '48px 0', color: 'var(--ink-500)' }}>No bills found. Upload and ingest invoices to see them here.</td></tr>
              )}
              {/* Filtered to nothing is not the same as having nothing, and the
                  way out is the button, not a page reload. */}
              {!loading && bills.length > 0 && displayBills.length === 0 && colFilters.count > 0 && (
                <tr><td colSpan={15} style={{ textAlign: 'center', padding: '40px 0', color: '#6b7280' }}>
                  No bills match these filters.{' '}
                  <button
                    onClick={colFilters.clearAll}
                    style={{ background: 'none', border: 'none', padding: 0, color: '#2563eb', fontWeight: 600, fontSize: 13, cursor: 'pointer', textDecoration: 'underline' }}
                  >
                    Clear {colFilters.count} filter{colFilters.count > 1 ? 's' : ''}
                  </button>
                </td></tr>
              )}
              {shownBills.map(bill => (
                <tr key={bill.invoice_id} className={`bp__row${checkedIds.has(bill.invoice_id) ? ' bp__row--checked' : ''}`} onClick={e => openBill(bill, e)} style={{ cursor: 'pointer' }}>
                  <td className="bp__td-check" onClick={e => { e.stopPropagation(); toggleOne(bill.invoice_id) }}>
                    <input type="checkbox" className="bp__chk" checked={checkedIds.has(bill.invoice_id)} onClick={e => e.stopPropagation()} onChange={() => toggleOne(bill.invoice_id)} />
                  </td>
                  <td onClick={e => e.stopPropagation()}><PriorityBadge bill={bill} isAdmin={user?.role === 'admin'} userName={user?.name} userRole={user?.role} companyId={user?.company_id} labels={priorityLabels} onLabelsSaved={setPriorityLabels} onRefresh={() => load(true)} /></td>
                  <td>
                    <div className="bp__vendor">{bill.vendor_name ?? <span style={{ color: '#d1d5db' }}>Unknown</span>}</div>
                    {bill.vendor_gstn && <div className="bp__gstn">{bill.vendor_gstn}</div>}
                  </td>
                  <td style={{ textAlign: 'right', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 5 }}>
                      {bill.fx_agent_severity === 'WARNING' && (
                        <span title={bill.fx_agent_note ?? 'FX Warning'} style={{ width: 7, height: 7, borderRadius: '50%', background: '#f59e0b', flexShrink: 0, boxShadow: '0 0 0 2px #fef3c7' }} />
                      )}
                      {bill.fx_agent_severity === 'OPPORTUNITY' && (
                        <span title={bill.fx_agent_note ?? 'FX Opportunity'} style={{ width: 7, height: 7, borderRadius: '50%', background: '#10b981', flexShrink: 0, boxShadow: '0 0 0 2px #d1fae5' }} />
                      )}
                      <span>
                        {bill.total_amount != null
                          ? fmt(bill.total_amount as unknown as string, 'amount', baseCur)
                          : <span style={{ color: '#d1d5db' }}>—</span>}
                      </span>
                    </div>
                    {bill.currency_code && bill.currency_code !== baseCur && bill.original_amount != null && (
                      <div style={{ fontSize: 11, color: '#6366f1', fontWeight: 500, marginTop: 1 }}>
                        {(() => { const sym = bill.currency_code === 'USD' ? '$' : bill.currency_code === 'EUR' ? '€' : bill.currency_code === 'GBP' ? '£' : bill.currency_code === 'JPY' ? '¥' : ''; return `${sym}${bill.original_amount!.toLocaleString('en-IN')} ${bill.currency_code}` })()}
                      </div>
                    )}
                  </td>
                  <td>
                    {bill.uploaded_by_name
                      ? <span className="bp__uploaded">
                          <span>{bill.uploaded_by_name}</span>
                          {bill.uploaded_by_role && <span className="bp__uploaded-role">{bill.uploaded_by_role.replace(/_/g, ' ')}</span>}
                        </span>
                      : <span style={{ color: '#d1d5db' }}>—</span>}
                  </td>
                  <td><StatusBadge status={bill.status} paymentStatus={bill.payment_status} statusLabel={bill.status_label} /></td>
                  <td>{fmt(bill.invoice_date, 'date')}</td>
                  <td>
                    {bill.due_date
                      ? <span style={{ color: new Date(bill.due_date) < new Date() ? '#ef4444' : undefined }}>{fmt(bill.due_date, 'date')}</span>
                      : <span style={{ color: '#d1d5db' }}>—</span>}
                  </td>
                  <td><span className="bp__id" title={bill.id}>{bill.id ? bill.id.slice(0, 8) + '…' : '—'}</span></td>
                  <td>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {bill.category
                        ? <span className="bp__category">{bill.category}</span>
                        : <span style={{ color: '#d1d5db' }}>—</span>}
                      {bill.department && (
                        <span style={{ fontSize: 11, color: '#6366f1', background: '#eef2ff', borderRadius: 4, padding: '1px 6px', display: 'inline-block', fontWeight: 500 }}>
                          {bill.department}
                        </span>
                      )}
                    </div>
                  </td>
                  {listTds && <td><TdsBadge bill={bill} /></td>}
                  {listTds && <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {bill.net_payable != null && bill.tds_status?.toUpperCase() === 'CALCULATED'
                      ? <span style={{ fontWeight: 600, color: '#059669' }}>{fmt(bill.net_payable as unknown as string, 'amount', baseCur)}</span>
                      : <span style={{ color: '#d1d5db' }}>—</span>}
                  </td>}
                  <td style={{ textAlign: 'center' }}>
                    {bill.upload_id
                      ? <span className="bp__doc" onClick={e => { e.stopPropagation(); openBill(bill, e) }}><FileText size={16} /></span>
                      : <span style={{ color: '#d1d5db' }}>—</span>}
                  </td>
                  <td>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 6 }}>
                      <PaymentCell bill={bill} onUpdate={handlePaymentUpdate} />
                      {payEnabled && user?.role === 'admin' && bill.status === 'APPROVED' && bill.supplier_id && bill.payment_status !== 'paid' && (bill.outstanding_amount ?? bill.total_amount ?? 0) > 0 && (
                        // Disabled (not hidden) for an unverified vendor: the row still
                        // has to show that payment is the pending action. Verification
                        // here is the vendor's KYB/GST flag only — a verified vendor with
                        // pending bank details stays clickable and hits the gateway's
                        // audited override path.
                        <button
                          disabled={!bill.vendor_verified}
                          onClick={e => {
                            e.stopPropagation()
                            if (!bill.vendor_verified) return
                            navigate(payPath(bill.supplier_id!, bill.invoice_id))
                          }}
                          title={bill.vendor_verified
                            ? 'Pay via Payment Gateway'
                            : 'Vendor not verified — verify the vendor before paying'}
                          style={{
                            fontSize: 11, fontWeight: 600, padding: '4px 8px', borderRadius: 6, border: 'none',
                            background: bill.vendor_verified ? '#059669' : '#e5e7eb',
                            color: bill.vendor_verified ? '#fff' : 'var(--ink-500)',
                            cursor: bill.vendor_verified ? 'pointer' : 'not-allowed',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          Pay Now
                        </button>
                      )}
                    </div>
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    {bill.tally_push_pending
                      ? <span className="bp__tally-badge bp__tally-badge--queued">Queued</span>
                      : <span className="bp__tally-badge bp__tally-badge--no">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {isPhone && (
            <LoadMore
              shown={billsFeed.shown}
              total={billsFeed.total}
              hasMore={billsFeed.hasMore}
              onLoadMore={billsFeed.loadMore}
              unit="bills"
            />
          )}
        </div>
        {billsPage.total > 0 && !isPhone && (
          <Pager
            label="Pages of bills"
            compact
            page={billsPage.page}
            perPage={billsPage.perPage}
            total={billsPage.total}
            onGo={n => { billsPage.setPage(n); document.querySelector('.bp__wrap')?.scrollTo({ top: 0 }) }}
            onPerPage={billsPage.setPerPage}
          />
        )}
        </>
        )}
      </div>

      {/* Bulk action bar */}
      {checkedIds.size > 0 && (
        <div className="bp__bulk-bar">
          <span className="bp__bulk-count">{checkedIds.size} selected</span>
          <button className="bp__bulk-clear" onClick={() => setCheckedIds(new Set())}><X size={13} /> Clear</button>
          <div className="bp__bulk-sep" />
          {canBulkApprove && (
            <button className="bp__bulk-btn bp__bulk-btn--approve" onClick={() => handleBulkAction('APPROVE')} disabled={bulkLoading}>
              <Check size={14} /> Approve{checkedIds.size > approvableCount ? ` (${approvableCount}/${checkedIds.size})` : ''}
            </button>
          )}
          {canBulkFinance && (
            <button className="bp__bulk-btn bp__bulk-btn--tally" onClick={() => handleBulkAction('PUSH_TALLY')} disabled={bulkLoading}>
              <ArrowUpCircle size={14} aria-hidden="true" /> Push to Tally{checkedIds.size > approvedSelectedCount ? ` (${approvedSelectedCount}/${checkedIds.size})` : ''}
            </button>
          )}
          {canBulkFinance && (
            <button className="bp__bulk-btn bp__bulk-btn--paid" onClick={() => handleBulkAction('MARK_PAID')} disabled={bulkLoading}>
              <Check size={14} /> Mark as Paid{checkedIds.size > approvedSelectedCount ? ` (${approvedSelectedCount}/${checkedIds.size})` : ''}
            </button>
          )}
          {payEnabled && payableSelectedBill && (
            <button
              className="bp__bulk-btn"
              style={payableSelectedBill.vendor_verified
                ? { background: '#059669', color: '#fff' }
                : { background: '#e5e7eb', color: 'var(--ink-500)', cursor: 'not-allowed' }}
              title={payableSelectedBill.vendor_verified
                ? 'Pay via Payment Gateway'
                : 'Vendor not verified — verify the vendor before paying'}
              onClick={() => navigate(payPath(payableSelectedBill.supplier_id!, payableSelectedBill.invoice_id))}
              disabled={bulkLoading || !payableSelectedBill.vendor_verified}
            >
              Pay Now
            </button>
          )}
          {user?.role === 'admin' && !isArchivedTab && (
            <>
              <div className="bp__bulk-sep" />
              <button
                className="bp__bulk-btn"
                style={{ background: '#d97706', color: '#fff' }}
                onClick={() => setPendingSoftStatus('on_hold')}
                disabled={bulkLoading}
              >
                <PauseCircle size={14} aria-hidden="true" /> Hold
              </button>
              <button
                className="bp__bulk-btn"
                style={{ background: '#dc2626', color: '#fff' }}
                onClick={() => setPendingSoftStatus('deleted')}
                disabled={bulkLoading}
              >
                <Trash2 size={14} aria-hidden="true" /> Delete
              </button>
            </>
          )}
          {user?.role === 'admin' && isArchivedTab && (
            <>
              <div className="bp__bulk-sep" />
              <button
                className="bp__bulk-btn"
                style={{ background: '#059669', color: '#fff' }}
                onClick={() => setPendingSoftStatus('active')}
                disabled={bulkLoading}
              >
                <RotateCcw size={14} aria-hidden="true" /> Restore
              </button>
            </>
          )}
          {bulkLoading && <span className="bp__bulk-spinner" />}
        </div>
      )}

      {/* Destructive bulk actions state their consequences before running. */}
      {(() => {
        const status = pendingSoftStatus
        if (!status) return null
        const affected = displayBills.filter(b => checkedIds.has(b.invoice_id))
        const total = affected.reduce((sum, b) => sum + (b.outstanding_amount ?? b.total_amount ?? 0), 0)
        const copy = {
          deleted: {
            title: `Delete ${affected.length} bill${affected.length !== 1 ? 's' : ''}?`,
            description:
              'They move to the Deleted tab, stop appearing in reports and stop counting toward outstanding balances. An admin can restore them.',
            confirmLabel: 'Delete bills',
            requireTyped: 'DELETE',
          },
          on_hold: {
            title: `Put ${affected.length} bill${affected.length !== 1 ? 's' : ''} on hold?`,
            description: 'Held bills pause in the approval chain and are excluded from payment runs until released.',
            confirmLabel: 'Put on hold',
            requireTyped: undefined,
          },
          active: {
            title: `Restore ${affected.length} bill${affected.length !== 1 ? 's' : ''}?`,
            description: 'They return to the active list and resume their place in the approval chain.',
            confirmLabel: 'Restore bills',
            requireTyped: undefined,
          },
        }[status]
        return (
          <ConfirmDialog
            open
            onCancel={() => setPendingSoftStatus(null)}
            onConfirm={() => handleBulkSoftStatus(status)}
            title={copy.title}
            description={copy.description}
            tone={status === 'deleted' ? 'danger' : 'primary'}
            confirmLabel={copy.confirmLabel}
            requireTyped={copy.requireTyped}
            impact={[
              { label: 'Bills', value: String(affected.length) },
              { label: 'Outstanding value', value: formatMoney(total) },
            ]}
            items={affected.map(
              b => `${b.vendor_name || 'Unknown vendor'} — ${b.id ?? '—'} · ${formatMoney(b.total_amount)}`,
            )}
          />
        )
      })()}

      {/* Result toast */}
      {bulkToast && (
        <div className={`bp__toast ${bulkToast.failed > 0 && bulkToast.succeeded === 0 ? 'bp__toast--err' : bulkToast.failed > 0 ? 'bp__toast--warn' : 'bp__toast--ok'}`}>
          {bulkToast.failed === 0
            ? <><Check size={14} /> {bulkToast.succeeded} bill{bulkToast.succeeded !== 1 ? 's' : ''} updated</>
            : <>{bulkToast.succeeded} succeeded · {bulkToast.failed} failed</>}
          <button className="bp__toast-close" onClick={() => setBulkToast(null)}><X size={12} /></button>
        </div>
      )}

      {/* Full-screen modal */}
      {selected && (
        <BillModal
          bill={selected} origin={origin} cachedDetail={detailCache.current.get(selected.invoice_id) ?? null}
          labels={priorityLabels}
          onClose={() => (routed ? navigate(PATHS.bills) : setSelected(null))}
          onRefresh={() => { detailCache.current.delete(selected.invoice_id); load(true) }}
          // The payment page is a route now, so opening it replaces this view
          // rather than stacking a second full-screen overlay on top of it.
          onPay={bill => bill.supplier_id && navigate(payPath(bill.supplier_id, bill.invoice_id))}
        />
      )}


      {/* ── Export Preview Modal ── */}
      {showExportPreview && (() => {
        const { headers, dataRows, fileName } = buildExportData()
        const hiddenCount = ALL_EXPORT_COLS.length - exportColsVisible.size
        return (
          <div
            onClick={() => { setShowExportPreview(false); setShowColPicker(false) }}
            style={{ position: 'fixed', inset: 0, zIndex: 10000, background: 'rgba(0,0,0,0.55)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
          >
            <div
              onClick={e => e.stopPropagation()}
              style={{ background: '#fff', borderRadius: 14, overflow: 'hidden', width: '92vw', maxWidth: 1300, maxHeight: '90vh', display: 'flex', flexDirection: 'column', boxShadow: '0 24px 60px rgba(0,0,0,0.35)' }}
            >
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid #e5e7eb', background: '#f9fafb', flexShrink: 0, gap: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                  <span style={{ fontSize: 15, fontWeight: 700, color: '#111827' }}>Export Preview</span>
                  <span style={{ fontSize: 12, color: '#6b7280', background: '#f3f4f6', border: '1px solid #e5e7eb', borderRadius: 6, padding: '2px 8px' }}>{dataRows.length} bills</span>
                  <span style={{ fontSize: 12, color: 'var(--ink-500)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fileName}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  {/* Column picker */}
                  <div style={{ position: 'relative' }}>
                    <button
                      onClick={() => setShowColPicker(p => !p)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 7, border: `1px solid ${showColPicker ? '#6366f1' : '#d1d5db'}`, background: showColPicker ? '#eef2ff' : '#fff', color: showColPicker ? '#4f46e5' : '#374151', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
                    >
                      <span style={{ fontSize: 14 }}>⚙</span> Columns {hiddenCount > 0 && <span style={{ background: '#fef3c7', color: '#92400e', borderRadius: 10, padding: '1px 6px', fontSize: 11 }}>{hiddenCount} hidden</span>}
                    </button>
                    {showColPicker && (
                      <div
                        onClick={e => e.stopPropagation()}
                        style={{ position: 'absolute', top: 'calc(100% + 6px)', right: 0, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', zIndex: 100, minWidth: 220, padding: '8px 0' }}
                      >
                        <div style={{ padding: '6px 14px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f3f4f6' }}>
                          <span style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>Show / Hide Columns</span>
                          <button
                            onClick={() => setExportColsVisible(new Set(ALL_EXPORT_COLS))}
                            style={{ fontSize: 11, color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontFamily: 'inherit' }}
                          >Reset all</button>
                        </div>
                        <div style={{ maxHeight: 320, overflowY: 'auto', padding: '4px 0' }}>
                          {ALL_EXPORT_COLS.map(col => {
                            const on = exportColsVisible.has(col)
                            return (
                              <label key={col} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 14px', cursor: 'pointer', userSelect: 'none' }}
                                onMouseEnter={e => (e.currentTarget.style.background = '#f9fafb')}
                                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                              >
                                <input
                                  type="checkbox"
                                  checked={on}
                                  onChange={() => setExportColsVisible(prev => {
                                    const next = new Set(prev)
                                    if (on) next.delete(col); else next.add(col)
                                    return next
                                  })}
                                  style={{ accentColor: '#6366f1', width: 14, height: 14, cursor: 'pointer' }}
                                />
                                <span style={{ fontSize: 13, color: on ? '#111827' : 'var(--ink-500)' }}>{col}</span>
                              </label>
                            )
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                  {/* The format picked in the toolbar menu becomes the primary
                      action here; the other stays available as a secondary. */}
                  {(exportFormat === 'xlsx'
                    ? (['csv', 'xlsx'] as ('xlsx' | 'csv')[])
                    : (['xlsx', 'csv'] as ('xlsx' | 'csv')[])
                  ).map(fmt => (
                    <Button
                      key={fmt}
                      variant={fmt === exportFormat ? 'primary' : 'secondary'}
                      size="sm"
                      iconLeft={<Download size={13} />}
                      onClick={() => { doExportDownload(fmt); setShowExportPreview(false); setShowColPicker(false) }}
                    >
                      {fmt === 'xlsx' ? 'Excel (.xlsx)' : 'CSV (.csv)'}
                    </Button>
                  ))}
                  <button onClick={() => { setShowExportPreview(false); setShowColPicker(false) }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6b7280', padding: 4, borderRadius: 6, display: 'flex' }}><X size={16} /></button>
                </div>
              </div>

              {/* Table */}
              <div style={{ flex: 1, overflow: 'auto' }}>
                <table style={{ borderCollapse: 'collapse', fontSize: 12, minWidth: '100%' }}>
                  <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
                    <tr>
                      <th style={{ background: '#1e3a5f', border: '1px solid #2d4a6f', padding: '8px 10px', color: 'var(--ink-500)', fontSize: 11, fontWeight: 600, textAlign: 'center', minWidth: 36, whiteSpace: 'nowrap' }}>#</th>
                      {headers.map((h, i) => (
                        <th key={i} style={{ background: '#1e3a5f', border: '1px solid #2d4a6f', padding: '8px 14px', color: '#fff', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap', minWidth: 80 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dataRows.map((row, ri) => (
                      <tr key={ri} style={{ background: ri % 2 === 0 ? '#fff' : '#f8fafc' }}>
                        <td style={{ border: '1px solid #e5e7eb', padding: '6px 8px', color: 'var(--ink-500)', fontSize: 11, textAlign: 'center', userSelect: 'none' }}>{ri + 1}</td>
                        {row.map((cell, ci) => (
                          <td key={ci} style={{ border: '1px solid #e5e7eb', padding: '6px 14px', color: '#374151', whiteSpace: 'nowrap', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }} title={String(cell)}>
                            {String(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {dataRows.length === 0 && (
                      <tr><td colSpan={headers.length + 1} style={{ padding: 32, textAlign: 'center', color: 'var(--ink-500)' }}>No bills match the current filters.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )
      })()}
    </>
  )
}
