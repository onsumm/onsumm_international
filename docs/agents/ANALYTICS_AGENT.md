# Analytics / Reporting Agent

| | |
|---|---|
| **Agent name** | `analytics` |
| **Fleet card** | "Weekly spend, exceptions and run rollup." |
| **Priority** | 🔴 **P0** · 🟦 MED |
| **Status** | 🟡 Partial — FX briefings and per-domain stats endpoints exist; there is no cross-domain rollup |
| **Default autonomy** | **L1** — drafts the report and the digest email, sends nothing |
| **Difficulty** | Medium — the work is query design and trust, not orchestration |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.4 |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Answer: **what happened this week, what needs attention, and what did the fleet
do about it?**

This is the agent that makes the rest of the fleet legible. Every other agent
produces findings scoped to one bill or one vendor; nobody sees the aggregate.
Analytics reads across the domain tables **and across `agent_runs`** to produce a
period report plus a draft digest email.

It is also the fleet's own accountability surface: run counts, failure rates, gate
latency and token spend per agent come from the same rollup. If an agent starts
quietly failing, this is where it shows up.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Weekly rollup | `schedule` | Monday 07:00 tenant-local — the primary use |
| Month-end close | `schedule` | Fuller report: spend by cost center, TDS/GST exposure, AP ageing |
| User action | `user` | "Generate report" with an arbitrary period |
| Delegated | `agent` | CFO orchestrator asking for a briefing before planning |
| Threshold breach | `event` | e.g. exceptions above a count, or FX exposure past a band — an ad-hoc alert report |

## 3. Inputs

```json
{
  "period": {"from": "2026-07-27", "to": "2026-08-02"},
  "scope": {"cost_center_id": null, "brand_id": null, "vendor_id": null},
  "sections": ["spend","exceptions","approvals","fleet","fx","ageing"],
  "format": "payload|html|pdf",
  "draft_email": true
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `query_spend(period, scope)` | **wrap** | Aggregate `fact_invoices` / `fact_bills` by vendor, category, cost center. COA semantics in [COA_RULES_AND_REPORTS.md](../COA_RULES_AND_REPORTS.md) |
| `query_exceptions(period)` | **wrap** | Open `billing_reconciliation_discrepancies`, RED verifications, failed disbursements, OCR failures |
| `query_approvals(period)` | **wrap** | `bill_approval_chain` + `wf_tasks` — pending, overdue, median time-to-approve |
| `query_ageing(as_of)` | **new** | AP ageing buckets (0–30/31–60/61–90/90+) from `fact_invoices` + `payment_schedules` |
| `query_disbursements(period)` | **reuse** | `GET /api/disbursements/stats` in [`disbursement_api.py`](../../backend_ocr/app/disbursement_api.py) |
| `query_fx(period)` | **reuse** | `GET /api/fx/report`, `/api/fx/exposure`, `/api/fx/agent-briefing` in [`fx_api.py`](../../backend_ocr/app/fx_api.py) |
| `query_runs(period)` | **new** | Fleet rollup from `agent_runs`: counts by agent/status, gate latency, tokens, `cost_usd` |
| `query_tds_gst_exposure(period)` | **wrap** | `fact_tds_ledger` + GST recon state |
| `render_report(sections)` | **new** | Deterministic assembly → structured payload, then HTML/PDF |
| `summarize(payload)` | **new** | LLM narrative **over already-computed numbers** |
| `draft_digest_email(report)` | **new** | Delegates the send to [`communication`](COMMUNICATION_AGENT.md) |

## 5. Steps

1. **Resolve period and scope**, honoring tenant fiscal config
   ([`utils/fiscal.py`](../../utils/fiscal.py)) — "last month" is not a calendar
   month for every tenant.
2. **Run section queries in parallel.** Each is independent; one failure degrades
   that section rather than the report. A failed section is rendered as
   "unavailable", never silently dropped — a missing exceptions block that looks
   like a clean week is worse than an error.
3. **Compute deltas** vs the prior comparable period. A number without a
   comparison isn't a report.
4. **Assemble** the structured payload deterministically.
5. **Narrate.** LLM writes prose *from the payload only*, with a hard rule: it may
   not introduce any figure absent from the payload. Post-check every numeral in
   the narrative against the payload and drop the narrative if it fails.
6. **Draft the digest email** (recipients from tenant report settings).
7. **Gate (L1).** Report is committed and visible; sending is a gate. At L2 the
   send happens automatically on schedule.

## 6. Outcome schema

```json
{
  "period": {"from": "2026-07-27", "to": "2026-08-02"},
  "spend": {
    "total_inr": 4820000.00, "bill_count": 87,
    "delta_pct_vs_prior": 12.4,
    "top_vendors": [{"vendor": "Signzy", "amount": 412500, "delta_pct": 3.1}],
    "by_cost_center": [{"name": "Ops", "amount": 1900000}]
  },
  "exceptions": {
    "recon_open": 14, "recon_high": 3,
    "verification_red": 2,
    "disbursement_failed": 1,
    "ocr_failed": 4,
    "items": [{"type": "recon_high", "entity": "bill:…", "detail": "₹14,500 variance"}]
  },
  "approvals": {"pending": 22, "overdue": 5, "median_hours_to_approve": 9.2},
  "ageing": {"b0_30": 2100000, "b31_60": 900000, "b61_90": 210000, "b90_plus": 45000},
  "fx": {"open_exposure_inr": 1250000, "unrealized_pnl_inr": -18400},
  "fleet": {
    "runs": 412, "failed": 7, "awaiting_approval": 9,
    "by_agent": [{"agent": "verification", "runs": 210, "failed": 1, "cost_usd": 0.84}],
    "total_cost_usd": 3.42
  },
  "narrative": "Spend rose 12.4% …",
  "email_draft": {"subject": "…", "body_html": "…", "recipients": ["…"]},
  "report_id": "uuid"
}
```

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: schedule`, `trigger_ref: weekly_rollup`, `reason: "Weekly CFO report"` |
| **WHOM** | `entity_type: tenant`, `entity_id: <tenant_id>` |
| **WHAT** | `action: "Weekly report: ₹48.2L spend, 14 open exceptions, 5 overdue approvals"`, `outcome:` above |
| **HOW** | `tools_used: [query_spend, query_exceptions, …, summarize]`; each step logs row counts scanned and ms |

Recording **row counts per query** matters here: it is how you catch a report that
looks healthy because a query silently returned nothing.

## 8. Guardrails

- **Read-only.** No writes to any domain table. Only its own report rows.
- **Every number is computed, never generated.** The LLM sees the payload and
  writes prose; the numeral post-check enforces it.
- **No cross-tenant aggregation, ever** — including in "benchmark" framing.
- **Send is gated at L1** and rate-limited via [`communication`](COMMUNICATION_AGENT.md).
- **Cost ceiling** — one narrative call per report; long payloads summarized
  section-wise, not by enlarging the context.
- **PII minimization** — vendor names and totals in the digest; no bank details,
  no attachments by default.

## 9. Data model

**Reuses:** `fact_invoices`, `fact_bills`, `stg_bills`, `dim_suppliers`,
`bill_approval_chain`, `wf_tasks`, `wf_nudges`, `fact_disbursements`,
`payment_schedules`, `billing_reconciliation_discrepancies`, `fact_tds_ledger`,
`fx_agent_notes`, `agent_runs`.

**New:**

```sql
CREATE TABLE IF NOT EXISTS agent_reports (
    report_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    run_id      uuid        REFERENCES agent_runs(run_id),
    kind        text        NOT NULL,          -- weekly|monthly|adhoc|alert
    period_from date        NOT NULL,
    period_to   date        NOT NULL,
    scope       jsonb       NOT NULL DEFAULT '{}',
    payload     jsonb       NOT NULL,          -- the full structured report
    narrative   text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_reports_period
    ON agent_reports (tenant_id, kind, period_to DESC);
```

Persisting the payload — not just rendering it — is what allows "why did last
month's report say X?" to be answerable after the underlying data has moved on.

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/analytics/run` | Generate a report for a period/scope |
| `GET` | `/api/agents/analytics/reports` | List reports |
| `GET` | `/api/agents/analytics/reports/{id}` | Payload + narrative |
| `GET` | `/api/agents/analytics/reports/{id}/export` | HTML/PDF/CSV |
| `GET` | `/api/agents/analytics/fleet` | Fleet health rollup — powers the Hub telemetry strip |

The fleet rollup is worth shipping first and separately: it is the smallest useful
piece and the Hub UI already has a telemetry area waiting for it.

## 11. Build plan

**Step 1 — fleet rollup (1 day).** `query_runs` over `agent_runs`. Small, and it
makes every other agent's behavior observable, so build it alongside Verification
rather than after.

**Step 2 — spend + exceptions (2–3 days).** The two sections a controller actually
opens. Reuse COA/cost-center semantics rather than re-deriving them.

**Step 3 — report persistence + assembly (1–2 days).** `agent_reports`,
`render_report`, structured payload only.

**Step 4 — approvals + ageing (2 days).** Ageing needs a due-date convention
decision (invoice date + terms vs `payment_schedules`).

**Step 5 — narrative + numeral post-check (1 day).** Including the drop-on-failure
rule.

**Step 6 — digest email (1 day).** Depends on [`communication`](COMMUNICATION_AGENT.md).

**Step 7 — FX/TDS/GST sections + PDF export (2–3 days).**

**Total ≈ 8–12 days**, front-loaded so the fleet strip and the spend/exception
sections land early.

## 12. KPIs

| KPI | Target |
|---|---|
| Report generation latency | < 60 s for a weekly rollup |
| Digest open rate | ≥ 70% of recipients |
| Numeral post-check failures | 0 shipped (drops are logged) |
| Exceptions surfaced here before being reported by a human | ≥ 80% |
| Report cost | < $0.05 per run |

## 13. Open questions

1. **Who receives the digest?** Tenant-level recipient list, per-role, or per-cost-center
   owner? Cost-center scoping changes the query shape, so decide before step 2.
2. **Ageing basis** — invoice date + payment terms, or `payment_schedules.due_date`
   where present? They disagree today.
3. **PDF rendering** — server-side (new dependency) or browser print of the HTML?
   Print-to-PDF is cheaper and probably sufficient.
4. **Should threshold-breach alerts live here or in each domain agent?** Proposal:
   here, so alerting rules sit in one place.
5. **Retention** — how long do report payloads stay? They embed a snapshot of
   spend data, which has a privacy footprint.
