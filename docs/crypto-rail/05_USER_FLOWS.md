# Crypto Rail — User Flows

Status: hackathon baseline, as of 29 Oct 2026 · Names follow [00_DECISIONS.md](00_DECISIONS.md)

This document shows every user-facing flow of the crypto rail, one section per flow. Each flow has a step diagram, a hand-off diagram where people or systems change, and the screens it touches.

Contents

1. [Foreign client onboarding](#1-foreign-client-onboarding)
2. [Vendor wallet onboarding](#2-vendor-wallet-onboarding)
3. [Wallet change](#3-wallet-change)
4. [Bill capture to approval](#4-bill-capture-to-approval)
5. [Payout by approvers](#5-payout-by-approvers)
6. [On-chain reconciliation and books](#6-on-chain-reconciliation-and-books)
7. [Withholding (Crypto TDS)](#7-withholding-crypto-tds)
8. [Auditor verification](#8-auditor-verification)
9. [Demo run-of-show](#9-demo-run-of-show)

Route notes: new pages live under `frontend/src/pages/crypto/`. Existing pages (`PaymentPage.tsx`, `PaymentHubPage.tsx`, `SettingsPage.tsx`, vendor pages) gain new cards or tabs. Routes are added to `frontend/src/routes/paths.ts` and wrapped in `RequireService` with the licence keys `crypto_payouts`, `crypto_tds` or `audit_anchor`.

---

## 1. Foreign client onboarding

Actor: Asha, founder of Lumen Labs Pte Ltd (Singapore). Goal: crypto rail enabled for the tenant.

```mermaid
flowchart TD
  A["Start: tenant admin opens International onboarding"] --> B["Entity profile<br/>legal name, country, registration no.,<br/>tax residency, functional currency"]
  B --> C{"Country is India?<br/>is_resident_india"}
  C -- "Yes" --> C1["Crypto rail stays disabled<br/>CRYPTO_DISABLED_RESIDENT_INDIA"]
  C1 --> C2["Bank rail IMPS only<br/>India packs: GST, TDS"]
  C -- "No" --> D["Upload KYB documents<br/>incorporation, registry extract"]
  D --> E["Add UBOs (25% or more)"]
  E --> F["StaticListScreening on each UBO"]
  F --> G{"Any screening HIT?"}
  G -- "Yes" --> G1["kyb_status = REJECTED<br/>Onsumm ops reviews"]
  G -- "No" --> H["Onsumm ops: manual KYB review screen"]
  H --> I{"Approved?"}
  I -- "No" --> I1["Request more documents"]
  I1 --> D
  I -- "Yes" --> J["kyb_status = APPROVED"]
  J --> K["Connect Safe<br/>paste Safe address on Base Sepolia"]
  K --> L["Backend reads owners and threshold on-chain<br/>treasury_wallets row, custody SAFE"]
  L --> M["Map Safe owners to Onsumm users<br/>Asha, Ben, Chen, threshold 2 of 3"]
  M --> N["Rail policy<br/>chain base-sepolia, token USDC,<br/>limits, cooling_minutes, test transfer"]
  N --> O["crypto_enabled = true"]
```

The India branch ends early by design: `PolicyEngine` will refuse crypto for resident tenants however the policy is set. Owners and threshold are read from the chain, never typed in, so Onsumm's view cannot drift from the Safe.

```mermaid
sequenceDiagram
  participant Asha as Asha (tenant admin)
  participant UI as Onsumm UI
  participant API as Onsumm API
  participant Ops as Onsumm ops
  participant Chain as Base Sepolia
  Asha->>UI: Fill entity profile and UBOs
  UI->>API: POST /api/onboarding/kyb
  UI->>API: POST /api/onboarding/ubos
  API->>API: Screen UBOs, write tenant_ubos
  Ops->>API: Approve KYB
  API-->>UI: kyb_status APPROVED
  Asha->>UI: Paste Safe address
  UI->>API: POST /api/treasury/wallets
  API->>Chain: getOwners() and getThreshold()
  Chain-->>API: 3 owners, threshold 2
  API-->>UI: Treasury wallet saved
  Asha->>UI: Set rail policy
  UI->>API: PUT /api/tenant/rail-policy
  API-->>UI: crypto_enabled true
```

KYB is a manual review screen for the hackathon; a KYB provider replaces it later. The Safe read is the only chain call in this flow.

| Screen | Route (`frontend/src`) | Key elements |
| --- | --- | --- |
| Entity profile | `pages/crypto/onboarding/EntityProfile.tsx` at `/onboarding/international` | Country, registration no., tax residency, functional currency, India-resident warning |
| KYB documents | `pages/crypto/onboarding/KybDocuments.tsx` | Upload list, status chip `kyb_status` |
| UBOs | `pages/crypto/onboarding/Ubos.tsx` | Name, country, ownership %, screening result per row |
| Ops KYB review | `admin/KybReview.tsx` at `/admin/kyb` | Documents viewer, approve or request more |
| Connect Safe | `pages/crypto/onboarding/ConnectSafe.tsx` | Safe address input, owners and threshold read back, owner-to-user mapping |
| Rail policy | `SettingsPage.tsx`, tab *Rail policy* | Chains, tokens, per-tx and daily limits, `cooling_minutes`, `require_test_transfer` |

---

## 2. Vendor wallet onboarding

Actors: Ben (finance controller at Lumen), Nova Design LLC (vendor). Goal: a vendor wallet in status `ACTIVE`.

```mermaid
flowchart TD
  A["Ben opens vendor Nova Design LLC"] --> B["Send wallet invite<br/>email or WhatsApp, signed link"]
  B --> C["Vendor opens portal link<br/>token valid 72 hours"]
  C --> D["Upload tax documents<br/>US vendor: W-8BEN-E"]
  D --> E["Connect wallet<br/>MetaMask or Rabby"]
  E --> F{"Wallet on Base Sepolia<br/>and address valid?"}
  F -- "No" --> F1["Ask vendor to switch network"]
  F1 --> E
  F -- "Yes" --> G["Vendor signs SIWE message"]
  G --> H["SiweVerifier checks signature,<br/>nonce, domain, expiry"]
  H --> I{"Signature valid?"}
  I -- "No" --> I1["Show error, nothing saved"]
  I -- "Yes" --> J["supplier_wallets row<br/>status PENDING_PROOF to COOLING"]
  J --> K["Screen address"]
  K --> L{"Screening result"}
  L -- "HIT" --> L1["status FROZEN<br/>SCREENING_HIT finding"]
  L -- "CLEAR" --> M["Cooling countdown<br/>cooling_minutes, demo 2 min"]
  M --> N["Ben approves wallet in Onsumm"]
  N --> O["status ACTIVE<br/>vendor can be paid"]
```

The vendor proves control by signing, not by sending funds. Approval is only allowed after cooling ends, so a stolen invite link alone cannot route a payment.

```mermaid
sequenceDiagram
  participant Ben as Ben (controller)
  participant API as Onsumm API
  participant Vendor as Nova Design (portal)
  participant Wallet as Vendor wallet
  participant Scr as Screening
  Ben->>API: POST /api/vendors/{id}/wallet-invites
  API-->>Vendor: Signed portal link by email or WhatsApp
  Vendor->>API: Upload W-8BEN-E (tax_docs)
  Vendor->>API: GET /api/vendor-portal/{token}/wallet/challenge
  API-->>Vendor: SIWE message with nonce
  Vendor->>Wallet: Sign message
  Wallet-->>Vendor: Signature
  Vendor->>API: POST /api/vendor-portal/{token}/wallet/verify
  API->>API: SiweVerifier recovers address
  API->>Scr: screen(chain, address)
  Scr-->>API: CLEAR
  API-->>Vendor: Wallet received, cooling started
  Note over API: cooling_until reached
  Ben->>API: POST /api/vendors/{id}/wallets/{wid}/approve
  API-->>Ben: Wallet ACTIVE
```

The portal link carries a signed token that expires after 72 hours; an expired or forwarded link is handled in [06_EDGE_CASES.md](06_EDGE_CASES.md) (EC-52). Tax documents depend on the payer. A US vendor gives a W-8BEN-E to a foreign payer; TRC and Form 10F matter only when the payer is Indian.

| Screen | Route | Key elements |
| --- | --- | --- |
| Vendor wallets card | Vendor detail page, card *Wallets* | Address with checksum, chain, status chip, cooling countdown, Approve button |
| Send invite | Modal on the vendor page | Channel (email or WhatsApp), contact picker |
| Vendor portal: tax docs | `pages/crypto/vendor-portal/TaxDocs.tsx` at `/vendor-portal/:token` | Upload W-8BEN-E, or TRC and 10F where relevant |
| Vendor portal: connect wallet | `pages/crypto/vendor-portal/ConnectWallet.tsx` | Connect button (wagmi injected), network check, Sign button |
| Vendor portal: done | same | "Wallet received, under review" with cooling time |

---

## 3. Wallet change

Actors: Nova Design (vendor), Ben (approver). Goal: replace a wallet safely. This is the flow attackers target most.

```mermaid
flowchart TD
  A["Vendor requests new wallet<br/>via portal link"] --> B["Old wallet set FROZEN<br/>WALLET_CHANGED finding"]
  B --> C["No payouts to this vendor<br/>until resolved"]
  C --> D["Vendor signs SIWE with new wallet"]
  D --> E["Screen new address"]
  E --> F{"CLEAR?"}
  F -- "No" --> F1["New wallet FROZEN<br/>SCREENING_HIT, ops review"]
  F -- "Yes" --> G["Notify old contact on record<br/>email to the previously known address"]
  G --> H["Cooling period starts"]
  H --> I{"Old contact disputes?"}
  I -- "Yes" --> I1["Keep both FROZEN<br/>Ben calls vendor out of band"]
  I -- "No" --> J["Cooling ends"]
  J --> K["Ben approves new wallet"]
  K --> L["New wallet ACTIVE<br/>old wallet REVOKED, replaced_by set"]
```

The old wallet freezes the moment a change is requested. The notice goes to the contact Onsumm already had, never to one supplied in the change request.

```mermaid
sequenceDiagram
  participant Vendor as Vendor (or attacker)
  participant API as Onsumm API
  participant Old as Old vendor contact
  participant Ben as Ben (approver)
  Vendor->>API: Request wallet change and sign SIWE with new wallet
  API->>API: Old wallet FROZEN, new wallet COOLING
  API-->>Old: Your payout wallet is changing, reply if not you
  API-->>Ben: Review Hub finding WALLET_CHANGED
  alt Old contact disputes
    Old->>API: Not me
    API-->>Ben: Keep frozen, call vendor
  else No dispute and cooling ended
    Ben->>API: Approve new wallet
    API-->>Vendor: New wallet ACTIVE
  end
```

| Screen | Route | Key elements |
| --- | --- | --- |
| Vendor portal: change wallet | `pages/crypto/vendor-portal/ChangeWallet.tsx` | Reason, connect new wallet, sign |
| Review Hub finding | existing Review Hub page | `WALLET_CHANGED`, old and new address side by side |
| Vendor wallets card | Vendor detail page | Old (FROZEN) and new (COOLING) rows, Approve, Revoke |

---

## 4. Bill capture to approval

Actors: AP clerk, approvers. Demo invoice NOVA-1042: USD 4,200 billed against a contract rate of USD 3,600.

```mermaid
flowchart TD
  A["Invoice NOVA-1042 arrives"] --> B{"Channel"}
  B -- "Email (exists)" --> C["Mailbox watcher picks it up"]
  B -- "WhatsApp (stretch)" --> C2["Twilio sandbox webhook"]
  B -- "Upload" --> C3["Upload page"]
  C --> D["OCR extracts vendor, amount, currency,<br/>chain, wallet address if printed"]
  C2 --> D
  C3 --> D
  D --> E{"Printed wallet differs<br/>from vendor master?"}
  E -- "Yes" --> E1["WALLET_MISMATCH finding<br/>printed address never used"]
  E -- "No or none printed" --> F["ContractRateCheck"]
  E1 --> F
  F --> G{"Amount above<br/>vendor_contract_rates?"}
  G -- "Yes, 4,200 vs 3,600" --> H["OVERBILLED_VS_CONTRACT<br/>bill held"]
  H --> I["AP clerk corrects payable to 3,600<br/>adds vendor note, short-pay reason"]
  G -- "No" --> J["Approval chain"]
  I --> J
  J --> K["PENDING_APPROVER to PENDING_ACCOUNTANT to PENDING_FC"]
  K --> L["Approved, ready for payout"]
```

A wallet printed on an invoice is only compared, never paid. The overbilling hold happens before approval, so approvers see the corrected amount.

```mermaid
sequenceDiagram
  participant Vendor as Nova Design
  participant Intake as Email intake
  participant OCR as OCR pipeline
  participant Recon as ContractRateCheck
  participant Clerk as AP clerk
  participant Chain as Approval chain
  Vendor->>Intake: Invoice NOVA-1042, USD 4,200
  Intake->>OCR: Document
  OCR->>Recon: Extracted bill
  Recon-->>Clerk: OVERBILLED_VS_CONTRACT, 600 over
  Clerk->>Chain: Corrected to USD 3,600 with note
  Chain-->>Clerk: Approved by all steps
```

| Screen | Route | Key elements |
| --- | --- | --- |
| Bill detail | existing bills page | Currency USD, findings banner, corrected payable |
| Review Hub | existing | `OVERBILLED_VS_CONTRACT`, `WALLET_MISMATCH` |
| Contract rates | Vendor detail page, card *Contract rates* | Item, unit price, currency, validity |

---

## 5. Payout by approvers

Actors: Ben (prepares and releases), Asha and Ben (Safe owners). Goal: USD 3,600 paid in USDC.

```mermaid
flowchart TD
  A["Ben selects approved bill NOVA-1042"] --> B["RailRouter: vendor preferred_rail CRYPTO"]
  B --> C["PolicyEngine checks<br/>resident, chain, token, wallet, limits, balance"]
  C --> D{"All checks pass?"}
  D -- "No" --> D1["Show refusal code<br/>e.g. WALLET_COOLING"]
  D -- "Yes" --> E["Preview<br/>gross 3,600, withholding 0,<br/>network fee, net 3,600 USDC,<br/>destination, Base Sepolia"]
  E --> F["Ben enters OTP<br/>status PENDING_OTP to PENDING"]
  F --> G["Safe proposal built<br/>sub_status PROPOSED"]
  G --> H["Asha signs in browser wallet<br/>PARTIALLY_SIGNED"]
  H --> I["Ben signs<br/>READY, threshold 2 of 3"]
  I --> J["GasRelayer submits execTransaction<br/>SUBMITTED"]
  J --> K["Block included<br/>CONFIRMING"]
  K --> L["Confirmations reached<br/>SUCCESS and CONFIRMED"]
  L --> M["Receipt with explorer link<br/>remittance advice to vendor"]
```

Two gates protect the money: Onsumm's OTP and the Safe threshold. The relayer only pays gas; it cannot move USDC without the owners' signatures.

```mermaid
sequenceDiagram
  participant Ben as Ben
  participant Asha as Asha
  participant UI as Onsumm UI
  participant API as Onsumm API
  participant Relay as GasRelayer
  participant Safe as Lumen Safe
  participant Chain as Base Sepolia
  Ben->>UI: Pay NOVA-1042 with USDC
  UI->>API: POST /api/bills/{id}/disbursements rail USDC
  API-->>UI: Quote, withholding, policy OK
  Ben->>UI: Enter OTP
  UI->>API: POST /api/disbursements/{id}/verify-otp
  API-->>UI: PROPOSED
  Asha->>UI: Open payout, sign
  UI->>API: GET /api/disbursements/{id}/safe-tx
  UI->>API: POST /api/disbursements/{id}/signatures
  Ben->>UI: Sign
  UI->>API: POST /api/disbursements/{id}/signatures
  API->>Relay: Threshold met, READY
  Relay->>Safe: execTransaction with 2 signatures
  Safe->>Chain: USDC transfer 3,600
  Chain-->>API: Receipt, then confirmations
  API-->>UI: SUCCESS, CONFIRMED, explorer link
```

| Screen | Route | Key elements |
| --- | --- | --- |
| Bill select | `pages/payment/BillSelectStep` | Rail choice IMPS or USDC, disabled with reason when refused |
| Beneficiary | `pages/payment/BeneficiaryCard` | Wallet address with checksum, chain, status ACTIVE |
| Review | `pages/payment/ReviewStep` | Gross, withholding, network fee, net USDC, FX snapshot time |
| OTP | `pages/payment/OtpStep` | Existing OTP step |
| Safe signing | `pages/crypto/payout/CryptoPayoutCard.tsx` | Signers list with ticks, Sign button, threshold progress |
| Processing | `pages/payment/PaymentProcessing` | Live `sub_status`, confirmations count |
| Receipt | `pages/payment/ReceiptStep` | Tx hash, explorer link, download remittance |

---

## 6. On-chain reconciliation and books

Actors: system workers, Ben (controller). Goal: bill marked *Paid and Reconciled* and posted to the ledger.

```mermaid
flowchart TD
  A["ChainWatcher polls USDC Transfer logs<br/>from chain_watch_cursors.last_block"] --> B["Write chain_transfers rows"]
  B --> C["OnchainMatcher"]
  C --> D{"Direction OUT and<br/>tx_hash matches a payout?"}
  D -- "Yes" --> E{"Amount, token, destination match?<br/>both legs if withholding, PAYOUT and TAX"}
  E -- "Yes" --> F["status MATCHED<br/>bill Paid and Reconciled"]
  E -- "No" --> E1["MISMATCH<br/>AMOUNT_MISMATCH finding"]
  D -- "OUT, no match" --> D1["UNMATCHED<br/>UNMATCHED_OUTBOUND_TRANSFER, critical"]
  D -- "IN" --> D2{"Matches a receivable?"}
  D2 -- "Yes" --> D3["RECEIVABLE matched"]
  D2 -- "No" --> D4["UNMATCHED_INBOUND_TRANSFER"]
  F --> G["LedgerPoster"]
  G --> H["TallyPaymentVoucherBuilder<br/>tx hash and explorer link in narration"]
  H --> I["ledger_postings row, status POSTED"]
  I --> J["Ben views daily wallet statement"]
  J --> K{"Statement close equals<br/>on-chain balance?"}
  K -- "Yes" --> K1["Day reconciled"]
  K -- "No" --> K2["UNRECONCILED_DAY finding"]
```

The chain is the source of truth for settlement. Anything leaving the treasury that Onsumm did not propose is a critical alert.

```mermaid
sequenceDiagram
  participant W as ChainWatcher
  participant M as OnchainMatcher
  participant DB as Postgres
  participant L as LedgerPoster
  participant T as Tally
  participant Ben as Ben
  W->>DB: Insert chain_transfers, move cursor
  M->>DB: Read new transfers
  M->>DB: Match to crypto_disbursement_details by tx_hash
  M->>DB: Bill paid, transfer MATCHED
  M->>L: Payout confirmed
  L->>T: Payment voucher XML
  T-->>L: Voucher reference
  L->>DB: ledger_postings POSTED
  Ben->>DB: GET /api/onchain/statements/{date}
```

| Screen | Route | Key elements |
| --- | --- | --- |
| On-chain tab | `PaymentHubPage.tsx`, tab *On-chain* | Transfers list, direction, match status, explorer link |
| Daily statement | `pages/crypto/onchain/Statement.tsx` | Opening, ins, outs, closing, on-chain balance, reconciled flag |
| Ledger postings | Bill detail, *Books* panel | Voucher reference, status, retry button |

---

## 7. Withholding (Crypto TDS)

Two cases: the demo tenant (Singapore, `SG-DEMO` pack at 0%) and the guardrail case (India-resident tenant).

```mermaid
flowchart TD
  A["Payout preview requests withholding<br/>GET /api/withholding/preview"] --> B{"Tenant is_resident_india?"}
  B -- "Yes" --> B1["USDC refused<br/>CRYPTO_DISABLED_RESIDENT_INDIA"]
  B1 --> B2["Bank rail preview<br/>IndiaNonResidentPack, Section 195"]
  B2 --> B3["Pack IN-195<br/>rate, DTAA option if TRC and 10F on file"]
  B -- "No" --> C["WithholdingEngine picks pack<br/>SG-DEMO ConfigurablePack"]
  C --> D["Rule for vendor category: 0%<br/>requires_signoff true"]
  D --> E{"Adviser sign-off recorded?"}
  E -- "No" --> E1["WITHHOLDING_SIGNOFF_REQUIRED<br/>payout can proceed, flagged"]
  E -- "Yes" --> F["Withholding final"]
  E1 --> F
  F --> G{"Withholding above zero?"}
  G -- "No (demo)" --> G1["Single USDC transfer to vendor"]
  G -- "Yes" --> H["One Safe transaction via MultiSend<br/>two USDC transfers: net to vendor,<br/>withheld amount to TAX treasury wallet"]
  H --> H2["OnchainMatcher matches both legs<br/>match_type PAYOUT and TAX"]
  H2 --> I["crypto_withholdings rows"]
  I --> J["WithholdingRemittance before due date<br/>withholding_remittances"]
  J --> K["Certificate to vendor"]
```

Indian sections appear only for an Indian payer, and then only on the bank rail. For the Singapore demo the engine records a 0% rule that a tax adviser must sign off.

Rule pack codes: `SG-DEMO` (demo client pack), `IN-SERVICES` (today's TDS engine), `IN-195` (non-resident payments), `IN-VDA` (Section 194S / s.393(1)). When withholding is above zero the payout is still **one** Safe transaction: a MultiSend with two USDC transfers, so the vendor and the tax leg settle together or not at all.

```mermaid
sequenceDiagram
  participant UI as Onsumm UI
  participant API as Onsumm API
  participant Pol as PolicyEngine
  participant WH as WithholdingEngine
  UI->>API: Pay foreign vendor with USDC
  API->>Pol: check(tenant, rail USDC)
  alt India-resident tenant
    Pol-->>API: CRYPTO_DISABLED_RESIDENT_INDIA
    API->>WH: preview on bank rail
    WH-->>API: Pack IN-195, Section 195 amount
    API-->>UI: USDC blocked, Section 195 shown for IMPS
  else Lumen Labs (Singapore)
    Pol-->>API: OK
    API->>WH: preview USDC
    WH-->>API: SG-DEMO 0%, requires_signoff
    API-->>UI: Withholding 0, sign-off flag
  end
```

| Screen | Route | Key elements |
| --- | --- | --- |
| Withholding rules | `SettingsPage.tsx`, tab *Withholding rules* | Pack, rule per vendor category, rate, adviser sign-off |
| Payout review | `pages/payment/ReviewStep` | Withholding line with pack and rule code |
| Remittances | `pages/crypto/withholding/Remittances.tsx` | Period, total, reference, certificate download |

---

## 8. Auditor verification

Actors: Ben (controller), external auditor. Goal: prove an approval record existed, unchanged, at anchor time.

```mermaid
flowchart TD
  A["Ben opens an audit record"] --> B["GET /api/audit/proof/{log_id}"]
  B --> C["Proof bundle JSON<br/>record, salt, row hash, Merkle paths, anchor tx"]
  C --> D{"Share how?"}
  D -- "Download" --> E["Ben sends JSON file"]
  D -- "Share link" --> F["Link with signed token"]
  E --> G["Auditor opens /verify"]
  F --> G
  G --> H["Browser recomputes SHA-256<br/>row hash, leaf, tenant root, top root"]
  H --> I["Fetch anchor event from Base Sepolia"]
  I --> J{"Recomputed root equals<br/>on-chain root?"}
  J -- "Yes" --> K["Verified<br/>record existed at anchor time"]
  J -- "No" --> L["Tampered or wrong bundle<br/>shows which step failed"]
```

Verification needs no Onsumm login and runs in the auditor's browser. Bundles are shared deliberately; nobody can look records up by bill ID.

| Screen | Route | Key elements |
| --- | --- | --- |
| Audit record | Bill detail, *Audit* panel | Download proof, copy share link |
| Public verify | `pages/crypto/VerifyProof.tsx` at `/verify` | Drop bundle or open link, step-by-step check, explorer link |

---

## 9. Demo run-of-show

Nine steps for Saturday 31 Oct. Each box names the screen used.

```mermaid
flowchart TD
  S1["Step 1 - Onboard Lumen Labs<br/>EntityProfile, Ubos, ConnectSafe, Rail policy"] --> S2["Step 2 - Onboard Nova Design<br/>invite, vendor portal SIWE, cooling 2 min, approve"]
  S2 --> S3["Step 3 - Invoice capture<br/>email intake or WhatsApp stretch, bill detail"]
  S3 --> S4["Step 4 - Overbilling flag<br/>OVERBILLED_VS_CONTRACT, correct to 3,600"]
  S4 --> S5["Step 5 - Approve and pay<br/>OTP, Asha signs, Ben signs, relayer executes"]
  S5 --> S6["Step 6 - On-chain recon<br/>On-chain tab, Paid and Reconciled"]
  S6 --> S7["Step 7 - Tax and books<br/>SG-DEMO 0%, USD and INR reference, Tally voucher"]
  S7 --> S8["Step 8 - Audit anchor and verify<br/>POST /api/audit/anchor, /verify page"]
  S8 --> S9["Step 9 - India guardrail moment<br/>switch tenant, USDC blocked, Section 195 on bank rail"]
```

Step 2 runs the 2-minute cooling countdown while steps 3 and 4 are shown, so no time is lost. Step 9 closes the pitch with compliance, not a feature.

| Step | Screen | What the judges see |
| --- | --- | --- |
| 1 | Onboarding pages | Safe owners and threshold read live from Base Sepolia |
| 2 | Vendor portal | Wallet signature instead of a penny drop |
| 3 | Bill detail | USD invoice parsed |
| 4 | Review Hub | USD 600 overbilling caught before approval |
| 5 | CryptoPayoutCard | Two signatures, then settlement in seconds |
| 6 | On-chain tab | Tx hash matched to bill automatically |
| 7 | Books panel | Voucher with tx hash; INR reference value stored |
| 8 | `/verify` | Green check against the on-chain root |
| 9 | Payment page (India tenant) | Refusal code and Section 195 preview |
