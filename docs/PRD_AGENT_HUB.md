# PRD — Agent Hub (CFO Multi-Agent System)

| | |
|---|---|
| **Status** | Draft v1.0 — for review |
| **Owner** | Subhash Bishnoi |
| **Last updated** | 2026-07-18 |
| **Source** | Whiteboard architecture session (Jul 2026) |
| **Related docs** | [IMPLEMENTATION_PLAN_NOVA_BILLING.md](IMPLEMENTATION_PLAN_NOVA_BILLING.md), [PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md](PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md), [Sandbox_GST_COMPLIANCE_INTERGRATION.md](Sandbox_GST_COMPLIANCE_INTERGRATION.md), [DISBURSEMENT_IMPS_API.md](DISBURSEMENT_IMPS_API.md), [tally_architecture.html](tally_architecture.html), [full_database_schema.md](full_database_schema.md) |

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Background & motivation](#2-background--motivation)
3. [Glossary](#3-glossary)
4. [Goals & non-goals](#4-goals--non-goals)
5. [Success metrics](#5-success-metrics)
6. [Personas](#6-personas)
7. [User journeys](#7-user-journeys)
8. [Product principles](#8-product-principles)
9. [System architecture](#9-system-architecture)
10. [Autonomy model & guardrails](#10-autonomy-model--guardrails)
11. [Data model](#11-data-model)
12. [The audit model (WHY / WHAT / WHOM / HOW)](#12-the-audit-model-why--what--whom--how)
13. [Agent catalog](#13-agent-catalog)
14. [Orchestration flows](#14-orchestration-flows)
15. [API design](#15-api-design)
16. [Hub UI / UX spec](#16-hub-ui--ux-spec)
17. [Integrations](#17-integrations)
18. [Security, permissions & multi-tenancy](#18-security-permissions--multi-tenancy)
19. [Observability & cost control](#19-observability--cost-control)
20. [Rollout plan & roadmap](#20-rollout-plan--roadmap)
21. [Risks & mitigations](#21-risks--mitigations)
22. [Open questions](#22-open-questions)
23. [Appendix](#23-appendix)

---

## 1. Executive summary

The Agent Hub turns Onsumm's existing finance-automation capabilities (OCR bill
ingestion, ledger/category enrichment, KYB verification, Nova reconciliation,
disbursement, FX, TDS) into a **fleet of governed, auditable AI agents**
orchestrated by a **CFO master agent**.

Today these capabilities are individual API endpoints and pipeline workers that
a human drives one screen at a time. The Agent Hub adds three things on top:

1. **Delegation** — a CFO orchestrator decomposes a goal ("close this vendor's
   month") and routes sub-tasks to specialized agents.
2. **Auditability** — every agent action is recorded along four axes — **WHY,
   WHAT, WHOM, HOW** — producing a reviewable trail for finance and compliance.
3. **A single surface** — the Hub, where a user sees every agent, triggers runs,
   watches them execute, approves gated actions, and reads outcomes.

The design deliberately **reuses** what exists. Roughly half the agents are
orchestration + audit layers over shipped code (Verification ← Sandbox KYB,
Recon ← Nova reconciliation, Payable ← disbursement, Contract/Document ← OCR).
The genuinely new infrastructure is the **agent runtime, registry, orchestrator,
and audit store**.

---

## 2. Background & motivation

### 2.1 Where we are

The platform is a multi-tenant FastAPI backend (`backend_ocr/app`) plus a Vite
frontend, backed by PostgreSQL and OpenAI (GPT-4o vision for OCR, GPT-4o-mini
for mapping/ledger reasoning). Capabilities already in production or in-flight:

| Capability | Module | Notes |
|---|---|---|
| Invoice OCR + extraction | `utils/ocr_engine.py`, `ocr.py`, `ingest.py` | GPT-4o vision → structured JSON → `stg_bills` / `fact_invoices` |
| Category / ledger enrichment | `pipelines/category_enrichment.py`, `main.py` | COA rules + GPT-4o-mini |
| KYB verification | `sandbox_kyb.py` | GSTIN, PAN, bank (penny-less) via Sandbox.co.in |
| Vendor billing reconciliation | `billing_api.py` | Nova usage vs invoice; `/runs`, `/discrepancies`, `_agent_recon_note` |
| Disbursement (payable) | `disbursement_api.py` | IMPS demo, OTP-gated, `fact_disbursements`, approval chain |
| FX standardization | `fx_api.py` | multi-currency → INR, `fx_agent_notes`, briefings |
| TDS settings | `tds_api.py` | rate/section config |
| Approval chains | `main.py` | `bill_approval_chain`, `tenant_chain_config` |
| Tally connector | `tally_agent_api.py` | pairing-token auth, XML ingest |

There are already **agent-flavored precedents** worth building on:
- `fx_agent_notes` and `_agent_recon_note` — LLM advisory notes attached to
  bills. These are proto-agent outcomes.
- `billing_api` `/runs` + `/discrepancies` — a run/finding model that the audit
  store generalizes.
- Tally connector "agent" pairing (`int_connections.agent_api_key_hash`) — an
  auth precedent for machine actors.
- Pipeline `BaseWorker` (LISTEN/NOTIFY) — an event-driven execution precedent.

### 2.2 The problem

- **Human is the orchestrator.** A user must know which screen does what and run
  each step manually. There is no "do the whole thing" layer.
- **No unified audit.** Actions are scattered across tables; there's no single
  "what did the system do to this vendor, and why" view — a hard requirement for
  a finance product.
- **Capabilities don't compose.** Verification, reconciliation, and payment are
  separate; nothing chains "verify → reconcile → pay" with gates between.

### 2.3 The opportunity

A CFO agent that composes existing capabilities, with a rigorous audit trail and
human approval gates, is a step-change in value and is largely an
**orchestration + governance** effort rather than net-new finance logic.

---

## 3. Glossary

| Term | Definition |
|---|---|
| **Agent** | A named unit that performs a scoped task, may call tools and other (child) agents, and always emits an auditable outcome. |
| **CFO Agent / Master** | The top-level orchestrator that decomposes goals and delegates to sub-agents. |
| **Domain agent** | TDS, GST, Recon, Payable, Receivable — owns a finance domain. |
| **Child / utility agent** | Verification, Contract/Document, Communication — invoked by other agents. |
| **Tool** | A deterministic function an agent may call (DB query, Sandbox API, SMTP send, IMPS initiate). |
| **Run** | One invocation of an agent, recorded in `agent_runs`. |
| **Outcome** | The structured result of a run (status + payload), conforming to that agent's outcome schema. |
| **Autonomy level** | L0–L3, how much an agent may do without human approval (see §10). |
| **Approval gate** | A point where a run pauses for human sign-off before a side-effecting action. |
| **Trigger** | What starts a run: user, event, schedule, or another agent. |

---

## 4. Goals & non-goals

### 4.1 Goals

- **G1** — A CFO orchestrator that accepts a goal/event and delegates to the
  correct sub-agents, sequencing them with gates.
- **G2** — A universal audit store (`agent_runs`) capturing WHY/WHAT/WHOM/HOW for
  every run, including nested child runs (`parent_run_id`).
- **G3** — An agent registry so agents are declared, discoverable, and
  independently versioned/toggled per tenant.
- **G4** — A Hub UI to list agents, trigger runs, watch execution, approve gates,
  and read the audit trail.
- **G5** — Ship the two `(0)` agents (Verification, Recon) on the new runtime as
  proof, reusing existing modules.
- **G6** — Human-in-the-loop by default for all side-effecting actions
  (payments, filings, outbound email), with per-agent autonomy configuration.
- **G7** — Strict multi-tenant isolation and PII handling consistent with the
  current `tenant_id` model.

### 4.2 Non-goals (this phase)

- **NG1** — Fully autonomous payments or statutory filings without human sign-off.
- **NG2** — Replacing existing screens; the Hub augments, it doesn't remove.
- **NG3** — A general-purpose agent marketplace / third-party agents.
- **NG4** — Real (non-demo) banking rails beyond what `disbursement_api` already
  models; the Payable agent uses the existing disbursement path.
- **NG5** — Cross-tenant analytics or model fine-tuning.

---

## 5. Success metrics

| Metric | Definition | Target (first 90 days post-GA) |
|---|---|---|
| **Automation rate** | % of eligible tasks completed by an agent without manual step | ≥ 60% for Verification, ≥ 40% for Recon |
| **Audit completeness** | % of runs with all four axes populated | 100% (enforced) |
| **Human-review time** | median time from gate-open to approval | < 2 min |
| **Recon touch reduction** | manual recon actions per bill before vs after | −50% |
| **Verification turnaround** | median vendor onboarding verification time | < 30 s |
| **Agent error rate** | runs ending `failed` / total runs | < 5%, none silent |
| **Trust** | % of agent outcomes accepted without override | ≥ 85% |

---

## 6. Personas

| Persona | Role | What they need from the Hub |
|---|---|---|
| **Finance Controller / CFO** | Owns the numbers | Delegate multi-step work; see what agents did and why; approve high-risk actions |
| **Accountant / AP clerk** | Day-to-day processing | Run Verification/Recon on a bill; act on discrepancies; send follow-ups |
| **Compliance / Auditor** | Assurance | Immutable WHY/WHAT/WHOM/HOW trail; filter by entity/agent/date |
| **Vendor** (indirect) | External | Receives agent-driven communications (verification requests, payment/follow-up emails) |
| **Platform admin** | Onsumm ops | Enable/disable agents per tenant, set autonomy levels, monitor cost/errors |

---

## 7. User journeys

### J1 — Onboard & verify a new vendor (Verification agent, L2)
1. Accountant adds a vendor (GSTIN, PAN, bank).
2. CFO agent triggers **Verification** on `vendor:{id}`.
3. Agent calls Sandbox KYB tools (GSTIN, PAN, bank), computes a trust score,
   writes outcome + audit (WHY: new vendor; WHAT: 3 checks + score; WHOM:
   vendor; HOW: Sandbox calls).
4. Hub shows a green/amber/red verification card; failures flagged for review.

### J2 — Close a vendor's monthly bill (CFO → Recon → Payable)
1. Controller: "Reconcile and prepare payment for Signzy, June."
2. CFO decomposes → **Recon** (Nova usage vs invoice) → discrepancies.
3. If clean and within tolerance and vendor is verified → **Payable** prepares a
   disbursement and **pauses at an approval gate**.
4. Controller approves; Payable initiates via existing disbursement/IMPS path,
   OTP-gated. Every step audited under one parent run.

### J3 — Weekly CFO report (Analytics/Reporting agent, L1)
1. Scheduled trigger.
2. Agent reads `agent_runs` + domain data, produces a summary (spend, exceptions,
   pending approvals, verification failures) and drafts a follow-up email digest.
3. Controller reviews and sends (or agent sends at L2).

### J4 — Auditor investigates a payment
1. Auditor opens the Hub audit view, filters `entity = vendor:Signzy`.
2. Sees the full chain: verification → recon findings → approval → disbursement,
   each with reason, tools used, and result. Exports as evidence.

---

## 8. Product principles

1. **Audit-first.** No run exists without an audit record. The audit contract is
   part of the runtime, not a logging afterthought.
2. **Reuse over rebuild.** Agents wrap existing modules/endpoints as tools;
   finance logic isn't duplicated.
3. **Human-in-the-loop by default.** Side effects (pay, file, email) require an
   approval gate unless a tenant explicitly raises the autonomy level.
4. **Deterministic core, LLM at the edges.** Prefer deterministic tools for
   money-affecting logic; use the LLM for classification, summarization, and
   drafting — never as the sole arbiter of a payment amount.
5. **Tenant isolation.** Every run, tool call, and record is scoped by
   `tenant_id`; no cross-tenant reads.
6. **Idempotency & traceability.** Re-running is safe; every child action links
   to its parent run.
7. **Fail loud.** Errors surface in the Hub; nothing fails silently.

---

## 9. System architecture

### 9.1 Component overview

```
┌──────────────────────────────────────────────────────────────────┐
│                            Hub UI (Vite)                           │
│  Agent list · Run console · Approval inbox · Audit explorer        │
└───────────────────────────────┬────────────────────────────────────┘
                                 │  REST  /api/agents/*
┌───────────────────────────────▼────────────────────────────────────┐
│                          Agent Hub API (FastAPI)                    │
│   registry · trigger · run status · approvals · audit query         │
└───────────────┬───────────────────────────────┬────────────────────┘
                │                                 │
      ┌─────────▼──────────┐            ┌─────────▼──────────┐
      │  CFO Orchestrator  │  spawns    │   Agent Runtime    │
      │  goal → plan →     │───────────▶│  (per-agent exec,  │
      │  delegate + gate   │            │   tool calls,      │
      └─────────┬──────────┘            │   audit logging)   │
                │                        └─────────┬──────────┘
                │ registry lookup                  │ uses
      ┌─────────▼──────────┐            ┌───────────▼─────────┐
      │   Agent Registry   │            │     Tool Layer      │
      │  declarative defs  │            │ DB · Sandbox · SMTP │
      │  per-tenant config │            │ IMPS · OCR · Nova   │
      └────────────────────┘            └───────────┬─────────┘
                                                     │
                        ┌────────────────────────────▼───────────────┐
                        │  PostgreSQL                                  │
                        │  agent_runs · agent_registry · agent_tasks   │
                        │  agent_approvals · agent_messages            │
                        │  + existing fact_invoices / dim_suppliers…   │
                        └──────────────────────────────────────────────┘
```

### 9.2 Components

**Agent Runtime (SDK).** A thin Python framework every agent uses. Provides:
- `AgentRun` context manager that opens an `agent_runs` row on entry, captures
  WHY/WHOM at start, records tool calls into HOW, and finalizes WHAT/status on
  exit (including exceptions → `failed`).
- A `Tool` abstraction: registered callables with input/output schemas that
  auto-log to the run's `steps`.
- A base class `Agent` with `plan()` / `act()` and an `outcome_schema`.
- Runs either **inline** (API request, for fast agents like Verification) or as
  a **background task / pipeline worker** (for long ones like Recon) using the
  existing `BaseWorker` LISTEN/NOTIFY pattern.

**Agent Registry.** Declarative definitions (code-defined, tenant-toggleable via
`agent_registry` table): name, description, triggers, inputs, allowed tools,
child agents, default autonomy level, outcome schema, version. The Hub reads
this to render the agent list; the orchestrator reads it to route.

**CFO Orchestrator.** Accepts a goal (NL or structured) or an event. Produces a
**plan** (ordered steps, each = an agent + inputs + gate?), executes it,
delegates via the runtime, and rolls up child outcomes into a parent run.
Planning is LLM-assisted but constrained to registered agents and a validated
plan schema (no free-form tool invention).

**Tool Layer.** Wraps existing capabilities as callable tools, e.g.
`verify_gstin`, `verify_pan`, `verify_bank` (← `sandbox_kyb`), `run_recon`,
`list_discrepancies` (← `billing_api`), `prepare_disbursement`, `initiate_imps`
(← `disbursement_api`), `send_email` (← SMTP), `parse_document` (← `ocr_engine`),
`fx_convert` (← `fx_api`). Tools are the **only** way agents touch the world;
this keeps the audit and permission surface finite.

**Audit & Memory store.** `agent_runs` (see §11/§12) plus optional
`agent_messages` for conversational/orchestration transcripts.

### 9.3 Execution model

- **Inline** for sub-second/second-scale agents invoked from a request.
- **Async worker** for anything doing external I/O loops (Recon over many lines,
  bulk verification), triggered via NOTIFY on a channel like
  `ch_agent_dispatch`, mirroring `pipelines/*` `BaseWorker`.
- **Scheduled** via the existing pipeline scheduler / cron for Analytics and
  follow-ups.

---

## 10. Autonomy model & guardrails

Each agent (per tenant) runs at an **autonomy level**:

| Level | Name | Behavior |
|---|---|---|
| **L0** | Suggest | Produces a recommendation only; no side effects. |
| **L1** | Draft | Prepares an artifact (draft email, prepared disbursement) but does not send/commit. |
| **L2** | Act-with-gate | Executes, pausing at an approval gate before any irreversible/outward action. |
| **L3** | Autonomous | Executes end-to-end within policy limits (amount caps, verified-vendor-only, tolerance thresholds). |

**Guardrails (always on, regardless of level):**
- **Approval gates** for: initiating payment, statutory filing (TDS/GST),
  outbound email to vendors, and any write to `fact_disbursements`.
- **Policy limits**: max amount per run, "verified vendor only" for payments,
  recon tolerance bands, daily send caps for Communication.
- **Money logic is deterministic.** Amounts/thresholds computed by tools, never
  by the LLM. LLM output is advisory (notes, classification, drafts).
- **Idempotency keys** on side-effecting tools (e.g. disbursement) to prevent
  double-execution on retry.
- **Kill switch** per agent per tenant in the registry.

Default levels: Verification L2, Recon L1 (surface findings), Analytics L1,
Communication L1, Payable L1 (prepare only), everything else L0 until proven.

---

## 11. Data model

New tables (PostgreSQL, `tenant_id uuid` on every row; times `timestamptz`).

### 11.1 `agent_registry`
Per-tenant enablement + config; canonical definition lives in code.
```sql
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_name      text        NOT NULL,
    tenant_id       uuid        NOT NULL,
    enabled         boolean     NOT NULL DEFAULT true,
    autonomy_level  smallint    NOT NULL DEFAULT 0,      -- 0..3
    config          jsonb       NOT NULL DEFAULT '{}',    -- caps, thresholds
    version         text,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, agent_name)
);
```

### 11.2 `agent_runs` — the audit backbone
```sql
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid        NOT NULL,
    agent_name     text        NOT NULL,
    parent_run_id  uuid        REFERENCES agent_runs(run_id),  -- child linkage
    -- WHY
    trigger_type   text        NOT NULL,     -- user|event|schedule|agent
    trigger_ref    text,                     -- user id, event name, cron id
    reason         text,                     -- human-readable why
    -- WHOM
    entity_type    text,                     -- vendor|bill|invoice|po|tenant
    entity_id      text,
    -- WHAT
    goal           text,                     -- requested outcome
    action         text,                     -- what it did
    outcome        jsonb,                    -- structured result (schema per agent)
    status         text        NOT NULL DEFAULT 'running', -- running|awaiting_approval|succeeded|failed|cancelled
    error          text,
    -- HOW
    tools_used     text[]      NOT NULL DEFAULT '{}',
    steps          jsonb       NOT NULL DEFAULT '[]',   -- ordered step/tool log
    autonomy_level smallint,
    tokens_used    integer,
    cost_usd       numeric(12,4),
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_entity
    ON agent_runs (tenant_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent ON agent_runs (parent_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs (tenant_id, status);
```

### 11.3 `agent_approvals` — gate records
```sql
CREATE TABLE IF NOT EXISTS agent_approvals (
    approval_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid        NOT NULL REFERENCES agent_runs(run_id),
    tenant_id     uuid        NOT NULL,
    gate          text        NOT NULL,     -- e.g. 'initiate_payment'
    payload       jsonb       NOT NULL,     -- what will happen if approved
    status        text        NOT NULL DEFAULT 'pending', -- pending|approved|rejected
    decided_by    text,
    decided_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_approvals_pending
    ON agent_approvals (tenant_id, status) WHERE status = 'pending';
```

### 11.4 `agent_messages` — orchestration transcript (optional but recommended)
```sql
CREATE TABLE IF NOT EXISTS agent_messages (
    message_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      uuid        NOT NULL REFERENCES agent_runs(run_id),
    tenant_id   uuid        NOT NULL,
    role        text        NOT NULL,   -- system|planner|agent|tool|user
    content     jsonb       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
```

### 11.5 Reuse of existing tables
Agents **read/write existing tables through tools**, not directly: `fact_invoices`,
`stg_bills`, `stg_bill_line_items`, `dim_suppliers`, `coa_rules`,
`billing_bill_reconciliation`, `fact_disbursements`, `bill_approval_chain`,
`supplier_bank_accounts`, `supplier_gstins`, `vendor_outreach_log`,
`fx_agent_notes`. Existing agent-note tables can be linked to runs via
`entity_id` for continuity.

---

## 12. The audit model (WHY / WHAT / WHOM / HOW)

The board's core requirement. Each axis maps to concrete `agent_runs` columns:

| Axis | Question | Columns |
|---|---|---|
| **WHY** | What caused this? | `trigger_type`, `trigger_ref`, `reason` |
| **WHAT** | What did it do & result? | `goal`, `action`, `outcome`, `status`, `error` |
| **WHOM** | On which entity? | `entity_type`, `entity_id` |
| **HOW** | By what means? | `tools_used`, `steps` (each step: tool, args-digest, result-digest, ms) |

**Example row (Verification run):**
```json
{
  "agent_name": "verification",
  "trigger_type": "event", "trigger_ref": "vendor.created", "reason": "New vendor onboarded",
  "entity_type": "vendor", "entity_id": "d0e0...002",
  "goal": "Verify vendor KYB", "action": "Ran GSTIN+PAN+bank checks; trust=GREEN",
  "outcome": {"gstin": "valid", "pan": "valid", "bank": "valid", "msme": true, "trust_score": 0.94},
  "status": "succeeded",
  "tools_used": ["verify_gstin", "verify_pan", "verify_bank"],
  "steps": [
    {"tool": "verify_gstin", "result": "ACTIVE", "ms": 420},
    {"tool": "verify_pan",   "result": "VALID",  "ms": 310},
    {"tool": "verify_bank",  "result": "NAME_MATCH", "ms": 900}
  ]
}
```

The Hub audit explorer renders any run — and its child runs via `parent_run_id`
— as a timeline grouped by these four axes.

---

## 13. Agent catalog

Each agent spec: **priority / tags · status · triggers · inputs · tools · steps ·
outcome · guardrails · reuse & gaps · KPIs.** Priority follows the whiteboard
numbers `(0)/(1)/(2)`.

### Priority legend

| Marker | Meaning | From board |
|---|---|---|
| 🏗️ **F** | Foundation — must exist before any agent runs | (prerequisite) |
| 🔴 **P0** | Build first — highest priority | `(0)` |
| 🟠 **P1** | Next | `(1)` |
| 🟡 **P2** | Later | `(2)` |
| 🔧 **Util** | Child/utility agent, called by others | CHILD bracket |
| 🔥 **HARD** | High implementation difficulty | `HARD` |
| ⭐ **IMP** | Explicitly flagged important | `IMP` |
| 🟦 **MED** | Medium difficulty | `MEDIUM` |

### Priority board (do-this-order)

| Order | Agent | Priority | Difficulty | Status | Why here |
|---|---|---|---|---|---|
| 0 | **CFO runtime + audit** | 🏗️ F | — | ❌ new | Nothing runs or is auditable without it |
| 1 | **Verification** | 🔴 P0 · 🔧 Util | easy | ✅ built | Fastest win; proves audit E2E (Sandbox KYB done) |
| 2 | **Recon** | 🔴 P0 · 🔥⭐ | hard | 🟡 partial | Highest-value centerpiece (Nova done) |
| 3 | **Analytics / Reporting** | 🔴 P0 · 🟦 | medium | 🟡 partial | Reads audit store; needs runtime first |
| 4 | **CFO Orchestrator** | 🏗️ F (Phase 2) | medium | ❌ new | Composes P0 agents into goals |
| 5 | **Payable** | ⚫ follows Recon | medium | 🟡 partial | Unnumbered on board; gated payments after recon |
| 6 | **Contract / Document** | 🟠 P1 · 🟦 · 🔧 Util | medium | 🟡 partial | Extends OCR to contracts |
| 7 | **TDS** | 🟡 P2 | medium | 🟡 partial | Domain filing agent |
| 8 | **GST** | 🟡 P2 | medium | 🟡/❌ | Domain filing agent |
| 9 | **Receivable** | 🟡 P2 | medium | ❌ new | Least-built domain |
| — | **Communication** | 🔧 Util | easy | 🟡 partial | Called by many; harden alongside P0 |

### 13.1 CFO Agent (Master) &nbsp;🏗️ **F**
- **Priority:** 🏗️ Foundation (orchestrator lands in Phase 2). **Status:** ❌ new.
- **Triggers:** user goal (NL/structured); high-level events (month close).
- **Inputs:** goal, scope (tenant, vendor, period).
- **Tools:** the registry (to plan) + delegates to sub-agents; `send_email`,
  analytics read tools.
- **Steps:** interpret goal → build validated plan (ordered agent steps + gates)
  → delegate → roll up outcomes → report.
- **Outcome:** `{plan, child_runs[], summary}`.
- **Guardrails:** may only invoke registered agents; plan validated against
  schema; no direct tool side effects except reporting.
- **KPIs:** goal completion rate, human interventions per goal.

### 13.2 Recon Agent &nbsp;🔴 **P0** · 🔥 **HARD** · ⭐ **IMP**
- **Status:** 🟡 partial — build on `billing_api` Nova reconciliation.
- **Triggers:** bill ingested/approved; user "reconcile"; scheduled.
- **Inputs:** `bill_id` or `vendor_id + period`.
- **Tools:** `pull_erp_usage` (Tally/Nova), `run_recon`, `list_discrepancies`,
  `match_po` (2-way/3-way), `bank_recon`, `verify_payment`.
- **Steps:** pull source-of-truth usage → compare to invoice lines → classify
  mismatches → PO match → produce discrepancy set + advisory note.
- **Outcome:** `{matched, discrepancies[], severity, recommended_action}`.
- **Guardrails:** read-only; thresholds deterministic; LLM only for the note.
- **Reuse & gaps:** reuse `_agent_recon_note`, `/runs`, `/discrepancies`; **new:**
  PO matching, bank-statement recon, payment verification.
- **KPIs:** discrepancy precision/recall, manual-touch reduction.

### 13.3 Verification Agent &nbsp;🔴 **P0** · 🔧 **Util**
- **Status:** ✅ mostly built — wrap `sandbox_kyb`.
- **Triggers:** vendor created/edited; bank account added; user.
- **Inputs:** `vendor_id` (+ GSTIN/PAN/bank).
- **Tools:** `verify_gstin`, `verify_pan`, `verify_bank`, `check_msme`.
- **Steps:** run checks → compute trust score → persist → flag failures.
- **Outcome:** `{gstin, pan, bank, msme, trust_score, flags[]}`.
- **Guardrails:** L2 default; PII minimized in `steps` (store results, not raw
  documents).
- **Reuse & gaps:** reuse GSTIN/PAN/bank; **new:** MSME check, trust score,
  "easy compliance audit" rollup.
- **KPIs:** verification turnaround, auto-clear rate.

### 13.4 Analytics / Reporting Agent &nbsp;🔴 **P0** · 🟦 **MED**
- **Status:** 🟡 partial.
- **Triggers:** scheduled (weekly/month-end); user.
- **Inputs:** tenant, period.
- **Tools:** `query_runs` (audit rollup), `query_spend`, `query_exceptions`,
  `render_report`, `send_email`.
- **Steps:** aggregate runs + domain data → build report → draft follow-up email
  digest.
- **Outcome:** `{report_url|payload, email_draft}`.
- **Guardrails:** L1 default (draft, don't send); send behind a gate.
- **KPIs:** report adoption, follow-up response rate.

### 13.5 Contract / Document Agent &nbsp;🟠 **P1** · 🟦 **MED** · 🔧 **Util**
- **Status:** 🟡 partial — extend `ocr_engine`.
- **Triggers:** document upload; user.
- **Inputs:** file, doc type.
- **Tools:** `parse_document`, `extract_terms`, `persist_document`.
- **Steps:** parse (invoice/contract) → extract structured fields/terms →
  persist + link to vendor/bill.
- **Outcome:** `{doc_id, fields, terms, links}`.
- **Guardrails:** parsing deterministic-checked (reuse the qty/rate/amount
  reconciliation logic in `ocr_engine`); persistence idempotent.
- **Reuse & gaps:** reuse invoice OCR + `vendor_documents`; **new:** contract
  parsing + term extraction.

### 13.6 Payable Agent &nbsp;⚫ **unnumbered** (follows Recon) · bills
- **Status:** 🟡 partial — build on `disbursement_api` + approval chain.
- **Triggers:** recon clean + approved; user; chain follow-up due.
- **Inputs:** `bill_id`/`disbursement` set.
- **Tools:** `prepare_disbursement`, `initiate_imps`, `check_payment_status`,
  `advance_approval_chain`, `distribute_cash`, `send_email`.
- **Steps:** verify vendor + recon status → prepare disbursement → **gate** →
  initiate (OTP) → track → chain follow-ups.
- **Outcome:** `{disbursement_id, status, receipts[]}`.
- **Guardrails:** payment behind approval gate + OTP; verified-vendor-only;
  amount cap; idempotency key; "cash distribution across vendors" is a
  deterministic allocation, LLM never sets amounts.
- **Reuse & gaps:** reuse `fact_disbursements`, IMPS demo, `bill_approval_chain`;
  **new:** follow-up automation, cash-distribution allocation.

### 13.7 Receivable Agent &nbsp;🟡 **P2**
- **Status:** ❌ new.
- **Triggers:** invoice issued; due-date approaching; payment received.
- **Inputs:** customer, invoice.
- **Tools:** `create_invoice`, `track_receivable`, `reconcile_receipt`,
  `send_email` (follow-up).
- **Steps:** create/track AR invoices → follow up on overdue → reconcile
  receipts.
- **Outcome:** `{ar_status, follow_ups[], reconciled[]}`.
- **Guardrails:** outbound email gated; L1 default.

### 13.8 TDS Agent &nbsp;🟡 **P2**
- **Status:** 🟡 partial — extend `tds_api`.
- **Triggers:** bill with TDS applicability; filing period; user.
- **Inputs:** period, deductee scope.
- **Tools:** `compute_tds`, `prepare_tds_return`, `tds_checker`, `report_tds`.
- **Steps:** compute per applicable bill → assemble return → checker/correction →
  report.
- **Outcome:** `{liability, return_draft, exceptions[]}`.
- **Guardrails:** filing behind gate; computations deterministic.

### 13.9 GST Agent &nbsp;🟡 **P2**
- **Status:** 🟡/❌ — align with `Sandbox_GST_COMPLIANCE_INTERGRATION.md`.
- **Triggers:** filing period; vendor-filing check; user.
- **Inputs:** period, GSTIN(s).
- **Tools:** `prepare_gst_return`, `gst_check_correct`, `vendor_filing_check`,
  `report_gst`.
- **Steps:** assemble returns → check/correct → verify vendor filing status →
  report.
- **Outcome:** `{return_draft, corrections[], vendor_filing_status[]}`.
- **Guardrails:** filing behind gate.

### 13.10 Communication Agent &nbsp;🔧 **Util**
- **Status:** 🟡 partial — SMTP + `vendor_outreach_log`.
- **Triggers:** called by other agents (follow-ups, verification requests, report
  digests).
- **Inputs:** recipient, template, context.
- **Tools:** `render_template`, `send_email`, `log_outreach`, `track_thread`.
- **Steps:** render → **gate (if L≤1)** → send → log → track replies.
- **Outcome:** `{message_id, status, thread_ref}`.
- **Guardrails:** daily send caps; suppression list; gated by default; SMTP creds
  validated (see §17 — current Gmail App Password fragility).

### 13.11 Agent dependency map
```
CFO ─┬─ Recon ───────── Verification, Communication
     ├─ Payable ─────── Verification, Communication
     ├─ Receivable ──── Communication
     ├─ TDS ─────────── (Recon data)
     ├─ GST ─────────── Verification (vendor filing), Communication
     ├─ Analytics ───── Communication
     └─ Contract/Doc ── (feeds Recon, Payable)
```

---

## 14. Orchestration flows

### 14.1 "Reconcile & prepare payment" (J2) — sequence
```
User ─▶ CFO.run(goal="close Signzy June")
  CFO: plan = [Recon(bill), gate?, Payable.prepare(bill), gate:initiate_payment]
  CFO ─▶ Recon.run(bill)            [parent_run_id = CFO run]
        Recon: pull_erp_usage → run_recon → list_discrepancies
        Recon → outcome{matched:true, discrepancies:[], severity:none}
  CFO: severity none AND vendor verified? → check Verification cache/run
  CFO ─▶ Payable.prepare(bill)      [parent_run_id = CFO run]
        Payable: prepare_disbursement → status=prepared
        Payable: open approval gate 'initiate_payment' → status=awaiting_approval
  Hub: shows pending approval to Controller
  Controller approves ─▶ Payable resumes: initiate_imps (OTP) → track
  CFO: roll up → report to user
```
Every node writes an `agent_runs` row; child rows carry `parent_run_id`.

### 14.2 Gate handling
When a step needs a gate, the agent writes an `agent_approvals` row and sets its
run `status = awaiting_approval`, then suspends. The Hub surfaces it in the
Approval inbox. On approve, the runtime resumes the run (background) from the
gated step; on reject, run ends `cancelled` with reason.

---

## 15. API design

New router `agents_api.py`, `prefix="/api/agents"`, `tags=["Agent Hub"]`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/agents` | List agents (registry + status) for tenant |
| `GET` | `/api/agents/{name}` | Agent detail + config + recent runs |
| `PATCH` | `/api/agents/{name}` | Enable/disable, set autonomy level, config |
| `POST` | `/api/agents/{name}/run` | Trigger a run (`{entity, inputs, goal}`) → `run_id` |
| `POST` | `/api/agents/cfo/goal` | Submit a goal to the CFO orchestrator |
| `GET` | `/api/agents/runs` | Query runs (filter: agent, entity, status, date) |
| `GET` | `/api/agents/runs/{run_id}` | Run detail incl. steps + child runs |
| `GET` | `/api/agents/runs/{run_id}/stream` | SSE live step stream for the console |
| `GET` | `/api/agents/approvals` | Pending approval gates for tenant |
| `POST` | `/api/agents/approvals/{id}/decide` | `{decision: approved|rejected, note}` |
| `GET` | `/api/agents/audit` | Audit explorer query (entity-centric) |

Conventions: reuse existing auth/tenant resolution (query/company scoping as in
`main.py`); JSON bodies via Pydantic models; long runs return `run_id` + `202`
and execute async.

---

## 16. Hub UI / UX spec

**Navigation:** new top-level "Agents" section.

1. **Agent Gallery** — cards per agent: name, one-liner, status (enabled/level),
   last run, quick "Run" action. Grouped: Master · Domain · Utility.
2. **Agent Detail** — description, triggers, config (autonomy slider, caps),
   recent runs table, KPIs.
3. **Run Console** — live view of a run: header (WHY/WHOM), step timeline (HOW)
   streaming via SSE, outcome (WHAT), child-run tree. Failure states in red.
4. **Approval Inbox** — pending gates with the exact payload ("Pay ₹X to Vendor Y
   via IMPS"), approve/reject + note. Mirrors the existing bill-approval UX.
5. **Audit Explorer** — entity-centric timeline (pick a vendor/bill → see all
   runs across agents), filters, export (CSV/PDF) for auditors.
6. **CFO Console** — a goal box ("what do you want done?") → shows the plan the
   CFO produced, with gates highlighted, then live execution.

**Design reuse:** follow existing bill/approval UI patterns and the FX
agent-note/severity styling already in the app.

---

## 17. Integrations

| Integration | Used by | Module / notes |
|---|---|---|
| **Sandbox.co.in KYB** | Verification, GST vendor-filing | `sandbox_kyb.py`; token cache; GSTIN/PAN/bank |
| **Tally connector** | Recon (ERP pull) | `tally_agent_api.py`; pairing-token auth |
| **Nova usage** | Recon | `billing_api.py` reconciliation |
| **OpenAI** | CFO planning, notes, drafting, OCR | GPT-4o (vision), GPT-4o-mini (reasoning); cost tracked per run |
| **SMTP (email)** | Communication, Analytics | ⚠️ currently Gmail App Password (`admin@onsumm.ai`) — fragile & rate-limited; **recommend SES/SendGrid** for agent-driven volume (see Risks) |
| **IMPS / disbursement** | Payable | `disbursement_api.py` (demo rails), OTP-gated |
| **GST portal** | GST agent | via Sandbox GST integration (planned) |

---

## 18. Security, permissions & multi-tenancy

- **Tenant isolation:** `tenant_id` on every agent table + tool query; no
  cross-tenant reads; orchestrator scopes all children to the parent's tenant.
- **AuthZ:** agent actions inherit the initiating user's role; approval gates
  require an approver role (reuse chain-config roles). Machine triggers
  (schedule/event) run as a system principal with the tenant's configured
  autonomy caps.
- **PII:** verification stores **results**, not raw KYC documents, in `steps`;
  redact account numbers to last-4 in audit payloads; secrets never logged.
- **Secrets:** tool credentials from env/`config.py` only; never in
  `agent_runs`. (See the SMTP credential incident — treat all env secrets as
  rotate-on-exposure.)
- **Idempotency:** side-effecting tools require an idempotency key derived from
  `run_id + step`.
- **Immutability:** `agent_runs` are append-only; corrections are new runs
  linked via `parent_run_id`/`entity_id`.

---

## 19. Observability & cost control

- **Per-run cost/tokens** recorded (`tokens_used`, `cost_usd`) for LLM steps.
- **Budgets:** per-tenant daily agent-cost cap in `agent_registry.config`; the
  runtime refuses new LLM steps past the cap (fail loud).
- **Metrics:** run counts by status/agent, gate latency, error rate, automation
  rate — surfaced in Analytics and an ops dashboard.
- **Tracing:** `parent_run_id` chains give end-to-end traces; `steps` give
  intra-run detail. Errors captured in `error` + surfaced in the console.
- **Alerting:** failed runs and stuck `awaiting_approval` beyond SLA notify ops.

---

## 20. Rollout plan & roadmap

### Phase 0 — Foundation (framework, no user-facing agent)
- `agent_runs`, `agent_registry`, `agent_approvals`, `agent_messages` tables.
- Agent Runtime SDK (`AgentRun`, `Tool`, `Agent`) + audit logging.
- Minimal registry + `agents_api.py` (list, run, runs, approvals).
- Hub UI shell: Gallery + Run Console + Approval Inbox.
- **Acceptance:** a trivial "echo" agent runs, logs all four axes, and shows in
  the console; an approval gate round-trips.

### Phase 1 — First agents `(0)`
- **Verification Agent** (wrap `sandbox_kyb`) — fastest, proves audit E2E.
- **Recon Agent** (build on Nova) — the HARD/IMP centerpiece; findings only (L1).
- **Analytics/Reporting** — audit + spend rollup, drafted email digest.
- **Acceptance:** J1 and J3 work end-to-end; Recon surfaces discrepancies with
  advisory note; audit explorer shows vendor-centric history.

### Phase 2 — Orchestration + `(1)`
- **CFO orchestrator** with validated plans; J2 flow (Recon → gate → Payable).
- **Payable Agent** (prepare + gated initiate over existing disbursement).
- **Contract/Document Agent** (extend OCR).
- **Acceptance:** J2 and J4 work; payment strictly gated + OTP.

### Phase 3 — `(2)` domains
- **TDS**, **GST**, **Receivable** agents.
- **Communication Agent** hardened (templates, caps, threading) — and move email
  off Gmail App Password.
- **Acceptance:** month-close goal spans multiple domain agents under one CFO run.

### Cross-cutting throughout
- Autonomy config UI, cost budgets, ops dashboard, exports for auditors.

---

## 21. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **LLM sets/edits money values** | Wrong payments | Deterministic tools for all amounts; LLM advisory only; gates + caps |
| **Over-automation erodes trust** | Adoption failure | Human-in-loop defaults (L0/L1); gates on all side effects; full audit |
| **Recon accuracy (HARD)** | False clears/flags | Reuse proven Nova logic; tolerance bands; precision/recall KPIs before raising autonomy |
| **Email deliverability** | Follow-ups fail silently | Move off Gmail App Password to SES/SendGrid; send caps; failure surfaced (not silent) |
| **Cost blowout** | Runaway LLM spend | Per-tenant budgets; token/cost per run; cap enforcement |
| **PII leakage in audit** | Compliance breach | Store results not raw docs; redact account numbers; secrets never logged |
| **Double execution on retry** | Duplicate payments | Idempotency keys on side-effecting tools |
| **Plan hallucination** | Invalid orchestration | Constrain CFO to registered agents + validated plan schema |
| **Tenant bleed** | Data isolation breach | `tenant_id` enforced in every tool + query; tests for isolation |

---

## 22. Open questions

1. **Autonomy default** — ship at L0/L1 (suggest/draft) org-wide first, or allow
   L2 for Verification immediately?
2. **First deliverable order** — framework-first (Phase 0 complete) vs a vertical
   slice (Verification end-to-end incl. minimal UI) to demo value sooner?
3. **CFO planner** — LLM-generated plans from day one, or start with
   hardcoded/templated plans for known goals and add LLM planning later?
4. **Trigger model** — start user-initiated only, or wire event triggers (on bill
   ingest/approval) in Phase 1?
5. **Email provider** — commit to SES/SendGrid now (blocks Communication agent)?
6. **Receivable scope** — is AR in scope for this platform now, or defer (it's the
   least-built domain)?
7. **Cost budget policy** — who sets per-tenant caps, and what's the default?

---

## 23. Appendix

### 23.1 Whiteboard transcription
- **CFO A₁ (MASTER)** — top; branches to Analytics/Reporting `(0)(MEDIUM)` and
  Follow-up (Emails).
- Left: **AGENT AUDITS → OUTCOME**: WHY, WHAT, WHOM, HOW.
- Children of CFO: **TDS A₁ `(2)`** (filings, reporting, checker/correct);
  **GST A₁ `(2)`** (filings, reporting, check/correct, vendor filing checker);
  **RECON A₁ `(0)(HARD)(IMP)`** (ERP pull/POS, mismatch handling, banking recon,
  2-way/3-way PO, payment check); **PAYABLE A₁ (bills)** (chain activity/
  follow-ups, redistribution of cash between vendors); **RECEIVABLE A₁ `(2)`**
  (tracking, follow-up, creation INV, reconciliation).
- Bottom (CHILD bracket): **VERIFICATION A₁ `(0)`** (vendor verify GST/TDS/PAN,
  MSME vendor, bank account → easy compliance audit / trust); **CONTRACT/
  DOCUMENT `(1)(MEDIUM)`** (parsing, persistence); **COMMUNICATION Agent**
  (email agents).

### 23.2 Priority summary
See the full **Priority board** in §13. Condensed:

| Priority | Agents |
|---|---|
| 🏗️ **F** foundation | CFO runtime + audit store; CFO orchestrator (Phase 2) |
| 🔴 **P0** first | Recon (🔥 HARD, ⭐ IMP), Verification, Analytics/Reporting (🟦 MED) |
| 🟠 **P1** next | Contract/Document (🟦 MED) |
| 🟡 **P2** later | TDS, GST, Receivable |
| ⚫ unnumbered | Payable (follows Recon) |
| 🔧 **Util** child | Verification, Contract/Document, Communication |

### 23.3 Module → agent reuse index
| Existing module | Feeds agent |
|---|---|
| `sandbox_kyb.py` | Verification, GST |
| `billing_api.py` (Nova) | Recon |
| `disbursement_api.py` | Payable |
| `ocr_engine.py` / `ingest.py` | Contract/Document, Recon |
| `fx_api.py` | Analytics, Recon |
| `tds_api.py` | TDS |
| `tally_agent_api.py` | Recon (ERP pull) |
| SMTP / `vendor_outreach_log` | Communication |

---

*Next step: resolve §22 (esp. Q1–Q3), then write the Phase 0 implementation plan
(tables + runtime SDK + `agents_api.py` + Hub shell).*
