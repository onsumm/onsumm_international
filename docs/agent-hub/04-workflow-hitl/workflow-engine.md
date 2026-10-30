# Workflow Engine

**Stage:** 04 · Workflow & human-in-the-loop
**Role:** The task fabric that moves an entity through people

## What it does

The task fabric that moves an entity through people. A discrepancy or a bill becomes a `wf_task` (approve / escalate / nudge) with a priority, assignee, and due date; overdue tasks fire nudges across channels and escalate if still unaddressed. The **approval chain** enforces who signs off before payment.

## Capabilities

- `wf_task_type` enum: `NUDGE` · `ESCALATE` · `APPROVE`
- `nudge_channel` enum: `EMAIL` / `SMS` / `WHATSAPP` / `SLACK` / `IN_APP`
- Per-tenant approval chain configuration — not a hardcoded one-size-fits-all sign-off order

## Data model

### `wf_tasks`
`task_type`, `entity_type`, `status`, `due_at`, `priority`. The core task record — what needs doing, on what entity, by when.

### `wf_nudges`
`channel`, `template`, `status`. A reminder sent for an overdue task, across whichever channel the tenant has configured.

### `wf_escalations`
`escalated_to`, `reason`. When a nudge doesn't get a task actioned in time, it escalates to someone else with a recorded reason.

### `bill_approval_chain`
Approver stages — the ordered sequence of who must sign off on a bill before it can proceed to payment.

## Where it fits in the pipeline

**Upstream:** any judgment agent (reconciliation, tax, anomaly) that determines a bill needs human sign-off creates a task here.
**Downstream:** an `APPROVE` task, once actioned, is the gate that lets a bill proceed to the [Money gate — OTP/MFA](./money-gate-otp.md).

## Why it matters

Judgment agents can flag, score, and recommend — but this is the layer that actually makes sure a human sees the flag in time. Without nudges and escalation, a task can sit unaddressed indefinitely; the priority/due-date/escalation machinery here is what turns "an agent found something" into "someone with the authority to act on it actually did, on time."
