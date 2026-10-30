"""Tenant user administration.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import bcrypt
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..security.tenancy import Tenant, assert_owned, owned_attachment, owned_bill, owned_vendor

from fastapi import APIRouter

router = APIRouter()

@router.get("/api/users")
def list_users(company_id: str = Query(default=None), department: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if department:
            cur.execute(
                "SELECT username, name, role, email, department FROM users WHERE company_id=%s AND department=%s ORDER BY name",
                (tenant, department)
            )
        else:
            cur.execute(
                "SELECT username, name, role, email, department FROM users WHERE company_id=%s ORDER BY name",
                (tenant,)
            )
        rows = cur.fetchall()
    put_conn(conn)
    return {"users": [dict(r) for r in rows]}

class UserCreateRequest(BaseModel):
    username: str
    password: str
    role: str
    name: str
    email: str
    department: str | None = None
    company_id: str | None = None

class UserUpdateRequest(BaseModel):
    role: str | None = None
    name: str | None = None
    email: str | None = None
    department: str | None = None
    password: str | None = None

@router.post("/api/users")
def create_user(body: UserCreateRequest):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    # Creating a user is privilege granting — without this, any member could mint
    # themselves an admin account inside their own tenant.
    require_role("admin")
    hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (username, password_hash, role, name, email, company_id, department)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (body.username, hashed, body.role, body.name, body.email, tenant, body.department)
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)
    return {"ok": True}

@router.put("/api/users/{username}")
def update_user(username: str, body: UserUpdateRequest, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    # Editing a user can change their role and reset their password.
    require_role("admin")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if body.role is not None:
                cur.execute("UPDATE users SET role=%s WHERE username=%s AND company_id=%s", (body.role, username, tenant))
            if body.name is not None:
                cur.execute("UPDATE users SET name=%s WHERE username=%s AND company_id=%s", (body.name, username, tenant))
            if body.email is not None:
                cur.execute("UPDATE users SET email=%s WHERE username=%s AND company_id=%s", (body.email, username, tenant))
            if body.department is not None:
                cur.execute("UPDATE users SET department=%s WHERE username=%s AND company_id=%s", (body.department, username, tenant))
            if body.password is not None:
                hashed = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
                cur.execute("UPDATE users SET password_hash=%s WHERE username=%s AND company_id=%s", (hashed, username, tenant))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)
    return {"ok": True}

@router.delete("/api/users/{username}")
def delete_user(username: str, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    require_role("admin")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Deleting the tenant's last admin would lock the whole company out of
            # Settings, user management and approvals, recoverable only by us in psql.
            cur.execute(
                "SELECT role FROM users WHERE username=%s AND company_id=%s",
                (username, tenant),
            )
            row = cur.fetchone()
            if row and row[0] == "admin":
                cur.execute(
                    "SELECT count(*) FROM users WHERE company_id=%s AND role='admin'",
                    (tenant,),
                )
                if cur.fetchone()[0] <= 1:
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot delete the last administrator for this company",
                    )
            cur.execute("DELETE FROM users WHERE username=%s AND company_id=%s", (username, tenant))
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return {"ok": True}
