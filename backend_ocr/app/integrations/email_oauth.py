"""
Gmail OAuth adapter for user-sent email.

A logged-in platform user connects their own Gmail mailbox once (OAuth
Authorization Code flow). We store their access/refresh tokens (encrypted at
rest) and later send messages *as that user* via the Gmail API — send-only
scope, no inbox access.

Provider-agnostic surface so a Microsoft/Graph adapter can slot in later:
    build_authorize_url(state)       -> str
    exchange_code(code)              -> dict(access, refresh, expires_at, email, scopes)
    refresh_access_token(refresh)    -> dict(access, expires_at)
    send_email(access, from, to, subject, body) -> message_id

State handling and token encryption live here too, since they're shared by the
router.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Any

import httpx
import jwt

from .. import config
from . import email_graph

# Provider dispatch. Gmail stays implemented inline in this module (it was here
# first and every call site imports it from here); anything else delegates to a
# sibling adapter exposing the same four functions.
_ADAPTERS = {"microsoft": email_graph}


def _adapter(provider: str | None):
    """Adapter module for a provider, or None when it's Gmail (inline below)."""
    key = (provider or "google").lower()
    if key not in ("google", *_ADAPTERS):
        raise ValueError(f"Unknown email provider: {provider!r}")
    return _ADAPTERS.get(key)


# ── Google endpoints / scope ─────────────────────────────────────────────────
GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL   = "https://oauth2.googleapis.com/revoke"
GMAIL_SEND_URL      = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_THREAD_URL    = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
GMAIL_MESSAGES_URL  = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_SCOPE         = "https://www.googleapis.com/auth/gmail.send"
# read scope so we can display the reply thread as a conversation
GMAIL_READ_SCOPE    = "https://www.googleapis.com/auth/gmail.readonly"
# userinfo email so we can record which mailbox was connected
USERINFO_SCOPE      = "https://www.googleapis.com/auth/userinfo.email"
USERINFO_URL        = "https://www.googleapis.com/oauth2/v2/userinfo"
# Sign-in without mailbox access — Workspace admins restrict the gmail.* scopes
# the same way Entra restricts Mail.*, so SSO needs a minimal option here too.
GOOGLE_LOGIN_ONLY_SCOPE = "openid email profile"

_STATE_ALGO = "HS256"
_STATE_TTL_SECONDS = 600  # OAuth round-trip must complete within 10 minutes


# ── Token encryption (Fernet) ────────────────────────────────────────────────

def _fernet():
    from cryptography.fernet import Fernet
    key = config.EMAIL_TOKEN_KEY
    if not key:
        raise RuntimeError("EMAIL_TOKEN_KEY is not set — cannot encrypt OAuth tokens")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    return _fernet().decrypt(ciphertext.encode()).decode()


# ── OAuth state (signed, expiring) ───────────────────────────────────────────

def sign_state(tenant_id: str, username: str, provider: str = "google",
               purpose: str = "connect") -> str:
    """Signed state carrying who is connecting, to verify on callback.

    Both providers *and* both flows share one callback URL, so the state carries
    everything the callback needs to route: which provider's token endpoint to
    exchange against, and whether this round-trip is a mailbox connect (we
    already know the user) or a sign-in (we don't know them yet — identity comes
    from the provider, and tenant_id/username are empty).
    """
    payload = {
        "t": tenant_id,
        "u": username,
        "p": provider,
        "k": purpose,
        "iat": int(time.time()),
        "exp": int(time.time()) + _STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=_STATE_ALGO)


def verify_state(state: str) -> dict[str, str]:
    """Return {tenant_id, username, provider, purpose} or raise if invalid/expired."""
    data = jwt.decode(state, config.JWT_SECRET, algorithms=[_STATE_ALGO])
    # States signed before Graph/SSO support carry neither claim; they're all
    # Gmail mailbox connects.
    return {"tenant_id": data["t"], "username": data["u"],
            "provider": data.get("p", "google"),
            "purpose": data.get("k", "connect")}


# ── Authorize / token exchange / refresh ─────────────────────────────────────

def build_authorize_url(state: str, provider: str = "google",
                        login_only: bool = False) -> str:
    adapter = _adapter(provider)
    if adapter:
        return adapter.build_authorize_url(state, login_only=login_only)
    from urllib.parse import urlencode
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": GOOGLE_LOGIN_ONLY_SCOPE if login_only
                 else f"{GMAIL_SCOPE} {GMAIL_READ_SCOPE} {USERINFO_SCOPE}",
        "access_type": "offline",       # request a refresh token
        "prompt": "consent",            # ensure refresh token is returned
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _expires_at(expires_in: int | None) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=(expires_in or 3600) - 60)


def exchange_code(code: str, provider: str = "google") -> dict[str, Any]:
    """Exchange an auth code for tokens + the connected mailbox address."""
    adapter = _adapter(provider)
    if adapter:
        return adapter.exchange_code(code)
    with httpx.Client(timeout=20) as client:
        resp = client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": config.OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        tok = resp.json()

        email = ""
        email_verified = False
        try:
            info = client.get(USERINFO_URL, headers={"Authorization": f"Bearer {tok['access_token']}"})
            if info.status_code == 200:
                data = info.json()
                email = data.get("email", "")
                # Google marks addresses it has actually proven the user owns.
                # SSO leans on this; mailbox-connect ignores it.
                email_verified = bool(data.get("verified_email", False))
        except Exception:
            pass

    return {
        "access": tok["access_token"],
        "refresh": tok.get("refresh_token"),
        "expires_at": _expires_at(tok.get("expires_in")),
        "email": email,
        "email_verified": email_verified,
        "scopes": tok.get("scope", ""),
    }


def refresh_access_token(refresh: str, provider: str = "google") -> dict[str, Any]:
    adapter = _adapter(provider)
    if adapter:
        return adapter.refresh_access_token(refresh)
    with httpx.Client(timeout=20) as client:
        resp = client.post(GOOGLE_TOKEN_URL, data={
            "refresh_token": refresh,
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        tok = resp.json()
    return {"access": tok["access_token"], "expires_at": _expires_at(tok.get("expires_in"))}


def revoke(token: str, provider: str = "google") -> None:
    """Best-effort revoke of a token at the provider (on disconnect)."""
    adapter = _adapter(provider)
    if adapter:
        return adapter.revoke(token)
    try:
        with httpx.Client(timeout=10) as client:
            client.post(GOOGLE_REVOKE_URL, data={"token": token},
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception:
        pass


# ── Send ─────────────────────────────────────────────────────────────────────

def build_message(from_email: str, to_email: str, subject: str, body: str,
                  cc: str | None = None, in_reply_to: str | None = None,
                  attachments: list[tuple[str, bytes, str]] | None = None,
                  html: str | None = None):
    """The MIME message `send_email` puts on the wire.

    Separate from the Gmail call itself so a sender that is not Gmail — the
    SMTP fallback the Payment Hub's receipt uses when the acting user has no
    mailbox connected (app/domain/payments/notify.py) — builds a byte-identical
    message instead of assembling its own slightly different one.

    Shape, in the order the standards want it:

      * body alone            -> text/plain
      * body + html           -> multipart/alternative, plain first
      * either + attachments  -> multipart/mixed wrapping the above

    `html` is the richer rendering of the same message, never a different one:
    clients pick the last part they can display, so a vendor on a text-only
    client reads `body` and everyone else reads `html`.
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.base import MIMEBase
    from email import encoders

    plain = MIMEText(body, "plain", "utf-8")
    if html:
        content = MIMEMultipart("alternative")
        content.attach(plain)
        # Last part wins in an alternative — the HTML has to come second.
        content.attach(MIMEText(html, "html", "utf-8"))
    else:
        content = plain

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(content)
        for filename, data, ctype in attachments:
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            part = MIMEBase(maintype or "application", subtype or "octet-stream")
            part.set_payload(data)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = content

    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    return msg


def send_email(access_token: str, from_email: str, to_email: str,
               subject: str, body: str, cc: str | None = None,
               thread_id: str | None = None, in_reply_to: str | None = None,
               attachments: list[tuple[str, bytes, str]] | None = None,
               html: str | None = None) -> dict[str, str]:
    """Send an email via the Gmail API.

    attachments: list of (filename, bytes, content_type). html: an HTML
    rendering of the same message, sent alongside `body` as the alternative a
    client should prefer. When thread_id / in_reply_to are given, it's threaded
    into the existing conversation. Returns {id, threadId}.
    """
    msg = build_message(from_email, to_email, subject, body, cc=cc,
                        in_reply_to=in_reply_to, attachments=attachments, html=html)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            GMAIL_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}",
                     "Content-Type": "application/json"},
            json=payload,
        )
        if resp.status_code >= 400:
            # Surface Google's actual error message (e.g. "Gmail API has not been
            # used in project … or it is disabled") instead of a generic failure.
            detail = resp.text
            try:
                detail = resp.json().get("error", {}).get("message", detail)
            except Exception:
                pass
            raise RuntimeError(f"Gmail API {resp.status_code}: {detail}")
        data = resp.json()
        return {"id": data.get("id", ""), "threadId": data.get("threadId", "")}


# ── Read a thread (conversation) ─────────────────────────────────────────────

def _decode_b64url(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", "replace")


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    import html as _html
    return _html.unescape(text).strip()


def _extract_body(payload: dict) -> str:
    """Best plain-text body from a Gmail message payload (recursing parts)."""
    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")
    if mime == "text/plain" and body_data:
        return _decode_b64url(body_data)
    parts = payload.get("parts", []) or []
    for p in parts:
        if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data"):
            return _decode_b64url(p["body"]["data"])
    for p in parts:
        got = _extract_body(p)
        if got:
            return got
    if mime == "text/html" and body_data:
        return _strip_html(_decode_b64url(body_data))
    return ""


def _extract_attachments(payload: dict) -> list[dict[str, Any]]:
    """Collect attachment parts (filename + Gmail attachmentId) from a payload."""
    out: list[dict[str, Any]] = []

    def walk(p: dict) -> None:
        fn = p.get("filename")
        body = p.get("body", {}) or {}
        if fn and body.get("attachmentId"):
            out.append({
                "filename": fn,
                "mime_type": p.get("mimeType", "application/octet-stream"),
                "size": body.get("size", 0),
                "attachment_id": body["attachmentId"],
            })
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return out


def get_attachment(access_token: str, message_id: str, attachment_id: str) -> bytes:
    """Fetch a single attachment's bytes from Gmail."""
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{attachment_id}"
    with httpx.Client(timeout=30) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
        data = resp.json().get("data", "")
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _clean_reply(text: str) -> str:
    """Trim the quoted trailer Gmail appends to replies, for a chat-like view."""
    import re
    text = re.split(r"\n[ \t]*On .+ wrote:\s*\n", text, maxsplit=1)[0]
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def _shape_message(m: dict) -> dict[str, Any]:
    """Normalise a raw Gmail message resource into our flat message dict."""
    headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
    raw_body = _extract_body(m.get("payload", {}))
    return {
        "id": m.get("id", ""),
        "thread_id": m.get("threadId", ""),
        "message_id": headers.get("message-id", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "subject": headers.get("subject", ""),
        "body": _clean_reply(raw_body) or m.get("snippet", ""),
        "attachments": _extract_attachments(m.get("payload", {})),
        "internal_date": int(m.get("internalDate", "0") or 0),
    }


def list_messages(access_token: str, query: str, max_results: int = 25) -> list[str]:
    """Return Gmail message ids matching a search query (newest first)."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            GMAIL_MESSAGES_URL,
            params={"q": query, "maxResults": max_results},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    return [m.get("id", "") for m in (data.get("messages") or []) if m.get("id")]


def list_threads(access_token: str, query: str, max_results: int = 30) -> list[dict[str, str]]:
    """Gmail threads matching a search query, newest first: [{id, snippet}].

    Follows nextPageToken until max_results threads are collected, so a busy
    vendor's older threads are not cut off at Gmail's page size.
    """
    out: list[dict[str, str]] = []
    page: str | None = None
    with httpx.Client(timeout=30) as client:
        while len(out) < max_results:
            params: dict[str, Any] = {"q": query, "maxResults": min(100, max_results - len(out))}
            if page:
                params["pageToken"] = page
            resp = client.get(GMAIL_THREAD_URL, params=params,
                              headers={"Authorization": f"Bearer {access_token}"})
            if resp.status_code >= 400:
                raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
            out += [{"id": t["id"], "snippet": t.get("snippet", "")}
                    for t in (data.get("threads") or []) if t.get("id")]
            page = data.get("nextPageToken")
            if not page:
                break
    return out[:max_results]


def get_thread_headers(access_token: str, thread_id: str) -> list[dict[str, Any]]:
    """A thread's messages, oldest first, headers only (no bodies).

    Cheap enough to call for every thread in a list: format=metadata skips the
    message bodies and attachments. Attachment presence comes from the
    message's top-level parts, which metadata still lists.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GMAIL_THREAD_URL}/{thread_id}",
            params=[("format", "metadata"), ("metadataHeaders", "From"), ("metadataHeaders", "To"),
                    ("metadataHeaders", "Cc"), ("metadataHeaders", "Subject"), ("metadataHeaders", "Date")],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    out: list[dict[str, Any]] = []
    for m in data.get("messages", []):
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        out.append({
            "id": m.get("id", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "snippet": m.get("snippet", ""),
            "internal_date": int(m.get("internalDate", "0") or 0),
            "has_attachment": "HAS_ATTACHMENT" in (m.get("labelIds") or [])
                              or any((p.get("filename") or "") for p in (m.get("payload", {}).get("parts") or [])),
        })
    out.sort(key=lambda x: x["internal_date"])
    return out


def get_message(access_token: str, message_id: str) -> dict[str, Any]:
    """Fetch and normalise a single Gmail message by id."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GMAIL_MESSAGES_URL}/{message_id}",
            params={"format": "full"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail API {resp.status_code}: {resp.text[:300]}")
        return _shape_message(resp.json())


def get_thread(access_token: str, thread_id: str) -> list[dict[str, Any]]:
    """Fetch a Gmail thread and return its messages, oldest first."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GMAIL_THREAD_URL}/{thread_id}",
            params={"format": "full"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("error", {}).get("message", detail)
            except Exception:
                pass
            raise RuntimeError(f"Gmail API {resp.status_code}: {detail}")
        data = resp.json()

    out: list[dict[str, Any]] = []
    for m in data.get("messages", []):
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        raw_body = _extract_body(m.get("payload", {}))
        out.append({
            "id": m.get("id", ""),
            "message_id": headers.get("message-id", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "body": _clean_reply(raw_body) or m.get("snippet", ""),
            "attachments": _extract_attachments(m.get("payload", {})),
            "internal_date": int(m.get("internalDate", "0") or 0),
        })
    out.sort(key=lambda x: x["internal_date"])
    return out
