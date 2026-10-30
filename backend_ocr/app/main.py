"""Composition root for the Onsumm Invoice API.

This module builds the FastAPI app, installs the cross-cutting middleware and
dependencies, and mounts every router. It holds no endpoints and no business
logic of its own — those live in `app/routers/` (one module per domain) and
`app/core/` (the few things more than one domain needs).

Layout
──────
    app/
      main.py            this file — app, middleware, router mounts
      migrations.py      schema DDL run at startup
      auth.py            identity: who is calling, and for which tenant
      tenancy.py         ownership: whether this row is theirs
      core/              shared building blocks (approval chain, tokens, paths)
      routers/           HTTP surface, one module per domain

Where to add an endpoint: the router whose domain it belongs to. If it needs a
helper that a second domain also needs, that helper moves to `core/` — a router
importing another router is the signal that a boundary is in the wrong place.

Every route is authenticated by `auth_context` and gated by `license_gate` below,
and must scope its queries to the tenant those establish;
`tests/test_tenant_route_map.py` fails the build if a new one does not.
"""
from __future__ import annotations

import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from starlette.formparsers import MultiPartParser

from .core import observability
from .security.auth import auth_context
from .security.licensing import impersonation_gate, license_gate, require_service

# Domain routers — the surface that used to be main.py's 115 endpoints.
from .routers import auth as auth_routes, bank_logos, bill_approvals, bill_attachments, bill_export, bill_tds, bills, categories, invoices, ledgers, item_mapping, org, reports, review_hub, settings as settings_routes, system, users, vendor_activity, vendor_banking, vendor_contacts, vendor_doc_classify, vendor_documents, vendor_gstins, vendor_logos, vendors

# Feature routers that already lived in their own modules.
from .routers.admin import router as admin_router
from .routers.agent import router as agent_router
from .routers.agent_hub import router as agents_router
from .routers.billing import router as billing_router
from .routers.disbursement import router as disbursement_router
from .routers.email_connection import router as email_router
from .routers.sso import router as sso_router
from .routers.fx import router as fx_router
from .routers.nova_credentials import router as nova_credentials_router
from .routers.tally_agent import router as tally_agent_router
from .routers.tds import router as tds_router

# Crypto rail (docs/crypto-rail): USDC payouts, on-chain reconciliation, audit anchor.
from .routers import audit_proof, crypto_onboarding, crypto_payouts, crypto_wallets, onchain, vendor_portal
# Which country's rules apply to a tenant (core/jurisdiction.py).
from .routers import jurisdiction as jurisdiction_routes

# Global tenant authentication: every request is validated by auth_context, which
# rejects non-public paths lacking a valid login JWT and pins the authenticated
# tenant for the request. Handlers read it via require_tenant() — never from a
# client-supplied company_id.
#
# license_gate runs *after* auth_context (app-level dependencies are solved in
# declaration order, so the tenant is already pinned) and 403s any request for a
# service the tenant is not licensed for. It resolves the service from the matched
# route template, which is how ~200 endpoints get gated without editing handlers.

# Declares the bearer scheme so Swagger UI shows an "Authorize" button and sends
# `Authorization: Bearer <token>` on Try-it-out. It is documentation only:
# auto_error=False means it never rejects a request, so the public paths keep
# working and enforcement stays entirely with auth_context below. Declared last so
# the auth_context -> license_gate ordering above is untouched.
_bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    description=(
        "Paste an access token (no `Bearer ` prefix — Swagger adds it).\n\n"
        "**Tenant token:** `POST /api/login` with a username and password, then copy "
        "`token` from the response.\n\n"
        "**Platform/ops token:** `POST /api/admin/login`. It is signed with a "
        "different key (PLATFORM_JWT_SECRET) and works only on `/api/admin/*`; a "
        "tenant token is rejected there and vice versa.\n\n"
        "Access tokens expire after ACCESS_TOKEN_TTL_MIN (60 minutes by default). "
        "When calls start returning 401, log in again or exchange the refresh token "
        "at `POST /api/auth/refresh`."
    ),
)

app = FastAPI(
    title="Onsumm Invoice API",
    version="1.1.0",
    description=(
        "Every path except a short public allowlist needs a bearer token. Click "
        "**Authorize**, paste a token from `POST /api/login`, and Try-it-out will "
        "send it on every request.\n\n"
        "The tenant is always read from the token, never from a `company_id` "
        "parameter — those still appear on many endpoints and are ignored."
    ),
    dependencies=[
        Depends(auth_context),
        Depends(license_gate),
        Depends(impersonation_gate),
        Depends(_bearer_scheme),
    ],
)

_MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB per file upload
# Starlette <0.42 does not accept max_part_size in request.form(); patch the class limit globally.
MultiPartParser.max_part_size = _MAX_UPLOAD_SIZE


# ── Core domain routers ──────────────────────────────────────────────────────
# Ungated here: these span services, so license_gate resolves the entitlement per
# route from the matched template.
for _router_module in (
    system,
    auth_routes,
    bills,
    bill_approvals,
    bill_attachments,
    bill_tds,
    bill_export,
    vendors,
    vendor_banking,
    vendor_contacts,
    vendor_gstins,
    vendor_doc_classify,
    vendor_documents,
    vendor_activity,
    bank_logos,
    vendor_logos,
    invoices,
    reports,
    settings_routes,
    ledgers,
    item_mapping,
    categories,
    users,
    org,
    review_hub,
):
    app.include_router(_router_module.router)


# ── Feature routers ──────────────────────────────────────────────────────────
# Routers that are entirely one service also carry a router-level gate. This is
# redundant with license_gate — deliberately so: a route added to one of these
# files is licensed correctly even before anyone updates the route map. Both paths
# share the same entitlement cache, so the second check costs nothing.
#
# The others are not gated here because they span services: email_router and
# agent_router both hang routes off /api/vendors/{id}/contacts, and
# tally_agent_router serves the public agent-update manifest. license_gate handles
# those per route.
app.include_router(tally_agent_router)
app.include_router(tds_router, dependencies=[Depends(require_service("tds"))])
app.include_router(fx_router, dependencies=[Depends(require_service("fx"))])
app.include_router(nova_credentials_router, dependencies=[Depends(require_service("nova_billing"))])
# Same entitlement as the credentials router: both are the Nova billing module.
app.include_router(billing_router, dependencies=[Depends(require_service("nova_billing"))])
app.include_router(disbursement_router, dependencies=[Depends(require_service("disbursement"))])
# Crypto rail. The Safe-signing routes are entirely the USDC payout service, so
# they carry a router-level gate like the routers above. The others span
# services (onboarding vs. audit vs. public portal/proofs) and are resolved per
# route by license_gate; vendor_portal and audit_proof's /api/public routes are
# public by design (security/auth.py PUBLIC_PREFIXES) and check their own tokens.
app.include_router(crypto_payouts.router, dependencies=[Depends(require_service("crypto_payouts"))])
app.include_router(crypto_onboarding.router)
app.include_router(crypto_wallets.router)
app.include_router(onchain.router)
app.include_router(audit_proof.router)
app.include_router(vendor_portal.router)
app.include_router(jurisdiction_routes.router)
app.include_router(email_router)
app.include_router(sso_router)
app.include_router(agent_router)
app.include_router(agents_router)
# Cross-tenant ops surface. Guarded by its own identity (PLATFORM_JWT_SECRET) in
# auth_context, and exempt from license_gate — it belongs to us, not to a tenant.
app.include_router(admin_router)


# Schema migrations are intentionally run as an explicit deployment operation:
# `python3 scripts/run_api_migrations.py`. Keeping DDL out of API startup avoids
# a database lock making the entire web service unavailable.
#
# Non-DDL background work is a different concern and belongs here: starting a
# thread or an asyncio task doesn't hold a DB lock the way schema DDL can, so
# there is no reason to make these part of that explicit deploy step, and doing
# so would mean nobody's mailbox gets watched and no adapter sweeps run until
# someone remembers to run a migration script that no longer has any migration
# left to do for them. (This used to happen via `migrations.register(app)`,
# removed in the same change that moved DDL out of startup — but nothing
# replaced the non-DDL half of what it did, which is what these two handlers
# restore.)
#
# Each one is wrapped individually: a FastAPI startup handler that raises
# aborts the *entire* app's lifespan — Starlette never finishes starting up and
# no route ever serves a request, not just the broken subsystem's routes. A
# missing dependency or a bad DB connection for an optional background worker
# must degrade to "that worker didn't start," logged loudly, never to "the API
# is down." (Found the hard way: a missing package took the whole service down
# in exactly this spot before this try/except existed.)
@app.on_event("startup")
async def _start_background_agents() -> None:
    try:
        from .routers.agent import start_agent_poller
        start_agent_poller()  # Communication Agent: the Vendor Bill mailbox watcher
    except Exception:
        logging.getLogger(__name__).exception(
            "[startup] Vendor Bill mailbox watcher failed to start — "
            "the API is still up, but no mailbox is being watched."
        )

    try:
        from .review.scheduler import start_review_workers
        await start_review_workers()  # Review Hub adapters, review agent, comm extraction
    except Exception:
        logging.getLogger(__name__).exception(
            "[startup] Review Hub / Compliance / Communication Agent workers failed "
            "to start — the API is still up, but no adapter sweeps or claim "
            "extraction are running."
        )

    try:
        from .domain.payments.scheduler import start_disbursement_sweeper
        await start_disbursement_sweeper()  # asks the bank about PENDING payments
    except Exception:
        logging.getLogger(__name__).exception(
            "[startup] Disbursement sweeper failed to start — the API is still up, "
            "but a payment left PENDING will stay PENDING until someone opens its "
            "receipt card and presses Check status."
        )

    try:
        from .domain.crypto.scheduler import start_crypto_workers
        await start_crypto_workers()  # chain watcher + matcher, Safe relay retry, ledger poster, daily anchor
    except Exception:
        logging.getLogger(__name__).exception(
            "[startup] Crypto rail workers failed to start — the API is still up, "
            "but USDC payouts will not be confirmed until the watcher runs "
            "(POST /api/onchain/rescan runs one pass by hand)."
        )


@app.on_event("shutdown")
async def _stop_background_agents() -> None:
    from .review.scheduler import stop_review_workers
    await stop_review_workers()
    from .domain.payments.scheduler import stop_disbursement_sweeper
    await stop_disbursement_sweeper()
    from .domain.crypto.scheduler import stop_crypto_workers
    await stop_crypto_workers()


# Every change made from the Review Hub (its endpoints, and the vendor / bill
# cards it opens) is recorded with its before → after — review/audit.py.
from .review.audit import ReviewAuditMiddleware  # noqa: E402
app.add_middleware(ReviewAuditMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Observability ────────────────────────────────────────────────────────────
# Request ids, structured logging, Prometheus metrics at /metrics, and one
# handler for exceptions that reach the ASGI boundary. Replaces the middleware
# that used to live here under a `TEMP:` comment and printed a line per request
# to stdout. See core/observability.py for what each piece is for — in
# particular db_pool_connections_in_use, which is the alert that catches the
# connection leak before it wedges the process.
observability.install(app)
