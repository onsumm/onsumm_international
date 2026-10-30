# Crypto Rail — Design Decisions and Glossary

Status: agreed baseline for Hacker House Goa 2026 · as of 29 Oct 2026

This file is the single source of truth for names, states and decisions. Every other document in `docs/crypto-rail/` uses the names defined here. If a document disagrees with this file, this file wins and the document is fixed.

| # | Document | What it covers |
| --- | --- | --- |
| 00 | [00_DECISIONS.md](00_DECISIONS.md) | This file: decisions, names, glossary |
| — | [README.md](README.md) | Hackathon README: demo, architecture, what is built |
| 01 | [01_BUSINESS.md](01_BUSINESS.md) | Business document |
| 02 | [02_TECHNICAL.md](02_TECHNICAL.md) | Technical document |
| 03 | [03_HLD.md](03_HLD.md) | High-level design with Mermaid diagrams |
| 04 | [04_LLD.md](04_LLD.md) | Low-level design with class, sequence and state diagrams |
| 05 | [05_USER_FLOWS.md](05_USER_FLOWS.md) | User flow diagrams per persona |
| 06 | [06_EDGE_CASES.md](06_EDGE_CASES.md) | Edge cases, failure handling, with diagrams |
| 07 | [07_IMPLEMENTATION_PLAN.md](07_IMPLEMENTATION_PLAN.md) | Hackathon build plan and post-hackathon plan |
| 08 | [08_API_CONTRACT.md](08_API_CONTRACT.md) | Binding request and response shapes for the build |
| 09 | [09_EXPLAINED_SIMPLY.md](09_EXPLAINED_SIMPLY.md) | The whole flow in plain bank terms, for non-crypto readers |

---

## 1. Decisions

### D1. Scope

- **Hackathon (29–31 Oct 2026):** testnet only (Base Sepolia, chain ID 84532), USDC only, no real funds, demo on Saturday 31 Oct morning.
- **Production (after the hackathon):** gated by an Indian legal opinion; see the business document.

### D2. Who uses the crypto rail

- Tenants **incorporated outside India** (`dim_tenants.is_resident_india = false`) may enable the crypto rail.
- For India-resident tenants the crypto rail is **hard-disabled** by `PolicyEngine`, because FEMA has no permission for residents to settle cross-border payments in stablecoins. The bank (IMPS) rail stays the only rail for them. The demo shows this block on purpose.

### D3. Custody: Onsumm never holds a key that can move funds

- `CustodyProvider` is an interface with two implementations:
  - **`SafeCustody` (hackathon and production option A):** the client's own Safe multisig. Approvers sign the Safe transaction (EIP-712) in their browser wallet inside the Onsumm UI. Onsumm stores the signatures. When the Safe threshold is met, a **gas-only relayer** (`GasRelayer`) submits `execTransaction`. The relayer key holds only test ETH for gas and cannot move USDC: a Safe only executes with valid owner signatures.
  - **`PartnerCustody` (production option B):** a regulated custody and payouts partner (Bridge, Fireblocks, Circle or BVNK) holding an MPC wallet under the client's policy.
- The anchor wallet (`ANCHOR_SIGNER_KEY`) holds only gas and can only write audit roots.

### D4. Asset and chain

- USDC only. The token contract on Base Sepolia is Circle's test USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 decimals). Verify it against Circle's docs before use.
- USDT is **not** offered: since July 2026 it cannot be used with MiCA-licensed providers in the EU. `tenant_rail_policy.allowed_tokens` exists so this can change later.
- Amounts on-chain are stored as integers in token units (`amount_units`, 1 USDC = 1,000,000 units).

### D5. Tax story (fixed from the original README)

- The demo payer is **Lumen Labs Pte Ltd (Singapore)**, functional currency **USD**. It pays **Nova Design LLC (US)**.
- Indian sections (195, 194S, reverse-charge GST) do **not** apply to a Singapore company paying a US vendor. The demo therefore uses a **client-jurisdiction rule pack** (`ConfigurablePack`, code `SG-DEMO`) whose rule for this vendor category is set to 0% and marked *requires adviser sign-off*. Onsumm does not assert Singapore tax law.
- Every payout stores its value in the tenant's functional currency (USD) **and** an INR reference value (`fx_snapshots.usd_inr`) for an Indian parent's consolidation.
- **Guardrail moment in the demo:** switching to the India-resident tenant *Onsumm Demo India Pvt Ltd* and trying to pay a foreign vendor in USDC is blocked by `PolicyEngine` (`CRYPTO_DISABLED_RESIDENT_INDIA`). The withholding preview then shows what an Indian payer would withhold under Section 195 on the bank rail. This is where Indian tax rules belong.
- India packs: `IndiaServicesPack` (exists today as `utils/tds_engine.py`), `IndiaNonResidentPack` (Section 195, new), `IndiaVdaPack` (Section 194S / s.393(1) of the Income-tax Act 2025: 1%, 20% without PAN, new, used only when an Indian resident buys a VDA).
- Rule pack codes stored in `crypto_withholdings.rule_pack`: `SG-DEMO` (`ConfigurablePack`), `IN-SERVICES`, `IN-195`, `IN-VDA`.
- When withholding is above zero, the payout is **one Safe transaction using `MultiSend`** with two USDC transfers: the net amount to the vendor and the withheld amount to the tenant's `TAX` treasury wallet. Both transfers are matched by `OnchainMatcher` (`match_type` `PAYOUT` and `TAX`). With zero withholding it is a single `transfer`.

### D6. Stack (matches the real codebase)

- Backend: Python FastAPI in `backend_ocr/app`. Libraries: `web3` (v7), `eth-account` (signature recovery, EIP-712), `siwe` (EIP-4361 parsing). No separate TypeScript service.
- Contracts: Foundry, folder `contracts/` at repo root.
- Frontend: existing React + Vite app in `frontend/src`. No new packages: the app talks to the browser wallet (MetaMask, Rabby) through the standard EIP-1193 provider (`window.ethereum`) in `frontend/src/web3/eip1193.ts`, and verifies proofs with WebCrypto in `frontend/src/web3/merkle.ts`. `wagmi` / `viem` can replace this later without changing the backend.
- Database: the existing Postgres (`onsumm-new-db` for the hackathon), migrations as idempotent functions in `backend_ocr/app/migrations.py`.
- Background workers run inside the API process, started from the startup hook like today's disbursement sweeper.

### D7. What the original README claimed that is not true today (must be built or not claimed)

| Claim | Reality | Decision |
| --- | --- | --- |
| Audit events are already hash-chained | `wf_audit_log` has no hash columns | **Build** `AuditHasher` on day 1 |
| Books post to Tally / ERPNext | No ERPNext; Tally has no payment-voucher builder; Zoho/QuickBooks/Xero are pull-only via Apideck | **Build** `TallyPaymentVoucherBuilder`; do not mention ERPNext |
| Bank rail is IMPS/NEFT/RTGS | Code does IMPS only (ICICI, HDFC, AU via Lendix) | Say IMPS |
| WhatsApp capture | Only an enum value exists | **Stretch**: Twilio WhatsApp sandbox webhook into existing OCR; fallback is the existing email intake |
| Recon flags overbilling vs PO | No contract-rate check exists | **Build** a minimal `vendor_contract_rates` check |
| "Ask Onsumm" co-pilot | Does not exist | Out of scope |
| Tables `orgs`, `vendors`, `bills`, `payments` | Real tables: `dim_tenants`, `dim_suppliers`, `fact_invoices` / `fact_bills`, `fact_disbursements` | Use real names |

### D8. Public verification

- Proofs are verified from a **proof bundle** (downloaded JSON or a share link containing a signed token), never by typing a raw bill ID. Bill IDs must not be enumerable.
- Every leaf includes a per-tenant secret salt (`tenant_audit_salts`), so short records cannot be guessed from their hash. The salt is included in the bundle given to the auditor.
- Hash function: SHA-256 for row hashes, leaves and tree nodes (sorted-pair hashing). The contract stores `bytes32` roots only.

### D8a. Hash chain and anchor, as built

- The hash chain is computed by a Postgres `BEFORE INSERT` trigger on `wf_audit_log`, so every existing writer is covered without code changes. The hashed text (`canonical`) is the `jsonb_build_object(...)::text` of the row's fields, produced by the same SQL expression at write time and at verification time. Proof bundles carry that exact text. The trigger never blocks an insert: if hashing fails it logs a warning and leaves the hash empty, which `verify-chain` reports.
- Anchoring has two modes. **`calldata` (hackathon default):** the anchor wallet sends a 0-value transaction to itself whose input is `"ONSM" ‖ 0x01 ‖ root ‖ fromSeq ‖ toSeq`. No contract needs deploying. **`contract`:** when `ANCHOR_CONTRACT` is set, the root goes to `OnsummAnchor.anchor(root, fromSeq, toSeq)` (deployed with Foundry). The format is fixed in [08_API_CONTRACT.md](08_API_CONTRACT.md) §7.

### D10. Jurisdiction profile: no India-only rule is assumed

Onsumm was built for Indian companies paying in rupees, so GSTIN, PAN, TDS, IFSC bank accounts, IMPS, the April fiscal year, `en-IN` number formatting and the ₹ sign were assumed on every screen and check. For an international tenant those either do not apply or block it outright (a foreign vendor can never become "GST verified", so the payment guardrail refused every payment).

- **One source of truth:** `backend_ocr/app/core/jurisdiction.py` (backend) and `frontend/src/auth/jurisdiction.ts` with `useJurisdiction()` (frontend). Every India-specific check and screen asks it.
- **Profile defaults:** `india` turns every pack on (exactly today's behaviour); `international` turns every pack off and offers the USDC rail. Country sets locale, time zone, fiscal-year start and tax labels (SG: `en-SG`, January, "UEN / GST reg. no."; AE: TRN, VAT; US: EIN, sales tax).
- **Rule packs**, each on or off per tenant: `gst`, `pan`, `tds_india`, `bank_india`, `imps`, `msme`.
- **Configurable:** a tenant admin can override any pack, the fiscal-year start and the tax labels in Settings → **Country rules** (`GET/PUT /api/tenant/jurisdiction`), stored in `dim_tenants.settings_jsonb -> 'jurisdiction'`. The profile, country and books currency are fixed at onboarding.
- **Workers** that sweep many tenants filter with `pack_enabled_sql(pack)`, the same rule in SQL.
- **Money:** every amount shows in the tenant's `base_currency` with its locale; a bill is "foreign" only when its currency differs from that. Upload FX conversion converts into the books currency (a cross rate through INR when needed), not always into INR.
- **Vendor verification** is per jurisdiction: India = GSTIN + bank verified (unchanged); international = an active wallet or verified bank account, plus tax documents.

### D9. Safety defaults (keep from our documents; the original README missed them)

| Control | Production default | Demo setting |
| --- | --- | --- |
| Cooling period on a new or changed vendor wallet | 24 hours | 2 minutes (`cooling_minutes = 2`), countdown shown on screen |
| Re-screen wallet before every payout | On | On |
| 1 USDC test transfer on first payout to a wallet | On | Off (policy flag shown) |
| Dual control above an amount | USD 10,000 | Safe threshold 2-of-3 covers it |
| Alert on any outgoing transfer Onsumm did not propose | On | On (demoed as an edge case if time allows) |
| Daily wallet statement vs on-chain balance | On | Generated on demand |
| Invoice wallet address used for payment | Never | Never; compared only (`WALLET_MISMATCH`) |
| Same invoice paid on two rails | Blocked | Blocked (`DUPLICATE_ACROSS_RAILS`) |

---

## 2. Canonical names

### 2.1 Tables

Changed:

| Table | New columns |
| --- | --- |
| `dim_tenants` | `country_code char(2) default 'IN'`, `base_currency char(3) default 'INR'`, `jurisdiction_profile text default 'india'`, `is_resident_india boolean default true`, `kyb_status text default 'NOT_STARTED'` |
| `dim_suppliers` | `country_code char(2)`, `tax_residency char(2)`, `preferred_rail text default 'BANK'`, `tax_docs jsonb default '{}'` |
| `fact_disbursements` | `rail text default 'IMPS'`, `currency_code char(3) default 'INR'`, `withholding_amount numeric default 0`, `net_amount numeric` |
| `wf_audit_log` | `seq bigint`, `prev_hash bytea`, `row_hash bytea`, `anchor_id bigint` |

New:

| Table | Key columns |
| --- | --- |
| `tenant_ubos` | `id uuid PK`, `tenant_id`, `name`, `country_code`, `ownership_pct numeric`, `screening_status`, `screened_at` |
| `tenant_rail_policy` | `tenant_id PK`, `crypto_enabled bool`, `allowed_chains text[]`, `allowed_tokens text[]`, `per_tx_limit_usd`, `daily_limit_usd`, `dual_control_above_usd`, `cooling_minutes int default 1440`, `require_test_transfer bool default true` |
| `treasury_wallets` | `id uuid PK`, `tenant_id`, `role` (`TREASURY`, `TAX`), `chain`, `address`, `custody` (`SAFE`, `PARTNER`), `safe_threshold int`, `safe_owners text[]`, `status` |
| `supplier_wallets` | `id uuid PK`, `tenant_id`, `supplier_id`, `chain`, `asset`, `address`, `is_primary`, `siwe_message`, `ownership_sig`, `verified_at`, `cooling_until`, `screening_status`, `status` (`PENDING_PROOF`, `COOLING`, `ACTIVE`, `FROZEN`, `REVOKED`), `approved_by`, `replaced_by uuid` |
| `wallet_screenings` | `id`, `chain`, `address`, `provider`, `result` (`CLEAR`, `HIT`, `ERROR`), `risk_score`, `categories jsonb`, `screened_at`, `raw jsonb` |
| `vendor_contract_rates` | `id`, `tenant_id`, `supplier_id`, `item_key`, `unit_price numeric`, `currency_code`, `valid_from`, `valid_to` |
| `crypto_disbursement_details` | `disbursement_id PK/FK`, `chain`, `token_contract`, `treasury_wallet_id`, `supplier_wallet_id`, `amount_units numeric(38,0)` (net amount to the vendor; the withheld amount is in `crypto_withholdings.amount_units`), `fx_snapshot_id`, `safe_tx_hash`, `safe_nonce bigint`, `idempotency_key text unique`, `sub_status`, `tx_hash`, `block_number`, `confirmations int`, `gas_fee_native numeric`, `submitted_at`, `confirmed_at`, `failure_reason` |
| `crypto_payout_signatures` | `disbursement_id`, `signer_address`, `user_id`, `signature`, `signed_at`; unique (`disbursement_id`, `signer_address`) |
| `fx_snapshots` | `id`, `token`, `token_usd numeric`, `usd_functional numeric`, `usd_inr numeric`, `source`, `captured_at`, `block_time` |
| `chain_transfers` | `id`, `chain`, `tx_hash`, `log_index` (unique with chain + tx_hash), `block_number`, `block_time`, `token_contract`, `from_address`, `to_address`, `amount_units`, `tenant_id`, `direction` (`IN`, `OUT`), `match_type` (`PAYOUT`, `TAX`, `FUNDING`, `RECEIVABLE`, `UNKNOWN`), `matched_disbursement_id`, `matched_invoice_id`, `status` (`MATCHED`, `UNMATCHED`, `MISMATCH`), `confirmations`, `removed bool` (reorg) |
| `chain_watch_cursors` | `chain PK`, `last_block`, `last_block_hash` (reorg check), `updated_at` |
| `crypto_withholdings` | `id`, `disbursement_id`, `rule_pack`, `rule_code`, `rate`, `base_amount`, `base_currency`, `amount_units`, `requires_signoff bool`, `status`, `remittance_id` |
| `withholding_remittances` | `id`, `tenant_id`, `period`, `authority`, `total_units`, `fiat_amount`, `reference`, `tx_hash`, `certificate_path`, `status` |
| `tenant_audit_salts` | `tenant_id PK`, `salt bytea` (secret) |
| `audit_anchors` | `id bigserial`, `merkle_root bytea`, `from_seq`, `to_seq`, `tenant_count`, `chain`, `contract`, `tx_hash`, `status`, `anchored_at` |
| `audit_anchor_tenant_roots` | `anchor_id`, `tenant_id`, `tenant_root`, `first_seq`, `last_seq`, `leaf_count` |
| `ledger_postings` | `id`, `tenant_id`, `source_type`, `source_id`, `target_system`, `payload jsonb`, `external_ref`, `status`, `error`, `created_at` |

### 2.2 Payout status

`fact_disbursements.status` keeps today's values so existing screens and the sweeper work. `crypto_disbursement_details.sub_status` carries the detail.

| `status` | `sub_status` | Meaning |
| --- | --- | --- |
| `PENDING_OTP` | `AWAITING_RELEASE` | Prepared; waiting for the Onsumm OTP release gate |
| `PENDING` | `PROPOSED` | Safe transaction built; no owner signatures yet |
| `PENDING` | `PARTIALLY_SIGNED` | Some owner signatures, below threshold |
| `PENDING` | `READY` | Threshold met; queued for the relayer |
| `PENDING` | `SUBMITTED` | Relayer sent `execTransaction`; transaction hash known |
| `PENDING` | `CONFIRMING` | In a block, below the confirmation target |
| `SUCCESS` | `CONFIRMED` | Final; bill updated; voucher queued |
| `FAILED` | `REJECTED` | Policy, screening or an owner rejected it |
| `FAILED` | `EXPIRED` | Signatures or FX snapshot expired before execution |
| `FAILED` | `REVERTED` | Executed on-chain but reverted |
| `FAILED` | `REPLACED` | Safe nonce used by another transaction (cancelled or replaced) |

### 2.3 Finding codes (Review Hub)

`WALLET_MISMATCH`, `WALLET_CHANGED`, `SCREENING_HIT`, `OVERBILLED_VS_CONTRACT`, `DUPLICATE_ACROSS_RAILS`, `UNMATCHED_OUTBOUND_TRANSFER`, `UNMATCHED_INBOUND_TRANSFER`, `AMOUNT_MISMATCH`, `PAYOUT_STUCK`, `REORG_DETECTED`, `UNRECONCILED_DAY`, `TAX_DOCS_MISSING`, `WITHHOLDING_SIGNOFF_REQUIRED`, `ANCHOR_FAILED`, `SAFE_CONFIG_CHANGED`, `TOKEN_DEPEG`, `AUDIT_CHAIN_BROKEN`.

Policy refusal codes: `CRYPTO_DISABLED_RESIDENT_INDIA`, `CRYPTO_NOT_ENABLED`, `SCREENING_UNAVAILABLE` (fail closed), `TOKEN_NOT_ALLOWED`, `CHAIN_NOT_ALLOWED`, `OVER_PER_TX_LIMIT`, `OVER_DAILY_LIMIT`, `WALLET_NOT_ACTIVE`, `WALLET_COOLING`, `INSUFFICIENT_TREASURY_BALANCE`, `ALREADY_IN_FLIGHT`.

### 2.4 Classes (backend, Python)

| Class | Module | Role |
| --- | --- | --- |
| `Rail` (Protocol) | `integrations/payments/rails.py` | `quote`, `check`, `initiate`, `status` |
| `ImpsRail` | same | Wraps today's bank adapters |
| `UsdcRail` | same | USDC payouts through a `CustodyProvider` |
| `RailRouter` | `domain/crypto/router.py` | Picks the rail after approval; enforces D2 |
| `PolicyEngine` | `domain/crypto/policy.py` | All refusal codes in 2.3 |
| `CustodyProvider` (Protocol) | `integrations/custody/base.py` | `propose`, `add_signature`, `ready`, `execute`, `status`, `balance` |
| `SafeCustody` | `integrations/custody/safe.py` | Safe transaction building, EIP-712 hash, signature checks |
| `PartnerCustody` | `integrations/custody/partner.py` | Production stub |
| `GasRelayer` | `integrations/custody/relayer.py` | Submits `execTransaction` with a gas-only key |
| `EvmChain` | `integrations/chains/evm.py` | RPC, logs, receipts, confirmations, explorer links |
| `WalletService` | `domain/crypto/wallets.py` | Register, prove, cool, approve, freeze wallets |
| `SiweVerifier` | `domain/crypto/siwe.py` | EIP-4361 challenge and verification |
| `ScreeningProvider` (Protocol), `StaticListScreening` | `integrations/screening/` | Address screening |
| `FxService` | `domain/crypto/fx.py` | `fx_snapshots` |
| `WithholdingEngine`, `RulePack`, `ConfigurablePack`, `IndiaServicesPack`, `IndiaNonResidentPack`, `IndiaVdaPack` | `domain/crypto/withholding/` | Crypto TDS |
| `PayoutService` | `domain/crypto/payouts.py` | Orchestrates the payout lifecycle |
| `ChainWatcher` | `domain/crypto/watcher.py` | Polls USDC `Transfer` logs into `chain_transfers` |
| `OnchainMatcher` | `domain/crypto/recon.py` | Matches transfers; raises findings |
| `ContractRateCheck` | `domain/crypto/recon.py` | `OVERBILLED_VS_CONTRACT` |
| `LedgerPoster`, `TallyPaymentVoucherBuilder` | `domain/crypto/ledger.py` | Vouchers, `ledger_postings` |
| `AuditHasher` | `domain/audit/hasher.py` | Hash chain on `wf_audit_log` |
| `MerkleBuilder`, `AnchorService`, `ProofService` | `domain/audit/anchor.py` | Merkle tree, anchoring, proof bundles |

### 2.5 API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/onboarding/kyb` | Entity profile and KYB documents |
| `POST` | `/api/onboarding/ubos` | UBOs, screened on save |
| `POST` | `/api/treasury/wallets` | Connect a Safe; owners and threshold read on-chain |
| `GET/PUT` | `/api/tenant/rail-policy` | Chains, tokens, limits, cooling, test transfer |
| `POST` | `/api/vendors/{id}/wallet-invites` | Email or WhatsApp a signed portal link to the vendor |
| `GET` | `/api/vendor-portal/{token}/wallet/challenge` | SIWE message to sign |
| `POST` | `/api/vendor-portal/{token}/wallet/verify` | Signature; starts screening and cooling |
| `POST` | `/api/vendors/{id}/wallets/{wid}/approve` | Admin approval of a new or changed wallet |
| `POST` | `/api/bills/{id}/disbursements` | Existing; now accepts `rail: "USDC"`; returns quote, withholding, policy result |
| `POST` | `/api/disbursements/{id}/verify-otp` | Existing; for USDC creates the Safe proposal |
| `GET` | `/api/disbursements/{id}/safe-tx` | EIP-712 typed data for approvers to sign |
| `POST` | `/api/disbursements/{id}/signatures` | Add an owner signature |
| `POST` | `/webhooks/chain` | Optional Alchemy webhook; the poller is the fallback |
| `GET` | `/api/onchain/transfers` | On-chain ledger with match status |
| `GET` | `/api/onchain/statements/{date}` | Daily wallet statement |
| `GET` | `/api/withholding/preview` | Withholding for a bill and rail |
| `POST` | `/api/audit/anchor` | Build and anchor now (admin; scheduled daily otherwise) |
| `GET` | `/api/audit/proof/{log_id}` | Proof bundle JSON |
| `GET` | `/verify` | Public page; verifies an uploaded or linked bundle in the browser |

Licence keys (added to `security/licensing.py`): `crypto_payouts`, `crypto_tds`, `audit_anchor`.

### 2.6 Demo cast

| Role | Name |
| --- | --- |
| Foreign client (payer) | Lumen Labs Pte Ltd, Singapore, USD |
| Client approvers (Safe owners, 2-of-3) | Founder (Asha), Finance controller (Ben), CFO backup (Chen) |
| Vendor | Nova Design LLC, United States |
| India-resident tenant (guardrail moment) | Onsumm Demo India Pvt Ltd |
| Invoice | NOVA-1042: USD 4,200 billed, contract rate USD 3,600 |

---

## 3. Glossary

| Term | Meaning |
| --- | --- |
| Safe | Smart-contract multisig wallet; executes only with enough owner signatures |
| EIP-712 | Standard for signing typed data; used for Safe transactions |
| SIWE / EIP-4361 | "Sign-In with Ethereum" message format; used to prove a vendor controls a wallet |
| Relayer | A gas-only account that submits a fully signed Safe transaction |
| Confirmation | A block built on top of the block containing a transaction |
| Reorg | The chain replaces recent blocks; a transfer seen earlier may disappear |
| Merkle root | One hash that commits to many records; a short proof shows one record is included |
| Anchor | Writing a Merkle root to a public chain to fix it in time |
| UBO | Ultimate beneficial owner (25% or more) |
| VDA | Virtual digital asset (Indian tax term for crypto, including stablecoins) |
| Rule pack | A set of withholding rules for one jurisdiction |
| Functional currency | The currency a tenant keeps its books in (USD for the demo tenant) |
