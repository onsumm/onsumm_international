import { useEffect, useState } from 'react'
import { clickOrigin } from '../../utils/clickOrigin'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import {
  fetchBillDetail, fetchPriorityLabels, invalidateDetailCache,
  type Bill, type BillDetail, type PriorityLabels,
} from '../../api/invoices'
import { BillModal } from '../../pages/BillsPage'
import { payPath } from '../../routes/paths'
import RecordCardStatus from './RecordCardStatus'
import CorrectionPins from './CorrectionPins'

/**
 * The list row BillModal expects, built from the bill's detail.
 *
 * BillModal was written for the Bills table and takes a row as well as the
 * detail it fetches itself. Opened by id there is no row, so fill one from the
 * detail: it only reads what both carry, plus soft_status. Fields only the
 * table uses (uploader, payment audit, FX note) are left empty.
 */
function billFromDetail(d: BillDetail): Bill {
  return {
    invoice_id: d.invoice_id,
    id: d.id,
    supplier_id: d.supplier_id,
    invoice_date: d.invoice_date,
    due_date: d.due_date,
    status: d.status,
    allowed_roles: d.allowed_roles,
    allowed_usernames: d.allowed_usernames,
    status_label: d.status_label,
    total_amount: d.total_amount,
    currency_code: d.currency_code,
    vendor_name: d.vendor?.name ?? null,
    vendor_gstn: d.vendor?.gstn ?? null,
    original_file: d.original_file,
    upload_id: d.upload_id,
    stg_bill_id: d.stg_bill_id,
    created_at: d.created_at,
    uploaded_by_name: null,
    uploaded_by_role: null,
    category: d.category,
    department: d.department,
    payment_status: null,
    amount_paid: d.amount_paid,
    outstanding_amount: d.outstanding_amount,
    payment_updated_by: null,
    payment_updated_at: null,
    tally_push_pending: false,
    manual_priority: d.manual_priority,
    soft_status: d.soft_status ?? 'active',
    subtotal: d.subtotal,
    tax_amount: d.tax_amount,
    tds_status: d.tds_status,
    tds_section_code: d.tds_section_code,
    tds_rate: d.tds_rate,
    tds_amount: d.tds_amount,
    tds_base_amount: d.tds_base_amount,
    net_payable: d.net_payable,
    original_amount: d.original_amount,
    inr_amount_current: d.inr_amount_current,
    brand_id: null,
    cost_center_id: null,
    cost_center_name: null,
    fx_agent_note: null,
    fx_agent_severity: null,
  }
}

/** The bill card (document, fields, approval, payment) for one bill id. */
export default function BillCard({ invoiceId, onClose, correctionsFor }: {
  invoiceId: string
  onClose: () => void
  /** A Review finding whose suggested corrections to show on this bill. */
  correctionsFor?: string
}) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [detail, setDetail] = useState<BillDetail | null>(null)
  const [error, setError] = useState('')
  const [labels, setLabels] = useState<PriorityLabels | undefined>(undefined)
  const [showFixes, setShowFixes] = useState(!!correctionsFor)
  const [refreshKey, setRefreshKey] = useState(0)
  // Taken once, at open: the card grows out of whatever was clicked to open it.
  const [origin] = useState(() => clickOrigin() ?? '0px 0px')

  const load = () =>
    fetchBillDetail(invoiceId)
      .then(d => { setDetail(d); setError('') })
      .catch(e => setError(e?.response?.status === 404 ? 'This bill no longer exists.' : 'Could not load this bill.'))

  useEffect(() => {
    setDetail(null); setError('')
    load()
  }, [invoiceId])

  useEffect(() => {
    if (user?.company_id) fetchPriorityLabels().then(setLabels).catch(() => {})
  }, [user?.company_id])

  if (!detail) return <RecordCardStatus label="bill" error={error} onClose={onClose} />

  return (
    <>
    <BillModal
      bill={billFromDetail(detail)}
      origin={origin}
      cachedDetail={detail}
      labels={labels}
      onClose={onClose}
      // Hold, delete and restore change the row, not just the detail the modal
      // keeps for itself — refetch so the card reflects them.
      onRefresh={() => { invalidateDetailCache(invoiceId); load() }}
      onPay={bill => bill.supplier_id && navigate(payPath(bill.supplier_id, bill.invoice_id))}
      refreshKey={refreshKey}
    />
    {/* Suggested corrections, pinned to the fields they would change. */}
    {correctionsFor && showFixes && (
      <CorrectionPins
        findingId={correctionsFor}
        invoiceId={invoiceId}
        onSaved={applied => {
          if (applied) { invalidateDetailCache(invoiceId); setRefreshKey(k => k + 1); void load() }
          setShowFixes(false)
        }}
        onSkip={() => setShowFixes(false)}
      />
    )}
    </>
  )
}
