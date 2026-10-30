import { useState, useRef, useEffect, useMemo, useCallback, useId } from 'react'
import { useAuth } from '../auth/AuthContext'
import { useJurisdiction, useService } from '../auth/EntitlementsContext'
import { formatTenantMoney } from '../auth/jurisdiction'
import { Document, Page, pdfjs } from 'react-pdf'
import {
  X, RotateCcw, CheckCircle2, ChevronDown,
  Calendar, ZoomIn, ZoomOut, FileText, Plus, Trash2,
  ChevronLeft, ChevronRight, Database, Paperclip, Send, MessageSquare, Search, AlertTriangle, Check,
  Lock, ShieldCheck,
} from 'lucide-react'

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

import type { InvoicePayload, IngestResult, TdsBreakdown as TdsBreakdownData } from '../api/invoices'
import { ingestInvoice, billAction, fetchUserDepartments, fetchDepartments, fetchChainConfig, previewTds, fetchCostCenters } from '../api/invoices'
import type { ChainStepAPI, CostCenter } from '../api/invoices'
import TdsBreakdown from '../components/TdsBreakdown'
import { getAllCategories, addCustomCategory, syncCategoriesFromApi } from '../utils/categories'
import { gstinChecksumError, isValidGstinChecksum } from '../utils/gstin'
import type { QueueItem } from '../App'
import { validateForSubmission, blockingIssues } from './reviewValidation'
import { useUnsavedGuard } from '../hooks/useUnsavedGuard'
import { Badge, PdfLoading } from '../ui'

interface Props {
  data: InvoicePayload | null
  loading: boolean
  error: string
  file: File | null
  queue?: QueueItem[]
  queueIndex?: number
  onNav?: (i: number) => void
  onBack: () => void
  onDone: (result: IngestResult) => void
}

function F({ label, value, onChange, icon, type = 'text', error, ok, readOnly, hint }: {
  label: string; value: string | number | null | undefined
  onChange: (v: string) => void; icon?: React.ReactNode; type?: string
  error?: string | null; ok?: boolean
  // Set for values this screen reports rather than collects — our own company
  // identity, and a verified vendor's details. Editing them here would be a lie:
  // the value is re-derived from the master record on save either way.
  readOnly?: boolean; hint?: string
}) {
  // Every field on this page comes through here, so binding the label once fixes
  // all of them. Before this the <label> had no htmlFor and the input no id:
  // a screen reader announced ~20 unlabelled edit boxes, clicking a label did
  // not focus its field, and aria-invalid was set with no reason attached.
  const id = useId()
  const errId = `${id}-err`
  const hintId = `${id}-hint`
  return (
    <div className="f">
      <label className="f__lbl" htmlFor={id}>{label}</label>
      <div className={`f__wrap${error ? ' f__wrap--err' : ''}`}>
        <input
          id={id}
          className={`f__inp${error ? ' f__inp--err' : ''}${readOnly ? ' f__inp--ro' : ''}`}
          type={type}
          value={value ?? ''}
          onChange={e => onChange(e.target.value)}
          readOnly={readOnly}
          // readOnly, not disabled: a disabled input is skipped by tab order and
          // unreadable to a screen reader, and this value is worth reading.
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errId : (hint ? hintId : undefined)}
        />
        {error
          ? <span className="f__icon f__icon--err"><AlertTriangle size={13} /></span>
          : readOnly
            ? <span className="f__icon f__icon--ro" title={hint}><Lock size={12} /></span>
            : icon && <span className="f__icon">{icon}</span>}
        {/* The reason, reachable by assistive tech rather than hover-only. */}
        {error && <span className="f__tip" id={errId} role="alert">{error}</span>}
        {ok && !error && (
          <span className="f__badge" title="Valid GSTIN checksum" aria-label="Valid GSTIN checksum">
            <Check size={11} strokeWidth={3} />
          </span>
        )}
      </div>
      {hint && !error && <span className="f__hint" id={hintId}>{hint}</span>}
    </div>
  )
}

function Sec({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="sec">
      <div className="sec__title">{title}</div>
      {children}
    </div>
  )
}

type LineItem = { description: string; hsn_sac: string; quantity: number | null; unit: string; rate: number | null; amount: number | null; account_id?: string | null; ledger_name?: string | null; sap_item_code?: string | null; sap_material_desc?: string | null; sap_map_score?: number | null }
type SapCandidate = {
  item_code: string; material_desc: string; hsn_code: string | null; score: number; hsn_match: boolean
  model_confidence?: number; confidence_label?: 'high' | 'medium' | 'low'; ambiguous?: boolean
  num_match?: boolean; type_match?: boolean; reason?: string
}

// Below this confidence the backend has demoted the match (wrong product type ≤0.30,
// wrong numbers ≤0.35). Treat it as NO MATCH: don't auto-fill, show "map manually".
const SAP_NO_MATCH_FLOOR = 0.45

type LedgerAccount = { account_id: string; account_name: string; account_code: string | null; account_type: string | null }
type LedgerSuggestion = { account_id: string; account_name: string; account_code: string | null; confidence: 'high' | 'medium' | 'low' | 'none'; reason: string; uses: number }

// Full per-bill working state, cached so the AI recommendation runs once per
// bill and the user's edits survive navigating back and forth in the queue.
type ReviewSession = {
  d: InvoicePayload
  category: string
  notes: { text: string; ts: string }[]
  sapSugg: Record<number, SapCandidate[]>
  sapCursor: Record<number, number>
  suggestions: Record<number, LedgerSuggestion[]>
  supportingDocs: File[]
  noteText: string
  vendorMaster: VendorMaster | null
  buyerFromProfile: boolean
}

// Set when /api/enrich-invoice recognised the biller as a vendor we already hold
// verified details for. `matched_on: 'PAN'` means same legal entity, GSTIN we had
// not seen — a branch in another state — so the bill keeps the GSTIN it was
// printed with while name, email and bank details come from the master record.
type VendorMaster = {
  supplier_id: string
  matched_on: 'GSTIN' | 'PAN'
  pan: string
  name: string
  master_gstin: string
  email: string
  payment_terms_days: number | null
  // Vendor verification (KYB/GSTIN) and BANK verification are separate. A vendor can
  // be verified while we hold no verified account for it, in which case the bank
  // block below stays as the document printed it and offers a Verify Now action.
  bank_verified: boolean
}

export default function ReviewPage({ data, loading: ocrLoading, error: ocrError, file, queue = [], queueIndex = 0, onNav, onBack, onDone }: Props) {
  const { user, activeBrand } = useAuth()
  const [d, setD] = useState<InvoicePayload>(data ?? {})
  const [loading, setLoading]         = useState(false)
  const [error, setError]             = useState('')
  const [dupWarning, setDupWarning]   = useState('')
  const [bulkLoading, setBulkLoading] = useState(false)
  const [bulkDone, setBulkDone]       = useState(0)
  const [numPages, setNumPages]         = useState(0)
  const [pageNum, setPageNum]           = useState(1)
  const [zoomDelta, setZoomDelta]       = useState(0)
  const [category, setCategory] = useState('')
  const [categories, setCategories]       = useState<string[]>(getAllCategories)
  const [addingCategory, setAddingCategory] = useState(false)
  const [userDepts, setUserDepts]          = useState<string[]>([])
  const [selectedDept, setSelectedDept]    = useState<string>(user?.department ?? '')
  const [costCenters, setCostCenters]      = useState<CostCenter[]>([])
  const [costCenterId, setCostCenterId]    = useState<string>('')
  const [vendorMaster, setVendorMaster]    = useState<VendorMaster | null>(null)
  const [verifyingBank, setVerifyingBank]  = useState(false)
  const [bankVerifyErr, setBankVerifyErr]  = useState<string | null>(null)
  const [buyerFromProfile, setBuyerFromProfile] = useState(false)
  useEffect(() => { syncCategoriesFromApi(user?.company_id).then(setCategories) }, [user?.company_id])
  // Cost centers are optional + brand-scoped; only the active (enabled) ones are offered.
  useEffect(() => {
    if (!user?.company_id || !activeBrand?.id) { setCostCenters([]); return }
    fetchCostCenters(user.company_id, activeBrand.id, false).then(setCostCenters).catch(() => setCostCenters([]))
  }, [user?.company_id, activeBrand?.id])
  useEffect(() => {
    if (!user?.company_id) return
    const loader = user.role === 'admin'
      ? fetchDepartments(user.company_id)
      : fetchUserDepartments(user.username, user.company_id)
    loader.then(depts => {
      setUserDepts(depts)
      setSelectedDept(depts.includes(user.department ?? '') ? (user.department ?? '') : (depts[0] ?? user.department ?? ''))
    }).catch(() => {})
  }, [user?.username, user?.company_id, user?.role])
  const [newCategoryText, setNewCategoryText] = useState('')
  const [showApprovals, setShowApprovals] = useState(false)
  const [supportingDocs, setSupportingDocs] = useState<File[]>([])
  const [noteText, setNoteText]             = useState('')
  const [notes, setNotes]                   = useState<{ text: string; ts: string }[]>([])
  const docInputRef = useRef<HTMLInputElement>(null)
  const [ledgers, setLedgers]               = useState<LedgerAccount[]>([])
  const [suggestions, setSuggestions]       = useState<Record<number, LedgerSuggestion[]>>({})
  const [sapSugg, setSapSugg]               = useState<Record<number, SapCandidate[]>>({})
  const [sapCursor, setSapCursor]           = useState<Record<number, number>>({})
  const [sapLoading, setSapLoading]         = useState<Record<number, boolean>>({})
  const sapEnabled                          = useService('sap_matching')
  // Which country's rules apply (auth/jurisdiction.ts): GSTIN / PAN fields, the
  // CGST/SGST/IGST split and the TDS preview exist only where those packs do.
  const jur = useJurisdiction()
  const gstOn = jur.packs.gst, panOn = jur.packs.pan, tdsOn = jur.packs.tds_india
  const [vendorSap, setVendorSap]           = useState<{ supplier_code: string | null; supplier_name?: string | null; matched_by?: string; vendor_in_master?: boolean | null } | null>(null)
  const [sapDropOpen, setSapDropOpen]       = useState<number | null>(null)
  const [sapSearchOpen, setSapSearchOpen]   = useState<number | null>(null)
  const [sapSearchQ, setSapSearchQ]         = useState('')
  const [sapSearchRes, setSapSearchRes]     = useState<{ item_code: string; material_desc: string; hsn_code: string | null }[]>([])
  const [sapSearching, setSapSearching]     = useState(false)
  const sapSearchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [ledgerOpen, setLedgerOpen]         = useState<number | null>(null)
  const [ledgerSearch, setLedgerSearch]     = useState('')
  const [ledgerAnchor, setLedgerAnchor]     = useState<{ top: number; left: number; width: number } | null>(null)
  const ledgerBtnRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const [enriching, setEnriching]           = useState(false)
  const [chainSteps, setChainSteps]         = useState<ChainStepAPI[]>([])
  const [tdsPreview, setTdsPreview]         = useState<TdsBreakdownData | null>(null)
  const [tdsLoading, setTdsLoading]         = useState(false)

  useEffect(() => {
    fetchChainConfig(user?.company_id, selectedDept || undefined)
      .then(setChainSteps)
      .catch(() => setChainSteps([]))
  }, [selectedDept, user?.company_id])
  const [pageNaturalW, setPageNaturalW] = useState(0)
  const [pageNaturalH, setPageNaturalH] = useState(0)

  // Per-bill session cache. ReviewPage is a SINGLE persistent instance reused
  // across the whole upload queue (only `data`/`file`/`queueIndex` change on
  // Prev/Next), so a ref survives navigation. We snapshot each bill's full
  // working state here keyed by its File object — a stable identity that, unlike
  // the queue index, does NOT shift when a sent bill is removed from the queue.
  const sessionsRef = useRef<Map<File, ReviewSession>>(new Map())
  const prevFileRef = useRef<File | null>(file)
  const liveRef     = useRef<ReviewSession | null>(null)
  // Mirror the live state every render so it can be snapshotted on bill switch.
  liveRef.current = { d, category, notes, sapSugg, sapCursor, suggestions, supportingDocs, noteText, vendorMaster, buyerFromProfile }

  // Run the GPT recommendation for one invoice once, merging results into state.
  const runEnrich = useCallback((src: InvoicePayload) => {
    setEnriching(true)
    fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/enrich-invoice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        invoice: src,
        company_id: user?.company_id ?? null,
        // The buyer name on the bill is the brand being worked in, not the
        // tenant's legal name — see _tenant_buyer_block.
        brand_id: activeBrand?.id ?? null,
      }),
    })
      .then(r => r.json())
      .then(res => {
        // 1. Category
        if (res.category) setCategory(res.category)
        // 2. Notes
        if (res.notes) {
          const ts = new Date().toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' })
          setNotes([{ text: res.notes, ts }])
        }
        // 3. Vendor master match (null when no verified vendor shares this PAN)
        setVendorMaster(res.vendor_master ?? null)
        // Whether the buyer block below is genuinely our profile data. If the
        // lookup came back empty the fields keep whatever OCR read and stay
        // editable — labelling document data "From your company profile" and
        // locking it, as an earlier version did, is worse than not filling it.
        setBuyerFromProfile(!!(res.buyer?.gstin || res.buyer?.name))
        setD(prev => {
          const updated = { ...prev }
          // 4. Ledger per line item
          if (Array.isArray(res.line_items)) {
            const items = [...((prev.line_items as LineItem[]) ?? [])]
            res.line_items.forEach((r: { account_id: string | null; account_name: string | null }, i: number) => {
              if (items[i] && r.account_id) {
                items[i] = { ...items[i], account_id: r.account_id, ledger_name: r.account_name }
              }
            })
            updated.line_items = items
          }
          // 5. Buyer is our own company, served from our records. The document's
          //    "Bill To" block never wins — but only once we actually have the
          //    profile values in hand. Address is not returned and stays whatever
          //    OCR found.
          if (res.buyer?.gstin || res.buyer?.name) {
            updated.buyer = { ...((prev.buyer as Record<string, unknown>) ?? {}), ...res.buyer }
          }
          // 6. Vendor + bank from the verified master record. When the backend
          //    matched one it also OVERWRITES the invoice's bank block: printed
          //    bank details are the standard payment-redirection vector, and our
          //    verified account is the one we would pay to regardless. With no
          //    match, keep the old fill-only-if-empty behaviour.
          if (res.vendor) {
            updated.vendor = { ...((prev.vendor as Record<string, unknown>) ?? {}), ...res.vendor }
          }
          if (res.bank_details) {
            const existing = (prev.bank_details as Record<string, unknown>) ?? {}
            if (res.vendor_master || (!existing.account_no && !existing.bank_name)) {
              updated.bank_details = { ...existing, ...res.bank_details }
            }
          }
          return updated
        })
      })
      .catch(() => {})
      .finally(() => setEnriching(false))
  }, [user?.company_id])

  // On bill switch (or first mount): snapshot the outgoing bill, then either
  // RESTORE the incoming bill's cached state (recommendation already ran) or
  // INITIALIZE a fresh bill and run the recommendation exactly once.
  useEffect(() => {
    if (!data) return

    // Snapshot the bill we're leaving so its edits + recommendation persist.
    if (prevFileRef.current && prevFileRef.current !== file && liveRef.current) {
      sessionsRef.current.set(prevFileRef.current, liveRef.current)
    }
    prevFileRef.current = file

    const cached = file ? sessionsRef.current.get(file) : undefined
    if (cached) {
      // Already recommended once — restore the user's working state, no re-run.
      setD(cached.d)
      setCategory(cached.category)
      setNotes(cached.notes)
      setSapSugg(cached.sapSugg)
      setSapCursor(cached.sapCursor)
      setSuggestions(cached.suggestions)
      setSupportingDocs(cached.supportingDocs)
      setNoteText(cached.noteText)
      setVendorMaster(cached.vendorMaster)
      setBuyerFromProfile(cached.buyerFromProfile)
      return
    }

    // Fresh bill — reset per-bill state, infer due date, then recommend once.
    const withDue = { ...data }
    if (!withDue.due_date) {
      const computed = inferDueDate((withDue.invoice_date as string) || '', (withDue.payment_terms as string) || '')
      if (computed) withDue.due_date = computed
    }
    setD(withDue)
    setCategory('')
    setNotes([])
    setSapSugg({})
    setSapCursor({})
    setSuggestions({})
    setSupportingDocs([])
    setNoteText('')
    setVendorMaster(null)
    setBuyerFromProfile(false)
    // Transient, so deliberately not part of the cached ReviewSession — a stale
    // "Verifying…" or an error from the previous bill must not carry over.
    setVerifyingBank(false)
    setBankVerifyErr(null)
    if (!ocrLoading) runEnrich(data)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, file, ocrLoading])

  // Close ledger dropdown on outside click or scroll
  useEffect(() => {
    if (ledgerOpen === null) return
    const close = (e: MouseEvent) => {
      if (!(e.target as Element).closest('.rv__li-ledger-cell') && !(e.target as Element).closest('.rv__ledger-dropdown')) setLedgerOpen(null)
    }
    const onScroll = () => setLedgerOpen(null)
    document.addEventListener('mousedown', close)
    document.addEventListener('scroll', onScroll, true)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('scroll', onScroll, true) }
  }, [ledgerOpen])

  // Fetch all ledger accounts once for autocomplete
  useEffect(() => {
    const qs = user?.company_id ? `?company_id=${user.company_id}` : ''
    const baseUrl = import.meta.env.VITE_API_URL ?? ''
    fetch(`${baseUrl}/api/ledgers${qs}`)
      .then(r => r.json())
      .then(d => setLedgers(d.ledgers ?? []))
      .catch(() => {})
  }, [user?.company_id])

  // Live TDS preview — mirrors what the pipeline will compute after ingestion.
  const v       = (d.vendor as Record<string, unknown>) ?? {}
  const tx      = (d.taxes  as Record<string, unknown>) ?? {}
  const vName   = (v.name as string) || ''
  const vGstin  = (v.gstin as string) || ''
  const subAmt  = Number(d.subtotal as number) || 0
  const totAmt  = Number(d.total_amount as number) || 0
  const taxComponents =
    (Number(tx.cgst_amount as number) || 0) + (Number(tx.sgst_amount as number) || 0)
    + (Number(tx.igst_amount as number) || 0) + (Number(tx.cess_amount as number) || 0)
  const taxAmt  = subAmt > 0 && totAmt > subAmt
    ? totAmt - subAmt
    // A bill that prints one combined "GST @ 18%" line has a stated total and
    // no components, so summing alone read it as untaxed.
    : taxComponents || (Number(tx.total_tax_amount as number) || 0)
  const billDate = (d.invoice_date as string) || ''

  useEffect(() => {
    if (ocrLoading) return
    if (!tdsOn) { setTdsPreview(null); return }  // no Indian TDS for this tenant
    if (!vName && !vGstin) { setTdsPreview(null); return }
    if (totAmt <= 0) { setTdsPreview(null); return }
    let cancelled = false
    setTdsLoading(true)
    const t = setTimeout(() => {
      previewTds({
        company_id: user?.company_id,
        vendor_name: vName,
        vendor_gstin: vGstin,
        subtotal: subAmt,
        tax_amount: taxAmt,
        total_amount: totAmt,
        bill_date: /^\d{4}-\d{2}-\d{2}$/.test(billDate) ? billDate : undefined,
      })
        .then(res => { if (!cancelled) setTdsPreview(res) })
        .catch(() => { if (!cancelled) setTdsPreview(null) })
        .finally(() => { if (!cancelled) setTdsLoading(false) })
    }, 500)
    return () => { cancelled = true; clearTimeout(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vName, vGstin, subAmt, totAmt, taxAmt, billDate, ocrLoading, user?.company_id, tdsOn])

  // ── Unsaved-work protection (audit R1) ────────────────────────────────────
  // Everything the reviewer corrects lives in memory only. Treat the editor as
  // dirty from the first edit until the bill is sent, so a refresh, tab close or
  // Back cannot silently discard twenty minutes of OCR correction.
  const [touched, setTouched] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const dirty = touched && !submitted
  useUnsavedGuard(dirty)

  useEffect(() => {
    // A new bill in the queue starts clean.
    setTouched(false)
    setSubmitted(false)
  }, [file])

  const confirmDiscard = () => {
    if (!dirty) return true
    return window.confirm(
      'You have unsaved corrections on this bill. Leaving now discards them.\n\nLeave anyway?',
    )
  }
  const guardedBack = () => {
    if (confirmDiscard()) onBack()
  }

  /**
   * Whether the bill preview is showing.
   *
   * The control labelled "Hide Bill" used to call guardedBack — it left the
   * review editor entirely, after a discard prompt. A button that says it will
   * hide a panel must hide that panel; leaving the page is what the ✕ is for.
   */
  const [billShown, setBillShown] = useState(
    // On a phone the two panes stack, so showing the bill first would push every
    // field below the fold. Start on the form there; keep the split on desktop.
    () => !(typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 640px)').matches),
  )

  // ── Submission gate (audit R2) ────────────────────────────────────────────
  const issues = validateForSubmission(d as Record<string, unknown>, category)
  const blockers = blockingIssues(issues)
  const warnings = issues.filter(i => i.severity === 'warning')
  const canSubmit = blockers.length === 0

  // Fetch suggestions for each line item when description/hsn changes
  const fetchSuggestion = async (idx: number, item: LineItem) => {
    const supplierId = (d.vendor as Record<string,unknown>|undefined)?.supplier_id as string | undefined
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/ledgers/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          supplier_id: supplierId ?? null,
          hsn_sac: item.hsn_sac || null,
          description: item.description || null,
          company_id: user?.company_id ?? null,
        }),
      })
      const body = await res.json()
      setSuggestions(prev => ({ ...prev, [idx]: body.suggestions ?? [] }))
    } catch { /* silent */ }
  }

  // Loop-safe write of the chosen SAP item code onto a line item (functional update).
  const setLineSap = (idx: number, code: string | null, score?: number | null) =>
    setD(prev => {
      const items = [...((prev.line_items as LineItem[]) ?? [])]
      if (items[idx]) items[idx] = { ...items[idx], sap_item_code: code, sap_map_score: score ?? null }
      return { ...prev, line_items: items }
    })

  // Map a line item's "Description of Goods" → SAP item code via embeddings.
  const fetchSapMatch = async (idx: number, item: LineItem, force = false) => {
    const desc = (item.description ?? '').trim()
    if (!desc) return
    setSapLoading(prev => ({ ...prev, [idx]: true }))
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/mapping/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          description: desc,
          hsn_sac: item.hsn_sac || null,
          company_id: user?.company_id ?? null,
          top_k: 30,
        }),
      })
      const body = await res.json()
      const cands: SapCandidate[] = body.candidates ?? []
      setSapSugg(prev => ({ ...prev, [idx]: cands }))
      setSapCursor(prev => ({ ...prev, [idx]: 0 }))
      // Auto-fill the top candidate only when it clears the no-match floor and
      // the user hasn't already chosen/edited one.
      if (cands.length && cands[0].score >= SAP_NO_MATCH_FLOOR && !cands[0].ambiguous && (force || !item.sap_item_code))
        setLineSap(idx, cands[0].item_code, cands[0].score)
    } catch { /* silent */ }
    finally { setSapLoading(prev => ({ ...prev, [idx]: false })) }
  }

  // Open the inline "search all material descriptions" box for a line.
  const openSapSearch = (idx: number) => {
    setSapSearchOpen(idx); setSapSearchQ(''); setSapSearchRes([])
  }

  // Debounced free-text search over the whole catalog.
  const runSapSearch = (q: string) => {
    setSapSearchQ(q)
    if (sapSearchTimer.current) clearTimeout(sapSearchTimer.current)
    if (!q.trim()) { setSapSearchRes([]); return }
    sapSearchTimer.current = setTimeout(async () => {
      setSapSearching(true)
      try {
        const res = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/mapping/search`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q, company_id: user?.company_id ?? null, limit: 25 }),
        })
        setSapSearchRes((await res.json()).results ?? [])
      } catch { setSapSearchRes([]) }
      finally { setSapSearching(false) }
    }, 250)
  }

  // User picked a material from the catalog search → make it the chosen candidate.
  const pickFromSearch = (idx: number, r: { item_code: string; material_desc: string; hsn_code: string | null }) => {
    const chosen: SapCandidate = {
      item_code: r.item_code, material_desc: r.material_desc, hsn_code: r.hsn_code, score: 1,
      hsn_match: false, model_confidence: 1, confidence_label: 'high', ambiguous: false,
      type_match: true, reason: 'manually selected from catalog',
    }
    setSapSugg(prev => ({ ...prev, [idx]: [chosen, ...(prev[idx] ?? []).filter(c => c.item_code !== r.item_code)] }))
    setSapCursor(prev => ({ ...prev, [idx]: 0 }))
    setLineSap(idx, r.item_code, 1)
    setSapSearchOpen(null); setSapSearchQ(''); setSapSearchRes([])
  }

  // Map many line items in a SINGLE request (one embeddings + one GPT-4o-mini call).
  const fetchSapMatchBatch = async (items: LineItem[], onlyIdx?: number[]) => {
    const include = onlyIdx ? new Set(onlyIdx) : null
    const payload = items
      .map((li, index) => ({ index, description: (li.description ?? '').trim(), hsn_sac: li.hsn_sac || null }))
      .filter(it => it.description && (!include || include.has(it.index)))
    if (!payload.length) return
    const idxs = payload.map(p => p.index)
    setSapLoading(prev => { const n = { ...prev }; idxs.forEach(i => (n[i] = true)); return n })
    try {
      const res = await fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/mapping/match-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: payload, company_id: user?.company_id ?? null, top_k: 30 }),
      })
      const body = await res.json()
      const results: { index: number; candidates: SapCandidate[] }[] = body.results ?? []
      setSapSugg(prev => { const n = { ...prev }; results.forEach(r => (n[r.index] = r.candidates)); return n })
      setSapCursor(prev => { const n = { ...prev }; results.forEach(r => (n[r.index] = 0)); return n })
      setD(prev => {
        const arr = [...((prev.line_items as LineItem[]) ?? [])]
        results.forEach(r => {
          if (r.candidates.length && r.candidates[0].score >= SAP_NO_MATCH_FLOOR && !r.candidates[0].ambiguous
              && arr[r.index] && !arr[r.index].sap_item_code)
            arr[r.index] = { ...arr[r.index], sap_item_code: r.candidates[0].item_code, sap_map_score: r.candidates[0].score }
        })
        return { ...prev, line_items: arr }
      })
    } catch { /* silent */ }
    finally { setSapLoading(prev => { const n = { ...prev }; idxs.forEach(i => (n[i] = false)); return n }) }
  }

  // Auto-match every unmatched line item once OCR data has loaded — one batched request.
  const sapAutoKey = (((d.line_items as LineItem[]) ?? []).map(li => `${li.description ?? ''}|${li.hsn_sac ?? ''}`).join('§'))
  useEffect(() => {
    if (ocrLoading || !sapEnabled) return
    const items = (d.line_items as LineItem[]) ?? []
    const pending = items.map((li, i) => i).filter(i => (items[i].description ?? '').trim() && !(i in sapSugg))
    if (pending.length) fetchSapMatchBatch(items, pending)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sapAutoKey, ocrLoading, sapEnabled])

  // Resolve the bill's vendor SAP (supplier) code. The backend keys this on the
  // vendor GSTIN (authoritative) and only falls back to an item-derived GUESS
  // when no GSTIN is present — see /api/mapping/vendor-code. Recomputes whenever
  // the vendor GSTIN or the set of chosen item codes changes.
  const sapCodesKey = ((d.line_items as LineItem[]) ?? []).map(li => li.sap_item_code ?? '').filter(Boolean).join(',')
  useEffect(() => {
    if (!sapEnabled) { setVendorSap(null); return }
    const codes = (((d.line_items as LineItem[]) ?? []).map(li => li.sap_item_code).filter(Boolean)) as string[]
    // Need either a GSTIN to key on or some matched items to guess from.
    if (!vGstin && !codes.length) { setVendorSap(null); return }
    let cancelled = false
    fetch(`${import.meta.env.VITE_API_URL ?? ''}/api/mapping/vendor-code`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_codes: codes, vendor_gstin: vGstin || null, company_id: user?.company_id ?? null }),
    })
      .then(r => r.json())
      .then(res => { if (!cancelled) setVendorSap(res ?? null) })
      .catch(() => { if (!cancelled) setVendorSap(null) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sapCodesKey, vGstin, sapEnabled, user?.company_id])

  // Display state for the vendor SAP code field (authoritative GST match vs.
  // vendor-not-in-master vs. unconfirmed item guess).
  const vendorSapDisplay = (() => {
    const by = vendorSap?.matched_by
    if (by === 'gstin' && vendorSap?.supplier_code)
      return { value: vendorSap.supplier_code, placeholder: '', hint: vendorSap.supplier_name ?? '' }
    if (vendorSap?.vendor_in_master === false)
      return { value: '', placeholder: 'Not in SAP master', hint: 'Vendor GSTIN not found in the SAP vendor master' }
    if (by === 'item' && vendorSap?.supplier_code)
      return { value: `${vendorSap.supplier_code} · unconfirmed`,
               placeholder: '',
               hint: `Guess from matched items (${vendorSap.supplier_name ?? '—'}) — GSTIN absent, not the confirmed vendor` }
    return { value: '', placeholder: '— from vendor GSTIN —', hint: '' }
  })()

  const previewRef = useRef<HTMLDivElement>(null)
  const [containerH, setContainerH] = useState(0)
  const [containerW, setContainerW] = useState(0)

  useEffect(() => {
    const el = previewRef.current
    if (!el) return
    const update = () => { setContainerH(el.clientHeight); setContainerW(el.clientWidth) }
    const ro = new ResizeObserver(update)
    ro.observe(el)
    update()
    return () => ro.disconnect()
  }, [])

  // Memoize blob URL — creating a new URL on every render causes react-pdf to
  // treat it as a new file and reload the PDF on every keystroke / state change.
  const pdfUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file])

  const set = (key: string, val: unknown) => {
    setTouched(true)
    setD(prev => ({ ...prev, [key]: val }))
  }

  const inferDueDate = (billDate: string, paymentTerms: string): string => {
    if (!billDate) return ''
    // Try multiple date formats: dd-mm-yyyy, dd/mm/yyyy, yyyy-mm-dd, "20 Mar 2026"
    let base: Date | null = null
    const ddmmyyyy = billDate.match(/^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$/)
    if (ddmmyyyy) base = new Date(`${ddmmyyyy[3]}-${ddmmyyyy[2].padStart(2,'0')}-${ddmmyyyy[1].padStart(2,'0')}`)
    if (!base || isNaN(base.getTime())) base = new Date(billDate) // ISO or natural language
    if (!base || isNaN(base.getTime())) return ''
    let days = 30 // default
    if (paymentTerms) {
      const lower = paymentTerms.toLowerCase().trim()
      if (/immediate|due on receipt|upon receipt|cash/.test(lower)) days = 0
      else { const m = lower.match(/(\d+)/); if (m) days = parseInt(m[1]) }
    }
    base.setDate(base.getDate() + days)
    return base.toLocaleDateString('en-GB').split('/').join('-') // dd-mm-yyyy
  }

  const setWithDueDate = (key: 'invoice_date' | 'payment_terms', val: string) => {
    setD(prev => {
      const updated = { ...prev, [key]: val }
      if (!updated.due_date) {
        const billDate = (key === 'invoice_date' ? val : prev.invoice_date) as string
        const terms    = (key === 'payment_terms' ? val : prev.payment_terms) as string
        updated.due_date = inferDueDate(billDate || '', terms || '')
      }
      return updated
    })
  }
  const setN = (parent: string, key: string, val: unknown) =>
    setD(prev => ({ ...prev, [parent]: { ...((prev[parent] as Record<string, unknown>) ?? {}), [key]: val } }))

  const vendor     = (d.vendor       as Record<string, unknown>) ?? {}
  const buyer      = (d.buyer        as Record<string, unknown>) ?? {}
  const taxes      = (d.taxes        as Record<string, unknown>) ?? {}
  const bank       = (d.bank_details as Record<string, unknown>) ?? {}
  const lineItems  = ((d.line_items  as LineItem[]) ?? [])

  // Two independent facts. A vendor can be KYB-verified while we hold no verified
  // account for it: then the bank block is whatever the document printed, and must
  // stay editable and unbadged until someone verifies it.
  const bankVerified = !!vendorMaster?.bank_verified

  // Verify the printed account against the KYB provider right now, rather than
  // waiting for the bank_verification pipeline. On success the account is stored
  // against the vendor, so the next bill from them arrives already verified.
  const verifyBankNow = async () => {
    if (!vendorMaster) return
    const account_number = ((bank.account_no as string) ?? '').trim()
    const ifsc           = ((bank.ifsc as string) ?? '').trim()
    if (!account_number || !ifsc) {
      setBankVerifyErr('Enter both an account number and an IFSC before verifying.')
      return
    }
    setVerifyingBank(true); setBankVerifyErr(null)
    try {
      const r = await fetch(
        `${import.meta.env.VITE_API_URL ?? ''}/api/vendors/${vendorMaster.supplier_id}/verify-bank`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            account_number, ifsc,
            bank_name:    ((bank.bank_name as string) ?? '').trim() || null,
            account_name: vendorMaster.name,
          }),
        },
      )
      const res = await r.json().catch(() => ({}))
      if (!r.ok) {
        setBankVerifyErr(res.detail ?? 'Bank verification failed.')
        return
      }
      if (res.name_at_bank) setN('bank_details', 'account_name', res.name_at_bank)
      // persisted=false means the provider confirmed the account but we could not
      // record it — don't claim a stored verified account in that case.
      if (res.persisted) {
        setVendorMaster(vm => (vm ? { ...vm, bank_verified: true } : vm))
      } else {
        setBankVerifyErr('Account verified, but it could not be saved to the vendor record.')
      }
    } catch (e) {
      setBankVerifyErr(e instanceof Error ? e.message : 'Bank verification failed.')
    } finally {
      setVerifyingBank(false)
    }
  }

  // GST tax-type verification: the first 2 digits of a GSTIN are the state code.
  // Seller state ≠ buyer state → inter-state → IGST only; same state → intra-state
  // → CGST + SGST only. "verified" when the tax type charged matches the states.
  const gstTaxCheck = (() => {
    const stateOf = (g: string) => (g || '').trim().toUpperCase().slice(0, 2)
    const sellerState = stateOf(vendor.gstin as string)
    const buyerState  = stateOf(buyer.gstin as string)
    const valid = (s: string) => /^[0-9]{2}$/.test(s)
    const cgst = Number(taxes.cgst_amount as number) || 0
    const sgst = Number(taxes.sgst_amount as number) || 0
    const igst = Number(taxes.igst_amount as number) || 0
    if (!valid(sellerState) || !valid(buyerState))
      return { status: 'unknown' as const, text: 'Cannot verify — vendor/buyer GSTIN missing or invalid' }
    if (cgst === 0 && sgst === 0 && igst === 0) {
      // Not the same statement. One says the bill is untaxed; the other says
      // it is taxed and the document never printed the split, which is a thing
      // to go and read off the invoice rather than a clean bill of health.
      const stated = Number(taxes.total_tax_amount as number) || 0
      return stated > 0
        ? { status: 'unknown' as const,
            text: 'GST is charged but the invoice prints no CGST/SGST/IGST split — check the bill' }
        : { status: 'unknown' as const, text: 'No GST charged on this bill' }
    }
    const interState = sellerState !== buyerState
    const hasIgst    = igst > 0
    const hasCgstSgst = cgst > 0 || sgst > 0
    if (interState) {
      const ok = hasIgst && !hasCgstSgst
      return ok
        ? { status: 'ok' as const,  text: `Inter-state (${sellerState}→${buyerState}) — IGST applied ✓` }
        : { status: 'bad' as const, text: `Inter-state (${sellerState}→${buyerState}) requires IGST, but ${hasCgstSgst ? 'CGST/SGST was' : 'no IGST was'} charged` }
    }
    const ok = hasCgstSgst && !hasIgst
    return ok
      ? { status: 'ok' as const,  text: `Intra-state (${sellerState}) — CGST + SGST applied ✓` }
      : { status: 'bad' as const, text: `Intra-state (${sellerState}) requires CGST + SGST, but ${hasIgst ? 'IGST was' : 'CGST/SGST was not'} charged` }
  })()

  const updateLine = (i: number, key: string, val: unknown) => {
    const items = [...lineItems]
    items[i] = { ...items[i], [key]: val }
    set('line_items', items)
  }
  const addLine    = () => set('line_items', [...lineItems, { description: '', hsn_sac: '', quantity: null, unit: '', rate: null, amount: null }])
  const removeLine = (i: number) => set('line_items', lineItems.filter((_, idx) => idx !== i))

  const handleIngest = async () => {
    setLoading(true); setError(''); setDupWarning('')
    // Persist only the AUTHORITATIVE (GSTIN-matched) vendor SAP code — never the
    // item-derived guess, which is usually a different vendor than the bill's.
    const vendorSapToSave = vendorSap?.matched_by === 'gstin' ? (vendorSap.supplier_code ?? null) : null
    const payload = { ...d, vendor_sap_code: vendorSapToSave, _uploaded_by_name: user?.name, _uploaded_by_role: user?.role, _department: selectedDept || user?.department || null, _category: category || null }
    try {
      const result = await ingestInvoice(payload, user?.company_id, activeBrand?.id, costCenterId || null)
      if (result.is_duplicate) {
        setDupWarning(`A bill with invoice ID "${result.duplicate_invoice_number}" has already been uploaded. The existing record has been updated.`)
        setLoading(false)
        return
      }
      if (result.invoice_id && user) {
        const noteComment = notes.map(n => n.text).join('\n') || undefined
        await billAction(result.invoice_id, { action: 'submit', actor_role: user.role, actor_name: user.name, comment: noteComment })
        // upload supporting documents
        const API_BASE = import.meta.env.VITE_API_URL ?? ''
        for (const file of supportingDocs) {
          const fd = new FormData()
          fd.append('file', file)
          fd.append('uploaded_by', user.name ?? '')
          fd.append('company_id', user.company_id ?? '')
          await fetch(`${API_BASE}/api/bills/${result.invoice_id}/attachments`, { method: 'POST', body: fd })
        }
      }
      onDone(result)
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (e as { message?: string })?.message || 'Failed to send for approval')
    } finally { setLoading(false) }
  }

  const handleBulkIngest = async () => {
    if (!queue.length) return
    setBulkLoading(true); setBulkDone(0); setError('')
    try {
      await Promise.all(queue.map(async (item, i) => {
        const payload = i === queueIndex
          ? { ...d, _uploaded_by_name: user?.name, _uploaded_by_role: user?.role, _department: selectedDept || user?.department || null, _category: category || null }
          : { ...item.data, _uploaded_by_name: user?.name, _uploaded_by_role: user?.role, _department: selectedDept || user?.department || null }
        const result = await ingestInvoice(payload, user?.company_id, activeBrand?.id, costCenterId || null)
        if (!result.invoice_id) {
          throw new Error(`Bill ${i + 1}: ingest did not return an invoice ID`)
        }
        if (!result.is_duplicate && user) {
          await billAction(result.invoice_id, { action: 'submit', actor_role: user.role, actor_name: user.name })
        } else if (result.is_duplicate && user) {
          try {
            await billAction(result.invoice_id, { action: 'submit', actor_role: user.role, actor_name: user.name })
          } catch {
            // duplicate bill may already be past DRAFT — skip silently
          }
        }
        setBulkDone(n => n + 1)
      }))
      onBack()
    } catch (e: unknown) {
      setError((e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (e as { message?: string })?.message || 'Bulk ingest failed')
    } finally { setBulkLoading(false) }
  }

  /* Height-first fit: scale the PDF so its height fills the container, then
     derive width from the aspect ratio. Zoom shifts from that base.          */
  const fitWidth = (() => {
    if (!containerH || !pageNaturalW || !pageNaturalH) return 520
    const scaleByH = containerH / pageNaturalH
    const base = scaleByH * pageNaturalW
    return Math.max(280, base + zoomDelta)
  })()

  const total      = d.total_amount as number
  const currency   = (d.currency_code as string) || jur.base_currency
  const fmtTotal   = (v: number) => formatTenantMoney(v, jur, { currency })
  const invoiceNum = (d.invoice_number as string) || '—'
  const invoiceDate = (d.invoice_date as string) || '—'

  return (
    <div className="rv">

      {/* ── LEFT ─────────────────────────────────────────────── */}
      <div className="rv__left">

        {/* Skeleton overlay while OCR runs */}
        {ocrLoading && (
          <div className="rv__skeleton">

            {/* Header row */}
            <div className="sk-hdr">
              <div className="sk sk-circle" />
              <div style={{ flex: 1 }}>
                <div className="sk sk-h1" />
                <div className="sk sk-h2" style={{ marginTop: 6 }} />
                <div className="sk sk-h3" style={{ marginTop: 5 }} />
              </div>
              <div className="sk sk-badge" />
            </div>

            {/* Approval chip */}
            <div className="sk sk-chip" />

            {/* Timeline */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              <div className="sk-timeline">
                {[0,1,2,3].map(i => (
                  <div key={i} className="sk-tl-step">
                    <div className="sk sk-tl-dot" />
                    <div className="sk sk-tl-lbl" />
                    {i < 3 && <div className="sk-tl-line" />}
                  </div>
                ))}
              </div>
            </div>

            {/* Amount */}
            <div className="sk-sec">
              <div className="sk sk-amt-lbl" />
              <div className="sk sk-amt" style={{ marginTop: 5 }} />
            </div>

            {/* Vendor Details */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              <div className="sk-g2">
                {[...Array(6)].map((_,i) => <div key={i} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
              </div>
            </div>

            {/* Buyer Details */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              <div className="sk-g2">
                {[...Array(4)].map((_,i) => <div key={i} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
              </div>
              <div className="sk-field" style={{ marginTop: 8 }}><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>
            </div>

            {/* Bill Summary */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              <div className="sk-g2">
                {[...Array(6)].map((_,i) => <div key={i} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
              </div>
              <div className="sk-g3" style={{ marginTop: 8 }}>
                {[...Array(3)].map((_,i) => <div key={i} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
              </div>
            </div>

            {/* Tax Breakdown */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              <div className="sk-g3">
                {[...Array(8)].map((_,i) => <div key={i} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
              </div>
            </div>

            {/* Line Items */}
            <div className="sk-sec">
              <div className="sk sk-sec-title" />
              {[0,1].map(i => (
                <div key={i} className="sk-line-item">
                  <div className="sk sk-li-num" />
                  <div className="sk-g2" style={{ flex: 1 }}>
                    {[...Array(6)].map((_,j) => <div key={j} className="sk-field"><div className="sk sk-flbl"/><div className="sk sk-finp"/></div>)}
                  </div>
                </div>
              ))}
            </div>

            {/* AI processing label */}
            <div className="rv__sk-ocr-label">
              <div className="rv__sk-bars">
                <span className="bar" style={{ animationDelay: '0s' }} />
                <span className="bar" style={{ animationDelay: '0.15s' }} />
                <span className="bar" style={{ animationDelay: '0.3s' }} />
                <span className="bar" style={{ animationDelay: '0.45s' }} />
                <span className="bar" style={{ animationDelay: '0.6s' }} />
              </div>
              <span>Extracting invoice data with AI…</span>
            </div>

          </div>
        )}

        {/* OCR error banner */}
        {ocrError && !ocrLoading && (
          <div className="rv__err" style={{ marginBottom: 0 }}>OCR failed: {ocrError}</div>
        )}

        {/* Queue nav bar */}
        {queue.length > 1 && onNav && (
          <div className="rv__nav">
            <button className="rv__nav-btn" disabled={queueIndex <= 0} onClick={() => onNav(queueIndex - 1)}>
              <ChevronLeft size={14} /> Prev
            </button>
            <span className="rv__nav-info">{queueIndex + 1} / {queue.length}</span>
            <button className="rv__nav-btn" disabled={queueIndex >= queue.length - 1} onClick={() => onNav(queueIndex + 1)}>
              Next <ChevronRight size={14} />
            </button>
          </div>
        )}

        {/* Header */}
        <div className="rv__hdr" style={{ opacity: ocrLoading ? 0.35 : 1 }}>
          <button className="rv__close" onClick={onBack}><X size={15} /></button>
          <div className="rv__hdr-info">
            <h2 className="rv__num">{invoiceNum}</h2>
            <div className="rv__type">Tax Invoice</div>
            <div className="rv__meta">{invoiceDate}{user?.name ? ` · by ${user.name}` : ''}</div>
          </div>
          <button className="rv__reset" onClick={() => data && setD({ ...data })}><RotateCcw size={13} /> Reset</button>
        </div>

        {/* AI enrichment indicator */}
        {enriching && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: '#2563eb', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 6, padding: '5px 10px' }}>
            <span className="rv__spin-dark" style={{ width: 10, height: 10, borderWidth: 1.5 }} />
            Running Recommendation...
          </div>
        )}

        {/* Multi-Approval */}
        <div className="rv__apr-wrap">
          <button className="rv__apr-btn" onClick={() => setShowApprovals(s => !s)}>
            <CheckCircle2 size={13} color="#10b981" /> Multi-Approval <ChevronDown size={11} />
          </button>
          {showApprovals && (
            <div className="rv__apr-pop">
              <div className="rv__apr-ttl">Approval Roles</div>
              {[
                { level: 1, role: 'Approver' },
                { level: 2, role: 'Accountant' },
                { level: 3, role: 'Finance Controller' },
              ].map(a => (
                <div key={a.level} className="rv__apr-row">
                  <CheckCircle2 size={13} color="#10b981" />
                  <span>Level {a.level}: <strong>{a.role}</strong></span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Timeline */}
        <Sec title="Timeline">
          <div className="rv__tl">
            {[
              { label: 'Bill Uploaded', sub: user?.name ? `by ${user.name}` : '', done: true },
              ...chainSteps.filter(s => !s.locked).map(s => ({ label: s.label, sub: s.assignees?.join(', ') || '', done: false })),
              ...(chainSteps.filter(s => !s.locked).length === 0 ? [
                { label: 'Pending Approver', sub: '', done: false },
                { label: 'Pending Accountant', sub: '', done: false },
              ] : []),
              { label: 'Approved', sub: '', done: false },
            ].map((step, i) => (
              <div key={i} className="rv__tl-step">
                <div className={`rv__tl-dot ${step.done ? 'rv__tl-dot--done' : ''}`} />
                <div className="rv__tl-lbl">
                  <div>{step.label}</div>
                  {step.sub && <div>{step.sub}</div>}
                </div>
              </div>
            ))}
          </div>
        </Sec>

        {/* Amount */}
        <div>
          <div className="rv__amt-lbl">Requested Amount (incl. {jur.indirect_tax_label})</div>
          <div className="rv__amt">{total ? fmtTotal(total) : '—'}</div>
        </div>

        {/* Vendor Details */}
        <Sec title="Vendor Details">
          {/* On a PAN match the bill carries a GSTIN we had not seen — a branch in
              another state. The GSTIN field below stays as printed so the state code,
              and the inter-state IGST check that reads it, stay truthful. */}
          {vendorMaster && (
            <span className="rv__badge-ok" title={vendorMaster.matched_on === 'PAN'
              ? `Matched by PAN ${vendorMaster.pan}. This bill's GSTIN is new for that vendor and is kept as printed.`
              : `Matched by GSTIN. Details come from your vendor master.`}>
              <ShieldCheck size={12} /> Vendor Verified
            </span>
          )}
          <div className="g2">
            <div className="f">
              <label className="f__lbl">Vendor Name</label>
              <div className="f__wrap f__wrap--sel">
                <input className={`f__inp${vendorMaster ? ' f__inp--ro' : ''}`} value={(vendor.name as string) ?? ''}
                       readOnly={!!vendorMaster}
                       onChange={e => setN('vendor', 'name', e.target.value)} />
                {vendorMaster ? <Lock size={12} className="f__icon f__icon--ro" /> : <ChevronDown size={13} className="f__icon" />}
              </div>
            </div>
            <div className="f">
              <label className="f__lbl">Bank Detail</label>
              <div className="f__wrap f__wrap--sel">
                <input className={`f__inp${bankVerified ? ' f__inp--ro' : ''}`} value={(bank.bank_name as string) ?? ''}
                       readOnly={bankVerified}
                       onChange={e => setN('bank_details', 'bank_name', e.target.value)} />
                {bankVerified ? <Lock size={12} className="f__icon f__icon--ro" /> : <ChevronDown size={13} className="f__icon" />}
              </div>
            </div>
            {/* Stays editable even on a match: this is the GSTIN printed on THIS
                bill, which on a PAN match legitimately differs from the master's. */}
            {/* Abroad there is no GSTIN to checksum: the same field holds the
                vendor's own tax ID (VAT, EIN, TRN...) as printed. */}
            <F label={gstOn ? 'Vendor GST/PAN' : 'Vendor tax ID'}  value={vendor.gstin as string}  onChange={v => setN('vendor', 'gstin', v)}
               error={gstOn ? gstinChecksumError((vendor.gstin as string) || '') : null}
               ok={gstOn ? isValidGstinChecksum((vendor.gstin as string) || '') : false} />
            {sapEnabled && (
              <div className="f">
                <label className="f__lbl">Vendor SAP Code</label>
                <div className="f__wrap">
                  <input className="f__inp" readOnly value={vendorSapDisplay.value}
                         placeholder={vendorSapDisplay.placeholder}
                         title={vendorSapDisplay.hint} />
                </div>
              </div>
            )}
            <F label="Source of Supply" value={vendor.address as string || 'Karnataka'} onChange={v => setN('vendor', 'address', v)} />
            <F label="Vendor Phone"    value={vendor.phone as string}   onChange={v => setN('vendor', 'phone', v)} />
            <F label="Vendor Email"    value={vendor.email as string}   onChange={v => setN('vendor', 'email', v)}
               readOnly={!!vendorMaster} hint={vendorMaster ? 'Verified vendor record' : undefined} />
          </div>
        </Sec>

        {/* Buyer Details — us, not the document. These used to be OCR-filled and
            editable, but nothing downstream ever read them: GET /api/bills/{id}
            builds its buyer block from dim_tenants, so an edit here silently
            vanished on save. The name is the brand currently selected.

            Locked ONLY when the profile lookup actually returned something —
            otherwise these still hold whatever OCR read off "Bill To", and
            presenting that as company data would be a lie. Address is exempt
            either way: there is no tenant address on file yet. */}
        <Sec title="Buyer Details">
          <div className="g2">
            <F label="Buyer Name"   value={buyer.name as string}
               onChange={v => setN('buyer', 'name', v)}
               readOnly={buyerFromProfile}
               hint={buyerFromProfile ? (activeBrand?.name ? 'Brand you are working in' : 'From your company profile') : undefined} />
            {gstOn && <F label="Buyer GSTIN"  value={buyer.gstin as string}
               onChange={v => setN('buyer', 'gstin', v)}
               readOnly={buyerFromProfile}
               hint={buyerFromProfile ? 'From your company profile' : undefined}
               error={buyerFromProfile ? null : gstinChecksumError((buyer.gstin as string) || '')}
               ok={isValidGstinChecksum((buyer.gstin as string) || '')} />}
            {panOn && <F label="Buyer PAN"    value={buyer.pan as string}
               onChange={v => setN('buyer', 'pan', v)}
               readOnly={buyerFromProfile}
               hint={buyerFromProfile ? 'Derived from your GSTIN' : undefined} />}
          </div>
          <F label="Buyer Address" value={buyer.address as string} onChange={v => setN('buyer', 'address', v)} />
        </Sec>

        {/* Bill Summary */}
        <Sec title="Bill Summary">
          <div className="g2">
            <F label="Bill Title"  value={(d.notes as string) } onChange={v => set('notes', v)} />
            <div className="f">
              <label className="f__lbl">Category</label>
              {addingCategory ? (
                <div style={{ display: 'flex', gap: 4 }}>
                  <input
                    className="f__inp"
                    autoFocus
                    placeholder="New category name…"
                    value={newCategoryText}
                    onChange={e => setNewCategoryText(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter') {
                        const name = newCategoryText.trim()
                        if (name) {
                          addCustomCategory(name)
                          const updated = getAllCategories()
                          setCategories(updated)
                          setCategory(name)
                        }
                        setAddingCategory(false)
                        setNewCategoryText('')
                      } else if (e.key === 'Escape') {
                        setAddingCategory(false)
                        setNewCategoryText('')
                      }
                    }}
                    style={{ flex: 1 }}
                  />
                  <button
                    type="button"
                    style={{ padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', background: '#2563eb', color: '#fff', fontSize: 12, cursor: 'pointer' }}
                    onClick={() => {
                      const name = newCategoryText.trim()
                      if (name) {
                        addCustomCategory(name)
                        const updated = getAllCategories()
                        setCategories(updated)
                        setCategory(name)
                      }
                      setAddingCategory(false)
                      setNewCategoryText('')
                    }}
                  >Add</button>
                  <button
                    type="button"
                    style={{ padding: '0 8px', borderRadius: 6, border: '1px solid #d1d5db', background: '#f3f4f6', fontSize: 12, cursor: 'pointer' }}
                    onClick={() => { setAddingCategory(false); setNewCategoryText('') }}
                  >✕</button>
                </div>
              ) : (
                <div className="f__wrap f__wrap--sel">
                  <select
                    className="f__inp f__sel"
                    value={category}
                    onChange={e => {
                      if (e.target.value === '__add__') {
                        setAddingCategory(true)
                      } else {
                        setCategory(e.target.value)
                      }
                    }}
                  >
                    <option value="">Select category…</option>
                    {categories.map(c => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                    {user?.role === 'admin' && <option value="__add__">+ Add custom category…</option>}
                  </select>
                  <ChevronDown size={13} className="f__icon" />
                </div>
              )}
            </div>
            <div className="f">
              <label className="f__lbl">Department</label>
              {userDepts.length > 1 ? (
                <div className="f__wrap f__wrap--sel">
                  <select
                    className="f__inp f__sel"
                    value={selectedDept}
                    onChange={e => setSelectedDept(e.target.value)}
                  >
                    {userDepts.map(d => <option key={d} value={d}>{d}</option>)}
                  </select>
                  <ChevronDown size={13} className="f__icon" />
                </div>
              ) : (
                <input
                  className="f__inp"
                  value={selectedDept || user?.department || '—'}
                  readOnly
                  style={{ background: '#f9fafb', color: '#6b7280', cursor: 'default' }}
                />
              )}
            </div>
            {costCenters.length > 0 && (
              <div className="f">
                <label className="f__lbl">Cost Center</label>
                <div className="f__wrap f__wrap--sel">
                  <select
                    className="f__inp f__sel"
                    value={costCenterId}
                    onChange={e => setCostCenterId(e.target.value)}
                  >
                    <option value="">— Unassigned —</option>
                    {costCenters.map(cc => <option key={cc.id} value={cc.id}>{cc.name}</option>)}
                  </select>
                  <ChevronDown size={13} className="f__icon" />
                </div>
              </div>
            )}
            <F label="Bill Number" value={d.invoice_number as string} onChange={v => set('invoice_number', v)} />
            <F label="Bill Date"   value={d.invoice_date as string}   onChange={v => setWithDueDate('invoice_date', v as string)}  icon={<Calendar size={12} />} />
            <F label="Service Month" value={d.service_month as string} onChange={v => set('service_month', v)} type="month" icon={<Calendar size={12} />} />
            <F label="Due Date"    value={d.due_date as string}       onChange={v => set('due_date', v)}       icon={<Calendar size={12} />} />
            <F label="Payment Terms" value={d.payment_terms as string} onChange={v => setWithDueDate('payment_terms', v as string)} />
            <F label="Amount in Words" value={d.amount_in_words as string} onChange={v => set('amount_in_words', v)} />
          </div>
          <div className="g3">
            <F label="Subtotal"     value={d.subtotal as number}     onChange={v => set('subtotal', v)}     type="number" />
            <F label="Discount"     value={d.discount as number}     onChange={v => set('discount', v)}     type="number" />
            <F label="Total Amount" value={d.total_amount as number} onChange={v => set('total_amount', v)} type="number" />
          </div>
        </Sec>

        {/* Taxes */}
        <Sec title="Tax Breakdown">
          {/* GST tax-type verification — gated to SAP-enabled tenants (currently
              only Global Spirits, d0e0…0004), same per-tenant flag as SAP. */}
          {sapEnabled && gstOn && (() => {
            const c = gstTaxCheck
            const style = c.status === 'ok'
              ? { bg: '#f0fdf4', border: '#bbf7d0', fg: '#15803d', icon: '✓ GST Verified' }
              : c.status === 'bad'
              ? { bg: '#fef2f2', border: '#fecaca', fg: '#b91c1c', icon: '✗ GST Mismatch' }
              : { bg: '#f8fafc', border: '#e2e8f0', fg: '#64748b', icon: 'GST Check' }
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '0 0 14px',
                            padding: '9px 13px', borderRadius: 8,
                            background: style.bg, border: `1px solid ${style.border}` }}>
                <span style={{ fontWeight: 700, fontSize: 12.5, color: style.fg, whiteSpace: 'nowrap' }}>{style.icon}</span>
                <span style={{ fontSize: 12.5, color: style.fg }}>{c.text}</span>
              </div>
            )
          })()}
          <div className="g3">
            <F label="Taxable Amt"  value={taxes.taxable_amount as number} onChange={v => setN('taxes','taxable_amount',v)} type="number" />
            {/* The Indian GST split. Abroad the printed tax total below is the tax. */}
            {gstOn && <>
            <F label="CGST Rate %"  value={taxes.cgst_rate as number}      onChange={v => setN('taxes','cgst_rate',v)}      type="number" />
            <F label="CGST Amount"  value={taxes.cgst_amount as number}    onChange={v => setN('taxes','cgst_amount',v)}    type="number" />
            <F label="SGST Rate %"  value={taxes.sgst_rate as number}      onChange={v => setN('taxes','sgst_rate',v)}      type="number" />
            <F label="SGST Amount"  value={taxes.sgst_amount as number}    onChange={v => setN('taxes','sgst_amount',v)}    type="number" />
            <F label="IGST Rate %"  value={taxes.igst_rate as number}      onChange={v => setN('taxes','igst_rate',v)}      type="number" />
            <F label="IGST Amount"  value={taxes.igst_amount as number}    onChange={v => setN('taxes','igst_amount',v)}    type="number" />
            <F label="Cess Amount"  value={taxes.cess_amount as number}    onChange={v => setN('taxes','cess_amount',v)}    type="number" />
            </>}
            {/* What the document printed as its tax total. Filled only when the
                invoice states one — a bill showing a single "GST @ 18%" line
                and no split has this and no components, and ingest keeps it
                rather than posting the bill with zero tax. */}
            <F label={gstOn ? 'Total Tax (as printed)' : `${jur.indirect_tax_label} (as printed)`} value={taxes.total_tax_amount as number}
               onChange={v => setN('taxes','total_tax_amount',v)} type="number" />
            {/* Not a tax figure, but the thing that decides which taxes are
                correct: CGST+SGST within a state, IGST across one. */}
            {gstOn && <F label="Place of Supply" value={d.place_of_supply as string}
               onChange={v => setD(prev => ({ ...prev, place_of_supply: v }))} />}
          </div>
        </Sec>

        {/* Line Items */}
        <Sec title={`Line Items (${lineItems.length})`}>
          <div className="rv__li-wrap">
            <table className="rv__li-table">
              <thead>
                <tr>
                  <th className="rv__li-th rv__li-th--num rv__li-th--del">#</th>
                  <th className="rv__li-th rv__li-th--desc">Description</th>
                  <th className="rv__li-th rv__li-th--hsn">{gstOn ? 'HSN/SAC' : 'Item code'}</th>
                  <th className="rv__li-th rv__li-th--num rv__li-th--qty">Qty</th>
                  <th className="rv__li-th rv__li-th--unit">Unit</th>
                  <th className="rv__li-th rv__li-th--num rv__li-th--rate">Rate</th>
                  <th className="rv__li-th rv__li-th--num rv__li-th--amt">Amount</th>
                  <th className="rv__li-th rv__li-th--ldgr">Ledger</th>
                  {sapEnabled && <th className="rv__li-th rv__li-th--sap">SAP Item Code</th>}
                  <th className="rv__li-th rv__li-th--del" />
                </tr>
              </thead>
              <tbody>
                {lineItems.map((item, i) => {
                  const sugg = suggestions[i] ?? []
                  const top  = sugg[0] ?? null
                  const isOpen = ledgerOpen === i
                  const filtered = ledgerSearch
                    ? ledgers.filter(l => l.account_name.toLowerCase().includes(ledgerSearch.toLowerCase()) || (l.account_code ?? '').toLowerCase().includes(ledgerSearch.toLowerCase()))
                    : ledgers
                  const CONF_COLOR: Record<string, string> = { high: '#059669', medium: '#d97706', low: '#6b7280', none: '#9ca3af' }

                  return (
                    <tr key={i} className="rv__li-row">
                      <td className="rv__li-td rv__li-td--num">
                        <span className="rv__line-num">{i + 1}</span>
                      </td>
                      <td className="rv__li-td rv__li-td--desc">
                        <input className="rv__li-inp" value={item.description ?? ''} onChange={e => updateLine(i,'description',e.target.value)}
                          onBlur={() => { fetchSuggestion(i, item); fetchSapMatch(i, item, true) }}
                          placeholder="Description" />
                      </td>
                      <td className="rv__li-td">
                        <input className="rv__li-inp" value={item.hsn_sac ?? ''} onChange={e => updateLine(i,'hsn_sac',e.target.value)}
                          onBlur={() => { fetchSuggestion(i, item); fetchSapMatch(i, item, true) }}
                          placeholder={gstOn ? 'HSN/SAC' : 'Item code'} />
                      </td>
                      <td className="rv__li-td rv__li-td--num">
                        <input className="rv__li-inp rv__li-inp--num" type="number" value={item.quantity ?? ''} onChange={e => updateLine(i,'quantity', e.target.value === '' ? null : parseFloat(e.target.value))} placeholder="—" />
                      </td>
                      <td className="rv__li-td">
                        <input className="rv__li-inp" value={item.unit ?? ''} onChange={e => updateLine(i,'unit',e.target.value)} placeholder="—" />
                      </td>
                      <td className="rv__li-td rv__li-td--num">
                        <input className="rv__li-inp rv__li-inp--num" type="number" value={item.rate ?? ''} onChange={e => updateLine(i,'rate', e.target.value === '' ? null : parseFloat(e.target.value))} placeholder="—" />
                      </td>
                      <td className="rv__li-td rv__li-td--num">
                        <input className="rv__li-inp rv__li-inp--num" type="number" value={item.amount ?? ''} onChange={e => updateLine(i,'amount', e.target.value === '' ? null : parseFloat(e.target.value))} placeholder="0.00" />
                      </td>

                      {/* ── Ledger cell ── */}
                      <td className="rv__li-td rv__li-ledger-cell" style={{ minWidth: 180 }}>
                        {/* Selected badge or suggestion chip */}
                        {item.account_id ? (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: '#111827', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 6, padding: '2px 7px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140 }}>
                              {item.ledger_name ?? item.account_id}
                            </span>
                            <button onClick={() => updateLine(i, 'account_id', null)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 0, lineHeight: 1 }}>×</button>
                          </div>
                        ) : top ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                            <button
                              onClick={() => { updateLine(i, 'account_id', top.account_id); updateLine(i, 'ledger_name', top.account_name) }}
                              style={{ display: 'flex', alignItems: 'center', gap: 5, background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 6, padding: '3px 7px', cursor: 'pointer', textAlign: 'left' }}
                            >
                              <span style={{ fontSize: 10, fontWeight: 700, color: CONF_COLOR[top.confidence] ?? '#6b7280', textTransform: 'uppercase' }}>{top.confidence}</span>
                              <span style={{ fontSize: 11, color: '#111827', fontWeight: 600, maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{top.account_name}</span>
                            </button>
                            <span style={{ fontSize: 10, color: '#9ca3af', paddingLeft: 2 }}>{top.reason}</span>
                          </div>
                        ) : null}

                        {/* Search / pick button */}
                        <button
                          ref={el => { ledgerBtnRefs.current[i] = el }}
                          onClick={() => {
                            if (isOpen) { setLedgerOpen(null); return }
                            const rect = ledgerBtnRefs.current[i]?.getBoundingClientRect()
                            if (rect) setLedgerAnchor({ top: rect.bottom + 4, left: rect.left, width: 260 })
                            setLedgerOpen(i); setLedgerSearch('')
                          }}
                          style={{ marginTop: item.account_id || top ? 4 : 0, fontSize: 11, color: '#6b7280', background: 'none', border: '1px solid #e5e7eb', borderRadius: 6, padding: '2px 8px', cursor: 'pointer' }}
                        >
                          {item.account_id ? 'Change' : top ? 'Override' : '+ Pick ledger'}
                        </button>

                        {/* Dropdown — rendered via fixed positioning to escape overflow:hidden */}
                        {isOpen && ledgerAnchor && (
                          <div className="rv__ledger-dropdown" style={{ position: 'fixed', top: ledgerAnchor.top, left: ledgerAnchor.left, zIndex: 9999, width: ledgerAnchor.width, background: '#fff', border: '1px solid #e5e7eb', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', padding: 8 }}>
                            <input
                              autoFocus
                              value={ledgerSearch}
                              onChange={e => setLedgerSearch(e.target.value)}
                              placeholder="Search ledger…"
                              style={{ width: '100%', padding: '5px 8px', borderRadius: 6, border: '1px solid #e5e7eb', fontSize: 12, marginBottom: 6, boxSizing: 'border-box', outline: 'none' }}
                            />
                            {/* Suggestions first */}
                            {!ledgerSearch && sugg.length > 0 && (
                              <div style={{ marginBottom: 6 }}>
                                <div style={{ fontSize: 10, fontWeight: 700, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '0.05em', padding: '0 4px 4px' }}>Suggested</div>
                                {sugg.map(s => (
                                  <button key={s.account_id}
                                    onClick={() => { updateLine(i,'account_id',s.account_id); updateLine(i,'ledger_name',s.account_name); setLedgerOpen(null) }}
                                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', padding: '6px 8px', borderRadius: 6, border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left' }}
                                    onMouseEnter={e => (e.currentTarget.style.background = '#f3f4f6')}
                                    onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                                  >
                                    <span style={{ fontSize: 12, color: '#111827', fontWeight: 500 }}>{s.account_name}</span>
                                    <span style={{ fontSize: 10, color: CONF_COLOR[s.confidence], fontWeight: 700, textTransform: 'uppercase' }}>{s.confidence}</span>
                                  </button>
                                ))}
                                <div style={{ height: 1, background: '#f3f4f6', margin: '4px 0' }} />
                              </div>
                            )}
                            {/* All ledgers */}
                            <div style={{ maxHeight: 180, overflowY: 'auto' }}>
                              {filtered.length === 0 && <div style={{ fontSize: 12, color: '#9ca3af', padding: '8px 4px' }}>No ledgers found</div>}
                              {filtered.map(l => (
                                <button key={l.account_id}
                                  onClick={() => { updateLine(i,'account_id',l.account_id); updateLine(i,'ledger_name',l.account_name); setLedgerOpen(null) }}
                                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', padding: '6px 8px', borderRadius: 6, border: 'none', background: 'none', cursor: 'pointer', textAlign: 'left' }}
                                  onMouseEnter={e => (e.currentTarget.style.background = '#f3f4f6')}
                                  onMouseLeave={e => (e.currentTarget.style.background = 'none')}
                                >
                                  <span style={{ fontSize: 12, color: '#111827' }}>{l.account_name}</span>
                                  {l.account_code && <span style={{ fontSize: 10, color: '#9ca3af', fontFamily: 'monospace' }}>{l.account_code}</span>}
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </td>

                      {/* ── SAP item-code cell (gated per tenant by DB flag) ── */}
                      {sapEnabled && (
                      <td className="rv__li-td">
                        {(() => {
                          const cands = sapSugg[i] ?? []
                          const cur   = sapCursor[i] ?? 0
                          const cand  = cands[cur] ?? null
                          const loading = !!sapLoading[i]
                          // Model's own confidence (falls back to the match score if absent).
                          const conf = cand ? (cand.model_confidence ?? cand.score) : 0
                          const confPct = cand ? Math.round(conf * 100) : null
                          const label = cand?.confidence_label ?? (conf >= 0.8 ? 'high' : conf >= 0.5 ? 'medium' : 'low')
                          // Below the floor there is no trustworthy catalog item (product type
                          // absent or numbers off) — present it as "no match", not an answer.
                          const noMatch = !!cand && cand.score < SAP_NO_MATCH_FLOOR
                          // Model is torn between candidates → let the user choose from a list.
                          const ambiguous = !!cand && !!cand.ambiguous && !noMatch
                          const picked = item.sap_item_code
                          // Ambiguous AND not yet chosen → prompt the user to "Select one".
                          const needsChoice = ambiguous && !picked
                          const searchOpen = sapSearchOpen === i
                          const searchBtn = (
                            <button className="rv__sap-search-btn" onClick={() => openSapSearch(i)}
                              title="Search all material descriptions">
                              <Search size={11} /> Search
                            </button>
                          )
                          return (
                            <div className="rv__sap">
                              <input
                                className={`rv__sap-code${item.sap_item_code ? '' : ' rv__sap-code--empty'}`}
                                value={item.sap_item_code ?? ''}
                                onChange={e => setLineSap(i, e.target.value || null, item.sap_map_score ?? null)}
                                placeholder="— type code —"
                                title={cand?.material_desc ?? 'SAP item code'} />
                              {searchOpen ? (
                                <div className="rv__sap-search">
                                  <input className="rv__sap-search-input" autoFocus
                                    placeholder="Search material description…"
                                    value={sapSearchQ} onChange={e => runSapSearch(e.target.value)} />
                                  <div className="rv__sap-search-list">
                                    {sapSearching ? (
                                      <span className="rv__sap-loading">searching…</span>
                                    ) : sapSearchRes.length ? sapSearchRes.map(r => (
                                      <button key={r.item_code} className="rv__sap-pick-opt rv__sap-search-opt"
                                        onClick={() => pickFromSearch(i, r)} title={r.material_desc}>
                                        <span className="rv__sap-pick-code">{r.item_code}</span>
                                        <span className="rv__sap-search-desc">{r.material_desc}</span>
                                      </button>
                                    )) : sapSearchQ ? (
                                      <span className="rv__sap-loading">no results</span>
                                    ) : (
                                      // Nothing typed yet → keep the top-5 suggestions visible.
                                      cands.slice(0, 5).map((c, ci) => (
                                        <button key={c.item_code}
                                          className={`rv__sap-pick-opt${ci < 2 && !noMatch ? ' rv__sap-pick-opt--top' : ''}${picked === c.item_code ? ' rv__sap-pick-opt--on' : ''}`}
                                          onClick={() => {
                                            setSapCursor(prev => ({ ...prev, [i]: ci }))
                                            setLineSap(i, c.item_code, c.model_confidence ?? c.score)
                                            setSapSearchOpen(null)
                                          }} title={c.material_desc}>
                                          {picked === c.item_code && <CheckCircle2 size={12} />}
                                          {!noMatch &&
                                            <span className="rv__sap-pick-code">{Math.round((c.model_confidence ?? c.score) * 100)}%</span>}
                                          <span className="rv__sap-search-desc">{c.material_desc}</span>
                                        </button>
                                      ))
                                    )}
                                  </div>
                                  <button className="rv__sap-reject" onClick={() => setSapSearchOpen(null)}>Close</button>
                                </div>
                              ) : loading ? (
                                <span className="rv__sap-loading">matching…</span>
                              ) : cand ? (
                                <>
                                  <button className={`rv__sap-selected${needsChoice ? ' rv__sap-selected--warn' : ''}${noMatch ? ' rv__sap-selected--nomatch' : ''}`}
                                    title={noMatch ? 'No good match — pick closest or search' : needsChoice ? 'Pick the right material' : 'Click to change match'}
                                    onClick={() => setSapDropOpen(sapDropOpen === i ? null : i)}>
                                    {needsChoice ? (
                                      <span className="rv__sap-pick-label">⚠ Select one</span>
                                    ) : noMatch ? (
                                      <>
                                        <span className="rv__sap-badge rv__sap-badge--low">No match</span>
                                        <span className="rv__sap-desc">not in catalog</span>
                                      </>
                                    ) : label === 'high' ? (
                                      <span className="rv__sap-conf rv__sap-conf--high"><CheckCircle2 size={13} /> {confPct}%</span>
                                    ) : (
                                      <span className={`rv__sap-conf rv__sap-conf--${label}`}>{confPct}%</span>
                                    )}
                                    {!needsChoice && !noMatch &&
                                      <span className="rv__sap-desc" title={cand.reason || cand.material_desc}>{cand.material_desc}</span>}
                                    <ChevronDown size={13} className="rv__sap-caret" />
                                  </button>
                                  {sapDropOpen === i && (
                                    <div className="rv__sap-drop">
                                      <button className="rv__sap-drop-search"
                                        onClick={() => { setSapDropOpen(null); openSapSearch(i) }}>
                                        <Search size={11} /> Search all materials
                                      </button>
                                      {cands.slice(0, 5).map((c, ci) => (
                                        <button key={c.item_code}
                                          className={`rv__sap-pick-opt${ci < 2 ? ' rv__sap-pick-opt--top' : ''}${picked === c.item_code ? ' rv__sap-pick-opt--on' : ''}`}
                                          onClick={() => {
                                            setSapCursor(prev => ({ ...prev, [i]: ci }))
                                            setLineSap(i, c.item_code, c.model_confidence ?? c.score)
                                            setSapDropOpen(null)
                                          }} title={c.material_desc}>
                                          {picked === c.item_code && <CheckCircle2 size={12} />}
                                          {!noMatch &&
                                            <span className="rv__sap-pick-code">{Math.round((c.model_confidence ?? c.score) * 100)}%</span>}
                                          <span className="rv__sap-search-desc">{c.material_desc}</span>
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </>
                              ) : (
                                <div className="rv__sap-actions">{searchBtn}</div>
                              )}
                            </div>
                          )
                        })()}
                      </td>
                      )}

                      <td className="rv__li-td rv__li-td--num">
                        <button className="rv__line-del" onClick={() => removeLine(i)}><Trash2 size={12} /></button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Both addLine() and the .rv__add-line style have existed since this
              page was written, with nothing wired between them — so a line the
              OCR missed could not be added, and the only way out was to correct
              the PDF and upload it again. */}
          <button type="button" className="rv__add-line" onClick={addLine}>
            <Plus size={13} /> Add line item
          </button>

        </Sec>

        {/* TDS Breakdown (live preview) */}
        {tdsOn && (tdsLoading || tdsPreview) && (
          <Sec title="TDS">
            <TdsBreakdown data={tdsPreview} loading={tdsLoading && !tdsPreview} title="TDS Breakdown" />
          </Sec>
        )}

        {/* Bank Details — served from the vendor master once we have a verified
            account for this PAN, in which case the invoice's printed block is
            ignored rather than merged. Bank details on a document are the usual
            payment-redirection vector, and a verified account is what we would
            pay to regardless, so showing anything else here is misleading. */}
        <Sec title="Bank Details">
          {bankVerified ? (
            <span className="rv__badge-ok"><ShieldCheck size={12} /> Bank Verified</span>
          ) : (
            <div className="rv__bank-unver">
              <span className="rv__badge-warn"><AlertTriangle size={12} /> Bank not verified</span>
              {/* Verifying stores the account against the vendor, which needs a
                  vendor to store it against — so the action only exists on a match. */}
              {vendorMaster && (
                <button className="rv__verify-btn" disabled={verifyingBank}
                        onClick={verifyBankNow}>
                  {verifyingBank ? 'Verifying…' : 'Verify now'}
                </button>
              )}
            </div>
          )}
          {bankVerifyErr && <div className="rv__verify-err">{bankVerifyErr}</div>}
          <div className="g3">
            <F label="Bank Name"     value={bank.bank_name as string}     onChange={v => setN('bank_details','bank_name',v)}
               readOnly={bankVerified} hint={bankVerified ? 'Verified vendor record' : undefined} />
            <F label="Account No"    value={bank.account_no as string}    onChange={v => setN('bank_details','account_no',v)}
               readOnly={bankVerified} hint={bankVerified ? 'Verified vendor record' : undefined} />
            <F label="IFSC"          value={bank.ifsc as string}          onChange={v => setN('bank_details','ifsc',v)}
               readOnly={bankVerified} hint={bankVerified ? 'Verified vendor record' : undefined} />
          </div>
          {/* Beneficiary name as confirmed by penny-less verification — only exists
              once an account has actually been verified. */}
          {bankVerified && (bank.account_name as string) && (
            <F label="Account Name" value={bank.account_name as string} onChange={() => {}}
               readOnly hint="Name confirmed at the bank" />
          )}
        </Sec>

        {/* Upload Supporting Documents */}
        <Sec title="Supporting Documents">
          <input
            ref={docInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={e => {
              const files = Array.from(e.target.files ?? [])
              setSupportingDocs(prev => [...prev, ...files])
              e.target.value = ''
            }}
          />
          <button className="rv__doc-upload" onClick={() => docInputRef.current?.click()}>
            <Paperclip size={14} /> + Upload Document
          </button>
          <p className="rv__doc-hint">Upload supporting documents e.g. receipts, contracts etc.</p>
          {supportingDocs.length > 0 && (
            <div className="rv__doc-list">
              {supportingDocs.map((f, i) => (
                <div key={i} className="rv__doc-row">
                  <FileText size={13} color="#6b7280" />
                  <span className="rv__doc-name">{f.name}</span>
                  <span className="rv__doc-size">{(f.size / 1024).toFixed(1)} KB</span>
                  <button className="rv__doc-del" onClick={() => setSupportingDocs(prev => prev.filter((_, idx) => idx !== i))}>
                    <X size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Sec>

        {/* Notes */}
        <Sec title="Notes">
          {notes.length > 0 && (
            <div className="rv__notes-list">
              {notes.map((n, i) => (
                <div key={i} className="rv__note">
                  <MessageSquare size={12} color="#6b7280" />
                  <div className="rv__note-body">
                    <span className="rv__note-text">{n.text}</span>
                    <span className="rv__note-ts">{n.ts}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="rv__note-input-wrap">
            <input
              className="rv__note-input"
              placeholder="Enter your comment"
              value={noteText}
              onChange={e => setNoteText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && noteText.trim()) {
                  setNotes(prev => [...prev, { text: noteText.trim(), ts: new Date().toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }) }])
                  setNoteText('')
                }
              }}
            />
            <button
              className="rv__note-send"
              disabled={!noteText.trim()}
              onClick={() => {
                if (!noteText.trim()) return
                setNotes(prev => [...prev, { text: noteText.trim(), ts: new Date().toLocaleString('en-IN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: 'short' }) }])
                setNoteText('')
              }}
            >
              <Send size={13} /> Enter
            </button>
          </div>
        </Sec>

        {dupWarning && (
          <div className="rv__warn">
            <span>⚠️</span>
            <span>{dupWarning}</span>
          </div>
        )}
        {error && <div className="rv__err">{error}</div>}

        {/* Validation summary. Blocking problems are listed explicitly rather
            than leaving the user to guess why the button is inert. */}
        {(blockers.length > 0 || warnings.length > 0) && (
          <div className={`rv__validation${blockers.length ? ' rv__validation--blocked' : ''}`} role="status">
            <div className="rv__validation-title">
              {blockers.length > 0
                ? `${blockers.length} thing${blockers.length > 1 ? 's' : ''} to fix before this can be approved`
                : `${warnings.length} thing${warnings.length > 1 ? 's' : ''} worth checking`}
            </div>
            <ul className="rv__validation-list">
              {[...blockers, ...warnings].map(i => (
                <li key={i.field + i.message}>
                  <Badge tone={i.severity === 'error' ? 'danger' : 'warn'}>{i.label}</Badge>
                  <span>{i.message}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rv__actions">
          <button className="rv__btn rv__btn--ghost" onClick={guardedBack} disabled={loading || bulkLoading}>Cancel</button>
          <button
            className="rv__btn rv__btn--primary"
            onClick={() => { setSubmitted(true); handleIngest() }}
            disabled={loading || bulkLoading || !canSubmit}
            title={canSubmit ? undefined : 'Fix the highlighted problems first'}
          >
            {loading ? <><span className="rv__spin" />Sending…</> : <><Send size={14} />Send for Approval</>}
          </button>
          {queue.length > 1 && (
            <button
              className="rv__btn rv__btn--bulk"
              onClick={() => { setSubmitted(true); handleBulkIngest() }}
              disabled={loading || bulkLoading || !canSubmit}
            >
              {bulkLoading
                ? <><span className="rv__spin" />Saving {bulkDone}/{queue.length}…</>
                : <><Database size={14} />Send All {queue.length} for Approval</>}
            </button>
          )}
        </div>
      </div>

      {/* ── RIGHT: PDF ───────────────────────────────────────── */}
      {!billShown && (
        <button
          className="rv__show-btn"
          onClick={() => setBillShown(true)}
          aria-expanded={false}
          aria-controls="rv-bill-pane"
        >
          <FileText size={14} aria-hidden="true" /> Show bill
        </button>
      )}
      <div className="rv__right" id="rv-bill-pane" hidden={!billShown}>
        <div className="rv__pdf-bar">
          <button
            className="rv__hide-btn"
            onClick={() => setBillShown(false)}
            aria-expanded={true}
            aria-controls="rv-bill-pane"
          >
            Hide bill <ChevronDown size={12} aria-hidden="true" />
          </button>
          <div className="rv__pdf-controls">
            <button className="rv__ctrl" onClick={() => setZoomDelta(d => d + 60)}><ZoomIn size={15} /></button>
            <button className="rv__ctrl" onClick={() => setZoomDelta(d => d - 60)}><ZoomOut size={15} /></button>
            <div className="rv__ctrl-div" />
            <button className="rv__ctrl" onClick={() => { setZoomDelta(0); setPageNum(1) }}><RotateCcw size={15} /></button>
          </div>
          {numPages > 1 && (
            <div className="rv__page-nav">
              <button className="rv__ctrl" disabled={pageNum <= 1}      onClick={() => setPageNum(p => p - 1)}>‹</button>
              <span className="rv__page-info">{pageNum} / {numPages}</span>
              <button className="rv__ctrl" disabled={pageNum >= numPages} onClick={() => setPageNum(p => p + 1)}>›</button>
            </div>
          )}
        </div>

        <div className="rv__pdf-body" ref={previewRef}>
          {pdfUrl ? (
            <Document
              file={pdfUrl}
              onLoadSuccess={({ numPages }) => { setNumPages(numPages); setPageNum(1) }}
              loading={<PdfLoading stage="rendering" />}
              error={<div className="rv__pdf-msg"><FileText size={36} color="#9ca3af" /><span>Could not load PDF</span></div>}
            >
              <Page
                pageNumber={pageNum}
                width={fitWidth}
                onLoadSuccess={p => { setPageNaturalW(p.originalWidth); setPageNaturalH(p.originalHeight) }}
                renderAnnotationLayer={false}
                renderTextLayer={false}
              />
            </Document>
          ) : (
            <div className="rv__pdf-msg">
              <FileText size={44} color="#9ca3af" />
              <span>PDF Preview</span>
              <span className="rv__pdf-sub">Upload a file to see preview here</span>
            </div>
          )}
        </div>
      </div>

      <style>{`
        /* ── Layout ─────────────────────────────────────────── */
        .rv__validation {
          margin-top: 14px; padding: 12px 14px;
          background: var(--warn-50); border: 1px solid var(--warn-100);
          border-radius: var(--r-md);
        }
        .rv__validation--blocked {
          background: var(--danger-50); border-color: var(--danger-100);
        }
        .rv__validation-title {
          font-size: var(--fs-sm); font-weight: 700; color: var(--ink-800); margin-bottom: 8px;
        }
        .rv__validation-list {
          display: flex; flex-direction: column; gap: 6px;
          margin: 0; padding: 0; list-style: none;
        }
        .rv__validation-list li {
          display: flex; align-items: center; gap: 8px;
          font-size: var(--fs-xs); color: var(--ink-700);
        }
        .rv__btn--primary:disabled { opacity: 0.5; cursor: not-allowed; }

        .rv {
          display: flex;
          height: calc(100vh - 54px);
          overflow: hidden;
          /* Arrives with a pop: extraction now hands straight off to this editor,
             so the transition needs to read as "here is your invoice" rather than
             as an abrupt page swap. */
          animation: rv-pop 260ms cubic-bezier(0.2, 1.15, 0.4, 1) both;
        }

        @keyframes rv-pop {
          0% {
            opacity: 0;
            transform: scale(0.975) translateY(8px);
          }

          100% {
            opacity: 1;
            transform: scale(1) translateY(0);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .rv { animation: none; }
        }

        /* ── Left panel ─────────────────────────────────────── */
        .rv__left {
          position: relative;
          flex: 1 1 0%;
          min-width: 520px;
          overflow-y: auto;
          padding: 20px 22px 28px;
          border-right: 1px solid #e5e7eb;
          background: #fff;
          display: flex;
          flex-direction: column;
          gap: 18px;
        }

        /* Header */
        .rv__hdr { display: flex; align-items: flex-start; gap: 10px; }
        .rv__close {
          width: 26px; height: 26px; border-radius: 50%;
          background: #f3f4f6; border: none; color: #6b7280;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; margin-top: 3px; cursor: pointer;
        }
        .rv__close:hover { background: #e5e7eb; }
        .rv__hdr-info { flex: 1; }
        .rv__num  { font-size: 22px; font-weight: 800; color: #111827; }
        .rv__type { font-size: 13px; font-weight: 600; color: #374151; margin-top: 1px; }
        .rv__meta { font-size: 11px; color: #9ca3af; margin-top: 2px; }
        .rv__reset {
          display: flex; align-items: center; gap: 4px; margin-left: auto;
          background: none; border: none; color: #2563eb;
          font-size: 12px; font-weight: 600; cursor: pointer; flex-shrink: 0;
          font-family: inherit;
        }
        .rv__reset:hover { text-decoration: underline; }

        /* Approval */
        .rv__apr-wrap { position: relative; }
        .rv__apr-btn {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 5px 11px; border-radius: 20px;
          background: #f3f4f6; border: 1px solid #e5e7eb;
          font-size: 12px; font-weight: 600; color: #374151; cursor: pointer;
          font-family: inherit;
        }
        .rv__apr-btn:hover { background: #e5e7eb; }
        .rv__apr-pop {
          position: absolute; top: 34px; left: 0; z-index: 30;
          background: #1f2937; color: white; border-radius: 10px;
          padding: 12px 14px; width: 300px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.2);
        }
        .rv__apr-ttl { font-size: 11px; font-weight: 700; color: #9ca3af; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
        .rv__apr-row { display: flex; align-items: center; gap: 7px; font-size: 12px; color: #e5e7eb; padding: 2px 0; }
        .rv__apr-row strong { color: white; }

        /* Timeline */
        .sec { display: flex; flex-direction: column; gap: 8px; }
        .sec__title { font-size: 13px; font-weight: 700; color: #1f2937; }
        .rv__tl { display: flex; align-items: flex-start; }
        .rv__tl-step {
          display: flex; flex-direction: column; align-items: center;
          gap: 4px; flex: 1; position: relative;
        }
        .rv__tl-step:not(:last-child)::after {
          content: ''; position: absolute; top: 9px; left: 50%; right: -50%;
          height: 2px; background: #e5e7eb; z-index: 0;
        }
        .rv__tl-dot {
          width: 18px; height: 18px; border-radius: 50%;
          border: 2px solid #d1d5db; background: white;
          position: relative; z-index: 1; flex-shrink: 0;
        }
        .rv__tl-dot--done   { background: #10b981; border-color: #10b981; }
        .rv__tl-dot--active { border-color: #2563eb; }
        .rv__tl-lbl { font-size: 10px; color: #6b7280; text-align: center; line-height: 1.3; }

        /* Amount */
        .rv__amt-lbl { font-size: 11px; color: #6b7280; font-weight: 500; margin-bottom: 2px; }
        .rv__amt { font-size: 26px; font-weight: 800; color: #111827; }

        /* Grids */
        .g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .g3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

        /* Fields */
        .f { display: flex; flex-direction: column; gap: 3px; }
        .f__lbl { font-size: 10px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.4px; }
        .f__wrap { position: relative; }
        .f__inp {
          width: 100%; padding: 7px 9px; border: 1px solid #e5e7eb;
          border-radius: 6px; font-size: 12px; color: #1f2937;
          background: #f9fafb; outline: none; font-family: inherit;
        }
        .f__inp:focus { border-color: #2563eb; background: white; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
        .f__wrap--sel .f__inp { padding-right: 24px; }
        .f__sel { appearance: none; cursor: pointer; }
        .f__icon { position: absolute; right: 7px; top: 50%; transform: translateY(-50%); color: #9ca3af; pointer-events: none; }

        /* Invalid GSTIN checksum — red line + hover tooltip */
        .f__inp--err { border-color: #dc2626; background: #fef2f2; color: #b91c1c; padding-right: 26px; }
        .f__inp--err:focus { border-color: #dc2626; box-shadow: 0 0 0 2px rgba(220,38,38,0.12); background: #fff; }
        .f__icon--err { color: #dc2626; }
        .f__tip {
          position: absolute; top: calc(100% + 6px); left: 0; z-index: 30;
          max-width: 260px; padding: 6px 9px;
          background: #7f1d1d; color: #fff; font-size: 11px; line-height: 1.4;
          border-radius: 6px; box-shadow: 0 6px 16px rgba(0,0,0,0.2);
          opacity: 0; visibility: hidden; transform: translateY(-2px);
          transition: opacity .12s ease, transform .12s ease; pointer-events: none;
        }
        .f__wrap--err:hover .f__tip { opacity: 1; visibility: visible; transform: translateY(0); }

        /* Read-only — value comes from a master record, not this form. Reads as
           settled rather than broken: no red, no focus ring, cursor says "not here". */
        .f__inp--ro {
          background: #f3f4f6; color: #4b5563; border-style: dashed;
          cursor: default; padding-right: 24px;
        }
        .f__inp--ro:focus { border-color: #d1d5db; background: #f3f4f6; box-shadow: none; }
        .f__icon--ro { color: #9ca3af; }
        .f__hint { font-size: 10px; color: #9ca3af; line-height: 1.3; }

        /* Verified-vendor banner above a section whose values we served ourselves */
        .rv__vm-note {
          display: flex; align-items: flex-start; gap: 6px;
          margin-bottom: 8px; padding: 7px 9px;
          background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px;
          font-size: 11px; line-height: 1.45; color: #166534;
        }
        .rv__vm-note svg { flex: none; margin-top: 1px; color: #16a34a; }
        .rv__vm-note strong { font-weight: 600; }
        /* Compact verified pill. The long sentences these replaced repeated the
           vendor name and PAN already shown in the fields directly below. */
        .rv__badge-ok {
          display: inline-flex; align-items: center; gap: 5px;
          margin-bottom: 8px; padding: 3px 9px;
          background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 999px;
          font-size: 11px; font-weight: 600; color: #166534;
        }
        .rv__badge-ok svg { flex: none; color: #16a34a; }
        .rv__bank-unver {
          display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
          margin-bottom: 8px;
        }
        .rv__badge-warn {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 3px 9px;
          background: #fffbeb; border: 1px solid #fde68a; border-radius: 999px;
          font-size: 11px; font-weight: 600; color: #92400e;
        }
        .rv__badge-warn svg { flex: none; color: #d97706; }
        .rv__verify-btn {
          padding: 3px 11px;
          background: #2563eb; color: #fff; border: none; border-radius: 6px;
          font-size: 11px; font-weight: 600; cursor: pointer; white-space: nowrap;
        }
        .rv__verify-btn:disabled { background: #93c5fd; cursor: default; }
        .rv__verify-err { color: #b91c1c; font-size: 11px; margin-bottom: 8px; }

        /* Valid GSTIN checksum — green tick badge on top-right corner */
        .f__badge {
          position: absolute; top: -6px; right: -6px; z-index: 20;
          display: flex; align-items: center; justify-content: center;
          width: 16px; height: 16px; border-radius: 50%;
          background: #16a34a; color: #fff;
          border: 1.5px solid #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        }

        /* Line items — table */
        .rv__li-wrap {
          border: 1px solid #e5e7eb; border-radius: 8px;
          overflow: hidden; overflow-x: auto;
        }
        .rv__li-table {
          width: 100%; border-collapse: collapse; font-size: 12px; min-width: 960px; table-layout: auto;
        }
        .rv__li-th {
          padding: 8px 10px;
          font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 0.04em; color: #6b7280;
          background: #f9fafb; border-bottom: 1px solid #e5e7eb;
          text-align: left; white-space: nowrap;
        }
        .rv__li-th--num { text-align: right; }
        /* Numeric columns get just enough fixed width to show their values; the
           text-heavy columns (Description, Ledger, SAP) are auto so they flex
           and absorb whatever space is left at any given time. */
        .rv__li-th--desc { width: auto; min-width: 180px; }
        .rv__li-th--hsn  { width: 76px; }
        .rv__li-th--qty  { width: 92px; min-width: 92px; }
        .rv__li-th--unit { width: 60px; }
        .rv__li-th--rate { width: 88px; }
        .rv__li-th--amt  { width: 96px; }
        .rv__li-th--ldgr { width: auto; min-width: 150px; }
        .rv__li-th--sap  { width: auto; min-width: 200px; }
        .rv__li-th--del  { width: 36px; }
        /* SAP item-code cell */
        .rv__sap { display: flex; flex-direction: column; gap: 4px; }
        .rv__sap-code {
          width: 100%; box-sizing: border-box; padding: 5px 8px;
          border: 1px solid #e5e7eb; border-radius: 6px; background: #fff;
          font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          font-size: 12px; font-weight: 600; color: #111827; outline: none;
        }
        .rv__sap-code:focus { border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
        .rv__sap-code--empty { color: #9ca3af; font-weight: 400; }
        .rv__sap-meta { display: flex; align-items: flex-start; gap: 6px; min-height: 16px; }
        .rv__sap-badge {
          font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 999px;
          white-space: nowrap; flex-shrink: 0;
        }
        .rv__sap-badge--high { background: #ecfdf5; color: #059669; }
        .rv__sap-badge--mid  { background: #fffbeb; color: #d97706; }
        .rv__sap-badge--low  { background: #f3f4f6; color: #9ca3af; }
        /* model-confidence indicator (replaces the raw match %) */
        .rv__sap-conf {
          display: inline-flex; align-items: center; gap: 3px;
          font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 10px; white-space: nowrap;
        }
        .rv__sap-conf--high   { background: #ecfdf5; color: #059669; }
        .rv__sap-conf--medium { background: #fffbeb; color: #d97706; }
        .rv__sap-conf--low    { background: #f3f4f6; color: #6b7280; }
        /* ambiguous: pick-one list */
        .rv__sap-pick { display: flex; flex-direction: column; gap: 3px; }
        .rv__sap-pick-label { font-size: 10px; font-weight: 700; color: #d97706; }
        .rv__sap-pick-opt {
          display: flex; align-items: center; gap: 5px; text-align: left;
          border: 1px solid #e5e7eb; background: #fff; border-radius: 6px;
          padding: 3px 6px; cursor: pointer; font-size: 10px; color: #374151; max-width: 100%;
        }
        .rv__sap-pick-opt:hover { border-color: #2563eb; background: #f8fafc; }
        .rv__sap-pick-opt--top { border-color: #34d399; background: #f0fdf4; }
        .rv__sap-pick-opt--top .rv__sap-pick-code { color: #059669; }
        .rv__sap-pick-opt--on { border-color: #059669; background: #ecfdf5; color: #059669; }
        .rv__sap-pick-opt svg { flex: 0 0 auto; color: #059669; }
        .rv__sap-pick-code { font-weight: 700; white-space: nowrap; }
        .rv__sap-pick-desc { color: #6b7280; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        /* inline "search all materials" */
        .rv__sap-search-btn {
          display: inline-flex; align-items: center; gap: 3px;
          border: none; background: none; color: #2563eb; cursor: pointer;
          font-size: 10px; font-weight: 600; padding: 0;
        }
        .rv__sap-search-btn:hover { text-decoration: underline; }
        .rv__sap-search { display: flex; flex-direction: column; gap: 4px; }
        .rv__sap-search-input {
          border: 1px solid #2563eb; border-radius: 6px; padding: 4px 8px; font-size: 11px;
          outline: none; box-shadow: 0 0 0 2px rgba(37,99,235,0.1);
        }
        .rv__sap-search-list { display: flex; flex-direction: column; gap: 3px; max-height: 168px; overflow-y: auto; }
        /* selected match → click to open the top-5 dropdown */
        .rv__sap-selected {
          display: flex; align-items: flex-start; gap: 6px; width: 100%;
          background: none; border: none; padding: 0; cursor: pointer; text-align: left;
        }
        .rv__sap-caret { color: #9ca3af; margin-left: auto; flex: 0 0 auto; margin-top: 1px; }
        .rv__sap-selected:hover .rv__sap-caret { color: #2563eb; }
        .rv__sap-selected--warn { border: 1px solid #fcd34d; background: #fffbeb; border-radius: 6px; padding: 3px 6px; }
        .rv__sap-selected--nomatch { border: 1px solid #e5e7eb; background: #f9fafb; border-radius: 6px; padding: 3px 6px; }
        .rv__sap-drop {
          display: flex; flex-direction: column; gap: 3px; margin-top: 2px;
          border: 1px solid #e5e7eb; border-radius: 6px; padding: 3px; background: #fff;
          max-height: 200px; overflow-y: auto;
        }
        .rv__sap-drop-search {
          display: flex; align-items: center; gap: 4px;
          border: none; background: none; color: #2563eb; cursor: pointer;
          font-size: 10px; font-weight: 600; padding: 4px 6px; text-align: left;
          border-bottom: 1px solid #f3f4f6; margin-bottom: 1px;
        }
        .rv__sap-drop-search:hover { background: #f8fafc; }
        /* search results: description only, wrapped to new lines (no code, no truncation) */
        .rv__sap-search-opt { align-items: flex-start; }
        .rv__sap-search-desc { color: #374151; white-space: normal; overflow-wrap: anywhere; line-height: 1.35; text-align: left; }
        .rv__sap-desc {
          font-size: 10px; color: #6b7280; flex: 1;
          white-space: normal; overflow-wrap: anywhere; line-height: 1.35;
        }
        .rv__sap-actions { display: flex; align-items: center; gap: 8px; }
        .rv__sap-reject {
          font-size: 10px; font-weight: 600; color: #dc2626; background: none;
          border: none; padding: 0; cursor: pointer; white-space: nowrap;
        }
        .rv__sap-reject:disabled { color: #d1d5db; cursor: default; }
        .rv__sap-rank { font-size: 9px; color: #9ca3af; white-space: nowrap; }
        .rv__sap-loading { font-size: 10px; color: #9ca3af; font-style: italic; }
        .rv__li-row:not(:last-child) td { border-bottom: 1px solid #f3f4f6; }
        .rv__li-row:hover td { background: #fafafe; }
        .rv__li-td { padding: 5px 6px; vertical-align: middle; }
        .rv__li-td--num { text-align: right; }
        .rv__li-td--desc { min-width: 160px; }
        .rv__li-inp {
          width: 100%; padding: 5px 7px;
          border: 1px solid transparent; border-radius: 5px;
          font-size: 12px; color: #1f2937; background: transparent;
          outline: none; font-family: inherit; min-width: 0; box-sizing: border-box;
        }
        .rv__li-inp:focus { border-color: #2563eb; background: white; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
        .rv__li-inp--num { text-align: right; }
        /* Hide the type=number spin buttons: they steal ~18px inside the box, which
           on a narrow numeric column clipped large values (a 129558 qty showed as
           "58"). Values are typed here, and arrow keys still step. */
        .rv__li-inp--num::-webkit-outer-spin-button,
        .rv__li-inp--num::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
        .rv__li-inp--num { -moz-appearance: textfield; appearance: textfield; }
        .rv__line-num {
          width: 20px; height: 20px; border-radius: 50%;
          background: #2563eb; color: white;
          font-size: 10px; font-weight: 700;
          display: inline-flex; align-items: center; justify-content: center;
        }
        .rv__line-del {
          width: 24px; height: 24px; border-radius: 5px;
          border: 1px solid #e5e7eb; background: none;
          color: #9ca3af; cursor: pointer;
          display: inline-flex; align-items: center; justify-content: center;
        }
        .rv__line-del:hover { background: #fee2e2; border-color: #ef4444; color: #ef4444; }
        .rv__add-line {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 7px 12px; border: 1px dashed #2563eb;
          border-radius: 6px; background: #eff6ff;
          color: #2563eb; font-size: 12px; font-weight: 600;
          cursor: pointer; font-family: inherit; width: fit-content;
        }
        .rv__add-line:hover { background: #2563eb; color: white; }

        /* Supporting docs */
        .rv__doc-upload {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 7px 14px; border: 1.5px dashed #2563eb;
          border-radius: 7px; background: #eff6ff;
          color: #2563eb; font-size: 13px; font-weight: 600;
          cursor: pointer; font-family: inherit;
          transition: background 0.12s;
        }
        .rv__doc-upload:hover { background: #dbeafe; }
        .rv__doc-hint { font-size: 11px; color: #9ca3af; margin: 0; }
        .rv__doc-list { display: flex; flex-direction: column; gap: 5px; }
        .rv__doc-row {
          display: flex; align-items: center; gap: 8px;
          padding: 7px 10px; background: #f9fafb;
          border: 1px solid #e5e7eb; border-radius: 6px;
        }
        .rv__doc-name { flex: 1; font-size: 12px; color: #374151; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rv__doc-size { font-size: 11px; color: #9ca3af; flex-shrink: 0; }
        .rv__doc-del {
          width: 20px; height: 20px; border-radius: 4px;
          border: 1px solid #e5e7eb; background: white; color: #9ca3af;
          display: flex; align-items: center; justify-content: center;
          cursor: pointer; flex-shrink: 0;
        }
        .rv__doc-del:hover { background: #fee2e2; color: #ef4444; border-color: #fecaca; }

        /* Notes */
        .rv__notes-list { display: flex; flex-direction: column; gap: 6px; margin-bottom: 6px; }
        .rv__note {
          display: flex; align-items: flex-start; gap: 8px;
          padding: 8px 10px; background: #f9fafb;
          border: 1px solid #e5e7eb; border-radius: 7px;
        }
        .rv__note-body { display: flex; flex-direction: column; gap: 2px; flex: 1; }
        .rv__note-text { font-size: 13px; color: #1f2937; }
        .rv__note-ts   { font-size: 10px; color: #9ca3af; }
        .rv__note-input-wrap {
          display: flex; align-items: center; gap: 8px;
          border: 1px solid #e5e7eb; border-radius: 8px;
          background: #f9fafb; padding: 6px 10px;
          transition: border-color 0.12s;
        }
        .rv__note-input-wrap:focus-within { border-color: #2563eb; background: white; box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
        .rv__note-input {
          flex: 1; border: none; background: transparent; outline: none;
          font-size: 13px; color: #1f2937; font-family: inherit;
        }
        .rv__note-input::placeholder { color: #9ca3af; }
        .rv__note-send {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 5px 11px; border-radius: 6px;
          background: #2563eb; color: white;
          border: none; font-size: 12px; font-weight: 600;
          cursor: pointer; font-family: inherit; flex-shrink: 0;
          transition: background 0.12s;
        }
        .rv__note-send:hover:not(:disabled) { background: #1d4ed8; }
        .rv__note-send:disabled { opacity: 0.4; cursor: not-allowed; }

        /* Error / actions */
        .rv__err { font-size: 12px; color: #ef4444; background: #fee2e2; padding: 8px 12px; border-radius: 6px; }
        .rv__warn { font-size: 12px; color: #92400e; background: #fef3c7; border: 1px solid #f59e0b; padding: 10px 14px; border-radius: 6px; display: flex; gap: 8px; align-items: flex-start; line-height: 1.5; }
        /* The primary action stays reachable. On a long invoice "Send for
           Approval" used to scroll off the bottom of the form, so the one thing
           the page exists to do required hunting for it. */
        .rv__actions {
          position: sticky;
          bottom: 0;
          z-index: 5;
          display: flex;
          gap: 8px;
          margin: 4px -22px -28px;
          padding: 12px 22px;
          border-top: 1px solid #e5e7eb;
          background: rgb(255 255 255 / 92%);
          backdrop-filter: blur(6px);
          flex-wrap: wrap;
        }

        /* Reopens the preview once it has been hidden — without this the pane
           was gone for the rest of the session. */
        .rv__show-btn {
          position: absolute;
          top: 12px;
          right: 16px;
          z-index: 6;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          border: 1px solid #d1d5db;
          border-radius: 8px;
          background: #fff;
          font-family: inherit;
          font-size: 12px;
          font-weight: 600;
          color: #374151;
          cursor: pointer;
          box-shadow: 0 1px 3px rgb(15 23 42 / 8%);
        }

        .rv__show-btn:hover { border-color: #9ca3af; }

        /* ── Responsive ───────────────────────────────────────────────────────
           The editor was a fixed two-pane split with a 520px minimum on the form
           and no media queries at all, so below ~900px the bill preview pushed
           the form off screen and the page scrolled sideways. Under 1024px the
           two stack, and the preview — now genuinely collapsible — starts hidden
           on a phone where there is no room for it beside the fields. */
        @media (max-width: 1024px) {
          .rv {
            flex-direction: column;
            height: auto;
            min-height: calc(100vh - 54px);
            overflow: visible;
          }

          .rv__left {
            min-width: 0;
            border-right: none;
            border-bottom: 1px solid #e5e7eb;
          }

          .rv__right {
            width: auto;
            min-width: 0;
            max-height: 70vh;
          }
        }

        @media (max-width: 640px) {
          .rv__left { padding: 16px 14px 22px; }

          .rv__actions {
            margin: 4px -14px -22px;
            padding: 10px 14px;
          }

          .rv__actions .rv__btn { flex: 1 1 auto; justify-content: center; }
        }
        .rv__btn {
          display: inline-flex; align-items: center; gap: 6px;
          padding: 10px 18px; border-radius: 6px; border: none;
          font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit;
        }
        .rv__btn--primary { background: #2563eb; color: white; }
        .rv__btn--primary:hover:not(:disabled) { background: #1d4ed8; }
        .rv__btn--primary:disabled { opacity: 0.55; cursor: not-allowed; }
        .rv__btn--ghost { background: none; border: 1px solid #e5e7eb; color: #4b5563; }
        .rv__btn--ghost:hover:not(:disabled) { background: #f3f4f6; }
        .rv__btn--bulk { background: #059669; color: white; }
        .rv__btn--bulk:hover:not(:disabled) { background: #047857; }
        .rv__btn--bulk:disabled { opacity: 0.55; cursor: not-allowed; }

        /* Queue nav */
        .rv__nav {
          display: flex; align-items: center; gap: 8px;
          padding: 8px 12px; background: #f0f9ff;
          border: 1px solid #bae6fd; border-radius: 8px;
        }
        .rv__nav-btn {
          display: inline-flex; align-items: center; gap: 3px;
          padding: 5px 12px; border-radius: 5px;
          border: 1px solid #d1d5db; background: white;
          font-size: 12px; font-weight: 600; color: #374151;
          cursor: pointer; font-family: inherit;
        }
        .rv__nav-btn:hover:not(:disabled) { background: #f3f4f6; }
        .rv__nav-btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .rv__nav-info { flex: 1; text-align: center; font-size: 12px; font-weight: 600; color: #0369a1; }
        .rv__spin {
          width: 12px; height: 12px; border-radius: 50%;
          border: 2px solid rgba(255,255,255,0.3); border-top-color: white;
          animation: rvSpin 0.7s linear infinite; display: inline-block;
        }

        /* ── Right: PDF ─────────────────────────────────────── */
        .rv__right {
          flex: 0 0 auto;
          width: fit-content;
          min-width: 300px;
          display: flex;
          flex-direction: column;
          background: #e5e7eb;
          overflow: hidden;
        }

        .rv__pdf-bar {
          display: flex; align-items: center; gap: 8px;
          padding: 8px 14px; background: white;
          border-bottom: 1px solid #e5e7eb;
          flex-shrink: 0;
          min-width: 0;
        }
        .rv__hide-btn {
          display: flex; align-items: center; gap: 5px;
          padding: 5px 12px; border-radius: 20px;
          border: 1px solid #d1d5db; background: white;
          font-size: 12px; font-weight: 600; color: #374151; cursor: pointer;
          font-family: inherit;
        }
        .rv__hide-btn:hover { background: #f3f4f6; }
        .rv__pdf-controls { display: flex; align-items: center; gap: 3px; margin-left: auto; }
        .rv__ctrl {
          width: 28px; height: 28px; border-radius: 5px;
          border: 1px solid #e5e7eb; background: none;
          color: #4b5563; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          font-size: 16px; font-family: inherit;
        }
        .rv__ctrl:hover:not(:disabled) { background: #f3f4f6; }
        .rv__ctrl:disabled { opacity: 0.4; cursor: not-allowed; }
        .rv__ctrl-div { width: 1px; height: 18px; background: #e5e7eb; margin: 0 3px; }
        .rv__page-nav { display: flex; align-items: center; gap: 4px; }
        .rv__page-info { font-size: 12px; color: #4b5563; font-weight: 500; white-space: nowrap; }

        /* PDF body — height-fitted; horizontal scroll only if zoomed in */
        .rv__pdf-body {
          flex: 1;
          overflow-x: auto;
          overflow-y: hidden;
          display: flex;
          align-items: flex-start;
          justify-content: flex-start;
        }
        .rv__pdf-body .react-pdf__Document { display: flex; flex-direction: column; gap: 0; }
        .rv__pdf-body .react-pdf__Page canvas { display: block; }

        .rv__pdf-msg {
          display: flex; flex-direction: column;
          align-items: center; gap: 8px;
          color: #9ca3af; font-size: 13px;
        }
        .rv__pdf-sub { font-size: 11px; }
        .rv__spin-dark {
          width: 14px; height: 14px; border-radius: 50%;
          border: 2px solid #d1d5db; border-top-color: #2563eb;
          animation: rvSpin 0.7s linear infinite; display: inline-block;
        }
        @keyframes rvSpin { to { transform: rotate(360deg); } }

        /* ── Skeleton ───────────────────────────────────────── */
        .rv__skeleton {
          position: absolute; inset: 0;
          background: white; z-index: 10;
          padding: 20px 22px 32px;
          display: flex; flex-direction: column; gap: 18px;
          overflow-y: auto;
        }

        /* shimmer base */
        .sk {
          border-radius: 6px;
          background: linear-gradient(90deg, #f1f2f6 0%, #e4e6ef 40%, #eceef5 60%, #f1f2f6 100%);
          background-size: 300% 100%;
          animation: shimmer 1.6s ease-in-out infinite;
        }
        @keyframes shimmer {
          0%   { background-position: 100% 0; }
          100% { background-position: -100% 0; }
        }

        /* header row */
        .sk-hdr { display: flex; align-items: flex-start; gap: 12px; }
        .sk-circle { width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0; margin-top: 2px; }
        .sk-h1  { height: 22px; width: 60%; border-radius: 5px; }
        .sk-h2  { height: 13px; width: 35%; border-radius: 4px; }
        .sk-h3  { height: 11px; width: 50%; border-radius: 4px; }
        .sk-badge { width: 52px; height: 22px; border-radius: 11px; flex-shrink: 0; margin-left: auto; margin-top: 3px; }

        /* chip */
        .sk-chip { height: 26px; width: 140px; border-radius: 13px; }

        /* section */
        .sk-sec { display: flex; flex-direction: column; gap: 10px; }
        .sk-sec-title { height: 13px; width: 30%; border-radius: 4px; }

        /* timeline */
        .sk-timeline { display: flex; align-items: center; }
        .sk-tl-step  { display: flex; flex-direction: column; align-items: center; gap: 5px; position: relative; flex: 1; }
        .sk-tl-step:last-child { flex: 0; }
        .sk-tl-dot   { width: 18px; height: 18px; border-radius: 50%; flex-shrink: 0; }
        .sk-tl-lbl   { height: 9px; width: 60px; border-radius: 4px; }
        .sk-tl-line  { position: absolute; top: 9px; left: 50%; right: -50%; height: 2px; background: #e5e7eb; z-index: 0; }

        /* amount */
        .sk-amt-lbl { height: 11px; width: 45%; border-radius: 4px; }
        .sk-amt     { height: 28px; width: 50%; border-radius: 5px; }

        /* grids */
        .sk-g2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .sk-g3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

        /* individual field */
        .sk-field { display: flex; flex-direction: column; gap: 4px; }
        .sk-flbl  { height: 9px; width: 55%; border-radius: 3px; }
        .sk-finp  { height: 32px; border-radius: 6px; }

        /* line item block */
        .sk-line-item {
          display: flex; gap: 8px; align-items: flex-start;
          background: #f9fafb; border: 1px solid #e5e7eb;
          border-radius: 7px; padding: 10px;
        }
        .sk-li-num { width: 20px; height: 20px; border-radius: 50%; flex-shrink: 0; margin-top: 18px; }

        /* AI label */
        .rv__sk-ocr-label {
          display: flex; align-items: center; gap: 10px;
          font-size: 12px; color: #6b7280;
          background: #eff6ff; border: 1px solid #dbeafe;
          border-radius: 8px; padding: 10px 14px;
          margin-top: 4px;
        }
        .rv__sk-bars {
          display: flex; align-items: flex-end; gap: 3px; height: 16px; flex-shrink: 0;
        }
        .rv__sk-bars .bar {
          width: 3px; border-radius: 2px;
          background: #2563eb;
          animation: barBounce 0.9s ease-in-out infinite;
        }
        .rv__sk-bars .bar:nth-child(1) { height: 6px; }
        .rv__sk-bars .bar:nth-child(2) { height: 10px; }
        .rv__sk-bars .bar:nth-child(3) { height: 14px; }
        .rv__sk-bars .bar:nth-child(4) { height: 10px; }
        .rv__sk-bars .bar:nth-child(5) { height: 6px; }
        @keyframes barBounce {
          0%, 100% { transform: scaleY(0.4); opacity: 0.5; }
          50%       { transform: scaleY(1);   opacity: 1; }
        }
      `}</style>
    </div>
  )
}
