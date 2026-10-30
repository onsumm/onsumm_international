/**
 * Vendor findings answered by changing one field of the vendor card, shown as
 * the in-field box (ClaimPin). Shared by Review (which findings open this way)
 * and ClaimPin (how to read and act on each).
 */
export interface ClaimRow { label: string; left: string; right: string }

export interface ClaimConfig {
  /** The vendor-card field (`data-fix`). */
  field: string
  /** What the field says now, and what it would become, from the evidence row. */
  current: (r: ClaimRow) => string
  proposed: (r: ClaimRow) => string
  /** The finding's action that makes the change. */
  useAction: string
  /** The finding's action for "keep it as it is"; none means just close it. */
  keepAction?: string
  /** Agent-read, unconfirmed: accepting asks first. */
  suspected: boolean
  /** One line under the box. */
  note: (r: ClaimRow) => string
}

const fromContract = (field: string): ClaimConfig => ({
  field,
  current: r => r.left,
  proposed: r => r.right,
  useAction: 'keep_new',
  keepAction: 'keep_current',
  suspected: true,
  note: () => 'Suspected — read from a vendor contract, not checked against your records.',
})

export const CLAIM_CONFIG: Record<string, ClaimConfig> = {
  vendor_pan_conflict: fromContract('pan'),
  vendor_gstin_conflict: fromContract('gstin'),
  payment_term_conflict: fromContract('payment_terms'),
  // The GST portal's name replaced the brand name: put the brand name back
  // and keep the portal's as the legal name. Verified, so no extra caution.
  vendor_name_replaced_by_gst_portal: {
    field: 'name',
    current: r => r.right,   // the name now is the portal's
    proposed: r => r.left,   // the name the business used
    useAction: 'keep_both_names',
    suspected: false,
    note: r => `${r.right} stays as the legal name (as on the GST portal).`,
  },
}
