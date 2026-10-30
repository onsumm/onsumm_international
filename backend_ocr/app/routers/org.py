"""Brands, cost centres, and tenant profile.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from pathlib import Path
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Any, List, Optional
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..security.licensing import entitlements, license_gate, impersonation_gate, require_service, invalidate as invalidate_license
from ..security.services import SERVICES, sort_order as service_sort_order

from fastapi import APIRouter

router = APIRouter()

from ..core import object_store
from ..core.tenant_settings import _sap_matching_enabled
from ..core.jurisdiction import get_jurisdiction

@router.get("/api/tenant/features")
def get_tenant_features(company_id: str | None = None):
    """Legacy single-flag endpoint, kept so older clients keep working.

    New code should call /api/tenant/entitlements, which returns every service.
    """
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    return {"sap_matching": _sap_matching_enabled(tenant)}

def _jurisdiction_payload(tenant_id: str) -> dict:
    j = get_jurisdiction(tenant_id)
    return {
        "base_currency": j.base_currency,
        "country_code": j.country_code,
        "is_resident_india": j.is_resident_india,
        "jurisdiction": j.to_dict(),
    }


@router.get("/api/tenant/entitlements")
def get_tenant_entitlements(company_id: str | None = None):
    """Everything this tenant is licensed for, for the UI to gate navigation.

    Hiding an unlicensed module here is a courtesy, not the control — the server
    403s the underlying endpoints regardless of what the client believes. The
    payload deliberately lists every service, enabled or not, so the UI can tell
    "not licensed" apart from "not loaded yet" and never flashes a module the
    tenant does not own.
    """
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    ent = entitlements(tenant)
    return {
        "company_id": tenant,
        # Which country's rules apply (core/jurisdiction.py): the books currency,
        # number/date locale, fiscal year, tax labels, and which India-only rule
        # packs (GST, PAN, TDS, IFSC banks, IMPS, MSME) the UI should show.
        # base_currency / country_code / is_resident_india are repeated at the
        # top level for clients that read them directly.
        **_jurisdiction_payload(tenant),
        "services": {
            svc.key: {
                "enabled": svc.key in ent.enabled,
                "display_name": svc.display_name,
                "category": svc.category,
                "limits": ent.limits.get(svc.key) or {},
            }
            for svc in SERVICES
        },
        # Mirrors the legacy key so a client can migrate without a flag day.
        "sap_matching": "sap_matching" in ent.enabled,
    }

def _require_admin():
    if current_role() != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

class CostCenterCreate(BaseModel):
    name: str
    brand_id: str
    company_id: str | None = None

class CostCenterUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    company_id: str | None = None

def _cc_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "brand_id": str(r["brand_id"]) if r["brand_id"] else None,
        "name": r["name"],
        "is_active": r["is_active"],
        "created_at": str(r["created_at"]) if r["created_at"] else None,
    }

@router.get("/api/cost-centers")
def list_cost_centers(brand_id: str | None = Query(None),
                      include_inactive: bool = Query(True),
                      company_id: str | None = Query(None)):
    """Cost centers for the tenant, optionally scoped to a brand. Any signed-in
    user may read them (needed by the review page); mutations are admin-only."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            q = "SELECT id, brand_id, name, is_active, created_at FROM cost_centers WHERE tenant_id = %s"
            params: list = [tenant]
            if brand_id:
                q += " AND brand_id = %s"
                params.append(brand_id)
            if not include_inactive:
                q += " AND is_active = true"
            q += " ORDER BY is_active DESC, name ASC"
            cur.execute(q, tuple(params))
            rows = cur.fetchall()
        return {"cost_centers": [_cc_row(r) for r in rows]}
    finally:
        put_conn(conn)

@router.post("/api/cost-centers", status_code=201)
def create_cost_center(body: CostCenterCreate):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    _require_admin()
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not body.brand_id:
        raise HTTPException(status_code=400, detail="brand_id is required")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Verify the brand belongs to this tenant (no cross-tenant tagging).
            cur.execute("SELECT 1 FROM brands WHERE id = %s AND tenant_id = %s", (body.brand_id, tenant))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Brand not found")
            cur.execute(
                "INSERT INTO cost_centers (tenant_id, brand_id, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, brand_id, name) DO UPDATE SET is_active = true "
                "RETURNING id, brand_id, name, is_active, created_at",
                (tenant, body.brand_id, name),
            )
            row = cur.fetchone()
        conn.commit()
        return _cc_row(row)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)

@router.patch("/api/cost-centers/{cc_id}")
def update_cost_center(cc_id: str, body: CostCenterUpdate):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    _require_admin()
    sets: list = []
    params: list = []
    if body.name is not None:
        nm = body.name.strip()
        if not nm:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        sets.append("name = %s")
        params.append(nm)
    if body.is_active is not None:
        sets.append("is_active = %s")
        params.append(body.is_active)
    if not sets:
        raise HTTPException(status_code=400, detail="nothing to update")
    params += [cc_id, tenant]
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"UPDATE cost_centers SET {', '.join(sets)} WHERE id = %s AND tenant_id = %s "
                "RETURNING id, brand_id, name, is_active, created_at",
                tuple(params),
            )
            row = cur.fetchone()
        conn.commit()
        if row is None:
            raise HTTPException(status_code=404, detail="Cost center not found")
        return _cc_row(row)
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)

@router.delete("/api/cost-centers/{cc_id}")
def delete_cost_center(cc_id: str, company_id: str | None = Query(None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    _require_admin()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Preserve history: refuse to hard-delete a cost center still on bills;
            # the admin should disable it (is_active=false) instead.
            cur.execute(
                "SELECT COUNT(*) FROM fact_invoices WHERE tenant_id = %s AND cost_center_id = %s",
                (tenant, cc_id),
            )
            in_use = cur.fetchone()[0]
            if in_use:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cost center is used by {in_use} bill(s). Disable it instead of deleting.",
                )
            cur.execute("DELETE FROM cost_centers WHERE id = %s AND tenant_id = %s", (cc_id, tenant))
        conn.commit()
        return {"deleted": True}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

class BrandCreate(BaseModel):
    name: str
    company_id: str

class BrandUpdate(BaseModel):
    name: str

@router.get("/api/brands")
def list_brands(company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, logo_path, is_default, created_at FROM brands WHERE tenant_id=%s ORDER BY is_default DESC, created_at ASC",
            (tenant,)
        )
        rows = cur.fetchall()
    put_conn(conn)
    return {"brands": [
        {
            "id": str(r[0]),
            "name": r[1],
            "has_logo": r[2] is not None,
            "is_default": r[3],
            "created_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]}

@router.post("/api/brands")
def create_brand(body: BrandCreate):
    import uuid as _uuid2
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO brands (tenant_id, name) VALUES (%s, %s) RETURNING id, created_at",
            (tenant, body.name.strip())
        )
        row = cur.fetchone()
    conn.commit()
    put_conn(conn)
    return {"id": str(row[0]), "name": body.name.strip(), "has_logo": False, "is_default": False, "created_at": row[1].isoformat()}

@router.put("/api/brands/{brand_id}")
def update_brand(brand_id: str, body: BrandUpdate, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE brands SET name=%s WHERE id=%s AND tenant_id=%s",
            (body.name.strip(), brand_id, tenant)
        )
    conn.commit()
    put_conn(conn)
    return {"status": "updated"}

@router.delete("/api/brands/{brand_id}")
def delete_brand(brand_id: str, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT is_default FROM brands WHERE id=%s AND tenant_id=%s", (brand_id, tenant))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Brand not found")
        if row[0]:
            raise HTTPException(status_code=400, detail="Cannot delete the default brand")
        cur.execute("DELETE FROM brands WHERE id=%s AND tenant_id=%s", (brand_id, tenant))
    conn.commit()
    put_conn(conn)
    return {"status": "deleted"}

@router.post("/api/brands/{brand_id}/logo")
async def upload_brand_logo(brand_id: str, request: Request, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    async with request.form() as form:
        file: UploadFile = form.get("file")
        if file is None:
            raise HTTPException(status_code=422, detail="file field is required")
        logo_bytes = await file.read()
        ext = Path(file.filename).suffix.lower() or ".png"

    import mimetypes
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Checked before the write: a brand id this tenant doesn't own is a
            # 404, and nothing is stored for it.
            cur.execute("SELECT logo_path FROM brands WHERE id=%s AND tenant_id=%s", (brand_id, tenant))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Brand not found")
            dest = object_store.save(object_store.key("brand-logos", tenant, f"{brand_id}{ext}"),
                                     logo_bytes, mimetypes.guess_type(f"x{ext}")[0])
            cur.execute(
                "UPDATE brands SET logo_path=%s WHERE id=%s AND tenant_id=%s",
                (dest, brand_id, tenant)
            )
        conn.commit()
    finally:
        put_conn(conn)
    if row[0] and row[0] != dest:
        object_store.drop_local_copy(row[0])
    return {"status": "uploaded"}

@router.get("/api/brands/{brand_id}/logo")
def get_brand_logo(brand_id: str, company_id: str = Query(default=None), signed: bool = Query(default=False)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT logo_path FROM brands WHERE id=%s AND tenant_id=%s", (brand_id, tenant))
        row = cur.fetchone()
    put_conn(conn)
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="No logo for this brand")
    if signed:
        link = object_store.signed_link_response(row[0])
        if link:
            return link
    data = object_store.load(row[0])
    if data is None:
        raise HTTPException(status_code=404, detail="Logo file missing")
    import mimetypes
    mt, _ = mimetypes.guess_type(row[0])
    return Response(content=data, media_type=mt or "image/png")
