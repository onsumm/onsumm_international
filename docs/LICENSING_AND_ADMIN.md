# Service Licensing & the Ops Panel

How a client is onboarded, what they are allowed to use, and how we change it.

Last updated: 2026-08-15

---

## 1. Why this exists

Before this, onboarding a client meant writing rows into Postgres by hand, and
per-tenant differences were expressed three different ways:

| Where | Example |
|---|---|
| A literal in source | `COMPANY_NAMES` — a dict of four tenant UUIDs stamped into the JWT |
| A one-off DB column | `dim_tenants.sap_matching_enabled` — the only real feature flag |
| Not at all | Nova billing reconciliation shipped to every tenant regardless |

There was also no cross-tenant principal: the only way to act across tenants was
SSH and `psql`.

Now every module is a named **service**, each tenant has an explicit row per
service, and ops manages all of it from `/admin`.

---

## 2. The model

**`tenant_service` is the only source of truth.** One row per (tenant, service).
A missing row means "inherit `service_catalog.default_enabled`", so adding a new
core service lights it up for every existing tenant with no backfill.

**Packages are onboarding convenience only.** Applying one *copies* rows into
`tenant_service`. Nothing resolves through a package at request time, so editing a
package never silently changes a live tenant.

**Licensing is not preference.** `dim_tenants.settings_jsonb` (auto-approve
threshold, TDS config, FX settings) and `agent_settings.enabled` remain *tenant
preferences*. A revoked licence overrides any preference; a granted licence does
not force a feature on.

### Adding a service

1. Add a `ServiceDef` to `SERVICES` in [backend_ocr/app/services.py](../backend_ocr/app/services.py).
2. Add a rule to `_RULES` in [backend_ocr/app/licensing.py](../backend_ocr/app/licensing.py)
   mapping its routes to the key.
3. Mirror the key and label in [frontend/src/auth/services.ts](../frontend/src/auth/services.ts).
4. Run `python3.12 tests/test_license_route_map.py --write` and review the diff.

The catalog syncs into `service_catalog` on every boot, so the key is available to
the admin panel immediately. **Never delete a `ServiceDef`** — dropping it from the
tuple stamps `retired_at`, which preserves the historical `tenant_service` rows.

---

## 3. Enforcement

Two layers, neither of which requires touching a handler:

1. **`license_gate`** — an app-level dependency registered after `auth_context`
   ([main.py](../backend_ocr/app/main.py)). It resolves the matched route to a
   service and 403s if the tenant is not licensed.
2. **Router-level `require_service(...)`** on the four routers that are entirely
   one service (tds, fx, billing/nova, disbursement). Redundant on purpose: a route
   added to one of those files is licensed before anyone updates the map.

### The map keys on the route *template*, not the URL

`request.scope["route"].path` gives `/api/bills/{invoice_id}`, not `/api/bills/abc`.
This matters because the namespaces overlap — `disbursement_api` mounts at `/api`
and owns `/api/vendors/{supplier_id}/payment-profile`, which sits *inside* the core
`/api/vendors` namespace. Matching on the concrete path would license
payment-profile as `vendors`, or all of vendors as `disbursement`. Rules resolve
**longest-template-first**, so a specific rule always beats the namespace it sits
inside, regardless of declaration order.

### Rollout mode

`LICENSE_ENFORCEMENT` = `off` | `log` | `enforce`, default **`log`**.

- `log` — resolves and records every would-be denial (`[license] DENY(log) …`) but
  lets the request through.
- `enforce` — 403s.

**Ship `log`.** Read the logs for a week; any denial for a tenant that legitimately
uses that feature is a backfill or map bug. Only flip to `enforce` when that log is
quiet. It is a per-process env var, so rolling back is one restart.

### Cache

Entitlements are cached in-process for `LICENSE_CACHE_TTL` (default 300s), and the
admin API busts the cache on write. **Caveat:** the bust is per-process. The
deployment runs multiple uvicorn workers, so a toggle takes effect immediately on
the worker that served the write and up to the TTL later on the others. A
`pg_notify`-based cross-worker bust is the natural next step; until then the TTL
bounds the staleness, which is no worse than the flag it replaced.

### The guard test

[tests/test_license_route_map.py](../tests/test_license_route_map.py) asserts that
every route resolves to a known service, and pins the whole resolution table to
`tests/fixtures/license_route_map.txt`. Totality alone would let someone silently
re-license `/api/bills` by widening a rule; the snapshot makes it a reviewable diff.

---

## 4. The ops identity

Completely separate from tenant auth:

| | Tenant token | Platform token | Impersonation token |
|---|---|---|---|
| Signed with | `JWT_SECRET` | `PLATFORM_JWT_SECRET` | `JWT_SECRET` |
| `typ` | absent | `platform` | absent |
| `company_id` | tenant uuid | **absent** | tenant uuid |
| `act` | absent | absent | `{sub, sid}` |

`PLATFORM_JWT_SECRET` has **no default and no fallback to `JWT_SECRET`**. If unset,
`/api/admin/*` returns 503. Set a **different value in dev and prod** — they
currently share `JWT_SECRET` (see [DEV_ENVIRONMENT.md](DEV_ENVIRONMENT.md) §6), so a
shared value would make a dev ops token valid against production.

Three independent reasons a platform token cannot act as a tenant: different
signing key, the explicit `typ` rejection in `auth_context`, and the absence of a
`company_id` claim (so `require_tenant()` raises). Pinned by
[tests/test_platform_auth_isolation.py](../tests/test_platform_auth_isolation.py).

### Ops roles

`readonly` < `support` < `admin` < `owner`.

- **readonly** — view tenants, usage, audit
- **support** — also open impersonation sessions
- **admin** — also change licensing, edit tenants, onboard clients
- **owner** — also manage platform users

### Impersonation

`POST /api/admin/tenants/{id}/impersonate` mints a short-lived (≤30 min, default 15)
*tenant-shaped* token. That shape is deliberate: every existing endpoint works
unmodified. What marks it is the `act` claim.

- **Read-only, enforced server-side.** `impersonation_gate` refuses every mutation
  via an **allowlist** (currently empty), not a denylist — a denylist over ~200
  routes is wrong within a month, and wrong in the dangerous direction.
- A reason of ≥10 characters is required and written to `admin_audit_log`.
- No refresh token is issued; the session dies at expiry.
- `sub` is the ops email, never a real tenant user, so audit trails record who was
  actually there.
- A customer can opt out entirely:
  `dim_tenants.settings_jsonb.impersonation_opt_out = true`.

Never allowlist: anything under `disbursement` (money movement), `/api/users*`
(privilege escalation that outlives the session), token minting, or outbound email.

---

## 5. Schema

Defined once in [backend_ocr/app/licensing_schema.py](../backend_ocr/app/licensing_schema.py)
and imported by both the startup hook and the migration script, so they cannot drift.

| Table | Purpose |
|---|---|
| `service_catalog` | Mirror of `services.SERVICES`; FK target for the rest |
| `tenant_service` | **The source of truth** — tenant × service, plus limits |
| `service_package` / `service_package_item` | Onboarding prefills (copy-only) |
| `platform_users` | Ops accounts, bcrypt, with lockout |
| `platform_refresh_tokens` | Rotation + reuse detection, mirroring `auth_refresh_tokens` |
| `platform_impersonation` | Support sessions, with reason and expiry |
| `tenant_usage` | Monthly rollup per tenant/service/metric |
| `admin_audit_log` | Append-only record of every ops action |
| `schema_migrations` | Ledger, so the one-shot backfill can't run twice |

Plus additive columns on `dim_tenants`: `pipeline_enabled`, `onboarded_at`,
`package_key`, `legal_name`, `primary_contact_email`.

### Applying it to BOTH databases

Startup DDL only ever reaches the database the running process points at, and
`DB_PROD_URL` is read by no application code. So prod needs the script — ideally
run **before** the code deploy, so the app boots into a ready schema.

```bash
# Always dry-run against prod first: applies, prints, then ROLLBACKs.
python3.12 scripts/licensing_migrate.py --target prod --dry-run

# Schema + catalog + packages, both databases
python3.12 scripts/licensing_migrate.py --target both --seed-catalog --seed-packages

# One-shot: every tenant keeps exactly what it has today (§6). Guarded by
# schema_migrations, so re-running is a no-op.
python3.12 scripts/licensing_migrate.py --target both --backfill

# First ops account. Password is prompted, never passed on the command line.
python3.12 scripts/licensing_migrate.py --target dev --bootstrap-admin ops@onsumm.ai
```

`--target` has no default: naming the database you are about to change should be a
deliberate act. A preflight refuses to run if `dim_tenants` is absent.

---

## 6. The backfill: nothing visibly changes

`--backfill` grants every existing tenant exactly what it has today:

- everything currently ungated → **on** for every tenant
- `sap_matching` → only where `dim_tenants.sap_matching_enabled` is true
- `gst_tax_verification` → **inherits the SAP flag**, because the UI currently
  piggybacks the GST banner on it. Backfilling identically means nothing changes on
  the day this ships; decoupling the two is then a deliberate, visible act.
- `nova_billing` → only tenants with rows in `billing_vendor_accounts`

Verify before enforcing — the script prints the resulting per-service counts:

```sql
SELECT service_key, count(*) FILTER (WHERE enabled) FROM tenant_service GROUP BY 1;
```

Cross-check that against `sap_matching_enabled` and `billing_vendor_accounts` on
both databases. A tenant's screen should be identical the day this ships.

---

## 7. Onboarding a client

`/admin/onboarding`, or `POST /api/admin/onboarding/provision`. **One transaction**,
all-or-nothing — a half-provisioned tenant (a company with no admin, or an admin who
cannot approve anything because no chain exists) is worse than a clean failure, and
is exactly what manual onboarding kept producing.

Steps, all in [tenant_defaults.py](../backend_ocr/app/tenant_defaults.py):

1. `dim_tenants` row (GSTIN stored as NULL if it collides, so provisioning survives)
2. Default brand, named after **this** tenant
3. Default approval chain, seeded as both working copy and snapshot, so "Reset to
   Default" works on day one
4. First admin user (bcrypt; a plain INSERT, never an upsert)
5. Services from the chosen package, or core services if none
6. `pipeline_enabled = true`

### The username caveat

`users.username` is **globally unique** and `/api/login` looks up by username alone,
so the second tenant cannot also have an `admin`. Call
`POST /api/admin/onboarding/validate` first — the wizard does — to surface the
collision before provisioning rather than as a failed transaction.

An email address always works. Scoping usernames per tenant
(`UNIQUE(company_id, username)` plus a company selector at sign-in) is a tracked
future change; nothing here forecloses it.

---

## 8. Pipeline processing

`PIPELINE_TENANT_ID` (comma-separated; empty = all tenants) now lives in `.env`,
not in `main.py`. Every worker filters on it, so while it was a source literal a
newly onboarded tenant got **zero** background processing — no OCR, no approvals,
no verification — until someone edited the file and redeployed.

`dim_tenants.pipeline_enabled` exists as the DB-driven successor. Wiring the 23
worker call sites to it is the follow-up; until then the env var governs.

**When you onboard a client, add their tenant id to `PIPELINE_TENANT_ID` (or clear
the variable to process all tenants) and restart the pipeline process.**

---

## 9. Environment variables

| Variable | Default | Notes |
|---|---|---|
| `PLATFORM_JWT_SECRET` | *(none)* | Required for `/api/admin/*`. Must differ from `JWT_SECRET` and between environments. |
| `LICENSE_ENFORCEMENT` | `log` | `off` \| `log` \| `enforce` |
| `LICENSE_CACHE_TTL` | `300` | Seconds |
| `PLATFORM_TOKEN_TTL_MIN` | `30` | Ops access token |
| `PLATFORM_REFRESH_TTL_DAYS` | `7` | |
| `IMPERSONATION_MAX_TTL_MIN` | `30` | Hard cap |
| `PIPELINE_TENANT_ID` | *(empty = all)* | Moved out of `main.py` |
| `DISBURSEMENT_DEMO_OTP` | *(empty = off)* | **Was `123456` by default** — a fixed bypass of 2FA on money movement in any environment that forgot to set it. Demo environments now opt in explicitly. |

---

## 10. Security fixes shipped alongside

These were prerequisites: licensing is decorative if tenant resolution can be spoofed.

| Where | Was | Now |
|---|---|---|
| `disbursement_api._tid()` | Tenant from a **client-supplied** `company_id`, defaulting to the demo tenant — a cross-tenant IDOR on the payment surface | `require_tenant()` from the verified token |
| `billing_api.require_tenant` | A second HS256 verifier bypassing `auth.py`, and therefore RBAC mode and the licensing seam | Deleted; imports the single verifier |
| `disbursement_api.initiate_disbursement` | Admin check read `body.actor_role` — satisfied by sending `"admin"` | `require_role("admin")` from the token |
| `POST/PUT/DELETE /api/users` | **No role check** — any member could mint themselves an admin | `require_role("admin")`, plus a guard against deleting the last admin |
| `auth._verify` local mode | No `require: ["exp"]`, so a token omitting the claim never expired | Expiry required |
| `AuthContext.clearSession` | Removed `onsum_bills_cache`, but the real keys are `onsum_bills_cache_<ver>_<company>_<role>_<brand>` — one tenant's bills survived sign-out | Prefix sweep |
