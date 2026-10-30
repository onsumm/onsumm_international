# Payment Hub — QA checklist before handover

Scope: `frontend/src/pages/PaymentHubPage.tsx` (+ `payment-hub.css`), the two
new backend reads (`GET /api/disbursements/queue`, `GET /api/disbursements/recent`
in `backend_ocr/app/routers/disbursement.py`), and the existing pay wizard
(`frontend/src/pages/payment/*`) that the hub hands off to.

**How this was verified:** typecheck, lint, and the automated test suite were
run and are green (below). The actual browser click-through — the Table/Lanes
toggle, scrolling, and the hand-off into the wizard — was **not** driven by me;
I have no browser-automation tool in this environment. Everything under
"Needs your own click-through" is still open until someone looks at it in a
real browser.

## Automated, verified by me

- [x] `npm run typecheck` — clean
- [x] `npx eslint src/pages/PaymentHubPage.tsx` — clean
- [x] `npx vitest run src/pages/payment` — 43/43 passing (BillSelectStep →
      ReviewStep → OtpStep → PaymentProcessing → ReceiptStep/ReceiptPrintout,
      plus `paymentFlow.test.ts` and `receiptPdf.test.ts`)
- [x] `GET /api/disbursements/queue` and `/recent` registered and reachable
      (401 without a token, not 404) against the local backend
- [x] Root `config.py` and `backend_ocr/app/config.py` both resolve `DEV_DB_URL`
      (the repo's `.env` didn't set `DB_URL`, so before this fix the backend,
      pipelines, and Nova service were all silently falling back to a broken
      empty connection string)

## Needs your own click-through (I can't drive a browser here)

**Payment Hub**
- [ ] Table view renders the full vendor list, scrolls (fixed: `.pmh` had no
      scroll container of its own — `.shell__content` is `overflow:hidden`
      for every route except Upload)
- [ ] Lanes view: each lane scrolls independently past ~560px instead of
      stretching the page (fixed — was unbounded before)
- [ ] Table ↔ Lanes toggle persists the current search/filter
- [ ] Search by vendor name / GSTIN filters both views
- [ ] Stage filter tabs (All / Ready / OTP sent / Needs verification) filter
      both views and their counts match
- [ ] KPI strip (balance, owed, disbursed, needs attention) matches the numbers
      in Bills/Vendors for the same tenant
- [ ] "Refresh" button re-fetches without a full-page loading flash
- [ ] Recent activity feed shows real disbursements, RRNs link up
- [ ] Clicking a vendor (either view) lands on `payPath(supplierId)` — the
      existing pay wizard — with that vendor pre-selected

**Pay wizard (reused, not new — confirm the hand-off didn't break anything)**
- [ ] Select bills → Review → OTP → Processing → Receipt, for a verified vendor
- [ ] OTP step: wrong code, expired code, resend
- [ ] Unverified vendor: override screen (`BeneficiaryCard.tsx`) appears,
      records a reason, and the payment proceeds
- [ ] Bulk pay (multiple bills at once) still enforces `require_role("admin")`
      server-side (this was a real bug fixed this session — it used to trust
      a client-supplied `actor_role` string)
- [ ] Partial failure on a bulk run shows per-bill outcome, not a single
      pass/fail for the whole batch
- [ ] Receipt PDF download works and matches the on-screen receipt

**Cross-cutting**
- [ ] Payment Hub sidebar entry only shows for `payables.view`/`payables.approve`
      + the `disbursement` service entitlement (hide it for a tenant without
      that service and confirm the direct URL 403s / redirects, doesn't 500)
- [ ] Mobile width (≤768px): Lanes view — `.pmh__lanes` drops to one column;
      confirm it's still usable, not just present

## Known, deliberate gaps (not bugs — scope decisions made earlier this session)

- No two-person maker/checker approval screen. The backend has one admin
  initiating a payment and a single emailed OTP releasing it — there is no
  second approver role to build a UI for. The original design mockup showed
  "awaiting checker" lanes; those were intentionally left out rather than
  built against a workflow that doesn't exist server-side.
- Settling animation, partial-failure detail, and the vendor-override screen
  from the mockup are not separate new components — they're the existing
  `PaymentProcessing.tsx`, receipt flow, and `BeneficiaryCard.tsx` inside the
  pay wizard, reused rather than duplicated.
