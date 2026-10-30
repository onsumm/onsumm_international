"""
Single sign-on — "Continue with Google / Microsoft" on the login screen.

One consent covers both things we need from the user: it proves who they are
*and* it hands us the mailbox tokens, so an SSO user never has to connect their
mailbox as a second step.

Identity rule (deliberate, see docs): we never create accounts. The address the
provider returns must already exist as a team member (``users.email``) in this
deployment, and must match exactly one of them. An address nobody has been
invited with cannot sign in, so the team member list in Settings is the
gate — adding someone there is what grants SSO access.

Password login is untouched. A user who has never used SSO keeps the existing
flow, and signing in with a password never disturbs a mailbox they already
connected: mailbox rows are keyed (tenant, username, provider) and this module
only ever upserts, never deletes.

Token hand-off: the callback is a browser redirect, so it cannot return JSON.
Rather than putting a session token in the URL (where it lands in history, logs
and Referer headers), the callback mints a single-use code, and the SPA trades
it for the real tokens over POST.
"""
from __future__ import annotations

import datetime
import hashlib
import secrets
import traceback

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import config
from ..config import get_conn, put_conn
from ..core.tokens import _build_access_token, _issue_refresh_token
from ..integrations import email_oauth

router = APIRouter(tags=["SSO"])

SUPPORTED_PROVIDERS = ("google", "microsoft")

# Short: the SPA redeems it immediately on page load. Long enough to survive a
# slow redirect chain, far too short to be worth capturing from history.
_SSO_CODE_TTL_SECONDS = 120


# ── Table ────────────────────────────────────────────────────────────────────

def ensure_sso_tables():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auth_sso_codes (
                id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
                code_hash   text        NOT NULL UNIQUE,
                username    text        NOT NULL,
                company_id  uuid        NOT NULL,
                provider    text        NOT NULL,
                expires_at  timestamptz NOT NULL,
                used_at     timestamptz,
                created_at  timestamptz NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_sso_codes_expiry ON auth_sso_codes (expires_at);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] auth_sso_codes table ready")
    except Exception:
        print("[startup] ERROR creating auth_sso_codes table:")
        traceback.print_exc()


def _hash_code(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Identity resolution ──────────────────────────────────────────────────────

class SsoLoginError(Exception):
    """Carries the reason code the callback puts in the redirect URL."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def resolve_sso_user(cur, email: str) -> dict:
    """Map a provider-supplied address to exactly one team member.

    Raises SsoLoginError with a reason the login page can render.
    """
    addr = (email or "").strip().lower()
    if not addr:
        raise SsoLoginError("no_email")

    # Case-insensitive: providers return mixed case, team members are typed by
    # hand, and "Admin@x.com" and "admin@x.com" are the same mailbox.
    cur.execute(
        "SELECT username, role, name, email, company_id, department "
        "FROM users WHERE lower(email) = %s",
        (addr,),
    )
    rows = cur.fetchall()
    if not rows:
        raise SsoLoginError("no_account")
    if len(rows) > 1:
        # By policy one person belongs to one company and changes address when
        # they move, so this is a data anomaly rather than a real multi-tenant
        # user. Refuse rather than guess a tenant — landing someone in the wrong
        # company would be silent and invisible.
        raise SsoLoginError("ambiguous_account")

    # Callers reach here with either a plain cursor or a RealDictCursor (the
    # OAuth callback uses the latter). Unpacking a dict row positionally would
    # yield column *names*, so read by key when that's what we got.
    row = rows[0]
    if isinstance(row, dict):
        vals = (row["username"], row["role"], row["name"], row["email"],
                row["company_id"], row["department"])
    else:
        vals = tuple(row)
    username, role, name, user_email, company_id, department = vals
    return {"username": username, "role": role, "name": name, "email": user_email,
            "company_id": str(company_id), "department": department}


def issue_sso_code(cur, username: str, company_id: str, provider: str) -> str:
    """Mint a single-use code the SPA trades for real tokens."""
    raw = secrets.token_urlsafe(32)
    expires = datetime.datetime.utcnow() + datetime.timedelta(seconds=_SSO_CODE_TTL_SECONDS)
    cur.execute(
        "INSERT INTO auth_sso_codes (code_hash, username, company_id, provider, expires_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (_hash_code(raw), username, company_id, provider, expires),
    )
    return raw


# ── Start SSO ────────────────────────────────────────────────────────────────

@router.get("/api/auth/sso/authorize")
def sso_authorize(provider: str = Query(...)):
    """Public: begin a sign-in. No tenant exists yet — that's what we're finding out."""
    provider = (provider or "").lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported SSO provider: {provider}")
    if provider == "google" and not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Google sign-in is not configured on the server")
    if provider == "microsoft" and not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Microsoft sign-in is not configured on the server")

    # Empty tenant/username: at sign-in we don't know them yet. purpose="login"
    # is what tells the shared callback to resolve identity instead of storing a
    # mailbox against a known user.
    state = email_oauth.sign_state("", "", provider, purpose="login")
    return {"auth_url": email_oauth.build_authorize_url(
        state, provider=provider, login_only=not config.SSO_REQUEST_MAILBOX_SCOPES)}


# ── Redeem the one-time code ─────────────────────────────────────────────────

class SsoExchangeRequest(BaseModel):
    code: str


@router.post("/api/auth/sso/exchange")
def sso_exchange(body: SsoExchangeRequest):
    """Public: trade the single-use code from the redirect for a real session."""
    raw = (body.code or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="code is required")

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, company_id, expires_at, used_at "
            "FROM auth_sso_codes WHERE code_hash = %s",
            (_hash_code(raw),),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(status_code=401, detail="Invalid sign-in code")
        rec_id, username, company_id, expires_at, used_at = rec

        # Single use. A replayed code means it was captured somewhere it
        # shouldn't have been, so burn it rather than serving a second session.
        if used_at is not None:
            cur.execute("DELETE FROM auth_sso_codes WHERE id = %s", (rec_id,))
            conn.commit()
            raise HTTPException(status_code=401, detail="Sign-in code already used")

        now = datetime.datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.datetime.utcnow()
        if expires_at < now:
            raise HTTPException(status_code=401, detail="Sign-in code expired")

        cur.execute("UPDATE auth_sso_codes SET used_at = now() WHERE id = %s", (rec_id,))

        # Re-read the user at redemption so a role change or a removal between
        # the redirect and this call takes effect immediately.
        cur.execute(
            "SELECT username, role, name, email, company_id, department "
            "FROM users WHERE username = %s AND company_id = %s",
            (username, str(company_id)),
        )
        urow = cur.fetchone()
        if not urow:
            raise HTTPException(status_code=401, detail="Account is no longer active")

        token, payload = _build_access_token(*urow)
        refresh = _issue_refresh_token(cur, urow[0], urow[4])

        # Opportunistic cleanup; these rows are worthless after two minutes.
        cur.execute("DELETE FROM auth_sso_codes WHERE expires_at < now() - interval '1 day'")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Sign-in failed")
    finally:
        put_conn(conn)

    return {
        "token": token,
        "refresh_token": refresh,
        "expires_in": config.ACCESS_TOKEN_TTL_MIN * 60,
        "user": {k: v for k, v in payload.items() if k not in ("exp", "iat")},
    }
