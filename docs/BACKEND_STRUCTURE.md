# Backend structure

`backend_ocr/app/main.py` used to be 8,822 lines: 115 endpoints, 26 schema
migrations, the approval-chain engine, token minting, four filesystem roots and
seven in-process caches, all in one module. That single fact sat behind most of
the findings in the scalability audit — when every handler lives in one file,
each one re-derives the connection handling, the tenant predicate and the row
serialisation by copy-paste, and a reviewer has no way to see that one of the
copies is missing a `WHERE tenant_id`.

It is now a composition root of ~180 lines that builds the app and mounts
routers. Nothing else.

## Layout

```
backend_ocr/app/
├── main.py               FastAPI app, middleware, router mounts — no endpoints
├── config.py             environment and the psycopg2 pool — the lowest layer
├── migrations.py         the 26 startup DDL steps, attached by register(app)
├── security/             who is calling, whose data, what they are licensed for
│   ├── auth.py           identity — the tenant from the verified JWT
│   ├── tenancy.py        ownership — whether this row belongs to that tenant
│   ├── licensing.py      entitlement gate, resolved per route template
│   ├── licensing_schema.py
│   └── services.py       the service catalog licensing keys off
├── core/                 building blocks used by more than one router
│   ├── approval_chain.py the configured chain, its statuses, who may act
│   ├── tokens.py         access/refresh token minting and hashing
│   ├── storage.py        every filesystem root, on one anchor
│   ├── dbconn.py         connection helpers over the pool
│   ├── observability.py  request ids, structured logs, Prometheus metrics
│   ├── notifications.py  cache behind the notification badge
│   ├── vendor_identity.py PAN derived from GSTIN
│   └── tenant_settings.py per-tenant feature flags
├── integrations/         outbound clients for third-party systems
│   ├── ocr.py            re-exports the shared utils/ocr_engine
│   ├── email_oauth.py    Gmail OAuth
│   ├── sandbox_kyb.py    KYB / GST verification provider
│   ├── imps_client.py    Lendix IMPS payment gateway
│   └── icici_act_codes.py
├── domain/               business logic with no HTTP surface of its own
│   ├── ingest.py
│   ├── fact_graph.py
│   └── tenant_defaults.py
├── routers/              the HTTP surface, one module per domain
│   ├── bills.py          list, detail, edit, payment, soft-status
│   ├── bill_approvals.py moving a bill through the chain, incl. email approval
│   ├── bill_attachments.py · bill_tds.py · bill_export.py
│   ├── vendors.py        master data, PAN de-duplication, merge
│   ├── vendor_banking.py · vendor_contacts.py · vendor_gstins.py
│   ├── vendor_documents.py
│   ├── invoices.py       upload, OCR, enrichment, document serving
│   ├── reports.py · settings.py · ledgers.py · item_mapping.py
│   ├── categories.py · users.py · org.py · system.py · auth.py
│   ├── disbursement.py   vendor payments over the IMPS gateway
│   ├── tds.py · fx.py · billing.py · nova_credentials.py
│   ├── admin.py          cross-tenant ops surface (platform identity)
│   ├── agent.py          the Vendor Bill Agent
│   ├── agent_hub.py      the /api/agents/* Agent Hub surface
│   ├── email_connection.py per-user Gmail connection
│   └── tally_agent.py
└── sql/
```

Only four modules sit at the package root: `main.py` (the composition root),
`config.py` (the lowest layer, imported by everything and by scripts outside the
package), `migrations.py`, and `__init__.py`.

Two modules were renamed on the way in, because their old names stopped working
once they sat beside their siblings: `agents_api.py` became `routers/agent_hub.py`
(it would otherwise have been `agent.py` next to `agent.py`), and `email_api.py`
became `routers/email_connection.py` rather than `email.py`, which would shadow
the standard library inside the package.

## Where does my code go?

**A new endpoint** goes in the router whose domain it belongs to. If no router
fits, add one and mount it in `main.py`; a router is cheap.

**A helper used by one router** stays in that router.

**A helper a second router needs** moves to `core/`. This is the rule that keeps
the boundaries honest: *a router must never import another router.* If you find
yourself wanting to, the helper belongs in `core/` and the urge is telling you
the boundary is in the wrong place.

**A schema change** does not go in `migrations.py` if you can avoid it. Those 26
steps still run in-process on every boot of every worker, which is its own
finding in the audit — the destination is a real migration tool run as a deploy
step, and adding to the pile makes that harder.

## Rules the tests enforce

- **Every route reaches the authenticated tenant.**
  `tests/test_tenant_route_map.py` classifies all 218 routes as scoped, public or
  exempt, and fails on anything else. It walks through helper calls, so indirect
  scoping counts. A snapshot fixture pins the whole table, so a change to any
  route's scoping status shows up as a reviewable diff rather than silently.
- **Every route resolves to a licensed service.**
  `tests/test_license_route_map.py`, same idiom, for entitlements.
- **Ownership checks answer 404, never 403.**
  `tests/test_tenancy.py`. A 403 would confirm the record exists and turn the
  endpoint into an existence oracle for other tenants' ids.
- **Metrics are labelled by route template, and /metrics is not open.**
  `tests/test_observability.py`. Labelling with the request path would mint a
  time series per invoice UUID; an unset `METRICS_TOKEN` answers 503 rather than
  serving to anyone. It also pins that 5xx bodies are scrubbed while 4xx detail
  survives.

## What the split did and did not change

The move was mechanical: code was relocated verbatim, with two edits per moved
route — `@app.<verb>` became `@router.<verb>`, and startup handlers lost their
decorator in favour of `migrations.register(app)`. Verified by the route
snapshot: all 218 routes kept identical paths and identical scoping.

Three things did change, each because the move forced it:

1. **Filesystem roots were re-anchored.** `UPLOADS_DIR`, `ATTACH_DIR`,
   `VENDOR_DOCS_DIR` and `BRAND_LOGOS_DIR` were each written as
   `Path(__file__).parent.parent` relative to `main.py`. Counted from a module
   one package deeper they would all have pointed one directory too shallow.
   They now derive from a single `BACKEND_ROOT` in `core/storage.py`, which is
   also why they are all in one place.
2. **Five more `__file__`-relative paths were re-anchored**, all the same bug:
   `_PIPELINE_ROOT` in `routers/settings.py` and `integrations/ocr.py` (three
   `dirname()` calls, needing four), `_TALLY_DIR` in `routers/tally_agent.py`
   (`parents[2]`, needing `parents[3]`), and the upload directories in
   `routers/agent.py` and `domain/ingest.py`. Each now uses `PROJECT_ROOT` or
   `UPLOADS_DIR` from `core/storage.py`.

   This is the failure mode to watch for when moving a module between packages:
   nothing errors at import time, the path is simply wrong, and `ocr.py` was the
   only one of the five that failed loudly (its `sys.path` insert stopped finding
   `utils/`). The other four would have silently read and written the wrong
   directory. Counting directories with `__file__` is why they were fragile;
   anchoring on one named constant is the fix.
3. **207 lines of unreachable code were deleted** — the SAP/TOON catalogue
   helpers (`_sap_catalog_toon`, `_toon_cell`, `_sap_collect`, `_sap_prefilter`
   and their two constants) plus `_verify_vendor_kyb`,
   `_gstin_verify_result_to_status` and `_ADMIN_EMAIL`. Each was reachable from
   no route, no startup handler and no other module.

Around 970 unused import statements were also dropped, since each extracted
module initially inherited `main.py`'s entire import header.

## Still open

The split does not by itself fix the findings it makes visible. Connection
handling is still hand-rolled per handler (85 acquisitions with no `finally`),
the caches in `core/` are still per-process and so still block a second replica,
and `migrations.py` still runs DDL at boot. Those are tracked separately; the
structure now makes each of them a change to one module instead of 115.

What did change is that the connection leak is no longer invisible:
`db_pool_connections_in_use` climbing and never returning to baseline is its
signature, and that gauge is exported now. Watch it before fixing the leaks, not
after — the failure mode is a box that wedges without crashing.
