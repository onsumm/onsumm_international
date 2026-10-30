# Receivable Agent

| | |
|---|---|
| **Agent name** | `receivable` |
| **Fleet card** | "Tracks AR, chases overdue invoices." |
| **Priority** | 🟡 **P2** |
| **Status** | ❌ Mostly new — customer tables and sales invoice staging exist; AR tracking, dunning and receipt matching do not |
| **Default autonomy** | **L1** — draft follow-ups, send nothing unattended |
| **Difficulty** | Medium — the least-built domain in the platform |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.7 |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Get paid faster, with fewer awkward emails written by hand.

This is the mirror of [`payable`](PAYABLE_AGENT.md), and the platform's thinnest
domain. `dim_customers`, `dim_end_customers`, `dim_end_customer_contacts`,
`stg_invoices` and `stg_invoice_line_items` exist; `ai_payment_predictions` and
`payment_schedules` hint at prior intent. What does not exist: an AR ledger, an
ageing view, a dunning ladder, or receipt-to-invoice matching.

The agent's real output is **a prioritized collections queue with a drafted message
for each item** — not automated nagging. Collections is a relationship activity;
the agent removes the clerical part and leaves the judgement.

**Scope honesty:** because so little exists beneath it, this agent is closer to
"build the AR module" than "wrap existing capability". It is correctly last in the
build order.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Invoice issued | `event` | `invoice.issued` → create the AR record, compute the due date from customer terms |
| Due-date approach | `schedule` | Daily: T−7, T−1 gentle reminders |
| Overdue | `schedule` | Daily: escalate through the dunning ladder |
| Receipt detected | `event` | `payment.received` (bank credit / manual entry) → match and settle |
| User action | `user` | "Chase this invoice", or generate a statement of account |
| Month close | `schedule` | AR ageing + DSO for [`analytics`](ANALYTICS_AGENT.md) |

## 3. Inputs

```json
{
  "mode": "track|dun|match_receipt|statement",
  "invoice_ids": ["uuid"],
  "customer_id": "uuid",
  "as_of": "2026-08-06",
  "receipt": {"amount": 250000, "date": "2026-08-05", "reference": "UTR…", "narration": "…"}
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `create_ar_record(invoice)` | **new** | From `stg_invoices` / `fact_invoices` → AR ledger with due date |
| `compute_due_date(invoice, terms)` | **new** | Customer payment terms; contracted terms via [`contract`](CONTRACT_AGENT.md) where available |
| `get_ageing(as_of, scope)` | **new** | Buckets 0–30 / 31–60 / 61–90 / 90+; shared with [`analytics`](ANALYTICS_AGENT.md) |
| `rank_collections(candidates)` | **new** | Deterministic prioritization (§6) |
| `draft_dunning(invoice, stage)` | **new** | LLM drafts within a fixed template per ladder stage |
| `send_followup(...)` | **reuse** | Via [`communication`](COMMUNICATION_AGENT.md) — gated, capped, logged |
| `match_receipt(receipt, open_invoices)` | **new** | Exact reference match → amount+customer match → partial allocation. Arithmetic, not LLM |
| `apply_receipt(invoice_id, amount)` | **new** | Settle full or partial; writes the AR ledger |
| `generate_statement(customer_id)` | **new** | Statement of account, all open items |
| `predict_payment_date(invoice)` | **wrap** | `ai_payment_predictions` exists — verify whether it is populated before relying on it |

## 5. Steps

**Dun mode (the core loop):**

1. **Refresh AR state** — open invoices, part-payments, credit notes, disputes.
2. **Compute ageing** as of the run date.
3. **Filter out the un-chaseable**: disputed items, agreed payment plans,
   suppression-listed contacts, invoices under a minimum value. Chasing a disputed
   invoice damages the relationship the collection depends on.
4. **Rank** the remainder deterministically (§6).
5. **Determine the ladder stage** per invoice from days overdue and prior contacts —
   the ladder never skips or repeats a stage.
6. **Draft a message per item** in the stage's template: correct contact, invoice
   reference, amount, days overdue, payment details.
7. **Gate (L1).** The queue is presented with drafts; the user sends, edits, or skips.
   At L2 stages 1–2 auto-send and stages 3+ still gate.
8. **Log** every contact to the outreach log — the ladder depends on accurate history.

**Match-receipt mode:** exact UTR/reference match → single-invoice amount match →
propose an allocation for anything ambiguous. **Never auto-allocates an ambiguous
receipt** — a wrongly applied receipt is discovered weeks later and is painful to
unwind.

## 6. Outcome schema

```json
{
  "mode": "dun",
  "as_of": "2026-08-06",
  "ageing": {"b0_30": 4200000, "b31_60": 1300000, "b61_90": 420000, "b90_plus": 180000,
             "total_open": 6100000, "dso_days": 47.3},
  "queue": [
    {
      "rank": 1,
      "invoice_id": "…", "customer": "Northwind Retail",
      "invoice_no": "SI-2026-0412", "amount_inr": 380000, "outstanding_inr": 380000,
      "due_date": "2026-07-07", "days_overdue": 30,
      "stage": 3,
      "contact": {"name": "…", "email": "…"},
      "prior_contacts": [{"stage": 1, "sent_at": "2026-07-08"},
                         {"stage": 2, "sent_at": "2026-07-18"}],
      "predicted_payment_date": "2026-08-19",
      "draft": {"subject": "…", "body": "…"},
      "reason": "₹3.8L, 30 days overdue, two prior reminders unanswered"
    }
  ],
  "excluded": [{"invoice_id": "…", "reason": "disputed — credit note pending"}],
  "totals": {"queued": 18, "value_inr": 2100000, "excluded": 4}
}
```

**Ranking (deterministic, config-weighted):** amount outstanding × days overdue,
then ladder stage (later stages first), then customer payment history, then
promise-to-pay date passed. Excluded items are always *listed* with a reason — a
silently dropped invoice is an invoice nobody chases.

**Dunning ladder:**

| Stage | Timing | Tone |
|---|---|---|
| 0 | T−7 | Courtesy notice, invoice attached |
| 1 | T+1 | Gentle reminder |
| 2 | T+10 | Firm, statement attached |
| 3 | T+30 | Escalation, copy the account owner |
| 4 | T+60 | Final notice before escalation — **always gated regardless of autonomy** |

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: schedule`, `trigger_ref: ar_daily_dun`, `reason: "Daily collections sweep"` |
| **WHOM** | `entity_type: customer` / `invoice` |
| **WHAT** | `action: "18 invoices queued (₹21L); 12 stage-1, 4 stage-2, 2 stage-3"`, `outcome:` above |
| **HOW** | `tools_used: [get_ageing, rank_collections, draft_dunning, send_followup]`; steps log ladder-stage decisions and exclusions with reasons |

## 8. Guardrails

- **Send caps and suppression** enforced in [`communication`](COMMUNICATION_AGENT.md):
  max one dunning email per customer per `config.min_days_between` (default 7), and
  never to a suppressed contact.
- **Disputed and payment-plan invoices are never chased** automatically.
- **Stage 4 always gates**, at any autonomy level. A final notice has legal and
  commercial weight.
- **Drafts are templated.** The LLM fills a fixed structure; it does not compose
  free-form demands, invent amounts, or make threats. Every figure comes from the AR
  record.
- **No ambiguous receipt auto-allocation.**
- **Receipt matching is arithmetic.** The LLM may parse a narration string; it never
  decides an allocation.
- **Customer-facing tone is a business decision** — templates are tenant-editable,
  not model-invented.
- **PII** — customer contact details stay out of the audit store beyond what is
  needed to identify the contact.

## 9. Data model

**Reuses:** `dim_customers`, `dim_end_customers`, `dim_end_customer_contacts`,
`stg_invoices`, `stg_invoice_line_items`, `fact_invoices`, `payment_schedules`,
`ai_payment_predictions`, `vendor_outreach_log` (generalize to counterparties).

**New:**

```sql
CREATE TABLE IF NOT EXISTS fact_receivables (
    ar_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL,
    customer_id    uuid        NOT NULL,
    invoice_id     uuid,
    invoice_no     text        NOT NULL,
    invoice_date   date        NOT NULL,
    due_date       date        NOT NULL,
    currency       text        NOT NULL DEFAULT 'INR',
    amount         numeric(18,2) NOT NULL,
    outstanding    numeric(18,2) NOT NULL,
    status         text        NOT NULL DEFAULT 'OPEN',  -- OPEN|PART_PAID|PAID|DISPUTED|WRITTEN_OFF
    dispute_note   text,
    promise_date   date,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, invoice_no)
);
CREATE INDEX IF NOT EXISTS idx_ar_open
    ON fact_receivables (tenant_id, due_date) WHERE status IN ('OPEN','PART_PAID');

CREATE TABLE IF NOT EXISTS fact_receipts (
    receipt_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    customer_id uuid,
    receipt_date date       NOT NULL,
    amount      numeric(18,2) NOT NULL,
    reference   text,                              -- UTR / cheque no
    narration   text,
    unallocated numeric(18,2) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, reference, receipt_date, amount)
);

CREATE TABLE IF NOT EXISTS fact_receipt_allocations (
    allocation_id uuid       PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid       NOT NULL,
    receipt_id    uuid       NOT NULL REFERENCES fact_receipts(receipt_id),
    ar_id         uuid       NOT NULL REFERENCES fact_receivables(ar_id),
    amount        numeric(18,2) NOT NULL,
    run_id        uuid       REFERENCES agent_runs(run_id),
    allocated_by  text,                            -- agent|user
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ar_dunning_log (
    dunning_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    ar_id       uuid        NOT NULL REFERENCES fact_receivables(ar_id),
    run_id      uuid        REFERENCES agent_runs(run_id),
    stage       smallint    NOT NULL,
    contact_id  uuid,
    sent_at     timestamptz,
    message_ref text,
    response    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
```

Separating receipts from allocations is what makes partial payments and
over-payments representable — a single `paid_amount` column on the invoice cannot.

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/receivable/run` | track / dun / match_receipt / statement |
| `GET` | `/api/receivables` | Open AR with ageing, filterable |
| `GET` | `/api/receivables/ageing` | Bucket summary + DSO |
| `GET` | `/api/agents/receivable/queue` | Ranked collections queue with drafts |
| `POST` | `/api/receipts` | Record a receipt |
| `POST` | `/api/receipts/{id}/allocate` | Confirm/adjust an allocation |
| `PATCH` | `/api/receivables/{ar_id}` | Mark disputed, set promise date, write off |
| `GET` | `/api/customers/{id}/statement` | Statement of account |

## 11. Build plan

**Step 1 — AR ledger (3 days).** `fact_receivables`, creation from issued invoices,
due-date computation from customer terms. Nothing else works without it.

**Step 2 — ageing + DSO (1–2 days).** Feeds [`analytics`](ANALYTICS_AGENT.md)
immediately, before any automation exists.

**Step 3 — receipts + allocation (3 days).** `fact_receipts`,
`fact_receipt_allocations`, exact and amount matching, manual allocation UI.
Correctness here precedes chasing — chasing an already-paid invoice is worse than
not chasing.

**Step 4 — ranking + queue (2 days).**

**Step 5 — dunning ladder + drafts (3 days).** Templates, stage logic,
`ar_dunning_log`. Depends on [`communication`](COMMUNICATION_AGENT.md).

**Step 6 — statements + disputes (2 days).**

**Step 7 — payment prediction (deferred).** Only after there is enough settled
history to learn from.

**Total ≈ 14–17 days.** Steps 1–3 are an AR module, not an agent — worth recognizing
explicitly when planning, because the agent value only starts at step 4.

## 12. KPIs

| KPI | Target |
|---|---|
| DSO reduction | −15% in 6 months |
| Invoices overdue > 60 days | −40% |
| Collections emails sent manually | → near 0 |
| Receipts auto-matched correctly | ≥ 85% |
| Mis-allocated receipts | 0 |
| Disputed invoices chased in error | 0 |

## 13. Open questions

1. **Does the platform actually issue sales invoices today, or is AR imported from
   Tally?** This determines whether step 1 is a creation hook or an import pipeline —
   and it changes the whole estimate.
2. **Where do receipts come from** — bank statement import, Tally, or manual? Same
   dependency as bank recon in [`recon`](RECON_AGENT.md); worth solving once for both.
3. **Who owns collections tone?** Templates need a business owner, or the ladder will
   be disabled after the first complaint.
4. **Customer contacts vs vendor contacts** — reuse `supplier_contacts` patterns or
   build a parallel structure? `dim_end_customer_contacts` exists but its coverage is
   unverified.
5. **Multi-currency AR** — is FX revaluation of open AR in scope? [`fx_api`](../../backend_ocr/app/fx_api.py)
   has the machinery, but nothing applies it to receivables.
6. **Is this genuinely wanted in this phase?** The platform is AP-first. Building AR
   because the fleet board has a card for it is the wrong reason; confirm demand
   before spending the ~3 weeks.
