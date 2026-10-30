# Tax & TDS Agent

**Stage:** 03 · Agent hub — reasoning & judgment (accounting)
**Role:** FY-aware TDS threshold tracking, GST computation, COA mapping

## What it does

Applies **TDS at the FY level**: it tracks cumulative billed amount per supplier per TDS section and knows the exact bill on which the statutory threshold was crossed — triggering catch-up deduction on that bill. GST is computed per line, and each bill is auto-mapped to the right ledger account via COA rules before any ERP sync happens.

## Capabilities

- FY state tracked per supplier/section: `threshold_crossed_on_bill_id`, `catchup_pending`
- `tax_type` enum: `GST` / `IGST` / `CGST` / `SGST` / `CESS`
- AI-assisted chart-of-accounts (COA) mapping — bills auto-map to the right ledger without manual rule-writing for every vendor

## Data model

### `vendor_tds_fy_state`
`cumulative_billed`, `threshold_crossed`, `catchup_*`. Per-supplier, per-section, per-financial-year running state — this is what makes threshold-crossing detection possible without re-scanning every bill for the year on every new invoice.

### `fact_tds_ledger`
Per-bill TDS postings — the actual deduction applied to each bill, once threshold logic has run.

### `coa_rules`
Ledger mapping rules — vendor/category → expense head, used to auto-map a bill to the correct chart-of-accounts entry.

## Where it fits in the pipeline

**Upstream:** runs on the clean (non-disputed) portion of a bill, after [reconciliation](./reconciliation-agent.md) and [anomaly/risk](./anomaly-risk-agent.md) have had their say.
**Downstream:** feeds the [Workflow engine](../04-workflow-hitl/workflow-engine.md) (an approval task is created with the correct tax already applied) and, after payment, the [ERP write-back](../05-disburse-post-learn/erp-writeback.md) stage, which needs the COA mapping to post correctly.

## Why it matters

TDS threshold logic is genuinely stateful and easy to get wrong by hand — a supplier's cumulative billed amount for a section can cross the statutory threshold mid-year on an otherwise ordinary bill, and that specific bill needs catch-up deduction applied retroactively within itself. Tracking `threshold_crossed_on_bill_id` explicitly, rather than recomputing from full bill history every time, makes this auditable: you can point to the exact bill where the threshold was crossed, not just trust a computed total.
