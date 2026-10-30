"""Chart of accounts: ledger suggestions and coding rules.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

router = APIRouter()

class LedgerSuggestRequest(BaseModel):
    supplier_id: str | None = None
    hsn_sac: str | None = None
    description: str | None = None
    company_id: str | None = None

@router.post("/api/ledgers/suggest")
def ledger_suggest(body: LedgerSuggestRequest):
    """
    Return a ranked list of ledger suggestions for a line item.
    Tier 1 — supplier + description history (ledger_mapping_log)
    Tier 2 — HSN/SAC history tenant-wide (ledger_mapping_log)
    Tier 3 — charges master default_ledger_id (dim_charges)
    Tier 4 — any active ledger (fallback)
    """
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    results: list[dict] = []
    seen: set[str] = set()

    try:
        with conn.cursor() as cur:
            # Tier 1: supplier + description history
            if body.supplier_id and body.description:
                cur.execute("""
                    SELECT a.account_id, a.account_name, a.account_code,
                           COUNT(*) AS uses,
                           MAX(l.created_at) AS last_used
                    FROM ledger_mapping_log l
                    JOIN dim_ledger_accounts a ON a.account_id = l.account_id
                    WHERE l.tenant_id = %s
                      AND l.supplier_id = %s
                      AND l.description ILIKE %s
                      AND a.is_active = true
                    GROUP BY a.account_id, a.account_name, a.account_code
                    ORDER BY uses DESC, last_used DESC
                    LIMIT 3
                """, (tenant, body.supplier_id, f"%{body.description[:40]}%"))
                for r in cur.fetchall():
                    aid = str(r[0])
                    if aid not in seen:
                        seen.add(aid)
                        results.append({
                            "account_id": aid,
                            "account_name": r[1],
                            "account_code": r[2],
                            "confidence": "high",
                            "reason": f"Used in last {r[3]} bill(s) from this vendor",
                            "uses": r[3],
                        })

            # Tier 2: HSN/SAC history tenant-wide
            if body.hsn_sac and body.hsn_sac.strip():
                cur.execute("""
                    SELECT a.account_id, a.account_name, a.account_code,
                           COUNT(*) AS uses,
                           MAX(l.created_at) AS last_used
                    FROM ledger_mapping_log l
                    JOIN dim_ledger_accounts a ON a.account_id = l.account_id
                    WHERE l.tenant_id = %s
                      AND l.hsn_code = %s
                      AND a.is_active = true
                    GROUP BY a.account_id, a.account_name, a.account_code
                    ORDER BY uses DESC, last_used DESC
                    LIMIT 2
                """, (tenant, body.hsn_sac.strip()))
                for r in cur.fetchall():
                    aid = str(r[0])
                    if aid not in seen:
                        seen.add(aid)
                        results.append({
                            "account_id": aid,
                            "account_name": r[1],
                            "account_code": r[2],
                            "confidence": "medium",
                            "reason": f"Used {r[3]}× for HSN/SAC {body.hsn_sac}",
                            "uses": r[3],
                        })

            # Tier 3: charges master
            if body.hsn_sac and body.hsn_sac.strip():
                cur.execute("""
                    SELECT a.account_id, a.account_name, a.account_code
                    FROM dim_charges c
                    JOIN dim_ledger_accounts a ON a.account_id = c.default_ledger_id
                    WHERE c.tenant_id = %s AND c.hsn_sac = %s
                      AND c.is_active = true AND a.is_active = true
                    LIMIT 1
                """, (tenant, body.hsn_sac.strip()))
                r = cur.fetchone()
                if r:
                    aid = str(r[0])
                    if aid not in seen:
                        seen.add(aid)
                        results.append({
                            "account_id": aid,
                            "account_name": r[1],
                            "account_code": r[2],
                            "confidence": "low",
                            "reason": "Charges master default",
                            "uses": 0,
                        })

            # Tier 4: fallback — return top active ledger accounts for manual pick
            if not results:
                cur.execute("""
                    SELECT account_id, account_name, account_code
                    FROM dim_ledger_accounts
                    WHERE tenant_id = %s AND is_active = true
                    ORDER BY account_name
                    LIMIT 5
                """, (tenant,))
                for r in cur.fetchall():
                    results.append({
                        "account_id": str(r[0]),
                        "account_name": r[1],
                        "account_code": r[2],
                        "confidence": "none",
                        "reason": "No history — pick manually",
                        "uses": 0,
                    })

        return {"suggestions": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

@router.get("/api/ledgers")
def list_ledgers(company_id: str | None = None):
    """Return all active ledger accounts for the tenant (used by autocomplete)."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT account_id, account_name, account_code, account_type
                FROM dim_ledger_accounts
                WHERE tenant_id = %s AND is_active = true
                ORDER BY account_name
            """, (tenant,))
            rows = cur.fetchall()
        return {"ledgers": [
            {"account_id": str(r[0]), "account_name": r[1],
             "account_code": r[2], "account_type": r[3]}
            for r in rows
        ]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

class CoaRuleCreate(BaseModel):
    exp_head: str
    vendor_name: str | None = None
    category: str | None = None
    priority: int = 0
    company_id: str | None = None

class CoaRuleUpdate(BaseModel):
    exp_head: str | None = None
    vendor_name: str | None = None
    category: str | None = None
    priority: int | None = None
    company_id: str | None = None

@router.get("/api/coa-rules")
def list_coa_rules(company_id: str | None = Query(None)):
    import psycopg2.extras
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, exp_head, vendor_name, category, priority, created_at
                FROM coa_rules
                WHERE tenant_id = %s
                ORDER BY exp_head, priority DESC
                """,
                (tenant,)
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        put_conn(conn)

@router.post("/api/coa-rules")
def create_coa_rule(body: CoaRuleCreate):
    import psycopg2.extras
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO coa_rules (tenant_id, exp_head, vendor_name, category, priority)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, exp_head, vendor_name, category, priority, created_at
                """,
                (tenant, body.exp_head, body.vendor_name, body.category, body.priority)
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        put_conn(conn)

@router.delete("/api/coa-rules/{rule_id}")
def delete_coa_rule(rule_id: str, company_id: str | None = Query(None)):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM coa_rules WHERE id = %s AND tenant_id = %s",
                (rule_id, tenant)
            )
        conn.commit()
        return {"deleted": True}
    finally:
        put_conn(conn)

@router.put("/api/coa-rules/{rule_id}")
def update_coa_rule(rule_id: str, body: CoaRuleUpdate):
    import psycopg2.extras
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            fields = []
            values = []
            if body.exp_head is not None:
                fields.append("exp_head = %s"); values.append(body.exp_head)
            if body.vendor_name is not None:
                fields.append("vendor_name = %s"); values.append(body.vendor_name)
            if body.category is not None:
                fields.append("category = %s"); values.append(body.category)
            if body.priority is not None:
                fields.append("priority = %s"); values.append(body.priority)
            if not fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            tenant = require_tenant()  # tenant from JWT; client company_id ignored
            values.extend([rule_id, tenant])
            cur.execute(
                f"UPDATE coa_rules SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s "
                "RETURNING id, exp_head, vendor_name, category, priority, created_at",
                values
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Rule not found")
        conn.commit()
        return dict(row)
    finally:
        put_conn(conn)
