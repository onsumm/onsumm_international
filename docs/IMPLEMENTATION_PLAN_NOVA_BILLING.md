# Implementation Plan — Nova Vendor Billing Reconciliation + AI Root-Cause

**Branch:** `uat3` · **Specs:**
`docs/PRD_NOVA_VENDOR_BILLING_RECONCILIATION.md` (engine) +
`docs/PRD_NOVA_BILLING_DISCREPANCY_ROOT_CAUSE.md` (attribution).

This is the **build plan** — concrete files, signatures, migrations, tests, and
a PR sequence. Grounded in the actual repo: migrations follow
`scripts/sap_catalog_migrate.py`; the worker follows `pipelines/base.py`
`BaseWorker` (async `process_batch() -> int`, `skip_backfill`, tuned via
`config.PIPELINE_CFG[name]`); routers mount in `backend_ocr/app/main.py` via
`app.include_router(...)` next to `tds_router`/`fx_router`; the AI call reuses
the `gpt-4o-mini` + catalog-validation anti-hallucination pattern from
`backend_ocr/app/main.py` (`_SAP_LLM_MODEL`, `_sap_collect`).

**Guiding constraint:** everything through PR3 is testable **offline** against
the committed `lendix/*.json` samples — no Nova API key, no network. That lets
the entire engine + attribution be validated before Phase-0 external
confirmations land.

---

## PR sequence (each independently shippable + reviewable)

| PR | Title | Depends on | Offline-testable |
|---|---|---|---|
| PR1 | Schema, enums, seed scripts | — | ✅ |
| PR2 | Nova snapshot ingestion (client + sample fallback) | PR1 | ✅ (sample files) |
| PR3 | Reconciliation engine + deterministic attribution | PR1, PR2 | ✅ |
| PR4 | AI root-cause finder | PR3 | ✅ (mockable) |
| PR5 | Pipeline worker (BaseWorker) + config | PR3 | ✅ |
| PR6 | FastAPI router | PR3–PR5 | ✅ |
| PR7 | Frontend page + nav | PR6 | needs PR6 |
| PR8 | Alerting (wf_tasks) + audit wiring | PR3, PR5 | ✅ |

---

## PR1 — Schema, enums, seed scripts

**Files**
- `scripts/billing_reconciliation_migrate.py` (new — idempotent, mirrors
  `sap_catalog_migrate.py`'s `MIGRATIONS = [(label, sql), ...]` + `run()`).
- `scripts/seed_billing_vendor_module_alias.py` (new).
- `models/enums.py` (edit — add the new enums if a Python mirror exists).

**Enums** (Postgres `CREATE TYPE`, `DO $$…$$` guard for re-run safety):
`billing_discrepancy_type`, `billing_discrepancy_status`,
`billing_reconciliation_status`, `billing_cause_code` (§4 taxonomy of the
root-cause PRD). Add value `'BILLING_DISCREPANCY'` to `wf_entity_type`.

**Tables** (all `CREATE TABLE IF NOT EXISTS`, `billing_` prefix — full columns
in engine PRD §8 + attribution PRD §9):
`billing_vendor_accounts`, `billing_vendor_module_alias`,
`billing_vendor_invoices`, `billing_vendor_invoice_lines`,
`billing_nova_usage_snapshots`, `billing_nova_usage_lines`,
`billing_reconciliation_runs`, `billing_reconciliation_discrepancies`
(**including** the attribution columns: `direction`, `primary_cause`,
`probable_causes jsonb`, `owner`, `suggested_action`, `attribution_confidence`,
`attribution_model`, `attribution_prompt_version`, `attribution_input_jsonb`).

Indexes: `(tenant_id, status)`, `(tenant_id, severity)`, `(run_id)`,
`(tenant_id, vendor_name, billing_month)` unique on snapshots,
`(vendor_account_id, invoice_number)` unique on invoices.

**Seed**
- `seed_billing_vendor_module_alias.py`: read `lendix/nova_vendor_mapping.json`
  → 57 `(vendor_name, nova_module)` rows, `nova_usage_slug` defaulted from the
  slug seen in `lendix/*.json` where known, `ON CONFLICT (vendor_name,
  invoice_line_pattern) DO NOTHING`. Curate the Signzy `invoice_line_pattern`
  ILIKE rows from the real invoice (e.g. `%pan%` → `PAN`/`pan`).
- One `billing_vendor_accounts` row: Bright Loans `…0003` × Signzy (legal
  entity/GSTIN behind an env-guarded insert — **do not** hardcode a GSTIN that
  Phase-0 hasn't confirmed; leave a `--seed-brightloans` flag).

**Done when**
- `python3 scripts/billing_reconciliation_migrate.py` → `N passed, 0 failed`,
  re-runnable with no errors.
- `python3 scripts/seed_billing_vendor_module_alias.py` → 57 alias rows; verify
  count query.
- `docs/full_database_schema.md` updated with the 8 tables.

---

## PR2 — Nova snapshot ingestion

**File:** `pipelines/nova_billing_client.py` (new)

```python
def fetch_nova_snapshot(vendor: str, month: str, *, api_key: str | None,
                        sample_dir: str | None = None) -> dict:
    """Return the parsed /billing/monthly payload for (vendor, month).
    Live: httpx.get(f"{NOVA_BILLING_BASE_URL}/billing/monthly",
                    params={"vendor": vendor, "month": month},
                    headers={"x-api-key": api_key}, timeout=15).
    Offline: if api_key is None, load lendix/<tenant>_<vendor>_billing_<month>.json."""

def store_snapshot(conn, tenant_id: str, payload: dict) -> str:
    """Upsert billing_nova_usage_snapshots (+ raw_response_jsonb) and one
    billing_nova_usage_lines per module. Returns snapshot_id.
    Unique key (tenant_id, vendor_name, billing_month) → ON CONFLICT re-fetch."""
```

**Config** (`backend_ocr/app/config.py`): `NOVA_BILLING_BASE_URL`
(default `https://api-dev.lendix.ai`), plus the tolerance/critical constants
from engine PRD §9.

**Done when** a script/test pulls Signzy/`2026-06` for Bright Loans from the
sample file → snapshot + 6 usage lines persisted; `billableOperations`, rate,
cost equal the JSON; `total_billable_operations = 2367`, `total_cost = 3146.0`.

---

## PR3 — Reconciliation engine + deterministic attribution

**Files:** `pipelines/billing_reconciliation.py` (engine),
`pipelines/billing_attribution.py` (deterministic generators).

**Engine** — pure, no worker/IO coupling so it's unit-testable:
```python
def reconcile(conn, tenant_id: str, vendor: str, month: str) -> dict:
    """Engine PRD §7. Returns {run, discrepancies[]}.
    1. resolve tenant (GSTIN → billing_vendor_accounts; fuzzy legal-name
       fallback reusing _name_match from pipelines/gst_verification.py)
    2. map each invoice line → nova_module via billing_vendor_module_alias ILIKE
    3. load snapshot for (vendor, month)
    4. per module: compute Δ, band (EXACT/MINOR/MATERIAL/SEVERE), direction
    5. for band≠EXACT: attribute() → cause; re-grade severity
    6. write billing_reconciliation_runs + _discrepancies rows"""
```

**Deterministic attribution** — attribution PRD §6:
```python
def generate_hypotheses(inv_line, nova_line, ctx) -> list[dict]:
    """Run all generators; return [{code, score, evidence}] (code ∈ §4 enum).
    Generators: digit-edit (drop/add/transpose/decimal-shift), lakh-separator
    reparse, apiCallCount-vs-billable, rounding, tax-inclusion,
    nova cost-consistency (billable*rate≠cost), sub-account sum, period/tz,
    snapshot-staleness, ocr-confidence."""

def attribute_deterministic(inv_line, nova_line, ctx) -> dict:
    """Pick top hypothesis → {primary_cause, probable_causes, owner,
    suggested_action, direction, attribution_model:'deterministic'}.
    This is the PR3 attribution; PR4 swaps in the AI ranker on top."""
```

**Severity re-grade** (attribution PRD §8): `clamp(floor=band_severity,
proposed=severity_from(primary_cause, owner))` — cause may only lower, never
raise above the magnitude ceiling.

**Tests** (`tests/test_billing_reconciliation.py`) — golden cases from
attribution PRD §13, all against sample JSON, **no AI**:
- eKYC invoice 47 vs Nova 470 → `NOVA_HIGHER`, `OCR_DIGIT_DROPPED`, owner US,
  `RE_EXTRACT`, LOW.
- PAN 3,23,654 vs 60 → `INVOICE_HIGHER`, SEVERE, `VENDOR_OVERBILL`, CRITICAL.
- Kreditbudha eKYC `5×1.59=0` → `NOVA_INTERNAL_INCONSISTENCY`, owner NOVA,
  `ESCALATE_NOVA`.
- bank-agg 4468 vs billable 217 → `BASIS_APICALLS_NOT_BILLABLE`.
- eSign ₹315.90 vs ₹315.86 → `ROUNDING`, `ACCEPT_BENIGN`, LOW.

**Done when** all five golden tests pass deterministically.

---

## PR4 — AI root-cause finder

**File:** extend `pipelines/billing_attribution.py`.

```python
def attribute_ai(inv_line, nova_line, ctx, hypotheses) -> dict:
    """Attribution PRD §7. One gpt-4o-mini call (temp 0, JSON-only) that RANKS
    the deterministic hypotheses + may add one taxonomy code, and writes the
    human rationale + owner + suggested_action + severity_recommendation.
    - Input: structured evidence (invoice/nova figures + hypotheses + direction
      prior), NEVER raw numbers alone.
    - Output validated against §4 enum + action/owner/severity sets; on any
      invalid field → one retry → fall back to attribute_deterministic().
    - Store attribution_input_jsonb, attribution_model, attribution_prompt_version."""
```
Config: `BILLING_ATTRIBUTION_MODEL`, `BILLING_ATTRIBUTION_MIN_CONF`,
`BILLING_ATTRIBUTION_PROMPT_VER = "v1"`.

**Guardrails (enforced in code, not the prompt):** enum-locked; reject→retry→
deterministic fallback; AI confidence may only lower severity within the band
ceiling; pipeline never blocks on the LLM (timeout → deterministic).

**Tests:** mock the LLM to return (a) a valid ranked response → merged onto the
discrepancy; (b) an out-of-enum code → falls back to deterministic; (c) timeout
→ deterministic. Assert magnitude ceiling is respected in all three.

**Done when** engine uses `attribute_ai` when enabled, `attribute_deterministic`
when disabled/unavailable, and all PR3 golden tests still pass with AI mocked.

---

## PR5 — Pipeline worker

**File:** `pipelines/billing_reconciliation_worker.py` (new)
```python
class BillingReconciliationWorker(BaseWorker):
    name = "billing_reconciliation"
    skip_backfill = True                 # time-driven, like fx_rate/fx_agent

    async def process_batch(self) -> int:
        # iterate ALL active billing_vendor_accounts (every tenant × vendor):
        #   - new invoice landed since last run? OR month rolled over?
        #   - fetch snapshot (nova_billing_client, key via nova_api_key_ref)
        #   - reconcile(); write run + discrepancies
        #   - HIGH/CRITICAL → wf_tasks(task_type='ALERT') scoped to tenant_id
        # return count processed (0 → idle, per BaseWorker contract)
```
**Config** (`PIPELINE_CFG["billing_reconciliation"]`): `poll_interval_s` and
`idle_poll_interval_s` ~6h (monthly cadence — no CDC/`notify_channel`).

**Done when** the worker, pointed at sample data, produces the same runs +
discrepancies as the PR3 direct-call tests (adding a tenant/vendor = one
`billing_vendor_accounts` row, no code change — assert with a 2nd seeded row).

---

## PR6 — FastAPI router

**File:** `backend_ocr/app/billing_api.py`; mount in `main.py`
(`app.include_router(billing_router)` next to `tds_router`/`fx_router`).

Endpoints (engine PRD §10 + attribution PRD §11): `POST/GET
/api/billing/invoices`, `POST /api/billing/reconcile`,
`GET /api/billing/runs/{id}`, `GET/PATCH /api/billing/discrepancies`,
`POST /api/billing/discrepancies/{id}/reattribute`, `GET /api/billing/health`,
`GET /api/billing/vendor-accounts`.

**Invariants (review checklist):** `tenant_id` **always** from the
authenticated session, never request body; every query filters by `tenant_id`;
`vendor` validated against the 17 `nova_vendor_mapping.json` names; discrepancy
list returns `direction/primary_cause/owner/suggested_action/
attribution_confidence/probable_causes`; `PATCH` requires `payables.approve` +
writes `wf_audit_log`; filters include `owner=` and `primary_cause=`.

**Done when** `GET /api/billing/discrepancies?severity=CRITICAL` returns the PAN
example shape (engine PRD §10) and cross-tenant access is proven impossible by a
test.

---

## PR7 — Frontend

**Files:** `frontend/src/pages/BillingVerificationPage.tsx` (new);
`Sidebar.tsx` + `NavPage` union (add `'billing-verification'`, `ShieldAlert`
icon, `payables.view`).

UI per attribution PRD §12: vendor/month filters, dismissible HIGH/CRITICAL
banner, KPI tiles, discrepancy table with inline attribution (colour by
**owner**: US amber / VENDOR red / NOVA purple), `[Re-extract]` /
`[Mark verified]` / `[Override cause ▾]`, per-run summary
("2 OCR / 1 vendor overbill"), and the API-health panel
(`error_rate = (apiCallCount − billableOperations)/apiCallCount`). Reuse
`readError()` from `frontend/src/api/invoices.ts`; chart via plain SVG (defer
`recharts`).

**Done when** the page renders the sample-data discrepancies with correct
owner colours and the banner fires only for HIGH/CRITICAL.

---

## PR8 — Alerting + audit

- Add `'BILLING_DISCREPANCY'` handling so HIGH/CRITICAL discrepancies create
  `wf_tasks(task_type='ALERT')` flowing through existing `NudgeChannel`
  (EMAIL/SLACK/IN_APP) — no parallel alert system.
- Every discrepancy status change → `wf_audit_log`
  (`table_name='billing_reconciliation_discrepancies'`) via existing
  `BaseWorker.log_audit()`.
- Ingestion/parse failures → `raw_ingestion_errors` via `log_error()`.
- **OCR-owned** discrepancies route to a "verify extraction" worklist, **not**
  an ALERT/page (attribution PRD §8).

**Done when** a CRITICAL vendor-overbill creates an ALERT task but an
OCR-caused LOW does not; both appear in `wf_audit_log`.

---

## Cross-cutting

**Test fixtures:** commit a synthetic Signzy invoice JSON (mirroring the real
line shapes: `"Pan extensive premium"`, qty, rate, IGST%) under
`tests/fixtures/billing/` so invoice-side tests don't need a PDF. Pair it with
the existing `lendix/*.json` snapshots.

**Phase-0 blockers (do not hard-code until confirmed):** `/billing/monthly`
auth model (tenant-implicit vs param); Bright Loans legal entity/GSTIN; real
Nova API key + secret storage. PR1–PR5 proceed on sample data regardless; only
live pulls and the seeded account row wait on these.

**Rollout** (engine PRD §14): P1 Bright Loans×Signzy → P2 more vendors (curate
alias rows) → P3 more tenants (add `billing_vendor_accounts` rows). No schema or
code change per tenant/vendor after PR1–PR8.

**Sequencing note:** PR1→PR3 is the critical path and delivers a working,
offline-verifiable engine + deterministic attribution (3 of 5 golden cases need
no AI). PR4 (AI) and PR6/PR7 (surface) layer on without touching the engine.
