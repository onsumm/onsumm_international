"""Bill lifecycle: listing, detail, edits, and the approval chain.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn, ensure_column
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role

from ..core.jurisdiction import get_jurisdiction
from ..core.money import format_money
from fastapi import APIRouter

router = APIRouter()

from ..core.approval_chain import _build_status_name_map, _build_status_role_map, _chain_statuses, _get_next_role_for_status, _get_status_label, _load_chain, _next_status_from_chain, _step_idx_to_status

@router.get("/api/bills")
def list_bills(company_id: str = Query(default=None), role: str = Query(default=None), username: str = Query(default=None), brand_id: str = Query(default=None), cost_center_id: str = Query(default=None), soft_status: str = Query(default='active')):
    tenant = require_tenant()  # tenant from JWT; client company_id ignored

    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    fi.invoice_id,
                    fi.invoice_number        AS id,
                    fi.invoice_date,
                    fi.due_date,
                    fi.status,
                    fi.total_amount,
                    fi.currency_code,
                    fi.created_at,
                    fi.supplier_id,
                    s.name                   AS vendor_name,
                    s.gstn                   AS vendor_gstn,
                    u.file_name              AS original_file,
                    u.uploaded_by_name,
                    u.uploaded_by_role,
                    fi.upload_id,
                    fi.stg_bill_id,
                    fi.category,
                    fi.department,
                    fi.amount_paid,
                    fi.outstanding_amount,
                    fi.payment_status,
                    pay_log.actor_name  AS payment_updated_by,
                    pay_log.created_at  AS payment_updated_at,
                    fi.tally_push_pending,
                    fi.manual_priority,
                    fi.soft_status,
                    fi.subtotal,
                    fi.tax_amount,
                    sb.tds_status,
                    sb.tds_section_code,
                    sb.tds_rate_percent,
                    sb.tds_amount,
                    sb.tds_base_amount,
                    sb.net_payable,
                    fi.original_amount,
                    fi.inr_amount_current,
                    fi.brand_id,
                    fi.cost_center_id,
                    cc.name                  AS cost_center_name,
                    fb.fx_agent_note,
                    fan.severity AS fx_agent_severity,
                    -- The vendor's KYB/GST verification flag — the same `dim_suppliers.status`
                    -- behind the Vendors page VERIFIED badge. Sent with the list so the Bills
                    -- table can grey out "Pay Now" for an unverified vendor without an N+1
                    -- payment-profile fetch per row. Deliberately NOT the payment profile's
                    -- stricter `can_pay` (which also demands a verified bank account): a
                    -- verified vendor whose bank details are still pending stays clickable and
                    -- gets the gateway's audited admin-override path.
                    COALESCE(s.status = 'VERIFIED', false) AS vendor_verified
                FROM fact_invoices fi
                LEFT JOIN dim_suppliers s
                       ON fi.supplier_id = s.supplier_id
                      AND s.tenant_id    = fi.tenant_id
                LEFT JOIN cost_centers cc
                       ON cc.id           = fi.cost_center_id
                      AND cc.tenant_id    = fi.tenant_id
                LEFT JOIN raw_invoice_uploads u
                       ON fi.upload_id   = u.upload_id
                      AND u.tenant_id    = fi.tenant_id
                LEFT JOIN stg_bills sb
                       ON COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) = sb.stg_bill_id
                      AND sb.tenant_id   = fi.tenant_id
                LEFT JOIN fact_bills fb
                       ON fb.bill_id = fi.invoice_id
                      AND fb.tenant_id = fi.tenant_id
                LEFT JOIN LATERAL (
                    SELECT severity
                    FROM fx_agent_notes
                    WHERE target_type = 'BILL'
                      AND target_id = fi.invoice_id
                      AND is_current = true
                    ORDER BY created_at DESC
                    LIMIT 1
                ) fan ON true
                LEFT JOIN LATERAL (
                    SELECT actor_name, created_at
                    FROM bill_approval_chain
                    WHERE invoice_id = fi.invoice_id
                      AND step = 99
                    ORDER BY created_at DESC
                    LIMIT 1
                ) pay_log ON true
                WHERE fi.tenant_id = %s
                  AND (%s::uuid IS NULL OR fi.brand_id = %s::uuid)
                  AND (%s::uuid IS NULL OR fi.cost_center_id = %s::uuid)
                  AND COALESCE(fi.soft_status, 'active') = %s
                ORDER BY fi.created_at DESC
                """,
                (tenant, brand_id, brand_id, cost_center_id, cost_center_id, soft_status or 'active'),
            )
            rows = cur.fetchall()
        put_conn(conn)

        # Build status→allowed maps per department (avoids N+1 queries)
        dept_role_maps: dict = {}
        dept_name_maps: dict = {}
        dept_label_maps: dict = {}
        for r in rows:
            dept = r["department"]
            if dept not in dept_role_maps:
                dept_role_maps[dept] = _build_status_role_map(tenant, dept)
                dept_name_maps[dept] = _build_status_name_map(tenant, dept)
                chain = _load_chain(tenant, dept)
                lm: dict = {}
                idx2 = 0
                for s in chain:
                    if s.get("locked"): continue
                    lm[_step_idx_to_status(idx2)] = s.get("label")
                    idx2 += 1
                dept_label_maps[dept] = lm

        bills = []
        for r in rows:
            dept = r["department"]
            role_map  = dept_role_maps.get(dept, {})
            name_map  = dept_name_maps.get(dept, {})
            label_map = dept_label_maps.get(dept, {})
            allowed_roles = sorted(role_map.get(r["status"], set()))
            allowed_names = name_map.get(r["status"])
            bills.append({
                "invoice_id":       str(r["invoice_id"]),
                "allowed_roles":    allowed_roles,
                "allowed_usernames": allowed_names,
                "status_label":     label_map.get(r["status"]),
                "id":            r["id"] or str(r["invoice_id"]),
                "supplier_id":   str(r["supplier_id"]) if r["supplier_id"] else None,
                "invoice_date":  r["invoice_date"].isoformat() if r["invoice_date"] else None,
                "due_date":      r["due_date"].isoformat() if r["due_date"] else None,
                "status":        r["status"],
                "total_amount":  float(r["total_amount"]) if r["total_amount"] is not None else None,
                "currency_code": r["currency_code"],
                "vendor_name":   r["vendor_name"],
                "vendor_gstn":   r["vendor_gstn"],
                "original_file": r["original_file"],
                "upload_id":        str(r["upload_id"]) if r["upload_id"] else None,
                "stg_bill_id":      str(r["stg_bill_id"]) if r["stg_bill_id"] else None,
                "created_at":       r["created_at"].isoformat() if r["created_at"] else None,
                "uploaded_by_name": r["uploaded_by_name"],
                "uploaded_by_role": r["uploaded_by_role"],
                "category":         r["category"],
                "department":       r["department"],
                **dict(zip(("amount_paid", "outstanding_amount"), _settled(
                    r["payment_status"] if "payment_status" in r.keys() else None,
                    r["amount_paid"], r["outstanding_amount"], r["total_amount"]))),
                "payment_status":     r["payment_status"] if "payment_status" in r.keys() else None,
                "payment_updated_by":  r["payment_updated_by"],
                "payment_updated_at":  r["payment_updated_at"].isoformat() if r["payment_updated_at"] else None,
                "tally_push_pending":  r["tally_push_pending"] or False,
                "manual_priority":    r["manual_priority"],
                "soft_status":        r.get("soft_status") or "active",
                "subtotal":        float(r["subtotal"]) if r["subtotal"] is not None else None,
                "tax_amount":      float(r["tax_amount"]) if r["tax_amount"] is not None else None,
                "tds_status":      r["tds_status"],
                "tds_section_code": r["tds_section_code"],
                "tds_rate":        float(r["tds_rate_percent"]) if r["tds_rate_percent"] is not None else None,
                "tds_amount":      float(r["tds_amount"]) if r["tds_amount"] is not None else None,
                "tds_base_amount": float(r["tds_base_amount"]) if r["tds_base_amount"] is not None else None,
                "net_payable":     float(r["net_payable"]) if r["net_payable"] is not None else None,
                "original_amount": float(r["original_amount"]) if r["original_amount"] is not None else None,
                "inr_amount_current": float(r["inr_amount_current"]) if r["inr_amount_current"] is not None else None,
                "brand_id":        str(r["brand_id"]) if r["brand_id"] else None,
                "cost_center_id":   str(r["cost_center_id"]) if r["cost_center_id"] else None,
                "cost_center_name": r.get("cost_center_name"),
                "fx_agent_note": r.get("fx_agent_note"),
                "fx_agent_severity": r.get("fx_agent_severity"),
                "vendor_verified": bool(r.get("vendor_verified")),
            })
        # Approvers see bills pending their action OR bills they have already acted on
        if role and role not in ("admin", "accountant", "finance_controller"):
            # Fetch invoice_ids this user has already acted on (approve/send_back/submit)
            already_acted: set = set()
            if username:
                try:
                    conn2 = get_conn()
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "SELECT DISTINCT invoice_id FROM bill_approval_chain WHERE tenant_id=%s AND actor_name=%s",
                            (tenant, username),
                        )
                        already_acted = {str(row[0]) for row in cur2.fetchall()}
                    put_conn(conn2)
                except Exception:
                    pass

            bills = [
                b for b in bills
                if str(b.get("invoice_id")) in already_acted
                or (
                    role in (b.get("allowed_roles") or [])
                    and (
                        not b.get("allowed_usernames")
                        or (username and username in (b.get("allowed_usernames") or []))
                    )
                )
            ]

        return {"bills": bills, "total": len(bills)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _bill_payee_bank(r) -> dict | None:
    """The account this bill would be paid into, and whether it is verified.

    Ordering mirrors invoices.py::_lookup_vendor_master — a supplier_bank_accounts
    row (verified and primary first), then the legacy dim_suppliers.bank_*
    columns. Unlike that lookup, an UNVERIFIED account is still returned here,
    carrying `verified: false`: this screen only displays it, and an approver is
    better served by "this is the account, and nobody has penny-dropped it" than
    by a blank that hides an account exists at all. The badge is what stops it
    reading as an account we vouch for, so it has to be unmissable in the UI.
    """
    if r["bank_account_number"]:
        return {
            "account_number": r["bank_account_number"],
            "ifsc":           r["bank_ifsc"] or "",
            "bank_name":      r["bank_name"] or "",
            "name_at_bank":   r["bank_name_at_bank"] or "",
            "verified":       bool(r["bank_verified"]),
        }
    if r["legacy_bank_account"]:
        return {
            "account_number": r["legacy_bank_account"],
            "ifsc":           r["legacy_bank_ifsc"] or "",
            "bank_name":      r["legacy_bank_name"] or "",
            "name_at_bank":   "",
            "verified":       bool(r["legacy_bank_verified"]),
        }
    return None


def _tax_breakdown(r) -> dict | None:
    """The CGST/SGST/IGST split, or None when the bill predates it being kept.

    `reconciles` is the honest part. The split is written once, at ingest, from
    what the document said; PUT /api/bills can change tax_amount afterwards and
    has no way to know how the new total divides. When that happens the
    components are still true of the *document* and no longer add up to the
    bill, so the caller is told rather than left to add three numbers and
    wonder.
    """
    parts = {
        "cgst": _num_or_none(r["cgst_amount"]),
        "sgst": _num_or_none(r["sgst_amount"]),
        "igst": _num_or_none(r["igst_amount"]),
        "cess": _num_or_none(r["cess_amount"]),
    }
    if all(v is None for v in parts.values()):
        return None

    total = sum(v for v in parts.values() if v)
    tax_amount = _num_or_none(r["tax_amount"])
    return {
        **parts,
        "cgst_rate": _num_or_none(r["cgst_rate"]),
        "sgst_rate": _num_or_none(r["sgst_rate"]),
        "igst_rate": _num_or_none(r["igst_rate"]),
        "taxable_amount": _num_or_none(r["taxable_amount"]),
        "place_of_supply": r["place_of_supply"],
        "components_total": total,
        # A rupee of slack: the parts are rounded to paise on the invoice and
        # their sum legitimately drifts a few paise from the stated total.
        "reconciles": tax_amount is None or abs(total - tax_amount) <= 1,
        # Both halves present means one tax split between centre and state,
        # which only happens within a state. Useful enough, and derived here
        # so three screens do not each decide it differently.
        "is_interstate": bool(parts["igst"]) and not (parts["cgst"] or parts["sgst"]),
    }


def _num_or_none(v):
    return float(v) if v is not None else None


@router.get("/api/bills/{invoice_id}")
def get_bill_detail(invoice_id: str):
    tenant = require_tenant()

    conn = None
    try:
        conn = get_conn()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    fi.invoice_id,
                    fi.tenant_id,
                    fi.invoice_number        AS id,
                    fi.invoice_date,
                    fi.due_date,
                    fi.status,
                    fi.subtotal,
                    fi.tax_amount,
                    fi.total_amount,
                    fi.amount_paid,
                    fi.outstanding_amount,
                    fi.payment_status,
                    fi.currency_code,
                    fi.created_at,
                    fi.upload_id,
                    COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) AS stg_bill_id,
                    fi.supplier_id,
                    s.name                   AS vendor_name,
                    s.gstn                   AS vendor_gstn,
                    s.email                  AS vendor_email,
                    s.payment_terms_days,
                    s.vendor_category_id,
                    u.file_name              AS original_file,
                    sb.bill_number,
                    sb.service_month,
                    sb.discount_amount,
                    sb.subtotal              AS stg_subtotal,
                    sb.tax_amount            AS stg_tax_amount,
                    sb.taxable_amount,
                    sb.cgst_amount, sb.sgst_amount, sb.igst_amount, sb.cess_amount,
                    sb.cgst_rate,   sb.sgst_rate,   sb.igst_rate,
                    sb.place_of_supply,
                    fi.category,
                    fi.department,
                    cc.name                  AS cost_center_name,
                    sb.vendor_sap_code,
                    fi.manual_priority,
                    fi.soft_status,
                    sb.tds_status,
                    sb.tds_section_code,
                    sb.tds_rate_percent,
                    sb.tds_amount,
                    sb.tds_base_amount,
                    sb.net_payable,
                    fi.original_amount,
                    fi.inr_amount_current,
                    fi.verification_flag,
                    fi.verification_detail,
                    fi.verified_at,
                    dt.company_name                    AS company_name,
                    br.name                            AS brand_name,
                    dt.gstn                            AS tenant_gstn,
                    -- The account this bill will actually be paid into, ordered
                    -- the way review and disbursement order it (verified first,
                    -- then primary). Unverified rows are NOT filtered out here:
                    -- this screen displays the account and badges its state, so
                    -- the approver sees what exists rather than a blank.
                    sba.account_number                 AS bank_account_number,
                    sba.ifsc                           AS bank_ifsc,
                    sba.bank_name                      AS bank_name,
                    sba.name_at_bank                   AS bank_name_at_bank,
                    sba.verified                       AS bank_verified,
                    s.bank_account                     AS legacy_bank_account,
                    s.bank_ifsc                        AS legacy_bank_ifsc,
                    s.bank_name                        AS legacy_bank_name,
                    s.bank_verified                    AS legacy_bank_verified
                FROM fact_invoices fi
                LEFT JOIN dim_suppliers s
                       ON fi.supplier_id = s.supplier_id AND s.tenant_id = fi.tenant_id
                LEFT JOIN raw_invoice_uploads u
                       ON fi.upload_id = u.upload_id AND u.tenant_id = fi.tenant_id
                LEFT JOIN stg_bills sb
                       ON COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) = sb.stg_bill_id
                      AND sb.tenant_id = fi.tenant_id
                LEFT JOIN dim_tenants dt
                       ON fi.tenant_id = dt.tenant_id
                LEFT JOIN brands br
                       ON fi.brand_id = br.id
                LEFT JOIN cost_centers cc
                       ON fi.cost_center_id = cc.id AND cc.tenant_id = fi.tenant_id
                LEFT JOIN LATERAL (
                    SELECT account_number, ifsc, bank_name, name_at_bank, verified
                      FROM supplier_bank_accounts
                     WHERE tenant_id = fi.tenant_id AND supplier_id = fi.supplier_id
                     ORDER BY verified DESC, is_primary DESC, created_at ASC
                     LIMIT 1
                ) sba ON true
                WHERE fi.invoice_id = %s AND fi.tenant_id = %s
                """,
                (invoice_id, tenant),
            )
            r = cur.fetchone()

            # Ensure target_step column exists before querying it
            ensure_column(cur, "bill_approval_chain", "target_step", "INTEGER DEFAULT NULL")

            # Fetch line items, approval chain, attachments in one round-trip
            stg_bill_id = r["stg_bill_id"] if r else None
            if stg_bill_id:
                cur.execute(
                    """
                    SELECT * FROM (
                        SELECT 'li' AS _t, deduped.line_number::text AS a, deduped.product_name AS b,
                               deduped.hsn_code AS c,
                               deduped.quantity::text AS d, deduped.unit AS e, deduped.unit_price::text AS f,
                               deduped.discount_amount::text AS g, deduped.tax_rate_percent::text AS h,
                               deduped.tax_amount::text AS i, deduped.line_total::text AS j,
                               deduped.sap_item_code AS k, deduped.sap_material_desc AS l,
                               deduped.sap_map_status AS m, deduped.sap_map_score::text AS n,
                               deduped.rate_is_percent::text AS o,
                               la.account_name AS p, la.account_code AS q
                        FROM (
                            SELECT DISTINCT ON (line_number) *
                            FROM stg_bill_line_items WHERE stg_bill_id = %s
                            ORDER BY line_number, ctid DESC
                        ) deduped
                        LEFT JOIN dim_ledger_accounts la ON la.account_id = deduped.account_id
                    ) li
                    UNION ALL
                    SELECT * FROM (
                        SELECT 'ac', id::text, step::text, actor_role, actor_name, action, comment,
                               created_at::text, target_step::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                               NULL, NULL
                        FROM bill_approval_chain WHERE invoice_id = %s ORDER BY created_at ASC
                    ) ac
                    UNION ALL
                    SELECT * FROM (
                        SELECT 'at', id::text, file_name, file_type, file_size::text, uploaded_by,
                               uploaded_at::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                               NULL, NULL
                        FROM bill_attachments WHERE invoice_id = %s AND deleted_at IS NULL ORDER BY uploaded_at DESC
                    ) at
                    """,
                    (stg_bill_id, invoice_id, invoice_id),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM (
                        SELECT 'ac', id::text, step::text, actor_role, actor_name, action, comment,
                               created_at::text, target_step::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                               NULL, NULL
                        FROM bill_approval_chain WHERE invoice_id = %s ORDER BY created_at ASC
                    ) ac
                    UNION ALL
                    SELECT * FROM (
                        SELECT 'at', id::text, file_name, file_type, file_size::text, uploaded_by,
                               uploaded_at::text, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                               NULL, NULL
                        FROM bill_attachments WHERE invoice_id = %s AND deleted_at IS NULL ORDER BY uploaded_at DESC
                    ) at
                    """,
                    (invoice_id, invoice_id),
                )
            rows2 = cur.fetchall()
            line_items = [
                {"line_number": int(x["a"]), "product_name": x["b"], "hsn_code": x["c"],
                 "quantity": float(x["d"]) if x["d"] else None, "unit": x["e"],
                 "unit_price": float(x["f"]) if x["f"] else None,
                 "discount_amount": float(x["g"]) if x["g"] else None,
                 "tax_rate_percent": float(x["h"]) if x["h"] else None,
                 "tax_amount": float(x["i"]) if x["i"] else None,
                 "line_total": float(x["j"]) if x["j"] else None,
                 "sap_item_code": x["k"], "sap_material_desc": x["l"],
                 "sap_map_status": x["m"],
                 "sap_map_score": float(x["n"]) if x["n"] else None,
                 # quantity is the base and unit_price the percent when this is set
                 "rate_is_percent": x["o"] == "true",
                 "ledger_name": x["p"], "ledger_code": x["q"]}
                for x in rows2 if x["_t"] == "li"
            ]
            chain = [
                {"id": x["a"], "step": int(x["b"]), "actor_role": x["c"],
                 "actor_name": x["d"], "action": x["e"], "comment": x["f"],
                 "created_at": x["g"], "target_step": int(x["h"]) if x["h"] else None}
                for x in rows2 if x["_t"] == "ac"
            ]
            attachments = [
                {"id": x["a"], "file_name": x["b"], "file_type": x["c"],
                 "file_size": int(x["d"]) if x["d"] else None,
                 "uploaded_by": x["e"], "uploaded_at": x["f"]}
                for x in rows2 if x["_t"] == "at"
            ]
        put_conn(conn)

        if not r:
            raise HTTPException(status_code=404, detail="Bill not found")

        def _f(v): return float(v) if v is not None else None
        def _d(v): return v.isoformat() if v else None

        # Payment terms shown on a bill must reflect THIS bill's actual dates, not the
        # vendor's standing default. If both dates are extracted, the real term is
        # (due_date - bill_date) days (0 = "due on receipt"); only fall back to the
        # vendor default when a date is missing.
        _terms = r["payment_terms_days"]
        if r["invoice_date"] and r["due_date"]:
            _terms = max((r["due_date"] - r["invoice_date"]).days, 0)

        return {
            "invoice_id":         str(r["invoice_id"]),
            "id":                 r["id"] or str(r["invoice_id"]),
            "bill_number":        r["bill_number"],
            "service_month":      r["service_month"],
            "invoice_date":       _d(r["invoice_date"]),
            "due_date":           _d(r["due_date"]),
            "status":             r["status"],
            "allowed_roles":      sorted(_build_status_role_map(str(r["tenant_id"]), r.get("department")).get(r["status"], set())),
            "allowed_usernames":  _build_status_name_map(str(r["tenant_id"]), r.get("department")).get(r["status"]),
            "status_label":       _get_status_label(str(r["tenant_id"]), r.get("department"), r["status"]),
            "subtotal":           _f(r["subtotal"]),
            "tax_amount":         _f(r["tax_amount"]),
            "tax_breakdown":      _tax_breakdown(r),
            "total_amount":       _f(r["total_amount"]),
            **dict(zip(("amount_paid", "outstanding_amount"),
                       _settled(r.get("payment_status"), r["amount_paid"], r["outstanding_amount"], r["total_amount"]))),
            "payment_status":     r.get("payment_status"),
            "discount_amount":    _f(r["discount_amount"]),
            "currency_code":       r["currency_code"],
            "original_amount":     _f(r["original_amount"]),
            "inr_amount_current":  _f(r["inr_amount_current"]),
            "payment_terms_days":  _terms,
            "upload_id":          str(r["upload_id"]) if r["upload_id"] else None,
            "stg_bill_id":        str(r["stg_bill_id"]) if r["stg_bill_id"] else None,
            "original_file":      r["original_file"],
            "created_at":         _d(r["created_at"]),
            "category":           r["category"],
            "department":         r["department"],
            "cost_center_name":   r["cost_center_name"],
            "vendor_sap_code":    r["vendor_sap_code"],
            "manual_priority":    r["manual_priority"],
            # Lets a bill card opened by id alone (no list row) offer Hold/Restore.
            "soft_status":        r.get("soft_status") or "active",
            "tds_status":         r["tds_status"],
            "tds_section_code":   r["tds_section_code"],
            "tds_rate":           _f(r["tds_rate_percent"]),
            "tds_amount":         _f(r["tds_amount"]),
            "tds_base_amount":    _f(r["tds_base_amount"]),
            "net_payable":        _f(r["net_payable"]),
            "supplier_id":        str(r["supplier_id"]) if r["supplier_id"] else None,
            "vendor_category_id": str(r["vendor_category_id"]) if r["vendor_category_id"] else None,
            "vendor": {
                "name":  r["vendor_name"],
                "gstn":  r["vendor_gstn"],
                "email": r["vendor_email"],
                # Carries `verified` rather than being withheld when false —
                # see _bill_payee_bank. None means no account on file at all.
                "bank":  _bill_payee_bank(r),
            },
            "buyer": {
                "name":  r["company_name"] or r["brand_name"] or "",
                "brand": r["brand_name"] or "",
                "gstn":  r["tenant_gstn"] or "",
            },
            "line_items": [
                {
                    "line_number":      li["line_number"],
                    "product_name":     li["product_name"],
                    "hsn_code":         li["hsn_code"],
                    "quantity":         _f(li["quantity"]),
                    "unit":             li["unit"],
                    "unit_price":       _f(li["unit_price"]),
                    "discount_amount":  _f(li["discount_amount"]),
                    "tax_rate_percent": _f(li["tax_rate_percent"]),
                    "tax_amount":       _f(li["tax_amount"]),
                    "line_total":       _f(li["line_total"]),
                    "sap_item_code":     li["sap_item_code"],
                    "sap_material_desc": li["sap_material_desc"],
                    "sap_map_status":    li["sap_map_status"],
                    "sap_map_score":     li["sap_map_score"],
                    # quantity is the base and unit_price the percent when set
                    "rate_is_percent":   li["rate_is_percent"],
                    "ledger_name":       li["ledger_name"],
                    "ledger_code":       li["ledger_code"],
                }
                for li in line_items
            ],
            "chain": [
                {
                    "id":          c["id"],
                    "step":        c["step"],
                    "actor_role":  c["actor_role"],
                    "actor_name":  c["actor_name"],
                    "action":      c["action"],
                    "comment":     c["comment"],
                    "target_step": c.get("target_step"),
                    "created_at":  c["created_at"] if isinstance(c["created_at"], str) else (c["created_at"].isoformat() if c["created_at"] else None),
                }
                for c in chain
            ],
            "attachments": [
                {
                    "id":          str(a["id"]),
                    "file_name":   a["file_name"],
                    "file_type":   a["file_type"],
                    "file_size":   a["file_size"],
                    "uploaded_by": a["uploaded_by"],
                    "uploaded_at": a["uploaded_at"] if isinstance(a["uploaded_at"], str) else (a["uploaded_at"].isoformat() if a["uploaded_at"] else None),
                }
                for a in attachments
            ],
        }
    except HTTPException:
        put_conn(conn)
        raise
    except Exception as e:
        put_conn(conn, close=True)
        raise HTTPException(status_code=500, detail=str(e))


# Reserved bill_approval_chain step for reconciliation-review audit events
# (99 is used for payment events; see record_payment).
RECON_AUDIT_STEP = 98


BULK_LIMIT = 100

_BULK_ALLOWED_ROLES = {"admin"}


class LineItemUpdate(BaseModel):
    # 0 (or omitted) means "this is a new line" — the row does not exist yet and
    # the server assigns the next free line_number. Any positive value addresses
    # the existing row with that number.
    line_number: int = 0
    product_name: str | None = None
    hsn_code: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    tax_rate_percent: float | None = None
    line_total: float | None = None
    # When true, `quantity` is the BASE amount and `unit_price` is a PERCENTAGE
    # of it, so the line is worth quantity * unit_price / 100 rather than
    # quantity * unit_price. Used for commission, platform fees, brokerage and
    # interest lines, which Indian invoices price as a percentage.
    rate_is_percent: bool = False

class BillUpdateRequest(BaseModel):
    vendor_name: str | None = None
    vendor_gstn: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    discount_amount: float | None = None
    payment_status: str | None = None   # unpaid | partial | paid
    amount_paid: float | None = None
    category: str | None = None
    department: str | None = None
    cost_center_id: str | None = None  # "" clears the tag; UUID sets it
    manual_priority: str | None = None  # p0 | p1 | p2 | p3 | null (admin override)
    actor_name: str | None = None
    actor_role: str | None = None
    # When present this is the AUTHORITATIVE full set of lines for the bill, not a
    # patch: lines missing from it are deleted, and entries with line_number 0 are
    # inserted. Send the whole list or omit the field entirely — a partial list
    # will delete whatever it leaves out.
    line_items: list[LineItemUpdate] | None = None

@router.put("/api/bills/{invoice_id}")
def update_bill(invoice_id: str, body: BillUpdateRequest):
    from ..domain.ingest import _parse_date, _gstn_clean

    tenant = require_tenant()

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT stg_bill_id, supplier_id, tenant_id,
                          invoice_date, due_date, subtotal, tax_amount, total_amount,
                          category
                   FROM fact_invoices WHERE invoice_id=%s AND tenant_id=%s""",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")
            stg_bill_id, supplier_id, bill_tenant_id = row[0], row[1], row[2]
            old = {
                "invoice_date": str(row[3])[:10] if row[3] else None,
                "due_date":     str(row[4])[:10] if row[4] else None,
                "subtotal":     float(row[5]) if row[5] is not None else None,
                "tax_amount":   float(row[6]) if row[6] is not None else None,
                "total_amount": float(row[7]) if row[7] is not None else None,
                "category":     row[8],
            }
            # discount lives in stg_bills, fetch separately if available
            if stg_bill_id:
                cur.execute("SELECT discount_amount FROM stg_bills WHERE stg_bill_id=%s", (stg_bill_id,))
                dr = cur.fetchone()
                old["discount_amount"] = float(dr[0]) if dr and dr[0] is not None else None
            else:
                old["discount_amount"] = None
            # fetch current vendor info for diff
            if supplier_id:
                cur.execute("SELECT name, gstn FROM dim_suppliers WHERE supplier_id=%s", (supplier_id,))
                vrow = cur.fetchone()
                old["vendor_name"] = vrow[0] if vrow else None
                old["vendor_gstn"] = vrow[1] if vrow else None
            else:
                # These defaults used to hang off the line-items condition below,
                # which meant any edit that did not also send line items reset the
                # vendor snapshot to None and lost the vendor name / GSTIN entries
                # from the audit diff. They belong to this branch.
                old["vendor_name"] = old["vendor_gstn"] = None

            # snapshot line items BEFORE update for audit. `is not None` rather
            # than truthiness so an empty list — the user deleting every line —
            # still snapshots what was there to report as removed.
            old_li_map: dict = {}
            if body.line_items is not None and stg_bill_id:
                cur.execute(
                    "SELECT line_number, product_name, hsn_code, quantity, unit_price, tax_rate_percent, line_total FROM stg_bill_line_items WHERE stg_bill_id=%s",
                    (str(stg_bill_id),)
                )
                old_li_map = {r[0]: r for r in cur.fetchall()}

            def _apply(table, pk_col, pk_val, mapping):
                sets, vals = [], []
                for col, val in mapping.items():
                    if val is not None:
                        sets.append(f"{col} = %s")
                        vals.append(val)
                if sets:
                    vals.append(pk_val)
                    cur.execute(f"UPDATE {table} SET {', '.join(sets)} WHERE {pk_col}=%s", vals)

            # Ensure manual_priority column exists (idempotent migration)
            cur.execute(
                "ALTER TABLE fact_invoices ADD COLUMN IF NOT EXISTS manual_priority varchar(4) DEFAULT NULL"
            )

            fi_map = {}
            if body.invoice_date is not None: fi_map["invoice_date"] = _parse_date(body.invoice_date)
            if body.due_date is not None:      fi_map["due_date"]     = _parse_date(body.due_date)
            if body.subtotal is not None:      fi_map["subtotal"]     = body.subtotal
            if body.tax_amount is not None:    fi_map["tax_amount"]   = body.tax_amount
            if body.total_amount is not None:
                fi_map["total_amount"]       = body.total_amount
                fi_map["outstanding_amount"] = body.total_amount
            if body.category is not None:
                fi_map["category"] = body.category or None
            if body.department is not None:
                fi_map["department"] = body.department or None
            if body.cost_center_id is not None:
                # Explicit statement so we control the ::uuid cast and allow clearing.
                cur.execute(
                    "UPDATE fact_invoices SET cost_center_id = %s::uuid WHERE invoice_id = %s",
                    ((body.cost_center_id.strip() or None), invoice_id),
                )
            if body.manual_priority is not None:
                valid = {'p0', 'p1', 'p2', 'p3', ''}
                if body.manual_priority not in valid:
                    raise HTTPException(status_code=400, detail="manual_priority must be p0/p1/p2/p3 or empty")
                fi_map["manual_priority"] = body.manual_priority or None
            if body.payment_status is not None:
                fi_map["payment_status"] = body.payment_status
            if body.amount_paid is not None:
                fi_map["amount_paid"] = body.amount_paid
                total_row = None
                cur.execute("SELECT total_amount FROM fact_invoices WHERE invoice_id=%s", (invoice_id,))
                total_row = cur.fetchone()
                if total_row and total_row[0] is not None:
                    fi_map["outstanding_amount"] = max(0, float(total_row[0]) - body.amount_paid)
            fi_map["source_updated_at"] = "now()"
            _apply("fact_invoices", "invoice_id", invoice_id, {k: v for k, v in fi_map.items() if k != "source_updated_at"})
            if fi_map:
                cur.execute("UPDATE fact_invoices SET source_updated_at=now() WHERE invoice_id=%s",
                            (invoice_id,))

            if stg_bill_id:
                sb_map = {}
                if body.invoice_date is not None:   sb_map["bill_date"]         = _parse_date(body.invoice_date)
                if body.due_date is not None:        sb_map["due_date"]          = _parse_date(body.due_date)
                if body.subtotal is not None:        sb_map["subtotal"]          = body.subtotal
                if body.tax_amount is not None:      sb_map["tax_amount"]        = body.tax_amount
                if body.total_amount is not None:
                    sb_map["total_amount"]       = body.total_amount
                    sb_map["outstanding_amount"] = body.total_amount
                if body.discount_amount is not None: sb_map["discount_amount"]   = body.discount_amount
                _apply("stg_bills", "stg_bill_id", str(stg_bill_id), sb_map)

            if supplier_id and (body.vendor_name or body.vendor_gstn):
                v_map = {}
                if body.vendor_name: v_map["name"] = body.vendor_name
                if body.vendor_gstn:
                    cleaned = _gstn_clean(body.vendor_gstn) or body.vendor_gstn
                    v_map["gstn"] = cleaned
                    # When the GSTIN is actually corrected, re-queue the vendor for GST
                    # verification so the new value is re-checked and the stale
                    # FAILED/VERIFIED status doesn't linger on the wrong GSTIN.
                    # Also refresh the embedded PAN (GSTIN chars 3-12) so PAN-based
                    # duplicate detection sees the corrected identity.
                    if cleaned and cleaned != (old.get("vendor_gstn") or ""):
                        v_map["status"] = "UNVERIFIED"
                        if len(cleaned) == 15:
                            v_map["pan"] = cleaned[2:12].upper()
                _apply("dim_suppliers", "supplier_id", str(supplier_id), v_map)

            added_lines: list[int] = []
            removed_lines: list[int] = []
            if body.line_items is not None and stg_bill_id:
                # The table has no unique constraint on (stg_bill_id, line_number)
                # and duplicates exist in the data — which is why the detail query
                # reads through SELECT DISTINCT ON (line_number) ... ctid DESC.
                # Collapse to exactly the rows the UI displayed before reconciling,
                # or an UPDATE by line_number would write to invisible rows and a
                # DELETE would leave orphans behind.
                cur.execute(
                    """DELETE FROM stg_bill_line_items
                       WHERE stg_bill_id = %s
                         AND line_item_id NOT IN (
                             SELECT DISTINCT ON (line_number) line_item_id
                             FROM stg_bill_line_items
                             WHERE stg_bill_id = %s
                             ORDER BY line_number, ctid DESC
                         )""",
                    (str(stg_bill_id), str(stg_bill_id)),
                )

                cur.execute(
                    "SELECT line_number FROM stg_bill_line_items WHERE stg_bill_id=%s",
                    (str(stg_bill_id),),
                )
                existing = {r[0] for r in cur.fetchall()}
                next_number = (max(existing) + 1) if existing else 1

                kept: set[int] = set()
                for li in body.line_items:
                    if li.line_number and li.line_number in existing:
                        kept.add(li.line_number)
                        li_map = {}
                        if li.product_name is not None:     li_map["product_name"]     = li.product_name
                        if li.hsn_code is not None:         li_map["hsn_code"]         = li.hsn_code
                        if li.quantity is not None:         li_map["quantity"]         = li.quantity
                        if li.unit is not None:             li_map["unit"]             = li.unit
                        if li.unit_price is not None:       li_map["unit_price"]       = li.unit_price
                        if li.tax_rate_percent is not None: li_map["tax_rate_percent"] = li.tax_rate_percent
                        if li.line_total is not None:       li_map["line_total"]       = li.line_total
                        li_map["rate_is_percent"] = bool(li.rate_is_percent)
                        if li_map:
                            sets = ", ".join(f"{k}=%s" for k in li_map)
                            vals = list(li_map.values()) + [str(stg_bill_id), li.line_number]
                            cur.execute(
                                f"UPDATE stg_bill_line_items SET {sets} WHERE stg_bill_id=%s AND line_number=%s",
                                vals
                            )
                        continue

                    # New line. product_name and line_total are NOT NULL, and
                    # tax_amount is derived the same way ingest derives it so a
                    # hand-added line and an OCR'd one agree.
                    rate = float(li.tax_rate_percent or 0)
                    total = float(li.line_total or 0)
                    if not total and li.rate_is_percent and li.quantity and li.unit_price:
                        # A percentage line the client left the total blank on.
                        # base x percent / 100 is the PRE-TAX amount — an invoice
                        # reading "Commission @ 4% on 1,00,000 ... 4,000" then adds
                        # GST on top of the 4,000 — so grossing up is the last step,
                        # not the first. line_total is stored tax-inclusive.
                        net = round(float(li.quantity) * float(li.unit_price) / 100, 4)
                        total = round(net * (1 + rate / 100), 4)
                    else:
                        net = total / (1 + rate / 100) if rate else total
                    cur.execute(
                        """INSERT INTO stg_bill_line_items
                               (stg_bill_id, tenant_id, line_number, product_name, hsn_code,
                                quantity, unit, unit_price, discount_amount,
                                tax_rate_percent, tax_amount, line_total, rate_is_percent)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s)""",
                        (str(stg_bill_id), tenant, next_number,
                         li.product_name or "", li.hsn_code,
                         li.quantity, li.unit, li.unit_price,
                         rate, round(total - net, 4), total,
                         bool(li.rate_is_percent)),
                    )
                    added_lines.append(next_number)
                    kept.add(next_number)
                    next_number += 1

                removed_lines = sorted(existing - kept)
                if removed_lines:
                    cur.execute(
                        "DELETE FROM stg_bill_line_items WHERE stg_bill_id=%s AND line_number = ANY(%s)",
                        (str(stg_bill_id), removed_lines),
                    )

            # Build audit diff — only log fields that actually changed
            def _fmt(v):
                if v is None: return "(none)"
                try:
                    f = float(v)
                    return str(int(f)) if f == int(f) else str(round(f, 6)).rstrip('0').rstrip('.')
                except (TypeError, ValueError):
                    return str(v)

            changed = []
            checks = [
                ("vendor_name",    body.vendor_name,    old.get("vendor_name"),    "vendor name"),
                ("vendor_gstn",    body.vendor_gstn,    old.get("vendor_gstn"),    "vendor GST"),
                ("invoice_date",   body.invoice_date,   old.get("invoice_date"),   "bill date"),
                ("due_date",       body.due_date,       old.get("due_date"),       "due date"),
                ("subtotal",       body.subtotal,       old.get("subtotal"),       "subtotal"),
                ("tax_amount",     body.tax_amount,     old.get("tax_amount"),     "tax"),
                ("total_amount",   body.total_amount,   old.get("total_amount"),   "total"),
                ("discount_amount",body.discount_amount,old.get("discount_amount"),"discount"),
                ("category",       body.category,       old.get("category"),       "category"),
            ]
            for _, new_val, old_val, label in checks:
                if new_val is None:
                    continue  # field not sent — not being changed
                new_s = _fmt(new_val)
                old_s = _fmt(old_val)
                if new_s != old_s:
                    changed.append(f"{label}: {old_s} → {new_s}")

            # Audit line item changes. Adds and removals are recorded as their own
            # entries — a bill whose total moved because a line was added reads very
            # differently in an approval history than one where a rate was corrected.
            for n in added_lines:
                cur.execute(
                    "SELECT product_name, line_total FROM stg_bill_line_items WHERE stg_bill_id=%s AND line_number=%s",
                    (str(stg_bill_id), n),
                )
                row = cur.fetchone()
                if row:
                    changed.append(f"line {n} added: {row[0] or '(no description)'} — {_fmt(row[1])}")
            for n in removed_lines:
                old_row = old_li_map.get(n)
                label = (old_row[1] if old_row else None) or "(no description)"
                amount = _fmt(old_row[6]) if old_row else "?"
                changed.append(f"line {n} removed: {label} — {amount}")

            if body.line_items and old_li_map:
                for li in body.line_items:
                    old_row = old_li_map.get(li.line_number)
                    if not old_row:
                        continue
                    _, old_name, old_hsn, old_qty, old_price, old_tax, old_total = old_row
                    li_checks = [
                        (li.product_name,      old_name,  f"line {li.line_number} description"),
                        (li.hsn_code,          old_hsn,   f"line {li.line_number} HSN"),
                        (li.quantity,          old_qty,   f"line {li.line_number} qty"),
                        (li.unit_price,        old_price, f"line {li.line_number} rate"),
                        (li.tax_rate_percent,  old_tax,   f"line {li.line_number} tax%"),
                        (li.line_total,        old_total, f"line {li.line_number} total"),
                    ]
                    for new_val, old_val, label in li_checks:
                        if new_val is None:
                            continue
                        new_s = _fmt(new_val)
                        old_s = _fmt(old_val)
                        if new_s != old_s:
                            changed.append(f"{label}: {old_s} → {new_s}")

            if changed:
                cur.execute(
                    """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
                       VALUES (%s,%s,%s,%s,%s,'edited',%s)""",
                    (invoice_id, bill_tenant_id, 0, body.actor_role, body.actor_name, "; ".join(changed)),
                )

        conn.commit()
        return {"status": "updated"}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

def _settled(payment_status, amount_paid, outstanding, total):
    """Paid and outstanding as a pair that adds up. A bill marked fully paid
    without an amount used to keep amount_paid at 0 next to outstanding 0,
    so it read as nothing paid and nothing owed. Report the total as paid."""
    paid = float(amount_paid) if amount_paid is not None else None
    out = float(outstanding) if outstanding is not None else None
    if (payment_status or "").lower() == "paid" and total is not None and not paid:
        return float(total), 0.0
    return paid, out


class PaymentUpdateRequest(BaseModel):
    payment_status: str          # unpaid | partial | paid
    amount_paid: float | None = None
    actor_name: str | None = None
    actor_role: str | None = None

@router.post("/api/bills/{invoice_id}/payment")
def update_payment(invoice_id: str, body: PaymentUpdateRequest):
    tenant = require_tenant()
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tenant_id, total_amount FROM fact_invoices WHERE invoice_id=%s AND tenant_id=%s",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")
            tenant_id, total_amount = row

            outstanding = None
            if body.payment_status == 'paid':
                outstanding = 0.0
            elif body.amount_paid is not None and total_amount is not None:
                outstanding = max(0.0, float(total_amount) - body.amount_paid)
            elif body.payment_status == 'unpaid':
                outstanding = float(total_amount) if total_amount is not None else None

            # Paid in full with no amount given means the whole bill was paid;
            # unpaid means nothing was. Otherwise paid and outstanding disagree.
            amount_paid = body.amount_paid
            if amount_paid is None and body.payment_status == 'paid' and total_amount is not None:
                amount_paid = float(total_amount)
            elif amount_paid is None and body.payment_status == 'unpaid':
                amount_paid = 0.0

            sets = ["payment_status=%s"]
            vals: list = [body.payment_status]
            if amount_paid is not None:
                sets += ["amount_paid=%s"]; vals.append(amount_paid)
            if outstanding is not None:
                sets += ["outstanding_amount=%s"]; vals.append(outstanding)
            vals.extend([invoice_id, tenant])
            cur.execute(f"UPDATE fact_invoices SET {', '.join(sets)} WHERE invoice_id=%s AND tenant_id=%s", vals)

            # Audit log in bill_approval_chain (step=99 reserved for payment events)
            action = f"payment_{body.payment_status}"  # payment_paid | payment_partial | payment_unpaid
            comment = None
            if body.payment_status == 'partial' and body.amount_paid is not None:
                comment = f"Partial payment of {format_money(body.amount_paid, get_jurisdiction(tenant).base_currency, decimals=0)} recorded"
            elif body.payment_status == 'paid':
                comment = "Marked as fully paid"
            elif body.payment_status == 'unpaid':
                comment = "Marked as unpaid"
            cur.execute(
                """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (invoice_id, tenant_id, 99, body.actor_role, body.actor_name, action, comment),
            )
        conn.commit()
        return {"status": body.payment_status, "outstanding_amount": outstanding}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

@router.patch("/api/bills/{invoice_id}/soft-status")
def set_bill_soft_status(
    invoice_id: str,
    status: str = Query(..., regex="^(active|deleted|on_hold)$"),
    company_id: str = Query(default=None),
):
    """Admin: soft-delete or put a bill on hold. status must be active|deleted|on_hold.

    Moving a bill to the hold/deleted collection releases its staging link
    (stg_bill_id → archived_stg_bill_id, live value NULL) so the partial unique
    index (tenant_id, stg_bill_id) frees up and the same bill can be re-uploaded.
    Restoring re-links the original stg_bill_id only if no active bill has taken
    that slot in the meantime (e.g. via a re-upload).
    """
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if status == "active":
                # Restore: re-link the archived staging row only if its slot is free.
                cur.execute(
                    """UPDATE fact_invoices fi SET
                           soft_status = 'active',
                           stg_bill_id = CASE
                               WHEN fi.stg_bill_id IS NULL
                                    AND fi.archived_stg_bill_id IS NOT NULL
                                    AND NOT EXISTS (
                                        SELECT 1 FROM fact_invoices x
                                        WHERE x.tenant_id = fi.tenant_id
                                          AND x.stg_bill_id = fi.archived_stg_bill_id
                                          AND x.invoice_id <> fi.invoice_id)
                               THEN fi.archived_stg_bill_id
                               ELSE fi.stg_bill_id
                           END,
                           archived_stg_bill_id = NULL
                       WHERE fi.invoice_id = %s::uuid AND fi.tenant_id = %s""",
                    (invoice_id, tenant),
                )
            else:
                # Archive (on_hold / deleted): release the staging slot.
                cur.execute(
                    """UPDATE fact_invoices SET
                           soft_status = %s,
                           archived_stg_bill_id = COALESCE(archived_stg_bill_id, stg_bill_id),
                           stg_bill_id = NULL
                       WHERE invoice_id = %s::uuid AND tenant_id = %s
                       RETURNING supplier_id, invoice_number, total_amount""",
                    (status, invoice_id, tenant),
                )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Bill not found")
            if status == "deleted":
                # Bills record no actor for a delete; the vendor's Activity log does.
                from ..core.vendor_activity_log import log_vendor_activity
                supplier_id, bill_no, total = cur.fetchone()
                if supplier_id:
                    log_vendor_activity(
                        cur, supplier_id, "bill_deleted",
                        f"Deleted bill {bill_no}" if bill_no else "Deleted a bill",
                        meta={"invoice_id": invoice_id, "bill_no": bill_no,
                              "amount": float(total) if total is not None else None},
                        tenant_id=tenant)
        conn.commit()
        return {"ok": True, "soft_status": status}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)
