import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { useService } from '../../auth/EntitlementsContext'
import { fetchVendor, type Vendor } from '../../api/invoices'
import { VendorDetailPanel } from '../../pages/VendorsPage'
import { payPath, type VendorTab } from '../../routes/paths'
import RecordCardStatus from './RecordCardStatus'
import { setFindingStatus } from '../../api/review'
import type { CardPrefill } from './recordLinks'
import ClaimPin from './ClaimPin'
import { REVIEW_CHANGED_EVENT } from './reviewEvents'

/** The vendor card (details, contacts, bank, GSTINs, TDS, documents) for one vendor id. */
export default function VendorCard({ supplierId, tab, onTabChange, onClose, onOpenBill, prefill, claimFor }: {
  supplierId: string
  tab: VendorTab
  onTabChange: (tab: VendorTab) => void
  onClose: () => void
  onOpenBill: (invoiceId: string) => void
  /** A form to open already filled in (a contact found by Review). */
  prefill?: CardPrefill
  /** A contract's claimed value to show in its field (a Review finding id). */
  claimFor?: string
}) {
  const [showClaim, setShowClaim] = useState(!!claimFor)
  const [refreshKey, setRefreshKey] = useState(0)
  const { user, activeBrand } = useAuth()
  const navigate = useNavigate()
  const payEnabled = useService('disbursement')
  const [vendor, setVendor] = useState<Vendor | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setVendor(null); setError('')
    fetchVendor(supplierId, activeBrand?.id)
      .then(setVendor)
      .catch(e => setError(e?.response?.status === 404 ? 'This vendor no longer exists.' : 'Could not load this vendor.'))
  }, [supplierId, activeBrand?.id])

  if (!vendor) return <RecordCardStatus label="vendor" error={error} onClose={onClose} />

  return (
    <>
    <VendorDetailPanel
      key={vendor.supplier_id}
      vendor={vendor}
      companyId={user?.company_id}
      tab={tab}
      onTabChange={onTabChange}
      onClose={onClose}
      onNavigateToBill={onOpenBill}
      onVendorUpdated={setVendor}
      onPayVendor={payEnabled ? (id => navigate(payPath(id))) : undefined}
      prefillContact={prefill?.contact}
      refreshKey={refreshKey}
      onPrefillSaved={prefill ? () => {
        // The contact the finding asked for now exists: close it, and let an
        // open Review queue drop it.
        setFindingStatus(prefill.findingId, { status: 'RESOLVED' })
          .catch(() => {})
          .finally(() => window.dispatchEvent(new CustomEvent(REVIEW_CHANGED_EVENT)))
      } : undefined}
    />
    {claimFor && showClaim && (
      <ClaimPin
        findingId={claimFor}
        onApplied={() => setRefreshKey(k => k + 1)}
        onDone={() => setShowClaim(false)}
      />
    )}
    </>
  )
}
