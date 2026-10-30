# Knowledge Graph — Onsumm Context Graph

Source of truth: `onsumm-agenthub-lowlevel.html` (Knowledge graph tab). Grounded in `fact_graph_nodes` / `fact_graph_edges` in the live PostgreSQL schema.

## Why it exists

Every entity in the system (a supplier, an invoice, a payment, an approver) and every fact an agent derives (a usage snapshot, a discrepancy) becomes a **node**. Relationships between them become weighted **edges**. Agents reason over this graph — grounded in verified nodes — instead of querying raw operational tables directly. This is the system's shared, semantically-searchable memory.

## Schema

### `fact_graph_nodes`

| Column | Type | Notes |
|---|---|---|
| `node_id` | `uuid` | Primary key |
| `tenant_id` | `uuid` | Tenant scope |
| `node_type` | `text` | e.g. `SUPPLIER`, `INVOICE`, `USAGE`, `DISCREPANCY`, `PAYMENT`, `APPROVER`, `CONTRACT` |
| `ref_id` | `uuid` | Points back to the source-of-truth row (e.g. `dim_suppliers.supplier_id`) |
| `label` | `text` | Human-readable display label |
| `properties` | `jsonb` | Cached fields for fast reasoning without a join |
| `embedding` | `vector` | pgvector embedding, indexed with **HNSW** for cosine similarity search |
| `content_hash` | `text` | Used to dedupe fact nodes |

**Dedup rules:**
- Entity nodes dedupe on `(tenant_id, node_type, ref_id)` — one node per real-world entity, ever.
- Fact nodes (derived, not directly backed by a single row) dedupe on `content_hash` — the same derived fact is never stored twice.

### `fact_graph_edges`

| Column | Type | Notes |
|---|---|---|
| `edge_id` | `uuid` | Primary key |
| `src_node_id` | `uuid` | → `fact_graph_nodes.node_id` |
| `dst_node_id` | `uuid` | → `fact_graph_nodes.node_id` |
| `relation` | `text` | e.g. `billed`, `has_contract`, `prices`, `reconciled_with`, `reveals`, `has_discrepancy`, `settled_by`, `approved` |
| `weight` | `double` | Relationship strength/confidence |
| `properties` | `jsonb` | Evidence/context for the edge |

**Uniqueness:** `(tenant_id, src_node_id, dst_node_id, relation)` — the same triple is never duplicated; re-asserting a fact strengthens/updates the existing edge instead of creating a new one.

## Node types (from the reference example graph)

| Node type | Backing table | `ref_id` points to | What it represents |
|---|---|---|---|
| `SUPPLIER` | `dim_suppliers` | `supplier_id` | Vendor master. Properties cache GSTIN, bank details, risk. |
| `CONTRACT` | `billing_vendor_accounts` | `vendor_account_id` | The rate card / account terms an invoice is priced against. |
| `INVOICE` | `fact_invoices` / `billing_vendor_invoices` | `invoice_id` | The bill itself — the entity most edges converge on. |
| `USAGE` | `billing_nova_usage_snapshots` | `snapshot_id` | The vendor's own usage ledger — ground truth for reconciliation. |
| `DISCREPANCY` | `billing_reconciliation_discrepancies` | `discrepancy_id` | A typed, severity-scored variance — the trigger for a dispute. |
| `PAYMENT` | `fact_disbursements` / `fact_payments` | `disbursement_id` | The settlement, carrying the bank RRN once cleared. |
| `APPROVER` | `org_users` | `user_id` | The named human who cleared the money gate with OTP. |

## Reference edge set (one invoice's full graph)

```
SUPPLIER --billed--------------> INVOICE
SUPPLIER --has_contract--------> CONTRACT
CONTRACT --prices--------------> INVOICE
INVOICE  --reconciled_with-----> USAGE
USAGE    --reveals-------------> DISCREPANCY
INVOICE  --has_discrepancy-----> DISCREPANCY
INVOICE  --settled_by----------> PAYMENT
APPROVER --approved------------> PAYMENT
```

## Traversal semantics — how a reconcile agent walks the graph

The diagram's "Traverse from a bill" animation demonstrates the canonical walk an agent performs to ground a judgment:

1. **Start at INVOICE** — the anchor most edges touch
2. Walk `billed` back to **SUPPLIER** — pull GSTIN + risk
3. Hop to **CONTRACT** via `has_contract` — the agreed rate
4. Follow `reconciled_with` to **USAGE** — the ground truth snapshot
5. The mismatch surfaces a **DISCREPANCY** node via `reveals`
6. `settled_by` links the invoice to its **PAYMENT**
7. `approved` ties the payment to the **APPROVER** who held the gate

This is exactly the traversal that grounds a reconciliation judgment: never trust a bare number, always be able to walk back to the verified nodes (contract terms, usage ground truth, the human who signed off) that justify it.

## Vector search

- Embeddings are stored per node (`fact_graph_nodes.embedding`), indexed with **HNSW** for approximate nearest-neighbor search.
- Similarity metric: **cosine**.
- Used for semantic recall — e.g., "find prior discrepancies similar to this one" or "find the supplier's related contract nodes" without needing an exact `ref_id` join.

## Relationship to the rest of the pipeline

The graph sits at **stage 02**, immediately after ingestion and before any judgment agent runs (see [`ARCHITECTURE.md`](./ARCHITECTURE.md)). Every downstream agent — reconciliation, anomaly/risk, tax, dispute, workflow — reads from and writes back to this graph rather than operating on raw tables in isolation. The **learn loop** (stage 05) closes the cycle: every settled outcome is re-embedded as new nodes/edges, so the graph — and therefore every agent's judgment — gets better over time.
