# Onsumm Context Graph

**Stage:** 02 · Memory
**Role:** The brain's shared memory — every entity and derived fact, as a graph

> This is the pipeline-stage summary. For full schema, node types, edges, and traversal walkthroughs, see [`../KNOWLEDGE_GRAPH.md`](../KNOWLEDGE_GRAPH.md).

## What it does

The heart of the system. Every entity (invoice, bill, supplier, payment) and every derived fact becomes a **node** in `fact_graph_nodes`, carrying a `ref_id` back to its source row, a `jsonb` property bag, and a **pgvector embedding** indexed with HNSW for semantic search. Relationships are weighted **subject–relation–object triples** in `fact_graph_edges`, uniquely keyed so the same fact is never stored twice. Agents reason over this graph — grounded in verified nodes — rather than querying raw tables directly.

## Capabilities

- Entity nodes dedupe on `(tenant, node_type, ref_id)` — one node per real-world entity
- Fact nodes dedupe on `content_hash` — the same derived fact is never duplicated
- HNSW cosine index for similarity recall
- Edges carry `weight` + `jsonb` evidence, not just a bare relationship label

## Data model

### `fact_graph_nodes`
`node_type`, `ref_id`, `label`, `embedding` (vector), `content_hash`.

### `fact_graph_edges`
`src_node_id → dst_node_id`, `relation`, `weight`, unique triple `(tenant, src, dst, relation)`.

## Where it fits in the pipeline

**Upstream:** [Ingestion](../01-ingestion/) — a bill and its extracted fields must exist before they can become graph nodes.
**Downstream:** every [judgment agent](../03-judgment/) — reconciliation, anomaly/risk, tax, dispute — reads from and writes back to this graph. It is the shared substrate all reasoning runs on top of.

## Why it matters

Without a shared graph, each agent would either re-derive the same facts independently (wasteful, and prone to disagreement between agents) or query raw operational tables directly (coupling every agent to schema details that shouldn't be its concern). The graph is the layer that lets an agent ask "what do we know about this invoice's supplier, contract, and prior discrepancies?" as a single grounded traversal instead of five separate table joins re-implemented five times.
