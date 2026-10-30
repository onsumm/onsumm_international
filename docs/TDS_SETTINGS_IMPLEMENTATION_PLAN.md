# TDS Dynamic Settings — Implementation Plan

> ONSUMM.AI | FY 2026-27 | Finalized 27 May 2026

---

## 1. Overview

Add a fully dynamic, admin-configurable TDS (Tax Deducted at Source) engine to ONSUMM.AI. Rules live in the database, not in code. Admins manage everything from the Settings page — sections, rates, thresholds, vendor mappings, and overrides — without needing a developer.

**Scope:** TDS on Accounts Payable (bills/invoices) only. TCS is out of scope for now.

---

## 2. Architecture — 3 Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    SETTINGS PAGE (UI)                        │
│  ┌──────────┐ ┌────────────┐ ┌───────────┐ ┌─────────────┐ │
│  │ Sections │ │ Categories │ │ Mappings  │ │ Preferences │ │
│  └─────┬────┘ └─────┬──────┘ └─────┬─────┘ └──────┬──────┘ │
│        │            │              │               │        │
└────────┼────────────┼──────────────┼───────────────┼────────┘
         ▼            ▼              ▼               ▼
┌─────────────────────────────────────────────────────────────┐
│                    DATABASE (Rules as Data)                  │
│                                                             │
│  dim_tds_sections ──── dim_vendor_categories                │
│         │                      │                            │
│         └──── dim_tds_mapping_rules ────┘                   │
│                      │                                      │
│         dim_vendor_tds_overrides (Sec 197)                  │
│                                                             │
│         settings_jsonb (global prefs)                       │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                TDS CALCULATION ENGINE                        │
│              (utils/tds_engine.py)                           │
│                                                             │
│  1. Resolve applicable section (mapping rules by priority)  │
│  2. Check thresholds (single + aggregate)                   │
│  3. Determine rate (default / override / no-PAN penalty)    │
│  4. Compute TDS amount on correct base                      │
│  5. Return TdsResult object                                 │
└─────────────────────────────────────────────────────────────┘
```

### Layer 1 — Section Master (WHAT rules exist)
Each row is one TDS section with its rate, threshold logic, and validity. The admin adds/edits/disables sections here. Zero hardcoded section numbers in the codebase.

### Layer 2 — Vendor Mapping Rules (WHO they apply to)
Priority-ordered rules that link vendor categories to sections. The engine walks down the list and picks the first match. Includes an optional `rate_override` for cases like 194C where the rate differs by entity type.

### Layer 3 — Global Preferences (HOW to calculate)
Tenant-level switches: when to deduct (booking vs payment), base amount logic (GST-exclusive per CBDT 23/2017), auto-apply toggle.

---

## 3. Data Model

### 3.1 `dim_vendor_categories`

Admin-defined categories for classifying vendors **for TDS section mapping purposes only**.

> **Important — Two separate category systems:**
> - `dim_suppliers.category` (existing `varchar(100)` text column) = **COA / expense category** used by `pipelines/category_enrichment.py` to backfill expense heads for reporting (values: "Software / SaaS", "Rent", "Food & Beverages", "Travel & Expenses", "Utilities", "Reimbursement"). This column is **not changed**.
> - `dim_suppliers.vendor_category_id` (new uuid FK) = **TDS vendor type** used by the TDS engine to resolve which Income Tax section applies (values: "Professional Services", "Contractor — Individual/HUF", etc.).
>
> These are orthogonal. A vendor can have category = "Software / SaaS" (for COA expense reports) AND vendor_category_id → "Technical Services" (for TDS 194JA deduction) simultaneously.

| Column | Type | Notes |
|--------|------|-------|
| `category_id` | uuid PK | `gen_random_uuid()` |
| `tenant_id` | uuid FK → dim_tenants | |
| `name` | text NOT NULL | e.g. "Professional Services", "Contractor — Individual/HUF" |
| `description` | text | Optional help text |
| `display_order` | integer DEFAULT 0 | Sort in UI |
| `is_active` | boolean DEFAULT true | Soft delete |
| `created_at` | timestamptz | |

**Unique:** `(tenant_id, name)`

**Seed data (TDS nature-of-payment categories per IT Act):**
- Professional Services
- Technical Services
- Contractor — Individual/HUF
- Contractor — Company/Firm
- Director Fees
- Rent — Building/Land
- Goods Purchase (194Q)
- Interest — General
- Interest — Non-Senior Citizen
- Interest — Senior Citizen

**COA expense categories (already in `dim_suppliers.category` — NOT seeded here):**
These are managed by `pipelines/category_enrichment.py` and stored as plain text: "Software / SaaS", "Rent", "Food & Beverages", "Travel & Expenses", "Utilities", "Reimbursement".

---

### 3.2 `dim_tds_sections`

The rules themselves. Each row is one TDS section.

| Column | Type | Notes |
|--------|------|-------|
| `tds_section_id` | uuid PK | |
| `tenant_id` | uuid FK | |
| `return_code` | text NOT NULL | New IT Act code: `1022`, `1027`, etc. |
| `old_section` | text | Old Act reference: `194A`, `194JB`, etc. |
| `new_section` | text | New Act reference: `393(1) [Table: ...]` |
| `nature_of_payment` | text NOT NULL | Human-readable description |
| `default_rate` | numeric(6,3) NOT NULL | e.g. `10.000` (%) |
| `pan_missing_rate` | numeric(6,3) DEFAULT 20.0 | Section 206AA penalty |
| `threshold_type` | text NOT NULL | Enum: see below |
| `threshold_amount` | numeric(14,2) | Primary threshold |
| `threshold_amount_secondary` | numeric(14,2) | Secondary threshold (e.g. 194C aggregate) |
| `base_calculation` | text DEFAULT 'GST_EXCLUSIVE' | `GST_EXCLUSIVE` / `TOTAL_AMOUNT` |
| `effective_from` | date NOT NULL | Start of validity |
| `effective_to` | date | NULL = no end date |
| `is_active` | boolean DEFAULT true | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

**`threshold_type` enum values:**

| Value | Meaning | Example |
|-------|---------|---------|
| `NONE` | No threshold, deduct from first rupee | Director fees (if no threshold) |
| `PER_TXN` | Exempt if single txn below amount | — |
| `AGGREGATE_FY` | Exempt if FY cumulative below amount | 194JB: ₹50,000/FY |
| `PER_MONTH` | Exempt if monthly total below amount | 194I(b): ₹50,000/month |
| `EXCESS_OF` | Deduct only on amount exceeding threshold | 194Q: 0.1% on amount > ₹50L |
| `DUAL` | Both per-txn AND aggregate thresholds apply | 194C: ₹30K/txn AND ₹1L/FY |

When `threshold_type = DUAL`:
- `threshold_amount` = per-transaction limit (₹30,000 for 194C)
- `threshold_amount_secondary` = aggregate FY limit (₹1,00,000 for 194C)
- Engine logic: **Deduct if** single txn ≥ threshold_amount **OR** FY cumulative ≥ threshold_amount_secondary

**Seed data (from client Excel — 9 sections):**

| # | Return Code | Old Section | Nature | Rate | Threshold Type | Primary | Secondary |
|---|-------------|-------------|--------|------|----------------|---------|-----------|
| 1 | 1022 | 194A | Interest (general) | 10% | AGGREGATE_FY | ₹10,000 | — |
| 2 | 1021 | 194A | Interest (non-senior) | 10% | AGGREGATE_FY | ₹50,000 | — |
| 3 | 1020 | 194A | Interest (senior citizen) | 10% | AGGREGATE_FY | ₹1,00,000 | — |
| 4 | 1027 | 194JB | Professional Services | 10% | AGGREGATE_FY | ₹50,000 | — |
| 5 | 1026 | 194JA | Technical Services | 2% | AGGREGATE_FY | ₹50,000 | — |
| 6 | 1028 | 194JB | Director Fees | 10% | AGGREGATE_FY | ₹50,000 | — |
| 7 | 1023 | 194C | Contractor | 1%* | DUAL | ₹30,000 | ₹1,00,000 |
| 8 | 1009 | 194I(b) | Rent (building/land) | 10% | PER_MONTH | ₹50,000 | — |
| 9 | 1031 | 194Q | Goods Purchase | 0.1% | EXCESS_OF | ₹50,00,000 | — |

*194C default_rate = 1%. The 2% rate for companies is handled via mapping rule `rate_override`.

---

### 3.3 `dim_tds_mapping_rules`

Priority-ordered rules linking vendor categories to TDS sections.

| Column | Type | Notes |
|--------|------|-------|
| `rule_id` | uuid PK | |
| `tenant_id` | uuid FK | |
| `tds_section_id` | uuid FK → dim_tds_sections | Which section to apply |
| `category_id` | uuid FK → dim_vendor_categories | NULL = catch-all |
| `priority` | integer NOT NULL | Lower = checked first |
| `rate_override` | numeric(6,3) | Overrides section's default_rate |
| `description` | text | Admin note |
| `is_active` | boolean DEFAULT true | |
| `created_at` | timestamptz | |

**How the engine uses this:**
```
FOR each mapping rule (ordered by priority ASC):
    IF vendor.category matches rule.category_id:
        section = rule.tds_section_id
        rate = rule.rate_override ?? section.default_rate
        BREAK  ← first match wins
```

**Seed mappings:**

| Priority | Category | Section | Rate Override | Why |
|----------|----------|---------|---------------|-----|
| 10 | Professional Services | 1027/194JB | — | 10% default |
| 20 | Technical Services | 1026/194JA | — | 2% default |
| 30 | Director Fees | 1028/194JB | — | 10% default |
| 40 | Contractor — Individual/HUF | 1023/194C | 1% | Individual rate |
| 50 | Contractor — Company/Firm | 1023/194C | 2% | Company rate |
| 60 | Rent — Building/Land | 1009/194I(b) | — | 10% default |
| 70 | Goods Purchase (194Q) | 1031/194Q | — | 0.1% default |
| 80 | Interest — General | 1022/194A | — | 10% default |
| 90 | Interest — Non-Senior | 1021/194A | — | 10% default |
| 100 | Interest — Senior Citizen | 1020/194A | — | 10% default |

---

### 3.4 `dim_vendor_tds_overrides`

Per-vendor exceptions. Covers Section 197 lower/nil deduction certificates.

| Column | Type | Notes |
|--------|------|-------|
| `override_id` | uuid PK | |
| `tenant_id` | uuid FK | |
| `supplier_id` | uuid FK → dim_suppliers | Specific vendor |
| `tds_section_id` | uuid FK → dim_tds_sections | Which section this overrides |
| `override_rate` | numeric(6,3) NOT NULL | Certificate rate (can be 0) |
| `certificate_no` | text | Lower deduction certificate number |
| `valid_from` | date NOT NULL | |
| `valid_to` | date NOT NULL | |
| `is_active` | boolean DEFAULT true | |
| `created_at` | timestamptz | |

**Engine priority:** Vendor override > Mapping rule > Section default

---

### 3.5 Schema Changes to Existing Tables

**`dim_suppliers` — add column:**
```sql
ALTER TABLE dim_suppliers
    ADD COLUMN IF NOT EXISTS vendor_category_id uuid REFERENCES dim_vendor_categories(category_id);
```
This links each supplier to a TDS vendor category so the TDS engine knows which mapping rules apply.
**Note:** The existing `dim_suppliers.category varchar(100)` column (COA expense category, managed by `category_enrichment.py`) is **untouched**. `vendor_category_id` is a separate FK for TDS purposes only.

**`stg_bills` / `fact_bills` — add columns:**
```sql
ALTER TABLE stg_bills
    ADD COLUMN IF NOT EXISTS tds_section_id    uuid,
    ADD COLUMN IF NOT EXISTS tds_rate          numeric(6,3),
    ADD COLUMN IF NOT EXISTS tds_amount        numeric(14,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tds_base_amount   numeric(14,2),
    ADD COLUMN IF NOT EXISTS tds_status        text DEFAULT 'PENDING';
    -- tds_status: PENDING / CALCULATED / DEDUCTED / NA

ALTER TABLE fact_bills
    ADD COLUMN IF NOT EXISTS tds_section_id    uuid,
    ADD COLUMN IF NOT EXISTS tds_rate          numeric(6,3),
    ADD COLUMN IF NOT EXISTS tds_amount        numeric(14,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tds_base_amount   numeric(14,2),
    ADD COLUMN IF NOT EXISTS tds_status        text DEFAULT 'PENDING';
```

**`stg_invoices` — add columns (same pattern):**
```sql
ALTER TABLE stg_invoices
    ADD COLUMN IF NOT EXISTS tds_section_id    uuid,
    ADD COLUMN IF NOT EXISTS tds_rate          numeric(6,3),
    ADD COLUMN IF NOT EXISTS tds_amount        numeric(14,2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tds_base_amount   numeric(14,2),
    ADD COLUMN IF NOT EXISTS tds_status        text DEFAULT 'PENDING';
```

---

### 3.6 Global Preferences (in `dim_tenants.settings_jsonb`)

```json
{
  "tds": {
    "deduct_on": "BOOKING",
    "base_default": "GST_EXCLUSIVE",
    "auto_apply": true,
    "fy_start_month": 4,
    "pan_missing_rate": 20.0
  }
}
```

| Key | Values | Default | Meaning |
|-----|--------|---------|---------|
| `deduct_on` | `BOOKING` / `PAYMENT` / `EARLIER` | `BOOKING` | When TDS is computed |
| `base_default` | `GST_EXCLUSIVE` / `TOTAL_AMOUNT` | `GST_EXCLUSIVE` | CBDT Circular 23/2017 compliant |
| `auto_apply` | boolean | `true` | Auto-calculate TDS on upload or require manual trigger |
| `fy_start_month` | 1–12 | `4` | April for Indian FY |
| `pan_missing_rate` | numeric | `20.0` | Section 206AA default |

---

## 4. TDS Calculation Engine (`utils/tds_engine.py`)

A pure-logic module with zero hardcoded sections. All rules come from the database.

### 4.1 Input

```python
@dataclass
class TdsInput:
    tenant_id: str
    supplier_id: str
    invoice_date: date
    taxable_amount: Decimal      # Amount before GST
    total_amount: Decimal         # Amount including GST
    gst_amount: Decimal           # GST component (if shown separately)
    is_gst_shown_separately: bool # For CBDT 23/2017 logic
```

### 4.2 Output

```python
@dataclass
class TdsResult:
    applicable: bool              # Whether TDS applies
    section_id: str | None        # dim_tds_sections.tds_section_id
    return_code: str | None       # e.g. "1027"
    old_section: str | None       # e.g. "194JB"
    rate: Decimal | None          # Effective rate applied
    base_amount: Decimal | None   # Bill's own taxable/base amount TDS was calculated on
    tds_amount: Decimal | None    # Final TDS (ROUND(base_amount × rate / 100, 0))
    reason: str                   # Human-readable explanation
    rate_source: str              # "DEFAULT" / "MAPPING_OVERRIDE" / "VENDOR_OVERRIDE" / "NO_PAN_PENALTY"
    threshold_status: str         # "BELOW_THRESHOLD" / "CROSSING" / "ABOVE_THRESHOLD" / "EXCESS_OF" / "NA"
```

### 4.3 Algorithm

```
FUNCTION calculate_tds(input: TdsInput) → TdsResult:

    1. LOAD tenant preferences from settings_jsonb
    
    2. LOAD vendor details:
       - category_id from dim_suppliers
       - PAN availability
       - Any active vendor override (dim_vendor_tds_overrides)
    
    3. RESOLVE SECTION:
       a. If vendor has an active override → use that section + override rate
       b. Else, walk dim_tds_mapping_rules (ORDER BY priority ASC):
          - First rule where category_id matches vendor's category → use it
          - rate = rule.rate_override ?? section.default_rate
       c. If no match → return TdsResult(applicable=False, reason="No mapping rule")
    
    4. CHECK VALIDITY:
       - section.effective_from ≤ invoice_date ≤ section.effective_to
       - If outside validity → return not applicable
    
    5. DETERMINE BASE AMOUNT:
       - If section.base_calculation = 'GST_EXCLUSIVE' AND gst shown separately:
           base = taxable_amount
       - Else:
           base = total_amount
    
    6. CHECK THRESHOLDS:
       ┌──────────────────────────────────────────────────────────────┐
       │  THRESHOLD RULE                                              │
       │                                                              │
       │  Prior bills (cumulative < threshold) → EXEMPT, no TDS      │
       │  Crossing bill (tips cumulative ≥ threshold) → TDS on       │
       │    that bill's own base amount only (no catch-up on prior)  │
       │  All subsequent bills → TDS on each bill's full base amount │
       └──────────────────────────────────────────────────────────────┘

       SWITCH section.threshold_type:

         NONE:
           → tds_base = base; proceed

         PER_TXN:
           → if base < threshold_amount → exempt
           → else: tds_base = base

         AGGREGATE_FY:
           cumulative = SUM(total_amount) FROM fact_bills
                        WHERE supplier_id = input.supplier_id
                          AND tds_section_id = section.tds_section_id
                          AND fiscal_year = fy_of(input.invoice_date)
           ← sum ALL bills for this vendor+section+FY (exempt or not)
              to track the true running total paid
           IF cumulative >= threshold_amount:
             → threshold already crossed in a prior bill; tds_base = base
           ELIF cumulative + base >= threshold_amount:
             → CROSSING BILL: tds_base = base  (TDS on this bill's amount only)
           ELSE:
             → exempt (running total still under threshold)

         PER_MONTH:
           monthly = SUM(total_amount) FROM fact_bills
                     WHERE supplier_id = input.supplier_id
                       AND tds_section_id = section.tds_section_id
                       AND month = month_of(input.invoice_date)
           IF monthly >= threshold_amount:
             → already crossed this month; tds_base = base
           ELIF monthly + base >= threshold_amount:
             → CROSSING BILL: tds_base = base
           ELSE:
             → exempt

         EXCESS_OF:
           cumulative = SUM(total_amount) FROM fact_bills (same vendor+section+FY)
           taxable_portion = max(0, cumulative + base - threshold_amount)
                           - max(0, cumulative - threshold_amount)
           IF taxable_portion <= 0 → exempt
           ELSE: tds_base = taxable_portion

         DUAL:
           per_txn_fires = (base >= threshold_amount)
           cumulative    = SUM(total_amount) as in AGGREGATE_FY above
           agg_crossed   = (cumulative >= threshold_amount_secondary)
           agg_crossing  = (cumulative + base >= threshold_amount_secondary)

           IF per_txn_fires OR agg_crossed OR agg_crossing:
             → tds_base = base  (TDS on this bill's amount)
           ELSE:
             → exempt

    7. DETERMINE RATE:
       a. If vendor has no PAN → rate = section.pan_missing_rate (206AA)
       b. If vendor override is active → rate = override.override_rate
       c. If mapping rule had rate_override → rate = rate_override
       d. Else → rate = section.default_rate
    
    8. COMPUTE:
       tds_amount = ROUND(tds_base * rate / 100, 0)  ← nearest rupee
    
    9. RETURN TdsResult(...)
```

### 4.4 Key Edge Cases

| Case | Handling |
|------|----------|
| **PAN missing** | 206AA: use `pan_missing_rate` (20%) regardless of section rate |
| **Section 197 certificate** | Vendor override with certificate_no, valid_from/to; rate can be 0% (nil deduction) |
| **Pre-threshold bills exempt** | Bills paid before the cumulative hits the threshold carry `tds_status = 'NA'` and `tds_amount = 0`. No TDS is deducted or retroactively applied on them. |
| **Crossing bill** | The bill that tips the cumulative ≥ threshold gets TDS on **its own amount only**. E.g. vendor paid ₹40K + ₹40K + ₹30K (194JB ₹50K threshold): bills 1 & 2 exempt, bill 3 (₹30K) is the crossing bill → TDS = rate × ₹30,000. |
| **Post-crossing bills** | All subsequent bills for the same vendor+section+FY get full TDS on their own amount (cumulative query shows already crossed). |
| **Cumulative tracking** | The engine queries `SUM(total_amount)` across **all** bills for that vendor+section+FY (including exempt ones) to correctly track the running total paid. |
| **194C dual threshold** | TDS fires if EITHER the single bill ≥ ₹30K OR the FY aggregate ≥ ₹1L. In both cases TDS is on that bill's own amount. |
| **194Q excess-of** | Only the incremental amount exceeding ₹50L is taxed. |
| **GST exclusive base** | Per CBDT Circular 23/2017: if GST is shown separately on invoice, TDS on pre-GST amount |
| **Mid-year rate change** | `effective_from/to` on section; engine picks the section valid on invoice_date |
| **Vendor with no category** | No mapping rule matches → TDS not applicable (admin must categorize) |

---

## 5. Pipeline Integration

### 5.1 When TDS is Calculated

Based on `settings_jsonb.tds.deduct_on`:

| Setting | Trigger Point | Pipeline |
|---------|--------------|----------|
| `BOOKING` | When bill is promoted to `stg_bills` | `staging_promotion.py` |
| `PAYMENT` | When payment is recorded | Payment workflow |
| `EARLIER` | Whichever happens first | Both pipelines check |

For MVP: implement `BOOKING` (at staging promotion).

### 5.2 Flow in `staging_promotion.py`

```
After bill passes validation and enters stg_bills:

1. Build TdsInput from the bill row
2. Call calculate_tds(input)
3. If result.applicable:
     UPDATE stg_bills SET
       tds_section_id = result.section_id,
       tds_rate = result.rate,
       tds_base_amount = result.base_amount,
       tds_amount = result.tds_amount,
       tds_status = 'CALCULATED'
4. If not applicable:
     UPDATE stg_bills SET tds_status = 'NA'
5. When bill is approved and promoted to fact_bills:
     Copy TDS fields to fact_bills
```

### 5.3 Recalculation Triggers

TDS may need recalculation when:
- Admin changes a section's rate or threshold
- Admin changes a vendor's category
- A vendor override is added/removed/expired
- A previously uploaded bill is edited

Strategy: flag affected bills as `tds_status = 'PENDING'` and let the next pipeline run recalculate. Bulk recalc endpoint for admin-triggered changes.

---

## 6. API Endpoints

All under `/api/settings/tds/...`. Follow existing CRUD pattern (like ChargesManager).

### 6.1 TDS Sections

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/api/settings/tds/sections` | — | List all sections for tenant |
| POST | `/api/settings/tds/sections` | `TdsSectionCreate` | Add new section |
| PUT | `/api/settings/tds/sections/{id}` | `TdsSectionUpdate` | Edit section |
| DELETE | `/api/settings/tds/sections/{id}` | — | Soft delete (set is_active=false) |

### 6.2 Vendor Categories

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/api/settings/tds/vendor-categories` | — | List all |
| POST | `/api/settings/tds/vendor-categories` | `{name, description}` | Create |
| PUT | `/api/settings/tds/vendor-categories/{id}` | `{name, description}` | Update |
| DELETE | `/api/settings/tds/vendor-categories/{id}` | — | Soft delete |

### 6.3 Mapping Rules

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/api/settings/tds/mapping-rules` | — | List all, ordered by priority |
| POST | `/api/settings/tds/mapping-rules` | `MappingRuleCreate` | Create |
| PUT | `/api/settings/tds/mapping-rules/{id}` | `MappingRuleUpdate` | Update |
| PUT | `/api/settings/tds/mapping-rules/reorder` | `[{id, priority}, ...]` | Bulk reorder |
| DELETE | `/api/settings/tds/mapping-rules/{id}` | — | Soft delete |

### 6.4 Vendor Overrides

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/api/settings/tds/vendor-overrides` | `?supplier_id=` | List (optionally filtered) |
| POST | `/api/settings/tds/vendor-overrides` | `VendorOverrideCreate` | Create |
| PUT | `/api/settings/tds/vendor-overrides/{id}` | `VendorOverrideUpdate` | Update |
| DELETE | `/api/settings/tds/vendor-overrides/{id}` | — | Soft delete |

### 6.5 Preferences

| Method | Path | Body | Notes |
|--------|------|------|-------|
| GET | `/api/settings/tds/preferences` | — | Get from settings_jsonb |
| PUT | `/api/settings/tds/preferences` | `TdsPreferences` | Update settings_jsonb.tds |

---

## 7. Frontend — Settings Page UI

### 7.1 Overall Structure

Add a new section to `SettingsPage.tsx` after Charges Master:

```
Settings Page
├── Approval Chain
├── Roles & Permissions
├── Team Members
├── Charges Master
└── TDS Configuration        ← NEW
    ├── TDS Sections          (always visible)
    ├── Vendor Categories     (always visible)
    ├── [Advanced ▼]          (toggle, collapsed by default)
    │   ├── Mapping Rules
    │   └── Vendor Overrides
    └── Preferences           (always visible)
```

### 7.2 TDS Sections Tab

**Layout:** Inline add form (top) + sortable table (below). Same pattern as ChargesManager.

**Table columns:**
| Column | Width | Notes |
|--------|-------|-------|
| Return Code | 80px | `1027` |
| Old Section | 80px | `194JB` |
| Nature of Payment | flex | Truncated with tooltip |
| Rate | 70px | `10%` |
| No-PAN Rate | 80px | `20%` |
| Threshold | 120px | `₹50,000/FY` (formatted from type + amount) |
| Valid From | 100px | Date |
| Status | 70px | Active/Inactive badge |
| Actions | 80px | Edit / Delete icons |

**Add/Edit form fields:**
- Return Code (text, required)
- Old Section (text, optional — for reference)
- New Section (text, optional — full Act reference)
- Nature of Payment (text, required)
- Default Rate % (number, required)
- No-PAN Rate % (number, default 20)
- Threshold Type (dropdown: None / Per Transaction / Aggregate FY / Per Month / Excess Of / Dual)
- Threshold Amount (number, shown when type ≠ NONE)
- Secondary Threshold (number, shown only when type = DUAL)
- Base Calculation (dropdown: GST Exclusive / Total Amount)
- Effective From (date, required)
- Effective To (date, optional)

**Threshold display formatting:**
```
NONE            → "—"
PER_TXN         → "₹30,000/txn"
AGGREGATE_FY    → "₹50,000/FY"
PER_MONTH       → "₹50,000/month"
EXCESS_OF       → "> ₹50,00,000"
DUAL            → "₹30,000/txn OR ₹1,00,000/FY"
```

### 7.3 Vendor Categories Tab

**Layout:** Simple list with inline add. Drag to reorder.

**Each row:**
- Name (editable text)
- Description (editable text, optional)
- Vendor count (read-only badge showing how many suppliers are in this category)
- Active toggle
- Delete button (disabled if vendors are assigned)

### 7.4 Mapping Rules (Advanced)

**Layout:** Priority-ordered table. Drag-to-reorder rows.

**Table columns:**
| Column | Notes |
|--------|-------|
| Priority | Auto-numbered, changes on drag |
| Vendor Category | Dropdown (from dim_vendor_categories) |
| TDS Section | Dropdown (from dim_tds_sections, show return_code + nature) |
| Rate Override | Number field (blank = use section default) |
| Description | Text |
| Actions | Edit / Delete |

**Visual:**
- Arrow indicators showing priority order
- "First match wins" label at top
- Rate Override field shows placeholder text "Section default: 10%" when empty

### 7.5 Vendor Overrides (Advanced)

**Layout:** Table with search/filter.

**Table columns:**
| Column | Notes |
|--------|-------|
| Vendor | Searchable dropdown (dim_suppliers) |
| TDS Section | Dropdown |
| Override Rate | Number (can be 0 for nil deduction) |
| Certificate No | Text field |
| Valid From | Date |
| Valid To | Date |
| Status | Active / Expired (auto-calculated) |
| Actions | Edit / Delete |

**Auto-expiry:** Show expired overrides in grey with "Expired" badge. Option to filter them out.

### 7.6 Preferences

**Layout:** Simple form with toggles and dropdowns.

| Field | Type | Options |
|-------|------|---------|
| Deduct TDS On | Radio group | Booking / Payment / Earlier of the two |
| Base Amount | Radio group | GST Exclusive (recommended) / Total Amount |
| Auto-Apply TDS | Toggle | On/Off with helper text |
| FY Start Month | Dropdown | April (default) |
| Default No-PAN Rate | Number | 20% (Section 206AA) |

**Helper text for Base Amount:**
"Per CBDT Circular 23/2017, when GST is shown separately on the invoice, TDS should be deducted on the pre-GST amount. Recommended: GST Exclusive."

---

## 8. Frontend Components & CSS

### 8.1 Component Structure

```
SettingsPage.tsx
└── TdsConfigManager              (new component, or inline in SettingsPage)
    ├── TdsSectionsPanel
    │   ├── TdsSectionForm        (add/edit inline form)
    │   └── TdsSectionTable       (table with rows)
    ├── VendorCategoriesPanel
    │   ├── CategoryForm
    │   └── CategoryList
    ├── AdvancedToggle            (collapsible)
    │   ├── MappingRulesPanel
    │   │   ├── MappingRuleForm
    │   │   └── MappingRuleTable  (drag-sortable)
    │   └── VendorOverridesPanel
    │       ├── OverrideForm
    │       └── OverrideTable
    └── TdsPreferencesPanel
        └── PreferencesForm
```

### 8.2 CSS Prefix

Use `tds__` prefix for all TDS-related styles (following existing `chg__` for charges, `sp__` for settings page).

### 8.3 TypeScript Interfaces (`api/invoices.ts`)

```typescript
// TDS Section
export interface TdsSection {
  tds_section_id: string;
  return_code: string;
  old_section: string | null;
  new_section: string | null;
  nature_of_payment: string;
  default_rate: number;
  pan_missing_rate: number;
  threshold_type: 'NONE' | 'PER_TXN' | 'AGGREGATE_FY' | 'PER_MONTH' | 'EXCESS_OF' | 'DUAL';
  threshold_amount: number | null;
  threshold_amount_secondary: number | null;
  base_calculation: 'GST_EXCLUSIVE' | 'TOTAL_AMOUNT';
  effective_from: string;
  effective_to: string | null;
  is_active: boolean;
}

// Vendor Category
export interface VendorCategory {
  category_id: string;
  name: string;
  description: string | null;
  display_order: number;
  is_active: boolean;
  vendor_count?: number;     // computed in API
}

// Mapping Rule
export interface TdsMappingRule {
  rule_id: string;
  tds_section_id: string;
  category_id: string | null;
  priority: number;
  rate_override: number | null;
  description: string | null;
  is_active: boolean;
  // Joined fields for display:
  section_return_code?: string;
  section_nature?: string;
  category_name?: string;
}

// Vendor Override
export interface VendorTdsOverride {
  override_id: string;
  supplier_id: string;
  tds_section_id: string;
  override_rate: number;
  certificate_no: string | null;
  valid_from: string;
  valid_to: string;
  is_active: boolean;
  // Joined:
  supplier_name?: string;
  section_return_code?: string;
}

// Preferences
export interface TdsPreferences {
  deduct_on: 'BOOKING' | 'PAYMENT' | 'EARLIER';
  base_default: 'GST_EXCLUSIVE' | 'TOTAL_AMOUNT';
  auto_apply: boolean;
  fy_start_month: number;
  pan_missing_rate: number;
}
```

---

## 9. New IT Act Form Mapping Reference

For admin reference in the UI (tooltip or info panel):

| Purpose | Old Form | New Form |
|---------|----------|----------|
| TDS certificate (non-salary) | Form 16A | Form 131 |
| Quarterly TDS return (non-salary, residents) | Form 26Q | Form 140 |
| PAN-based TDS (property, rent, etc.) | Forms 26QB/QC/QD/QE | Form 141 |
| Annual Information Statement | Form 26AS | Form 168 |
| Nil/Lower deduction application | Form 15G / 15H | Form 121 |
| Tax Audit Report | Form 3CA/3CB/3CD | Form 26 |

---

## 10. Implementation Order

### Phase 1 — Backend Foundation
1. Migration: create 4 new tables + alter existing tables
2. Seed data: vendor categories + TDS sections from Excel
3. CRUD APIs: all 5 endpoint groups (sections, categories, mapping rules, overrides, preferences)
4. Unit tests for API endpoints

### Phase 2 — TDS Engine
5. `utils/tds_engine.py` — pure calculation logic
6. Unit tests for all threshold types + edge cases (no PAN, expired override, dual threshold, excess-of)
7. Integration with `staging_promotion.py` (BOOKING mode)

### Phase 3 — Frontend Settings UI
8. TypeScript interfaces + API functions in `invoices.ts`
9. TDS Sections panel (table + form)
10. Vendor Categories panel
11. Preferences panel
12. Advanced toggle + Mapping Rules panel
13. Advanced toggle + Vendor Overrides panel

### Phase 4 — Bills Integration
14. TDS columns in BillsPage table (section, rate, amount, status)
15. TDS summary in bill detail view
16. Manual TDS override on individual bills (admin)

### Phase 5 — Reports (Future)
17. TDS register / ledger report
18. Form 140 (26Q replacement) data export
19. Vendor-wise TDS summary

---

## 11. Files to Create / Modify

| File | Action | What |
|------|--------|------|
| `backend_ocr/app/main.py` | MODIFY | Add migration + 15 CRUD endpoints |
| `utils/tds_engine.py` | CREATE | Calculation engine (~200 lines) |
| `frontend/src/api/invoices.ts` | MODIFY | Add TDS interfaces + API functions |
| `frontend/src/pages/SettingsPage.tsx` | MODIFY | Add TDS config section |
| `frontend/src/pages/SettingsPage.css` | MODIFY | Add `tds__*` styles |
| `pipelines/staging_promotion.py` | MODIFY | Call TDS engine after promotion |

---

*End of plan. No code has been written. Awaiting approval to begin implementation.*
