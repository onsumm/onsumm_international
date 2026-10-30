# Payments — Status Reporting and Reconciliation

> Commits: `25417cc`, `73ab726` — October 16, 2026
> Tenant worked: `d0e00000-0000-0000-0000-000000000003` (BrightLoans / ACM)
> Banks involved: AU Small Finance Bank (live UAT), ICICI, HDFC
> Related: `docs/PAYMENT_GUARDRAILS.md`, `docs/DISBURSEMENT_IMPS_API.md`

---

## Why this session happened

A ₹6 AU payment sat on the receipt card headed **"Processing at the bank"** with
the word **"SUCCESS"** printed directly underneath it. Pulling that thread found
a cluster of defects with one shape in common: the system reported a payment
outcome that the bank had not given.

None of them were visible as errors. Every status shown was a real string from a
real gateway response; the bug each time was *which* string, or *whether anyone
had asked recently enough for it to still be true*.

Net effect on the worked tenant: three payments that failed at AU on 2026-10-15
had been reading as "processing" for a day, and nothing in the system would ever
have corrected them.

---

## 1. Bugs fixed

"Silent" means nothing surfaced the error — the UI rendered a plausible status
and the test suite passed, because no test exercised the path.

| # | Area | Bug | Effect | Fix |
|---|---|---|---|---|
| 1 | `routers/disbursement.py` | AU answers an enquiry at **two levels** — batch (`responseMessage: SUCCESS`) and transaction (`status: SENTTOCORE`). Lendix flattens the batch word into `statusMessage`, which was stored and rendered verbatim | Receipt read "Processing at the bank" with "SUCCESS" beneath it. Silent; both strings were genuine | `_display_message()` — a message carrying real content is kept; a bare outcome word claiming something other than the resolved status is replaced. "ACCEPTED" next to PENDING agrees and stays |
| 2 | `routers/disbursement.py` | `refresh_status` wrote the **canonical** status to the column but returned the gateway's **raw** word to the caller and gated the receipt mail on it | Latent. Had AU returned `PAID`, the row would settle the bill while the API answered `"PAID"` — which `toneOf()` maps to neither ok nor wait, so a settled payment would render **"Payment failed"**, and no receipt would be sent. Both `verify_otp` paths already used the canonical value | One `new_status` resolved once, used for the column, the reply and the receipt gate |
| 3 | `routers/disbursement.py` | `"demo": True` **hardcoded** in `disbursement_stats` | Hub showed "DEMO — no real funds move" and stamped receipts "NO REAL FUNDS MOVED" regardless of where the gateway pointed — including on the day the base URL is switched to production | `config.is_demo_gateway()` — true on forced-success or a non-production endpoint. An endpoint it cannot place is treated as **production**: wrongly promising safety over a live rail is the costly direction |
| 4 | `routers/disbursement.py` | "This account so far" aggregate had **no bank filter** while sitting directly beneath one bank's account and balance | AU's card read "29 payments, 25 failed" when AU had 6 payments and 2 failures; the other 23 were ICICI's and HDFC's | Aggregate scoped to the selected `bank_provider` |
| 5 | **Architecture** | **Nothing reconciled PENDING payments in the background.** `refresh-status` was reachable only from the receipt card, polling while — and only while — the user sat on that screen | A payment froze as PENDING the moment you navigated away. Runs both ways: a settled payment never credits its bill or sends its receipt; a failed one leaves money looking spent *and* the in-flight guard blocks its own retry | `domain/payments/sweeper.py` — enquires every 5 minutes until terminal |
| 6 | `payment/usePaymentFlow.ts` | Receipt card polled at a **flat 3s** — ~20 enquiries per 60s budget against a bank whose document asks for one every 5 minutes — then stopped entirely on navigate-away | Maximum load exactly when the status was least likely to have changed; zero coverage over the window where it actually changes | Backoff 3s ×1.5 to a 15s cap (~8 per budget). A fast settle still lands on screen at once |
| 7 | Data (3 rows) | Three AU payments resolved **FAILED at the bank** on 2026-10-15; local rows still read PENDING a day later | Money looked spent; the bills were blocked from retry | Reconciled through the real audited path, not a manual `UPDATE` |
| 8 | `routers/disbursement.py` | **Introduced and fixed in this session.** Adding the sweeper created a second caller that could settle the same payment as a user's Check status | Both see PENDING, both get SUCCESS back, both credit the bill — the vendor credited twice for one transfer | `reconcile_disbursement`'s UPDATE carries `AND status='PENDING'`; `rowcount == 1` claims the *transition*. Only the claiming caller settles and mails |

### Known, not fixed

| # | Area | Finding |
|---|---|---|
| 9 | `app/disbursement_api.py` | A stale 52 KB **duplicate** of the disbursement router, dated 2026-10-13. Not mounted (`main.py` imports `routers.disbursement`), so it is inert — but it still carries bug #3 and its own `refresh_status`. An easy file to edit by mistake. Deletion not attempted this session |
| 10 | Remitter accounts | AU's remitter row is seeded with a **fictional ₹50,00,000** balance |
| 11 | `pipelines/bank_verification.py` | `BankVerificationWorker` still disabled — 11 workers, not 12. Needs the DDL in §4 |

---

## 2. What the sweeper does, and deliberately does not

`backend_ocr/app/domain/payments/sweeper.py`, started by
`domain/payments/scheduler.py` from app startup under advisory lock
`875_291_004_419` (distinct from the pipeline's `…417` and review's `…418`).

| Property | Value | Why |
|---|---|---|
| Cadence | 300s | AU's document for statusCode 100/101: *"Re-enquire every 5 minutes."* |
| Minimum age | 60s | Don't race the receipt card; the bank has had no time to act |
| Batch | 25 | One pass cannot become a long run of gateway calls |
| Give up after | 7 days | Keeps the row **PENDING** and logs a warning every pass |
| Actor | `system:disbursement-sweeper` | "Who changed this" is the first question asked of a payment record |

**It never initiates a payment.** AU's document says re-enquire, *do not
re-initiate*. A test asserts the module references no initiate path at all, so
the worst a bug here can do is leave a stale row — never a second transfer.

**An aged-out payment is reported, not closed.** Writing FAILED would assert
something the bank never said, and would release the bill for a second attempt.
"We never found out" is a real state and operations needs to see it.

**It runs in the API process, not `pipelines/`.** It reuses
`reconcile_disbursement` — the same psycopg2 path the Check status button runs.
Settlement credits a bill and draws down a remitter balance; an asyncpg
reimplementation in a pipeline worker would have been a second copy of that
logic, free to drift from the one the endpoint uses.

---

## 3. Testing

| Suite | Result |
|---|---|
| `tests/test_disbursement_sweeper.py` | **17 new** — all passing |
| `tests/test_disbursement_guards.py` | 56 (24 new this session) |
| Full Python suite | 918 passed, 22 failed |
| Frontend (`vitest`) | 449 passed, 2 failed |

The 22 Python and 2 frontend failures are pre-existing and unrelated
(`test_license_route_map`, `test_tenant_route_map`, `test_nova_panel_states`,
`src/api/authedFiles.test.ts`), verified unchanged across the session.

**Isolation.** The sweeper tests were re-run with `psycopg2.connect` *and*
`httpx` sabotaged to raise on call; all 17 still pass. This is not a formality —
an earlier test in this area ran the real settlement path and credited a live
invoice, which then had to be found and reverted.

### Verified against the live bank

| Check | Result |
|---|---|
| Sweeper start | `started — every 300s, batch 25, giving up after 7 days` |
| First pass (16:23:28) | `{'checked': 1, 'settled': 0, 'failed': 0, 'still_pending': 1, 'errors': 0}` |
| Second pass (16:28:29) | identical — cadence holding at exactly 300s |
| Stale row repair | `PAYDE2D1F5E01394D` PENDING → FAILED by `system:disbursement-sweeper` |
| AU compound key | `(00, 101)` / `SENTTOCORE` → INTERIM → `PENDING` — correctly refused to settle |

---

## 4. Open items for the team

1. **Run the blocked DDL** to re-enable bank verification:
   ```sql
   ALTER TABLE dim_suppliers     ALTER COLUMN bank_verified DROP NOT NULL;
   ALTER TABLE dim_end_customers ALTER COLUMN bank_verified DROP NOT NULL;
   ```
2. **Decide the fate of `app/disbursement_api.py`** (#9) — delete, or mount it and retire the other.
3. **Replace AU's fictional remitter balance** (#10) with the real figure before anyone reads it as fact.
4. **`DISBURSEMENT_FORCE_SUCCESS` is `false`** and the IP is whitelisted, so the gateway is live. The base URL is `api-dev.lendix.ai` → `api.aubankuat.in`, a non-production path end to end — the DEMO badge is correct today and will disappear on its own when that URL changes.

---

## 5. State of AU at close

Six AU payments have been made through the app. None has succeeded.

| When | Amount | Status | UTR | Cause |
|---|---:|---|---|---|
| 10-15 15:02 | ₹8 | FAILED | — | `401 Invalid API key` (pre-whitelist) |
| 10-15 15:04 | ₹9 | FAILED | — | `400 All vendors failed` (pre-whitelist) |
| 10-15 15:07 | ₹9 | FAILED | — | Rejected at AU, code 99 |
| 10-15 15:10 | ₹9 | FAILED | — | Rejected at AU, code 99 |
| 10-15 15:21 | ₹2 | FAILED | — | Rejected at AU, code 99 |
| 10-16 10:07 | ₹6 | PENDING | `628915671352` | In flight — `SENTTOCORE` |

The 10-16 payment is the **first AU payment through the app to receive a UTR**,
consistent with the IP whitelist landing that morning. It is the live end-to-end
test of the settlement path: when AU flips it to `PAID`, the sweeper should
credit the bill and send the receipt with nobody watching.

> A seventh AU payment, `payment167571` (₹10, `PAID`, UTR `628515663581`,
> 2026-10-12), reached settlement and is **not** in this table. It was a direct
> curl against AU's API while verifying the status-code mapping — it never went
> through the app, so there is no `fact_disbursements` row and no
> `disbursement_gateway_calls` entry. It proves the rail and the mapping work;
> it is not an app payment and correctly appears in no total.
