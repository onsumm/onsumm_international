# Processing Pipeline

Async Python pipeline for the multi-tenant cashflow management application.
Twelve workers run concurrently via `asyncio.gather()`. Each worker follows a
two-phase CDC lifecycle: **Phase 1 — full backfill** (drains all existing
unprocessed rows on startup), then **Phase 2 — incremental CDC** (PostgreSQL
`LISTEN/NOTIFY` wakes the worker the instant new rows arrive; a safety-poll
runs periodically as a fallback).

---

## Architecture

```
main.py
  ├─ create_notify_triggers()          Install LISTEN/NOTIFY triggers (idempotent)
  └─ asyncio.gather(return_exceptions=True)
       ├─ ApiSyncWorker          raw_api_payloads          → stg_invoices / stg_bills / stg_payments / dim_*
       ├─ WebhookWorker          raw_webhook_events        → stg_payments
       ├─ UploadOcrWorker        raw_invoice_uploads       → raw_ocr_results → stg_invoices / stg_bills
       ├─ StagingPromotionWorker stg_invoices/bills/payments → fact_invoices (AR) / fact_bills (AP) / fact_payments
       ├─ ApprovalWorker         fact_invoices / payment_transactions / bill_approval_chain → wf_tasks / wf_nudges / wf_escalations / wf_audit_log
       ├─ TallyReadWorker        int_connections (Tally)   → stg_* (incremental PULL)
       ├─ DeduplicationWorker    stg_bills / stg_invoices  → validation_flag / ai_anomalies / credit recalc
       ├─ GstVerificationWorker  dim_end_customers + dim_suppliers (PENDING/UNVERIFIED gstin) → kyc_status/status / pan backfill
       ├─ BankVerificationWorker dim_end_customers + dim_suppliers (bank_verified=false) → bank_verified flag
       ├─ CategoryEnrichmentWorker fact_invoices (category IS NULL) → category / stg_bills.category / dim_suppliers.category
       ├─ TdsProcessingWorker    stg_bills (tds_status=PENDING) → tds_amount / net_payable / fact_bills back-patch
       └─ TallySyncWorker        int_connections (Tally)   → bidirectional PUSH+PULL (optional)
```

All workers extend `BaseWorker` ([pipelines/base.py](pipelines/base.py)).

---

## CDC Lifecycle (Phase 1 → Phase 2)

Every data-driven worker goes through two phases automatically:

### Phase 1 — Backfill (startup)

On first run the worker drains **all existing unprocessed rows** in large batches
(`backfill_batch` from `PIPELINE_CFG`) with no sleep between iterations — just
`asyncio.sleep(0)` to yield the event loop. The loop exits when `process_batch()`
returns 0.

```
startup
  └─ while process_batch() > 0:
         # large batch, no sleep
     logger.info("backfill complete — N rows")
```

### Phase 2 — Incremental (CDC steady-state)

**Workers with `notify_channel` set** (all data-driven workers):

A dedicated `asyncpg` connection `LISTEN`s on a PostgreSQL channel.  When a row
is inserted or updated on the source table, a trigger fires `pg_notify(channel, '')`
and the worker wakes immediately via an `asyncio.Event`.  A safety-poll timeout
(`idle_poll_interval_s`) fires even without a notification so no row is ever
permanently stuck.

```
LISTEN ch_xxx
  └─ wait(event OR timeout)
       event.clear()
       while process_batch() > 0:    # drain; normal batch size
           pass
       sleep(poll_interval_s)        # brief cooldown
```

**Workers with `skip_backfill = True`** (approval, tally_read, tally_sync):

These are time-driven, not row-driven.  They skip Phase 1 entirely and use pure
exponential-backoff polling with the cadence defined in `PIPELINE_CFG`.

### NOTIFY trigger map

| Source table | Event | Channel | Worker woken |
|---|---|---|---|
| `raw_api_payloads` | INSERT | `ch_api_sync` | ApiSyncWorker |
| `raw_webhook_events` | INSERT | `ch_webhook` | WebhookWorker |
| `raw_invoice_uploads` | INSERT | `ch_upload_ocr` | UploadOcrWorker |
| `stg_invoices` | INSERT | `ch_staging_promotion` | StagingPromotionWorker |
| `stg_bills` | INSERT | `ch_staging_promotion` | StagingPromotionWorker |
| `stg_payments` | INSERT | `ch_staging_promotion` | StagingPromotionWorker |
| `stg_bills` | INSERT OR UPDATE | `ch_tds_processing` | TdsProcessingWorker |
| `fact_invoices` | INSERT | `ch_category_enrichment` | CategoryEnrichmentWorker |
| `fact_invoices` | INSERT | `ch_deduplication` | DeduplicationWorker |
| `dim_suppliers` | INSERT | `ch_gst_verification` | GstVerificationWorker |
| `dim_end_customers` | INSERT | `ch_gst_verification` | GstVerificationWorker |
| `dim_suppliers` | INSERT | `ch_bank_verification` | BankVerificationWorker |

Triggers are created by `create_notify_triggers()` in [pipelines/cdc.py](pipelines/cdc.py).
The function is **idempotent** (`DROP TRIGGER IF EXISTS` + `CREATE`) — safe on every restart.

---

## Uniform Config

All pipeline tuning lives in `config.PIPELINE_CFG`.  Workers declare only
`name`, `notify_channel`, and optionally `skip_backfill` — no per-worker
`poll_interval` attribute.

```python
# config.py
PIPELINE_CFG = {
    "_default": {
        "backfill_batch":       200,   # rows per iteration during Phase 1
        "incremental_batch":     50,   # rows per iteration in CDC mode
        "poll_interval_s":        5.0, # cooldown after finding work
        "idle_poll_interval_s":  60.0, # LISTEN timeout / empty-queue sleep
        "error_backoff_s":        5.0, # initial retry delay on exception
        "error_backoff_cap_s":  120.0, # max retry delay
    },
    "api_sync":           { "backfill_batch": 100, "incremental_batch": 25,  "poll_interval_s": 1.0  },
    "webhook":            { "backfill_batch": 100, "incremental_batch": 25,  "poll_interval_s": 1.0  },
    "upload_ocr":         { "backfill_batch":  20, "incremental_batch":  5,  "poll_interval_s": 2.0  },
    "staging_promotion":  { "backfill_batch": 200, "incremental_batch": 50,  "poll_interval_s": 2.0  },
    "tds_processing":     { "backfill_batch": 100, "incremental_batch": 50,  "poll_interval_s": 5.0  },
    "category_enrichment":{ "backfill_batch": 200, "incremental_batch": 100, "poll_interval_s": 10.0 },
    "gst_verification":   { "backfill_batch":  50, "incremental_batch": 20,  "poll_interval_s": 5.0  },
    "bank_verification":  { "backfill_batch":  50, "incremental_batch": 20,  "poll_interval_s": 5.0  },
    "deduplication":      { "backfill_batch": 100, "incremental_batch": 50,  "poll_interval_s": 10.0 },
    # Time-driven (skip_backfill = True)
    "approval":           { "poll_interval_s": 30.0, "idle_poll_interval_s": 90.0  },
    "tally_read":         { "poll_interval_s": 60.0, "idle_poll_interval_s": 120.0 },
    "tally_sync":         { "poll_interval_s": 60.0, "idle_poll_interval_s": 120.0 },
}
```

`BaseWorker._cfg` merges `_default` with the worker-specific override dict at runtime — no subclass needs to override anything beyond `name`.

---

## Directory Structure

```
Processing_Pipline/
├── main.py                          Entry point — CDC trigger bootstrap + starts all 12 workers
├── config.py                        Env-var settings + PIPELINE_CFG (all pipeline tuning)
├── db.py                            asyncpg SSL pool singleton (get_pool / init_pool / close_pool)
├── pytest.ini                       pytest config (asyncio_mode=auto, testpaths=tests)
│
├── models/
│   └── enums.py                     Python mirrors of all PostgreSQL enum types
│
├── utils/
│   ├── fiscal.py                    Indian FY helpers (Apr–Mar, get_fiscal_year, fiscal_period)
│   ├── ocr_engine.py                Shared GPT-4o vision OCR (used by pipeline + FastAPI server)
│   └── tds_engine.py                TDS calculation engine (Sandbox API + local fallback)
│
├── pipelines/
│   ├── base.py                      BaseWorker: Phase-1 backfill + Phase-2 CDC LISTEN/NOTIFY
│   ├── cdc.py                       NOTIFY trigger bootstrap (create_notify_triggers)
│   ├── api_sync.py                  ApiSyncWorker
│   ├── webhook.py                   WebhookWorker
│   ├── upload_ocr.py                UploadOcrWorker
│   ├── staging_promotion.py         StagingPromotionWorker
│   ├── approval.py                  ApprovalWorker (skip_backfill=True)
│   ├── tally_read.py                TallyReadWorker (skip_backfill=True)
│   ├── tally_sync.py                TallySyncWorker (skip_backfill=True)
│   ├── deduplication.py             DeduplicationWorker
│   ├── gst_verification.py          GstVerificationWorker (Sandbox GSTIN verify; concurrent batches)
│   ├── bank_verification.py         BankVerificationWorker (Sandbox penny-less verify; concurrent batches)
│   ├── category_enrichment.py       CategoryEnrichmentWorker (COA rules + keyword → fact_invoices.category)
│   ├── tds_processing.py            TdsProcessingWorker (Sandbox TDS API + local fallback)
│   └── expense_report.py            Standalone script + importable module for CSV expense reports
│
├── tally2database/                  Tally Prime XML sync utilities (used by TallySyncWorker)
│   ├── tally_client.py
│   ├── tally_parser.py
│   ├── tally_builder.py
│   └── sync_audit_logger.py
│
├── backend_ocr/                     FastAPI HTTP upload server (separate process)
│   └── app/
│       ├── main.py
│       ├── ocr.py
│       ├── tds_api.py               TDS settings endpoints (/api/tds/*)
│       └── config.py
│
├── tests/                           Unit test suite (no live DB required)
├── scripts/
│   ├── fix_tds_tenant_rules.py      One-time: copy TDS mapping rules to all tenants
│   └── test_tds_live.py             Live DB smoke test for TDS engine
└── .env                             Environment variables (not committed)
```

---

## Workers

### 1. ApiSyncWorker ([pipelines/api_sync.py](pipelines/api_sync.py))

**Source:** `raw_api_payloads` where `is_processed = false`
**CDC channel:** `ch_api_sync`

Handles data pushed by external API integrations (Apideck, Tally, Zoho, etc.).

| `entity_type` | Target |
|---|---|
| `INVOICE` | `stg_invoices` + `dim_end_customers` |
| `BILL` | `stg_bills` + `dim_suppliers` |
| `PAYMENT` | `stg_payments` |
| `CUSTOMER` | `dim_end_customers` |
| `SUPPLIER` | `dim_suppliers` |
| `JOURNAL` | `stg_journal_entries` |

Marks each row `is_processed = true` on success. Rows with `NULL tenant_id` are quarantined immediately. Errors increment `retry_count`; after 3 failures the row goes to `raw_ingestion_errors`.

---

### 2. WebhookWorker ([pipelines/webhook.py](pipelines/webhook.py))

**Source:** `raw_webhook_events` where `is_processed = false AND is_signature_verified = true`
**CDC channel:** `ch_webhook`

| `source` | Handled events |
|---|---|
| Razorpay | `payment.captured`, `payment.failed`, `refund.created` |
| Stripe | `payment_intent.succeeded/failed`, `invoice.paid`, `charge.refunded` |

Writes to `stg_payments`. Same tenant null guard and dead letter queue as ApiSyncWorker.

---

### 3. UploadOcrWorker ([pipelines/upload_ocr.py](pipelines/upload_ocr.py))

**Source:** `raw_invoice_uploads`
**CDC channel:** `ch_upload_ocr`

6-stage status machine:

```
RECEIVED → PREPROCESSING → OCR_PRIMARY → OCR_FALLBACK? → VALIDATING → DONE
                                                                   ↓ (on error)
                                                                  ERROR
```

| Stage | What happens |
|---|---|
| RECEIVED → PREPROCESSING | Validate file type (PDF/PNG/JPG/TIFF/XLSX/CSV) |
| PREPROCESSING → OCR_PRIMARY | GPT-4o vision OCR or structured file parser |
| OCR_PRIMARY → OCR_FALLBACK | If GPT-4o confidence < 0.6 → Sarvam OCR (fallback) |
| VALIDATING → DONE | Route to `stg_invoices` (AR) or `stg_bills` (AP) |

---

### 4. StagingPromotionWorker ([pipelines/staging_promotion.py](pipelines/staging_promotion.py))

**Source:** `stg_invoices`, `stg_bills`, `stg_payments` where `sync_status = 'PENDING'`
**CDC channel:** `ch_staging_promotion`

Promotes staged records to the fact layer:
- `stg_invoices` → `fact_invoices` (AR, with Indian fiscal period)
- `stg_bills` → `fact_bills` (AP — separate table)
- `stg_payments` → `fact_payments` + updates `outstanding_amount` on linked invoices

`fact_cashflow_daily` is recomputed by pg_cron every 15 min (decoupled from the hot path).

---

### 5. TdsProcessingWorker ([pipelines/tds_processing.py](pipelines/tds_processing.py))

**Source:** `stg_bills` where `tds_status = 'PENDING' OR tds_status IS NULL`
**CDC channel:** `ch_tds_processing` (fires on both INSERT and UPDATE of `stg_bills`)

Calculates TDS deduction for each AP bill and writes results to both `stg_bills` and `fact_bills`.

**Engine flow (utils/tds_engine.py):**

1. Resolve TDS section via `dim_tds_mapping_rules` (tenant + vendor category match, ordered by priority)
2. Evaluate threshold type:

| Threshold type | Logic |
|---|---|
| `PER_TXN` | Apply TDS if `total_amount ≥ threshold` |
| `AGGREGATE_FY` | Sum all bills in the current Indian FY for this vendor; apply if total ≥ threshold |
| `PER_MONTH` | Sum bills in the current calendar month; apply if total ≥ threshold |
| `EXCESS_OF` | Deduct TDS only on the portion above the threshold |
| `DUAL` | Apply both PER_TXN and AGGREGATE_FY checks; use the more restrictive |

3. Call Sandbox TDS Calculator API (`POST /tds/calculator/non-salary`) with the resolved `nature_of_payment`
4. On API error / unsupported payment type: fall back to `dim_tds_sections.default_rate` (or `pan_missing_rate = 20%` when PAN not available)

**TDS status values written to `stg_bills` and `fact_bills`:**

| Status | Meaning |
|---|---|
| `CALCULATED` | TDS computed successfully (section, rate, amount, net_payable written) |
| `NA` | Below threshold — no TDS applicable |
| `PENDING` | Supplier has no `vendor_category_id` assigned yet |
| `ERROR` | Calculation failed (engine exception) |

**Back-patch:** For bills already promoted to `fact_bills` before TDS ran, the worker updates `fact_bills` in the same transaction via `stg_bill_id` FK.

**Multi-tenant mapping rules** (`dim_tds_mapping_rules`):
Each tenant needs its own mapping rules linking `vendor_category_id → tds_section_id`.
Run `scripts/fix_tds_tenant_rules.py` once to copy seed rules to all tenants.

---

### 6. ApprovalWorker ([pipelines/approval.py](pipelines/approval.py))

**skip_backfill:** `True` (time-driven)
**Poll interval:** 30 s, idle cap: 90 s

8 sub-tasks per cycle:

| Sub-task | Action |
|---|---|
| `_mark_overdue_schedules` | Sets `payment_schedules.status = OVERDUE` for past-due rows |
| `_create_nudge_tasks_for_overdue_invoices` | Creates `wf_task (NUDGE)` per overdue AR invoice |
| `_create_approve_tasks_for_new_ar_invoices` | Creates `wf_task (APPROVE)` for AR invoices < 48 h old |
| `_create_approve_tasks_for_large_payments` | Creates `wf_task (APPROVE)` for transactions above threshold |
| `_auto_approve_small_payments` | Advances small transactions to `PROCESSING`; writes `wf_audit_log` |
| `_generate_nudges` | Inserts `wf_nudge (EMAIL)` per pending NUDGE task |
| `_escalate_stale_tasks` | Creates `wf_escalation` for NUDGE tasks stale > 7 days |
| `_audit_bill_approval_chain` | Polls `bill_approval_chain` for unaudited rows; writes `wf_audit_log` |

**Per-tenant auto-approve threshold** via `dim_tenants.settings_jsonb`:
```json
{ "auto_approve_enabled": true, "auto_approve_threshold_inr": 25000 }
```
Falls back to `AUTO_APPROVE_THRESHOLD` env var (default ₹50,000).

---

### 7. TallyReadWorker ([pipelines/tally_read.py](pipelines/tally_read.py))

**skip_backfill:** `True` (time-driven)
**Poll interval:** 60 s, idle cap: 120 s

PULL-only. Runs one cycle per active `int_connections` row whenever `last_sync_at` is older than `TALLY_SYNC_INTERVAL_MINUTES` (default 15 min).

**Incremental window:**
```python
from_date = last_sync_at.date()           # last successful sync
to_date   = date.today()
```

**Resilience:** `consecutive_failures` per connection; marks `status = 'ERROR'` after 3 failures.

---

### 8. DeduplicationWorker ([pipelines/deduplication.py](pipelines/deduplication.py))

**Source:** `stg_bills` and `stg_invoices` in `PENDING` sync_status
**CDC channel:** `ch_deduplication`

4-step deduplication cycle:

| Step | Logic |
|---|---|
| Exact dup (within stg) | `ROW_NUMBER() OVER (PARTITION BY tenant, entity, number, amount, date)` → flag `rn > 1` as DUPLICATE |
| Cross-table (stg vs fact) | Match on same tenant + entity + number + amount + date |
| Soft dup | Amount diff ≤ 1 INR **and** date diff ≤ 7 days → flag REVIEW |
| Credit recalc | Recompute `amount_paid` / `outstanding_amount` after FAILED/BOUNCED/REVERSED transactions |

Anomaly types: `DUPLICATE_BILL`, `DUPLICATE_INVOICE`, `SOFT_DUPLICATE_BILL`, `SOFT_DUPLICATE_INVOICE`, `PAYMENT_FAILURE_AP`, `PAYMENT_FAILURE_AR`. Inserts are idempotent.

---

### 9. GstVerificationWorker ([pipelines/gst_verification.py](pipelines/gst_verification.py))

**Source:** `dim_end_customers` (kyc_status=PENDING) + `dim_suppliers` (status=UNVERIFIED)
**CDC channel:** `ch_gst_verification`

Up to `_CONCURRENCY = 5` rows verified simultaneously via Sandbox GSTIN API.

Decision chain: GSTIN registered → taxpayer active → name match → PAN match.
PAN is back-filled from the API response when the DB row has none.

---

### 10. BankVerificationWorker ([pipelines/bank_verification.py](pipelines/bank_verification.py))

**Source:** `dim_end_customers` + `dim_suppliers` where `bank_verified = false`
**CDC channel:** `ch_bank_verification`

Up to `_CONCURRENCY = 5` rows verified simultaneously via Sandbox penny-less verify API.

Three-state outcome: `true` (confirmed) / `NULL` (checked, does not exist) / unchanged (transient error, retry next batch).

---

### 11. CategoryEnrichmentWorker ([pipelines/category_enrichment.py](pipelines/category_enrichment.py))

**Source:** `fact_invoices` where `category IS NULL OR category = ''`
**CDC channel:** `ch_category_enrichment`

Resolution order (highest priority first):

| Priority | Source |
|---|---|
| 1 | COA rule: vendor name substring match with category set (`coa_rules`) |
| 2 | `dim_suppliers.category` default |
| 3 | Built-in keyword match (30+ keywords across 6 categories) |
| — | Skip — never guesses blindly |

Writes to `fact_invoices.category`, `stg_bills.category`, and `dim_suppliers.category`
(backfill so future bills from the same vendor skip this queue).

---

### 12. TallySyncWorker ([pipelines/tally_sync.py](pipelines/tally_sync.py))

**skip_backfill:** `True` (time-driven)
**Poll interval:** 60 s, idle cap: 120 s

Bidirectional PUSH+PULL. Not started by default in `main.py`; add to workers list when needed.

---

## Resilience Design

| Concern | Mechanism |
|---|---|
| Worker crash isolation | `asyncio.gather(return_exceptions=True)` — one crashed worker never brings down others |
| Phase-1 error recovery | Exponential backoff on exception during backfill; resets on next successful batch |
| Phase-2 LISTEN reconnect | On unhandled exception the inner loop restarts; the `asyncpg` listen connection is released/re-acquired |
| Safety-poll | Every worker checks for rows even without a NOTIFY (every `idle_poll_interval_s`) — no row is ever permanently stuck |
| Row-level locking | `FOR UPDATE SKIP LOCKED` on every batch query — safe for horizontal scale-out |
| Dead letter queue | `retry_count` incremented per failure; after 3 attempts row is quarantined to `raw_ingestion_errors` |
| Tally circuit breaker | `consecutive_failures` per connection; marks `int_connections.status = 'ERROR'` after 3 failures |
| Duplicate suppression | DeduplicationWorker blocks exact-dup stg rows; soft dups held for human review; credit recalc after payment reversal |

---

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | Neon PostgreSQL host |
| `DB_PORT` | `5432` | DB port |
| `DB_NAME` | `neondb` | Database name |
| `DB_USER` | `neondb_owner` | DB user |
| `DB_PASSWORD` | _(required)_ | DB password |
| `DB_SSL` | `require` | SSL mode |
| `OPENAI_API_KEY` | — | GPT-4o OCR (required for UploadOcrWorker) |
| `SARVAM_API_KEY` | — | Sarvam OCR fallback (optional) |
| `POPPLER_PATH` | — | Path to Poppler bin (Windows: `C:\poppler\bin`) |
| `TALLY_HOST` | `localhost` | Tally Prime host |
| `TALLY_PORT` | `9000` | Tally Prime XML port |
| `TALLY_COMPANY_NAME` | — | Active Tally company |
| `SYNC_INTERVAL_MINUTES` | `15` | Tally sync cadence |
| `TALLY_PULL_DAYS` | `30` | Days to pull vouchers on first run |
| `TALLY_THREAD_POOL` | `4` | Threads for blocking Tally HTTP calls |
| `POLL_INTERVAL_S` | `5` | Default `poll_interval_s` for `_default` PIPELINE_CFG entry |
| `AUTO_APPROVE_THRESHOLD` | `50000` | Global fallback auto-approve threshold (₹) |
| `TENANT_ID` | — | Single-tenant bootstrap UUID (optional) |
| `LOG_LEVEL` | `INFO` | Logging level |
| `SANDBOX_API_KEY` | _(required)_ | Sandbox.co.in API key (KYC / GST / TDS) |
| `SANDBOX_API_SECRET` | _(required)_ | Sandbox.co.in API secret |
| `SANDBOX_BASE_URL` | `https://api.sandbox.co.in` | Sandbox base URL (override for test env) |
| `APPROVAL_SECRET` | _(required)_ | Secret for signing approval email link tokens |
| `SMTP_HOST` | — | SMTP host for approval notification emails |
| `SMTP_PORT` | `465` | SMTP port (SSL) |
| `SMTP_USER` | — | SMTP sender address |
| `SMTP_PASSWORD` | — | SMTP password |
| `SMTP_ADMIN_EMAIL` | — | Always CC'd on bill approval notifications |
| `AGENT_LATEST_VERSION` | `1.0.0` | Latest Tally Agent version |
| `AGENT_DOWNLOAD_URL` | — | HTTPS URL to latest `OnsummTallyAgent.exe` |
| `AGENT_DOWNLOAD_SHA256` | — | SHA-256 checksum of agent download |
| `AGENT_MIN_VERSION` | `1.0.0` | Minimum supported agent version |

> **Batch sizes and poll intervals are no longer set via `.env`.**
> Override them in `config.PIPELINE_CFG` per worker.

---

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in credentials
cp .env.example .env

# Run all workers (CDC triggers installed automatically on startup)
python main.py

# Run unit tests (no live DB required)
python -m pytest

# One-time: copy TDS mapping rules to all tenants
python scripts/fix_tds_tenant_rules.py

# Live TDS smoke test
python scripts/test_tds_live.py
```

**Windows / Poppler:** Download Poppler for Windows, extract it, and set `POPPLER_PATH=C:\poppler\bin` in `.env`.

---

## FastAPI Upload Server (`backend_ocr/`)

A separate HTTP service for direct PDF uploads from the frontend.

```bash
cd backend_ocr
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001
```

Key endpoints:

| Endpoint | Description |
|---|---|
| `GET /health` | Health check |
| `POST /api/invoices/ocr` | OCR a single PDF |
| `POST /api/invoices/ocr-bulk` | OCR multiple PDFs concurrently |
| `POST /api/invoices/ingest` | Ingest normalised invoice payload (no OCR) |
| `GET /api/bills` | List AP bills (with TDS columns: `tds_status`, `tds_section_code`, `tds_rate`, `tds_amount`, `tds_base_amount`, `net_payable`) |
| `GET /api/bills/{id}` | Bill detail (line items + approval chain + TDS breakdown) |
| `GET /api/vendors` | List vendors with `vendor_category_id`, `vendor_category_name` |
| `GET /api/tds/sections` | TDS section master (for settings UI) |
| `GET /api/tds/mapping-rules` | Tenant TDS mapping rules |
| `POST /api/tds/mapping-rules` | Create / update mapping rule |

**Startup migrations** run on every `uvicorn` start (`ALTER TABLE … ADD COLUMN IF NOT EXISTS`).
The 11 separate migration handlers each open their own DB connection — this causes ~60 s cold-start
on a fresh Neon instance.

---

## Data Flow (end-to-end)

```
Upload (HTTP)           → backend_ocr FastAPI → raw_invoice_uploads (RECEIVED)
                                                       ↓  NOTIFY ch_upload_ocr
                                               UploadOcrWorker (Phase-2 CDC)
                                            PREPROCESSING → OCR_PRIMARY (GPT-4o)
                                            → OCR_FALLBACK (Sarvam, optional)
                                            → VALIDATING → stg_invoices / stg_bills
                                                       ↓  NOTIFY ch_staging_promotion
                                                          NOTIFY ch_tds_processing
                                                          NOTIFY ch_deduplication
                                      ┌────────────────────────┐
                                      │  Concurrent (parallel)  │
                              DeduplicationWorker          TdsProcessingWorker
                                exact/soft dup check        resolve section
                                credit recalc               call Sandbox TDS API
                                → ai_anomalies              or local fallback
                              └────────────────────────┘
                                                       ↓  (PENDING rows only)
                                         StagingPromotionWorker
                                              → fact_invoices  (AR)
                                              → fact_bills     (AP)
                                              → fact_payments
                                                       ↓  NOTIFY ch_category_enrichment
                                                          pg_cron every 15 min
                                         ┌────────────────────────┐
                                         │  Concurrent (parallel)  │
                                CategoryEnrichmentWorker    fact_cashflow_daily
                                  COA rules + keywords       recomputed
                                  → fact_invoices.category
                                └────────────────────────┘
                                                       ↓
                                           ApprovalWorker (time-driven, 30s)
                                             → wf_tasks / wf_nudges / wf_escalations
                                             → wf_audit_log

API Push         → raw_api_payloads  → ch_api_sync  → ApiSyncWorker  → stg_* → (same flow)
Webhook          → raw_webhook_events→ ch_webhook   → WebhookWorker  → stg_payments
Tally Prime      ← TallyReadWorker (time-driven, 60s; incremental PULL circuit-breaker)
GST / Bank KYC   → dim_suppliers/customers → ch_gst_verification / ch_bank_verification
                   → VERIFIED / FAILED status

Error path       → raw_ingestion_errors (after 3 retry_count failures)
Duplicate path   → stg row flagged DUPLICATE/REVIEW, anomaly → ai_anomalies
TDS path         → stg_bills.tds_status: CALCULATED / NA / PENDING / ERROR
                   fact_bills back-patched in same transaction
```

---

## Pending TODOs

| File | TODO |
|---|---|
| [pipelines/upload_ocr.py](pipelines/upload_ocr.py) | `_parse_structured_file()` requires actual file bytes on disk — integrate with your storage layer (S3/local path) once `storage_path` is a real path |
| [pipelines/upload_ocr.py](pipelines/upload_ocr.py) | Sarvam OCR fallback activates automatically once `SARVAM_API_KEY` is set in `.env` |
| [backend_ocr/app/main.py](backend_ocr/app/main.py) | 11 startup migration handlers each open a separate Neon connection (~60 s cold-start) — consolidate into a single connection to eliminate the delay |
| [pipelines/tds_processing.py](pipelines/tds_processing.py) | 54 `stg_bills` rows remain `PENDING` — their suppliers have no `vendor_category_id` assigned. Assign categories via the TDS Settings UI or `scripts/fix_tds_tenant_rules.py` |
