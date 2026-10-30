"""The platform (ops) API: /api/admin/*.

This is the cross-tenant surface — the one place in the product where a request is
not scoped to a single tenant. Three things keep that safe:

  * A separate identity. Tokens here are signed with PLATFORM_JWT_SECRET and carry
    `typ: "platform"`; `auth_context` handles this prefix in its own branch and
    never pins a tenant, so `require_tenant()` still refuses. See auth.py.
  * A role ladder. readonly < support < admin < owner, enforced per endpoint by
    `require_platform_role`.
  * An audit row for every mutation, written in the same transaction as the change.

Onboarding is the reason this module exists: creating a client used to mean hand-
writing rows in psql.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import secrets
import uuid
from typing import Any, Optional

import bcrypt
import jwt
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..security.auth import current_platform_role, current_platform_user, require_platform_admin, require_platform_role
from ..config import IMPERSONATION_MAX_TTL_MIN, JWT_AUDIENCE_PLATFORM, JWT_SECRET, PLATFORM_JWT_SECRET, PLATFORM_REFRESH_TTL_DAYS, PLATFORM_TOKEN_TTL_MIN, get_conn, put_conn
from ..security.licensing import invalidate as invalidate_license
from ..security.services import SERVICES
from ..domain.tenant_defaults import apply_package, create_tenant_user, ensure_tenant, seed_core_services, seed_default_brand, seed_default_chain, username_taken

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Platform Admin"])

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


# ── Plumbing ──────────────────────────────────────────────────────────────────
class _Tx:
    """Borrow a pooled connection and run one explicit transaction."""

    def __enter__(self):
        self.conn = get_conn()
        self.conn.autocommit = False
        return self.conn

    def __exit__(self, exc_type, *_):
        try:
            if exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
        finally:
            put_conn(self.conn)
        return False


def _dc(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _audit(cur, action: str, target_type: str, target_id: str | None,
           tenant_id: str | None = None, before: Any = None, after: Any = None,
           request: Request | None = None) -> None:
    """Write one audit row. Called inside the same transaction as the change, so a
    rolled-back change can never leave a log entry claiming it happened."""
    cur.execute(
        """
        INSERT INTO admin_audit_log
            (actor_platform_user_id, actor_email, action, target_type, target_id,
             tenant_id, before, after, ip, user_agent)
        VALUES (
            (SELECT id FROM platform_users WHERE lower(email) = lower(%s)),
            %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        """,
        (
            current_platform_user(), current_platform_user() or "system",
            action, target_type, str(target_id) if target_id else None,
            tenant_id,
            psycopg2.extras.Json(before) if before is not None else None,
            psycopg2.extras.Json(after) if after is not None else None,
            (request.client.host if request and request.client else None),
            (request.headers.get("user-agent") if request else None),
        ),
    )


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_platform_token(email: str, name: str, role: str) -> tuple[str, dict]:
    if not PLATFORM_JWT_SECRET:
        raise HTTPException(status_code=503, detail="Platform admin is not configured")
    now = datetime.datetime.utcnow()
    payload = {
        "sub": email,
        "name": name,
        "prole": role,
        "typ": "platform",
        "aud": JWT_AUDIENCE_PLATFORM,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=PLATFORM_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, PLATFORM_JWT_SECRET, algorithm="HS256"), payload


# ── Auth ──────────────────────────────────────────────────────────────────────
class AdminLogin(BaseModel):
    email: str
    password: str


@router.post("/login")
def admin_login(body: AdminLogin, request: Request):
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT id, email, name, role, password_hash, is_active,
                          failed_logins, locked_until
                     FROM platform_users WHERE lower(email) = lower(%s)""",
                (body.email,),
            )
            row = cur.fetchone()
            # Same generic message whichever check fails, so the endpoint does not
            # confirm which ops emails exist.
            invalid = HTTPException(status_code=401, detail="Invalid email or password")
            if not row or not row["is_active"]:
                raise invalid
            if row["locked_until"] and row["locked_until"] > datetime.datetime.now(
                    row["locked_until"].tzinfo):
                raise HTTPException(status_code=423, detail="Account temporarily locked")

            if not bcrypt.checkpw(body.password.encode(), row["password_hash"].encode()):
                failed = (row["failed_logins"] or 0) + 1
                cur.execute(
                    """UPDATE platform_users
                          SET failed_logins = %s,
                              locked_until = CASE WHEN %s >= %s
                                                  THEN now() + (%s || ' minutes')::interval
                                                  ELSE locked_until END
                        WHERE id = %s""",
                    (failed, failed, MAX_FAILED_LOGINS, str(LOCKOUT_MINUTES), row["id"]),
                )
                raise invalid

            token, payload = _issue_platform_token(row["email"], row["name"], row["role"])
            refresh = secrets.token_urlsafe(48)
            cur.execute(
                """INSERT INTO platform_refresh_tokens
                       (token_hash, platform_user_id, expires_at)
                   VALUES (%s, %s, now() + (%s || ' days')::interval)""",
                (_hash(refresh), row["id"], str(PLATFORM_REFRESH_TTL_DAYS)),
            )
            cur.execute(
                """UPDATE platform_users
                      SET last_login_at = now(), failed_logins = 0, locked_until = NULL
                    WHERE id = %s""",
                (row["id"],),
            )
    return {
        "token": token,
        "refresh_token": refresh,
        "expires_in": PLATFORM_TOKEN_TTL_MIN * 60,
        "admin": {"email": row["email"], "name": row["name"], "role": row["role"]},
    }


class AdminRefresh(BaseModel):
    refresh_token: str


@router.post("/auth/refresh")
def admin_refresh(body: AdminRefresh):
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT r.id, r.revoked, r.expires_at,
                          u.id AS uid, u.email, u.name, u.role, u.is_active
                     FROM platform_refresh_tokens r
                     JOIN platform_users u ON u.id = r.platform_user_id
                    WHERE r.token_hash = %s""",
                (_hash(body.refresh_token),),
            )
            row = cur.fetchone()
            if not row or not row["is_active"]:
                raise HTTPException(status_code=401, detail="Invalid refresh token")
            if row["revoked"]:
                # Presenting an already-rotated token means it leaked; kill the
                # whole family rather than just refusing this one.
                cur.execute(
                    "UPDATE platform_refresh_tokens SET revoked = true, revoked_at = now() "
                    "WHERE platform_user_id = %s AND NOT revoked",
                    (row["uid"],),
                )
                raise HTTPException(status_code=401, detail="Refresh token reuse detected")
            if row["expires_at"] <= datetime.datetime.now(row["expires_at"].tzinfo):
                raise HTTPException(status_code=401, detail="Refresh token expired")

            new_raw = secrets.token_urlsafe(48)
            cur.execute(
                """UPDATE platform_refresh_tokens
                      SET revoked = true, revoked_at = now(), replaced_by = %s
                    WHERE id = %s""",
                (_hash(new_raw), row["id"]),
            )
            cur.execute(
                """INSERT INTO platform_refresh_tokens
                       (token_hash, platform_user_id, expires_at)
                   VALUES (%s, %s, now() + (%s || ' days')::interval)""",
                (_hash(new_raw), row["uid"], str(PLATFORM_REFRESH_TTL_DAYS)),
            )
            token, _ = _issue_platform_token(row["email"], row["name"], row["role"])
    return {
        "token": token,
        "refresh_token": new_raw,
        "expires_in": PLATFORM_TOKEN_TTL_MIN * 60,
        "admin": {"email": row["email"], "name": row["name"], "role": row["role"]},
    }


@router.post("/auth/logout")
def admin_logout(body: AdminRefresh):
    with _Tx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE platform_refresh_tokens SET revoked = true, revoked_at = now()
                    WHERE token_hash = %s AND NOT revoked""",
                (_hash(body.refresh_token),),
            )
            # Signing out ends any live support sessions this admin opened.
            cur.execute(
                """UPDATE platform_impersonation SET revoked_at = now()
                    WHERE platform_user_id = (
                        SELECT platform_user_id FROM platform_refresh_tokens
                         WHERE token_hash = %s)
                      AND revoked_at IS NULL""",
                (_hash(body.refresh_token),),
            )
    return {"ok": True}


@router.get("/me")
def admin_me():
    return {"email": require_platform_admin(), "role": current_platform_role()}


# ── Service catalog ───────────────────────────────────────────────────────────
@router.get("/services")
def list_services():
    require_platform_admin()
    return {"services": [
        {
            "key": s.key, "display_name": s.display_name, "description": s.description,
            "category": s.category, "is_core": s.is_core,
            "default_enabled": s.default_enabled, "metered_unit": s.metered_unit,
        }
        for s in SERVICES
    ]}


@router.get("/packages")
def list_packages():
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT p.key, p.name, p.description,
                          array_agg(i.service_key ORDER BY i.service_key)
                              FILTER (WHERE i.enabled) AS services
                     FROM service_package p
                     LEFT JOIN service_package_item i ON i.package_id = p.package_id
                    WHERE p.is_active
                    GROUP BY p.key, p.name, p.description, p.sort_order
                    ORDER BY p.sort_order""",
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"packages": rows}


# ── Tenants ───────────────────────────────────────────────────────────────────
@router.get("/tenants")
def list_tenants(q: str | None = Query(None), service: str | None = Query(None)):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """
                SELECT t.tenant_id, t.company_name, t.legal_name, t.gstn, t.plan_tier,
                       t.package_key, t.is_active, t.pipeline_enabled, t.created_at,
                       t.onboarded_at, t.primary_contact_email,
                       (SELECT count(*) FROM users u WHERE u.company_id = t.tenant_id) AS user_count,
                       (SELECT count(*) FROM tenant_service ts
                         WHERE ts.tenant_id = t.tenant_id AND ts.enabled) AS service_count
                  FROM dim_tenants t
                 WHERE (%s IS NULL OR t.company_name ILIKE '%%' || %s || '%%')
                   AND (%s IS NULL OR EXISTS (
                          SELECT 1 FROM tenant_service ts
                           WHERE ts.tenant_id = t.tenant_id
                             AND ts.service_key = %s AND ts.enabled))
                 ORDER BY t.company_name
                """,
                (q, q, service, service),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"tenants": rows}


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: str):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT * FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
            tenant = cur.fetchone()
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")
            cur.execute(
                """SELECT c.service_key, c.display_name, c.category, c.is_core,
                          c.metered_unit,
                          COALESCE(ts.enabled, c.default_enabled) AS enabled,
                          COALESCE(ts.limits, '{}'::jsonb) AS limits,
                          ts.notes, ts.updated_at
                     FROM service_catalog c
                     LEFT JOIN tenant_service ts
                            ON ts.service_key = c.service_key AND ts.tenant_id = %s
                    WHERE c.retired_at IS NULL
                    ORDER BY c.sort_order""",
                (tenant_id,),
            )
            services = [dict(r) for r in cur.fetchall()]
    return {"tenant": dict(tenant), "services": services}


class TenantPatch(BaseModel):
    company_name: Optional[str] = None
    legal_name: Optional[str] = None
    gstn: Optional[str] = None
    timezone: Optional[str] = None
    region: Optional[str] = None
    is_active: Optional[bool] = None
    pipeline_enabled: Optional[bool] = None
    primary_contact_email: Optional[str] = None


@router.patch("/tenants/{tenant_id}")
def patch_tenant(tenant_id: str, body: TenantPatch, request: Request):
    require_platform_role("admin")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT * FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
            before = cur.fetchone()
            if not before:
                raise HTTPException(status_code=404, detail="Tenant not found")
            sets = ", ".join(f"{k} = %s" for k in fields)
            cur.execute(
                f"UPDATE dim_tenants SET {sets} WHERE tenant_id = %s RETURNING *",
                (*fields.values(), tenant_id),
            )
            after = cur.fetchone()
            _audit(cur, "tenant.update", "tenant", tenant_id, tenant_id,
                   {k: str(before[k]) for k in fields},
                   {k: str(after[k]) for k in fields}, request)
    return {"tenant": dict(after)}


# ── Licensing ─────────────────────────────────────────────────────────────────
class ServiceToggle(BaseModel):
    service_key: str
    enabled: bool
    limits: dict = {}
    notes: Optional[str] = None


class LicensingUpdate(BaseModel):
    changes: list[ServiceToggle]


@router.put("/tenants/{tenant_id}/services")
def set_tenant_services(tenant_id: str, body: LicensingUpdate, request: Request,
                        force: bool = Query(False)):
    """Apply licensing changes. One transaction, one audit row per service."""
    require_platform_role("admin")
    known = {s.key: s for s in SERVICES}
    unknown = [c.service_key for c in body.changes if c.service_key not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown services: {unknown}")

    # Revoking a core service breaks the product for that tenant. Allowed, because
    # a non-paying tenant is a real case, but never by accident.
    core_off = [c.service_key for c in body.changes
                if not c.enabled and known[c.service_key].is_core]
    if core_off and not force:
        raise HTTPException(
            status_code=409,
            detail={"error": "core_service_disable",
                    "services": core_off,
                    "message": "These are core services. Re-send with ?force=true to disable."},
        )

    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT 1 FROM dim_tenants WHERE tenant_id = %s", (tenant_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Tenant not found")
            for change in body.changes:
                cur.execute(
                    "SELECT enabled, limits FROM tenant_service "
                    "WHERE tenant_id = %s AND service_key = %s",
                    (tenant_id, change.service_key),
                )
                prev = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO tenant_service
                        (tenant_id, service_key, enabled, limits, notes,
                         enabled_at, disabled_at, updated_by, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s,
                            CASE WHEN %s THEN now() END,
                            CASE WHEN NOT %s THEN now() END,
                            (SELECT id FROM platform_users WHERE lower(email) = lower(%s)),
                            now())
                    ON CONFLICT (tenant_id, service_key) DO UPDATE SET
                        enabled     = EXCLUDED.enabled,
                        limits      = EXCLUDED.limits,
                        notes       = EXCLUDED.notes,
                        enabled_at  = COALESCE(EXCLUDED.enabled_at, tenant_service.enabled_at),
                        disabled_at = COALESCE(EXCLUDED.disabled_at, tenant_service.disabled_at),
                        updated_by  = EXCLUDED.updated_by,
                        updated_at  = now()
                    """,
                    (tenant_id, change.service_key, change.enabled,
                     psycopg2.extras.Json(change.limits), change.notes,
                     change.enabled, change.enabled, current_platform_user()),
                )
                _audit(cur,
                       "license.enable" if change.enabled else "license.disable",
                       "service", change.service_key, tenant_id,
                       dict(prev) if prev else None,
                       {"enabled": change.enabled, "limits": change.limits},
                       request)

    # Bust the cache so the change is visible on the next request rather than up to
    # LICENSE_CACHE_TTL later. Note this only clears *this* worker; see
    # docs/LICENSING_AND_ADMIN.md for the cross-worker caveat.
    invalidate_license(tenant_id)
    return {"ok": True, "changed": len(body.changes)}


class ApplyPackage(BaseModel):
    package_key: str


@router.post("/tenants/{tenant_id}/apply-package")
def post_apply_package(tenant_id: str, body: ApplyPackage, request: Request):
    require_platform_role("admin")
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT 1 FROM service_package WHERE key = %s", (body.package_key,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Package not found")
            count = apply_package(cur, tenant_id, body.package_key)
            cur.execute("UPDATE dim_tenants SET package_key = %s WHERE tenant_id = %s",
                        (body.package_key, tenant_id))
            _audit(cur, "license.apply_package", "tenant", tenant_id, tenant_id,
                   None, {"package": body.package_key, "services": count}, request)
    invalidate_license(tenant_id)
    return {"ok": True, "services": count}


# ── Usage ─────────────────────────────────────────────────────────────────────
@router.get("/tenants/{tenant_id}/usage")
def tenant_usage(tenant_id: str, months: int = Query(3, ge=1, le=24)):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """
                SELECT u.service_key, u.metric, u.period_month, u.used,
                       COALESCE(ts.limits, '{}'::jsonb) AS limits
                  FROM tenant_usage u
                  LEFT JOIN tenant_service ts
                         ON ts.tenant_id = u.tenant_id AND ts.service_key = u.service_key
                 WHERE u.tenant_id = %s
                   AND u.period_month >= date_trunc('month', now()) - (%s || ' months')::interval
                 ORDER BY u.period_month DESC, u.service_key
                """,
                (tenant_id, str(months)),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"usage": rows}


# ── Tenant users ──────────────────────────────────────────────────────────────
@router.get("/tenants/{tenant_id}/users")
def tenant_users(tenant_id: str):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT username, name, role, email, department
                     FROM users WHERE company_id = %s ORDER BY name""",
                (tenant_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"users": rows}


# ── Onboarding ────────────────────────────────────────────────────────────────
class OnboardAdmin(BaseModel):
    username: str
    password: str
    name: str
    email: str


class OnboardRequest(BaseModel):
    company_name: str
    legal_name: Optional[str] = None
    gstn: Optional[str] = None
    timezone: str = "Asia/Kolkata"
    region: str = "ap-south-1"
    primary_contact_email: Optional[str] = None
    package_key: Optional[str] = None
    admin: OnboardAdmin
    services_override: dict[str, bool] = {}


@router.post("/onboarding/validate")
def validate_onboarding(body: OnboardRequest):
    """Dry-run the collision checks so the wizard can show them before provisioning.

    Worth its own endpoint because users.username is globally unique — a name like
    "admin" is very likely already taken by another tenant, and finding that out
    only when the transaction aborts is a poor experience.
    """
    require_platform_role("admin")
    problems: list[dict] = []
    with _Tx() as conn:
        with _dc(conn) as cur:
            if username_taken(cur, body.admin.username):
                problems.append({
                    "field": "admin.username",
                    "message": f"Username '{body.admin.username}' is already taken. "
                               "Usernames are unique across all companies — try an "
                               "email address or a company prefix.",
                })
            if body.gstn:
                cur.execute("SELECT company_name FROM dim_tenants WHERE gstn = %s", (body.gstn,))
                clash = cur.fetchone()
                if clash:
                    problems.append({
                        "field": "gstn",
                        "message": f"GSTIN already registered to {clash['company_name']}. "
                                   "The tenant will be created without a GSTIN.",
                        "severity": "warning",
                    })
            if body.package_key:
                cur.execute("SELECT 1 FROM service_package WHERE key = %s", (body.package_key,))
                if not cur.fetchone():
                    problems.append({"field": "package_key",
                                     "message": f"Unknown package '{body.package_key}'"})
    blocking = [p for p in problems if p.get("severity") != "warning"]
    return {"ok": not blocking, "problems": problems}


@router.post("/onboarding/provision")
def provision_tenant(body: OnboardRequest, request: Request):
    """Create a working tenant in one transaction.

    All-or-nothing on purpose: a half-provisioned tenant — a company row with no
    admin user, or an admin who cannot approve anything because no chain exists —
    is worse than a clean failure, and is exactly what manual psql onboarding kept
    producing.
    """
    require_platform_role("admin")
    tenant_id = str(uuid.uuid4())
    with _Tx() as conn:
        with _dc(conn) as cur:
            if username_taken(cur, body.admin.username):
                raise HTTPException(
                    status_code=409,
                    detail={"error": "username_taken", "field": "admin.username",
                            "message": f"Username '{body.admin.username}' is already in use."},
                )

            ensure_tenant(
                cur, tenant_id, body.company_name, body.gstn,
                timezone=body.timezone, region=body.region,
                legal_name=body.legal_name,
                primary_contact_email=body.primary_contact_email,
                package_key=body.package_key,
            )
            seed_default_brand(cur, tenant_id, body.legal_name or body.company_name)
            seed_default_chain(cur, tenant_id)
            create_tenant_user(
                cur, body.admin.username, body.admin.password, "admin",
                body.admin.name, body.admin.email, tenant_id,
            )

            if body.package_key:
                apply_package(cur, tenant_id, body.package_key)
            else:
                seed_core_services(cur, tenant_id)

            for key, enabled in body.services_override.items():
                if key not in {s.key for s in SERVICES}:
                    raise HTTPException(status_code=400, detail=f"Unknown service: {key}")
                cur.execute(
                    """INSERT INTO tenant_service
                           (tenant_id, service_key, enabled, enabled_at, notes)
                       VALUES (%s, %s, %s, CASE WHEN %s THEN now() END, 'onboarding: override')
                       ON CONFLICT (tenant_id, service_key) DO UPDATE
                          SET enabled = EXCLUDED.enabled, updated_at = now()""",
                    (tenant_id, key, enabled, enabled),
                )

            _audit(cur, "tenant.create", "tenant", tenant_id, tenant_id, None,
                   {"company_name": body.company_name, "package": body.package_key,
                    "admin_username": body.admin.username}, request)

    invalidate_license(tenant_id)
    return {"tenant_id": tenant_id, "admin_username": body.admin.username}


# ── Impersonation ─────────────────────────────────────────────────────────────
class ImpersonateRequest(BaseModel):
    reason: str
    ttl_minutes: int = 15
    assumed_role: str = "auditor"


@router.post("/tenants/{tenant_id}/impersonate")
def impersonate(tenant_id: str, body: ImpersonateRequest, request: Request):
    """Mint a short-lived, read-only tenant token for support.

    The token is tenant-shaped and signed with the *tenant* secret, which is what
    lets every existing endpoint work unmodified. What marks it is the `act` claim:
    `auth_context` reads it, and `impersonation_gate` refuses every mutation.
    """
    require_platform_role("support")
    if len(body.reason.strip()) < 10:
        raise HTTPException(status_code=400,
                            detail="A reason of at least 10 characters is required")
    ttl = max(1, min(body.ttl_minutes, IMPERSONATION_MAX_TTL_MIN))

    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                "SELECT company_name, settings_jsonb FROM dim_tenants WHERE tenant_id = %s",
                (tenant_id,),
            )
            tenant = cur.fetchone()
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")
            # A customer can switch support access off entirely.
            if (tenant["settings_jsonb"] or {}).get("impersonation_opt_out"):
                raise HTTPException(
                    status_code=403,
                    detail="This customer has disabled support access to their account",
                )

            cur.execute(
                """INSERT INTO platform_impersonation
                       (platform_user_id, tenant_id, assumed_role, reason, read_only, expires_at)
                   VALUES ((SELECT id FROM platform_users WHERE lower(email) = lower(%s)),
                           %s, %s, %s, true, now() + (%s || ' minutes')::interval)
                   RETURNING session_id, expires_at""",
                (current_platform_user(), tenant_id, body.assumed_role,
                 body.reason.strip(), str(ttl)),
            )
            session = cur.fetchone()
            _audit(cur, "impersonation.start", "tenant", tenant_id, tenant_id, None,
                   {"reason": body.reason.strip(), "ttl_minutes": ttl,
                    "session_id": str(session["session_id"])}, request)

    actor = current_platform_user()
    now = datetime.datetime.utcnow()
    token = jwt.encode(
        {
            # sub is the ops email, never a real tenant user: support acts as
            # itself, so every audit trail records who was actually there.
            "sub": actor,
            "role": body.assumed_role,
            "name": f"{actor} (Onsumm Support)",
            "email": actor,
            "company_id": tenant_id,
            "company_name": tenant["company_name"],
            "department": None,
            "imp": True,
            "jti": str(session["session_id"]),
            "act": {"sub": actor, "sid": str(session["session_id"])},
            "iat": now,
            "exp": now + datetime.timedelta(minutes=ttl),
        },
        JWT_SECRET, algorithm="HS256",
    )
    # No refresh token is issued: the session dies at exp and cannot be extended.
    return {
        "token": token,
        "expires_at": session["expires_at"].isoformat(),
        "impersonation": {
            "id": str(session["session_id"]),
            "tenant_id": tenant_id,
            "tenant_name": tenant["company_name"],
            "read_only": True,
            "actor": actor,
        },
    }


@router.post("/impersonations/{session_id}/revoke")
def revoke_impersonation(session_id: str, request: Request):
    require_platform_role("support")
    with _Tx() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE platform_impersonation SET revoked_at = now() "
                "WHERE session_id = %s AND revoked_at IS NULL",
                (session_id,),
            )
            _audit(cur, "impersonation.revoke", "impersonation", session_id,
                   None, None, None, request)
    return {"ok": True}


@router.get("/impersonations")
def list_impersonations(active: bool = Query(True)):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT i.session_id, i.tenant_id, t.company_name, i.reason,
                          i.assumed_role, i.read_only, i.issued_at, i.expires_at,
                          i.revoked_at, u.email AS actor
                     FROM platform_impersonation i
                     JOIN platform_users u ON u.id = i.platform_user_id
                     LEFT JOIN dim_tenants t ON t.tenant_id = i.tenant_id
                    WHERE (NOT %s) OR (i.revoked_at IS NULL AND i.expires_at > now())
                    ORDER BY i.issued_at DESC LIMIT 200""",
                (active,),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"impersonations": rows}


# ── Platform users ────────────────────────────────────────────────────────────
class PlatformUserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: str = "support"


@router.get("/platform-users")
def list_platform_users():
    require_platform_role("admin")
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """SELECT id, email, name, role, is_active, last_login_at, created_at
                     FROM platform_users ORDER BY email""",
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"platform_users": rows}


@router.post("/platform-users")
def create_platform_user(body: PlatformUserCreate, request: Request):
    require_platform_role("owner")
    if body.role not in ("owner", "admin", "support", "readonly"):
        raise HTTPException(status_code=400, detail="Invalid role")
    if len(body.password) < 12:
        raise HTTPException(status_code=400,
                            detail="Password must be at least 12 characters")
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT 1 FROM platform_users WHERE lower(email) = lower(%s)",
                        (body.email,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Email already registered")
            hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
            cur.execute(
                """INSERT INTO platform_users (email, password_hash, name, role, created_by)
                   VALUES (%s, %s, %s, %s,
                           (SELECT id FROM platform_users WHERE lower(email) = lower(%s)))
                   RETURNING id, email, name, role""",
                (body.email, hashed, body.name, body.role, current_platform_user()),
            )
            created = cur.fetchone()
            _audit(cur, "platform_user.create", "platform_user", str(created["id"]),
                   None, None, {"email": body.email, "role": body.role}, request)
    return {"platform_user": dict(created)}


class PlatformUserPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/platform-users/{user_id}")
def patch_platform_user(user_id: str, body: PlatformUserPatch, request: Request):
    require_platform_role("owner")
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute("SELECT id, email, role, is_active FROM platform_users WHERE id = %s",
                        (user_id,))
            before = cur.fetchone()
            if not before:
                raise HTTPException(status_code=404, detail="Not found")
            # No self-demotion and no self-deactivation: locking the last owner out
            # of the admin panel is only recoverable in psql.
            if before["email"].lower() == (current_platform_user() or "").lower():
                raise HTTPException(status_code=400,
                                    detail="You cannot change your own role or status")
            fields = {k: v for k, v in body.model_dump().items() if v is not None}
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            sets = ", ".join(f"{k} = %s" for k in fields)
            cur.execute(
                f"UPDATE platform_users SET {sets}, updated_at = now() WHERE id = %s "
                "RETURNING id, email, name, role, is_active",
                (*fields.values(), user_id),
            )
            after = cur.fetchone()
            _audit(cur, "platform_user.update", "platform_user", user_id, None,
                   dict(before), dict(after), request)
    return {"platform_user": dict(after)}


# ── Audit ─────────────────────────────────────────────────────────────────────
@router.get("/audit")
def list_audit(tenant_id: str | None = Query(None), action: str | None = Query(None),
               actor: str | None = Query(None), limit: int = Query(100, ge=1, le=500),
               before_id: int | None = Query(None)):
    require_platform_admin()
    with _Tx() as conn:
        with _dc(conn) as cur:
            cur.execute(
                """
                SELECT a.id, a.actor_email, a.action, a.target_type, a.target_id,
                       a.tenant_id, t.company_name, a.before, a.after,
                       a.impersonation_id, a.ip, a.created_at
                  FROM admin_audit_log a
                  LEFT JOIN dim_tenants t ON t.tenant_id = a.tenant_id
                 WHERE (%s IS NULL OR a.tenant_id = %s::uuid)
                   AND (%s IS NULL OR a.action = %s)
                   AND (%s IS NULL OR a.actor_email ILIKE '%%' || %s || '%%')
                   AND (%s IS NULL OR a.id < %s)
                 ORDER BY a.id DESC
                 LIMIT %s
                """,
                (tenant_id, tenant_id, action, action, actor, actor,
                 before_id, before_id, limit),
            )
            rows = [dict(r) for r in cur.fetchall()]
    return {"entries": rows, "next_before_id": rows[-1]["id"] if rows else None}
