# Email Agent

**Stage:** 01 · Ingestion & capture
**Role:** Vendor email → draft bill, thread-aware

## What it does

Watches a connected Gmail inbox and drafts a bill straight from a vendor's email + attachment. It is **thread-aware**: a follow-up email correcting an amount updates the *same* draft (matched on `thread_id`) instead of creating a duplicate. Every action is logged idempotently by `message_gmail_id`, so re-processing the same message twice (e.g. after a retry) never double-drafts.

## Capabilities

- OCR payload is attached to each draft bill, not just a summary
- `thread_id` keys the correction-vs-duplicate logic — this is the mechanism that distinguishes "vendor sent a corrected amount" from "vendor sent a second, separate bill"
- Toggleable per tenant via `agent_settings` — not every tenant necessarily wants this running

## Data model

### `agent_draft_bills`
`thread_id`, `ocr_payload`, `analysis`, `suggestion`, `related_invoice`. The draft itself, plus the agent's own analysis/suggestion for a human reviewer, and a pointer to a related invoice if this looks like a correction or duplicate of something already in the system.

### `agent_email_events`
`action`, `draft_id`, `unique(message_gmail_id)`. An event log of what the agent did with each inbound message — the uniqueness constraint on `message_gmail_id` is what makes re-processing safe.

### `user_email_connections`
Access/refresh token, `agent_last_scan_at`. The OAuth connection to the watched inbox and a watermark of how recently it was last scanned.

## Where it fits in the pipeline

**Upstream:** nothing — this is an independent capture path parallel to [Upload & webhooks](./upload-webhooks.md), triggered by inbound email rather than a manual action.
**Downstream:** drafted bills feed the same OCR/extraction pipeline (see [OCR engines](./ocr-engines.md)) and eventually become `INVOICE` nodes in the [knowledge graph](../02-knowledge-graph/context-graph.md), same as any other ingestion path.

## Why it matters

The thread-aware duplicate/correction detection is the single most important design decision here: without it, every vendor email chain (original bill → "actually the amount is different" → "sorry, one more correction") would silently create three separate bills instead of one bill with a corrected value. This was specifically built and tested to close that gap (see `docs/VENDOR_BILL_AGENT.md` for the implementation detail and the incident that motivated it).
