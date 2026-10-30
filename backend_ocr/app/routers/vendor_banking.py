"""Vendor bank accounts and penny-drop verification.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import datetime
import traceback
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
from ..core.vendor_activity_log import log_vendor_activity, mask_account

from fastapi import APIRouter

from ..core.jurisdiction_guard import require_pack
router = APIRouter()

class BankVerifyRequest(BaseModel):
    account_number: str
    ifsc: str
    # Sent so the provider can confirm the beneficiary name, and so a newly added
    # account row is not left without the bank's display name.
    bank_name: str | None = None
    account_name: str | None = None

@router.post("/api/vendors/{supplier_id}/verify-bank", dependencies=[Depends(require_pack("bank_india", methods=None))])
def verify_vendor_bank(supplier_id: str, body: BankVerifyRequest):
    """Verify a vendor bank account against Sandbox penny-less, synchronously.

    Called straight from the review screen so the user gets an answer while the bill
    is still in front of them, rather than waiting for the bank_verification pipeline
    to come round. On success the account is recorded against the vendor -- added if
    it is new, or flipped to verified if we already held the same account unverified
    -- so the next bill from this vendor arrives with a verified bank block.
    """
    from ..integrations.sandbox_kyb import verify_bank_account
    tenant = require_tenant()  # tenant from JWT; a client-supplied id is ignored

    acct = (body.account_number or "").strip()
    ifsc = (body.ifsc or "").strip().upper()
    if not acct or not ifsc:
        raise HTTPException(status_code=422, detail="Account number and IFSC are both required")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Scope to the tenant BEFORE calling the provider: verifying an account
            # for someone else's supplier_id would both leak and bill the wrong org.
            cur.execute(
                "SELECT name FROM dim_suppliers WHERE supplier_id = %s AND tenant_id = %s",
                (supplier_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Vendor not found")
            vendor_name = row[0]
    finally:
        put_conn(conn)

    result = verify_bank_account(acct, ifsc, name=body.account_name or vendor_name)
    if not result.get("valid"):
        # 'skipped' means the provider was unavailable, not that the account is bad --
        # surfacing them the same way would teach users to distrust a real failure.
        detail = result.get("error") or "Account not found"
        raise HTTPException(status_code=503 if result.get("skipped") else 422,
                            detail=f"Bank account verification failed: {detail}")

    name_at_bank = result.get("name_at_bank")
    persisted = False
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # Same account already on file -> mark it verified rather than adding a
            # duplicate row (the vendor page lists these, and the payment guardrail
            # picks the primary one).
            cur.execute(
                """UPDATE supplier_bank_accounts
                   SET verified = true,
                       name_at_bank = COALESCE(%s, name_at_bank),
                       bank_name    = COALESCE(NULLIF(%s, ''), bank_name)
                   WHERE tenant_id = %s AND supplier_id = %s
                     AND regexp_replace(account_number, '[[:space:]]', '', 'g') = %s
                     AND upper(ifsc) = %s
                   RETURNING id""",
                (name_at_bank, body.bank_name, tenant, supplier_id, acct, ifsc),
            )
            existing = cur.fetchone()
            if existing:
                log_vendor_activity(
                    cur, supplier_id, "bank_verified",
                    f"Bank account {mask_account(acct)} verified"
                    + (f" · name at bank: {name_at_bank}" if name_at_bank else ""),
                    meta={"entity_id": str(existing[0]), "ifsc": ifsc}, tenant_id=tenant)
            else:
                # First account for this vendor becomes primary, so the payment path
                # has something to select without a further decision.
                cur.execute(
                    """INSERT INTO supplier_bank_accounts
                           (tenant_id, supplier_id, account_number, ifsc, bank_name,
                            is_primary, verified, name_at_bank)
                       VALUES (%s, %s, %s, %s, %s,
                               NOT EXISTS (SELECT 1 FROM supplier_bank_accounts
                                            WHERE tenant_id = %s AND supplier_id = %s),
                               true, %s)
                       RETURNING id""",
                    (tenant, supplier_id, acct, ifsc, body.bank_name or "",
                     tenant, supplier_id, name_at_bank),
                )
                new_id = cur.fetchone()[0]
                log_vendor_activity(
                    cur, supplier_id, "bank_added",
                    f"Added bank account {mask_account(acct)} (verified)",
                    changes=[{"field": "Bank account", "from": None, "to": mask_account(acct)},
                             {"field": "IFSC", "from": None, "to": ifsc}],
                    meta={"entity_id": str(new_id), "ifsc": ifsc, "verified": True}, tenant_id=tenant)
            # Keep the legacy columns in step: disbursement_api still reads them as a
            # fallback, and leaving them stale would show an unverified bank there.
            cur.execute(
                """UPDATE dim_suppliers
                   SET bank_account = %s, bank_ifsc = %s, bank_verified = true,
                       bank_name = COALESCE(NULLIF(%s, ''), bank_name)
                   WHERE supplier_id = %s AND tenant_id = %s""",
                (acct, ifsc, body.bank_name, supplier_id, tenant),
            )
        conn.commit()
        persisted = True
    except Exception:
        conn.rollback()
        traceback.print_exc()
    finally:
        put_conn(conn)

    return {
        "verified": True,
        # The provider said yes but we could not record it -- the caller must not
        # present this as a stored verified account.
        "persisted": persisted,
        "name_at_bank": name_at_bank,
        "account_number": acct,
        "ifsc": ifsc,
    }

def _get_vendor_bank_accounts_list(cur, tenant_id: str, supplier_id: str):
    cur.execute("""
        SELECT id::text, account_number, ifsc, bank_name, is_primary, verified, name_at_bank, created_at
        FROM supplier_bank_accounts
        WHERE tenant_id=%s AND supplier_id=%s
        ORDER BY is_primary DESC, created_at ASC
    """, (tenant_id, supplier_id))
    accounts = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT bank_account, bank_ifsc, bank_verified, bank_name FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s LIMIT 1",
                (supplier_id, tenant_id))
    legacy = cur.fetchone()

    if legacy and legacy["bank_account"]:
        legacy_acct_num = legacy["bank_account"]
        is_already_present = any(a["account_number"] == legacy_acct_num for a in accounts)
        if not is_already_present:
            has_primary = any(a["is_primary"] for a in accounts)
            accounts.append({
                "id": "legacy",
                "account_number": legacy["bank_account"],
                "ifsc": legacy["bank_ifsc"],
                "bank_name": legacy.get("bank_name") or "Legacy Account",
                "is_primary": not has_primary,
                "verified": bool(legacy["bank_verified"]),
                "name_at_bank": None,
                "created_at": None
            })

    # Sort again so primary is always first
    import datetime
    accounts.sort(key=lambda a: (not a["is_primary"], a["created_at"] or datetime.datetime.min))
    return accounts

@router.get("/api/vendors/{supplier_id}/bank-accounts")
async def list_vendor_bank_accounts(supplier_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
            row = cur.fetchone()
            if not row:
                put_conn(conn)
                return []
            tenant_id = str(row["tenant_id"])
        accounts = _get_vendor_bank_accounts_list(cur, tenant_id, supplier_id)
        put_conn(conn)
        return accounts
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to fetch bank accounts")

class BankAccountBody(BaseModel):
    account_number: str
    ifsc: str | None = None
    bank_name: str | None = None
    is_primary: bool = False
    company_id: str | None = None

@router.post("/api/vendors/{supplier_id}/bank-accounts")
async def add_vendor_bank_account(supplier_id: str, body: BankAccountBody):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
            row = cur.fetchone()
            if not row:
                put_conn(conn)
                raise HTTPException(status_code=404, detail="Supplier not found")
            tenant_id = str(row["tenant_id"])
        if body.is_primary:
            cur.execute("UPDATE supplier_bank_accounts SET is_primary=false WHERE tenant_id=%s AND supplier_id=%s",
                        (tenant_id, supplier_id))
        cur.execute("""
            INSERT INTO supplier_bank_accounts (tenant_id, supplier_id, account_number, ifsc, bank_name, is_primary)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id::text, account_number, ifsc, bank_name, is_primary, verified, name_at_bank, created_at
        """, (tenant_id, supplier_id, body.account_number, body.ifsc, body.bank_name, body.is_primary))
        result = dict(cur.fetchone())
        masked = mask_account(body.account_number)
        log_vendor_activity(
            cur, supplier_id, "bank_added",
            f"Added bank account {masked}" + (f" ({body.bank_name})" if body.bank_name else "")
            + (" as primary" if body.is_primary else ""),
            changes=[c for c in (
                {"field": "Bank account", "from": None, "to": masked},
                {"field": "IFSC", "from": None, "to": body.ifsc} if body.ifsc else None,
            ) if c],
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
        raise HTTPException(status_code=500, detail="Failed to add bank account")

@router.delete("/api/vendors/{supplier_id}/bank-accounts/{account_id}")
async def delete_vendor_bank_account(supplier_id: str, account_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
            row = cur.fetchone()
            if not row:
                put_conn(conn)
                raise HTTPException(status_code=404, detail="Supplier not found")
            tenant_id = str(row["tenant_id"])
        cur.execute("SELECT id, is_primary, account_number, bank_name FROM supplier_bank_accounts WHERE id=%s AND tenant_id=%s AND supplier_id=%s",
                    (account_id, tenant_id, supplier_id))
        acct = cur.fetchone()
        if not acct:
            put_conn(conn)
            raise HTTPException(status_code=404, detail="Account not found")
        cur.execute("SELECT COUNT(*) as cnt FROM supplier_bank_accounts WHERE tenant_id=%s AND supplier_id=%s",
                    (tenant_id, supplier_id))
        cnt = cur.fetchone()["cnt"]
        if cnt == 1 and acct["is_primary"]:
            put_conn(conn)
            raise HTTPException(status_code=400, detail="Cannot delete the only primary account")
        cur.execute("DELETE FROM supplier_bank_accounts WHERE id=%s", (account_id,))
        masked = mask_account(acct["account_number"])
        log_vendor_activity(
            cur, supplier_id, "bank_deleted",
            f"Removed bank account {masked}" + (f" ({acct['bank_name']})" if acct.get("bank_name") else ""),
            changes=[{"field": "Bank account", "from": masked, "to": None}],
            meta={"entity_id": str(acct["id"])}, tenant_id=tenant_id)
        conn.commit()
        put_conn(conn)
        return {"deleted": True}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to delete bank account")

@router.post("/api/vendors/{supplier_id}/bank-accounts/{account_id}/set-primary")
async def set_primary_bank_account(supplier_id: str, account_id: str, company_id: str = ""):
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
            row = cur.fetchone()
            if not row:
                put_conn(conn)
                raise HTTPException(status_code=404, detail="Supplier not found")
            tenant_id = str(row["tenant_id"])
        cur.execute("UPDATE supplier_bank_accounts SET is_primary=false WHERE tenant_id=%s AND supplier_id=%s",
                    (tenant_id, supplier_id))
        cur.execute("UPDATE supplier_bank_accounts SET is_primary=true WHERE id=%s AND tenant_id=%s AND supplier_id=%s"
                    " RETURNING account_number",
                    (account_id, tenant_id, supplier_id))
        made_primary = cur.fetchone()
        if made_primary:
            log_vendor_activity(
                cur, supplier_id, "bank_primary_changed",
                f"Made bank account {mask_account(made_primary['account_number'])} the primary account",
                meta={"entity_id": account_id}, tenant_id=tenant_id)
        conn.commit()
        accounts = _get_vendor_bank_accounts_list(cur, tenant_id, supplier_id)
        put_conn(conn)
        return accounts
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to set primary account")

@router.post("/api/vendors/{supplier_id}/reverify-bank", dependencies=[Depends(require_pack("bank_india", methods=None))])
async def reverify_bank(supplier_id: str, company_id: str = "", account_id: str = ""):
    """Reset bank_verified to false so the bank_verification pipeline re-verifies.

    account_id (optional) targets one row in supplier_bank_accounts — a vendor can
    have several accounts, each independently polled by bank_verification.py, so
    resetting only dim_suppliers.bank_verified (the vendor's own single legacy
    account) would silently do nothing for a specific supplier_bank_accounts row.
    Without account_id, falls back to the legacy dim_suppliers.bank_verified reset.
    """
    conn = None
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        tenant_id = require_tenant()  # tenant from JWT; client company_id ignored
        if not tenant_id:
            cur.execute("SELECT tenant_id FROM dim_suppliers WHERE supplier_id=%s LIMIT 1", (supplier_id,))
            row = cur.fetchone()
            if not row:
                put_conn(conn)
                raise HTTPException(status_code=404, detail="Supplier not found")
            tenant_id = str(row["tenant_id"])

        if account_id:
            cur.execute(
                "UPDATE supplier_bank_accounts SET verified = false"
                " WHERE id = %s AND tenant_id = %s AND supplier_id = %s"
                " RETURNING account_number",
                (account_id, tenant_id, supplier_id),
            )
            reset = cur.fetchone()
            if not reset:
                put_conn(conn)
                raise HTTPException(status_code=404, detail="Bank account not found")
        else:
            cur.execute(
                "UPDATE dim_suppliers SET bank_verified = false WHERE supplier_id = %s AND tenant_id = %s"
                " RETURNING bank_account AS account_number",
                (supplier_id, tenant_id),
            )
            reset = cur.fetchone()
        acct_no = reset["account_number"] if reset else None
        log_vendor_activity(
            cur, supplier_id, "bank_reverify_requested",
            f"Asked to re-verify bank account {mask_account(acct_no)}" if acct_no
            else "Asked to re-verify the bank account",
            meta={"entity_id": account_id} if account_id else None, tenant_id=tenant_id)
        conn.commit()
        put_conn(conn)
        return {"ok": True, "message": "Bank verification queued — pipeline will re-verify shortly"}
    except HTTPException:
        if conn: put_conn(conn, close=True)
        raise
    except Exception:
        if conn: put_conn(conn, close=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to queue reverification")
