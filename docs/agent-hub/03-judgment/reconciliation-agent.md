# Reconciliation Agent

**Stage:** 03 · Agent hub — reasoning & judgment
**Role:** Invoice vs. vendor's own usage — line by line

## What it does

For usage-based vendors (e.g. Nova), it pulls the vendor's **own usage snapshot** and matches the invoice line-by-line: quantity, rate, and cost against real billable operations. A run is created per `(tenant, vendor, month)` and produces a variance amount — the raw material for a dispute.

## Capabilities

- `billing_reconciliation_status` lifecycle: `RUNNING → COMPLETED[_WITH_DISCREPANCIES]`
- Line-level `quantity × rate × cost` check, not just a total-amount comparison
- One run per vendor per billing month — idempotent, re-runnable without duplicating results

## Data model

### `billing_reconciliation_runs`
`total_invoice_amount` vs `total_nova_cost`, `variance_amount`. The run-level summary — one row per `(tenant, vendor, month)`.

### `billing_nova_usage_snapshots`
`billable_operations`, `cost_rupees`, `raw_response`. The vendor's own usage ledger, pulled and stored as the ground truth to reconcile against.

### `billing_bill_reconciliation`
Per-invoice result, stored as `jsonb`. The line-level detail behind a run's summary.

## Where it fits in the pipeline

**Upstream:** the invoice must already be a node in the [knowledge graph](../02-knowledge-graph/context-graph.md) — reconciliation walks from the `INVOICE` node to its `USAGE` node via the `reconciled_with` edge.
**Downstream:** [Anomaly & risk agent](./anomaly-risk-agent.md) turns a variance into a typed, severity-scored discrepancy; [Dispute & outreach agent](./dispute-outreach-agent.md) acts on confirmed discrepancies.

## Why it matters

This is the difference between "the vendor sent a bill" and "the vendor sent a bill that matches what they themselves recorded using." Comparing against the vendor's *own* usage snapshot — not an internal estimate — is what makes a resulting dispute defensible: you're not arguing with the vendor about your numbers, you're pointing at theirs.
