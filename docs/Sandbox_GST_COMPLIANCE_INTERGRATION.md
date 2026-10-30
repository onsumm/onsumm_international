# Sandbox GST & Compliance Integration — ONSUMM.AI

**Source:** [Sandbox Developer Portal](https://developer.sandbox.co.in/api-reference/gst/overview)  
**MCP Server:** `sandbox` (`.mcp.json` + `.claude/settings.json`)  
**Schema ref:** `full_database_schema.md`  
**Last Updated:** 2026-05-17

---

## Table of Contents

1. [What Sandbox Provides](#1-what-sandbox-provides)
2. [Authentication](#2-authentication)
3. [Public APIs — GSTIN & Return Verification](#3-public-apis--gstin--return-verification)
4. [Analytics — GSTR-2A / GSTR-2B Reconciliation](#4-analytics--gstr-2a--gstr-2b-reconciliation)
5. [Taxpayer APIs — GSTR Filing & ITC Ledger](#5-taxpayer-apis--gstr-filing--itc-ledger)
6. [E-Invoice (IRN) Generation](#6-e-invoice-irn-generation)
7. [E-Way Bill](#7-e-way-bill)
8. [Integration Map — Existing Tables vs APIs](#8-integration-map--existing-tables-vs-apis)
9. [Minimalistic Schema Changes Required](#9-minimalistic-schema-changes-required)
10. [Pipeline Worker Integration Points](#10-pipeline-worker-integration-points)
11. [Open Doubts — Tricky & Inconclusive Only](#11-open-doubts--tricky--inconclusive-only)

---

## 1. What Sandbox Provides

Sandbox Financial Technologies is an ASP (Application Service Provider) partnered with Quicko GSP.

| API Group | What It Does | Auth Needed |
|---|---|---|
| **Public APIs** | Verify any GSTIN, track supplier GST filing status, PAN → GSTIN lookup | Sandbox JWT only |
| **Analytics** | Async GSTR-2A / GSTR-2B reconciliation jobs | Sandbox JWT only |
| **Taxpayer APIs** | File GSTR-1/3B, pull GSTR-2A, access ITC/cash ledgers | Sandbox JWT + GST portal OTP |
| **E-Invoice** | Generate IRN, cancel, PDF, EWB from invoice | Sandbox JWT + GST portal OTP |
| **E-Way Bill** | Generate/cancel/extend/update EWBs | Sandbox JWT + GST portal OTP |

**Base URLs:**
- Test: `https://test-api.sandbox.co.in`
- Production: `https://api.sandbox.co.in`

---

## 2. Authentication

### 2.1 Sandbox JWT (all API calls)

```
POST /authenticate

Headers:
  x-api-key:    <SANDBOX_API_KEY>      # test_ or live_ prefix
  x-api-secret: <SANDBOX_API_SECRET>

Response:
  data.access_token  — JWT, valid 24h
  
Usage: authorization: <token>   # NO "Bearer" prefix
```

Cache token in memory with 23h TTL. Store keys in `.env`:
```env
SANDBOX_API_KEY=test_...
SANDBOX_API_SECRET=test_...
SANDBOX_BASE_URL=https://test-api.sandbox.co.in
```

### 2.2 GST Portal Session (taxpayer / e-invoice / EWB calls only)

Per-tenant OTP flow — required for any API touching the tenant's own GST portal:

```
Step 1: POST /gst/compliance/taxpayer/auth/otp/generate
  Body: { "gstin": "<dim_tenants.gstn>" }
  → OTP → taxpayer's registered mobile

Step 2: POST /gst/compliance/taxpayer/auth/otp/verify
  Body: { "gstin": "...", "otp": "..." }
  → gst_session_token  (~6h validity)

Step 3: All subsequent calls → Header: gst-session-token: <token>

Step 4: POST /gst/compliance/taxpayer/auth/token/refresh  (before expiry)
```

Store `gst_session_token` per tenant in `int_connections.sync_config_jsonb`:
```json
{
  "sandbox_gst_session_token": "...",
  "sandbox_gst_session_expires_at": "2026-05-17T18:00:00Z"
}
```
Use `int_connections` row with `source = 'GENERIC_REST'` and `tally_company_id = 'sandbox_gst'` as the Sandbox connection anchor until `data_source` enum is extended.

---

## 3. Public APIs — GSTIN & Return Verification

No taxpayer credentials. Safe for background pipeline workers.

### 3.1 Search GSTIN

```
POST /gst/compliance/public/gstin/search
Headers: authorization, x-api-key, x-accept-cache: true
Body: { "gstin": "29AFSPB9500E1ZY" }
```

**Key response fields:**
| Field | Meaning | Maps to ONSUMM |
|---|---|---|
| `sts` | `Active` / `Cancelled` / `Provisional` | `dim_suppliers.gstin_status` (new col) |
| `lgnm` | Legal name from GSTN | Overwrite `dim_suppliers.name` if differs |
| `einvoiceStatus` | `Yes` / `No` | `dim_suppliers.einvoice_eligible` (new col) |
| `ctb` | Constitution type (Pvt Ltd, LLP, etc.) | `dim_suppliers.gstn` cross-check |

**Trigger points in ONSUMM schema:**
- `UploadOcrWorker` → after writing to `stg_bills` → read `stg_bills.gstin_seller` → call API → update `dim_suppliers`
- `ApiSyncWorker` → after writing `stg_invoices` → read `stg_invoices.gstin_buyer` → verify `dim_end_customers.gstin`
- `StagingPromotionWorker` → block promotion if `dim_suppliers.gstin_status = 'Cancelled'` + write `ai_anomaly`

**Existing column to use:** `stg_bills.gstin_seller` (already exists) and `dim_suppliers.gstn` (already exists). No new source column needed. Only new enrichment columns on `dim_suppliers`.

### 3.2 Track GST Return Filing Status

```
POST /gst/compliance/public/gstrs/track?financial_year=2025-26&gstr=GSTR1
Body: { "gstin": "<dim_suppliers.gstn>" }
```

**Response key:**
```json
{
  "EFiledlist": [{
    "rtntype": "GSTR1",
    "ret_prd": "042026",      // MMYYYY = April 2026
    "status": "Filed",        // Filed | Not Filed | Pending
    "dof": "11-05-2026"
  }]
}
```

**ONSUMM use:**
- For each `stg_bill`, derive `ret_prd` from `stg_bills.bill_date` (fiscal month → MMYYYY)
- If supplier's GSTR-1 for that period `status ≠ Filed` → write to `ai_anomalies`:
  ```
  anomaly_type: 'SUPPLIER_GST_NOT_FILED'
  entity_type:  BILL
  entity_id:    stg_bill_id
  severity:     HIGH
  features_jsonb: { gstin, ret_prd, bill_number }
  ```
- `ai_anomalies.anomaly_type` is `text` — no enum migration needed to add new types.

### 3.3 Search GSTIN by PAN

```
POST /gst/compliance/public/pan/search?state_code=29
Body: { "pan": "<dim_end_customers.pan>" }
```

**ONSUMM use:** Vendor onboarding — `dim_end_customers.pan` already exists. Feed PAN → discover all GSTINs → populate `dim_end_customers.gstin`.

**Rate limit:** 120 req/window. Use `x-accept-cache: true`.

---

## 4. Analytics — GSTR-2A / GSTR-2B Reconciliation

Most impactful integration. Matches `fact_bills` (our internal purchase ledger) against what suppliers filed with GSTN.

### 4.1 Job Flow (4 steps)

**Step 1 — Submit**
```
POST /gst/analytics/gstr-2a-reconciliation
Body:
{
  "@entity": "in.co.sandbox.gst.analytics.gstr-2a_reconciliation.request",
  "gstin": "<dim_tenants.gstn>",
  "year": 2026,
  "month": 4,
  "reconciliation_criteria": "strict"   // strict | moderate | flexible
}

Response:
{
  "job_id": "uuid",
  "gstr_2a_url": "https://s3.amazonaws.com/...presigned...",
  "purchase_ledger_url": "https://s3.amazonaws.com/...presigned..."
}
```

**Step 2 — Upload files to presigned S3 URLs**
```python
# Purchase ledger — built from fact_bills + stg_bills for the month
requests.put(purchase_ledger_url, json=[{
    "invoice_no":      row["bill_number"],          # stg_bills.bill_number
    "invoice_date":    row["bill_date"].isoformat(), # stg_bills.bill_date
    "supplier_gstin":  row["gstin_seller"],          # stg_bills.gstin_seller
    "taxable_amount":  row["subtotal"],              # stg_bills.subtotal
    "igst":            0,
    "cgst":            row["tax_amount"] / 2,        # approximation
    "sgst":            row["tax_amount"] / 2,
    "total_amount":    row["total_amount"]           # stg_bills.total_amount
}])

# GSTR-2A data — fetched from taxpayer APIs (section 5.2)
requests.put(gstr_2a_url, json=gstr_2a_data)
```

**Step 3 — Poll**
```
GET /gst/analytics/gstr-2a-reconciliation?job_id=<uuid>
Response: { status: "created|queued|in_progress|succeeded|failed" }
```

**Step 4 — Download report from `reconciliation_report_url`**

Report identifies:
- `matched` — ITC safe to claim
- `mismatched` — amount/date mismatch → `ai_anomaly: GST_ITC_MISMATCH`
- `in_ledger_not_in_2a` — supplier unfiled → `ai_anomaly: SUPPLIER_NOT_FILED`
- `in_2a_not_in_ledger` — missing from our system → `ai_anomaly: UNRECORDED_PURCHASE`

### 4.2 GSTR-2B Reconciliation

Identical flow. Endpoint: `/gst/analytics/gstr-2b-reconciliation`. Use 2B for ITC claim decisions (static monthly snapshot, more reliable than 2A).

### 4.3 New Table: `fact_gst_reconciliation`

```sql
CREATE TABLE fact_gst_reconciliation (
  recon_id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tenant_id         UUID NOT NULL REFERENCES dim_tenants(tenant_id),
  gstin             TEXT NOT NULL,                    -- dim_tenants.gstn
  fiscal_year       INTEGER NOT NULL,
  fiscal_month      INTEGER NOT NULL,                 -- 1=April (ONSUMM convention)
  sandbox_job_id    UUID NOT NULL,
  recon_type        TEXT NOT NULL,                    -- 'GSTR2A' | 'GSTR2B'
  criteria          TEXT NOT NULL,                    -- strict|moderate|flexible
  status            TEXT NOT NULL,                    -- succeeded|failed
  matched_count     INTEGER,
  mismatched_count  INTEGER,
  missing_in_2a     INTEGER,
  missing_in_ledger INTEGER,
  report_url        TEXT,
  created_at        TIMESTAMPTZ DEFAULT NOW(),
  updated_at        TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. Taxpayer APIs — GSTR Filing & ITC Ledger

Requires per-tenant GST portal OTP session (section 2.2). Relevant for tenants who want ONSUMM to act as their filing interface.

| Return | Operations |
|---|---|
| GSTR-1 | File B2B invoices from `fact_invoices` (AR side) |
| GSTR-2A | Pull to use as input for reconciliation (section 4) |
| GSTR-2B | Pull auto-drafted ITC statement |
| GSTR-3B | Auto-calculate liability, file return |
| GSTR-9 | Annual return |
| Ledgers | Cash ledger → `fact_cashflow_daily`; ITC ledger → new display only |

**Pull GSTR-2A for reconciliation:**
```
GET /gst/compliance/taxpayer/gstr-2a/b2b?ret_prd=042026
Header: gst-session-token: <session_token>
→ Returns supplier-filed invoice data for the month
```

---

## 6. E-Invoice (IRN) Generation

Mandatory for businesses with turnover > ₹5 Cr. `dim_tenants.gstn` tells us the tenant's GSTIN; `dim_suppliers.einvoice_eligible` (new) tells us if the seller is registered.

### 6.1 Endpoint

```
POST /gst/compliance/e-invoice/generate
Headers: authorization, x-api-key, gst-session-token

Body (key fields mapped to ONSUMM columns):
{
  "DocDtls": {
    "No":  <stg_invoices.invoice_number>,
    "Dt":  <stg_invoices.invoice_date>          // DD/MM/YYYY
  },
  "SellerDtls": {
    "Gstin": <stg_invoices.gstin_seller>,        // already exists
    "LglNm": <dim_tenants.company_name>
  },
  "BuyerDtls": {
    "Gstin": <stg_invoices.gstin_buyer>,         // already exists
    "Pos":   <stg_invoices.place_of_supply>      // already exists
  },
  "ItemList": // from stg_invoice_line_items:
    [{
      "HsnCd":    <stg_invoice_line_items.hsn_code>,
      "AssAmt":   <stg_invoice_line_items.unit_price * quantity>,
      "GstRt":    <stg_invoice_line_items.tax_rate_percent>,
      "IgstAmt":  // if inter-state (buyer.state ≠ seller.state)
      "CgstAmt":  // if intra-state
      "SgstAmt":  // if intra-state
    }]
}
```

**Response fields to store:**
```
Irn          → stg_invoices.irn          (new col)
AckNo        → stg_invoices.irn_ack_no   (new col)
AckDt        → stg_invoices.irn_ack_dt   (new col)
SignedQRCode → stg_invoices.signed_qr_code (new col)
EwbNo        → stg_invoices.ewb_no        (new col, if EWB generated)
```

**Trigger:** `ApprovalWorker` — after `bill_approval_chain` reaches approved state for invoices where `dim_end_customers.einvoice_eligible = true`.

### 6.2 Other E-Invoice Endpoints

| Operation | Endpoint | When |
|---|---|---|
| Cancel IRN | `POST /gst/compliance/e-invoice/cancel` | Approval reversal within 24h |
| Generate PDF | `POST /gst/compliance/e-invoice/pdf` | After IRN generation → store in `storage_path` |
| Get by IRN | `GET /gst/compliance/e-invoice?irn=...` | Idempotency check before re-generating |

---

## 7. E-Way Bill

Mandatory for goods movement > ₹50,000. Service invoices (most ONSUMM tenants) don't require EWBs.

| Operation | Endpoint |
|---|---|
| Generate | `POST /gst/compliance/ewb/generate` |
| Cancel | `POST /gst/compliance/ewb/cancel` |
| Extend validity | `POST /gst/compliance/ewb/extend` |
| HSN lookup | `GET /gst/compliance/ewb/hsn` |

**When relevant for ONSUMM:** Only for tenants dealing in goods. Check `stg_bill_line_items.hsn_code` — HSN codes starting with chapters 1–97 are goods; SAC codes (99xxxx) are services. Can use the HSN endpoint to classify.

---

## 8. Integration Map — Existing Tables vs APIs

| Existing Column | Table | Sandbox API Use |
|---|---|---|
| `gstn` | `dim_suppliers` | Input to Search GSTIN + Track Returns |
| `gstn` | `dim_end_customers` | Input to Search GSTIN (customer side) |
| `gstn` | `dim_tenants` | Input to taxpayer auth OTP + reconciliation job |
| `pan` | `dim_end_customers` | Input to PAN search API |
| `gstin_seller` | `stg_bills` | Validated via Search GSTIN |
| `gstin_seller` | `stg_invoices` | Seller GSTIN for IRN generation |
| `gstin_buyer` | `stg_invoices` | Buyer GSTIN for IRN generation |
| `place_of_supply` | `stg_invoices` | Determines IGST vs CGST+SGST in IRN |
| `reverse_charge` | `stg_invoices` | Mapped to `TranDtls.RegRev` in IRN body |
| `hsn_code` | `stg_bill_line_items` | Used in purchase ledger for reconciliation |
| `hsn_code` | `stg_invoice_line_items` | IRN `ItemList.HsnCd` |
| `tax_rate_percent` | `stg_invoice_line_items` | IRN `ItemList.GstRt` |
| `anomaly_type` (text) | `ai_anomalies` | New GST anomaly types — no migration needed |
| `is_reconciled` | `payment_transactions` | Mark transactions matched in GSTR-2A recon |
| `sync_config_jsonb` | `int_connections` | Store Sandbox GST session token per tenant |
| `validation_flag` | `stg_bills` / `stg_invoices` | Set `MISMATCH` if GSTIN inactive |

---

## 9. Minimalistic Schema Changes Required

Only changes that cannot be avoided. Everything else uses existing columns.

### 9.1 `dim_suppliers` — 3 new columns

```sql
ALTER TABLE dim_suppliers
  ADD COLUMN gstin_status         TEXT,          -- 'Active'|'Cancelled'|'Provisional'
  ADD COLUMN einvoice_eligible    BOOLEAN DEFAULT FALSE,
  ADD COLUMN gstin_verified_at    TIMESTAMPTZ;   -- last Sandbox check timestamp
```

These carry GSTN-authoritative data separate from the editable `name` field.

### 9.2 `dim_end_customers` — 2 new columns

```sql
ALTER TABLE dim_end_customers
  ADD COLUMN einvoice_eligible    BOOLEAN DEFAULT FALSE,
  ADD COLUMN gstin_verified_at    TIMESTAMPTZ;
```

### 9.3 `stg_invoices` — IRN columns (only when e-invoice feature is built)

```sql
ALTER TABLE stg_invoices
  ADD COLUMN irn              TEXT,
  ADD COLUMN irn_ack_no       BIGINT,
  ADD COLUMN irn_ack_dt       TIMESTAMPTZ,
  ADD COLUMN signed_qr_code   TEXT,
  ADD COLUMN ewb_no           TEXT;
```

IRN stored on `stg_invoices` (not `fact_invoices`) because IRN generation happens during/after approval, before or alongside promotion. `fact_invoices` picks these up at promotion time via the existing `stg_invoice_id` join.

### 9.4 New table: `fact_gst_reconciliation`

See DDL in section 4.3.

### 9.5 `int_connector_limits` — add Sandbox rate limit row

```sql
INSERT INTO int_connector_limits (connector, per_minute_limit, per_day_limit, concurrency_limit)
VALUES ('sandbox_gst', 2, 120, 1);  -- 120/day on PAN search; GSTIN search unclear
```

### 9.6 No enum changes needed

- `ai_anomalies.anomaly_type` is already `text` → add new GST anomaly strings freely
- `data_source` enum does NOT need `SANDBOX` — use `GENERIC_REST` for `int_connections.source`
- `validation_flag` enum has `MISMATCH` — use it when GSTIN status = Cancelled

---

## 10. Pipeline Worker Integration Points

### Phase 1 — GSTIN validation (low effort, immediate value)

**`UploadOcrWorker`** — after writing `stg_bills`:
```python
if stg_bill.gstin_seller:
    result = await sandbox.search_gstin(stg_bill.gstin_seller)
    await conn.execute("""
        UPDATE dim_suppliers
        SET gstin_status = $1, einvoice_eligible = $2, gstin_verified_at = NOW()
        WHERE supplier_id = $3
    """, result["sts"], result["einvoiceStatus"] == "Yes", stg_bill.supplier_id)

    if result["sts"] == "Cancelled":
        # validation_flag already exists on stg_bills
        await conn.execute("""
            UPDATE stg_bills SET validation_flag = 'MISMATCH' WHERE stg_bill_id = $1
        """, stg_bill.stg_bill_id)
        await write_anomaly(conn, "SUPPLIER_GSTIN_INACTIVE", "BILL",
                            stg_bill.stg_bill_id, "HIGH",
                            {"gstin": stg_bill.gstin_seller})
```

**`StagingPromotionWorker`** — add compliance gate before promotion:
```python
# Don't promote bills with MISMATCH validation_flag (already filtered via OK gate)
# Existing: WHERE validation_flag = 'OK' AND sync_status = 'PENDING'
# No change needed — MISMATCH blocks promotion automatically
```

### Phase 2 — Supplier filing check (medium effort)

**New `ComplianceWorker`** or extend `DeduplicationWorker` — monthly:
```python
# For each distinct gstin_seller in stg_bills for the month
# call Track GST Returns, write SUPPLIER_GST_NOT_FILED anomaly if not filed
```

### Phase 3 — GSTR-2A/2B reconciliation (high effort)

**New `ReconciliationWorker`** — month-end trigger:
- Build purchase ledger from `fact_bills` JOIN `stg_bills` (for `gstin_seller`)
- Submit job, poll until `succeeded`
- Parse report → write to `fact_gst_reconciliation` + `ai_anomalies`
- Mark `payment_transactions.is_reconciled = true` for matched transactions

### Phase 4 — E-Invoice (high effort, deferred)

**`ApprovalWorker`** enhancement — after final approval:
- Check `dim_end_customers.einvoice_eligible` (new) OR `dim_tenants.gstn` turnover threshold
- Generate IRN → store on `stg_invoices` → promote to `fact_invoices`

---

## 11. Open Doubts — Tricky & Inconclusive Only

These are decisions that block implementation. Other questions (pricing, webhooks) are confirmable via Sandbox support without affecting architecture.

---

**Q1 — `dim_tenants.gstn` is unique-constrained: one GSTIN per tenant**

The schema enforces `UNIQUE (gstn)` on `dim_tenants`. Multi-state companies have multiple GSTINs (e.g., 29XXXX for Karnataka, 27XXXX for Maharashtra). Taxpayer APIs and reconciliation jobs run per-GSTIN. If a tenant has 3 GSTINs, we need 3 OTP sessions and 3 reconciliation jobs, but the current schema supports only one GSTIN per tenant.

**Decision needed:** Do we extend `dim_tenants` with a `gstin_jsonb TEXT[]`, add a new `dim_tenant_gstins` table, or restrict ONSUMM to single-GSTIN tenants for this release?

---

**Q2 — `stg_bills.gstin_seller` vs `dim_suppliers.gstn`: which governs compliance?**

`stg_bills.gstin_seller` is the GSTIN printed on the specific bill. `dim_suppliers.gstn` is the master GSTIN for the supplier. A supplier might invoice using a different state GSTIN than their master record. Sandbox GSTIN search on which field — the bill's or the supplier master's?

**Decision needed:** For compliance check, always use `stg_bills.gstin_seller` (bill-level, correct per transaction) or `dim_suppliers.gstn` (master, cached, fewer API calls)?

---

**Q3 — IGST vs CGST+SGST: `place_of_supply` is `text` but needs state code**

`stg_invoices.place_of_supply` exists but its current format is unspecified — it could be `"Karnataka"`, `"29"`, or `"29-Karnataka"`. IRN generation requires the numeric state code (`"29"`) in `BuyerDtls.Pos`. The seller state code is derivable from the first 2 chars of `stg_invoices.gstin_seller`. IGST applies if seller state ≠ buyer state.

**Decision needed:** Standardize `place_of_supply` to 2-digit state code format — requires a migration + normalization of existing data. Or derive it from `gstin_buyer` first 2 chars?

---

**Q4 — IRN on `stg_invoices` vs `fact_invoices`: promotion timing conflict**

If IRN is generated post-approval but pre-promotion, it should live on `stg_invoices` (proposed in section 9.3). But the approval worker currently reads `bill_approval_chain` which references `stg_bills`, not `stg_invoices` directly. If we generate IRN during the approval subtask `_audit_bill_approval_chain`, we need to confirm the approval chain entity type maps to invoices (not just bills).

**Decision needed:** Does `bill_approval_chain` cover AR invoices or only AP bills? If only bills, a parallel `invoice_approval_chain` table is needed before IRN integration can proceed.

---

**Q5 — Purchase ledger CGST/SGST split is not stored per tax type**

`stg_bills.tax_amount` is a single total. `stg_bill_line_items.tax_rate_percent` exists but there's no split into `cgst_amount`, `sgst_amount`, `igst_amount` per line. The reconciliation upload (section 4.1) and IRN generation both need this split.

`dim_tax_rates` has `tax_type` enum (`CGST`, `SGST`, `IGST`) but `stg_bill_line_items` has only `tax_amount` (total), no FK to `dim_tax_rates` (unlike `stg_invoice_line_items` which has `tax_rate_id`).

**Decision needed:** Do we add CGST/SGST columns to `stg_bill_line_items`, or derive the split from `place_of_supply` at runtime (assuming 50/50 for intra-state)?

---

**Q6 — `gstn` column type is `character` (char) with no explicit length**

`dim_suppliers.gstn`, `dim_tenants.gstn`, `dim_customers.gstn` are all typed `character` in the schema. PostgreSQL `character` without length is `char(1)` by default — but GSTINs are 15 characters. The schema dump likely means `char(15)` but shows as just `character`. Validation logic like `length(gstn) = 15` or regex `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$` will fail silently if the column is actually `char(1)`.

**Decision needed:** Confirm the actual column definition via `\d dim_suppliers` in psql. If it's `char` without length (=`char(1)`), this is a data integrity bug independent of Sandbox integration.

---

*MCP server `sandbox` active — use `search_sandbox` or `query_docs_filesystem_sandbox` tools to query live Sandbox docs in future sessions.*  
*Schema ref: `full_database_schema.md` — verify column definitions via `\d <table>` before running migrations.*
