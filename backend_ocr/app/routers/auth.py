"""Login, token refresh/logout, and the password-reset OTP flow.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import datetime
import bcrypt
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
import time as _time
import random as _random
import time as _time

from fastapi import APIRouter

router = APIRouter()

from ..core.tokens import _build_access_token, _hash_token, _issue_refresh_token

class LoginRequest(BaseModel):
    username: str
    password: str

@router.post("/api/login")
def login(body: LoginRequest):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, password_hash, role, name, email, company_id, department FROM users WHERE username = %s",
            (body.username,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        username, password_hash, role, name, email, company_id, department = row
        if not bcrypt.checkpw(body.password.encode(), password_hash.encode()):
            raise HTTPException(status_code=401, detail="Invalid username or password")

        token, payload = _build_access_token(username, role, name, email, company_id, department)
        refresh = _issue_refresh_token(cur, username, company_id)
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return {
        "token": token,
        "refresh_token": refresh,
        "expires_in": ACCESS_TOKEN_TTL_MIN * 60,
        "user": {k: v for k, v in payload.items() if k not in ("exp", "iat")},
    }

class RefreshRequest(BaseModel):
    refresh_token: str

@router.post("/api/auth/refresh")
def refresh_token_endpoint(body: RefreshRequest):
    """Exchange a valid refresh token for a new access token (+ rotated refresh
    token). Public: the expired access token is irrelevant here — the refresh
    token is the credential."""
    raw = (body.refresh_token or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="refresh_token is required")
    token_hash = _hash_token(raw)

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, company_id, expires_at, revoked FROM auth_refresh_tokens WHERE token_hash = %s",
            (token_hash,),
        )
        rec = cur.fetchone()
        if not rec:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        rec_id, username, company_id, expires_at, revoked = rec

        # Reuse-detection: a revoked token being presented again means it was
        # captured — revoke the whole family and force re-login.
        if revoked:
            cur.execute(
                "UPDATE auth_refresh_tokens SET revoked = true, revoked_at = now() WHERE username = %s AND revoked = false",
                (username,),
            )
            conn.commit()
            raise HTTPException(status_code=401, detail="Refresh token reuse detected")

        now = datetime.datetime.now(expires_at.tzinfo) if expires_at.tzinfo else datetime.datetime.utcnow()
        if expires_at < now:
            raise HTTPException(status_code=401, detail="Refresh token expired")

        # Re-read the user so role/name/email changes propagate on refresh.
        cur.execute(
            "SELECT username, role, name, email, company_id, department FROM users WHERE username = %s AND company_id = %s",
            (username, str(company_id)),
        )
        urow = cur.fetchone()
        if not urow:
            raise HTTPException(status_code=401, detail="User no longer exists")

        token, payload = _build_access_token(*urow)
        new_refresh = _issue_refresh_token(cur, username, company_id)
        cur.execute(
            "UPDATE auth_refresh_tokens SET revoked = true, revoked_at = now(), replaced_by = %s WHERE id = %s",
            (_hash_token(new_refresh), rec_id),
        )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return {
        "token": token,
        "refresh_token": new_refresh,
        "expires_in": ACCESS_TOKEN_TTL_MIN * 60,
        "user": {k: v for k, v in payload.items() if k not in ("exp", "iat")},
    }

@router.post("/api/auth/logout")
def logout(body: RefreshRequest):
    """Revoke the presented refresh token (best-effort). The access token simply
    expires on its own."""
    raw = (body.refresh_token or "").strip()
    if raw:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE auth_refresh_tokens SET revoked = true, revoked_at = now() WHERE token_hash = %s",
                    (_hash_token(raw),),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            put_conn(conn)
    return {"ok": True}

_otp_store: dict = {}  # username → {otp, expires_at}

@router.post("/api/auth/forgot-password")
def forgot_password(body: dict):
    username = (body.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
    finally:
        put_conn(conn)

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="User not found or no email on file")

    email = row[0]
    otp = str(_random.randint(100000, 999999))
    _otp_store[username] = {"otp": otp, "expires_at": _time.monotonic() + 600}  # 10 min

    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:480px;margin:auto;color:#333;">
      <h2 style="color:#004080;">Password Reset OTP</h2>
      <p>You requested a password reset for your Onsumm account.</p>
      <div style="font-size:36px;font-weight:bold;letter-spacing:10px;color:#004080;
                  background:#eff4ff;padding:20px;text-align:center;border-radius:8px;margin:24px 0;">
        {otp}
      </div>
      <p>This OTP is valid for <b>10 minutes</b>. Do not share it with anyone.</p>
      <p style="font-size:12px;color:#888;">If you did not request this, ignore this email.</p>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Onsumm Password Reset OTP"
    msg["From"] = SMTP_USER
    msg["To"] = email
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [email], msg.as_string())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send OTP email: {e}")

    masked = email[:2] + "***@" + email.split("@")[-1]
    return {"message": f"OTP sent to {masked}"}

@router.post("/api/auth/verify-otp")
def verify_otp(body: dict):
    username = (body.get("username") or "").strip()
    otp = (body.get("otp") or "").strip()
    entry = _otp_store.get(username)
    if not entry or entry["otp"] != otp or _time.monotonic() > entry["expires_at"]:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return {"valid": True}

@router.post("/api/auth/reset-password")
def reset_password(body: dict):
    username = (body.get("username") or "").strip()
    otp = (body.get("otp") or "").strip()
    new_password = body.get("new_password") or ""

    entry = _otp_store.get(username)
    if not entry or entry["otp"] != otp or _time.monotonic() > entry["expires_at"]:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash=%s WHERE username=%s", (hashed, username))
        conn.commit()
    finally:
        put_conn(conn)

    del _otp_store[username]
    return {"message": "Password updated successfully"}
