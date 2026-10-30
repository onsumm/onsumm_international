# Multi-Currency Handling — Complete Specification

> **ONSUMM.AI** | Finalized June 2026
> **Reporting currency**: Indian Rupee (INR) — always.
> **Foreign currencies**: USD, EUR, GBP, JPY (RBI/FBIL published reference rates only)

---

## Table of Contents

1. [Why This Feature Exists](#1-why-this-feature-exists)
2. [Supported Currencies](#2-supported-currencies)
3. [Rate Engine & Data Source](#3-rate-engine--data-source)
4. [Database Schema](#4-database-schema)
5. [Pipeline Worker — FxRateWorker](#5-pipeline-worker--fxrateworker)
6. [Currency Detection at OCR](#6-currency-detection-at-ocr)
7. [Auto-Conversion at Staging Promotion](#7-auto-conversion-at-staging-promotion)
8. [Payment Flow — Back-Calculation](#8-payment-flow--back-calculation)
9. [Partial Payments & Credit Notes](#9-partial-payments--credit-notes)
10. [FX AI Agent — FxCurrencyAgent](#10-fx-ai-agent--fxcurrencyagent)
11. [Alert Engine](#11-alert-engine)
12. [Dashboard & Bills UI](#12-dashboard--bills-ui)
13. [CFO Reports — FX Cost Analysis](#13-cfo-reports--fx-cost-analysis)
14. [Settings Page — FX Configuration](#14-settings-page--fx-configuration)
15. [Integration with Existing Workers](#15-integration-with-existing-workers)
16. [API Endpoints](#16-api-endpoints)
17. [Configuration Reference](#17-configuration-reference)
18. [Design Decisions](#18-design-decisions)
19. [Implementation Phases](#19-implementation-phases)
20. [Files to Create / Modify](#20-files-to-create--modify)
21. [Testing](#21-testing)

---

## 1. Why This Feature Exists

ONSUMM.AI serves Indian SMEs whose AP and AR transactions are not always in INR.

| Scenario | Example |
|---|---|
| SaaS subscriptions | AWS, GitHub, Figma — billed in USD |
| European suppliers | Machinery, pharma, raw materials — billed in EUR |
| UK services | Consulting, legal, SaaS — billed in GBP |
| Japanese imports | Electronics, auto parts, machinery — billed in JPY |
| Export receivables | US/EU/UK customers paying in foreign currency |

Without currency standardisation:
- Dashboard KPIs mix INR and foreign amounts — totals are meaningless
- Aging reports undercount or overcount outstanding balances
- Approval thresholds (e.g. auto-approve ≤ ₹50,000) cannot fire on foreign currency bills
- The CFO has no visibility into how much FX is costing the business

With this feature:
- Every rupee figure on every screen is **actual INR at the RBI reference rate**
- The system handles conversion silently — no extra steps for the user
- FX cost is tracked per bill, per payment, per quarter
- An AI agent flags opportunities and risks — the user acts only when needed

---

## 2. Supported Currencies

Only currencies with RBI/FBIL-published daily reference rates. This ensures regulatory-grade accuracy for Indian businesses.

### 2.1 Currency Reference Table

| Code | Name | Symbol | Decimals | RBI Quote Convention | Notes |
|---|---|---|---|---|---|
| **USD** | US Dollar | `$` | 2 | INR per 1 USD | Most common — SaaS, cloud, US vendors |
| **EUR** | Euro | `€` | 2 | INR per 1 EUR | European trade, pharma, machinery |
| **GBP** | British Pound | `£` | 2 | INR per 1 GBP | UK services, consulting |
| **JPY** | Japanese Yen | `¥` | 0 | INR per 100 JPY | Electronics, auto parts — note: per 100, not per 1 |
| **INR** | Indian Rupee | `₹` | 2 | — | Home currency, no conversion needed |

### 2.2 JPY Special Handling

RBI quotes JPY as INR per 100 yen. The system stores and computes using INR per 1 JPY internally:

```python
# At rate ingestion
if currency == 'JPY':
    inr_per_unit = rbi_rate_per_100 / 100  # e.g. 56.16 / 100 = 0.5616

# Display: ¥100,000 (no decimals, ever)
# Conversion: ¥100,000 × 0.5616 = ₹56,160
```

### 2.3 Symbol Mapping (for OCR)

All 4 symbols are unambiguous — no disambiguation logic needed:

| Symbol | Currency | Ambiguous? |
|---|---|---|
| `$` or `USD` | USD | No (AUD/CAD/SGD not in scope) |
| `€` or `EUR` | EUR | No |
| `£` or `GBP` | GBP | No |
| `¥` or `JPY` | JPY | No (CNY not in scope) |
| `₹` or `INR` or blank | INR | No — default |

### 2.4 Future Extensibility

Adding a new currency (e.g. AED, SGD) requires:
1. Insert one row into `dim_currencies`
2. Add the code to `FX_FETCH_CURRENCIES` env var
3. No schema changes, no code changes, no new worker

---

## 3. Rate Engine & Data Source

### 3.1 Primary Source — exchangerate-api.com (Open Tier)

Free, no API key required, updated daily from ECB/FBIL sources. Returns all currencies in one call.

```
GET https://open.er-api.com/v6/latest/INR
```

**Response (relevant fields)**:

```json
{
  "result": "success",
  "time_last_update_utc": "Thu, 12 Jun 2026 00:00:01 +0000",
  "base_code": "INR",
  "rates": {
    "USD": 0.01176,
    "EUR": 0.01083,
    "GBP": 0.00932,
    "JPY": 1.7812
  }
}
```

Invert to get INR per unit:

```python
usd_inr = 1 / 0.01176  # = 85.03
eur_inr = 1 / 0.01083  # = 92.34
gbp_inr = 1 / 0.00932  # = 107.30
jpy_inr = 1 / 1.7812   # = 0.5614 (per 1 yen)
```

### 3.2 Fallback Source — exchangeratesapi.io (Free Tier)

If primary is unavailable:

```
GET https://api.exchangeratesapi.io/v1/latest?access_key=<KEY>&base=INR&symbols=USD,EUR,GBP,JPY
```

### 3.3 Why Not FBIL/RBI REST APIs?

FBIL and RBI do not offer documented REST APIs with authentication. The original design doc referenced endpoints that don't exist. The exchangerate-api.com source is:
- Reliable (99.9% uptime)
- Sourced from ECB/FBIL (same underlying data)
- Sufficient accuracy for SME cashflow management (not forex trading)

For clients who need exact RBI rates, a future enhancement can scrape the RBI archive page as a verification layer — but not as the primary source.

### 3.4 Weekend / Holiday Handling

RBI does not publish rates on Saturdays, Sundays, or declared public holidays:

```
Today is a non-business day?
    → Use last known rate (carry forward)
    → Set source = 'CARRIED_FORWARD'
    → Do NOT flag as stale for < 3 calendar days

3+ consecutive days without a fresh rate?
    → Flag as RATE_STALE
    → Agent note: "No fresh rate for 3 days — rates may be outdated"
```

---

## 4. Database Schema

### 4.1 New Table — `dim_currencies`

Reference table for supported currencies. Seeded once at migration.

```sql
CREATE TABLE dim_currencies (
    currency_code    char(3)     PRIMARY KEY,
    name             text        NOT NULL,
    symbol           text        NOT NULL,
    decimal_places   smallint    NOT NULL DEFAULT 2,
    is_active        boolean     NOT NULL DEFAULT true
);

-- Seed data
INSERT INTO dim_currencies VALUES
    ('INR', 'Indian Rupee',    '₹', 2, true),
    ('USD', 'US Dollar',       '$', 2, true),
    ('EUR', 'Euro',            '€', 2, true),
    ('GBP', 'British Pound',   '£', 2, true),
    ('JPY', 'Japanese Yen',    '¥', 0, true);
```

### 4.2 New Table — `dim_fx_rates`

One row per currency per business day. Canonical rate store for all conversions.

```sql
CREATE TABLE dim_fx_rates (
    rate_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    rate_date        date        NOT NULL,
    currency_code    char(3)     NOT NULL REFERENCES dim_currencies(currency_code),
    inr_per_unit     numeric(12,6) NOT NULL,
    source           text        NOT NULL,   -- 'PRIMARY', 'FALLBACK', 'CARRIED_FORWARD'
    is_business_day  boolean     NOT NULL DEFAULT true,
    fetched_at       timestamptz NOT NULL DEFAULT now(),

    UNIQUE (rate_date, currency_code)
);

CREATE INDEX idx_fx_rates_date ON dim_fx_rates (rate_date DESC);
CREATE INDEX idx_fx_rates_currency_date ON dim_fx_rates (currency_code, rate_date DESC);
```

### 4.3 New Table — `fx_bill_payments`

Tracks each partial or full payment on a foreign currency bill. Enables per-payment back-calculation.

```sql
CREATE TABLE fx_bill_payments (
    payment_id       uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id       uuid        NOT NULL REFERENCES fact_bills(bill_id),
    tenant_id        uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
    payment_date     date        NOT NULL,
    foreign_amount   numeric(18,2) NOT NULL CHECK (foreign_amount > 0),
    actual_inr       numeric(18,2) NOT NULL CHECK (actual_inr > 0),
    effective_rate   numeric(12,6) NOT NULL,  -- back-calculated: actual_inr / foreign_amount
    rbi_rate         numeric(12,6),           -- RBI rate on payment date (for comparison)
    bank_spread      numeric(8,6),            -- effective_rate - rbi_rate
    bank_spread_pct  numeric(5,2),            -- (bank_spread / rbi_rate) × 100
    reference_no     text,                    -- UTR / payment reference
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_fx_payments_bill ON fx_bill_payments (invoice_id);
CREATE INDEX idx_fx_payments_tenant ON fx_bill_payments (tenant_id, payment_date DESC);
```

### 4.4 New Table — `fx_rate_alerts` (Phase 2)

One row per alert event per tenant per day.

```sql
CREATE TABLE fx_rate_alerts (
    alert_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
    alert_type       text        NOT NULL,
    currency_code    char(3)     NOT NULL DEFAULT 'USD',
    rate_date        date        NOT NULL,
    rate_inr         numeric(12,6) NOT NULL,
    threshold_inr    numeric(12,6),
    direction        text,                    -- 'ABOVE' | 'BELOW'
    recommendation   text        NOT NULL,
    affected_bill_ids   uuid[],
    affected_invoice_ids uuid[],
    exposure_foreign numeric(18,2),
    exposure_inr     numeric(18,2),
    status           text        NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE | DISMISSED | ACTED
    created_at       timestamptz NOT NULL DEFAULT now(),
    dismissed_at     timestamptz,
    dismissed_by     text,

    UNIQUE (tenant_id, alert_type, currency_code, rate_date)
);

CREATE INDEX idx_fx_alerts_active ON fx_rate_alerts (tenant_id, status, created_at DESC);
```

### 4.5 New Table — `fx_agent_notes` (Phase 2)

AI-generated insights per bill or per report.

```sql
CREATE TABLE fx_agent_notes (
    note_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid        NOT NULL REFERENCES dim_tenants(tenant_id),
    target_type      text        NOT NULL,     -- 'BILL' | 'INVOICE' | 'EXPOSURE' | 'REPORT'
    target_id        uuid,                     -- fact_bills.invoice_id or NULL for tenant-level
    currency_code    char(3)     NOT NULL,
    severity         text        NOT NULL,     -- 'NONE' | 'INFO' | 'WARNING' | 'OPPORTUNITY'
    note_text        text        NOT NULL,
    confidence       numeric(3,2),
    context_snapshot jsonb,                    -- structured data that generated this note
    rate_at_generation numeric(12,6),
    is_current       boolean     NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_fx_notes_target ON fx_agent_notes (target_type, target_id, is_current)
    WHERE is_current = true;
CREATE INDEX idx_fx_notes_tenant ON fx_agent_notes (tenant_id, target_type, created_at DESC);
```

### 4.6 Altered Tables — stg_bills / fact_bills

Note: `currency_code` already exists on stg_bills and fact_bills (default `'INR'`).
No new currency column needed — reuse the existing one. Only add FX-specific columns.

```sql
-- Staging bills (currency_code already exists)
ALTER TABLE stg_bills
    ADD COLUMN IF NOT EXISTS original_amount    numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_rate_id         uuid          REFERENCES dim_fx_rates(rate_id);

-- Fact bills (core) (currency_code already exists)
ALTER TABLE fact_bills
    ADD COLUMN IF NOT EXISTS original_amount     numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_rate_id          uuid          REFERENCES dim_fx_rates(rate_id),
    ADD COLUMN IF NOT EXISTS inr_amount_current  numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_gain_loss        numeric(18,2);

-- Fact bills (payment settlement — core)
-- NOTE: fx_bank_spread removed (D7) — compute from fx_bill_payments at query time
ALTER TABLE fact_bills
    ADD COLUMN IF NOT EXISTS fx_actual_inr       numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_effective_rate    numeric(12,6),
    ADD COLUMN IF NOT EXISTS fx_realized_cost     numeric(18,2);

-- Fact bills (agent — phase 2)
ALTER TABLE fact_bills
    ADD COLUMN IF NOT EXISTS fx_agent_note        text,
    ADD COLUMN IF NOT EXISTS fx_agent_note_at     timestamptz;
```

### 4.7 Altered Tables — stg_invoices / fact_invoices

```sql
-- Staging invoices (currency_code already exists)
ALTER TABLE stg_invoices
    ADD COLUMN IF NOT EXISTS original_amount    numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_rate_id         uuid          REFERENCES dim_fx_rates(rate_id);

-- Fact invoices (currency_code already exists)
ALTER TABLE fact_invoices
    ADD COLUMN IF NOT EXISTS original_amount     numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_rate_id          uuid          REFERENCES dim_fx_rates(rate_id),
    ADD COLUMN IF NOT EXISTS inr_amount_current  numeric(18,2),
    ADD COLUMN IF NOT EXISTS fx_gain_loss        numeric(18,2);
```

### 4.8 Existing `original_amount` for INR Bills

For INR bills: `original_amount = total_amount`, `currency_code = 'INR'` (already the default). This ensures all queries that use `original_amount` work without COALESCE — one code path for all currencies.

Note: `currency_code` already exists on all stg/fact tables. No new column needed. The existing column already defaults to `'INR'` and is populated by OCR/API sync pipelines.

### 4.9 FX Settings in `dim_tenants.settings_jsonb`

```jsonc
{
  "fx": {
    "enabled": true,
    "reconciliation_tolerance_pct": 2.0,
    "revaluation_frequency": "DAILY",
    "alerts_enabled": true,
    "stale_alert_days": 3,
    "thresholds": {
      "USD": {
        "pay_now_below": 82.00,
        "delay_pay_above": 87.00,
        "collect_now_above": 85.00,
        "delay_collect_below": 80.00,
        "volatility_threshold_pct": 1.5
      },
      "EUR": {
        "pay_now_below": 89.00,
        "delay_pay_above": 95.00,
        "collect_now_above": 92.00,
        "delay_collect_below": 87.00,
        "volatility_threshold_pct": 1.5
      },
      "GBP": {
        "pay_now_below": 105.00,
        "delay_pay_above": 112.00,
        "collect_now_above": 108.00,
        "delay_collect_below": 103.00,
        "volatility_threshold_pct": 1.5
      },
      "JPY": {
        "pay_now_below": 0.55,
        "delay_pay_above": 0.62,
        "collect_now_above": 0.58,
        "delay_collect_below": 0.53,
        "volatility_threshold_pct": 2.0
      }
    }
  }
}
```

---

## 5. Pipeline Worker — FxRateWorker

**File**: `pipelines/fx_rate.py`
**Type**: Time-driven (`skip_backfill = True`, no CDC channel)
**Poll interval**: Once daily after 14:00 IST (RBI publishes ~13:30)
**Config key**: `fx_rate`

### 5.1 Worker Lifecycle

```
Startup
  └─ Check if dim_fx_rates is empty → if yes, run historical backfill (90 days)
  └─ Enter Phase 2: time-driven poll (once daily)

Every poll cycle:
  1. Already have a fresh rate for today? → skip
  2. Fetch all 4 currencies in one API call
  3. Upsert 4 rows into dim_fx_rates
  4. Revalue outstanding foreign currency bills (update inr_amount_current, fx_gain_loss)
  5. [Phase 2] Run FxAlertEngine for each tenant with FX exposure
  6. [Phase 2] Run FxAgentInsightGenerator for bills with material FX impact
```

### 5.2 Rate Fetch

```python
async def _fetch_rates(self) -> dict[str, Decimal] | None:
    """Returns {'USD': 85.03, 'EUR': 92.34, 'GBP': 107.30, 'JPY': 0.5614} or None."""
    for fetcher in [self._fetch_primary, self._fetch_fallback]:
        try:
            rates = await fetcher()
            if rates:
                return rates
        except Exception as exc:
            self._log.warning("FX fetch failed (%s): %s", fetcher.__name__, exc)
    return None

async def _fetch_primary(self) -> dict[str, Decimal] | None:
    """exchangerate-api.com — one call, all currencies."""
    resp = await self._http.get("https://open.er-api.com/v6/latest/INR")
    data = resp.json()
    if data.get("result") != "success":
        return None
    rates = {}
    for code in ['USD', 'EUR', 'GBP', 'JPY']:
        raw = data["rates"].get(code)
        if raw:
            rates[code] = (Decimal(1) / Decimal(str(raw))).quantize(Decimal("0.0001"))
    return rates
```

### 5.3 Revaluation (Daily)

Bulk UPDATE per currency (no tenant loop needed — RBI rate is the same for everyone):

```sql
-- Run once per currency (4 statements total, not per-tenant)
UPDATE fact_bills
SET inr_amount_current = original_amount * $1,   -- today's rate
    fx_gain_loss       = (original_amount * $1) - total_amount
WHERE currency_code = $2
  AND currency_code != 'INR'
  AND status NOT IN ('PAID', 'VOID');
```

4 UPDATE statements daily (one per currency). Runs once after 14:00 IST. The rate only changes once per day.

### 5.4 Historical Backfill

On first deployment, if `dim_fx_rates` is empty:

```python
async def _backfill_historical(self, days: int = 90) -> int:
    """Fetch past 90 days of rates for all 4 currencies.
    Uses the timeseries endpoint (1 API call for the full range)
    to avoid burning 90 individual calls against the free-tier rate limit.
    """
    # exchangeratesapi.io timeseries: GET /timeseries?start_date=...&end_date=...
    # Returns all dates in one response — 1 call, not 90
    ...
```

### 5.4.1 Rate-Limit Awareness

The free-tier API has ~1,500 requests/month. Safeguards:
- Always check `dim_fx_rates` before calling the API (skip if today's rate exists)
- Use timeseries endpoint for backfill (1 call for 90 days, not 90 calls)
- Respect `Retry-After` header if rate-limited
- Log warnings on 429 responses; do not retry immediately

### 5.5 PIPELINE_CFG

```python
PIPELINE_CFG = {
    "fx_rate": {
        "poll_interval_s":      3600.0,    # 1 hour (but skips if today's rate exists)
        "idle_poll_interval_s": 14400.0,   # 4 hours outside market hours
        "error_backoff_s":      300.0,
        "error_backoff_cap_s":  3600.0,
    },
}
```

---

## 6. Currency Detection at OCR

### 6.1 OCR Prompt Extension

The existing GPT-4o vision prompt in `utils/ocr_engine.py` is extended to extract `currency_code`:

```
Extract the following additional field:
- currency_code: ISO 4217 currency code (USD, EUR, GBP, JPY, or INR). 
  Look for currency symbols ($, €, £, ¥, ₹) or text indicators.
  Default to INR if no foreign currency indicator is found.
```

### 6.2 Confidence-Based Gating

| OCR Confidence | Handling |
|---|---|
| High (clear symbol, explicit "USD" text, known foreign vendor) | Auto-apply currency, no flag |
| Low (ambiguous, Indian vendor with `$` in description) | Set `validation_flag = REVIEW`, user confirms currency at review stage |
| None / blank | Default to INR |

At the review stage, if currency needs confirmation, a single dropdown appears: "Currency: [INR ▾]" — one click to change if needed.

### 6.3 Structured Uploads (CSV/XLSX)

Accept an optional `currency` column. If present, values must be one of: `INR`, `USD`, `EUR`, `GBP`, `JPY`. If absent, default to INR.

### 6.4 API Payloads (Apideck / Tally)

Apideck-unified and Tally payloads already carry a `currency` field. The `ApiSyncWorker` normaliser maps it to `currency_code`. Unknown currency codes default to INR with a warning log.

---

## 7. Auto-Conversion at Staging Promotion

### 7.1 Conversion Logic

`StagingPromotionWorker` calls the conversion function before writing to fact tables:

```python
async def _convert_currency(self, conn, bill: dict) -> dict:
    currency = bill.get("currency_code", "INR")
    
    if currency == "INR":
        bill["original_amount"] = bill["total_amount"]
        return bill

    # Find the rate for bill_date (or nearest available before it)
    rate_row = await conn.fetchrow(
        """SELECT inr_per_unit, rate_id FROM dim_fx_rates
           WHERE currency_code = $1 AND rate_date <= $2
           ORDER BY rate_date DESC LIMIT 1""",
        currency, bill["bill_date"],
    )

    if not rate_row:
        # No rate available — BLOCK promotion, surface to user (D11)
        # A foreign bill without a rate would show wrong INR amounts
        self._log.warning(
            "No FX rate for %s on %s — bill %s blocked in staging",
            currency, bill["bill_date"], bill.get("bill_number"),
        )
        raise FxRateMissing(currency, bill["bill_date"], bill.get("bill_number"))

    rate = rate_row["inr_per_unit"]
    original = Decimal(str(bill["original_amount"] or bill["total_amount"]))

    bill["total_amount"]  = (original * rate).quantize(Decimal("0.01"))
    bill["fx_rate_id"]    = rate_row["rate_id"]
    # original_amount and currency_code already set from OCR/ingest
    return bill
```

### 7.2 Key Rule

`total_amount` is **always INR** after promotion. This is the canonical reporting amount. Every existing query, report, approval threshold, and TDS calculation continues to work without modification.

### 7.3 Missing Rate — Block Promotion (D11)

If no rate exists for the bill date:
1. Uses the nearest available rate before that date (normal path)
2. If no rate exists at all for that currency, **block promotion**:
   - Set `validation_flag = 'REVIEW'` with reason `'NO_FX_RATE'`
   - Bill stays in staging; user sees it in the Review page
   - Message: "No exchange rate available for {currency} on {date}. Will retry automatically."
   - Promotion resumes automatically once FxRateWorker fetches the rate
3. Never silently promote a foreign bill as INR — a $10,000 bill showing as ₹10,000 destroys trust

---

## 8. Payment Flow — Back-Calculation

### 8.1 Principle

The system doesn't ask the user for bank rates, SWIFT charges, or settlement details. The user enters **one number** — actual INR paid — and the system derives everything else.

### 8.2 Mark as Paid — Non-INR Bills

When the user clicks "Mark as Paid" on a foreign currency bill, the existing payment modal adds one field:

```
┌─ Mark as Paid ──────────────────────────────────┐
│                                                 │
│  Actual Amount Paid (INR):  [₹ 85,750     ]     │
│                                                 │
│  Payment Reference/UTR:    [UTIB2026061234]     │
│  Payment Date:             [15 Jun 2026   ]     │
│                                                 │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄   │
│  RBI rate today: ₹85.00 (for reference only)    │
│  Estimated: ₹85,000  ·  You paid ₹750 more     │
│                                                 │
│              [ Cancel ]   [ Confirm ]           │
└─────────────────────────────────────────────────┘
```

**Default value**: Pre-filled with `original_amount × today's RBI rate`. User can accept (one click, same as today) or override with actual bank debit.

**Live feedback**: The summary line under the input updates as the user types — instant back-calculation, no submit-wait-result cycle.

For **INR bills** — this field does not appear. Payment flow stays exactly as it is today.

### 8.3 Back-Calculation

When the user enters actual INR paid:

```python
effective_rate    = actual_inr / foreign_amount         # ₹85,750 / $1,000 = ₹85.75
rbi_rate          = get_rate(payment_date, currency)     # ₹85.00
bank_spread       = effective_rate - rbi_rate            # ₹0.75
bank_spread_pct   = (bank_spread / rbi_rate) * 100       # 0.88%
fx_realized_cost  = actual_inr - total_amount            # ₹85,750 - ₹83,420 = ₹2,330
```

### 8.4 Storage

Written to `fx_bill_payments` (one row per payment) and summary fields on `fact_bills`:
- `fx_actual_inr` = total actual INR paid across all partial payments
- `fx_effective_rate` = weighted average effective rate
- `fx_realized_cost` = `fx_actual_inr - total_amount` (vs booked INR)

Note: Bank spread is stored per-payment in `fx_bill_payments` only (D7). The weighted average spread for a bill is computed at query time from `fx_bill_payments`, not denormalized onto `fact_bills`.

### 8.5 Auto-Reconciliation

When settlement data arrives from an external source (Tally sync, bank statement CSV import), the system:
1. Matches the payment to a bill by amount/date/reference
2. Compares against the recorded `fx_actual_inr`
3. If difference < reconciliation tolerance (default 2%) → auto-update silently, log in audit trail
4. If difference > tolerance → flag for review: "Bank statement shows ₹85,750 vs recorded ₹85,000. Approve the update?"

---

## 9. Partial Payments & Credit Notes

### 9.1 Design Principle — Everything at Booked Rate

All amounts are tracked in the original foreign currency. INR conversion uses the booked rate (rate on bill_date) for the canonical `total_amount`. FX cost is only computed when money actually moves — at payment time.

### 9.2 Partial Payments

$1,000 USD bill, paid in two instalments:

| Event | Foreign | Actual INR | Effective Rate | FX Cost |
|---|---|---|---|---|
| Bill booked (01 Jun) | $1,000 | — | ₹83.42 (booked) | — |
| Payment 1 (15 Jun) | $500 | ₹42,875 | ₹85.75 | +₹1,165 |
| Payment 2 (15 Jul) | $500 | ₹41,500 | ₹83.00 | -₹210 |
| **Total** | **$1,000** | **₹84,375** | **₹84.38 avg** | **+₹955** |

Each payment is a separate row in `fx_bill_payments`. The bill detail shows each payment line with its own effective rate. FX cost accumulates.

Between payments, outstanding is calculated as:
- Foreign remaining: $500
- Booked INR remaining: ₹41,710 (500 × 83.42)
- Current INR estimate: $500 × today's rate (for dashboard/exposure display)

### 9.3 Credit Notes

A credit note reduces the bill in the original currency at the original booked rate:

```
Bill: $1,000 → booked at ₹83.42 → total_amount = ₹83,420
Credit: -$200 → total_amount = ₹66,736 (800 × 83.42)
```

- `original_amount` drops from $1,000 to $800
- `total_amount` drops from ₹83,420 to ₹66,736
- No `fx_bill_payments` entry — no money moved
- No FX gain/loss from the credit itself
- Logged in audit trail / `bill_approval_chain` as today

### 9.4 Bill Detail — Partial Payment View

```
┌─────────────────────────────────────────────────┐
│  AWS Invoice #1234              Partially Paid  │
│  Original: $1,000 USD  ·  Credit: -$200         │
│  Net: $800  ·  Booked @ ₹83.42                  │
│                                                 │
│  Payments:                                      │
│  ├─ 15 Jun: $400 → ₹33,900 (@ ₹84.75)          │
│  └─ Remaining: $400 ≈ ₹34,000 at today's rate   │
│                                                 │
│  FX Cost So Far: ₹532                           │
└─────────────────────────────────────────────────┘
```

---

## 10. FX AI Agent — FxCurrencyAgent

### 10.1 Architecture

The FX agent is not just a pipeline worker — it's an LLM-powered agent that generates contextual insights using GPT-4o-mini.

```
┌─────────────────────────────────────────────────────────────┐
│                      FX CURRENCY AGENT                       │
│                                                             │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────┐   │
│  │ Rate Engine │    │ Analysis     │    │ GPT-4o-mini   │   │
│  │ (fetch +    │───>│ Engine       │───>│ Insight       │   │
│  │  store)     │    │ (compute)    │    │ Generator     │   │
│  └─────────────┘    └──────────────┘    └───────────────┘   │
│        │                   │                    │            │
│        v                   v                    v            │
│  dim_fx_rates      FxAgentContext        fx_agent_notes     │
│  (deterministic)   (structured data      (per-bill,         │
│                     for LLM prompt)       per-report)       │
└─────────────────────────────────────────────────────────────┘
```

### 10.2 Three Components

**Component 1 — Rate Engine** (deterministic, no LLM)
The `FxRateWorker` from §5. Fetches rates, stores, revalues. Pure data.

**Component 2 — Analysis Engine** (deterministic, no LLM)
Computes structured metrics that feed the LLM:

```python
@dataclass
class FxAgentContext:
    # Rate data (per currency)
    rates: dict  # {currency: {today, 7d_ago, 30d_ago, trend, volatility_pct}}
    
    # Tenant exposure (per currency)
    outstanding_bills: list     # [{bill_id, vendor, amount_foreign, currency, due_date, booked_rate, current_inr, fx_impact}]
    outstanding_invoices: list  # same shape for AR
    net_exposure: dict          # {currency: {ap_foreign, ar_foreign, net, ap_inr, ar_inr}}
    
    # Payment history
    recent_payments: list       # [{bill_id, vendor, amount_foreign, currency, booked_rate, effective_rate, bank_spread_pct, fx_cost}]
    avg_bank_spread: dict       # {currency: pct}
    total_fx_cost_qtd: Decimal
```

**Component 3 — GPT Insight Generator** (LLM-powered)
Takes structured context and generates natural language insights. Different prompts for different contexts:

```python
FX_AGENT_PROMPTS = {
    "bill_note": """
        You are an FX advisor for an Indian SME. Given this bill and rate context,
        write 1-2 sentences for the bill detail view.
        
        Rules:
        - Only write if there's an actionable insight or notable fact
        - If FX impact < ₹500 or rate change < 1%, return empty
        - Be specific: mention amounts, dates, percentages
        - Suggest actions when appropriate
        - Never use jargon (hedge, forward contract, etc.)
        
        Context: {context_json}
        Return JSON: {"note": "...", "severity": "INFO|WARNING|OPPORTUNITY", "confidence": 0.0-1.0}
    """,
    
    "payment_analysis": """
        User just paid a foreign currency bill. Back-calculation done.
        Write 1-2 sentences about this payment.
        Flag if bank spread is higher than their average.
        Compare to previous payments.
        
        Payment: {payment_json}
        History: {history_json}
    """,
    
    "exposure_briefing": """
        Write a 3-4 sentence briefing for the CFO about their current
        foreign currency exposure. Lead with the most important insight.
        Quantify everything in rupees. If there's a clear action, say it.
        Compare to their own history, not abstract benchmarks.
        
        Context: {context_json}
    """,
    
    "quarterly_report": """
        Generate a quarterly FX cost analysis narrative. Cover:
        - Total FX cost (rate movement + bank spread, broken out)
        - Month-over-month trend
        - One specific, actionable recommendation
        
        Context: {context_json}
    """
}
```

### 10.3 When the Agent Runs

| Trigger | What Happens | Output |
|---|---|---|
| **Daily rate fetch** (14:00 IST) | Revalue → compute exposure → generate notes for bills with material FX impact | Bill notes updated, exposure briefing cached |
| **User marks bill as Paid** (event) | Back-calculate → generate payment insight | Payment analysis note frozen on bill |
| **CFO opens FX report** (on-demand) | Fresh context → generate report narrative | Rendered in report UI |
| **Rate moves >1% from yesterday** (detected during poll) | Re-run notes for affected bills → notification | Notification created if actionable |

### 10.4 Worthiness Check — Don't Generate Noise

The agent only calls GPT when there's something non-obvious to say:

```python
def _is_worth_generating(self, bill, rate_change_pct, fx_impact_inr) -> bool:
    if abs(fx_impact_inr) < 500:           # less than ₹500 impact
        return False
    if abs(rate_change_pct) < 1.0:          # rate moved less than 1%
        return False
    if not self._pattern_detected(bill):    # nothing interesting in history
        return False
    return True
```

This cuts GPT calls by ~80% and ensures notes that DO appear are worth reading.

### 10.5 Severity Levels

| Severity | UI Behavior | Example |
|---|---|---|
| `NONE` | No note shown | INR bill, or trivial FX impact |
| `INFO` | Small text in bill detail, no badge in list | "Booked at ₹83.42, current ₹83.50 — minimal impact" |
| `WARNING` | Amber dot on bill in list + note in detail | "Rate has risen ₹2.58 since booking — ₹2,580 more if paid today" |
| `OPPORTUNITY` | Green dot + note + notification | "Rate dropped to ₹82.15 — paying now saves ₹1,270 vs last week" |

Low confidence notes (< 0.7) are prefixed with "Estimated:".

### 10.6 Cost

GPT-4o-mini: ~$0.15 per 1M input tokens. Per-bill notes: ~250 tokens. For a tenant with 20 outstanding foreign currency bills: ~$0.02/month. Negligible.

---

## 11. Alert Engine

### 11.1 Overview

Runs inside `FxRateWorker` after each daily rate upsert. Evaluates per-tenant, per-currency, only for tenants with active foreign currency exposure.

### 11.2 Alert Types

| Alert Type | Trigger | Recommendation |
|---|---|---|
| `PAY_NOW_OPPORTUNITY` | Rate ≤ `pay_now_below` threshold | Currency is cheap — pay foreign bills now |
| `DELAY_PAYMENT` | Rate ≥ `delay_pay_above` threshold | Currency is expensive — defer if possible |
| `COLLECT_NOW_OPPORTUNITY` | Rate ≥ `collect_now_above` threshold | Currency is valuable — chase foreign receivables |
| `RATE_VOLATILE` | 7-day std-dev ÷ mean × 100 ≥ `volatility_threshold_pct` | Rate swinging widely — be cautious |
| `RATE_STALE` | No fresh rate for ≥ `stale_alert_days` days | Data may be outdated |

### 11.3 Alert Deduplication

One ACTIVE alert per `(tenant_id, alert_type, currency_code, rate_date)`. Same condition on same day → upsert, not duplicate.

### 11.4 Alert Delivery

Alerts surface via the **existing notification bell** — same pattern as approval notifications. No separate alert drawer or page. The user sees:

> "EUR/INR dropped to ₹89.50 — below your ₹90.00 threshold. You have 2 EUR bills worth €3,000 due this week."

Click → jumps to affected bills. Dismiss → marks alert DISMISSED.

### 11.5 Alerts Are Advisory Only

No alert prevents a bill from being paid or an invoice from being collected. The flags are recommendations. The user retains full control.

---

## 12. Dashboard & Bills UI

### 12.1 Design Principle

> Every number the user sees is at the current rate. The system handles FX silently. UI surfaces information only when the user needs to act or when the agent has a non-obvious insight.

### 12.2 Dashboard — Current Rate, No Dual Display

All existing KPIs (Total Outstanding, Spend, Pending Approval) use `total_amount` for INR bills and `inr_amount_current` for foreign currency bills. The number reflects reality — what you'd actually pay today. No "booked vs current" comparison on the dashboard.

**FX Exposure Card** (appears only when tenant has foreign currency bills):

```
┌─────────────────────────────────────┐
│  Foreign Currency Exposure          │
│                                     │
│  Total: ₹12,40,000 across 3        │
│         currencies                  │
│                                     │
│  USD  $5,000   ₹4,25,000   62%     │
│  EUR  €2,000   ₹1,90,000   28%     │
│  GBP  £1,000   ₹1,07,300   10%     │
│                                     │
│  [🤖 Agent briefing if available]   │
└─────────────────────────────────────┘
```

Only currencies with active exposure shown. No empty rows. Agent briefing appears only when the agent has computed something worth saying.

### 12.3 Bills Page — Currency Tag

Foreign currency bills get a small tag after the amount. INR bills — no change.

```
| Vendor       | Amount              | Due     | Status  |
|--------------|---------------------|---------|---------|
| AWS Inc.     | ₹85,000 ($1,000)    | 15 Jun  | PENDING |
| Siemens AG   | ₹1,84,680 (€2,000)  | 30 Jun  | PENDING |
| Rent Co      | ₹50,000             | 01 Jul  | PENDING |
```

Agent severity dots (Phase 2):
- Amber dot — WARNING level agent note
- Green dot — OPPORTUNITY level agent note
- No dot — INFO or NONE (user only sees note if they open the bill detail)

### 12.4 Bill Detail — Foreign Currency Bills

```
┌─────────────────────────────────────────────────┐
│  AWS Invoice #1234                              │
│  Amount: ₹85,000  ·  $1,000 USD @ ₹85.00       │
│                                                 │
│  [🤖 Agent note — only if severity > NONE]      │
│                                                 │
│  TDS: ₹8,342  ·  Net Payable: ₹76,658          │
└─────────────────────────────────────────────────┘
```

### 12.5 Paid Bill Detail — Settlement Info

```
┌─────────────────────────────────────────────────┐
│  AWS Invoice #1234                    ✓ PAID    │
│  Amount: ₹85,750 paid  ·  $1,000 USD           │
│                                                 │
│  Booked @ ₹83.42 (05 Jun)                      │
│  Settled @ ₹85.75 (15 Jun)                     │
│  FX Cost: ₹2,330  ·  Bank spread: ₹0.75/USD    │
│                                                 │
│  [🤖 Agent insight on settlement]               │
└─────────────────────────────────────────────────┘
```

### 12.6 INR Bills — Zero Change

No currency tags, no FX strips, no agent notes, no extra payment fields. Identical to current UI.

---

## 13. CFO Reports — FX Cost Analysis

### 13.1 Scope

One report to start: **FX Cost Analysis** — the quarterly answer to "how much did currency cost us?"

Accessible via Reports page → "Currency & FX" tab. Only appears for tenants that have had foreign currency bills.

### 13.2 Report Layout

```
┌────────────────────────────────────────────────────────────────┐
│  FX Cost Analysis — Q1 FY 2026-27 (Apr–Jun)                  │
│                                                                │
│  Currency  Paid        Booked INR    Actual INR    FX Cost     │
│  USD       $12,500     ₹10,42,750    ₹10,68,200    ₹25,450   │
│  EUR       €4,000      ₹3,64,400     ₹3,71,800     ₹7,400    │
│  GBP       £1,000      ₹1,05,200     ₹1,07,300     ₹2,100    │
│  ──────────────────────────────────────────────────────────    │
│  Total                 ₹15,12,350    ₹15,47,300    ₹34,950   │
│                                                                │
│  Breakdown:                                                    │
│    Rate movement:    ₹27,700  (currencies moved against INR)   │
│    Bank spread:      ₹7,250   (avg 0.84% across all payments)  │
│                                                                │
│  ┌─ Monthly Trend ────────────────────────────────────┐        │
│  │  April:  FX cost ₹4,200   (1.7% of booked)       │        │
│  │  May:    FX cost ₹8,100   (2.1% of booked)       │        │
│  │  June:   FX cost ₹22,650  (3.8% of booked)       │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                │
│  ┌─ Bank Spread by Currency ──────────────────────────┐        │
│  │  USD:  avg 0.92%  (12 payments)                   │        │
│  │  EUR:  avg 0.78%  (4 payments)                    │        │
│  │  GBP:  avg 0.65%  (2 payments)                    │        │
│  └────────────────────────────────────────────────────┘        │
│                                                                │
│  ┌─ 🤖 Agent Analysis ────────────────────────────────────┐    │
│  │  [GPT-generated quarterly narrative — trends, bank     │    │
│  │   spread analysis, one actionable recommendation]      │    │
│  └────────────────────────────────────────────────────────┘    │
│                                                                │
│  [ Download XLSX ]  [ Download CSV ]                           │
└────────────────────────────────────────────────────────────────┘
```

### 13.3 Expense Report Update

The existing monthly XLSX expense report adds columns automatically when non-INR bills exist:

| New Column | Description |
|---|---|
| ORIGINAL CURRENCY | `USD`, `EUR`, `GBP`, `JPY`, or `INR` |
| ORIGINAL AMOUNT | Raw amount before conversion |
| FX RATE | Rate used for conversion (booked rate) |

A separate "FX Revaluation" sheet is added for outstanding items showing current vs booked values.

---

## 14. Settings Page — FX Configuration

### 14.1 Location

New section in Settings: **"FX & Currency"** — appears only for admin users.

### 14.2 Layout

```
FX & Currency Settings
│
├─ Enable FX Alerts          [Toggle: ON/OFF]
├─ Reconciliation Tolerance  [2.0 %]
│
├─ Currency Thresholds
│  (shows only currencies tenant has had bills in)
│
│  ┌─ USD ─────────────────────────────────┐
│  │  Pay now below:       [₹ 82.00]      │
│  │  Delay payment above: [₹ 87.00]      │
│  │  Collect now above:   [₹ 85.00]      │
│  │  Volatility alert:    [1.5 %]        │
│  └───────────────────────────────────────┘
│
│  ┌─ EUR ─────────────────────────────────┐
│  │  (same fields)                        │
│  └───────────────────────────────────────┘
│
│  [ + Add Currency Thresholds ]
│
└─ Stale Rate Alert (days):  [3]
```

### 14.3 Defaults

Thresholds are pre-populated with sensible defaults from `FX_DEFAULT_SETTINGS` in config. Admin can override per currency.

---

## 15. Integration with Existing Workers

| Worker | Change Required | Phase |
|---|---|---|
| `UploadOcrWorker` | Extract `currency_code` from OCR payload; default INR | Core |
| `ApiSyncWorker` | Map Apideck/Tally `currency` field → `currency_code` | Core |
| `StagingPromotionWorker` | Call `_convert_currency()` before writing to fact tables | Core |
| `DeduplicationWorker` | Use `COALESCE(original_amount, total_amount)` + `currency_code` in dedup partition key (D12) | Core |
| `WebhookWorker` | No change — Razorpay/Stripe settle in INR | — |
| `ApprovalWorker` | No change — thresholds use `total_amount` (already INR) | — |
| `TdsProcessingWorker` | No change — calculates on `total_amount` (already INR) | — |
| `CategoryEnrichmentWorker` | No change | — |
| `GstVerificationWorker` | No change | — |
| `BankVerificationWorker` | No change | — |
| `TallyReadWorker` | Map Tally voucher currency → `currency_code` | Core |
| `TallySyncWorker` | No change — Tally handles its own currency | — |
| `FxRateWorker` (new) | Fetch rates, revalue, run alert engine | Core |
| `FxCurrencyAgent` (new) | AI insights, per-bill notes | Phase 2 |

---

## 16. API Endpoints

### 16.1 Core Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/fx/rate?currency=USD` | Current rate for a currency |
| GET | `/api/fx/rates` | Current rates for all 4 currencies |
| GET | `/api/fx/history?currency=USD&days=30` | Rate history |
| GET | `/api/fx/exposure?company_id=<uuid>` | Per-currency AP/AR exposure summary (see query below) |
| POST | `/api/bills/{id}/fx-payment` | Record FX payment with actual INR (back-calculates) |
| GET | `/api/bills/{id}/fx-payments` | List all FX payments for a bill |

**Exposure query (D10 — prevent N+1):**

```sql
SELECT currency_code,
       COUNT(*)                    AS bill_count,
       SUM(original_amount)        AS total_foreign,
       SUM(inr_amount_current)     AS total_inr_current,
       SUM(total_amount)           AS total_inr_booked,
       SUM(inr_amount_current - total_amount) AS unrealised_gain_loss
FROM fact_bills
WHERE tenant_id = $1
  AND currency_code != 'INR'
  AND status NOT IN ('PAID', 'VOID')
GROUP BY currency_code
ORDER BY SUM(inr_amount_current) DESC;
```

### 16.2 Phase 2 Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/fx/alerts?company_id=<uuid>` | Active alerts for tenant |
| POST | `/api/fx/alerts/{id}/dismiss` | Dismiss an alert |
| GET | `/api/fx/settings?company_id=<uuid>` | FX threshold settings |
| PUT | `/api/fx/settings` | Update FX threshold settings |
| GET | `/api/fx/report?company_id=<uuid>&quarter=Q1&fy=2026-27` | FX cost analysis data |
| GET | `/api/fx/agent-note?bill_id=<uuid>` | Current agent note for a bill |

---

## 17. Configuration Reference

### 17.1 Environment Variables

| Variable | Default | Description |
|---|---|---|
| `FX_FETCH_CURRENCIES` | `USD,EUR,GBP,JPY` | Currencies to fetch (comma-separated) |
| `FX_PRIMARY_SOURCE_URL` | `https://open.er-api.com/v6/latest/INR` | Primary rate source |
| `FX_FALLBACK_API_KEY` | — | exchangeratesapi.io key (fallback) |
| `FX_BACKFILL_DAYS` | `90` | Historical days to backfill on first run |
| `FX_ALERT_POLL_INTERVAL_S` | `3600` | Seconds between FxRateWorker polls |

### 17.2 Per-Tenant Defaults

Applied when a tenant has no FX settings configured yet:

```python
FX_DEFAULT_SETTINGS = {
    "enabled": True,
    "reconciliation_tolerance_pct": 2.0,
    "revaluation_frequency": "DAILY",
    "alerts_enabled": True,
    "stale_alert_days": 3,
    "thresholds": {
        "USD": {"pay_now_below": 82.0, "delay_pay_above": 87.0, "collect_now_above": 85.0, "volatility_threshold_pct": 1.5},
        "EUR": {"pay_now_below": 89.0, "delay_pay_above": 95.0, "collect_now_above": 92.0, "volatility_threshold_pct": 1.5},
        "GBP": {"pay_now_below": 105.0, "delay_pay_above": 112.0, "collect_now_above": 108.0, "volatility_threshold_pct": 1.5},
        "JPY": {"pay_now_below": 0.55, "delay_pay_above": 0.62, "collect_now_above": 0.58, "volatility_threshold_pct": 2.0},
    },
}
```

---

## 18. Design Decisions

### 18.1 Rate locked at transaction date, not payment date

The `fx_rate_id` references the rate on `bill_date` (invoice date). This mirrors IAS 21 — foreign currency transactions are initially recognised at the spot rate on the transaction date. The difference between booked rate and settlement rate is an FX cost — tracked per payment in `fx_bill_payments`.

### 18.2 INR is always the canonical amount in `total_amount`

All existing queries, indexes, approval thresholds, and report aggregations use `total_amount`. Keeping this column always in INR means zero changes to all downstream consumers. `original_amount` + `currency_code` (existing column) is additive metadata.

### 18.3 RBI-sourced rates, not mid-market

The RBI/FBIL reference rate is the only rate with regulatory standing in India. Using a commercial mid-market rate would create discrepancies with import/export documentation and bank reconciliation. The exchangerate-api.com source tracks FBIL/ECB rates closely.

### 18.4 One worker, shared rate table

A single `FxRateWorker` fetches one rate per currency per day into `dim_fx_rates`. The alert engine runs per-tenant against shared rates. No duplicate API calls across tenants.

### 18.5 Alerts are advisory, not blocking

No alert prevents payment or collection. The user retains full control. SMEs have contractual deadlines and cash constraints that override FX optimisation.

### 18.6 Agentic philosophy — silent by default, flag when it matters

The system handles multi-currency silently. No extra columns, strips, or badges unless the agent has a non-obvious insight. The user doesn't manage FX — the system does. The user acts only when flagged.

### 18.7 Back-calculation over manual entry

Instead of asking for bank rate, SWIFT charges, or settlement details, the user enters one number (actual INR paid) and the system derives everything. Pre-filled default means zero extra friction for users who accept the estimate.

### 18.8 Booked rate for credit notes

Credit notes adjust the foreign currency liability, not the INR. They're converted at the original booked rate because no money moves — there's no real FX transaction. This avoids phantom FX gains/losses on paper adjustments.

### 18.9 Partial payments tracked individually

Each partial payment gets its own row with its own effective rate and bank spread. This enables per-payment cost analysis and accurate bank spread tracking over time.

### 18.10 Four currencies only — regulatory grade

Limited to USD, EUR, GBP, JPY because these are the only currencies with RBI/FBIL-published daily reference rates. No market-tier or aggregator-only currencies. The schema supports adding more — one row in `dim_currencies` + config change.

### 18.11 Revaluation once daily, not hourly

RBI publishes one rate per day. Hourly revaluation creates the illusion of instability with no new information. Daily revaluation after 14:00 IST (post-RBI publish) is sufficient.

---

## 19. Implementation Phases

### Phase 1 — Core

| # | Task | Files |
|---|---|---|
| 1 | Migration: create `dim_currencies`, `dim_fx_rates`, `fx_bill_payments` + alter stg/fact tables | `main.py` |
| 2 | Seed `dim_currencies` with 5 rows (INR + 4 foreign) | `main.py` |
| 3 | `FxRateWorker` — rate fetch, upsert, historical backfill, daily revaluation | `pipelines/fx_rate.py` |
| 4 | OCR prompt — extract `currency_code` | `utils/ocr_engine.py` |
| 5 | Upload OCR — pass `currency_code` through to stg tables | `pipelines/upload_ocr.py` |
| 6 | API sync — map Apideck/Tally currency field | `pipelines/api_sync.py` |
| 7 | Staging promotion — `_convert_currency()` before fact write; block if no rate (D11) | `pipelines/staging_promotion.py` |
| 8 | Dedup — use `COALESCE(original_amount, total_amount)` + `currency_code` in partition key (D12) | `pipelines/deduplication.py` |
| 9 | FX API endpoints (rate, rates, history, exposure, fx-payment) | `backend_ocr/app/fx_api.py` |
| 10 | Frontend: TypeScript interfaces for FX types | `frontend/src/api/invoices.ts` |
| 11 | Frontend: currency tag on bills list | `frontend/src/pages/BillsPage.tsx` |
| 12 | Frontend: FX info in bill detail (for foreign currency bills) | `frontend/src/pages/BillsPage.tsx` |
| 13 | Frontend: payment modal — actual INR field for non-INR bills | `frontend/src/pages/BillsPage.tsx` |
| 14 | Frontend: paid bill detail — settlement info | `frontend/src/pages/BillsPage.tsx` |
| 15 | Frontend: partial payment view in bill detail | `frontend/src/pages/BillsPage.tsx` |
| 16 | Frontend: FX exposure card on dashboard | `frontend/src/pages/DashboardPage.tsx` |
| 17 | Frontend: expense report — currency columns when non-INR exists | `frontend/src/pages/ReportsPage.tsx` |

### Phase 2 — AI Agent & Alerts

| # | Task | Files |
|---|---|---|
| 18 | Migration: create `fx_rate_alerts`, `fx_agent_notes` + agent columns on fact_bills | `main.py` |
| 19 | `FxCurrencyAgent` — analysis engine + GPT insight generator | `pipelines/fx_agent.py` |
| 20 | Alert engine — threshold evaluation, alert creation, notification integration | `pipelines/fx_rate.py` |
| 21 | FX API endpoints (alerts, dismiss, settings, report, agent-note) | `backend_ocr/app/fx_api.py` |
| 22 | Frontend: agent note display on bill detail | `frontend/src/pages/BillsPage.tsx` |
| 23 | Frontend: severity dots on bills list | `frontend/src/pages/BillsPage.tsx` |
| 24 | Frontend: agent briefing on dashboard exposure card | `frontend/src/pages/DashboardPage.tsx` |
| 25 | Frontend: FX Cost Analysis report tab | `frontend/src/pages/ReportsPage.tsx` |
| 26 | Frontend: FX settings section (thresholds, toggles) | `frontend/src/pages/SettingsPage.tsx` |
| 27 | Frontend: alert notifications via existing notification bell | `frontend/src/components/TopBar.tsx` |

---

## 20. Files to Create / Modify

| File | Action | Phase |
|---|---|---|
| `pipelines/fx_rate.py` | **CREATE** — rate fetch + revaluation + alert engine | Core + Phase 2 |
| `pipelines/fx_agent.py` | **CREATE** — AI analysis + GPT insight generation | Phase 2 |
| `backend_ocr/app/fx_api.py` | **CREATE** — all FX API endpoints | Core + Phase 2 |
| `backend_ocr/app/main.py` | MODIFY — migration for new tables/columns, include fx_api router | Core |
| `backend_ocr/app/ingest.py` | MODIFY — pass `original_amount` through (currency_code already handled) | Core |
| `backend_ocr/app/config.py` | MODIFY — add FX config vars and defaults | Core |
| `pipelines/staging_promotion.py` | MODIFY — `_convert_currency()` before fact write | Core |
| `pipelines/upload_ocr.py` | MODIFY — extract and pass `currency_code` | Core |
| `pipelines/deduplication.py` | MODIFY — use `COALESCE(original_amount, total_amount)` + `currency_code` in dedup partition (D12) | Core |
| `pipelines/api_sync.py` | MODIFY — map currency field from API payloads | Core |
| `utils/ocr_engine.py` | MODIFY — add `currency_code` to OCR prompt | Core |
| `main.py` (root) | MODIFY — add `FxRateWorker` and `FxCurrencyAgent` to worker list | Core + Phase 2 |
| `frontend/src/api/invoices.ts` | MODIFY — FX TypeScript interfaces + API functions | Core |
| `frontend/src/pages/BillsPage.tsx` | MODIFY — currency tag, payment modal, settlement info, agent notes | Core + Phase 2 |
| `frontend/src/pages/DashboardPage.tsx` | MODIFY — FX exposure card | Core + Phase 2 |
| `frontend/src/pages/ReportsPage.tsx` | MODIFY — FX Cost Analysis tab, expense report currency columns | Core + Phase 2 |
| `frontend/src/pages/SettingsPage.tsx` | MODIFY — FX & Currency settings section | Phase 2 |
| `frontend/src/components/TopBar.tsx` | MODIFY — FX alert notifications | Phase 2 |

---

## 21. Testing

### 21.1 Unit Tests (`tests/test_fx.py`)

| Test | Phase |
|---|---|
| `test_convert_usd_to_inr` — $1,000 × ₹85.00 = ₹85,000.00 | Core |
| `test_convert_eur_to_inr` — €2,000 × ₹92.34 = ₹1,84,680.00 | Core |
| `test_convert_gbp_to_inr` — £500 × ₹107.30 = ₹53,650.00 | Core |
| `test_convert_jpy_to_inr` — ¥100,000 × ₹0.5614 = ₹56,140 (zero decimals) | Core |
| `test_convert_inr_passthrough` — INR amounts pass through unchanged | Core |
| `test_rate_carry_forward_weekend` — Saturday uses Friday's rate | Core |
| `test_missing_rate_graceful` — no rate for date → uses nearest, no crash | Core |
| `test_back_calculation` — actual ₹85,750 / $1,000 = effective ₹85.75 | Core |
| `test_bank_spread` — effective ₹85.75 - RBI ₹85.00 = spread ₹0.75 (0.88%) | Core |
| `test_partial_payment_accumulation` — two partials accumulate fx_cost correctly | Core |
| `test_credit_note_at_booked_rate` — credit reduces at booked rate, no FX entry | Core |
| `test_dedup_same_currency_bill` — exact dup USD bill (same amount+date) blocked | Core |
| `test_dedup_cross_currency` — $1,000 USD ≠ ₹85,000 INR (different bills) | Core |
| `test_tds_on_foreign_bill` — TDS calculated on `total_amount` (already INR) | Core |
| `test_stale_alert_after_3_days` — no fresh rate for 3 days → RATE_STALE | Phase 2 |
| `test_pay_now_alert` — rate below threshold → PAY_NOW_OPPORTUNITY | Phase 2 |
| `test_volatility_alert` — std-dev > threshold → RATE_VOLATILE | Phase 2 |
| `test_alert_deduplication` — same alert same day → upsert not duplicate | Phase 2 |
| `test_agent_worthiness_check` — trivial impact → no GPT call | Phase 2 |
| `test_agent_note_severity` — correct severity assignment | Phase 2 |

### 21.1b Additional Unit Tests — Review Gaps (D9)

| Test | Phase |
|---|---|
| `test_fetch_primary_success` — mock API returns valid rates, all 4 currencies parsed | Core |
| `test_fetch_primary_fails_fallback_success` — primary 500, fallback returns rates | Core |
| `test_fetch_both_fail` — both sources fail → returns None, no crash | Core |
| `test_rate_inversion_jpy` — 1/1.7812 and /100 gives correct per-yen rate | Core |
| `test_backfill_trigger_empty_table` — dim_fx_rates empty → backfill runs | Core |
| `test_backfill_skip_when_data_exists` — dim_fx_rates has data → backfill skips | Core |
| `test_revalue_updates_non_inr_only` — INR bills untouched, USD/EUR/GBP/JPY updated | Core |
| `test_revalue_skips_paid_and_void` — PAID/VOID bills not revalued | Core |
| `test_poll_skip_when_todays_rate_exists` — today's rate in DB → API not called | Core |
| `test_missing_rate_blocks_promotion` — non-INR bill with no rate → validation_flag=REVIEW (D11) | Core |
| `test_promotion_resumes_after_rate_fetched` — blocked bill promotes after rate arrives | Core |
| `test_api_get_rates_returns_all_currencies` — GET /fx/rates returns USD,EUR,GBP,JPY | Core |
| `test_api_fx_payment_bill_not_found` — POST /fx-payment with invalid bill_id → 404 | Core |
| `test_api_fx_payment_already_fully_paid` — payment on PAID bill → 400 | Core |
| `test_api_fx_payment_invalid_currency` — payment on INR bill → 400 | Core |
| `test_conversion_plus_promotion_integration` — end-to-end: stg USD bill → convert → fact_bills has INR total_amount | Core |
| `test_dedup_includes_currency_code` — dedup partition uses currency_code + original_amount (D12) | Core |

### 21.2 Integration Smoke Test

```bash
python scripts/test_fx_live.py

# Expected output:
# [OK] Fetched rates: USD=85.03, EUR=92.34, GBP=107.30, JPY=0.5614
# [OK] Upserted 4 rows into dim_fx_rates
# [OK] Revalued 5 outstanding foreign currency bills
# [OK] Back-calculated payment: effective rate ₹85.75, spread 0.88%
```

---

---

## Appendix: Engineering Review Decisions (2026-06-15)

| # | Decision | Change |
|---|---|---|
| D1 | Reuse existing `currency_code` column | Removed `original_currency` from all ALTER TABLE statements. `currency_code` already exists on stg/fact tables. |
| D2 | Increase rate precision to `numeric(12,6)` | All rate columns changed from `(12,4)` to `(12,6)` for JPY accuracy. |
| D3 | Add rate-limit awareness | Use timeseries endpoint for backfill (1 call, not 90). Respect `Retry-After` header. |
| D4 | Add real FK on `fx_bill_payments.invoice_id` | Added `REFERENCES fact_bills(bill_id)` constraint. |
| D5 | Bulk revaluation per currency | Replaced per-tenant loop with 4 bulk UPDATE statements (one per currency). |
| D6 | Remove CDC trigger | FxRateWorker is time-driven. CDC trigger on dim_fx_rates would be a circular no-op. Removed from task list and files. |
| D7 | Remove `fx_bank_spread` from `fact_bills` | Spread stored per-payment in `fx_bill_payments` only. Weighted average computed at query time. |
| D8 | Validate payment amounts > 0 | Added `CHECK (foreign_amount > 0)` and `CHECK (actual_inr > 0)` constraints. |
| D9 | Add 17 missing test cases | FxRateWorker internals, API endpoints, integration tests, dedup with currency_code. |
| D10 | Add SQL for exposure endpoint | Single GROUP BY query prevents N+1 pattern. |
| D11 | Block promotion when no rate exists | Foreign currency bills without a rate set `validation_flag='REVIEW'` instead of silently promoting as INR. |
| D12 | Fix dedup for foreign bills | Use `COALESCE(original_amount, total_amount)` + `currency_code` in partition key. |

---

*ONSUMM.AI — Multi-Currency Handling Specification*
*Currencies: USD, EUR, GBP, JPY (RBI/FBIL reference rates)*
*Reviewed: 2026-06-15 via /plan-eng-review (12 issues, 0 unresolved)*
*Last updated: June 2026*
