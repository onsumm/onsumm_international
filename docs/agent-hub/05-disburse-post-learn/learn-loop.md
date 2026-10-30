# Learn Loop

**Stage:** 05 · Disburse, post & learn
**Role:** Closes the cycle — outcomes become the graph's next set of facts

## What it does

Every settled outcome — a confirmed overcharge, a verified payment, a resolved discrepancy — is **re-embedded back into the [knowledge graph](../02-knowledge-graph/context-graph.md)** as new nodes and edges. Vendor risk profiles update, and future reconciliations start smarter. Accuracy compounds cycle over cycle instead of every reconciliation run starting from zero context.

## Capabilities

- Resolved exceptions become fact nodes — a settled dispute isn't just closed, it's a new piece of grounded knowledge
- Payment outcomes update the graph — a completed payment strengthens the `settled_by` and `approved` edges for that invoice
- Recovery and accuracy compound — the more cycles run, the richer the graph an agent has to reason over

## Data model

### `fact_graph_nodes`
New fact nodes, with fresh embeddings, representing settled outcomes.

### `fact_payments`
Confirmed payment facts — the record that feeds both the ERP write-back and the graph's re-embedding.

## Where it fits in the pipeline

**Upstream:** [ERP write-back](./erp-writeback.md) and [Disbursement](./disbursement.md) — an outcome must be settled before it's worth learning from.
**Downstream:** every [judgment agent](../03-judgment/) in the *next* cycle — a richer graph means a reconciliation or risk-scoring agent has more grounded prior context to reason from.

## Why it matters

This is what stops the system from being a fixed pipeline that treats every bill identically forever. A vendor with a history of rate mismatches, once that history is embedded as graph facts, is reasoned about differently the next time — not because of a hardcoded rule, but because the graph itself now contains that evidence for any agent to walk to.
