# Dispute & Outreach Agent

**Stage:** 03 · Agent hub — reasoning & judgment (action)
**Role:** Turns a confirmed discrepancy into recovered money

## What it does

The recovery upgrade. When a discrepancy is confirmed, it assembles an **evidence packet** and drafts vendor outreach with the exact fields in question — tracked by `thread_id` and `message_id` so replies reconcile automatically back to the original dispute. Detection becomes recovered money, not just a flag sitting in a table.

## Capabilities

- `requested_fields` array on each outreach — the outreach is specific about what's being disputed, not a generic "please review"
- Thread + message id link replies back to the originating discrepancy automatically
- Pending outreach is indexed for follow-up, so a vendor that goes quiet doesn't fall through the cracks

## Data model

### `vendor_outreach_log`
`requested_fields`, `thread_id`, `message_id`, `status`. One row per outreach attempt, with the specific fields being disputed and a status for tracking follow-up.

### `poc_email_messages`
Captured vendor correspondence — the actual email thread with the vendor's point of contact, preserved for evidence and for matching replies back to the dispute.

## Where it fits in the pipeline

**Upstream:** [Anomaly & risk agent](./anomaly-risk-agent.md) — only confirmed, severity-scored discrepancies trigger outreach, not raw variances.
**Downstream:** a resolved dispute (vendor credits the overcharge, or confirms the original bill was correct) becomes a new fact re-embedded into the [knowledge graph](../02-knowledge-graph/context-graph.md) via the [Learn loop](../05-disburse-post-learn/learn-loop.md).

## Why it matters

A reconciliation system that only *flags* overcharges without acting on them leaves money on the table — someone still has to manually email the vendor, track the thread, and remember to follow up. Automating the outreach itself, with the specific disputed fields already assembled as evidence, is what turns "we detected an overcharge" into "we recovered ₹84,502."
