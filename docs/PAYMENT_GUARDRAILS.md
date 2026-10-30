# Payment Guardrails

Application-level controls governing when and how a vendor payment can be initiated, on top of the bank-side rails documented in [`DISBURSEMENT_IMPS_API.md`](./DISBURSEMENT_IMPS_API.md) (Disbursement IMPS API + Vendor Payment Module). That doc covers what the bank accepts and returns; this doc covers what our application must enforce *before* a request ever reaches the bank.

Status key used below: **Implemented** (live in `backend_ocr/app/disbursement_api.py` / `frontend/src/pages/PaymentGatewayPage.tsx` / `frontend/src/pages/BillsPage.tsx` today), **Partial**, or **Not yet built** (requirement stated, no implementation exists).

---

## 1. Access control

| Rule | Status |
|---|---|
| Pay Now is only enabled on **Approved** bills — not Draft, Pending, or any other status. | Implemented (`BillsPage.tsx`, row-level and bulk-bar Pay Now both gate on `bill.status === 'APPROVED'`) |
| Payment initiation, OTP verification, and the admin-override path are **admin-role only**. | Implemented (`disbursement_api.py`: `if (body.actor_role or "").lower() != "admin": raise ...`; frontend also hides Pay Now unless `user?.role === 'admin'`) |
| The payment approval chain itself (who can trigger a disbursement, who can override) is admin-only — no secondary approver role currently exists for payments. | Implemented |

---

## 2. Auto-payment eligibility gate (GSTIN + KYC + bank details)

"Auto" here means **no manual beneficiary-detail entry** — for a vendor that is already GSTIN + KYC + bank verified, the admin's only actions are click **Pay Now** and enter the emailed **OTP**. Beneficiary account/IFSC/name are pulled straight from the vendor's verified profile; nothing is hand-typed. This is the default/happy path, not a separate feature to build.

A vendor qualifies for this no-manual-entry path when **all three** are true:
- GSTIN verified (`kyb_status = 'VERIFIED'` / `gstin_verified = true`)
- Bank account on file and verified (`bank_verified = true`)
- The above two combine into a single `can_pay = bank_verified AND gstin_verified` flag surfaced to the UI as "Ready to pay" vs "Needs verification"

If any check fails, beneficiary details are **not** auto-populated and the payment is blocked until an admin manually supplies substitute details via override — see §3. That override path (typing in account/IFSC/name + a reason) is the *exception*; it only appears when verification is missing, never for an already-verified vendor. This gate is enforced server-side (not just UI), so a client can't bypass it: `disbursement_api.py`'s guardrail function raises `HTTPException` with *"Vendor bank account and/or GSTIN not verified — an admin override with a reason is required"* when `gstin_verified` is false and no override is attached.

**Status: Implemented.** Note: a separate *unattended/scheduled* auto-payment queue (bills paid on a cadence with no admin click at all) is a different, larger feature and does **not** exist — no `pipelines/` worker initiates disbursements today. If that's ever built, it must still stop for OTP and still run through this same eligibility gate; that's out of scope for the "auto" behavior described here, which is about skipping manual data entry, not skipping the admin/OTP step.

---

## 3. Manual override + audit trail

When GSTIN/KYC/bank verification fails, an admin can override with substitute beneficiary details, but:

- Override requires **all** of: `override_account_number`, `override_ifsc`, `override_beneficiary_name`, and `override_reason` — missing any of these is rejected with a 400.
- Every override writes a row to `disbursement_overrides`, capturing both the **original** (pre-override) beneficiary details and GSTIN-verified state, and the **override** values supplied — nothing is silently substituted in place.
- Every override additionally writes a `wf_audit_log` entry (`table_name='disbursement_overrides'`, `action='INSERT'`) recording **who** (`actor_name`, `actor_role`), **when** (row `created_at`), and **what changed** (`old_values_jsonb`/`new_values_jsonb` diff: vendor bank/GSTIN verification state before, override values after, plus the stated reason).
- In a bulk/multi-bill batch, **one override row is written per bill**, so each bill's audit trail stands alone even when several bills are overridden and paid in the same batch.

**Status: Implemented.**

---

## 4. OTP verification

- OTP is a 6-digit numeric code, generated server-side, **SHA-256 hashed at rest** (`otp_code_hash`) — the plaintext code is never stored, only emailed.
- Delivered by email to the admin's registered address (masked in the UI response as `otp_sent_to`, e.g. `a***n@onsumm.ai`).
- Verification requires the disbursement to still be in `PENDING_OTP` status; a capped number of attempts is enforced (`otp_attempts`, max 5) before the OTP is invalidated.
- A bulk/multi-bill batch shares **one OTP** across all selected bills — resend is disabled for bulk batches specifically because resending would desync the shared hash; the flow restarts instead.

**OTP validity window:** `OTP_TTL_SECONDS = 120` (2 minutes) — deliberately tighter than the unrelated password-reset OTP (10 minutes, a separate constant in `main.py`, untouched), since this one gates a live money movement. Applies uniformly to single-bill and bulk-batch payments, including OTPs issued via resend.

**RSA note:** RSA encryption applies to the *bank API payload* (per `DISBURSEMENT_IMPS_API.md`'s `encryptedKey`/`encryptedData` fields), not to the OTP itself — the OTP is a symmetric, hashed, single-use code at the application layer. These are two independent protections and should not be conflated: RSA protects the wire payload to ICICI/AU Bank; OTP+admin-only+audit-log protects who is allowed to trigger that payload in the first place.

---

## 5. Amount constraints

- **Cannot exceed outstanding balance:** enforced server-side per bill — `if amount <= 0 or amount > outstanding_amount + 0.01: reject`. `outstanding_amount` on `fact_invoices` already reflects the bill **net of TDS deduction** (it's computed from `total_amount − amount_paid`, where the payable base already accounts for `tds_amount`/`net_payable` set during TDS processing) — so this one check inherently enforces "cannot pay more than what's owed after TDS," not a separate rule layered on top.
- **Minimum ₹1:** inherited from the bank side (ICICI's hard minimum, documented in `DISBURSEMENT_IMPS_API.md` §Amount Limits). The app's `amount <= 0` check combined with the bank's own validation covers this; the app does not currently add a stricter minimum of its own.
- **Per-environment maximums** (UAT ₹10 / Staging ₹1,000 / Production ₹1,50,000 / ICICI hard cap ₹5,00,000) are bank-side limits from `DISBURSEMENT_IMPS_API.md` — the app does not duplicate these caps locally; a request over the bank's limit surfaces as a bank-side error (see that doc's ActCode table), not a pre-flight app rejection. **Not yet built:** an app-side pre-check that mirrors these environment caps and fails fast with a clearer message before hitting the bank.

**Status: Implemented** for the outstanding-amount cap; **Partial** for minimum/maximum enforcement (bank-side only, no app-side pre-check).

---

## 6. Multi-bill / bulk payment

- Multiple bills for the **same vendor** can be paid together in a single initiation.
- Each bill's amount is entered independently (partial payment per bill is allowed, capped per-bill by that bill's own outstanding amount — see §5) — this is not "one amount split evenly," each line item is its own value.
- One shared OTP covers the whole batch; verifying it confirms every bill in the batch together.
- Each bill in the batch still gets its own override row and audit entry if it needed one (§3), and its own bank call/`tranRefNo`/status tracking — bulk is a UI/OTP convenience, not a single merged bank transaction.

**Status: Implemented** (`initiateBulkDisbursement` / `verifyBatchDisbursementOtp` on the frontend, batch guardrail + per-bill override logic on the backend).

---

## 7. Post-payment status check + settlement

Initiating a payment (OTP verification) does not by itself guarantee the bank has moved money — the bank can also return `PENDING`, in which case the app must call the status API before treating a bill as settled.

- **On OTP verify:** the app calls the bank's initiate/status result immediately. If it comes back `SUCCESS`, settlement runs right away in the same transaction as OTP verification (`_apply_success`, in `disbursement_api.py`) — no separate status call is needed for the immediate-success case.
- **On `PENDING`:** `POST /api/disbursements/{disbursement_id}/refresh-status` calls the bank's status endpoint (`/disbursement/imps/status` / `/vendor-payment/status` per `DISBURSEMENT_IMPS_API.md`). If that check now reports `SUCCESS`, settlement runs at that point instead. A disbursement not in `PENDING` short-circuits and returns its current status unchanged — refresh only ever does work on a genuinely pending transaction.
- **Settlement, once `SUCCESS` is confirmed (either path):**
  - The bill's `amount_paid` is incremented by the amount just paid, and `outstanding_amount` is recomputed as `total_amount − amount_paid` (floored at 0).
  - `payment_status` is set to **`paid`** when the recomputed outstanding amount is ≤ ₹0.01 (rounding tolerance), or **`partial`** when a positive balance remains — this is what turns a partially-paid bill into a **"Partial · ₹X"** badge versus a fully **"Paid"** one in the Bills page payment cell.
  - The demo remitter account balance is debited by the paid amount, so the Payment Gateway page's account-balance KPI stays consistent with what's actually been disbursed.
  - A `bill_approval_chain` row (`step=99`, action `payment_paid` or `payment_partial`) records the settlement itself as part of the bill's own audit trail, separate from the `wf_audit_log` override trail in §3 — every settlement is traceable back to the specific bank RRN that produced it.
- **Bulk batches:** each bill in a batch is settled independently by the same logic — one bill coming back `SUCCESS` while another in the same batch is `PENDING`/`FAILED` does not block or roll back the others; each is judged on its own bank result.

**Status: Implemented** (`_apply_success`, `verify_otp`, `refresh_status`, and their batch equivalents in `backend_ocr/app/disbursement_api.py`).

---

## 8. Summary — enforced today vs. open gaps

| Guardrail | Status |
|---|---|
| Pay Now enabled only for Approved bills | Implemented |
| Admin-only payment access + approval chain | Implemented |
| GSTIN + KYC + bank-verified gate before straight-through pay | Implemented |
| "Auto" pay for verified vendors — no manual beneficiary entry, just OTP + Pay Now | Implemented |
| Unattended/scheduled auto-payment queue (no admin click at all) — out of scope here | Not built, not required |
| Manual override requires full reason + substitute details | Implemented |
| Every override audit-logged (who/when/what) | Implemented |
| Admin-only email OTP, hashed at rest | Implemented |
| OTP window = 120 seconds | Implemented |
| Amount ≤ outstanding (post-TDS) | Implemented |
| Amount ≥ bank minimum (₹1) | Implemented (bank-side; no app-side duplicate check) |
| Per-environment amount ceilings | Bank-side only — no app-side pre-check |
| Multi-bill same-vendor payment, per-bill amount entry | Implemented |
| Post-payment status check on `PENDING`, settlement (outstanding/amount_paid/payment_status) on confirmed `SUCCESS` | Implemented |
