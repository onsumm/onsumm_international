# Payable Agent

| | |
|---|---|
| **Agent name** | `payable` |
| **Fleet card** | "Gated disbursement, OTP, chain follow-ups." · **NEXT** |
| **Priority** | ⚫ Unnumbered on the board — follows Recon |
| **Status** | 🟡 Partial — disbursement, OTP and approval chain are built; the agent layer, follow-up automation and cash allocation are not |
| **Default autonomy** | **L1** — prepare only. L2 (act-with-gate) once trusted. **L3 is out of scope** |
| **Difficulty** | Medium to build, highest consequence in the fleet |
| **Parent spec** | [PRD_AGENT_HUB.md](../PRD_AGENT_HUB.md) §13.6 |
| **Related** | [DISBURSEMENT_IMPS_API.md](../DISBURSEMENT_IMPS_API.md), [DISBURSEMENT_PAYMENT_INTEGRATION_PLAN.md](../DISBURSEMENT_PAYMENT_INTEGRATION_PLAN.md), [PAYMENT_GUARDRAILS.md](../PAYMENT_GUARDRAILS.md) |
| **Last updated** | 2026-08-06 |

---

## 1. Purpose

Get the right amount to the right verified vendor at the right time — with a human
signature on every rupee that leaves.

The pieces exist: [`disbursement_api.py`](../../backend_ocr/app/disbursement_api.py)
prepares and initiates IMPS disbursements with OTP verification, status refresh and
an audit trail; [`pipelines/approval.py`](../../pipelines/approval.py) drives the
approval chain, nudges and escalations. What is missing is the layer that decides
**what to pay next and why**, chases stalled approvals, and allocates limited cash
across competing bills.

**This agent is the reason the guardrails in this fleet are written the way they
are.** Everything else produces information; this one moves money.

## 2. Triggers

| Trigger | Type | Detail |
|---|---|---|
| Recon clean + bill approved | `event` | `bill.approved` **and** recon within tolerance → prepare |
| User action | `user` | "Pay" on bill or vendor payment page |
| Delegated | `agent` | CFO plan step 3 (`close_vendor_period`) |
| Approval stalled | `schedule` | Chain step overdue → follow-up via [`communication`](COMMUNICATION_AGENT.md) |
| Due-date sweep | `schedule` | Daily: bills due within N days, MSME 45-day terms, early-payment discounts |
| Payment status unresolved | `schedule` | `PENDING`/`IN_PROCESS` disbursements → refresh status |

## 3. Inputs

```json
{
  "mode": "prepare|follow_up|allocate|status_sweep",
  "bill_ids": ["uuid"],
  "vendor_id": "uuid",
  "available_cash_inr": 2500000,     // allocate mode only
  "value_date": "2026-08-07",
  "method": "IMPS"
}
```

## 4. Tools

| Tool | Source | Implementation |
|---|---|---|
| `get_payment_profile(supplier_id)` | **reuse** | `GET /api/vendors/{supplier_id}/payment-profile` — verified account, KYB state, masked details |
| `check_verification(vendor_id)` | **reuse** | [`verification`](VERIFICATION_AGENT.md) verdict — **hard precondition** |
| `check_recon(bill_id)` | **reuse** | [`recon`](RECON_AGENT.md) outcome — `within_tolerance`, `recommended_action` |
| `compute_payable_amount(bill_id)` | **wrap** | Invoice total − TDS ([`utils/tds_engine.py`](../../utils/tds_engine.py)) − credit notes − recon-agreed deductions. **Pure arithmetic** |
| `prepare_disbursement(...)` | **reuse** | `POST /api/bills/{invoice_id}/disbursements` — creates the record, sends OTP, does **not** move money |
| `initiate_imps(disbursement_id, otp)` | **reuse** | `POST /api/disbursements/{id}/verify-otp` → `_mock_imps_initiate` |
| `bulk_prepare(supplier_id, bills)` | **reuse** | `POST /api/vendors/{supplier_id}/disbursements/bulk` + `batch-verify-otp` |
| `check_payment_status(id)` | **reuse** | `POST /api/disbursements/{id}/refresh-status` |
| `advance_approval_chain(bill_id)` | **wrap** | `bill_approval_chain` + `wf_tasks` in [`approval.py`](../../pipelines/approval.py) |
| `nudge_approver(task_id)` | **wrap** | `wf_nudges` — existing nudge machinery |
| `allocate_cash(cash, candidates)` | **new** | Deterministic prioritized allocation (§6) |
| `send_remittance_advice(...)` | **new** | Via [`communication`](COMMUNICATION_AGENT.md), after settlement |

## 5. Steps

**Prepare mode:**

1. **Pre-flight, all hard preconditions.** Any failure aborts with a specific
   reason — never a partial prepare:
   - vendor verification is current and `payable: true`;
   - bank account exists and is name-matched (`supplier_bank_accounts`);
   - bill is approved through its full chain;
   - recon exists and is `within_tolerance`, or a human explicitly overrode it;
   - no existing non-failed disbursement for this bill (**duplicate-payment
     check**, the single most important line in this document);
   - amount within the tenant's per-run and per-day caps.
2. **Compute the payable amount** deterministically, itemizing every deduction.
3. **Prepare the disbursement** — record created in `PENDING_OTP`, no money moved.
4. **Gate.** The approval payload states it plainly: *"Pay ₹3,98,000 to Signzy
   Technologies, A/C XXXX4321, IFSC HDFC0000123, via IMPS. Bill INV-2026-06-114.
   TDS ₹8,000 deducted under 194J."* Approving a vague payload is not approving.
5. **On approval → OTP.** OTP goes to the *human*, verified via the existing
   endpoint. **The agent never possesses or auto-fills an OTP.**
6. **Initiate**, with an idempotency key derived from
   `(tenant, bill_id, amount, value_date)`.
7. **Track** to a terminal state; on failure surface it loudly and do not retry
   automatically.
8. **Remittance advice** to the vendor after settlement (gated at L1).

**Follow-up mode:** find overdue chain steps → nudge → escalate per
`wf_escalations` → report who is blocking what.

**Allocate mode:** given available cash, rank candidate bills and propose a payment
run. **Proposal only** — each payment still prepares and gates individually.

## 6. Outcome schema

```json
{
  "mode": "prepare",
  "bill_id": "…", "vendor": "Signzy Technologies",
  "amount": {
    "invoice_total": 412500.00,
    "deductions": [
      {"type": "TDS", "section": "194J", "rate": 2.0, "amount": 8000.00},
      {"type": "RECON_AGREED", "amount": 14500.00, "ref": "discrepancy:…"}
    ],
    "payable_inr": 390000.00
  },
  "preconditions": {
    "verification": {"band": "GREEN", "payable": true},
    "recon": {"within_tolerance": true, "variance_pct": 3.64},
    "approval_chain": {"complete": true, "approvers": ["…"]},
    "duplicate_check": "clear",
    "cap_check": {"per_run_limit": 500000, "within": true}
  },
  "disbursement_id": "…",
  "status": "awaiting_approval",
  "beneficiary": {"name": "Signzy Technologies", "account_masked": "XXXX4321", "ifsc": "HDFC0000123"},
  "method": "IMPS",
  "idempotency_key": "…"
}
```

**Allocation ranking (deterministic, config-weighted):**

1. Statutory/contractual deadlines first — **MSME 45-day** obligations, then
   contractual due dates.
2. Early-payment discount value (net benefit, computed).
3. Overdue ageing.
4. Vendor criticality flag.
5. Amount, ascending — clear more obligations per rupee.

No LLM anywhere in this ranking, and the weights are visible in the outcome so a
controller can see why a bill ranked where it did.

## 7. Audit record

| Axis | Value |
|---|---|
| **WHY** | `trigger_type: agent`, `trigger_ref: cfo:plan:<id>`, `reason: "Recon clean, bill approved — preparing payment"` |
| **WHOM** | `entity_type: bill`, `entity_id: <invoice_id>` |
| **WHAT** | `action: "Prepared ₹3,90,000 IMPS to Signzy; awaiting approval"`, `outcome:` above |
| **HOW** | `tools_used: [get_payment_profile, check_verification, check_recon, compute_payable_amount, prepare_disbursement]`; `steps[]` logs **every precondition with its result**, plus the arithmetic of each deduction |

`disbursement_api._audit` already writes old/new state per disbursement change.
Link it to `agent_runs.run_id` rather than duplicating it — one payment, one
narrative, two levels of detail.

**Never in the audit store:** OTPs, OTP hashes, full account numbers. Masking
happens at the tool boundary (`_mask_account`, `_mask_email` exist for this).

## 8. Guardrails

Beyond [PAYMENT_GUARDRAILS.md](../PAYMENT_GUARDRAILS.md):

- **Verified vendors only.** No verification, or not `payable` → refuse. Not a
  warning; a refusal.
- **Duplicate-payment check** against all non-failed disbursements for the bill,
  plus the idempotency key at initiation. Two independent defenses because one is
  not enough for this failure mode.
- **Human OTP, always.** The OTP is the human's signature. An agent that can obtain
  an OTP has defeated the gate.
- **Caps** — per-run, per-day, per-vendor amount limits in `agent_registry.config`,
  checked before prepare *and* re-checked at initiation.
- **No autonomous retry.** A failed payment stays failed until a human acts.
- **L3 is disallowed** for this agent regardless of tenant config. There is no
  configuration in which unattended payment initiation is correct here.
- **Amounts are never LLM-derived.** The LLM may draft remittance-advice prose. It
  may not see or set a figure.
- **Bank change freeze** — if the beneficiary account changed within
  `config.bank_change_freeze_hours` (default 24), refuse and require re-verification.
  This is the standard defense against payment-redirection fraud.
- **Value-date sanity** — no back-dating, no dates beyond a configured horizon.

## 9. Data model

**Reuses:** `fact_disbursements`, `supplier_bank_accounts`, `dim_suppliers`,
`fact_invoices`, `bill_approval_chain`, `tenant_chain_config`, `wf_tasks`,
`wf_nudges`, `wf_escalations`, `wf_audit_log`, `payment_schedules`,
`payment_transactions`, `fact_tds_ledger`.

**New:**

```sql
-- A proposed payment run from allocate mode. Proposal, not execution.
CREATE TABLE IF NOT EXISTS payment_runs (
    payment_run_id uuid       PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid       NOT NULL,
    run_id         uuid       REFERENCES agent_runs(run_id),
    available_cash numeric(18,2) NOT NULL,
    value_date     date       NOT NULL,
    allocations    jsonb      NOT NULL,   -- [{bill_id, amount, rank, reason}]
    total_allocated numeric(18,2) NOT NULL,
    status         text       NOT NULL DEFAULT 'proposed',  -- proposed|accepted|discarded
    created_at     timestamptz NOT NULL DEFAULT now()
);
```

`fact_disbursements` may need an `agent_run_id` column so a payment points back at
the run that prepared it.

## 10. API

Existing (keep): the full `disbursement_api` surface — payment profile, prepare,
bulk, OTP verify/resend, batch verify, refresh status, stats.

New:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/agents/payable/run` | Prepare / follow-up / allocate / status-sweep |
| `GET` | `/api/agents/payable/queue` | What the agent would pay next, ranked, with reasons |
| `POST` | `/api/agents/payable/allocate` | Propose a payment run for available cash |
| `GET` | `/api/agents/payable/blocked` | Bills blocked and precisely why (unverified, recon variance, stalled approver) |

`/blocked` is the endpoint a controller will actually live in — "why hasn't this
been paid" is the daily question.

## 11. Build plan

**Prerequisites:** [`verification`](VERIFICATION_AGENT.md) shipped;
[`recon`](RECON_AGENT.md) usage mode shipped. Both are hard preconditions in step 1
— building Payable first means building it without its safety interlocks.

**Step 1 — precondition engine (2 days).** All checks including the duplicate-payment
check, as pure testable functions. Build and test this before anything can prepare.

**Step 2 — amount computation (2 days).** Invoice − TDS − credit notes − agreed
deductions, itemized. Reuse `tds_engine`.

**Step 3 — agent wrapper + gate (2 days).** Register the agent, wire the existing
prepare path, build the explicit gate payload, link `_audit` to `run_id`.

**Step 4 — status sweep (1 day).** Scheduled `refresh-status` for non-terminal
disbursements. Small and immediately useful.

**Step 5 — follow-up mode (2–3 days).** Overdue chain detection, nudges,
escalation. Depends on [`communication`](COMMUNICATION_AGENT.md).

**Step 6 — allocation (3 days).** Ranking, `payment_runs`, the `/queue` and
`/blocked` views.

**Step 7 — remittance advice (1 day).**

**Total ≈ 13–16 days.** Steps 1–4 are the safe core and are worth shipping alone;
5–7 are convenience on top.

## 12. KPIs

| KPI | Target |
|---|---|
| Duplicate payments | **0** — non-negotiable |
| Payments to unverified vendors | **0** |
| Preparation-to-approval median | < 4 h |
| Manual data entry per payment | → 0 fields |
| MSME 45-day breaches | 0 |
| Early-payment discounts captured | ≥ 80% of available |
| Failed disbursements needing manual investigation | < 2% |

## 13. Open questions

1. **When do real banking rails replace the IMPS mock?** Everything above is written
   for real money. The mock (`_mock_imps_initiate`) makes this agent safe to build
   now and dangerous to leave half-audited when rails switch. Treat the switchover
   as its own reviewed change.
2. **Does an AMBER verification gate or block a payment?** Proposal: gate with the
   verification detail in the payload.
3. **Batch OTP scope** — one OTP per vendor batch (current bulk behavior) or per
   payment? Per-payment is safer, worse UX at volume.
4. **Who owns caps** — platform admin or tenant controller? Tenant-settable caps can
   be raised by whoever is compromised.
5. **Partial payments** — is paying an agreed-down amount against a disputed bill
   allowed, and does it need its own approval type?
6. **Cash position source** — is `available_cash_inr` entered manually, or read from
   a bank balance? Allocation quality depends entirely on this.
