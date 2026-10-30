# Verification Agent

| | |
|---|---|
| **Agent name** | `verification` |
| **Fleet card** | "GSTIN, PAN, bank and MSME checks → trust score." |
| **Priority** | 🔴 **P0** · 🔧 Utility (called by Recon, Payable, GST) |
| **Status** | 🟡 Partial — the three KYB calls are built; the agent wrapper, MSME check and trust score are not |
| **Default autonomy** | **L2** — act with gate (checks are read-only; the gate is on *marking a vendor payable*) |
| **Difficulty** | Easy — the highest-value-per-effort agent in the fleet |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.3 |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Answer one question with evidence: **is this vendor safe to transact with?**

The platform already calls Sandbox.co.in for GSTIN, PAN and bank verification, but
the results are scattered across `supplier_gstins`, `supplier_bank_accounts` and
`dim_suppliers`, each written by a different worker, with no single verdict and no
record of *why* a check ran. This agent composes them into one auditable run that
produces a **trust score** and a **payable / not-payable flag** other agents can
depend on.

It is a utility agent: Payable refuses to prepare a disbursement without a green
verification, and Recon annotates findings with vendor trust.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Vendor created | `event` | `vendor.created` — new row in `dim_suppliers` |
| GSTIN / PAN edited | `event` | `vendor.tax_id_changed` — invalidates the prior verdict |
| Bank account added or changed | `event` | `vendor.bank_changed` — **always** re-verifies; this is the payment-fraud surface |
| Called by another agent | `agent` | Payable pre-flight, GST vendor-filing check |
| User action | `user` | "Verify now" on the vendor detail page |
| Staleness sweep | `schedule` | Nightly: re-verify any vendor whose verdict is older than `config.max_age_days` (default 90) |

## 3. Inputs

```json
{
  "vendor_id": "uuid",          // required — dim_suppliers.supplier_id
  "checks": ["gstin","pan","bank","msme"],   // optional, defaults to all configured
  "force": false                // ignore a cached fresh verdict
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `verify_gstin(gstin)` | **reuse** | [`sandbox_kyb.verify_gstin`](../../backend_ocr/app/sandbox_kyb.py) — returns status, legal name, address, registration date |
| `verify_pan(pan, name)` | **reuse** | [`sandbox_kyb.verify_pan`](../../backend_ocr/app/sandbox_kyb.py) — validity + name match |
| `verify_bank(account, ifsc, name)` | **reuse** | [`sandbox_kyb.verify_bank_account`](../../backend_ocr/app/sandbox_kyb.py) — penny-less name match |
| `check_msme(udyam_or_pan)` | **new** | Udyam registration lookup. Drives MSME 45-day payment terms, so it is a payables input, not a nicety |
| `check_gst_filing_status(gstin)` | **wrap** | Sandbox "track return filing" per [Sandbox_GST_COMPLIANCE_INTERGRATION.md](../Sandbox_GST_COMPLIANCE_INTERGRATION.md) §3.2 — a vendor who stopped filing is an ITC risk |
| `compute_trust_score(results)` | **new** | Deterministic weighted score (§6). No LLM |
| `persist_verification(vendor_id, verdict)` | **wrap** | Writes across `supplier_gstins`, `supplier_bank_accounts`, `dim_suppliers`; logic exists inside [`gst_verification.py`](../../pipelines/gst_verification.py) and [`bank_verification.py`](../../pipelines/bank_verification.py) but is currently worker-shaped, not tool-shaped |

**Token handling:** `sandbox_kyb._get_token` already caches the Sandbox JWT with a
force-reauth retry (`_call_with_retry`). The agent must not re-implement auth.

## 5. Steps

1. **Load** vendor identity — GSTIN, PAN, legal name, bank rows.
2. **Short-circuit** if a verdict exists, is younger than `max_age_days`, and no
   identity field changed since. Record the run as `succeeded` with
   `action: "cache hit"` — a skipped check still gets an audit row.
3. **Run configured checks in parallel**, each as its own `steps[]` entry with
   latency. One failing check does not abort the others.
4. **Name-match reconciliation** — compare the legal name returned by GSTIN, PAN
   and bank against `dim_suppliers.supplier_name`. Divergence is a flag, not a
   failure; fuzzy match with an explicit threshold, and put the score in the step
   log so a reviewer can see how close it was.
5. **Compute trust score** deterministically, derive `payable` boolean.
6. **Persist** and emit flags.
7. **Gate (L2)** — if the verdict flips a vendor from not-payable to payable, or
   any bank detail changed, open an approval gate. Everything else auto-commits.

## 6. Outcome schema

```json
{
  "vendor_id": "d0e0...002",
  "gstin":  {"status": "ACTIVE",     "legal_name": "…", "checked_at": "…"},
  "pan":    {"status": "VALID",      "name_match": 0.97},
  "bank":   {"status": "NAME_MATCH", "account_masked": "XXXX4321", "ifsc": "…"},
  "msme":   {"registered": true, "udyam": "UDYAM-…", "category": "SMALL"},
  "gst_filing": {"last_filed_period": "2026-06", "lapsed": false},
  "trust_score": 0.94,
  "band": "GREEN",
  "payable": true,
  "flags": []
}
```

**Scoring (deterministic, tenant-tunable in `agent_registry.config`):**

| Signal | Weight | Notes |
|---|---|---|
| GSTIN active | 0.30 | Cancelled/suspended → hard fail, score capped at RED |
| PAN valid + name match ≥ 0.90 | 0.25 | |
| Bank name match | 0.30 | Mismatch → hard fail; this is the fraud surface |
| GST filings current | 0.10 | Lapsed > 2 periods → flag `FILING_LAPSED` |
| MSME registered | 0.05 | Never reduces the score; sets payment-term expectations |

Bands: **GREEN** ≥ 0.85 → `payable: true` · **AMBER** 0.60–0.85 → payable with a
gate · **RED** < 0.60 or any hard fail → `payable: false`.

Hard fails are absolute — they are not outvoted by a high weighted total.

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: event`, `trigger_ref: vendor.bank_changed`, `reason: "Bank account replaced — re-verifying before any payment"` |
| **WHOM** | `entity_type: vendor`, `entity_id: <supplier_id>` |
| **WHAT** | `goal: "Verify vendor KYB"`, `action: "GSTIN+PAN+bank+MSME; trust 0.94 GREEN"`, `outcome:` above |
| **HOW** | `tools_used: [verify_gstin, verify_pan, verify_bank, check_msme]`, `steps:` one entry per call with result and ms |

**PII rule:** `steps[]` stores *verdicts and digests*, never raw KYB documents or
full account numbers. Account numbers are masked at the tool boundary
(`disbursement_api._mask_account` is the existing precedent) before they can reach
the audit store.

## 8. Guardrails

- **Read-only against the outside world.** No Sandbox endpoint used here mutates
  anything.
- **Never auto-clears a hard fail.** A RED verdict cannot be raised by re-running;
  it takes a human override, recorded as its own run.
- **Rate limits.** Sandbox is a metered third party — cache verdicts, respect
  `int_connector_limits`, and back off rather than retry-storm.
- **Cost ceiling.** Cap checks per vendor per day (`config.max_checks_per_day`,
  default 5) so a trigger loop cannot burn the Sandbox quota.
- **No LLM in the verdict path.** Every field in the outcome is derived from an
  API response or arithmetic.

## 9. Data model

**Reuses:** `dim_suppliers`, `supplier_gstins`, `supplier_bank_accounts`,
`supplier_gstns`, `vendor_documents`.

**New:**

```sql
-- One row per verdict, append-only, so trust over time is auditable.
CREATE TABLE IF NOT EXISTS vendor_verification_verdicts (
    verdict_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid        NOT NULL,
    supplier_id  uuid        NOT NULL,
    run_id       uuid        REFERENCES agent_runs(run_id),
    trust_score  numeric(4,3) NOT NULL,
    band         text        NOT NULL,          -- GREEN|AMBER|RED
    payable      boolean     NOT NULL,
    results      jsonb       NOT NULL,          -- per-check detail
    flags        text[]      NOT NULL DEFAULT '{}',
    is_current   boolean     NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vvv_current
    ON vendor_verification_verdicts (tenant_id, supplier_id) WHERE is_current;
```

The `is_current` supersede pattern mirrors `fx_agent_notes` — new verdict flips
the prior row false in the same transaction.

## 10. API

Standard Hub surface (see [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §15):
`POST /api/agents/verification/run`, `GET /api/agents/runs/{run_id}`.

Agent-specific reads:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/vendors/{supplier_id}/verification` | Current verdict + history, for the vendor detail page |
| `POST` | `/api/vendors/{supplier_id}/verification/override` | Human override of a RED verdict, with mandatory reason |

## 11. Build plan

**Step 1 — tool wrappers (½ day).** Give the three `sandbox_kyb` functions
agent-callable signatures returning normalized `{status, detail, ms}`. No logic
change.

**Step 2 — verdict table + scoring (1 day).** `vendor_verification_verdicts`,
`compute_trust_score`, `persist_verification`. Pure functions, unit-testable
without Sandbox.

**Step 3 — agent registration (½ day).** Register `verification` with the runtime,
wire the four triggers, emit the audit row.

**Step 4 — MSME + filing-status checks (1–2 days).** New Sandbox calls. MSME first
— it has a legal payment-terms consequence.

**Step 5 — UI (1 day).** Verdict badge on vendor detail, run console, override
flow.

**Total ≈ 4–5 days.** Deliberately the first agent built: it proves the audit
contract end-to-end against real third-party calls while being nearly free to
implement.

## 12. KPIs

| KPI | Target |
|---|---|
| Verification turnaround (trigger → verdict) | < 30 s p50 |
| Auto-clear rate (GREEN without human touch) | ≥ 80% |
| Sandbox calls per vendor per month | ≤ 6 (cache working) |
| Payments blocked on stale/missing verification | 0 |
| False RED rate (overridden verdicts) | < 5% |

## 13. Open questions

1. **Bank re-verification on every change, or only above an amount threshold?**
   Sandbox charges per call; changing a bank account is also the single most
   common vendor-fraud vector. Recommendation: always re-verify, no threshold.
2. **Does an AMBER vendor block Payable, or gate it?** Gate is the proposal —
   blocking will get worked around outside the system.
3. **Verdict TTL** — 90 days assumed. GSTIN status can change sooner; the nightly
   sweep may need to be filing-cycle-aware instead of age-based.
4. **MSME source** — is Udyam lookup available on the current Sandbox plan, or
   does this need a second provider?
