# BUG-001: Pipeline entrypoint (`main.py`) has no singleton guard — duplicate worker instances run concurrently, double-processing every table

## Status
**Fixed.** Both the singleton guard and the actual root cause of the observed
"duplicate" storm (see Update below) are patched and verified locally.

## Severity
High. Confirmed to cause duplicate external API calls, duplicate DB writes, and duplicate alert generation in a live run — not theoretical.

## Environment
- Branch: `uat2`
- Host: local dev machine (Windows), pipeline launched via `python main.py`

## Evidence

Two separate OS processes are both running the full pipeline worker set:

```
PID 17588  "python.exe" main.py   — earlier launch
PID 22912  "python.exe" main.py   — later launch
```

Both hold LISTEN/NOTIFY subscriptions on the same Postgres channels and poll the
same tables (`api_sync`, `webhook`, `upload_ocr`, `staging_promotion`, `approval`,
`deduplication`, `gst_verification`, `bank_verification`, `category_enrichment`,
`tds_processing`, `fx_rate`, `fx_agent`).

Directly observable effect in `logs/fx_rate.log` / `logs/pipeline.log`: the
`fx_rate` worker's "stored rates" line — which should log once per poll cycle —
appears in tight duplicate pairs, ~1-2 seconds apart, with identical rate values:

```
INFO pipelines.fx_rate [fx_rate] stored rates {...}; revalued 8 bills; generated 6 alerts
INFO pipelines.fx_rate [fx_rate] stored rates {...}; revalued 8 bills; generated 6 alerts
INFO httpx  HTTP Request: GET https://open.er-api.com/v6/latest/INR "HTTP/1.1 200 OK"
INFO httpx  HTTP Request: GET https://open.er-api.com/v6/latest/INR "HTTP/1.1 200 OK"
```

`logs/fx_rate.log` grew to 7.8MB / 40,619 "stored rates" entries in under an hour
of runtime — roughly 2x the expected volume for a single instance polling on its
configured interval.

Same duplication pattern exists on the frontend: two independent `npm run dev`
(Vite) processes are running, one bound to :5173 and a second to :5174 after the
first port was taken, rather than the second launch detecting/reusing the first.

## Root cause

`main.py` starts all 12 asyncio pipeline workers unconditionally on invocation
with no check for an already-running instance — no PID file, no DB advisory lock,
no OS-level mutex. Running `python main.py` a second time (e.g. after a restart
where the first process was never actually killed) silently starts a second,
fully independent set of workers against the same database rather than failing
fast or attaching to the existing instance.

## Impact
- Duplicate outbound calls to the FX rate provider (`open.er-api.com`) — quota/rate-limit risk.
- Every bill revaluation and alert-generation pass runs twice, doubling write load on Postgres and risking duplicate alerts surfacing to users.
- Same class of risk applies to every other worker (OCR, GST/bank verification, TDS, approvals) if two instances race on the same CDC-triggered rows — not just `fx_rate`, which merely happens to be the one whose log made the duplication visible.

## Suggested fix
Acquire a Postgres advisory lock (or PID-file-based mutex) at the top of `main.py`
before starting workers; exit immediately with a clear error if another instance
already holds it, instead of starting a second worker set.

## How to reproduce (pre-fix)
1. Start the pipeline: `python main.py`
2. Without stopping it, start it again in a second shell: `python main.py`
3. Tail `logs/fx_rate.log` (or any worker log) — duplicate log lines with matching timestamps/content appear within seconds.

## Update — the storm's real root cause turned out to be separate, and worse

While reproducing this on the dedicated ETL server (`etl.onsumm.ai`), the exact
same tight-loop "duplicate `fx_rate` stored rates" pattern showed up with
**only one** `main.py` process running (confirmed via `ps aux` and the
`onsumm-pipelines.service` systemd unit — a single PID, not two). That ruled
out "duplicate process" as the sole explanation and pointed at a bug inside
the `fx_rate` worker itself, independent of how many processes are running.

**Actual root cause:** `pipelines/fx_rate.py`'s `_has_todays_rates()` checked
whether today's rate was already stored with:
```sql
SELECT COUNT(*) FROM dim_fx_rates
WHERE rate_date = $1 AND currency_code = ANY($2::char[])
```
`dim_fx_rates.currency_code` is `char(3)`, but casting the parameter array to
a bare, unqualified `char[]` makes Postgres default to `character(1)[]` —
silently truncating `'USD'` to `'U'`, `'EUR'` to `'E'`, etc. before comparing.
Those truncated single letters never match the real 3-letter codes stored in
the column, so the query always returned `count = 0` and `_has_todays_rates()`
always returned `False` — the worker believed it never had today's rate, so
it re-fetched from the external FX API, re-upserted, re-revalued every
outstanding foreign-currency bill, and re-ran the alert engine on *every
single poll pass*, forever.

This combined with `pipelines/base.py`'s `_run_poll()`, which only sleeps
when a poll finds nothing to do (`n == 0`) — when work was "done" (`n != 0`,
which was every single pass here), it loops straight back into the next
`process_batch()` with no sleep at all. That's what produced the tight,
rapid-fire storm seen in the logs, entirely from a single process.

The originally-diagnosed "no singleton lock" issue is real and was fixed too
(two genuine duplicate `main.py` processes were found running locally), but
it was not what caused the `fx_rate` storm specifically — this SQL type-cast
bug was.

### Fixes applied
1. **`pipelines/fx_rate.py`** — `_has_todays_rates()` now casts to `$2::text[]`
   instead of the bare `$2::char[]`, so the comparison against the real
   3-letter `currency_code` values actually matches.
2. **`main.py`** — added a Postgres advisory-lock singleton guard
   (`pg_try_advisory_lock`, held on a dedicated pooled connection for the
   process lifetime). A second `python main.py` now exits immediately with a
   clear error instead of starting a competing worker set.

### Verification
- Killed both duplicate local `main.py` processes, started one fresh instance
  with the fix: `logs/fx_rate.log` stayed at 3 startup lines over 15+ seconds
  (previously: continuous duplicate "stored rates" entries, ~1/sec).
- Started a second `python main.py` alongside the first: exited immediately
  with `exit code 1` and `"Another instance of this pipeline already holds
  the singleton lock (pg_try_advisory_lock key=875291004417) — exiting rather
  than double-processing."`

---

# BUG-002: Nova reconciliation blocks the whole bill instead of reconciling the line items that ARE mapped

## Status
Open — no code change made yet, reporting only.

## Severity
Medium. Not a crash, but it hides work the system is already capable of doing and shows the user a message that doesn't say why.

## Where
- `services/nova_reconciliation/app/algorithm.py` (`_reconcile_recurring`, `_rollup`)
- `backend_ocr/app/billing_api.py` (`reconcile_bill_view`, `_skip_status`, lines ~275-355)
- `frontend/src/pages/BillsPage.tsx` (`ReconciliationPanel`)

## Repro case
Signzy Technologies bill for Avinash Capital Markets / Brightloans, 2 line items:
- "Pan Extensive Premium v3" → resolves to certified `PAN Premium API` (rate ₹0.79/hit matches) — this line has everything it needs to certify automatically.
- "Phone KYC - Phone to Prefill V2" → resolves to `Phone-to-Prefill`, whose rate-card row is flagged `"verified": false` in `lendix/nova_vendor_mapping.json` (mapping not yet confirmed against a real bill) → correctly can't be auto-certified.

## What happens
The bill as a whole is presented to the user as one undifferentiated failure:
"⚠ Nova Reconciliation — this bill couldn't be reconciled automatically" (the
generic fallback string at `billing_api.py:316`, or the panel's flat rollup
banner). Nothing on screen tells the reviewer that one line item DID succeed
and exactly one specific line item is the blocker, or why that line is blocked.

## What should happen
1. Line-level reconciliation should still surface per line: the PAN line (fully
   mapped, rate matches) should show as reconciled/certified even though a
   sibling line on the same bill isn't. `algorithm.py` already computes this
   correctly at the line level (`certification_status` per line, `NOVA_LOGIC_MISSING`
   only on the Phone-to-Prefill line) — the data exists.
2. For the line that can't be certified, the user should see an apt,
   specific message on that line (e.g. "Phone-to-Prefill rate mapping not yet
   verified for this vendor — pending confirmation") instead of the bill-level
   generic "couldn't be reconciled automatically" text, which gives the
   reviewer no lever to pull.

## Suspected root cause
- `algorithm.py`'s `_rollup()` collapses all lines into a single `rollup_status`
  (RED/YELLOW/GREEN) for the whole bill; a single yellow line drags the entire
  bill to non-green.
- `billing_api.py`'s `_skip_status()` / the `resp.status_code == 404` branch
  (lines 304-316) falls back to the generic string `"this bill couldn't be
  reconciled automatically"` whenever the queue reason is empty, rather than
  surfacing the per-line `certification_status` reasons that `algorithm.py`
  already computed.
- `BillsPage.tsx`'s `ReconciliationPanel` renders one flat banner for
  `BLOCKED`/non-green statuses instead of a per-line breakdown (it does have
  per-line `modules`/mismatch rendering for the COMPLETED+not-within-tolerance
  path, but the generic-reason paths above don't reach that view).

## Suggested fix (not applied — reporting only per instruction)
Keep bill-level `rollup_status` for the overall badge, but always render the
per-line breakdown (line item, status, reason) underneath rather than only
on the "within tolerance" path — so a bill with 1 certified + 1 pending line
shows both facts instead of one blanket failure message.

---

# BUG-003: Assigned TDS category doesn't display on the vendor card — shows "— Not assigned —" even after being set

## Status
Open — no code change made, reporting only per instruction.

## Severity
Medium. Data entered by the user (Finance/Ops assigning a TDS category to a vendor) doesn't reliably reflect back in the UI, which reads as data loss even if the underlying value is actually saved.

## Where
- `frontend/src/pages/VendorsPage.tsx` — `VendorTdsPanel` (~line 775-807)

## Repro case
Vendor: Signzy Technologies Pvt. Ltd. → Vendor detail → TDS tab. "TDS CATEGORY" dropdown shows "— Not assigned —" despite the category having been assigned previously (per user report).

## Suspected root cause
`VendorTdsPanel`'s local selection state is seeded once from a prop and never
re-synced:

```tsx
function VendorTdsPanel({ vendor, companyId, tdsCategories, onCategoryChange }) {
  const [catId, setCatId] = useState(vendor.vendor_category_id ?? '')
  ...
```

`useState(vendor.vendor_category_id ?? '')` only runs its initializer on the
component's first mount. If:
- the `vendor` object passed in (`VendorsPage.tsx:1171`, `useState<Vendor>(initialVendor)`)
  was seeded from list-row data that doesn't carry a populated `vendor_category_id`
  at the moment the modal opens (e.g. it arrived from a cached/earlier list fetch,
  or the list and detail paths source this field differently), or
- the panel component instance is reused/re-rendered with a new `vendor` prop
  without remounting (no `key` tied to `vendor.supplier_id` forcing a fresh
  instance),

then `catId` stays `''` and the dropdown renders "— Not assigned —" regardless
of what's actually persisted for that vendor. There is no `useEffect` watching
`vendor.vendor_category_id` to correct `catId` after the initial render.

This is a plausible root cause based on reading the code, not yet confirmed
against the live network payload (didn't inspect what the vendor list/detail
API actually returns for `vendor_category_id` on this vendor) — worth checking
that response first to confirm whether this is a frontend state bug or the
value genuinely isn't being persisted/returned by the backend.

## Suggested fix (not applied — reporting only per instruction)
Sync `catId` to `vendor.vendor_category_id` whenever the vendor identity
changes, e.g. `useEffect(() => setCatId(vendor.vendor_category_id ?? ''), [vendor.supplier_id, vendor.vendor_category_id])`,
or key the panel by `vendor.supplier_id` so it remounts fresh per vendor.

---

# BUG-004: "Export PDF" fails — backend is missing the `pypdf` dependency it imports at request time

## Status
Open — root cause confirmed live in this environment.

## Severity
High. The feature is completely non-functional, not intermittent — every export request 500s.

## Repro case
Bill B-001 (HANTO WORKSPACE TECHNOLOGIES PVT LTD, ₹1,41,600) → "Export PDF"
button → button flips to "⚠ Export failed" (red state) every time.

## Root cause — confirmed
`backend_ocr/app/main.py:8548`, `export_bill_pdf()`, imports `pypdf` **inside
the function body** (executed at request time, not at process startup):

```python
@app.get("/api/bills/{invoice_id}/export-pdf")
def export_bill_pdf(invoice_id: str, company_id: str = Query(default=None)):
    ...
    import pypdf
    from reportlab.lib.pagesizes import A4
    ...
```

`pypdf` is listed in `backend_ocr/requirements.txt` (`pypdf>=4.0.0`) but is
**not actually installed** in the Python environment the backend runs under:

```
$ python -c "import pypdf"
ModuleNotFoundError: No module named 'pypdf'
```

(Verified against the same `python.exe` — Python312 — that the running
`uvicorn app.main:app` backend process uses.) `reportlab` IS installed
(v5.0.0), so only the `pypdf` import fails — every call to this endpoint
raises `ModuleNotFoundError`, FastAPI returns 500, and the frontend
(`BillsPage.tsx`'s `ExportPdfButton`, ~line 369-388) treats any non-OK
response as a generic failure and shows "Export failed" with no detail.

Because the import is deferred to request time instead of module load time,
this also means the whole service *starts up successfully* and looks
healthy — the missing dependency only surfaces when a user actually clicks
Export.

## Suggested fix (not applied — reporting only per instruction)
`pip install pypdf` (or `pip install -r backend_ocr/requirements.txt` fully)
in whatever environment actually serves `backend_ocr`, then restart the
service. Moving the `import pypdf` to module scope would also make this
class of gap fail loudly at process startup instead of silently at first use.

---

# BUG-005: Bill shows "No Document Attached" — uploaded source document isn't linked/displayed

## Status
Open — plausible cause identified, not fully confirmed (would need to check this bill's DB row / ingest logs).

## Severity
Medium-High. If the user genuinely uploaded a source document for this bill, that document is now effectively lost from the UI's point of view.

## Repro case
Bill B-001 (HANTO WORKSPACE TECHNOLOGIES PVT LTD) → detail view, document panel
reads "No Document Attached / Bill entered manually" — per user report, a
document was uploaded for this bill, so this should not be showing.

## Suspected root cause
`backend_ocr/app/main.py`'s `export_bill_pdf` (and presumably the document
viewer panel's own fetch) resolve the source PDF via `fact_invoices.upload_id`
joined to `raw_invoice_uploads`:

```python
SELECT fi.invoice_id, ..., fi.upload_id, u.file_name, ...
FROM fact_invoices fi
LEFT JOIN raw_invoice_uploads u ON fi.upload_id = u.upload_id
```

"No Document Attached / Bill entered manually" is the UI's label for
`upload_id IS NULL` (or a NULL join). That label is correct behavior for a
bill genuinely entered manually with no source file — but if this bill's
source PDF WAS uploaded, then either:
- the bill was created through a path that never wrote `upload_id` back onto
  `fact_invoices` (an ingest/linking gap), or
- the row in `raw_invoice_uploads` it points to has `file_data IS NULL` and
  no file present at `UPLOADS_DIR / file_name` on disk either (upload
  record exists but the actual bytes were never persisted/were lost).

Not confirmed against this specific bill's DB row in this session — next
step to verify would be `SELECT upload_id FROM fact_invoices WHERE
invoice_id = ...` for B-001, and if non-null, check whether
`raw_invoice_uploads.file_data` / the on-disk file actually exists for it.

## Suggested fix (not applied — reporting only per instruction)
Confirm via DB query whether `upload_id` is NULL (ingest never linked a file)
or non-NULL-but-unresolvable (file bytes missing) for this bill, then fix the
corresponding gap — either the linking step in the manual-entry/OCR ingest
path, or the upload storage step that should have persisted `file_data`.

---

# BUG-006: Nova integration shows "Not Connected" again after leaving and returning to the Integrations page — saved API credentials appear lost

## Status
Open — root cause confirmed by reading the code, not applied.

## Severity
High. Reads as data loss to the user (they saved a key, it appears gone), and it will make them re-save/re-generate the key repeatedly.

## Repro case (matches user report)
Integrations page → Nova card → paste API key → Save Key → card correctly
flips to "Connected" with masked key + base URL shown (confirmed in the
user's second screenshot, which included a "Last updated" timestamp).
Navigate away to another page/tab and back to Integrations (or simply reload) → Nova
card reverts to "Not Connected" with empty "Paste the API key…" / blank base
URL fields, as if nothing was ever saved.

## Root cause — confirmed
The save and fetch endpoints for Nova credentials return **different
response shapes**, and the frontend's fetch path is typed for the wrong one.

Backend (`backend_ocr/app/nova_credentials_api.py`):
- `POST /api/nova/credentials` (save) returns a single `CredentialsStatus`
  object: `{configured, brand, masked_key, base_url, is_active, updated_at}`.
- `GET /api/nova/credentials` (fetch) returns a **list wrapper**,
  `CredentialsList`: `{"credentials": [ {..same fields per brand..} ]}`
  (line 148, `response_model=CredentialsList`) — because a tenant can hold
  one tenant-wide default key plus a separate key per brand, so the list
  shape is deliberate on the backend.

Frontend (`frontend/src/pages/IntegrationsPage.tsx`):
```ts
async function fetchNovaCredentials(): Promise<NovaCredentials> {
  const r = await fetch(`${...}/api/nova/credentials`)
  if (!r.ok) throw new Error(await readError(r))
  return r.json()   // actually {credentials: [...]}, not a NovaCredentials object
}
```
`NovaCredentials` is declared as the single-object shape
(`{configured, masked_key, base_url, is_active, updated_at}`), but the real
GET response is `{credentials: [...]}`. So after `loadNova()` calls this on
mount (`useEffect(() => { if (novaEnabled) loadNova() }, [novaEnabled,
loadNova])`, line 262-264), `setNovaCreds(c)` stores an object with no
`configured`/`masked_key`/etc keys at all — `novaCreds.configured` is
`undefined`, which the UI treats as falsy → renders the empty/"Not
Connected" state, regardless of what's actually stored in the database.

Right after a save, the page shows "Connected" correctly because
`handleNovaSave` sets `novaCreds` directly from the POST response (which
IS single-object shaped) — the bug only shows up on the next GET, i.e. any
page revisit/reload, which is exactly the "after change tab" symptom
reported.

Separately, this GET/list shape also means the frontend never sends or reads
a `brand` value at all — it always operates on `body.brand` omitted (the
tenant-wide default row), so per-brand Nova keys (the feature the backend
was clearly built to support) aren't reachable from this UI either.

## Suggested fix (not applied — reporting only per instruction)
Make `fetchNovaCredentials()` read `r.json().credentials` and select the
right entry (tenant-wide default when no brand is selected, or matching
`brand` when one is), matching the shape `GET` actually returns; or change
the GET endpoint to return a single `CredentialsStatus` for a given brand
query param, matching what the frontend already assumes.
