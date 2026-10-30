# COA Rules & Expense Report

## Overview

The COA (Chart of Accounts) Rules engine maps your vendors and bill categories to standard **Expense Heads (EXP HEAD)** — just like Mysa's COA Rules feature. Once rules are configured, you can generate a formatted Excel expense report (`.xlsx`) matching the `Actual EXP APR-26` format.

---

## COA Rules

### What is a COA Rule?

A rule tells the system: _"When a bill comes from this vendor and/or this category, tag it as EXP HEAD X."_

| Field | Description |
|---|---|
| **EXP HEAD** | The expense category (e.g. `IT`, `MARKETING`, `UTILITY`) |
| **Vendor** | Optional — leave blank to match any vendor |
| **Category** | Optional — leave blank to match any category |
| **Priority** | Higher number = checked first when multiple rules match |

### Matching Logic (3-tier)

When classifying a bill, rules are checked in this order:

1. **Vendor + Category match** — most specific, wins first
2. **Vendor only match** — if no vendor+category rule found
3. **Category only match** — if no vendor rule found
4. **Default → `OTHER`** — if no rule matches at all

### Default Rules (seeded on first startup)

| Category | EXP HEAD |
|---|---|
| Rent | UTILITY |
| Software / SaaS | IT |
| Food & Beverages | PANTRY |
| Utilities | UTILITY |
| Travel & Expenses | ADMINISTRATION |
| Reimbursement | ADMINISTRATION |

### Available EXP HEADs

```
ACCOUNTS · ADMINISTRATION · ASSETS · DUTIES & TAXES
IT · LEGAL · MAN POWER · MARKETING · OFFICE
PANTRY · PROFESSIONAL · RENT · UTILITY · OTHER
```

### API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/coa-rules?company_id=` | List all rules for tenant |
| `POST` | `/api/coa-rules` | Create a new rule |
| `PUT` | `/api/coa-rules/{id}` | Update an existing rule |
| `DELETE` | `/api/coa-rules/{id}?company_id=` | Delete a rule |

**POST / PUT body:**
```json
{
  "exp_head": "IT",
  "vendor_name": "Amazon Internet Services",
  "category": "Software / SaaS",
  "priority": 10,
  "company_id": "..."
}
```

---

## Expense Report (Excel Export)

### How to Generate

1. Go to **Reports** page
2. Click the **Expense Report** tab
3. Select a **Month** (e.g. April 2026)
4. Choose a **Status filter** (All bills or Approved only)
5. Click **Download Excel Report**

The file downloads as `Expense_Report_APR-26.xlsx`.

### Excel Structure

#### Sheet 1 — `RAW DATA`

One row per bill, matching the Avinash Capital format:

| Column | Source |
|---|---|
| COMPANY | Tenant/company name |
| VENDOR NAME | Supplier name from `dim_suppliers` |
| INVOICE NO | `stg_bill_id` or `invoice_id` |
| SERVICE MONTH | Invoice date formatted as `APR-26` |
| SUB HEAD | Supplier short name |
| SERVICE | Bill category (e.g. `Software / SaaS`) |
| EXP HEAD | Resolved via COA Rules engine |
| TOTAL | `total_amount` |
| OPEX | `Y` if paid or partially paid, blank otherwise |
| MONTHLY EXPENSE | Same as TOTAL |

#### Sheet 2 — `SUMMARY`

Grouped by EXP HEAD:

| Column | Description |
|---|---|
| EXP HEAD | Expense group |
| TOTAL AMOUNT | Sum of all bills in this group |
| % OF GRAND TOTAL | Share of total spend |
| Last row | **GRAND TOTAL** across all groups |

Headers are **bold** with a light grey background. Columns are auto-sized.

### API Endpoint

```
GET /api/reports/expense-xlsx
    ?company_id=<uuid>
    &month=2026-04
    &status=APPROVED      ← optional, omit for all bills
```

Returns: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`  
Filename: `Expense_Report_APR-26.xlsx`

---

## Database Schema

```sql
CREATE TABLE coa_rules (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    exp_head    text        NOT NULL,
    vendor_name text,                    -- NULL = match any vendor
    category    text,                    -- NULL = match any category
    priority    int         NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
```

---

## Frontend Location

- **Page:** Reports (`/reports` nav item)
- **Section:** `COA Rules & Expense Report` panel at the top of the Reports page
- **Sub-tabs:** `COA Rules` | `Expense Report`

---

## Example Workflow

```
1. Upload invoice from "Amazon Internet Services" → category "Software / SaaS"
2. COA Rule: category = "Software / SaaS" → EXP HEAD = IT  ✓
3. Another invoice from "Rana Enterprises" → category "Food & Beverages"
4. COA Rule: vendor = "Rana Enterprises" + category = "Food & Beverages" → EXP HEAD = PANTRY  ✓
5. Download April 2026 report → both bills appear in RAW DATA with correct EXP HEADs
6. SUMMARY sheet shows: IT total, PANTRY total, GRAND TOTAL
```
