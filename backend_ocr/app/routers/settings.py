"""Tenant settings, including the approval chain configuration.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from ..core.storage import PROJECT_ROOT

import os
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from ..integrations.ocr import enforce_pdf_page_limit
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..security.tenancy import Tenant, assert_owned, owned_attachment, owned_bill, owned_vendor

from fastapi import APIRouter

router = APIRouter()

from ..core.approval_chain import _invalidate_chain_cache, _load_chain

# Payment priority labels are presentation names only: p0–p3 remain the stable
# values stored on invoices and used by the priority calculation.  Keeping the
# labels in a tenant-keyed table means one company's vocabulary can never leak
# into another company's bill list.
_DEFAULT_PRIORITY_LABELS = {"p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3"}

def _ensure_priority_label_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tenant_priority_labels (
            tenant_id uuid PRIMARY KEY,
            p0 varchar(48) NOT NULL DEFAULT 'P0',
            p1 varchar(48) NOT NULL DEFAULT 'P1',
            p2 varchar(48) NOT NULL DEFAULT 'P2',
            p3 varchar(48) NOT NULL DEFAULT 'P3',
            updated_at timestamptz NOT NULL DEFAULT now(),
            updated_by varchar(255)
        )
    """)

class PriorityLabelsRequest(BaseModel):
    p0: str
    p1: str
    p2: str
    p3: str

@router.get("/api/settings/priority-labels")
def get_priority_labels():
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_priority_label_table(cur)
            cur.execute("SELECT p0, p1, p2, p3 FROM tenant_priority_labels WHERE tenant_id=%s", (tenant,))
            row = cur.fetchone()
        conn.commit()
        return dict(zip(("p0", "p1", "p2", "p3"), row)) if row else _DEFAULT_PRIORITY_LABELS
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

@router.put("/api/settings/priority-labels")
def update_priority_labels(body: PriorityLabelsRequest):
    tenant = require_tenant()
    if current_role() != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can change priority names")
    labels = {key: getattr(body, key).strip() for key in _DEFAULT_PRIORITY_LABELS}
    if any(not value or len(value) > 48 for value in labels.values()):
        raise HTTPException(status_code=400, detail="Each priority name must be between 1 and 48 characters")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            _ensure_priority_label_table(cur)
            cur.execute("""
                INSERT INTO tenant_priority_labels (tenant_id, p0, p1, p2, p3, updated_at, updated_by)
                VALUES (%s, %s, %s, %s, %s, now(), %s)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    p0=EXCLUDED.p0, p1=EXCLUDED.p1, p2=EXCLUDED.p2, p3=EXCLUDED.p3,
                    updated_at=now(), updated_by=EXCLUDED.updated_by
            """, (tenant, labels["p0"], labels["p1"], labels["p2"], labels["p3"], current_username()))
        conn.commit()
        return labels
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

class ChainStepModel(BaseModel):
    step_order: int
    label: str
    role: str
    locked: bool = False
    assignees: list[str] | None = None

@router.get("/api/settings/chain/default")
def get_chain_default(company_id: str = Query(default=None), department: str = Query(default=None)):
    """Load the saved default snapshot for a chain."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        if department:
            cur.execute(
                "SELECT step_order, label, role, locked FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=true ORDER BY step_order",
                (tenant, department)
            )
        else:
            cur.execute(
                "SELECT step_order, label, role, locked FROM tenant_chain_config WHERE tenant_id=%s AND department IS NULL AND is_snapshot=true ORDER BY step_order",
                (tenant,)
            )
        rows = cur.fetchall()
    put_conn(conn)
    return {"steps": [dict(r) for r in rows]}

@router.get("/api/settings/chain")
def get_chain(company_id: str = Query(default=None), department: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    chain = _load_chain(tenant, department)
    return {"steps": chain}

@router.post("/api/settings/chain")
def save_chain(steps: list[ChainStepModel], company_id: str = Query(default=None), department: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if department:
                cur.execute("DELETE FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=false", (tenant, department))
            else:
                cur.execute("DELETE FROM tenant_chain_config WHERE tenant_id=%s AND department IS NULL AND is_snapshot=false", (tenant,))
            import json as _json
            for s in steps:
                cur.execute(
                    """INSERT INTO tenant_chain_config (tenant_id, department, step_order, label, role, locked, assignees, is_snapshot, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,false,now())""",
                    (tenant, department, s.step_order, s.label, s.role, s.locked,
                     _json.dumps(s.assignees) if s.assignees else None),
                )
        conn.commit()
        _invalidate_chain_cache(tenant, department)
        return {"status": "saved", "steps": len(steps)}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

@router.get("/api/settings/departments")
def get_departments(company_id: str = Query(default=None)):
    """Return list of departments configured for this tenant."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT department FROM tenant_chain_config WHERE tenant_id=%s AND department IS NOT NULL AND is_snapshot=false ORDER BY department",
            (tenant,)
        )
        rows = cur.fetchall()
    put_conn(conn)
    return {"departments": [r[0] for r in rows]}

@router.get("/api/settings/user-departments")
def get_user_departments(username: str = Query(...), company_id: str = Query(default=None)):
    """Return departments where this user appears as an assignee (or matches role) in the chain."""
    import psycopg2, json as _json
    tenant = require_tenant()  # tenant from JWT; client company_id ignored

    conn = get_conn()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Get user's name and role
        cur.execute("SELECT name, role FROM users WHERE username=%s AND company_id=%s", (username, tenant))
        row = cur.fetchone()
        if not row:
            put_conn(conn)
            return {"departments": []}
        user_name, user_role = row["name"], row["role"]

        # Get all dept chains
        cur.execute(
            "SELECT department, role, assignees FROM tenant_chain_config WHERE tenant_id=%s AND department IS NOT NULL AND is_snapshot=false AND locked=false",
            (tenant,)
        )
        steps = cur.fetchall()
    put_conn(conn)

    matched = set()
    for step in steps:
        dept = step["department"]
        assignees = step["assignees"]
        if isinstance(assignees, str):
            try: assignees = _json.loads(assignees)
            except: assignees = []
        # User matches if: they're specifically assigned, OR no assignees and role matches
        if assignees:
            if user_name in assignees or any(a.strip() == user_name for a in assignees):
                matched.add(dept)
        else:
            if step["role"] == user_role:
                matched.add(dept)

    return {"departments": sorted(matched)}

@router.post("/api/settings/departments")
def add_department(body: dict, company_id: str = Query(default=None)):
    """Add a new department with empty chain (just submitted step)."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    dept = body.get("department", "").strip()
    if not dept:
        raise HTTPException(status_code=400, detail="department required")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Check if department already exists for this tenant
            cur.execute(
                "SELECT 1 FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=false LIMIT 1",
                (tenant, dept)
            )
            if cur.fetchone():
                return {"status": "exists", "department": dept}
            cur.execute("""
                INSERT INTO tenant_chain_config (tenant_id, department, step_order, label, role, locked, is_snapshot)
                VALUES (%s, %s, 0, 'Submitted', 'member', true, false)
            """, (tenant, dept))
        conn.commit()
    finally:
        put_conn(conn)
    return {"status": "created", "department": dept}

@router.delete("/api/settings/departments/{department}")
def delete_department(department: str, company_id: str = Query(default=None)):
    """Delete a department and all its chain config."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM tenant_chain_config WHERE tenant_id=%s AND department=%s",
                (tenant, department)
            )
        conn.commit()
        _invalidate_chain_cache(tenant, department)
    finally:
        put_conn(conn)
    return {"status": "deleted"}

@router.post("/api/settings/chain/save-default")
def save_chain_as_default(company_id: str = Query(default=None), department: str = Query(default=None)):
    """Snapshot current chain as the default for Reset to Default."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Load current working chain
            if department:
                cur.execute(
                    "SELECT step_order, label, role, locked FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=false ORDER BY step_order",
                    (tenant, department)
                )
            else:
                cur.execute(
                    "SELECT step_order, label, role, locked FROM tenant_chain_config WHERE tenant_id=%s AND department IS NULL AND is_snapshot=false ORDER BY step_order",
                    (tenant,)
                )
            rows = cur.fetchall()
            # Delete old snapshot
            if department:
                cur.execute(
                    "DELETE FROM tenant_chain_config WHERE tenant_id=%s AND department=%s AND is_snapshot=true",
                    (tenant, department)
                )
            else:
                cur.execute(
                    "DELETE FROM tenant_chain_config WHERE tenant_id=%s AND department IS NULL AND is_snapshot=true",
                    (tenant,)
                )
            # Insert new snapshot
            for step_order, label, role, locked in rows:
                cur.execute(
                    "INSERT INTO tenant_chain_config (tenant_id, department, step_order, label, role, locked, is_snapshot) VALUES (%s,%s,%s,%s,%s,%s,true)",
                    (tenant, department, step_order, label, role, locked)
                )
        conn.commit()
    finally:
        put_conn(conn)
    return {"status": "saved"}

# ── Approval chain extraction ─────────────────────────────────────────────────
_CHAIN_SYSTEM = """You are an expert at extracting approval workflow steps from text or documents.
Given a description of an approval process, extract an ordered list of approval steps.
Return ONLY valid JSON in this exact format (no markdown, no explanation):
{
  "steps": [
    {"label": "Step Name", "role": "role_key"},
    ...
  ]
}

Available role keys and when to use them:
- "member"             → the person who submits/initiates (always first, always included)
- "approver"           → department manager, line manager, team lead, supervisor, approver
- "accountant"         → accountant, accounts team, finance team, bookkeeper
- "finance_controller" → finance controller, CFO, finance head, VP finance, finance director
- "admin"              → admin, system admin, final admin approval

Rules:
- Always include "member" (Submitted) as the first step with label "Submitted"
- Map described roles to the closest available role key
- Preserve the order mentioned in the description
- Use clear, short labels (max 3 words)
- Return 2-5 steps total including "member"
"""

@router.post("/api/settings/extract-chain")
async def extract_chain(
    text: str = Form(default=None),
    file: UploadFile = File(default=None),
):
    # Reads no rows — it runs the uploaded text through the model and returns a
    # proposed approval chain the caller then saves separately. Authenticated so
    # it is not an open door to the OpenAI key.
    require_tenant()

    import os, sys, json, tempfile
    # Anchored on core.storage rather than counted out with dirname(): this
    # module now sits a package deeper than main.py did, so the old three-deep
    # count would have landed on backend_ocr instead of the repository root.
    _PIPELINE_ROOT = str(PROJECT_ROOT)
    if _PIPELINE_ROOT not in sys.path:
        sys.path.insert(0, _PIPELINE_ROOT)

    import config as cfg
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=cfg.OPENAI_API_KEY)

    content = text or ""

    # If file uploaded, extract text from it
    if file and file.filename:
        raw = await file.read()
        enforce_pdf_page_limit(raw, file.filename or "")
        fname = (file.filename or "").lower()
        if fname.endswith(".pdf"):
            # Use existing PDF OCR to extract raw text
            try:
                import fitz  # PyMuPDF
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                doc = fitz.open(tmp_path)
                content = "\n".join(page.get_text() for page in doc)
                doc.close()
                os.unlink(tmp_path)
            except Exception:
                content = raw.decode("utf-8", errors="ignore")
        elif fname.endswith((".xlsx", ".xls")):
            try:
                import openpyxl, io
                wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
                lines = []
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        line = " | ".join(str(c) for c in row if c is not None)
                        if line.strip():
                            lines.append(line)
                content = "\n".join(lines)
            except Exception:
                content = raw.decode("utf-8", errors="ignore")
        else:
            content = raw.decode("utf-8", errors="ignore")

    if not content.strip():
        raise HTTPException(status_code=400, detail="No content provided")

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _CHAIN_SYSTEM},
                {"role": "user",   "content": f"Extract approval chain from:\n\n{content[:4000]}"},
            ],
            temperature=0,
        )
        result = json.loads(resp.choices[0].message.content)
        return result
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Could not parse AI response as JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
