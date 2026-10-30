# Agent Hub — Fleet Specs

One spec per agent on the Hub fleet board. Each doc is the buildable contract for
that agent: triggers, tools (with the module that already implements them), steps,
outcome schema, audit mapping, guardrails, and a phased build plan.

The system-level design — runtime, registry, `agent_runs` audit store, autonomy
levels, orchestration flows — lives in **[PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md)**.
These docs deliberately do not restate it; they reference it.

---

## Fleet

| # | Agent | Spec | Priority | Status | Default autonomy |
|---|---|---|---|---|---|
| 0 | **Vendor Bill** | [VENDOR_BILL_AGENT.md](../VENDOR_BILL_AGENT.md) | shipped | ✅ live | L1 (drafts only) |
| 1 | **Verification** | [VERIFICATION_AGENT.md](VERIFICATION_AGENT.md) | 🔴 P0 · 🔧 Util | 🟡 tools exist | L2 |
| 2 | **Recon** | [RECON_AGENT.md](RECON_AGENT.md) | 🔴 P0 · 🔥 HARD · ⭐ IMP | 🟡 partial | L1 |
| 3 | **Analytics** | [ANALYTICS_AGENT.md](ANALYTICS_AGENT.md) | 🔴 P0 · 🟦 MED | 🟡 partial | L1 |
| 4 | **CFO Orchestrator** | [CFO_ORCHESTRATOR_AGENT.md](CFO_ORCHESTRATOR_AGENT.md) | 🏗️ Phase 2 | ❌ new | L1 |
| 5 | **Payable** | [PAYABLE_AGENT.md](PAYABLE_AGENT.md) | ⚫ NEXT (follows Recon) | 🟡 partial | L1 → L2 |
| 6 | **Contract** | [CONTRACT_AGENT.md](CONTRACT_AGENT.md) | 🟠 P1 · 🔧 Util | 🟡 partial | L1 |
| 7 | **TDS** | [TDS_AGENT.md](TDS_AGENT.md) | 🟡 P2 | 🟡 partial | L0 → L1 |
| 8 | **GST** | [GST_AGENT.md](GST_AGENT.md) | 🟡 P2 | 🟡 partial | L0 → L1 |
| 9 | **Receivable** | [RECEIVABLE_AGENT.md](RECEIVABLE_AGENT.md) | 🟡 P2 | ❌ new | L1 |
| — | **Communication** | [COMMUNICATION_AGENT.md](COMMUNICATION_AGENT.md) | 🔧 Util (shared) | 🟡 partial | L1 |

**Communication is not a fleet card** but four agents (Payable, Receivable,
Analytics, GST) call it for outbound email. It is specced here because none of
them are implementable without a single gated, rate-capped send path.

---

## Reading order for implementation

```
Phase 0  agent runtime + agent_runs + agent_registry     ← PRD_AGENT_HUB §11, blocks everything
Phase 1  Verification → Recon → Analytics                ← P0, proves the audit trail E2E
Phase 2  CFO Orchestrator → Payable                      ← composition + first gated money path
Phase 3  Contract → TDS → GST → Receivable               ← domain depth
         Communication hardened alongside Phase 1
```

Nothing in Phase 1+ ships before Phase 0. Every agent below assumes the runtime
exists and that `run()` returns a `run_id` with a complete WHY/WHAT/WHOM/HOW row.

---

## Spec template

Every doc follows the same 13 sections so they can be diffed and reviewed
against each other:

1. Purpose · 2. Triggers · 3. Inputs · 4. Tools · 5. Steps · 6. Outcome schema ·
7. Audit record · 8. Guardrails · 9. Data model · 10. API · 11. Build plan ·
12. KPIs · 13. Open questions

**Status legend:** ✅ built · 🟡 partial (module exists, agent wrapper doesn't) ·
❌ new. **Tool column `Source`:** `reuse` = call existing code as-is ·
`wrap` = existing logic, needs an agent-callable tool signature · `new` = build it.

---

## Conventions that apply to all agents

- **Tools, not SQL.** An agent never writes raw SQL against domain tables. It
  calls a registered tool; the tool owns the query and the `tenant_id` scope.
- **Money is deterministic.** Amounts, rates, tolerances and thresholds are
  computed in Python/SQL. The LLM may classify, summarize and draft — it never
  decides a number that reaches a payment or a filing.
- **Every side effect is gated** unless the tenant explicitly raised that agent's
  autonomy level, and gates carry the exact payload the human is approving.
- **Idempotency keys** on every side-effecting tool, so a retried run cannot
  double-pay or double-file.
- **Tenant isolation** comes from the JWT (`auth.py` global dependency); a
  client-supplied `company_id` is never trusted.
