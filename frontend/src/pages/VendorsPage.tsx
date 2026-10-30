import { useEffect, useLayoutEffect, useMemo, useRef, useState, lazy, Suspense, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { RefreshCw, AlertCircle, Mail, FileText, Plus, Upload, X, CheckCircle2, ShieldCheck, ShieldAlert, Link2, Building2, BadgeCheck, ArrowRightLeft, Paperclip, Sparkles, ChevronRight, Eye, Pencil, ZoomIn, ZoomOut, RotateCcw } from 'lucide-react'
import { EyeOff, Trash2, AlertTriangle, Info } from 'lucide-react'
import { Document, Page, pdfjs } from 'react-pdf'

// Same worker ReviewPage.tsx points react-pdf at — set independently here too
// since this page can be the first (or only) one loaded that renders a PDF.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()
import { useAuthedObjectUrl, downloadAuthed, fetchAuthedBlob } from '../api/authedFiles'
import { fetchVendors, fetchVendor, createVendor, bulkCreateVendorsExcel, invalidateVendorsCache, fetchBills, fetchVendorBankAccounts, addVendorBankAccount, deleteVendorBankAccount, setPrimaryBankAccount, fetchVendorContacts, addVendorContact, deleteVendorContact, setPrimaryVendorContact, fetchEmailConnection, startEmailAuthorize, type EmailConnection, updateVendor, fetchSupplierGstins, addSupplierGstin, reverifySupplierGstin, reverifyPrimaryGstin, deleteSupplierGstin, fetchVendorOverrides, createVendorOverride, deleteVendorOverride, submitCatchupDecision, fetchFyState, fetchVendorCategories, fetchTdsSections, fetchVendorPanDuplicates, mergeVendors, deferVendorDedup, type Vendor, type Bill, type BankAccount, type VendorContact, type SupplierGstin, type VendorTdsOverride, type VendorTdsFyState, type TdsSection, type CatchupSummary, type PanDuplicate } from '../api/invoices'
import { ConfirmDialog, PdfLoading, useScrollLock, useColumnFilters, FilterTh, FilterStatus, LoadMore, Pager, useInfiniteRows, usePagedRows, type FilterField } from '../ui'
import { PHONE_QUERY, useMediaQuery } from '../hooks/useMediaQuery'
import { useNavigate, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { useJurisdiction, useService } from '../auth/EntitlementsContext'
import { PATHS, VENDOR_TABS, findingPath, payPath, vendorPath, type VendorTab } from '../routes/paths'
import './vendors.css'
import PdfFullscreen from '../components/PdfFullscreen'
import { Check, CalendarDays, IdCard, Copy, FolderOpen, History, Landmark, LayoutGrid, MessageSquare, Receipt, Shield, Star, TrendingUp, UserPlus, Wallet } from 'lucide-react'
import { deleteVendorDocument, fetchVendorDocuments, fetchVendorInbox, type VendorDocument, type VendorInbox as VendorInboxData } from '../api/vendorCard'
import { money, setBooksCurrency, summarize } from '../components/vendor/vendorStats'
import { BankMark } from '../components/vendor/bankLogos'
import {
  Avatar, ContractsCard, KpiStrip, OpenBills, PaymentSchedule, SectionHead, SideSection, SpendCard, SpendView, ThreadsCard,
  initials as nameInitials, today, type LoadStatus,
} from '../components/vendor/VendorViews'
import { LogoEditor, VendorLogo } from '../components/vendor/VendorLogo'
import VendorDocuments, { type BillFilter, type DocFolder } from '../components/vendor/VendorDocuments'
import VendorInbox from '../components/vendor/VendorInbox'
import VendorActivity from '../components/vendor/VendorActivity'
import '../components/vendor/vendorCard.css'
import VendorCryptoWallets from '../components/crypto/VendorCryptoWallets'

// Loaded when an attachment preview is opened, not on first paint: this is the
// one component that pulls in the ~400 kB xlsx parser.
const ExcelPreview = lazy(() => import('../components/ExcelPreview').then(m => ({ default: m.ExcelPreview })))

const API_BASE = import.meta.env.VITE_API_URL ?? ''

const STATUS_STYLE: Record<string, { bg: string; color: string }> = {
  VERIFIED:   { bg: '#def7ea', color: '#1d7a5f' },
  UNVERIFIED: { bg: '#fff3d6', color: '#9a5b00' },
  BLOCKED:    { bg: '#fde8e8', color: '#b42318' },
}

/** Whole units in the tenant's books currency (set from useJurisdiction() via setBooksCurrency). */
function fmtCurrency(val: number) {
  return money(val)
}

function fmtDate(val: string | null) {
  if (!val) return '—'
  return new Date(val).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

// Derive a human-friendly filename (always with an extension) for a bill's
// stored document, used both for previewing and for the email attachment name.
function billFileName(b: Bill): string {
  const raw = b.original_file?.split(/[\\/]/).pop()?.trim()
  if (raw) return /\.[a-z0-9]+$/i.test(raw) ? raw : `${raw}.pdf`
  return `Bill_${b.id || b.invoice_id || 'document'}.pdf`
}


const GSTN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/

/** The last four digits only — how an account is named anywhere it isn't asked for in full. */
const maskAcct = (no: string | null | undefined) => `•••• ${(no ?? '').replace(/\s+/g, '').slice(-4)}`

/** Backend statuses in words; the raw codes ("PENDING", "FAILED") aren't for people. */
const STATUS_WORDS: Record<string, string> = {
  VERIFIED: 'Verified', UNVERIFIED: 'Not verified', PENDING: 'Verification pending', FAILED: 'Verification failed', BLOCKED: 'Blocked',
}
const statusWords = (s: string | null | undefined) => (s ? STATUS_WORDS[s.toUpperCase()] ?? s.charAt(0) + s.slice(1).toLowerCase() : 'Not verified')

/** The server's own reason (axios or fetch-shaped), not "Request failed with status code 422". */
function apiError(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  // FastAPI validation errors arrive as a list of { msg }.
  if (Array.isArray(detail) && detail.length) return detail.map(d => (d as { msg?: string })?.msg).filter(Boolean).join('; ') || fallback
  return fallback
}

// Layers opened from the vendor card, newest last. Only the top one answers
// Escape and keeps Tab inside itself — otherwise Escape in a preview would
// also close the card underneath, and two traps would fight over focus.
const layerStack: HTMLElement[] = []
/** How long a document delete waits for Undo before it's sent. Matches the info toast. */
const UNDO_MS = 5000
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** A modal this stack doesn't manage (a shared Modal, the upload dialog, the add-contact form) is open over us. */
function foreignModalOpen(node: HTMLElement) {
  return Array.from(document.querySelectorAll<HTMLElement>('[aria-modal="true"]'))
    .some(el => el !== node && !el.contains(node) && !layerStack.includes(el))
}

function isTopLayer(node: HTMLElement) {
  if (layerStack[layerStack.length - 1] !== node || foreignModalOpen(node)) return false
  // Focus inside some other dialog (the logo picker): that one owns the keys.
  const host = (document.activeElement as HTMLElement | null)?.closest?.('[aria-modal="true"], [role="dialog"]')
  return !host || host === node || host.contains(node)
}

/**
 * Focus moves in on open and back on close, Tab stays inside, Escape asks to
 * close. Listens in the bubble phase so a nested dialog that handles Escape
 * itself (and stops it) is left to do so.
 */
function useLayer(ref: { current: HTMLElement | null }, onEscape: () => void) {
  const esc = useRef(onEscape)
  esc.current = onEscape
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const before = document.activeElement as HTMLElement | null
    layerStack.push(node)
    if (!node.contains(document.activeElement) && !foreignModalOpen(node)) node.focus({ preventScroll: true })
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isTopLayer(node)) return
      if (e.key === 'Escape') {
        // Escape in a text box belongs to that box (a composer closing itself, say).
        const t = e.target as HTMLElement | null
        if (t && (t.tagName === 'TEXTAREA' || t.isContentEditable)) return
        e.preventDefault()
        esc.current()
        return
      }
      if (e.key !== 'Tab') return
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (items.length === 0) { e.preventDefault(); node.focus(); return }
      const first = items[0], last = items[items.length - 1]
      if (!node.contains(document.activeElement)) { e.preventDefault(); first.focus() }
      else if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) { e.preventDefault(); last.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      const i = layerStack.lastIndexOf(node)
      if (i >= 0) layerStack.splice(i, 1)
      if (before?.isConnected) before.focus?.({ preventScroll: true })
    }
  }, [ref])
}

// ── Migrate Brand Modal ────────────────────────────────────────────────────────
function MigrateBrandModal({ vendor, supplierIds, companyId, onClose, onDone }: {
  vendor?: Vendor               // single vendor
  supplierIds?: string[]        // bulk: list of supplier_ids
  companyId?: string
  onClose: () => void
  onDone: () => void
}) {
  const { brands } = useAuth()
  const isBulk = !vendor
  const initialBrandId = vendor?.brand_id ?? null
  const [selectedBrandId, setSelectedBrandId] = useState<string | null>(initialBrandId)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const ids = isBulk ? (supplierIds ?? []) : [vendor!.supplier_id]

  const handleSave = async () => {
    setSaving(true); setErr('')
    try {
      const res = await fetch(`${API_BASE}/api/vendors/${ids[0]}/brand`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ company_id: companyId, brand_id: selectedBrandId, supplier_ids: ids }),
      })
      if (!res.ok) { const d = await res.json(); setErr(d.detail ?? 'Failed'); return }
      invalidateVendorsCache(companyId)
      onDone()
      onClose()
    } catch { setErr('Network error') } finally { setSaving(false) }
  }

  const currentBrandName = vendor
    ? (brands.find(b => b.id === vendor.brand_id)?.name ?? (vendor.brand_id ? 'Unknown brand' : 'Shared (all brands)'))
    : `${ids.length} vendors selected`

  const unchanged = !isBulk && selectedBrandId === initialBrandId

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.45)', zIndex:1100, display:'flex', alignItems:'center', justifyContent:'center' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={{ background:'#fff', borderRadius:14, width:420, boxShadow:'0 20px 60px rgba(0,0,0,0.2)', overflow:'hidden' }}>
        <div style={{ padding:'20px 24px 16px', borderBottom:'1px solid #f3f4f6', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div>
            <div style={{ fontSize:15, fontWeight:700, color:'#111827', display:'flex', alignItems:'center', gap:7 }}>
              <ArrowRightLeft size={16} color="#6366f1" /> Migrate Vendor Brand
            </div>
            <div style={{ fontSize:12, color:'var(--ink-500)', marginTop:3 }}>{currentBrandName}</div>
          </div>
          <button onClick={onClose} style={{ border:'none', background:'none', cursor:'pointer', color:'var(--ink-500)', padding:4, display:'flex' }}><X size={18} /></button>
        </div>

        <div style={{ padding:'20px 24px' }}>
          {!isBulk && (
            <>
              <div style={{ fontSize:12, color:'#6b7280', marginBottom:4 }}>Current brand</div>
              <div style={{ fontSize:13, fontWeight:600, color:'#374151', marginBottom:16,
                padding:'7px 12px', background:'#f3f4f6', borderRadius:7, display:'inline-block' }}>
                {brands.find(b => b.id === vendor!.brand_id)?.name ?? (vendor!.brand_id ? 'Unknown' : 'Shared (all brands)')}
              </div>
            </>
          )}

          <div style={{ fontSize:12, fontWeight:600, color:'#374151', marginBottom:8 }}>Move to</div>
          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            <label style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 14px',
              border:`1.5px solid ${selectedBrandId === null ? '#6366f1' : '#e5e7eb'}`,
              borderRadius:9, cursor:'pointer', background: selectedBrandId === null ? '#f5f3ff' : '#fff' }}>
              <input type="radio" name="brand" checked={selectedBrandId === null}
                onChange={() => setSelectedBrandId(null)} style={{ accentColor:'#6366f1' }} />
              <div>
                <div style={{ fontSize:13, fontWeight:600, color:'#111827' }}>Shared — all brands</div>
                <div style={{ fontSize:11, color:'var(--ink-500)' }}>Visible under every brand in this account</div>
              </div>
            </label>
            {brands.map(b => (
              <label key={b.id} style={{ display:'flex', alignItems:'center', gap:10, padding:'10px 14px',
                border:`1.5px solid ${selectedBrandId === b.id ? '#6366f1' : '#e5e7eb'}`,
                borderRadius:9, cursor:'pointer', background: selectedBrandId === b.id ? '#f5f3ff' : '#fff' }}>
                <input type="radio" name="brand" checked={selectedBrandId === b.id}
                  onChange={() => setSelectedBrandId(b.id)} style={{ accentColor:'#6366f1' }} />
                <div>
                  <div style={{ fontSize:13, fontWeight:600, color:'#111827' }}>{b.name}</div>
                  {b.is_default && <div style={{ fontSize:11, color:'var(--ink-500)' }}>Default brand</div>}
                </div>
              </label>
            ))}
          </div>

          {err && <div style={{ marginTop:12, color:'#dc2626', fontSize:12 }}>{err}</div>}

          <div style={{ display:'flex', gap:8, marginTop:20 }}>
            <button onClick={handleSave} disabled={saving || unchanged} style={{
              flex:1, padding:'9px 0', borderRadius:8, border:'none',
              background: saving || unchanged ? '#a5b4fc' : '#6366f1',
              color:'#fff', fontSize:13, fontWeight:600, cursor: saving || unchanged ? 'not-allowed' : 'pointer', fontFamily:'inherit',
            }}>
              {saving ? 'Saving…' : isBulk ? `Move ${ids.length} Vendors` : 'Move Vendor'}
            </button>
            <button onClick={onClose} style={{ padding:'9px 18px', borderRadius:8, border:'1px solid #e5e7eb', background:'#fff', color:'#6b7280', fontSize:13, cursor:'pointer', fontFamily:'inherit' }}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Add Vendor Modal ──────────────────────────────────────────────────────────
function AddVendorModal({ companyId, onClose, onDone }: {
  companyId?: string
  onClose: () => void
  onDone: () => void
}) {
  const [tab, setTab] = useState<'manual' | 'excel'>('manual')

  // manual form
  const [form, setForm] = useState({ name: '', gstn: '', email: '', payment_terms_days: '30', bank_account: '', bank_ifsc: '' })
  const [saving, setSaving] = useState(false)
  const [manualErr, setManualErr] = useState('')
  const [manualOk, setManualOk] = useState(false)
  const [verifyResult, setVerifyResult] = useState<{
    legal_name?: string; trade_name?: string
    gstin_verified?: boolean; pan?: string; pan_verified?: boolean; pan_warning?: string
    bank_verified?: boolean; bank_skipped?: boolean; name_at_bank?: string; bank_provided?: boolean; bank_warning?: string
  } | null>(null)

  // excel
  const fileRef = useRef<HTMLInputElement>(null)
  const [xlFile, setXlFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [xlResult, setXlResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(null)
  const [xlErr, setXlErr] = useState('')

  const GSTN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/

  const handleManualSubmit = async () => {
    if (!form.name.trim()) { setManualErr('Vendor name is required'); return }
    const gstn = form.gstn.trim().toUpperCase()
    if (!gstn) { setManualErr('GSTN is required'); return }
    if (!GSTN_RE.test(gstn)) { setManualErr('Invalid GSTN format (e.g. 29AABCT1332L1ZU)'); return }
    setSaving(true); setManualErr('')
    try {
      const res = await createVendor({
        name: form.name.trim(),
        gstn,
        email: form.email.trim() || undefined,
        payment_terms_days: parseInt(form.payment_terms_days) || 30,
        company_id: companyId,
        bank_account: form.bank_account.trim() || undefined,
        bank_ifsc: form.bank_ifsc.trim().toUpperCase() || undefined,
      })
      setVerifyResult({
        legal_name: res.legal_name,
        trade_name: res.trade_name,
        gstin_verified: res.gstin_verified,
        pan: res.pan,
        pan_verified: res.pan_verified,
        pan_warning: res.pan_warning,
        bank_verified: res.bank_verified,
        name_at_bank: res.name_at_bank,
        bank_provided: !!(form.bank_account.trim() && form.bank_ifsc.trim()),
        bank_skipped: res.bank_skipped,
        bank_warning: res.bank_warning,
      })
      invalidateVendorsCache(companyId)
      setManualOk(true)
    } catch (e: unknown) {
      setManualErr(e instanceof Error ? e.message : 'Failed to create vendor')
    } finally {
      setSaving(false)
    }
  }

  const downloadTemplate = () => {
    const rows = [
      ['Name', 'GSTN', 'Email', 'Payment Terms'],
      ['Acme Pvt Ltd', '29AABCT1332L1ZU', 'billing@acme.com', '30'],
      ['XYZ Technologies', '27AAACX1234A1Z5', 'accounts@xyz.in', '45'],
    ]
    const csv = rows.map(r => r.map(c => `"${c}"`).join(',')).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'vendor_upload_template.csv'
    a.click(); URL.revokeObjectURL(url)
  }

  const handleExcelUpload = async () => {
    if (!xlFile) return
    setUploading(true); setXlErr(''); setXlResult(null)
    try {
      const result = await bulkCreateVendorsExcel(xlFile, companyId)
      invalidateVendorsCache(companyId)
      setXlResult(result)
      if (result.created > 0) onDone()
    } catch (e: unknown) {
      setXlErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.4)', zIndex:1000, display:'flex', alignItems:'center', justifyContent:'center' }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div style={{ background:'#fff', borderRadius:14, width:480, boxShadow:'0 20px 60px rgba(0,0,0,0.18)', overflow:'hidden' }}>

        {/* Header */}
        <div style={{ padding:'20px 24px 0', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
          <div style={{ fontSize:16, fontWeight:700, color:'#111827' }}>Add Vendor</div>
          <button onClick={onClose} style={{ border:'none', background:'none', cursor:'pointer', color:'var(--ink-500)', padding:4, display:'flex' }}>
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display:'flex', gap:0, padding:'16px 24px 0', borderBottom:'1px solid #e5e7eb' }}>
          {(['manual','excel'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{
              border:'none', background:'none', cursor:'pointer', padding:'8px 16px',
              fontSize:13, fontWeight:600, fontFamily:'inherit',
              color: tab===t ? '#2563eb' : '#6b7280',
              borderBottom: tab===t ? '2px solid #2563eb' : '2px solid transparent',
              marginBottom:-1,
            }}>
              {t === 'manual' ? '✏️ Add Manually' : '📊 Upload Excel'}
            </button>
          ))}
        </div>

        <div style={{ padding:'24px' }}>
          {tab === 'manual' ? (
            <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
              {[
                { label:'Vendor Name *', key:'name', placeholder:'e.g. Acme Pvt Ltd', type:'text', hint: '' },
                { label:'GSTN *', key:'gstn', placeholder:'e.g. 29AABCT1332L1ZU', type:'text', hint: '15-character GST Identification Number' },
                { label:'Email', key:'email', placeholder:'billing@vendor.com', type:'email', hint: '' },
                { label:'Payment Terms (days)', key:'payment_terms_days', placeholder:'30', type:'number', hint: '' },
                { label:'Bank Account Number', key:'bank_account', placeholder:'e.g. 1234567890', type:'text', hint: '' },
                { label:'IFSC Code', key:'bank_ifsc', placeholder:'e.g. HDFC0001234', type:'text', hint: '' },
              ].map(f => {
                const val = form[f.key as keyof typeof form]
                const gstnInvalid = f.key === 'gstn' && val.trim().length > 0 && !GSTN_RE.test(val.trim().toUpperCase())
                return (
                  <div key={f.key}>
                    <div style={{ fontSize:12, fontWeight:600, color:'#374151', marginBottom:5 }}>{f.label}</div>
                    {f.hint && <div style={{ fontSize:11, color:'var(--ink-500)', marginBottom:4 }}>{f.hint}</div>}
                    <input
                      type={f.type}
                      placeholder={f.placeholder}
                      value={val}
                      onChange={e => setForm(p => ({ ...p, [f.key]: (f.key === 'gstn' || f.key === 'bank_ifsc') ? e.target.value.toUpperCase() : e.target.value }))}
                      style={{ width:'100%', padding:'8px 12px', border:`1px solid ${gstnInvalid ? '#fca5a5' : '#e5e7eb'}`, borderRadius:8, fontSize:13, fontFamily:'inherit', boxSizing:'border-box', outline:'none', background: gstnInvalid ? '#fff5f5' : '#fff' }}
                      onFocus={e => { if (!gstnInvalid) e.target.style.borderColor='#2563eb' }}
                      onBlur={e => { if (!gstnInvalid) e.target.style.borderColor='#e5e7eb' }}
                    />
                    {gstnInvalid && <div style={{ fontSize:11, color:'#dc2626', marginTop:3 }}>Invalid GSTN format</div>}
                  </div>
                )
              })}
              {manualErr && <div style={{ color:'#dc2626', fontSize:12 }}>{manualErr}</div>}
              {manualOk && verifyResult && (
                <div style={{ background:'#f0fdf4', border:'1px solid #86efac', borderRadius:10, padding:'14px 16px' }}>
                  <div style={{ display:'flex', alignItems:'center', gap:6, color:'#15803d', fontSize:13, fontWeight:700, marginBottom:10 }}>
                    <CheckCircle2 size={15} /> Vendor added & verified
                  </div>
                  {verifyResult.legal_name && (
                    <div style={{ fontSize:11, color:'#166534', marginBottom:8, fontWeight:600 }}>
                      {verifyResult.legal_name}{verifyResult.trade_name && verifyResult.trade_name !== verifyResult.legal_name ? ` · ${verifyResult.trade_name}` : ''}
                    </div>
                  )}
                  <div style={{ display:'flex', flexDirection:'column', gap:5 }}>
                    {[
                      {
                        label: 'GSTIN Active',
                        ok: verifyResult.gstin_verified,
                        note: verifyResult.gstin_verified ? 'Confirmed active on GST portal' : 'Could not verify',
                      },
                      {
                        label: `PAN ${verifyResult.pan ?? ''}`,
                        ok: verifyResult.pan_verified,
                        note: verifyResult.pan_verified ? 'Confirmed via GSTIN (embedded PAN)' : (verifyResult.pan_warning ?? 'Could not verify'),
                      },
                      {
                        label: 'Bank Account',
                        ok: !verifyResult.bank_provided || verifyResult.bank_skipped ? null : verifyResult.bank_verified,
                        note: !verifyResult.bank_provided
                          ? 'Not provided'
                          : verifyResult.bank_skipped
                            ? 'Skipped — verify again later'
                            : verifyResult.bank_verified
                              ? `Account exists · ${verifyResult.name_at_bank ?? ''}`
                              : (verifyResult.bank_warning ?? 'Verification failed'),
                      },
                    ].map(row => (
                      <div key={row.label} style={{ display:'flex', alignItems:'center', gap:8, fontSize:12 }}>
                        <span style={{
                          width:16, height:16, borderRadius:'50%', display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
                          background: row.ok === null ? '#e5e7eb' : row.ok ? '#dcfce7' : '#fef9c3',
                          color:       row.ok === null ? 'var(--ink-500)' : row.ok ? '#16a34a' : '#854d0e',
                          fontSize:10, fontWeight:700,
                        }}>
                          {row.ok === null ? '—' : row.ok ? '✓' : '!'}
                        </span>
                        <span style={{ fontWeight:600, color:'#374151', minWidth:90 }}>{row.label}</span>
                        <span style={{ color:'#6b7280' }}>{row.note}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {manualOk ? (
                <button onClick={() => { onDone(); onClose() }} style={{
                  marginTop:4, padding:'10px 0', borderRadius:8, border:'none', background:'#15803d',
                  color:'#fff', fontSize:13, fontWeight:600, cursor:'pointer', fontFamily:'inherit',
                }}>
                  Done
                </button>
              ) : (
                <button onClick={handleManualSubmit} disabled={saving} style={{
                  marginTop:4, padding:'10px 0', borderRadius:8, border:'none', background:'#2563eb',
                  color:'#fff', fontSize:13, fontWeight:600, cursor:'pointer', fontFamily:'inherit',
                  opacity: saving ? 0.7 : 1,
                }}>
                  {saving ? 'Verifying & adding…' : 'Add Vendor'}
                </button>
              )}
            </div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
              <div style={{ background:'#f8fafc', border:'1px solid #e5e7eb', borderRadius:8, padding:'14px 16px' }}>
                <div style={{ fontSize:12, fontWeight:600, color:'#374151', marginBottom:8 }}>Required columns (first row = headers)</div>
                <div style={{ display:'flex', flexDirection:'column', gap:5, marginBottom:12 }}>
                  {[
                    { col:'Name', req:true, note:'Vendor company name' },
                    { col:'GSTN', req:true, note:'15-char GST number e.g. 29AABCT1332L1ZU' },
                    { col:'Email', req:false, note:'Billing contact email' },
                    { col:'Payment Terms', req:false, note:'Number of days e.g. 30' },
                  ].map(r => (
                    <div key={r.col} style={{ display:'flex', alignItems:'center', gap:8, fontSize:12 }}>
                      <span style={{ fontWeight:700, color:'#111827', minWidth:100 }}>{r.col}</span>
                      {r.req
                        ? <span style={{ fontSize:10, fontWeight:600, color:'#dc2626', background:'#fee2e2', padding:'1px 6px', borderRadius:4 }}>REQUIRED</span>
                        : <span style={{ fontSize:10, color:'var(--ink-500)', background:'#f3f4f6', padding:'1px 6px', borderRadius:4 }}>optional</span>}
                      <span style={{ color:'#6b7280' }}>{r.note}</span>
                    </div>
                  ))}
                </div>
                <button onClick={downloadTemplate} style={{ display:'inline-flex', alignItems:'center', gap:6, padding:'7px 14px', border:'1px solid #2563eb', borderRadius:7, background:'#eff6ff', color:'#2563eb', fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'inherit' }}>
                  ⬇ Download Template
                </button>
              </div>

              <div
                onClick={() => fileRef.current?.click()}
                style={{
                  border: `2px dashed ${xlFile ? '#2563eb' : '#d1d5db'}`,
                  borderRadius:10, padding:'28px 16px', textAlign:'center', cursor:'pointer',
                  background: xlFile ? '#eff6ff' : '#fafafa',
                  transition:'all 0.15s',
                }}>
                <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display:'none' }}
                  onChange={e => { setXlFile(e.target.files?.[0] ?? null); setXlResult(null); setXlErr('') }} />
                <Upload size={24} color={xlFile ? '#2563eb' : '#5b6473'} style={{ margin:'0 auto 8px' }} />
                {xlFile
                  ? <div style={{ fontSize:13, fontWeight:600, color:'#2563eb' }}>{xlFile.name}</div>
                  : <div style={{ fontSize:13, color:'#6b7280' }}>Click to choose Excel file</div>}
              </div>

              {xlErr && <div style={{ color:'#dc2626', fontSize:12 }}>{xlErr}</div>}
              {xlResult && (
                <div style={{ background: xlResult.created > 0 ? '#f0fdf4' : '#fef3c7', border:`1px solid ${xlResult.created > 0 ? '#a7f3d0' : '#fde68a'}`, borderRadius:8, padding:'10px 14px', fontSize:13 }}>
                  <div style={{ fontWeight:600, color:'#111827', marginBottom:4 }}>
                    {xlResult.created} vendor{xlResult.created !== 1 ? 's' : ''} added · {xlResult.skipped} skipped
                  </div>
                  {xlResult.errors.length > 0 && (
                    <div style={{ color:'#dc2626', fontSize:12 }}>{xlResult.errors.slice(0,3).join(', ')}</div>
                  )}
                </div>
              )}

              <button onClick={handleExcelUpload} disabled={!xlFile || uploading} style={{
                padding:'10px 0', borderRadius:8, border:'none', background:'#2563eb',
                color:'#fff', fontSize:13, fontWeight:600, cursor: !xlFile || uploading ? 'not-allowed' : 'pointer',
                fontFamily:'inherit', opacity: !xlFile || uploading ? 0.6 : 1,
              }}>
                {uploading ? 'Uploading…' : 'Upload & Import'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Document Preview Modal ───────────────────────────────────────────────────
interface EditEntry { id: string; edited_by: string | null; edited_at: string | null; diff: { sheet: string; row: number; col: number; header: string; oldValue: string; newValue: string }[] }

const PDF_CTRL_BTN_STYLE: CSSProperties = {
  width: 28, height: 28, borderRadius: 5, border: '1px solid #e5e7eb', background: 'none',
  color: '#4b5563', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
  fontSize: 16, fontFamily: 'inherit',
}

interface ClaimFindingAction {
  id: string
  label: string
  kind: string
  endpoint: string | null
  method: string
  body: Record<string, unknown>
}

interface DocClaim {
  scope: string
  field: string
  target_ref?: string | null
  new_value: string
  unit?: string | null
  confidence: number
  evidence_quote: string
  status: string
  /** What's on file today, when approving this would REPLACE it. Absent for a
   *  claim that only adds (a new POC, a new bank account) — which is how the
   *  panel tells an either/or from a plain approval, since CONFLICTED marks
   *  both as awaiting a human. */
  current_value?: string | null
  /** Why this claim didn't apply, set by the approve endpoints. A FAILED
   *  claim is not necessarily an unreadable one — a perfectly-read GSTIN
   *  whose PAN doesn't match the vendor's is refused by the endpoint that
   *  owns that write, and this is where it says so. */
  error?: string | null
  // Present only when master_data_conflict.py turned this claim into an open
  // Review Hub finding — the same finding an admin would see (and approve)
  // in the hub itself. Absent means: already applied, failed validation, or
  // not yet swept.
  finding?: { finding_id: string; actions: ClaimFindingAction[] } | null
}

interface DocClaimsResponse {
  status: string
  error: string | null
  extraction: { extraction_id: string; model: string | null; created_at: string | null; claims: DocClaim[] } | null
}

// Groups claims into the same kind of labelled sections a bill's approval
// card uses ("VENDOR DETAILS", "BUYER DETAILS", …) so this panel reads as the
// same product rather than a bolted-on AI feature. "Other" catches any scope
// this map doesn't yet know about rather than dropping the claim silently.
const CLAIM_SECTION_LABEL: Record<string, string> = {
  vendor_bank: 'Vendor details', vendor_gstin: 'Vendor details', vendor_pan: 'Vendor details',
  payment_term: 'Payment terms', bill_line_item: 'Line items', vendor_contact: 'Point of contact',
}
const CLAIM_SECTION_ORDER = ['Vendor details', 'Payment terms', 'Line items', 'Point of contact', 'Other']
const CLAIM_FIELD_LABEL: Record<string, string> = {
  account_number: 'Account number', ifsc: 'IFSC', bank_name: 'Bank name',
  gstin: 'GSTIN', pan: 'PAN', rate: 'Rate', quantity: 'Quantity', term: 'Payment term',
  name: 'Name', email: 'Email', phone: 'Phone',
}

/** Approve every still-open, approvable claim on this document in one call —
 * the single Approve button for the whole panel, rather than one per row.
 * Runs the same validation and writes as approving one claim at a time
 * would; see approve_document_claim / approve_all_document_claims in
 * vendor_documents.py. */
/** Apply what the document states.
 *
 *  `override` is sent only when this REPLACES a value already on file — it says
 *  the user has read both and chosen this one, and without it the approve
 *  endpoint deliberately refuses to overwrite. An addition (a new POC, a new
 *  bank account) overrides nothing and must not claim to. */
async function keepContractValue(supplierId: string, docId: string, index: number, override: boolean) {
  const res = await fetch(
    `${API_BASE}/api/vendors/${supplierId}/documents/${docId}/claims/${index}/approve`
      + (override ? '?override=true' : ''),
    { method: 'POST' },
  )
  const json = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(json.detail || `Couldn't apply (${res.status})`)
  if (json.status === 'FAILED') throw new Error(json.message || "Couldn't apply this value")
  return json
}

/** Keep what's on the vendor record and retire the claim, so the conflict
 *  stops being raised. Leaves the vendor record untouched. */
async function keepCurrentValue(supplierId: string, docId: string, index: number) {
  const res = await fetch(
    `${API_BASE}/api/vendors/${supplierId}/documents/${docId}/claims/${index}/discard`,
    { method: 'POST' },
  )
  const json = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(json.detail || `Couldn't save that choice (${res.status})`)
  return json
}

async function approveAllClaims(supplierId: string, docId: string): Promise<{ applied: number; failed: number; deferred: number }> {
  const res = await fetch(`${API_BASE}/api/vendors/${supplierId}/documents/${docId}/claims/approve-all`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`Approve failed (${res.status})`)
  return res.json().catch(() => ({ applied: 0, failed: 0, deferred: 0 }))
}

/** Persist a human's correction to what the OCR/LLM extracted, before it's
 * approved — the fix for a misread value rather than only accept-or-live-
 * with-it. Resets the claim to PENDING server-side so the next approve
 * re-validates the corrected value from scratch. */
async function editClaimValue(supplierId: string, docId: string, index: number, newValue: string) {
  const res = await fetch(`${API_BASE}/api/vendors/${supplierId}/documents/${docId}/claims/${index}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_value: newValue }),
  })
  if (!res.ok) throw new Error(`Save failed (${res.status})`)
  return res.json().catch(() => ({}))
}

// Only the "anchor" claim of a multi-claim scope is its own unit of approval
// — a bank account's ifsc/bank_name or a contact's name/phone describe the
// anchor (account_number / email) rather than standing alone, exactly
// mirroring vendor_documents.py's own anchor-field rule. A single-valid
// scope (PAN/GSTIN/payment term) has no siblings, so any claim of it counts.
function isApprovableClaim(c: DocClaim): boolean {
  if (c.scope === 'vendor_bank') return !c.field || c.field === 'account_number'
  if (c.scope === 'vendor_contact') return !c.field || c.field === 'email'
  return c.scope === 'vendor_pan' || c.scope === 'vendor_gstin' || c.scope === 'payment_term'
}

/** One row of a claim the Communication Agent extracted from this contract —
 * the same {scope, field, new_value, confidence, evidence_quote} shape
 * comm_extract.py's CLAIM_SCHEMA defines and master_data_conflict.py /
 * contract_bill_variance.py act on. Nothing here has been written to the
 * vendor record yet unless `status` already reads APPLIED — approving (via
 * the panel's single Approve button) is the only thing that writes it. */
function ClaimRow({ c, index, editing, draftValue, onDraftChange, supplierId, docId, onResolved }: {
  c: DocClaim; index: number; editing: boolean
  draftValue: string | undefined; onDraftChange: (index: number, value: string) => void
  supplierId?: string; docId?: string; onResolved: () => void
}) {
  const navigate = useNavigate()
  const [deciding, setDeciding] = useState<'contract' | 'current' | null>(null)
  const [decideErr, setDecideErr] = useState<string | null>(null)

  const decide = async (which: 'contract' | 'current') => {
    if (!supplierId || !docId) return
    setDeciding(which); setDecideErr(null)
    try {
      if (which === 'contract') await keepContractValue(supplierId, docId, index, isReplacement)
      else await keepCurrentValue(supplierId, docId, index)
      onResolved()
    } catch (e) {
      setDecideErr(e instanceof Error ? e.message : "Couldn't save that choice")
    } finally {
      setDeciding(null)
    }
  }
  const fieldLabel = CLAIM_FIELD_LABEL[c.field] ?? c.field.replace(/_/g, ' ')
  const label = c.target_ref ? `${fieldLabel} — ${c.target_ref}` : fieldLabel
  const editable = editing && c.status !== 'APPLIED'
  // Straight to this finding's own drawer once the sweep has raised it;
  // otherwise to the hub, which is where it will appear moments later.
  const hubHref = c.finding?.finding_id ? findingPath(c.finding.finding_id) : PATHS.review
  // Only an anchor claim carries an action: a contact's name or an account's
  // IFSC is applied by approving the email/account it belongs to.
  const needsDecision = (c.status === 'PENDING' || c.status === 'CONFLICTED') && isApprovableClaim(c)
  // …and only a claim with something on file to overwrite is an either/or.
  const isReplacement = needsDecision && !!c.current_value

  return (
    <tr>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top', color: 'var(--ink-500)', fontSize: 'var(--fs-2xs)', textTransform: 'uppercase', letterSpacing: '0.03em', wordBreak: 'break-word' }}>
        {label}
      </td>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top', wordBreak: 'break-word' }}>
        {editable ? (
          <input
            className="tnum"
            value={draftValue ?? c.new_value}
            onChange={e => onDraftChange(index, e.target.value)}
            style={{
              width: '100%', fontSize: 'var(--fs-sm)', fontWeight: 'var(--fw-semibold)', color: 'var(--ink-900)',
              border: '1px solid var(--line-300)', borderRadius: 'var(--r-sm)', padding: '3px 6px',
              fontFamily: 'inherit', background: 'var(--surface-0)', boxSizing: 'border-box',
            }}
          />
        ) : (
          <div className="tnum" style={{ fontSize: 'var(--fs-sm)', fontWeight: 'var(--fw-semibold)', color: 'var(--ink-900)' }}>
            {c.new_value}{c.unit ? ` ${c.unit}` : ''}
          </div>
        )}
        {c.evidence_quote && (
          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)', fontStyle: 'italic', marginTop: 2 }}>
            "{c.evidence_quote}"
          </div>
        )}
        {/* The reason lives beside the value, not squeezed into the status
            column — "conflicts with the PAN already on file (ACJFA3124E)" is
            the whole point of the row and doesn't fit in a badge. */}
        {c.error && (
          <div style={{ fontSize: 'var(--fs-2xs)', color: c.status === 'CONFLICTED' ? 'var(--warn-700)' : 'var(--danger-700)', marginTop: 3 }}>
            {c.error}
          </div>
        )}
        {/* Two shapes, not one. Replacing a value on file is a real
            either/or and names both sides; adding a POC or a bank account
            replaces nothing, so it gets a single Approve — "keep current"
            against a value that does not exist is nonsense. Sibling rows
            (a contact's name, an account's IFSC) carry no action at all:
            they ride along with their anchor. */}
        {needsDecision && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', flexWrap: 'wrap', marginTop: 5 }}>
            <button
              onClick={() => decide('contract')}
              disabled={deciding !== null}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: '#fff',
                background: 'var(--brand-600)', border: 'none', borderRadius: 'var(--r-full)',
                padding: '4px 12px', cursor: deciding ? 'default' : 'pointer',
                opacity: deciding && deciding !== 'contract' ? 0.5 : 1, fontFamily: 'inherit',
              }}
            >
              {deciding === 'contract'
                ? <RefreshCw size={12} style={{ animation: 'vp-spin 0.8s linear infinite' }} />
                : <CheckCircle2 size={13} />}
              {isReplacement ? "Keep contract's" : 'Approve'}
            </button>
            {isReplacement && (
              <button
                onClick={() => decide('current')}
                disabled={deciding !== null}
                style={{
                  fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--ink-700)',
                  background: 'var(--surface-0)', border: '1px solid var(--line-300)',
                  borderRadius: 'var(--r-full)', padding: '4px 12px',
                  cursor: deciding ? 'default' : 'pointer',
                  opacity: deciding && deciding !== 'current' ? 0.5 : 1, fontFamily: 'inherit',
                }}
              >
                {deciding === 'current' ? 'Saving…' : `Keep current${c.current_value ? ` (${c.current_value})` : ''}`}
              </button>
            )}
            <button
              onClick={() => navigate(hubHref)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 2, padding: 0,
                background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)', textDecoration: 'underline',
              }}
            >
              Review in the hub <ChevronRight size={11} />
            </button>
          </div>
        )}
        {decideErr && (
          <div style={{ fontSize: 'var(--fs-2xs)', color: 'var(--danger-700)', marginTop: 3 }}>{decideErr}</div>
        )}
      </td>
      <td style={{ padding: 'var(--sp-2) var(--sp-3)', verticalAlign: 'top', textAlign: 'right' }}>
        {c.status === 'APPLIED' ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--success-700)' }}>
            <CheckCircle2 size={13} /> On file
          </span>
        ) : isReplacement ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--warn-700)' }} title="Differs from what's on file — approving replaces it">
            <AlertCircle size={13} /> Conflict
          </span>
        ) : needsDecision ? (
          // Nothing is being replaced, so it is not a conflict — just something
          // new waiting on a yes.
          <span style={{ fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--brand-600)' }}>New</span>
        ) : c.status === 'FAILED' ? (
          <span style={{ fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--danger-700)' }} title={c.error || undefined}>
            Not applied
          </span>
        ) : isApprovableClaim(c) ? (
          <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)' }}>Pending</span>
        ) : (
          <span style={{ fontSize: 'var(--fs-2xs)', color: 'var(--ink-500)' }}>Extracted</span>
        )}
      </td>
    </tr>
  )
}

function ClaimSection({ title, claims, editing, drafts, onDraftChange, supplierId, docId, onResolved }: {
  title: string; claims: { claim: DocClaim; index: number }[]
  editing: boolean; drafts: Record<number, string>; onDraftChange: (index: number, value: string) => void
  supplierId?: string; docId?: string; onResolved: () => void
}) {
  if (claims.length === 0) return null
  return (
    <tbody>
      <tr>
        <th colSpan={3} style={{ textAlign: 'left', padding: 'var(--sp-3) var(--sp-3) var(--sp-1)', fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-bold)', color: 'var(--ink-500)', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid var(--line-200)' }}>
          {title}
        </th>
      </tr>
      {claims.map(({ claim, index }) => (
        <ClaimRow
          key={index} c={claim} index={index} editing={editing}
          draftValue={drafts[index]} onDraftChange={onDraftChange}
          supplierId={supplierId} docId={docId} onResolved={onResolved}
        />
      ))}
    </tbody>
  )
}

function DocPreviewModal({
  url, name, onClose,
  docId, supplierId, companyId, currentUser,
}: {
  url: string; name: string; onClose: () => void
  docId?: string; supplierId?: string; companyId?: string; currentUser?: string
}) {
  const isImage = /\.(png|jpe?g|gif|svg|webp)$/i.test(name)
  const isPdf   = /\.pdf$/i.test(name)
  const isExcel = /\.(xlsx?|csv)$/i.test(name)
  // Images need the bytes fetched with the auth header — a bare <img src> cannot
  // send one, and the token must never travel in the URL.
  const image = useAuthedObjectUrl(isImage ? url : null)
  const [blobUrl, setBlobUrl]   = useState<string | null>(null)
  const [blobData, setBlobData] = useState<ArrayBuffer | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [history, setHistory] = useState<EditEntry[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // The Communication Agent's extracted terms for this document, shown beside
  // the PDF the same way a bill's extracted fields sit beside its PDF in
  // ReviewPage — except only ever present for a PDF that actually went
  // through extract_claims_from_contract (comm_extract.py), and each
  // still-pending claim carries an Approve button rather than being read-only:
  // nothing it names has touched the vendor record yet.
  const [claimsResp, setClaimsResp] = useState<DocClaimsResponse | null>(null)
  const loadClaims = () => {
    if (!isPdf || !docId || !supplierId) return
    fetch(`${API_BASE}/api/vendors/${supplierId}/documents/${docId}/claims?company_id=${companyId ?? ''}`)
      .then(r => r.ok ? r.json() : null)
      .then(json => setClaimsResp(json))
      .catch(() => setClaimsResp(null))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadClaims() }, [isPdf, docId, supplierId, companyId])

  // Only worth a panel once there is something to say: a document nobody ever
  // queued for extraction (claims_status absent from the response entirely —
  // can't happen for a real row, but guards a malformed one) shows nothing,
  // matching today's plain preview exactly.
  const showClaims = !!claimsResp?.status

  const claimSections = useMemo(() => {
    const claims = claimsResp?.extraction?.claims ?? []
    const bucket: Record<string, { claim: DocClaim; index: number }[]> = {
      'Vendor details': [], 'Payment terms': [], 'Line items': [], 'Point of contact': [], 'Other': [],
    }
    claims.forEach((c, index) => { bucket[CLAIM_SECTION_LABEL[c.scope] ?? 'Other'].push({ claim: c, index }) })
    return CLAIM_SECTION_ORDER.map(title => ({ title, claims: bucket[title] })).filter(s => s.claims.length > 0)
  }, [claimsResp])

  // One Edit / one Approve for the whole panel, not a button per row: Edit
  // toggles every not-yet-applied value into an editable input (for
  // correcting a misread extraction before it's approved); Approve applies
  // every still-open claim in a single call.
  const [claimsEditing, setClaimsEditing] = useState(false)
  const [claimDrafts, setClaimDrafts] = useState<Record<number, string>>({})
  const [savingEdits, setSavingEdits] = useState(false)
  const [approvingAll, setApprovingAll] = useState(false)
  const [claimsActionErr, setClaimsActionErr] = useState<string | null>(null)
  const [claimsNote, setClaimsNote] = useState<string | null>(null)

  // Only what one click will really write: approve-all leaves a claim that
  // would replace a value on file for its own row's either/or, so counting it
  // made "Approve (5)" apply 3 and report the rest as a failure.
  const pendingApprovalCount = (claimsResp?.extraction?.claims ?? [])
    .filter(c => (c.status === 'PENDING' || c.status === 'CONFLICTED') && isApprovableClaim(c) && !c.current_value).length

  const onEditToggle = async () => {
    if (!claimsEditing) { setClaimsEditing(true); return }
    if (!supplierId || !docId) return
    setSavingEdits(true); setClaimsActionErr(null)
    try {
      await Promise.all(
        Object.entries(claimDrafts).map(([idx, value]) => editClaimValue(supplierId, docId, Number(idx), value))
      )
      setClaimDrafts({})
      loadClaims()
    } catch {
      setClaimsActionErr("Couldn't save one or more edits — try again.")
    } finally {
      setSavingEdits(false)
      setClaimsEditing(false)
    }
  }

  const onApproveAll = async () => {
    if (!supplierId || !docId) return
    setApprovingAll(true); setClaimsActionErr(null); setClaimsNote(null)
    try {
      const res = await approveAllClaims(supplierId, docId)
      // Approving writes the vendor's own PAN/GSTIN/terms/contacts, and the
      // vendor card reads through a localStorage cache — without this the
      // panel says "On file" while the card beside it still shows the old
      // value, which reads as the write having silently not happened.
      if (res?.applied) invalidateVendorsCache(companyId)
      if (res?.deferred) {
        // Expected, not a failure: those rows carry their own keep-which choice.
        setClaimsNote(
          `${res.deferred} ${res.deferred === 1 ? 'value differs' : 'values differ'} from what's already ` +
          'on file and was left for you to choose on its row.'
        )
      }
      if (res?.failed) setClaimsActionErr(`${res.failed} ${res.failed === 1 ? 'value' : 'values'} couldn't be applied — see the row for why.`)
      loadClaims()
    } catch {
      setClaimsActionErr("Couldn't approve — try again or use the Review Hub.")
    } finally {
      setApprovingAll(false)
    }
  }

  // Escape, the backdrop and ✕ all come through here: corrected claim values
  // that haven't been saved are asked about rather than dropped.
  const claimsDirty = claimsEditing && Object.keys(claimDrafts).length > 0
  const [askDiscard, setAskDiscard] = useState(false)
  const requestClose = () => { if (claimsDirty) setAskDiscard(true); else onClose() }
  const layerRef = useRef<HTMLDivElement>(null)
  useLayer(layerRef, requestClose)
  useScrollLock(true)

  // Page-fit PDF viewer state — same height-first-fit trick ReviewPage.tsx
  // uses for a bill's PDF: the page is sized to the pane's height, so one
  // page always fits with no vertical scroll, and pageNum/numPages move
  // between pages instead.
  const pdfPaneRef = useRef<HTMLDivElement>(null)
  const [pdfPaneH, setPdfPaneH] = useState(0)
  const [pageNum, setPageNum] = useState(1)
  const [numPages, setNumPages] = useState(0)
  const [zoomDelta, setZoomDelta] = useState(0)
  const [pageNaturalW, setPageNaturalW] = useState(0)
  const [pageNaturalH, setPageNaturalH] = useState(0)

  useEffect(() => {
    const el = pdfPaneRef.current
    if (!el) return
    const update = () => setPdfPaneH(el.clientHeight)
    const ro = new ResizeObserver(update)
    ro.observe(el)
    update()
    return () => ro.disconnect()
  }, [])

  const pdfFitWidth = (() => {
    if (!pdfPaneH || !pageNaturalW || !pageNaturalH) return 520
    const scaleByH = pdfPaneH / pageNaturalH
    return Math.max(280, scaleByH * pageNaturalW + zoomDelta)
  })()

  // A failed fetch used to leave "Fetching…" up for good; it now says so, with
  // Retry and Download. A 4xx/5xx body is an error page, not the document.
  const [fileErr, setFileErr] = useState(false)
  const [fileTry, setFileTry] = useState(0)
  useEffect(() => {
    if (!isPdf && !isExcel) return
    let prevUrl: string | null = null
    let live = true
    setFileErr(false)
    const fail = () => { if (live) setFileErr(true) }
    const ok = (r: Response) => { if (!r.ok) throw new Error(String(r.status)); return r }
    if (isExcel) {
      fetch(url)
        .then(ok).then(r => r.arrayBuffer())
        .then(buf => { if (live) setBlobData(buf) })
        .catch(fail)
    } else {
      fetch(`${url}${url.includes('?') ? '&' : '?'}preview=true`)
        .then(ok).then(r => r.blob())
        .then(blob => { if (!live) return; const u = URL.createObjectURL(blob); prevUrl = u; setBlobUrl(u) })
        .catch(fail)
    }
    return () => { live = false; if (prevUrl) URL.revokeObjectURL(prevUrl) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, fileTry])
  const fileError = (
    <div role="alert" style={{ color: 'var(--ink-600)', fontSize: 13, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, padding: 24, textAlign: 'center' }}>
      <AlertCircle size={28} color="var(--danger-600)" />
      <span>Couldn't load this file.</span>
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" className="vc__btn vc__btn--sm" onClick={() => setFileTry(n => n + 1)}><RefreshCw size={13} />Retry</button>
        <button type="button" className="vc__btn vc__btn--sm" onClick={() => downloadAuthed(url.replace(/[?&]preview=true/, ''), name)}>Download</button>
      </div>
    </div>
  )

  const loadHistory = async () => {
    if (!docId || !supplierId) return
    setHistoryLoading(true)
    try {
      const res = await fetch(`${API_BASE}/api/vendors/${supplierId}/documents/${docId}/edits?company_id=${companyId ?? ''}`)
      const json = await res.json()
      setHistory(json.edits ?? [])
    } catch { /* ignore */ }
    setHistoryLoading(false)
  }

  const toggleHistory = () => {
    setShowHistory(p => {
      if (!p) loadHistory()
      return !p
    })
  }

  const handleSave = async (file: File, diff: import('../components/ExcelPreview').CellDiff[]) => {
    if (!docId || !supplierId) return
    const form = new FormData()
    form.append('file', file)
    form.append('edited_by', currentUser ?? '')
    form.append('diff', JSON.stringify(diff))
    const res = await fetch(`${API_BASE}/api/vendors/${supplierId}/documents/${docId}?company_id=${companyId ?? ''}`, {
      method: 'PUT', body: form,
    })
    if (!res.ok) throw new Error('Save failed')
    // Reload history if open
    if (showHistory) loadHistory()
  }

  return (
    <div className="doc-preview-overlay" onClick={e => { if (e.target === e.currentTarget) requestClose() }}>
      <ConfirmDialog
        open={askDiscard}
        title="Discard your corrections?"
        description="You've corrected values in Extracted terms that aren't saved yet. Closing now drops them."
        confirmLabel="Discard"
        cancelLabel="Keep editing"
        onCancel={() => setAskDiscard(false)}
        onConfirm={() => { setAskDiscard(false); onClose() }}
      />
      {/* Near full-page once there's a claims panel to show beside the
          document — the same "document here, what we read off it there"
          shape the bill review screen uses, just at bill-review scale rather
          than the plain single-pane preview's modest dialog. */}
      {/* Sized to its content, not to the viewport: the document pane is only
          as wide as the fitted page actually needs, so a fixed 96vw left a
          dead grey gutter on either side of the page. Height is what's fixed
          here — the page is fitted to it, and the modal's width follows from
          that. */}
      <div ref={layerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={`Preview ${name}`}
        className={`doc-preview-content${showClaims ? ' dpm--claims' : ''}`} style={{ display: 'flex', flexDirection: 'column', outline: 'none', ...(showClaims ? { width: 'auto', maxWidth: '96vw', height: '92vh' } : {}) }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid #f3f4f6', background: '#f9fafb', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <FileText size={18} color="#4f46e5" />
            <span style={{ fontSize: 14, fontWeight: 600, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {name}
            </span>
            {showClaims && (
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--sp-1)', fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--brand-600)', background: 'var(--brand-50)', padding: '2px var(--sp-2)', borderRadius: 'var(--r-full)', flexShrink: 0 }}>
                <Sparkles size={11} /> Contract read by the Communication Agent
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {isExcel && docId && (
              <button
                onClick={toggleHistory}
                style={{
                  fontSize: 12, padding: '4px 10px', border: '1px solid #e5e7eb', borderRadius: 6,
                  background: showHistory ? '#eff6ff' : '#fff', color: showHistory ? '#1d4ed8' : '#6b7280',
                  cursor: 'pointer', fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 5,
                }}
              >
                <ArrowRightLeft size={12} /> Edit History
              </button>
            )}
            <button
              onClick={requestClose}
              aria-label="Close preview"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-500)', padding: 4, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'background 0.15s, color 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.background = '#e5e7eb'; e.currentTarget.style.color = '#374151' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = 'var(--ink-500)' }}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="dpm__body" style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
          {/* Extracted terms panel — on the LEFT, same side the bill review
              screen puts its extracted fields, with the document itself on
              the right. Always visible once there's something to show, not
              toggle-gated like Edit History: the whole point is "document
              here, what was read off it right there." */}
          {showClaims && (
            <div className="dpm__claims" style={{ flex: '0 1 520px', minWidth: 0, borderRight: '1px solid var(--line-200)', background: 'var(--canvas)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ padding: 'var(--sp-3) var(--sp-4)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 'var(--sp-2)', borderBottom: '1px solid var(--line-200)', background: 'var(--surface-1)' }}>
                <span style={{ fontWeight: 'var(--fw-semibold)', fontSize: 'var(--fs-sm)', color: 'var(--ink-700)' }}>
                  Extracted terms
                </span>
                {claimSections.length > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                    <button
                      onClick={onEditToggle}
                      disabled={savingEdits || approvingAll}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: 'var(--ink-700)',
                        background: 'var(--surface-0)', border: '1px solid var(--line-300)',
                        borderRadius: 'var(--r-full)', padding: '4px 12px',
                        cursor: (savingEdits || approvingAll) ? 'default' : 'pointer', fontFamily: 'inherit',
                        opacity: (savingEdits || approvingAll) ? 0.7 : 1,
                      }}
                    >
                      <Pencil size={12} /> {savingEdits ? 'Saving…' : claimsEditing ? 'Save' : 'Edit'}
                    </button>
                    {!claimsEditing && pendingApprovalCount > 0 && (
                      <button
                        onClick={onApproveAll}
                        disabled={approvingAll}
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 4,
                          fontSize: 'var(--fs-2xs)', fontWeight: 'var(--fw-semibold)', color: '#fff',
                          background: 'var(--brand-600)', border: 'none', borderRadius: 'var(--r-full)',
                          padding: '4px 12px', cursor: approvingAll ? 'default' : 'pointer',
                          opacity: approvingAll ? 0.7 : 1, fontFamily: 'inherit',
                        }}
                      >
                        {approvingAll
                          ? <RefreshCw size={12} style={{ animation: 'vp-spin 0.8s linear infinite' }} />
                          : <CheckCircle2 size={13} />}
                        {approvingAll ? 'Applying…' : `Approve (${pendingApprovalCount})`}
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div style={{ flex: 1, overflow: 'auto' }}>
                {claimsActionErr && (
                  <div role="alert" style={{ color: 'var(--danger-700)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-2) var(--sp-3)' }}>
                    {claimsActionErr}
                  </div>
                )}
                {claimsNote && (
                  <div role="status" style={{ color: 'var(--ink-600)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-2) var(--sp-3)', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                    <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />{claimsNote}
                  </div>
                )}
                {claimsResp!.status === 'PENDING' || claimsResp!.status === 'RUNNING' ? (
                  <div style={{ color: 'var(--ink-500)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-3)', display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                    <RefreshCw size={12} style={{ animation: 'vp-spin 0.8s linear infinite' }} /> Reading this document…
                  </div>
                ) : claimsResp!.status === 'SKIPPED' ? (
                  <div style={{ color: 'var(--ink-500)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-3)' }}>
                    Couldn't find a usable text layer — this looks like a scanned document, so nothing was
                    extracted automatically.
                  </div>
                ) : claimsResp!.status === 'FAILED' ? (
                  <div style={{ color: 'var(--danger-700)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-3)' }}>
                    {claimsResp!.error || 'Extraction failed.'}
                  </div>
                ) : claimSections.length === 0 ? (
                  <div style={{ color: 'var(--ink-500)', fontSize: 'var(--fs-xs)', padding: 'var(--sp-3)' }}>
                    No bank, GSTIN, PAN, payment-term, contact or line-item terms found in this document.
                  </div>
                ) : (
                  <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
                    {/* Declared widths: on auto layout the status column got
                        squeezed to nothing and clipped mid-word against the
                        panel edge. */}
                    <colgroup>
                      <col style={{ width: 112 }} />
                      <col />
                      <col style={{ width: 86 }} />
                    </colgroup>
                    {claimSections.map(s => (
                      <ClaimSection
                        key={s.title} title={s.title} claims={s.claims}
                        editing={claimsEditing} drafts={claimDrafts}
                        onDraftChange={(index, value) => setClaimDrafts(d => ({ ...d, [index]: value }))}
                        supplierId={supplierId} docId={docId}
                        onResolved={() => { invalidateVendorsCache(companyId); loadClaims() }}
                      />
                    ))}
                  </table>
                )}
              </div>
            </div>
          )}

          {/* Main content — the document itself, on the right once there's a
              claims panel to sit beside. Beside the claims panel its width is
              the fitted page's own width (capped so zooming can't crowd the
              text out), which is what keeps the page flush against both edges
              of its pane instead of floating in grey. On its own — a bill
              attachment with no claims — it just fills the dialog. */}
          <div className="dpm__doc" style={isPdf
            ? showClaims
              ? { flex: '0 1 auto', width: pdfFitWidth, maxWidth: '58vw', minWidth: 0, position: 'relative', background: '#e5e7eb', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
              : { flex: 1, minWidth: 0, position: 'relative', background: '#e5e7eb', display: 'flex', flexDirection: 'column', overflow: 'hidden' }
            : { flex: 1, position: 'relative', background: isExcel ? '#fff' : '#f3f4f6', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', flexDirection: 'column' }
          }>
            {isImage ? (
              image.url
                ? <img src={image.url} alt={name} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block' }} />
                : <div style={{ color: 'var(--ink-500)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {image.error
                      ? 'Could not load this image.'
                      : <><RefreshCw size={14} style={{ animation: 'vp-spin 0.8s linear infinite' }} /> Loading…</>}
                  </div>
            ) : isPdf ? (
              <>
                {/* Page-fit toolbar — one page always fills the pane's height
                    (fitWidth below is derived from it), so ‹ › moves between
                    pages instead of scrolling through one long strip. */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', background: '#fff', borderBottom: '1px solid #e5e7eb', flexShrink: 0, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 3, marginLeft: 'auto' }}>
                    <button onClick={() => setZoomDelta(d => d + 60)} style={PDF_CTRL_BTN_STYLE} aria-label="Zoom in" title="Zoom in"><ZoomIn size={15} /></button>
                    <button onClick={() => setZoomDelta(d => d - 60)} style={PDF_CTRL_BTN_STYLE} aria-label="Zoom out" title="Zoom out"><ZoomOut size={15} /></button>
                    <div style={{ width: 1, height: 18, background: '#e5e7eb', margin: '0 3px' }} />
                    <button onClick={() => { setZoomDelta(0); setPageNum(1) }} style={PDF_CTRL_BTN_STYLE} aria-label="Reset zoom and go to page 1" title="Reset"><RotateCcw size={15} /></button>
                  </div>
                  {numPages > 1 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      <button
                        disabled={pageNum <= 1} onClick={() => setPageNum(p => p - 1)} aria-label="Previous page"
                        style={{ ...PDF_CTRL_BTN_STYLE, opacity: pageNum <= 1 ? 0.4 : 1, cursor: pageNum <= 1 ? 'not-allowed' : 'pointer' }}
                      >‹</button>
                      <span className="tnum" style={{ fontSize: 12, color: '#4b5563', fontWeight: 500, whiteSpace: 'nowrap' }}>{pageNum} / {numPages}</span>
                      <button
                        disabled={pageNum >= numPages} onClick={() => setPageNum(p => p + 1)} aria-label="Next page"
                        style={{ ...PDF_CTRL_BTN_STYLE, opacity: pageNum >= numPages ? 0.4 : 1, cursor: pageNum >= numPages ? 'not-allowed' : 'pointer' }}
                      >›</button>
                    </div>
                  )}
                </div>
                {/* flex-start once zoomed: a centred flex item inside a scroll
                    container puts its overflowing left edge out of reach. */}
                <div ref={pdfPaneRef} style={{ flex: 1, overflowX: 'auto', overflowY: 'hidden', display: 'flex', alignItems: 'flex-start', justifyContent: zoomDelta > 0 ? 'flex-start' : 'center' }}>
                  {blobUrl ? (
                    <Document
                      file={blobUrl}
                      onLoadSuccess={({ numPages: n }) => { setNumPages(n); setPageNum(1) }}
                      loading={<PdfLoading stage="rendering" />}
                      error={<div style={{ color: 'var(--ink-500)', fontSize: 13, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: 24 }}><FileText size={36} color="#9ca3af" /><span>Could not load PDF</span></div>}
                    >
                      <Page
                        pageNumber={pageNum}
                        width={pdfFitWidth}
                        onLoadSuccess={p => { setPageNaturalW(p.originalWidth); setPageNaturalH(p.originalHeight) }}
                        renderAnnotationLayer={false}
                        renderTextLayer={false}
                      />
                    </Document>
                  ) : fileErr ? fileError : (
                    <PdfLoading stage="fetching" />
                  )}
                </div>
              </>
            ) : isExcel ? (
              blobData
                ? <Suspense fallback={<div style={{ color: 'var(--ink-500)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}><RefreshCw size={14} style={{ animation: 'vp-spin 0.8s linear infinite' }} /> Loading…</div>}>
                    <ExcelPreview blobData={blobData} editable={!!docId} onSave={docId ? handleSave : undefined} />
                  </Suspense>
                : fileErr ? fileError
                : <div style={{ color: 'var(--ink-500)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}><RefreshCw size={14} style={{ animation: 'vp-spin 0.8s linear infinite' }} /> Loading…</div>
            ) : (
              <div style={{ textAlign: 'center', padding: 24 }}>
                <FileText size={48} strokeWidth={1} style={{ margin: '0 auto 12px', display: 'block', color: 'var(--ink-500)' }} />
                <div style={{ fontSize: 14, color: '#374151', marginBottom: 12 }}>Preview not available for this file type.</div>
                <button
                  onClick={() => downloadAuthed(url.replace('&preview=true', ''), name)}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 16px', background: '#2563eb', color: '#fff', fontSize: 13, fontWeight: 600, borderRadius: 6, border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}>
                  Download File
                </button>
              </div>
            )}
          </div>

          {/* Edit history panel */}
          {showHistory && (
            <div style={{ width: 320, borderLeft: '1px solid #e5e7eb', background: '#fafafa', display: 'flex', flexDirection: 'column', overflow: 'hidden', flexShrink: 0 }}>
              <div style={{ padding: '12px 16px', fontWeight: 600, fontSize: 13, color: '#374151', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>Edit History</div>
              <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
                {historyLoading ? (
                  <div style={{ color: 'var(--ink-500)', fontSize: 12, padding: 8, display: 'flex', alignItems: 'center', gap: 6 }}><RefreshCw size={12} style={{ animation: 'vp-spin 0.8s linear infinite' }} /> Loading…</div>
                ) : history.length === 0 ? (
                  <div style={{ color: 'var(--ink-500)', fontSize: 12, padding: 8 }}>No edits recorded yet.</div>
                ) : history.map(entry => (
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
}

// ── Attach Bill Picker ───────────────────────────────────────────────────────
// Lets the user attach this vendor's own bills to an email straight from the
// server — with an inline preview — without downloading them first.
function AttachBillPicker({ bills, loading, attaching, onPreview, onAttach, onClose }: {
  bills: Bill[]
  loading: boolean
  attaching: boolean
  onPreview: (b: Bill) => void
  onAttach: (bills: Bill[]) => void
  onClose: () => void
}) {
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const attachable = bills.filter(b => !!b.upload_id)
  const selectedBills = attachable.filter(b => selected[b.invoice_id])
  const toggle = (id: string) => setSelected(s => ({ ...s, [id]: !s[id] }))
  const layerRef = useRef<HTMLDivElement>(null)
  useLayer(layerRef, () => { if (!attaching) onClose() })
  useScrollLock(true)

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 5000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}
      onClick={e => { if (e.target === e.currentTarget && !attaching) onClose() }}>
      <div ref={layerRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Attach a bill" style={{ outline: 'none', background: '#fff', borderRadius: 14, width: 480, maxWidth: '100%', maxHeight: '80vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 60px rgba(0,0,0,0.25)', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #f3f4f6', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#111827', display: 'flex', alignItems: 'center', gap: 7 }}>
            <FileText size={16} color="#4f46e5" /> Attach a bill
          </div>
          <button onClick={onClose} disabled={attaching} aria-label="Close" style={{ border: 'none', background: 'none', cursor: 'pointer', color: 'var(--ink-500)', padding: 4, display: 'flex' }}><X size={18} /></button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px' }}>
          {loading ? (
            <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--ink-500)', fontSize: 13 }}>Loading bills…</div>
          ) : attachable.length === 0 ? (
            <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--ink-500)', fontSize: 13 }}>
              <FileText size={30} strokeWidth={1} style={{ margin: '0 auto 8px', display: 'block', color: '#e5e7eb' }} />
              No bills with an attached document for this vendor
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {attachable.map(b => {
                const checked = !!selected[b.invoice_id]
                return (
                  <div key={b.invoice_id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 9,
                    border: `1.5px solid ${checked ? '#6366f1' : '#eef0f3'}`, background: checked ? '#f5f3ff' : '#fff' }}>
                    <input type="checkbox" checked={checked} onChange={() => toggle(b.invoice_id)}
                      style={{ accentColor: '#6366f1', width: 15, height: 15, flexShrink: 0, cursor: 'pointer' }} />
                    <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={() => toggle(b.invoice_id)}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: '#111827', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{billFileName(b)}</div>
                      <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>
                        {fmtDate(b.invoice_date)}{b.total_amount != null ? ` · ${fmtCurrency(b.total_amount)}` : ''}
                      </div>
                    </div>
                    <button onClick={() => onPreview(b)} title="Preview"
                      style={{ padding: '5px 8px', fontSize: 12, color: '#2563eb', border: '1px solid #dbeafe', borderRadius: 6, background: '#eff6ff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, fontFamily: 'inherit', flexShrink: 0 }}>
                      <Eye size={13} /> Preview
                    </button>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 16px', borderTop: '1px solid #f3f4f6', display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={onClose} disabled={attaching}
            style={{ padding: '9px 16px', borderRadius: 8, border: '1px solid #e5e7eb', background: '#fff', color: '#6b7280', fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}>Cancel</button>
          <div style={{ flex: 1 }} />
          <button onClick={() => onAttach(selectedBills)} disabled={selectedBills.length === 0 || attaching}
            style={{ padding: '9px 18px', borderRadius: 8, border: 'none',
              background: selectedBills.length === 0 || attaching ? '#a5b4fc' : '#6366f1',
              color: '#fff', fontSize: 13, fontWeight: 600, cursor: selectedBills.length === 0 || attaching ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
              display: 'flex', alignItems: 'center', gap: 6 }}>
            {attaching
              ? <><RefreshCw size={13} style={{ animation: 'vp-spin 1s linear infinite' }} /> Attaching…</>
              : <><Paperclip size={13} /> Attach{selectedBills.length ? ` ${selectedBills.length}` : ''}</>}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Bank Verify Strip ────────────────────────────────────────────────────────

function BankVerifyStrip({ acct, supplierId, companyId, onRefresh, children }: {
  acct: BankAccount; supplierId: string; companyId?: string
  /** Re-read the accounts, so the strip shows what the check found. */
  onRefresh?: () => void
  /** A verified account's badge already says so: its Reverify goes in the card header, not a banner. */
  children: (parts: { action: ReactNode; notice: ReactNode }) => ReactNode
}) {
  const [reverifying, setReverifying] = useState(false)
  const [status, setStatus] = useState<'verified' | 'pending' | 'queued'>(acct.verified ? 'verified' : 'pending')
  const [err, setErr] = useState('')
  // A refreshed account is the truth: a queued check that has finished shows its result.
  useEffect(() => { setStatus(acct.verified ? 'verified' : 'pending') }, [acct.verified])

  const handleReverify = async () => {
    setReverifying(true); setErr('')
    try {
      const params = new URLSearchParams({ company_id: companyId || '', account_id: acct.id })
      // fetch doesn't throw on a 4xx/5xx, so "queued" has to be earned by res.ok.
      const res = await fetch(`${API_BASE}/api/vendors/${supplierId}/reverify-bank?${params}`, { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setErr(typeof body?.detail === 'string' ? body.detail : `Couldn't start the check (${res.status}). Try again.`)
        return
      }
      setStatus('queued')
      // The check runs in the background; look again shortly for its result.
      window.setTimeout(() => onRefresh?.(), 6000)
    } catch {
      setErr("Couldn't reach the server. Check your connection and try again.")
    } finally { setReverifying(false) }
  }

  const errLine = err && (
    <div role="alert" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, fontSize: 12, color: '#b42318' }}>
      <AlertCircle size={12} />{err}
    </div>
  )

  if (status === 'queued') {
    return children({ action: null, notice: (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', background: '#eff6ff', borderRadius: 8, marginTop: 8 }}>
        <RefreshCw size={13} color="#2563eb" style={{ animation: 'vp-spin 2s linear infinite' }} />
        <span style={{ fontSize: 12, color: '#1d4ed8', fontWeight: 500 }} role="status">Re-verification queued — this updates on its own when the check finishes</span>
        <span className="vc__grow" />
        {onRefresh && <button type="button" onClick={onRefresh} style={{ fontSize: 11, color: '#1d4ed8', background: 'none', border: '1px solid #bfdbfe', borderRadius: 6, padding: '3px 10px', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500 }}>Check now</button>}
      </div>
    ) })
  }

  if (status === 'verified') {
    return children({
      // Same button as a GSTIN's Reverify.
      action: <button type="button" className="vc__btn vc__btn--sm" onClick={handleReverify} disabled={reverifying}><RefreshCw size={12} />{reverifying ? 'Checking…' : 'Reverify'}</button>,
      notice: errLine,
    })
  }

  return children({ action: null, notice: (
    <>
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', background: '#fef3c7', borderRadius: 8, marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <AlertCircle size={13} color="#d97706" />
        <span style={{ fontSize: 12, color: '#92400e', fontWeight: 500 }}>Verification pending</span>
      </div>
      <button onClick={handleReverify} disabled={reverifying} data-guide="bank.verify"
        style={{ fontSize: 11, color: '#fff', background: '#d97706', border: 'none', borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 600 }}>
        {reverifying ? 'Verifying...' : 'Retry Verification'}
      </button>
    </div>
    {errLine}
    </>
  ) })
}

// ── TDS Settings Panel (inside vendor detail) ──────────────────────────────────

const TDS_MODE_OPTS = [
  { value: 'cumulative', label: 'Cumulative (default)', hint: 'Deduct after FY total crosses threshold' },
  { value: 'always_deduct', label: 'Always deduct', hint: 'From first bill — I know this vendor will cross the threshold' },
  { value: 'per_bill_threshold', label: 'Per-bill threshold', hint: 'Only if this specific bill crosses the threshold' },
]
const TDS_MODE_BADGE: Record<string, { bg: string; color: string }> = {
  always_deduct: { bg: '#E1F5EE', color: '#0F6E56' },
  per_bill_threshold: { bg: '#E6F1FB', color: '#185FA5' },
  cumulative: { bg: '#F1EFE8', color: '#5F5E5A' },
}

const EMPTY_OV_FORM = { tds_section_id: '', tds_mode: 'cumulative', override_rate: '', certificate_no: '', effective_from: '', valid_to: '', mode_reason: '' }

function VendorTdsPanel({ vendor, companyId, tdsCategories, onCategoryChange, canEdit }: {
  vendor: Vendor
  companyId?: string
  tdsCategories: { category_id: string; name: string; tds_rate: number | null; section_code: string | null; section_label: string | null; is_active?: boolean }[]
  /** Saves the category; rejects when the save fails, so the choice can roll back. */
  onCategoryChange: (catId: string) => Promise<void>
  /** Same rule as GSTINs and bank accounts: only admins and finance controllers change tax setup. */
  canEdit: boolean
}) {
  const [catId, setCatId] = useState(vendor.vendor_category_id ?? '')
  const [catState, setCatState] = useState<{ kind: 'idle' | 'saving' | 'saved' } | { kind: 'error'; msg: string }>({ kind: 'idle' })
  const [sections, setSections] = useState<TdsSection[]>([])
  const [overrides, setOverrides] = useState<VendorTdsOverride[]>([])
  const [fyStates, setFyStates] = useState<VendorTdsFyState[]>([])
  const [loading, setLoading] = useState(true)
  // A failed load must not read as "No override — using category default":
  // that would be a false statement about how tax is being deducted.
  const [loadErr, setLoadErr] = useState(false)
  const [showAddOverride, setShowAddOverride] = useState(false)
  const [editingOverrideId, setEditingOverrideId] = useState<string | null>(null)
  const [ovForm, setOvForm] = useState(EMPTY_OV_FORM)
  const [ovErr, setOvErr] = useState('')
  const [ovSaving, setOvSaving] = useState(false)
  const [catchupAlert, setCatchupAlert] = useState<{ override_id: string; summary: CatchupSummary } | null>(null)
  const [catchupErr, setCatchupErr] = useState('')
  const [confirm, setConfirm] = useState<null | { title: string; description: string; confirmLabel: string; tone?: 'danger' | 'primary'; impact?: { label: string; value: ReactNode }[]; run: () => Promise<void> }>(null)

  const load = () => {
    setLoading(true); setLoadErr(false)
    Promise.all([
      fetchVendorOverrides(vendor.supplier_id, companyId),
      fetchFyState(vendor.supplier_id, undefined, companyId),
      fetchTdsSections(companyId),
    ]).then(([o, f, s]) => { setOverrides(o); setFyStates(Array.isArray(f) ? f : []); setSections(s) })
      .catch(() => setLoadErr(true))
      .finally(() => setLoading(false))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [vendor.supplier_id])
  // The header's category is the saved one; follow it when it changes from outside.
  useEffect(() => { setCatId(vendor.vendor_category_id ?? '') }, [vendor.vendor_category_id])

  const activeCat = tdsCategories.find(c => c.category_id === catId)
  const activeOverride = overrides.find(o => o.is_active)

  const handleCatChange = async (newCatId: string) => {
    const before = catId
    setCatId(newCatId); setCatState({ kind: 'saving' })
    try {
      await onCategoryChange(newCatId)
      setCatState({ kind: 'saved' })
    } catch (e) {
      // Put the dropdown back: it must never show a category that didn't save.
      setCatId(before)
      setCatState({ kind: 'error', msg: apiError(e, "Couldn't change the TDS category. Try again.") })
    }
  }

  const validateOverride = (): string => {
    if (ovForm.override_rate.trim()) {
      const r = Number(ovForm.override_rate)
      if (!Number.isFinite(r) || r < 0 || r > 100) return 'Rate must be between 0 and 100%.'
    }
    if (ovForm.effective_from && ovForm.valid_to && ovForm.valid_to < ovForm.effective_from) return "'Valid to' can't be before 'Effective from'."
    return ''
  }

  const handleAddOverride = async () => {
    if (ovSaving) return
    const invalid = validateOverride()
    if (invalid) { setOvErr(invalid); return }
    setOvErr(''); setOvSaving(true)
    try {
      const res = await createVendorOverride({
        supplier_id: vendor.supplier_id,
        tds_section_id: ovForm.tds_section_id || undefined,
        tds_mode: ovForm.tds_mode,
        override_rate: ovForm.override_rate.trim() ? parseFloat(ovForm.override_rate) : undefined,
        certificate_no: ovForm.certificate_no || undefined,
        effective_from: ovForm.effective_from || undefined,
        valid_to: ovForm.valid_to || undefined,
        mode_reason: ovForm.mode_reason || undefined,
        company_id: companyId,
      })
      if (res.catchup) setCatchupAlert({ override_id: res.override_id, summary: res.catchup })
      fetchVendorOverrides(vendor.supplier_id, companyId).then(setOverrides).catch(() => setLoadErr(true))
      setShowAddOverride(false)
      setEditingOverrideId(null)
      setOvForm(EMPTY_OV_FORM)
    } catch (e) {
      setOvErr(apiError(e, "Couldn't save the override. Try again."))
    } finally { setOvSaving(false) }
  }

  // A vendor can only have one override row (unique per tenant+supplier), so editing
  // just re-opens the add form pre-filled — saving upserts the same underlying row.
  const handleEditOverride = (o: VendorTdsOverride) => {
    setOvErr('')
    setEditingOverrideId(o.override_id)
    setOvForm({
      tds_section_id: o.tds_section_id || '',
      tds_mode: o.tds_mode || 'cumulative',
      override_rate: o.override_rate != null ? String(o.override_rate) : '',
      certificate_no: o.certificate_no || '',
      effective_from: o.effective_from || '',
      valid_to: o.lower_cert_valid_to || '',
      mode_reason: o.mode_reason || '',
    })
    setShowAddOverride(true)
  }

  const handleCancelOverrideForm = () => {
    setShowAddOverride(false)
    setEditingOverrideId(null)
    setOvErr('')
    setOvForm(EMPTY_OV_FORM)
  }

  const askDeleteOverride = (o: VendorTdsOverride) => setConfirm({
    title: 'Delete this override?',
    description: 'TDS on this vendor\'s next bills goes back to the category default.',
    confirmLabel: 'Delete override',
    run: async () => {
      try {
        await deleteVendorOverride(o.override_id)
        setOverrides(os => os.filter(x => x.override_id !== o.override_id))
      } catch (e) { setOvErr(apiError(e, "Couldn't delete the override. Try again.")) }
    },
  })

  // Recover or absorb can't be undone, so it is confirmed with the amount at stake.
  const askCatchup = (decision: 'recover' | 'absorb') => {
    if (!catchupAlert) return
    const amt = `₹${catchupAlert.summary.catchup_pending.toLocaleString('en-IN')}`
    setConfirm({
      title: decision === 'recover' ? 'Recover from the next bill?' : 'Absorb as company liability?',
      description: decision === 'recover'
        ? `${amt} of TDS missed on earlier bills is deducted from this vendor's next bill. This can't be undone.`
        : `Your company pays the ${amt} of TDS missed on earlier bills; the vendor isn't charged. This can't be undone.`,
      confirmLabel: decision === 'recover' ? 'Recover from next bill' : 'Absorb liability',
      tone: 'primary',
      impact: [{ label: 'Bills affected', value: catchupAlert.summary.affected_bill_count }, { label: 'TDS owed', value: amt }],
      run: async () => {
        setCatchupErr('')
        try {
          await submitCatchupDecision(catchupAlert.override_id, decision)
          setCatchupAlert(null)
          fetchFyState(vendor.supplier_id, undefined, companyId).then(f => setFyStates(Array.isArray(f) ? f : [])).catch(() => {})
        } catch (e) { setCatchupErr(apiError(e, "Couldn't save that decision. Try again.")) }
      },
    })
  }

  if (loading) return <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--ink-500)', fontSize: 13 }}>Loading TDS settings...</div>

  return (
    <div style={{ marginTop: 16 }}>
      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ''}
        description={confirm?.description ?? ''}
        confirmLabel={confirm?.confirmLabel}
        tone={confirm?.tone}
        impact={confirm?.impact}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => { await confirm?.run(); setConfirm(null) }}
      />
      {/* ── TDS Category ── */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 8 }}>TDS CATEGORY</div>
        <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16 }}>
          <select style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 14, fontFamily: 'inherit', background: canEdit ? '#fff' : '#f3f4f6' }}
            aria-label="TDS category" value={catId} disabled={!canEdit || catState.kind === 'saving'}
            title={canEdit ? undefined : 'Only admins and finance controllers can change this'}
            onChange={e => handleCatChange(e.target.value)}>
            <option value="">— Not assigned —</option>
            {tdsCategories.filter(c => c.is_active !== false).map(c => (
              <option key={c.category_id} value={c.category_id}>{c.name}</option>
            ))}
          </select>
          {catState.kind === 'saving' && <div className="vc__muted vc__small" role="status" style={{ marginTop: 8 }}>Saving…</div>}
          {catState.kind === 'saved' && <div role="status" style={{ marginTop: 8, fontSize: 12, color: '#16a34a', display: 'flex', alignItems: 'center', gap: 4 }}><Check size={12} />Saved</div>}
          {catState.kind === 'error' && <div role="alert" style={{ marginTop: 8, fontSize: 12, color: '#dc2626' }}>{catState.msg}</div>}
          {activeCat && activeCat.tds_rate != null && (
            <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', background: '#eff6ff', color: '#2563eb', borderRadius: 20, fontSize: 12, fontWeight: 600 }}>
                {activeCat.section_label || activeCat.section_code} · {activeCat.name}
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 10px', background: '#f0fdf4', color: '#16a34a', borderRadius: 20, fontSize: 12, fontWeight: 600 }}>
                {activeCat.tds_rate}% TDS
                {activeCat.section_code && <span style={{ fontWeight: 400, color: '#6b7280', fontSize: 11 }}>({activeCat.section_code})</span>}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── Active Override ── */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#374151' }}>DEDUCTION OVERRIDE</div>
          {/* One override per vendor: with one in force, "add" would silently replace it. */}
          {!showAddOverride && canEdit && !loadErr && (
            <button onClick={() => { if (activeOverride) handleEditOverride(activeOverride); else { setEditingOverrideId(null); setShowAddOverride(true) } }}
              style={{ fontSize: 12, padding: '4px 12px', borderRadius: 6, border: '1px solid #d1d5db', background: '#fff', color: '#374151', cursor: 'pointer', fontFamily: 'inherit', fontWeight: 500 }}>
              {activeOverride ? 'Edit override' : '+ Add override'}
            </button>
          )}
        </div>

        {/* Catch-up alert */}
        {catchupAlert && (
          <div style={{ background: '#FAEEDA', border: '1px solid #EF9F27', borderRadius: 10, padding: '12px 16px', marginBottom: 12 }}>
            <div style={{ color: '#633806', fontWeight: 500, fontSize: 13, marginBottom: 4 }}>TDS liability on prior bills — action required</div>
            <div style={{ color: '#854F0B', fontSize: 12 }}>
              {catchupAlert.summary.affected_bill_count} bills totalling ₹{catchupAlert.summary.bills_total_amount.toLocaleString('en-IN')} processed without TDS.
              Outstanding liability: <strong>₹{catchupAlert.summary.catchup_pending.toLocaleString('en-IN')}</strong>
            </div>
            {canEdit ? (
              <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                <button style={{ background: '#E1F5EE', color: '#0F6E56', border: 'none', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontWeight: 500, fontSize: 12, fontFamily: 'inherit' }}
                  onClick={() => askCatchup('recover')}>
                  Recover from next bill
                </button>
                <button style={{ background: '#f3f4f6', color: '#374151', border: '1px solid #d1d5db', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 12, fontFamily: 'inherit' }}
                  onClick={() => askCatchup('absorb')}>
                  Absorb as company liability
                </button>
              </div>
            ) : (
              <div style={{ color: '#854F0B', fontSize: 12, marginTop: 8 }}>An admin or finance controller needs to choose how to settle this.</div>
            )}
            {catchupErr && <div role="alert" style={{ color: '#dc2626', fontSize: 12, marginTop: 8 }}>{catchupErr}</div>}
          </div>
        )}

        {/* Add override form */}
        {showAddOverride && (
          <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 12, padding: 16, marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#111827' }}>{editingOverrideId ? 'Edit vendor override' : 'Add vendor override'}</div>
              <button onClick={handleCancelOverrideForm} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--ink-500)', padding: 4 }}>
                <X size={16} />
              </button>
            </div>

            {/* TDS Section */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>TDS SECTION</div>
              <select style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit', background: '#fff' }}
                value={ovForm.tds_section_id} onChange={e => setOvForm(f => ({ ...f, tds_section_id: e.target.value }))}>
                <option value="">— Use category default —</option>
                {sections.filter(s => s.is_active).map(s => (
                  <option key={s.tds_section_id} value={s.tds_section_id}>
                    {s.old_section_label || s.return_code} · {s.nature_of_payment} ({s.default_rate}%)
                  </option>
                ))}
              </select>
            </div>

            {/* Deduction Mode */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 6, letterSpacing: 0.5 }}>DEDUCTION MODE</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {TDS_MODE_OPTS.map(m => {
                  const selected = ovForm.tds_mode === m.value
                  return (
                    <label key={m.value} style={{
                      display: 'flex', alignItems: 'flex-start', gap: 10, padding: '10px 14px', borderRadius: 10,
                      border: `1.5px solid ${selected ? '#3b82f6' : '#e5e7eb'}`,
                      background: selected ? '#eff6ff' : '#fff', cursor: 'pointer', transition: 'all 0.15s',
                    }}>
                      <input type="radio" name="tds_mode" checked={selected} onChange={() => setOvForm(f => ({ ...f, tds_mode: m.value }))}
                        style={{ marginTop: 2 }} />
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>{m.label}</div>
                        <div style={{ fontSize: 11, color: '#6b7280', marginTop: 1 }}>{m.hint}</div>
                      </div>
                    </label>
                  )
                })}
              </div>
            </div>

            {/* Rate + Certificate */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>RATE OVERRIDE %</div>
                <input type="number" step="0.1" min={0} max={100} placeholder="Section default" aria-label="Rate override %"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit' }}
                  value={ovForm.override_rate} onChange={e => setOvForm(f => ({ ...f, override_rate: e.target.value }))} />
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>CERTIFICATE NO.</div>
                <input placeholder="Sec 197"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit' }}
                  value={ovForm.certificate_no} onChange={e => setOvForm(f => ({ ...f, certificate_no: e.target.value }))} />
              </div>
            </div>

            {/* Dates */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>EFFECTIVE FROM</div>
                <input type="date"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit' }}
                  value={ovForm.effective_from} onChange={e => setOvForm(f => ({ ...f, effective_from: e.target.value }))} />
              </div>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>VALID TO (OPTIONAL)</div>
                <input type="date" min={ovForm.effective_from || undefined} aria-label="Valid to"
                  style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit' }}
                  value={ovForm.valid_to} onChange={e => setOvForm(f => ({ ...f, valid_to: e.target.value }))} />
              </div>
            </div>

            {/* Reason */}
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: '#6b7280', marginBottom: 4, letterSpacing: 0.5 }}>REASON / NOTE</div>
              <input placeholder="e.g. Annual contract ₹5L on record"
                style={{ width: '100%', padding: '8px 12px', borderRadius: 8, border: '1px solid #d1d5db', fontSize: 13, fontFamily: 'inherit' }}
                value={ovForm.mode_reason} onChange={e => setOvForm(f => ({ ...f, mode_reason: e.target.value }))} />
            </div>

            {ovErr && <div role="alert" style={{ color: '#dc2626', fontSize: 12, marginBottom: 8 }}>{ovErr}</div>}

            <button onClick={handleAddOverride} disabled={ovSaving}
              style={{ width: '100%', padding: '10px 0', borderRadius: 8, background: '#111827', color: '#fff', border: 'none', fontSize: 14, fontWeight: 600, cursor: ovSaving ? 'default' : 'pointer', opacity: ovSaving ? 0.7 : 1, fontFamily: 'inherit' }}>
              {ovSaving ? 'Saving…' : editingOverrideId ? 'Update override' : 'Save override'}
            </button>
          </div>
        )}

        {/* Existing overrides list */}
        {loadErr ? (
          <div role="alert" style={{ background: '#fff5f5', border: '1px solid #fca5a5', borderRadius: 12, padding: '16px', color: '#b42318', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertCircle size={14} /><span className="vc__grow">Couldn't load this vendor's TDS overrides.</span>
            <button type="button" className="vc__btn vc__btn--sm" onClick={load}><RefreshCw size={13} />Retry</button>
          </div>
        ) : overrides.length === 0 && !showAddOverride ? (
          <div style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 12, padding: '20px 16px', textAlign: 'center', color: 'var(--ink-500)', fontSize: 13 }}>
            No override — using category default
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {overrides.map(o => {
              const mc = TDS_MODE_BADGE[o.tds_mode] || TDS_MODE_BADGE.cumulative
              return (
                <div key={o.override_id} style={{
                  background: o.is_active ? '#fff' : '#f9fafb',
                  border: `1px solid ${o.is_active ? '#e5e7eb' : '#f3f4f6'}`,
                  borderRadius: 12, padding: '12px 14px',
                  opacity: o.is_active ? 1 : 0.6,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span style={{ background: mc.bg, color: mc.color, padding: '2px 10px', borderRadius: 6, fontSize: 12, fontWeight: 600 }}>
                        {o.tds_mode === 'always_deduct' ? 'Always deduct' : o.tds_mode === 'per_bill_threshold' ? 'Per-bill' : 'Cumulative'}
                      </span>
                      {o.section_return_code && (
                        <span style={{ fontSize: 12, color: '#6b7280' }}>
                          {o.old_section_label || o.section_return_code}
                        </span>
                      )}
                      {o.override_rate != null && (
                        <span style={{ fontSize: 12, fontWeight: 600, color: '#2563eb' }}>@ {o.override_rate}%</span>
                      )}
                      {!o.is_active && <span style={{ fontSize: 11, color: 'var(--ink-500)', fontStyle: 'italic' }}>Expired</span>}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                      {canEdit && <button onClick={() => handleEditOverride(o)} title="Edit this override" aria-label="Edit this override"
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#9ca3af', padding: 2, display: 'flex' }}>
                        <Pencil size={14} />
                      </button>}
                      {o.is_active && canEdit && (
                        <button onClick={() => askDeleteOverride(o)}
                          title="Delete this override" aria-label="Delete this override"
                          style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#d1d5db', padding: 2, display: 'flex' }}>
                          <X size={14} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--ink-500)' }}>
                    {o.effective_from && <>Effective {o.effective_from}</>}
                    {o.mode_reason && <> · {o.mode_reason}</>}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ── FY State ── */}
      {(() => {
        const visibleFyStates = fyStates.filter(fs =>
          Number(fs.cumulative_billed) > 0 || Number(fs.cumulative_tds_deducted) > 0)
        if (visibleFyStates.length === 0) return null
        // The year comes from the rows themselves, newest first — never a hardcoded label.
        const fys = [...new Set(visibleFyStates.map(fs => fs.fy).filter(Boolean))].sort().reverse()
        return (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#374151', marginBottom: 4 }}>
            {fys.length === 1 ? `FY ${fys[0]} STATE` : fys.length > 1 ? `FY ${fys.join(', ')} STATE` : 'FINANCIAL YEAR STATE'}
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--ink-500)', lineHeight: 1.5, marginBottom: 10 }}>
            Each tax section below tracks its own running total for the financial year — a vendor billed
            under two sections (e.g. 194A and 194C) will show two cards. Most sections deduct TDS on the bill
            amount before GST, so they'll total less than "Total billed" above; a few sections deduct on the
            full amount including GST instead — each card below says which applies. Don't add the cards
            together and compare to "Total billed" — they're not meant to match.
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {visibleFyStates.map(fs => {
              // Prefer the section's actual configured rate over a blended
              // TDS-deducted/billed ratio — thresholds (e.g. 194Q only taxes the amount
              // above ₹50L) dilute that ratio well below the real deduction rate.
              const blendedRate = Number(fs.cumulative_billed) > 0
                ? (Number(fs.cumulative_tds_deducted) / Number(fs.cumulative_billed)) * 100
                : 0
              const effRate = fs.default_rate != null ? Number(fs.default_rate) : blendedRate
              const isGross = (fs.base_calculation || '').toUpperCase() === 'GROSS'
              return (
              <div key={fs.state_id} style={{ background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 12, padding: '12px 14px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: '#6b7280' }}>
                    {fs.return_code && <span style={{ fontWeight: 600 }}>{fs.old_section_label || fs.return_code}</span>}
                    <span style={{ marginLeft: 6 }}>FY {fs.fy}</span>
                  </span>
                  {fs.threshold_crossed
                    ? <span style={{ fontSize: 11, fontWeight: 600, color: '#059669', background: '#d1fae5', padding: '2px 8px', borderRadius: 4 }}
                        title="Total bills for this vendor this year passed the tax-free limit, so TDS now applies to every bill.">
                        Threshold crossed
                      </span>
                    : <span style={{ fontSize: 11, color: 'var(--ink-500)' }}
                        title="Total bills for this vendor this year are still under the tax-free limit, so no TDS is deducted yet.">
                        Below threshold
                      </span>}
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--ink-500)', fontWeight: 500 }}
                      title={isGross
                        ? "Sum of this vendor's bills for this financial year, including GST — this section deducts TDS on the full billed amount."
                        : "Sum of this vendor's bills for this financial year, excluding GST — this is the amount TDS is calculated on."}>
                      Billed this year {isGross ? '(incl. GST)' : '(excl. GST)'}
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', fontVariantNumeric: 'tabular-nums' }}>
                      {Number(fs.cumulative_billed).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 10, color: 'var(--ink-500)', fontWeight: 500 }}
                      title="Total tax withheld from this vendor's payments so far this financial year, and deposited to the government on their behalf.">
                      TDS deducted so far
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: '#111827', fontVariantNumeric: 'tabular-nums' }}>
                      {Number(fs.cumulative_tds_deducted).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })}
                      {effRate > 0 && (
                        <span style={{ fontSize: 10, fontWeight: 500, color: 'var(--ink-500)', marginLeft: 4 }}
                          title={fs.default_rate != null
                            ? "This section's configured TDS rate."
                            : "Blended rate — TDS deducted divided by amount billed. Lower than the section's real rate if part of the billed amount fell under a tax-free threshold."}>
                          ({fs.default_rate != null ? '' : '~'}{effRate < 0.1 ? effRate.toFixed(2) : effRate.toFixed(1)}%)
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                {Number(fs.catchup_pending) > 0 && (
                  <div style={{ marginTop: 8, padding: '6px 10px', background: '#FAEEDA', borderRadius: 6, fontSize: 12, color: '#854F0B' }}
                    title="Some earlier bills didn't have TDS deducted yet (e.g. the rule changed mid-year). This is the extra tax still owed to catch up.">
                    Catch-up TDS still owed: <strong>₹{Number(fs.catchup_pending).toLocaleString('en-IN')}</strong>
                    {fs.catchup_decision && <span style={{ marginLeft: 8, fontStyle: 'italic' }}>({fs.catchup_decision})</span>}
                  </div>
                )}
              </div>
              )
            })}
          </div>
        </div>
        )
      })()}
    </div>
  )
}

// ── Vendor Detail Panel ───────────────────────────────────────────────────────
// Exported for components/records/VendorCard, which opens it by id over any page.
/** The card's six views; every route tab (old ones included) maps onto one. */
type View = 'overview' | 'spend' | 'documents' | 'conversations' | 'activity' | 'compliance'
const VIEW_OF: Record<VendorTab, View> = {
  details: 'overview', spend: 'spend', documents: 'documents', statements: 'documents',
  conversations: 'conversations', contacts: 'conversations', activity: 'activity',
  compliance: 'compliance', bank: 'compliance', gstins: 'compliance', tds: 'compliance',
}
const TAB_OF: Record<View, VendorTab> = {
  overview: 'details', spend: 'spend', documents: 'documents', conversations: 'conversations', activity: 'activity', compliance: 'compliance',
}

/**
 * A text box that wraps and grows like the text it replaces, so a long name
 * takes the same lines in the box as it did on the page. Enter saves.
 */
function GrowBox({ value, onChange, onEnter, i, className = '', ...rest }: {
  value: string; onChange: (v: string) => void; onEnter?: () => void; i: number; className?: string
  'aria-label': string; placeholder?: string; inputMode?: 'email' | 'text'
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    // border-box: the box's height is its text plus padding plus border.
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`
  }, [value])
  return (
    <textarea ref={ref} rows={1} className={`vc__kv-in ${className}`} style={{ '--i': i } as CSSProperties} value={value}
      onChange={e => onChange(e.target.value.replace(/\n/g, ' '))}
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); onEnter?.() } }} {...rest} />
  )
}

/** A small green tick on a value that was checked at the source, so it's left alone. */
function VerifiedMark({ what }: { what: string }) {
  return <span className="vc__vmark" title={`Verified: ${what}`} aria-label="Verified"><Check size={7} strokeWidth={4} /></span>
}

export function VendorDetailPanel({ vendor: initialVendor, onClose, companyId, onNavigateToBill, onVendorUpdated, onPayVendor, tab, onTabChange, prefillContact, onPrefillSaved, refreshKey = 0 }: {
  vendor: Vendor
  onClose: () => void
  companyId?: string
  onNavigateToBill?: (invoiceId: string) => void
  onVendorUpdated?: (v: Vendor) => void
  onPayVendor?: (supplierId: string) => void
  /** Controlled by the route, so the open tab is part of the URL. */
  tab: VendorTab
  onTabChange: (tab: VendorTab) => void
  /** Open the Add Contact form already filled in (from a Review finding). */
  prefillContact?: { name: string; role: string; email: string; phone: string }
  /** The prefilled form was saved. */
  onPrefillSaved?: () => void
  /** Bump to re-read the vendor after an outside write (an accepted claim). */
  refreshKey?: number
}) {
  const [vendor, setVendor] = useState<Vendor>(initialVendor)
  const view: View = VIEW_OF[tab] ?? 'overview'
  // USDC payout wallets and contract rates (docs/crypto-rail), on the
  // Compliance view next to bank accounts, for tenants licensed for the rail.
  const cryptoOn = useService('crypto_payouts')
  // Which country's rules apply (auth/jurisdiction.ts): an international tenant
  // has no GSTIN, PAN, Indian TDS or IFSC accounts to show or check.
  const j = useJurisdiction()
  const gstOn = j.packs.gst, panOn = j.packs.pan, tdsOn = j.packs.tds_india, bankOn = j.packs.bank_india
  setBooksCurrency(j.base_currency, j.locale)
  // An outside write (a contract value accepted on this card): read it again.
  useEffect(() => {
    if (!refreshKey) return
    invalidateVendorsCache(companyId)
    fetchVendor(vendor.supplier_id)
      .then(v => { setVendor(v); onVendorUpdated?.(v) })
      .catch(() => {})
  }, [refreshKey])
  const pan = vendor.gstn && vendor.gstn.length === 15 ? vendor.gstn.slice(2, 12) : vendor.pan
  // A PAN read off the GSTIN can't differ from it, so it isn't edited on its own.
  const panFromGstin = !!vendor.gstn && vendor.gstn.length === 15

  // ── Alert toast ───────────────────────────────────────────────
  const [alert, setAlert] = useState<{ msg: string; type: 'error' | 'info'; action?: { label: string; run: () => void } } | null>(null)
  // One timer for the one toast: a second toast restarts it instead of the
  // first toast's timer cutting the second one short.
  const alertTimer = useRef<number | undefined>(undefined)
  const showAlert = (msg: string, type: 'error' | 'info' = 'error', action?: { label: string; run: () => void }) => {
    setAlert({ msg, type, action })
    window.clearTimeout(alertTimer.current)
    alertTimer.current = window.setTimeout(() => setAlert(null), type === 'error' ? 8000 : 5000)
  }
  useEffect(() => () => window.clearTimeout(alertTimer.current), [])

  // ── In-app confirm, for anything destructive or that moves money ─────────
  const [confirm, setConfirm] = useState<null | {
    title: string; description: string; confirmLabel: string; tone?: 'danger' | 'primary'
    impact?: { label: string; value: ReactNode }[]; requireTyped?: string; children?: ReactNode
    run: () => Promise<void> | void
  }>(null)

  // ── GSTIN tab state ───────────────────────────────────────────
  const [gstins, setGstins] = useState<SupplierGstin[]>([])
  const [gstinsLoading, setGstinsLoading] = useState(false)
  const [showAddGstin, setShowAddGstin] = useState(false)
  const [newGstin, setNewGstin] = useState('')
  const [gstinAdding, setGstinAdding] = useState(false)
  const [reverifying, setReverifying] = useState<string | null>(null)

  // The failed-verification toast is said once per GSTIN while the card is open,
  // not again on every visit to Compliance; the row keeps its red strip.
  const toldFailed = useRef(new Set<string>())
  const [gstinsErr, setGstinsErr] = useState(false)
  const loadGstins = () => {
    setGstinsLoading(true); setGstinsErr(false)
    fetchSupplierGstins(vendor.supplier_id, companyId)
      .then(data => {
        setGstins(data)
        const failed = data.find(g => g.status === 'FAILED' && g.error_message && !toldFailed.current.has(g.gstin))
        if (failed) { toldFailed.current.add(failed.gstin); showAlert(failed.error_message!, 'error') }
      })
      .catch(() => setGstinsErr(true))
      .finally(() => setGstinsLoading(false))
  }
  useEffect(() => { if (view === 'compliance') loadGstins() }, [view, vendor.supplier_id])

  const [gstinErr, setGstinErr] = useState('')
  const handleAddGstin = async () => {
    const gstin = newGstin.trim().toUpperCase()
    if (!gstin || gstinAdding) return
    if (!GSTN_RE.test(gstin)) { setGstinErr('That isn’t a valid GSTIN — 15 characters, e.g. 27AABCT1332L1ZU.'); return }
    setGstinErr('')
    setGstinAdding(true)
    try {
      const added = await addSupplierGstin(vendor.supplier_id, gstin, companyId)
      setGstins(prev => [...prev, added])
      setNewGstin(''); setShowAddGstin(false)
      if (added.status === 'FAILED') showAlert(added.error_message || 'GSTIN added but verification failed', 'error')
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showAlert(detail || (e instanceof Error ? e.message : 'Failed to add GSTIN'))
    } finally { setGstinAdding(false) }
  }

  const handleReverify = async (g: SupplierGstin) => {
    const key = g.id ?? 'primary'
    setReverifying(key)
    try {
      const updated = g.id
        ? await reverifySupplierGstin(vendor.supplier_id, g.id, companyId)
        : await reverifyPrimaryGstin(vendor.supplier_id, companyId)
      setGstins(prev => prev.map(x => (x.id === g.id && x.gstin === g.gstin) ? updated : x))
      if (updated.status === 'VERIFIED') {
        const nameMsg = (updated as SupplierGstin & { name_updated?: boolean; new_name?: string }).name_updated
          ? `GSTIN verified — vendor name updated to "${(updated as SupplierGstin & { new_name?: string }).new_name}"`
          : 'GSTIN verified successfully'
        showAlert(nameMsg, 'info')
        if (g.is_primary) {
          const newName = (updated as SupplierGstin & { new_name?: string }).new_name
          setVendor(v => ({ ...v, status: 'VERIFIED', ...(newName ? { name: newName } : {}) }))
          onVendorUpdated?.({ ...vendor, status: 'VERIFIED', ...(newName ? { name: newName } : {}) })
          invalidateVendorsCache(companyId)
        }
      } else {
        showAlert(updated.error_message || 'Verification failed', 'error')
      }
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showAlert(detail || 'Re-verification failed')
    } finally { setReverifying(null) }
  }

  const handleDeleteGstin = (g: SupplierGstin) => {
    if (!g.id) return
    setConfirm({
      title: 'Remove this GSTIN?',
      description: `${g.gstin} will no longer be on file for ${vendor.name}.`,
      confirmLabel: 'Remove GSTIN',
      run: async () => {
        try {
          await deleteSupplierGstin(vendor.supplier_id, g.id!, companyId)
          setGstins(prev => prev.filter(x => x.id !== g.id))
        } catch (e: unknown) {
          showAlert(apiError(e, 'Failed to remove GSTIN'))
        }
      },
    })
  }

  // ── Edit state ────────────────────────────────────────────────
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({
    name: vendor.name,
    legal_name: vendor.legal_name ?? '',
    email: vendor.email ?? '',
    payment_terms_days: String(vendor.payment_terms_days),
    pan: (vendor.gstn && vendor.gstn.length === 15 ? vendor.gstn.slice(2, 12) : vendor.pan) ?? '',
    vendor_category_id: vendor.vendor_category_id ?? '',
  })
  // Legal name is written only when someone actually typed in it — renaming
  // the brand must not quietly store the old brand name as the legal name.
  const [legalTouched, setLegalTouched] = useState(false)
  const [tdsCategories, setTdsCategories] = useState<{ category_id: string; name: string; tds_rate: number | null; section_code: string | null; section_label: string | null; is_active?: boolean }[]>([])
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState('')
  const { user, activeBrand } = useAuth()
  const canEdit = user?.role === 'admin' || user?.role === 'finance_controller'

  useEffect(() => {
    fetch(`${API_BASE}/api/settings/tds/vendor-categories?company_id=${companyId ?? ''}`)
      .then(r => r.json()).then(d => setTdsCategories(Array.isArray(d) ? d.filter((c: { is_active: boolean }) => c.is_active) : []))
      .catch(() => {})
  }, [companyId])

  const startEdit = () => {
    setLegalTouched(false)
    setEditForm({
      name: vendor.name,
      legal_name: vendor.legal_name ?? '',
      email: vendor.email ?? '',
      payment_terms_days: String(vendor.payment_terms_days),
      pan: (vendor.gstn && vendor.gstn.length === 15 ? vendor.gstn.slice(2, 12) : vendor.pan) ?? '',
      vendor_category_id: vendor.vendor_category_id ?? '',
    })
    setSaveErr('')
    setEditing(true)
  }

  const cancelEdit = () => { setEditing(false); setSaveErr('') }

  const handleSave = async () => {
    if (saving) return
    if (!editForm.name.trim()) { setSaveErr('Vendor name is required'); return }
    // 0 is a real term (due on receipt); only a blank or nonsense value is refused.
    const termsRaw = editForm.payment_terms_days.trim()
    if (!termsRaw) { setSaveErr('Enter the payment terms in days (0 if bills are due on receipt).'); return }
    const n = parseInt(termsRaw, 10)
    const terms = Number.isFinite(n) ? n : 30
    if (terms < 0) { setSaveErr('Payment terms can’t be negative.'); return }
    const legal = legalTouched ? editForm.legal_name.trim() : undefined
    setSaving(true); setSaveErr('')
    try {
      await updateVendor(vendor.supplier_id, {
        name: editForm.name.trim(),
        legal_name: legal,
        email: editForm.email.trim() || undefined,
        payment_terms_days: terms,
        // An empty PAN clears it; undefined would have left the old one in place.
        pan: editForm.pan.trim(),
        vendor_category_id: editForm.vendor_category_id || null,
      }, companyId)
      const cat = tdsCategories.find(c => c.category_id === editForm.vendor_category_id)
      const updated: Vendor = {
        ...vendor,
        name: editForm.name.trim(),
        legal_name: legal === undefined ? vendor.legal_name : legal || null,
        email: editForm.email.trim() || null,
        payment_terms_days: terms,
        pan: editForm.pan.trim() || null,
        vendor_category_id: editForm.vendor_category_id || null,
        vendor_category_name: cat?.name ?? null,
      }
      setVendor(updated)
      onVendorUpdated?.(updated)
      invalidateVendorsCache(companyId)
      setEditing(false)
    } catch (e: unknown) {
      setSaveErr(apiError(e, "Couldn't save. Check the details and try again."))
    } finally {
      setSaving(false)
    }
  }
  // What's typed differs from what's saved — closing the card would lose it.
  const editDirty = editing && (
    editForm.name !== vendor.name || editForm.email !== (vendor.email ?? '') ||
    editForm.payment_terms_days !== String(vendor.payment_terms_days) ||
    editForm.pan !== ((vendor.gstn && vendor.gstn.length === 15 ? vendor.gstn.slice(2, 12) : vendor.pan) ?? '') ||
    (legalTouched && editForm.legal_name !== (vendor.legal_name ?? ''))
  )
  const [statements, setStatements] = useState<Bill[]>([])
  const [billsStatus, setBillsStatus] = useState<LoadStatus>('loading')
  const stmtLoading = billsStatus === 'loading'
  const [previewDoc, setPreviewDoc] = useState<{ url: string; file_name: string; doc_id?: string } | null>(null)
  // Documents are scoped to the brand being viewed (activeBrand): listed by it and
  // stamped with it on upload, so a shared vendor keeps a set per brand.
  const [vendorDocs, setVendorDocs] = useState<VendorDocument[] | null>(null)
  const [docsStatus, setDocsStatus] = useState<LoadStatus>('loading')
  // A failed reload keeps what was already listed, but still says it failed.
  const loadDocs = () => {
    fetchVendorDocuments(vendor.supplier_id, activeBrand?.id)
      .then(d => { setVendorDocs(d); setDocsStatus('ready') })
      .catch(() => setDocsStatus('error'))
  }
  useEffect(() => { loadDocs() }, [vendor.supplier_id, activeBrand?.id])
  // A contract takes a moment to read: look again while one is still being read.
  useEffect(() => {
    if (!vendorDocs?.some(d => d.doc_type === 'contract' && (d.claims_status === 'PENDING' || d.claims_status === 'RUNNING'))) return
    const t = window.setTimeout(loadDocs, 8000)
    return () => window.clearTimeout(t)
  }, [vendorDocs])

  const docUrl = (d: VendorDocument, preview: boolean) =>
    `${API_BASE}/api/vendors/${vendor.supplier_id}/documents/${d.id}/download?company_id=${companyId ?? ''}${preview ? '&preview=true' : ''}`
  // Only a contract has extracted values, so only it opens with the claims panel.
  const previewVendorDoc = (d: VendorDocument) =>
    setPreviewDoc({ url: docUrl(d, true), file_name: d.file_name, doc_id: d.doc_type === 'contract' ? d.id : undefined })
  const downloadVendorDoc = (d: VendorDocument) => { downloadAuthed(docUrl(d, false), d.file_name) }
  // There's no restore on the server, so a delete waits a few seconds before
  // it's sent: the file disappears at once, and Undo simply never sends it.
  // Closing the card sends whatever is still waiting.
  const pendingDeletes = useRef(new Map<string, { timer: number; send: () => void }>())
  const handleDocDelete = (d: VendorDocument) => {
    const send = () => {
      pendingDeletes.current.delete(d.id)
      deleteVendorDocument(vendor.supplier_id, d.id)
        .catch(() => { showAlert(`Couldn’t delete ${d.file_name}. It’s back in the list.`); loadDocs() })
    }
    setVendorDocs(ds => ds?.filter(x => x.id !== d.id) ?? ds)
    pendingDeletes.current.set(d.id, { timer: window.setTimeout(send, UNDO_MS), send })
    showAlert(`Deleted ${d.file_name}`, 'info', {
      label: 'Undo',
      run: () => {
        const p = pendingDeletes.current.get(d.id)
        if (!p) return
        window.clearTimeout(p.timer)
        pendingDeletes.current.delete(d.id)
        setVendorDocs(ds => ds && !ds.some(x => x.id === d.id) ? [d, ...ds] : ds)
        setAlert(null)
      },
    })
  }
  useEffect(() => () => {
    for (const p of pendingDeletes.current.values()) { window.clearTimeout(p.timer); p.send() }
  }, [])

  // Bank accounts state — loaded once on mount, refreshed only after mutations
  const [bankAccounts, setBankAccounts] = useState<BankAccount[]>([])
  const [bankStatus, setBankStatus] = useState<LoadStatus>('loading')
  const bankLoading = bankStatus === 'loading'
  // Account ids shown in full on the Compliance tab; masked otherwise.
  const [shownAccts, setShownAccts] = useState<Set<string>>(new Set())
  const toggleAcct = (id: string) => setShownAccts(prev => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const [showAddBankForm, setShowAddBankForm] = useState(false)
  const [bankForm, setBankForm] = useState({ account_number: '', ifsc: '', bank_name: '', is_primary: false })
  const [bankFormSaving, setBankFormSaving] = useState(false)
  const [bankFormError, setBankFormError] = useState('')

  // Fetch once when the panel opens — not on every tab switch. A refresh after
  // a re-verify keeps the list on screen rather than flashing the skeleton.
  const loadBank = (quiet = false) => {
    if (!quiet) setBankStatus('loading')
    fetchVendorBankAccounts(vendor.supplier_id, companyId)
      .then(a => { setBankAccounts(a); setBankStatus('ready') })
      .catch(() => { if (!quiet) setBankStatus('error') })
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadBank() }, [vendor.supplier_id, companyId])

  const acctName = (a: Pick<BankAccount, 'bank_name' | 'ifsc' | 'account_number'>) =>
    `${a.bank_name || (a.ifsc ? a.ifsc.slice(0, 4) : 'Bank')} ${maskAcct(a.account_number)}`

  // The primary account is where payments go, so changing it is always
  // confirmed with both accounts named — old and new — never on one click.
  const primaryChange = (from: BankAccount | undefined, to: string): ReactNode => (
    <div className="vc__swap">
      <div><span className="vc__muted vc__xs">Payments go to now</span><b className="vc__mono">{from ? acctName(from) : 'No primary account'}</b></div>
      <ArrowRightLeft size={14} aria-hidden="true" />
      <div><span className="vc__muted vc__xs">Payments will go to</span><b className="vc__mono">{to}</b></div>
    </div>
  )

  const saveBankAccount = async () => {
    setBankFormSaving(true); setBankFormError('')
    try {
      await addVendorBankAccount(vendor.supplier_id, { ...bankForm, company_id: companyId })
      const updated = await fetchVendorBankAccounts(vendor.supplier_id, companyId)
      setBankAccounts(updated); setBankStatus('ready')
      setShowAddBankForm(false)
      setBankForm({ account_number: '', ifsc: '', bank_name: '', is_primary: false })
    } catch (e) { setBankFormError(apiError(e, "Couldn't add the account. Try again.")) }
    finally { setBankFormSaving(false) }
  }

  const handleAddBankAccount = () => {
    if (!canEdit || bankFormSaving) return
    if (!bankForm.account_number.trim()) { setBankFormError('Account number is required'); return }
    const current = bankAccounts.find(a => a.is_primary)
    if (bankForm.is_primary && current) {
      setConfirm({
        title: 'Make the new account primary?',
        description: `Future payments to ${vendor.name} will go to the new account instead of the current primary.`,
        confirmLabel: 'Add and make primary',
        tone: 'primary',
        children: primaryChange(current, acctName({ ...bankForm, account_number: bankForm.account_number.trim() })),
        run: saveBankAccount,
      })
      return
    }
    void saveBankAccount()
  }

  const handleDeleteBankAccount = (accountId: string) => {
    if (!canEdit) return
    const acct = bankAccounts.find(a => a.id === accountId)
    setConfirm({
      title: 'Delete this bank account?',
      description: `${acct ? acctName(acct) : 'This account'} is removed from ${vendor.name}. Payments can't be sent to it after this.`,
      confirmLabel: 'Delete account',
      requireTyped: 'DELETE',
      run: async () => {
        try {
          await deleteVendorBankAccount(vendor.supplier_id, accountId, companyId)
          setBankAccounts(prev => prev.filter(a => a.id !== accountId))
        } catch (e: unknown) {
          showAlert(apiError(e, 'Failed to delete'))
        }
      },
    })
  }

  // Contacts (POCs) state — loaded lazily when the Contacts tab opens
  const [contacts, setContacts] = useState<VendorContact[]>([])
  const [contactsStatus, setContactsStatus] = useState<LoadStatus>('loading')
  const contactsLoading = contactsStatus === 'loading'
  const [showAddContactForm, setShowAddContactForm] = useState(false)
  const emptyContactForm = { name: '', role: '', email: '', phone: '', is_primary: false }
  const [contactForm, setContactForm] = useState(emptyContactForm)
  const [contactFormSaving, setContactFormSaving] = useState(false)
  const [contactFormError, setContactFormError] = useState('')
  // A contact Review found: the form opens filled in, the user checks it and saves.
  const [fromPrefill, setFromPrefill] = useState(false)
  const contactFormRef = useRef<HTMLDivElement>(null)
  // The form sits below the contact list, which loads after it opens — so wait
  // for the list, then bring the form (and its Save) into the middle of view.
  useEffect(() => {
    if (!fromPrefill || contactsLoading || !showAddContactForm) return
    const t = window.setTimeout(() => {
      contactFormRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }, 350)
    return () => window.clearTimeout(t)
  }, [fromPrefill, contactsLoading, showAddContactForm])
  useEffect(() => {
    if (!prefillContact) return
    setContactForm({
      name: prefillContact.name ?? '', role: prefillContact.role ?? '',
      email: prefillContact.email ?? '', phone: prefillContact.phone ?? '', is_primary: false,
    })
    setShowAddContactForm(true)
    setFromPrefill(true)
  }, [prefillContact?.email, prefillContact?.name])

  const loadContacts = () => {
    setContactsStatus('loading')
    fetchVendorContacts(vendor.supplier_id, companyId)
      .then(c => { setContacts(c); setContactsStatus('ready') })
      .catch(() => setContactsStatus('error'))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadContacts() }, [vendor.supplier_id, companyId])

  const handleAddContact = async () => {
    if (!contactForm.name.trim()) { setContactFormError('Name is required'); return }
    if (!contactForm.role.trim()) { setContactFormError('Role is required'); return }
    if (!contactForm.email.trim()) { setContactFormError('Email is required'); return }
    setContactFormSaving(true); setContactFormError('')
    try {
      await addVendorContact(vendor.supplier_id, { ...contactForm, company_id: companyId })
      const updated = await fetchVendorContacts(vendor.supplier_id, companyId)
      setContacts(updated); setContactsStatus('ready')
      setShowAddContactForm(false)
      setContactForm(emptyContactForm)
      loadInbox()   // the new contact's threads now count as this vendor's
      if (fromPrefill) { setFromPrefill(false); onPrefillSaved?.() }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Failed to add contact'
      setContactFormError(msg)
    } finally { setContactFormSaving(false) }
  }

  const handleDeleteContact = (contactId: string) => {
    const c = contacts.find(x => x.id === contactId)
    setConfirm({
      title: 'Delete this contact?',
      description: `${c?.name ?? 'This contact'}${c?.email ? ` (${c.email})` : ''} is removed from ${vendor.name}.`,
      confirmLabel: 'Delete contact',
      run: async () => {
        try {
          await deleteVendorContact(vendor.supplier_id, contactId, companyId)
          setContacts(prev => prev.filter(x => x.id !== contactId))
        } catch { showAlert('Failed to delete contact') }
      },
    })
  }

  const handleSetPrimaryContact = async (contactId: string) => {
    try {
      const updated = await setPrimaryVendorContact(vendor.supplier_id, contactId, companyId)
      setContacts(updated)
    } catch { showAlert('Failed to set primary contact') }
  }

  // ── Mailbox + the vendor's inbox ──────────────────────────────────────────
  // The inbox lists every thread with this vendor in the user's own mailbox,
  // including the ones the vendor started (the old per-contact list only had
  // the threads we sent). It also feeds "last heard".
  const [emailConn, setEmailConn] = useState<EmailConnection | null>(null)
  const [connectingEmail, setConnectingEmail] = useState(false)
  const [attachFiles, setAttachFiles] = useState<File[]>([])
  const [showBillPicker, setShowBillPicker] = useState(false)
  const [attachingBills, setAttachingBills] = useState(false)
  // Which attached file is which bill, so the composer can name it and the AI can write about it.
  const [billFiles, setBillFiles] = useState<Record<string, string>>({})
  const [inbox, setInbox] = useState<VendorInboxData | null>(null)
  const [inboxLoading, setInboxLoading] = useState(false)
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null)
  // Read after an await: has the user moved to another conversation since?
  const activeThreadRef = useRef(activeThreadId)
  activeThreadRef.current = activeThreadId
  const [composeTo, setComposeTo] = useState<string | null>(null)

  const loadInbox = () => {
    setInboxLoading(true)
    fetchVendorInbox(vendor.supplier_id)
      .then(setInbox)
      // A failed load says so (the inbox shows its warning bar), keeping any threads already shown,
      // instead of passing for an empty, disconnected mailbox.
      .catch(e => {
        const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
        const error = typeof detail === 'string' && detail ? detail : 'Couldn’t load conversations. Check your connection and try again.'
        setInbox(prev => ({ ...(prev ?? { me: null, connected: false, read_available: false, addresses: [], domains: [], threads: [] }), error }))
      })
      .finally(() => setInboxLoading(false))
  }
  useEffect(() => { loadInbox() }, [vendor.supplier_id])

  const loadEmailConn = () => {
    if (!user?.company_id || !user?.username) return
    fetchEmailConnection(user.company_id, user.username)
      .then(setEmailConn)
      .catch(() => setEmailConn({ connected: false }))
  }
  useEffect(() => { loadEmailConn() }, [user?.company_id, user?.username])

  // Detect return from the OAuth consent flow (?email_connected=1)
  useEffect(() => {
    const p = new URLSearchParams(window.location.search)
    if (p.get('email_connected') === '1') {
      showAlert('Mailbox connected', 'info')
      loadEmailConn()
      loadInbox()
      p.delete('email_connected'); p.delete('provider')
      window.history.replaceState({}, '', `${window.location.pathname}${p.toString() ? '?' + p : ''}`)
    } else if (p.get('email_connected') === '0') {
      showAlert('Mailbox connection was cancelled', 'error')
      p.delete('email_connected'); p.delete('reason')
      window.history.replaceState({}, '', `${window.location.pathname}${p.toString() ? '?' + p : ''}`)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleConnectEmail = async () => {
    if (!user?.company_id || !user?.username) return
    setConnectingEmail(true)
    try {
      const url = await startEmailAuthorize(user.company_id, user.username)
      window.location.href = url   // redirect to Google consent
    } catch {
      showAlert('Could not start Gmail connection')
      setConnectingEmail(false)
    }
  }

  // A sender Review or the inbox found: the contact form opens with their address.
  const openAddContact = (email?: string) => {
    setContactForm({ ...emptyContactForm, email: email ?? '' })
    setContactFormError('')
    setShowAddContactForm(true)
  }

  const handleSetPrimary = (accountId: string) => {
    if (!canEdit) return
    const next = bankAccounts.find(a => a.id === accountId)
    if (!next) return
    setConfirm({
      title: 'Change the primary bank account?',
      description: `Future payments to ${vendor.name} will go to ${acctName(next)}. Check this against the vendor's own records before you confirm.`,
      confirmLabel: 'Make primary',
      tone: 'primary',
      children: primaryChange(bankAccounts.find(a => a.is_primary), acctName(next)),
      run: async () => {
        try {
          const updated = await setPrimaryBankAccount(vendor.supplier_id, accountId, companyId)
          setBankAccounts(updated)
          showAlert(`Payments now go to ${acctName(next)}`, 'info')
        } catch (e) { showAlert(apiError(e, 'Failed to set primary')) }
      },
    })
  }

  // Preview a bill's stored document inline (reuses the shared DocPreviewModal).
  const previewBill = (b: Bill) => {
    if (!b.upload_id) return
    setPreviewDoc({ url: `${API_BASE}/api/docs/${b.upload_id}`, file_name: billFileName(b) })
  }

  // Pull the selected bills' stored PDFs straight from the server and stage them
  // as email attachments — no manual download/re-upload round-trip.
  const handleAttachBills = async (bills: Bill[]) => {
    setAttachingBills(true)
    const startedIn = activeThreadRef.current
    try {
      const existing = new Set(attachFiles.map(f => f.name))
      const collected: File[] = []
      let failed = 0
      for (const b of bills) {
        if (!b.upload_id) continue
        const name = billFileName(b)
        if (existing.has(name)) continue
        try {
          const blob = await fetchAuthedBlob(`${API_BASE}/api/docs/${b.upload_id}`)
          collected.push(new File([blob], name, { type: blob.type || 'application/pdf' }))
          existing.add(name)
          setBillFiles(m => ({ ...m, [name]: b.invoice_id }))
        } catch { failed++ }
      }
      // The files were fetched for one email; don't drop them into another.
      if (activeThreadRef.current !== startedIn) {
        if (collected.length) showAlert('Bills not attached: you moved to another conversation', 'info')
        return
      }
      if (collected.length) setAttachFiles(a => [...a, ...collected])
      if (failed) showAlert(`${failed} bill${failed > 1 ? 's' : ''} could not be attached`, 'error')
    } finally {
      setAttachingBills(false)
      setShowBillPicker(false)
    }
  }

  // Fetch statements once on mount — bills are already cached in localStorage.
  // Statements are scoped to the brand currently being viewed (activeBrand), so even
  // a vendor shared across brands shows only the statements raised under this brand —
  // and migrating a vendor never carries its statements to another brand.
  // A failed fetch is an error, not "no bills": the KPIs, schedule and nudges
  // that depend on bills step aside instead of claiming ₹0 outstanding.
  const loadStatements = () => {
    setBillsStatus('loading')
    fetchBills(false, companyId)
      .then(bills => {
        setStatements(bills.filter(b =>
          (b.supplier_id ? b.supplier_id === vendor.supplier_id : b.vendor_name?.toUpperCase() === vendor.name.toUpperCase()) &&
          (activeBrand == null || b.brand_id === activeBrand.id)
        ))
        setBillsStatus('ready')
      })
      .catch(() => setBillsStatus('error'))
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { loadStatements() }, [vendor.supplier_id, vendor.name, activeBrand?.id, companyId])
  const retryBills = () => loadStatements()
  const retryDocsAndBills = () => { if (billsStatus === 'error') loadStatements(); if (docsStatus === 'error') loadDocs() }

  // ── Card layout ───────────────────────────────────────────────────────────
  const navigate = useNavigate()
  const now = useMemo(() => today(), [])
  // base_currency is a dependency: what counts as "converted" depends on it.
  const summary = useMemo(() => summarize(statements, now), [statements, now, j.base_currency]) // eslint-disable-line react-hooks/exhaustive-deps
  // Verified under the tenant's own rules when the backend says so (GSTIN for an
  // Indian tenant, wallet / tax ID abroad); otherwise the GST check is all there is,
  // and only means anything where GST applies.
  const verification = vendor.verification ?? null
  const isVerified = verification
    ? verification.status?.toUpperCase() === 'VERIFIED'
    : gstOn && vendor.status?.toUpperCase() === 'VERIFIED'
  const verifiedLabel = verification?.label || (gstOn ? 'KYB verified' : 'Verified')
  const unverifiedLabel = verification && verification.status?.toUpperCase() !== 'VERIFIED'
    ? verification.label
    : gstOn ? statusWords(vendor.status) : 'Not verified'
  // The bank card shows the last four digits until asked; hidden again when another vendor opens.
  const [showAcct, setShowAcct] = useState(false)
  useEffect(() => { setShowAcct(false) }, [vendor.supplier_id])
  const primaryBank = bankAccounts.find(a => a.is_primary) ?? bankAccounts[0] ?? null
  const contactNames = useMemo(
    () => Object.fromEntries(contacts.filter(c => c.email).map(c => [c.email!.toLowerCase(), c.name])),
    [contacts],
  )
  const newSenders = [...new Set(inbox?.threads.flatMap(t => t.unknown_senders) ?? [])].filter(a => !contactNames[a])
  const payFn = user?.role === 'admin' && onPayVendor ? () => onPayVendor(vendor.supplier_id) : undefined

  const [docFolder, setDocFolder] = useState<DocFolder>('bills')
  const [billFilter, setBillFilter] = useState<BillFilter>('all')
  const [selMonth, setSelMonth] = useState<string | null>(null)
  const [uploadReq, setUploadReq] = useState(0)
  useEffect(() => { if (tab === 'statements') setDocFolder('bills') }, [tab])

  const mainRef = useRef<HTMLElement>(null)
  const tabsRef = useRef<HTMLDivElement>(null)
  const tabBtns = useRef<Partial<Record<View, HTMLButtonElement | null>>>({})
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null)
  useLayoutEffect(() => {
    const place = () => {
      const b = tabBtns.current[view]
      if (b) setInk({ left: b.offsetLeft, width: b.offsetWidth })
    }
    place()
    window.addEventListener('resize', place)
    // Tabs widen when their counts arrive; the underline follows them, not just the window.
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(place) : null
    if (ro && tabsRef.current) {
      ro.observe(tabsRef.current)
      Object.values(tabBtns.current).forEach(b => { if (b) ro.observe(b) })
    }
    return () => { window.removeEventListener('resize', place); ro?.disconnect() }
  }, [view])

  const stackSide = useMediaQuery('(width <= 960px)')
  const goView = (v: View) => {
    onTabChange(TAB_OF[v])
    // Bring the tabs back to the top of whatever actually scrolls: the main
    // column on wide screens, the whole split once it stacks (960px and below).
    const tabs = tabsRef.current
    let scroller: HTMLElement | null = tabs?.parentElement ?? null
    while (scroller && !(scroller.scrollHeight > scroller.clientHeight && /(auto|scroll)/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement
    if (!tabs || !scroller) return
    const top = tabs.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
    if (scroller.scrollTop > top) scroller.scrollTo({ top: top - 2, behavior: 'smooth' })
  }

  // Tabs follow the tab pattern: arrows move between them (and open them),
  // Home/End jump to the ends; only the open tab is in the Tab order.
  const onTabKey = (e: ReactKeyboardEvent<HTMLButtonElement>, v: View) => {
    const order: View[] = ['overview', 'spend', 'documents', 'conversations', 'activity', 'compliance']
    const i = order.indexOf(v)
    const to = e.key === 'ArrowRight' ? order[(i + 1) % order.length]
      : e.key === 'ArrowLeft' ? order[(i - 1 + order.length) % order.length]
      : e.key === 'Home' ? order[0] : e.key === 'End' ? order[order.length - 1] : null
    if (!to) return
    e.preventDefault()
    tabBtns.current[to]?.focus()
    goView(to)
  }

  // ── Closing: a real modal, and unsaved edits are asked about first ────────
  const cardRef = useRef<HTMLDivElement>(null)
  const requestClose = () => {
    if (!editDirty) { onClose(); return }
    setConfirm({
      title: 'Discard your changes?',
      description: `Your edits to ${vendor.name}'s details aren't saved. Closing the card drops them.`,
      confirmLabel: 'Discard changes',
      run: () => { setEditing(false); onClose() },
    })
  }
  useLayer(cardRef, requestClose)
  const goBills = (f: BillFilter) => { setDocFolder('bills'); setBillFilter(f); goView('documents') }
  const goCompliance = (section: 'bank' | 'gstins' | 'tds') => onTabChange(section)
  const openThread = (id: string) => { setActiveThreadId(id); goView('conversations') }
  const composeNew = (contactId?: string) => { setComposeTo(contactId ?? null); setActiveThreadId('__new'); goView('conversations') }

  // An old link (…/bank, …/gstins, …/tds) lands on its section of Compliance.
  useEffect(() => {
    if (tab !== 'bank' && tab !== 'gstins' && tab !== 'tds') return
    const t = window.setTimeout(() => document.getElementById(`vc-sec-${tab}`)?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 250)
    return () => window.clearTimeout(t)
  }, [tab])

  // Which sources failed to load, so nothing reads a failure as "none".
  const failed = { bills: billsStatus === 'error', bank: bankStatus === 'error', inbox: !!inbox?.error }

  const copyLink = () => {
    navigator.clipboard?.writeText(window.location.href)
      .then(() => showAlert('Link copied', 'info'), () => showAlert(window.location.href, 'info'))
  }

  const VIEWS: { v: View; label: string; icon: ReactNode; count?: number; hot?: number }[] = [
    { v: 'overview', label: 'Overview', icon: <LayoutGrid size={14} /> },
    { v: 'spend', label: 'Spend', icon: <TrendingUp size={14} /> },
    { v: 'documents', label: 'Documents', icon: <FolderOpen size={14} />, count: summary.bills.length + (vendorDocs?.length ?? 0) },
    { v: 'conversations', label: 'Conversations', icon: <MessageSquare size={14} />, count: inbox?.threads.length, hot: inbox?.threads.filter(t => t.needs_reply).length },
    { v: 'activity', label: 'Activity', icon: <History size={14} /> },
    { v: 'compliance', label: 'Compliance', icon: <Shield size={14} /> },
  ]

  const since = vendor.created_at ? new Date(vendor.created_at) : null
  const fixLabel = (icon: ReactNode, text: string) => <div className="vd__field-lbl">{icon}{text}</div>

  // Vendor details, People and Bank account. On a wide screen they're a
  // sidebar; once the card stacks (960px and below) they sit between the
  // header and the tabs, folded, rather than several screens down.
  const sidebar = (
    <>
            <SideSection id="details" title="Vendor details" forceOpen={editing} actions={<>
                {canEdit && !editing && <button className="vc__iconbtn vc__iconbtn--sm" aria-label="Edit vendor details" title="Edit" onClick={startEdit}><Pencil size={14} /></button>}
                {editing && (
                  <span className="vc__edit-acts">
                    <button className="vc__btn vc__btn--sm" disabled={saving} onClick={cancelEdit}>Cancel</button>
                    <button className="vc__btn vc__btn--sm vc__btn--primary" disabled={saving} onClick={handleSave}>{saving ? 'Saving…' : 'Save'}</button>
                  </span>
                )}
              </>}>
              {/* One list in both modes: editing turns the values that can change into
                  boxes where they stand; what's verified stays as it is, marked. */}
              <div className={`vc__kvs${editing ? ' is-editing' : ''}`}>
                <div className="vc__kv" data-fix="name" data-guide="details.name">
                  {fixLabel(<Building2 size={14} />, 'Brand name')}
                  <div className="vc__kv-v">
                    {editing
                      ? <GrowBox i={0} aria-label="Brand name" value={editForm.name} onEnter={handleSave} onChange={v => setEditForm(f => ({ ...f, name: v }))} />
                      : vendor.name}
                  </div>
                </div>
                {/* Shown while editing even when it matches the brand name (or is empty),
                    so it can be set at all; a verified one stays the GST portal's. */}
                {((editing && !isVerified) || (vendor.legal_name && vendor.legal_name.trim().toUpperCase() !== vendor.name.trim().toUpperCase())) && (
                  <div className="vc__kv">{fixLabel(<Building2 size={14} />, 'Legal name')}
                    <div className="vc__kv-v">
                      {editing && !isVerified
                        ? <GrowBox i={1} aria-label="Legal name" value={editForm.legal_name} placeholder="As registered, if different" onEnter={handleSave} onChange={v => { setLegalTouched(true); setEditForm(f => ({ ...f, legal_name: v })) }} />
                        : <>{vendor.legal_name}{isVerified && gstOn && <VerifiedMark what="Legal name, from the GST portal" />}</>}
                    </div>
                  </div>
                )}
                {!gstOn && (
                  <div className="vc__kv">
                    {fixLabel(<BadgeCheck size={14} />, 'Tax ID')}
                    <div className="vc__kv-v">
                      <span className="vc__mono">{vendor.tax_docs?.tax_id ?? '—'}</span>
                      {vendor.country_code && <span className="vc__muted vc__xs">{vendor.country_code}</span>}
                    </div>
                  </div>
                )}
                {gstOn && <div className="vc__kv" data-fix="gstin">
                  {fixLabel(<BadgeCheck size={14} />, 'GSTIN')}
                  <div className="vc__kv-v">
                    <span className="vc__mono">{vendor.gstn ?? '—'}</span>
                    {vendor.gstn && isVerified && <VerifiedMark what="GSTIN, checked with the GST portal" />}
                    {vendor.gstn && !editing && <button className="vc__copy" aria-label="Copy GSTIN" onClick={() => navigator.clipboard?.writeText(vendor.gstn!).then(() => showAlert('GSTIN copied', 'info'))}><Copy size={13} /></button>}
                    <button className="vc__lnk vc__lnk--brand vc__xs" onClick={() => goCompliance('gstins')}>All GSTINs</button>
                  </div>
                </div>}
                {panOn && <div className="vc__kv" data-fix="pan">
                  {fixLabel(<BadgeCheck size={14} />, 'PAN')}
                  <div className="vc__kv-v">
                    {editing && !panFromGstin
                      ? <input className="vc__kv-in vc__mono" style={{ '--i': 2 } as CSSProperties} aria-label="PAN" data-guide="details.pan-input" placeholder="e.g. AAKCA9812J" value={editForm.pan} onChange={e => setEditForm(f => ({ ...f, pan: e.target.value.toUpperCase() }))} />
                      : <>
                          <span className="vc__mono" data-guide="details.pan">{pan ?? '—'}</span>
                          {panFromGstin && isVerified && <VerifiedMark what="PAN, part of the verified GSTIN" />}
                          {pan && !editing && <button className="vc__copy" aria-label="Copy PAN" onClick={() => navigator.clipboard?.writeText(pan).then(() => showAlert('PAN copied', 'info'))}><Copy size={13} /></button>}
                        </>}
                  </div>
                </div>}
                <div className="vc__kv">{fixLabel(<Mail size={14} />, 'Email')}
                  <div className="vc__kv-v">
                    {editing
                      ? <GrowBox i={3} aria-label="Email" inputMode="email" placeholder="billing@vendor.com" value={editForm.email} onEnter={handleSave} onChange={v => setEditForm(f => ({ ...f, email: v.trim() }))} />
                      : vendor.email ?? <span className="vc__muted">—</span>}
                  </div>
                </div>
                <div className="vc__kv" data-fix="payment_terms">
                  {fixLabel(<CalendarDays size={14} />, 'Payment terms')}
                  <div className="vc__kv-v">
                    {editing
                      ? <span className="vc__kv-days"><input className="vc__kv-in vc__kv-in--num" style={{ '--i': 4 } as CSSProperties} type="number" min={0} aria-label="Payment terms (days)" aria-invalid={!editForm.payment_terms_days.trim() && !!saveErr} value={editForm.payment_terms_days} onChange={e => setEditForm(f => ({ ...f, payment_terms_days: e.target.value }))} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleSave() } }} />days</span>
                      : `${vendor.payment_terms_days} days`}
                  </div>
                </div>
                {tdsOn && <div className="vc__kv">{fixLabel(<Receipt size={14} />, 'TDS category')}
                  <div className="vc__kv-v">{vendor.vendor_category_name ?? (editing ? <span className="vc__muted">—</span> : <button className="vc__lnk vc__lnk--brand" onClick={() => goCompliance('tds')}>Set category</button>)}</div>
                </div>}
                <div className="vc__kv">{fixLabel(<CalendarDays size={14} />, 'Last bill')}<div className="vc__kv-v">{fmtDate(vendor.last_invoice_date)}</div></div>
                {editing && saveErr && <div className="vc__err" role="alert">{saveErr}</div>}
              </div>
            </SideSection>

            <SideSection id="people" title="People" count={contacts.length} actions={
                <button className="vc__iconbtn vc__iconbtn--sm" aria-label="Add contact" title="Add contact" onClick={() => openAddContact()}><Plus size={14} /></button>
              }>
              {contactsLoading ? <div className="vc__muted vc__small">Loading…</div>
                : contactsStatus === 'error' ? (
                  <div className="vc__loaderr vc__loaderr--side" role="alert"><AlertCircle size={14} /><span className="vc__grow">Couldn't load contacts.</span><button type="button" className="vc__btn vc__btn--sm" onClick={loadContacts}>Retry</button></div>
                ) : contacts.map(c => (
                  <div key={c.id} className="vc__person">
                    <Avatar name={c.name} size="lg" />
                    <div className="vc__person-main">
                      <div className="vc__person-n">{c.name}{c.is_primary && <span className="vc__badge">Primary</span>}</div>
                      <div className="vc__person-r">{[c.role, c.email].filter(Boolean).join(' · ')}</div>
                    </div>
                    <div className="vc__person-acts">
                      {c.email && <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Email ${c.name}`} title="Email" onClick={() => composeNew(c.id)}><Mail size={14} /></button>}
                      {!c.is_primary && <button className="vc__iconbtn vc__iconbtn--sm" aria-label={`Make ${c.name} primary`} title="Make primary" onClick={() => handleSetPrimaryContact(c.id)}><Star size={14} /></button>}
                      <button className="vc__iconbtn vc__iconbtn--sm vc__iconbtn--danger" aria-label={`Delete ${c.name}`} title="Delete" onClick={() => handleDeleteContact(c.id)}><Trash2 size={14} /></button>
                    </div>
                  </div>
                ))}
              {newSenders.slice(0, 2).map(a => (
                <div key={a} className="vc__newsender">
                  <Avatar name={a} />
                  <div className="vc__grow vc__min0"><div className="vc__strong vc__ellipsis">{a}</div><div className="vc__muted">Emails you · not a contact yet</div></div>
                  <button className="vc__btn vc__btn--sm" onClick={() => openAddContact(a)}>Add</button>
                </div>
              ))}
            </SideSection>

            {bankOn && <SideSection id="bank" title="Bank account">
              {bankLoading ? <div className="vc__muted vc__small">Loading…</div>
                : bankStatus === 'error' ? (
                  <div className="vc__loaderr vc__loaderr--side" role="alert"><AlertCircle size={14} /><span className="vc__grow">Couldn't load the bank account.</span><button type="button" className="vc__btn vc__btn--sm" onClick={() => loadBank()}>Retry</button></div>
                ) : primaryBank ? (
                  // Not a div-button: the card's own link is a real button laid
                  // over it, so Show/Hide is a sibling, not a button inside a button.
                  <div className="vc__bankcard">
                    <button type="button" className="vc__bankcard-open" aria-label="Open bank accounts" onClick={() => goCompliance('bank')} />
                    <div className="vc__bankcard-top"><BankMark ifsc={primaryBank.ifsc} name={primaryBank.bank_name} /><span className="vc__grow" />
                      {/* Amber, not a near-twin of the verified pill: this is read right before paying. */}
                      {primaryBank.verified
                        ? <span className="vc__vchip"><Check size={11} />Verified</span>
                        : <span className="vc__vchip vc__vchip--warn"><AlertTriangle size={11} />Not verified</span>}
                    </div>
                    <div className="vc__bankcard-row">
                      {/* The number flips over to show itself in full. */}
                      <div className={`vc__bankflip${showAcct ? ' is-shown' : ''}`} aria-live="polite">
                        <div className="vc__bankcard-no vc__bankflip-face" aria-hidden={showAcct}>•••• •••• {primaryBank.account_number.slice(-4)}</div>
                        <div className="vc__bankcard-no vc__bankflip-face vc__bankflip-back" aria-hidden={!showAcct}>
                          {primaryBank.account_number.replace(/\s+/g, '').replace(/(.{4})(?=.)/g, '$1 ')}
                        </div>
                      </div>
                      <button className="vc__bankshow" aria-label={showAcct ? 'Hide account number' : 'Show account number'} aria-pressed={showAcct}
                        onClick={e => { e.stopPropagation(); setShowAcct(v => !v) }}>
                        {showAcct ? <EyeOff size={12} /> : <Eye size={12} />}{showAcct ? 'Hide' : 'Show'}
                      </button>
                    </div>
                    <div className="vc__bankcard-ft">{primaryBank.ifsc ?? ''}<span className="vc__grow" />{bankAccounts.length > 1 ? `+${bankAccounts.length - 1} more` : ''}</div>
                  </div>
                ) : (
                  <div className="vc__bankempty"><Landmark size={16} /><span className="vc__grow">No bank account on record.</span>{canEdit && <button className="vc__btn vc__btn--sm" onClick={() => { goCompliance('bank'); setShowAddBankForm(true) }}>Add</button>}</div>
                )}
            </SideSection>}
    </>
  )

  return (
    <>
      {/* A PDF that's only being looked at opens in the shared full-screen viewer;
          a contract keeps the side-by-side view with its extracted values. */}
      {previewDoc && /\.pdf$/i.test(previewDoc.file_name) && !previewDoc.doc_id && (
        <PdfFullscreen url={previewDoc.url} name={previewDoc.file_name} onClose={() => setPreviewDoc(null)} />
      )}
      {previewDoc && !(/\.pdf$/i.test(previewDoc.file_name) && !previewDoc.doc_id) && (
        <DocPreviewModal
          url={previewDoc.url}
          name={previewDoc.file_name}
          onClose={() => setPreviewDoc(null)}
          docId={previewDoc.doc_id}
          supplierId={vendor.supplier_id}
          companyId={companyId}
          currentUser={user?.name ?? ''}
        />
      )}
      <ConfirmDialog
        open={!!confirm}
        title={confirm?.title ?? ''}
        description={confirm?.description ?? ''}
        confirmLabel={confirm?.confirmLabel}
        tone={confirm?.tone}
        impact={confirm?.impact}
        requireTyped={confirm?.requireTyped}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => { await confirm?.run(); setConfirm(null) }}
      >
        {confirm?.children}
      </ConfirmDialog>
      {showBillPicker && (
        <AttachBillPicker
          bills={statements}
          loading={stmtLoading}
          attaching={attachingBills}
          onPreview={previewBill}
          onAttach={handleAttachBills}
          onClose={() => setShowBillPicker(false)}
        />
      )}
      <div className="vd__overlay vd__overlay--card" onClick={e => { if (e.target === e.currentTarget) requestClose() }}>
      <div className="vc__card" role="dialog" aria-modal="true" aria-label={vendor.name} ref={cardRef} tabIndex={-1}>
        <div className="vc__top">
          <div className="vc__crumb"><Building2 size={15} /><span>Vendors</span><ChevronRight size={14} /><b className="vc__ellipsis">{vendor.name}</b></div>
          <div className="vc__top-r">
            <button className="vc__iconbtn" title="Copy link" aria-label="Copy link" onClick={copyLink}><Link2 size={15} /></button>
            <button className="vc__iconbtn" onClick={requestClose} aria-label="Close"><X size={16} /></button>
          </div>
        </div>

        <div className="vc__split">
          <main className="vc__main" ref={mainRef}>
            <div className="vc__main-in">
              <div className="vc__atmos" aria-hidden="true" />

              <div className="vc__hero">
                <VendorLogo supplierId={vendor.supplier_id} className="vc__logo" fallback={nameInitials(vendor.name)}>
                  <span className={`vc__logo-badge${isVerified ? '' : ' vc__logo-badge--warn'}`} title={isVerified ? verifiedLabel : unverifiedLabel}>
                    {isVerified ? <Check size={12} strokeWidth={3} /> : <ShieldAlert size={12} />}
                  </span>
                  <LogoEditor supplierId={vendor.supplier_id} notify={showAlert} />
                </VendorLogo>
                <div className="vc__hero-id">
                  <div className="vc__hero-name">{vendor.name}</div>
                  <div className="vc__hero-meta">
                    {vendor.is_active !== false
                      ? <span className="vc__tagc vc__tagc--ok"><span className="vc__pulse" />Active</span>
                      : <span className="vc__tagc">Inactive</span>}
                    {isVerified
                      ? <span className="vc__tagc vc__tagc--verified"><ShieldCheck size={13} />{verifiedLabel}</span>
                      : <span className="vc__tagc vc__tagc--warn"><ShieldAlert size={13} />{unverifiedLabel}</span>}
                    {/* When the vendor was added here — not when the relationship began. */}
                    {since && <span className="vc__tagc" title="When this vendor was added to Onsumm"><CalendarDays size={13} />Added {since.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' })}</span>}
                    {summary.bills.length > 0 && <button type="button" className="vc__tagc vc__tagc--btn" title="Open all bills" onClick={() => goBills('all')}><Receipt size={13} />{summary.bills.length} bills</button>}
                  </div>
                </div>
                <div className="vc__hero-acts">
                  <button className="vc__btn" onClick={() => composeNew()}><Mail size={15} />Email</button>
                  <button className="vc__btn" onClick={() => { goView('documents'); setUploadReq(1) }}><Upload size={15} />Upload</button>
                  {payFn && <button className="vc__btn vc__btn--primary" onClick={payFn}><Wallet size={15} />Pay vendor</button>}
                </div>
              </div>

              {stmtLoading
                ? <div className="vc__kpis vc__kpis--loading" aria-busy="true" />
                : <KpiStrip s={summary} now={now} inbox={inbox} failed={failed}
                    onGo={(v, f) => { if (v === 'documents') goBills((f as BillFilter) ?? 'all'); else goView(v) }} />}

              {stackSide && <section className="vc__side vc__side--inline" aria-label="Vendor details">{sidebar}</section>}


              <div className="vc__tabs" ref={tabsRef} role="tablist" aria-label="Vendor views">
                {VIEWS.map(t => (
                  <button key={t.v} ref={el => { tabBtns.current[t.v] = el }} role="tab" aria-selected={view === t.v}
                          id={`vc-tab-${t.v}`} aria-controls="vc-tabpanel" tabIndex={view === t.v ? 0 : -1}
                          onKeyDown={e => onTabKey(e, t.v)}
                          className={`vc__tab${view === t.v ? ' on' : ''}`} onClick={() => goView(t.v)}>
                    {t.icon}{t.label}
                    {t.hot ? <span className="vc__cnt vc__cnt--hot" title={`${t.hot} need a reply`}>{t.hot}</span>
                      : t.count != null && t.count > 0 ? <span className="vc__cnt">{t.count}</span> : null}
                  </button>
                ))}
                {ink && <span className="vc__tabs-ink" style={{ left: ink.left, width: ink.width }} />}
              </div>

              <div key={view} className="vc__fadein" role="tabpanel" id="vc-tabpanel" aria-labelledby={`vc-tab-${view}`}>
                {view === 'overview' && (
                  <>
                    <div className="vc__sec vc__ovtop">
                      <SpendCard s={summary} status={billsStatus} onRetry={retryBills} onOpenSpend={() => goView('spend')} onSelectMonth={ym => { setSelMonth(ym); goView('spend') }} />
                      <PaymentSchedule s={summary} onPay={payFn} failed={failed} onRetry={retryBills} />
                    </div>
                    <div className="vc__sec">
                      <SectionHead title="Open bills" count={summary.open.length}>
                        {/* The upload page reads the vendor off the bill itself; it takes no vendor to pre-fill. */}
                        <button className="vc__lnk" title="Upload a bill — the vendor is read from the bill" onClick={() => navigate(PATHS.upload)}><Plus size={14} />Bill</button>
                        <button className="vc__lnk" onClick={() => goBills('all')}>All bills <ChevronRight size={14} /></button>
                      </SectionHead>
                      <OpenBills s={summary} now={now} failed={failed} onRetry={retryBills} onOpenBill={onNavigateToBill} onViewAll={() => goBills('outstanding')} onViewPaid={() => goBills('paid')} />
                    </div>
                    <div className="vc__sec vc__grid2">
                      <div>
                        <SectionHead title="Conversations">
                          <button className="vc__lnk" onClick={() => composeNew()}><Plus size={14} />Email</button>
                          <button className="vc__lnk" onClick={() => goView('conversations')}>All <ChevronRight size={14} /></button>
                        </SectionHead>
                        <ThreadsCard inbox={inbox} now={now} onOpen={openThread} onAll={() => goView('conversations')} onConnect={handleConnectEmail} />
                      </div>
                      <div>
                        <SectionHead title="Contracts & IOs">
                          <button className="vc__lnk" onClick={() => { setDocFolder('contracts'); goView('documents'); setUploadReq(1) }}><Plus size={14} />Contract</button>
                          <button className="vc__lnk" onClick={() => { setDocFolder('contracts'); goView('documents') }}>All <ChevronRight size={14} /></button>
                        </SectionHead>
                        {docsStatus === 'error' && !vendorDocs
                          ? <div className="vc__loaderr vc__loaderr--card" role="alert"><AlertCircle size={14} /><span className="vc__grow">Couldn't load contracts.</span><button type="button" className="vc__btn vc__btn--sm" onClick={loadDocs}><RefreshCw size={13} />Retry</button></div>
                          : <ContractsCard docs={vendorDocs} onOpen={previewVendorDoc} onUpload={() => { setDocFolder('contracts'); goView('documents'); setUploadReq(1) }} />}
                      </div>
                    </div>
                  </>
                )}

                {view === 'spend' && (
                  <SpendView s={summary} now={now} paymentTerms={vendor.payment_terms_days || null} status={billsStatus} onRetry={retryBills}
                             selected={selMonth} onSelect={setSelMonth} onOpenBill={onNavigateToBill} />
                )}

                {view === 'documents' && (
                  <VendorDocuments
                    vendorName={vendor.name} supplierId={vendor.supplier_id} brandId={activeBrand?.id ?? null}
                    bills={summary.bills} docs={vendorDocs} now={now}
                    billsStatus={billsStatus} docsStatus={docsStatus} onRetry={retryDocsAndBills}
                    folder={docFolder} onFolder={setDocFolder} billFilter={billFilter} onBillFilter={setBillFilter}
                    currentUser={user?.name ?? ''} onReload={loadDocs}
                    onPreviewDoc={previewVendorDoc} onDownloadDoc={downloadVendorDoc} onDeleteDoc={handleDocDelete}
                    onPreviewBill={previewBill} onOpenBill={onNavigateToBill}
                    onUploadBill={() => navigate(PATHS.upload)} notify={showAlert} uploadRequest={uploadReq} onUploadRequestDone={() => setUploadReq(0)}
                  />
                )}

                {view === 'conversations' && (
                  <VendorInbox
                    supplierId={vendor.supplier_id} inbox={inbox} loading={inboxLoading}
                    emailConn={emailConn} connecting={connectingEmail} contacts={contacts}
                    activeThreadId={activeThreadId} onActiveThread={setActiveThreadId}
                    onReload={loadInbox} onConnect={handleConnectEmail} onAddContact={openAddContact}
                    attachFiles={attachFiles} setAttachFiles={setAttachFiles} onPickBills={() => setShowBillPicker(true)}
                    notify={showAlert} composeTo={composeTo}
                    bills={summary.bills} billFiles={billFiles} onOpenBill={onNavigateToBill} onDocsChanged={loadDocs}
                    brandId={activeBrand?.id ?? null} signer={user?.name ?? undefined}
                  />
                )}

                {/* Mounted only while open: it fetches on first view and starts over for a new vendor. */}
                {view === 'activity' && (
                  <VendorActivity key={vendor.supplier_id} supplierId={vendor.supplier_id} onOpenBill={onNavigateToBill} now={now} />
                )}

                {view === 'compliance' && (
                  <div className="vc__cgrid">
                    {/* India-only tiles follow the tenant's rule packs; abroad the
                        vendor's foreign tax documents take their place. */}
                    {!gstOn && !tdsOn && (
                      <section id="vc-sec-taxdocs" className="vc__csec vc__ctile">
                        <div className="vc__ctile-h">
                          <span className="vc__ctile-ic vc__ctile-ic--brand"><IdCard size={18} /></span>
                          <div className="vc__ctile-t">Tax documents</div>
                          <span className="vc__grow" />
                          {vendor.tax_residency && <span className="vc__cpill">Tax resident: {vendor.tax_residency}</span>}
                        </div>
                        {vendor.tax_docs && Object.keys(vendor.tax_docs).length > 0 ? (
                          <div className="vc__kvs">
                            {Object.entries(vendor.tax_docs).map(([k, v]) => (
                              <div key={k} className="vc__kv">
                                <div className="vc__kv-k">{k === 'tax_id' ? 'Tax ID' : k}</div>
                                <div className="vc__kv-v vc__mono">{String(v)}</div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="vc__muted vc__small">No tax documents on file. The vendor can add them from their wallet invite link.</div>
                        )}
                      </section>
                    )}
                    {gstOn && <section id="vc-sec-gstins" className="vc__csec vc__ctile">
            <>
              <div className="vc__ctile-h">
                <span className="vc__ctile-ic vc__ctile-ic--brand"><IdCard size={18} /></span>
                <div className="vc__ctile-t">GST registrations</div>
                <span className="vc__grow" />
                {!gstinsLoading && gstins.length > 0 && (gstins.every(g => g.status === 'VERIFIED')
                  ? <span className="vc__cpill vc__cpill--ok"><Check size={12} />All verified</span>
                  : <span className="vc__cpill vc__cpill--warn"><i />{gstins.filter(g => g.status !== 'VERIFIED').length} to verify</span>)}
                {canEdit && (
                  <button className="vc__btn vc__btn--sm" data-guide="gstins.add"
                    onClick={() => { setShowAddGstin(v => !v); setNewGstin(''); setGstinErr('') }}>
                    {showAddGstin ? 'Cancel' : <><Plus size={13} />Add</>}
                  </button>
                )}
              </div>

              {/* Add GSTIN form */}
              {showAddGstin && (
                <div className="vc__gstadd">
                  <div className="vc__gstadd-t">Add and verify a GSTIN</div>
                  <div className="vc__gstadd-d">Its PAN must match this vendor’s PAN ({pan ?? 'not set'}). It’s checked live with the GST portal.</div>
                  <div className="vc__gstadd-row">
                    <input
                      className="vc__gstadd-in vc__mono"
                      placeholder="e.g. 27AABCT1332L1ZU"
                      aria-label="New GSTIN"
                      aria-invalid={!!gstinErr}
                      value={newGstin}
                      maxLength={15}
                      autoFocus
                      onChange={e => { setNewGstin(e.target.value.toUpperCase()); setGstinErr('') }}
                      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddGstin() } }}
                    />
                    <button className="vc__btn vc__btn--sm vc__btn--primary" onClick={handleAddGstin} disabled={gstinAdding || !newGstin.trim()}>
                      {gstinAdding ? 'Verifying…' : 'Add & verify'}
                    </button>
                  </div>
                  {gstinErr && <div className="vc__gst-err" role="alert"><AlertCircle size={12} />{gstinErr}</div>}
                </div>
              )}

              {/* GSTIN list */}
              {gstinsLoading ? (
                <div className="vc__gst-empty">Loading…</div>
              ) : gstinsErr ? (
                <div className="vc__loaderr vc__loaderr--card" role="alert"><AlertCircle size={14} /><span className="vc__grow">Couldn't load GSTINs.</span><button type="button" className="vc__btn vc__btn--sm" onClick={loadGstins}><RefreshCw size={13} />Retry</button></div>
              ) : gstins.length === 0 ? (
                <div className="vc__gst-empty">No GSTINs on record</div>
              ) : (
                <div className="vc__gstlist">
                  {gstins.map(g => {
                    const isOk = g.status === 'VERIFIED'
                    const isFailed = g.status === 'FAILED'
                    // Reverify only when the status needs attention; a verified GSTIN
                    // is re-checked by the pipeline whenever its details change.
                    const needsVerify = !isOk || !g.verified_at
                    const isRunning = reverifying === (g.id ?? 'primary')
                    return (
                      <div key={g.gstin} className={`vc__gst${isOk ? ' is-ok' : isFailed ? ' is-bad' : ''}`}>
                        <div className="vc__gst-h">
                          <span className="vc__mono vc__strong">{g.gstin}</span>
                          {g.is_primary && <span className="vc__badge">Primary</span>}
                          <span className={`vc__chip vc__chip--xs vc__chip--${isOk ? 'ok' : isFailed ? 'bad' : 'warn'}`}>{statusWords(g.status)}</span>
                          <span className="vc__grow" />
                          {needsVerify ? (
                            <button className="vc__btn vc__btn--sm" disabled={isRunning} data-guide="gstins.verify" onClick={() => handleReverify(g)}>
                              <RefreshCw size={12} />{isRunning ? 'Checking…' : 'Reverify'}
                            </button>
                          ) : (
                            <span className="vc__gst-auto" title="Re-verification runs automatically when GSTIN details change"><Check size={11} />Auto</span>
                          )}
                          {g.id && !g.is_primary && canEdit && (
                            <button className="vc__iconbtn vc__iconbtn--sm vc__iconbtn--danger" onClick={() => handleDeleteGstin(g)}
                              aria-label={`Delete GSTIN ${g.gstin ?? ''}`} title="Delete GSTIN"><Trash2 size={13} aria-hidden="true" /></button>
                          )}
                        </div>
                        {(g.legal_name || g.trade_name || g.pan) && (
                          <div className="vc__gst-d">
                            {g.legal_name && <span><b>Legal</b> {g.legal_name}</span>}
                            {g.trade_name && g.trade_name !== g.legal_name && <span><b>Trade</b> {g.trade_name}</span>}
                            {g.pan && <span><b>PAN</b> <span className="vc__mono">{g.pan}</span></span>}
                          </div>
                        )}
                        {/* Always shown for a failed check, not just on hover. */}
                        {isFailed && (
                          <div className="vc__gst-err"><AlertCircle size={12} /><span>{g.error_message || 'Verification failed. Reverify to check its current status on the GST portal.'}</span></div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </>
                    </section>}
                    {bankOn && <section id="vc-sec-bank" className="vc__csec vc__ctile">
            <>
              <div className="vc__ctile-h">
                <span className="vc__ctile-ic vc__ctile-ic--ok"><Landmark size={18} /></span>
                <div className="vc__ctile-t">Bank accounts</div>
                <span className="vc__grow" />
                {/* Each account carries its own Verified badge; the header only flags what's pending. */}
                {!bankLoading && bankAccounts.some(a => !a.verified) && (
                  <span className="vc__cpill vc__cpill--warn"><i />{bankAccounts.filter(a => !a.verified).length} pending</span>)}
                {/* Where payments go: same gate as GSTINs — admins and finance controllers only. */}
                {canEdit && bankStatus !== 'error' && (
                  <button className="vc__btn vc__btn--sm" data-guide="bank.add" onClick={() => { setShowAddBankForm(v => !v); setBankFormError('') }}>
                    {showAddBankForm ? 'Cancel' : <><Plus size={13} />Add</>}
                  </button>
                )}
              </div>

              {bankLoading ? (
                <div className="vd__bank-skeleton">
                  <div className="vd__skel-card"><div className="vd__skel-line vd__skel-line--w60" /><div className="vd__skel-line vd__skel-line--w40" /></div>
                </div>
              ) : bankStatus === 'error' ? (
                <div className="vc__loaderr vc__loaderr--card" role="alert"><AlertCircle size={14} /><span className="vc__grow">Couldn't load bank accounts.</span><button type="button" className="vc__btn vc__btn--sm" onClick={() => loadBank()}><RefreshCw size={13} />Retry</button></div>
              ) : (
                <div className="vd__bank-list">
                  {bankAccounts.length === 0 && (
                    <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--ink-500)', fontSize: 13 }}>No bank account on record</div>
                  )}
                  {bankAccounts.map(acct => (
                    <BankVerifyStrip key={acct.id} acct={acct} supplierId={vendor.supplier_id} companyId={companyId} onRefresh={() => loadBank(true)}>{({ action, notice }) => (
                    <div className="vd__bank-item">
                      <div className="vd__bank-item-header">
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <BankMark ifsc={acct.ifsc} name={acct.bank_name} variant="line" />
                          <span style={{ fontSize: 13, fontWeight: 600, color: '#111827' }}>
                            {acctName(acct)}
                          </span>
                          {acct.is_primary && (
                            <span className="vd__badge vd__badge--blue">Primary</span>
                          )}
                          <span className={`vd__badge ${acct.verified ? 'vd__badge--green' : 'vd__badge--amber'}`}>
                            {acct.verified ? 'Verified' : 'Not verified'}
                          </span>
                        </div>
                        <div style={{ display: 'flex', gap: 6 }}>
                          {action}
                          {!acct.is_primary && canEdit && (
                            <button className="vd__ghost-btn" onClick={() => handleSetPrimary(acct.id)}>Set as primary</button>
                          )}
                          {canEdit && (bankAccounts.length > 1 || !acct.is_primary) && acct.id !== 'legacy' && (
                            <button
                              className="vd__ghost-btn vd__ghost-btn--danger"
                              onClick={() => handleDeleteBankAccount(acct.id)}
                              aria-label={`Delete bank account ending ${acct.account_number.slice(-4)}`}
                              title="Delete bank account"><Trash2 size={13} aria-hidden="true" /></button>
                          )}
                        </div>
                      </div>
                      <div className="vd__detail-grid" style={{ marginTop: 12 }}>
                        <div className="vd__detail-field">
                          <div className="vd__field-lbl">Account Number</div>
                          {/* Masked like the header and the sidebar card, until asked. */}
                          <div className="vd__field-val vd__mono vc__acctno">
                            <span>{shownAccts.has(acct.id) ? acct.account_number : `•••• •••• ${acct.account_number.replace(/\s+/g, '').slice(-4)}`}</span>
                            <button type="button" className="vc__acctshow" aria-pressed={shownAccts.has(acct.id)}
                              aria-label={shownAccts.has(acct.id) ? 'Hide account number' : 'Show account number'} onClick={() => toggleAcct(acct.id)}>
                              {shownAccts.has(acct.id) ? <EyeOff size={12} /> : <Eye size={12} />}{shownAccts.has(acct.id) ? 'Hide' : 'Show'}
                            </button>
                          </div>
                        </div>
                        <div className="vd__detail-field">
                          <div className="vd__field-lbl">IFSC Code</div>
                          <div className="vd__field-val vd__mono">{acct.ifsc ?? '—'}</div>
                        </div>
                        {acct.name_at_bank && (
                          <div className="vd__detail-field">
                            <div className="vd__field-lbl">Name at Bank</div>
                            <div className="vd__field-val">{acct.name_at_bank}</div>
                          </div>
                        )}
                      </div>
                      {notice}
                    </div>
                    )}</BankVerifyStrip>
                  ))}
                </div>
              )}

              {showAddBankForm && canEdit && (
                <div className="vd__add-bank-card">
                  <div className="vd__add-bank-header">
                    <Building2 size={14} color="#2563eb" />
                    <span>New Bank Account</span>
                  </div>

                  <div className="vd__add-bank-grid">
                    <div className="vd__add-bank-field vd__add-bank-field--full">
                      <label className="vd__add-bank-lbl">Account Number <span style={{ color: '#ef4444' }}>*</span></label>
                      <input
                        className="vd__add-bank-input vd__mono"
                        value={bankForm.account_number}
                        onChange={e => setBankForm(f => ({ ...f, account_number: e.target.value }))}
                        placeholder="e.g. 1234567890"
                        autoFocus
                      />
                    </div>
                    <div className="vd__add-bank-field">
                      <label className="vd__add-bank-lbl">IFSC Code</label>
                      <input
                        className="vd__add-bank-input vd__mono"
                        value={bankForm.ifsc}
                        onChange={e => setBankForm(f => ({ ...f, ifsc: e.target.value.toUpperCase() }))}
                        placeholder="e.g. HDFC0001234"
                      />
                    </div>
                    <div className="vd__add-bank-field">
                      <label className="vd__add-bank-lbl">Bank Name <span style={{ color: 'var(--ink-500)', fontWeight: 400 }}>(optional)</span></label>
                      <input
                        className="vd__add-bank-input"
                        value={bankForm.bank_name}
                        onChange={e => setBankForm(f => ({ ...f, bank_name: e.target.value }))}
                        placeholder="e.g. HDFC Bank"
                      />
                    </div>
                  </div>

                  {/* A real switch: reachable by Tab, toggled by Space or Enter. */}
                  <div className="vd__add-bank-primary-toggle">
                    <button
                      type="button" role="switch" aria-checked={bankForm.is_primary} aria-labelledby="vc-bank-primary-lbl"
                      className={`vd__toggle vc__toggle-btn ${bankForm.is_primary ? 'vd__toggle--on' : ''}`}
                      onClick={() => setBankForm(f => ({ ...f, is_primary: !f.is_primary }))}
                    >
                      <span className="vd__toggle-thumb" />
                    </button>
                    <span id="vc-bank-primary-lbl" onClick={() => setBankForm(f => ({ ...f, is_primary: !f.is_primary }))}>Set as primary account</span>
                  </div>

                  {bankFormError && (
                    <div className="vd__add-bank-error" role="alert">
                      <AlertCircle size={12} /> {bankFormError}
                    </div>
                  )}

                  <div className="vd__add-bank-actions">
                    <button
                      className="vd__add-bank-save"
                      disabled={bankFormSaving}
                      onClick={handleAddBankAccount}
                    >
                      {bankFormSaving
                        ? <><RefreshCw size={12} style={{ animation: 'vp-spin 1s linear infinite' }} /> Saving…</>
                        : <><CheckCircle2 size={12} /> Save Account</>}
                    </button>
                    <button
                      className="vd__add-bank-cancel"
                      onClick={() => { setShowAddBankForm(false); setBankFormError(''); setBankForm({ account_number: '', ifsc: '', bank_name: '', is_primary: false }) }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </>
                    </section>}
                    {tdsOn && <section id="vc-sec-tds" className="vc__csec vc__ctile">
              <div className="vc__ctile-h">
                <span className="vc__ctile-ic vc__ctile-ic--teal"><Receipt size={18} /></span>
                <div className="vc__ctile-t">TDS</div>
                <span className="vc__grow" />
                {(() => {
                  const c = tdsCategories.find(x => x.category_id === vendor.vendor_category_id)
                  return c && c.tds_rate != null
                    ? <span className="vc__cpill vc__cpill--info">{c.section_label || c.section_code ? `${c.section_label || c.section_code} · ` : ''}{c.tds_rate}%</span>
                    : <span className="vc__cpill">Not set</span>
                })()}
              </div>
            <VendorTdsPanel vendor={vendor} companyId={companyId} tdsCategories={tdsCategories} canEdit={canEdit}
              onCategoryChange={async (catId) => {
                // Rejects on failure so the panel rolls its dropdown back.
                await updateVendor(vendor.supplier_id, { vendor_category_id: catId || null }, companyId)
                const cat = tdsCategories.find(c => c.category_id === catId)
                setVendor(v => ({ ...v, vendor_category_id: catId || null, vendor_category_name: cat?.name ?? null }))
                onVendorUpdated?.({ ...vendor, vendor_category_id: catId || null, vendor_category_name: cat?.name ?? null })
                invalidateVendorsCache(companyId)
              }}
            />
                    </section>}
                    {cryptoOn && (
                      <section id="vc-sec-crypto" className="vc__csec vc__ctile">
                        <VendorCryptoWallets
                          supplierId={vendor.supplier_id}
                          canEdit={canEdit}
                          onWalletsChanged={() => {
                            // Approving or freezing a wallet changes whether the vendor is
                            // verified: re-read it so the badge follows.
                            invalidateVendorsCache(companyId)
                            fetchVendor(vendor.supplier_id)
                              .then(v => { setVendor(v); onVendorUpdated?.(v) })
                              .catch(() => {})
                          }}
                        />
                      </section>
                    )}
                  </div>
                )}
              </div>
            </div>
          </main>

          {!stackSide && <aside className="vc__side" aria-label="Vendor details">{sidebar}</aside>}
        </div>

        {/* Errors interrupt (role=alert) and look like errors; info just reports. */}
        {alert && (
          <div className={`vc__toast vc__toast--${alert.type}`} role={alert.type === 'error' ? 'alert' : 'status'}>
            {alert.type === 'error' ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}<span>{alert.msg}</span>
            {alert.action && <button className="vc__toast-act" onClick={alert.action.run}>{alert.action.label}</button>}
            <button onClick={() => setAlert(null)} aria-label="Dismiss">×</button>
          </div>
        )}
      </div>
      </div>

      {showAddContactForm && (
        <div className="vc__mov" onMouseDown={e => { if (e.target === e.currentTarget && !contactFormSaving) setShowAddContactForm(false) }}>
          <div className="vc__modal" role="dialog" aria-modal="true" aria-label="Add contact" ref={contactFormRef}>
            <div className="vc__modal-h">
              <div className="vc__icotile"><UserPlus size={16} /></div>
              <div className="vc__grow"><div className="vc__modal-t">Add a contact</div><div className="vc__muted vc__xs">Someone the team or the email agent can reach at {vendor.name}</div></div>
              <button className="vc__iconbtn" aria-label="Close" onClick={() => setShowAddContactForm(false)}><X size={16} /></button>
            </div>
            <div className="vc__modal-b">
              <label className="vc__field"><span className="vc__lbl">Name *</span><input autoFocus value={contactForm.name} placeholder="e.g. Priya Sharma" onChange={e => setContactForm(f => ({ ...f, name: e.target.value }))} /></label>
              <label className="vc__field"><span className="vc__lbl">Role *</span><input value={contactForm.role} placeholder="e.g. Accounts receivable" onChange={e => setContactForm(f => ({ ...f, role: e.target.value }))} /></label>
              <label className="vc__field"><span className="vc__lbl">Email *</span><input type="email" value={contactForm.email} placeholder="e.g. priya@vendor.com" onChange={e => setContactForm(f => ({ ...f, email: e.target.value }))} /></label>
              <label className="vc__field"><span className="vc__lbl">Phone</span><input value={contactForm.phone} placeholder="e.g. +91 98765 43210" onChange={e => setContactForm(f => ({ ...f, phone: e.target.value }))} /></label>
              <label className="vc__row8 vc__mt12 vc__small"><input type="checkbox" checked={contactForm.is_primary} onChange={e => setContactForm(f => ({ ...f, is_primary: e.target.checked }))} />Make this the primary contact</label>
              {contactFormError && <div className="vc__err">{contactFormError}</div>}
            </div>
            <div className="vc__modal-f">
              <span className="vc__grow" />
              <button className="vc__btn" disabled={contactFormSaving} onClick={() => { setShowAddContactForm(false); setContactFormError(''); setContactForm(emptyContactForm) }}>Cancel</button>
              <button className="vc__btn vc__btn--primary" disabled={contactFormSaving} onClick={handleAddContact} data-guide="contacts.save">{contactFormSaving ? 'Saving…' : 'Save contact'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function VendorsPage({ onNavigateToBill }: { onNavigateToBill?: (invoiceId: string) => void } = {}) {
  const { user, activeBrand, brands } = useAuth()
  const [vendors, setVendors]         = useState<Vendor[]>([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState('')
  const [search, setSearch]           = useState('')
  const [showAdd, setShowAdd]         = useState(false)
  // The open vendor lives in the URL, not in component state: /vendors/<id>/<tab>
  // is linkable, survives a refresh, and Back closes the panel.
  const navigate = useNavigate()
  // Vendor payments are a licensed module; without it the panel drops its Pay
  // button rather than offering a link the route will refuse.
  const payEnabled = useService('disbursement')
  const jur = useJurisdiction()
  setBooksCurrency(jur.base_currency, jur.locale)  // list totals are in the books currency
  const { supplierId: routeSupplierId, tab: routeTab } = useParams<{ supplierId?: string; tab?: string }>()
  const openTab: VendorTab = VENDOR_TABS.includes(routeTab as VendorTab) ? (routeTab as VendorTab) : 'details'
  const selectedVendor = routeSupplierId
    ? vendors.find(v => v.supplier_id === routeSupplierId) ?? null
    : null
  // Opening a vendor is a history entry (Back closes it); switching its tabs
  // replaces that entry, so Back still closes the card in one step.
  const openVendor = (v: Vendor, tab?: VendorTab, replace = false) => navigate(vendorPath(v.supplier_id, tab), { replace })
  const closeVendor = () => navigate(PATHS.vendors)
  const [migrateVendor, setMigrateVendor] = useState<Vendor | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showBulkMigrate, setShowBulkMigrate] = useState(false)
  const isMultiBrand = brands.length > 1 && user?.role === 'admin'

  /**
   * Every column, as a filter. This page had one search box over name/GSTN/email,
   * so "who is on 60-day terms with nothing outstanding" or "which vendors have no
   * TDS category set" could not be asked at all.
   *
   * Built in the component because Brand needs the brand list to turn an id into
   * the name the table prints.
   */
  const filterFields = useMemo<FilterField<Vendor>[]>(() => [
    { key: 'name', label: 'Vendor', kind: 'text', value: v => v.name },
    { key: 'gstn', label: 'GSTN', kind: 'text', value: v => v.gstn },
    { key: 'pan', label: 'PAN', kind: 'text', value: v => v.pan },
    { key: 'email', label: 'Email', kind: 'text', value: v => v.email },
    {
      key: 'status', label: 'Status', kind: 'enum',
      value: v => v.status,
      format: s => s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(),
    },
    { key: 'source', label: 'Source', kind: 'enum', value: v => v.source },
    { key: 'total_bills', label: 'Bills', kind: 'number', value: v => v.total_bills },
    { key: 'total_billed', label: 'Total Billed', kind: 'number', value: v => v.total_billed },
    { key: 'outstanding_amount', label: 'Outstanding', kind: 'number', value: v => v.outstanding_amount },
    { key: 'last_invoice_date', label: 'Last Invoice', kind: 'date', value: v => v.last_invoice_date },
    { key: 'payment_terms_days', label: 'Payment Terms (days)', kind: 'number', value: v => v.payment_terms_days },
    { key: 'vendor_category_name', label: 'TDS Category', kind: 'enum', value: v => v.vendor_category_name },
    {
      key: 'tds_ready', label: 'TDS Status', kind: 'enum',
      // The column is a badge derived from the category, not a stored field.
      value: v => (v.vendor_category_name ? 'READY' : 'NOT_SET'),
      format: s => (s === 'READY' ? 'TDS Ready' : 'Not Set'),
    },
    {
      key: 'bank_verified', label: 'Bank', kind: 'enum',
      value: v => (v.bank_verified ? 'VERIFIED' : 'UNVERIFIED'),
      format: s => (s === 'VERIFIED' ? 'Verified' : 'Unverified'),
    },
    {
      key: 'brand_id', label: 'Brand', kind: 'enum',
      // A sentinel rather than a blank: no brand means shared across all of them,
      // which is a real answer and should not read as missing data.
      value: v => v.brand_id ?? '__shared__',
      format: id => (id === '__shared__' ? 'Shared (all brands)' : brands.find(b => b.id === id)?.name ?? 'Unknown brand'),
    },
  ], [brands])

  const colFilters = useColumnFilters(filterFields)

  // Deduplicate-later flow (green button on vendors that have a deferred PAN duplicate)
  const [dedupFor, setDedupFor]       = useState<Vendor | null>(null)
  const [dedupTarget, setDedupTarget] = useState<PanDuplicate | null>(null)
  const [dedupBusy, setDedupBusy]     = useState(false)
  const [dedupErr, setDedupErr]       = useState('')

  const openDedup = async (v: Vendor) => {
    setDedupBusy(true); setDedupErr(''); setDedupTarget(null); setDedupFor(v)
    try {
      const res = await fetchVendorPanDuplicates(v.supplier_id, user?.company_id)
      if (res.duplicates.length > 0) {
        setDedupTarget(res.duplicates[0])
      } else {
        // Duplicate is already gone — clear the flag and close.
        await deferVendorDedup(v.supplier_id, false)
        invalidateVendorsCache(user?.company_id)
        setDedupFor(null)
        load(true)
      }
    } catch (e: unknown) {
      setDedupErr(e instanceof Error ? e.message : 'Failed to check duplicates')
    } finally { setDedupBusy(false) }
  }

  const confirmDedup = async () => {
    if (!dedupFor || !dedupTarget) return
    setDedupBusy(true); setDedupErr('')
    try {
      await mergeVendors(dedupFor.supplier_id, dedupTarget.supplier_id, { actor_name: user?.name, actor_role: user?.role })
      invalidateVendorsCache(user?.company_id)
      setDedupFor(null); setDedupTarget(null)
      load(true)
    } catch (e: unknown) {
      setDedupErr(e instanceof Error ? e.message : 'Merge failed')
    } finally { setDedupBusy(false) }
  }

  const toggleSelect = (id: string) => setSelectedIds(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const toggleAll = () => setSelectedIds(prev =>
    prev.size === filtered.length ? new Set() : new Set(filtered.map(v => v.supplier_id))
  )
  const clearSelection = () => setSelectedIds(new Set())

  const load = async (forceRefresh = false) => {
    setLoading(true)
    setError('')
    try {
      setVendors(await fetchVendors(forceRefresh, user?.company_id, fresh => setVendors(fresh), activeBrand?.id))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load vendors')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [activeBrand?.id])

  // Search first, then the column filters — so a value list shows what is left
  // after the search box, and the counts match the table.
  const searched = vendors.filter(v =>
    !search || v.name.toLowerCase().includes(search.toLowerCase()) ||
    (v.gstn ?? '').toLowerCase().includes(search.toLowerCase()) ||
    (v.email ?? '').toLowerCase().includes(search.toLowerCase())
  )
  const filtered = colFilters.apply(searched)
  // Paged at the foot of the table; back to page 1 when the search or a
  // filter changes what is listed.
  const vendorsPage = usePagedRows(filtered, 'vendors', [search, colFilters.count, filtered.length])
  // Phones scroll instead: 50 rows at a time, more as the end comes into view.
  const isPhone = useMediaQuery(PHONE_QUERY)
  const vendorsFeed = useInfiniteRows(filtered, [search, colFilters.count, filtered.length])

  return (
    <>
      {showAdd && (
        <AddVendorModal
          companyId={user?.company_id}
          onClose={() => setShowAdd(false)}
          onDone={() => load(true)}
        />
      )}
      {/* A link to a vendor that isn't in the list: say so, rather than show nothing. */}
      {routeSupplierId && !selectedVendor && (
        <div className="vd__overlay vd__overlay--card" onClick={e => { if (e.target === e.currentTarget) closeVendor() }}>
          <div className="vc__card vc__card--state" role="dialog" aria-modal="true" aria-label={loading ? 'Loading vendor' : 'Vendor not found'}>
            {loading ? (
              <div className="vc__state" aria-busy="true"><RefreshCw size={18} className="vp__spin" /><span>Loading vendor…</span></div>
            ) : (
              <div className="vc__state" role="alert">
                <AlertCircle size={20} />
                <b>{error ? "Couldn't load vendors" : 'Vendor not found'}</b>
                <span className="vc__muted">{error || 'It may have been merged, removed, or belong to another brand.'}</span>
                <div className="vc__row8">
                  {error && <button className="vc__btn vc__btn--sm" onClick={() => load(true)}><RefreshCw size={13} />Retry</button>}
                  <button className="vc__btn vc__btn--sm vc__btn--primary" autoFocus onClick={closeVendor}>Back to vendors</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
      {selectedVendor && (
        <VendorDetailPanel
          key={selectedVendor.supplier_id}
          vendor={selectedVendor}
          onClose={closeVendor}
          companyId={user?.company_id}
          onNavigateToBill={onNavigateToBill}
          // The panel's tab is part of the address, so "look at this vendor's
          // contacts" is a link rather than a set of directions.
          tab={openTab}
          onTabChange={t => openVendor(selectedVendor, t, true)}
          onVendorUpdated={updated =>
            // selectedVendor is derived from this list, so refreshing the list is
            // all that is needed — there is no second copy to keep in sync.
            setVendors(prev => prev.map(v => v.supplier_id === updated.supplier_id ? updated : v))
          }
          // The vendor panel has to close, not just sit behind the payment page:
          // both are full-screen overlays, so leaving it mounted left the vendor
          // card (and its own Pay Vendor button) covering the payment flow.
          // A route, not an overlay: the payment flow replaces this panel
          // instead of being stacked behind it.
          onPayVendor={payEnabled ? (supplierId => navigate(payPath(supplierId))) : undefined}
        />
      )}
      {migrateVendor && (
        <MigrateBrandModal vendor={migrateVendor} companyId={user?.company_id} onClose={() => setMigrateVendor(null)} onDone={() => { load(true); clearSelection() }} />
      )}
      {showBulkMigrate && (
        <MigrateBrandModal supplierIds={Array.from(selectedIds)} companyId={user?.company_id} onClose={() => setShowBulkMigrate(false)} onDone={() => { load(true); clearSelection() }} />
      )}
      {dedupFor && (
        <div className="vp__dedup-backdrop" onClick={e => { if (e.target === e.currentTarget && !dedupBusy) { setDedupFor(null); setDedupTarget(null) } }}>
          <div className="vp__dedup-dialog">
            <div className="vp__dedup-title">Deduplicate vendor</div>
            {dedupBusy && !dedupTarget ? (
              <div className="vp__dedup-body">Checking for duplicates…</div>
            ) : dedupTarget ? (
              <>
                <div className="vp__dedup-body">
                  <b>{dedupFor.name}</b> will be merged into the vendor below (same PAN <b>{dedupTarget.pan}</b>).
                  All its bills and history move over, and <b>{dedupFor.name}</b> is deactivated.
                  <div className="vp__dedup-target">
                    <div className="vp__dedup-target-name">{dedupTarget.name || 'Unnamed vendor'}</div>
                    <div className="vp__dedup-target-meta">
                      <span style={{ fontFamily: 'monospace' }}>{dedupTarget.gstn || '—'}</span>
                      <span>· {dedupTarget.bill_count} bill{dedupTarget.bill_count === 1 ? '' : 's'}</span>
                      {dedupTarget.status && <span>· {dedupTarget.status}</span>}
                    </div>
                  </div>
                </div>
                {dedupErr && <div className="vp__dedup-err">{dedupErr}</div>}
                <div className="vp__dedup-actions">
                  <button className="vp__dedup-cancel" disabled={dedupBusy} onClick={() => { setDedupFor(null); setDedupTarget(null) }}>Cancel</button>
                  <button className="vp__dedup-confirm" disabled={dedupBusy} onClick={confirmDedup}>
                    {dedupBusy ? 'Merging…' : 'Merge vendors'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="vp__dedup-body">{dedupErr || 'No duplicate vendor found.'}</div>
                <div className="vp__dedup-actions">
                  <button className="vp__dedup-cancel" onClick={() => { setDedupFor(null); setDedupTarget(null) }}>Close</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      <div className="vp">
        {/* Header */}
        <div className="vp__header">
          <div>
            <h1 className="vp__title">Vendors</h1>
            <p className="vp__sub">
              {loading ? 'Loading…' : `${vendors.length} vendor${vendors.length !== 1 ? 's' : ''}`}
            </p>
          </div>
          <div className="vp__toolbar" style={{ display: 'flex', gap: 10 }}>
            <input
              className="vp__search"
              placeholder="Search by name, GSTN or email…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <button className="vp__refresh" onClick={() => load(true)} disabled={loading}>
              <RefreshCw size={15} className={loading ? 'vp__spin' : ''} />
              Refresh
            </button>
            <button className="vp__add" onClick={() => setShowAdd(true)}>
              <Plus size={15} /> Add Vendor
            </button>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="vp__error">
            <AlertCircle size={16} />{error}
          </div>
        )}

        {/* The sheet's status bar, only while something is filtering. */}
        {colFilters.count > 0 && (
          <div className="vp__filterbar">
            <FilterStatus state={colFilters} total={searched.length} shown={filtered.length} unit="vendors" />
          </div>
        )}

        {/* Table */}
        <div className={`vp__wrap${vendorsPage.total > 0 && !isPhone ? ' vp__wrap--paged' : ''}`}>
          <table className="vp__table">
            <thead>
              <tr>
                {isMultiBrand && (
                  <th style={{ width: 36, padding: '11px 8px 11px 14px' }}>
                    <input type="checkbox"
                      checked={filtered.length > 0 && selectedIds.size === filtered.length}
                      ref={el => { if (el) el.indeterminate = selectedIds.size > 0 && selectedIds.size < filtered.length }}
                      onChange={toggleAll}
                      style={{ cursor: 'pointer', accentColor: '#6366f1' }}
                    />
                  </th>
                )}
                {/* Header = control: click it for the AutoFilter dropdown. */}
                <FilterTh state={colFilters} fieldKey="name" rows={searched}>Vendor</FilterTh>
                <FilterTh state={colFilters} fieldKey="gstn" rows={searched}>GSTN</FilterTh>
                <FilterTh state={colFilters} fieldKey="email" rows={searched}>Email</FilterTh>
                <FilterTh state={colFilters} fieldKey="status" rows={searched}>Status</FilterTh>
                <FilterTh state={colFilters} fieldKey="total_bills" rows={searched} align="right">Bills</FilterTh>
                <FilterTh state={colFilters} fieldKey="total_billed" rows={searched} align="right">Total Billed</FilterTh>
                <FilterTh state={colFilters} fieldKey="outstanding_amount" rows={searched} align="right">Outstanding</FilterTh>
                <FilterTh state={colFilters} fieldKey="last_invoice_date" rows={searched}>Last Invoice</FilterTh>
                <FilterTh state={colFilters} fieldKey="payment_terms_days" rows={searched}>Payment Terms</FilterTh>
                <FilterTh state={colFilters} fieldKey="vendor_category_name" rows={searched}>TDS Category</FilterTh>
                <FilterTh state={colFilters} fieldKey="tds_ready" rows={searched}>TDS Status</FilterTh>
                {brands.length > 1 && user?.role === 'admin' && (
                  <FilterTh state={colFilters} fieldKey="brand_id" rows={searched}>Brand</FilterTh>
                )}
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={isMultiBrand ? 13 : 11} style={{ textAlign: 'center', padding: '40px 0', color: 'var(--ink-500)' }}>Loading vendors…</td></tr>
              )}
              {!loading && filtered.length === 0 && !error && (
                <tr><td colSpan={isMultiBrand ? 13 : 11} style={{ textAlign: 'center', padding: '48px 0', color: 'var(--ink-500)' }}>
                  {colFilters.count > 0 ? (
                    <>
                      No vendors match these filters.{' '}
                      <button
                        onClick={colFilters.clearAll}
                        style={{ background: 'none', border: 'none', padding: 0, color: '#4f46e5', fontWeight: 600, fontSize: 13, cursor: 'pointer', textDecoration: 'underline' }}
                      >
                        Clear {colFilters.count} filter{colFilters.count > 1 ? 's' : ''}
                      </button>
                    </>
                  ) : search ? 'No vendors match your search.' : 'No vendors yet. Upload and ingest invoices to see vendors here.'}
                </td></tr>
              )}
              {(isPhone ? vendorsFeed.rows : vendorsPage.pageRows).map(v => {
                const st = STATUS_STYLE[v.status?.toUpperCase()] ?? { bg: '#f3f4f6', color: '#6b7280' }
                const isChecked = selectedIds.has(v.supplier_id)
                return (
                  <tr key={v.supplier_id} className="vp__row" onClick={() => openVendor(v)} style={{ cursor: 'pointer', background: isChecked ? '#f5f3ff' : undefined }}>
                    {isMultiBrand && (
                      <td className="vp__td-check" style={{ padding: '12px 8px 12px 14px' }} onClick={e => { e.stopPropagation(); toggleSelect(v.supplier_id) }}>
                        <input type="checkbox" checked={isChecked} onChange={() => toggleSelect(v.supplier_id)}
                          style={{ cursor: 'pointer', accentColor: '#6366f1' }} onClick={e => e.stopPropagation()} />
                      </td>
                    )}
                    <td className="vp__td-name">
                      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                        <VendorLogo supplierId={v.supplier_id} className="vp__avatar" fallback={v.name.charAt(0).toUpperCase()} />
                        <div>
                          <div className="vp__name">{v.name}</div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                            <div className="vp__source">{v.source}</div>
                            {(v.has_pan_duplicate || v.pan_dup_deferred) && (
                              <button
                                className="vp__dedup-btn"
                                title="A vendor with the same PAN exists — click to deduplicate"
                                onClick={e => { e.stopPropagation(); openDedup(v) }}
                              >
                                <ArrowRightLeft size={11} strokeWidth={2.5} /> Deduplicate
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td>
                      {v.gstn
                        ? <span className="vp__mono">{v.gstn}</span>
                        : <span style={{ color: '#d1d5db' }}>—</span>}
                    </td>
                    <td>
                      {v.email
                        ? <span style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#374151' }}>
                            <Mail size={12} color="#5b6473" />{v.email}
                          </span>
                        : <span style={{ color: '#d1d5db' }}>—</span>}
                    </td>
                    <td className="vp__td-status">
                      <span className="vp__status-badge" style={{ background: st.bg, color: st.color }}>
                        {v.status}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <span className="vp__metric">
                        <FileText size={12} color="#5b6473" />{v.total_bills}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <span className="vp__money vp__money--strong">{fmtCurrency(v.total_billed)}</span>
                    </td>
                    <td className="vp__td-out" style={{ textAlign: 'right' }}>
                      <span className={`vp__money ${v.outstanding_amount > 0 ? 'vp__money--danger' : 'vp__money--muted'}`}>
                        {fmtCurrency(v.outstanding_amount)}
                      </span>
                    </td>
                    <td className="vp__meta">{fmtDate(v.last_invoice_date)}</td>
                    <td className="vp__meta">{v.payment_terms_days} days</td>
                    <td style={{ fontSize: 12 }}>
                      {v.vendor_category_name
                        ? <span className="vp__category-name">{v.vendor_category_name}</span>
                        : <span style={{ color: '#d1d5db' }}>—</span>}
                    </td>
                    <td>
                      {v.vendor_category_name
                        ? <span className="vp__tds-badge vp__tds-badge--ready">TDS Ready</span>
                        : <span className="vp__tds-badge vp__tds-badge--warn">Not Set</span>}
                    </td>
                    {brands.length > 1 && user?.role === 'admin' && (
                      <td onClick={e => e.stopPropagation()}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span style={{ fontSize: 12, color: '#6b7280' }}>
                            {v.brand_id ? (brands.find(b => b.id === v.brand_id)?.name ?? 'Unknown') : <span style={{ color: 'var(--ink-500)', fontStyle: 'italic' }}>Shared</span>}
                          </span>
                          <button
                            onClick={() => setMigrateVendor(v)}
                            title="Migrate to another brand"
                            style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 6, border: '1px solid #e5e7eb', background: '#f9fafb', color: '#6366f1', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
                          >
                            <ArrowRightLeft size={11} /> Migrate
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
          {isPhone && (
            <LoadMore
              shown={vendorsFeed.shown}
              total={vendorsFeed.total}
              hasMore={vendorsFeed.hasMore}
              onLoadMore={vendorsFeed.loadMore}
              unit="vendors"
            />
          )}
        </div>
        {vendorsPage.total > 0 && !isPhone && (
          <Pager
            label="Pages of vendors"
            compact
            page={vendorsPage.page}
            perPage={vendorsPage.perPage}
            total={vendorsPage.total}
            onGo={n => { vendorsPage.setPage(n); document.querySelector('.vp__wrap')?.scrollTo({ top: 0 }) }}
            onPerPage={vendorsPage.setPerPage}
          />
        )}
      </div>

      {/* Bulk action bar */}
      {isMultiBrand && selectedIds.size > 0 && (
        <div style={{
          position: 'fixed', bottom: 28, left: '50%', transform: 'translateX(-50%)',
          background: '#1e1b4b', borderRadius: 12, padding: '12px 20px',
          display: 'flex', alignItems: 'center', gap: 16,
          boxShadow: '0 8px 32px rgba(0,0,0,0.32)', zIndex: 800,
          animation: 'vp-bar-in 0.2s cubic-bezier(0.34,1.06,0.64,1)',
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: '#c7d2fe' }}>
            {selectedIds.size} vendor{selectedIds.size !== 1 ? 's' : ''} selected
          </span>
          <button
            onClick={() => setShowBulkMigrate(true)}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 14px', borderRadius: 8, border: 'none', background: '#6366f1', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
          >
            <ArrowRightLeft size={13} /> Migrate Brand
          </button>
          <button
            onClick={clearSelection}
            style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '7px 10px', borderRadius: 8, border: '1px solid #4338ca', background: 'transparent', color: '#a5b4fc', fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}
          >
            <X size={13} /> Clear
          </button>
        </div>
      )}

    </>
  )
}
