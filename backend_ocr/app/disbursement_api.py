"""Vendor Disbursement (IMPS) — DEMO/MOCK implementation.

Per docs/DISBURSEMENT_PAYMENT_INTEGRATION_PLAN.md, scoped down for a showcase
build. Every route is tenant-scoped from the verified token via `_tid()`, and the
module as a whole is gated by the `disbursement` service licence:

  - No real bank call is ever made. `_mock_imps_initiate` / `_mock_imps_status`
    fabricate responses shaped exactly like docs/DISBURSEMENT_IMPS_API.md so the
    UI, DB, and audit trail all behave like the real integration would — this
    is intentional and must stay clearly labelled DEMO in the UI; it is not
    wired to api-dev.lendix.ai and moves no real money.
  - The "RSA-based verification" the API doc mentions is a bank-gateway detail
    we don't implement (§8.2 of the plan is still open). What *is* implemented
    here is the plan's independent 2FA control (§5.5/§8.4): a one-time email
    passcode, sent via the SMTP creds already in config.py, that gates the
    (mock) bank call. There is no TOTP/authenticator-app step in this demo cut
    — email OTP only. A demo environment can set config.DISBURSEMENT_DEMO_OTP
    (e.g. 123456) to also accept that fixed code, so a walkthrough doesn't need
    inbox access; the OTP step then shows the code, and its use is recorded in
    the audit trail as `demo_otp: true`. Unset — the default, and what any
    non-demo environment must keep — only the real emailed OTP works and the UI
    shows no shortcut.
  - `remitter_accounts` is a new, demo-only table holding one fake ICICI
    account per tenant with a dummy balance, decremented on each simulated
    SUCCESS so the KPI strip (§ stats endpoint) moves realistically over a demo.

Guardrail (§7.1/§7.2 of the plan): a payment only proceeds against the vendor's
own verified bank account (supplier_bank_accounts.verified / legacy
dim_suppliers.bank_verified) AND a verified GSTIN (dim_suppliers.status ==
'VERIFIED', set by pipelines/gst_verification.py). Only a caller with
actor_role == 'admin' may initiate a payment at all; only an admin may supply
an override block that substitutes different beneficiary details when the
vendor isn't (yet) verified — every override is written to
`disbursement_overrides` plus a `wf_audit_log` diff, never silently applied.

Routes
──────
GET  /api/vendors/{supplier_id}/payment-profile   GSTINs, bank accounts, pending bills for one vendor
GET  /api/disbursements/stats                     remitter balance + txn KPIs (dump data)
GET  /api/bills/{invoice_id}/disbursements         payment history for one bill
POST /api/bills/{invoice_id}/disbursements         start a payment (admin-only) → emails OTP
POST /api/disbursements/{disbursement_id}/verify-otp   confirm OTP → mock bank call
POST /api/disbursements/{disbursement_id}/resend-otp   regenerate + resend OTP
POST /api/disbursements/{disbursement_id}/refresh-status  poll a PENDING mock txn
"""
from __future__ import annotations

import hashlib
import hmac
import json
import random
import smtplib
import string
import time
import traceback
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth import require_role, require_tenant
from .config import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL,
    DISBURSEMENT_DEMO_OTP, get_conn, put_conn,
)

router = APIRouter(prefix="/api", tags=["Disbursement (Demo)"])

OTP_TTL_SECONDS = 120  # 2 minutes — tighter than the password-reset OTP convention since this gates a live money movement
MAX_OTP_ATTEMPTS = 5
DEMO_BANK_PROVIDER = "ICICI"
DEMO_REMITTER_ACCOUNT = "010101000"  # matches the doc's own sample remitterAccountNumber


def _tid(company_id: Optional[str] = None) -> str:
    """Authenticated tenant for this request.

    The ``company_id`` argument is accepted only so the existing call sites and
    request models keep their shape — it is deliberately **ignored**. It used to
    be returned verbatim (falling back to the demo ``TENANT_ID``), which let any
    logged-in user read or move money for another tenant by passing that tenant's
    id in a query param or request body. Tenant now comes from the verified token,
    matching the convention every handler in main.py already follows.
    """
    return require_tenant()


def _dictcur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _jsonable_row(row: dict) -> dict:
    from decimal import Decimal
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _ensure_tables():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS remitter_accounts (
                id               uuid        NOT NULL DEFAULT gen_random_uuid(),
                tenant_id        uuid        NOT NULL,
                account_number   text        NOT NULL,
                bank_name        text        NOT NULL,
                ifsc             text,
                bank_provider    text        NOT NULL DEFAULT 'ICICI',
                balance          numeric     NOT NULL DEFAULT 5000000,
                notify_email     text,
                created_at       timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (id),
                UNIQUE (tenant_id, account_number)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_disbursements (
                disbursement_id   uuid        NOT NULL DEFAULT gen_random_uuid(),
                tenant_id         uuid        NOT NULL,
                invoice_id        uuid        NOT NULL,
                supplier_id       uuid,
                amount            numeric     NOT NULL,
                beneficiary_name  text        NOT NULL,
                beneficiary_account text      NOT NULL,
                beneficiary_ifsc  text        NOT NULL,
                beneficiary_mobile text,
                remitter_account_number text  NOT NULL,
                bank_provider     text        NOT NULL DEFAULT 'ICICI',
                tran_ref_no       text        NOT NULL,
                bank_tran_ref_no  text,
                bank_rrn          text,
                status            text        NOT NULL DEFAULT 'PENDING_OTP',
                status_message    text,
                act_code          text,
                otp_code_hash     text,
                otp_expires_at    double precision,
                otp_verified_at   timestamptz,
                otp_attempts      int         NOT NULL DEFAULT 0,
                initiated_by      text,
                raw_response_jsonb jsonb,
                is_overridden     bool        NOT NULL DEFAULT false,
                override_id       uuid,
                created_at        timestamptz NOT NULL DEFAULT now(),
                updated_at        timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (disbursement_id),
                UNIQUE (tenant_id, tran_ref_no)
            );
            CREATE INDEX IF NOT EXISTS idx_fdis_tenant_invoice ON fact_disbursements (tenant_id, invoice_id);
            CREATE INDEX IF NOT EXISTS idx_fdis_tenant_status ON fact_disbursements (tenant_id, status);
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS is_overridden bool NOT NULL DEFAULT false;
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS override_id uuid;
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS disbursement_overrides (
                override_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
                tenant_id              uuid        NOT NULL,
                invoice_id             uuid        NOT NULL,
                supplier_id            uuid,
                overridden_by          text,
                overridden_by_role     text,
                reason                 text        NOT NULL,
                original_account_number text,
                original_ifsc          text,
                original_gstin_verified bool,
                 override_account_number text       NOT NULL,
                override_ifsc           text        NOT NULL,
                override_beneficiary_name text     NOT NULL,
                created_at             timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (override_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dover_tenant_supplier ON disbursement_overrides (tenant_id, supplier_id);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] disbursement demo tables ready")
    except Exception:
        print("[startup] ERROR creating disbursement demo tables:")
        traceback.print_exc()


_ensure_tables()


def _get_or_seed_remitter_account(cur, tenant_id: str) -> dict:
    cur.execute(
        "SELECT * FROM remitter_accounts WHERE tenant_id=%s ORDER BY created_at LIMIT 1",
        (tenant_id,),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    cur.execute(
        """INSERT INTO remitter_accounts (tenant_id, account_number, bank_name, ifsc, bank_provider, balance, notify_email)
           VALUES (%s,%s,%s,%s,%s,%s,%s)
           RETURNING *""",
        (tenant_id, DEMO_REMITTER_ACCOUNT, "ICICI Bank (Demo)", "ICIC0000101", DEMO_BANK_PROVIDER, 5000000, SMTP_ADMIN_EMAIL),
    )
    return dict(cur.fetchone())


def _get_verified_vendor_account(cur, tenant_id: str, supplier_id: str) -> Optional[dict]:
    """Mirrors main.py's vendor bank-account resolution: prefer a verified row in
    supplier_bank_accounts (primary first), fall back to the legacy dim_suppliers
    columns if those are verified. Returns None if nothing verified is on file."""
    cur.execute(
        """SELECT account_number, ifsc, bank_name, name_at_bank FROM supplier_bank_accounts
           WHERE tenant_id=%s AND supplier_id=%s AND verified=true
           ORDER BY is_primary DESC, created_at ASC LIMIT 1""",
        (tenant_id, supplier_id),
    )
    row = cur.fetchone()
    if row:
        return dict(row)
    cur.execute(
        "SELECT bank_account, bank_ifsc, bank_verified, name AS vendor_name FROM dim_suppliers WHERE tenant_id=%s AND supplier_id=%s",
        (tenant_id, supplier_id),
    )
    legacy = cur.fetchone()
    if legacy and legacy["bank_verified"] and legacy["bank_account"]:
        return {
            "account_number": legacy["bank_account"],
            "ifsc": legacy["bank_ifsc"],
            "bank_name": None,
            "name_at_bank": legacy["vendor_name"],
        }
    return None


def _mask_account(account_number: str) -> str:
    return ("*" * max(0, len(account_number) - 4)) + account_number[-4:]


def _get_supplier_kyb(cur, tenant_id: str, supplier_id: str) -> Optional[dict]:
    """`dim_suppliers.status` is the vendor's KYB/GST verification flag — driven
    by pipelines/gst_verification.py (UNVERIFIED → VERIFIED/FAILED), same field
    shown as the vendor list's status badge. `status == 'VERIFIED'` is what this
    module treats as "GSTIN verified" for the payment guardrail."""
    cur.execute(
        "SELECT name, gstn, status FROM dim_suppliers WHERE tenant_id=%s AND supplier_id=%s",
        (tenant_id, supplier_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ── GET /api/vendors/{supplier_id}/payment-profile ───────────────────────────

@router.get("/vendors/{supplier_id}/payment-profile")
def payment_profile(supplier_id: str, company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            supplier = _get_suppliZer_kyb(cur, tenant_id, supplier_id)
            if not supplier:
                raise HTTPException(status_code=404, detail="Vendor not found")
            gstin_verified = supplier["status"] == "VERIFIED"

            cur.execute(
                "SELECT gstin, status, trade_name FROM supplier_gstins WHERE tenant_id=%s AND supplier_id=%s ORDER BY is_primary DESC",
                (tenant_id, supplier_id),
            )
            additional_gstins = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT id::text, account_number, ifsc, bank_name, is_primary, verified, name_at_bank
                   FROM supplier_bank_accounts WHERE tenant_id=%s AND supplier_id=%s
                   ORDER BY is_primary DESC, created_at ASC""",
                (tenant_id, supplier_id),
            )
            bank_accounts = [dict(r) for r in cur.fetchall()]
            if not bank_accounts:
                cur.execute(
                    "SELECT bank_account, bank_ifsc, bank_verified, bank_name FROM dim_suppliers WHERE tenant_id=%s AND supplier_id=%s",
                    (tenant_id, supplier_id),
                )
                legacy = cur.fetchone()
                if legacy and legacy["bank_account"]:
                    bank_accounts = [{
                        "id": "legacy", "account_number": legacy["bank_account"], "ifsc": legacy["bank_ifsc"],
                        "bank_name": legacy["bank_name"], "is_primary": True, "verified": bool(legacy["bank_verified"]),
                        "name_at_bank": None,
                    }]
            for b in bank_accounts:
                b["account_number_masked"] = _mask_account(b["account_number"])
                del b["account_number"]
            bank_verified = any(b["verified"] for b in bank_accounts)

            cur.execute(
                """SELECT invoice_id, invoice_number AS id, invoice_date, due_date, total_amount, outstanding_amount
                   FROM fact_invoices WHERE tenant_id=%s AND supplier_id=%s AND outstanding_amount > 0
                   ORDER BY due_date ASC NULLS LAST""",
                (tenant_id, supplier_id),
            )
            bills = [_jsonable_row(r) for r in cur.fetchall()]

        return {
            "vendor_name": supplier["name"],
            "primary_gstin": supplier["gstn"],
            "kyb_status": supplier["status"],
            "gstin_verified": gstin_verified,
            "additional_gstins": additional_gstins,
            "bank_accounts": bank_accounts,
            "bank_verified": bank_verified,
            "can_pay": bank_verified and gstin_verified,
            "bills": bills,
        }
    finally:
        put_conn(conn)


def _gen_tran_ref_no() -> str:
    # Bank caps this at 20 chars (docs/DISBURSEMENT_IMPS_API.md).
    return "PAY" + uuid.uuid4().hex[:14].upper()


def _get_vendor_name_and_month(cur, tenant_id: str, invoice_id: str, supplier_id: Optional[str]) -> tuple:
    """Resolves the two Vendor Payment Module metadata fields (docs/DISBURSEMENT_IMPS_API.md
    § Vendor Payment Module — `vendorName`/`month`) for a bill: the payee's name and the
    billing period being settled. Both are request-only metadata per that doc ("validated,
    logged, and stored but not sent to the bank") — captured here for the audit trail only,
    never persisted as new fact_disbursements columns since they're always re-derivable from
    invoice_id/supplier_id, which are already stored."""
    vendor_name = None
    if supplier_id:
        cur.execute("SELECT name FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant_id))
        row = cur.fetchone()
        vendor_name = row["name"] if row else None
    cur.execute(
        """SELECT sb.service_month FROM fact_invoices fi
           LEFT JOIN stg_bills sb ON COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) = sb.stg_bill_id
                                  AND sb.tenant_id = fi.tenant_id
           WHERE fi.invoice_id=%s AND fi.tenant_id=%s""",
        (invoice_id, tenant_id),
    )
    row = cur.fetchone()
    month = row["service_month"] if row else None
    return vendor_name, month


def _mock_imps_initiate(tran_ref_no: str, amount: float, beneficiary_name: str,
                         vendor_name: Optional[str] = None, month: Optional[str] = None) -> dict:
    """Fabricates a response shaped like the real POST /vendor-payment/pay
    (docs/DISBURSEMENT_IMPS_API.md § Vendor Payment Module) — no network call. This
    endpoint exclusively pays vendor bills, so it mirrors that module (not the generic
    /disbursement/imps/initiate) — `vendorInfo.endpoint` reflects that, and the
    request-only `vendorName`/`month` metadata fields are echoed under `requestMetadata`
    purely so they land in `raw_response_jsonb`/the audit trail; the real bank response
    never echoes them back (they're stripped before the bank call per that doc's Notes).
    Weighted outcome so a demo naturally shows all three states without needing special
    trigger values."""
    roll = random.random()
    if roll < 0.75:
        status, status_message, act_code = "SUCCESS", "Transaction Successful", "0"
    elif roll < 0.90:
        status, status_message, act_code = "PENDING", "Gateway processing — check status", "997"
    else:
        status, status_message, act_code = "FAILED", "Insufficient balance in remitter account", "91"
    return {
        "success": status != "FAILED",
        "data": {
            "tranRefNo": tran_ref_no,
            "bankRRN": "".join(random.choices(string.digits, k=12)) if status != "FAILED" else None,
            "beneficiaryName": beneficiary_name,
            "remitterAccountNumber": DEMO_REMITTER_ACCOUNT,
            "status": status,
            "statusMessage": status_message,
        },
        "vendorInfo": {
            "vendorName": DEMO_BANK_PROVIDER, "vendorPriority": 1,
            "endpoint": "VENDOR_PAYMENT_INITIATE", "reason": "HEALTH_BASED_SELECTION",
        },
        "requestMetadata": {"vendorName": vendor_name, "month": month},
        "actCode": act_code,
    }


def _mock_imps_status(disbursement: dict) -> dict:
    """Fabricates a POST /vendor-payment/status response for a PENDING mock
    transaction — always resolves to SUCCESS on the first status check, matching
    the doc's description of PENDING as a transient gateway state."""
    bank_rrn = disbursement["bank_rrn"] or "".join(random.choices(string.digits, k=12))
    return {
        "success": True,
        "data": {
            "tranRefNo": disbursement["tran_ref_no"],
            "bankRRN": bank_rrn,
            "beneficiaryName": disbursement["beneficiary_name"],
            "remitterAccountNumber": DEMO_REMITTER_ACCOUNT,
            "status": "SUCCESS",
            "statusMessage": "BC Outward transaction Successful",
        },
        "vendorInfo": {
            "vendorName": DEMO_BANK_PROVIDER, "vendorPriority": 1,
            "endpoint": "VENDOR_PAYMENT_STATUS", "reason": "HEALTH_BASED_SELECTION",
        },
    }


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _check_otp(supplied: str, stored_hash: Optional[str]) -> tuple[bool, bool]:
    """Returns (ok, via_demo_code).

    This module is a mock IMPS demo, so a fixed demo code (config's
    DISBURSEMENT_DEMO_OTP) is accepted alongside the emailed OTP — a walkthrough
    shouldn't stall on inbox access. The demo code also skips the 2-minute expiry
    and attempt cap, which exist to protect a real money movement; `via_demo_code`
    is returned so the caller can still record its use in the audit trail. Set
    DISBURSEMENT_DEMO_OTP=off to require the real emailed code only.
    """
    supplied = (supplied or "").strip()
    if DISBURSEMENT_DEMO_OTP and hmac.compare_digest(supplied, DISBURSEMENT_DEMO_OTP):
        return True, True
    if not stored_hash:
        return False, False
    return hmac.compare_digest(_hash_otp(supplied), stored_hash), False


def _send_otp_email(to_email: str, otp: str, vendor_name: str, amount: float) -> None:
    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:480px;margin:auto;color:#333;">
      <h2 style="color:#004080;">[DEMO] Vendor Payment Confirmation</h2>
      <p>A disbursement of <b>₹{amount:,.2f}</b> to <b>{vendor_name}</b> is awaiting your confirmation.</p>
      <div style="font-size:36px;font-weight:bold;letter-spacing:10px;color:#004080;
                  background:#eff4ff;padding:20px;text-align:center;border-radius:8px;margin:24px 0;">
        {otp}
      </div>
      <p>This code is valid for <b>2 minutes</b>. Do not share it with anyone.</p>
      <p style="font-size:12px;color:#888;">Demo environment — no real funds move.</p>
    </body></html>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[DEMO] Vendor Payment OTP"
    msg["From"] = SMTP_USER
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return (name[:2] + "***@" + domain) if len(name) > 2 else ("*** @" + domain)


def _audit(cur, tenant_id: str, disbursement_id: str, old: Optional[dict], new: dict, actor: Optional[str]):
    cur.execute(
        """INSERT INTO wf_audit_log (tenant_id, table_name, entity_id, action, old_values_jsonb, new_values_jsonb)
           VALUES (%s, 'fact_disbursements', %s, %s, %s, %s)""",
        (
            tenant_id, disbursement_id, "UPDATE" if old else "INSERT",
            json.dumps(old, default=str) if old else None,
            json.dumps({**new, "actor": actor}, default=str),
        ),
    )


# ── GET /api/disbursements/stats ─────────────────────────────────────────────

@router.get("/disbursements/stats")
def disbursement_stats(company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            remitter = _get_or_seed_remitter_account(cur, tenant_id)
            conn.commit()
            cur.execute(
                """SELECT
                       count(*) FILTER (WHERE status = 'SUCCESS') AS successful,
                       count(*) FILTER (WHERE status = 'FAILED') AS failed,
                       count(*) FILTER (WHERE status = 'PENDING') AS pending,
                       count(*) AS total,
                       COALESCE(sum(amount) FILTER (WHERE status = 'SUCCESS'), 0) AS total_disbursed
                   FROM fact_disbursements WHERE tenant_id=%s""",
                (tenant_id,),
            )
            agg = dict(cur.fetchone())
        return {
            "remitter_account_number": remitter["account_number"],
            "bank_name": remitter["bank_name"],
            "bank_provider": remitter["bank_provider"],
            "balance": float(remitter["balance"]),
            "total_transactions": agg["total"],
            "successful_payments": agg["successful"],
            "failed_payments": agg["failed"],
            "pending_payments": agg["pending"],
            "total_disbursed": float(agg["total_disbursed"]),
            "demo": True,
        }
    finally:
        put_conn(conn)


# ── GET /api/bills/{invoice_id}/disbursements ────────────────────────────────

@router.get("/bills/{invoice_id}/disbursements")
def list_disbursements(invoice_id: str, company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT disbursement_id, amount, status, status_message, bank_rrn, bank_provider,
                          tran_ref_no, initiated_by, created_at, updated_at
                   FROM fact_disbursements WHERE tenant_id=%s AND invoice_id=%s
                   ORDER BY created_at DESC""",
                (tenant_id, invoice_id),
            )
            return {"disbursements": [dict(r) for r in cur.fetchall()]}
    finally:
        put_conn(conn)


# ── Shared guardrail + override resolution (single-bill and bulk both use this) ──

class OverrideFields(BaseModel):
    company_id: Optional[str] = None
    actor_name: Optional[str] = None
    actor_role: Optional[str] = None
    # Admin-only manual override — substitutes different beneficiary details
    # when the vendor's bank account and/or GSTIN aren't (yet) verified.
    # Every override is written to disbursement_overrides + wf_audit_log (§7.2).
    override: bool = False
    override_account_number: Optional[str] = None
    override_ifsc: Optional[str] = None
    override_beneficiary_name: Optional[str] = None
    override_reason: Optional[str] = None


def _resolve_beneficiary(cur, tenant_id: str, invoice_id: str, supplier_id: str, body: "OverrideFields"):
    """Enforces the verified-bank + verified-GSTIN guardrail (§7.1) and, when an
    admin override is supplied, writes disbursement_overrides + a wf_audit_log
    diff (§7.2) before returning the beneficiary details to actually pay to.
    Raises HTTPException on any guardrail failure. One override row is written
    per bill even in a bulk batch, so each bill's audit trail stands alone."""
    supplier = _get_supplier_kyb(cur, tenant_id, supplier_id)
    gstin_verified = bool(supplier and supplier["status"] == "VERIFIED")
    vendor_account = _get_verified_vendor_account(cur, tenant_id, supplier_id)

    if vendor_account and gstin_verified and not body.override:
        return (
            vendor_account["account_number"], vendor_account["ifsc"] or "NPCI0000001",
            vendor_account.get("name_at_bank") or (supplier["name"] if supplier else "Vendor"),
            False, None,
        )
    if body.override:
        if not (body.override_account_number and body.override_ifsc and body.override_beneficiary_name and body.override_reason):
            raise HTTPException(status_code=400, detail="Override requires account number, IFSC, beneficiary name, and a reason")
        beneficiary_account = body.override_account_number
        beneficiary_ifsc = body.override_ifsc
        beneficiary_name = body.override_beneficiary_name
        cur.execute(
            """INSERT INTO disbursement_overrides
                   (tenant_id, invoice_id, supplier_id, overridden_by, overridden_by_role, reason,
                    original_account_number, original_ifsc, original_gstin_verified,
                    override_account_number, override_ifsc, override_beneficiary_name)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING override_id""",
            (
                tenant_id, invoice_id, supplier_id, body.actor_name, body.actor_role, body.override_reason,
                vendor_account["account_number"] if vendor_account else None,
                vendor_account["ifsc"] if vendor_account else None,
                gstin_verified,
                beneficiary_account, beneficiary_ifsc, beneficiary_name,
            ),
        )
        override_id = cur.fetchone()["override_id"]
        cur.execute(
            """INSERT INTO wf_audit_log (tenant_id, table_name, entity_id, action, old_values_jsonb, new_values_jsonb)
               VALUES (%s, 'disbursement_overrides', %s, 'INSERT', %s, %s)""",
            (
                tenant_id, override_id,
                json.dumps({
                    "vendor_bank_verified": bool(vendor_account), "gstin_verified": gstin_verified,
                    "original_account_number": vendor_account["account_number"] if vendor_account else None,
                    "original_ifsc": vendor_account["ifsc"] if vendor_account else None,
                }, default=str),
                json.dumps({
                    "override_account_number": beneficiary_account, "override_ifsc": beneficiary_ifsc,
                    "override_beneficiary_name": beneficiary_name, "reason": body.override_reason,
                    "overridden_by": body.actor_name, "overridden_by_role": body.actor_role,
                }, default=str),
            ),
        )
        return beneficiary_account, beneficiary_ifsc, beneficiary_name, True, override_id
    raise HTTPException(
        status_code=400,
        detail="Vendor bank account and/or GSTIN not verified — an admin override with a reason is required",
    )


# ── POST /api/bills/{invoice_id}/disbursements ───────────────────────────────

class DisbursementCreate(OverrideFields):
    amount: float


@router.post("/bills/{invoice_id}/disbursements")
def initiate_disbursement(invoice_id: str, body: DisbursementCreate):
    tenant_id = _tid(body.company_id)
    # Role comes from the verified token, not body.actor_role — the body field is
    # client-controlled, so the old check was satisfied by simply sending
    # actor_role="admin". body.actor_name/actor_role remain for the audit trail.
    require_role("admin")

    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                """SELECT invoice_id, tenant_id, supplier_id, outstanding_amount
                   FROM fact_invoices WHERE invoice_id=%s AND tenant_id=%s""",
                (invoice_id, tenant_id),
            )
            bill = cur.fetchone()
            if not bill:
                raise HTTPException(status_code=404, detail="Bill not found")
            outstanding = float(bill["outstanding_amount"] or 0)
            if body.amount <= 0 or body.amount > outstanding + 0.01:
                raise HTTPException(status_code=400, detail=f"Amount must be between ₹1 and the outstanding ₹{outstanding:,.2f}")

            if not bill["supplier_id"]:
                raise HTTPException(status_code=400, detail="Bill has no linked vendor")
            supplier_id = str(bill["supplier_id"])
            beneficiary_account, beneficiary_ifsc, beneficiary_name, is_overridden, override_id = \
                _resolve_beneficiary(cur, tenant_id, invoice_id, supplier_id, body)

            remitter = _get_or_seed_remitter_account(cur, tenant_id)

            otp = "".join(random.choices(string.digits, k=6))
            otp_expires_at = time.time() + OTP_TTL_SECONDS
            tran_ref_no = _gen_tran_ref_no()

            cur.execute(
                """INSERT INTO fact_disbursements
                       (tenant_id, invoice_id, supplier_id, amount, beneficiary_name, beneficiary_account,
                        beneficiary_ifsc, beneficiary_mobile, remitter_account_number, bank_provider,
                        tran_ref_no, status, otp_code_hash, otp_expires_at, initiated_by, is_overridden, override_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_OTP',%s,%s,%s,%s,%s)
                   RETURNING disbursement_id""",
                (
                    tenant_id, invoice_id, supplier_id, body.amount, beneficiary_name,
                    beneficiary_account, beneficiary_ifsc,
                    "9999999999",  # demo placeholder — vendor mobile isn't captured elsewhere in this schema yet
                    remitter["account_number"], remitter["bank_provider"],
                    tran_ref_no, _hash_otp(otp), otp_expires_at, body.actor_name,
                    is_overridden, override_id,
                ),
            )
            disbursement_id = cur.fetchone()["disbursement_id"]
            _audit(cur, tenant_id, disbursement_id, None,
                   {"event": "initiated", "amount": body.amount, "invoice_id": invoice_id, "is_overridden": is_overridden},
                   body.actor_name)
            conn.commit()

        notify_email = remitter["notify_email"] or SMTP_ADMIN_EMAIL
        try:
            _send_otp_email(notify_email, otp, beneficiary_name, body.amount)
        except Exception as e:
            # OTP row already exists — resend-otp can retry the email without
            # re-validating everything, so don't fail the whole request here.
            print(f"[disbursement] OTP email send failed: {e}")

        # `demo_otp` is the fixed code this environment also accepts (null when
        # disabled) — the OTP step shows it so a demo needn't open the inbox.
        # `otp_expires_at` (epoch seconds) lets the UI count down to the real
        # expiry rather than assume OTP_TTL_SECONDS and drift out of sync.
        return {"disbursement_id": str(disbursement_id), "otp_sent_to": _mask_email(notify_email),
                "status": "PENDING_OTP", "demo_otp": DISBURSEMENT_DEMO_OTP or None,
                "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)


# ── POST /api/vendors/{supplier_id}/disbursements/bulk ───────────────────────
# Multi-bill selection for one vendor (plan §5.1a/§6.1a): one email OTP gates
# the whole selection — each selected bill still gets its own fact_disbursements
# row (and its own disbursement_overrides row if overridden) so every bill's
# payment history and audit trail stand alone, same as a single-bill payment.

class BulkBillAmount(BaseModel):
    invoice_id: str
    amount: float


class BulkDisbursementCreate(OverrideFields):
    bills: list[BulkBillAmount]


@router.post("/vendors/{supplier_id}/disbursements/bulk")
def initiate_bulk_disbursement(supplier_id: str, body: BulkDisbursementCreate):
    tenant_id = _tid(body.company_id)
    if (body.actor_role or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can initiate a payment")
    if not body.bills:
        raise HTTPException(status_code=400, detail="Select at least one bill to pay")

    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            remitter = _get_or_seed_remitter_account(cur, tenant_id)
            otp = "".join(random.choices(string.digits, k=6))
            otp_hash = _hash_otp(otp)
            otp_expires_at = time.time() + OTP_TTL_SECONDS

            disbursement_ids = []
            beneficiary_name = None
            total_amount = 0.0
            for item in body.bills:
                cur.execute(
                    """SELECT invoice_id, supplier_id, outstanding_amount
                       FROM fact_invoices WHERE invoice_id=%s AND tenant_id=%s""",
                    (item.invoice_id, tenant_id),
                )
                bill = cur.fetchone()
                if not bill or str(bill["supplier_id"]) != supplier_id:
                    raise HTTPException(status_code=404, detail=f"Bill {item.invoice_id} not found for this vendor")
                outstanding = float(bill["outstanding_amount"] or 0)
                if item.amount <= 0 or item.amount > outstanding + 0.01:
                    raise HTTPException(status_code=400, detail=f"Amount for bill {item.invoice_id} must be between ₹1 and its outstanding ₹{outstanding:,.2f}")

                beneficiary_account, beneficiary_ifsc, beneficiary_name, is_overridden, override_id = \
                    _resolve_beneficiary(cur, tenant_id, item.invoice_id, supplier_id, body)

                tran_ref_no = _gen_tran_ref_no()
                cur.execute(
                    """INSERT INTO fact_disbursements
                           (tenant_id, invoice_id, supplier_id, amount, beneficiary_name, beneficiary_account,
                            beneficiary_ifsc, beneficiary_mobile, remitter_account_number, bank_provider,
                            tran_ref_no, status, otp_code_hash, otp_expires_at, initiated_by, is_overridden, override_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_OTP',%s,%s,%s,%s,%s)
                       RETURNING disbursement_id""",
                    (
                        tenant_id, item.invoice_id, supplier_id, item.amount, beneficiary_name,
                        beneficiary_account, beneficiary_ifsc, "9999999999",
                        remitter["account_number"], remitter["bank_provider"],
                        tran_ref_no, otp_hash, otp_expires_at, body.actor_name,
                        is_overridden, override_id,
                    ),
                )
                disbursement_id = cur.fetchone()["disbursement_id"]
                disbursement_ids.append(str(disbursement_id))
                total_amount += item.amount
                _audit(cur, tenant_id, disbursement_id, None,
                       {"event": "initiated", "amount": item.amount, "invoice_id": item.invoice_id,
                        "is_overridden": is_overridden, "bulk": True},
                       body.actor_name)
            conn.commit()

        notify_email = remitter["notify_email"] or SMTP_ADMIN_EMAIL
        try:
            _send_otp_email(notify_email, otp, f"{beneficiary_name} ({len(disbursement_ids)} bills)", total_amount)
        except Exception as e:
            print(f"[disbursement] Bulk OTP email send failed: {e}")

        return {"disbursement_ids": disbursement_ids, "otp_sent_to": _mask_email(notify_email),
                "status": "PENDING_OTP", "demo_otp": DISBURSEMENT_DEMO_OTP or None,
                "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)


class BatchVerifyOtpBody(BaseModel):
    disbursement_ids: list[str]
    otp: str
    company_id: Optional[str] = None


@router.post("/disbursements/batch-verify-otp")
def verify_batch_otp(body: BatchVerifyOtpBody):
    tenant_id = _tid(body.company_id)
    if not body.disbursement_ids:
        raise HTTPException(status_code=400, detail="No disbursements to verify")

    conn = get_conn()
    conn.autocommit = False
    try:
        results = []
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT * FROM fact_disbursements WHERE tenant_id=%s AND disbursement_id::text = ANY(%s)",
                (tenant_id, body.disbursement_ids),
            )
            rows = {str(r["disbursement_id"]): dict(r) for r in cur.fetchall()}
            if len(rows) != len(body.disbursement_ids):
                raise HTTPException(status_code=404, detail="One or more disbursements not found")

            # All rows in a bulk batch share the same OTP hash — check once against the first.
            first = next(iter(rows.values()))
            if first["status"] != "PENDING_OTP":
                raise HTTPException(status_code=400, detail="This batch is no longer awaiting OTP")
            ok, via_demo = _check_otp(body.otp, first["otp_code_hash"])
            if not via_demo:
                if time.time() > (first["otp_expires_at"] or 0):
                    raise HTTPException(status_code=400, detail="OTP expired — request a new one")
                if first["otp_attempts"] >= MAX_OTP_ATTEMPTS:
                    raise HTTPException(status_code=400, detail="Too many attempts — request a new OTP")
            if not ok:
                cur.execute(
                    "UPDATE fact_disbursements SET otp_attempts = otp_attempts + 1, updated_at=now() WHERE disbursement_id::text = ANY(%s)",
                    (body.disbursement_ids,),
                )
                conn.commit()
                raise HTTPException(status_code=400, detail="Incorrect OTP")

            for disbursement_id, d in rows.items():
                vendor_name, month = _get_vendor_name_and_month(cur, tenant_id, str(d["invoice_id"]), str(d["supplier_id"]) if d["supplier_id"] else None)
                bank_result = _mock_imps_initiate(d["tran_ref_no"], float(d["amount"]), d["beneficiary_name"], vendor_name, month)
                data = bank_result["data"]
                new_status = data["status"]
                cur.execute(
                    """UPDATE fact_disbursements SET
                           status=%s, status_message=%s, act_code=%s, bank_rrn=%s, bank_tran_ref_no=%s,
                           otp_verified_at=now(), raw_response_jsonb=%s, updated_at=now()
                       WHERE disbursement_id=%s""",
                    (
                        new_status, data["statusMessage"], bank_result["actCode"], data["bankRRN"],
                        data["tranRefNo"], json.dumps(bank_result), disbursement_id,
                    ),
                )
                if new_status == "SUCCESS":
                    _apply_success(cur, tenant_id, d, bank_result)
                _audit(cur, tenant_id, disbursement_id, d, {"event": "otp_verified", **bank_result, "bulk": True, "demo_otp": via_demo}, d["initiated_by"])
                results.append({
                    "disbursement_id": disbursement_id, "invoice_id": str(d["invoice_id"]),
                    "amount": float(d["amount"]), "status": new_status,
                    "status_message": data["statusMessage"], "bank_rrn": data["bankRRN"],
                })
            conn.commit()
        return {"results": results}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)


# ── POST /api/disbursements/{id}/resend-otp ──────────────────────────────────

class ResendOtpBody(BaseModel):
    company_id: Optional[str] = None


@router.post("/disbursements/{disbursement_id}/resend-otp")
def resend_otp(disbursement_id: str, body: ResendOtpBody):
    tenant_id = _tid(body.company_id)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT * FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s",
                (disbursement_id, tenant_id),
            )
            d = cur.fetchone()
            if not d:
                raise HTTPException(status_code=404, detail="Disbursement not found")
            if d["status"] != "PENDING_OTP":
                raise HTTPException(status_code=400, detail="This payment is no longer awaiting OTP")

            otp = "".join(random.choices(string.digits, k=6))
            otp_expires_at = time.time() + OTP_TTL_SECONDS
            cur.execute(
                "UPDATE fact_disbursements SET otp_code_hash=%s, otp_expires_at=%s, otp_attempts=0, updated_at=now() WHERE disbursement_id=%s",
                (_hash_otp(otp), otp_expires_at, disbursement_id),
            )
            remitter = _get_or_seed_remitter_account(cur, tenant_id)
            conn.commit()

        notify_email = remitter["notify_email"] or SMTP_ADMIN_EMAIL
        _send_otp_email(notify_email, otp, d["beneficiary_name"], float(d["amount"]))
        return {"otp_sent_to": _mask_email(notify_email), "otp_expires_at": otp_expires_at,
                "otp_ttl_seconds": OTP_TTL_SECONDS}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to resend OTP: {e}")
    finally:
        put_conn(conn)


# ── POST /api/disbursements/{id}/verify-otp ──────────────────────────────────

class VerifyOtpBody(BaseModel):
    otp: str
    company_id: Optional[str] = None


def _apply_success(cur, tenant_id: str, d: dict, bank_result: dict):
    """On a mock SUCCESS: debit the demo remitter balance and mark the bill paid,
    mirroring the existing /api/bills/{id}/payment convention (main.py:4702) —
    same fact_invoices columns, same bill_approval_chain step=99 audit row."""
    cur.execute(
        "UPDATE remitter_accounts SET balance = balance - %s WHERE tenant_id=%s AND account_number=%s",
        (d["amount"], tenant_id, d["remitter_account_number"]),
    )
    cur.execute("SELECT total_amount, amount_paid FROM fact_invoices WHERE invoice_id=%s", (d["invoice_id"],))
    inv = cur.fetchone()
    new_paid = float(inv["amount_paid"] or 0) + float(d["amount"])
    total = float(inv["total_amount"] or 0)
    outstanding = max(0.0, total - new_paid)
    new_status = "paid" if outstanding <= 0.01 else "partial"
    cur.execute(
        "UPDATE fact_invoices SET payment_status=%s, amount_paid=%s, outstanding_amount=%s WHERE invoice_id=%s",
        (new_status, new_paid, outstanding, d["invoice_id"]),
    )
    cur.execute(
        """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
           VALUES (%s,%s,99,%s,%s,%s,%s)""",
        (
            d["invoice_id"], tenant_id, "Admin", d["initiated_by"] or "Admin",
            f"payment_{new_status}",
            f"IMPS disbursement ₹{float(d['amount']):,.2f} via {bank_result['data']['bankRRN']} (demo)",
        ),
    )


@router.post("/disbursements/{disbursement_id}/verify-otp")
def verify_otp(disbursement_id: str, body: VerifyOtpBody):
    tenant_id = _tid(body.company_id)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT * FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s",
                (disbursement_id, tenant_id),
            )
            d = cur.fetchone()
            if not d:
                raise HTTPException(status_code=404, detail="Disbursement not found")
            if d["status"] != "PENDING_OTP":
                raise HTTPException(status_code=400, detail="This payment is no longer awaiting OTP")

            ok, via_demo = _check_otp(body.otp, d["otp_code_hash"])
            if not via_demo:
                if time.time() > (d["otp_expires_at"] or 0):
                    raise HTTPException(status_code=400, detail="OTP expired — request a new one")
                if d["otp_attempts"] >= MAX_OTP_ATTEMPTS:
                    raise HTTPException(status_code=400, detail="Too many attempts — request a new OTP")

            if not ok:
                cur.execute(
                    "UPDATE fact_disbursements SET otp_attempts = otp_attempts + 1, updated_at=now() WHERE disbursement_id=%s",
                    (disbursement_id,),
                )
                _audit(cur, tenant_id, disbursement_id, dict(d), {"event": "otp_failed"}, None)
                conn.commit()
                raise HTTPException(status_code=400, detail="Incorrect OTP")

            vendor_name, month = _get_vendor_name_and_month(cur, tenant_id, str(d["invoice_id"]), str(d["supplier_id"]) if d["supplier_id"] else None)
            bank_result = _mock_imps_initiate(d["tran_ref_no"], float(d["amount"]), d["beneficiary_name"], vendor_name, month)
            data = bank_result["data"]
            new_status = data["status"]  # SUCCESS | PENDING | FAILED

            cur.execute(
                """UPDATE fact_disbursements SET
                       status=%s, status_message=%s, act_code=%s, bank_rrn=%s, bank_tran_ref_no=%s,
                       otp_verified_at=now(), raw_response_jsonb=%s, updated_at=now()
                   WHERE disbursement_id=%s""",
                (
                    new_status, data["statusMessage"], bank_result["actCode"], data["bankRRN"],
                    data["tranRefNo"], json.dumps(bank_result), disbursement_id,
                ),
            )

            if new_status == "SUCCESS":
                _apply_success(cur, tenant_id, d, bank_result)

            _audit(cur, tenant_id, disbursement_id, dict(d), {"event": "otp_verified", **bank_result, "demo_otp": via_demo}, d["initiated_by"])
            conn.commit()

        return {
            "disbursement_id": disbursement_id,
            "status": new_status,
            "status_message": data["statusMessage"],
            "bank_rrn": data["bankRRN"],
            "tran_ref_no": data["tranRefNo"],
        }
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)


# ── POST /api/disbursements/{id}/refresh-status ──────────────────────────────

class RefreshStatusBody(BaseModel):
    company_id: Optional[str] = None


@router.post("/disbursements/{disbursement_id}/refresh-status")
def refresh_status(disbursement_id: str, body: RefreshStatusBody):
    tenant_id = _tid(body.company_id)
    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT * FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s",
                (disbursement_id, tenant_id),
            )
            d = cur.fetchone()
            if not d:
                raise HTTPException(status_code=404, detail="Disbursement not found")
            if d["status"] != "PENDING":
                # Already resolved (a second check, or another tab got there
                # first). Return the same shape as the refresh path — omitting
                # bank_rrn here made the caller render "reference —" for a
                # payment that has a perfectly good reference on file.
                return {"disbursement_id": disbursement_id, "status": d["status"],
                        "bank_rrn": d["bank_rrn"], "status_message": d["status_message"]}

            bank_result = _mock_imps_status(dict(d))
            data = bank_result["data"]
            cur.execute(
                """UPDATE fact_disbursements SET status=%s, status_message=%s, bank_rrn=%s,
                       raw_response_jsonb=%s, updated_at=now() WHERE disbursement_id=%s""",
                (data["status"], data["statusMessage"], data["bankRRN"], json.dumps(bank_result), disbursement_id),
            )
            if data["status"] == "SUCCESS":
                _apply_success(cur, tenant_id, dict(d), bank_result)
            _audit(cur, tenant_id, disbursement_id, dict(d), {"event": "status_refreshed", **bank_result}, None)
            conn.commit()
        return {"disbursement_id": disbursement_id, "status": data["status"], "bank_rrn": data["bankRRN"],
                "status_message": data["statusMessage"]}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)
