# Nova Reconciliation Logic — Standalone Service

A three-way check, run per canonical service, scoped through entity/brand/cost-centre
certification:

1. **Certification** (`lendix/nova_vendor_mapping.json`) — is this service even allowed to be
   billed against this entity/cost-centre, who actually owns the cost, and has our own mapping of
   it to a live Nova module actually been confirmed (§2.1's `verified` flag)?
2. **Invoice vs Nova** — does the vendor's actual invoice line match what Nova's own usage
   ledger recorded, in quantity? (Amount is deliberately no longer an independent check — see
   §4's "why cost isn't its own check" note.)
3. **Nova vs mapping** — is Nova's own billed rate within tolerance of the certified rate card,
   and is Nova's own arithmetic (`billableOperations x costPerOperationRupees = totalCostRupees`)
   internally consistent?

Inputs:

- **`lendix/nova_vendor_mapping.json`** — the certification source for (1) and (3): which
  Registered Entity or Non-RE (Vimanotech) is allowed to consume which vendor service, at what
  rate, under which brand/cost-centre, and whether that row has been confirmed against a real
  Nova module (`verified`, §2.1).
- **Nova API output** for `(vendor, month)` — the usage-ledger side of (2) and (3), fetched live
  by this service itself (§0) via the confirmed real contract (§2.2).
- **The vendor's invoice line items** — the invoice side of (2): `{description, quantity, rate,
  amount}` per line, read directly from `stg_bill_line_items`/`fact_invoices` by this service's own
  orchestrator (§0) — not passed in by a caller.

This runs as **its own microservice** (`services/nova_reconciliation/`) — not a stage inside any
existing pipeline, and its orchestrator lives inside this same service too, not as a
`pipelines/` worker. It owns its own database schema and its own API, and — unlike the original
design — it now also owns *triggering itself*: discovering newly-uploaded mapped-vendor bills,
calling Nova, and reconciling them automatically (§0). The only thing it still depends on
externally is the mapping JSON being kept current and the platform DB it reads
`fact_invoices`/`stg_bill_line_items`/`dim_tenants`/`dim_cost_centres`/`int_nova_credentials` from.

## 0. The orchestrator — automatic trigger, backlog sweep, self-healing on mapping changes

`services/nova_reconciliation/app/orchestrator.py`, started as a background `asyncio` task
alongside the FastAPI app (`app/main.py`'s startup event) and polled every
`NOVA_ORCHESTRATOR_POLL_SECONDS` (default 120s). One `run_sweep()` does four things, in order:

1. **`_requeue_if_mapping_changed`** — compares `nova_vendor_mapping.json`'s file-modified time
   against what was last seen. If it changed (a rate correction, a `verified` flip, an alias
   rename, a rate card extended to a sibling brand), every previously-`DONE` bill is reset to
   `PENDING` so it gets re-reconciled against the *current* mapping in this same sweep — a mapping
   fix shows up on the dashboard on its own, nobody has to remember to re-run anything.
2. **`discover_eligible_bills`** — scans `fact_invoices` (joined to `dim_suppliers`) for any bill
   whose supplier name matches a canonical vendor in `nova_vendor_mapping.json`'s `vendors{}`
   index (via `vendor_match.py`'s conservative normalized-containment matcher — precision over
   recall, since a false positive here means calling the live Nova API and reconciling an
   unrelated bill) and hasn't been queued before, and inserts one `PENDING` row per invoice into
   `nova_reconciliation_bill_queue` (§9.2). This is genuinely idempotent (`ON CONFLICT DO
   NOTHING`) and is what covers *both* the existing backlog (every bill already in `fact_invoices`
   gets discovered on the very first sweep) and brand-new uploads (discovered on the next poll) —
   one mechanism, not two.
   - `NOVA_ORCHESTRATOR_MIN_INVOICE_DATE` (env var, e.g. `2026-08-01`) excludes anything dated
     before it from ever being queued at all — a hard cutoff for "don't touch stale test/backlog
     data," independent of whether a queue row for it happens to exist.
3. **`process_pending`** — for every `PENDING` row (or `ERROR` row under `MAX_ATTEMPTS = 5`
   retries), runs `process_one`:
   - Resolve the tenant's entity in the mapping JSON; no entry → `SKIPPED`.
   - Resolve `brand_or_cost_centre` (§0.2) — sticky once resolved (§0.3); no signal and not
     resolvable → `SKIPPED` with a reason, never guessed.
   - Load invoice lines from `stg_bill_line_items` and resolve `service_month` (§0.4); missing →
     `SKIPPED`.
   - Resolve that tenant/brand's own Nova API key (§0.5); not configured → `SKIPPED`.
   - Call the live Nova API (§2.2); a transport/HTTP error → `ERROR` (retried later).
   - Run `algorithm.reconcile()` (§4), then `apply_sibling_brand_qty_recheck` (§4.3), then
     persist via `routes.persist_reconcile_result` (shared with the manual `POST /v1/reconcile`
     path, §6) → `DONE`, with `resolved_brand` stored for future stickiness and — if
     `service_month` had to fall back to the invoice date rather than an extracted billing period
     — a `reason` flagging that for manual verification, not a silent guess.
4. Both a manual `POST /v1/orchestrator/run-once` (forces one sweep synchronously) and
   `GET /v1/orchestrator/status` (queue counts by status, plus the last 50 `SKIPPED`/`ERROR`/
   flagged-`DONE` rows with their reasons — so a gap is visible, not silently missing from the
   dashboard) exist for operators.

### 0.1 Why this isn't a `pipelines/` worker

Explicit design decision: the automatic trigger lives *inside* `services/nova_reconciliation`
itself — its own polling loop, its own DB queries against the shared platform DB, its own state
table (`nova_reconciliation_bill_queue`) — rather than as a new CDC-driven worker under
`pipelines/` (which was the initial approach, and was corrected). This keeps this service's
"independently deployed, independent failure domain" property (§1) intact even though it now
also decides *when* to run itself — a bad reconciliation sweep still can't block or slow down
bill ingestion, OCR, or any pipeline worker, because it's a different process entirely.

### 0.2 Resolving `brand_or_cost_centre` automatically

`orchestrator.resolve_brand_or_cost_centre()` — never guesses:

1. If the bill (invoice-level `fact_invoices.cost_center_id`, or any line's
   `stg_bill_line_items.cost_centre_id`) names a cost centre matching one of the entity's
   `brands[]`, use it.
2. Otherwise, if the bill carries a **brand association** (`fact_invoices.brand_id` →
   `brands.name`, what the Bills page shows as "Brand name"), and exactly one of the entity's
   `brands[]` matches it, use that. This is a real signal printed on the bill, not an inference —
   a Signzy invoice addressed to Avinash Capital Markets under brand "Brightloans Private
   Limited" is a Brightloans bill, and calling it ambiguous ignores what the platform already
   knows. Matched on a **normalized prefix** (`_norm`: lowercase, alphanumerics only) because the
   two sources spell the same brand differently — the mapping JSON says `Brightloans`, the
   `brands` table says `Brightloans Private Limited`, and `brands[]` vs `cost_centers[].name`
   disagree on case (`Kreditbuddha` vs `KreditBuddha`). Still requires exactly one match, so two
   brands sharing a prefix stay ambiguous rather than resolving to whichever was checked first.
3. Otherwise, of the entity's `brands[]`, find which ones actually have a certified rate card for
   this vendor (`algorithm.certified_pairs`). Exactly one candidate → use it. Zero or more than
   one (e.g. Avinash's Brightloans *and* Kreditbuddha both certify the same three Signzy
   services since the rate-card mirroring, §8.5) → `SKIPPED`, `"ambiguous"`, and the bill sits
   until either a cost-centre signal appears or a human resolves it once via a direct
   `POST /v1/reconcile` call (§0.3).

**Why step 2 was added.** Two real Avinash Signzy bills (SIGNZY2627/01275, SIGNZY2627/00607) sat
`SKIPPED` as "ambiguous" while both carried `brand_id` → "Brightloans Private Limited" the whole
time: resolution only looked at cost-centre columns, so the answer already on the bill was never
consulted. Both resolve and reconcile automatically now.

### 0.3 Sticky brand resolution

Once a bill has a `resolved_brand` recorded (on the queue row, set whenever a run completes
`DONE`, whether by the automatic path or a manual `POST /v1/reconcile`), every future
re-reconciliation of that same bill — including the mapping-change auto-requeue in step 1 above —
reuses it directly instead of calling `resolve_brand_or_cost_centre` again. This exists
specifically because a mapping change can make an *already-resolved* bill ambiguous again (e.g.
extending Kreditbuddha's rate card to match Brightloans' made every future auto-resolution of an
Avinash bill ambiguous) — without stickiness, a mapping-change-triggered requeue would silently
regress a bill that a human had already resolved once. `resolved_brand` is set via `COALESCE` on
every mark, so a later `SKIPPED`/`ERROR` never wipes out a previously-known-good value.

### 0.4 Resolving `service_month` — priority order

`orchestrator._load_invoice_lines()`, per line:

1. The line item's own `stg_bill_line_items.service_month`, if OCR extracted one.
2. Else the bill header's `stg_bills.service_month` (the OCR-extracted "Billing Period" printed
   on the invoice) — this is *not* the same as the invoice date; a bill dated 12 August can
   genuinely carry billing period "Jul 2026", and calling Nova for the wrong month based on
   invoice date alone produces a large, entirely spurious mismatch.
3. Only as a last resort, the invoice date's own month — and when it falls back this far, the
   run still completes (`DONE`) but is flagged with a `reason` naming the approximation, visible
   via `GET /v1/orchestrator/status`, rather than silently trusted.

### 0.5 Nova API key resolution — per tenant *and brand*, never from an environment variable

`services/nova_reconciliation/app/credentials.py` is a **self-contained copy** of the resolver —
same table (`int_nova_credentials`), same Fernet encryption
(`NOVA_KEY_ENCRYPTION_KEY`), same lookup order — deliberately *not* imported from
`backend_ocr.app.nova_credentials_api` (that would be a cross-package dependency between two
independently-deployed services). A tenant enters their own key under **Integrations → Nova**
(`frontend/src/pages/IntegrationsPage.tsx` → `POST /api/nova/credentials`,
`backend_ocr/app/nova_credentials_api.py`), which writes to the same `int_nova_credentials` row
this service reads.

```python
from app.credentials import resolve_tenant_nova_key, NovaKeyNotConfigured

try:
    creds = resolve_tenant_nova_key(conn, tenant_id, brand)   # {"api_key": ..., "base_url": ...}
except NovaKeyNotConfigured:
    # Normal, expected — the orchestrator marks the bill SKIPPED with this as the reason.
    ...
```

A tenant with multiple brands can hold a **different key per brand** — `int_nova_credentials` has
a nullable `brand` column; lookup tries the brand-specific row first, then the tenant-wide default
(`brand IS NULL`). There is no fallback to a shared operator key or an env var at any point.
`base_url` falls back to `NOVA_BILLING_BASE_URL` only when neither row has a per-tenant override.

## 1. Why a separate service

- **Different lifecycle.** The mapping JSON changes when finance restructures entities/brands/cost
  centres, or when a verification sweep flips a `verified` flag — rare, manual, reviewed. Nova API
  output changes every billing cycle. This service versions/caches the mapping independently
  (`mapping.py` hot-reloads on the file's mtime — the same mechanism §0's auto-requeue watches).
- **Different failure domain.** A bad reconciliation sweep must never be able to block or slow
  down bill ingestion, OCR, or any other pipeline. Running this out-of-process — including its own
  automatic trigger (§0), not a `pipelines/` worker — means its worst failure mode is
  "reconciliation is stale," never "an unrelated pipeline stalled."
- **Different consumer.** Its only output consumer is the Nova dashboard (§7). It doesn't need to
  participate in whatever internal event bus or worker queue the rest of the platform uses — a
  plain REST API (plus its own internal polling loop) is enough.

## 2. Inputs, precisely

### 2.1 `nova_vendor_mapping.json` — the certification source

Each entry in `entities[]` is one of:

- **`entity_type: "RE"`** — a Registered Entity, one or more brands. Its `vendor_rates[]` is the
  list of `(vendor, service)` pairs its brand(s) may be billed for. Avinash Capital Markets has
  two brands (Brightloans, Kreditbuddha) sharing one rate card via `brand_mapping` on each row
  (§8.5) — the "single-brand RE" assumption in earlier drafts of this doc no longer holds
  generally.
- **`entity_type: "NON-RE"`** — Vimanotech. Its `vendor_rates[]` is the shared IT/verification
  catalog it bills centrally across *all* of its `cost_centers[]` (each cost centre = one lending
  brand, plus two non-brand centres: Credme, Salary112). Its `brand_mapping` fields are empty
  because the shared catalog isn't brand-split by nature.

**`vendor_rates[].verified`** (added by a July-2026 bill verification sweep, §8.6) — `false` means
this row's rate/service exists in the source rate sheet but nobody has confirmed which (if any)
live Nova module actually represents it. `certified_pairs()` excludes these rows entirely (never
`CERTIFIED`); a line resolving to one of them gets the distinct `NOVA_LOGIC_MISSING` outcome
(§4), not a silently-wrong `CERTIFIED` match and not a generic `UNCERTIFIED` (which would wrongly
imply the service is simply unauthorized, rather than "authorized but its Nova mapping isn't
defined yet"). Absence of the key means verified — the default for every row the sweep hasn't
touched.

`vendors{}` in the mapping JSON is a derived index (vendor → distinct services across all
entities) — used by `vendor_match.py` (§0) for the "is this supplier even a Nova vendor" pre-check,
and by the alias resolver (§3) as its candidate pool.

`reconciliation_policy` carries the only tolerance numbers this service uses:
`default_tolerance_pct: 2`, `account_aggregator_tolerance_pct: 20`. No other tolerance source
exists — if a future policy needs to differ, it's added to this JSON, not hardcoded.

### 2.2 Nova API output — the real, confirmed contract

```
GET {base_url}/billing/monthly?vendor=<name>&month=<YYYY-MM>
X-API-Key: <tenant's own key>
```

Confirmed live against `api.lendix.ai` with a real tenant key (2026-08-23) — `X-API-Key` is
correct; `Authorization: Bearer <key>` returns a real `401 Invalid API key`, despite being the
initially-assumed contract. `services/nova_reconciliation/app/nova_client.py` implements exactly
this.

```json
{
  "success": true,
  "data": {
    "vendor": "Signzy",
    "month": "2026-07",
    "modules": [
      { "module": "bank-aggregator", "apiCallCount": 401009, "billableOperations": 43974, "costPerOperationRupees": 4, "totalCostRupees": 175896 },
      { "module": "pan", "apiCallCount": 271661, "billableOperations": 242272, "costPerOperationRupees": 0.79, "totalCostRupees": 191394.88 }
    ],
    "totals": { "totalBillableOperations": 1159678, "totalCostRupees": 1747966.81 }
  },
  "timestamp": "2026-08-23T09:26:45.036Z",
  "referenceId": "8773e6f2-2877-4589-abe4-c8328d544e4e"
}
```

**The sandbox's underlying usage numbers drift over wall-clock time between separate calls for the
same `(vendor, month)`** — confirmed by calling it three times over ~10 minutes for the identical
query and getting three different `totalCostRupees` figures (though two calls made back-to-back
returned identical data, so it isn't per-request randomness — the dataset itself changes on some
cadence). Every reconciliation run is therefore a snapshot as of when it happened to call Nova,
not a stable historical ledger — re-running the same bill later can show a different mismatch
purely because Nova's own numbers moved, not because anything in this service changed.

### 2.3 Invoice line items — read directly by the orchestrator, not passed in

```sql
SELECT li.line_item_id, li.product_name, li.quantity, li.unit_price, li.line_total, li.service_month
FROM stg_bill_line_items li JOIN fact_invoices fi ON fi.stg_bill_id = li.stg_bill_id
WHERE fi.invoice_id = :invoice_id
```

Unlike the original design (invoice lines arriving in the `POST /v1/reconcile` body from an
unspecified upstream extractor), the orchestrator's automatic path reads these directly from the
platform DB. The manual `POST /v1/reconcile` endpoint still accepts an explicit `invoice_lines`
array for one-off/testing use, but the normal path is entirely orchestrator-driven. `description`
is free text and is resolved to a canonical service the same way a Nova `module` slug is (§3) —
joined on canonical service, not matching text.

**Tax is excluded from every invoice amount this service reconciles.** Nova's ledger is a usage
cost and carries no GST, so `stg_bill_line_items.line_total` — which is tax-INCLUSIVE — must never
be compared against it directly. `orchestrator._net_of_tax()` subtracts the extracted
`tax_amount`, and that net figure is what lands in `invoice_amount` on every line. A real Signzy
line: 806901 x ₹0.79 = ₹6,37,451.79 taxable, +₹1,14,741.32 IGST @18% = ₹7,52,193.11 in
`line_total`; reconciling the gross figure inflated one bill's reported variance from 133.88% to
175.98% — purely tax. The net total then ties exactly to the invoice's own printed Sub Total
(₹10,10,701.51), which is the check to apply when this looks wrong.

The subtraction uses the extracted `tax_amount` rather than re-deriving `quantity x rate`
deliberately — see the OCR note below: re-deriving would silently paper over a bad `line_total`
instead of letting it surface. Quantity and rate checks were never affected either way, since
neither carries tax.

**A known, real OCR data-quality issue**: `stg_bill_line_items.line_total` has been observed
disagreeing with `quantity × unit_price` on real bills — e.g. a line printed on the invoice as
Qty 3,23,654 × ₹0.79 = ₹2,55,686.66 had `line_total = ₹3,01,710.26` in the DB, suspiciously close
to the *invoice's own grand total*, suggesting OCR extraction grabbed the wrong number for that
field on that document. This service uses whatever the DB gives it — it does not re-derive
`amount` from `quantity × rate` — so this kind of upstream extraction error surfaces as a real
(but spurious) reconciliation mismatch. Since the cost/amount check was removed as an independent
comparison anyway (§4), this specific defect class no longer produces a false `COST_MISMATCH`, but
it's worth knowing the raw `invoice_amount` shown on a line can still be wrong for reasons that
have nothing to do with Nova.

### 2.4 The raw-API explorer — `GET /v1/nova/raw`

A read-only window onto exactly what Nova returned for one `(brand, vendor, month)`, with no
reconciliation applied — surfaced as the **Raw Nova API** tab under Nova. It exists to separate
"what did Nova say?" from "what did we conclude?", which is the first fork to resolve when a bill
looks wrong, and which otherwise means reading service logs.

Each module is annotated with what it resolves to (§3), whether that service is certified for the
chosen brand (§2.1), the certified rate and variance against it, and Nova's own self-consistency
(§4) — a bare slug doesn't explain why a line ended up `UNCERTIFIED` or `NOVA_LOGIC_MISSING`.

**Brand is a real input, not a filter.** Each cost centre holds its own API key (§0.5), so a
different brand means a different key and therefore a different ledger — not a subset of one
response. Confirmed on Signzy/July-2026: Brightloans returns 11 modules / 2,003,295 billable ops,
Kreditbuddha 8 modules / 612,765, and their PAN Premium API ops (547,009 + 257,005) sum to the
804,014 the §4.5 combined view reports.

**Two guards this endpoint applies that the reconciliation path deliberately does not:**

- **An unknown brand is a 400, not a fallback.** §0.5's lookup order falls back to the tenant-wide
  key (`brand IS NULL`) when a brand has no key of its own — correct for reconciliation, wrong
  here, where substituting a different cost centre's ledger under the requested brand's name is a
  wrong answer rather than a lenient one. Confirmed real: tenant `d0e00000-…-0003` has a
  tenant-wide row holding Brightloans' key, so before this guard a nonexistent brand returned
  Brightloans' data labelled as that brand.
- **`key_scope` names which credential answered** — `brand` or `tenant-default`. A *valid* brand
  with no key of its own still falls back, and the UI says so rather than letting the brand
  selector imply otherwise.

## 3. Canonical service resolution

`module` slugs in Nova API output are not the same strings as `vendor_rates[].service` in the
mapping JSON. No hardcoded slug→service table exists by design — resolution is computed at
request time:

1. **Persisted correction, if one exists** (`nova_reconciliation_module_aliases` — an operator
   fixing a specific bad auto-resolution) wins outright.
2. **Otherwise, score the slug against that vendor's known services** — normalized
   (lowercased, punctuation stripped) containment/sequence-similarity scoring against the mapping
   JSON's `vendors{vendor: [service, ...]}` candidate pool. `MIN_SCORE = 0.45`; below that,
   `UNRESOLVED_MODULE`.

**This scoring has a real, confirmed blind spot, not just a theoretical one**: a short slug that
is a prefix of *multiple* candidate service names scores higher against whichever candidate is
*closer in length* to the slug, regardless of which one is actually correct. Nova's real `"pan"`
module (a genuine, high-volume PAN Premium API usage stream) scored **0.875** against the wrong
candidate "PAN OCR" (`normalize("panocr")`, 6 chars) versus **0.808** against the correct "PAN
Premium API" (`normalize("panpremiumapi")`, 13 chars) — purely because "panocr" is closer in
length to "pan". Worse, since both `"pan"` and the genuinely-separate `"pan-ocr"` module then
resolved to the *same* canonical key, one silently overwrote the other in the module index,
losing real usage data entirely. Fixed via a persisted correction
(`nova_reconciliation_module_aliases`: vendor `Signzy`, slug `pan` → `PAN Premium API`) — this is
exactly the designed escape hatch for this failure mode, and should be the standard response
whenever this scoring picks the wrong candidate, rather than tuning the general heuristic (which
risks new false positives elsewhere for every fix to one case).

**A canonical-service label mismatch across entities is a second real, confirmed failure mode**:
the mapping JSON's source data labeled the *same* real Nova module two different ways —
Vimanotech's own row called it `"Name Match"` (rate 0.5), while Avinash Capital Markets and four
other REs called it `"GSTIN Company Name Match"` (rate 0.6) for what Nova reports as one module,
`"name-match"`. Because resolution is global per vendor (not entity-scoped), it always picked
whichever label scored higher against the slug — meaning Brightloans bills never matched at all,
regardless of alias corrections, until the *source data itself* was normalized (Vimanotech's own
row renamed to match the other five entities' convention, §8.6). An alias-table fix cannot solve a
same-vendor same-slug conflict between two different canonical labels; only normalizing the
mapping JSON can.

**Do not "fix" a resolution gap by aliasing a slug to an unrelated service just because nothing
else matches.** A real regression from this session: Nova's real `"eKYC"` module was briefly
aliased to canonical `"Phone-to-Prefill"` on the theory that it was the closest available
candidate for an unmatched invoice line — but `"eKYC"` is Signzy's real, distinct eKYC-with-
Digilocker service, unrelated to Phone-to-Prefill. The correct outcome when a rate-card service
genuinely has no confirmed Nova module is `NOVA_LOGIC_MISSING` (§2.1, §4) — a *stated absence of a
mapping* — not an invented one. The alias table (`nova_reconciliation_module_aliases`) exists to
correct a *wrong automatic score*, not to force a mapping where none has actually been confirmed.

No vendor-specific code was written to make any of this work — the same scorer resolves modules
for any vendor already in the mapping JSON's `vendors{}` index, and picks up new
vendors/services/aliases automatically as the mapping JSON or the alias table is edited.

## 4. Core reconciliation algorithm

Given a bill (`bill_id`), a tenant, `brand_or_cost_centre`, `vendor`, `month`, and a Nova
response for `(vendor, month)` — resolved and fetched automatically by the orchestrator (§0), or
supplied explicitly to `POST /v1/reconcile` for manual/test use:

```
1. Look up tenant_id in nova_vendor_mapping.json's entities[].

2a. entity_type == "RE" (Flow A — recurring):
      certified   = certified_pairs(entity, vendor, brand_or_cost_centre)    -- verified rows only
      unverified  = unverified_pairs(entity, vendor, brand_or_cost_centre)   -- verified: false rows
      -> single pass, §4A below. Never a second Nova call.

2b. entity_type == "NON-RE" (Flow B — non-recurring):
      own_certified  = certified_pairs(entity, vendor)                         -- Phase 1 scope
      owning_re      = the RE whose brands[] contains brand_or_cost_centre
      cc_certified   = certified_pairs(owning_re, vendor, brand_or_cost_centre) -- Phase 2 scope
      (own_unverified / cc_unverified computed the same way, for NOVA_LOGIC_MISSING)
      -> two-phase pass, §4B below.

2c. anything else (no entity found for tenant_id):
      raise 404 — this vendor tenant has no certified catalog to reconcile against yet.
```

### 4A. Flow A — recurring vendor tenant (single pass)

For every canonical service present on either the (single) Nova response or the invoice lines:

- in `certified` → `CERTIFIED`, `scope = RE`, `owner_tenant_id = tenant_id`,
  `cost_centre = brand_or_cost_centre` (provisional — §4.4's attribution may reassign it to the
  sibling cost centre whose ledger actually accounts for the usage).
  Then run the qty/rate/self-consistency checks below.
- not in `certified`, but in `unverified` → **`NOVA_LOGIC_MISSING`** — the rate card names this
  service for this brand, but no row has been confirmed to actually match a live Nova module. Not
  a violation; a data gap.
- not in `certified` and not in `unverified` → `UNCERTIFIED`. A hard exception — no extended
  lookup exists for this flow.
- canonical resolution failed on both sides → `UNRESOLVED_MODULE`.

### 4B. Flow B — non-recurring vendor tenant (two phases)

For every canonical service present on the own-scope Nova response, the extended Nova response (if
supplied), or the invoice lines:

- in `own_certified` only → **Phase 1**, `CERTIFIED`, `scope = NON_RE_SHARED_IT`. Reconciled
  against the own-scope Nova response — no second call needed for this line.
- in `cc_certified` only → **Phase 2**, outside the tenant's own mapping scope.
  - `extended_nova_response` supplied and contains this service → `CERTIFIED`, `scope =
    COST_CENTRE_OWNED`, `owner_tenant_id = owning_re`'s tenant.
  - Otherwise → `PENDING_EXTENDED_LOOKUP` — nothing guessed; the cost centre is named, but Nova
    needs to be called again for it.
- in both `own_certified` and `cc_certified` → `AMBIGUOUS` — never guess which owns it.
- in `own_unverified` or `cc_unverified` (and not certified in either) → `NOVA_LOGIC_MISSING`.
- canonical resolution failed on all sides → `UNRESOLVED_MODULE`.
- in neither certified nor unverified anywhere → `UNCERTIFIED`.

### For every line certified (or `NOVA_LOGIC_MISSING`) in either flow

```
match_status =
  present on both Nova and invoice    -> MATCHED
  Nova only, no invoice               -> MISSING_IN_INVOICE
  invoice only, no Nova               -> EXTRA_IN_INVOICE

if CERTIFIED and MATCHED:
  is_match(bill_value, nova_value) =
    allowed_variance set on this invoice line?
      -> |bill_value - nova_value| <= allowed_variance                          (absolute, per-line)
      -> else: |bill_value - nova_value| / nova_value <= mapping tolerance_pct  (AA: 20%, else: 2%)
  qty_status = is_match(invoice.quantity, module.billableOperations) ? QTY_OK : QTY_MISMATCH
  -- cost_status is always null (see below — no longer an independent check)

if the line has a Nova module (MATCHED or MISSING_IN_INVOICE) and is CERTIFIED:
  rate_status = compare module.costPerOperationRupees vs the CERTIFIED mapping rate
                (RATE_OK / RATE_MISMATCH / ZERO_VOLUME / NOT_AUTO_COMPARABLE)
  nova_self_consistent = abs(billableOperations * costPerOperationRupees - totalCostRupees)
                         / (billableOperations * costPerOperationRupees) <= 2%
```

**Why cost isn't its own check anymore.** The original design compared bill amount vs
`totalCostRupees` *and* bill quantity vs `billableOperations` *and* rate vs the certified rate —
three checks over the same underlying relationship (`cost = qty × rate`), which meant one real
discrepancy could double- or triple-count as separate mismatches. `qty_cost_check()` now only
computes `qty_status`; `cost_status`/`cost_variance_pct` are always `null`. Rate and quantity are
each checked exactly once, independently, and that's sufficient — an amount mismatch with no
underlying qty or rate mismatch cannot occur once both of those are individually correct.

### 4.3 Sibling-brand combined quantity recheck

`orchestrator.apply_sibling_brand_qty_recheck()`, run after `algorithm.reconcile()` and before
persisting — a real, confirmed finding that a vendor can bill *multiple sibling brands of the same
registered entity* on one combined invoice, while Nova tracks their usage separately per brand.

For every `CERTIFIED` line whose own-brand `qty_status` is `QTY_MISMATCH` with
`qty_variance_pct >= QTY_COMBINED_CHECK_TRIGGER_PCT` (5.0 — a gate on when it's worth an extra live
Nova call, not the tolerance the recheck itself uses): if the entity is an `RE` with sibling
brands, and a sibling also certifies this canonical service, fetch that sibling's own Nova data
for the same `(vendor, month)` and recheck the bill's quantity against the **combined** billable
operations, still using the mapping's normal 2%/20% tolerance. A pass flips the line to `QTY_OK`
and records what was checked in `combined_brand_check` (visible on the line, e.g. *"Brightloans+
Kreditbuddha combined billable ops: 804014 vs invoice qty 806901 (0.36% variance, was 47.51% on
Brightloans alone)"*); a non-pass (or no sibling certifying the service, or the sibling has no
Nova key configured) leaves the original single-brand result untouched — this only ever confirms
a genuine match, never manufactures one. Confirmed real case: Avinash Capital Markets' Brightloans
PAN Premium API line went from 47.51% off (Brightloans alone) to 0.36% off (Brightloans +
Kreditbuddha combined) for July 2026 — decisive evidence Signzy bills both brands' PAN usage on
one Brightloans-addressed document. The same bill's Phone-to-Prefill-labeled line only improved
from 73.88% to 29.03% combined — still genuinely mismatched even with both brands summed, so the
recheck correctly left it flagged rather than forcing a pass.

### 4.4 Cost-centre attribution — which cost centre a line is *truly* billed for

`orchestrator.apply_cost_centre_attribution()`, run after `algorithm.reconcile()` and before
persisting. This generalizes §4.3's sibling recheck from a narrow rescue ("can summing siblings
excuse this mismatch?") into a first-class question asked of every certified line: **whose cost is
this?**

Nova has no cost-centre parameter (§2.2, §8.6) — separation comes from the API key. Each cost
centre holds its own key in `int_nova_credentials` (`brand` column, §0.5), so "Brightloans' usage"
and "Kreditbuddha's usage" are two different calls to the same endpoint. Attribution uses that to
decide, per line, which ledger actually accounts for the invoiced quantity.

Resolution order — most specific explanation first:

| # | Test | Outcome | `cost_centre` |
|---|---|---|---|
| 1 | The bill's own cost centre already matches within tolerance | `OWN` | the bill's own |
| 2 | Exactly one sibling's ledger **alone** matches | `SIBLING_OWNED` | **reassigned to that sibling** |
| 3 | The **combined** ledger matches | `SHARED` | the bill's own (the line really does span both) |
| 4 | Nothing explains it | `UNATTRIBUTED` | the bill's own |

Only case 2 moves a line off the bill's own cost centre. It is the direct answer to "reconcile only
what is truly billed for them": a line whose quantity matches a sibling's ledger and not this one's
is that sibling's cost appearing on this document, not this cost centre's discrepancy — so it stops
being counted as this bill's quantity mismatch and is labelled with the cost centre that owns it.

**What deliberately did not change.** Certification (`certified_pairs`, `verified`), canonical
service resolution (§3), and the tolerances (`default_tolerance_pct: 2`,
`account_aggregator_tolerance_pct: 20`, plus a line's own absolute `allowed_variance`) are all
untouched and still read from the mapping JSON. Attribution only decides *whose ledger* a line is
measured against; every comparison it makes then runs through the same `_within_variance` as
everywhere else. `QTY_COMBINED_CHECK_TRIGGER_PCT = 5.0` likewise stays a **cost gate** on when
extra live Nova calls are worth making, never a tolerance — a line already `QTY_OK` against its own
cost centre costs no extra call at all.

**Two-or-more siblings matching alone is not resolved by guessing.** If several siblings' ledgers
each independently match the invoiced quantity, no single owner can be claimed, so attribution
falls through to the combined test (case 3), which is a statement about all of them together
rather than a pick among them — the same refuse-to-guess principle as §0.2.

**Case-insensitive cost-centre matching.** Real data disagrees on case for the same cost centre:
the mapping JSON's `brands[]` says `Kreditbuddha` while its own `cost_centers[].name` and
`dim_cost_centres.name` say `KreditBuddha`. Sibling resolution compares with `casefold()` so this
does not silently produce "no siblings" and quietly disable attribution.

Attribution never invents a pass: every flip to `QTY_OK` is a real match against a real Nova
ledger, and any non-match leaves the original single-cost-centre result exactly as it was.

### 4.5 The combined-cost-centre view — `GET /v1/reconciliation/combined`

§4.4 reaches for a sibling cost centre only when one *line* doesn't add up. This is the
explicit whole-bill alternative: reconcile against **every** certified cost centre of the
entity, summed. Surfaced as a "Compare all brands combined" toggle on the Bills panel.

It exists because a vendor can bill one document covering an entity's whole brand portfolio,
in which case per-cost-centre is the wrong frame for the entire bill rather than for one
line. Confirmed on Avinash/Signzy July 2026: the bill's Nova comparison goes from
₹4,32,137.11 (Brightloans alone) to ₹6,35,171.06 (Brightloans + Kreditbuddha), and PAN
Premium API's billable ops from 547,009 to 804,014 against an invoiced 806,901.

Mechanics:

- One Nova call per cost centre, each with **that cost centre's own API key** (§0.5), merged
  by module slug: `apiCallCount`, `billableOperations` and `totalCostRupees` summed.
- `costPerOperationRupees` is **recomputed** as total cost / total ops — a weighted
  effective rate — rather than copied from any one cost centre, so the rate check still
  compares like with like if two cost centres were ever billed at different per-op rates.
- Certification still resolves against the bill's **own** cost centre. Summing changes which
  usage a line is measured against, never what the entity is allowed to be billed for.
- Every certified line comes back `SHARED` by construction: in this frame there is no
  "other" cost centre left to attribute to, so §4.4's sibling attribution is meaningless.
- `combined_over` names the cost centres actually included; `missing_keys` names any left
  out because their Nova key was missing or the call failed. A partial sum is worse than no
  sum if it reads as complete, so the UI states the exclusion.

**Deliberately not persisted and not the default.** It answers an operator's question ("does
this bill add up if every brand is one pool?"), not the question of which cost centre owns
the cost. Writing it into `nova_reconciliation_runs` would overwrite the per-cost-centre run
that the dashboard and cost allocation depend on.

### 4.1 Worked example — Flow A, `Avinash Capital Markets` (Brightloans, recurring)

Tenant = Avinash Capital Markets (`d0e00000-…-0003`), `brand_or_cost_centre = "Brightloans"`,
vendor = Signzy, month = 2026-07 (real live data). Brightloans certifies six Signzy services;
three (Phone-to-Prefill, GST Detail API, GSTIN Company Name Match) are `verified: false` (§8.6).

| Line item | Canonical service | Certification | Match | Qty vs Nova |
|---|---|---|---|---|
| "Pan extensive premium…" | PAN Premium API | `CERTIFIED`, scope `RE` | MATCHED | `QTY_OK` after the §4.3 sibling recheck (Brightloans alone was `QTY_MISMATCH` at 47.51%) |
| "Phone KYC - Phone to Prefill V2…" | Phone-to-Prefill | **`NOVA_LOGIC_MISSING`** | EXTRA_IN_INVOICE | — (no rate/qty check runs; the mapping itself doesn't have a confirmed Nova module for this service) |
| — (Nova has usage, not on this document) | Account Aggregator | `CERTIFIED` | MISSING_IN_INVOICE | — |
| — (Nova has usage, not on this document) | Aadhaar Extraction | `CERTIFIED` | MISSING_IN_INVOICE | — |
| — (Nova has usage, not on this document) | GSTIN Company Name Match | `NOVA_LOGIC_MISSING` | MISSING_IN_INVOICE | — |
| — (Nova has usage, not on this document) | eSign, eKYC, email-verification, facematch, image-liveliness, penny-drop, pan-ocr (7 modules) | `UNCERTIFIED` | MISSING_IN_INVOICE | — |

Result: `rollup_status = RED`. Dashboard KPI tiles for this bill are scoped to the **two** services
actually on the invoice (§7's `on_bill` scoping) — `certified: 1`, `nova_logic_missing: 1` — not
the ten-plus services Nova's full-month response happens to include, most of which this specific
document never claimed.

### 4.2 Worked example — Flow B, `Vimanotech` (Vimono Tech, non-recurring), across two calls

Tenant = Vimanotech (`3894e068-…-63a7`), `brand_or_cost_centre = "Brightloans"`, vendor = Signzy.
`owning_re` = Avinash Capital Markets. Invoice has 2 lines: L1 (Esign, within Vimanotech's own
scope) and L2 (Account Aggregator, outside it — Brightloans' RE-owned service).

**Call 1 — `extended_nova_response` omitted:**

| Line item | Canonical service | Certification | Match | Cost centre |
|---|---|---|---|---|
| L1 | Esign | `CERTIFIED`, scope `NON_RE_SHARED_IT` | MATCHED, `QTY_OK` | Brightloans |
| L2 | Account Aggregator | **`PENDING_EXTENDED_LOOKUP`**, scope `COST_CENTRE_OWNED` | — | Brightloans |

`rollup_status = YELLOW` (nothing wrong yet — just incomplete).

**Call 2 — same `bill_id`, `extended_nova_response` now supplied:**

| Line item | Canonical service | Certification | Match | Qty vs Nova |
|---|---|---|---|---|
| L1 | Esign | `CERTIFIED`, scope `NON_RE_SHARED_IT` | MATCHED | `QTY_OK` |
| L2 | Account Aggregator | `CERTIFIED`, scope `COST_CENTRE_OWNED` → Avinash Capital Markets | MATCHED | `QTY_OK` |

`rollup_status = GREEN`. `owner_tenant_id` on L2 is Avinash Capital Markets even though the bill
was paid under Vimanotech's vendor account.

## 5. Result record

One row per canonical service touched by either side, per ingestion — immutable. Re-ingesting the
same `(tenant, vendor, month)` (whether via a manual `POST /v1/reconcile` or the orchestrator
requeuing a bill, §0) creates a new run tagged with a fresh `ingested_at`; the dashboard reads only
the **latest** run per `(vendor, billing_month)` (`DISTINCT ON`, §7) — summing every historical run
for the same bill would silently double- (or triple-, or quadruple-) count it each time it's ever
been re-reconciled, which was a real bug this design fixed.

## 6. Service API surface

```
POST /v1/reconcile
  body: { "bill_id": ..., "tenant_id": ..., "brand_or_cost_centre": "Brightloans", "vendor": "Signzy",
          "month": "2026-07", "nova_response": { ...own-scope Nova pull... },
          "invoice_lines": [ { "line_item_id": ..., "description": ..., "quantity": ..., "rate": ...,
                                "amount": ..., "allowed_variance": null }, ... ],
          "extended_nova_response": null   -- Non-RE only; omit until Phase 2's cost centre is known
        }
  → runs §4 end-to-end (without the §4.3 sibling recheck, which only runs on the orchestrator's
    automatic path — this endpoint's caller supplies its own single Nova response by design),
    writes reconciliation_line rows, returns the per-line results plus a bill-level rollup
    (worst-status-wins: any of {AMBIGUOUS, UNCERTIFIED, RATE_MISMATCH, QTY_MISMATCH, Nova
    self-inconsistent} -> RED; NOVA_LOGIC_MISSING, UNRESOLVED_MODULE, PENDING_EXTENDED_LOOKUP, or
    MISSING_IN_INVOICE/EXTRA_IN_INVOICE only -> YELLOW; otherwise GREEN)
  This is now mainly a manual/testing path — the orchestrator (§0) is what normally creates runs,
  calling algorithm.reconcile() + apply_sibling_brand_qty_recheck() directly and persisting through
  the same shared routes.persist_reconcile_result() this endpoint uses.

GET /v1/reconciliation?bill_id=          (the dispatch view's natural key)
GET /v1/reconciliation?tenant_id=&vendor=&month=
  → latest ingested_at run for that bill/key, with its lines

GET /v1/dashboard/summary?owner_tenant_id=&vendor=&period=
  → aggregated counts and net ₹ variance, grouped by owner_tenant_id, scoped to the latest run per
    (vendor, billing_month) and to services actually on the bill (§7) — see §7 for exactly what's
    in and out of scope per tile.

POST /v1/orchestrator/run-once
  → forces one discover+requeue+process sweep synchronously (§0). Same sweep the background
    poll loop runs on its own schedule.

GET /v1/orchestrator/status
  → { counts: {status: n, ...}, issues: [ {invoice_id, tenant_id, vendor, status, reason,
      attempt_count, last_attempted_at}, ... up to 50, SKIPPED/ERROR/flagged-DONE ] }
```

`invoice_lines` on `POST /v1/reconcile` defaults to empty — a bill posted with no invoice yet
still gets certification and Nova-self-consistency checks; every line simply comes back
`MISSING_IN_INVOICE` until the invoice side is supplied.

**The service now calls the Nova API itself** (§0, §2.2, `nova_client.py`) — this reverses the
original design's explicit "the service never calls Nova itself" principle. That principle held
right up until the point where "who triggers reconciliation on bill upload" needed an answer; the
resolution was to make this service own that trigger internally (§0.1) rather than push it into a
separate `pipelines/` worker, which is why the Nova-calling responsibility moved in-service too.
`POST /v1/reconcile` still accepts an externally-supplied `nova_response` for manual/test use — the
service doesn't *require* calling Nova itself for every code path, it just also does so on its own
automatic path now.

## 7. Dispatch view consumption

Every mismatch is queryable straight from `GET /v1/reconciliation?bill_id=` — the dispatch view's
natural read path, returning that bill's full set of lines. The frontend (`NovaOwnershipPanel.tsx`)
shows **every invoice line item unconditionally** in the detail table — nothing on the bill is
hidden, regardless of certification/match outcome; only pure Nova-only entries with no invoice
line at all (`MISSING_IN_INVOICE`) are left out of this table (they're counted in their own KPI
tile instead). Each row shows a plain-text reason under its certification badge whenever it isn't
a clean `CERTIFIED` (e.g. `NOVA_LOGIC_MISSING` → *"rate card lists this service, but its Nova
module mapping isn't confirmed yet"*).

`GET /v1/dashboard/summary` (§6) is the aggregate rollup view:

- a status chip per vendor/month cell — RED/YELLOW/GREEN per the rollup rule in §6, scoped to the
  **latest** run per `(vendor, billing_month)` only (§5) — not summed across every historical
  re-reconciliation of the same bill.
- **Certification-status tiles (`certified`, `uncertified`, `ambiguous`, `unresolved_module`,
  `pending_extended_lookup`, `nova_logic_missing`) and the rate/qty-mismatch tiles are scoped to
  services actually ON the bill** — lines whose `match_status` is `MATCHED` or `EXTRA_IN_INVOICE`,
  both of which mean an invoice line produced them — not Nova's entire monthly module list for
  that vendor. A bill with 2 real line items shows KPI counts against those 2 services, not the
  10+ Nova modules the vendor happened to bill *someone* for that month. `missing_in_invoice` and
  `extra_in_invoice` deliberately stay full-scope, since they're specifically defined by
  presence/absence relative to the bill — scoping `missing_in_invoice` to "services on the bill"
  would make it always read zero, defeating its purpose.
- drill-down to `GET /v1/reconciliation?bill_id=` for the exact lines behind a non-GREEN cell.
- for Vimanotech specifically, a second axis: cost-centre breakdown, since a single vendor bill
  can (and in the §4.2 worked example, does) span both Vimanotech's own scope and one or more RE
  scopes in the same month.

## 8. What this design deliberately leaves open — and what's since been resolved

1. **Only Signzy's real module set (across several actual months of live data) and Digitap's
   sample set have been verified against the similarity scorer** (§3). A low-similarity slug for
   some other vendor could still land below `MIN_SCORE` and come back `UNRESOLVED_MODULE` on first
   contact, or — confirmed, not hypothetical — land *above* the floor against the *wrong*
   candidate (the `"pan"` vs "PAN OCR"/"PAN Premium API" case, §3). The escape hatch is a
   persisted correction in `nova_reconciliation_module_aliases`, which should be the default
   response to a wrong resolution, not a scorer-heuristic change.
2. **Tiered/package rates can't be auto-compared.** Several `rate_unit: "tiered"` or `"package"`
   rows (Smartping SMS, Sendgrid's package pricing, Wocom WhatsApp) route to
   `NOT_AUTO_COMPARABLE` rather than inventing a comparison rule not in the source data.
3. ~~`Kreditbuddha` has no owning RE / no `tenant_id`~~ — **RESOLVED 2026-08-23.** Kreditbuddha is
   confirmed owned by Avinash Capital Markets Limited, shares its tenant record
   (`d0e00000-…-0003`), has its own `dim_cost_centres` row, and — per direct instruction to mirror
   Brightloans' mapping — now shares Brightloans' exact six-service Signzy rate card via
   `brand_mapping` on each row (§8.5). It is **not** a separately negotiated rate card; if
   Kreditbuddha ever gets its own distinct rates, those rows need splitting apart. One real
   consequence: because both brands now certify the same services, a bill with no cost-centre
   signal is genuinely ambiguous between them (§0.2) — the orchestrator correctly refuses to guess
   rather than silently picking one, and a human resolving it once via `POST /v1/reconcile` makes
   that resolution sticky (§0.3) for every future re-reconciliation of that bill.
4. **`Vrinda Finlease`'s two Signzy rows are tagged `Rupee112` instead of `Rupeeontime`** in the
   mapping JSON — still an open, undecided-whether-to-fix data gap. Until corrected at the source,
   any bill scoped to `Rupeeontime` will find those two services `UNCERTIFIED` even though they
   should plainly be certified.
5. **Scope for a bill on `POST /v1/reconcile` must be passed explicitly by its caller.** The
   orchestrator's automatic path resolves it itself (§0.2); a manual/test call to this endpoint
   still owns getting `brand_or_cost_centre` right.
6. **Whether the two Nova pulls in Flow B are ever genuinely different responses is unverified** —
   no cost-centre parameter exists on the one confirmed real Nova endpoint (§2.2), so it's
   plausible the "second call" returns the same shape of data and Phase 2's real value is
   sequencing (explicitly re-checking out-of-scope items), not fresh data per cost centre.
7. **Invoice-line extraction (OCR) can produce a wrong `line_total` independent of Nova
   entirely** (§2.3) — this service trusts whatever the DB gives it and does not re-derive amounts
   from `quantity × rate`. Worth checking before trusting a large reported "bill value" on any
   one line.
8. **Free-text invoice descriptions are noisier to resolve than Nova's own module slugs** — expect
   `UNRESOLVED_MODULE`/`EXTRA_IN_INVOICE` false negatives on unusual invoice wording until the
   correction table accumulates real fixes, the same way `pan`/`name-match` did.

### 8.5 Rate-card mirroring across sibling brands of one RE

When one registered entity's brands should be certified identically (Avinash Capital Markets'
Brightloans and Kreditbuddha, per direct instruction), the correct representation is adding the
second brand to each existing row's `brand_mapping` — **not** duplicating the row. This keeps a
future rate correction a single edit rather than two that can drift apart. The trade-off, already
realized in practice (§0.2, §8.3 above): once two brands certify identically, a bill with no
cost-centre signal is structurally ambiguous between them, and the orchestrator cannot
auto-resolve it — this is a correctness feature (refusing to guess), not a defect, but it does
mean every such bill needs either a real cost-centre tag on the source data or one human
resolution to become sticky.

### 8.6 The July-2026 bill verification sweep and `verified: false`

A verification pass cross-checked every `vendor_rates` row against actual July bills, per
vendor/service. 36 rows across Vimanotech (several Digitap/KYCKart/Smartping/Sendgrid/Girnar/
Wocom/Credeau/Netcore rows), the four RE entities that don't have a Nova `tenant_id` yet (Solomon
Capitals, Aadhar India Finvest, Vrinda Finlease, Devmuni — their Phone-to-Prefill/GST Detail
API/GSTIN Company Name Match rows), and Avinash Capital Markets (the same three services, both
cost centres, since they share one row per §8.5) were marked `verified: false` — the rate exists
in the source sheet, but no confirmed match against a real bill exists yet. `certified_pairs()`
excludes these entirely; `algorithm.py` surfaces them as the distinct `NOVA_LOGIC_MISSING`
outcome (§2.1, §4) rather than a false `CERTIFIED` or a misleading plain `UNCERTIFIED`.

## 9. Database schema

### 9.1 Existing tables this service reads (read-only)

**`dim_tenants`**, **`dim_cost_centres`**, **`dim_suppliers`**, **`fact_invoices`**,
**`stg_bill_line_items`**, **`stg_bills`** — full column lists in `docs/full_database_schema.md`.
The orchestrator additionally reads `stg_bills.service_month` (§0.4) and
`fact_invoices.cost_center_id` / `stg_bill_line_items.cost_centre_id` (§0.2), beyond what the
original design anticipated needing.

**`int_nova_credentials`** — per-tenant, per-brand Nova API keys (§0.5). Not in the original
design; added when tenant-configurable keys replaced env-var-based ones.

| Column | Type | Notes |
|---|---|---|
| `credential_id` | `uuid` | PK |
| `tenant_id` | `uuid` | → `dim_tenants.tenant_id` |
| `brand` | `text`, nullable | `NULL` = tenant-wide default; else scoped to one brand |
| `api_key_encrypted` | `text` | Fernet, `NOVA_KEY_ENCRYPTION_KEY` |
| `base_url` | `text`, nullable | falls back to `NOVA_BILLING_BASE_URL` |
| `is_active` | `boolean` | |
| `created_at` / `updated_at` | `timestamptz` | |

Partial unique indexes on `(tenant_id, brand) WHERE brand IS NOT NULL` and `(tenant_id) WHERE
brand IS NULL`.

### 9.2 New tables/columns this service owns

```sql
CREATE TABLE nova_reconciliation_module_aliases (
    alias_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor               text NOT NULL,
    module_slug          text NOT NULL,
    canonical_service     text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nova_reconciliation_module_aliases_vendor_slug_key UNIQUE (vendor, module_slug)
);

CREATE TABLE nova_reconciliation_runs (
    run_id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bill_id                         text,                        -- fact_invoices.invoice_id (as text) for orchestrator-created runs; a human-supplied string for manual POSTs
    tenant_id                       uuid NOT NULL REFERENCES dim_tenants (tenant_id),
    brand_or_cost_centre            text NOT NULL,
    cost_centre_id                  uuid REFERENCES dim_cost_centres (cost_centre_id),
    vendor                          text NOT NULL,
    billing_month                   text NOT NULL,               -- 'YYYY-MM'
    nova_reference_id               text,
    nova_response_jsonb             jsonb NOT NULL,
    extended_nova_response_jsonb    jsonb,
    rollup_status                   text NOT NULL,
    total_lines                     integer NOT NULL DEFAULT 0,
    certified_count                 integer NOT NULL DEFAULT 0,
    uncertified_count               integer NOT NULL DEFAULT 0,
    ambiguous_count                 integer NOT NULL DEFAULT 0,
    unresolved_module_count         integer NOT NULL DEFAULT 0,
    pending_extended_lookup_count   integer NOT NULL DEFAULT 0,
    nova_logic_missing_count        integer NOT NULL DEFAULT 0,   -- rate card names it, Nova mapping unconfirmed — §2.1/§4
    rate_mismatch_count             integer NOT NULL DEFAULT 0,
    qty_mismatch_count              integer NOT NULL DEFAULT 0,
    cost_mismatch_count             integer NOT NULL DEFAULT 0,   -- always 0 — cost is no longer an independent check, §4
    missing_in_invoice_count        integer NOT NULL DEFAULT 0,
    extra_in_invoice_count          integer NOT NULL DEFAULT 0,
    self_inconsistent_count         integer NOT NULL DEFAULT 0,
    total_cost_rupees               numeric NOT NULL DEFAULT 0,
    total_invoice_amount            numeric NOT NULL DEFAULT 0,
    variance_amount_rupees          numeric NOT NULL DEFAULT 0,
    ingested_at                     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX nova_reconciliation_runs_lookup_idx
    ON nova_reconciliation_runs (tenant_id, vendor, billing_month, ingested_at DESC);
CREATE INDEX nova_reconciliation_runs_bill_id_idx ON nova_reconciliation_runs (bill_id);

CREATE TABLE nova_reconciliation_lines (
    line_id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  uuid NOT NULL REFERENCES nova_reconciliation_runs (run_id) ON DELETE CASCADE,
    owner_tenant_id          uuid REFERENCES dim_tenants (tenant_id),
    line_item_id             text,
    cost_centre              text,
    service_month            text,
    module_slug              text,
    invoice_description      text,
    canonical_service        text,
    scope                    text,
    certification_status     text NOT NULL,   -- CERTIFIED | UNCERTIFIED | AMBIGUOUS | UNRESOLVED_MODULE | PENDING_EXTENDED_LOOKUP | NOVA_LOGIC_MISSING
    match_status              text,           -- MATCHED | MISSING_IN_INVOICE | EXTRA_IN_INVOICE
    rate_status               text,
    qty_status                text,
    cost_status               text,           -- always null now — §4
    nova_self_consistent      boolean,
    billed_rate               numeric,
    expected_rate             numeric,
    rate_variance_pct         numeric,
    qty_variance_pct          numeric,
    cost_variance_pct         numeric,        -- always null now
    billable_operations       numeric,
    api_call_count            numeric,
    total_cost_rupees         numeric,
    invoice_qty                numeric,
    invoice_rate                numeric,
    invoice_amount              numeric,
    allowed_variance             numeric,
    combined_brand_check         text,         -- human-readable basis when §4.3/§4.4 ran; null otherwise
    cost_centre_attribution      text          -- §4.4: OWN | SIBLING_OWNED | SHARED | UNATTRIBUTED; null on the manual POST path
);
CREATE INDEX nova_reconciliation_lines_run_id_idx ON nova_reconciliation_lines (run_id);
CREATE INDEX nova_reconciliation_lines_owner_tenant_idx ON nova_reconciliation_lines (owner_tenant_id);

-- §0's automatic-trigger state, added when the orchestrator moved in-service:
CREATE TABLE nova_reconciliation_bill_queue (
    invoice_id      uuid PRIMARY KEY,           -- fact_invoices.invoice_id
    tenant_id       uuid NOT NULL,
    vendor          text NOT NULL,               -- canonical vendor key, from vendor_match.py
    status          text NOT NULL DEFAULT 'PENDING',  -- PENDING | DONE | SKIPPED | ERROR
    run_id          uuid,                        -- -> nova_reconciliation_runs.run_id once DONE
    reason          text,                        -- why SKIPPED/ERROR, or a caveat on a flagged DONE (§0.4)
    resolved_brand  text,                        -- sticky brand_or_cost_centre once resolved — §0.3
    attempt_count   smallint NOT NULL DEFAULT 0,
    last_attempted_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_nova_bill_queue_status ON nova_reconciliation_bill_queue(status);
```

**Why a `runs` table plus a `lines` table:** the dashboard's primary read (`GET
/v1/dashboard/summary`) is a rollup query, and `nova_reconciliation_runs` carries pre-aggregated
counts so that query never has to `GROUP BY` `nova_reconciliation_lines` at read time. Line-level
detail is only queried on drill-down (`GET /v1/reconciliation`), the low-traffic path.

**Why the dashboard query still can't just trust `nova_reconciliation_runs`' stored counts
directly:** those counts reflect *that run's* full line set, not the on-bill scoping §7 applies —
`GET /v1/dashboard/summary` recomputes totals from `nova_reconciliation_lines` at read time,
scoped to the latest run per `(vendor, billing_month)` and filtered to `match_status IN (MATCHED,
EXTRA_IN_INVOICE)` for the certification/rate/qty tiles.

**Why `nova_reconciliation_bill_queue` exists separately from `nova_reconciliation_runs`:** a
queue row can exist in a state that has no corresponding run at all (`PENDING`, or terminally
`SKIPPED`/`ERROR`) — the queue is "have we considered this bill, and what happened," while a run
is "one completed reconciliation." A bill can also accumulate multiple runs over its lifetime (a
manual test, then an orchestrator re-run after a mapping fix) while its queue row's `run_id`
always points at only the latest one.

**What's deliberately not a foreign key:** `nova_reconciliation_module_aliases`, every
`canonical_service` string, and `vendor_rates[].verified` are all validated against
`lendix/nova_vendor_mapping.json` at request/sweep time, not against a DB table — the mapping JSON
is not mirrored into any table in `docs/full_database_schema.md`. If it's ever migrated into its
own `dim_*` tables, `canonical_service` should become a real FK; until then it's a validated text
column, and `verified` is a JSON-only concept the DB schema doesn't need to know about at all.
