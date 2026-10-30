# Recon Agent

| | |
|---|---|
| **Agent name** | `recon` |
| **Fleet card** | "Invoice lines vs ERP usage → discrepancy set." |
| **Priority** | 🔴 **P0** · 🔥 **HARD** · ⭐ **IMP** — the centerpiece |
| **Status** | 🟡 Partial — Nova reconciliation is built and in production; PO matching and bank recon are not |
| **Default autonomy** | **L1** — surface findings, never adjust a bill |
| **Difficulty** | Hard — matching is genuinely ambiguous and the blast radius of a wrong match is a wrong payment |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.2 |
| **Related** | [PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md](../PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md), [PRD_NOVA_RECONCILIATION_AT_APPROVAL.md](../PRD_NOVA_RECONCILIATION_AT_APPROVAL.md), [PRD_NOVA_BILLING_DISCREPANCY_ROOT_CAUSE.md](../PRD_NOVA_BILLING_DISCREPANCY_ROOT_CAUSE.md) |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Answer: **does this invoice match what we actually consumed, ordered, and paid?**

Reconciliation exists today for one shape of that question — Nova platform usage
vs vendor invoice lines, in [`billing_api.py`](../../backend_ocr/app/billing_api.py)
and [`billing_reconciliation.py`](../../pipelines/billing_reconciliation.py). It
already does the hard parts: month snapshotting, module-alias resolution, LLM line
matching with a numeric guardrail, and an advisory note.

The agent generalizes that into **four recon modes over one discrepancy model**:

| Mode | Compares | Status |
|---|---|---|
| **Usage recon** | Invoice lines ↔ Nova usage snapshot | ✅ built |
| **ERP recon** | Invoice lines ↔ Tally ledger/purchase entries | 🟡 connector built, comparison new |
| **PO recon** | Invoice ↔ purchase order (2-way and 3-way with GRN) | ❌ new — **no PO table exists yet** |
| **Bank recon** | Disbursement ↔ bank statement line | ❌ new |

This is the agent that makes the fleet worth having, and the one most likely to be
wrong in a way that costs money. Hence L1 by default: it produces findings, a human
decides.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Bill ingested | `event` | `bill.ingested` — recon before it enters the approval chain, so the approver sees findings |
| Bill approval requested | `event` | `bill.approval_requested` — the pattern in [PRD_NOVA_RECONCILIATION_AT_APPROVAL.md](../PRD_NOVA_RECONCILIATION_AT_APPROVAL.md) |
| User action | `user` | "Reconcile" on the bill detail page (`?refresh=true` bypasses cache) |
| Month close | `schedule` | Vendor + period sweep at month end |
| Delegated | `agent` | CFO orchestrator, as step 1 of "close this vendor's month" |
| Payment settled | `event` | `disbursement.succeeded` → bank recon mode |

## 3. Inputs

```json
{
  "mode": "usage|erp|po|bank|auto",   // auto = pick by vendor config + available data
  "bill_id": "uuid",                  // one of bill_id …
  "vendor_id": "uuid", "period": "2026-06",   // … or vendor + period
  "refresh": false,                   // ignore the cached recon result
  "tolerance_pct": 2.0                // optional override of the tenant band
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `pull_nova_usage(vendor, month)` | **reuse** | `billing_api._fetch_nova_snapshot_sync` → `billing_nova_usage_snapshots` / `_lines` |
| `pull_erp_usage(vendor, period)` | **wrap** | [`tally_agent_api.py`](../../backend_ocr/app/tally_agent_api.py) + [`tally_read.py`](../../pipelines/tally_read.py) — pairing-token auth, XML ingest |
| `run_recon(invoice_id)` | **reuse** | `billing_api.reconcile_bill_view` / `POST /api/billing/reconcile`; core in [`utils/billing_recon_core.py`](../../utils/billing_recon_core.py) |
| `match_line(description, candidates)` | **reuse** | `billing_api._llm_match_line` — **already wrapped in `_numeric_guardrail`**, which is the pattern the whole fleet should copy: the LLM proposes, arithmetic vetoes |
| `list_discrepancies(filters)` | **reuse** | `GET /api/billing/discrepancies` |
| `resolve_discrepancy(id, status)` | **reuse** | `PATCH /api/billing/discrepancies/{id}` |
| `match_po(invoice_id)` | **new** | 2-way (invoice↔PO) and 3-way (invoice↔PO↔GRN). Blocked on a PO data model |
| `bank_recon(period)` | **new** | Match `fact_disbursements` against bank statement lines; needs a statement import path |
| `write_recon_note(findings)` | **reuse** | `billing_api._agent_recon_note` — LLM advisory prose, already redacted via `_redact` |

## 5. Steps

1. **Resolve scope** — bill, or vendor+period → bill set. Resolve the vendor
   account (`billing_api._resolve_vendor_account` handles name/GSTIN/supplier_id
   ambiguity; do not reinvent it).
2. **Select mode.** `auto` picks by what data exists: Nova account → usage; Tally
   connection → ERP; PO present → PO; else line-arithmetic-only.
3. **Pull the source of truth** into a snapshot. Snapshot, not live query — recon
   must be reproducible after the fact, which is the entire point of the audit.
4. **Deterministic pass first.** Exact matches on module alias, quantity, rate,
   amount. Anything that matches arithmetically never reaches the LLM.
5. **LLM pass on the residue only.** Unmatched descriptions → `match_line` with
   candidates → **`_numeric_guardrail` vetoes any pick whose numbers don't
   corroborate.** A vetoed pick becomes an unmatched line, not a silent match.
6. **Classify each discrepancy** by type and severity (§6), using tolerance bands
   from tenant config — deterministically.
7. **Root-cause annotation** — per
   [PRD_NOVA_BILLING_DISCREPANCY_ROOT_CAUSE.md](../PRD_NOVA_BILLING_DISCREPANCY_ROOT_CAUSE.md),
   group discrepancies into a probable cause (alias drift, mid-month plan change,
   proration, genuine overbill).
8. **Cache** the result (`_store_recon_result`) and write the advisory note.
9. **Emit outcome.** No bill is edited, no amount changed, nothing approved.

## 6. Outcome schema

```json
{
  "mode": "usage",
  "bill_id": "…", "vendor": "Signzy", "period": "2026-06",
  "invoice_total": 412500.00,
  "source_total":  398000.00,
  "variance": 14500.00, "variance_pct": 3.64,
  "matched_lines": 18, "unmatched_lines": 2,
  "within_tolerance": false,
  "discrepancies": [
    {
      "discrepancy_id": "…",
      "type": "QTY_MISMATCH",
      "severity": "HIGH",
      "invoice_line": {"description": "eKYC verifications", "qty": 12000, "rate": 12.5, "amount": 150000},
      "source_line":  {"module": "ekyc", "qty": 10840, "amount": 135500},
      "delta": 14500.00,
      "probable_cause": "Invoice billed provisioned seats; Nova reports actual calls"
    }
  ],
  "severity": "HIGH",
  "recommended_action": "hold_for_vendor_query",
  "agent_note": "Two of twenty lines diverge …"
}
```

**Discrepancy types:** `QTY_MISMATCH` · `RATE_MISMATCH` · `AMOUNT_MISMATCH` ·
`MISSING_IN_SOURCE` (billed, not consumed — the expensive one) ·
`MISSING_IN_INVOICE` (consumed, not billed) · `DUPLICATE_LINE` ·
`TAX_MISMATCH` · `PERIOD_MISMATCH` · `UNMATCHED`.

**Severity** is deterministic: by absolute delta against tenant bands **and** by
type — any `MISSING_IN_SOURCE` is at least MEDIUM regardless of value, because it
is the signature of being billed for nothing.

**`recommended_action`:** `clean_to_pay` · `pay_partial` · `hold_for_vendor_query`
· `escalate`. Advisory. Payable reads it; a human decides it.

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: event`, `trigger_ref: bill.approval_requested`, `reason: "Reconcile before approver sign-off"` |
| **WHOM** | `entity_type: bill`, `entity_id: <invoice_id>` (+ vendor in outcome) |
| **WHAT** | `action: "Usage recon: 18/20 matched, ₹14,500 variance (3.64%), HIGH"`, `outcome:` above |
| **HOW** | `tools_used: [pull_nova_usage, run_recon, match_line, write_recon_note]`; `steps[]` records **every LLM match attempt including guardrail vetoes** |

Logging the vetoes is not optional. When someone asks in three months why a line
was flagged, "the model proposed X and arithmetic rejected it" is the answer that
matters.

## 8. Guardrails

- **Read-only.** Recon never edits an invoice line, changes a total, or advances an
  approval. It writes only to its own recon/discrepancy tables.
- **Arithmetic outranks the LLM, always.** `_numeric_guardrail` is load-bearing.
  Any new matching path must pass through an equivalent check.
- **Snapshot immutability.** A completed recon run's inputs are frozen; `refresh`
  creates a *new* run rather than mutating the old one.
- **Tolerance bands are config, not model output.**
- **No cross-tenant reads** — every billing table is `tenant_id`-scoped and the
  vendor-name canonicalization helpers must stay inside that scope.
- **Cost control.** LLM matching only on residual lines; cache by
  `(invoice_id, source_snapshot_hash)` so re-running a clean bill is free.

## 9. Data model

**Reuses:** `billing_vendor_accounts`, `billing_vendor_invoices`,
`billing_vendor_invoice_lines`, `billing_nova_usage_snapshots`,
`billing_nova_usage_lines`, `billing_reconciliation_runs`,
`billing_reconciliation_discrepancies`, `billing_vendor_module_alias`,
`fact_invoices`, `stg_bills`, `stg_bill_line_items`.

`billing_reconciliation_runs` is the precedent `agent_runs` generalizes — link
them by writing `agent_runs.run_id` onto the billing run rather than duplicating
findings.

**New, for PO recon** (the real blocker — there is no purchase-order table today):

```sql
CREATE TABLE IF NOT EXISTS fact_purchase_orders (
    po_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL,
    supplier_id  uuid        NOT NULL,
    po_number    text        NOT NULL,
    po_date      date,
    currency     text        NOT NULL DEFAULT 'INR',
    total_amount numeric(18,2),
    status       text        NOT NULL DEFAULT 'OPEN',   -- OPEN|PARTIAL|CLOSED|CANCELLED
    source       text,                                   -- tally|manual|erp
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, supplier_id, po_number)
);

CREATE TABLE IF NOT EXISTS fact_po_line_items (
    po_line_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id       uuid        NOT NULL REFERENCES fact_purchase_orders(po_id),
    tenant_id   uuid        NOT NULL,
    description text,
    hsn_code    text,
    qty         numeric(18,4),
    rate        numeric(18,4),
    amount      numeric(18,2),
    qty_received numeric(18,4) NOT NULL DEFAULT 0,   -- GRN, for 3-way match
    qty_billed   numeric(18,4) NOT NULL DEFAULT 0    -- guards over-billing across invoices
);
```

`qty_billed` is what makes over-billing across *multiple* invoices detectable —
without it, each invoice looks fine in isolation.

## 10. API

Existing (keep): `POST /api/billing/reconcile`, `GET /api/billing/runs`,
`GET /api/billing/runs/{run_id}`, `GET /api/billing/bill/{invoice_id}/reconcile`,
`GET /api/billing/discrepancies`, `PATCH /api/billing/discrepancies/{id}`.

New: `POST /api/agents/recon/run` (mode-aware), and
`GET /api/agents/recon/coverage` — which vendors/periods have never been
reconciled, i.e. where the blind spots are.

## 11. Build plan

**Step 1 — wrap what exists (1–2 days).** Register `recon`, route usage mode
through the existing `billing_api` path, write the `agent_runs` row with real
step logging. Zero new finance logic; immediate audit value.

**Step 2 — unify the discrepancy model (2–3 days).** Normalize types/severity
across modes; make severity deterministic and configurable.

**Step 3 — ERP recon (3–5 days).** Compare invoice lines to Tally purchase
entries via the existing connector. Highest-value new mode: it works for *every*
vendor, not just Nova.

**Step 4 — PO model + 2-way match (5+ days).** Tables above, Tally PO import,
then invoice↔PO. Ship 2-way before 3-way.

**Step 5 — 3-way + bank recon (5+ days).** Needs GRN capture and bank statement
import. Both are data-acquisition problems more than matching problems — scope
them separately before committing.

**Step 6 — root-cause grouping (2 days).** Per the root-cause PRD.

**Note on sequencing:** steps 4–5 are each larger than most other agents in the
fleet. Recon is P0 for steps 1–3; PO and bank recon should be planned as their own
efforts rather than hidden inside "build the Recon agent".

## 12. KPIs

| KPI | Target |
|---|---|
| Discrepancy precision (flagged and real) | ≥ 90% |
| Discrepancy recall vs manual audit sample | ≥ 85% |
| Manual recon touches per bill | −50% vs baseline |
| Recon latency p50 (usage mode, cached source) | < 10 s |
| LLM match veto rate | tracked, not targeted — a rising rate means alias drift |
| Bills paid without any recon | → 0 |

## 13. Open questions

1. **Where do POs come from?** Tally import, manual entry, or a customer ERP feed?
   This decides whether PO recon is weeks or months of work. Nothing else in step 4
   can be estimated until it is answered.
2. **Tolerance ownership** — global default, per-vendor, or per-category? Per-vendor
   is most useful and most work to administer.
3. **Does a HIGH severity finding *block* the approval chain, or annotate it?**
   Annotating is the safe start; blocking is what actually prevents overpayment.
4. **Bank statement source** — manual CSV upload, or a bank API? Determines whether
   bank recon is realistic this year.
5. **Should recon re-run automatically when a vendor's module aliases change?**
   Alias drift silently invalidates past reconciliations.
