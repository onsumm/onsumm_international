# Vendor Disbursement (IMPS) Payment Integration — Implementation Plan

Status: DRAFT (for review)
Owner: TBD
Related docs: [`DISBURSEMENT_IMPS_API.md`](./DISBURSEMENT_IMPS_API.md), [`full_database_schema.md`](./full_database_schema.md)

## 1. Goal

Let an admin pay a vendor bill directly from the Bills page using the bank's IMPS rail
(`POST /disbursement/imps/initiate`), instead of just marking a bill "paid" manually. Payment can be full or
partial, must only ever go to a **verified** bank account, must never move money to unverified/user-typed
details without an explicit, logged admin override, and must be fully auditable and trackable to final bank
settlement status. An admin can also pay **multiple pending bills of the same vendor in one go** (bulk
select), and every initiated payment must clear a **second-factor confirmation** (email passcode +
authenticator-app TOTP tied to the remitter account) before the bank call actually fires.

## 1a. Feature additions in this revision

- **Bulk payment**: from the payment entry point, the admin sees all of a vendor's outstanding bills, can
  multi-select any subset, adjust amounts per bill (default = full outstanding), and initiate them together.
  See §4.5, §5.1a, §6.1a.
- **2FA confirmation gate**: after the admin submits a payment (single or bulk), before the bank is actually
  called, a passcode is emailed to the remitter account's configured email address **and** the admin must
  supply the current TOTP code from a third-party authenticator app (Google Authenticator/Authy/etc.)
  registered against that remitter account. The RSA/AES encryption question from the original open-questions
  list (§8.2/§10) is **left as-is / unresolved for now** — this 2FA gate is an additional control on top of it,
  not a replacement, and does not require re-deciding who owns the RSA encryption. See §4.6, §5.5, §6.4, §8.4.

## 2. Current state (as found in the codebase)

- **Vendor master**: `dim_suppliers` — `bank_account`, `bank_ifsc`, `bank_name`, `bank_verified` (bool),
  `status` (KYB text status). Multiple accounts per vendor live in `supplier_bank_accounts`
  (`account_number`, `ifsc`, `bank_name`, `is_primary`, `verified`, `name_at_bank`). There's also a newer
  `dim_bank_accounts` (customer-side, has `account_number_enc`) — **not** the vendor table, don't confuse the two.
- **Bank-account CRUD** already exists: `backend_ocr/app/main.py` — list (`:3303`), add (`:3335`), delete
  (`:3371`), set-primary (`:3411`), reverify (`:3443`). Verification flag comes from this flow (screenshot:
  "Bank account is verified" / "Reverify").
- **Bills**: `fact_bills` has `net_payable`, `outstanding_amount`, `amount_paid`, `status`. There is also an
  older/parallel `fact_invoices` row that the existing ad-hoc payment endpoint
  (`POST /api/bills/{invoice_id}/payment`, `main.py:4702-4754`) actually writes to (`payment_status`,
  `amount_paid`, `outstanding_amount`) plus a `bill_approval_chain` row with `step=99` reserved for payment
  events. **This dual-table situation must be resolved as part of this work** — decide once which table is
  authoritative for `outstanding_amount`/`amount_paid` and stop writing both, or the new disbursement flow
  will silently diverge from the UI's displayed numbers.
- **No AP disbursement table exists.** `fact_payments` / `payment_schedules` / `payment_transactions` are all
  AR-oriented (customer receivables) or generic ledger schedules — none model an outbound bank-to-bank
  transfer with a bank RRN, vendor beneficiary snapshot, and bank status polling.
- **Audit pattern to reuse**: `vendor_tds_config_log` (`tenant_id, override_id, field_changed, old_value,
  new_value, reason, changed_by, changed_at`), written from `tds_api.py:559-620`. Also the generic
  `wf_audit_log` (`table_name, entity_id, action, old_values_jsonb, new_values_jsonb, correlation_id`) is
  available for less structured logging.
- **External integration template**: `backend_ocr/app/sandbox_kyb.py` — `httpx` client, API key/secret from
  `config.py`, token caching, `_call_with_retry` wrapper. Use this shape for the new IMPS client, not a new
  pattern.
- **No RSA/AES code exists yet** anywhere in the repo, despite `dim_bank_accounts.account_number_enc`
  implying it was planned. This integration is the first thing that will actually need it (the IMPS API
  itself handles RSA/AES on the wire — see below — but we still must decide how *we* store/encrypt
  `beneficiaryAccount` at rest in our own new tables).
- **UI**: `frontend/src/pages/BillsPage.tsx` — "Net Payable" column (`:3273`), per-row net payable
  (`:2294`, `:2363`), approve/status actions (`:1156-1181`), Payment Status card (`:1430-1446`). This is
  where the "Pay" button and payment modal attach.
- **Vendor detail modal** (screenshots) already has Bank tab showing verified/primary account — this is the
  natural source of the beneficiary snapshot for payment.

## 3. Non-goals / guardrails up front

- We will **not** disburse to an unverified bank account, ever, without an explicit admin override that is
  itself logged (see §7). "Unverified" vendors (e.g. `GOOGLE INDIA`, `LAXMI KANT SINHA` in the screenshot)
  cannot be paid via this flow until KYB + bank verification passes, unless overridden.
  Note that the IMPS API doc's own amount ceilings (UAT ₹10 / Staging ₹1,000 / Production ₹1,50,000; ICICI
  hard cap ₹5,00,000) must be enforced client-side before calling the bank, not just relied on as a bank-side
  rejection.
- We will not build a generic "payment gateway" abstraction beyond IMPS/ICICI/AUBANK — scope is this one API.
- We will not retro-migrate the existing manual "mark as paid" flow in this phase; it can continue to exist
  for non-bank payment methods (cheque/cash), but the new "Pay via bank" button is additive.

## 4. Database changes

All new tables follow existing conventions: `uuid` PK via `gen_random_uuid()`, `tenant_id` FK to
`dim_tenants`, `created_at timestamptz default now()`.

### 4.1 `fact_disbursements` (new)

One row per payment *attempt* (a bill can have multiple partial disbursements).

| Column | Type | Notes |
|---|---|---|
| `disbursement_id` | uuid PK | |
| `tenant_id` | uuid FK `dim_tenants` | |
| `bill_id` | uuid FK `fact_bills.bill_id` | |
| `supplier_id` | uuid FK `dim_suppliers.supplier_id` | |
| `supplier_bank_account_id` | uuid FK `supplier_bank_accounts.id` | snapshot source; nullable if override used (see below) |
| `amount` | numeric NOT NULL | this attempt's amount (partial or full) |
| `beneficiary_name_snapshot` | text NOT NULL | frozen at time of payment, not a live join |
| `beneficiary_account_snapshot` | text NOT NULL | encrypted at rest (see §8) |
| `beneficiary_ifsc_snapshot` | text NOT NULL | |
| `beneficiary_mobile_snapshot` | text | required by API; sourced from vendor contact or entered at payment time |
| `is_overridden` | bool NOT NULL default false | true if any beneficiary field diverged from the verified record on file |
| `override_id` | uuid FK `disbursement_overrides.override_id` NULL | set only when `is_overridden` |
| `bank_provider` | text NOT NULL | `ICICI` \| `AUBANK` |
| `remitter_account_number` | text NOT NULL | which of our accounts paid |
| `tran_ref_no` | text NOT NULL | our idempotency key sent to the bank (≤20 chars) |
| `bank_tran_ref_no` | text | bank's returned ref, used for status polls |
| `bank_rrn` | text | bank's 12-digit RRN |
| `status` | text NOT NULL default `'INITIATED'` | `INITIATED` \| `PENDING` \| `SUCCESS` \| `FAILED` \| `TIMEOUT` |
| `status_message` | text | last status message from bank |
| `act_code` | text | ICICI ActCode for error triage |
| `initiated_by` | uuid FK `org_users.user_id` | admin who clicked Pay |
| `approved_by` | uuid FK `org_users.user_id` NULL | if a second-approval step is required (see §7.3) |
| `initiated_at` | timestamptz default now() | |
| `last_status_checked_at` | timestamptz | |
| `raw_initiate_response_jsonb` | jsonb | full vendor response for support/debugging |
| `raw_status_response_jsonb` | jsonb | latest status response |
| `created_at` / `updated_at` | timestamptz | |

Indexes: `(tenant_id, bill_id)`, `(tenant_id, status)`, unique `(tenant_id, tran_ref_no)` for idempotency.

### 4.2 `disbursement_overrides` (new)

Captures *what* was overridden and *why*, separate from the audit log (which captures the field-level diff).
Mirrors the existing `dim_vendor_tds_overrides` + `vendor_tds_config_log` split.

| Column | Type | Notes |
|---|---|---|
| `override_id` | uuid PK | |
| `tenant_id` | uuid FK | |
| `bill_id` | uuid FK `fact_bills.bill_id` | |
| `supplier_id` | uuid FK `dim_suppliers.supplier_id` | |
| `overridden_by` | uuid FK `org_users.user_id` NOT NULL | must be an admin (role-checked, see §7.2) |
| `reason` | text NOT NULL | free-text justification, required |
| `original_account_number` | text | vendor's verified account, for reference |
| `original_ifsc` | text | |
| `override_account_number` | text | account actually paid |
| `override_ifsc` | text | |
| `override_beneficiary_name` | text | |
| `created_at` | timestamptz default now() | |

### 4.3 `disbursement_audit_log` (new, or reuse `wf_audit_log`)

Recommendation: **reuse `wf_audit_log`** (`table_name='fact_disbursements'`, `entity_id=disbursement_id`,
`old_values_jsonb`/`new_values_jsonb`, `user_id`, `correlation_id`) for every state transition (initiate →
pending → success/failed) and every override, rather than inventing a parallel table. This keeps one place
to query "what changed on this payment" and matches the existing generic-audit convention already used
elsewhere in the schema. Only add a dedicated table if `wf_audit_log`'s shape proves insufficient once
building §5.

### 4.4 `fact_bills` — no new columns required

`net_payable`, `outstanding_amount`, `amount_paid`, `status` already exist and are sufficient; they get
updated (not duplicated) when a disbursement reaches `SUCCESS`. **Decide and document which of
`fact_bills` vs `fact_invoices` is authoritative before writing this update logic** (see §2) — do not update
both independently.

### 4.5 Bulk payment: `fact_disbursement_batches` + `disbursement_bill_allocations` (new)

A bulk payment still results in one or more actual bank calls (each bank call = one `fact_disbursements`
row, unchanged from §4.1), but a single bank call can now cover **multiple bills for the same vendor** in one
transfer. Two changes make this possible:

- `fact_disbursements.bill_id` becomes **nullable** — a batch-level disbursement is not tied to one bill.
- New table `disbursement_bill_allocations`: `allocation_id` (uuid PK), `disbursement_id` (FK
  `fact_disbursements.disbursement_id`), `bill_id` (FK `fact_bills.bill_id`), `allocated_amount` (numeric).
  One disbursement can have many allocation rows (one per bill it paid toward); the sum of
  `allocated_amount` for a disbursement must equal `fact_disbursements.amount`. This is what drives per-bill
  `outstanding_amount` updates once the disbursement succeeds (§5.1a).
- New table `fact_disbursement_batches`: `batch_id` (uuid PK), `tenant_id`, `supplier_id` (FK
  `dim_suppliers.supplier_id`), `total_amount`, `bill_count`, `status` (`PENDING_2FA` \| `INITIATED` \|
  `PARTIALLY_COMPLETE` \| `COMPLETE` \| `FAILED` \| `CANCELLED`), `initiated_by`, `created_at`. Groups the
  one-or-more `fact_disbursements` rows a bulk submission produced (add `fact_disbursements.batch_id` FK,
  nullable — null for a plain single-bill payment). A batch splits into multiple disbursement rows only when
  the selected total exceeds the environment's IMPS ceiling (§3) — same "chunking" idea already described in
  §6.2, now generalized to bulk.

This keeps the existing single-bill flow byte-for-byte compatible (a single-bill payment is just a batch of
one bill with one allocation row equal to the full disbursement amount) rather than forking the schema in two.

### 4.6 2FA config and challenges: `remitter_security_config` + `disbursement_otp_challenges` (new)

- `remitter_security_config`: one row per `remitter_account_number` (the *our-side* bank account that funds
  disbursements, from §8.1's NBFC/remitter config — extend that table/config rather than duplicating it if
  it already exists by the time this is built). Adds: `notify_email` (email that receives the passcode —
  this is the remitter's/treasury owner's email, **not** the vendor's), `totp_secret_enc` (encrypted TOTP
  seed, provisioned once via a standard TOTP enrollment flow — QR code scanned into Google
  Authenticator/Authy/etc.), `totp_enabled` (bool).
- `disbursement_otp_challenges`: `challenge_id` (uuid PK), `tenant_id`, `batch_id` (FK
  `fact_disbursement_batches.batch_id`) — a challenge is issued once per batch/submission, not once per bank
  call — `remitter_account_number`, `email_code_hash` (hashed, never store the raw passcode), `email_sent_at`,
  `email_verified_at`, `totp_verified_at`, `attempt_count`, `expires_at` (short TTL, e.g. 10 minutes),
  `created_at`. A batch only moves from `PENDING_2FA` to `INITIATED` once **both** `email_verified_at` and
  `totp_verified_at` are set. Failed/expired challenges are logged to `wf_audit_log` like everything else.

## 5. Backend changes (`backend_ocr/app/`)

### 5.1 New module `disbursement_api.py` (mounted via `app.include_router`, same pattern as `tds_api.py`)

- `POST /api/bills/{bill_id}/disbursements` — initiate a payment.
  - Input: `amount`, `bank_provider`, `remitter_account_number`, optional `override` block
    (`account_number`, `ifsc`, `beneficiary_name`, `reason`) — override block requires admin role.
  - Validates: bill belongs to tenant; `amount <= outstanding_amount`; amount within the environment's IMPS
    ceiling; vendor bank account is `verified=true` **unless** override block present and caller is admin.
  - Resolves beneficiary snapshot from `supplier_bank_accounts` (primary+verified row) or from override block.
  - Generates `tran_ref_no` (short, unique, e.g. `PAY{bill_id_short}{counter}`).
  - Calls new `imps_client.py` (see 5.2), writes `fact_disbursements` row, writes `wf_audit_log` entry,
    writes `disbursement_overrides` row if applicable.
  - Returns disbursement id + initial status to the UI; UI then polls or the backend polls (see 5.3).
- `GET /api/bills/{bill_id}/disbursements` — list all attempts for a bill (for the "payment history" panel).
- `POST /api/disbursements/{disbursement_id}/refresh-status` — calls `/disbursement/imps/status`, updates
  `fact_disbursements.status`/`bank_rrn`/`raw_status_response_jsonb`, and — only on transition into
  `SUCCESS` — updates, **per allocation row** (§4.5), each affected bill's
  `fact_bills.amount_paid`/`outstanding_amount`/`status` and writes a `bill_approval_chain` step-99 row per
  bill (reusing the existing convention from `main.py:4742-4746`) so it shows up alongside other bill
  history — a bulk payment must still show up as a distinct payment event on *each* bill it touched.

### 5.1a New endpoints for bulk vendor payment

- `GET /api/vendors/{supplier_id}/bills/pending` — all bills for that vendor with `outstanding_amount > 0`,
  for the multi-select list (§6.1a). Returns bill id, number, due date, outstanding amount, overdue flag.
- `POST /api/vendors/{supplier_id}/disbursement-batches` — bulk initiate. Input: list of
  `{bill_id, amount}`, `bank_provider`, `remitter_account_number`, optional override block (same admin-only
  rule as §5.1). Validates every bill belongs to the vendor/tenant, every `amount <= outstanding_amount` for
  its bill, and the vendor's bank account is verified (or override). Creates one
  `fact_disbursement_batches` row (`status='PENDING_2FA'`), does **not** call `imps_client` yet — that only
  happens after the 2FA gate (§5.5) passes. If the summed amount exceeds the IMPS ceiling, split into
  multiple planned disbursements at this stage (still all under one batch) rather than surprising the admin
  later.
- `GET /api/disbursement-batches/{batch_id}` — batch status + its child disbursements + allocations, for the
  UI to poll while `PENDING_2FA` / `INITIATED`.

### 5.5 2FA confirmation gate (email passcode + authenticator TOTP)

Applies to both the single-bill flow (§5.1) and the bulk flow (§5.1a) — internally, a single-bill payment is
just a one-bill batch (§4.5), so this is one code path, not two.

- On batch creation (`status='PENDING_2FA'`), generate a random 6-digit passcode, hash it, store the hash in
  `disbursement_otp_challenges.email_code_hash`, and email the plaintext code to
  `remitter_security_config.notify_email` for the chosen `remitter_account_number`. This email step is the
  "our responsibility" leg — plain SMTP/whatever mailer this app already uses, not a third-party service.
- `POST /api/disbursement-batches/{batch_id}/confirm` — takes `email_code` and `totp_code`. Verifies
  `email_code` against the stored hash (rate-limited attempts, short TTL — see §4.6) and `totp_code` against
  `remitter_security_config.totp_secret_enc` using a standard TOTP algorithm (RFC 6238 — same algorithm every
  authenticator app implements; no vendor lock-in). Both must pass. On success, mark both `*_verified_at`
  timestamps, flip the batch to `INITIATED`, and only **now** loop over its planned disbursements calling
  `imps_client.initiate` for each (§5.2) — i.e. the bank is never called until both factors clear.
- Failure handling: wrong/expired code → batch stays `PENDING_2FA`, attempt is logged to `wf_audit_log`;
  after N failed attempts the challenge is invalidated and the admin must restart from batch creation (fresh
  passcode), not retry the same one indefinitely.

### 5.2 New module `imps_client.py`

Modeled on `sandbox_kyb.py`'s shape (`httpx`, retry wrapper, config-driven secrets):

- Wraps `POST /disbursement/imps/initiate` and `POST /disbursement/imps/status`.
- Reads `IMPS_API_BASE_URL`, `IMPS_API_KEY`, and per-`nbfcName` remitter account config from environment
  (see §8).
- Handles the two banks' differing response shapes (ICICI echoes your `tranRefNo`; AU Bank returns its own —
  the client must always persist whichever `tranRefNo` came back as `bank_tran_ref_no`, never assume it's
  the one we sent).
- Retries only on `ActCode 18` (`NPCI_DOWN`) / `997` (`TRANSACTION_PENDING` → immediately triggers a status
  check instead of blind retry, to preserve idempotency). Never retries with a *new* `tran_ref_no` for the
  same payment attempt — that would double-pay.

### 5.3 Status polling

A pending disbursement's terminal state isn't always known synchronously. Add a lightweight background job
(reuse whatever job runner already exists — check for existing cron/async workers before introducing a new
one) that calls `refresh-status` for any `fact_disbursements` row still `INITIATED`/`PENDING` older than a
short delay, on a backoff schedule, until `SUCCESS`/`FAILED`/`TIMEOUT`. The UI's manual refresh (below) covers
the interactive case; the background job covers the "admin closed the tab" case.

### 5.4 Fixing the dual-table write

Before wiring `fact_bills` updates into 5.1, trace every existing writer of `fact_invoices.amount_paid` /
`outstanding_amount` / `payment_status` (starting at `main.py:4702-4754`) and either (a) make `fact_bills`
the sole source of truth and have `fact_invoices` derive/sync from it, or (b) confirm `fact_invoices` is
already dead code for AP bills and stop touching it. This is a prerequisite, not optional cleanup — shipping
disbursement writes to `fact_bills` while the UI reads `fact_invoices` (or vice versa) will show incorrect
outstanding amounts.

## 6. Frontend changes (`frontend/src/pages/BillsPage.tsx`)

### 6.1 Pay button

- Add near the existing Payment Status card (~line 1430) in the bill detail panel, and as a row action in
  the table (~line 3273 area) when `outstanding_amount > 0`.
- Disabled (with tooltip "Vendor bank account not verified") when the vendor's bank account is not verified
  and the current user is not an admin.

### 6.1a Vendor-level bulk payment entry point

- Add a "Pay Vendor" action on the vendor detail modal (screenshots show `Details / Bank / GSTINs / TDS /
  Documents / Statements` tabs — add this as a button near the header, not a new tab, so it stays a one-click
  action) and/or a "Pay Selected" bulk-bar on the Bills table when the admin filters to one vendor and
  multi-selects rows via the existing row checkboxes (table already has a checkbox column, per the
  screenshot).
- Opens a variant of the payment modal (§6.2) pre-loaded from `GET /api/vendors/{supplier_id}/bills/pending`:
  a checkbox list of the vendor's outstanding bills (bill number, due date, overdue badge, outstanding
  amount), each individually selectable and with an editable per-bill amount (defaults to full outstanding
  for selected rows). A running total updates as bills are checked/unchecked or amounts edited, validated
  against the vendor's single verified bank account and the environment's IMPS ceiling.

### 6.2 Payment modal

On click, open a modal showing:
- Vendor name, GSTIN, verified bank account (masked account number, IFSC, bank name) — pulled live so the
  admin sees current bank details, not stale bill data.
- Outstanding amount, editable "Amount to pay" field (default = full outstanding, allow partial, validate
  `0 < amount <= outstanding`).
- Bank provider selector if the tenant has more than one remitter account configured (else auto-selected).
  If IMPS ceilings are exceeded, don't ask the payer to split it manually — surface both remitter accounts as chunked suggestions or simply block with a clear "exceeds ₹X limit for this environment" message and let them re-enter a smaller amount.
- **Override toggle** (admin-only, hidden entirely for non-admins): reveals editable beneficiary
  name/account/IFSC fields plus a required "Reason for override" text field. Submitting with the toggle on
  shows a confirmation step ("You are paying an account different from the vendor's verified bank details")
  before allowing submit.
- Submit → creates a batch (`POST /api/bills/{bill_id}/disbursements` for a single bill, or
  `POST /api/vendors/{supplier_id}/disbursement-batches` for bulk — both land on the same `PENDING_2FA` batch
  model, §4.5) → modal advances to the 2FA step (§6.4) rather than immediately showing "Payment initiated".

### 6.3 Status display on Bills page

- Replace/augment the current binary Payment Status card with a small state machine badge:
  `Not Paid` → `Processing` (amber, spinner) → `Paid` (green) / `Partially Paid` (blue) / `Failed` (red, with
  retry button that opens the same modal pre-filled).
- Add a "Payment History" section (list of `fact_disbursements` rows: date, amount, status, bank RRN,
  initiated-by) — this doubles as a lightweight audit view for end users, backed by the same
  `GET /api/bills/{bill_id}/disbursements` endpoint.
- Table's "Net Payable" / status columns should reflect partial payments (i.e. outstanding after each
  successful disbursement), consistent with whichever table wins in §5.4.

### 6.4 2FA confirmation step

- After batch creation, the modal shows: "A passcode has been emailed to the configured remitter contact.
  Enter the passcode and your authenticator app code to confirm this payment." Two input fields: email
  passcode, authenticator TOTP code. A "Resend passcode" link (rate-limited) in case the email is delayed.
- Submit → `POST /api/disbursement-batches/{batch_id}/confirm`. On success, the modal moves to the existing
  "Payment initiated" state (bank tranRefNo(s), polling — same as before), now actually calling the bank. On
  failure, show the specific reason (wrong code / expired / too many attempts) and let the admin retry within
  the attempt limit, or restart if the challenge was invalidated.
- If the vendor/tenant has no `remitter_security_config` row yet (TOTP not enrolled), block payment entirely
  with a clear message pointing to wherever remitter/bank config gets set up — don't silently skip 2FA.

## 7. Guardrails, approvals, and audit trail (detail)

### 7.1 Verified-only by default

Enforced server-side in `disbursement_api.py`, not just in the UI (client-side disabling is a UX nicety, not
a control). The check reads `supplier_bank_accounts.verified` (or `dim_suppliers.bank_verified` if that's
the field still in use — reconcile which one is canonical during implementation, both currently exist).

### 7.2 Admin-only override

- Reuse whatever role/permission check already gates other admin-only actions in this app (find the existing
  pattern — e.g. how `org_user_access_levels` or role checks are used elsewhere in `main.py` — do not invent
  a new role system for this).
- Override requires a non-empty `reason`; both the override record (`disbursement_overrides`) and the diff
  (`wf_audit_log`) are written atomically with the disbursement row, in the same DB transaction, so a partial
  write can never leave a payment initiated without its override justification on record.

### 7.3 Optional second-approval step (flag for discussion, not committed)

Given this codebase already has a multi-step `bill_approval_chain` for bill approval, consider whether
large/overridden payments should require a second admin's sign-off before the bank call fires (e.g. reuse
the chain's step mechanism at `step=99` with a `PENDING`→`APPROVED` sub-state) rather than firing
immediately on the initiating admin's click. Recommend deciding this with the user before building 5.1,
since it changes the initiate endpoint's contract (fire immediately vs. queue for approval).

### 7.4 Audit trail coverage

Every one of these writes a `wf_audit_log` row (`table_name='fact_disbursements'`):
- Disbursement initiated (new_values = full request snapshot, minus raw account number — see §8.2).
- Status transitions (old status → new status) from both manual refresh and the background poller.
- Any override (separately captured in full in `disbursement_overrides`, referenced by `correlation_id`).
- Any admin edit to a vendor's bank account details in the existing bank-account CRUD endpoints
  (`main.py:3303-3474`) should already be logged somewhere — if it isn't, extend those existing endpoints to
  also write `wf_audit_log` rows, since a bank-account edit followed immediately by a payment is exactly the
  kind of thing that needs a trail.

## 8. Security & environment

### 8.1 Secrets / config

New environment variables (add to whatever config/secrets mechanism `config.py` already uses — do not
hardcode):
- `IMPS_API_BASE_URL` (e.g. `https://api-dev.lendix.ai` for UAT)
- `IMPS_API_KEY`
- Per-tenant/NBFC remitter account mapping (`nbfcName` → `remitterAccountNumber` → `bankProvider`) — this
  should not be hardcoded per environment; store it in a small config table or the existing tenant-config
  mechanism (`tenant_chain_config` is a precedent for tenant-scoped config tables) so ops can update it
  without a deploy.
- Separate ceilings per environment (`IMPS_MAX_AMOUNT`) so the UAT/staging/prod limits from the API doc are
  enforced in our own validation, not just discovered via a bank rejection.

### 8.2 Data at rest

- `fact_disbursements.beneficiary_account_snapshot` holds a bank account number — encrypt it at rest
  (this is the first place in the codebase that actually needs the encryption implied by
  `dim_bank_accounts.account_number_enc`). Use a standard authenticated encryption (e.g. AES-GCM with a key
  from the existing secrets store) — do not build custom crypto. Store only masked/last-4 in any UI response
  and in audit log snapshots; never write the full account number into `wf_audit_log.new_values_jsonb`.
- The IMPS API's own RSA/AES envelope (mentioned in the doc) is between our backend and the bank vendor
  gateway — that's handled inside the vendor's SDK/response format, not something we implement ourselves;
  our `imps_client.py` just calls the documented REST endpoint over TLS with the API key. Confirm with
  whoever owns `api-dev.lendix.ai` whether the RSA/AES encryption is done transparently by their gateway or
  requires us to encrypt the payload client-side before this is built — the doc's examples show already-
  encrypted blobs in *responses*, but it's ambiguous whether the *request* body (shown in plaintext JSON in
  the doc's own examples) needs client-side encryption too.

### 8.3 Idempotency

`tran_ref_no` must be generated once per disbursement attempt and never reused, and the unique constraint on
`(tenant_id, tran_ref_no)` in §4.1 prevents a double-submit (e.g. accidental double-click) from creating two
bank calls. If the initiate call times out client-side without a clear success/fail, the correct recovery is
a `/disbursement/imps/status` check using the *same* `tran_ref_no` — not a fresh initiate.

### 8.4 Post-initiation confirmation gate (2FA)

This is a control on top of, not a replacement for, the RSA/AES question in §8.2 — that question is left
open/unresolved as originally documented (revisit only if the API owner confirms who's responsible for
request-side encryption). What's new here:

- **Email passcode**: a short-lived, single-use, randomly generated code sent to the *remitter's* configured
  email (`remitter_security_config.notify_email`) — this is a treasury/finance-controller inbox, not the
  vendor's email. Store only a hash of it (never the plaintext) and give it a short TTL (§4.6).
- **Authenticator-app TOTP**: standard RFC 6238 time-based OTP, enrolled once per remitter account via a QR
  code scan into any standard authenticator app (Google Authenticator, Authy, 1Password, etc. — no
  proprietary app, no new dependency beyond a TOTP library). The encrypted seed lives in
  `remitter_security_config.totp_secret_enc`, using the same at-rest encryption approach as §8.2 (do not
  invent a second encryption scheme for this one field).
- **Ordering**: the bank is called only after *both* factors verify successfully for that batch (§5.5) — a
  correct email code alone, or a correct TOTP alone, is not sufficient to release funds.
- **Scope**: the passcode/TOTP challenge is issued once per batch (i.e. once per admin submission, whether
  single-bill or bulk), not once per individual bank call within a batch that got split across the IMPS
  ceiling — that would make bulk payments needlessly tedious without adding security, since the ceiling split
  already happens after the admin has committed to the batch.
- **Enrollment flow**: out of scope to fully design here, but needs at minimum an admin-only "Set up 2FA for
  this remitter account" screen (QR code + email field) before that remitter account can be used for any
  disbursement — flag this as its own small sub-task in the rollout plan (§9).

## 9. Rollout plan

1. **Schema migration**: add `fact_disbursements`, `disbursement_overrides`, `fact_disbursement_batches`,
   `disbursement_bill_allocations`, `remitter_security_config`, `disbursement_otp_challenges`; reconcile
   `fact_bills` vs `fact_invoices` (§5.4) as a prerequisite migration/cleanup PR.
2. **Backend (single-bill core)**: `imps_client.py` + `disbursement_api.py` (initiate, status refresh, list),
   wired against UAT (`api-dev.lendix.ai`, ₹10 ceiling) first. No UI, no 2FA yet — verify via direct API
   calls / Postman.
3. **Backend (2FA gate)**: `remitter_security_config` enrollment (TOTP seed provisioning + QR) and the
   `disbursement-batches/.../confirm` endpoint (§5.5) — build and test this *before* wiring it into the UI,
   since every subsequent step depends on it working.
4. **Backend (bulk)**: `disbursement-batches` create endpoint + allocation logic (§5.1a, §4.5).
5. **Frontend**: Pay button + single-bill modal + 2FA step + status badge + payment history panel on
   `BillsPage.tsx`, then the vendor-level bulk entry point and multi-select modal (§6.1a).
6. **Background poller** for pending disbursements.
7. **Audit review**: confirm every code path in §7.4 (plus 2FA attempts, §8.4) is actually writing
   `wf_audit_log`; add a quick internal query/report to view a bill's full disbursement + audit history for
   QA sign-off.
8. **Staging** (₹1,000 ceiling) end-to-end test with a real (small) IMPS transfer — including at least one
   bulk/multi-bill payment and one deliberately-failed 2FA attempt — before requesting production credentials
   /ceiling (₹1,50,000).

## 10. Open questions to resolve before/while building

- Which table (`fact_bills` vs `fact_invoices`) is authoritative for AP outstanding amounts? (§2, §5.4)
- Which field is canonical for "vendor bank verified" — `dim_suppliers.bank_verified` or
  `supplier_bank_accounts.verified`? (§7.1)
- Does a payment need a second-admin approval step before the bank call fires, or is single-admin-initiate
  acceptable? (§7.3)
- Is request-side RSA/AES encryption our responsibility, or handled transparently by the bank gateway?
  (§8.2) — **explicitly left unresolved per latest guidance; not blocking this build.**
- Where should the per-tenant `nbfcName` → remitter account mapping live — new table, or an existing
  tenant-config table? (§8.1) — `remitter_security_config` (§4.6) should key off whatever this ends up being,
  so settle this one first since it's now a dependency of two features, not one.
- Who is allowed to be the `notify_email` recipient for a remitter account — a fixed treasury inbox, or any
  admin's own email? And is there an existing mailer/SMTP service in this app to reuse, or does one need to
  be added? (§8.4)
- If a remitter account has no 2FA enrolled, should disbursement be hard-blocked (as currently written,
  §6.4) or should there be a break-glass path for initial rollout? Recommend hard-block, but flagging since
  it affects how soon Step 3 of the rollout plan (§9) must ship relative to Step 2.
- For bulk payments that get chunked across the IMPS ceiling (§4.5), should each resulting bank call still
  require its own status confirmation before the *next* chunk fires, or can they all be initiated in
  parallel once the single 2FA challenge for the batch clears? (§5.5)
