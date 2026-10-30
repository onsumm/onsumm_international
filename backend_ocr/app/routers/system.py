"""Health check, notification counts, and the Tally bridge.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import secrets
import datetime
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

router = APIRouter()

from ..core.approval_chain import _build_status_role_map
from ..core.notifications import _NOTIF_TTL, _notif_cache

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/api/notifications/count")
def notification_count(role: str, company_id: str = Query(default=None)):
    import time as _t
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    _key = f"{tenant}:{role}"
    _cached = _notif_cache.get(_key)
    if _cached and _t.monotonic() < _cached[1]:
        return {"count": _cached[0]}

    if role == "member":
        statuses = ["NEEDS_REVISION"]
    else:
        role_map = _build_status_role_map(tenant)
        statuses = [s for s, roles in role_map.items() if role in roles]
    if not statuses:
        _notif_cache[_key] = (0, _t.monotonic() + _NOTIF_TTL)
        return {"count": 0}
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            ph = ",".join(["%s"] * len(statuses))
            cur.execute(
                f"SELECT COUNT(*) FROM fact_invoices WHERE tenant_id=%s AND status IN ({ph})",
                [tenant] + statuses,
            )
            count = int(cur.fetchone()[0])
        put_conn(conn)
        _notif_cache[_key] = (count, _t.monotonic() + _NOTIF_TTL)
        return {"count": count}
    except Exception as e:
        if conn: put_conn(conn, close=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/tally/generate-token")
def generate_tally_pairing_token():
    """
    Generate a one-time pairing token for a tenant.
    Called from the dashboard when user clicks "Connect Tally".
    Returns a token the user pastes into the desktop agent.
    Token expires in 30 minutes.
    """
    import secrets
    from datetime import datetime, timedelta, timezone

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    token   = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO int_connections
                    (connection_id, tenant_id, source, tally_mode, status,
                     agent_pairing_token, agent_pairing_expires_at, sync_config_jsonb)
                VALUES (gen_random_uuid(), %s, 'TALLY', 'XML_SOCKET', 'PENDING',
                        %s, %s, '{}')
                ON CONFLICT (tenant_id, source) DO UPDATE
                  SET agent_pairing_token       = EXCLUDED.agent_pairing_token,
                      agent_pairing_expires_at  = EXCLUDED.agent_pairing_expires_at,
                      status                    = 'PENDING'
                """,
                (tenant, token, expires),
            )
        conn.commit()
    finally:
        put_conn(conn)

    return {
        "token":      token,
        "expires_at": expires.isoformat(),
        "instructions": (
            "Download the Onsumm Tally Agent, run it, and paste this token when prompted. "
            "Token expires in 30 minutes."
        ),
    }
