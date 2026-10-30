# Contract / Document Agent

| | |
|---|---|
| **Agent name** | `contract` |
| **Fleet card** | "Parses contracts, extracts and links terms." |
| **Priority** | 🟠 **P1** · 🟦 MED · 🔧 Utility (feeds Recon and Payable) |
| **Status** | 🟡 Partial — invoice OCR is production-grade; contract parsing and term extraction are new |
| **Default autonomy** | **L1** — extract and link, never enforce |
| **Difficulty** | Medium — extraction is tractable; *trusting* extracted terms is the hard part |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.5 |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Turn vendor paperwork into structured terms the rest of the fleet can reason with:
**rates, payment terms, SLAs, renewal and escalation clauses**.

This closes a real gap. Recon can today tell you an invoice doesn't match usage. It
cannot tell you the invoice doesn't match **the contracted rate**, because the
contracted rate lives in a PDF nobody parsed. Payable can compute a due date from a
default term, not from the term actually agreed. The Contract agent supplies both.

It reuses the OCR stack in [`utils/ocr_engine.py`](../../utils/ocr_engine.py) —
GPT-4o vision, structured output, and an existing qty/rate/amount arithmetic
reconciliation that is the right precedent for validating anything a model extracts.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Document uploaded | `event` | `document.uploaded` with type contract/agreement/MSA/SOW/PO/rate card |
| Vendor onboarding | `event` | Contract attached during vendor creation |
| User action | `user` | "Extract terms" on a document in the vendor documents list |
| Delegated | `agent` | Recon asking for the contracted rate on a flagged line |
| Renewal sweep | `schedule` | Daily: contracts expiring or auto-renewing within N days |

## 3. Inputs

```json
{
  "document_id": "uuid",              // or a direct upload ref
  "doc_type": "contract|msa|sow|rate_card|po|amendment|auto",
  "vendor_id": "uuid",                // optional; inferred and confirmed if absent
  "link_to": {"bill_id": null, "po_id": null}
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `parse_document(file)` | **reuse** | [`ocr_engine.py`](../../utils/ocr_engine.py) — page split, vision extraction, structured JSON |
| `classify_document(text)` | **new** | contract vs SOW vs rate card vs amendment vs invoice. Cheap, `gpt-4o-mini` |
| `extract_terms(text, doc_type)` | **new** | Typed extraction per §6, each field carrying a page/quote citation |
| `validate_terms(terms)` | **new** | Deterministic checks: dates ordered, rates positive, currencies known, tier ranges non-overlapping |
| `resolve_vendor(text)` | **wrap** | Same name/GSTIN matching used by `billing_api._resolve_vendor_account` |
| `persist_document(...)` | **wrap** | `vendor_documents` + stored file path (the pattern in [`ingest.py`](../../backend_ocr/app/ingest.py)) |
| `link_terms(...)` | **new** | Attach terms to vendor / bill / PO |
| `diff_amendment(old, new)` | **new** | An amendment supersedes specific clauses, not the whole contract |
| `get_contracted_rate(vendor_id, item, date)` | **new** | The lookup Recon calls. Date-aware — the rate as of the invoice period |

## 5. Steps

1. **Classify** the document. Wrong type → route (an invoice goes to the normal
   ingest path) rather than force contract extraction.
2. **Parse** to text/layout via the existing OCR engine.
3. **Resolve the vendor** — matched by name/GSTIN. Ambiguous → flag for confirmation
   rather than guess; a contract linked to the wrong vendor poisons every downstream
   rate check.
4. **Extract typed terms**, each with a **citation** (page + verbatim quote). An
   uncited term is dropped.
5. **Validate deterministically.** Invalid or contradictory fields are marked
   `needs_review` rather than discarded — a wrong effective date is more dangerous
   silently absent than visibly wrong.
6. **Amendment handling** — if it amends an existing contract, diff and supersede
   only the affected clauses, keeping both versions.
7. **Persist and link**, with the document stored and referenced.
8. **Confidence gate.** Below `config.min_confidence` (default 0.80), the term set
   is stored as `needs_review` and is **not visible to `get_contracted_rate`**.
   Recon must never silently reconcile against an unreviewed rate.
9. **Emit renewal/expiry events** for the sweep.

## 6. Outcome schema

```json
{
  "document_id": "…", "doc_type": "msa",
  "vendor_id": "…", "vendor_confidence": 0.98,
  "contract": {
    "contract_number": "MSA-2026-014",
    "effective_from": "2026-04-01",
    "effective_to": "2027-03-31",
    "auto_renew": true,
    "notice_period_days": 60,
    "currency": "INR",
    "governing_law": "India"
  },
  "payment_terms": {
    "net_days": 30,
    "early_payment_discount": {"percent": 2.0, "within_days": 10},
    "late_fee_percent_pm": 1.5
  },
  "rate_card": [
    {"item": "eKYC verification", "uom": "per call", "rate": 11.50,
     "tiers": [{"from": 0, "to": 100000, "rate": 11.50},
               {"from": 100001, "to": null, "rate": 9.75}],
     "citation": {"page": 7, "quote": "eKYC verification — ₹11.50 per call up to 1,00,000 …"}}
  ],
  "sla": [{"metric": "uptime", "target": "99.5%", "penalty": "5% of monthly fee", "citation": {"page": 9}}],
  "escalation_clause": {"annual_increase_percent": 5.0, "effective_month": "04"},
  "confidence": 0.87,
  "needs_review": false,
  "flags": []
}
```

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: event`, `trigger_ref: document.uploaded`, `reason: "New MSA uploaded for Signzy"` |
| **WHOM** | `entity_type: vendor`, `entity_id: <supplier_id>` (document id in outcome) |
| **WHAT** | `action: "Parsed MSA-2026-014: 12 rate lines, Net-30, 2/10 discount, auto-renew"`, `outcome:` above |
| **HOW** | `tools_used: [classify_document, parse_document, extract_terms, validate_terms, persist_document]`; steps log pages processed, model, tokens, per-field confidence |

Citations are the audit story for this agent. When Recon later flags an invoice for
breaching a contracted rate, the trail must reach a page and a quote — not "the
model said ₹11.50".

## 8. Guardrails

- **Extraction never enforces.** The agent records what the contract says. It does
  not block a bill, adjust an amount, or reject an invoice.
- **Citations mandatory.** No citation → field dropped. This is the main defense
  against fabricated terms.
- **Deterministic validation** over every extracted number and date.
- **Confidence gating** — unreviewed terms are invisible to consumers.
- **Amendments supersede, never overwrite.** Contract history is auditable.
- **PII/confidentiality** — contracts are sensitive commercial documents. Store
  citations as short quotes, not full clause dumps, in the audit store; keep files
  under existing tenant-scoped storage rules.
- **Cost control** — page cap per document (default 40) and a re-parse cache keyed on
  file hash, so re-uploads are free.
- **No auto-linking to bills** on a low-confidence vendor match.

## 9. Data model

**Reuses:** `vendor_documents`, `raw_invoice_uploads`, `dim_suppliers`, `stg_bills`,
`stg_bill_line_items`, and `fact_purchase_orders` once
[`recon`](RECON_AGENT.md) introduces it.

**New:**

```sql
CREATE TABLE IF NOT EXISTS vendor_contracts (
    contract_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid        NOT NULL,
    supplier_id     uuid        NOT NULL,
    document_id     uuid,                        -- vendor_documents
    run_id          uuid        REFERENCES agent_runs(run_id),
    contract_number text,
    doc_type        text        NOT NULL,        -- msa|sow|rate_card|amendment
    amends_id       uuid        REFERENCES vendor_contracts(contract_id),
    effective_from  date,
    effective_to    date,
    auto_renew      boolean     NOT NULL DEFAULT false,
    currency        text        NOT NULL DEFAULT 'INR',
    terms           jsonb       NOT NULL,        -- payment terms, SLA, escalation
    confidence      numeric(4,3),
    needs_review    boolean     NOT NULL DEFAULT true,
    is_current      boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vendor_contract_rates (
    rate_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id uuid        NOT NULL REFERENCES vendor_contracts(contract_id),
    tenant_id   uuid        NOT NULL,
    item        text        NOT NULL,
    normalized_item text,                        -- joins to billing_vendor_module_alias
    uom         text,
    rate        numeric(18,4) NOT NULL,
    tier_from   numeric(18,4),
    tier_to     numeric(18,4),
    citation    jsonb,
    CHECK (rate >= 0)
);
CREATE INDEX IF NOT EXISTS idx_contract_rates_lookup
    ON vendor_contract_rates (tenant_id, normalized_item);
```

`normalized_item` is what lets contracted rates join to the alias table Recon
already maintains — without it, rate checking degrades into fuzzy string matching a
second time.

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/contract/run` | Parse + extract for a document |
| `GET` | `/api/vendors/{supplier_id}/contracts` | Contracts + current terms |
| `GET` | `/api/vendors/{supplier_id}/contracts/{id}` | Full terms, citations, amendment chain |
| `PATCH` | `/api/vendors/{supplier_id}/contracts/{id}` | Human correction; clears `needs_review` |
| `GET` | `/api/vendors/{supplier_id}/contracted-rate` | `?item=&as_of=` — the Recon lookup |
| `GET` | `/api/agents/contract/renewals` | Expiring / auto-renewing within N days |

## 11. Build plan

**Step 1 — classification + persistence (2 days).** Classify, store, link to vendor.
No term extraction. Immediately useful: vendor documents become organized.

**Step 2 — payment terms extraction (2 days).** Net days, discount, late fee. Small
field set, high value — Payable can use it right away for due dates and discounts.

**Step 3 — rate card extraction + citations (3–4 days).** The hard part: tables
across pages, tiers, UOM normalization. Citations mandatory from the start.

**Step 4 — validation + confidence gate (2 days).**

**Step 5 — `get_contracted_rate` + Recon integration (2 days).** Rate-vs-contract
becomes a new discrepancy type in [`recon`](RECON_AGENT.md).

**Step 6 — amendments + renewal sweep (2–3 days).**

**Total ≈ 11–15 days.** Steps 1–2 stand alone and are worth shipping early even if
rate extraction is deferred.

## 12. KPIs

| KPI | Target |
|---|---|
| Payment-term extraction accuracy | ≥ 95% |
| Rate-line extraction accuracy | ≥ 90% |
| Fields without citation reaching storage | 0 |
| Contracts requiring human correction | < 25% |
| Rate-vs-contract discrepancies found by Recon | tracked — this is the agent's payoff |
| Cost per contract parsed | < $0.15 |

## 13. Open questions

1. **Is a rate card the same object as a contract?** Rate cards change more often
   than MSAs and often arrive as spreadsheets. They may deserve their own
   lighter-weight path rather than full contract extraction.
2. **How are contract items reconciled to billing modules?** `normalized_item` needs
   a mapping strategy — reuse `billing_vendor_module_alias`, or a new mapping UI?
3. **Multi-currency contracts** — does FX conversion happen at extraction or at
   comparison? Comparison-time is more correct, more work.
4. **Who reviews `needs_review` terms?** Without an owner, the gate becomes a queue
   nobody drains and the agent's output is never trusted.
5. **Do we parse customer-side contracts too** (for [`receivable`](RECEIVABLE_AGENT.md)),
   or vendor-only in this phase?
