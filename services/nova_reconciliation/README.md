# Nova Reconciliation Service

Standalone microservice. Design: `docs/NOVA_RECONCILIATION_LOGIC.md`. API contract:
`docs/nova-reconciliation-service.openapi.yaml`. Not a stage inside `pipelines/` or
`backend_ocr/` — its own process, own DB pool, own deploy.

## What it does

Reconciles an uploaded bill (`bill_id`) against Nova, per the Bill Reconciliation Flow: capture
service month + vendor, call Nova, then branch on the tenant's `entity_type` in
`lendix/nova_vendor_mapping.json`:

- **Flow A — recurring vendor tenant (RE)**, e.g. Bright Loan: every bill line item is checked
  directly against that tenant's own certified catalog. Never a second Nova call.
- **Flow B — non-recurring vendor tenant (Non-RE)**, e.g. Vimono Tech: line items within scope are
  reconciled against the tenant's own Nova pull; line items outside scope are held as
  `PENDING_EXTENDED_LOOKUP`, naming the cost centre, until a second Nova pull for that cost centre
  (`extended_nova_response`) is supplied — then they're reconciled against it.

Within either flow, every certified line runs a three-way check: (1) is this service certified for
this entity/cost-centre at all; (2) does the vendor's invoice line match what Nova's own usage
ledger recorded, within the allowed variance (a per-line absolute `allowed_variance` override, or
the mapping's percentage tolerance otherwise); (3) is Nova's own billed rate within tolerance of
the certified rate card, and is Nova's own arithmetic internally consistent. See the worked
examples in the design doc §4.1 (Flow A) and §4.2 (Flow B, across both calls).

**Kreetibudha** is named in the flow spec as a recurring vendor tenant but currently has no
`tenant_id` and no `vendor_rates` in the mapping JSON — it cannot be onboarded into either flow
until those are added (see design doc §0 and §8.3).

## Run locally

```bash
cd services/nova_reconciliation
pip install -r requirements.txt
uvicorn app.main:app --port 8100 --reload
```

Health check: `GET http://localhost:8100/health`.

The three owned tables (`nova_reconciliation_module_aliases`, `nova_reconciliation_runs`,
`nova_reconciliation_lines`) already exist on the shared platform DB — the migration was applied
directly and the one-shot `schema.sql` removed rather than kept around as a file that could drift
from what's actually live. Full DDL is documented in `docs/NOVA_RECONCILIATION_LOGIC.md` §9.2 if
it ever needs to be re-applied elsewhere (a fresh DB, a different environment).

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `NOVA_RECONCILIATION_DB_URL` | falls back to `DB_URL` | Postgres connection string. Same instance as the platform DB works fine — this service only needs read access to `dim_tenants`/`dim_cost_centres` and owns its own three tables. |
| `NOVA_VENDOR_MAPPING_PATH` | `<repo root>/lendix/nova_vendor_mapping.json` | Path to the mapping JSON. Hot-reloaded on file mtime change — no restart needed when finance edits it. |
| `NOVA_RECONCILIATION_PORT` | `8100` | Local dev port (uvicorn's `--port` flag is what actually binds it; this is read by deploy tooling, not by `uvicorn` itself). |
| `NOVA_RECONCILIATION_CORS_ORIGINS` | `*` | Comma-separated allowed origins. Set to the frontend's real origin(s) before any non-local deploy. |

## Endpoints

- `POST /v1/reconcile` — run reconciliation for one bill, persist, return the result. Re-POST the
  same `bill_id` with `extended_nova_response` filled in to complete a Flow B bill's Phase 2.
- `GET /v1/reconciliation?bill_id=` — read the latest persisted run for a bill (the dispatch view's
  natural read path). `?tenant_id=&vendor=&month=` also works.
- `GET /v1/dashboard/summary?owner_tenant_id=&period=` — aggregated counts for the dashboard, grouped by the tenant that actually **owns** each line (not the tenant that paid for the bill — see design doc §4.2 for why those differ).

## Known limitations (see design doc §8 for the full list)

- Module-slug resolution (`app/aliases.py`) is a similarity scorer against the mapping JSON's own
  `vendors{}` index, not a hardcoded table — it's only been verified against Signzy's 8 modules
  from the one sample bill available. A slug that scores below `MIN_SCORE` for every candidate
  comes back `UNRESOLVED_MODULE`; add a row to `nova_reconciliation_module_aliases` to correct it,
  which then wins outright over the scorer for that `(vendor, module_slug)` going forward.
- Tiered/package rates (`rate_unit` other than `INR/hit`) are never auto-compared — they come
  back as `NOT_AUTO_COMPARABLE`, not a guessed pass/fail.
- `Kreditbuddha`, `Credme`, and `Salary112` have no owning Registered Entity in the mapping
  JSON today, so bills scoped to them can never produce a `COST_CENTRE_OWNED` line.
