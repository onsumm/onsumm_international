# Onsumm.ai — Architecture Overview

> Pilot-testing reference. High-level architecture, tech stack, and services for the Onsumm.ai AP/AR cashflow platform. For the full endpoint-by-endpoint reference see [`README.md`](../README.md); for the router/module layout of the API see [`BACKEND_STRUCTURE.md`](BACKEND_STRUCTURE.md).

## 1. System overview

Three independently deployed processes share one database and coordinate only through it:

| Process | What it is | Where it runs |
|---|---|---|
| **Frontend** | React 18 + Vite + TypeScript SPA | AWS Amplify |
| **Backend API** | FastAPI + Uvicorn, 218 HTTP routes | AWS EC2 |
| **Pipeline engine** | Python asyncio, 12 CDC workers | AWS EC2 |
| **Database** | PostgreSQL 15 | AWS RDS |

The pipeline is what makes this an automation platform rather than a form over a database: it is the only component that reacts to data changes on its own, via Postgres `LISTEN`/`NOTIFY`. The API only writes in response to a request.

```
React SPA (Amplify)
      │  REST / multipart
      ▼
FastAPI backend (EC2)  ───────────────┐
      │  asyncpg / psycopg2 pool      │  asyncpg pool (shared)
      ▼                               ▼
PostgreSQL 15 (RDS)  ◄──────  Pipeline engine (EC2, 12 workers)
      ▲                               │
      │                               ├─► OpenAI GPT-4o (OCR, primary)
      │                               ├─► Sarvam AI (OCR, fallback)
      │                               ├─► Sandbox.co.in (GSTIN/bank KYB, TDS calc)
      │                               └─► Tally Prime XML (:9000, server-side pull)
      │
      └── API router `disbursement.py` calls Lendix (ICICI Bank IMPS) directly
          for vendor-payment initiation AND on-demand status reconciliation
```

A Windows `.exe` (`OnsummTallyAgent.exe`) is the alternate path into Tally when the pipeline server cannot reach the customer's LAN — it pushes XML to `/api/tally/ingest` instead of the pipeline pulling from Tally directly.

## 2. Technology stack

### Frontend
React 18 · Vite 6 · TypeScript · react-router-dom v7 · axios · react-dropzone · react-pdf · xlsx · react-force-graph-2d + d3-force · Vitest / Testing Library / Storybook 8 · ESLint 9 / Prettier / Stylelint

### Backend API
Python 3.10+ · FastAPI 0.115 + Uvicorn 0.32 · Pydantic 2.7 · psycopg2-binary · PyJWT + bcrypt (auth) · cryptography/Fernet (OAuth token encryption) · prometheus-client (`/metrics`, token-gated) · pytest + pytest-asyncio

### Pipeline engine
Python asyncio (native) · asyncpg 0.29 (pool: min 2 / max 10) · PostgreSQL `LISTEN`/`NOTIFY` as the event bus · openai ≥1.58 + pymupdf + Pillow (OCR) · Poppler (`pdftoppm`, PDF→image) · openpyxl (CSV/XLSX intake) · requests + lxml (Tally XML) · httpx (Sandbox.co.in)

### Data & infrastructure
| Concern | Technology | Notes |
|---|---|---|
| Database | **PostgreSQL 15 — AWS RDS** | one managed instance shared by API + pipeline |
| Frontend hosting | AWS Amplify | `amplify.yml`: `npm ci` → `vite build` → `dist/` |
| Backend API hosting | **AWS EC2** | `uvicorn app.main:app`, DB over SSL to RDS |
| Pipeline hosting | **AWS EC2** | `python main.py` as a long-lived process (systemd/supervisor), same VPC as RDS |
| Payment gateway | **Lendix → ICICI Bank (primary), AU Bank (secondary)** | RSA-encrypted REST, IMPS rail |
| Windows agent packaging | PyInstaller + pystray + pywin32 | single-file `.exe`, tray app, self-update |

## 3. Services

### 3.1 OCR — GPT-4o + Sarvam
Every upload runs a 5-stage status machine (`UploadOcrWorker`): PREPROCESSING → **OCR_PRIMARY** (GPT-4o vision) → **OCR_FALLBACK** (Sarvam AI, triggered when GPT-4o confidence < 0.6, fields merged by per-field confidence weight) → VALIDATING → DONE. GPT-4o-mini separately powers AP note generation, category classification, and ledger-account suggestion (`/api/enrich-invoice`).

### 3.2 Payments — Lendix (ICICI Bank) only
All vendor disbursement and reconciliation now run on a single rail:

- **Gateway**: Lendix (`api-dev.lendix.ai`), RSA-encrypted payloads.
- **Bank**: ICICI Bank is the primary `bankProvider` (₹1 minimum, ₹5,00,000 hard limit per transaction); AU Bank is kept only as a secondary provider — no automatic fallback between them.
- **Initiation**: `POST /vendor-payment/pay` (`backend_ocr/app/integrations/imps_client.py` → `vendor_payment_pay`), invoked from `routers/disbursement.py`.
- **Reconciliation**: Lendix does not push webhooks, so reconciliation is on-demand rather than event-driven — `POST /api/disbursements/{id}/refresh-status` calls `vendor_payment_status`, logs the call to `disbursement_gateway_calls`, and updates the transaction status in place.
- Every call is idempotent on `tran_ref_no`: a retry must reuse the same reference, never mint a fresh one for the same payment attempt.

### 3.3 KYC / compliance — Sandbox.co.in
- **GST verification** (`GstVerificationWorker`): registration check, active-status check, legal-name match, PAN match; 5 concurrent calls via `asyncio.Semaphore`.
- **Bank verification** (`BankVerificationWorker`): penny-less account verification, three-state outcome (verified / definitively not found / retry).
- **TDS calculation** (`TdsProcessingWorker`): section lookup + rate resolution via the Sandbox TDS Calculator API, with a local rate-table fallback on API failure.

### 3.4 Accounting sync — Tally Prime
Two integration modes, chosen per deployment: **server-side pull** (`TallyReadWorker`, LAN access to Tally's XML socket) or **agent-side push** (`OnsummTallyAgent.exe` on the customer's machine, for NAT/VPN-isolated Tally installs). `TallySyncWorker` additionally pushes approved payables back to Tally as payment vouchers.

### 3.5 Automation core — 12-worker CDC pipeline
Every worker inherits `pipelines/base.py:BaseWorker` and runs a two-phase lifecycle: drain everything outstanding on startup (Phase 1), then either wake on a Postgres `NOTIFY` channel or poll on a schedule with exponential idle backoff (Phase 2).

| # | Worker | Trigger | Role |
|---|---|---|---|
| 1 | UploadOcrWorker | `ch_upload_ocr` | OCR status machine |
| 2 | StagingPromotionWorker | `ch_staging_promotion` | `stg_*` → `fact_*`, fiscal period, credit recalc |
| 3 | DeduplicationWorker | `ch_deduplication` | exact/soft/cross-table duplicate detection |
| 4 | ApprovalWorker | time-driven | nudges, escalations, threshold auto-approval |
| 5 | ApiSyncWorker | `ch_api_sync` | Tally/Zoho/QBO/Xero/Sage/Wave payload normalisation |
| 6 | WebhookWorker | `ch_webhook` | inbound gateway payment events (signature-verified only) |
| 7 | TallyReadWorker | 15-min poll | server-side Tally pull |
| 8 | TallySyncWorker | 60s poll | bidirectional Tally push + pull |
| 9 | GstVerificationWorker | `ch_gst_verification` | GSTIN verification |
| 10 | BankVerificationWorker | `ch_bank_verification` | penny-less bank verification |
| 11 | CategoryEnrichmentWorker | `ch_category_enrichment` | COA rule → supplier default → keyword |
| 12 | TdsProcessingWorker | `ch_tds_processing` | TDS calculation + `fact_bills` back-patch |

*(Lendix disbursement/reconciliation is API-triggered, not a pipeline worker — see 3.2.)*

### 3.6 Payment Hub — approval guardrails

The guardrails sit above the Lendix/ICICI rail (§3.2) as application-level controls in `backend_ocr/app/disbursement_api.py`, not bank-side limits:

- **Eligibility gate**: `can_pay = bank_verified AND gstin_verified`, enforced server-side before a bill can be paid at all.
- **Access control**: Pay Now is gated to `status = 'APPROVED'` bills; initiating a payment, entering the OTP, or overriding beneficiary details is admin-role only (`frontend/src/pages/BillsPage.tsx`, `PaymentGatewayPage.tsx`).
- **OTP step**: 6-digit code, SHA-256 hashed at rest (`otp_code_hash`), max 5 attempts, 120s TTL; one shared OTP covers an entire bulk batch.
- **Manual override**: bypassing the verified-account default requires `override_account_number` / `ifsc` / `beneficiary_name` / `reason`, logged to `disbursement_overrides` and `wf_audit_log`.
- **Bulk payment**: same-vendor batches with independent per-bill amounts, a shared OTP, and a per-bill override/audit row each.
- **Settlement**: `POST /api/disbursements/{id}/refresh-status` polls Lendix for `PENDING → SUCCESS`, updates `amount_paid` / `outstanding_amount` / `payment_status`, and writes a step-99 row to `bill_approval_chain`.
- Amount is capped at the bill's TDS-net `outstanding_amount`; per-environment bank-side caps (UAT ₹10 / Staging ₹1,000 / Prod ₹1,50,000, ICICI hard cap ₹5,00,000) are not yet mirrored at the application layer — tracked as an open gap in `docs/PAYMENT_GUARDRAILS.md`.

### 3.7 Nova reconciliation service

A standalone microservice, deployed and run independently of both the API and the 12-worker pipeline: `services/nova_reconciliation/` (`app/main.py`, `orchestrator.py`, `algorithm.py`, `routes.py`), with its own tables (`nova_reconciliation_runs`, `nova_reconciliation_lines`, `nova_reconciliation_bill_queue`).

- **Problem it solves**: 3-way reconciliation for vendors billed through Nova (e.g. Signzy) — the vendor's invoice line, Nova's own usage ledger for that `(vendor, month)`, and the tenant's certified rate card (`lendix/nova_vendor_mapping.json`) must all agree before a bill is trusted.
- **Checks run, in order**: (1) certification — is this vendor/service even mapped and verified; (2) invoice vs. Nova — does the billed quantity match Nova's usage; (3) Nova vs. mapping — is Nova's own rate and arithmetic (`billableOperations × costPerOperationRupees = totalCostRupees`) within tolerance of the certified rate.
- **Trigger**: self-contained `asyncio` background task started in the service's own startup event, sweeping every `NOVA_ORCHESTRATOR_POLL_SECONDS` (default 120s) for eligible bills in `fact_invoices` / `stg_bill_line_items`; a manual sweep is available at `POST /v1/orchestrator/run-once`, status at `GET /v1/orchestrator/status`.
- **Per-tenant configuration**: each tenant enters their own Nova API key per brand under Integrations → Nova (`frontend/src/pages/IntegrationsPage.tsx` → `POST /api/nova/credentials` → `backend_ocr/app/routers/nova_credentials.py`), Fernet-encrypted at rest in `int_nova_credentials`.
- Deliberately duplicates its own credential resolver (`services/nova_reconciliation/app/credentials.py`) rather than importing the API package, so the two services can be deployed and scaled independently.

### 3.8 Review Hub — the compliance & communication agent

Production feature (`docs/HLD_LLD_COMM_COMPLIANCE_REVIEWHUB.md`, `docs/REVIEW_HUB_DESIGN_BRIEF.md`), router `backend_ocr/app/routers/review_hub.py`, backed by `rh_findings` plus seven satellite tables. It's the layer that turns raw ingestion into flagged, human-reviewable findings — the recent "flag the one bill a vendor filed differently" and GST-detail-drift work both landed here.

**Compliance side** — 12 deterministic adapters plus an LLM review agent, evaluated per fact as it lands:
- `vendor_identity.py`, `gst_name_overwrite.py`, `billing_discrepancies.py` are the adapters already shipping.
- `contract_bill_variance.py` is newly added; `vendor_compliance` and `master_data_conflict` adapters are designed but not yet built.
- Findings render through a fixed set of shapes — diff, table, timeline, field-list, prose — so a new adapter needs no bespoke UI (`REVIEW_HUB_DESIGN_BRIEF.md` §2). UI lives at `/review` in the frontend.

**Communication side** — three previously separate pieces unified under one name:
- The mailbox OAuth connector (`routers/email_connection.py`, `integrations/email_oauth.py`) that lets a user link their inbox.
- The Vendor Bill Agent's own mailbox watcher (§3.9).
- Fact extraction from email content (`domain/fact_graph.py`), plus a planned `notify` action kind so a compliance finding can trigger an outbound email through the same connector.

### 3.9 Agent Hub &amp; the Vendor Bill Agent

`docs/PRD_AGENT_HUB.md` frames Agent Hub as a CFO-orchestrator layer over capabilities that already exist elsewhere in the platform (OCR, KYB, Nova reconciliation, disbursement, FX, TDS) — each wrapped as a governed "agent" with a WHY/WHAT/WHOM/HOW audit trail. Agent-by-agent specs live in `docs/agents/` (`CFO_ORCHESTRATOR_AGENT.md`, `VERIFICATION_AGENT.md`, `RECON_AGENT.md`, `PAYABLE_AGENT.md`, …).

- **`/api/agents/*`** (`routers/agent_hub.py`) is the orchestration surface: `GET /fact-graph`, `/fact-graph/search`, `/fact-graph/neighbors`, `/fact-graph/stream` (server-sent events), `GET /payment-context` — all reading `domain/fact_graph.py`, tenant-scoped through `security/auth.require_tenant()`.
- **`/api/agent/*`** (singular, `routers/agent.py`) is a distinct, older surface: the **Vendor Bill Agent** itself — status/toggle/scan/events/POCs/drafts.
- **Vendor Bill Agent mechanics**: polls each connected Gmail mailbox every `AGENT_POLL_INTERVAL` (default 600s), filters to mail from flagged POCs (`supplier_contacts.agent_enabled`) that carries an attachment, classifies bill-vs-conversation with an OpenAI model (`AGENT_CLASSIFY_MODEL`, default `gpt-4o-mini`), runs the OCR pipeline on anything classified as a bill, and drops the result into `agent_draft_bills` for human review — it never auto-submits a bill. Thread-aware, so a follow-up correction on the same email thread is detected rather than filed as a duplicate.
- No dedicated model beyond what OCR already uses (GPT-4o / GPT-4o-mini) — Agent Hub is an orchestration and audit layer, not a separate inference stack.

## 4. Data model

Table prefixes encode pipeline stage: `raw_` (inbound) → `stg_` (validated, pre-fact) → `fact_` (analytics-ready) → `wf_` (workflow/audit); `dim_` (masters), `int_` (external connection state), `ai_` (anomalies). Fiscal period (India's April–March year) is computed once by `utils/fiscal.py` and stored on every fact row.

## 5. Security & multi-tenancy

Three concentric checks, each its own module under `backend_ocr/app/security/`:

1. **Identity** (`auth.py`) — tenant resolved from a verified JWT.
2. **Ownership** (`tenancy.py`) — row belongs to the calling tenant; failures return `404`, never `403`, so a wrong tenant can't use the status code to confirm another tenant's record exists.
3. **Entitlement** (`licensing.py`) — route resolves to a licensed service for the tenant's plan.

All three are pinned mechanically by the test suite (`test_tenant_route_map.py`, `test_license_route_map.py`, `test_tenancy.py`), not left to reviewer discipline.

## 6. Authentication — password login + Google/Microsoft SSO

Both a conventional email/password flow and SSO exist in code today, sharing one token model.

**Password login** (`POST /api/login`, `backend_ocr/app/routers/auth.py`)
- `bcrypt.checkpw` against `users.password_hash`.
- `/api/auth/forgot-password` → OTP → `/api/auth/verify-otp` → `/api/auth/reset-password` for resets.

**Google + Microsoft SSO** (`backend_ocr/app/routers/sso.py`)
- `GET /api/auth/sso/authorize?provider=google|microsoft` and `POST /api/auth/sso/exchange`, built on the same OAuth adapter used for mailbox connection (`integrations/email_oauth.py`). Config gates: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, `MS_CLIENT_ID`/`MS_CLIENT_SECRET`. `SUPPORTED_PROVIDERS = ("google", "microsoft")` — no other provider is wired in.
- **SSO never creates an account.** The provider email must already match an existing `users.email` row; an unmatched or ambiguous email is rejected outright (`resolve_sso_user`) rather than silently provisioning a user.
- **No JWT in the redirect URL.** The OAuth callback mints a single-use, SHA-256-hashed code (`auth_sso_codes`, 120s TTL) instead; the SPA (`frontend/src/auth/AuthContext.tsx`'s `loginWithSso`) trades that code via `POST /api/auth/sso/exchange` for the same JWT/refresh-token pair password login produces.
- When `SSO_REQUEST_MAILBOX_SCOPES` is set, the same consent screen that logs the user in also grants the mailbox-read scopes the Vendor Bill Agent (§3.9) and Review Hub's communication side (§3.8) need — one OAuth grant, two purposes.

**Shared token issuance** (`backend_ocr/app/core/tokens.py`)
- Access token: PyJWT, HS256, `ACCESS_TOKEN_TTL_MIN` (default 60 min).
- Refresh token: opaque `secrets.token_urlsafe(48)`, stored SHA-256-hashed in `auth_refresh_tokens`, `REFRESH_TOKEN_TTL_DAYS` (default 30) — never the raw token at rest, matching the pattern used for the Tally agent's API key (§3.4) and Lendix's `tran_ref_no` handling (§3.2).
- `/api/auth/refresh` and `/api/auth/logout` round out the cycle.
- Frontend session state: `frontend/src/auth/AuthContext.tsx`, `services.ts`, `permissions.ts`, `guards.tsx`.

## 7. Deployment topology

| Component | Host | Notes |
|---|---|---|
| Frontend | AWS Amplify | static build from `frontend/dist` |
| Backend API | AWS EC2 | `uvicorn`, same VPC as RDS |
| Pipeline engine | AWS EC2 | separate long-lived process, same VPC as RDS |
| Database | AWS RDS (PostgreSQL 15) | shared by API + pipeline; also the LISTEN/NOTIFY bus |
| Payment gateway | Lendix / ICICI Bank | called directly from the API layer, no infra of its own |

---
*Compiled from the in-repo technical reference and backend structure notes. Reflects Lendix/ICICI as the sole payment rail, AWS RDS for the database, and AWS EC2 for backend + pipeline hosting.*
