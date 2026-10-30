# GST Agent

| | |
|---|---|
| **Agent name** | `gst` |
| **Fleet card** | "Assembles returns, checks vendor filings." |
| **Priority** | 🟡 **P2** |
| **Status** | 🟡 Partial — GSTIN validation is built; filing-status checks, 2A/2B reconciliation and return assembly are designed but not built |
| **Default autonomy** | **L0 → L1** — verify and draft. Filing is never autonomous |
| **Difficulty** | Medium per capability, large in aggregate |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.9 |
| **Related** | [Sandbox_GST_COMPLIANCE_INTERGRATION.md](../Sandbox_GST_COMPLIANCE_INTERGRATION.md) — the integration contract this agent implements |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Protect input tax credit and keep GST filings defensible.

The concrete money problem: **ITC is only claimable if the vendor actually filed.**
Today nothing systematically checks that. A vendor can invoice with GST, get paid in
full, and never file — and the tax cost surfaces months later as a reversal with
interest. This agent makes that visible *before* payment.

[`pipelines/gst_verification.py`](../../pipelines/gst_verification.py) already
validates GSTINs against Sandbox. The design for the rest — filing-status tracking,
GSTR-2A/2B reconciliation job flow, `fact_gst_reconciliation` — is specified in
[Sandbox_GST_COMPLIANCE_INTERGRATION.md](../Sandbox_GST_COMPLIANCE_INTERGRATION.md)
§3–4 and §9–10. This agent is the orchestration and audit layer over that plan, in
its three phases.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Bill ingested with GST | `event` | Validate supplier GSTIN, check place-of-supply and tax arithmetic |
| Before payment | `event` | Delegated by [`payable`](PAYABLE_AGENT.md) — is the vendor filing current? |
| Monthly 2A/2B window | `schedule` | After the 14th — reconcile purchases against 2B |
| Return window | `schedule` | GSTR-3B / GSTR-1 assembly ahead of the 20th |
| Vendor onboarding | `agent` | Called by [`verification`](VERIFICATION_AGENT.md) for filing history |
| User action | `user` | "Check GST" for a vendor or period |

## 3. Inputs

```json
{
  "mode": "validate_gstin|check_filing|reconcile_2b|assemble_return",
  "gstin": "27AABCU9603R1ZM",
  "supplier_ids": ["uuid"],
  "period": "2026-07",
  "form": "GSTR-3B"
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `search_gstin(gstin)` | **reuse** | Sandbox public API — status, legal name, address, taxpayer type (integration doc §3.1) |
| `track_filing_status(gstin)` | **wrap** | Sandbox "track GST return filing status" (§3.2) — return-wise filing history. **The ITC-risk signal** |
| `search_gstin_by_pan(pan)` | **wrap** | §3.3 — finds all registrations for a PAN; catches vendors invoicing from an unregistered arm |
| `validate_bill_gst(bill)` | **new** | Deterministic: CGST/SGST vs IGST by place of supply, rate plausibility vs HSN, tax arithmetic vs line totals |
| `fetch_2b(period)` | **new** | GSTR-2B pull via the 4-step job flow (§4.1) — requires a GST portal session (§2.2) |
| `reconcile_2b(period)` | **new** | Purchase register vs 2B → matched / missing-in-2B / missing-in-books / value mismatch |
| `assemble_return(period, form)` | **new** | GSTR-3B / GSTR-1 draft from `fact_invoices` + purchase data |
| `compute_itc_at_risk(period)` | **new** | ITC on invoices from non-filing or unmatched vendors |
| `report_gst(period)` | **wrap** | Feeds [`analytics`](ANALYTICS_AGENT.md) |

**Auth note:** public GSTIN/filing lookups need only the Sandbox JWT
(`sandbox_kyb._get_token`). 2A/2B and taxpayer APIs additionally need a **GST portal
session with OTP** (§2.2) — a human-in-the-loop step the agent cannot perform alone.
That constraint shapes the whole build order below.

## 5. Steps

**Check-filing mode (highest value, lowest cost):**

1. Resolve GSTINs in scope (`supplier_gstins` / `supplier_gstns`).
2. Track filing status per GSTIN, respecting `int_connector_limits`.
3. Classify: `CURRENT` · `LAPSED_1` · `LAPSED_2_PLUS` · `CANCELLED` · `NOT_FOUND`.
4. Compute **ITC at risk** — invoice tax value from vendors not filing.
5. Emit flags; `LAPSED_2_PLUS` or `CANCELLED` also feeds the vendor's
   [verification](VERIFICATION_AGENT.md) band and gates payment.

**Reconcile-2B mode:**

1. Ensure a portal session (fail cleanly with "session required" — never block on it
   silently).
2. Submit the 2B job, poll, fetch (§4.1).
3. Match against the purchase register on `(supplier GSTIN, invoice no, date, taxable
   value, tax)` — exact first, then tolerance-based, then unmatched. Same discipline
   as [`recon`](RECON_AGENT.md): arithmetic decides.
4. Classify differences and compute claimable vs deferred ITC.
5. Persist to `fact_gst_reconciliation` (§4.3).

**Assemble-return mode:** aggregate outward/inward supplies → draft → validate →
**gate**. Draft only; no filing.

## 6. Outcome schema

```json
{
  "mode": "check_filing",
  "period": "2026-07",
  "vendors_checked": 128,
  "filing_status": [
    {"supplier": "Acme Ltd", "gstin": "27AABCU9603R1ZM",
     "status": "CURRENT", "last_filed": {"GSTR-1": "2026-06", "GSTR-3B": "2026-06"}},
    {"supplier": "Beta Services", "gstin": "…",
     "status": "LAPSED_2_PLUS", "last_filed": {"GSTR-3B": "2026-04"},
     "itc_at_risk_inr": 84600.00, "flag": "HOLD_PAYMENT"}
  ],
  "totals": {"itc_at_risk_inr": 132400.00, "vendors_at_risk": 3, "cancelled_gstins": 1},
  "reconciliation": {
    "period": "2026-07",
    "matched": 96, "missing_in_2b": 7, "missing_in_books": 3, "value_mismatch": 2,
    "itc_claimable_inr": 1840000.00, "itc_deferred_inr": 132400.00
  },
  "return_draft": null,
  "flags": ["VENDOR_FILING_LAPSED", "GSTIN_CANCELLED"]
}
```

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: agent`, `trigger_ref: payable:preflight`, `reason: "Confirm vendor filing current before releasing ₹8.4L"` |
| **WHOM** | `entity_type: vendor` (single) or `tenant` (period sweep) |
| **WHAT** | `action: "128 vendors checked; 3 lapsed; ₹1,32,400 ITC at risk"`, `outcome:` above |
| **HOW** | `tools_used: [track_filing_status, compute_itc_at_risk]`; steps log each GSTIN queried with its response digest and latency |

For an ITC reversal dispute, the audit answer must be "on this date the portal
reported the vendor had filed through June" — which requires storing the response
digest, not just the derived verdict.

## 8. Guardrails

- **No filing, ever, without a gate.** GSTR-3B is an irreversible statutory act.
- **No LLM in tax numbers.** Rates, ITC and matching are deterministic. LLM may
  narrate a reconciliation summary only.
- **Portal session is human-initiated.** OTP-backed sessions cannot be established
  autonomously; the agent detects absence and reports it rather than degrading
  silently.
- **Rate limits.** Sandbox is metered — batch GSTIN checks, cache filing status for
  `config.filing_cache_hours` (default 24), honor `int_connector_limits`.
- **Read-only on books.** The agent never edits a bill's tax fields; mismatches are
  findings.
- **Filing lapse gates payment, not blocks it** — with the ITC exposure in the gate
  payload, so the controller decides with the number in front of them.
- **Effective-dated rules** — GST rates change; validation uses the rate applicable
  at invoice date.

## 9. Data model

**Reuses:** `supplier_gstins`, `supplier_gstns`, `dim_suppliers` (+ the 3 new
columns in integration doc §9.1), `dim_end_customers` (§9.2), `fact_invoices`,
`stg_invoices`, `stg_bills`, `int_connector_limits` (§9.5).

**New** — `fact_gst_reconciliation` per integration doc §4.3, plus:

```sql
CREATE TABLE IF NOT EXISTS gst_filing_status (
    status_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid        NOT NULL,
    supplier_id   uuid,
    gstin         text        NOT NULL,
    run_id        uuid        REFERENCES agent_runs(run_id),
    status        text        NOT NULL,        -- CURRENT|LAPSED_1|LAPSED_2_PLUS|CANCELLED|NOT_FOUND
    last_filed    jsonb       NOT NULL DEFAULT '{}',   -- {"GSTR-1":"2026-06", …}
    itc_at_risk   numeric(18,2) NOT NULL DEFAULT 0,
    checked_at    timestamptz NOT NULL DEFAULT now(),
    is_current    boolean     NOT NULL DEFAULT true
);
CREATE INDEX IF NOT EXISTS idx_gst_filing_current
    ON gst_filing_status (tenant_id, gstin) WHERE is_current;

CREATE TABLE IF NOT EXISTS gst_return_drafts (
    draft_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    run_id      uuid        REFERENCES agent_runs(run_id),
    form        text        NOT NULL,          -- GSTR-1|GSTR-3B
    period      text        NOT NULL,          -- YYYY-MM
    payload     jsonb       NOT NULL,
    validation  jsonb       NOT NULL DEFAULT '[]',
    status      text        NOT NULL DEFAULT 'draft',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, form, period)
);
```

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/gst/run` | validate_gstin / check_filing / reconcile_2b / assemble_return |
| `GET` | `/api/agents/gst/filing-status` | Vendor filing status + ITC at risk |
| `GET` | `/api/agents/gst/reconciliation` | `?period=` 2B reconciliation result |
| `GET` | `/api/agents/gst/returns/{form}/{period}` | Return draft + validation |
| `POST` | `/api/agents/gst/portal-session` | Human-initiated portal session (OTP) |
| `GET` | `/api/agents/gst/itc-at-risk` | ITC exposure rollup — the number a CFO wants |

## 11. Build plan

Follows the phasing already set out in
[Sandbox_GST_COMPLIANCE_INTERGRATION.md](../Sandbox_GST_COMPLIANCE_INTERGRATION.md) §10.

**Phase 1 — GSTIN validation as an agent (2 days).** Wrap the existing worker with
audit. Low effort, immediate value.

**Phase 2 — filing status + ITC at risk (3–4 days).** `track_filing_status`,
`gst_filing_status`, exposure computation, Payable pre-flight integration. **This is
the highest-ROI item in the whole GST scope** — public API, no portal session, and it
prevents real tax loss. Consider pulling it forward out of P2.

**Phase 3 — bill GST validation (2 days).** Place-of-supply, CGST/SGST vs IGST, tax
arithmetic. Purely deterministic, no external calls.

**Phase 4 — 2A/2B reconciliation (5–8 days).** Portal session handling, job flow,
matching, `fact_gst_reconciliation`. High effort, gated on the session UX.

**Phase 5 — return assembly (5+ days).** GSTR-3B and GSTR-1 drafts.

**Phase 6 — e-invoice / EWB (deferred).** Integration doc §6–7. Separate project;
only relevant if the tenant issues e-invoices.

**Total ≈ 12–17 days** for phases 1–3 plus a scoped phase 4. Phases 5–6 should be
planned separately rather than absorbed into "build the GST agent".

## 12. KPIs

| KPI | Target |
|---|---|
| ITC reversals due to vendor non-filing | → 0 |
| Vendors with unverified GSTIN | < 1% |
| ITC at risk identified before payment | ≥ 95% |
| 2B match rate | ≥ 95% |
| GST return preparation time | days → < 2 h review |
| Sandbox calls per vendor per month | ≤ 2 (caching works) |

## 13. Open questions

1. **Is a GST portal session practical operationally?** OTP-backed sessions expire;
   if a human must re-authenticate for every 2B pull, phase 4's value drops sharply.
   Answer this before committing to phase 4.
2. **Does filing lapse block payment or only warn?** Blocking has real vendor-relations
   consequences; warning with the ITC number is the proposal.
3. **Where does the purchase register come from for 2B matching** — `fact_invoices`,
   or Tally? They may disagree, and the mismatch would look like a 2B discrepancy.
4. **Do we file, or export for a CA?** Export is the honest phase-5 scope.
5. **Multi-GSTIN tenants** — one tenant filing across several state registrations
   changes return assembly from one draft to N.
6. **Should phase 2 be promoted out of P2?** It is cheap, needs no portal session, and
   prevents money loss — arguably it belongs alongside the P0 agents.
