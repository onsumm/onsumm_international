# TDS Agent

| | |
|---|---|
| **Agent name** | `tds` |
| **Fleet card** | "Computes liability, drafts the return." |
| **Priority** | 🟡 **P2** |
| **Status** | 🟡 Partial — computation, sections, mapping rules and the YTD ledger are built; return assembly and filing are not |
| **Default autonomy** | **L0 → L1** — compute and draft. Filing is never autonomous |
| **Difficulty** | Medium — the engine exists; correctness bar is statutory |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.8 |
| **Related** | [TDS_SETTINGS_IMPLEMENTATION_PLAN.md](../TDS_SETTINGS_IMPLEMENTATION_PLAN.md) |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Get withholding right, every bill, and make quarterly filing a review rather than a
reconstruction.

Most of the hard work is done. [`tds_api.py`](../../backend_ocr/app/tds_api.py)
manages sections, vendor categories, priority-ordered mapping rules and per-vendor
overrides; [`utils/tds_engine.py`](../../utils/tds_engine.py) computes;
[`utils/tds_catchup.py`](../../utils/tds_catchup.py) handles the threshold-crossing
catch-up case; [`pipelines/tds_processing.py`](../../pipelines/tds_processing.py)
applies TDS during bill processing; `fact_tds_ledger` tracks YTD per
`(supplier, fiscal_year, section)`.

What the agent adds: **exception detection before filing**, **quarterly return
assembly (26Q)**, and a **challan/payment reconciliation** loop. The value is
catching the under-deduction in month two, not discovering it in the quarter-three
filing.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Bill with TDS applicability | `event` | `bill.ingested` — compute and attach; already largely handled by the worker |
| Threshold crossing | `event` | Cumulative payments cross a section threshold → catch-up deduction (`tds_catchup`) |
| Quarterly filing window | `schedule` | 15 Jul / 15 Oct / 15 Jan / 31 May — assemble the return draft |
| Monthly deposit due | `schedule` | 7th of each month — deposit liability and challan reminder |
| User action | `user` | "Review TDS" for a period; recompute a vendor |
| Rate/section config change | `event` | New rules → re-evaluate open bills in the current period |

## 3. Inputs

```json
{
  "mode": "compute|review|assemble_return|reconcile_challan",
  "bill_id": "uuid",
  "period": {"fiscal_year": "2026-27", "quarter": "Q1"},
  "supplier_ids": ["uuid"],
  "form": "26Q"
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `resolve_section(bill)` | **reuse** | Priority-ordered `dim_tds_mapping_rules` + `dim_vendor_tds_overrides`, via `tds_engine` |
| `compute_tds(bill)` | **reuse** | `tds_engine` — rate, base, amount, cumulative mode |
| `compute_catchup(supplier, section)` | **reuse** | [`tds_catchup.py`](../../utils/tds_catchup.py); decision endpoint already exists (`POST /vendor-overrides/{id}/catchup-decision`) |
| `get_ytd_ledger(supplier, fy, section)` | **wrap** | `fact_tds_ledger` |
| `detect_exceptions(period)` | **new** | Missing PAN, under/over-deduction, wrong section, unapplied threshold, higher-rate cases (206AA no-PAN → 20%) |
| `assemble_return(fy, quarter, form)` | **new** | 26Q deductee-wise draft with challan mapping |
| `reconcile_challan(period)` | **new** | Deducted vs deposited vs challan; the gap that becomes interest liability |
| `compute_interest_penalty(gaps)` | **new** | 201(1A) interest — deterministic |
| `report_tds(period)` | **wrap** | Feeds [`analytics`](ANALYTICS_AGENT.md) |

## 5. Steps

**Review mode (the primary value):**

1. **Scope** the period using tenant fiscal config ([`utils/fiscal.py`](../../utils/fiscal.py)).
2. **Recompute expected TDS** for every bill in scope, independently of what was
   applied — a shadow calculation, not a read of `stg_bills.tds_amount`.
3. **Diff expected vs applied.** Every mismatch is an exception with a rupee value.
4. **Detect structural exceptions**: no PAN (→ 206AA higher rate), inactive
   section, threshold crossed but no catch-up, vendor override expired mid-year,
   deductee category mismatch.
5. **Classify severity** by exposure — under-deduction carries interest and
   disallowance risk; over-deduction is a vendor-relations and refund problem.
6. **Propose corrections**, each with the arithmetic shown. **Never applies them.**
7. **Emit** the exception set.

**Assemble-return mode:** group by section and deductee → map challans → produce a
26Q draft + validation report → **gate before any filing**.

**Reconcile-challan mode:** deducted (ledger) vs deposited (challans) per month →
shortfalls with 201(1A) interest computed.

## 6. Outcome schema

```json
{
  "mode": "review",
  "period": {"fiscal_year": "2026-27", "quarter": "Q1"},
  "totals": {
    "bills_reviewed": 214,
    "tds_applied_inr": 486300.00,
    "tds_expected_inr": 498100.00,
    "net_exposure_inr": 11800.00
  },
  "exceptions": [
    {
      "type": "UNDER_DEDUCTION",
      "severity": "HIGH",
      "bill_id": "…", "supplier": "Acme Consulting",
      "section": "194J", "expected_rate": 10.0, "applied_rate": 2.0,
      "expected_amount": 12000.00, "applied_amount": 2400.00, "delta": 9600.00,
      "cause": "Vendor category mapped to 194C; services are professional",
      "proposed_correction": "Reclassify to 194J; catch-up ₹9,600 on next bill",
      "interest_if_unfixed_inr": 288.00
    },
    {"type": "MISSING_PAN", "severity": "HIGH", "supplier": "…",
     "note": "206AA applies — 20% instead of 10%", "delta": 8500.00}
  ],
  "by_section": [{"section": "194J", "deductees": 42, "base": 4200000, "tds": 420000}],
  "challan_status": {"deducted": 486300, "deposited": 486300, "shortfall": 0},
  "return_draft": null
}
```

**Exception types:** `UNDER_DEDUCTION` · `OVER_DEDUCTION` · `WRONG_SECTION` ·
`MISSING_PAN` · `THRESHOLD_NOT_APPLIED` · `EXPIRED_OVERRIDE` ·
`CHALLAN_SHORTFALL` · `LATE_DEPOSIT`.

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: schedule`, `trigger_ref: tds_quarterly_review`, `reason: "Q1 FY2026-27 review ahead of 15 Jul filing"` |
| **WHOM** | `entity_type: tenant` for period runs; `entity_type: bill` for single-bill compute |
| **WHAT** | `action: "Reviewed 214 bills; 6 exceptions, ₹11,800 net exposure"`, `outcome:` above |
| **HOW** | `tools_used: [resolve_section, compute_tds, get_ytd_ledger, detect_exceptions]`; steps record which mapping rule matched, by priority, per bill |

Logging **which rule matched and why** is the difference between a defensible
filing and a black box. In an assessment, "rule #3 (category=professional) matched
at priority 3" is the answer that holds.

## 8. Guardrails

- **Every computation deterministic.** No LLM in any TDS number, ever. The LLM's
  only permitted role is narrating an exception in plain language.
- **Never auto-applies a correction.** Corrections are proposals; applying one
  changes a bill's payable amount, which is [`payable`](PAYABLE_AGENT.md)'s
  gated territory.
- **Filing behind a hard gate**, always, at any autonomy level.
- **Section/rate config is data, not inference.** An unmapped case is an exception,
  not a guess — a plausible-looking wrong section is worse than a flagged unknown.
- **Fiscal-year awareness** — rates and thresholds are effective-dated
  (`dim_tds_sections.effective_from`); computation must use the rate as of the bill
  date, not today's.
- **Idempotent recompute.** Shadow calculation writes nothing to `stg_bills` or
  `fact_tds_ledger`.
- **PAN handling** — PANs are PII; store masked in the audit store.

## 9. Data model

**Reuses:** `dim_tds_sections`, `dim_tds_mapping_rules`, `dim_vendor_tds_overrides`,
`fact_tds_ledger`, `stg_bills.tds_*`, `fact_bills`, `dim_suppliers`.

**New:**

```sql
CREATE TABLE IF NOT EXISTS tds_challans (
    challan_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL,
    bsr_code      text,
    challan_no    text,
    deposit_date  date        NOT NULL,
    fiscal_year   text        NOT NULL,
    section_code  text        NOT NULL,
    amount        numeric(18,2) NOT NULL,
    interest      numeric(18,2) NOT NULL DEFAULT 0,
    fee_234e      numeric(18,2) NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, bsr_code, challan_no, deposit_date)
);

CREATE TABLE IF NOT EXISTS tds_return_drafts (
    draft_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL,
    run_id       uuid        REFERENCES agent_runs(run_id),
    form         text        NOT NULL,            -- 26Q|27Q
    fiscal_year  text        NOT NULL,
    quarter      text        NOT NULL,
    deductees    jsonb       NOT NULL,            -- deductee-wise lines
    challan_map  jsonb       NOT NULL,
    totals       jsonb       NOT NULL,
    validation   jsonb       NOT NULL DEFAULT '[]',
    status       text        NOT NULL DEFAULT 'draft',  -- draft|approved|filed
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, form, fiscal_year, quarter)
);
```

## 10. API

Existing (keep): the full `/api/settings/tds` surface — sections, vendor
categories, mapping rules with reorder, vendor overrides, catch-up decisions,
FY state.

New:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/tds/run` | compute / review / assemble_return / reconcile_challan |
| `GET` | `/api/agents/tds/exceptions` | Open exceptions for a period |
| `GET` | `/api/agents/tds/returns/{fy}/{quarter}` | Return draft + validation report |
| `POST` | `/api/agents/tds/challans` | Record a deposited challan |
| `GET` | `/api/agents/tds/liability` | Month-wise deducted vs deposited |

## 11. Build plan

**Step 1 — shadow recompute + exception detection (3 days).** The whole point.
Recompute independently, diff, classify. No new statutory logic.

**Step 2 — agent wrapper + rule-match logging (1 day).**

**Step 3 — challan recording + reconciliation (2–3 days).** `tds_challans`,
deducted-vs-deposited, 201(1A) interest.

**Step 4 — 26Q assembly (4–5 days).** Deductee grouping, challan mapping,
validation. Draft only; no portal integration.

**Step 5 — correction proposals into Payable (2 days).** An accepted correction
becomes a deduction in the payable amount.

**Step 6 — 27Q / non-resident (deferred).** Different rules; scope separately.

**Total ≈ 10–14 days** for steps 1–5. Step 1 alone justifies the effort and can
ship independently.

## 12. KPIs

| KPI | Target |
|---|---|
| Under-deductions caught before filing | ≥ 95% |
| TDS computation accuracy vs manual audit | ≥ 99.5% |
| Return preparation time | days → < 1 h review |
| 201(1A) interest paid | → ₹0 |
| Late-deposit incidents | 0 |
| Unmapped bills (no section resolved) | < 2% |

## 13. Open questions

1. **Does return filing integrate with a portal/utility, or export a FVU file for a
   CA?** Export-only is the honest scope now; portal filing is a separate project.
2. **Where do challans come from** — manual entry, bank statement parsing, or a
   TRACES pull? Manual entry is fine to start, but it is the weak link in
   reconciliation.
3. **Do we handle lower-deduction certificates (197)?** Common enough that ignoring
   them produces false over-deduction exceptions.
4. **26AS / TRACES matching** — is reconciling our deductions to the vendor's 26AS in
   scope? It is the strongest correctness signal available and entirely new work.
5. **Who accepts a correction proposal** — the AP clerk who raised the bill, or a
   finance controller? It changes a payable amount, so probably the latter.
