# PRD — SAP Item Code Mapping via Embeddings

**Status:** Phase 0–1 implemented (catalog trained + matcher)
**Author:** subash@1buy.ai
**Created:** 2026-06-19
**Owner:** OCR / Pipelines team

> **Scope (as built).** This is a **bill-only** feature: each bill line item's
> free-text *Description of Goods* is mapped to a single **SAP Item Code**, shown
> as a new **SAP Code** column in the bill-preview line-item table. The catalog
> keeps **every distinct `(item_code, material_desc)` pair** (carrying its HSN),
> so vendor invoices match the *alternate surface forms* of an item — not just
> one canonical description. **No embeddings.** Matching is a two-stage
> **lexical prefilter (rapidfuzz) → GPT-4o-mini rerank**: stage 1 narrows the
> catalog to a focused candidate set per line item, stage 2 ranks those by
> textual + numeric fit. First tenant: **Global Spirits**
> (`d0e00000-0000-0000-0000-000000000004`), loaded from
> `Purchase_Reg_Feb_Mar_2026.csv` (29,606 rows → 1006 item codes / ~2,453
> distinct (code, desc) pairs).

---

## 1. Summary

When an invoice is OCR'd, each line item produces a free-text **Description of
Goods** (e.g. `"Teflon Bush 45 ID X 72 OD, X 100 Length"`, `"C.I Pulley 16" X 2
Groove X B Section"`). These descriptions are vendor-authored and never match
the buyer's internal SAP catalog verbatim. Today nothing links an OCR'd line
item back to the SAP **Item Code** that the AP/procurement team posts against in
MIRO.

This project builds a **semantic mapping layer**: given an OCR line-item
description (optionally with its HSN code, unit and rate), return the best
matching **SAP Item Code**, along with its **Material Description** and **HSN
Code**, plus a confidence score. Matching uses **OpenAI text embeddings** over a
catalog built from the SAP material master, with HSN code as a hard/soft filter
and a confidence threshold that decides auto-accept vs. human review.

The result is written back onto the staged line item (`product_id` /
`hsn_code`), so downstream GST, category, and Tally/SAP sync pipelines operate on
catalog-grade data instead of raw OCR text.

---

## 2. Problem Statement & Motivation

- OCR extracts `product_name` as raw vendor text into `stg_bill_line_items` /
  `stg_invoice_line_items`. The `product_id` FK to `dim_products` is left
  `NULL` because there is no reliable way to resolve vendor text → catalog item.
- Manual mapping of every line item to a SAP Item Code is slow, inconsistent
  across operators, and the main bottleneck before a bill can be posted.
- Exact-string and keyword matching fail on real invoices: abbreviations
  (`C.I` = Cast Iron), reordered tokens, dimensions embedded in the name,
  vendor-specific spellings, and units inside the description.
- Without a resolved Item Code, downstream HSN validation, GL/category
  assignment, and SAP/Tally posting cannot be automated.

**Goal:** Automatically and accurately map OCR line-item descriptions to SAP
Item Codes, reducing manual mapping effort while keeping a human in the loop for
low-confidence matches.

---

## 3. Goals & Non-Goals

### Goals
1. Build a searchable catalog of SAP items keyed by **Item Code**, carrying
   **Material Description** + **HSN Code** (and Material Group, UOM where useful).
2. Embed the catalog with an OpenAI embedding model; store vectors for ANN
   search.
3. Given an OCR line item, return ranked candidate Item Codes with confidence.
4. Use **HSN code** to constrain / boost candidates when present.
5. Auto-accept high-confidence matches; route the rest to a review queue.
6. Persist the resolved mapping to the staged line item and learn from operator
   corrections.

### Non-Goals
- Not replacing the OCR extraction pipeline; this consumes its output.
- Not auto-creating new SAP material master records.
- Not posting to SAP/MIRO — that remains in the existing sync pipelines.
- Not building a general product-classification model; scope is catalog lookup.

---

## 4. Background — Data Inputs

### 4.1 SAP material master (the catalog / "ground truth")

Sourced from the SAP Purchase Register export (e.g.
`Purchase_Reg_Feb_Mar_2026.csv`). The columns we care about:

| Source column | Role | Example |
|---|---|---|
| `Item Code` | **Target** — the value we want to predict | `4560000390` |
| `Material Description` | Primary text to embed | `COAL` |
| `HSN Code` | Filter / disambiguator | `2701` |

> Note: one Item Code appears on many rows (29,606 CSV rows → 1006 unique item
> codes / ~2,453 distinct `(Item Code, Material Description)` pairs). The catalog
> keeps **every distinct pair** — collapsing to one description per code dropped
> the very vendor surface forms invoices actually match (e.g. `Belt Fastner 2"`,
> `SS Stub End 40NB`, `Stubend ss 40 nb`). HSN for a pair is the most frequent
> HSN seen on that exact pair. Material Group / Type / UOM are **not** carried.

### 4.2 OCR line item (the query)

From `stg_bill_line_items` / `stg_invoice_line_items` after OCR:

| Column | Role |
|---|---|
| `product_name` | **Query text** — the "Description of Goods" |
| `hsn_code` | Filter / boost |
| `unit` | Optional validation |
| `unit_price` | Optional validation |

Example queries from `Invoice No. 057.pdf` (C. B. Company):
`External Circlip 65mm` (HSN 7318), `Teflon Bush 45 ID X 72 OD, X 100 Length`
(HSN 391729), `C.I Pulley 16" X 2 Groove X B Section` (HSN 8483).

---

## 5. Proposed Solution

### 5.1 High-level flow

```
SAP Purchase Register CSV
        │  (offline build)
        ▼
[Catalog Builder] ── dedupe by Item Code ─▶ sap_item_catalog (Postgres)
        │
        ▼
[Embedding Job] ── OpenAI embeddings ─▶ sap_item_embeddings (float8[], no pgvector)
        ▲
        │ (online)
Bill line item (product_name, hsn_code)
        │
        ▼
[Matcher] ─ embed query ─ in-app cosine (HSN-blended) ─ confidence
        │
        ├─ score ≥ AUTO_ACCEPT ──▶ write sap_item_code (+desc/score) to line item
        ├─ REVIEW ≤ score < AUTO  ─▶ write candidate + mapping_review_queue (top-N)
        └─ score < REVIEW ────────▶ sap_map_status='NONE', sap_item_code NULL
        │
        ▼
[Feedback] operator confirms/corrects ─▶ sap_item_aliases (learned synonyms)
```

### 5.2 Catalog construction (offline / batch)

- Parse the SAP CSV; project to `(item_code, material_description, hsn_code,
  material_group_desc, material_type_desc, uom)`.
- Deduplicate to one canonical row per `item_code` (most frequent description /
  HSN wins).
- Build an **embedding text** per item from the kept fields only:
  `"{material_description} | HSN {hsn_code}"` (description alone when HSN absent).
- Store in `sap_item_catalog`.

Implemented by `scripts/sap_catalog_load.py` (reads the CSV as `latin-1`, dedupes,
upserts, then embeds).

### 5.3 Retrieval & ranking (no embeddings)

The matcher (`backend_ocr/app/main.py`, `/api/mapping/match` and
`/api/mapping/match-batch`) runs two stages per line item:

1. **Numeric-aware lexical prefilter — `rapidfuzz` + number overlap.** Both the
   query and every catalog description are normalized (lower-case; expand inch
   marks `16"`→`inch` only after a digit; expand abbreviations `SS`→stainless
   steel, `NB`→nominal bore, `C.I`→cast iron, `OD/ID`, `SCH`; drop other
   punctuation, keep numeric specs). Each row scores
   `0.65 · fuzz.WRatio + 0.35 · numeric-overlap`, where numeric-overlap is the
   fraction of the line's numbers (40, 10.0, 10.2, 22-40-10…) present in the row.
   The top **K = 30** per line are kept — so same-spec rows rank first. No model
   call; robust to reordering and misspellings (`Fastener`/`Fastner`,
   `Stub End`/`Stubend`).
2. **GPT-4o-mini rerank.** Only the K candidates (compact per-line TOON table)
   are sent to `gpt-4o-mini`, instructed that **numbers are decisive** — match
   size/bore, diameters, dimensions, schedule first, then text. Returns
   `{item_code, material_desc, confidence, reason}`.
3. **Numeric guard (`_sap_collect`).** Candidates are validated against the
   catalog (anti-hallucination); if the chosen description does not contain ALL
   of the line's numbers, confidence is capped to **0.35** (no-match band) and
   flagged `num_match: false`. This prevents confident wrong-size matches
   (40 NB vs 80 NB, 10.0 mm vs 10.2 mm).

There are **no embeddings, no pgvector, and no `text-embedding-3-small`** call in
the live path. The `sap_item_embeddings` table and `text_hash`/`embedding_text`
columns are retained but unused by the matcher. (`scripts/sap_item_match.py` is
the superseded embeddings prototype.)

### 5.4 Matching (online)

1. Build the query text from the OCR line item (same template shape as catalog,
   description + HSN).
2. Embed the query (cache identical query texts).
3. **HSN filtering:**
   - If the line item has an HSN code: first search **within the same HSN**
     (matched at the 4/6/8-digit prefix level, since invoice HSN granularity
     varies — e.g. invoice `391729` vs catalog `3917`). Fall back to an
     unfiltered search if the HSN bucket is empty.
   - If no HSN: search the full catalog.
4. ANN search → top-K candidates by cosine similarity.
5. **Rerank** with a small score blend:
   `score = w1·cosine + w2·hsn_match + w3·token_overlap (+ w4·unit_match)`.
6. Decide by threshold (see §7).

### 5.5 Write-back & feedback

- Auto-accepted match → write `sap_item_code`, `sap_material_desc`,
  `sap_map_score` and `sap_map_status='AUTO'` onto `stg_bill_line_items`
  (a direct column, **not** `dim_products`). Surfaced as the **SAP Code** column
  in the bill preview.
- Review match → write the best candidate with `sap_map_status='REVIEW'` and
  enqueue top-N candidates in `mapping_review_queue` for an operator.
- No match → `sap_map_status='NONE'`, `sap_item_code` left `NULL`.
- Operator confirmation/correction → store the
  `(query_text → item_code)` pair in `sap_item_aliases` so future identical or
  near-identical descriptions resolve instantly (alias lookup short-circuits the
  embedding call).

---

## 6. Data Model (new tables)

> Tenant-scoped to match existing conventions (`tenant_id` everywhere).
> Created by `scripts/sap_catalog_migrate.py` (idempotent; `IF NOT EXISTS`).

```sql
-- Canonical SAP item master, ONE row per item code per tenant.
-- Keeps only the three required fields: item_code, material_desc, hsn_code.
CREATE TABLE sap_item_catalog (
    catalog_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         uuid NOT NULL REFERENCES dim_tenants(tenant_id),
    item_code         text NOT NULL,
    material_desc     text NOT NULL,
    hsn_code          text,
    embedding_text    text NOT NULL,        -- the string actually embedded
    text_hash         text NOT NULL,        -- sha256(embedding_text) for cache
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, item_code)            -- item_code is the unique key
);

-- Vectors stored as plain Postgres float8[] — NO pgvector.
-- Cosine similarity is computed in the application layer.
CREATE TABLE sap_item_embeddings (
    catalog_id   uuid PRIMARY KEY REFERENCES sap_item_catalog(catalog_id) ON DELETE CASCADE,
    tenant_id    uuid NOT NULL REFERENCES dim_tenants(tenant_id),
    model        text NOT NULL,             -- e.g. 'text-embedding-3-small'
    dim          integer NOT NULL,          -- 1536
    embedding    double precision[] NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_sap_item_emb_tenant ON sap_item_embeddings (tenant_id);

-- Learned synonyms from operator confirmations
CREATE TABLE sap_item_aliases (
    alias_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES dim_tenants(tenant_id),
    alias_text    text NOT NULL,            -- normalized OCR description
    item_code     text NOT NULL,
    source        text NOT NULL DEFAULT 'operator',  -- operator | auto
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, alias_text)
);

-- Review queue for low/medium-confidence matches (bill line items only)
CREATE TABLE mapping_review_queue (
    review_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES dim_tenants(tenant_id),
    line_item_id  uuid NOT NULL,            -- stg_bill_line_items.line_item_id
    line_item_kind text NOT NULL DEFAULT 'bill',
    query_text    text NOT NULL,
    candidates    jsonb NOT NULL,           -- [{item_code, material_desc, hsn_code, score}, ...]
    status        text NOT NULL DEFAULT 'PENDING',  -- PENDING | RESOLVED | REJECTED
    chosen_item_code text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    resolved_at   timestamptz
);

-- Write-back columns added directly on the bill line item (shown in bill preview).
-- Resolves the "dim_products vs. direct column" open question in favour of a
-- direct sap_item_code column.
ALTER TABLE stg_bill_line_items ADD COLUMN sap_item_code     text;
ALTER TABLE stg_bill_line_items ADD COLUMN sap_material_desc text;
ALTER TABLE stg_bill_line_items ADD COLUMN sap_map_score     numeric(5,4);
ALTER TABLE stg_bill_line_items ADD COLUMN sap_map_status    text;  -- AUTO | REVIEW | NONE
```

---

## 7. Confidence Thresholds & Decisioning

Cosine similarity blended score in `[0, 1]`. Initial defaults (tune on a labeled
set — see §10):

| Band | Score | Action |
|---|---|---|
| **Auto-accept** | `≥ 0.86` **and** HSN matches | Write `product_id` automatically |
| **Review** | `0.60 – 0.86`, or high score with HSN mismatch | Enqueue top-5 candidates |
| **No match** | `< 0.60` | Leave `NULL`, flag for manual mapping |

Additional rules:
- An exact `sap_item_aliases` hit always auto-accepts (score = 1.0), bypassing
  the embedding call.
- HSN mismatch caps the band at **Review** even if cosine is high (prevents
  confident wrong matches across unrelated commodity classes).
- A configurable `score_margin` between rank-1 and rank-2 is required to
  auto-accept (ambiguous ties go to review).

---

## 8. Integration Points

- **Pipeline:** add a new `ItemMappingWorker` under `pipelines/` following the
  `BaseWorker` CDC pattern (`pipelines/base.py`) — backfill existing
  `product_id IS NULL` line items, then react via a notify channel on new line
  items. Mirrors `category_enrichment.py`'s "find NULLs and backfill" model.
- **Config:** thresholds, model name, K, and weights live in
  `config.PIPELINE_CFG["item_mapping"]`, consistent with other workers.
- **OpenAI client:** reuse the existing OpenAI setup / key from `.env`
  (already present for OCR vision calls).
- **Frontend (done):** the bill-detail line-item table in
  `frontend/src/pages/BillsPage.tsx` now renders a **SAP Code** column
  (green = AUTO, amber = REVIEW, `—` = unmapped; tooltip shows the matched
  material description + score). Served by `get_bill_detail` in
  `backend_ocr/app/main.py`. A future mapping-review surface can let operators
  pick from `mapping_review_queue` candidates and create an alias.
- **Scripts (done), under `scripts/`:**
  - `sap_catalog_migrate.py` — create all tables + line-item columns (idempotent).
  - `sap_catalog_load.py` — (re)load the SAP register CSV → `sap_item_catalog`,
    dedupe to one row per item code, embed changed rows.
  - `sap_item_match.py` — map descriptions → item codes; `--test` for ad-hoc
    queries, `--backfill` to write back onto unmapped bill line items.

---

## 9. APIs (indicative)

```
POST /mapping/match
  body: { tenant_id, query_text, hsn_code?, unit?, top_k? }
  → { candidates: [{ item_code, material_desc, hsn_code, score }], decision }

POST /mapping/confirm
  body: { tenant_id, review_id, chosen_item_code }
  → writes line item product_id, creates alias, resolves review row

POST /mapping/catalog/refresh   (admin)
  body: { tenant_id, csv_path | upload }
  → { items_upserted, items_embedded, items_skipped_cached }
```

---

## 10. Success Metrics

- **Auto-accept rate:** % of line items resolved without human touch (target:
  ≥ 70% after alias learning warms up).
- **Precision @ auto-accept:** of auto-accepted matches, % correct on a labeled
  audit set (target: ≥ 98%).
- **Recall @ top-5:** correct Item Code present in candidates shown for review
  (target: ≥ 95%).
- **Manual mapping time** per bill (before vs. after).
- **Cost:** OpenAI embedding spend per 1k line items (catalog embedding is
  one-time + delta; query embedding is cached by alias).

Build a **labeled evaluation set** (a few hundred OCR description → known Item
Code pairs) before launch to tune thresholds and weights.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Confident wrong match across HSN classes | HSN gate caps band at Review on mismatch |
| Sparse/garbage Material Descriptions in SAP | Enrich embedding text with group/type; flag un-embeddable rows |
| Invoice HSN granularity ≠ catalog HSN | Match on 4/6/8-digit prefixes with fallback |
| OpenAI latency/cost at scale | Batch + cache by text hash; alias short-circuit |
| Multiple valid Item Codes for one description | `score_margin` tie-break → review |
| New SAP items not yet in catalog | Scheduled catalog refresh; "no match" flag surfaces gaps |
| Model/dim change | `model` column on embeddings + re-embed job; never mix dims in one index |

---

## 12. Phased Delivery

1. **Phase 0 — Data prep:** catalog builder + dedupe from SAP CSV; labeled eval
   set.
2. **Phase 1 — Embedding & search:** pgvector tables, batch embed, ANN +
   HSN-filtered match; offline accuracy report on eval set; tune thresholds.
3. **Phase 2 — Pipeline integration:** `ItemMappingWorker`, write-back to line
   items, review queue.
4. **Phase 3 — Review UI + feedback loop:** operator confirmation, alias
   learning, catalog-refresh script.
5. **Phase 4 — Hardening:** monitoring of auto-accept rate/precision, cost
   dashboards, scheduled refresh.

---

## 13. Open Questions

- **Resolved — `dim_products` vs. direct column:** use a direct `sap_item_code`
  column on `stg_bill_line_items` (plus `sap_material_desc`, `sap_map_score`,
  `sap_map_status`). No `dim_products` mirroring.
- **Resolved — vector store:** plain Postgres `double precision[]` + in-app
  cosine. No pgvector.
- **Resolved — scope:** bills only; the catalog carries only item_code +
  material_desc + hsn_code, one row per item_code.
- Is the SAP Item Code catalog tenant-global or per-plant? (CSV has `Plant`.)
  Current build is tenant-scoped (one tenant: Global Spirits).
- Re-rank with a cross-encoder / LLM judge for the Review band, or keep it
  embedding-only for v1? (v1 is embedding + HSN blend.)
- Catalog refresh cadence — event-driven from SAP export, or scheduled?
