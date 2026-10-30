# Agent Hub — Overall Architecture

Source of truth: `onsumm-agenthub-lowlevel.html` (interactive low-level architecture diagram). This document is the written companion — every stage, table, and enum named here is grounded in that diagram and the live PostgreSQL schema (AWS RDS), not invented.

## What the Agent Hub is

A pipeline of specialized agents that takes a bill from "an email or PDF landed somewhere" to "paid, posted to the ERP, and learned from" — with a knowledge graph as shared memory in the middle, and a non-negotiable human approval gate before any money moves. Agents draft and judge; a named human confirms the payment.

## The five stages

| # | Stage | Folder | What happens |
|---|---|---|---|
| 01 | **Ingestion & capture** | [`01-ingestion/`](./01-ingestion/) | Raw docs, email, API pushes, and webhooks all become a draft bill |
| 02 | **Knowledge graph** | [`02-knowledge-graph/`](./02-knowledge-graph/) | Every entity and derived fact becomes a node; relationships become weighted edges |
| 03 | **Agent hub — reasoning & judgment** | [`03-judgment/`](./03-judgment/) | Reconciliation, anomaly/risk scoring, tax/TDS, dispute & outreach — all grounded in graph facts |
| 04 | **Workflow & human-in-the-loop** | [`04-workflow-hitl/`](./04-workflow-hitl/) | Tasks route through an approval chain; a money gate (OTP/MFA) blocks payment until a named human confirms |
| 05 | **Disburse, post & learn** | [`05-disburse-post-learn/`](./05-disburse-post-learn/) | IMPS payout, ERP write-back (Tally/Zoho/SAP/…), and outcomes re-embedded into the graph |

Two **cross-cutting** concerns sit underneath every stage, not beside them — see [`cross-cutting/`](./cross-cutting/):
- **Trust & audit** — every mutating write is captured in an append-only, before/after JSONB ledger
- **Multi-tenant isolation** — every table carries `tenant_id`; no customer's data can collide with another's

## End-to-end example (the trace the diagram animates)

1. A vendor emails a bill → the **email agent** drafts it into `agent_draft_bills`, thread-aware
2. **OCR** (PaddleOCR → Servam fallback) extracts fields into `raw_ocr_results` with a confidence map
3. The bill and its supplier become **nodes** in `fact_graph_nodes`, linked by weighted edges
4. **Reconcile** matches the invoice against `billing_nova_usage_snapshots` — a variance is found
5. A `RATE_MISMATCH` **discrepancy** is written with severity + an explainable risk score
6. The **dispute agent** logs vendor outreach in `vendor_outreach_log` to recover the overcharge
7. On the clean portion, **TDS FY state** advances and GST + COA mapping are applied
8. A `wf_task` (`APPROVE`) routes through the `bill_approval_chain`
9. **Money gate**: the disbursement sits at `PENDING_OTP` until a named approver verifies
10. IMPS payout fires; `bank_rrn` is captured back onto `fact_disbursements`
11. Results sync to Tally/Zoho via `int_sync_log` — books updated
12. The outcome is **re-embedded into the graph** — the next cycle starts smarter

## Design principles (evident from the schema)

- **Agents reason over the graph, not raw tables.** `fact_graph_nodes`/`fact_graph_edges` is the layer agents actually query — grounding judgment in verified, deduplicated facts rather than re-deriving them from scratch each time.
- **Every judgment is typed and explainable.** Discrepancies carry a `billing_discrepancy_type` enum and a severity; risk scores carry `features_jsonb` + `explanation_jsonb`. Nothing is a black-box number.
- **Nothing moves money without a name attached.** `fact_disbursements` sits in `PENDING_OTP` until a human verifies; manual overrides are written to `disbursement_overrides`, never applied silently.
- **Nothing is lost.** Raw ingestion has a status enum and retry counters (`raw_invoice_uploads`, `raw_api_payloads`); failures land in `raw_ingestion_errors` for replay, not a dead end.
- **Everything is reconstructable.** `wf_audit_log` captures before/after JSONB on every mutating write, keyed by a correlation id.
- **Everything is tenant-scoped.** `tenant_id` on every table, most unique keys tenant-scoped, RBAC via `org_user_access_levels`.

## Full table index by stage

| Stage | Tables |
|---|---|
| Ingestion | `raw_invoice_uploads`, `raw_api_payloads`, `raw_webhook_events`, `raw_ingestion_errors`, `raw_ocr_results`, `agent_draft_bills`, `agent_email_events`, `agent_settings`, `user_email_connections` |
| Knowledge graph | `fact_graph_nodes`, `fact_graph_edges` |
| Judgment | `billing_reconciliation_runs`, `billing_nova_usage_snapshots`, `billing_bill_reconciliation`, `billing_reconciliation_discrepancies`, `ai_anomalies`, `ai_risk_scores`, `vendor_tds_fy_state`, `fact_tds_ledger`, `coa_rules`, `vendor_outreach_log`, `poc_email_messages` |
| Workflow / HITL | `wf_tasks`, `wf_nudges`, `wf_escalations`, `bill_approval_chain`, `fact_disbursements` (OTP fields), `disbursement_overrides` |
| Disburse / post / learn | `fact_disbursements`, `remitter_accounts`, `int_connections`, `int_sync_log`, `int_field_mappings`, `int_conflict_log`, `fact_payments` |
| Cross-cutting | `wf_audit_log`, `dim_tenants`, `org_users`, `org_user_access_levels` |

## See also

- [`KNOWLEDGE_GRAPH.md`](./KNOWLEDGE_GRAPH.md) — deep dive on node/edge schema, node types, and traversal semantics
- Each stage folder's individual agent docs for full detail per agent
