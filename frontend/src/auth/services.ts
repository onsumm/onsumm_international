/**
 * The service vocabulary — a mirror of backend_ocr/app/services.py.
 *
 * Sits in `auth/` beside permissions.ts rather than in `admin/`, because the
 * tenant app needs it too and tenant code must never import from `admin/`.
 *
 * Hiding an unlicensed module in the UI is a courtesy, not the control: the
 * server 403s the underlying endpoints regardless of what the client believes.
 * That is why this file carries no logic beyond labels — nothing here is a
 * security boundary.
 *
 * Keep in step with services.py; services.test.ts checks this list is internally
 * consistent, and the backend is the authority on what actually resolves.
 */

export type ServiceKey =
  // core
  | 'bills' | 'vendors' | 'upload_ocr' | 'approvals' | 'reports'
  | 'ledger' | 'brands' | 'users' | 'settings' | 'review_hub'
  // compliance
  | 'tds' | 'fx' | 'gst_tax_verification' | 'kyc_verification'
  // payments
  | 'disbursement' | 'nova_billing' | 'demo_payments'
  | 'crypto_payouts' | 'crypto_tds' | 'audit_anchor'
  // ai
  | 'agent_hub' | 'fact_graph' | 'anomaly_ai' | 'sap_matching'
  // integrations
  | 'data_extraction' | 'tally_connector' | 'accounting_api_sync' | 'email_sending'

export const SERVICE_LABELS: Record<ServiceKey, string> = {
  bills: 'Bills & Invoices',
  vendors: 'Vendor Master',
  upload_ocr: 'Upload & OCR Extraction',
  approvals: 'Approval Workflow',
  reports: 'Reports & Dashboards',
  ledger: 'Ledger, COA & Categories',
  brands: 'Brands',
  users: 'Users, Roles & Team',
  settings: 'Tenant Settings',
  tds: 'TDS Engine',
  fx: 'FX / Multi-Currency',
  gst_tax_verification: 'GST Tax-Type Verification',
  kyc_verification: 'Vendor KYB Verification',
  disbursement: 'Vendor Payments',
  nova_billing: 'Nova Billing Reconciliation',
  demo_payments: 'Demo Payments',
  crypto_payouts: 'Crypto Payouts (USDC)',
  crypto_tds: 'Crypto Withholding',
  audit_anchor: 'On-chain Audit Anchor',
  review_hub: 'Review Hub',
  agent_hub: 'Agent Hub',
  fact_graph: 'Fact Graph',
  anomaly_ai: 'Anomaly & Duplicate Detection',
  sap_matching: 'SAP Item-Code Matching',
  data_extraction: 'Bulk Data Extraction',
  tally_connector: 'Tally Prime Connector',
  accounting_api_sync: 'SAP / ERP Connector',
  email_sending: 'Outbound Email',
}

export const SERVICE_DESCRIPTIONS: Record<ServiceKey, string> = {
  bills: 'The bill list, bill detail, approvals, attachments and edits.',
  vendors: 'Vendor records, contacts, bank accounts, GSTINs and documents.',
  upload_ocr: 'Invoice upload and OCR extraction into a reviewable draft bill.',
  approvals: 'Department approval chains, email approve links and escalations.',
  reports: 'In-app dashboards and expense analytics.',
  ledger: 'Chart of accounts rules, categories, cost centres and charges.',
  brands: 'Multiple billing entities under one tenant, with logos.',
  users: 'Tenant user accounts and role assignment.',
  settings: 'Company profile, approval chain configuration and preferences.',
  tds: 'Automatic TDS section mapping, deduction and FY threshold tracking.',
  fx: 'Foreign-currency bills, live rates, exposure reporting and alerts.',
  gst_tax_verification: 'Flags CGST/SGST vs IGST mismatches against the place of supply.',
  kyc_verification: 'GSTIN and bank-account verification of vendors.',
  disbursement: 'Initiating vendor payments over IMPS, with OTP authorisation.',
  nova_billing: 'Reconciles vendor API usage billing against received invoices.',
  demo_payments: 'Pay is simulated: no bank call, the bill settles as paid, and receipts go only to one demo POC email.',
  crypto_payouts: 'Pays approved bills in USDC from the client\'s own Safe, with on-chain reconciliation.',
  crypto_tds: 'Works out, holds and records tax withheld from USDC payouts.',
  audit_anchor: 'Hash-chains the audit log and anchors a daily Merkle root on a public chain.',
  review_hub: 'One queue for everything that needs correcting, with a link to the root of each problem.',
  agent_hub: 'The email-to-bill agent: watches a mailbox and drafts bills.',
  fact_graph: 'Knowledge graph over vendors, bills and payments.',
  anomaly_ai: 'Duplicate-bill detection, risk scores and payment predictions.',
  sap_matching: "Maps invoice line items to your SAP material master.",
  data_extraction: 'Bulk XLSX/CSV export and the vendor bulk-upload sheet.',
  tally_connector: 'The on-premise Tally agent: pairing, sync and ingest.',
  accounting_api_sync: 'SAP agent pairing and accounting-system synchronisation.',
  email_sending: 'Sending vendor emails from your own connected mailbox.',
}

/** Grouping for the admin toggle screen and the tenant usage view. */
export const SERVICE_CATEGORIES: { label: string; services: ServiceKey[] }[] = [
  {
    label: 'Core AP',
    services: ['bills', 'vendors', 'upload_ocr', 'approvals', 'reports', 'ledger', 'brands', 'users', 'settings', 'review_hub'],
  },
  { label: 'Tax & Compliance', services: ['tds', 'fx', 'gst_tax_verification', 'kyc_verification', 'crypto_tds', 'audit_anchor'] },
  { label: 'Payments', services: ['disbursement', 'nova_billing', 'demo_payments', 'crypto_payouts'] },
  { label: 'AI & Automation', services: ['agent_hub', 'fact_graph', 'anomaly_ai', 'sap_matching'] },
  { label: 'Integrations', services: ['data_extraction', 'tally_connector', 'accounting_api_sync', 'email_sending'] },
]

/** Core services are granted at onboarding; revoking one breaks the product. */
export const CORE_SERVICES: ServiceKey[] = SERVICE_CATEGORIES[0].services

export const ALL_SERVICES: ServiceKey[] = SERVICE_CATEGORIES.flatMap(c => c.services)
