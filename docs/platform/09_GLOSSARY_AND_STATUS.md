# Onsumm Platform — Glossary, Feature Status & Decisions

| | |
|---|---|
| **Document** | 09 — Glossary & Status |
| **As of** | 2026-10-31 (commit `6e1278f`) |
| **Related** | [00 Index](README.md) |

---

## 1. Glossary

| Term | Meaning |
|---|---|
| **AP** | Accounts payable: bills the tenant owes vendors |
| **AR** | Accounts receivable: invoices the tenant raises |
| **Bill** | An AP record in `fact_invoices` with `invoice_type='AP'` |
| **Tenant** | A customer organisation (`dim_tenants`) |
| **Vendor / supplier** | A party the tenant pays (`dim_suppliers`) |
| **POC** | Vendor point of contact (`supplier_contacts`); `agent_enabled` makes the mailbox agent watch them |
| **Approval chain** | Ordered steps (role, assignees) a bill must pass; per department |
| **Send back** | Return a bill to an earlier step or `NEEDS_REVISION` |
| **Step 99** | `bill_approval_chain` step reserved for payment events |
| **KYB** | Know Your Business: GSTIN, PAN, bank checks (India) or entity + UBO checks (crypto) |
| **GSTIN** | 15-character Indian GST registration number |
| **PAN** | Indian tax id (characters 3–12 of the GSTIN) |
| **Penny-less verification** | Bank account check without sending money |
| **TDS** | Tax Deducted at Source (Indian withholding on vendor payments) |
| **s.195 / DTAA** | Indian withholding on non-resident payments / treaty rates (needs TRC + Form 10F) |
| **VDA / s.194S** | Virtual Digital Asset and its 1% TDS rule |
| **IMPS** | Indian instant interbank transfer rail |
| **Lendix** | Payment gateway that fronts ICICI / AU Bank IMPS; also hosts Nova |
| **`tran_ref_no`** | Unique per-payment reference; reused on retries |
| **OTP** | 6-digit one-time code; payment OTP 120 s / 5 attempts |
| **Override** | Admin bypass of the verified-beneficiary rule, with reason, logged |
| **Remitter account** | Tenant bank account paying out (`remitter_accounts`) |
| **USDC** | USD stablecoin (6 decimals); units = amount × 10^6 |
| **Base / Base Sepolia** | Ethereum L2 / its testnet (chain id 84532) |
| **Safe** | Smart-contract multisig wallet; payouts need `threshold` owner signatures |
| **Owner / threshold** | Safe signers / number of signatures required |
| **Nonce (Safe)** | Sequence number of Safe transactions; strict order |
| **MultiSend** | Safe helper that batches transfers (vendor net + tax leg) |
| **Relayer** | Onsumm-run EOA that pays gas to submit a signed Safe tx; holds no client funds |
| **SIWE / EIP-4361** | Sign-In With Ethereum message used to prove wallet ownership |
| **EIP-712** | Typed-data signing standard used for Safe transactions |
| **Cooling period** | Wait (default 24 h) before a newly proved wallet can be approved |
| **Screening** | Sanctions check of a wallet address or name |
| **Treasury wallet** | Tenant's paying Safe (`role=TREASURY`); `TAX` wallet receives withheld tax |
| **Rail** | Payment method: `IMPS` or `USDC` |
| **Rail policy** | Per-tenant crypto rules: chains, tokens, limits, cooling, dual control |
| **FX snapshot** | Frozen exchange rates used for one payout |
| **Withholding pack** | Rule set computing crypto withholding (`IN-SERVICES`, `IN-195`, `IN-VDA`, `SG-DEMO`) |
| **Chain transfer** | A USDC `Transfer` log stored in `chain_transfers` |
| **Reorg** | Chain reorganisation; watcher rewinds and re-reads |
| **Hash chain** | Each audit row's hash includes the previous row's hash |
| **Merkle root / anchor** | Single hash summarising many audit rows, written on-chain |
| **Proof / share token** | Merkle path for one audit row; HMAC link for public verification |
| **Finding** | A Review Hub issue (`rh_findings`) with family, severity, audience, actions |
| **Adapter** | Deterministic detector producing findings |
| **Review agent** | LLM detector, claims verified against DB |
| **Suppression** | Rule hiding a finding type for a subject or tenant |
| **Fact Graph** | Graph of tenant, vendor, bill, contact and fact nodes with embeddings |
| **Nova** | Lendix usage ledger; Nova recon = invoice × usage × certified rate |
| **Medallion layers** | `raw_` → `stg_` → `fact_` table stages |
| **CDC worker** | Pipeline worker woken by Postgres `NOTIFY` |
| **Jurisdiction pack** | Country feature switch: `gst`, `pan`, `tds_india`, `bank_india`, `imps`, `msme` |
| **Service / package** | Licensable feature / bundle (`starter`, `growth`, `enterprise`) |
| **Impersonation** | Read-only support session into a tenant |

---

## 2. Feature status

| Area | Feature | Status |
|---|---|---|
| Capture | Upload + GPT-4o OCR + Sarvam fallback | Live |
| Capture | Mailbox agent drafts (Gmail) | Live |
| Capture | ERP intake (Apideck) | Built |
| Capture | WhatsApp capture | Planned (design docs) |
| Verify | GSTIN / PAN verification worker | Live |
| Verify | Bank verification worker | **Disabled** (NOT NULL bug); on-demand verify works |
| Verify | Dedup, category enrichment, TDS workers | Live |
| Review | Review Hub, 17 adapters | Live |
| Review | LLM review agent + comm extraction | Live |
| Approve | Configurable chain, email links, bulk | Live |
| Pay | IMPS via Lendix/ICICI, OTP, bulk, override, sweeper, receipts | Live |
| Pay | AU Bank secondary | Available, no auto-fallback |
| Pay | USDC via Safe on Base Sepolia | Live (testnet) |
| Pay | Demo settlement mode | Live |
| Pay | Partner custody | Stub |
| Pay | Mainnet, fiat on/off-ramp | Out of scope |
| Tax | TDS engine | Live |
| Tax | Crypto withholding packs | Live |
| Tax | Withholding remittances | Table only |
| FX | Rates, alerts, AI briefings | Live |
| FX | Live FX source for crypto | Not built (`fixed` only) |
| Books | Tally agent push | Live |
| Books | Tally server pull / sync | **Disabled** |
| Books | Crypto ledger vouchers | Live |
| Audit | Hash chain + daily anchor + public proof | Live |
| AI | Agent Hub, Fact Graph | Live |
| Recon | Nova service | Live (dev) |
| Platform | Licensing, packages, impersonation | Live (`LICENSE_ENFORCEMENT=log`) |
| International | Country-aware Add Vendor, tenant currency, jurisdiction rules | Live |

---

## 3. Architecture decision log

| # | Date | Decision | Reason |
|---|---|---|---|
| 1 | — | Postgres `LISTEN/NOTIFY` as event bus | No broker to run; same-transaction triggers |
| 2 | — | Medallion tables `raw_ → stg_ → fact_` | Traceability from source to fact |
| 3 | — | Split `main.py` (8,822 lines) into 44 routers; routers never import routers | Reviewable tenant scoping; shared code in `core/` |
| 4 | — | 404 not 403 for foreign rows | No existence oracle |
| 5 | — | Lendix/ICICI as single INR rail, no auto bank fallback | Predictable reconciliation |
| 6 | — | Polling instead of payment webhooks | Lendix sends none |
| 7 | — | Nova recon as separate service with its own credential resolver | Independent deploy and scale |
| 8 | — | Review Hub fixed render shapes | New adapters need no UI work |
| 9 | 2026-10 | USDC rail beside IMPS, not replacing it | Foreign tenants only; India FEMA/VDA uncertainty |
| 10 | 2026-10 | Client-owned Safe; Onsumm never holds keys | Not a custodian; maker-checker by threshold |
| 11 | 2026-10 | Payout wallet only from verified vendor master | Blocks invoice-phishing |
| 12 | 2026-10 | Freeze FX at prepare | One rate for withholding, books, INR reference |
| 13 | 2026-10 | Anchor daily Merkle root; calldata mode by default | Cheap, contract optional |
| 14 | 2026-10 | Jurisdiction packs instead of India-only code paths | One codebase for India and international |

---

## 4. Open questions

| # | Question | Owner |
|---|---|---|
| 1 | Which database backs each environment: RDS or self-hosted Postgres on EC2? | Ops |
| 2 | When does dev get its own DB and secrets? | Ops |
| 3 | When does `LICENSE_ENFORCEMENT` move to `enforce`? | Product |
| 4 | Which sanctions provider replaces the static list? | Compliance |
| 5 | Mainnet readiness criteria for USDC (legal, audit, limits)? | Leadership + counsel |
| 6 | Fix plan for approval role-from-body and missing server role checks? | Engineering |
| 7 | Re-enable bank verification worker after the NOT NULL fix? | Engineering |
