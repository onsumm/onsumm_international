"""
email_api.py — user-sent email over Gmail OAuth.

Endpoints (prefix mixed — kept flat to match existing /api/... routes):
  GET    /api/me/email-connection            — connection status for a user
  GET    /api/me/email-connection/authorize  — start OAuth, returns Google auth URL
  GET    /api/oauth/email/callback           — Google redirect target; stores tokens
  DELETE /api/me/email-connection            — revoke + remove connection
  POST   /api/vendors/{sid}/contacts/{cid}/email — send an email as the user, logged

`send_vendor_contact_email` is the core of that last route, extracted so
routers/review_hub.py's `notify` action (POST /api/review/act/notify) can send
over the same path — same thread handling, same outreach log, same POC-store
mirror — without a second copy of any of it.

Connections are keyed on (tenant_id, username) — the same identity the rest of
the app uses. Tokens are encrypted at rest (email_oauth.encrypt_token).
"""
from __future__ import annotations

import re
import traceback
from datetime import datetime, timezone
from email.utils import getaddresses

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from ..integrations.ocr import enforce_pdf_page_limit
from fastapi.responses import RedirectResponse, Response
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from .. import config
from ..integrations import email_oauth
from ..config import get_conn, put_conn
from . import sso
from ..security.auth import current_username, require_tenant

router = APIRouter(tags=["User Email"])


# ── Table ────────────────────────────────────────────────────────────────────

def ensure_user_email_connections_table():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_email_connections (
                id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id        uuid        NOT NULL,
                username         text        NOT NULL,
                provider         text        NOT NULL DEFAULT 'google',
                email            text        NOT NULL,
                access_token     text        NOT NULL,
                refresh_token    text,
                token_expires_at timestamptz,
                scopes           text,
                status           text        NOT NULL DEFAULT 'active',
                connected_at     timestamptz NOT NULL DEFAULT now(),
                updated_at       timestamptz NOT NULL DEFAULT now(),
                UNIQUE (tenant_id, username, provider)
            );
            CREATE INDEX IF NOT EXISTS idx_uec_user ON user_email_connections (tenant_id, username);
            -- thread linkage for the conversation view
            ALTER TABLE vendor_outreach_log ADD COLUMN IF NOT EXISTS thread_id  text;
            ALTER TABLE vendor_outreach_log ADD COLUMN IF NOT EXISTS message_id text;
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] user_email_connections table ready")
    except Exception:
        print("[startup] ERROR creating user_email_connections table:")
        traceback.print_exc()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_tenant(cur, supplier_id: str, company_id: str | None) -> str:
    # Tenant is derived from the authenticated JWT; client company_id is ignored.
    return require_tenant()


def _caller(username: str | None) -> str:
    """The signed-in user, whatever the request says.

    These routes used to take `username` from the query string and trust it, so
    any signed-in user could read another user's mailbox by naming them. The
    JWT subject is the same value the app sends (AuthContext maps `sub` to
    `username`), so prefer it; the parameter only fills in for a token without
    a subject.
    """
    who = current_username() or username
    if not who:
        raise HTTPException(status_code=401, detail="Authentication required")
    return who


def _load_connection(cur, tenant_id: str, username: str, provider: str | None = None):
    """The user's mailbox connection. provider=None means "whichever they linked".

    The table allows one row per provider, but a user connects a single mailbox
    in practice, so callers that just want "their mailbox" pass nothing and get
    it back regardless of provider. Prefer an active row over a broken one.
    """
    if provider:
        cur.execute("""
            SELECT * FROM user_email_connections
            WHERE tenant_id=%s AND username=%s AND provider=%s
            LIMIT 1
        """, (tenant_id, username, provider))
    else:
        cur.execute("""
            SELECT * FROM user_email_connections
            WHERE tenant_id=%s AND username=%s
            ORDER BY (status='active') DESC, updated_at DESC
            LIMIT 1
        """, (tenant_id, username))
    return cur.fetchone()


def _valid_access_token(cur, conn, row) -> str:
    """Return a live access token, refreshing (and persisting) if expired."""
    provider = row.get("provider") or "google"
    # Every send/read path funnels through here, so this is the one place that
    # has to stop an Outlook token being handed to the Gmail REST calls. Reading
    # and sending over Graph land next; connecting already works.
    if provider != "google":
        raise HTTPException(
            status_code=501,
            detail="Outlook mailboxes can be connected, but sending and reading over "
                   "Microsoft Graph isn't available yet — use a Gmail mailbox for now.",
        )

    exp = row["token_expires_at"]
    if exp and exp > datetime.now(timezone.utc):
        return email_oauth.decrypt_token(row["access_token"])

    refresh = email_oauth.decrypt_token(row["refresh_token"])
    if not refresh:
        raise HTTPException(status_code=409, detail="Mailbox needs to be reconnected")
    try:
        fresh = email_oauth.refresh_access_token(refresh, provider=provider)
    except Exception:
        cur.execute("UPDATE user_email_connections SET status='error', updated_at=now() WHERE id=%s", (row["id"],))
        conn.commit()
        raise HTTPException(status_code=409, detail="Mailbox authorization expired — please reconnect")
    # Entra rotates refresh tokens on use and the old one stops working after a
    # short overlap, so persist a replacement whenever one comes back. Google
    # doesn't rotate, so this is a no-op there.
    if fresh.get("refresh"):
        cur.execute(
            "UPDATE user_email_connections SET refresh_token=%s WHERE id=%s",
            (email_oauth.encrypt_token(fresh["refresh"]), row["id"]),
        )
    cur.execute(
        "UPDATE user_email_connections SET access_token=%s, token_expires_at=%s, status='active', updated_at=now() WHERE id=%s",
        (email_oauth.encrypt_token(fresh["access"]), fresh["expires_at"], row["id"]),
    )
    conn.commit()
    return fresh["access"]


# ── Status ───────────────────────────────────────────────────────────────────

@router.get("/api/me/email-connection")
def get_email_connection(company_id: str = Query(default=None), username: str = Query(...)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _load_connection(cur, company_id, username)
        put_conn(conn)
        if not row:
            return {"connected": False}
        return {"connected": row["status"] == "active", "provider": row["provider"],
                "email": row["email"], "status": row["status"]}
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load email connection")


# ── Start OAuth ──────────────────────────────────────────────────────────────

@router.get("/api/me/email-connection/authorize")
def authorize_email(company_id: str = Query(default=None), username: str = Query(...),
                    provider: str = Query(default="google")):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    provider = (provider or "google").lower()
    if provider not in ("google", "microsoft"):
        raise HTTPException(status_code=400, detail=f"Unsupported email provider: {provider}")
    if provider == "google" and not (config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Gmail OAuth is not configured on the server")
    if provider == "microsoft" and not (config.MS_CLIENT_ID and config.MS_CLIENT_SECRET):
        raise HTTPException(status_code=500, detail="Outlook OAuth is not configured on the server")
    state = email_oauth.sign_state(company_id, username, provider)
    return {"auth_url": email_oauth.build_authorize_url(state, provider=provider)}


# ── SSO login completion ─────────────────────────────────────────────────────

# Scope fragments that mean "we can actually use this mailbox". Providers return
# these in varying forms — Graph as full URIs, Google as googleapis.com URLs — so
# match on the distinctive tail rather than the whole string.
_MAILBOX_SCOPE_MARKERS = ("mail.read", "mail.send", "gmail.readonly", "gmail.send")


def _granted_mailbox_access(scopes: str) -> bool:
    granted = (scopes or "").lower()
    return any(marker in granted for marker in _MAILBOX_SCOPE_MARKERS)


def _login_return_url() -> str:
    """Where a *sign-in* round-trip sends the browser back to.

    The site root, deliberately, even though only the login screen redeems the
    code: this is a hard navigation from the provider, and a deep link like
    /login is served to the browser by the CDN's SPA rewrite rule. If that rule
    is missing on a deployment the request 404s before any JavaScript runs,
    which is unfixable from inside the app. The root is always served, and the
    SPA carries the code on to the login screen client-side.
    """
    return config.APP_RETURN_URL.rstrip("/") or config.APP_RETURN_URL


def _state_purpose(state: str) -> str:
    """Which flow a state belongs to, on paths where it may not verify.

    A cancelled consent comes back with the state intact, so this normally
    answers correctly; an expired or tampered one falls back to the mailbox
    connect wording rather than trusting unsigned claims.
    """
    try:
        return email_oauth.verify_state(state).get("purpose", "connect")
    except Exception:
        return "connect"



def _complete_sso_login(provider: str, tok: dict, return_url: str) -> RedirectResponse:
    """Finish a "Continue with Google/Microsoft" round-trip.

    Resolves the provider's address to a team member, stores the mailbox tokens
    against that account (so SSO users never connect a mailbox separately), and
    hands the SPA a single-use code to trade for a session.
    """
    email = (tok.get("email") or "").strip()

    # Google tells us whether it actually proved ownership of the address.
    # Microsoft has no equivalent claim, so there the team member list is the
    # only gate — an address has to have been invited before it can sign in.
    if provider == "google" and not tok.get("email_verified", False):
        return RedirectResponse(f"{return_url}?sso_error=email_unverified")

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            user = sso.resolve_sso_user(cur, email)
        except sso.SsoLoginError as exc:
            put_conn(conn)
            return RedirectResponse(f"{return_url}?sso_error={exc.reason}")

        tenant_id, username = user["company_id"], user["username"]

        # Only record a mailbox if mail access was actually granted. Sign-in can
        # legitimately carry no mail scopes (SSO_REQUEST_MAILBOX_SCOPES=false, or
        # a provider that granted a subset), and storing a login-only token as a
        # mailbox connection would show "Connected" for a mailbox we cannot read
        # or send from — a lie the user only discovers when sending fails.
        if not _granted_mailbox_access(tok.get("scopes", "")):
            code = sso.issue_sso_code(cur, username, tenant_id, provider)
            conn.commit()
            put_conn(conn)
            return RedirectResponse(f"{return_url}?sso={code}")

        # Same upsert the mailbox-connect path uses: keep an existing refresh
        # token when the provider withholds one on repeat consent, so signing in
        # again never downgrades a working mailbox to one that cannot refresh.
        existing = _load_connection(cur, tenant_id, username, provider)
        refresh_enc = email_oauth.encrypt_token(tok["refresh"]) if tok.get("refresh") else (
            existing["refresh_token"] if existing else None)
        cur.execute("""
            INSERT INTO user_email_connections
                (tenant_id, username, provider, email, access_token, refresh_token, token_expires_at, scopes, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', now())
            ON CONFLICT (tenant_id, username, provider) DO UPDATE SET
                email=EXCLUDED.email,
                access_token=EXCLUDED.access_token,
                refresh_token=COALESCE(EXCLUDED.refresh_token, user_email_connections.refresh_token),
                token_expires_at=EXCLUDED.token_expires_at,
                scopes=EXCLUDED.scopes,
                status='active',
                updated_at=now()
        """, (tenant_id, username, provider, email,
              email_oauth.encrypt_token(tok["access"]), refresh_enc,
              tok["expires_at"], tok.get("scopes", "")))

        code = sso.issue_sso_code(cur, username, tenant_id, provider)
        conn.commit()
        put_conn(conn)
        return RedirectResponse(f"{return_url}?sso={code}")
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        return RedirectResponse(f"{return_url}?sso_error=error")


# ── OAuth callback ───────────────────────────────────────────────────────────

@router.get("/api/oauth/email/callback")
def email_oauth_callback(code: str = Query(default=""), state: str = Query(default=""),
                         error: str = Query(default=""),
                         admin_consent: str = Query(default=""),
                         tenant: str = Query(default="")):
    return_url = config.APP_RETURN_URL

    # Entra's tenant-wide admin consent lands here with ?admin_consent=True and
    # no code — the grant already succeeded, there is just nothing to exchange.
    # Without this it reads as "cancelled", telling an admin who just approved
    # the app that it failed.
    if admin_consent:
        ok = "1" if (admin_consent or "").lower() in ("true", "1") and not error else "0"
        return RedirectResponse(f"{return_url}?admin_consent={ok}&tenant={tenant or ''}")

    if error or not code or not state:
        # Cancelling a sign-in has to land on the login screen with a sign-in
        # message; the mailbox-connect wording would be nonsense there.
        if _state_purpose(state) == "login":
            return RedirectResponse(f"{_login_return_url()}?sso_error=cancelled")
        return RedirectResponse(f"{return_url}?email_connected=0&reason=cancelled")

    try:
        who = email_oauth.verify_state(state)
    except Exception:
        return RedirectResponse(f"{return_url}?email_connected=0&reason=bad_state")

    conn = None
    try:
        provider = who["provider"]
        tok = email_oauth.exchange_code(code, provider=provider)

        # Sign-in flow: identity comes from the provider, so resolve the team
        # member first and connect their mailbox under the account we just
        # proved. Mailbox-connect flow falls through to the original path below,
        # where the user was already known before the round-trip started.
        if who.get("purpose") == "login":
            return _complete_sso_login(provider, tok, _login_return_url())

        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # Upsert on (tenant, user, provider). Preserve an existing refresh token
        # if the provider didn't return a new one on re-consent.
        existing = _load_connection(cur, who["tenant_id"], who["username"], provider)
        refresh_enc = email_oauth.encrypt_token(tok["refresh"]) if tok["refresh"] else (
            existing["refresh_token"] if existing else None)
        cur.execute("""
            INSERT INTO user_email_connections
                (tenant_id, username, provider, email, access_token, refresh_token, token_expires_at, scopes, status, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', now())
            ON CONFLICT (tenant_id, username, provider) DO UPDATE SET
                email=EXCLUDED.email,
                access_token=EXCLUDED.access_token,
                refresh_token=COALESCE(EXCLUDED.refresh_token, user_email_connections.refresh_token),
                token_expires_at=EXCLUDED.token_expires_at,
                scopes=EXCLUDED.scopes,
                status='active',
                updated_at=now()
        """, (who["tenant_id"], who["username"], provider, tok["email"],
              email_oauth.encrypt_token(tok["access"]), refresh_enc,
              tok["expires_at"], tok["scopes"]))
        conn.commit()
        put_conn(conn)
        return RedirectResponse(f"{return_url}?email_connected=1&provider={provider}")
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        return RedirectResponse(f"{return_url}?email_connected=0&reason=error")


# ── Disconnect ───────────────────────────────────────────────────────────────

@router.delete("/api/me/email-connection")
def disconnect_email(company_id: str = Query(default=None), username: str = Query(...),
                     provider: str | None = Query(default=None)):
    company_id = require_tenant()  # tenant from JWT; client company_id ignored
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _load_connection(cur, company_id, username, provider)
        if row:
            try:
                email_oauth.revoke(email_oauth.decrypt_token(row["refresh_token"]) or
                                   email_oauth.decrypt_token(row["access_token"]),
                                   provider=row.get("provider") or "google")
            except Exception:
                pass
            cur.execute("DELETE FROM user_email_connections WHERE id=%s", (row["id"],))
            conn.commit()
        put_conn(conn)
        return {"disconnected": True}
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to disconnect mailbox")


# ── Send ─────────────────────────────────────────────────────────────────────

def send_vendor_contact_email(
    cur, conn, *, tenant_id: str, supplier_id: str, contact_id: str, username: str,
    subject: str, message: str, cc: str | None = None, thread_id: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None, html: str | None = None,
) -> dict:
    """Send as `username`'s connected mailbox to one of a vendor's contacts.

    The shared core of `POST /api/vendors/{sid}/contacts/{cid}/email` —
    extracted so the Review Hub's `notify` action (routers/review_hub.py) can
    reach the same send path, with the same thread/outreach-log/fact-graph
    side effects, rather than carrying a second copy of them. Takes an
    already-open `cur`/`conn`; the caller owns acquiring and releasing it
    (get_conn/put_conn, or a review_hub._PoolConn transaction — either way this
    function never calls either).

    `html` is an optional richer rendering of the same `message` (see
    email_oauth.build_message). Only `message` is logged and mirrored into the
    POC store: the thread views, the extraction worker and the fact graph all
    read plain text, and storing a wall of markup in `body` would make an
    outbound message unreadable everywhere it is shown.

    Raises HTTPException for setup problems (no mailbox connected, contact not
    found, no subject) and for a send that Gmail itself rejected — every one of
    these is exactly what the route already raised, unchanged, so a caller that
    lets it propagate keeps that behaviour. Returns the same shape the route
    always returned on success.
    """
    subject = (subject or "").strip()
    message = message or ""

    # Sender: the user's connected mailbox
    row = _load_connection(cur, tenant_id, username)
    if not row or row["status"] != "active":
        raise HTTPException(status_code=409, detail="Connect your mailbox before sending")

    # Recipient: resolved server-side from the contact (scoped to this vendor/tenant)
    cur.execute("""
        SELECT name, email FROM supplier_contacts
        WHERE id=%s AND tenant_id=%s AND supplier_id=%s LIMIT 1
    """, (contact_id, tenant_id, supplier_id))
    contact = cur.fetchone()
    if not contact or not contact["email"]:
        raise HTTPException(status_code=404, detail="Contact not found or has no email")
    return _send_and_log(cur, conn, row=row, tenant_id=tenant_id, supplier_id=supplier_id,
                         contact_id=contact_id, username=username, to_email=contact["email"],
                         subject=subject, message=message, cc=cc, thread_id=thread_id,
                         attachments=attachments, html=html)


def _send_and_log(
    cur, conn, *, row, tenant_id: str, supplier_id: str, contact_id: str | None, username: str,
    to_email: str, subject: str, message: str, cc: str | None = None, thread_id: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None, html: str | None = None,
) -> dict:
    """Send from the connected mailbox `row` and record it against the vendor.

    `to_email` may list several addresses ("a@x.com, b@x.com"): Gmail reads
    the recipients from the headers. Shared by the per-contact route (one
    contact) and the vendor inbox (reply all, forward, anyone on the thread).
    """
    access = _valid_access_token(cur, conn, row)

    # A thread_id means "reply into this conversation" — derive Re: subject and
    # In-Reply-To from that thread. No thread_id means start a NEW conversation
    # (each subject is its own chain).
    thread_id = (thread_id or "").strip() or None
    in_reply_to = None
    if thread_id:
        try:
            msgs = email_oauth.get_thread(access, thread_id)
            if msgs:
                in_reply_to = msgs[-1].get("message_id")
                if not subject:
                    base = msgs[0].get("subject", "") or ""
                    subject = base if base.lower().startswith("re:") else f"Re: {base}".strip()
        except Exception:
            pass
    if not subject:
        raise HTTPException(status_code=400, detail="Subject is required")

    status = "sent"
    send_error = None
    sent_thread_id = thread_id
    sent_message_id = None
    try:
        res = email_oauth.send_email(access, row["email"], to_email, subject, message,
                                     cc=cc, thread_id=thread_id, in_reply_to=in_reply_to,
                                     attachments=attachments or None, html=html or None)
        sent_thread_id = res.get("threadId") or thread_id
        sent_message_id = res.get("id")
    except Exception as e:
        status = "failed"
        send_error = str(e)
        print(f"[email] send failed: {send_error}")

    cur.execute("""
        INSERT INTO vendor_outreach_log
            (supplier_id, tenant_id, requested_by, email_to, subject, body, status, thread_id, message_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (supplier_id, tenant_id, username, to_email, subject, message, status,
          sent_thread_id, sent_message_id))
    conn.commit()

    # Mirror the outbound message into the unified POC store and enrich the
    # fact graph off the request path (fire-and-forget; never blocks the send).
    _pem_id = None
    if status == "sent":
        try:
            cur.execute("""
                INSERT INTO poc_email_messages
                    (tenant_id, supplier_id, contact_id, direction, gmail_msg_id, thread_id,
                     to_email, subject, body)
                VALUES (%s,%s,%s,'outbound',%s,%s,%s,%s,%s)
                ON CONFLICT (tenant_id, gmail_msg_id, direction) WHERE gmail_msg_id IS NOT NULL
                DO UPDATE SET body=EXCLUDED.body, subject=EXCLUDED.subject
                RETURNING message_id::text
            """, (tenant_id, supplier_id, contact_id, sent_message_id, sent_thread_id,
                  to_email, subject, message))
            _row = cur.fetchone()
            _pem_id = _row["message_id"] if _row else None
            conn.commit()
        except Exception:
            conn.rollback()
            traceback.print_exc()

        # Flip the thread to "we're waiting on the vendor now". Plain
        # psycopg2 UPSERT because this router is sync, unlike the asyncpg
        # worker that flips it back to awaiting_internal on the reply
        # (backend_ocr/app/review/comm_extraction_worker.py) — comm_thread_state
        # has to be reachable from both stacks since both write it.
        # thread_id is the table's primary key half, so a send with no
        # thread (a brand-new conversation with no Gmail thread id yet)
        # has nothing to key the row on and is skipped rather than
        # guessed at.
        if sent_thread_id:
            try:
                cur.execute("""
                    INSERT INTO comm_thread_state
                        (tenant_id, thread_id, supplier_id, contact_id,
                         last_direction, last_message_at, status)
                    VALUES (%s, %s, %s, %s, 'outbound', now(), 'awaiting_vendor')
                    ON CONFLICT (tenant_id, thread_id) DO UPDATE SET
                        supplier_id     = COALESCE(EXCLUDED.supplier_id, comm_thread_state.supplier_id),
                        contact_id      = COALESCE(EXCLUDED.contact_id, comm_thread_state.contact_id),
                        last_direction  = 'outbound',
                        last_message_at = now(),
                        status          = 'awaiting_vendor',
                        updated_at      = now()
                """, (tenant_id, sent_thread_id, supplier_id, contact_id))
                conn.commit()
            except Exception:
                conn.rollback()
                traceback.print_exc()

    if _pem_id:
        try:
            from ..domain import fact_graph
            fact_graph.enqueue(fact_graph.enrich_email, tenant_id, _pem_id)
        except Exception:
            pass

    if status == "failed":
        raise HTTPException(status_code=502, detail=send_error or "Gmail rejected the message — check the mailbox connection")
    return {"status": "sent", "email_to": to_email, "from": row["email"], "thread_id": sent_thread_id}


@router.post("/api/vendors/{supplier_id}/contacts/{contact_id}/email")
async def send_contact_email(
    supplier_id: str, contact_id: str,
    body: str = Form(...),
    company_id: str = Form(...),
    username: str = Form(...),
    subject: str = Form(default=""),
    cc: str | None = Form(default=None),
    thread_id: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
):
    message = body or ""
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message body is required")

    # Read uploads up-front (before touching the DB connection).
    attachments: list[tuple[str, bytes, str]] = []
    for f in files or []:
        if not f or not f.filename:
            continue
        data = await f.read()
        enforce_pdf_page_limit(data, f.filename)
        attachments.append((f.filename, data, f.content_type or "application/octet-stream"))

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant(cur, supplier_id, company_id)
        username = _caller(username)

        result = send_vendor_contact_email(
            cur, conn, tenant_id=tenant_id, supplier_id=supplier_id, contact_id=contact_id,
            username=username, subject=subject, message=message, cc=cc, thread_id=thread_id,
            attachments=attachments or None,
        )
        put_conn(conn)
        return result
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to send email")


# ── Conversations (threads) ──────────────────────────────────────────────────

@router.get("/api/vendors/{supplier_id}/contacts/{contact_id}/threads")
def list_contact_threads(supplier_id: str, contact_id: str,
                         company_id: str = Query(...), username: str = Query(...)):
    """List the distinct conversations (threads) with a contact — one per subject
    chain — newest first. Built from our send log so it's cheap."""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant(cur, supplier_id, company_id)
        username = _caller(username)

        row = _load_connection(cur, tenant_id, username)
        connected = bool(row and row["status"] == "active")
        me = row["email"] if row else None

        cur.execute("""
            SELECT email FROM supplier_contacts
            WHERE id=%s AND tenant_id=%s AND supplier_id=%s LIMIT 1
        """, (contact_id, tenant_id, supplier_id))
        contact = cur.fetchone()
        if not contact or not contact["email"]:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Contact not found or has no email")
        to_email = contact["email"]

        cur.execute("""
            SELECT thread_id,
                   (array_agg(subject ORDER BY requested_at ASC))[1]  AS subject,
                   (array_agg(body    ORDER BY requested_at DESC))[1] AS last_body,
                   MAX(requested_at) AS last_at,
                   COUNT(*)          AS sent_count
            FROM vendor_outreach_log
            WHERE tenant_id=%s AND supplier_id=%s AND email_to=%s
              AND thread_id IS NOT NULL AND status='sent'
            GROUP BY thread_id
            ORDER BY last_at DESC
        """, (tenant_id, supplier_id, to_email))
        threads = [{
            "thread_id": r["thread_id"],
            "subject": r["subject"] or "(no subject)",
            "last_body": r["last_body"],
            "last_at": str(r["last_at"]),
            "sent_count": r["sent_count"],
        } for r in cur.fetchall()]
        put_conn(conn)
        return {"me": me, "connected": connected, "threads": threads}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to list conversations")


@router.get("/api/vendors/{supplier_id}/contacts/{contact_id}/thread")
def get_contact_thread(supplier_id: str, contact_id: str,
                       thread_id: str = Query(...),
                       company_id: str = Query(...), username: str = Query(...)):
    """Full messages of one conversation (Gmail thread), oldest first."""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant(cur, supplier_id, company_id)
        username = _caller(username)

        row = _load_connection(cur, tenant_id, username)
        me = row["email"] if row else None
        if not row or row["status"] != "active":
            # Not connected → show what we logged for this thread only.
            cur.execute("""
                SELECT subject, body, requested_at FROM vendor_outreach_log
                WHERE tenant_id=%s AND supplier_id=%s AND thread_id=%s AND status='sent'
                ORDER BY requested_at ASC
            """, (tenant_id, supplier_id, thread_id))
            logged = cur.fetchall()
            put_conn(conn)
            return {"me": me, "read_available": False, "messages": [
                {"id": None, "outbound": True, "from": me or "", "date": str(r["requested_at"]),
                 "subject": r["subject"], "body": r["body"]} for r in logged
            ]}

        access = _valid_access_token(cur, conn, row)
        put_conn(conn)

        try:
            raw = email_oauth.get_thread(access, thread_id)
        except Exception as e:
            raise HTTPException(status_code=409, detail=f"Reconnect your mailbox to read replies: {e}")

        me_l = (me or "").lower()
        messages = [{
            "id": m["id"],
            "outbound": me_l in (m["from"] or "").lower(),
            "from": m["from"], "date": m["date"], "subject": m["subject"], "body": m["body"],
            "attachments": m.get("attachments", []),
        } for m in raw]
        return {"me": me, "read_available": True, "messages": messages}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load conversation")


_SUMMARY_SYSTEM_PROMPT = """You are an assistant for an accounts-payable / finance team at the company.
You are given an email conversation between a team member ("You") and a vendor contact.
Summarize the conversation in 1-3 plain sentences (what it's about, current state).
Then suggest concrete next actions the finance team should take — ONLY if there are clear,
actionable items (e.g. a requested document, a payment to release, a dispute to resolve, a
deadline to meet). If there is nothing actionable, return an empty actions list — do NOT invent
busy-work.
Respond as strict JSON: {"summary": "<text>", "actions": [{"label": "<short imperative>", "detail": "<one line>"}]}."""


def _summarize_thread(msgs: list[dict], me: str | None) -> dict:
    import json as _json
    from ..config import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        return {"summary": "AI summary is unavailable (OpenAI key not configured).", "actions": []}
    me_l = (me or "").lower()
    lines = []
    for m in msgs:
        who = "You" if me_l and me_l in (m.get("from") or "").lower() else (m.get("from") or "Vendor")
        lines.append(f"[{who}] Subject: {m.get('subject','')}\n{m.get('body','')}")
    transcript = "\n\n---\n\n".join(lines)[:8000]
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
        )
        data = _json.loads(resp.choices[0].message.content or "{}")
        return {"summary": data.get("summary", ""), "actions": data.get("actions", []) or []}
    except Exception as e:
        traceback.print_exc()
        return {"summary": f"Could not generate summary: {e}", "actions": []}


@router.post("/api/vendors/{supplier_id}/contacts/{contact_id}/thread/summarize")
def summarize_contact_thread(supplier_id: str, contact_id: str,
                             thread_id: str = Query(...),
                             company_id: str = Query(...), username: str = Query(...)):
    """AI summary + suggested actions for one conversation."""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant(cur, supplier_id, company_id)
        username = _caller(username)
        row = _load_connection(cur, tenant_id, username)
        if not row or row["status"] != "active":
            put_conn(conn)
            raise HTTPException(status_code=409, detail="Connect your mailbox to summarize conversations")
        me = row["email"]
        access = _valid_access_token(cur, conn, row)
        put_conn(conn)

        try:
            msgs = email_oauth.get_thread(access, thread_id)
        except Exception as e:
            raise HTTPException(status_code=409, detail=f"Reconnect your mailbox to read this thread: {e}")
        if not msgs:
            return {"summary": "No messages to summarize.", "actions": []}
        return _summarize_thread(msgs, me)
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to summarize conversation")


@router.get("/api/vendors/{supplier_id}/contacts/{contact_id}/attachment")
def download_attachment(supplier_id: str, contact_id: str,
                        message_id: str = Query(...), attachment_id: str = Query(...),
                        filename: str = Query(default="attachment"),
                        company_id: str = Query(...), username: str = Query(...)):
    """Stream a single Gmail attachment back to the browser as a download."""
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant(cur, supplier_id, company_id)
        username = _caller(username)
        row = _load_connection(cur, tenant_id, username)
        if not row or row["status"] != "active":
            put_conn(conn)
            raise HTTPException(status_code=409, detail="Connect your mailbox to download attachments")
        access = _valid_access_token(cur, conn, row)
        put_conn(conn)

        data = email_oauth.get_attachment(access, message_id, attachment_id)
        safe = filename.replace('"', "").replace("\n", "")
        return Response(
            content=data,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe}"'},
        )
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to download attachment")


# ── Vendor inbox: every thread with a vendor, whoever started it ──────────────
#
# The per-contact routes above list threads from our own send log, so a thread
# the vendor started never showed up. These read the signed-in user's Gmail for
# anything to or from the vendor's contacts (and, for a company domain, anyone
# at it), and say who started each thread.

# Mirrors review/comm_extraction_worker._FREEMAIL_DOMAINS: a shared mail domain
# says nothing about which company a sender is from, so it is never searched.
_FREEMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in",
    "hotmail.com", "outlook.com", "live.com", "rediffmail.com", "icloud.com",
    "protonmail.com", "proton.me", "aol.com",
})
_INBOX_MAX_THREADS = 30
_INBOX_LOOKBACK = "2y"


def _domain(addr: str) -> str:
    _, at, d = (addr or "").strip().lower().rpartition("@")
    return d if at else ""


def _addresses(header: str) -> list[str]:
    """Bare lower-case addresses from a From/To/Cc header."""
    from email.utils import getaddresses
    return [a.lower() for _, a in getaddresses([header or ""]) if a and "@" in a]


def _vendor_addresses(cur, tenant_id: str, supplier_id: str) -> tuple[dict[str, dict], set[str]]:
    """The vendor's known addresses ({email: contact}) and company domains."""
    cur.execute("""
        SELECT id, name, email FROM supplier_contacts
        WHERE tenant_id=%s AND supplier_id=%s AND email IS NOT NULL AND email <> ''
    """, (tenant_id, supplier_id))
    contacts = {r["email"].strip().lower(): {"id": str(r["id"]), "name": r["name"]} for r in cur.fetchall()}
    cur.execute("SELECT email FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s",
                (supplier_id, tenant_id))
    row = cur.fetchone()
    extra = (row or {}).get("email") or ""
    known = set(contacts) | ({extra.strip().lower()} if "@" in extra else set())
    domains = {_domain(a) for a in known} - _FREEMAIL_DOMAINS - {""}
    for a in known - set(contacts):
        contacts.setdefault(a, {"id": None, "name": None})
    return contacts, domains


def _is_vendor(addr: str, contacts: dict, domains: set[str]) -> bool:
    return addr in contacts or _domain(addr) in domains


def _thread_involves_vendor(msgs: list[dict], contacts: dict, domains: set[str]) -> bool:
    for m in msgs:
        for a in _addresses(m.get("from", "")) + _addresses(m.get("to", "")) + _addresses(m.get("cc", "")):
            if _is_vendor(a, contacts, domains):
                return True
    return False


def _iso_ms(ms: int) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


def _people(msgs: list[dict]) -> dict[str, str]:
    """{address: display name} for everyone on a thread, best name kept."""
    from email.utils import getaddresses
    names: dict[str, str] = {}
    for m in msgs:
        for name, addr in getaddresses([m.get("from", ""), m.get("to", ""), m.get("cc", "")]):
            a = (addr or "").lower()
            if "@" not in a:
                continue
            nm = (name or "").strip().strip('"')
            if nm and not names.get(a):
                names[a] = nm
            else:
                names.setdefault(a, "")
    return names


def _summarize_headers(tid: str, snippet: str, msgs: list[dict], me: str,
                       contacts: dict, domains: set[str]) -> dict:
    """One row of the vendor's thread list, from a thread's headers."""
    first, last = msgs[0], msgs[-1]
    me_l = (me or "").lower()
    from_me = lambda m: bool(me_l) and me_l in (m.get("from") or "").lower()
    names = _people(msgs)
    label = lambda a: names.get(a) or (contacts.get(a) or {}).get("name") or a
    vendor_side: list[str] = []
    for m in msgs:
        for a in _addresses(m.get("from", "")) + _addresses(m.get("to", "")) + _addresses(m.get("cc", "")):
            if _is_vendor(a, contacts, domains) and a not in vendor_side:
                vendor_side.append(a)
    contact = next((contacts[a] for a in vendor_side if contacts.get(a, {}).get("id")), None)
    # The person this thread is with: whoever on their side last wrote, else
    # whoever we wrote to. Never us — a list of our own address says nothing.
    senders = [a for m in reversed(msgs) for a in _addresses(m.get("from", "")) if _is_vendor(a, contacts, domains)]
    who = senders[0] if senders else (vendor_side[0] if vendor_side else None)
    return {
        "thread_id": tid,
        "subject": first.get("subject") or "(no subject)",
        "snippet": snippet or last.get("snippet", ""),
        "first_at": _iso_ms(first["internal_date"]),
        "last_at": _iso_ms(last["internal_date"]),
        "message_count": len(msgs),
        "started_by": "us" if from_me(first) else "vendor",
        "started_by_name": (first.get("from") or "").split("<")[0].strip().strip('"') or first.get("from"),
        "last_from": (last.get("from") or "").split("<")[0].strip().strip('"') or last.get("from"),
        "last_outbound": from_me(last),
        "needs_reply": not from_me(last),
        "has_attachments": any(m.get("has_attachment") for m in msgs),
        "vendor_addresses": vendor_side,
        "contact_id": contact["id"] if contact else None,
        # People at the vendor's domain who write in but aren't contacts yet.
        "unknown_senders": [a for a in vendor_side if not contacts.get(a, {}).get("id")],
        "counterparty": {"email": who, "name": label(who)} if who else None,
        "participants": [{"email": a, "name": label(a), "vendor": _is_vendor(a, contacts, domains), "me": a == me_l}
                         for a in names],
    }


def _thread_states(cur, tenant_id: str, thread_ids: list[str]) -> dict[str, dict]:
    """comm_thread_state rows for these threads (empty if the table isn't there)."""
    if not thread_ids:
        return {}
    try:
        cur.execute("""
            SELECT thread_id, status, updated_at FROM comm_thread_state
            WHERE tenant_id=%s AND thread_id = ANY(%s)
        """, (tenant_id, thread_ids))
        return {r["thread_id"]: r for r in cur.fetchall()}
    except Exception:
        cur.connection.rollback()
        return {}


def _apply_status(t: dict, state: dict | None) -> dict:
    """needs_reply / waiting / done. Done holds until someone writes again."""
    done = False
    if state and state["status"] == "resolved" and t.get("last_at"):
        last = datetime.fromisoformat(t["last_at"])
        done = state["updated_at"] >= last
    t["status"] = "done" if done else ("needs_reply" if t["needs_reply"] else "waiting")
    t["needs_reply"] = t["status"] == "needs_reply"
    return t


@router.get("/api/vendors/{supplier_id}/inbox")
def vendor_inbox(supplier_id: str):
    """Every email thread with this vendor in the signed-in user's mailbox.

    Without a connected mailbox, falls back to the threads we sent (the send
    log), so the list is never emptier than before.
    """
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()
        username = _caller(None)
        contacts, domains = _vendor_addresses(cur, tenant_id, supplier_id)
        row = _load_connection(cur, tenant_id, username)
        connected = bool(row and row["status"] == "active")
        me = row["email"] if row else None

        cur.execute("""
            SELECT thread_id, (array_agg(subject ORDER BY requested_at ASC))[1] AS subject,
                   (array_agg(body ORDER BY requested_at DESC))[1] AS last_body,
                   MIN(requested_at) AS first_at, MAX(requested_at) AS last_at, COUNT(*) AS n,
                   (array_agg(email_to ORDER BY requested_at ASC))[1] AS email_to
            FROM vendor_outreach_log
            WHERE tenant_id=%s AND supplier_id=%s AND thread_id IS NOT NULL AND status='sent'
            GROUP BY thread_id ORDER BY last_at DESC LIMIT %s
        """, (tenant_id, supplier_id, _INBOX_MAX_THREADS))
        logged = {r["thread_id"]: r for r in cur.fetchall()}

        def from_log(r) -> dict:
            to = (r["email_to"] or "").lower()
            c = contacts.get(to) or {}
            return {
                "thread_id": r["thread_id"], "subject": r["subject"] or "(no subject)",
                "snippet": (r["last_body"] or "")[:200],
                "first_at": r["first_at"].isoformat() if r["first_at"] else None,
                "last_at": r["last_at"].isoformat() if r["last_at"] else None,
                "message_count": int(r["n"]), "started_by": "us", "started_by_name": me or username,
                "last_from": me or username, "last_outbound": True, "needs_reply": False,
                "has_attachments": False, "vendor_addresses": [to] if to else [],
                "contact_id": c.get("id"), "unknown_senders": [],
                "counterparty": {"email": to, "name": c.get("name") or to} if to else None,
                "participants": [{"email": to, "name": c.get("name") or to, "vendor": True, "me": False}] if to else [],
            }

        if not connected or not (contacts or domains):
            access = None
        else:
            access = _valid_access_token(cur, conn, row)
        put_conn(conn); conn = None

        threads: list[dict] = []
        read_error = None
        if access:
            terms = [f"from:{a} OR to:{a}" for a in sorted(contacts)] + \
                    [f"from:{d} OR to:{d}" for d in sorted(domains)]
            query = f"({' OR '.join(terms)}) newer_than:{_INBOX_LOOKBACK}"
            try:
                found = email_oauth.list_threads(access, query, max_results=_INBOX_MAX_THREADS)
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=8) as pool:
                    heads = list(pool.map(lambda t: email_oauth.get_thread_headers(access, t["id"]), found))
                for t, msgs in zip(found, heads):
                    if msgs and _thread_involves_vendor(msgs, contacts, domains):
                        threads.append(_summarize_headers(t["id"], t["snippet"], msgs, me, contacts, domains))
            except Exception as e:
                traceback.print_exc()
                read_error = f"Couldn't read your mailbox: {e}"

        seen = {t["thread_id"] for t in threads}
        threads += [from_log(r) for tid, r in logged.items() if tid not in seen]
        threads.sort(key=lambda t: t["last_at"] or "", reverse=True)
        conn = get_conn()
        states = _thread_states(conn.cursor(cursor_factory=RealDictCursor), tenant_id, [t["thread_id"] for t in threads])
        put_conn(conn); conn = None
        threads = [_apply_status(t, states.get(t["thread_id"])) for t in threads]
        return {
            "me": me, "connected": connected, "read_available": bool(access) and not read_error,
            "error": read_error, "addresses": sorted(contacts), "domains": sorted(domains),
            "threads": threads,
        }
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load conversations")


def _vendor_thread(supplier_id: str, thread_id: str) -> tuple[str | None, list[dict], dict, set[str]]:
    """The signed-in user's copy of a thread, refused unless it is with this vendor."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()
        username = _caller(None)
        contacts, domains = _vendor_addresses(cur, tenant_id, supplier_id)
        row = _load_connection(cur, tenant_id, username)
        if not row or row["status"] != "active":
            raise HTTPException(status_code=409, detail="Connect your mailbox to read this conversation")
        access = _valid_access_token(cur, conn, row)
        me = row["email"]
    finally:
        put_conn(conn)
    try:
        msgs = email_oauth.get_thread(access, thread_id)
    except Exception as e:
        raise HTTPException(status_code=409, detail=f"Reconnect your mailbox to read this conversation: {e}")
    if not _thread_involves_vendor(msgs, contacts, domains):
        raise HTTPException(status_code=404, detail="Conversation not found for this vendor")
    return me, msgs, contacts, domains


@router.get("/api/vendors/{supplier_id}/inbox/thread")
def vendor_inbox_thread(supplier_id: str, thread_id: str = Query(...)):
    """One conversation with the vendor, oldest first, with attachments."""
    try:
        me, msgs, contacts, domains = _vendor_thread(supplier_id, thread_id)
        me_l = (me or "").lower()
        return {"me": me, "messages": [{
            "id": m["id"],
            "outbound": bool(me_l) and me_l in (m["from"] or "").lower(),
            "from": m["from"], "to": m.get("to", ""), "cc": m.get("cc", ""), "date": m["date"],
            "internal_date": m.get("internal_date"), "subject": m["subject"], "body": m["body"],
            "attachments": m.get("attachments", []),
        } for m in msgs]}
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to load conversation")


@router.post("/api/vendors/{supplier_id}/inbox/thread/summarize")
def vendor_inbox_summarize(supplier_id: str, thread_id: str = Query(...)):
    """AI summary and suggested next steps for one conversation."""
    try:
        me, msgs, _, _ = _vendor_thread(supplier_id, thread_id)
        if not msgs:
            return {"summary": "No messages to summarize.", "actions": []}
        return _summarize_thread(msgs, me)
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to summarize conversation")


@router.get("/api/vendors/{supplier_id}/inbox/attachment")
def vendor_inbox_attachment(supplier_id: str, thread_id: str = Query(...), message_id: str = Query(...),
                            attachment_id: str = Query(...), filename: str = Query(default="attachment")):
    """Download an attachment from a conversation with this vendor."""
    try:
        _, msgs, _, _ = _vendor_thread(supplier_id, thread_id)
        if not any(m["id"] == message_id for m in msgs):
            raise HTTPException(status_code=404, detail="Attachment not found")
        conn = get_conn()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            row = _load_connection(cur, require_tenant(), _caller(None))
            access = _valid_access_token(cur, conn, row)
        finally:
            put_conn(conn)
        data = email_oauth.get_attachment(access, message_id, attachment_id)
        safe = filename.replace('"', "").replace("\n", "")
        return Response(content=data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{safe}"'})
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to download attachment")



# ── Vendor inbox: act on a conversation without leaving the card ─────────────

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def _address_list(raw: str | None) -> list[str]:
    """'a@x.com, B <b@y.com>' -> ['a@x.com', 'b@y.com'], or 422 on a bad one."""
    out: list[str] = []
    for _, addr in getaddresses([raw or ""]):
        a = (addr or "").strip().lower()
        if not a:
            continue
        if not _EMAIL_RE.match(a):
            raise HTTPException(status_code=422, detail=f"“{a}” isn’t an email address")
        if a not in out:
            out.append(a)
    return out


@router.post("/api/vendors/{supplier_id}/inbox/send")
async def vendor_inbox_send(
    supplier_id: str,
    to: str = Form(...),
    body: str = Form(...),
    subject: str = Form(default=""),
    cc: str | None = Form(default=None),
    thread_id: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
):
    """Send from the user's mailbox: a new email, a reply, reply all or a forward.

    Unlike the per-contact route, recipients are whoever the user picks (the
    vendor's people, someone new at the vendor, a colleague for a forward).
    It's their own mailbox, as it would be in Gmail; what this adds is the
    record against the vendor.
    """
    if not (body or "").strip():
        raise HTTPException(status_code=400, detail="Write a message first")
    to_list, cc_list = _address_list(to), _address_list(cc)
    if not to_list:
        raise HTTPException(status_code=422, detail="Add at least one recipient")
    attachments: list[tuple[str, bytes, str]] = []
    for f in files or []:
        if not f or not f.filename:
            continue
        data = await f.read()
        enforce_pdf_page_limit(data, f.filename)
        attachments.append((f.filename, data, f.content_type or "application/octet-stream"))

    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()
        username = _caller(None)
        row = _load_connection(cur, tenant_id, username)
        if not row or row["status"] != "active":
            raise HTTPException(status_code=409, detail="Connect your mailbox before sending")
        contacts, _ = _vendor_addresses(cur, tenant_id, supplier_id)
        contact_id = next((contacts[a]["id"] for a in to_list + cc_list if (contacts.get(a) or {}).get("id")), None)
        result = _send_and_log(
            cur, conn, row=row, tenant_id=tenant_id, supplier_id=supplier_id, contact_id=contact_id,
            username=username, to_email=", ".join(to_list), subject=subject, message=body,
            cc=", ".join(cc_list) or None, thread_id=thread_id, attachments=attachments or None,
        )
        put_conn(conn)
        return {**result, "to": to_list, "cc": cc_list}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to send email")


class ThreadStateBody(BaseModel):
    thread_id: str
    status: str  # 'resolved' | 'open'


@router.post("/api/vendors/{supplier_id}/inbox/thread/state")
def vendor_inbox_thread_state(supplier_id: str, body: ThreadStateBody):
    """Mark a conversation done, or open it again. A new message reopens it anyway."""
    if body.status not in ("resolved", "open"):
        raise HTTPException(status_code=422, detail="status must be 'resolved' or 'open'")
    me, msgs, contacts, domains = _vendor_thread(supplier_id, body.thread_id)  # refuses others' threads
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO comm_thread_state (tenant_id, thread_id, supplier_id, status, last_message_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, thread_id) DO UPDATE SET
                status = EXCLUDED.status,
                supplier_id = COALESCE(comm_thread_state.supplier_id, EXCLUDED.supplier_id),
                updated_at = now()
        """, (require_tenant(), body.thread_id, supplier_id, body.status,
              _iso_ms(msgs[-1]["internal_date"]) if msgs else None))
        conn.commit()
    finally:
        put_conn(conn)
    return {"thread_id": body.thread_id, "status": "done" if body.status == "resolved" else "open"}


_DRAFT_SYSTEM = """You write emails for an accounts-payable team to one of its vendors.
Write the body of the reply only: no subject line, no placeholders like [Name].
Plain, polite, short (under 120 words). When "Bills this email is about" are
given, the email is about those bills: name each by its number and quote
amounts, due dates, status and payment dates exactly as given (if they are
attached, say so). Otherwise use the vendor's open bills when the email is about
payments. Never invent a payment date or promise one that isn't in the facts.
Sign off with the sender's name. Respond as JSON: {"body": "<email text>"}."""


class DraftBody(BaseModel):
    thread_id: str | None = None
    intent: str | None = None      # what the user wants to say, in their words
    signer: str | None = None
    # Bills the email is about: attached to it, or mentioned in the thread.
    bill_ids: list[str] = []
    attached_bill_ids: list[str] = []


def _bills_by_id(cur, tenant_id: str, supplier_id: str, invoice_ids: list[str]) -> list[dict]:
    """The facts an email about these bills needs, payment date included."""
    if not invoice_ids:
        return []
    cur.execute("""
        SELECT fi.invoice_id::text AS invoice_id, fi.invoice_number, fi.invoice_date, fi.due_date,
               fi.total_amount, fi.status, fi.payment_status, fi.amount_paid, fi.outstanding_amount,
               pay_log.created_at AS paid_on
        FROM fact_invoices fi
        LEFT JOIN LATERAL (
            SELECT created_at FROM bill_approval_chain
            WHERE invoice_id = fi.invoice_id AND step = 99
            ORDER BY created_at DESC LIMIT 1
        ) pay_log ON true
        WHERE fi.tenant_id = %s AND fi.supplier_id = %s AND fi.invoice_id::text = ANY(%s)
        LIMIT 20
    """, (tenant_id, supplier_id, invoice_ids[:20]))
    return [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in cur.fetchall()]


@router.post("/api/vendors/{supplier_id}/inbox/draft")
def vendor_inbox_draft(supplier_id: str, body: DraftBody):
    """An AI-written reply (or new email) for the user to edit before sending."""
    import json as _json
    from ..config import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="AI drafting isn’t set up on this server")
    tenant_id = require_tenant()
    transcript, me = "", None
    if body.thread_id:
        me, msgs, _, _ = _vendor_thread(supplier_id, body.thread_id)
        me_l = (me or "").lower()
        transcript = "\n\n---\n\n".join(
            f"[{'You' if me_l and me_l in (m.get('from') or '').lower() else m.get('from')}] {m.get('body', '')}"
            for m in msgs[-6:])[:7000]
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT name FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant_id))
        vendor = (cur.fetchone() or {}).get("name") or "the vendor"
        cur.execute("""
            SELECT invoice_number, invoice_date, due_date, total_amount, status, payment_status, outstanding_amount
            FROM fact_invoices
            WHERE supplier_id=%s AND tenant_id=%s AND COALESCE(soft_status,'active')='active'
              AND COALESCE(payment_status,'unpaid') <> 'paid'
            ORDER BY due_date NULLS LAST LIMIT 10
        """, (supplier_id, tenant_id))
        bills = [{k: (str(v) if v is not None else None) for k, v in r.items()} for r in cur.fetchall()]
        about = _bills_by_id(cur, tenant_id, supplier_id, list(dict.fromkeys(body.attached_bill_ids + body.bill_ids)))
        attached = set(body.attached_bill_ids)
        for b in about:
            b["attached_to_this_email"] = b["invoice_id"] in attached
    except Exception:
        conn.rollback()
        bills, about = [], []
    finally:
        put_conn(conn)
    prompt = (f"Vendor: {vendor}\nSender: {body.signer or 'the AP team'}\n"
              + (f"Bills this email is about: {_json.dumps(about)}\n" if about else "")
              + f"Open bills: {_json.dumps(bills)}\n"
              f"What to say: {body.intent or 'Reply helpfully to the latest message.'}\n\n"
              f"Conversation so far:\n{transcript or '(new email)'}")
    try:
        from openai import OpenAI
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini", temperature=0.3, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _DRAFT_SYSTEM}, {"role": "user", "content": prompt}],
        )
        text = (_json.loads(resp.choices[0].message.content or "{}").get("body") or "").strip()
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=502, detail="Couldn’t write a draft just now. Try again.")
    return {"body": text}


class SaveAttachmentBody(BaseModel):
    thread_id: str
    message_id: str
    attachment_id: str
    filename: str
    doc_type: str = "other"
    doc_label: str | None = None
    brand_id: str | None = None


@router.post("/api/vendors/{supplier_id}/inbox/attachment/save")
def vendor_inbox_save_attachment(supplier_id: str, body: SaveAttachmentBody):
    """File an email attachment into the vendor's Documents, typed like an upload."""
    from .vendor_documents import _clean_type, store_vendor_document
    doc_type, doc_label = _clean_type(body.doc_type, body.doc_label)
    _, msgs, _, _ = _vendor_thread(supplier_id, body.thread_id)
    if not any(m["id"] == body.message_id for m in msgs):
        raise HTTPException(status_code=404, detail="Attachment not found")
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()
        username = _caller(None)
        access = _valid_access_token(cur, conn, _load_connection(cur, tenant_id, username))
    finally:
        put_conn(conn)
    data = email_oauth.get_attachment(access, body.message_id, body.attachment_id)
    name = body.filename.replace("/", "_").replace("\\", "_")[:200] or "attachment"
    doc_id = store_vendor_document(tenant_id, supplier_id, body.brand_id, name, data,
                                   f"{username} (from email)", doc_type, doc_label)
    return {"id": doc_id, "file_name": name, "doc_type": doc_type, "doc_label": doc_label}
