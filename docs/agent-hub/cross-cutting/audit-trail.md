# Trust & Audit

**Scope:** Cross-cutting — sits under every mutating write, not beside any one stage
**Role:** Append-only, tamper-evident record of every change

## What it does

A single append-only ledger sits under every mutating write in the system. Each row records the table, entity, action, and a **before/after JSONB** snapshot with a correlation id — so any state can be reconstructed and every action carries someone's (or some agent's) name. Audit-ready by default, not by end-of-quarter scramble.

## Capabilities

- `audit_action` enum: `INSERT` · `UPDATE` · `DELETE` — every mutation type is captured
- `old_values_jsonb` + `new_values_jsonb` — a full before/after diff, not just "something changed"
- Indexed by `(tenant, table, entity)` and time — fast to answer "what happened to this specific row, and when"

## Data model

### `wf_audit_log`
`table_name`, `entity_id`, `action`, `user_id`, `correlation_id`. The single ledger every other table's mutations flow into.

## Which stages this applies to

Every stage in [`ARCHITECTURE.md`](../ARCHITECTURE.md) — ingestion, the knowledge graph, every judgment agent, workflow/HITL actions, disbursement, and ERP write-back all write here. This is not a separate agent with its own pipeline position; it is the substrate every other stage writes through.

## Why it matters

The absence of this exact pattern is precisely what made a real incident on this project traceable after the fact: rows written directly to the database (bypassing the application) left **zero** `wf_audit_log` entries, which is what confirmed those writes hadn't gone through any legitimate agent or user action. The audit trail's value isn't abstract — it's the difference between "we can reconstruct exactly what happened" and "we're guessing," and that gap was concretely demonstrated when it was missing.
