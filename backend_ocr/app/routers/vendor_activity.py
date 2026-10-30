"""A vendor's Activity timeline: GET /api/vendors/{supplier_id}/activity.

Pulls every place that records something about a vendor — bills, approvals,
payments, verification results, documents, contacts, emails, TDS changes,
Review edits and vendor_activity_log — and merges them newest first.

Each source runs in its own savepoint. Several of these tables are created
outside this repo (wf_audit_log, vendor_tds_config_log, rh_audit_log …) and may
be missing in a given database; a source that fails is rolled back to its
savepoint and skipped, the response says `partial: true`, and the rest still
renders. The row → event wording lives in domain/vendor_activity.py.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query
from psycopg2.extras import RealDictCursor

from ..config import get_conn, put_conn
from ..domain import vendor_activity as va
from ..security.auth import require_tenant

log = logging.getLogger(__name__)

router = APIRouter()

# Sources whose rows carry several timestamps (a payment's start, outcome and
# receipt; a document's upload and delete) are fetched with a wider window so
# a late second event on an old row is not cut off by the row limit.
_MULTI_CAP = 1000

_NO_CUTOFF = "infinity"


def _rows(cur, sql: str, params: tuple) -> list[dict]:
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ── Source queries ───────────────────────────────────────────────────────────
# Each takes (cur, tenant, supplier_id, before, cap) and returns rows for the
# matching normaliser. `before` is an ISO string or 'infinity'.

def _q_bills(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT fi.invoice_id::text, fi.invoice_number, fi.total_amount, fi.currency_code,
               fi.inr_amount_current, fi.soft_status, fi.created_at,
               u.uploaded_by_name, u.uploaded_by_role
          FROM fact_invoices fi
          LEFT JOIN raw_invoice_uploads u ON u.upload_id = fi.upload_id
         WHERE fi.tenant_id = %s AND fi.supplier_id = %s AND fi.created_at < %s::timestamptz
         ORDER BY fi.created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_approvals(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT bac.id, bac.invoice_id::text, bac.step, bac.actor_role, bac.actor_name,
               bac.action, bac.comment, bac.created_at,
               fi.invoice_number, fi.total_amount, fi.currency_code, fi.inr_amount_current
          FROM bill_approval_chain bac
          JOIN fact_invoices fi ON fi.invoice_id = bac.invoice_id AND fi.tenant_id = bac.tenant_id
         WHERE fi.tenant_id = %s AND fi.supplier_id = %s AND bac.created_at < %s::timestamptz
         ORDER BY bac.created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_disbursements(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT d.disbursement_id::text, d.invoice_id::text, d.amount, d.status, d.status_message,
               d.beneficiary_account, d.initiated_by, d.updated_by,
               d.created_at, d.updated_at, d.receipt_emailed_at, d.rail, d.currency_code,
               fi.invoice_number
          FROM fact_disbursements d
          LEFT JOIN fact_invoices fi ON fi.invoice_id = d.invoice_id
         WHERE d.tenant_id = %s AND d.supplier_id = %s AND d.created_at < %s::timestamptz
         ORDER BY d.created_at DESC LIMIT %s""", (tenant, sid, before, _MULTI_CAP))


def _q_overrides(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT o.override_id::text, o.invoice_id::text, o.overridden_by, o.overridden_by_role,
               o.reason, o.original_account_number, o.override_account_number, o.created_at,
               fi.invoice_number
          FROM disbursement_overrides o
          LEFT JOIN fact_invoices fi ON fi.invoice_id = o.invoice_id
         WHERE o.tenant_id = %s AND o.supplier_id = %s AND o.created_at < %s::timestamptz
         ORDER BY o.created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_wf_audit(cur, tenant, sid, before, cap):
    # The verification pipelines retry, and a vendor stuck on the same answer
    # can collect thousands of identical rows. Keep only the rows where the
    # outcome changed from the previous one in the same stream (the GST check,
    # or one bank account), computed over the whole history so paging is stable.
    return _rows(cur, """
        WITH w AS (
            SELECT a.log_id::text, a.table_name, a.entity_id, a.new_values_jsonb, a.created_at,
                   b.account_number,
                   CASE WHEN a.table_name = 'supplier_bank_accounts' THEN 'bank:' || a.entity_id::text
                        WHEN a.new_values_jsonb ? 'bank_verified' THEN 'bank:legacy'
                        ELSE 'gst' END AS stream,
                   concat_ws('|', a.new_values_jsonb->>'status', a.new_values_jsonb->>'bank_verified',
                             a.new_values_jsonb->>'verified', a.new_values_jsonb->>'error',
                             a.new_values_jsonb->>'fail_reason', a.new_values_jsonb->>'reason') AS sig
              FROM wf_audit_log a
              LEFT JOIN supplier_bank_accounts b
                     ON a.table_name = 'supplier_bank_accounts' AND b.id = a.entity_id
             WHERE a.tenant_id = %s
               AND ((a.table_name = 'dim_suppliers' AND a.entity_id = %s::uuid)
                 OR (a.table_name = 'supplier_bank_accounts' AND a.entity_id IN (
                        SELECT id FROM supplier_bank_accounts WHERE tenant_id = %s AND supplier_id = %s)))
        ), changed AS (
            SELECT w.*, lag(sig) OVER (PARTITION BY stream ORDER BY created_at, log_id) AS prev_sig
              FROM w
        )
        SELECT log_id, table_name, new_values_jsonb, created_at, account_number
          FROM changed
         WHERE prev_sig IS DISTINCT FROM sig AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, tenant, sid, before, cap))


def _q_documents(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, file_name, doc_type, doc_label, uploaded_by, uploaded_at, deleted_at, deleted_by
          FROM vendor_documents
         WHERE tenant_id = %s AND supplier_id = %s AND uploaded_at < %s::timestamptz
         ORDER BY uploaded_at DESC LIMIT %s""", (tenant, sid, before, _MULTI_CAP))


def _q_doc_edits(cur, tenant, sid, before, cap):
    # vendor_doc_edits is created lazily on a document's first edit, so a
    # database without it simply has no edits — not a failed source.
    cur.execute("SELECT to_regclass('vendor_doc_edits') IS NOT NULL AS present")
    if not cur.fetchone()["present"]:
        return []
    return _rows(cur, """
        SELECT e.id::text, e.edited_by, e.edited_at, e.diff_json, d.file_name, d.doc_type, d.doc_label
          FROM vendor_doc_edits e
          JOIN vendor_documents d ON d.id = e.doc_id AND d.tenant_id = e.tenant_id
         WHERE e.tenant_id = %s AND d.supplier_id = %s AND e.edited_at < %s::timestamptz
         ORDER BY e.edited_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_contacts(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, name, role, email, created_at
          FROM supplier_contacts
         WHERE tenant_id = %s AND supplier_id = %s AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_gstins(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, gstin, status, error_message, verified_at, created_at
          FROM supplier_gstins
         WHERE tenant_id = %s AND supplier_id = %s AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, _MULTI_CAP))


def _q_bank_accounts(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, account_number, bank_name, created_at
          FROM supplier_bank_accounts
         WHERE tenant_id = %s AND supplier_id = %s AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_outreach(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, requested_by, requested_at, sent_at, sent_by, email_to, subject, status
          FROM vendor_outreach_log
         WHERE tenant_id = %s AND supplier_id = %s
           AND COALESCE(sent_at, requested_at) < %s::timestamptz
         ORDER BY COALESCE(sent_at, requested_at) DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_tds(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT l.log_id::text, l.field_changed, l.old_value, l.new_value, l.reason, l.changed_at
          FROM vendor_tds_config_log l
          JOIN dim_vendor_tds_overrides o ON o.override_id = l.override_id
         WHERE l.tenant_id = %s AND o.supplier_id = %s AND l.changed_at < %s::timestamptz
         ORDER BY l.changed_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_review(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT audit_id, actor, actor_role, method, path, changes, status_code, created_at
          FROM rh_audit_log
         WHERE tenant_id = %s AND entity_type = 'vendor' AND entity_id = %s
           AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_agent_drafts(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, from_email, subject, status, created_at
          FROM agent_draft_bills
         WHERE tenant_id = %s AND supplier_id = %s AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_activity_log(cur, tenant, sid, before, cap):
    return _rows(cur, """
        SELECT id::text, actor, actor_role, action, title, changes, meta, created_at
          FROM vendor_activity_log
         WHERE tenant_id = %s AND supplier_id = %s AND created_at < %s::timestamptz
         ORDER BY created_at DESC LIMIT %s""", (tenant, sid, before, cap))


def _q_log_started(cur, tenant) -> Any:
    """When this tenant's Activity log began — older Review / re-verify rows
    are shown from their own tables, newer ones come from the log."""
    cur.execute("SELECT min(created_at) AS started FROM vendor_activity_log WHERE tenant_id = %s", (tenant,))
    row = cur.fetchone()
    return row["started"] if row else None


# name -> (query, normaliser, normaliser needs log_started_at)
SOURCES: dict[str, tuple[Callable, Callable, bool]] = {
    "bills": (_q_bills, va.bills_to_events, False),
    "approvals": (_q_approvals, va.approvals_to_events, False),
    "disbursements": (_q_disbursements, va.disbursements_to_events, False),
    "overrides": (_q_overrides, va.overrides_to_events, False),
    "verifications": (_q_wf_audit, va.wf_audit_to_events, True),
    "documents": (_q_documents, va.documents_to_events, False),
    "document_edits": (_q_doc_edits, va.doc_edits_to_events, False),
    "contacts": (_q_contacts, va.contacts_to_events, False),
    "gstins": (_q_gstins, va.gstins_to_events, False),
    "bank_accounts": (_q_bank_accounts, va.bank_accounts_to_events, False),
    "outreach": (_q_outreach, va.outreach_to_events, False),
    "tds": (_q_tds, va.tds_to_events, False),
    "review": (_q_review, va.review_to_events, True),
    "agent_drafts": (_q_agent_drafts, va.agent_drafts_to_events, False),
    "activity_log": (_q_activity_log, va.activity_log_to_events, False),
}


def _in_savepoint(cur, name: str, fn: Callable, *args):
    """Run fn in its own savepoint; on error roll back to it and return the exception."""
    # Not released on success: the whole read is rolled back at the end, and
    # skipping RELEASE saves a round trip per source.
    sp = f"va_{name}"
    cur.execute(f"SAVEPOINT {sp}")
    try:
        return fn(cur, *args)
    except Exception as exc:  # missing table / changed column / timeout
        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        return exc


def collect_activity(cur, tenant: str, supplier_id: str, limit: int, before: str | None) -> dict:
    """Run every source, normalise, merge. Separate from the route for testing."""
    limit = va.clamp_limit(limit)
    cutoff = va.iso(va.parse_ts(before)) if before else _NO_CUTOFF
    cap = limit + 1

    started = _in_savepoint(cur, "log_started", _q_log_started, tenant)
    if isinstance(started, BaseException):
        started = None  # the activity_log source reports the failure itself

    results: dict[str, Any] = {}
    for name, (query, normalise, wants_start) in SOURCES.items():
        rows = _in_savepoint(cur, name, query, tenant, supplier_id, cutoff, cap)
        if isinstance(rows, BaseException):
            results[name] = rows
            continue
        try:
            results[name] = normalise(rows, started) if wants_start else normalise(rows)
        except Exception as exc:  # a row shape we did not expect
            log.warning("[vendor_activity] normalising %s failed", name, exc_info=True)
            results[name] = exc
    return va.merge_events(results, limit=limit, before=before)


@router.get("/api/vendors/{supplier_id}/activity")
def vendor_activity(supplier_id: str, limit: int = Query(default=va.DEFAULT_LIMIT),
                    before: str | None = Query(default=None)):
    """Everything that happened to a vendor, newest first.

    `before` is an exclusive ISO cursor — pass the previous page's
    `next_before`. `limit` is capped at 200."""
    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    try:
        _uuid.UUID(supplier_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if before and va.parse_ts(before) is None:
        raise HTTPException(status_code=422, detail="before must be an ISO timestamp")

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET LOCAL statement_timeout = '10s'")
            cur.execute("SELECT 1 FROM dim_suppliers WHERE supplier_id = %s AND tenant_id = %s",
                        (supplier_id, tenant))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Vendor not found")
            result = collect_activity(cur, tenant, supplier_id, limit, before)
        conn.rollback()  # read-only; release the snapshot
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        log.exception("[vendor_activity] failed for %s", supplier_id)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)
