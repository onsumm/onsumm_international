# Anomaly & Risk Agent

**Stage:** 03 · Agent hub — reasoning & judgment
**Role:** Turns a variance into a typed, explainable finding

## What it does

Turns reconciliation output into **typed discrepancies** (rate mismatch, quantity mismatch, month-over-month rate change, self-inconsistent snapshot, …) each with a severity, and layers statistical anomaly detection and per-customer risk scoring on top. Every score carries a `features_jsonb` and an `explanation_jsonb` — so it's defensible, not a bare number a human has to trust blindly.

## Capabilities

- `billing_discrepancy_type` enum includes `RATE_MISMATCH`, `RATE_CHANGED_MOM`, and others
- `severity_level` + a full status lifecycle (open → acknowledged → resolved, etc.)
- Risk buckets from `LOW` to `CRITICAL`, each explainable via `explanation_jsonb`

## Data model

### `billing_reconciliation_discrepancies`
`type`, `severity`, `variance_pct`/`variance_amount`, `status`. One row per confirmed discrepancy, typed and severity-scored.

### `ai_anomalies`
`anomaly_type`, `features_jsonb`, `is_resolved`. Statistical anomalies detected outside the strict reconciliation match (e.g. unusual patterns that don't fit a clean rate/quantity mismatch).

### `ai_risk_scores`
`score`, `risk_bucket`, `explanation_jsonb`. Per-customer or per-vendor risk scoring, always paired with an explanation — never a score without a reason.

## Where it fits in the pipeline

**Upstream:** [Reconciliation agent](./reconciliation-agent.md) — a variance must exist before it can be typed and scored.
**Downstream:** confirmed, high-severity discrepancies feed the [Dispute & outreach agent](./dispute-outreach-agent.md); all discrepancies also become `DISCREPANCY` nodes in the [knowledge graph](../02-knowledge-graph/context-graph.md), linked to the invoice via `has_discrepancy` and to the usage snapshot via `reveals`.

## Why it matters

An unexplained anomaly score is not actionable — a human (or another agent) can't decide what to do with "risk: 0.87" alone. Pairing every score and every discrepancy with structured, typed evidence (`features_jsonb`, `explanation_jsonb`, a specific `billing_discrepancy_type`) is what makes this stage's output usable downstream instead of just another number to ignore.
