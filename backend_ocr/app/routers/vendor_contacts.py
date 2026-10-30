"""Vendor contact people.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import traceback
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..core.vendor_activity_log import log_vendor_activity

from fastapi import APIRouter

router = APIRouter()

def _resolve_tenant_for_supplier(cur, supplier_id: str, company_id: str | None) -> str:
    """Authenticated tenant. Client company_id and the supplier row are ignored
    for the tenant value — the JWT is the sole source of truth."""
    return require_tenant()

def _get_vendor_contacts_list(cur, tenant_id: str, supplier_id: str):
    cur.execute("""
        SELECT id::text, name, role, email, phone, is_primary,
               COALESCE(agent_enabled, false) AS agent_enabled, created_at
        FROM supplier_contacts
        WHERE tenant_id=%s AND supplier_id=%s
        ORDER BY is_primary DESC, created_at ASC
    """, (tenant_id, supplier_id))
    return [dict(r) for r in cur.fetchall()]

@router.get("/api/vendors/{supplier_id}/contacts")
async def list_vendor_contacts(supplier_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
        row = cur.fetchone()
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            put_conn(conn)
            return []
        contacts = _get_vendor_contacts_list(cur, tenant_id, supplier_id)
        put_conn(conn)
        return contacts
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch contacts")

class ContactBody(BaseModel):
    name: str
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    is_primary: bool = False
    company_id: str | None = None

@router.post("/api/vendors/{supplier_id}/contacts")
async def add_vendor_contact(supplier_id: str, body: ContactBody):
    conn = None
    try:
        name = (body.name or "").strip()
        role = (body.role or "").strip()
        email = (body.email or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Contact name is required")
        if not role:
            raise HTTPException(status_code=400, detail="Contact role is required")
        if not email:
            raise HTTPException(status_code=400, detail="Contact email is required")
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant_for_supplier(cur, supplier_id, body.company_id)
        if body.is_primary:
            cur.execute("UPDATE supplier_contacts SET is_primary=false WHERE tenant_id=%s AND supplier_id=%s",
                        (tenant_id, supplier_id))
        cur.execute("""
            INSERT INTO supplier_contacts (tenant_id, supplier_id, name, role, email, phone, is_primary)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id::text, name, role, email, phone, is_primary, created_at
        """, (tenant_id, supplier_id, name, role, email,
              (body.phone or "").strip() or None, body.is_primary))
        result = dict(cur.fetchone())
        log_vendor_activity(
            cur, supplier_id, "contact_added",
            f"Added contact {name} ({role}, {email})" + (" as primary" if body.is_primary else ""),
            meta={"entity_id": result["id"], "is_primary": body.is_primary}, tenant_id=tenant_id)
        conn.commit()
        put_conn(conn)
        return result
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to add contact")

@router.delete("/api/vendors/{supplier_id}/contacts/{contact_id}")
async def delete_vendor_contact(supplier_id: str, contact_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant_for_supplier(cur, supplier_id, company_id or None)
        cur.execute("SELECT id, name, email FROM supplier_contacts WHERE id=%s AND tenant_id=%s AND supplier_id=%s",
                    (contact_id, tenant_id, supplier_id))
        gone = cur.fetchone()
        if not gone:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Contact not found")
        cur.execute("DELETE FROM supplier_contacts WHERE id=%s", (contact_id,))
        who = gone.get("name") or gone.get("email") or "a contact"
        log_vendor_activity(
            cur, supplier_id, "contact_deleted",
            f"Removed contact {who}" + (f" ({gone['email']})" if gone.get("email") and gone.get("name") else ""),
            meta={"entity_id": contact_id}, tenant_id=tenant_id)
        conn.commit()
        put_conn(conn)
        return {"deleted": True}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to delete contact")

@router.post("/api/vendors/{supplier_id}/contacts/{contact_id}/set-primary")
async def set_primary_contact(supplier_id: str, contact_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = _resolve_tenant_for_supplier(cur, supplier_id, company_id or None)
        cur.execute("UPDATE supplier_contacts SET is_primary=false WHERE tenant_id=%s AND supplier_id=%s",
                    (tenant_id, supplier_id))
        cur.execute("UPDATE supplier_contacts SET is_primary=true WHERE id=%s AND tenant_id=%s AND supplier_id=%s"
                    " RETURNING name, email",
                    (contact_id, tenant_id, supplier_id))
        made_primary = cur.fetchone()
        if made_primary:
            log_vendor_activity(
                cur, supplier_id, "contact_primary_changed",
                f"Made {made_primary.get('name') or made_primary.get('email') or 'a contact'} the primary contact",
                meta={"entity_id": contact_id}, tenant_id=tenant_id)
        conn.commit()
        contacts = _get_vendor_contacts_list(cur, tenant_id, supplier_id)
        put_conn(conn)
        return contacts
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to set primary contact")
