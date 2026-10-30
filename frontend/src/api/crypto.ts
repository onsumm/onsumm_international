/**
 * Crypto rail API — typed from docs/crypto-rail/08_API_CONTRACT.md, which is
 * binding for the hackathon build. Authenticated calls go through the shared
 * axios instance (token + silent refresh from api/invoices.ts). The vendor
 * portal is public and authenticated by its own link token, so it uses a bare
 * axios instance: a 401 there means "this link is invalid", never "refresh the
 * signed-in user's session".
 */

import axios from 'axios'

// ── shared shapes ────────────────────────────────────────────────────────────

export interface PolicyResult {
  allowed: boolean
  codes: string[]
  messages: string[]
}

/** The backend's error envelope: `{detail: {error, codes, messages}}` or a plain string. */
export interface ApiErrorInfo {
  status: number | null
  error: string | null
  codes: string[]
  message: string
}

export function apiError(e: unknown, fallback = 'Request failed'): ApiErrorInfo {
  if (axios.isAxiosError(e)) {
    const status = e.response?.status ?? null
    const detail = e.response?.data?.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const d = detail as { error?: string; codes?: string[]; messages?: string[]; message?: string }
      const messages = d.messages ?? []
      return {
        status,
        error: d.error ?? null,
        codes: d.codes ?? [],
        message: messages.join(' ') || d.message || d.error || fallback,
      }
    }
    if (typeof detail === 'string') return { status, error: null, codes: [], message: detail }
    if (Array.isArray(detail)) return { status, error: 'VALIDATION', codes: [], message: 'Some fields are invalid.' }
    return { status, error: null, codes: [], message: e.message || fallback }
  }
  return { status: null, error: null, codes: [], message: (e as Error)?.message || fallback }
}

/** What each refusal code means to the person looking at the screen. */
export const CODE_COPY: Record<string, string> = {
  CRYPTO_DISABLED_RESIDENT_INDIA:
    'Crypto payouts are off for companies resident in India. FEMA has no permission for residents to settle cross-border payments in stablecoins, so the bank rail is the only rail.',
  CRYPTO_NOT_ENABLED: 'The crypto rail is not switched on for this company. Turn it on in the rail policy.',
  SCREENING_UNAVAILABLE: 'Wallet screening is unavailable right now, so the payout is held. Try again shortly.',
  TOKEN_NOT_ALLOWED: 'This token is not allowed by the rail policy.',
  CHAIN_NOT_ALLOWED: 'This chain is not allowed by the rail policy.',
  OVER_PER_TX_LIMIT: 'The amount is above the per-payment limit.',
  OVER_DAILY_LIMIT: 'This payout would exceed today’s limit.',
  WALLET_NOT_ACTIVE: 'The vendor has no active, approved wallet.',
  WALLET_COOLING: 'The vendor’s wallet is still in its cooling period.',
  INSUFFICIENT_TREASURY_BALANCE: 'The treasury Safe does not hold enough USDC for this payout.',
  ALREADY_IN_FLIGHT: 'A payment for this bill is already in progress.',
  DUPLICATE_ACROSS_RAILS: 'This bill is already being paid on another rail.',
  CHAIN_NOT_CONFIGURED: 'The crypto rail is not configured on this server (no RPC endpoint).',
  WALLET_MISMATCH: 'The wallet printed on the invoice differs from the vendor’s verified wallet.',
  OVERBILLED_VS_CONTRACT: 'The invoice charges more than the agreed contract rate.',
  SAFE_CONFIG_CHANGED: 'The Safe’s owners or threshold changed outside Onsumm.',
  PORTAL_LINK_INVALID: 'This link has expired or is not valid. Ask your client for a new one.',
}

export function codeCopy(code: string): string {
  return CODE_COPY[code] ?? code.replace(/_/g, ' ').toLowerCase()
}

// ── 1. tenant onboarding and policy ──────────────────────────────────────────

export interface Profile {
  tenant_id: string
  company_name: string | null
  legal_name: string | null
  country_code: string | null
  base_currency: string | null
  jurisdiction_profile: string | null
  is_resident_india: boolean
  kyb_status: string
  registration_number: string | null
  tax_residency: string | null
  crypto_enabled: boolean
}

export interface KybInput {
  legal_name: string
  country_code: string
  registration_number: string
  tax_residency: string
  base_currency: string
  documents: { name: string; note?: string }[]
}

export interface Ubo {
  id: string
  name: string
  country_code: string
  ownership_pct: number | string
  screening_status: string
  screened_at: string | null
}

export interface TreasuryWallet {
  id: string
  role: 'TREASURY' | 'TAX'
  chain: string
  address: string
  custody: 'SAFE' | 'EOA'
  safe_threshold: number | null
  safe_owners: string[]
  status: string
  usdc_balance_units: string | null
  explorer: string | null
  /** Book figures (USDC units): what demo settlement took out, what payouts
   *  waiting on the Safe have claimed, and what is left to pay with. */
  settled_offchain_units?: string
  reserved_units?: string
  reserved_count?: number
  paid_today_units?: string
  paid_today_count?: number
  available_units?: string | null
  settlement_mode?: 'chain' | 'demo' | string
}

export interface RailPolicy {
  crypto_enabled: boolean
  allowed_chains: string[]
  allowed_tokens: string[]
  per_tx_limit_usd: number | string | null
  daily_limit_usd: number | string | null
  dual_control_above_usd: number | string | null
  cooling_minutes: number
  require_test_transfer: boolean
}

export async function fetchProfile(): Promise<Profile> {
  return (await axios.get<Profile>('/api/onboarding/profile')).data
}
export async function submitKyb(body: KybInput): Promise<Profile> {
  return (await axios.post<Profile>('/api/onboarding/kyb', body)).data
}
export async function decideKyb(decision: 'APPROVED' | 'REJECTED', note?: string): Promise<Profile> {
  return (await axios.post<Profile>('/api/onboarding/kyb/decision', { decision, note })).data
}
export async function fetchUbos(): Promise<Ubo[]> {
  return (await axios.get<{ ubos: Ubo[] }>('/api/onboarding/ubos')).data.ubos
}
export async function addUbo(body: { name: string; country_code: string; ownership_pct: number }): Promise<Ubo> {
  return (await axios.post<Ubo>('/api/onboarding/ubos', body)).data
}
export async function deleteUbo(id: string): Promise<void> {
  await axios.delete(`/api/onboarding/ubos/${id}`)
}
export async function fetchTreasuryWallets(): Promise<TreasuryWallet[]> {
  return (await axios.get<{ wallets: TreasuryWallet[] }>('/api/treasury/wallets')).data.wallets
}
export async function connectTreasuryWallet(body: { role: 'TREASURY' | 'TAX'; chain: string; address: string }): Promise<TreasuryWallet> {
  return (await axios.post<TreasuryWallet>('/api/treasury/wallets', body)).data
}
export async function refreshTreasuryWallet(id: string): Promise<TreasuryWallet> {
  return (await axios.post<TreasuryWallet>(`/api/treasury/wallets/${id}/refresh`)).data
}
export async function fetchRailPolicy(): Promise<RailPolicy> {
  return (await axios.get<RailPolicy>('/api/tenant/rail-policy')).data
}
export async function saveRailPolicy(body: Partial<RailPolicy>): Promise<RailPolicy> {
  return (await axios.put<RailPolicy>('/api/tenant/rail-policy', body)).data
}

// ── 2. vendor wallets and contract rates ─────────────────────────────────────

export type WalletStatus = 'PENDING_PROOF' | 'COOLING' | 'ACTIVE' | 'FROZEN' | 'REVOKED'

export interface SupplierWallet {
  id: string
  supplier_id: string
  chain: string
  asset: string
  address: string
  is_primary: boolean
  status: WalletStatus | string
  screening_status: string | null
  verified_at: string | null
  cooling_until: string | null
  approved_by: string | null
  replaced_by: string | null
  explorer: string | null
}

export interface ContractRate {
  id?: string
  item_key: string
  unit_price: number | string
  currency_code: string
  valid_from: string | null
  valid_to: string | null
}

export interface WalletInvite {
  invite_url: string
  token: string
  expires_at: string
  emailed_to: string | null
}

export async function createWalletInvite(supplierId: string, email?: string): Promise<WalletInvite> {
  return (await axios.post<WalletInvite>(`/api/vendors/${supplierId}/wallet-invites`, email ? { email } : {})).data
}
export async function fetchSupplierWallets(supplierId: string): Promise<SupplierWallet[]> {
  return (await axios.get<{ wallets: SupplierWallet[] }>(`/api/vendors/${supplierId}/wallets`)).data.wallets
}
export async function approveSupplierWallet(supplierId: string, walletId: string): Promise<SupplierWallet> {
  return (await axios.post<SupplierWallet>(`/api/vendors/${supplierId}/wallets/${walletId}/approve`)).data
}
export async function freezeSupplierWallet(supplierId: string, walletId: string, reason: string): Promise<SupplierWallet> {
  return (await axios.post<SupplierWallet>(`/api/vendors/${supplierId}/wallets/${walletId}/freeze`, { reason })).data
}
export async function fetchContractRates(supplierId: string): Promise<ContractRate[]> {
  return (await axios.get<{ rates: ContractRate[] }>(`/api/vendors/${supplierId}/contract-rates`)).data.rates
}
export async function saveContractRates(supplierId: string, rates: ContractRate[]): Promise<ContractRate[]> {
  const body = { rates: rates.map(({ id: _id, ...r }) => r) }
  return (await axios.put<{ rates: ContractRate[] }>(`/api/vendors/${supplierId}/contract-rates`, body)).data.rates
}

// ── 3. vendor portal (public) ────────────────────────────────────────────────

const publicApi = axios.create({ baseURL: (import.meta.env.VITE_API_URL as string | undefined) || undefined })

export interface PortalInfo {
  supplier_name: string
  tenant_name: string
  chain: string
  chain_id: number
  expires_at: string
  wallets: SupplierWallet[]
}

export interface PortalChallenge {
  message: string
  nonce: string
  expires_at: string
}

export interface PortalVerifyResult {
  wallet_id: string
  status: string
  screening: string
  cooling_until: string | null
  needs_admin_approval: boolean
}

export type TaxDocType = 'W-8BEN-E' | 'TRC' | 'FORM_10F' | 'NO_PE'

const portal = (token: string) => `/api/vendor-portal/${encodeURIComponent(token)}`

export async function fetchPortal(token: string): Promise<PortalInfo> {
  return (await publicApi.get<PortalInfo>(portal(token))).data
}
export async function fetchPortalChallenge(token: string, chain: string, address: string): Promise<PortalChallenge> {
  return (await publicApi.get<PortalChallenge>(`${portal(token)}/wallet/challenge`, { params: { chain, address } })).data
}
export async function verifyPortalWallet(token: string, message: string, signature: string): Promise<PortalVerifyResult> {
  return (await publicApi.post<PortalVerifyResult>(`${portal(token)}/wallet/verify`, { message, signature })).data
}
export async function submitPortalTaxDoc(token: string, doc_type: TaxDocType, reference: string): Promise<{ tax_docs: Record<string, unknown> }> {
  return (await publicApi.post(`${portal(token)}/tax-docs`, { doc_type, reference })).data
}

// ── 4. payouts ───────────────────────────────────────────────────────────────

export interface WithholdingView {
  rule_pack: string
  rule_code: string | null
  rate: string | number
  amount_units: string
  requires_signoff: boolean
}

export interface PayoutPreview {
  disbursement_id: string
  status: string
  sub_status: string
  rail: 'USDC'
  bill: { invoice_id: string; invoice_number?: string | null; billed: string; approved: string; currency: string }
  quote: { fx_snapshot_id: string; token_usd: string; usd_inr: string; usd_functional?: string }
  withholding: WithholdingView
  gross_units: string
  net_units: string
  to: { supplier: string; address: string; chain: string }
  from: { safe: string; threshold: number; owners: number }
  tax_wallet?: string | null
  estimated_gas_fee: string | null
  policy: PolicyResult
  otp_sent_to?: string
  demo_otp?: string | null
  otp_expires_at?: number
  otp_ttl_seconds?: number
}

export interface WithholdingPreview {
  rule_pack: string
  rule_code: string | null
  rate: string | number
  base_amount: string
  base_currency: string
  amount_units: string
  net_units: string
  requires_signoff: boolean
  policy: PolicyResult
  /** The amount in USDC units before withholding, its USD value, and the rate used. */
  gross_units?: string
  amount_usd?: string
  token_usd?: string
  alternative?: { rail: 'IMPS'; rule_pack: string; rule_code: string | null; rate: string | number; amount: string; currency: string; note: string }
}

export interface SafeTxView {
  safe: string
  chain_id: number
  safe_tx_hash: string
  nonce: number
  typed_data: {
    domain: Record<string, unknown>
    primaryType: string
    types: Record<string, { name: string; type: string }[]>
    message: Record<string, unknown>
  }
  signatures: { signer: string; signed_at: string }[]
  threshold: number
  /** The Safe's owners, read from the chain. */
  owners?: string[]
  /** The Safe's own nonce on-chain; transactions execute strictly in nonce order. */
  safe_nonce_onchain?: number | null
  /** An earlier payout from the same Safe that must be signed (or cancelled) first. */
  waiting_for?: {
    disbursement_id: string
    nonce: number
    sub_status: string
    invoice_id: string
    supplier_id: string
    amount: string
  } | null
  /** No payout holds the lower nonce any more: rebuild this one at `next_free_nonce`. */
  nonce_gap?: boolean
  next_free_nonce?: number | null
  /** Demo settlement is on: payouts are never broadcast, so the Safe's nonce never moves. */
  demo_settlement?: boolean
}

export interface ChainTransfer {
  id: string
  chain: string
  tx_hash: string
  log_index: number
  block_number: number
  block_time: string | null
  direction: 'IN' | 'OUT'
  from_address: string
  to_address: string
  amount_units: string
  match_type: string | null
  matched_disbursement_id: string | null
  matched_invoice_id: string | null
  status: string
  confirmations: number
  removed: boolean
  explorer: string | null
  /** Recorded by a demo settlement, not read from the chain. */
  simulated?: boolean
}

export interface CryptoStatus {
  disbursement_id: string
  invoice_id: string
  status: string
  sub_status: string
  amount: string
  currency: string
  gross_units: string
  net_units: string
  withholding_units: string
  safe: string
  safe_tx_hash: string | null
  nonce: number | null
  signatures: { signer: string; signed_at: string }[]
  threshold: number
  tx_hash: string | null
  explorer: string | null
  block_number: number | null
  confirmations: number
  confirmations_target: number
  failure_reason: string | null
  transfers: ChainTransfer[]
  /** Demo settlement: validated against the real Safe with eth_call, never broadcast. */
  simulated?: boolean
}

export async function fetchWithholdingPreview(invoiceId: string, amount: string, rail = 'USDC'): Promise<WithholdingPreview> {
  return (await axios.get<WithholdingPreview>('/api/withholding/preview', { params: { invoice_id: invoiceId, rail, amount } })).data
}
export async function createUsdcPayout(invoiceId: string, amount: string): Promise<PayoutPreview> {
  return (await axios.post<PayoutPreview>(`/api/bills/${invoiceId}/disbursements`, { rail: 'USDC', amount })).data
}
export async function verifyUsdcOtp(disbursementId: string, otp: string): Promise<{ disbursement_id: string; status: string; sub_status: string; safe_tx_hash: string; nonce: number }> {
  return (await axios.post(`/api/disbursements/${disbursementId}/verify-otp`, { otp })).data
}
export async function fetchSafeTx(disbursementId: string): Promise<SafeTxView> {
  return (await axios.get<SafeTxView>(`/api/disbursements/${disbursementId}/safe-tx`)).data
}
export async function postSafeSignature(disbursementId: string, signer: string, signature: string): Promise<{ sub_status: string; signatures: number; threshold: number; tx_hash?: string | null; explorer?: string | null }> {
  return (await axios.post(`/api/disbursements/${disbursementId}/signatures`, { signer, signature })).data
}
export async function fetchCryptoStatus(disbursementId: string): Promise<CryptoStatus> {
  return (await axios.get<CryptoStatus>(`/api/disbursements/${disbursementId}/crypto-status`)).data
}
export interface PendingUsdcPayout {
  disbursement_id: string
  invoice_id: string
  supplier_id: string
  invoice_number: string | null
  amount: string
  currency: string | null
  nonce: number | null
  sub_status: string
  signatures: number
  threshold: number | null
  created_at: string
}

/** USDC payouts waiting for Safe signatures or for the chain, oldest nonce first. */
export async function fetchPendingUsdcPayouts(supplierId?: string): Promise<PendingUsdcPayout[]> {
  const res = await axios.get<{ payouts: PendingUsdcPayout[] }>('/api/disbursements/pending-usdc',
    { params: supplierId ? { supplier_id: supplierId } : undefined })
  return res.data.payouts
}

/**
 * Keep this payout and cancel the vendor's other unsent USDC payouts; this one is
 * rebuilt at the Safe's next free nonce (signatures already collected are cleared).
 */
export async function keepOnlyCryptoPayout(disbursementId: string):
  Promise<{ cancelled: string[]; nonce: number | null; rebuilt: boolean; signatures_cleared: boolean }> {
  return (await axios.post(`/api/disbursements/${disbursementId}/keep-only`)).data
}

/** Rebuild an unsubmitted payout at the Safe's next free nonce. Collected signatures are cleared. */
export async function reproposeCryptoPayout(disbursementId: string): Promise<{ nonce: number; changed: boolean }> {
  return (await axios.post<{ nonce: number; changed: boolean }>(`/api/disbursements/${disbursementId}/repropose`)).data
}

export async function cancelCryptoPayout(disbursementId: string, reason: string): Promise<CryptoStatus> {
  return (await axios.post<CryptoStatus>(`/api/disbursements/${disbursementId}/cancel`, { reason })).data
}

// ── 5. bill checks ───────────────────────────────────────────────────────────

export interface CryptoFinding {
  code: string
  severity: string
  title: string
  details: Record<string, unknown> | string | null
}

export async function runCryptoChecks(invoiceId: string): Promise<{ invoice_id: string; findings: CryptoFinding[] }> {
  return (await axios.post(`/api/bills/${invoiceId}/crypto-checks`)).data
}

// ── 6. on-chain ledger ───────────────────────────────────────────────────────

export interface OnchainStatus {
  configured: boolean
  chain: string
  chain_id: number
  head_block: number | null
  cursor_block: number | null
  lag_blocks: number | null
  watched_addresses: string[]
}

export interface WalletStatement {
  address: string
  role: string
  opening_units: string
  inflow_units: string
  outflow_units: string
  closing_units_ledger: string
  closing_units_chain: string | null
  reconciled: boolean
}

export interface DailyStatement {
  date: string
  wallets: WalletStatement[]
  lines: ChainTransfer[]
}

export interface LedgerPosting {
  id: string
  source_type: string
  source_id: string
  target_system: string
  external_ref: string | null
  status: string
  error: string | null
  created_at: string
}

export async function fetchOnchainStatus(): Promise<OnchainStatus> {
  return (await axios.get<OnchainStatus>('/api/onchain/status')).data
}
export async function fetchOnchainTransfers(limit = 50, status?: string): Promise<ChainTransfer[]> {
  return (await axios.get<{ transfers: ChainTransfer[] }>('/api/onchain/transfers', { params: { limit, ...(status ? { status } : {}) } })).data.transfers
}
export async function rescanOnchain(fromBlock?: number): Promise<{ processed: number; cursor_block: number }> {
  return (await axios.post('/api/onchain/rescan', fromBlock ? { from_block: fromBlock } : {})).data
}
export async function fetchStatement(date: string): Promise<DailyStatement> {
  return (await axios.get<DailyStatement>(`/api/onchain/statements/${date}`)).data
}
export async function fetchPostings(): Promise<LedgerPosting[]> {
  return (await axios.get<{ postings: LedgerPosting[] }>('/api/onchain/postings')).data.postings
}
export async function fetchVoucherXml(postingId: string): Promise<string> {
  return (await axios.get<string>(`/api/onchain/postings/${postingId}/voucher`, { responseType: 'text' })).data
}
