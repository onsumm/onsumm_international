"""Files attached to a bill, and the edit history of spreadsheet attachments.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from ..integrations.ocr import enforce_pdf_page_limit
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..security.tenancy import Tenant, assert_owned, owned_attachment, owned_bill, owned_vendor
import uuid as _uuid

from fastapi import APIRouter

router = APIRouter()

from ..core import object_store

@router.get("/api/bills/{invoice_id}/attachments")
def list_attachments(invoice_id: str, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id::text, file_name, file_type, file_size, uploaded_by, uploaded_at
               FROM bill_attachments
               WHERE invoice_id=%s AND tenant_id=%s AND deleted_at IS NULL
               ORDER BY uploaded_at DESC""",
            (invoice_id, tenant)
        )
        rows = [dict(r) for r in cur.fetchall()]
    put_conn(conn)
    return {"attachments": rows}

@router.post("/api/bills/{invoice_id}/attachments")
async def upload_attachment(
    invoice_id: str,
    request: Request,
):
    import psycopg2, uuid as _uuid

    async with request.form() as form:
        file: UploadFile = form.get("file")
        uploaded_by: str = form.get("uploaded_by", "")
        company_id: str = form.get("company_id", "")
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        safe_name = Path(file.filename).name
        content_type = file.content_type
        file_bytes = await file.read()
    enforce_pdf_page_limit(file_bytes, safe_name)

    tenant = require_tenant()  # tenant from JWT; client company_id ignored

    unique_name = f"{_uuid.uuid4().hex[:8]}_{safe_name}"
    dest = object_store.save(object_store.key("bill-attachments", tenant, invoice_id, unique_name),
                             file_bytes, content_type)
    file_size = len(file_bytes)

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO bill_attachments (invoice_id, tenant_id, file_path, file_name, file_type, file_size, uploaded_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, uploaded_at""",
                (invoice_id, tenant, dest, safe_name, content_type, file_size, uploaded_by or None),
            )
            row = cur.fetchone()
        conn.commit()
        put_conn(conn)
        return {
            "id":          str(row[0]),
            "file_name":   safe_name,
            "file_type":   content_type,
            "file_size":   file_size,
            "uploaded_by": uploaded_by or None,
            "uploaded_at": row[1].isoformat(),
        }
    except Exception as e:
        object_store.drop_local_copy(dest)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/bills/{invoice_id}/attachments/{attachment_id}/download")
def download_attachment(invoice_id: str, attachment_id: str, preview: bool = Query(default=False),
                        signed: bool = Query(default=False)):
    tenant = require_tenant()
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT file_path, file_name, file_type FROM bill_attachments WHERE id=%s AND invoice_id=%s AND tenant_id=%s AND deleted_at IS NULL",
                (attachment_id, invoice_id, tenant),
            )
            row = cur.fetchone()
        put_conn(conn)
        conn = None
        if not row:
            raise HTTPException(status_code=404, detail="Attachment not found")
        file_path, file_name, file_type = row
        if signed:
            link = object_store.signed_link_response(file_path)
            if link:
                return link
        mime = file_type or "application/octet-stream"
        disposition = "inline" if preview else f'attachment; filename="{file_name}"'
        from fastapi.responses import Response
        content = object_store.load(file_path)
        if content is None:
            raise HTTPException(status_code=404, detail="File missing")
        return Response(
            content=content,
            media_type=mime,
            headers={"Content-Disposition": disposition},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/bills/{invoice_id}/attachments/{attachment_id}")
async def update_attachment(invoice_id: str, attachment_id: str, request: Request):
    """Replace an Excel/CSV attachment with an edited version, recording the diff."""
    import json as _json
    conn = get_conn()
    # Ensure audit table exists
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bill_attachment_edits (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                attachment_id UUID NOT NULL,
                invoice_id    UUID NOT NULL,
                edited_by     VARCHAR(100),
                edited_at     TIMESTAMPTZ DEFAULT now(),
                diff_json     JSONB NOT NULL
            )
        """)
    conn.commit()

    async with request.form() as form:
        file: UploadFile = form.get("file")
        edited_by: str = form.get("edited_by", "")
        diff_raw: str = form.get("diff", "[]")
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        contents = await file.read()
    enforce_pdf_page_limit(contents, file.filename or "")

    tenant = require_tenant()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT file_path FROM bill_attachments WHERE id=%s AND invoice_id=%s AND tenant_id=%s AND deleted_at IS NULL",
            (attachment_id, invoice_id, tenant),
        )
        row = cur.fetchone()
    if not row:
        put_conn(conn)
        raise HTTPException(status_code=404, detail="Attachment not found")

    object_store.replace(row["file_path"], contents, file.content_type)

    with conn.cursor() as cur:
        cur.execute("UPDATE bill_attachments SET file_size=%s WHERE id=%s", (len(contents), attachment_id))
        diff_data = _json.loads(diff_raw) if diff_raw else []
        cur.execute(
            "INSERT INTO bill_attachment_edits (attachment_id, invoice_id, edited_by, diff_json) VALUES (%s,%s,%s,%s)",
            (attachment_id, invoice_id, edited_by or None, psycopg2.extras.Json(diff_data))
        )
    conn.commit()
    put_conn(conn)
    return {"status": "updated"}

@router.get("/api/bills/{invoice_id}/attachments/{attachment_id}/edits")
def get_attachment_edits(invoice_id: str, attachment_id: str):
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # bill_attachment_edits carries no tenant_id of its own, so ownership
            # is established through the parent attachment before the edits are
            # read. The 404 it raises must not skip put_conn below.
            owned_attachment(cur, invoice_id, attachment_id)
            cur.execute(
                "SELECT id, edited_by, edited_at, diff_json FROM bill_attachment_edits WHERE attachment_id=%s AND invoice_id=%s ORDER BY edited_at DESC",
                (attachment_id, invoice_id)
            )
            rows = cur.fetchall()
    finally:
        put_conn(conn)
    def _s(v): return v.isoformat() if v else None
    return {"edits": [{"id": str(r["id"]), "edited_by": r["edited_by"], "edited_at": _s(r["edited_at"]), "diff": r["diff_json"]} for r in rows]}

@router.delete("/api/bills/{invoice_id}/attachments/{attachment_id}")
def delete_attachment(invoice_id: str, attachment_id: str):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                # Soft delete: hidden everywhere, the file stays in S3 as the
                # record; only the disk copy goes.
                """UPDATE bill_attachments SET deleted_at=now(), deleted_by=%s
                    WHERE id=%s AND invoice_id=%s AND tenant_id=%s AND deleted_at IS NULL
                RETURNING file_path""",
                (current_username() or None, attachment_id, invoice_id, require_tenant()),
            )
            row = cur.fetchone()
        conn.commit()
        put_conn(conn)
        if not row:
            raise HTTPException(status_code=404, detail="Attachment not found")
        object_store.drop_local_copy(row[0])
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
