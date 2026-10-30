# Change Release — Nova Billing Reconciliation moved off prod tenant to dev tenant

**Date:** 2026-07-13
**Related:** `docs/PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md`

## Nova billing reconciliation — what was built (§16.0 realignment)

Before the tenant move below, the feature itself was realigned to the
authoritative §16.0 spec and live-tested end-to-end against the real DB and
the live Nova API. This section is the build summary; the rest of this doc
covers the subsequent prod→dev tenant cleanup.

**Closed:**
- `NOVA_SELF_INCONSISTENT` verified live before building (per §16.0's
  explicit instruction) — Feb-2026 eKYC really does show 0 ops / 0 rate but
  ₹14,041.29 charged.
- **Reconciler rewrite**: `billing_basis`-aware, longest-pattern tie-break,
  three-way self-consistency check, idempotent upsert (verified by re-running
  3× — run count, discrepancy count, and alert count all stayed constant),
  RD-1 flag rollup.
- **Vertical slice, live**: registered the real Feb-2026 Signzy invoice,
  live-pulled the matching Nova month → **flag = RED**, correct detail, 10
  discrepancies including the self-inconsistency finding.
- **JWT tenant auth + IDOR protection**: verified live — no token → 401, bad
  token → 401, cross-tenant ID guess → 404 not data.
- **Entity resolution** via `supplier_id` gate + GSTIN soft-confirm, no fuzzy
  matching.
- **Found and fixed a real bug**: the spec named `fact_bills` as the flag's
  mirror target, but `main.py`'s own comments say that's wrong — the UI
  actually reads `fact_invoices`. Fixed the migration and worker, then
  verified by temporarily linking a real existing Bright Loans bill,
  confirming the flag wrote correctly, and reverting it clean.
- **UI**: deleted the dropped dashboard entirely (not left as dead code),
  built the thin read-only `NovaPage` and the `BillVerificationCard` in
  `BillsPage.tsx`. `tsc -b --force` clean throughout.

**Still open** (flagged in PRD §17.1, not silently skipped): D6
auto-registration hook into the bill-promotion pipeline, `pg_notify`
prompt-wake + worker liveness heartbeat, D4 approval-flow flag warning, and
an automated (vs. manual) cross-tenant isolation test.

---

## Tenant cleanup — what happened

The Nova vendor billing reconciliation feature (§1–§17 of the PRD) was built
and tested against `tenant_id = d0e00000-0000-0000-0000-000000000003`. That
tenant is **production** — real users (e.g. `admin2` / Vikram Singh, logged
in with the BrightLoans-branded UI) are actively on it. All of that test data
has been removed from `003` and moved to
`tenant_id = d0e00000-0000-0000-0000-000000000002`, the **dev/test** tenant.

| Tenant | `dim_tenants.company_name` | Role |
|---|---|---|
| `...-002` | Demo2 Company Pvt Ltd | **Dev/test** — Nova billing reconciliation lives here now |
| `...-003` | AVINASH CAPITAL MARKETS PRIVATE LIMITED | **Production** — real users; no Nova billing test data belongs here |

## What was removed from prod (`...-003`)

| Table | Rows deleted |
|---|---|
| `wf_tasks` (BILLING_DISCREPANCY alerts) | 9 |
| `billing_reconciliation_discrepancies` | 23 |
| `billing_reconciliation_runs` | 2 |
| `billing_vendor_invoice_lines` | 9 |
| `billing_vendor_invoices` | 2 |
| `billing_nova_usage_lines` | 66 |
| `billing_nova_usage_snapshots` | 20 |
| `billing_vendor_accounts` | 17 |

Verified before and after: `fact_invoices`/`fact_bills` rows for tenant
`003` never carried a non-null `verification_flag` (the one real bill that
was briefly, deliberately linked during testing —
`SIGNZY2627/01275` — was already unlinked and reverted to `NULL` in the same
session it was touched, before this cleanup ran). **Prod is fully clean.**

## What was added to dev (`...-002`)

- `billing_vendor_accounts`: all 17 vendors from `nova_vendor_mapping.json`,
  same shape as before — `legal_entity_name = "AVINASH CAPITAL MARKETS
  PRIVATE LIMITED"`, `gstin = "07AACCA2775M1ZF"`,
  `nova_api_key_ref = "NOVA_API_KEY__BRIGHTLOANS"` (the same live Nova key —
  confirmed tenant-scoped, not vendor-scoped, so it's reused for dev testing
  regardless of which internal tenant row references it).
  **Known compromise:** tenant `002`'s own `dim_tenants.company_name` is
  "Demo2 Company Pvt Ltd", not Avinash Capital Markets — the
  `legal_entity_name`/`gstin` on these rows intentionally still reflect the
  *real* Signzy invoice's bill-to entity (the only vendor we have a real
  document for), not tenant `002`'s own identity. This is fine for
  reconciliation testing (module mapping, discrepancy logic, live Nova pulls)
  but is not a faithful entity-resolution setup — don't build/test the O2/
  supplier_id entity-resolution work against this seed without accounting
  for that mismatch.
- `billing_vendor_module_alias` (61 rows) was **not** touched — it's keyed by
  `vendor_name` only, not `tenant_id`, so it was already shared/global and
  needed no migration.

## Not touched (unaffected by this change)

- The 8 `billing_*` tables' schema, the 2 new enums, and the `§16.0`
  hardening (idempotency constraint, `supplier_id`/`billing_basis` columns,
  `bill_verification_flag` enum, `fact_invoices`/`fact_bills` flag columns) —
  all schema-level, tenant-agnostic, apply equally to every tenant.
- `pipelines/billing_reconciliation.py`, `backend_ocr/app/billing_api.py` —
  fully tenant-generic already (driven by DB rows / the caller's JWT), no
  code changes were needed to make this split work.
- The real Signzy invoice PDF sample
  (`lendix/SIGNZY_TEST_2026-05_reconciliation.pdf`, generated for a prior
  test run) has been deleted — it was registered against prod and is no
  longer needed there.
  `scripts/generate_test_signzy_invoice.py` (the generator) is kept as a
  reusable fixture — regenerate and register against **tenant `002`** for
  any future testing, never `003`.

## Config changes

- `scripts/billing_reconciliation_migrate.py`: `BRIGHT_LOANS_TENANT_ID`
  changed from `...-003` to `...-002`, with a comment explaining the prod/dev
  split so a future re-run of this script can't accidentally reseed prod.

## How to test this feature going forward

1. Log in as a **dev-tenant** user (`company_id = d0e00000-0000-0000-0000-000000000002`) —
   not `admin2`/Vikram, who is on prod (`003`).
2. Register test invoices via `POST /api/billing/invoices` (or the
   `generate_test_signzy_invoice.py` fixture) — they'll resolve against
   tenant `002`'s seeded `billing_vendor_accounts` automatically once the
   caller's JWT `company_id` is `002`.
3. The pipeline worker (`pipelines/billing_reconciliation.py`) and API are
   unchanged and fully tenant-driven — no restart or config flag needed to
   point them at dev vs. prod; they follow whichever tenant's data actually
   exists.

## Follow-up (not done here)

Promoting this feature to prod (`003`) for real use is a deliberate,
separate action — it means seeding real `billing_vendor_accounts` rows for
Bright Loans' actual production Nova account/GSTIN, and building the D6
OCR→`supplier_id` auto-registration path (§17.1's open gap) so real vendor
invoices flow in without a manual `POST /api/billing/invoices` call. Not
scheduled here.

---

## Session log — same day, after the tenant cleanup above (2026-07-13)

Everything below happened against dev tenant `...-002` only, after the
prod→dev move — no further prod (`003`) writes.

### Test fixture: synthetic Signzy invoice covering every discrepancy type

`scripts/generate_test_signzy_invoice.py` (kept as a reusable fixture)
generates a `reportlab`-built, clearly-marked-synthetic Signzy tax invoice
for the `2026-05` service month, deliberately calibrated against a **live**
Nova pull (this sandbox's numbers for a period can drift between pulls —
`2026-05` eSign billable ops moved from `72` in the originally-committed
sample JSON to `70,624` on a later pull) so each line lands in exactly one
discrepancy bucket:

| Line | Module | Design | Result |
|---|---|---|---|
| 1 | Bank Aggregator | exact qty/rate match | GREEN control — no finding |
| 2 | Aadhaar OCR | no curated alias pattern exists yet | `UNMAPPED_LINE_ITEM` |
| 3 | eSign | qty exact, rate ₹10.50 vs Nova's ₹9.29 | `RATE_MISMATCH` (HIGH) |
| 4 | Image Liveliness | qty/rate exact, amount ₹2,30,000 vs correct ₹1,96,078.68 (~17%) | `COST_MISMATCH` |
| 5 | Penny Drop | 15× Nova's billable ops | `QTY_MISMATCH` (CRITICAL) |
| 6 | Company Name to GST | Nova has zero usage this month | `MISSING_IN_NOVA` (HIGH) |
| 7 | Platform Access & Compliance Fee | no alias pattern matches | `UNMAPPED_LINE_ITEM` |
| — | PAN, PAN OCR, Name Match, Email Verification, Face Match, eKYC | Nova has real usage, invoice never bills for them | `MISSING_IN_INVOICE` × 6 |

Registered via `POST /api/billing/invoices` against tenant `002`, reconciled
live: **flag = RED**, 13 discrepancies, all 6 discrepancy types present in
one run (a first calibration attempt got several lines swallowed into
`QTY_MISMATCH` because a mismatched qty short-circuits the rate/cost checks
in `_compare_line` — fixed by setting the qty-exact lines to Nova's *exact*
live billable-ops figure).

### Bugs found and fixed while exercising the feature

- **`UNMAPPED_LINE_ITEM` dedup collision.** The idempotency key
  (`run_id`, `discrepancy_type`, `nova_module`) is fine for every type except
  `UNMAPPED_LINE_ITEM`, which always has `nova_module = NULL` — two
  unmapped invoice lines in the same run shared that key and the second
  silently overwrote the first (only the last unmapped line ever showed up).
  Fixed in `pipelines/billing_reconciliation.py` by disambiguating
  `UNMAPPED_LINE_ITEM` findings on the invoice line's own description too.
  Verified: both `Aadhaar OCR Verification` and `Platform Access &
  Compliance Fee` now persist as two independent findings.
- **`nova_module` never persisted onto the invoice line.** The reconciler
  resolved each invoice line's module internally (used for the qty/rate/cost
  comparison) but never wrote it back to
  `billing_vendor_invoice_lines.nova_module` — so the Nova page's "Resolved
  Module" column showed "unmapped" for every line regardless of whether it
  actually matched. Fixed with an `UPDATE` right after a successful
  resolution; verified 5 of 7 lines now show their correct module and only
  the 2 genuinely-unmapped lines stay `NULL`.
- **Invoice total shown was stale.** After correcting the Image Liveliness
  test line's amount, only the PDF was regenerated, not the DB registration
  — the Nova page kept showing the old total until the invoice was
  re-registered (`POST /api/billing/invoices` upserts by
  `(vendor_account_id, invoice_number)`) and re-reconciled. Not a bug in the
  app, a reminder that "regenerate the PDF" and "update the registered data"
  are two separate steps in this manual-registration flow.

### UI/UX overhaul, Phase 1 — KPIs + colour coding on the Nova page

Per explicit request, scoped to the read-only Nova page only (OCR
auto-registration, Phase 2, deliberately left alone this session).

- **Backend**: `GET /api/billing/runs/{id}` now also returns `bill_flag`
  (the tri-state flag + one-line detail), `summary` (severity counts +
  total), and a server-computed `modules` array — one row per module with
  invoice qty/rate/amount *and* Nova apiCalls/billOps/rate/cost side by
  side, plus a per-row RED/YELLOW/GREEN colour computed with the same
  RD-1-equivalent logic as the bill-level flag. This keeps the frontend a
  "dumb renderer" (§16.14's stated design principle) — no reconciliation or
  colour logic added client-side.
- **Frontend** (`frontend/src/pages/NovaPage.tsx`, full rewrite): KPI strip
  (flag badge + detail, Invoice Total, Nova Total, Variance, discrepancy
  count), severity chips (CRITICAL/HIGH/MEDIUM/LOW, colour-coded, muted at
  zero), and the two previously-separate invoice/Nova tables replaced by one
  merged, colour-coded module table with a "Finding" badge per row.

### Bug: blank white screen after the UI overhaul, and its two real causes

- **Backend**: `uvicorn --reload` did not pick up the `billing_api.py`
  edit (WatchFiles missed it) — the running server kept serving `get_run()`'s
  *old* response shape (no `bill_flag`/`summary`/`modules`) even after the
  file was saved. Confirmed by diffing the live response against the source;
  fixed with a clean restart, then re-verified the live response carried the
  new keys.
- **Frontend**: `detail?.summary.severity_counts` — the optional-chaining
  `?.` only guarded `detail`, not `.summary`; when the (then-stale) backend
  response lacked `summary`, this threw an uncaught `TypeError` during
  render. This app has no error boundary anywhere, so an uncaught render
  error white-screens the *entire* app, not just the broken page. Fixed the
  missing `?.` (and a matching unguarded `detail.modules.map(...)`) — this
  is a real robustness gap worth carrying forward: consider adding a
  top-level React error boundary so a future bug degrades to a visible error
  message instead of a blank screen.

**State at end of session:** dev tenant `002` has one fully-reconciled test
invoice (`SIGNZY2526/TEST-0501`, `2026-05`, flag RED, 13 discrepancies
across all 6 types), the Nova page renders KPIs/colour-coding against it,
and the bill-detail verification card is wired but untested against a real
OCR-ingested bill this session (no bill in `002` has gone through the normal
upload flow with a linked Nova invoice yet).
