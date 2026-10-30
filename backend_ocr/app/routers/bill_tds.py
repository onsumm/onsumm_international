"""Per-bill TDS recalculation and manual override.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from typing import Any, List, Optional
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from fastapi import APIRouter

from ..core.jurisdiction_guard import require_pack
router = APIRouter()

class TdsRecalcRequest(BaseModel):
    company_id: Optional[str] = None
    force: bool = False

@router.post("/api/bills/{invoice_id}/recalc-tds", dependencies=[Depends(require_pack("tds_india", methods=None))])
def recalc_tds(invoice_id: str, body: TdsRecalcRequest):
    """Reset a bill's tds_status to PENDING so the pipeline recalculates it.

    calculate_tds() always adds the bill's base amount on top of the stored FY
    cumulative total rather than replacing it, so before requeuing we unwind this
    bill's own prior contribution (its current tds_base_amount/tds_amount) from
    vendor_tds_fy_state. Otherwise reprocessing would double-count it.
    """
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Resolve stg_bill_id from fact_invoices
            cur.execute(
                "SELECT COALESCE(stg_bill_id, archived_stg_bill_id) FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")
            stg_bill_id = row[0]

            if stg_bill_id:
                cur.execute(
                    """SELECT tenant_id, supplier_id, tds_section_id, bill_date,
                              COALESCE(tds_base_amount, 0), COALESCE(tds_amount, 0)
                       FROM stg_bills WHERE stg_bill_id = %s""",
                    (stg_bill_id,),
                )
                bill = cur.fetchone()
                if bill:
                    b_tenant_id, b_supplier_id, b_section_id, b_bill_date, prev_base, prev_tds = bill
                    if b_section_id and b_bill_date:
                        fy_year = b_bill_date.year if b_bill_date.month >= 4 else b_bill_date.year - 1
                        fy = f"{fy_year}-{str(fy_year + 1)[-2:]}"
                        cur.execute(
                            """UPDATE vendor_tds_fy_state SET
                                   cumulative_billed = GREATEST(0, cumulative_billed - %s),
                                   cumulative_tds_deducted = GREATEST(0, cumulative_tds_deducted - %s),
                                   updated_at = now()
                               WHERE tenant_id = %s AND supplier_id = %s
                                 AND tds_section_id = %s AND fy = %s""",
                            (prev_base, prev_tds, b_tenant_id, b_supplier_id, b_section_id, fy),
                        )

                cur.execute("UPDATE stg_bills SET tds_status = 'PENDING' WHERE stg_bill_id = %s", (stg_bill_id,))
                cur.execute("UPDATE fact_bills SET tds_status = 'PENDING' WHERE stg_bill_id = %s", (stg_bill_id,))
            conn.commit()
    except HTTPException:
        # 404 from the ownership check — must not be reshaped into a 500 by the
        # handler below, which would leak "this bill exists" as a status code.
        conn.rollback()
        put_conn(conn)
        raise
    except Exception as e:
        conn.rollback()
        put_conn(conn, close=True)
        raise HTTPException(500, str(e))
    else:
        put_conn(conn)
    return {"ok": True, "tds_status": "PENDING", "force": body.force}

class TdsOverrideRequest(BaseModel):
    tds_amount: float
    reason: str = ""
    company_id: Optional[str] = None

@router.post("/api/bills/{invoice_id}/tds-override", dependencies=[Depends(require_pack("tds_india", methods=None))])
def tds_manual_override(invoice_id: str, body: TdsOverrideRequest):
    tenant = require_tenant()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Scoped to the caller's tenant — the bill's own tenant_id is read for
            # the audit rows below, never to decide whose bill this is.
            cur.execute(
                """SELECT COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) AS stg_bill_id,
                          fi.tenant_id, fi.supplier_id
                   FROM fact_invoices fi WHERE fi.invoice_id = %s AND fi.tenant_id = %s""",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Bill not found")
            stg_bill_id = row[0]
            tid = str(row[1])
            supplier_id = str(row[2]) if row[2] else None

            # Update stg_bills
            if stg_bill_id:
                cur.execute(
                    """UPDATE stg_bills SET tds_amount = %s, tds_status = 'DEDUCTED',
                              net_payable = total_amount - %s
                       WHERE stg_bill_id = %s""",
                    (body.tds_amount, body.tds_amount, stg_bill_id),
                )
            # Update fact_bills (linked by stg_bill_id)
            cur.execute(
                """UPDATE fact_bills SET tds_amount = %s, tds_status = 'DEDUCTED',
                          net_payable = total_amount - %s
                   WHERE stg_bill_id = %s""",
                (body.tds_amount, body.tds_amount, stg_bill_id),
            )
            # Audit log — vendor_tds_config_log.override_id is NOT NULL with a FK to
            # dim_vendor_tds_overrides, so it can only log against a vendor-level
            # override that actually exists. A bill-level manual override (this
            # endpoint) doesn't have — and shouldn't silently create — a persistent
            # vendor-wide override row just to satisfy that FK (a fake/sentinel UUID
            # here previously made every save fail with a constraint violation).
            if supplier_id:
                cur.execute(
                    "SELECT override_id FROM dim_vendor_tds_overrides "
                    "WHERE supplier_id = %s AND tenant_id = %s AND is_active = true LIMIT 1",
                    (supplier_id, tid),
                )
                override_row = cur.fetchone()
                if override_row:
                    cur.execute(
                        """INSERT INTO vendor_tds_config_log
                               (tenant_id, override_id, field_changed, new_value, reason)
                           VALUES (%s, %s, 'bill_tds_override', %s, %s)""",
                        (tid, override_row[0], str(body.tds_amount), body.reason),
                    )
            conn.commit()
    except HTTPException:
        conn.rollback()
        put_conn(conn, close=True)
        raise
    except Exception as e:
        conn.rollback()
        put_conn(conn, close=True)
        raise HTTPException(500, str(e))
    else:
        put_conn(conn)
    return {"ok": True, "tds_amount": body.tds_amount}
