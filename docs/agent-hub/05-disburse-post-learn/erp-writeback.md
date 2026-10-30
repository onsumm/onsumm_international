# ERP Write-back

**Stage:** 05 · Disburse, post & learn
**Role:** Sync results back to the ERP the tenant already runs

## What it does

Validated results post back to the ERP the tenant already runs — Tally, Zoho, QBO, Xero, SAP, and more — through a connector layer with field mappings and a full sync log. Conflicts are resolved by policy (outbound-write / skip / log), so the tenant's books stay the source of truth rather than silently diverging from what Onsumm computed.

## Capabilities

- `data_source` enum: `TALLY` · `ZOHO` · `SAP` · … (extensible connector set)
- `conflict_resolution` enum: `OUTBOUND_WRITE` / `SKIP` / `LOG` — explicit policy for what happens when the ERP and Onsumm disagree, not an undefined behavior
- `sync_job_status` tracked per run, so a failed or partial sync is visible, not silent

## Data model

### `int_connections`
`status`, `provider`, credentials reference. The connection to a specific tenant's ERP instance.

### `int_sync_log`
Job status, direction. A record of every sync attempt, which way data flowed, and whether it succeeded.

### `int_field_mappings`
Source ↔ Onsumm field map. How a field in the tenant's ERP maps to Onsumm's own schema — configurable per connection, not hardcoded per ERP.

### `int_conflict_log`
Conflicts + resolution. When the ERP's state and Onsumm's computed state disagree, this is where the conflict and how it was resolved get recorded.

## Where it fits in the pipeline

**Upstream:** [Disbursement](./disbursement.md) — a payment must be settled before it's posted to the books; also receives validated tax/COA mapping from the [Tax & TDS agent](../03-judgment/tax-tds-agent.md).
**Downstream:** feeds the [Learn loop](./learn-loop.md) — a confirmed, posted payment is itself a fact worth re-embedding into the graph.

## Why it matters

This is what lets Onsumm sit *on top of* a tenant's existing stack (per the architecture-level positioning — "nothing gets ripped out") rather than requiring a full migration off Tally/Zoho/SAP. The explicit conflict-resolution policy matters because ERP write-back without one is exactly the kind of silent data-divergence risk that erodes trust in an automated system over time.
