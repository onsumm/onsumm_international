"""Minting and hashing of access and refresh tokens.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import hashlib
import secrets
import datetime
import jwt
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn

# Company display names come from dim_tenants. This used to be a literal dict of
# four tenant UUIDs, so a tenant we onboarded — or any tenant not in the dict — got
# an empty company_name in its token and a blank company in the UI.
_COMPANY_NAME_CACHE: dict[str, tuple[float, str]] = {}

_COMPANY_NAME_TTL = 300.0

def _company_name(tenant_id: str) -> str:
    import time
    key = str(tenant_id)
    hit = _COMPANY_NAME_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _COMPANY_NAME_TTL:
        return hit[1]
    name = ""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT company_name FROM dim_tenants WHERE tenant_id = %s", (key,)
                )
                row = cur.fetchone()
                name = (row[0] or "") if row else ""
        finally:
            put_conn(conn)
    except Exception as exc:
        import logging
        logging.warning("[auth] company name lookup failed for %s: %s", key, exc)
        return name
    _COMPANY_NAME_CACHE[key] = (time.time(), name)
    return name

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def _build_access_token(username, role, name, email, company_id, department):
    company_id_str = str(company_id)
    payload = {
        "sub": username,
        "role": role,
        "name": name,
        "email": email,
        "company_id": company_id_str,
        "company_name": _company_name(company_id_str),
        "department": department,
        "iat": datetime.datetime.utcnow(),
        "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_TTL_MIN),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256"), payload

def _issue_refresh_token(cur, username: str, company_id) -> str:
    raw = secrets.token_urlsafe(48)
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=REFRESH_TOKEN_TTL_DAYS)
    cur.execute(
        "INSERT INTO auth_refresh_tokens (token_hash, username, company_id, expires_at) VALUES (%s, %s, %s, %s)",
        (_hash_token(raw), username, str(company_id), expires),
    )
    return raw
