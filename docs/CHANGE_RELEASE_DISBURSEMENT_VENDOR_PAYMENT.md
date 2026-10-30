# Change Release — Disbursement / Vendor Payment Module

**Date:** 2026-07-22
**Related:** `docs/DISBURSEMENT_IMPS_API.md`, `docs/PAYMENT_GUARDRAILS.md`, `docs/DISBURSEMENT_PAYMENT_INTEGRATION_PLAN.md`

Running log of changes to the vendor payment / disbursement flow (`backend_ocr/app/disbursement_api.py`, `frontend/src/pages/BillsPage.tsx`, `frontend/src/pages/PaymentGatewayPage.tsx`) and to the two API/guardrail docs that describe it.

---

## Bills page — Pay Now gating, layout, single-bill payment view

- **Pay Now now only enabled on Approved bills** — row-level and the (new) bulk-action-bar button both gate on `bill.status === 'APPROVED'`. Previously it was offered on any unpaid bill regardless of approval state.
- **Payment cell layout**: the payment-status badge (e.g. "Partial · ₹X" / "Unpaid") and the Pay Now button now stack vertically instead of sitting side by side.
- **Pay Now added to the bottom bulk-action bar** — same gating and same `setPayGateway` call as the row button, shown only when exactly one qualifying (Approved, unpaid, with a linked supplier) bill is checked. Same functionality, second entry point, not a separate payment path.
- **Bill-level Pay Now now shows only that one bill**, not the vendor's full outstanding-bills list. `PaymentGatewayPage` filters to `initialInvoiceId` when it's set (i.e. opened from the Bills page). The Vendors page's "Pay Vendor" bulk flow is unchanged — it never sets `initialInvoiceId`, so it still shows the full outstanding list for manual multi-select.

## `docs/PAYMENT_GUARDRAILS.md` — created

New doc enumerating the application-level controls that sit in front of the bank API (`DISBURSEMENT_IMPS_API.md` covers what the bank accepts/returns; this doc covers what the app enforces before a request ever reaches it):

- Access control (Approved-only Pay Now, admin-only payment/OTP/override access)
- GSTIN + KYC + bank-verified eligibility gate — "auto" pay means no manual beneficiary entry (details pulled from the verified vendor profile), not an unattended/scheduled queue (confirmed no such queue exists in `pipelines/`)
- Manual override + full audit trail (who/when/what, one override row per bill even in a batch)
- OTP verification mechanics and window
- Amount constraints (≤ outstanding post-TDS, ≥ bank minimum, per-environment ceilings — bank-side only, no app-side pre-check yet)
- Multi-bill/bulk payment (same vendor, independent per-bill amounts, one shared OTP)
- Post-payment status check + settlement (see below)

Every claim in the doc was verified directly against the live code in `disbursement_api.py`/`BillsPage.tsx`/`PaymentGatewayPage.tsx` before being written down, not just transcribed from the request.

## OTP window tightened: 10 minutes → 2 minutes

`OTP_TTL_SECONDS` in `disbursement_api.py` changed from `600` to `120`, specifically for vendor-payment OTPs — this gates a live money movement, so it's deliberately tighter than the unrelated password-reset OTP (10 minutes, a separate constant in `main.py`, left untouched). The OTP email template's "valid for 10 minutes" copy was updated to "valid for 2 minutes" to match.

## Post-payment status check + settlement — verified, documented

Confirmed (not newly built — already implemented) that a `SUCCESS` result, whether from the immediate OTP-verify response or a later `refresh-status` poll on a `PENDING` transaction, drives settlement: `amount_paid`/`outstanding_amount` update, `payment_status` flips to `paid` (outstanding ≤ ₹0.01) or `partial` (balance remains), the demo remitter balance is debited, and a `bill_approval_chain` audit row is written. Documented as §7 of `PAYMENT_GUARDRAILS.md`; each bill in a bulk batch settles independently of the others' outcomes.

## `docs/DISBURSEMENT_IMPS_API.md` — Vendor Payment Module added

Added a full second module to the doc, `# Vendor Payment Module` (`POST /vendor-payment/pay`, `POST /vendor-payment/status`), alongside the existing Disbursement IMPS API — same ICICI/AU Bank rails and RSA encryption, plus two vendor-payment-specific request-only metadata fields:

- `vendorName` — the payee/supplier being paid
- `month` — billing period being settled, `YYYY-MM`

Both are validated, logged, and stored, but stripped before the actual bank call — they never appear in the bank's response. Also added a "Supported Vendors" table (ICICI primary, AU Bank secondary) to both modules.

## Disbursement mock brought in line with the Vendor Payment Module

`disbursement_api.py`'s mock bank calls (`_mock_imps_initiate` / `_mock_imps_status`) were still shaped like the generic `/disbursement/imps/initiate` endpoint — stale, since this code path exclusively pays vendor bills, which is exactly what the new Vendor Payment Module documents. Since the mock never calls a real bank (demo/showcase only, per this file's own header comment), this was a documentation-accuracy fix, not a behavior change to any real integration:

- `vendorInfo.endpoint` now reports `VENDOR_PAYMENT_INITIATE` / `VENDOR_PAYMENT_STATUS` instead of the old generic label.
- Added `_get_vendor_name_and_month()` — resolves the payee's name (`dim_suppliers.name`) and the bill's billing period (`stg_bills.service_month`) at OTP-verify time (both single-bill and bulk paths), and passes them into the mock exchange under a `requestMetadata` key so they land in `raw_response_jsonb`/the audit trail — matching the doc's framing that these are request-only metadata, never echoed back by the real bank.
- **No new DB columns.** `vendor_name`/`month` are always re-derivable from the `invoice_id`/`supplier_id` foreign keys already on `fact_disbursements`, so nothing needed persisting — resolved fresh at verify time instead.

**Verification:** `ast.parse()` clean, backend restarted cleanly (`Application startup complete`, no errors) after every change in this batch.

**Still open / not addressed in this pass:**
- App-side pre-check mirroring the bank's per-environment amount ceilings (UAT ₹10 / Staging ₹1,000 / Production ₹1,50,000) — currently bank-side only, surfaces as a bank error rather than a fast app-side rejection.
- No unattended/scheduled auto-payment queue exists (confirmed, not required per this round's scope) — every payment still requires an admin to click Pay Now and complete OTP.
