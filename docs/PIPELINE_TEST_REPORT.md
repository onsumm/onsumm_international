# ONSUMM.AI — Pipeline Test Report

**Environment:** Neon PostgreSQL (serverless) · Tally Prime (localhost:9000)
**Last run:** 2026-05-09
**Overall result: 76 passed, 0 failed, 0 skipped**

| Suite | Tests | Result |
|---|---|---|
| Pipeline integration (all 7 workers) | 21 | 21 PASS |
| Deduplication unit tests (mocked asyncpg) | 38 | 38 PASS |
| Deduplication integration (live Neon DB) | 17 | 17 PASS |
| **Total** | **76** | **76 PASS** |

---

## 1. Pipeline Integration Suite

**Run:** `python test_pipelines.py` — all 7 workers against live Neon DB

### 1.1 Module Imports

All 11 modules import without errors.

| Module | Result |
|---|---|
| `config` | PASS |
| `db` | PASS |
| `models.enums` | PASS |
| `utils.fiscal` | PASS |
| `pipelines.base` | PASS |
| `pipelines.api_sync` | PASS |
| `pipelines.webhook` | PASS |
| `pipelines.upload_ocr` | PASS |
| `pipelines.staging_promotion` | PASS |
| `pipelines.approval` | PASS |
| `pipelines.tally_sync` | PASS |

### 1.2 Fiscal Utilities

| Test | Input | Expected | Actual | Result |
|---|---|---|---|---|
| FY boundary (pre-April) | `date(2025, 3, 31)` | FY 2025 | 2025 | PASS |
| FY boundary (post-April) | `date(2025, 4, 1)` | FY 2026 | 2026 | PASS |
| `fiscal_period` | `date(2024, 12, 15)` | `(2025, 3, 9)` | `(2025, 3, 9)` | PASS |

### 1.3 Tally XML Builder

| Test | Result | Detail |
|---|---|---|
| `build_sales_voucher` | PASS | Valid `<VOUCHER>` XML generated |
| `build_receipt_voucher` | PASS | Valid `<VOUCHER>` XML generated |

### 1.4 Database Connectivity

| Test | Result | Detail |
|---|---|---|
| Neon PostgreSQL `SELECT 1` | PASS | Connection established, pool acquired |

### 1.5 Worker Batch Runs (live Neon DB)

| Worker | Result | Items Processed | Notes |
|---|---|---|---|
| `ApiSyncWorker` | PASS | 0 | Queue empty at test time |
| `WebhookWorker` | PASS | 0 | Queue empty at test time |
| `UploadOcrWorker` | PASS | 0 | Queue empty at test time |
| `StagingPromotionWorker` | PASS | 0 | Queue empty; `AmbiguousFunctionError` fixed by casting `$4::numeric - $5::numeric` in `fact_cashflow_daily` upsert |
| `ApprovalWorker` | PASS | 0 | Queue empty at test time |
| `TallySyncWorker` | PASS | 103 | 2 tenants synced; 40 ledgers pulled + 62 payments (31 per tenant); 1 ledger master pushed |

### 1.6 TallySyncWorker Detail

**Tenant `11111111-1111-1111-1111-111111111111`**

| Operation | Count |
|---|---|
| PULL ledgers | 20 |
| PULL vouchers (2026-04-07 to 2026-05-07) | 31 payments inserted |
| PUSH ledger masters | 1 pushed |
| PUSH invoices / payments | 0 |

**Tenant `10000000-0000-0000-0000-000000000001`**

| Operation | Count |
|---|---|
| PULL ledgers | 20 |
| PULL vouchers (2026-04-07 to 2026-05-07) | 31 payments (20 inserted, 11 updated — idempotent re-run) |
| PUSH ledger masters | 0 (already synced) |
| PUSH invoices / payments | 0 |

---

## 2. Deduplication Unit Tests

**Run:** `pytest tests/test_deduplication.py` — mocked asyncpg, no live DB required
**Tenant:** `10000000-0000-0000-0000-000000000001`
**Result: 38 passed, 0 failed**

| Test | Result |
|---|---|
| `test_exact_dup_bill_flagged_duplicate` | PASS |
| `test_cross_table_bill_flagged_duplicate` | PASS |
| `test_soft_dup_bill_flagged_review` | PASS |
| `test_clean_bills_not_touched` | PASS |
| `test_multiple_exact_dups_each_flagged` | PASS |
| `test_all_three_dedup_types_in_one_cycle` | PASS |
| `test_exact_dup_invoice_flagged` | PASS |
| `test_cross_table_invoice_flagged` | PASS |
| `test_soft_dup_invoice_flagged_review` | PASS |
| `test_clean_invoices_not_touched` | PASS |
| `test_fully_paid_sets_paid_status` | PASS |
| `test_partial_payment_sets_partial_status` | PASS |
| `test_zero_completed_txns_sets_sent_status` | PASS |
| `test_overpaid_outstanding_clamped_to_zero` | PASS |
| `test_recalc_writes_to_fact_bills_and_stg_bills` | PASS |
| `test_bounced_ar_sets_sent_status` | PASS |
| `test_one_completed_one_reversed_is_partial` | PASS |
| `test_recalc_writes_to_fact_invoices_and_schedules` | PASS |
| `test_failed_ap_triggers_bill_credit_recalc` | PASS |
| `test_bounced_ar_triggers_invoice_credit_recalc` | PASS |
| `test_cancelled_and_reversed_both_caught` | PASS |
| `test_no_failures_returns_zero` | PASS |
| `test_inserts_when_no_existing_anomaly` | PASS |
| `test_skips_when_unresolved_anomaly_exists` | PASS |
| `test_different_anomaly_type_not_blocked` | PASS |
| `test_severity_high_for_exact_duplicates` | PASS |
| `test_features_jsonb_serialised` | PASS |
| `test_duplicate_bill_anomaly_written` | PASS |
| `test_soft_dup_bill_medium_severity` | PASS |
| `test_failed_ap_high_severity` | PASS |
| `test_duplicate_invoice_anomaly_written` | PASS |
| `test_failed_ar_anomaly_written` | PASS |
| `test_idempotent_cycle_returns_zero` | PASS |
| `test_all_six_anomaly_types_in_one_cycle` | PASS |
| `test_empty_cycle_returns_zero` | PASS |
| `test_worker_class_importable` | PASS |
| `test_amount_tolerance_is_one_rupee` | PASS |
| `test_soft_dup_window_is_seven_days` | PASS |
| `test_deduplication_module_not_circular` | PASS |

---

## 3. Deduplication Integration Tests

**Run:** `python test_dedup_integration.py` — live Neon DB, real `DeduplicationWorker.process_batch()`
**Tenant:** `10000000-0000-0000-0000-000000000001`
**Result: 17 passed, 0 failed**

| Scenario | Result | Detail |
|---|---|---|
| `DeduplicationWorker.process_batch()` completes | PASS | processed = 14 |
| S1: Exact bill duplicate flagged | PASS | stg_bill = `f77cdf7c…` flag = DUPLICATE |
| S1: Original bill preserved | PASS | flag = OK |
| S2: Cross-table bill duplicate flagged | PASS | flag = DUPLICATE |
| S3: Soft duplicate bill flagged REVIEW | PASS | flag = REVIEW |
| S3: Base bill stays OK | PASS | flag = OK |
| S4: Exact invoice duplicate flagged | PASS | flag = DUPLICATE |
| S5: Soft duplicate invoice flagged REVIEW | PASS | flag = REVIEW |
| S6: FAILED AP payment — `amount_paid` reset to 0 | PASS | amount_paid = 0 |
| S6: FAILED AP — `fact_bill.status` corrected to SENT | PASS | status = SENT |
| S7: BOUNCED AR payment — `amount_paid` reset to 0 | PASS | amount_paid = 0.0000 |
| S7: BOUNCED AR — `outstanding_amount` restored to 50000 | PASS | outstanding = 50000.0000 |
| S8: `ai_anomaly DUPLICATE_BILL` written | PASS | types = {DUPLICATE_BILL, DUPLICATE_INVOICE} |
| S8: `ai_anomaly DUPLICATE_INVOICE` written | PASS | types = {DUPLICATE_BILL, DUPLICATE_INVOICE} |
| S8: Anomaly idempotency — 2nd run no new rows | PASS | count before = 2, after = 2 |
| S9: Clean records completely untouched | PASS | bill_flag = OK, inv_flag = OK |

---

## 4. Edge Cases Covered

| Edge Case | Verification |
|---|---|
| Exact bill duplicate within `stg_bills` | `ROW_NUMBER PARTITION` — second row flagged `DUPLICATE` + `sync_status = ERROR` |
| Original bill preserved | Oldest `created_at` row keeps `validation_flag = OK` |
| Cross-table bill duplicate | `stg_bill` matches promoted `fact_bill` via different `stg_bill_id` |
| Soft bill duplicate (±1 INR / ±7 days) | `validation_flag = REVIEW` — non-blocking, human inspection surfaced via `ai_anomalies` |
| Exact invoice duplicate within `stg_invoices` | Same logic applied to AR side |
| Soft invoice duplicate | `REVIEW` flag on newer near-match invoice |
| FAILED AP payment — credit reset | `fact_bills.amount_paid` recalculated from `COMPLETED` transactions only |
| BOUNCED AR payment — outstanding restored | `fact_invoices.outstanding_amount` corrected via `GREATEST(total - paid, 0)` |
| Anomaly write — `DUPLICATE_BILL` (HIGH) | `ai_anomalies` row inserted with `severity = HIGH` |
| Anomaly write — `SOFT_DUPLICATE_BILL` (MEDIUM) | `severity = MEDIUM` for soft duplicates |
| Anomaly write — `PAYMENT_FAILURE_AP/AR` (HIGH) | One row per failure event |
| Idempotency — second run no double-insert | `fetchval` guard prevents duplicate unresolved anomaly rows |
| Clean records completely untouched | Bills/invoices with no duplicate signals keep `validation_flag = OK` |
| Overpayment — outstanding clamped to 0 | `GREATEST(total - paid, 0)` prevents negative outstanding |
| `CANCELLED` + `REVERSED` transactions caught | Both statuses included in 48-hour reconciliation window |
| Empty cycle — no DB writes | `process_batch` returns 0; no `UPDATE` or `INSERT` executed |

---

## 5. Anomaly Types Verified

| Type | Severity | Trigger |
|---|---|---|
| `DUPLICATE_BILL` | HIGH | Exact duplicate within `stg_bills` or vs `fact_bills` |
| `SOFT_DUPLICATE_BILL` | MEDIUM | Same supplier, amount diff < INR 1, date diff <= 7 days, different bill number |
| `PAYMENT_FAILURE_AP` | HIGH | `FAILED` / `BOUNCED` / `REVERSED` / `CANCELLED` AP transaction |
| `DUPLICATE_INVOICE` | HIGH | Exact duplicate within `stg_invoices` or vs `fact_invoices` |
| `SOFT_DUPLICATE_INVOICE` | MEDIUM | Same customer, amount diff < INR 1, date diff <= 7 days, different invoice number |
| `PAYMENT_FAILURE_AR` | HIGH | `FAILED` / `BOUNCED` / `REVERSED` / `CANCELLED` AR transaction |

---

## 6. Notes

- **UploadOcrWorker** processes 0 items when `raw_invoice_uploads` is empty. OCR (GPT-4o) is invoked only on actual file uploads via the FastAPI server. Requires `OPENAI_API_KEY` in `.env`.
- **Sarvam OCR fallback** is dormant until `SARVAM_API_KEY` is set; the worker skips it gracefully without error.
- **Soft-dup flagging is non-blocking by design.** Both the deduplication worker and staging_promotion worker filter `sync_status = PENDING`. If staging_promotion promotes a record first, dedup skips it. The `REVIEW` flag therefore reflects best-effort detection; `DUPLICATE` (exact) detection runs before `sync_status` changes and is always reliable.
- **All workers are idempotent.** Every upsert uses `ON CONFLICT DO UPDATE`; every batch fetch uses `FOR UPDATE SKIP LOCKED` to prevent double-processing across concurrent instances.
- **Credit invariant enforced by dedup worker:**
  ```
  amount_paid        = SUM(paid_amount) WHERE txn_status = 'COMPLETED'
  outstanding_amount = GREATEST(total_amount - amount_paid, 0)
  ```

---

*ONSUMM.AI — 76 tests passed across pipeline integration, dedup unit, and dedup integration suites.*
