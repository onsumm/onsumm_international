"""
Microsoft Graph OAuth adapter — the Outlook counterpart to email_oauth's Gmail.

Mirrors the Gmail surface so the dispatcher in email_oauth can treat the two
interchangeably:
    build_authorize_url(state)    -> str
    exchange_code(code)           -> dict(access, refresh, expires_at, email, scopes)
    refresh_access_token(refresh) -> dict(access, expires_at)
    revoke(token)                 -> None

The app registration is multitenant ("Any Entra ID tenant + personal Microsoft
accounts"), so we always talk to the shared /common authority and never hold a
per-customer tenant id — a user at any company, or on outlook.com, lands here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .. import config

# ── Endpoints / scopes ───────────────────────────────────────────────────────
# /common accepts both work-school and personal accounts. /organizations would
# exclude outlook.com; a tenant GUID would pin us to one customer.
AUTHORITY  = "https://login.microsoftonline.com/common"
AUTH_URL   = f"{AUTHORITY}/oauth2/v2.0/authorize"
TOKEN_URL  = f"{AUTHORITY}/oauth2/v2.0/token"
GRAPH_ME   = "https://graph.microsoft.com/v1.0/me"

# offline_access is what yields a refresh token — without it every connection
# dies at the first access-token expiry (~1h) with no way back.
SCOPES = "offline_access User.Read Mail.Read Mail.Send"

# Exactly the five permissions Entra pre-classifies as low impact. An app that
# asks for only these, from a verified publisher, can be consented to by an
# ordinary user under the default enterprise policy — no admin involved.
LOGIN_ONLY_SCOPES = "openid profile email offline_access User.Read"


def _expires_at(expires_in: int | None) -> datetime:
    # 60s of slack so we refresh before Graph starts rejecting, matching Gmail's.
    return datetime.now(timezone.utc) + timedelta(seconds=(expires_in or 3600) - 60)


def _raise_for_oauth_error(resp: httpx.Response) -> None:
    """Surface Entra's error_description, which names the actual misconfiguration.

    Entra's bodies are far more diagnostic than the status code — AADSTS50011 is
    a redirect-URI mismatch, AADSTS7000215 a bad client secret, AADSTS65001 a
    missing consent. Swallowing them turns a two-minute fix into a guessing game.
    """
    if resp.status_code < 400:
        return
    detail = resp.text[:500]
    try:
        body = resp.json()
        detail = body.get("error_description") or body.get("error") or detail
    except Exception:
        pass
    raise RuntimeError(f"Microsoft OAuth {resp.status_code}: {detail}")


# ── Authorize / token exchange / refresh ─────────────────────────────────────

def build_authorize_url(state: str, login_only: bool = False) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": config.MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.MS_OAUTH_REDIRECT_URI,
        "response_mode": "query",
        "scope": LOGIN_ONLY_SCOPES if login_only else SCOPES,
        "state": state,
        # Always show the picker: users routinely have a work and a personal
        # account signed in, and silently reusing the wrong one connects the
        # wrong mailbox with no visible clue.
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    """Exchange an auth code for tokens + the connected mailbox address."""
    with httpx.Client(timeout=20) as client:
        resp = client.post(TOKEN_URL, data={
            "client_id": config.MS_CLIENT_ID,
            "client_secret": config.MS_CLIENT_SECRET,
            "code": code,
            "redirect_uri": config.MS_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
            "scope": SCOPES,
        })
        _raise_for_oauth_error(resp)
        tok = resp.json()

        email = ""
        upn = ""
        ms_user_id = ""
        try:
            info = client.get(GRAPH_ME, headers={"Authorization": f"Bearer {tok['access_token']}"})
            if info.status_code == 200:
                data = info.json()
                upn = data.get("userPrincipalName") or ""
                # Personal accounts often have a null `mail`; userPrincipalName
                # is the address there. Work accounts populate both.
                email = data.get("mail") or upn
                ms_user_id = data.get("id") or ""
        except Exception:
            pass

    # Entra has no equivalent of Google's verified_email: a tenant admin can set
    # a user's `mail` to any address, so this claim alone does not prove domain
    # ownership. Callers that use it for sign-in must gate on something else
    # (here: the address must already exist in the tenant's team member list).
    # upn / ms_user_id are recorded so a stricter domain check can be added later
    # without re-plumbing the flow.
    return {
        "access": tok["access_token"],
        "refresh": tok.get("refresh_token"),
        "expires_at": _expires_at(tok.get("expires_in")),
        "email": email,
        "email_verified": False,
        "upn": upn,
        "provider_user_id": ms_user_id,
        "scopes": tok.get("scope", ""),
    }


def refresh_access_token(refresh: str) -> dict[str, Any]:
    with httpx.Client(timeout=20) as client:
        resp = client.post(TOKEN_URL, data={
            "client_id": config.MS_CLIENT_ID,
            "client_secret": config.MS_CLIENT_SECRET,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
            "scope": SCOPES,
        })
        _raise_for_oauth_error(resp)
        tok = resp.json()
    out = {"access": tok["access_token"], "expires_at": _expires_at(tok.get("expires_in"))}
    # Entra rotates refresh tokens: the response usually carries a new one, and
    # the old one keeps working only for a short overlap. Hand it back so the
    # caller can persist it, or connections silently rot after ~90 days.
    if tok.get("refresh_token"):
        out["refresh"] = tok["refresh_token"]
    return out


def revoke(token: str) -> None:
    """No-op: Microsoft exposes no per-token revocation endpoint.

    Google's /revoke kills one grant. The Graph equivalent, /me/revokeSignInSessions,
    signs the user out of *everything* — far beyond disconnecting one mailbox — so
    we deliberately do nothing here and rely on deleting our stored tokens. Users
    can drop the grant themselves at https://myapps.microsoft.com.
    """
    return None
