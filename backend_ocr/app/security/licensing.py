"""Service licensing: route -> service resolution, entitlement caching, and the
app-level gate that turns an unlicensed request into a 403.

`tenant_service` is the only source of truth for what a tenant may use. Packages
(`service_package`) are an onboarding convenience that *copies* rows into it —
nothing resolves through a package at request time, so changing a package never
silently changes a live tenant.

Three things worth knowing before editing this file:

**The map is keyed on the route template, not the URL path.** Starlette puts the
matched route into the request scope before dependencies are solved, so
`request.scope["route"].path` is `/api/bills/{invoice_id}`, not `/api/bills/abc`.
This matters because the namespaces overlap: `disbursement_api` mounts at `/api`
and owns `/api/vendors/{supplier_id}/payment-profile`, which sits *inside* the
core `/api/vendors` namespace. Matching on the concrete path would either license
payment-profile as `vendors` or all of vendors as `disbursement`. Templates plus
longest-match-wins resolves it exactly.

**Rules are resolved longest-first, not in declaration order.** The order below is
for humans; `_SORTED` is what runs. So a specific rule always beats the broad
namespace it sits inside, no matter where it is written.

**Enforcement is staged** via LICENSE_ENFORCEMENT=off|log|enforce. `log` records
every would-be denial without acting on it, so a mis-mapped route shows up in the
log rather than as a customer outage. Ship `log`, read the logs for a week, then
flip to `enforce`.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..security.auth import current_impersonation, current_tenant
from ..config import get_conn, put_conn
from ..security.services import SERVICE_KEYS

log = logging.getLogger(__name__)

# 'off' — resolve nothing, gate is a no-op (kill switch).
# 'log' — resolve and log denials, but let the request through (shadow mode).
# 'enforce' — 403 on an unlicensed service.
ENFORCEMENT = os.getenv("LICENSE_ENFORCEMENT", "log").strip().lower()

# Sentinel for routes that are authenticated but deliberately not licensed:
# login, the entitlements endpoint itself, health, and the whole platform-admin
# surface (which has no tenant at all).
UNLICENSED = "__unlicensed__"


# ── Route template -> service key ─────────────────────────────────────────────
# (methods, route-template prefix, service key). methods "*" means any.
_RULES: tuple[tuple[str, str, str], ...] = (
    # ── Review Hub ────────────────────────────────────────────────────────────
    ("*", "/api/review", "review_hub"),
    # Longest template wins, so this one overrides the line above: queueing an
    # agent review is the add-on, the queue itself is not.
    ("*", "/api/review/rescan", "anomaly_ai"),
    # ── Not tenant-licensed ───────────────────────────────────────────────────
    ("*", "/", UNLICENSED),
    ("*", "/health", UNLICENSED),
    # Operational surface, authenticated by METRICS_TOKEN rather than a tenant
    # login — a scraper has no tenant and nothing to be licensed for.
    ("*", "/metrics", UNLICENSED),
    ("*", "/docs", UNLICENSED),
    ("*", "/redoc", UNLICENSED),
    ("*", "/openapi.json", UNLICENSED),
    ("*", "/api/login", UNLICENSED),
    ("*", "/api/auth/", UNLICENSED),
    ("*", "/api/admin/", UNLICENSED),          # platform surface — no tenant
    ("*", "/api/tenant/features", UNLICENSED),  # must answer even when nothing is licensed
    ("*", "/api/tenant/entitlements", UNLICENSED),
    ("*", "/api/me/", UNLICENSED),
    ("*", "/api/oauth/email/", UNLICENSED),
    ("*", "/api/notifications", UNLICENSED),    # nav badge; denying it breaks the shell
    # Public, token-signed surfaces that carry no login session — the gate already
    # skips them for want of a tenant, but map them so they never look unmapped.
    ("*", "/api/bills/approve-link", UNLICENSED),
    ("*", "/api/tally/pair", UNLICENSED),
    ("*", "/api/tally/ingest", UNLICENSED),
    ("*", "/api/tally/agent/status", UNLICENSED),

    # ── Tax & compliance ──────────────────────────────────────────────────────
    ("*", "/api/settings/tds", "tds"),
    ("*", "/api/bills/{invoice_id}/recalc-tds", "tds"),
    ("*", "/api/bills/{invoice_id}/tds-override", "tds"),
    ("*", "/api/vendors/{supplier_id}/tds", "tds"),

    ("*", "/api/fx", "fx"),
    ("*", "/api/bills/{bill_id}/fx-payment", "fx"),
    ("*", "/api/bills/{bill_id}/fx-payments", "fx"),
    ("*", "/api/settings/fx", "fx"),

    # Only the *verification* calls are KYB. Recording a GSTIN or a bank account
    # is plain vendor master data, so a tenant without KYB can still keep records
    # — it simply never gets a verified badge.
    ("*", "/api/vendors/{supplier_id}/verify-bank", "kyc_verification"),
    ("*", "/api/vendors/{supplier_id}/reverify-bank", "kyc_verification"),
    ("*", "/api/vendors/{supplier_id}/gstins/primary/reverify", "kyc_verification"),
    ("*", "/api/vendors/{supplier_id}/gstins/{gstin_id}/reverify", "kyc_verification"),

    # ── Payments ──────────────────────────────────────────────────────────────
    ("*", "/api/billing", "nova_billing"),
    # The Nova credentials routes had no rule at all, so service_for_route()
    # returned None for them and test_every_route_is_mapped had been failing.
    # They were gated in practice — main.py mounts the router behind
    # require_service("nova_billing") — but the map, which is what the guard test
    # reads and what any future per-route gating would consult, did not know it.
    ("*", "/api/nova/credentials", "nova_billing"),
    ("*", "/api/disbursements", "disbursement"),
    ("*", "/api/bills/{invoice_id}/disbursements", "disbursement"),
    ("*", "/api/bills/{invoice_id}/payment", "disbursement"),
    ("*", "/api/vendors/{supplier_id}/disbursements", "disbursement"),
    ("*", "/api/vendors/{supplier_id}/payment-profile", "disbursement"),
    ("*", "/api/remitter", "disbursement"),
    # Crypto rail (docs/crypto-rail). The Safe signing routes hang off
    # /api/disbursements, so they are claimed here, longest template first.
    ("*", "/api/disbursements/{disbursement_id}/safe-tx", "crypto_payouts"),
    ("*", "/api/disbursements/{disbursement_id}/signatures", "crypto_payouts"),
    ("*", "/api/disbursements/{disbursement_id}/repropose", "crypto_payouts"),
    ("*", "/api/disbursements/{disbursement_id}/keep-only", "crypto_payouts"),
    ("*", "/api/disbursements/pending-usdc", "crypto_payouts"),
    ("*", "/api/vendors/{supplier_id}/wallets", "crypto_payouts"),
    ("*", "/api/vendors/{supplier_id}/wallet-invites", "crypto_payouts"),
    ("*", "/api/vendors/{supplier_id}/contract-rates", "crypto_payouts"),
    ("*", "/api/bills/{invoice_id}/crypto-checks", "crypto_payouts"),
    ("*", "/api/onboarding", "crypto_payouts"),
    ("*", "/api/treasury", "crypto_payouts"),
    ("*", "/api/tenant/rail-policy", "crypto_payouts"),
    # Which country's rules apply: core tenant settings, never a paid add-on.
    ("*", "/api/tenant/jurisdiction", "settings"),
    ("*", "/api/onchain", "crypto_payouts"),
    ("*", "/api/withholding", "crypto_tds"),
    # Every USDC payout shows its withholding before release, so the preview
    # belongs to the payout licence, not the remittance/certificate add-on.
    ("*", "/api/withholding/preview", "crypto_payouts"),
    ("*", "/api/audit", "audit_anchor"),
    # Public by design: the vendor portal is authenticated by its own signed,
    # expiring link token, and proof bundles are verified without a login.
    ("*", "/api/vendor-portal", UNLICENSED),
    ("*", "/api/public", UNLICENSED),

    # ── AI & automation ───────────────────────────────────────────────────────
    ("*", "/api/agent/", "agent_hub"),
    # Per-contact opt-in for the mailbox agent (agent_api.py).
    ("*", "/api/vendors/{supplier_id}/contacts/{contact_id}/agent", "agent_hub"),
    ("*", "/api/agents/fact-graph", "fact_graph"),
    ("*", "/api/agents/payment-context", "fact_graph"),
    ("*", "/api/mapping/", "sap_matching"),

    # ── Integrations ──────────────────────────────────────────────────────────
    ("*", "/api/sap/", "accounting_api_sync"),
    ("*", "/api/tally/", "tally_connector"),
    ("*", "/agent/version", UNLICENSED),        # public agent update manifest
    ("*", "/agent/download", UNLICENSED),       # public agent installer
    # All of these live in email_api.py and read or send from the user's mailbox.
    ("*", "/api/vendors/{supplier_id}/contacts/{contact_id}/email", "email_sending"),
    ("*", "/api/vendors/{supplier_id}/contacts/{contact_id}/thread", "email_sending"),
    ("*", "/api/vendors/{supplier_id}/contacts/{contact_id}/threads", "email_sending"),
    ("*", "/api/vendors/{supplier_id}/contacts/{contact_id}/attachment", "email_sending"),
    ("*", "/api/vendors/{supplier_id}/request-info", "email_sending"),
    ("*", "/api/vendors/{supplier_id}/outreach", "email_sending"),
    # The vendor card's inbox reads the user's mailbox like the thread routes.
    ("*", "/api/vendors/{supplier_id}/inbox", "email_sending"),

    # Bulk export is licensed separately from viewing reports in-app: a tenant can
    # read its dashboards without being entitled to pull the whole ledger out.
    ("*", "/api/reports/expense-xlsx", "data_extraction"),
    ("*", "/api/reports/expense-csv", "data_extraction"),
    ("*", "/api/vendors/bulk-excel", "data_extraction"),

    # ── Core (broad namespaces — always lose to the specific rules above) ──────
    ("*", "/api/invoices/", "upload_ocr"),
    ("*", "/api/docs/", "upload_ocr"),
    ("*", "/api/enrich-invoice", "upload_ocr"),
    ("*", "/api/ledgers/suggest", "upload_ocr"),
    ("*", "/api/bills", "bills"),
    ("*", "/api/vendors", "vendors"),
    ("*", "/api/vendor-document-types", "vendors"),
    ("*", "/api/vendor-logos", "vendors"),
    ("*", "/api/bank-logos", "vendors"),
    ("*", "/api/users", "users"),
    ("*", "/api/brands", "brands"),
    ("*", "/api/settings", "settings"),
    ("*", "/api/reports", "reports"),
    ("*", "/api/ledgers", "ledger"),
    ("*", "/api/coa-rules", "ledger"),
    ("*", "/api/categories", "ledger"),
    ("*", "/api/category-exp-heads", "ledger"),
    ("*", "/api/cost-centers", "ledger"),
    ("*", "/api/charges", "ledger"),
)

# Longest template wins; declaration order breaks ties.
_SORTED: tuple[tuple[str, str, str], ...] = tuple(
    sorted(_RULES, key=lambda r: len(r[1]), reverse=True)
)


def service_for_route(method: str, template: str) -> str | None:
    """Resolve a route template to a service key.

    Returns the key, ``UNLICENSED``, or ``None`` when the route matches no rule —
    which is a bug, and the guard test exists to catch it before it ships.
    """
    for methods, prefix, key in _SORTED:
        if methods != "*" and method.upper() not in methods.split(","):
            continue
        if _matches(template, prefix):
            return key
    return None


def _matches(template: str, prefix: str) -> bool:
    """Exact match, or a match on a whole path segment.

    The segment check is what stops `/api/bills` from claiming
    `/api/billsomething`, the same prefix trap the frontend's paths test guards.
    `/` is exact-match-only: as a prefix it would swallow every route that no
    other rule claimed, and silently turn the unmapped-route alarm off.
    """
    if template == prefix:
        return True
    if prefix == "/":
        return False
    return template.startswith(prefix.rstrip("/") + "/")


# ── Entitlement resolution + cache ────────────────────────────────────────────
@dataclass(frozen=True)
class Entitlements:
    enabled: frozenset[str] = field(default_factory=frozenset)
    limits: dict = field(default_factory=dict)


_TTL = float(os.getenv("LICENSE_CACHE_TTL", "300"))
_CACHE: dict[str, tuple[float, Entitlements]] = {}
_CACHE_LOCK = threading.Lock()


def _load(tenant: str) -> Entitlements:
    """Read effective entitlements for a tenant.

    LEFT JOIN from the catalog so a tenant with no row for a newly added service
    inherits ``service_catalog.default_enabled`` rather than being denied. That
    means adding a core service lights it up for every existing tenant on the next
    boot, with no backfill write.
    """
    enabled: set[str] = set()
    limits: dict = {}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.service_key,
                       COALESCE(ts.enabled, c.default_enabled) AS enabled,
                       COALESCE(ts.limits, '{}'::jsonb)        AS limits
                  FROM service_catalog c
                  LEFT JOIN tenant_service ts
                         ON ts.service_key = c.service_key
                        AND ts.tenant_id   = %s
                 WHERE c.retired_at IS NULL
                """,
                (tenant,),
            )
            for key, is_on, lim in cur.fetchall():
                if is_on:
                    enabled.add(key)
                if lim:
                    limits[key] = lim
        conn.commit()
    except Exception as exc:
        conn.rollback()
        # Fail OPEN on a resolution error. The alternative — denying every request
        # because one query failed — turns a transient DB blip into a total outage
        # for every tenant. Tenant isolation does not depend on this path; it is
        # enforced by require_tenant(), which is unaffected.
        log.error("[license] entitlement lookup failed for %s: %s", tenant, exc)
        raise
    finally:
        put_conn(conn)
    return Entitlements(enabled=frozenset(enabled), limits=limits)


def entitlements(tenant: str) -> Entitlements:
    """Cached entitlements for a tenant. See `invalidate` for the write path."""
    hit = _CACHE.get(tenant)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    try:
        ent = _load(tenant)
    except Exception:
        # Serve a stale entry rather than failing the request outright.
        if hit:
            return hit[1]
        return Entitlements(enabled=SERVICE_KEYS, limits={})
    with _CACHE_LOCK:
        _CACHE[tenant] = (time.time(), ent)
    return ent


def invalidate(tenant: str | None = None) -> None:
    """Drop cached entitlements so the next request re-reads from the DB.

    Called by the admin API after a toggle write, and by the pg_notify listener so
    the *other* uvicorn workers drop theirs too — an in-process cache alone means a
    toggle takes effect on one worker immediately and the rest up to TTL later.
    """
    with _CACHE_LOCK:
        if tenant:
            _CACHE.pop(tenant, None)
        else:
            _CACHE.clear()


def has_service(tenant: str, key: str) -> bool:
    return key in entitlements(tenant).enabled


# ── The gate ──────────────────────────────────────────────────────────────────
def _deny(key: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={
            "error": "service_not_licensed",
            "service": key,
            "message": "This feature is not enabled for your account.",
        },
    )


async def license_gate(request: Request):
    """App-level dependency, registered after ``auth_context`` so the tenant is
    already pinned. Gates every tenant route without touching a single handler."""
    if ENFORCEMENT == "off":
        return
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if not template:
        return

    key = service_for_route(request.method, template)

    if key is None:
        # An unmapped route is a mapping bug, not a licensing decision. Shout about
        # it, but never 503 a working endpoint over it — the guard test is what
        # keeps this from shipping.
        log.error("[license] UNMAPPED ROUTE %s %s", request.method, template)
        return
    if key == UNLICENSED:
        return

    tenant = current_tenant()
    if not tenant:
        return  # public route, or auth_context already rejected it

    ent = await run_in_threadpool(entitlements, tenant)
    if key in ent.enabled:
        return

    log.warning(
        "[license] DENY(%s) %s %s tenant=%s service=%s",
        ENFORCEMENT, request.method, template, tenant, key,
    )
    if ENFORCEMENT == "enforce":
        raise _deny(key)


# ── Impersonation write-gate ──────────────────────────────────────────────────
# Support sessions are read-only. This is an allowlist rather than a denylist on
# purpose: a denylist over ~200 routes is wrong within a month, and it is wrong in
# the dangerous direction. An allowlist is wrong only in the safe one.
#
# Entries are (METHOD, route template). Add them one at a time, with a reason.
IMPERSONATION_WRITE_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()

# Never allowlist these, even if a writable support mode is ever added:
#   * anything under disbursement — money movement is not a support capability
#   * /api/users* — minting a tenant admin is privilege escalation that outlives
#     the session
#   * /api/tally/generate-token, /api/sap/generate-token — credential minting
#   * outbound email — support must not mail the customer's vendors as them


async def impersonation_gate(request: Request):
    """App-level dependency: block mutations during an ops impersonation session."""
    if current_impersonation() is None:
        return
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    route = request.scope.get("route")
    template = getattr(route, "path", "")
    if (request.method, template) in IMPERSONATION_WRITE_ALLOWLIST:
        return
    log.warning("[impersonation] blocked %s %s session=%s",
                request.method, template, current_impersonation())
    raise HTTPException(
        status_code=403,
        detail={
            "error": "read_only_session",
            "message": "Support sessions are read-only. Ask the customer to perform this action.",
        },
    )


def require_service(key: str):
    """Dependency factory for gating a whole router at `include_router` time.

    Belt-and-braces alongside `license_gate`: a new route added to an
    already-single-service router is licensed even before anyone updates the map.
    Both paths share the same cache, so double-gating costs nothing.
    """
    async def _dep():
        if ENFORCEMENT == "off":
            return
        tenant = current_tenant()
        if not tenant:
            return
        ent = await run_in_threadpool(entitlements, tenant)
        if key not in ent.enabled:
            log.warning("[license] DENY(%s) router-level tenant=%s service=%s",
                        ENFORCEMENT, tenant, key)
            if ENFORCEMENT == "enforce":
                raise _deny(key)

    return _dep
