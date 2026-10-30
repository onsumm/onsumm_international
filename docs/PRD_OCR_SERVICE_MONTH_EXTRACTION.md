# PRD — OCR Service-Month Extraction (detailed, edge-case-complete)

**Status:** Draft · **Branch:** `uat3` · **Part of:** Nova Vendor Billing
Reconciliation. **Pairs with:** `docs/PRD_NOVA_RECONCILIATION_AT_APPROVAL.md`.
**Prerequisite for** reconciliation — it supplies the month key everything else
is joined on.

---

## 1. Purpose & principle

Nova reconciliation compares an invoice to Nova's usage ledger for a specific
**(vendor, month)**. That comparison is agentic and slow, so it runs later, at
approval. The one thing OCR must capture — cheaply, inside the existing single
GPT-4o call, adding **zero latency** — is **which service/billing month the
invoice covers**.

**The service month ≠ the invoice date.** An invoice dated `5-Mar-2026` routinely
bills the **Feb-2026** service period. Getting this wrong silently reconciles the
bill against the wrong Nova snapshot, so this field is treated as
correctness-critical, extracted at two levels, normalised deterministically, and
always operator-correctable.

**Design stance:** *extract generously, normalise deterministically, degrade
safely, never guess silently.* Every value carries where it came from and how
confident we are, so the reconciliation agent and the operator can tell a
high-confidence header month from a shaky inference.

---

## 2. Data model

### 2.1 Extracted object (per bill)
```jsonc
{
  "service_month": "2026-02",              // canonical YYYY-MM, or null
  "service_month_meta": {                  // NEW sidecar (audit + downstream trust)
    "raw": "Billing Period: 01-Feb-2026 to 28-Feb-2026",
    "source": "header",                    // header | line_item | description | invoice_date_fallback | operator
    "label": "Billing Period",             // the label text we matched, if any
    "form": "range",                       // range | month_year | month_only | numeric | iso | fy | quarter | relative
    "confidence": 0.94,                     // 0..1 (see §6)
    "inferred_year": false,                 // year was inferred from invoice_date
    "needs_review": false                   // true → review page highlights the field
  },
  "line_items": [
    { "...": "...", "service_month": "2026-01" }  // per-line, only when it differs
  ]
}
```

### 2.2 Persisted columns
- `stg_bills.service_month text`, `stg_bills.service_month_source text`,
  `stg_bills.service_month_confidence numeric(5,4)`.
- `stg_bill_line_items.service_month text`.
- Idempotent `ADD COLUMN IF NOT EXISTS` in the Nova billing migration.

### 2.3 Resolution hierarchy (used later by reconciliation, not at OCR)
`line_item.service_month` → `bill.service_month` → month of `invoice_date`.
Stored raw at each level; the *effective* month is resolved at reconciliation
time so a correction to any level re-drives it.

---

## 3. Where it appears & label lexicon

**Header level** (most common). Match, case-/space-insensitive, any of:
`Billing Period`, `Billing Month`, `Bill Period`, `Service Period`,
`Service Month`, `Service Mon`, `Svc Month`, `Usage Month`, `Usage Period`,
`For the month of`, `For the period`, `Period`, `Month`, `Month of Supply`,
plus abbreviations/enums: `SER MON`, `SER. MONTH`, `SVC MTH`, `Billing Prd`,
`Prd`, `Mth`, `B.Period`, `Period of Supply`.

**Line-item level**: a `Period`/`Month` column, or embedded in the description
(`"PAN verification — Feb 2026"`, `"SMS charges (Jan'26)"`).

**Never** treat these as the service month: `Invoice Date`, `Due Date`, `PO Date`,
`Delivery Date`, `E-way Bill Date`, `Date` (bare, in the header block). The prompt
must explicitly disambiguate the service/billing month from the invoice date.

---

## 4. Canonical normalisation — the algorithm

Target: **`YYYY-MM`**. Run a deterministic normaliser
`_normalize_service_month(raw, invoice_date) -> (value|None, meta)` over the LLM
output (belt-and-suspenders; the model sometimes returns "Feb 2026"). Order of
attempts:

1. **ISO** `2026-02`, `2026/02`, `2026.02` → as-is. (`form=iso`)
2. **Month-name + year**: `Feb 2026`, `February 2026`, `Feb-2026`, `Feb'26`,
   `FEB 26` → map name→MM, 2-digit year→20YY. (`form=month_year`)
3. **Numeric** `MM/YYYY`, `MM-YYYY`, `M/YY` → zero-pad month. (`form=numeric`)
4. **Range** `01-Feb-2026 to 28-Feb-2026`, `01/02/26–28/02/26`,
   `Feb 1 – Mar 19, 2026` → take the **period-start** month/year, set
   `form=range`. (See §5 for the multi-month-range rule.)
5. **Fiscal-year forms** `FY25-26 / Feb`, `F.Y. 2025-26 Feb` → resolve the named
   month into the correct calendar year using the Indian FY (Apr–Mar): Feb of
   FY25-26 = `2026-02`; May of FY25-26 = `2025-05`. (`form=fy`)
6. **Quarter** `Q3 FY26`, `Jan-Mar 2026`, `Q4 2025-26` → this is a *multi-month*
   value; see §5 (default: start month + `needs_review=true`).
7. **Relative** `current month`, `this month`, `monthly` → `null` unless an
   invoice_date exists, then that month with `form=relative`,
   `confidence≤0.5`, `needs_review=true`.
8. **Unparseable** → `null`, `needs_review=true`.

**Year inference** (when a month name has no year): use `invoice_date`. Since
invoices bill *past* usage, if the extracted month is numerically **after** the
invoice month, roll the year back one (`Dec` service on a `Jan` invoice →
previous year). Set `inferred_year=true`, cap `confidence≤0.8`.

**DD/MM vs MM/DD**: assume **DD/MM/YYYY** (Indian invoices). `02/03/2026` inside a
range is `2-Mar-2026`; a bare `02/2026` is `MM/YYYY` = Feb-2026. If a token >12
disambiguates (`13/02/2026`), use it.

**Sanity bounds:** the resolved month must be within **[invoice_date − 13 months,
invoice_date + 1 month]**. Outside → keep the value but `needs_review=true`,
`confidence≤0.4` (catches OCR year misreads like `2062-02`, and stray dates).

---

## 5. Edge cases & exact handling

| # | Case | Handling |
|---|---|---|
| 1 | **Multiple header labels** present (`Billing Period` and `Month`) | Priority: explicit `Billing/Service Period/Month` > generic `Period` > bare `Month`. Record which in `label`. |
| 2 | **Range within one month** (`1–28 Feb 2026`) | `2026-02`, `form=range`, full confidence. |
| 3 | **Range spanning two months** (`20 Feb–19 Mar 2026`, a 20th-to-19th cycle) | Use **start** month (`2026-02`). If an explicit single "Billing Month: Mar 2026" also appears, the explicit value wins. `needs_review=false` but note span in `raw`. |
| 4 | **Range spanning year boundary** (`20 Dec 2025–19 Jan 2026`) | Start month/year → `2025-12`. |
| 5 | **Quarter / multi-month** (`Q3 FY26`, `Jan–Mar 2026`) | Start month + `form=quarter/range`, `needs_review=true`. Reconciliation may fan out per month (see recon PRD §7.3). |
| 6 | **Bare month, no year** | Infer year from invoice_date (§4). `inferred_year=true`. |
| 7 | **No month anywhere** | `null`, `needs_review=true`; review page shows empty editable field with a "required for reconciliation" hint for Nova vendors. |
| 8 | **Month only in a line-item description** | Extract to that line's `service_month`; if all lines agree and header is empty, also set bill-level (`source=description`, confidence −0.1). |
| 9 | **Different months per line** | Keep each line's own `service_month`; bill-level = the **most frequent** line month (mode), `needs_review=true` if no clear majority. |
| 10 | **Header month conflicts with line months** | Keep both; bill-level = header value but set `needs_review=true` so the operator reconciles the discrepancy. |
| 11 | **Credit note / adjustment for a prior month** | Extract the *referenced service month* if stated (`"credit for Jan 2026"`), not the credit-note date. `document_type` stays as-is. |
| 12 | **Two months in one field** (`Feb–Mar 2026`, not a range but a list) | Treat as range → start month, `needs_review=true`. |
| 13 | **Non-English / localised month** (`फ़रवरी`, `Feb.` with dot, regional) | Prompt handles common ones; normaliser has an alias map incl. Hindi transliterations; unknown → `null`+review. |
| 14 | **Typos** (`Febuary`, `Feburary`, `Setember`) | Fuzzy month-name match (edit distance ≤2) in the normaliser. |
| 15 | **OCR digit misread in year** (`2026`→`2026`/`2O26`/`2062`) | `O`/`o`→`0`; then sanity-bound (§4) catches impossible years → `needs_review`. |
| 16 | **Annual / one-time / setup fee** (no service month concept) | `null`, `form=null`, `needs_review=false` if `document_type` suggests one-time; reconciliation treats as `NOT_APPLICABLE`. |
| 17 | **Invoice for future month** (advance billing) | Allowed within +1 month bound; beyond → `needs_review`. |
| 18 | **Multi-page, month on a later page** | Existing engine merges pages before parse — no special handling. |
| 19 | **Duplicate / re-upload** | Deterministic — same input → same value; idempotent with the existing `invoice_upload_id`. |
| 20 | **Relative term only** (`current billing cycle`) | §4 rule 7 — infer from invoice_date, low confidence, review. |
| 21 | **Financial year without month** (`FY 2025-26`, no month) | Cannot resolve to a single month → `null`, `needs_review=true`. |
| 22 | **Amount/qty accidentally parsed as month** | Sanity bounds + `form` validation reject non-month tokens. |
| 23 | **Operator edits the field** | `source=operator`, `confidence=1.0`, `needs_review=false`; overrides everything downstream. |

---

## 6. Confidence scoring

`confidence` starts at a base by `form` and is reduced by risk flags:

| form | base |
|---|---|
| iso / month_year (explicit, with year) | 0.95 |
| numeric | 0.9 |
| range (single month) | 0.92 |
| fy (resolved) | 0.85 |
| range (multi-month) / quarter | 0.6 |
| month_only + inferred year | 0.75 |
| relative | 0.45 |

Penalties: `−0.1` if `source=description`; `−0.15` if header⇄line conflict;
clamp to `≤0.4` if out of sanity bounds. `needs_review = confidence < 0.7`.

---

## 7. Prompt changes (`utils/ocr_engine.py:SYSTEM_PROMPT`)

Add `service_month` to the top-level schema and each `line_items[]` entry
(strings, `""` when absent). Append rules:

> **Service month:** Find the *service / billing month* the invoice covers — this
> is DIFFERENT from the invoice date. Look for labels like Billing Period,
> Service Month, Service Period, SER MON, Period, For the month of, Usage Month,
> or a Period/Month column or month mentioned in a line's description. Return it
> as `YYYY-MM`. For a date range, use the **start** month. If only a month name
> is given, infer the year from the invoice date (invoices bill past usage, so a
> month after the invoice's month belongs to the previous year). If a line item
> states its own month, set that line's `service_month`; otherwise leave it `""`
> and rely on the bill-level value. If none is present, return `""` — do not
> guess.

Deterministic `_normalize_service_month` runs after `_call_vision` on the
bill-level value and every line value, producing the canonical `YYYY-MM` + meta.

---

## 8. UX & signaling — Review page (`frontend/src/pages/ReviewPage.tsx`)

The operator's job here is a 2-second glance: *"is the service month right, and
do I need to touch it?"* Everything below serves that. **We surface trust and
provenance in plain language; we never expose internal scores or enum codes.**

### 8.1 Placement & layout
- A **"Service Month"** field in the **Bill Summary** section (near Invoice Date /
  Due Date so the operator reads them together and can catch a date-vs-month
  mix-up). `<input type="month">` bound to `d.service_month` (value `YYYY-MM`).
- Directly beneath the input: a single-line **provenance chip** (the trust
  signal). One chip, never a paragraph.

### 8.2 The three trust states (exact copy, colour, icon)
Driven by `service_month_meta` — but the operator sees words, not numbers:

| Condition | Chip text | Colour | Icon | Field border |
|---|---|---|---|---|
| Found on the bill, high confidence (`source∈{header,line_item}`, `confidence≥0.7`, in bounds) | `Found on bill · "{label}"` (e.g. `Found on bill · "Billing Period"`) | green | ✓ | normal |
| Inferred (year guessed, relative term, or from invoice date) | `Inferred from invoice date — please confirm` | amber | ⓘ | amber |
| Not found / low confidence / out of bounds (`needs_review`) | `Not found on bill — please set the service month` | red | ⚠ | red |
| Operator edited it | `Set by you` | slate | ✎ | normal |

- **Tooltip on the chip** (hover / long-press) shows the *raw* snippet we read:
  `Read: "Billing Period: 01-Feb-2026 to 28-Feb-2026"`. This is the only place the
  raw text appears — it's the "show your work" affordance, opt-in.
- The chip text is **also** conveyed by icon + border colour, never colour alone
  (accessibility — colour-blind safe).

### 8.3 What the operator SHOULD see
- The month itself (editable), the trust chip, and the raw snippet on hover.
- A **non-blocking** inline warning **only** when it matters: if the vendor is a
  Nova/SAP vendor **and** the month is empty/`needs_review` →
  `⚠ Reconciliation needs the service month — approvers will see a variance check
  for this vendor.` (Explains *why* they should care, not just "fill this in".)
- On a **multi-month bill**, a subtle note under the header field:
  `Some line items are for different months — see the Month column below.`

### 8.4 What the operator should NOT see (internal only)
Kept in `service_month_meta` / audit, **never rendered as UI**:
- The numeric `confidence` float, the `form` classification, the sanity-bound
  math, "inferred_year" as a raw boolean. (These map to the plain chips above.)
- Any model/prompt internals.
Rationale: a "0.82" means nothing to an AP clerk and invites false precision;
"Inferred — please confirm" tells them exactly what to do.

### 8.5 Per-line month
- Hidden by default. A **"Month"** column appears in the line-items table **only
  when** at least one line has its own `service_month` (keeps the 95%
  single-month case clean).
- When shown, each line's cell is an editable `type="month"`; empty cells render
  a muted placeholder `— bill month —` meaning "inherits the bill-level month".
- A line whose month differs from the bill month gets a small amber dot so the
  divergence is visible at a glance.

### 8.6 Edit & save signaling
- Editing the field (header or line) immediately flips the chip to **`Set by
  you`** and clears any warning — instant feedback that their input is
  authoritative (`source=operator`, `confidence=1.0`).
- On submit, no special modal; the value rides the normal save. If a Nova-vendor
  bill is submitted with the month still empty, show a **one-time confirm**:
  `No service month set — reconciliation will be skipped until it's added.
  Submit anyway?` (soft — never blocks, but makes the consequence explicit).

### 8.7 Empty / error states
- OCR still running → field shows a skeleton, no chip.
- OCR failed to return the field → treated as "Not found" (red chip), field
  empty and focus-highlighted so it's the obvious next action.

### 8.8 Copy principles
- Plain, consequence-led: say *what to do* and *why it matters* ("approvers will
  see a variance check"), never internal jargon.
- One signal per state; never stack a chip + banner + tooltip saying the same
  thing.

---

## 9. Persistence (`backend_ocr/app/ingest.py`)

- `_upsert_bill`: write `service_month`, `service_month_source`,
  `service_month_confidence` (insert + `COALESCE` update).
- `_insert_line_items`: write each line's `service_month`.
- No reconciliation, no Nova calls here.

---

## 10. Testing matrix (fixtures)

Cover every §5 row plus: Signzy `SIGNZY2526/08775` (invoice 5-Mar, period Feb) →
`2026-02`; `Period: 01-Feb-2026 to 28-Feb-2026` → `2026-02`; bare `Feb` on a
Mar invoice → `2026-02`; `Dec` on a Jan-2026 invoice → `2025-12`;
`FY25-26 / Feb` → `2026-02`; `Q3 FY26` → `2025-10` + `needs_review`; line
`"SMS Jan 2026"` on a Feb bill → line `2026-01`, bill `2026-02`; garbage →
`null` + `needs_review`; `2062-02` (OCR misread) → kept but `needs_review`,
`confidence≤0.4`.

---

## 11. Non-goals
- No reconciliation / Nova calls / agent at OCR time.
- No backfill of historical bills (re-OCR or set the month manually).
- No change to any other extracted field.

## 12. Acceptance
- All §10 fixtures pass.
- `needs_review` correctly flips for low-confidence/ambiguous cases.
- Operator edit persists and overrides; reconciliation later reads the resolved
  month via the §2.3 hierarchy.
