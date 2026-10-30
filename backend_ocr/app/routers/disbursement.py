"""Vendor Disbursement (IMPS) — real gateway integration (UAT).

Per docs/payments_docs/DISBURSEMENT_PAYMENT_INTEGRATION_PLAN_LENDIX.md and
docs/DISBURSEMENT_IMPS_API.md. Every route is tenant-scoped from the verified
token via `_tid()`, and the module as a whole is gated by the `disbursement`
service licence:

  - A real bank call IS made, via `imps_client.py` (`_real_imps_initiate` /
    `_real_imps_status`), against `config.DISBURSEMENT_BASE_URL`
    (`api-dev.lendix.ai` by default — a UAT environment with its own ₹10/txn
    ceiling, not the production gateway). This module previously fabricated
    responses (`_mock_imps_initiate`/`_mock_imps_status`, still present below,
    unused) so the UI/DB/audit trail behaved identically either way — that
    mock path is kept only as reference/fallback, not called by default
    anymore. **Every "Pay Now" in this environment moves real (UAT-capped)
    money — there is no separate demo mode to fall back into.**
  - The "RSA-based verification" the API doc mentions is a bank-gateway detail
    handled inside Lendix's own SDK/gateway, not implemented client-side here
    (§8.2 of the plan is explicitly left open/unresolved — the request body
    the gateway expects is plain JSON per the doc's own curl examples).
  - The independent 2FA control (§5.5/§8.4 of the plan) is implemented as a
    one-time email passcode, sent via the SMTP creds already in config.py,
    that gates the (now real) bank call. There is no TOTP/authenticator-app
    step in this cut — email OTP only. `config.DISBURSEMENT_DEMO_OTP` can set
    a fixed fallback code for environments that need to skip inbox access;
    OFF by default, and its use is recorded in the audit trail as
    `demo_otp: true` whenever it is used.
  - `remitter_accounts` holds one ICICI account per tenant with a tracked
    balance, decremented on each real SUCCESS so the KPI strip (§ stats
    endpoint) reflects what's actually been disbursed.

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
GET  /api/disbursements/stats                     remitter balance + txn KPIs
GET  /api/disbursements/queue                     every vendor with money owed, across the tenant — the Payment Hub table
GET  /api/disbursements/recent                    latest disbursements across all vendors — the Payment Hub activity feed
GET  /api/bills/{invoice_id}/disbursements         payment history for one bill
POST /api/bills/{invoice_id}/disbursements         start a payment (admin-only) → emails OTP
POST /api/disbursements/{disbursement_id}/verify-otp   confirm OTP → real bank call (imps_client.py)
POST /api/disbursements/{disbursement_id}/resend-otp   regenerate + resend OTP
POST /api/disbursements/{disbursement_id}/refresh-status  poll a PENDING real txn

On every SUCCESS — OTP verified, bulk batch, or a PENDING txn that settles on a
status refresh — the vendor's POC is emailed a receipt (HTML body, PDF
attached) by the Communication Agent. That runs off the request path and is
claimed per disbursement, so it can neither delay a payment response nor send
the same receipt twice: see app/domain/payments/notify.py, with
`fact_disbursements.receipt_email_status` carrying the outcome.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
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

from ..core.jurisdiction import get_jurisdiction, jurisdiction_for_cursor
from ..domain.payments import notify as payment_notify
from ..domain.payments import demo as demo_payments
from ..integrations import imps_client
from ..integrations import payments as bank_payments
from ..integrations.payments.icici import explain as explain_act_code
from ..security.auth import require_role, require_tenant, current_username, current_role
from ..config import gateway_profile, is_demo_gateway, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, DISBURSEMENT_DEMO_OTP, DISBURSEMENT_NBFC_NAME, DISBURSEMENT_FORCE_SUCCESS, get_conn, put_conn

router = APIRouter(prefix="/api", tags=["Disbursement (Demo)"])

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 120  # 2 minutes — tighter than the password-reset OTP convention since this gates a live money movement
MAX_OTP_ATTEMPTS = 5
DEMO_BANK_PROVIDER = "ICICI"
DEMO_REMITTER_ACCOUNT = "010101000"  # matches the doc's own sample remitterAccountNumber


def _actor() -> Optional[str]:
    """The verified caller's identity (email/sub from the token), for every
    write that needs a record of who did it — never the client-supplied
    ``actor_name`` on its own, which a caller can set to anything."""
    return current_username()


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
                nbfc_name        text,
                created_at       timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (id),
                UNIQUE (tenant_id, account_number)
            );
            ALTER TABLE remitter_accounts ADD COLUMN IF NOT EXISTS nbfc_name text;
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
                nbfc_name         text,
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
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS nbfc_name text;
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS updated_by text;
            -- Receipt-to-vendor state (app/domain/payments/notify.py). NULL means
            -- "not attempted", which is also what makes a row claimable: the
            -- agent takes it by writing 'SENDING' here in one conditional UPDATE,
            -- so a retried verify and a racing status refresh cannot both mail
            -- the same vendor the same receipt.
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS receipt_email_status text;
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS receipt_emailed_at timestamptz;
            ALTER TABLE fact_disbursements ADD COLUMN IF NOT EXISTS receipt_email_error text;
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
                override_ifsc          text        NOT NULL,
                override_beneficiary_name text     NOT NULL,
                created_at             timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (override_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dover_tenant_supplier ON disbursement_overrides (tenant_id, supplier_id);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS disbursement_gateway_calls (
                call_id          uuid        NOT NULL DEFAULT gen_random_uuid(),
                disbursement_id  uuid        NOT NULL,
                tenant_id        uuid        NOT NULL,
                endpoint         text        NOT NULL,
                request_jsonb    jsonb       NOT NULL,
                response_jsonb   jsonb,
                success          boolean,
                error_message    text,
                called_at        timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (call_id)
            );
            CREATE INDEX IF NOT EXISTS idx_dgc_disbursement ON disbursement_gateway_calls (disbursement_id, called_at);
        """)
        conn.commit()
        put_conn(conn)
        print("[startup] disbursement demo tables ready")
    except Exception:
        print("[startup] ERROR creating disbursement demo tables:")
        traceback.print_exc()


_ensure_tables()


# Seed details per bank. The balance each row tracks is decremented on a
# successful payment (keyed on tenant_id + account_number), so a bank without
# its own row would draw down another bank's balance.
_REMITTER_SEEDS = {
    "ICICI":  {"bank_name": "ICICI Bank (Demo)",            "ifsc": "ICIC0000101", "account": DEMO_REMITTER_ACCOUNT},
    "HDFC":   {"bank_name": "HDFC Bank (Demo)",             "ifsc": "HDFC0000001", "account": "020202000"},
    "AUBANK": {"bank_name": "AU Small Finance Bank (Demo)", "ifsc": "AUBL0002026", "account": "2602201113751781"},
}


def _get_or_seed_remitter_account(cur, tenant_id: str, bank: Optional[str] = None) -> dict:
    """The remitter account this tenant pays from, for a given bank.

    One row per (tenant, bank). remitter_accounts is already UNIQUE on
    (tenant_id, account_number) and the balance decrement is keyed on that same
    pair, so giving each bank its own row is what makes the drawdown land on the
    right balance — before this, an AU payment sent AU's account on the wire but
    decremented the ICICI-seeded row.

    `bank` omitted keeps the old behaviour (the tenant's first row), which is
    what the stats panel and OTP resend want: they describe the account, not a
    specific payment's routing.
    """
    if bank:
        cur.execute(
            "SELECT * FROM remitter_accounts WHERE tenant_id=%s AND bank_provider=%s ORDER BY created_at LIMIT 1",
            (tenant_id, bank),
        )
    else:
        cur.execute(
            "SELECT * FROM remitter_accounts WHERE tenant_id=%s ORDER BY created_at LIMIT 1",
            (tenant_id,),
        )
    row = cur.fetchone()
    if row:
        return dict(row)

    code = bank_payments.normalise(bank) or DEMO_BANK_PROVIDER
    seed = _REMITTER_SEEDS.get(code, _REMITTER_SEEDS[DEMO_BANK_PROVIDER])
    profile = gateway_profile(code)
    cur.execute(
        """INSERT INTO remitter_accounts (tenant_id, account_number, bank_name, ifsc, bank_provider, balance, notify_email, nbfc_name)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (tenant_id, account_number) DO UPDATE SET bank_provider = EXCLUDED.bank_provider
           RETURNING *""",
        (tenant_id, profile["remitter_account"] or seed["account"], seed["bank_name"], seed["ifsc"],
         code, 5000000, SMTP_ADMIN_EMAIL, profile["nbfc_name"] or DISBURSEMENT_NBFC_NAME),
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
            supplier = _get_supplier_kyb(cur, tenant_id, supplier_id)
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
    billing period being settled. Never persisted as new fact_disbursements columns since
    they're always re-derivable from invoice_id/supplier_id, which are already stored.

    `month` is documented as request-only metadata ("validated, logged, and stored but not
    sent to the bank") but is enforced as **required** by the live gateway — confirmed
    2026-09-14, a real call with month=null was rejected with `400 month is required`.
    `stg_bills.service_month` is null on ~550 of this tenant's bills (never backfilled for
    older imports), so it can't be the only source; falls back to invoice_date's YYYY-MM,
    then due_date's, so this is never null on an actual gateway call."""
    from datetime import date

    vendor_name = None
    if supplier_id:
        cur.execute("SELECT name FROM dim_suppliers WHERE supplier_id=%s AND tenant_id=%s", (supplier_id, tenant_id))
        row = cur.fetchone()
        vendor_name = row["name"] if row else None
    cur.execute(
        """SELECT sb.service_month, fi.invoice_date, fi.due_date FROM fact_invoices fi
           LEFT JOIN stg_bills sb ON COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) = sb.stg_bill_id
                                  AND sb.tenant_id = fi.tenant_id
           WHERE fi.invoice_id=%s AND fi.tenant_id=%s""",
        (invoice_id, tenant_id),
    )
    row = cur.fetchone()
    month = row["service_month"] if row else None
    if not month and row:
        fallback = row["invoice_date"] or row["due_date"]
        month = fallback.strftime("%Y-%m") if fallback else None
    if not month:
        month = date.today().strftime("%Y-%m")
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


def _log_gateway_call(cur, disbursement_id: str, tenant_id: str, endpoint: str,
                       request_payload: dict, response_body: Optional[dict],
                       success: Optional[bool], error_message: Optional[str] = None) -> None:
    """Append-only record of every real gateway call — request AND response,
    never overwritten. fact_disbursements.raw_response_jsonb only ever holds
    the LATEST call's response (an initiate followed by a status check
    silently loses the initiate's own response), which made a real incident
    ambiguous to diagnose after the fact. This table exists so every call in
    a disbursement's history — initiate, every status poll — stays queryable,
    not just the most recent one.

    Written on its OWN connection, committed immediately, and deliberately not
    on the caller's cursor. This function always claimed the record survived
    the caller rolling back, but writing through `cur` meant it rolled back
    too — destroying the only evidence that money had already moved, on
    exactly the path where that evidence matters most. A bulk batch calls the
    bank per bill inside one transaction; if a later bill raises, the earlier
    transfers have happened and the rollback erased all trace of them.

    `cur` is still accepted so every call site keeps its shape, and is used
    only as a fallback if a separate connection cannot be obtained — a log
    entry inside the caller's transaction beats none at all.
    """
    params = (
        disbursement_id, tenant_id, endpoint, json.dumps(request_payload, default=str),
        json.dumps(response_body, default=str) if response_body is not None else None,
        success, error_message,
    )
    sql = """INSERT INTO disbursement_gateway_calls
                 (disbursement_id, tenant_id, endpoint, request_jsonb, response_jsonb, success, error_message)
             VALUES (%s,%s,%s,%s,%s,%s,%s)"""
    log_conn = None
    try:
        log_conn = get_conn()
        log_conn.autocommit = True
        with log_conn.cursor() as log_cur:
            log_cur.execute(sql, params)
    except Exception:
        # Never let an audit write break a payment in progress.
        traceback.print_exc()
        try:
            cur.execute(sql, params)
        except Exception:
            traceback.print_exc()
    finally:
        if log_conn is not None:
            put_conn(log_conn)


def _already_sent_to_gateway(disbursement_id: str) -> bool:
    """Whether a /pay has already been sent for this disbursement.

    AU requires tranRefNo to be unique per transaction, and the tempting fix —
    mint a fresh ref on every attempt — is the wrong one: a retry after an
    ambiguous outcome (a timeout, or a rollback that erased the evidence) would
    then arrive at the bank as a brand-new payment and the vendor would be paid
    twice. AU rejecting a repeated ref is a protection, not an obstacle.

    So the ref stays fixed per payment and this stops it ever being sent twice.
    Read on its own connection, matching _log_gateway_call: the record it looks
    for is committed independently, so it is visible even when the caller's
    transaction has since rolled back.
    """
    conn = None
    try:
        conn = get_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM disbursement_gateway_calls
                   WHERE disbursement_id = %s AND endpoint LIKE %s LIMIT 1""",
                (disbursement_id, "%PAY%"),
            )
            return cur.fetchone() is not None
    except Exception:
        # Unavailable evidence is not evidence of absence, but blocking every
        # payment because the audit table is unreachable is worse than allowing
        # one through the guard the OTP flow already gates.
        traceback.print_exc()
        return False
    finally:
        if conn is not None:
            put_conn(conn)


def _real_imps_initiate(cur, d: dict, vendor_name: Optional[str], month: Optional[str]) -> dict:
    """Real replacement for _mock_imps_initiate — same input/output shape
    (`data.status`/`data.bankRRN`/`data.statusMessage`/`data.tranRefNo`, plus
    `actCode`) so every caller below works unchanged. `d` is a
    fact_disbursements row (already has beneficiary_account/ifsc/mobile,
    remitter_account_number, bank_provider from when the payment was created).
    Every call — success, gateway rejection, or transport error — is logged to
    disbursement_gateway_calls via `cur` before returning, so the attempt is
    on record even if the caller's own transaction later rolls back.

    Calls POST /vendor-payment/pay, not the generic /disbursement/imps/initiate
    — DISBURSEMENT_API_KEY previously lacked permission for vendor-payment
    (confirmed 403 as of 2026-08-24, see imps_client.py's module docstring),
    which forced the generic endpoint; that permission is granted now, and
    vendor-payment is the correct endpoint for what this module actually does
    (pays a vendor's bill, hence vendor_name/month riding along as metadata
    the bank stores but never uses for routing)."""
    # AU's document requires the IFSC upper-case; a vendor row holding
    # "sbin0001470" is rejected at the gateway with a message that reads like a
    # bad account number. Normalised once here so the payload that gets logged
    # to disbursement_gateway_calls is exactly what went on the wire.
    # One /pay per tranRefNo, ever. See _already_sent_to_gateway: re-sending is
    # what a duplicate-ref rejection protects against, and minting a fresh ref
    # instead would turn that protection into a second transfer.
    if _already_sent_to_gateway(str(d["disbursement_id"])):
        return {
            "success": False,
            "data": {
                "tranRefNo": d["tran_ref_no"], "bankRRN": None,
                "beneficiaryName": d["beneficiary_name"],
                "remitterAccountNumber": d["remitter_account_number"], "status": "PENDING",
                "statusMessage": ("This payment was already sent to the bank. Its outcome is "
                                  "unknown here, so it has not been sent again — check its "
                                  "status before retrying."),
            },
            "vendorInfo": {}, "actCode": None,
        }

    bene_ifsc = (d["beneficiary_ifsc"] or "").strip().upper()
    request_payload = {
        "leadId": str(d["invoice_id"]), "tranRefNo": d["tran_ref_no"], "amount": float(d["amount"]),
        "beneficiaryAccount": (d["beneficiary_account"] or "").strip(), "beneficiaryIFSC": bene_ifsc,
        "beneficiaryName": d["beneficiary_name"], "beneficiaryMobile": d["beneficiary_mobile"] or "9999999999",
        "remitterAccountNumber": d["remitter_account_number"], "bankProvider": d["bank_provider"],
        "nbfcName": d.get("nbfc_name"), "vendorName": vendor_name or d["beneficiary_name"], "month": month,
    }
    try:
        body = imps_client.vendor_payment_pay(
            lead_id=str(d["invoice_id"]),
            tran_ref_no=d["tran_ref_no"],
            amount=float(d["amount"]),
            beneficiary_account=d["beneficiary_account"],
            beneficiary_ifsc=bene_ifsc,
            beneficiary_name=d["beneficiary_name"],
            beneficiary_mobile=d["beneficiary_mobile"] or "9999999999",
            remitter_account_number=d["remitter_account_number"],
            bank_provider=d["bank_provider"],
            # The payment's reference, as shown on our receipt, so the vendor's
            # statement line matches it (the bill's UUID ran past the 40-character limit).
            payment_remarks=f"Vendor payment {d['tran_ref_no']}",
            vendor_name=vendor_name,
            month=month,
            nbfc_name=d.get("nbfc_name"),
        )
    except imps_client.ImpsApiError as e:
        _log_gateway_call(cur, d["disbursement_id"], d["tenant_id"], "VENDOR_PAYMENT_PAY",
                           request_payload, e.raw_body, False, str(e))
        return {
            "success": False,
            "data": {
                "tranRefNo": d["tran_ref_no"], "bankRRN": None, "beneficiaryName": d["beneficiary_name"],
                "remitterAccountNumber": d["remitter_account_number"], "status": "FAILED",
                "statusMessage": str(e),
            },
            "vendorInfo": {}, "actCode": None,
        }

    if not body.get("success"):
        error = body.get("error") or {}
        _log_gateway_call(cur, d["disbursement_id"], d["tenant_id"], "VENDOR_PAYMENT_PAY",
                           request_payload, body, False, error.get("message"))
        return {
            "success": False,
            "data": {
                "tranRefNo": d["tran_ref_no"], "bankRRN": None, "beneficiaryName": d["beneficiary_name"],
                "remitterAccountNumber": d["remitter_account_number"], "status": "FAILED",
                "statusMessage": error.get("message") or "Disbursement gateway rejected the request",
            },
            "vendorInfo": body.get("vendorInfo") or {}, "actCode": body.get("actCode"),
        }

    _log_gateway_call(cur, d["disbursement_id"], d["tenant_id"], "VENDOR_PAYMENT_PAY", request_payload, body, True)
    data = body.get("data") or {}
    act_code = body.get("actCode")
    raw_message = data.get("statusMessage") or ""
    return {
        "success": True,
        "data": {
            "tranRefNo": data.get("tranRefNo") or d["tran_ref_no"],
            "bankRRN": data.get("bankRRN"),
            "beneficiaryName": data.get("beneficiaryName") or d["beneficiary_name"],
            "remitterAccountNumber": data.get("remitterAccountNumber") or d["remitter_account_number"],
            "status": data.get("status") or "PENDING",
            # Explained with the table belonging to the bank that actually
            # processed this payment, not always ICICI's: an AU statusCode of
            # 100 ("processing — do not re-initiate") means nothing in ICICI's
            # ActCode table and would surface as a raw gateway string.
            # §6/§3 of each bank's doc turns a bare code like "997" into
            # "Internal exception / Bad request — declined by ICICI API Gateway
            # — check the request packet...", falling back to the gateway's own
            # message when the code is not in that bank's table.
            "statusMessage": bank_payments.for_bank(d["bank_provider"]).explain(
                act_code=act_code, status_code=act_code, fallback_message=raw_message),
        },
        "vendorInfo": body.get("vendorInfo") or {}, "actCode": act_code,
    }


def _forced_success_initiate(cur, d: dict, vendor_name: Optional[str], month: Optional[str]) -> dict:
    """TEMPORARY stand-in for `_real_imps_initiate`, used only while
    `config.DISBURSEMENT_FORCE_SUCCESS` is on (Lendix's vendor-payment API is
    currently failing and blocking the Pay Now demo). Synthesizes an
    always-SUCCESS response in the same shape `_real_imps_initiate` returns —
    every caller below works unchanged — and still logs the (skipped) call to
    `disbursement_gateway_calls` so it's visibly distinguishable from a real
    bank response in the audit trail, not silently indistinguishable from one.
    Remove this and the config flag once Lendix's API is confirmed working."""
    request_payload = {
        "leadId": str(d["invoice_id"]), "tranRefNo": d["tran_ref_no"], "amount": float(d["amount"]),
        "beneficiaryAccount": d["beneficiary_account"], "beneficiaryIFSC": d["beneficiary_ifsc"],
        "beneficiaryName": d["beneficiary_name"], "bankProvider": d["bank_provider"],
        "vendorName": vendor_name or d["beneficiary_name"], "month": month,
    }
    response = {
        "success": True,
        "data": {
            "tranRefNo": d["tran_ref_no"],
            "bankRRN": "".join(random.choices(string.digits, k=12)),
            "beneficiaryName": d["beneficiary_name"],
            "remitterAccountNumber": d["remitter_account_number"],
            "status": "SUCCESS",
            "statusMessage": "Transaction Successful (forced — Lendix API unavailable, demo mode)",
        },
        "vendorInfo": {"vendorName": d["bank_provider"], "endpoint": "VENDOR_PAYMENT_INITIATE (forced-success demo)"},
        "actCode": "0",
    }
    _log_gateway_call(cur, d["disbursement_id"], d["tenant_id"], "VENDOR_PAYMENT_PAY (forced-success demo)",
                       request_payload, response, True, "Lendix API skipped — DISBURSEMENT_FORCE_SUCCESS is on")
    return response


def _bank_enquiry(body: dict) -> dict:
    """The bank's own answer inside a gateway status response, where it has one.

    AU's enquiry result rides in vendorInfo.vendorResponse.apiCalls[].response.
    TransactionEnquiryResponse: batchStatusCode for the batch, and per transfer
    a statusCode / status (OPEN, TOBEPROCESSED, SENTTOCORE, PAID) / errorMessage.
    The gateway's own data.statusMessage only says whether the enquiry call
    worked ("SUCCESS"), not what happened to the money.
    """
    calls = (((body.get("vendorInfo") or {}).get("vendorResponse") or {}).get("apiCalls")) or []
    for call in reversed(calls):
        enq = ((call or {}).get("response") or {}).get("TransactionEnquiryResponse")
        if isinstance(enq, dict):
            detail = (enq.get("transactionEnquiryDetails") or [{}])[0] or {}
            return {
                "batch_status_code": enq.get("batchStatusCode"),
                "status_code": detail.get("statusCode"),
                "status": detail.get("status"),
                "error_message": detail.get("errorMessage") or "",
            }
    return {}


def status_explanation(bank_provider: Optional[str], body: dict, status: str,
                       act_code: Optional[str], raw_message: str) -> str:
    """What a status check means, read with the table of the bank that processed
    the payment (not always ICICI's). A transfer still with the bank is never
    described by the gateway's bare "SUCCESS", which is about the enquiry call."""
    enq = _bank_enquiry(body)
    fallback = enq.get("error_message") or raw_message
    if status == "PENDING" and fallback.strip().upper() in ("SUCCESS", "SUCCESSFUL", "OK", ""):
        fallback = "Processing at the bank"
    msg = bank_payments.for_bank(bank_provider).explain(
        act_code=act_code, status_code=enq.get("status_code") or act_code,
        batch_status_code=enq.get("batch_status_code"), status=enq.get("status"),
        fallback_message=fallback,
    )
    if status == "PENDING" and msg.strip().upper() in ("SUCCESS", "SUCCESSFUL"):
        return "Processing at the bank"
    return msg


def _real_imps_status(cur, disbursement: dict) -> dict:
    """Real replacement for _mock_imps_status. Uses bank_tran_ref_no (the
    value the `pay` response returned) per docs/DISBURSEMENT_IMPS_API.md's
    "Important — tranRefNo in Response" note — AU Bank's own reference, or
    ICICI's echo of ours; never the tran_ref_no we originally generated.
    Every status poll is logged to disbursement_gateway_calls (see
    _real_imps_initiate's docstring) — a disbursement can accumulate several
    of these over its lifetime, each kept, not just the latest.

    Calls POST /vendor-payment/status, matching _real_imps_initiate's move to
    /vendor-payment/pay — a disbursement that was paid via vendor-payment must
    be polled via its status counterpart, not the generic Disbursement one."""
    from datetime import datetime, timezone

    tran_ref = disbursement.get("bank_tran_ref_no") or disbursement["tran_ref_no"]
    date_str = datetime.now(timezone.utc).strftime("%m/%d/%Y")
    request_payload = {
        "tranRefNo": tran_ref, "date": date_str, "nbfcName": disbursement.get("nbfc_name"),
        "bankProvider": disbursement["bank_provider"], "remitterAccountNumber": disbursement["remitter_account_number"],
    }
    try:
        body = imps_client.vendor_payment_status(
            tran_ref_no=tran_ref, date=date_str,
            remitter_account_number=disbursement["remitter_account_number"],
            bank_provider=disbursement["bank_provider"],
            nbfc_name=disbursement.get("nbfc_name"),
        )
    except imps_client.ImpsApiError as e:
        _log_gateway_call(cur, disbursement["disbursement_id"], disbursement["tenant_id"], "VENDOR_PAYMENT_STATUS",
                           request_payload, e.raw_body, False, str(e))
        return {
            "success": False,
            "data": {
                "tranRefNo": tran_ref, "bankRRN": disbursement.get("bank_rrn"),
                "beneficiaryName": disbursement["beneficiary_name"],
                "remitterAccountNumber": disbursement["remitter_account_number"],
                "status": "PENDING", "statusMessage": str(e),
            },
            "vendorInfo": {},
        }

    _log_gateway_call(cur, disbursement["disbursement_id"], disbursement["tenant_id"], "VENDOR_PAYMENT_STATUS",
                       request_payload, body, bool(body.get("success")))
    data = body.get("data") or {}
    act_code = body.get("actCode")
    raw_message = data.get("statusMessage") or ""
    status = data.get("status") or "PENDING"
    return {
        "success": bool(body.get("success")),
        "data": {
            "tranRefNo": data.get("tranRefNo") or tran_ref,
            "bankRRN": data.get("bankRRN") or disbursement.get("bank_rrn"),
            "beneficiaryName": data.get("beneficiaryName") or disbursement["beneficiary_name"],
            "remitterAccountNumber": data.get("remitterAccountNumber") or disbursement["remitter_account_number"],
            "status": status,
            "statusMessage": status_explanation(disbursement.get("bank_provider"), body, status, act_code, raw_message),
        },
        "vendorInfo": body.get("vendorInfo") or {}, "actCode": act_code,
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


OTP_EXPIRED_MESSAGE = "OTP expired — no code entered within 2 minutes"


def _fail_expired_otp(cur, tenant_id: str, ids: list[str], old_by_id: dict) -> None:
    """Flips PENDING_OTP rows whose 2-minute window has passed to FAILED, with
    one wf_audit_log row each. Never touches a row still inside its window or
    one released via the demo code, which deliberately bypasses this clock
    (see _check_otp) — callers only pass ids they've already confirmed expired."""
    cur.execute(
        "UPDATE fact_disbursements SET status='FAILED', status_message=%s, updated_at=now(), updated_by=%s "
        "WHERE disbursement_id::text = ANY(%s)",
        (OTP_EXPIRED_MESSAGE, _actor(), ids),
    )
    for did in ids:
        _audit(cur, tenant_id, did, old_by_id.get(did), {"event": "otp_expired"}, _actor())


def _expire_stale_otps(cur, tenant_id: str) -> None:
    """Sweeps every PENDING_OTP row for this tenant whose OTP has outlived
    OTP_TTL_SECONDS without anyone entering a code. Called at the top of every
    read that shows a disbursement's status — the queue, the activity feed, a
    bill's own history — so a row reads FAILED the next time anyone looks at
    it, not only the next time someone happens to retry verifying it. Caller
    commits; this only executes the UPDATE/audit inserts."""
    cur.execute(
        "SELECT disbursement_id::text AS disbursement_id, invoice_id, supplier_id, amount, "
        "beneficiary_name, status, status_message, otp_expires_at FROM fact_disbursements "
        "WHERE tenant_id=%s AND status='PENDING_OTP' AND otp_expires_at IS NOT NULL AND otp_expires_at < %s",
        (tenant_id, time.time()),
    )
    stale = cur.fetchall()
    if not stale:
        return
    old_by_id = {r["disbursement_id"]: dict(r) for r in stale}
    _fail_expired_otp(cur, tenant_id, list(old_by_id.keys()), old_by_id)


# ── GET /api/disbursements/stats ─────────────────────────────────────────────

@router.get("/disbursements/banks")
def list_disbursal_banks():
    """Banks a payment may be routed through, for the Payment Hub picker.

    Served from the adapter registry rather than a list in the frontend, so a
    bank the backend cannot actually explain can never be offered as a choice.
    """
    return {"banks": bank_payments.supported_banks(), "default": bank_payments.DEFAULT_BANK}


class DemoSettingsBody(BaseModel):
    poc_email: Optional[str] = None
    company_id: Optional[str] = None


@router.get("/disbursements/demo-settings")
def get_demo_settings(company_id: Optional[str] = None):
    """Whether Pay is simulated for this tenant, and where its receipts go."""
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            return {"demo": demo_payments.is_demo(tenant_id), "poc_email": demo_payments.poc_email(cur, tenant_id)}
    finally:
        put_conn(conn)


@router.put("/disbursements/demo-settings")
def put_demo_settings(body: DemoSettingsBody):
    tenant_id = _tid(body.company_id)
    require_role("admin")
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            try:
                email = demo_payments.set_poc_email(cur, tenant_id, body.poc_email)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
        conn.commit()
        return {"demo": demo_payments.is_demo(tenant_id), "poc_email": email}
    finally:
        put_conn(conn)


@router.get("/disbursements/stats")
def disbursement_stats(company_id: Optional[str] = None, bank: Optional[str] = None):
    """The remitter account and payment counts for the Payment Hub card.

    `bank` picks which bank's remitter account is described: each bank pays
    from its own account (and balance), so the card follows the bank chosen in
    the hub. Omitted, it's the tenant's first account, as before. A bank with no
    account yet gets its demo account, the same one its first payment would.
    """
    tenant_id = _tid(company_id)
    code = bank_payments.normalise(bank) if bank else None
    if bank and not code:
        raise HTTPException(status_code=422, detail=f"Unsupported disbursal bank: {bank}")
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            remitter = _get_or_seed_remitter_account(cur, tenant_id, code)
            conn.commit()
            # Scoped to the chosen bank, because the card this feeds is headed
            # "This account so far" and sits directly beneath that one bank's
            # account and balance. Counting the whole tenant put every bank's
            # history under one account's heading — AU's card read "29
            # payments, 25 failed" when AU had six payments and two failures,
            # the other 23 being ICICI's and HDFC's.
            where, params = "tenant_id=%s", [tenant_id]
            if code:
                where += " AND bank_provider=%s"
                params.append(code)
            cur.execute(
                f"""SELECT
                       count(*) FILTER (WHERE status = 'SUCCESS') AS successful,
                       count(*) FILTER (WHERE status = 'FAILED') AS failed,
                       count(*) FILTER (WHERE status = 'PENDING') AS pending,
                       count(*) AS total,
                       COALESCE(sum(amount) FILTER (WHERE status = 'SUCCESS'), 0) AS total_disbursed
                   FROM fact_disbursements WHERE {where}""",
                tuple(params),
            )
            agg = dict(cur.fetchone())
        return {
            "remitter_account_number": remitter["account_number"],
            "bank_name": remitter["bank_name"],
            "bank_provider": remitter["bank_provider"],
            "ifsc": remitter["ifsc"],
            "balance": float(remitter["balance"]),
            "total_transactions": agg["total"],
            "successful_payments": agg["successful"],
            "failed_payments": agg["failed"],
            "pending_payments": agg["pending"],
            "total_disbursed": float(agg["total_disbursed"]),
            # Whether this is a demo is a fact about the payment path, not a
            # constant: the gateway is either skipped (forced success) or
            # pointed at a bank's non-production environment. Hardcoded True,
            # the badge would have promised "no real funds move" unchanged on
            # the day the base URL was switched to production.
            "demo": demo_payments.is_demo(tenant_id) or is_demo_gateway(gateway_profile(remitter["bank_provider"])["base_url"]),
        }
    finally:
        put_conn(conn)


# ── GET /api/disbursements/queue ─────────────────────────────────────────────
# The Payment Hub's landing table: every vendor across the tenant with money
# owed, in one call — payment-profile only ever covers one vendor at a time,
# so a "who can I pay right now" view had nowhere to come from. Verification
# and outstanding amounts are the same read as payment-profile/§7.1, just
# aggregated per vendor instead of fetched one at a time.

@router.get("/disbursements/queue")
def disbursement_queue(company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            _expire_stale_otps(cur, tenant_id)
            conn.commit()
            cur.execute(
                """
                SELECT
                    s.supplier_id::text AS supplier_id,
                    s.name AS vendor_name,
                    s.gstn AS gstin,
                    s.status AS kyb_status,
                    (s.status = 'VERIFIED') AS gstin_verified,
                    COALESCE(sba.any_verified, s.bank_verified, false) AS bank_verified,
                    COUNT(i.invoice_id) AS bills_count,
                    COALESCE(SUM(i.outstanding_amount), 0) AS payable_amount,
                    MIN(i.due_date) AS earliest_due,
                    pd.status AS pending_status,
                    pd.disbursement_id::text AS pending_disbursement_id,
                    pd.otp_expires_at AS pending_otp_expires_at,
                    COALESCE(pd.batch_ids, ARRAY[]::text[]) AS pending_disbursement_ids,
                    pd.batch_amount AS pending_amount,
                    COALESCE(lf.failures, '[]'::jsonb) AS failed_attempts,
                    -- Raw ingredients only — due_date, total_amount and
                    -- manual_priority are exactly what utils/payPriority.ts's
                    -- getPayPriority() needs. The bucket itself (P0..P3) is
                    -- computed once, in that shared function, the same place
                    -- the Bills page computes it — not re-derived here.
                    jsonb_agg(
                        jsonb_build_object(
                            'invoice_id', i.invoice_id::text,
                            'id', i.invoice_number,
                            'due_date', i.due_date,
                            'amount', i.outstanding_amount,
                            'total_amount', i.total_amount,
                            'manual_priority', i.manual_priority
                        ) ORDER BY i.due_date ASC NULLS LAST
                    ) AS bills,
                    COALESCE(bacct.account_number, s.bank_account) AS beneficiary_account,
                    COALESCE(bacct.bank_name, NULL) AS beneficiary_bank_name
                FROM dim_suppliers s
                JOIN fact_invoices i
                    ON i.supplier_id = s.supplier_id AND i.tenant_id = s.tenant_id AND i.outstanding_amount > 0
                LEFT JOIN LATERAL (
                    SELECT bool_or(b.verified) AS any_verified
                    FROM supplier_bank_accounts b
                    WHERE b.tenant_id = s.tenant_id AND b.supplier_id = s.supplier_id
                ) sba ON true
                -- Whatever account is on file, verified or not — the queue's
                -- "Beneficiary account" column is "where this would go today",
                -- same account _get_verified_vendor_account would pick if it
                -- were verified. Falls back to the legacy dim_suppliers column
                -- only when supplier_bank_accounts has nothing at all.
                LEFT JOIN LATERAL (
                    SELECT account_number, bank_name
                    FROM supplier_bank_accounts b
                    WHERE b.tenant_id = s.tenant_id AND b.supplier_id = s.supplier_id
                    ORDER BY b.verified DESC, b.is_primary DESC, b.created_at ASC
                    LIMIT 1
                ) bacct ON true
                LEFT JOIN LATERAL (
                    SELECT
                        d.disbursement_id,
                        d.status,
                        d.otp_expires_at,
                        -- Every row of the same batch: a bulk payment writes one
                        -- fact_disbursements row per bill, all sharing one OTP
                        -- hash, so releasing only the newest would leave the
                        -- rest of the batch stranded on PENDING_OTP.
                        (
                            SELECT array_agg(b.disbursement_id::text ORDER BY b.created_at)
                            FROM fact_disbursements b
                            WHERE b.tenant_id = d.tenant_id
                              AND b.supplier_id = d.supplier_id
                              AND b.status = d.status
                              AND b.otp_code_hash IS NOT DISTINCT FROM d.otp_code_hash
                        ) AS batch_ids,
                        -- What this OTP actually releases — one bill's ad-hoc
                        -- amount, or a batch's sum — never the vendor's whole
                        -- outstanding total. The OTP modal used to show
                        -- vendor.payable_amount regardless, so a ₹10 test
                        -- payment read as "releasing ₹3,71,200".
                        (
                            SELECT SUM(b.amount)
                            FROM fact_disbursements b
                            WHERE b.tenant_id = d.tenant_id
                              AND b.supplier_id = d.supplier_id
                              AND b.status = d.status
                              AND b.otp_code_hash IS NOT DISTINCT FROM d.otp_code_hash
                        ) AS batch_amount
                    FROM fact_disbursements d
                    WHERE d.tenant_id = s.tenant_id AND d.supplier_id = s.supplier_id
                      AND d.status IN ('PENDING_OTP', 'PENDING')
                    ORDER BY d.created_at DESC LIMIT 1
                ) pd ON true
                -- A FAILED disbursement moved no money (§ _apply_success only
                -- runs on SUCCESS), so the bill stays outstanding and the
                -- vendor keeps showing as ready/blocked on its own merits —
                -- this lists every attempt that didn't go through, not just the
                -- newest, since a vendor can fail more than once (a wrong
                -- account the first time, an expired OTP the second).
                LEFT JOIN LATERAL (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'disbursement_id', d.disbursement_id::text,
                            'invoice_id', d.invoice_id::text,
                            'amount', d.amount,
                            'reason', d.status_message,
                            'failed_at', d.updated_at
                        ) ORDER BY d.updated_at DESC
                    ) AS failures
                    FROM fact_disbursements d
                    WHERE d.tenant_id = s.tenant_id AND d.supplier_id = s.supplier_id
                      AND d.status = 'FAILED'
                ) lf ON true
                WHERE s.tenant_id = %s
                GROUP BY s.supplier_id, s.name, s.gstn, s.status, s.bank_verified, s.bank_account,
                         sba.any_verified, pd.status, pd.disbursement_id,
                         pd.otp_expires_at, pd.batch_ids, pd.batch_amount, lf.failures,
                         bacct.account_number, bacct.bank_name
                ORDER BY earliest_due ASC NULLS LAST, payable_amount DESC
                """,
                (tenant_id,),
            )
            vendors = []
            for row in cur.fetchall():
                v = _jsonable_row(row)
                v["can_pay"] = bool(v["gstin_verified"]) and bool(v["bank_verified"])
                v["beneficiary_account_masked"] = _mask_account(v["beneficiary_account"]) if v.get("beneficiary_account") else None
                del v["beneficiary_account"]
                vendors.append(v)
        return {"vendors": vendors}
    finally:
        put_conn(conn)


# ── GET /api/disbursements/recent ────────────────────────────────────────────
# Cross-vendor activity feed for the Payment Hub — every other list endpoint
# here is scoped to one bill or one vendor.

@router.get("/disbursements/recent")
def disbursement_recent(limit: int = 25, company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    limit = max(1, min(limit, 100))
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            _expire_stale_otps(cur, tenant_id)
            conn.commit()
            cur.execute(
                """
                SELECT
                    d.disbursement_id::text, d.invoice_id::text, d.supplier_id::text,
                    s.name AS vendor_name, fi.invoice_number,
                    d.amount, d.status, d.status_message, d.act_code, d.bank_rrn, d.tran_ref_no,
                    d.initiated_by, d.updated_by, d.created_at, d.updated_at,
                    -- Everything a receipt needs for a payment already made —
                    -- the Payment Hub's "Released" lane builds a real PDF from
                    -- this, the same builder the wizard's own receipt uses,
                    -- not a re-summary of what's already visible on the card.
                    d.beneficiary_name, d.beneficiary_account, d.beneficiary_ifsc,
                    d.bank_provider, d.remitter_account_number, d.is_overridden
                FROM fact_disbursements d
                LEFT JOIN dim_suppliers s ON s.supplier_id = d.supplier_id AND s.tenant_id = d.tenant_id
                LEFT JOIN fact_invoices fi ON fi.invoice_id = d.invoice_id AND fi.tenant_id = d.tenant_id
                WHERE d.tenant_id = %s
                ORDER BY d.created_at DESC
                LIMIT %s
                """,
                (tenant_id, limit),
            )
            return {"disbursements": [_jsonable_row(r) for r in cur.fetchall()]}
    finally:
        put_conn(conn)


# ── GET /api/bills/{invoice_id}/disbursements ────────────────────────────────

@router.get("/bills/{invoice_id}/disbursements")
def list_disbursements(invoice_id: str, company_id: Optional[str] = None):
    tenant_id = _tid(company_id)
    conn = get_conn()
    try:
        with _dictcur(conn) as cur:
            _expire_stale_otps(cur, tenant_id)
            conn.commit()
            cur.execute(
                """SELECT disbursement_id, amount, status, status_message, bank_rrn, bank_provider,
                          tran_ref_no, initiated_by, updated_by, created_at, updated_at
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
    # Which bank actually moves the money. Omitted means the remitter account's
    # own bank, which is how every payment behaved before this was selectable.
    # Validated against the adapter registry rather than stored as given: an
    # unrecognised value must not reach the gateway as a bankProvider.
    bank_provider: Optional[str] = None
    # Admin-only manual override — substitutes different beneficiary details
    # when the vendor's bank account and/or GSTIN aren't (yet) verified.
    # Every override is written to disbursement_overrides + wf_audit_log (§7.2).
    override: bool = False
    override_account_number: Optional[str] = None
    override_ifsc: Optional[str] = None
    override_beneficiary_name: Optional[str] = None
    override_reason: Optional[str] = None


def _assert_imps_available(tenant_id: str) -> None:
    """IMPS is an Indian rail: refuse it for a tenant whose jurisdiction does
    not offer it (core/jurisdiction.py), before any routing or OTP happens."""
    j = get_jurisdiction(tenant_id)
    if not j.has("imps"):
        raise HTTPException(status_code=400, detail={
            "error": "RAIL_NOT_AVAILABLE",
            "detail": f"IMPS is not available to {j.country_code} tenants; offered rails: {', '.join(j.rails) or 'none'}",
        })


def _resolve_beneficiary(cur, tenant_id: str, invoice_id: str, supplier_id: str, body: "OverrideFields"):
    """Enforces the verified-bank + verified-GSTIN guardrail (§7.1) and, when an
    admin override is supplied, writes disbursement_overrides + a wf_audit_log
    diff (§7.2) before returning the beneficiary details to actually pay to.
    Raises HTTPException on any guardrail failure. One override row is written
    per bill even in a bulk batch, so each bill's audit trail stands alone."""
    supplier = _get_supplier_kyb(cur, tenant_id, supplier_id)
    # A verified GSTIN is required only where Indian GST applies
    # (core/jurisdiction.py); a vendor of a tenant outside India has none.
    gst_required = jurisdiction_for_cursor(cur, tenant_id).has("gst")
    gstin_verified = bool(supplier and supplier["status"] == "VERIFIED") if gst_required else True
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
                # The verified token identity is the record of who overrode this,
                # not the client-supplied body.actor_name/actor_role — those are
                # kept as a display-name fallback only, never as the audited actor.
                tenant_id, invoice_id, supplier_id, _actor() or body.actor_name, current_role() or body.actor_role,
                body.override_reason,
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
                    "overridden_by": _actor() or body.actor_name, "overridden_by_role": current_role() or body.actor_role,
                }, default=str),
            ),
        )
        return beneficiary_account, beneficiary_ifsc, beneficiary_name, True, override_id
    raise HTTPException(
        status_code=400,
        detail="Vendor bank account and/or GSTIN not verified — an admin override with a reason is required",
    )


# fact_disbursements.status is only ever settled at these two. Everything else —
# PENDING_OTP, PENDING, or a value a gateway invented — means the payment may
# still be moving, and is treated as in flight.
_TERMINAL_STATUSES = ("SUCCESS", "FAILED")


def _canonical_status(bank_provider: Optional[str], raw_status: Optional[str],
                      act_code: Optional[str] = None, reason: Optional[str] = None) -> str:
    """The gateway's answer expressed in the one vocabulary this table stores.

    fact_disbursements.status used to take whatever the gateway said, verbatim.
    That works while every payment goes through ICICI, which happens to answer
    in SUCCESS/PENDING/FAILED — but AU's enquiry speaks OPEN, TOBEPROCESSED,
    SENTTOCORE and PAID, and its statusCode 100/101 means PROCESSING. Any of
    those landing in the column reads as "not in flight" to the guard that stops
    a bill being paid twice, because that guard can only match statuses it
    knows. Normalising here keeps the column meaning one thing; the bank's raw
    answer is still kept in raw_response_jsonb.

    An unrecognised status becomes PENDING, never SUCCESS: "we cannot tell"
    must leave the payment open to a status check, not record money as
    delivered.
    """
    s = (raw_status or "").strip().upper()
    if s in _TERMINAL_STATUSES or s == "PENDING":
        return s
    resolved = bank_payments.for_bank(bank_provider).to_txn_status(
        status=s or None, act_code=act_code, status_code=act_code or (s or None), reason=reason,
    )
    if resolved:
        return resolved
    logger.warning("[disbursement] unrecognised %s status %r — recording PENDING",
                   bank_provider, raw_status)
    return "PENDING"


# Messages that are nothing but an outcome word, mapped to what that word
# claims. Only these can contradict a status; anything else carries detail of
# its own. None means the word asserts nothing at all.
_BARE_OUTCOME_WORDS = {
    "SUCCESS": "SUCCESS", "SUCCESSFUL": "SUCCESS", "OK": "SUCCESS",
    "APPROVED": "SUCCESS", "COMPLETED": "SUCCESS", "PAID": "SUCCESS",
    "FAILED": "FAILED", "FAILURE": "FAILED", "REJECTED": "FAILED",
    "DECLINED": "FAILED", "ERROR": "FAILED",
    "PENDING": "PENDING", "PROCESSING": "PENDING", "IN PROGRESS": "PENDING",
    "INPROGRESS": "PENDING", "ACCEPTED": "PENDING",
    "NA": None, "N/A": None, "-": None, "UNKNOWN": None,
}

_CANONICAL_PHRASE = {
    "SUCCESS": "Transaction successful",
    "FAILED": "Transaction failed",
    "PENDING": "Processing at the bank — not yet confirmed",
}


def _display_message(canonical_status: str, raw_message: Optional[str]) -> str:
    """The message stored beside the status, guaranteed not to contradict it.

    AU answers a transaction enquiry at two levels: the *batch* was accepted
    ("responseMessage": "SUCCESS") while the transaction inside it is still
    SENTTOCORE. Lendix flattens the batch word into `statusMessage`, so a
    payment the bank had not confirmed arrived with status PENDING and the
    message "SUCCESS" — which the receipt rendered directly underneath
    "Processing at the bank", telling the user two different things about
    their money.

    A message with real content is kept as-is: it is the bank's own
    explanation and usually the most useful line on the card. Only a bare
    outcome word is examined, and only replaced when it claims an outcome
    other than the one we resolved — "ACCEPTED" next to PENDING agrees and
    stays, "SUCCESS" next to PENDING does not.
    """
    msg = (raw_message or "").strip()
    if not msg:
        return _CANONICAL_PHRASE.get(canonical_status, msg)
    key = msg.upper().rstrip(".")
    if key in _BARE_OUTCOME_WORDS and _BARE_OUTCOME_WORDS[key] != canonical_status:
        return _CANONICAL_PHRASE.get(canonical_status, msg)
    return msg


def _assert_not_already_in_flight(cur, tenant_id: str, invoice_id: str, amount: float,
                                  outstanding: float) -> None:
    """Refuse a payment that, together with ones already in flight, overpays the bill.

    fact_invoices.outstanding_amount is only reduced in _apply_success, so
    between initiating a payment and the bank confirming it the bill still reads
    as fully outstanding. Nothing else stopped a second payment being started in
    that window: a double-click, two admins on the same bill, or a retry while
    the first was PROCESSING all produced two real transfers for one bill.

    Amount-based rather than a blanket one-at-a-time, so genuine part-payments
    still work. PENDING_OTP and PENDING are the in-flight states; a FAILED
    disbursement is settled and correctly leaves the bill payable again.
    """
    cur.execute(
        """SELECT COALESCE(SUM(amount), 0) AS inflight, count(*) AS n
           FROM fact_disbursements
           WHERE tenant_id = %s AND invoice_id = %s AND status <> ALL(%s)""",
        (tenant_id, invoice_id, list(_TERMINAL_STATUSES)),
    )
    row = cur.fetchone() or {}
    inflight = float(row.get("inflight") or 0)
    if inflight and inflight + amount > outstanding + 0.01:
        raise HTTPException(
            status_code=409,
            detail=(f"This bill already has {int(row['n'])} payment(s) in flight totalling "
                    f"₹{inflight:,.2f}. Adding ₹{amount:,.2f} would exceed the outstanding "
                    f"₹{outstanding:,.2f}. Wait for those to settle, or refresh their status first."),
        )


def _resolve_bank_routing(cur, tenant_id: str, body) -> tuple[str, str, str, dict]:
    """(bank_provider, remitter_account_number, nbfc_name, remitter_row).

    An explicit bank choice is validated through the adapter registry, so a typo
    or an unsupported bank is refused here rather than reaching the gateway as a
    bankProvider it does not recognise. No choice falls back to the tenant's
    existing remitter row — what every payment did before the bank was
    selectable.

    The bank is resolved *before* the remitter row is fetched, because the row
    is per (tenant, bank): its balance is what a successful payment draws down,
    so paying through AU has to debit AU's row, not whichever row happened to be
    created first.

    The account and nbfcName then come from that bank's gateway profile, because
    the same Lendix endpoint expects a different pair per bank — AU's UAT
    contract is nbfcName EXUDE against remitter 2602201113751781, and sending
    ICICI's nbfcName with bankProvider AUBANK is rejected. They are stored on the
    disbursement row rather than substituted at call time, so the row records
    what was actually sent and the later status check reads the same values back.
    """
    chosen = getattr(body, "bank_provider", None)
    bank = None
    if chosen:
        bank = bank_payments.normalise(chosen)
        if bank is None:
            supported = ", ".join(b["code"] for b in bank_payments.supported_banks())
            raise HTTPException(status_code=400,
                                detail=f"Unsupported disbursal bank {chosen!r}. Supported: {supported}")

    remitter = _get_or_seed_remitter_account(cur, tenant_id, bank)
    bank = bank or remitter["bank_provider"]
    profile = gateway_profile(bank)
    return (
        bank,
        profile["remitter_account"] or remitter["account_number"],
        profile["nbfc_name"] or remitter.get("nbfc_name") or DISBURSEMENT_NBFC_NAME,
        remitter,
    )


# ── POST /api/bills/{invoice_id}/disbursements ───────────────────────────────

class DisbursementCreate(OverrideFields):
    amount: float
    # "USDC" routes the payment to the crypto rail (domain/crypto/payouts.py);
    # omitted or "IMPS" is the bank flow below, unchanged.
    rail: Optional[str] = None


@router.post("/bills/{invoice_id}/disbursements")
def initiate_disbursement(invoice_id: str, body: DisbursementCreate):
    tenant_id = _tid(body.company_id)
    # Role comes from the verified token, not body.actor_role — the body field is
    # client-controlled, so the old check was satisfied by simply sending
    # actor_role="admin". body.actor_name/actor_role remain for the audit trail.
    require_role("admin")
    if (body.rail or "").strip().upper() == "USDC":
        # The crypto rail has its own guardrails (PolicyEngine) and settles from
        # the client's Safe, so none of the bank routing below applies.
        from ..domain.crypto.payouts import PayoutService
        return PayoutService().prepare(tenant_id, invoice_id, body.amount, _actor() or body.actor_name)
    _assert_imps_available(tenant_id)

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
            _assert_not_already_in_flight(cur, tenant_id, invoice_id, body.amount, outstanding)

            if not bill["supplier_id"]:
                raise HTTPException(status_code=400, detail="Bill has no linked vendor")
            supplier_id = str(bill["supplier_id"])
            beneficiary_account, beneficiary_ifsc, beneficiary_name, is_overridden, override_id = \
                _resolve_beneficiary(cur, tenant_id, invoice_id, supplier_id, body)

            bank_provider, remitter_account_no, nbfc_name, remitter = _resolve_bank_routing(cur, tenant_id, body)
            # The bank's own per-transaction bounds, checked before a tran_ref_no
            # is spent on a payment it would reject.
            why = bank_payments.for_bank(bank_provider).check_amount(float(body.amount))
            if why:
                raise HTTPException(status_code=400, detail=why)

            otp = "".join(random.choices(string.digits, k=6))
            otp_expires_at = time.time() + OTP_TTL_SECONDS
            tran_ref_no = _gen_tran_ref_no()

            cur.execute(
                """INSERT INTO fact_disbursements
                       (tenant_id, invoice_id, supplier_id, amount, beneficiary_name, beneficiary_account,
                        beneficiary_ifsc, beneficiary_mobile, remitter_account_number, bank_provider, nbfc_name,
                        tran_ref_no, status, otp_code_hash, otp_expires_at, initiated_by, is_overridden, override_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_OTP',%s,%s,%s,%s,%s)
                   RETURNING disbursement_id""",
                (
                    tenant_id, invoice_id, supplier_id, body.amount, beneficiary_name,
                    beneficiary_account, beneficiary_ifsc,
                    "9999999999",  # demo placeholder — vendor mobile isn't captured elsewhere in this schema yet
                    remitter_account_no, bank_provider, nbfc_name,
                    tran_ref_no, _hash_otp(otp), otp_expires_at, _actor() or body.actor_name,
                    is_overridden, override_id,
                ),
            )
            disbursement_id = cur.fetchone()["disbursement_id"]
            _audit(cur, tenant_id, disbursement_id, None,
                   {"event": "initiated", "amount": body.amount, "invoice_id": invoice_id, "is_overridden": is_overridden},
                   _actor() or body.actor_name)
            conn.commit()

        notify_email = remitter["notify_email"] or SMTP_ADMIN_EMAIL
        if demo_payments.is_demo(tenant_id):
            # Demo payments: nothing is mailed; the UI confirms with this code itself.
            return {"disbursement_id": str(disbursement_id), "otp_sent_to": "", "status": "PENDING_OTP",
                    "demo_otp": otp, "demo_payment": True,
                    "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
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
    # Role from the verified token, not body.actor_role (client-controlled) —
    # matches the single-bill endpoint above; body.actor_name/actor_role remain
    # for the audit trail only.
    require_role("admin")
    _assert_imps_available(tenant_id)
    if not body.bills:
        raise HTTPException(status_code=400, detail="Select at least one bill to pay")

    conn = get_conn()
    conn.autocommit = False
    try:
        with _dictcur(conn) as cur:
            bank_provider, remitter_account_no, nbfc_name, remitter = _resolve_bank_routing(cur, tenant_id, body)
            bank_adapter = bank_payments.for_bank(bank_provider)
            why = bank_adapter.check_batch(len(body.bills))
            if why:
                raise HTTPException(status_code=400, detail=why)
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
                _assert_not_already_in_flight(cur, tenant_id, str(item.invoice_id), item.amount, outstanding)
                why = bank_adapter.check_amount(float(item.amount))
                if why:
                    raise HTTPException(status_code=400, detail=f"Bill {item.invoice_id}: {why}")

                beneficiary_account, beneficiary_ifsc, beneficiary_name, is_overridden, override_id = \
                    _resolve_beneficiary(cur, tenant_id, item.invoice_id, supplier_id, body)

                tran_ref_no = _gen_tran_ref_no()
                cur.execute(
                    """INSERT INTO fact_disbursements
                           (tenant_id, invoice_id, supplier_id, amount, beneficiary_name, beneficiary_account,
                            beneficiary_ifsc, beneficiary_mobile, remitter_account_number, bank_provider, nbfc_name,
                            tran_ref_no, status, otp_code_hash, otp_expires_at, initiated_by, is_overridden, override_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING_OTP',%s,%s,%s,%s,%s)
                       RETURNING disbursement_id""",
                    (
                        tenant_id, item.invoice_id, supplier_id, item.amount, beneficiary_name,
                        beneficiary_account, beneficiary_ifsc, "9999999999",
                        remitter_account_no, bank_provider, nbfc_name,
                        tran_ref_no, otp_hash, otp_expires_at, _actor() or body.actor_name,
                        is_overridden, override_id,
                    ),
                )
                disbursement_id = cur.fetchone()["disbursement_id"]
                disbursement_ids.append(str(disbursement_id))
                total_amount += item.amount
                _audit(cur, tenant_id, disbursement_id, None,
                       {"event": "initiated", "amount": item.amount, "invoice_id": item.invoice_id,
                        "is_overridden": is_overridden, "bulk": True},
                       _actor() or body.actor_name)
            conn.commit()

        notify_email = remitter["notify_email"] or SMTP_ADMIN_EMAIL
        if demo_payments.is_demo(tenant_id):
            return {"disbursement_ids": disbursement_ids, "otp_sent_to": "", "status": "PENDING_OTP",
                    "demo_otp": otp, "demo_payment": True,
                    "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
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
    # Same admin-only rule as initiating a payment (§7.1) — this call makes the
    # real bank transfer on a successful OTP, so it can't be left open to any
    # authenticated user the way it was before.
    require_role("admin")
    if not body.disbursement_ids:
        raise HTTPException(status_code=400, detail="No disbursements to verify")

    conn = get_conn()
    conn.autocommit = False
    try:
        results = []
        settled: list[str] = []
        with _dictcur(conn) as cur:
            cur.execute(
                "SELECT * FROM fact_disbursements WHERE tenant_id=%s AND disbursement_id::text = ANY(%s)",
                (tenant_id, body.disbursement_ids),
            )
            rows = {str(r["disbursement_id"]): dict(r) for r in cur.fetchall()}
            if len(rows) != len(body.disbursement_ids):
                raise HTTPException(status_code=404, detail="One or more disbursements not found")
            if any((r.get("rail") or "IMPS") == "USDC" for r in rows.values()):
                raise HTTPException(status_code=400, detail="USDC payouts are released one bill at a time")

            # All rows in a bulk batch share the same OTP hash — check once against the first.
            first = next(iter(rows.values()))
            if first["status"] != "PENDING_OTP":
                raise HTTPException(status_code=400, detail="This batch is no longer awaiting OTP")
            ok, via_demo = _check_otp(body.otp, first["otp_code_hash"])
            if not via_demo:
                if time.time() > (first["otp_expires_at"] or 0):
                    _fail_expired_otp(cur, tenant_id, list(rows.keys()), rows)
                    conn.commit()
                    raise HTTPException(status_code=400, detail="OTP expired — request a new one")
                if first["otp_attempts"] >= MAX_OTP_ATTEMPTS:
                    raise HTTPException(status_code=400, detail="Too many attempts — request a new OTP")
            if not ok:
                cur.execute(
                    "UPDATE fact_disbursements SET otp_attempts = otp_attempts + 1, updated_at=now(), updated_by=%s WHERE disbursement_id::text = ANY(%s)",
                    (_actor(), body.disbursement_ids),
                )
                conn.commit()
                raise HTTPException(status_code=400, detail="Incorrect OTP")

            # Each bill is its own unit of work, committed as soon as its bank
            # call returns. Previously the whole batch shared one transaction
            # and committed at the end: a bill that raised rolled back the
            # status rows of every earlier bill whose money had already moved,
            # leaving them reading PENDING_OTP with the transfer done. One
            # bill's failure must never rewrite another bill's settled history.
            for disbursement_id, d in rows.items():
                try:
                    vendor_name, month = _get_vendor_name_and_month(cur, tenant_id, str(d["invoice_id"]), str(d["supplier_id"]) if d["supplier_id"] else None)
                    bank_result = _forced_success_initiate(cur, d, vendor_name, month) if demo_payments.skips_gateway(tenant_id) else _real_imps_initiate(cur, d, vendor_name, month)
                    data = bank_result["data"]
                    new_status = _canonical_status(d["bank_provider"], data["status"],
                                                   bank_result.get("actCode"), data.get("statusMessage"))
                    message = _display_message(new_status, data.get("statusMessage"))
                    cur.execute(
                        """UPDATE fact_disbursements SET
                               status=%s, status_message=%s, act_code=%s, bank_rrn=%s, bank_tran_ref_no=%s,
                               otp_verified_at=now(), raw_response_jsonb=%s, updated_at=now(), updated_by=%s
                           WHERE disbursement_id=%s""",
                        (
                            new_status, message, bank_result["actCode"], data["bankRRN"],
                            data["tranRefNo"], json.dumps(bank_result), _actor(), disbursement_id,
                        ),
                    )
                    # Settlement follows a real SUCCESS and nothing else: the
                    # bill is only marked paid, and the remitter balance only
                    # drawn down, when the bank actually said so.
                    if new_status == "SUCCESS":
                        _apply_success(cur, tenant_id, d, bank_result)
                        settled.append(disbursement_id)
                    _audit(cur, tenant_id, disbursement_id, d, {"event": "otp_verified", **bank_result, "bulk": True, "demo_otp": via_demo}, _actor())
                    conn.commit()
                    results.append({
                        "disbursement_id": disbursement_id, "invoice_id": str(d["invoice_id"]),
                        "amount": float(d["amount"]), "status": new_status,
                        "status_message": message, "bank_rrn": data["bankRRN"],
                    })
                except Exception as exc:
                    # Only this bill's partial work is discarded. Whether its
                    # money moved is unknown — _real_imps_initiate may have
                    # reached the bank before the failure — so it is recorded
                    # PENDING, never FAILED, and _already_sent_to_gateway stops
                    # it being re-sent. The gateway-call row is written on its
                    # own connection, so it survives this rollback.
                    conn.rollback()
                    traceback.print_exc()
                    unknown = ("Interrupted after the payment was handed to the bank; "
                               "its outcome is unknown. Check status before any retry.")
                    try:
                        cur.execute(
                            """UPDATE fact_disbursements
                                  SET status='PENDING', status_message=%s, otp_verified_at=now(),
                                      updated_at=now(), updated_by=%s
                                WHERE disbursement_id=%s""",
                            (unknown, _actor(), disbursement_id),
                        )
                        _audit(cur, tenant_id, disbursement_id, d,
                               {"event": "otp_verify_interrupted", "error": str(exc),
                                "bulk": True, "resolved": False}, _actor())
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        traceback.print_exc()
                    results.append({
                        "disbursement_id": disbursement_id, "invoice_id": str(d["invoice_id"]),
                        "amount": float(d["amount"]), "status": "PENDING",
                        "status_message": unknown, "bank_rrn": None,
                    })

        # One receipt per vendor covering the whole batch, not one per bill —
        # the agent groups these (app/domain/payments/notify.py).
        if settled:
            payment_notify.enqueue_payment_receipt(tenant_id, settled, _actor())

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
    require_role("admin")
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
                "UPDATE fact_disbursements SET otp_code_hash=%s, otp_expires_at=%s, otp_attempts=0, updated_at=now(), updated_by=%s WHERE disbursement_id=%s",
                (_hash_otp(otp), otp_expires_at, _actor(), disbursement_id),
            )
            if (d.get("rail") or "IMPS") == "USDC":
                # A USDC payout has no remitter bank account (don't seed one), and its
                # OTP mail is best-effort, as on the first send in domain/crypto/payouts.py.
                _audit(cur, tenant_id, disbursement_id, None, {"event": "otp_resent", "rail": "USDC"}, _actor())
                conn.commit()
                if demo_payments.is_demo(tenant_id):
                    return {"otp_sent_to": "", "demo_otp": otp, "demo_payment": True,
                            "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
                sent_to = ""
                if SMTP_ADMIN_EMAIL:
                    try:
                        _send_otp_email(SMTP_ADMIN_EMAIL, otp, d["beneficiary_name"], float(d["amount"]))
                        sent_to = _mask_email(SMTP_ADMIN_EMAIL)
                    except Exception as e:
                        print(f"[disbursement] USDC OTP email send failed: {e}")
                return {"otp_sent_to": sent_to, "demo_otp": DISBURSEMENT_DEMO_OTP or None,
                        "otp_expires_at": otp_expires_at, "otp_ttl_seconds": OTP_TTL_SECONDS}
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
    require_role("admin")
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
            if (d.get("rail") or "IMPS") == "USDC":
                # A USDC payout's OTP releases it to the Safe owners for signing,
                # not to a bank — domain/crypto/payouts.py owns that step.
                conn.rollback()
                from ..domain.crypto.payouts import PayoutService
                return PayoutService().release_after_otp(tenant_id, disbursement_id, body.otp, _actor())
            if d["status"] != "PENDING_OTP":
                raise HTTPException(status_code=400, detail="This payment is no longer awaiting OTP")

            ok, via_demo = _check_otp(body.otp, d["otp_code_hash"])
            if not via_demo:
                if time.time() > (d["otp_expires_at"] or 0):
                    _fail_expired_otp(cur, tenant_id, [disbursement_id], {disbursement_id: dict(d)})
                    conn.commit()
                    raise HTTPException(status_code=400, detail="OTP expired — request a new one")
                if d["otp_attempts"] >= MAX_OTP_ATTEMPTS:
                    raise HTTPException(status_code=400, detail="Too many attempts — request a new OTP")

            if not ok:
                cur.execute(
                    "UPDATE fact_disbursements SET otp_attempts = otp_attempts + 1, updated_at=now(), updated_by=%s WHERE disbursement_id=%s",
                    (_actor(), disbursement_id),
                )
                _audit(cur, tenant_id, disbursement_id, dict(d), {"event": "otp_failed"}, _actor())
                conn.commit()
                raise HTTPException(status_code=400, detail="Incorrect OTP")

            vendor_name, month = _get_vendor_name_and_month(cur, tenant_id, str(d["invoice_id"]), str(d["supplier_id"]) if d["supplier_id"] else None)
            # Everything from the bank call onwards gets its own handler, for the
            # same reason the batch path does: once this returns, money may have
            # moved, and the outer `except` would roll the row back to
            # PENDING_OTP — a state that says "nothing has happened yet" about a
            # payment that may already be with the bank.
            try:
                bank_result = _forced_success_initiate(cur, d, vendor_name, month) if demo_payments.skips_gateway(tenant_id) else _real_imps_initiate(cur, d, vendor_name, month)
                data = bank_result["data"]
                new_status = _canonical_status(d["bank_provider"], data["status"],
                                               bank_result.get("actCode"), data.get("statusMessage"))
                message = _display_message(new_status, data.get("statusMessage"))

                cur.execute(
                    """UPDATE fact_disbursements SET
                           status=%s, status_message=%s, act_code=%s, bank_rrn=%s, bank_tran_ref_no=%s,
                           otp_verified_at=now(), raw_response_jsonb=%s, updated_at=now(), updated_by=%s
                       WHERE disbursement_id=%s""",
                    (
                        new_status, message, bank_result["actCode"], data["bankRRN"],
                        data["tranRefNo"], json.dumps(bank_result), _actor(), disbursement_id,
                    ),
                )

                # Settlement follows a confirmed SUCCESS and nothing else.
                if new_status == "SUCCESS":
                    _apply_success(cur, tenant_id, d, bank_result)

                _audit(cur, tenant_id, disbursement_id, dict(d), {"event": "otp_verified", **bank_result, "demo_otp": via_demo}, _actor())
                conn.commit()
            except Exception as exc:
                conn.rollback()
                traceback.print_exc()
                new_status = "PENDING"
                unknown = ("Interrupted after the payment was handed to the bank; "
                           "its outcome is unknown. Check status before any retry.")
                try:
                    cur.execute(
                        """UPDATE fact_disbursements
                              SET status='PENDING', status_message=%s, otp_verified_at=now(),
                                  updated_at=now(), updated_by=%s
                            WHERE disbursement_id=%s""",
                        (unknown, _actor(), disbursement_id),
                    )
                    _audit(cur, tenant_id, disbursement_id, dict(d),
                           {"event": "otp_verify_interrupted", "error": str(exc), "resolved": False},
                           _actor())
                    conn.commit()
                except Exception:
                    conn.rollback()
                    traceback.print_exc()
                return {
                    "disbursement_id": disbursement_id, "status": "PENDING",
                    "status_message": unknown, "bank_rrn": None,
                    "tran_ref_no": d["tran_ref_no"],
                }

        # Committed money, so now tell the vendor. Off the request path and
        # after the commit, deliberately: the receipt describes a payment that
        # has already happened, and a mail problem must not fail — or delay —
        # the answer to a payment that went through.
        if new_status == "SUCCESS":
            payment_notify.enqueue_payment_receipt(tenant_id, [disbursement_id], _actor())

        return {
            "disbursement_id": disbursement_id,
            "status": new_status,
            "status_message": message,
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


def reconcile_disbursement(conn, tenant_id: str, disbursement_id: str, *, actor: Optional[str] = None,
                           event: str = "status_refreshed") -> dict:
    """Ask the bank where one PENDING payment stands, and record the answer.

    The single implementation of "find out what happened and act on it",
    shared by the user pressing Check status and by the background sweeper.
    Keeping it in one place is the point: settlement credits the bill and
    draws down the remitter balance, so a second copy that drifted would
    quietly pay a bill twice or not at all.

    Commits on success. Returns the same shape the endpoint replies with,
    plus `settled` — True only on the transition into SUCCESS, so exactly one
    caller sends the receipt however many times this is invoked.
    """
    who = actor or _actor()
    with _dictcur(conn) as cur:
        cur.execute(
            "SELECT * FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s",
            (disbursement_id, tenant_id),
        )
        d = cur.fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="Disbursement not found")
        if d["status"] != "PENDING":
            # Already resolved (a second check, the sweeper and a tab racing,
            # or another process got there first). Return the same shape as the
            # refresh path — omitting bank_rrn here made the caller render
            # "reference —" for a payment that has a perfectly good reference.
            return {"disbursement_id": disbursement_id, "status": d["status"],
                    "bank_rrn": d["bank_rrn"], "status_message": d["status_message"],
                    "settled": False}

        bank_result = _real_imps_status(cur, dict(d))
        data = bank_result["data"]
        # Resolved once and used for the column, the receipt and the reply.
        # Returning the gateway's raw word instead — as this did — means a
        # payment stored SUCCESS could still reach the caller as "PAID",
        # which the receipt renders as a failure, and skip its receipt mail.
        new_status = _canonical_status(d["bank_provider"], data["status"],
                                       bank_result.get("actCode"), data.get("statusMessage"))
        message = _display_message(new_status, data.get("statusMessage"))
        cur.execute(
            """UPDATE fact_disbursements SET status=%s, status_message=%s, act_code=%s, bank_rrn=%s,
                   raw_response_jsonb=%s, updated_at=now(), updated_by=%s
               WHERE disbursement_id=%s AND status='PENDING'""",
            (new_status, message, bank_result.get("actCode"), data["bankRRN"],
             json.dumps(bank_result), who, disbursement_id),
        )
        # `AND status='PENDING'` above makes the transition the thing that is
        # claimed, not merely observed: if the sweeper and a user's Check
        # status both see PENDING and both get SUCCESS back, only the one whose
        # UPDATE matched a still-PENDING row settles the bill and sends the
        # receipt. The other reads zero rows and does nothing.
        claimed = cur.rowcount == 1
        settled = False
        if claimed and new_status == "SUCCESS":
            _apply_success(cur, tenant_id, dict(d), bank_result)
            settled = True
        if claimed:
            _audit(cur, tenant_id, disbursement_id, dict(d), {"event": event, **bank_result}, who)
        conn.commit()

    return {"disbursement_id": disbursement_id, "status": new_status, "bank_rrn": data["bankRRN"],
            "status_message": message, "settled": settled}


@router.post("/disbursements/{disbursement_id}/refresh-status")
def refresh_status(disbursement_id: str, body: RefreshStatusBody):
    tenant_id = _tid(body.company_id)
    conn = get_conn()
    conn.autocommit = False
    try:
        # A USDC payout has no bank to ask; its status comes from the chain.
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(rail, 'IMPS') FROM fact_disbursements WHERE disbursement_id=%s AND tenant_id=%s",
                        (disbursement_id, tenant_id))
            rail_row = cur.fetchone()
        if rail_row and rail_row[0] == "USDC":
            raise HTTPException(status_code=400, detail="Use /crypto-status for USDC payouts")
        result = reconcile_disbursement(conn, tenant_id, disbursement_id)
        # A PENDING payment that has just settled at the bank earns the same
        # receipt as one that settled on the OTP — claimed by disbursement id,
        # so whichever path gets there first is the only one that sends.
        if result.pop("settled", False):
            payment_notify.enqueue_payment_receipt(tenant_id, [disbursement_id], _actor())
        return result
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)
