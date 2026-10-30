"""The platform-admin identity must never cross with a tenant identity.

Two directions have to hold, and both are load-bearing:

  * A **tenant** token must not authenticate against /api/admin/*, or any logged-in
    customer could read and change every other customer's licensing.
  * A **platform** token must not satisfy require_tenant(), or an ops token would
    silently act inside whichever tenant it named.

There are three independent reasons each direction fails (different signing key,
the `typ` claim, and the absence of a company_id claim). These tests pin all of
them, so removing one by accident during a refactor still fails loudly here.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

TENANT = "d0e00000-0000-0000-0000-000000000001"


def _mods():
    try:
        import jwt
        from app.security import auth as auth_mod
        from app import config as config_mod
    except Exception as exc:                    # noqa: BLE001
        pytest.skip(f"backend not importable here: {exc}")
    return jwt, auth_mod, config_mod


def _request(path: str, token: str | None):
    from starlette.requests import Request

    headers = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({
        "type": "http", "http_version": "1.1", "method": "GET",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers, "scheme": "http", "server": ("test", 80),
        "client": ("test", 1), "root_path": "",
    })


def _tenant_token(jwt, config_mod, **extra):
    now = datetime.datetime.utcnow()
    payload = {
        "sub": "admin", "role": "admin", "company_id": TENANT,
        "iat": now, "exp": now + datetime.timedelta(minutes=10),
    }
    payload.update(extra)
    return jwt.encode(payload, config_mod.JWT_SECRET, algorithm="HS256")


def _platform_token(jwt, secret, **extra):
    now = datetime.datetime.utcnow()
    payload = {
        "sub": "ops@onsumm.ai", "typ": "platform", "prole": "owner",
        "aud": "onsumm-platform",
        "iat": now, "exp": now + datetime.timedelta(minutes=10),
    }
    payload.update(extra)
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_tenant_token_is_rejected_on_the_admin_surface(monkeypatch):
    jwt, auth_mod, config_mod = _mods()
    monkeypatch.setattr(auth_mod, "PLATFORM_JWT_SECRET", "a-different-platform-secret")
    from fastapi import HTTPException

    token = _tenant_token(jwt, config_mod)
    with pytest.raises(HTTPException) as exc:
        await auth_mod.auth_context(_request("/api/admin/tenants", token))
    assert exc.value.status_code == 401
    assert auth_mod.current_platform_user() is None


@pytest.mark.asyncio
async def test_platform_token_does_not_satisfy_require_tenant(monkeypatch):
    """Even naming a tenant in the token must not produce a tenant context."""
    jwt, auth_mod, config_mod = _mods()
    secret = "a-different-platform-secret"
    monkeypatch.setattr(auth_mod, "PLATFORM_JWT_SECRET", secret)
    from fastapi import HTTPException

    # A platform token that also carries company_id — the greedy case.
    token = _platform_token(jwt, secret, company_id=TENANT)
    with pytest.raises(HTTPException) as exc:
        await auth_mod.auth_context(_request("/api/bills", token))
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException):
        auth_mod.require_tenant()


@pytest.mark.asyncio
async def test_platform_request_pins_no_tenant(monkeypatch):
    jwt, auth_mod, _ = _mods()
    secret = "a-different-platform-secret"
    monkeypatch.setattr(auth_mod, "PLATFORM_JWT_SECRET", secret)

    await auth_mod.auth_context(_request("/api/admin/tenants", _platform_token(jwt, secret)))
    assert auth_mod.current_platform_user() == "ops@onsumm.ai"
    assert auth_mod.current_platform_role() == "owner"
    # The whole point: no tenant is pinned, so require_tenant() still refuses.
    assert auth_mod.current_tenant() is None
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        auth_mod.require_tenant()


@pytest.mark.asyncio
async def test_admin_surface_503s_when_unconfigured(monkeypatch):
    """An unset PLATFORM_JWT_SECRET must refuse, never fall back to JWT_SECRET."""
    jwt, auth_mod, config_mod = _mods()
    monkeypatch.setattr(auth_mod, "PLATFORM_JWT_SECRET", "")
    from fastapi import HTTPException

    token = _platform_token(jwt, config_mod.JWT_SECRET)
    with pytest.raises(HTTPException) as exc:
        await auth_mod.auth_context(_request("/api/admin/tenants", token))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_token_without_exp_is_rejected(monkeypatch):
    """PyJWT only enforces expiry when the claim is present, so it must be required."""
    jwt, auth_mod, config_mod = _mods()
    from fastapi import HTTPException

    forever = jwt.encode(
        {"sub": "admin", "role": "admin", "company_id": TENANT},
        config_mod.JWT_SECRET, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        await auth_mod.auth_context(_request("/api/bills", forever))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_impersonation_claims_are_exposed(monkeypatch):
    """An impersonated session looks like a normal tenant session to handlers —
    that is why ~200 endpoints need no changes — but `act` records who really acted."""
    jwt, auth_mod, config_mod = _mods()

    token = _tenant_token(
        jwt, config_mod,
        jti="11111111-1111-1111-1111-111111111111",
        act={"sub": "ops@onsumm.ai", "pid": "p1", "sid": "s1"},
    )
    await auth_mod.auth_context(_request("/api/bills", token))
    assert auth_mod.current_tenant() == TENANT          # handlers see a normal tenant
    assert auth_mod.is_impersonating() is True
    assert auth_mod.current_actor()["sub"] == "ops@onsumm.ai"
