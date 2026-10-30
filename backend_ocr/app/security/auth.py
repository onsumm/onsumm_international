"""Central tenant authentication / isolation primitive.

Every tenant-scoped API request must carry a valid, short-lived bearer token.
The tenant, roles and permissions are read from the *verified* token — never
from a client-supplied ``company_id`` query/body field. This closes the
cross-tenant IDOR hole where any logged-in user could read or write another
tenant's data by changing a request parameter.

Token verification is pluggable:

  * **RBAC mode** — when ``RBAC_PROD_URL`` is configured, tokens are verified
    against the RBAC identity service's public key. The endpoint is fetched once
    and cached (``RBAC_KEY_CACHE_TTL``); both JWKS (``{"keys":[...]}``, key
    selected by ``kid``) and a bare PEM public key are auto-detected. Signature
    is checked with the asymmetric algorithms in ``RBAC_JWT_ALGORITHMS``
    (RS256/ES256), and ``exp`` (the 60-minute expiry) is always enforced.
  * **Local mode** — when ``RBAC_PROD_URL`` is empty (dev / until RBAC ships),
    tokens fall back to the app's own HS256 ``JWT_SECRET`` so the app keeps
    working. Login (``/api/login``) issues these.

Enforcement:
  * ``auth_context`` is registered as an app-level dependency (see main.py), so
    it runs for *every* request, rejecting non-public paths that lack a valid
    token with 401 before any handler runs.
  * Handlers/helpers call ``require_tenant()`` for the authenticated tenant.

Browser-native GETs (``<img src>``, download links, PDF viewer, SSE
``EventSource``) cannot set an Authorization header, so the token may instead be
passed as an ``?access_token=`` query param. ``approve-link`` uses its own
``?token=`` (APPROVAL_SECRET-signed) param and is public, so there is no clash.
"""
import contextvars
import json
import threading
import time
from typing import Optional

import jwt
from fastapi import Request, HTTPException

from ..config import JWT_SECRET, RBAC_PROD_URL, RBAC_JWT_ALGORITHMS, RBAC_JWT_ISSUER, RBAC_JWT_AUDIENCE, RBAC_KEY_CACHE_TTL, PLATFORM_JWT_SECRET, JWT_AUDIENCE_PLATFORM

# ── Request-scoped authenticated identity ────────────────────────────────────
_current_tenant = contextvars.ContextVar("current_tenant", default=None)
_current_role = contextvars.ContextVar("current_role", default=None)
_current_roles = contextvars.ContextVar("current_roles", default=())
_current_perms = contextvars.ContextVar("current_perms", default=())
_current_user = contextvars.ContextVar("current_user", default=None)

# Platform (ops) identity — only ever set on /api/admin/* requests. A platform
# request sets NO tenant, so require_tenant() still raises 401 and no tenant-scoped
# handler can be reached with an admin token.
_current_platform_user = contextvars.ContextVar("current_platform_user", default=None)
_current_platform_role = contextvars.ContextVar("current_platform_role", default=None)

# Set when a tenant token was minted by an ops impersonation. Carries who really
# acted, so audit trails and the write-gate can see through the assumed identity.
_current_actor = contextvars.ContextVar("current_actor", default=None)
_current_impersonation = contextvars.ContextVar("current_impersonation", default=None)

# Every route under here is platform-admin surface, authenticated by a different
# key and carrying no tenant at all.
PLATFORM_PREFIX = "/api/admin/"

# ── Public paths (no login required) ─────────────────────────────────────────
# Exact path matches. Everything not listed here requires a valid token.
PUBLIC_PATHS = {
    "/",
    "/health",
    "/metrics",                    # authenticated by METRICS_TOKEN; a scraper has no tenant JWT
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/login",
    "/api/auth/refresh",           # exchanges a refresh token — access token may be expired
    "/api/auth/logout",            # revokes a refresh token
    "/api/auth/forgot-password",
    "/api/auth/verify-otp",
    "/api/auth/reset-password",
    "/api/oauth/email/callback",   # OAuth callback — tenant carried in signed state
    "/api/auth/sso/authorize",     # starts SSO — no session exists yet, that's the point
    "/api/auth/sso/exchange",      # trades the single-use SSO code for a session
    "/api/bills/approve-link",     # email approval — its own APPROVAL_SECRET token
    "/api/tally/pair",             # pairing token, not a login token
    "/api/tally/ingest",           # authenticated by agent API key (Depends _require_agent)
    "/api/tally/agent/status",     # authenticated by agent API key (Depends _require_agent)
    "/agent/version",              # public agent update manifest
    "/agent/download",             # public agent installer (no secrets; pairing needs a token)
    "/api/admin/login",            # platform admin sign-in
    "/api/admin/auth/refresh",     # platform refresh — access token may be expired
    "/api/admin/auth/logout",
}


# Prefix matches, for public routes that carry their own credential in the path.
# The vendor portal is authenticated by a signed, expiring link token
# (domain/crypto/wallets.py); /api/public serves audit proof bundles and anchor
# roots, which are meant to be checked by anyone without a login.
PUBLIC_PREFIXES = (
    "/api/vendor-portal/",
    "/api/public/",
)


def _is_public(request: Request) -> bool:
    # CORS preflight carries no credentials — always allow it through.
    if request.method == "OPTIONS":
        return True
    path = request.url.path
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


# ── RBAC public-key fetch + cache ────────────────────────────────────────────
_key_lock = threading.Lock()
_key_cache: dict = {"fetched_at": 0.0, "jwk_client": None, "pem": None}


def _fetch_rbac_key(force: bool = False):
    """Populate the module key cache from RBAC_PROD_URL. Returns (jwk_client, pem).

    Auto-detects the response shape:
      * JWKS  -> {"keys": [...]}            -> a jwt.PyJWKClient (kid-aware)
      * PEM   -> "-----BEGIN PUBLIC KEY-----..."  (raw, or wrapped in a JSON field)
    """
    now = time.monotonic()
    fresh = (now - _key_cache["fetched_at"]) < RBAC_KEY_CACHE_TTL
    if not force and fresh and (_key_cache["jwk_client"] or _key_cache["pem"]):
        return _key_cache["jwk_client"], _key_cache["pem"]

    with _key_lock:
        # Re-check inside the lock in case another thread just refreshed.
        now = time.monotonic()
        fresh = (now - _key_cache["fetched_at"]) < RBAC_KEY_CACHE_TTL
        if not force and fresh and (_key_cache["jwk_client"] or _key_cache["pem"]):
            return _key_cache["jwk_client"], _key_cache["pem"]

        import httpx
        resp = httpx.get(RBAC_PROD_URL, timeout=10)
        resp.raise_for_status()
        body = resp.text.strip()

        jwk_client = None
        pem = None
        looks_json = body.startswith("{")
        if looks_json:
            data = json.loads(body)
            if isinstance(data, dict) and data.get("keys"):
                # JWKS — let PyJWKClient handle kid selection + its own caching.
                jwk_client = jwt.PyJWKClient(RBAC_PROD_URL)
            else:
                # JSON wrapper around a PEM, e.g. {"public_key": "-----BEGIN..."}
                for field in ("public_key", "publicKey", "key", "pem"):
                    if isinstance(data.get(field), str):
                        pem = data[field]
                        break
        elif "BEGIN" in body:
            pem = body

        if not jwk_client and not pem:
            raise HTTPException(status_code=503, detail="RBAC public key unavailable")

        _key_cache.update(fetched_at=time.monotonic(), jwk_client=jwk_client, pem=pem)
        return jwk_client, pem


def _decode_options() -> dict:
    return {
        "verify_aud": bool(RBAC_JWT_AUDIENCE),
        "verify_iss": bool(RBAC_JWT_ISSUER),
        "require": ["exp"],   # short-lived tokens must carry an expiry
    }


def _verify(token: str) -> dict:
    """Verify a JWT and return its claims. Raises 401 on any failure."""
    kwargs = {}
    if RBAC_JWT_AUDIENCE:
        kwargs["audience"] = RBAC_JWT_AUDIENCE
    if RBAC_JWT_ISSUER:
        kwargs["issuer"] = RBAC_JWT_ISSUER

    if RBAC_PROD_URL:
        # Verify against the RBAC identity service's public key.
        jwk_client, pem = _fetch_rbac_key()
        try:
            if jwk_client is not None:
                signing_key = jwk_client.get_signing_key_from_jwt(token).key
            else:
                signing_key = pem
            return jwt.decode(
                token, signing_key,
                algorithms=RBAC_JWT_ALGORITHMS,
                options=_decode_options(),
                **kwargs,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Session expired")
        except (jwt.InvalidTokenError, Exception) as exc:
            # A kid miss can mean the RBAC keys rotated — refetch once and retry.
            if jwk_client is not None:
                _fetch_rbac_key(force=True)
                try:
                    signing_key = _key_cache["jwk_client"].get_signing_key_from_jwt(token).key
                    return jwt.decode(
                        token, signing_key,
                        algorithms=RBAC_JWT_ALGORITHMS,
                        options=_decode_options(),
                        **kwargs,
                    )
                except jwt.ExpiredSignatureError:
                    raise HTTPException(status_code=401, detail="Session expired")
                except Exception:
                    pass
            raise HTTPException(status_code=401, detail="Invalid token")

    # Local / dev fallback: app-issued HS256 token.
    # `require: ["exp"]` matches the RBAC branch above. Without it a token that
    # simply omits the claim never expires — PyJWT only checks expiry when the
    # claim is present.
    try:
        return jwt.decode(
            token, JWT_SECRET, algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _extract_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.query_params.get("access_token")


def decode_token(request: Request) -> Optional[dict]:
    """Verified JWT claims from the Authorization header or ``access_token``
    query param, or None if no token was supplied. Raises 401 for a present but
    invalid/expired token."""
    token = _extract_token(request)
    if not token:
        return None
    return _verify(token)


# ── Claim extraction (defensive: works across claim-name variants) ───────────
def _claim_tenant(payload: dict) -> Optional[str]:
    val = payload.get("tenant_id") or payload.get("company_id") or payload.get("org_id")
    return str(val) if val else None


def _claim_roles(payload: dict):
    roles = payload.get("roles")
    if isinstance(roles, str):
        roles = [roles]
    if not roles:
        single = payload.get("role")
        roles = [single] if single else []
    return tuple(roles)


def _claim_perms(payload: dict):
    perms = payload.get("permissions") or payload.get("scopes") or payload.get("scope")
    if isinstance(perms, str):
        perms = perms.split()
    return tuple(perms or ())


def _verify_platform(token: str) -> dict:
    """Verify a platform-admin token. Raises 401 on any failure, 503 if unconfigured."""
    if not PLATFORM_JWT_SECRET:
        # Refuse rather than fall back to JWT_SECRET: a fallback would make every
        # tenant token a valid admin token.
        raise HTTPException(status_code=503, detail="Platform admin is not configured")
    try:
        claims = jwt.decode(
            token, PLATFORM_JWT_SECRET, algorithms=["HS256"],
            audience=JWT_AUDIENCE_PLATFORM,
            options={"require": ["exp", "aud"], "verify_aud": True},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if claims.get("typ") != "platform":
        raise HTTPException(status_code=401, detail="Invalid token")
    return claims


def _reset_identity():
    _current_tenant.set(None)
    _current_roles.set(())
    _current_role.set(None)
    _current_perms.set(())
    _current_user.set(None)
    _current_platform_user.set(None)
    _current_platform_role.set(None)
    _current_actor.set(None)
    _current_impersonation.set(None)


async def auth_context(request: Request):
    """App-level dependency: authenticate the request and pin the identity.

    Async so the context-var writes propagate into sync path operations (which
    FastAPI runs in a threadpool that copies the current context)."""
    # ── Platform admin surface ────────────────────────────────────────────────
    # Handled first and separately. Note what this branch does NOT do: it never
    # sets _current_tenant. So an admin token cannot satisfy require_tenant(), and
    # no tenant-scoped handler is reachable with one.
    if request.url.path.startswith(PLATFORM_PREFIX):
        _reset_identity()
        if _is_public(request):
            return
        token = _extract_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Authentication required")
        claims = _verify_platform(token)
        _current_platform_user.set(claims.get("sub"))
        _current_platform_role.set(claims.get("prole") or "readonly")
        _current_user.set(claims.get("sub"))
        return

    try:
        payload = decode_token(request)
    except HTTPException:
        # decode_token rejects a token that is present but unverifiable. On a
        # public path that is too strict: the path needs no identity, so a bad or
        # foreign Authorization header should read as "anonymous" rather than
        # reject the request. /metrics is the case that surfaced this — it carries
        # its own METRICS_TOKEN as a bearer, which is not a JWT and never will be —
        # but the same applies to a stale token left in the header while hitting
        # /api/login. Non-public paths still raise exactly as before.
        if not _is_public(request):
            raise
        _reset_identity()
        return
    # A platform token presented on a tenant route. It would already fail below —
    # it is signed with the wrong key, and carries no company_id — but reject it
    # explicitly so the failure is unambiguous rather than a confusing 401.
    if payload and payload.get("typ") == "platform":
        _reset_identity()
        raise HTTPException(status_code=401, detail="Invalid token")
    tenant = _claim_tenant(payload) if payload else None
    if payload and tenant:
        _current_tenant.set(tenant)
        _current_roles.set(_claim_roles(payload))
        _current_role.set(payload.get("role") or (_claim_roles(payload)[0] if _claim_roles(payload) else None))
        _current_perms.set(_claim_perms(payload))
        _current_user.set(payload.get("sub") or payload.get("username"))
        # Impersonation: a tenant-shaped token minted by ops. Everything above is
        # unchanged — that is the point, so all ~200 handlers work untouched — but
        # `act` records who really acted, which the write-gate and audit rows use.
        if payload.get("act"):
            _current_actor.set(payload["act"])
            _current_impersonation.set(payload.get("jti"))
        return
    # No valid identity on this request.
    _reset_identity()
    if not _is_public(request):
        raise HTTPException(status_code=401, detail="Authentication required")


# ── Accessors used by handlers ────────────────────────────────────────────────
def current_tenant() -> Optional[str]:
    """Authenticated tenant id, or None on a public/unauthenticated request."""
    return _current_tenant.get()


def require_tenant() -> str:
    """Authenticated tenant id; raises 401 if the request had no valid token.

    Use this everywhere a handler needs the tenant. Any client-supplied
    ``company_id`` must be ignored in favour of this value."""
    tenant = _current_tenant.get()
    if not tenant:
        raise HTTPException(status_code=401, detail="Authentication required")
    return tenant


def current_role() -> Optional[str]:
    return _current_role.get()


def current_roles() -> tuple:
    return _current_roles.get()


def current_permissions() -> tuple:
    return _current_perms.get()


def current_username() -> Optional[str]:
    return _current_user.get()


def has_permission(permission: str) -> bool:
    return permission in _current_perms.get()


def require_role(*allowed: str):
    """Raise 403 unless the caller holds one of ``allowed`` roles."""
    held = set(_current_roles.get())
    if held.isdisjoint(allowed):
        raise HTTPException(status_code=403, detail="Insufficient role")


# ── Platform (ops) accessors ─────────────────────────────────────────────────
def current_platform_user() -> Optional[str]:
    """Email of the authenticated platform admin, or None on a tenant request."""
    return _current_platform_user.get()


def current_platform_role() -> Optional[str]:
    return _current_platform_role.get()


def require_platform_admin() -> str:
    """Authenticated platform admin; 401 if the request had no valid admin token."""
    who = _current_platform_user.get()
    if not who:
        raise HTTPException(status_code=401, detail="Authentication required")
    return who


# Ordered least → most privileged. `readonly` can look; `support` can also
# impersonate; `admin` can change licences and tenants; `owner` manages admins.
_PLATFORM_ROLE_RANK = {"readonly": 0, "support": 1, "admin": 2, "owner": 3}


def require_platform_role(minimum: str):
    """Raise 403 unless the platform admin holds at least ``minimum``."""
    require_platform_admin()
    held = _PLATFORM_ROLE_RANK.get(_current_platform_role.get() or "readonly", 0)
    if held < _PLATFORM_ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail=f"Requires platform role: {minimum}")


# ── Impersonation accessors ──────────────────────────────────────────────────
def current_actor() -> Optional[dict]:
    """The real principal behind an impersonated tenant session, or None."""
    return _current_actor.get()


def current_impersonation() -> Optional[str]:
    """Impersonation session id (JWT jti) when this request is a support session."""
    return _current_impersonation.get()


def is_impersonating() -> bool:
    return _current_impersonation.get() is not None
