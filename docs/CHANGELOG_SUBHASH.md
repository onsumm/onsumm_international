# Multi-Currency Support

> Commits: `b965997`, `530e562` — June 17, 2026

---

## What Changed

### 1. OCR — Currency Detection (`utils/ocr_engine.py`)
- Added `currency_code` field to the GPT-4o system prompt schema
- Detects currency from symbols or text on the invoice:
  - `$` or `USD` → `USD`
  - `€` or `EUR` → `EUR`
  - `£` or `GBP` → `GBP`
  - `¥` or `JPY` → `JPY`
  - `₹` or `INR` or blank → `INR` (default)

### 2. Ingest — Store Foreign Amount (`backend_ocr/app/ingest.py`)
- `_upsert_bill`: stores `currency_code` and `original_amount` (the raw foreign amount from OCR) in `stg_bills`
- `_upsert_fact_invoice`: stores `currency_code` and `original_amount` in `fact_invoices`
- `total_amount` remains the INR-converted value — pipeline (Rishit) fills this via `dim_fx_rates`

### 3. API — Expose FX Fields (`backend_ocr/app/main.py`)
- `GET /api/bills` list: added `original_amount`, `inr_amount_current` to each bill in response
- `GET /api/bills/{id}` detail: added `original_amount`, `inr_amount_current` to response

### 4. Frontend — Currency Display

#### Bills List (`BillsPage.tsx`)
- For non-INR bills: shows foreign amount tag below the INR total
  - e.g. `₹85,000` on top, `$1,000 USD` in indigo below

#### Bill Detail — Hero Section
- Foreign currency badge shown below the main INR amount
  - e.g. `$1,000 USD`

#### Bill Detail — Financials Section
- Indigo FX strip: `🌐 $1,000 USD → ₹85,000 (converted)`
- Strip only appears for non-INR bills with `original_amount` set
- INR conversion (`→ ₹...`) appears once pipeline fills `total_amount`

### 5. TypeScript Types (`frontend/src/api/invoices.ts`)
- `Bill` interface: added `original_amount: number | null`, `inr_amount_current: number | null`
- `BillDetail` interface: added same two fields

---

## How It Works End-to-End

```
Invoice PDF (e.g. $1,000 USD)
        ↓
OCR (GPT-4o) → currency_code = "USD", total_amount = 1000
        ↓
ingest.py → stg_bills: currency_code="USD", original_amount=1000
          → fact_invoices: currency_code="USD", original_amount=1000
        ↓
Pipeline (Rishit) → fetches RBI rate, converts to INR
                  → fact_invoices: total_amount=85000, inr_amount_current=85000, fx_rate_id=...
        ↓
API → returns original_amount=1000, total_amount=85000, currency_code="USD"
        ↓
UI → shows ₹85,000 with $1,000 USD tag
```

---

## Files Modified

| File | Change |
|------|--------|
| `utils/ocr_engine.py` | Added `currency_code` field to OCR schema and detection rule |
| `backend_ocr/app/ingest.py` | Store `currency_code` + `original_amount` in stg_bills and fact_invoices |
| `backend_ocr/app/main.py` | Return `original_amount` + `inr_amount_current` in bill list and detail API |
| `frontend/src/api/invoices.ts` | Added `original_amount`, `inr_amount_current` to Bill and BillDetail interfaces |
| `frontend/src/pages/BillsPage.tsx` | Currency tag on list row, hero badge, and FX strip in financials |
