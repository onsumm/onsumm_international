# Upload & Webhooks

**Stage:** 01 · Ingestion & capture
**Role:** Entry point — three doors into the system

## What it does

Three doors into the system: a user uploads a PDF/image, an ERP pushes a payload via API, or a payment gateway fires a webhook. Each lands in its own raw staging table with a **status enum** and a retry counter, so nothing is lost and every failure is replayable.

## Capabilities

- `upload_status` lifecycle: `RECEIVED → PREPROCESSING → OCR_* → DONE`
- Signature-verified webhooks only — unverified events are flagged, not silently trusted
- Failures are captured (not dropped) for replay

## Data model

### `raw_invoice_uploads`
File metadata, `storage_path`, `status`, `ocr_engine_used`. The record a manually-uploaded PDF/image lands in before OCR runs.

### `raw_api_payloads`
`source`, `entity_type`, `is_processed`, `retry_count`. Inbound pushes from connected ERPs/systems.

### `raw_webhook_events`
`is_signature_verified`, `correlation_id`. Inbound webhook events (e.g. from a payment gateway) — signature verification is checked and recorded before any downstream processing trusts the payload.

### `raw_ingestion_errors`
`stage`, `validation_errors` (jsonb). Every failure at any point in ingestion is captured here with enough structure to diagnose and replay, rather than silently disappearing.

## Where it fits in the pipeline

**Upstream:** nothing — this is the entry point.
**Downstream:** feeds [OCR engines](./ocr-engines.md) (for uploaded files) and, in parallel, the [email agent](./email-agent.md) has its own independent capture path. Successfully processed records eventually become nodes in the [knowledge graph](../02-knowledge-graph/context-graph.md).

## Why it matters

A bill-processing system that silently drops a malformed upload or an unverified webhook is unusable in production. The status-enum + retry-counter + error-capture pattern here means every failure is visible and replayable — nothing requires a human to notice something just vanished.
