# CFO Orchestrator Agent

| | |
|---|---|
| **Agent name** | `cfo` |
| **Fleet card** | "Plans a goal, delegates it across the fleet." · **PHASE 2** |
| **Priority** | 🏗️ Foundation, but sequenced **after** the P0 agents |
| **Status** | ❌ New |
| **Default autonomy** | **L1** — produces a plan, executes read-only steps, gates every side effect |
| **Difficulty** | Medium mechanically, high in failure modes |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.1, §14 |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Turn a goal into a sequence of agent runs: *"reconcile and prepare payment for
Signzy for June"* → Verification → Recon → gate → Payable, with one parent run
tying the whole chain together.

The orchestrator is the fleet's only planner. It owns no finance logic whatsoever —
its entire job is decomposition, sequencing, gate placement, and rollup. Every
number it reports comes from a child run.

**It is deliberately Phase 2.** Orchestrating agents that don't exist yet is
worthless, and orchestrating agents whose outcome schemas are still moving is
actively expensive. Verification, Recon and Analytics must be stable first.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| User goal | `user` | Natural-language or structured goal from the CFO Console |
| Month close | `schedule` | "Close the month for all vendors" — fans out per vendor |
| Composite event | `event` | e.g. `bill.approval_requested` on a high-value bill → verify + recon + prepare |
| Resumption | `event` | A gate was approved → continue the paused plan |

## 3. Inputs

```json
{
  "goal": "Reconcile and prepare payment for Signzy for June 2026",
  "goal_structured": {
    "intent": "close_vendor_period",
    "vendor_id": "uuid", "period": "2026-06"
  },
  "scope": {"cost_center_id": null, "max_amount_inr": 500000},
  "dry_run": false
}
```

`goal_structured` is optional; when absent the planner derives it and **echoes the
derived intent back for confirmation** before executing anything with a side
effect. Free-text goals are parsed, not obeyed.

## 4. Tools

The orchestrator has almost no tools by design. It has **delegation** and **read**.

| Tool | Source | Implementation |
|---|---|---|
| `list_agents()` | **new** | Registry read — which agents are enabled, at what autonomy, with what caps |
| `plan(goal, registry)` | **new** | LLM produces a plan; **validated against a schema and the registry** before a single step runs |
| `invoke_agent(name, inputs, parent_run_id)` | **new** | Runtime call. Only registered, enabled agents |
| `wait_for_gate(approval_id)` | **new** | Suspend the plan; resume on decision |
| `query_runs(filters)` | **reuse** | Shared with [`analytics`](ANALYTICS_AGENT.md) — for rollup |
| `summarize_outcomes(child_runs)` | **new** | LLM narrative over child outcomes |

**No direct tool has a side effect.** The orchestrator cannot pay, file, or email.
It can only ask an agent that can, and every such agent gates.

## 5. Steps

1. **Parse the goal** into `{intent, entities, period, constraints}`. Unrecognized
   intent → fail fast with the supported intent list. It must not improvise a plan
   for a goal it doesn't understand.
2. **Build a plan** from the intent using a **library of plan templates**, with the
   LLM selecting and parameterizing a template rather than inventing a DAG.
3. **Validate the plan**: every step names a registered, enabled agent; inputs match
   that agent's input schema; every side-effecting step carries a gate; total
   projected cost is within budget. Invalid → fail, never "best effort".
4. **Confirm** (free-text goals, or when the plan includes a money step): show the
   plan and wait. This is the plan-level gate, distinct from step gates.
5. **Execute sequentially**, each child run carrying `parent_run_id`. Independent
   steps may run in parallel; anything touching money stays serial.
6. **Evaluate branch conditions** deterministically between steps — e.g. *proceed
   to Payable only if* `recon.within_tolerance` **and** `verification.payable`.
   Conditions are expressions over child outcome fields, never an LLM judgement.
7. **Suspend at gates**, persisting plan state so approval can arrive hours later.
8. **Roll up** — aggregate child outcomes, narrate, report.
9. **On child failure**: halt the branch, mark the plan `partially_completed`,
   report exactly which steps ran. Never retry a side-effecting step automatically.

## 6. Outcome schema

```json
{
  "goal": "Reconcile and prepare payment for Signzy for June 2026",
  "intent": "close_vendor_period",
  "plan": [
    {"seq": 1, "agent": "verification", "inputs": {"vendor_id": "…"}, "gate": null},
    {"seq": 2, "agent": "recon", "inputs": {"vendor_id": "…", "period": "2026-06"},
     "gate": null, "condition": "verification.payable == true"},
    {"seq": 3, "agent": "payable", "inputs": {"bill_id": "…"},
     "gate": "initiate_payment", "condition": "recon.within_tolerance == true"}
  ],
  "child_runs": [
    {"seq": 1, "run_id": "…", "agent": "verification", "status": "succeeded"},
    {"seq": 2, "run_id": "…", "agent": "recon", "status": "succeeded"},
    {"seq": 3, "run_id": "…", "agent": "payable", "status": "awaiting_approval"}
  ],
  "status": "awaiting_approval",
  "blocked_on": {"approval_id": "…", "gate": "initiate_payment",
                 "payload": {"amount_inr": 398000, "vendor": "Signzy", "method": "IMPS"}},
  "summary": "Signzy verified GREEN. June recon matched 18/20 lines with ₹14,500 variance (3.6%, within the 5% band). A ₹3,98,000 disbursement is prepared and awaiting your approval."
}
```

**Plan statuses:** `planned` · `running` · `awaiting_approval` ·
`partially_completed` · `succeeded` · `failed` · `cancelled`.

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: user`, `trigger_ref: <user_id>`, `reason: "<the goal, verbatim>"` |
| **WHOM** | `entity_type: vendor`, `entity_id: <supplier_id>` — the plan's primary subject |
| **WHAT** | `goal`, `action: "3-step plan; 2 completed, 1 awaiting approval"`, `outcome:` above |
| **HOW** | `tools_used: [list_agents, plan, invoke_agent]`; `steps[]` records the plan, each delegation, each condition evaluation with its result |

The parent run is the **one place** an auditor can start to see an entire "close
the month" operation, then descend into children via `parent_run_id`. Condition
evaluations must be logged with their inputs — "why did it skip the payment?" is
the question this agent will be asked most.

## 8. Guardrails

- **May only invoke registered, enabled agents** at their configured autonomy. It
  cannot elevate a child's autonomy, and it cannot call a tool directly.
- **Plan templates, not free-form DAGs.** The LLM parameterizes a known plan; it
  does not compose arbitrary agent graphs. This is the single most important
  constraint in this doc — a planner that can invent sequences over money-moving
  agents is an unbounded risk surface.
- **Branch conditions are deterministic expressions** over child outcomes.
- **Every side effect gates**, and the orchestrator cannot bypass a gate even at L3.
- **Step budget** — max steps per plan (default 10) and max fan-out per plan
  (default 25 vendors) so month-close cannot spawn unbounded work.
- **Token/cost budget per plan**, enforced before each delegation, not after.
- **No automatic retry of side-effecting steps.** Retrying a payment is a human
  decision.
- **Idempotent resumption** — resuming after a gate must not re-run completed steps.
- **Kill switch** — cancelling a plan halts pending steps; already-committed child
  effects are reported, not silently reversed.

## 9. Data model

**Reuses:** `agent_runs` (with `parent_run_id` — the whole plan tree),
`agent_approvals`, `agent_registry`, `agent_messages` (planner transcript).

**New:**

```sql
CREATE TABLE IF NOT EXISTS agent_plans (
    plan_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid        NOT NULL,
    run_id      uuid        NOT NULL REFERENCES agent_runs(run_id),  -- the parent run
    goal        text        NOT NULL,
    intent      text        NOT NULL,
    template    text,                                  -- which plan template
    steps       jsonb       NOT NULL,                  -- ordered, with gates + conditions
    cursor      smallint    NOT NULL DEFAULT 0,        -- next step to execute
    status      text        NOT NULL DEFAULT 'planned',
    budget_usd  numeric(10,4),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_plans_open
    ON agent_plans (tenant_id, status) WHERE status IN ('planned','running','awaiting_approval');
```

The `cursor` column is what makes suspend/resume across a human-scale approval
delay survivable, including across a backend restart.

## 10. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/cfo/goal` | Submit a goal → `{plan, plan_id, run_id}` |
| `POST` | `/api/agents/cfo/plans/{id}/confirm` | Approve the plan itself and begin execution |
| `GET` | `/api/agents/cfo/plans` | Open and recent plans |
| `GET` | `/api/agents/cfo/plans/{id}` | Plan + child run tree + current blocker |
| `POST` | `/api/agents/cfo/plans/{id}/cancel` | Halt pending steps |
| `GET` | `/api/agents/cfo/intents` | Supported intents + required inputs — also drives the Console's autocomplete |

## 11. Build plan

**Prerequisite:** Verification, Recon and Analytics live on the runtime with stable
outcome schemas. Building this earlier means rewriting it.

**Step 1 — intent + template library (2 days).** Start with exactly three:
`verify_vendor`, `reconcile_vendor_period`, `close_vendor_period`. Hard-coded,
schema-validated, no LLM in the loop yet.

**Step 2 — plan executor (3 days).** `agent_plans`, cursor, sequential execution,
`parent_run_id` linkage, deterministic condition evaluation.

**Step 3 — gate suspend/resume (2 days).** The genuinely tricky part: durable state
across a restart, resumption without re-running completed steps.

**Step 4 — LLM goal parsing (1–2 days).** Free text → intent + entities, with
confirmation echo. Only *after* templates work end-to-end with structured input.

**Step 5 — rollup + narrative (1 day).**

**Step 6 — CFO Console UI (3 days).** Goal box, plan preview with gates
highlighted, live child-run tree.

**Step 7 — fan-out plans (2 days).** Month-close across vendors, with the fan-out
cap enforced.

**Total ≈ 12–15 days** after the P0 agents are stable.

## 12. KPIs

| KPI | Target |
|---|---|
| Goal completion rate (no human step beyond gates) | ≥ 70% |
| Plan validation failures reaching execution | 0 |
| Human interventions per plan (excluding designed gates) | < 0.5 |
| Median plan wall-clock excluding gate wait | < 3 min |
| Plans stuck in `awaiting_approval` > 48 h | < 5% |
| Cost per plan | < $0.10 |

## 13. Open questions

1. **How far does template-only planning get us?** If real goals routinely fall
   outside three templates, the answer is more templates — not a free-form planner.
   Worth revisiting only with evidence from logged unmatched goals.
2. **Plan-level confirmation always, or only when money is involved?** Always is
   safer; it will be perceived as friction on read-only plans.
3. **Partial failure semantics** — if Recon succeeds and Payable fails, is the plan
   `partially_completed` (proposed) or `failed`? Affects retry UX.
4. **Concurrency** — may two plans touch the same vendor at once? Proposal: no,
   take an advisory lock per `(tenant, vendor)`.
5. **Should the orchestrator be allowed to *schedule* future work**, or only run
   now? Scheduling adds a durable-timer dependency.
