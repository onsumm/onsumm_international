/**
 * The search and answer-engine vocabulary, in one place.
 *
 * Two things read this file. `useSeo` builds the per-route `keywords` meta and
 * the JSON-LD `featureList` from the clusters below; the landing copy is
 * written against `PHRASING`, which fixes *how* each concept is spelled on the
 * page so "AP automation", "A/P automation" and "ap-automation" don't all
 * appear across four files and split the term.
 *
 * Adding a keyword here does not put it on a page. Terms earn ranking from
 * visible copy and structured data, so a new term belongs in a sentence a
 * customer would actually read — the clusters are the checklist for that, not
 * a substitute.
 */

/** Keyword clusters, grouped by the buying question each one answers. */
export const KEYWORD_CLUSTERS = {
  apFinance: [
    'AP automation',
    'accounts payable automation',
    'accounts payable software',
    'AP software',
    'invoice automation',
    'invoice processing',
    'finance automation',
    'finance operations automation',
  ],
  india: [
    'AP automation India',
    'Indian AP automation',
    'AP software India',
    'AP automation for Indian SMEs',
    'invoice automation India',
    'accounting automation India',
    'GST accounting automation',
  ],
  tally: [
    'Tally automation',
    'Tally Prime automation',
    'TallyPrime automation',
    'Tally invoice automation',
    'Tally AP automation',
    'Tally integration',
    'Tally Prime integration',
    'Tally invoice posting',
    'automated Tally posting',
    'direct Tally posting',
    'Tally connector',
    'Tally bridge',
  ],
  zoho: [
    'Zoho Books automation',
    'Zoho Books integration',
    'Zoho Books AP automation',
    'invoice automation Zoho Books',
    'Tally Zoho Books integration',
    'Tally Prime + Zoho Books',
  ],
  ocr: [
    'invoice OCR',
    'AI invoice OCR',
    'OCR invoice processing',
    'invoice data extraction',
    'AI invoice processing',
    'automated invoice extraction',
    'invoice scanning',
    'invoice digitization',
  ],
  approval: [
    'invoice approval workflow',
    'AP approval workflow',
    'invoice approval automation',
    'multi-level approval',
    'approval matrix',
    'vendor bill approval',
    'purchase invoice approval',
  ],
  tds: [
    'TDS automation',
    'TDS deduction automation',
    'TDS on vendor bills',
    'TDS tracking',
    'automated TDS calculation',
    'TDS compliance',
    'vendor TDS',
    'TDS invoice automation',
  ],
  gst: [
    'GST verification',
    'GSTIN verification',
    'GSTIN validation',
    'GST invoice validation',
    'GST compliance',
    'GST automation',
    'GST reconciliation',
    'ITC reconciliation',
    'input tax credit validation',
  ],
  bankVendor: [
    'bank account verification',
    'vendor bank verification',
    'vendor bank account verification',
    'vendor verification',
    'supplier verification',
    'vendor master verification',
    'bank account validation',
    'vendor bank change detection',
  ],
  duplicateFraud: [
    'duplicate invoice detection',
    'duplicate invoice check',
    'duplicate payment detection',
    'duplicate vendor payment',
    'duplicate bill detection',
    'invoice fraud detection',
    'payment fraud detection',
    'anomaly detection',
    'duplicate payment prevention',
  ],
} as const

export type KeywordCluster = keyof typeof KEYWORD_CLUSTERS

/** Every term, de-duplicated, in cluster order. */
export const ALL_KEYWORDS: readonly string[] = Array.from(
  new Set(Object.values(KEYWORD_CLUSTERS).flat()),
)

export function keywordsFor(...clusters: KeywordCluster[]): string[] {
  return Array.from(new Set(clusters.flatMap(c => KEYWORD_CLUSTERS[c])))
}

/**
 * The spelling each concept gets in visible copy.
 *
 * One phrasing per concept, so the term consolidates instead of splitting
 * across near-variants. Headline case is the caller's business — these are the
 * word forms, not the capitalisation of a sentence that starts with one.
 */
export const PHRASING = {
  apAutomation: 'AP automation',
  accountsPayableAutomation: 'accounts payable automation',
  invoiceAutomation: 'invoice automation',
  invoiceProcessing: 'invoice processing',
  invoiceOcr: 'invoice OCR',
  india: 'India',
  indianSmes: 'Indian SMEs',
  indianBusinesses: 'Indian businesses',
  sme: 'SME',
  tallyPrime: 'Tally Prime',
  tally: 'Tally',
  zohoBooks: 'Zoho Books',
  approvalWorkflow: 'approval workflow',
  tds: 'TDS',
  tdsTracking: 'TDS tracking',
  gst: 'GST',
  gstin: 'GSTIN',
  vendor: 'vendor',
  vendorBankVerification: 'vendor bank verification',
  bankAccount: 'bank account',
  bankVerification: 'bank verification',
  duplicatePayments: 'duplicate payments',
  duplicateInvoice: 'duplicate invoice',
  software: 'software',
  payments: 'payments',
} as const
