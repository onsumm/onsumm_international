"""Expense categories, expense heads, and additional charges.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

router = APIRouter()

class ChargeCreateRequest(BaseModel):
    name: str
    hsn_sac: str | None = None
    default_ledger_id: str | None = None
    default_tax_rate: float | None = None
    company_id: str | None = None

class ChargeUpdateRequest(BaseModel):
    name: str | None = None
    hsn_sac: str | None = None
    default_ledger_id: str | None = None
    default_tax_rate: float | None = None
    is_active: bool | None = None
    company_id: str | None = None

@router.get("/api/charges")
def list_charges(company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT charge_id, tenant_id, name, hsn_sac,
                       default_ledger_id, default_tax_rate, is_active,
                       created_at, updated_at
                FROM dim_charges
                WHERE tenant_id = %s AND is_active = true
                ORDER BY name ASC
                """,
                (tenant,),
            )
            rows = cur.fetchall()
        put_conn(conn)
        charges = [
            {
                "charge_id":         str(r["charge_id"]),
                "tenant_id":         str(r["tenant_id"]),
                "name":              r["name"],
                "hsn_sac":           r["hsn_sac"],
                "default_ledger_id": str(r["default_ledger_id"]) if r["default_ledger_id"] else None,
                "default_tax_rate":  float(r["default_tax_rate"]) if r["default_tax_rate"] is not None else None,
                "is_active":         r["is_active"],
                "created_at":        r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at":        r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]
        return {"charges": charges, "total": len(charges)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/charges")
def create_charge(body: ChargeCreateRequest):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO dim_charges (tenant_id, name, hsn_sac, default_ledger_id, default_tax_rate)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING charge_id, name, hsn_sac, default_ledger_id, default_tax_rate, is_active, created_at, updated_at
                """,
                (
                    tenant,
                    body.name.strip(),
                    body.hsn_sac.strip() if body.hsn_sac else None,
                    body.default_ledger_id or None,
                    body.default_tax_rate,
                ),
            )
            r = cur.fetchone()
        conn.commit()
        put_conn(conn)
        return {
            "charge_id":         str(r["charge_id"]),
            "name":              r["name"],
            "hsn_sac":           r["hsn_sac"],
            "default_ledger_id": str(r["default_ledger_id"]) if r["default_ledger_id"] else None,
            "default_tax_rate":  float(r["default_tax_rate"]) if r["default_tax_rate"] is not None else None,
            "is_active":         r["is_active"],
            "created_at":        r["created_at"].isoformat(),
            "updated_at":        r["updated_at"].isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/api/charges/{charge_id}")
def update_charge(charge_id: str, body: ChargeUpdateRequest):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    sets, params = [], []
    if body.name is not None:
        sets.append("name = %s"); params.append(body.name.strip())
    if body.hsn_sac is not None:
        sets.append("hsn_sac = %s"); params.append(body.hsn_sac.strip())
    if body.default_ledger_id is not None:
        sets.append("default_ledger_id = %s"); params.append(body.default_ledger_id)
    if body.default_tax_rate is not None:
        sets.append("default_tax_rate = %s"); params.append(body.default_tax_rate)
    if body.is_active is not None:
        sets.append("is_active = %s"); params.append(body.is_active)
    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets.append("updated_at = now()")
    params.extend([charge_id, tenant])
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"UPDATE dim_charges SET {', '.join(sets)} WHERE charge_id = %s AND tenant_id = %s RETURNING *",
                params,
            )
            r = cur.fetchone()
        conn.commit()
        put_conn(conn)
        if not r:
            raise HTTPException(status_code=404, detail="Charge not found")
        return {
            "charge_id":         str(r["charge_id"]),
            "name":              r["name"],
            "hsn_sac":           r["hsn_sac"],
            "default_ledger_id": str(r["default_ledger_id"]) if r["default_ledger_id"] else None,
            "default_tax_rate":  float(r["default_tax_rate"]) if r["default_tax_rate"] is not None else None,
            "is_active":         r["is_active"],
            "updated_at":        r["updated_at"].isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/api/charges/{charge_id}")
def delete_charge(charge_id: str, company_id: str = Query(default=None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dim_charges SET is_active = false, updated_at = now() WHERE charge_id = %s AND tenant_id = %s",
                (charge_id, tenant),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Charge not found")
        conn.commit()
        put_conn(conn)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/categories")
def list_categories(company_id: str | None = Query(None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, exp_head FROM dim_categories WHERE tenant_id = %s AND is_builtin = false ORDER BY created_at",
                (tenant,)
            )
            rows = cur.fetchall()
        return {"categories": [{"id": str(r["id"]), "name": r["name"], "exp_head": r["exp_head"]} for r in rows]}
    finally:
        put_conn(conn)

class CategoryBody(BaseModel):
    name: str
    exp_head: str | None = None
    company_id: str | None = None

@router.post("/api/categories", status_code=201)
def create_category(body: CategoryBody):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO dim_categories (tenant_id, name, exp_head) VALUES (%s, %s, %s) "
                "ON CONFLICT (tenant_id, name) DO UPDATE SET exp_head = EXCLUDED.exp_head "
                "RETURNING id, name, exp_head",
                (tenant, name, body.exp_head or None)
            )
            row = cur.fetchone()
        conn.commit()
        return {"id": str(row["id"]), "name": row["name"], "exp_head": row["exp_head"]}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)

class CategoryUpdateBody(BaseModel):
    exp_head: str | None = None
    company_id: str | None = None

@router.patch("/api/categories/{category_id}")
def update_category(category_id: str, body: CategoryUpdateBody):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE dim_categories SET exp_head = %s WHERE id = %s AND tenant_id = %s "
                "RETURNING id, name, exp_head",
                (body.exp_head or None, category_id, tenant)
            )
            row = cur.fetchone()
        conn.commit()
        if row is None:
            raise HTTPException(status_code=404, detail="Category not found")
        return {"id": str(row["id"]), "name": row["name"], "exp_head": row["exp_head"]}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)

@router.get("/api/category-exp-heads")
def get_category_exp_heads(company_id: str | None = Query(None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT name, exp_head FROM dim_categories WHERE tenant_id = %s AND exp_head IS NOT NULL",
                (tenant,)
            )
            rows = cur.fetchall()
        return {r["name"]: r["exp_head"] for r in rows}
    finally:
        put_conn(conn)

class CategoryExpHeadBody(BaseModel):
    name: str
    exp_head: str | None = None
    company_id: str | None = None

@router.put("/api/category-exp-heads")
def set_category_exp_head(body: CategoryExpHeadBody):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    name = body.name.strip()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if body.exp_head:
                cur.execute(
                    """
                    INSERT INTO dim_categories (tenant_id, name, exp_head, is_builtin)
                    VALUES (%s, %s, %s, true)
                    ON CONFLICT (tenant_id, name) DO UPDATE SET exp_head = EXCLUDED.exp_head
                    """,
                    (tenant, name, body.exp_head)
                )
            else:
                cur.execute(
                    "UPDATE dim_categories SET exp_head = NULL WHERE tenant_id = %s AND name = %s AND is_builtin = false",
                    (tenant, name)
                )
                cur.execute(
                    "DELETE FROM dim_categories WHERE tenant_id = %s AND name = %s AND is_builtin = true",
                    (tenant, name)
                )
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        put_conn(conn)

@router.delete("/api/categories/{category_id}")
def delete_category(category_id: str, company_id: str | None = Query(None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM dim_categories WHERE id = %s AND tenant_id = %s",
                (category_id, tenant)
            )
        conn.commit()
        return {"deleted": True}
    finally:
        put_conn(conn)
