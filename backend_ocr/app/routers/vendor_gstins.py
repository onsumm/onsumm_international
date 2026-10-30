"""The several GSTINs a vendor may hold, and their verification.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from typing import Any, List, Optional
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..core.vendor_activity_log import log_vendor_activity

from fastapi import APIRouter

from ..core.jurisdiction_guard import require_pack
router = APIRouter()

@router.get("/api/vendors/{supplier_id}/gstins")
def list_supplier_gstins(supplier_id: str, company_id: Optional[str] = None):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gstn, pan, status FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s",
                (supplier_id, tenant),
            )
            sup = cur.fetchone()
            if not sup:
                raise HTTPException(404, "Vendor not found")

            cur.execute(
                "SELECT id, gstin, pan, is_primary, status, trade_name, legal_name,"
                "       gstin_status, error_message, verified_at, created_at"
                " FROM supplier_gstins WHERE supplier_id=%s ORDER BY is_primary DESC, created_at ASC",
                (supplier_id,),
            )
            rows = [dict(r) for r in cur.fetchall()]

            # Inject primary GSTIN if not yet in supplier_gstins table
            existing_gstins = {r["gstin"] for r in rows}
            primary_gstn = (sup["gstn"] or "").strip().upper()
            if primary_gstn and primary_gstn not in existing_gstins:
                # Map vendor KYB status → GSTIN status (don't assume VERIFIED)
                kyb = (sup["status"] or "").upper()
                primary_status = "VERIFIED" if kyb == "VERIFIED" else ("FAILED" if kyb == "FAILED" else "UNVERIFIED")

                # Pull latest audit reason for FAILED primary (wf_audit_log)
                primary_error = None
                if primary_status == "FAILED":
                    cur.execute(
                        """
                        SELECT new_values_jsonb
                        FROM   wf_audit_log
                        WHERE  table_name = 'dim_suppliers'
                          AND  entity_id  = %s::uuid
                          AND  action     = 'UPDATE'
                          AND  (new_values_jsonb ? 'fail_reason'
                             OR new_values_jsonb ? 'error'
                             OR new_values_jsonb->>'status' = 'FAILED')
                        ORDER  BY created_at DESC
                        LIMIT  1
                        """,
                        (supplier_id,),
                    )
                    log = cur.fetchone()
                    if log and log["new_values_jsonb"]:
                        nv = log["new_values_jsonb"]
                        reason = nv.get("fail_reason", "")
                        if reason == "trade_name_mismatch":
                            db_name  = nv.get("db_name", "") or nv.get("db_name_normalised", "")
                            api_name = nv.get("api_legal_name", "")
                            primary_error = f"Name mismatch: registered '{db_name}' vs GST portal '{api_name}'"
                        elif reason == "pan_mismatch":
                            primary_error = f"PAN mismatch — vendor: {nv.get('db_pan')}, portal: {nv.get('api_pan')}"
                        elif reason.startswith("gstin_status_"):
                            portal_status = reason.replace("gstin_status_", "").upper()
                            primary_error = f"GSTIN inactive on portal (status: {portal_status})"
                        elif reason == "invalid_gstin":
                            primary_error = "GSTIN not found / invalid on the GST portal"
                        elif nv.get("error"):
                            primary_error = str(nv["error"])
                        else:
                            primary_error = reason or "Verification failed — check GSTIN on GST portal"

                rows.insert(0, {
                    "id": None,
                    "gstin": primary_gstn,
                    "pan": sup["pan"] or (primary_gstn[2:12] if len(primary_gstn) == 15 else None),
                    "is_primary": True,
                    "status": primary_status,
                    "trade_name": None,
                    "legal_name": None,
                    "gstin_status": kyb,
                    "error_message": primary_error,
                    "verified_at": None,
                    "created_at": None,
                })
            return rows
    finally:
        conn.close()

class AddGstinRequest(BaseModel):
    gstin: str
    # False by default: adding a GSTIN normally means "another registration for
    # this vendor", not a replacement. The Review Hub's master_data_conflict
    # "keep new" action sets this when a vendor email claims a different
    # *primary* GSTIN than what's on file, so the write demotes the current
    # primary rather than sitting beside it as a second one.
    make_primary: bool = False

@router.post("/api/vendors/{supplier_id}/gstins", dependencies=[Depends(require_pack("gst", methods=None))])
def add_supplier_gstin(supplier_id: str, body: AddGstinRequest, company_id: Optional[str] = None):
    import re as _re
    from ..integrations.sandbox_kyb import verify_gstin as _verify_gstin

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    GSTN_RE = r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$'
    gstin = body.gstin.strip().upper()
    if not _re.match(GSTN_RE, gstin):
        raise HTTPException(422, "Invalid GSTIN format (e.g. 29AABCT1332L1ZU)")

    new_pan = gstin[2:12]

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            # Get primary vendor GSTIN and PAN
            cur.execute(
                "SELECT gstn, pan FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s",
                (supplier_id, tenant),
            )
            sup = cur.fetchone()
            if not sup:
                raise HTTPException(404, "Vendor not found")

            primary_gstn = (sup["gstn"] or "").strip().upper()
            primary_pan = sup["pan"] or (primary_gstn[2:12] if len(primary_gstn) == 15 else None)

            # PAN constraint: must match existing PAN
            if primary_pan and new_pan != primary_pan:
                raise HTTPException(
                    409,
                    f"PAN mismatch: new GSTIN has PAN {new_pan} but vendor PAN is {primary_pan}. "
                    "All GSTINs for a vendor must belong to the same PAN."
                )

            # Duplicate check
            if gstin == primary_gstn:
                raise HTTPException(409, "This GSTIN is already the primary GSTIN for this vendor")
            cur.execute("SELECT id FROM supplier_gstins WHERE supplier_id=%s AND gstin=%s", (supplier_id, gstin))
            if cur.fetchone():
                raise HTTPException(409, "This GSTIN is already registered for this vendor")

        # Verify via Sandbox
        result = _verify_gstin(gstin)

        status = "VERIFIED" if result.get("valid") and (result.get("status") or "").lower() == "active" else "FAILED"
        error_msg = None if status == "VERIFIED" else (result.get("error") or f"GST status: {result.get('status', 'unknown')}")

        from datetime import datetime, timezone as _tz
        verified_at = datetime.now(_tz.utc) if status == "VERIFIED" else None
        with conn.cursor() as cur:
            if body.make_primary:
                # Demote whatever was primary before — is_primary is a
                # single-row flag here, not a set, so the new row can't just be
                # added beside the old one.
                cur.execute(
                    "UPDATE supplier_gstins SET is_primary=false "
                    " WHERE supplier_id=%s AND tenant_id=%s",
                    (supplier_id, tenant),
                )
            cur.execute(
                """INSERT INTO supplier_gstins
                   (supplier_id, tenant_id, gstin, pan, is_primary, status,
                    trade_name, legal_name, gstin_status, error_message, verified_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, gstin, pan, is_primary, status, trade_name, legal_name,
                             gstin_status, error_message, verified_at, created_at""",
                (
                    supplier_id, tenant, gstin, new_pan, body.make_primary, status,
                    result.get("trade_name"), result.get("legal_name"),
                    result.get("status"), error_msg, verified_at,
                ),
            )
            row = dict(cur.fetchone())
            outcome = " · verified" if status == "VERIFIED" else " · verification failed"
            log_vendor_activity(
                cur, supplier_id, "gstin_added",
                f"Added GSTIN {gstin}{outcome}" + (" as primary" if body.make_primary else ""),
                changes=[{"field": "GSTIN", "from": None, "to": gstin}],
                meta={"entity_id": str(row["id"]), "status": status, "detail": error_msg},
                tenant_id=tenant)
        conn.commit()
        return row
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@router.post("/api/vendors/{supplier_id}/gstins/primary/reverify", dependencies=[Depends(require_pack("gst", methods=None))])
def reverify_primary_gstin(supplier_id: str, company_id: Optional[str] = None):
    """Reset primary GSTIN to UNVERIFIED and notify the pipeline to re-verify asynchronously."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gstn, name, pan FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s",
                (supplier_id, tenant),
            )
            sup = cur.fetchone()
            if not sup or not sup["gstn"]:
                raise HTTPException(404, "Vendor or GSTIN not found")
            gstn   = sup["gstn"].strip().upper()
            db_pan = (sup["pan"] or (gstn[2:12] if len(gstn) == 15 else None) or "").strip().upper()

            # Reset to UNVERIFIED so the pipeline worker picks it up on its next poll
            cur.execute(
                "UPDATE dim_suppliers SET status='UNVERIFIED' WHERE supplier_id=%s AND tenant_id=%s",
                (supplier_id, tenant),
            )
            cur.execute(
                """INSERT INTO wf_audit_log (tenant_id, table_name, entity_id, action, new_values_jsonb)
                   VALUES (%s,'dim_suppliers',%s::uuid,'UPDATE',%s::jsonb)""",
                (tenant, supplier_id, psycopg2.extras.Json({"status": "UNVERIFIED", "reason": "manual_reverify"})),
            )
            log_vendor_activity(
                cur, supplier_id, "gstin_reverify_requested",
                f"Asked to re-check GSTIN {gstn}", meta={"gstin": gstn, "primary": True},
                tenant_id=tenant)
            # Wake the GST verification pipeline worker immediately via PostgreSQL NOTIFY
            cur.execute("SELECT pg_notify('ch_gst_verification', %s)", (str(supplier_id),))
        conn.commit()
        return {
            "id": None,
            "gstin": gstn,
            "pan": db_pan or (gstn[2:12] if len(gstn) == 15 else None),
            "is_primary": True,
            "status": "UNVERIFIED",
            "trade_name": None,
            "legal_name": None,
            "gstin_status": None,
            "error_message": None,
            "verified_at": None,
            "created_at": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@router.post("/api/vendors/{supplier_id}/gstins/{gstin_id}/reverify", dependencies=[Depends(require_pack("gst", methods=None))])
def reverify_supplier_gstin(supplier_id: str, gstin_id: str, company_id: Optional[str] = None):
    """Reset secondary GSTIN to UNVERIFIED and notify the pipeline to re-verify asynchronously."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT gstin, pan FROM supplier_gstins WHERE id=%s AND supplier_id=%s AND tenant_id=%s",
                (gstin_id, supplier_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "GSTIN record not found")
            gstin  = row["gstin"]
            db_pan = (row["pan"] or "").strip().upper()

            # Reset to UNVERIFIED so the pipeline worker picks it up
            cur.execute(
                """UPDATE supplier_gstins
                   SET status='UNVERIFIED', error_message=NULL, verified_at=NULL
                   WHERE id=%s
                   RETURNING id, gstin, pan, is_primary, status, trade_name, legal_name,
                             gstin_status, error_message, verified_at, created_at""",
                (gstin_id,),
            )
            updated = dict(cur.fetchone())
            log_vendor_activity(
                cur, supplier_id, "gstin_reverify_requested",
                f"Asked to re-check GSTIN {gstin}", meta={"entity_id": gstin_id, "gstin": gstin},
                tenant_id=tenant)
            # Wake the GST verification pipeline worker immediately
            cur.execute("SELECT pg_notify('ch_gst_verification', %s)", (gstin,))
        conn.commit()
        return updated
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@router.delete("/api/vendors/{supplier_id}/gstins/{gstin_id}")
def delete_supplier_gstin(supplier_id: str, gstin_id: str, company_id: Optional[str] = None):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM supplier_gstins WHERE id=%s AND supplier_id=%s AND tenant_id=%s RETURNING id, gstin",
                (gstin_id, supplier_id, tenant),
            )
            gone = cur.fetchone()
            if not gone:
                raise HTTPException(404, "GSTIN not found")
            log_vendor_activity(
                cur, supplier_id, "gstin_deleted", f"Removed GSTIN {gone[1]}",
                changes=[{"field": "GSTIN", "from": gone[1], "to": None}],
                meta={"entity_id": gstin_id}, tenant_id=tenant)
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
