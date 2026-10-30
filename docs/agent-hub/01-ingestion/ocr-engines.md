# OCR Engines

**Stage:** 01 · Ingestion & capture
**Role:** Document → structured text, with a confidence signal

## What it does

A two-engine cascade. **PaddleOCR** runs first; if confidence is low, the **Servam** fallback runs, and results are merged. Per-page raw response and a confidence map are stored so downstream extraction can weigh fields by how sure the OCR was — a low-confidence field can be treated differently than a high-confidence one instead of being trusted blindly.

## Capabilities

- `ocr_engine` enum: `PADDLEOCR` · `SERVAM` · `MERGED`
- Per-page `confidence_jsonb` — field-level confidence, not just a single document-level score
- Feeds structured fields directly into the draft bill

## Data model

### `raw_ocr_results`
`engine`, `page_number`, `raw_response_jsonb`, `confidence_jsonb`. One row per page per engine run, preserving the raw output — nothing is thrown away after extraction, so a downstream bug in field-mapping can always be re-run against the original OCR output without re-scanning the document.

## Where it fits in the pipeline

**Upstream:** [Upload & webhooks](./upload-webhooks.md) — a raw file must exist in `raw_invoice_uploads` first.
**Downstream:** structured fields flow into the draft bill (see [Email agent](./email-agent.md) for the `agent_draft_bills` shape) and ultimately become properties on an `INVOICE` node in the [knowledge graph](../02-knowledge-graph/context-graph.md).

## Why it matters

Cascading to a second engine only when confidence is low keeps the common case fast and cheap while still catching the documents PaddleOCR alone would get wrong. Storing the confidence map (not just merged text) means every extracted field carries its own trust signal all the way downstream — a reconciliation agent can, in principle, weight a low-confidence extracted amount differently than a high-confidence one.
