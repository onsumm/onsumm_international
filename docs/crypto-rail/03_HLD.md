# Crypto Rail — High-Level Design

Status: hackathon design, as of 29 Oct 2026 · Names follow [00_DECISIONS.md](00_DECISIONS.md)

Contents

1. [Design principles](#1-design-principles)
2. [System context](#2-system-context)
3. [Containers and components](#3-containers-and-components)
4. [Bill-to-books pipeline](#4-bill-to-books-pipeline)
5. [Payout sequence](#5-payout-sequence)
6. [Audit anchoring data flow](#6-audit-anchoring-data-flow)
7. [Deployment view](#7-deployment-view)
8. [Trust boundaries and key custody](#8-trust-boundaries-and-key-custody)
9. [Non-functional requirements](#9-non-functional-requirements)
10. [Technology choices](#10-technology-choices)
11. [How it plugs into existing modules](#11-how-it-plugs-into-existing-modules)

---

## 1. Design principles

| # | Principle | Source |
| --- | --- | --- |
| P1 | Onsumm never holds a key that can move client funds. The client's Safe signs; a gas-only relayer submits. | D3 |
| P2 | The crypto rail exists only for tenants incorporated outside India. `PolicyEngine` hard-blocks India-resident tenants. | D2 |
| P3 | Pay only to a wallet in the verified vendor master. An address printed on an invoice is compared, never used. | D9 |
| P4 | The chain is the source of truth for settlement. A payout is `SUCCESS` only after `OnchainMatcher` sees it. | D9 |
| P5 | Every material action is written to `wf_audit_log`, hash-chained, and anchored daily on-chain. | D8 |
| P6 | Proofs are verified from a proof bundle, never by a raw bill ID. Leaves are salted per tenant. | D8 |
| P7 | Crypto is a second rail beside IMPS. Existing statuses, screens and the sweeper keep working. | D6, 2.2 |
| P8 | Safety defaults are policy, not code: cooling period, re-screening, test transfer, limits. | D9 |

---

## 2. System context

```mermaid
flowchart LR
  APR["Client approvers<br/>Safe owners"]
  CLERK["AP clerk"]
  VEN["Vendor<br/>Nova Design LLC"]
  AUD["Auditor"]

  ONS["Onsumm platform"]

  SAFE["Client Safe<br/>2 of 3 on Base Sepolia"]
  USDC["USDC token contract"]
  ANCH["OnsummAnchor contract"]
  RELAY["Gas relayer<br/>gas-only key"]
  RPC["Base Sepolia RPC<br/>public or Alchemy"]
  SCR["Screening list<br/>static file"]
  TALLY["Tally<br/>payment vouchers"]
  BANK["IMPS banks via Lendix<br/>ICICI, HDFC, AU"]

  CLERK -->|"upload or email bills"| ONS
  APR -->|"approve, OTP, sign Safe tx"| ONS
  VEN -->|"prove wallet with SIWE"| ONS
  AUD -->|"verify proof bundle"| ONS
  ONS -->|"collect signatures"| APR
  ONS -->|"signed Safe tx"| RELAY
  RELAY -->|"execTransaction"| SAFE
  SAFE -->|"USDC transfer"| USDC
  ONS -->|"read logs and receipts"| RPC
  RPC --- USDC
  ONS -->|"write Merkle root"| ANCH
  ONS -->|"screen address"| SCR
  ONS -->|"payment voucher XML"| TALLY
  ONS -->|"INR payouts, unchanged"| BANK
```

Onsumm sits in the middle but never signs a fund transfer. Money moves only from the client's Safe, after the owners sign.

---

## 3. Containers and components

```mermaid
flowchart TB
  subgraph FE["Frontend: React + Vite + wagmi"]
    FE_PAY["Payment flow pages"]
    FE_CRY["Crypto pages<br/>wallets, Safe signing, on-chain hub"]
    FE_VER["Public verify page"]
    FE_PORT["Vendor portal wallet page"]
  end

  subgraph API["FastAPI routers"]
    R_DISB["routers/disbursement.py"]
    R_WAL["Wallet and vendor-portal routers"]
    R_ONC["On-chain and proof routers"]
    R_ONB["Onboarding and rail-policy routers"]
  end

  subgraph DOM["Domain services"]
    D_ROUTER["RailRouter"]
    D_POL["PolicyEngine"]
    D_PAY["PayoutService"]
    D_WAL["WalletService + SiweVerifier"]
    D_FX["FxService"]
    D_WH["WithholdingEngine"]
    D_TDS["utils/tds_engine.py"]
    D_HASH["AuditHasher"]
  end

  subgraph WRK["Workers in the API process"]
    W_WATCH["ChainWatcher"]
    W_MATCH["OnchainMatcher"]
    W_SWEEP["Disbursement sweeper"]
    W_ANCH["AnchorService scheduler"]
    W_LEDG["LedgerPoster"]
  end

  subgraph INT["Integrations"]
    I_BANK["integrations/payments<br/>ImpsRail and bank adapters"]
    I_USDC["UsdcRail"]
    I_SAFE["SafeCustody + GasRelayer"]
    I_EVM["EvmChain"]
    I_SCR["StaticListScreening"]
  end

  DB[("Postgres onsumm-new-db")]

  subgraph SC["Contracts on Base Sepolia"]
    C_SAFE["Client Safe"]
    C_USDC["USDC"]
    C_ANCH["OnsummAnchor"]
  end

  FE_PAY --> R_DISB
  FE_CRY --> R_WAL
  FE_CRY --> R_ONC
  FE_PORT --> R_WAL
  FE_VER --> R_ONC
  R_ONB --> D_POL
  R_DISB --> D_ROUTER
  D_ROUTER --> D_POL
  D_ROUTER --> I_BANK
  D_ROUTER --> D_PAY
  D_PAY --> D_WH
  D_PAY --> D_FX
  D_PAY --> I_USDC
  D_WH --> D_TDS
  I_USDC --> I_SAFE
  R_WAL --> D_WAL
  D_WAL --> I_SCR
  I_SAFE --> C_SAFE
  W_WATCH --> I_EVM
  I_EVM --> C_USDC
  W_ANCH --> C_ANCH
  W_WATCH --> DB
  W_MATCH --> DB
  W_SWEEP --> DB
  W_LEDG --> DB
  D_PAY --> DB
  D_HASH --> DB

  classDef existing fill:#eef2f7,stroke:#8a96a8,color:#1b2430
  classDef new fill:#e3f4ea,stroke:#2f8f5b,color:#10301f
  class FE_PAY,R_DISB,D_TDS,W_SWEEP,I_BANK,DB existing
  class FE_CRY,FE_VER,FE_PORT,R_WAL,R_ONC,R_ONB,D_ROUTER,D_POL,D_PAY,D_WAL,D_FX,D_WH,D_HASH,W_WATCH,W_MATCH,W_ANCH,W_LEDG,I_USDC,I_SAFE,I_EVM,I_SCR,C_SAFE,C_USDC,C_ANCH new
```

Grey boxes exist today; green boxes are new. Note that the new work hangs off one existing entry point, `routers/disbursement.py`, through `RailRouter`.

---

## 4. Bill-to-books pipeline

```mermaid
flowchart LR
  CAP["Capture<br/>OCR, email intake"]
  VER["Verify<br/>wallet, screening, tax docs"]
  REC["Recon<br/>contract rate, duplicates"]
  TAX["Tax<br/>WithholdingEngine"]
  GATE["Human gate<br/>approval chain + OTP"]
  RR{"RailRouter"}
  IMPS["IMPS rail<br/>unchanged"]
  SAFEP["USDC via client Safe"]
  OREC["On-chain recon<br/>OnchainMatcher"]
  BOOKS["Books<br/>Tally payment voucher"]
  ANC["Audit anchor<br/>daily Merkle root"]

  CAP --> VER --> REC --> TAX --> GATE --> RR
  RR -->|"INR or India-resident tenant"| IMPS
  RR -->|"USDC, foreign tenant"| SAFEP
  SAFEP --> OREC --> BOOKS
  IMPS --> BOOKS
  BOOKS --> ANC

  classDef existing fill:#eef2f7,stroke:#8a96a8,color:#1b2430
  classDef extended fill:#fff4dc,stroke:#c48a12,color:#3a2a05
  classDef new fill:#e3f4ea,stroke:#2f8f5b,color:#10301f
  class CAP,GATE,IMPS existing
  class VER,REC,TAX extended
  class RR,SAFEP,OREC,BOOKS,ANC new
```

| Step | Today | Change |
| --- | --- | --- |
| Capture | OCR, email intake | Read `currency`, compare any invoice wallet (`WALLET_MISMATCH`); WhatsApp is a stretch goal |
| Verify | GSTIN, bank account | Add wallet active, screening clear, tax docs present |
| Recon | Duplicate checks | Add `ContractRateCheck` (`OVERBILLED_VS_CONTRACT`) and `DUPLICATE_ACROSS_RAILS` |
| Tax | India TDS engine | `WithholdingEngine` with rule packs; India engine becomes `IndiaServicesPack` |
| Human gate | Approval chain, OTP | Unchanged, then Safe owner signatures for USDC |
| RailRouter | Does not exist | New; enforces D2 |
| On-chain recon | Does not exist | New; chain is the source of truth |
| Books | No payment posting | New `TallyPaymentVoucherBuilder` |
| Audit anchor | Plain audit log | New hash chain and daily anchor |

Yellow steps exist and are extended. The two new money steps are the Safe payout and the on-chain match.

---

## 5. Payout sequence

```mermaid
sequenceDiagram
  autonumber
  participant APR as Approver
  participant API as Onsumm API
  participant POL as PolicyEngine
  participant SAFE as SafeCustody
  participant WAL as Approver wallets
  participant REL as GasRelayer
  participant CH as Base Sepolia
  participant WAT as ChainWatcher
  participant MAT as OnchainMatcher
  participant LED as LedgerPoster

  APR->>API: Create USDC payout for bill NOVA-1042
  API->>POL: check tenant, wallet, limits, balance
  POL-->>API: allowed
  API-->>APR: quote, withholding, net USDC
  APR->>API: verify OTP
  alt withholding above zero
    API->>SAFE: propose one MultiSend tx, net to vendor and tax to TAX wallet
  else zero withholding
    API->>SAFE: propose one USDC transfer
  end
  SAFE-->>API: safe_tx_hash and nonce
  API->>WAL: request EIP-712 signatures
  WAL-->>API: signature from Asha
  WAL-->>API: signature from Ben
  API->>REL: execute when threshold 2 of 3 met
  REL->>CH: execTransaction
  CH-->>REL: tx hash
  WAT->>CH: poll USDC Transfer logs
  WAT->>MAT: new transfer rows
  MAT->>API: match found, mark SUCCESS
  API->>LED: queue Tally payment voucher
```

The OTP gate is Onsumm's existing release control. The Safe threshold is the client's own control and holds even if Onsumm is compromised. When tax is withheld, the vendor and the tax wallet are paid in the same Safe transaction, so one cannot settle without the other.

---

## 6. Audit anchoring data flow

```mermaid
flowchart LR
  LOG[("wf_audit_log rows")]
  HASH["AuditHasher<br/>row_hash = sha256 of prev_hash + canonical row"]
  SALT[("tenant_audit_salts")]
  TREE["MerkleBuilder<br/>per tenant, salted leaves"]
  TROOT["Tenant roots<br/>audit_anchor_tenant_roots"]
  TOP["Top root<br/>tree of tenant roots"]
  ANC["OnsummAnchor contract<br/>Anchored event"]
  REC[("audit_anchors")]
  PB["ProofService<br/>proof bundle JSON"]
  VP["Public /verify page<br/>checks in the browser"]

  LOG --> HASH --> LOG
  LOG --> TREE
  SALT --> TREE
  TREE --> TROOT --> TOP --> ANC
  ANC --> REC
  REC --> PB
  TROOT --> PB
  PB --> VP
  VP -->|"read root"| ANC
```

Only one 32-byte root goes on-chain per run. The proof bundle carries the record, its salt and both Merkle paths, so the auditor needs no Onsumm login.

---

## 7. Deployment view

```mermaid
flowchart TB
  subgraph HOST["Demo host: laptop or EC2"]
    FEV["Vite dev server :5173"]
    APIV["FastAPI + workers :8001"]
    RKEY["RELAYER_KEY and ANCHOR_SIGNER_KEY<br/>in .env, testnet gas only"]
  end
  subgraph EC2["EC2 t2.micro, Mumbai"]
    PG[("Postgres 18 + pgvector<br/>onsumm-new-db")]
  end
  subgraph BASE["Base Sepolia, chain 84532"]
    BRPC["Public RPC sepolia.base.org"]
    BSAFE["Lumen Labs Safe 2 of 3"]
    BUSDC["Circle test USDC"]
    BANC["OnsummAnchor"]
  end
  BROWSER["Approver browser<br/>MetaMask or Rabby"]

  BROWSER --> FEV
  FEV -->|"/api proxy"| APIV
  APIV --> PG
  APIV --> BRPC
  BRPC --- BSAFE
  BRPC --- BUSDC
  BRPC --- BANC
  BROWSER -->|"sign EIP-712"| BROWSER
```

| Concern | Hackathon | Production target |
| --- | --- | --- |
| Database | Docker Postgres on a t2.micro, port 5432 public | RDS in a private subnet, off-server backups |
| Secrets | `.env` file | AWS Secrets Manager; anchor key in AWS KMS |
| RPC | One public endpoint | Two providers per chain with failover |
| Compute | Single process | API and `ChainWatcher` as separate processes, at least 2 GB RAM |
| Custody | `SafeCustody` | `SafeCustody` or `PartnerCustody` per tenant |

The hackathon setup is not fit for real money. The production column lists the gate items before any mainnet use.

---

## 8. Trust boundaries and key custody

```mermaid
flowchart LR
  subgraph CLIENT["Client boundary"]
    K1["Safe owner keys<br/>in approvers' wallets"]
  end
  subgraph ONSUMM["Onsumm boundary"]
    K2["Relayer key<br/>gas only"]
    K3["Anchor key<br/>gas only"]
    K4["Partner API key<br/>production only"]
    DBX[("Onsumm database")]
  end
  subgraph CHAIN["Public chain"]
    SAFEX["Client Safe holds USDC"]
    ANCX["OnsummAnchor"]
  end

  K1 -->|"can sign transfers up to threshold"| SAFEX
  K2 -->|"can submit only fully signed txs"| SAFEX
  K3 -->|"can write roots only"| ANCX
  K4 -->|"can propose, cannot sign"| SAFEX
  DBX -.->|"stores signatures, never keys"| K1
```

| Key | Held by | Can do | Cannot do |
| --- | --- | --- | --- |
| Safe owner keys | Each approver's wallet | Sign Safe transactions | Move funds alone below the 2-of-3 threshold |
| Relayer key | Onsumm, `.env` (hackathon) | Pay gas for `execTransaction` | Move USDC; a Safe needs owner signatures |
| Anchor key | Onsumm, `.env` then KMS | Call `OnsummAnchor.anchor` | Touch any client wallet |
| Partner API key | Onsumm, Secrets Manager (production) | Create transfer requests, read balances | Approve or sign; change client policy |

The worst case from a full Onsumm breach is a proposed payout that the owners never sign. Owner signatures are the only path to moving money.

---

## 9. Non-functional requirements

| Area | Requirement | How it is met |
| --- | --- | --- |
| Payout latency | Under 30 s from last signature to `CONFIRMED` on the demo | Base Sepolia blocks about 2 s; `CONFIRMATIONS=3`; poll every 4 s |
| Confirmation target | Demo: 3 blocks. Production: by amount, up to Base hard finality (about 15 to 19 minutes) for large payouts | `EvmChain` confirmation policy |
| Idempotency | No double payment on retry or double click | `idempotency_key` unique; `ALREADY_IN_FLIGHT`; one Safe nonce per payout |
| Atomicity | Vendor payment and withheld tax settle together | One Safe transaction via `MultiSendCallOnly` when withholding is above zero |
| Correctness | Every payout matched exactly | `OnchainMatcher` rule: tx hash, token contract, to address, `amount_units` |
| Availability | Watcher survives restarts and RPC gaps | `chain_watch_cursors`; backfill from last block; webhook optional |
| Reorg safety | No `SUCCESS` before confirmation depth | Block hash check; `removed` flag; `REORG_DETECTED` finding |
| Auditability | Every material action provable | Hash chain plus daily anchor |
| Observability | Payout and watcher metrics | Existing `/metrics` endpoint (Prometheus), access log with request IDs |
| Security | No fund keys in Onsumm | D3; relayer and anchor keys gas-only |
| Data privacy | No business data on-chain | Only salted hashes and roots |

---

## 10. Technology choices

| Choice | Picked | Why |
| --- | --- | --- |
| Chain | Base Sepolia | Cheap, fast, EVM; the same code path serves Safe, USDC and the anchor |
| Custody | Client Safe 2-of-3 | No partner contract needed; client owns keys; threshold maps to approval roles |
| Token | USDC | MiCA-compliant; Circle test token on Base Sepolia |
| Backend chain library | `web3` v7, `eth-account`, `siwe` | Backend is Python; no extra service to run |
| Frontend wallet | `wagmi` v2 + `viem`, injected connector | Fits the React app; signs EIP-712 in the browser |
| Contracts | Foundry | Fast compile, test and deploy |
| Batched transfers | Safe `MultiSendCallOnly` | Pays vendor and tax wallet atomically; call-only variant cannot delegate further |
| Hashing | SHA-256 | Available in Python and in browser WebCrypto |
| Screening | Static list file | No vendor account needed for a demo; provider interface ready |
| Scheduling | Workers in the API process | Same pattern as today's disbursement sweeper |

---

## 11. How it plugs into existing modules

| Existing module | How the crypto rail uses it |
| --- | --- |
| `routers/disbursement.py` | `POST /api/bills/{id}/disbursements` accepts `rail: "USDC"`. OTP release, overrides and `_apply_success` are reused. The GSTIN half of `_resolve_beneficiary` is skipped for international tenants. |
| `integrations/payments/` | Today's bank adapters sit behind `ImpsRail`. `UsdcRail` is added beside it under the `Rail` protocol. |
| `domain/payments/sweeper.py` | Also polls `PENDING` USDC payouts and raises `PAYOUT_STUCK` when one stalls. |
| `security/licensing.py` | New keys `crypto_payouts`, `crypto_tds`, `audit_anchor` gate every new route. |
| Review Hub (`backend_ocr/app/review/`) | Receives every finding code in 00_DECISIONS section 2.3. |
| `utils/tds_engine.py` | Becomes `IndiaServicesPack` (code `IN-SERVICES`) inside `WithholdingEngine`, beside `IN-195`, `IN-VDA` and the demo pack `SG-DEMO`. |
| `wf_audit_log` | Gains `seq`, `prev_hash`, `row_hash`, `anchor_id`. |
| `migrations.py` | Hosts all new idempotent DDL. |
