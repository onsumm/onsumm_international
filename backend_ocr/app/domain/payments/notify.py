"""Telling the vendor their money is on the way — the Payment Hub's one call
into the Communication Agent.

A successful disbursement (app/routers/disbursement.py) is an *event*, not a
user asking for an email, so this is the event-triggered sibling of the Review
Hub's `notify` action (app/domain/review/notify_actions.py) and follows the
same three rules that module set:

  1. **Reuse the send path that already owns the operation.** Mail to a vendor
     contact goes through `email_connection.send_vendor_contact_email`, so a
     receipt lands in `vendor_outreach_log`, in the POC store, and in the
     thread state exactly like every other message we send a vendor — the
     vendor's reply comes back into the same conversation instead of
     disappearing into a no-reply void.
  2. **Wrap it in an `agent_runs` row.** Every agent's work answers "who did
     what, to whom, and did it land" from the one table that exists for it.
  3. **Be honest when it cannot do the thing.** No POC with an email, no
     mailbox and no SMTP credentials — each is a real outcome a human needs to
     see, recorded as SKIPPED with the reason on the disbursement, never
     faked into a success.

Two rules this module adds of its own, because it spends real money's
reputation:

  * **A receipt is never sent twice.** `fact_disbursements.receipt_email_status`
    is claimed with a conditional UPDATE before anything is rendered, so two
    concurrent calls — a retried request, a status refresh racing the OTP
    verify — cannot both get through. A failed attempt is left as `failed`
    with the error rather than silently re-queued: a vendor getting one receipt
    late is a support ticket, getting three is a credibility problem.
  * **It never runs on the request path.** The bank call is already the slow
    part of verifying an OTP; rendering a PDF and waiting on Gmail behind it
    would hold the payer's response open, and a mail failure must never turn a
    payment that *did* go through into a 500. `enqueue_payment_receipt` hands
    the work to a small thread pool and returns immediately.
"""
from __future__ import annotations

import json
import smtplib
import traceback
from concurrent.futures import ThreadPoolExecutor

from ...config import (SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER,
                       get_conn, put_conn)
from . import demo
from . import receipt as rc

# Bounded, and separate from fact_graph's enrichment pool on purpose: a receipt
# must not queue behind a backlog of embedding calls.
_EXECUTOR: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="payment-receipt")
    return _EXECUTOR


def enqueue_payment_receipt(tenant_id: str, disbursement_ids: list[str],
                            actor_username: str | None) -> None:
    """Fire-and-forget the receipt for one or more settled disbursements.

    Callers pass their *already-resolved* tenant and actor rather than letting
    this re-read them: by the time the pool picks the job up the request's
    auth context is long gone, so `require_tenant()` / `current_username()`
    would raise or, worse, read another request's.
    """
    ids = [str(i) for i in (disbursement_ids or []) if i]
    if not ids:
        return

    def _run():
        try:
            send_payment_receipt(tenant_id, ids, actor_username)
        except Exception:
            # Nothing above us to report to — the payment is already committed
            # and answered. The disbursement row carries the failure.
            traceback.print_exc()

    try:
        _executor().submit(_run)
    except Exception:
        traceback.print_exc()


# ── The work ─────────────────────────────────────────────────────────────────

def send_payment_receipt(tenant_id: str, disbursement_ids: list[str],
                         actor_username: str | None) -> list[dict]:
    """Claim, build and send. One email per vendor, covering every bill the
    caller settled for that vendor in this batch — a bulk payment of four
    bills is one receipt listing four, not four emails.

    Returns one result dict per vendor, for tests and for a caller that wants
    to run it synchronously. Never raises for a delivery problem; only a bug
    (or a dead database) gets out of here.
    """
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=_dict_cursor())
        claimed = _claim(cur, conn, tenant_id, disbursement_ids)
        if not claimed:
            return []

        # One receipt per vendor. A bulk batch is already single-vendor, but
        # grouping rather than assuming it means a caller that mixes vendors
        # gets correct mail instead of one vendor's bills on another's receipt.
        by_supplier: dict[str, list[dict]] = {}
        orphans: list[dict] = []
        for row in claimed:
            supplier_id = str(row["supplier_id"]) if row["supplier_id"] else ""
            if supplier_id:
                by_supplier.setdefault(supplier_id, []).append(row)
            else:
                orphans.append(row)

        results = []
        for row in orphans:
            # A disbursement with no supplier_id has no contact list to reach:
            # the money moved against beneficiary details alone.
            _finish(cur, conn, [row], "no_vendor")
            results.append({"status": "skipped", "reason": "no_vendor",
                            "disbursement_ids": [str(row["disbursement_id"])]})

        for supplier_id, rows in by_supplier.items():
            try:
                results.append(_send_for_vendor(cur, conn, tenant_id, supplier_id,
                                                rows, actor_username))
            except Exception as exc:
                traceback.print_exc()
                _safe_rollback(conn)
                _finish(cur, conn, rows, "failed", error=str(exc))
                results.append({"status": "failed", "supplier_id": supplier_id,
                                "error": str(exc)})
        return results
    finally:
        put_conn(conn)


def _dict_cursor():
    from psycopg2.extras import RealDictCursor
    return RealDictCursor


def _safe_rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        pass


def _claim(cur, conn, tenant_id: str, disbursement_ids: list[str]) -> list[dict]:
    """Take ownership of the rows this call will email for, atomically.

    `receipt_email_status IS NULL` is the whole guard: the UPDATE locks each
    row it matches, so of two callers racing on the same disbursement exactly
    one gets it back and the other gets an empty list and stops. Only SUCCESS
    rows are eligible — a PENDING or FAILED payment has no receipt to send,
    and leaving its status NULL means the row is still eligible later if it
    does settle.
    """
    cur.execute(
        """
        UPDATE fact_disbursements
           SET receipt_email_status = 'SENDING'
         WHERE tenant_id = %s
           AND disbursement_id::text = ANY(%s)
           AND status = 'SUCCESS'
           AND receipt_email_status IS NULL
        RETURNING disbursement_id, invoice_id, supplier_id, amount, beneficiary_name,
                  beneficiary_account, beneficiary_ifsc, bank_rrn, tran_ref_no,
                  otp_verified_at, updated_at, initiated_by
        """,
        (tenant_id, [str(i) for i in disbursement_ids]),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    return rows


def _finish(cur, conn, rows: list[dict], status: str, error: str | None = None) -> None:
    """Record how it went on the disbursements themselves, so the Payment Hub
    can show it and a human can tell "we told the vendor" from "we meant to"."""
    ids = [str(r["disbursement_id"]) for r in rows]
    try:
        cur.execute(
            """
            UPDATE fact_disbursements
               SET receipt_email_status = %s,
                   receipt_emailed_at   = CASE WHEN %s = 'sent' THEN now() ELSE receipt_emailed_at END,
                   receipt_email_error  = %s
             WHERE disbursement_id::text = ANY(%s)
            """,
            (status, status, str(error)[:500] if error else None, ids),
        )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        traceback.print_exc()


def _send_for_vendor(cur, conn, tenant_id: str, supplier_id: str, rows: list[dict],
                     actor_username: str | None) -> dict:
    if demo.is_demo(tenant_id):
        # Demo payments: the receipt goes to the one POC the tenant chose, never
        # the vendor's real contacts. No POC set means nobody is mailed.
        poc = demo.poc_email(cur, tenant_id)
        contact = {"id": None, "name": None, "email": poc} if poc else None
    else:
        contact = _primary_contact(cur, tenant_id, supplier_id)
    if not contact:
        _finish(cur, conn, rows, "no_contact")
        return {"status": "skipped", "reason": "no_contact", "supplier_id": supplier_id,
                "disbursement_ids": [str(r["disbursement_id"]) for r in rows]}

    doc = _build_receipt(cur, tenant_id, supplier_id, rows, contact)
    run_id = _open_run(cur, conn, tenant_id, supplier_id, rows, actor_username)

    try:
        sent = _deliver(cur, conn, tenant_id=tenant_id, supplier_id=supplier_id,
                        contact=contact, doc=doc, actor_username=actor_username)
    except Exception as exc:
        _close_run(cur, conn, run_id, "FAILED", {"error": str(exc)[:500]}, error=str(exc))
        _finish(cur, conn, rows, "failed", error=str(exc))
        return {"status": "failed", "supplier_id": supplier_id, "error": str(exc)}

    ok = sent.get("status") == "sent"
    _close_run(cur, conn, run_id, "SUCCEEDED" if ok else "SKIPPED", {
        "result": sent.get("status"),
        "via": sent.get("via"),
        "email_to": contact["email"],
        "bills": len(doc.bills),
        "amount": doc.total,
    })
    _finish(cur, conn, rows, "sent" if ok else "manual_required",
            error=None if ok else sent.get("reason"))
    return {**sent, "supplier_id": supplier_id, "run_id": str(run_id),
            "email_to": contact["email"],
            "disbursement_ids": [str(r["disbursement_id"]) for r in rows]}


# ── Who it goes to ───────────────────────────────────────────────────────────

def _primary_contact(cur, tenant_id: str, supplier_id: str) -> dict | None:
    """The vendor's POC: their primary contact, else the oldest one with an
    email — the same ordering the vendor's contact list is shown in
    (routers/vendor_contacts.py). `agent_enabled` is deliberately *not* a
    filter here: that flag picks whose inbox the Communication Agent watches
    for replies, not who is owed a receipt for money they were just paid."""
    cur.execute(
        """
        SELECT id::text AS id, name, email
          FROM supplier_contacts
         WHERE tenant_id = %s AND supplier_id = %s
           AND email IS NOT NULL AND btrim(email) <> ''
         ORDER BY is_primary DESC, created_at ASC
         LIMIT 1
        """,
        (tenant_id, supplier_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ── What it says ─────────────────────────────────────────────────────────────

def _build_receipt(cur, tenant_id: str, supplier_id: str, rows: list[dict],
                   contact: dict) -> rc.Receipt:
    cur.execute("SELECT company_name, timezone FROM dim_tenants WHERE tenant_id = %s",
                (tenant_id,))
    tenant = cur.fetchone() or {}
    cur.execute("SELECT name FROM dim_suppliers WHERE tenant_id = %s AND supplier_id = %s",
                (tenant_id, supplier_id))
    supplier = cur.fetchone() or {}

    cur.execute(
        """
        SELECT invoice_id::text AS invoice_id, invoice_number, invoice_date, outstanding_amount
          FROM fact_invoices
         WHERE tenant_id = %s AND invoice_id::text = ANY(%s)
        """,
        (tenant_id, [str(r["invoice_id"]) for r in rows]),
    )
    bills_by_id = {r["invoice_id"]: dict(r) for r in cur.fetchall()}

    # Bank details are identical across a bulk batch (one beneficiary, one
    # vendor) — the first row speaks for all of them.
    head = rows[0]
    bank_name = _bank_name(cur, tenant_id, supplier_id, head.get("beneficiary_account"))

    bills = []
    for r in sorted(rows, key=lambda r: str(r["invoice_id"])):
        bill = bills_by_id.get(str(r["invoice_id"]), {})
        bills.append(rc.ReceiptBill(
            number=bill.get("invoice_number") or str(r["invoice_id"])[:8],
            amount=float(r["amount"]),
            date=rc.format_day(bill.get("invoice_date")),
            reference=r.get("bank_rrn") or r.get("tran_ref_no"),
            outstanding=float(bill["outstanding_amount"]) if bill.get("outstanding_amount") else None,
        ))

    return rc.Receipt(
        payer_name=tenant.get("company_name") or "Your customer",
        vendor_name=supplier.get("name") or head.get("beneficiary_name") or "Vendor",
        beneficiary_name=head.get("beneficiary_name") or supplier.get("name") or "Vendor",
        beneficiary_account=rc.mask_account(head.get("beneficiary_account") or ""),
        beneficiary_ifsc=head.get("beneficiary_ifsc"),
        beneficiary_bank=bank_name,
        paid_at=rc.format_moment(head.get("otp_verified_at") or head.get("updated_at"),
                                 tenant.get("timezone") or "Asia/Kolkata"),
        contact_name=contact.get("name"),
        bills=tuple(bills),
    )


def _bank_name(cur, tenant_id: str, supplier_id: str, account_number: str | None) -> str | None:
    """The bank behind the account we paid into, when it's on file — nice to
    have on a receipt, never worth failing one over."""
    if not account_number:
        return None
    try:
        cur.execute(
            """SELECT bank_name FROM supplier_bank_accounts
                WHERE tenant_id = %s AND supplier_id = %s AND account_number = %s
                LIMIT 1""",
            (tenant_id, supplier_id, account_number),
        )
        row = cur.fetchone()
        return (row or {}).get("bank_name")
    except Exception:
        return None


# ── How it gets there ────────────────────────────────────────────────────────

def _deliver(cur, conn, *, tenant_id: str, supplier_id: str, contact: dict,
             doc: rc.Receipt, actor_username: str | None) -> dict:
    """The acting user's own mailbox first, the system mailbox as a fallback.

    Preferring the user's mailbox is what makes the receipt a *conversation*:
    it threads, it is logged against the vendor like every other message, and
    a vendor who replies "this is short by ₹400" reaches the person who
    released the payment. But a receipt is transactional mail that has to
    arrive, and the person who verified an OTP may never have connected a
    mailbox — so SMTP, the same credentials the OTP and approval mails already
    go out on, catches that case instead of dropping the receipt.
    """
    subject = rc.subject(doc)
    text = rc.render_text(doc)
    html = rc.render_html(doc)
    pdf = [(rc.file_name(doc), rc.render_pdf(doc), "application/pdf")]

    from ...routers import email_connection

    # A demo payment's receipt (no vendor contact behind it) always goes out on
    # the system mailbox, never from a user's own inbox.
    if actor_username and contact["id"]:
        connection = email_connection._load_connection(cur, tenant_id, actor_username)
        # Provider matters here and nowhere else in the send path: only Gmail
        # can actually send today (email_oauth.send_email is the Gmail API;
        # integrations/email_graph.py does OAuth for Outlook but no send), so a
        # user whose linked mailbox is Microsoft would produce a failed receipt
        # rather than a sent one. Falling through to SMTP delivers it instead.
        # Drop this check the day Graph sending lands.
        if (connection and connection["status"] == "active"
                and (connection.get("provider") or "google") == "google"):
            sent = email_connection.send_vendor_contact_email(
                cur, conn, tenant_id=tenant_id, supplier_id=supplier_id,
                contact_id=contact["id"], username=actor_username,
                subject=subject, message=text, html=html, attachments=pdf,
            )
            return {"status": "sent", "via": "mailbox", "from": sent.get("from"),
                    "thread_id": sent.get("thread_id")}

    if not (SMTP_USER and SMTP_PASSWORD):
        return {"status": "manual_required", "via": None,
                "reason": ("Nobody's mailbox is connected and no system mail is "
                           "configured — send this receipt to the vendor yourself.")}

    return _send_via_smtp(cur, conn, tenant_id=tenant_id, supplier_id=supplier_id,
                          to_email=contact["email"], subject=subject, text=text,
                          html=html, attachments=pdf, actor_username=actor_username)


def _send_via_smtp(cur, conn, *, tenant_id: str, supplier_id: str, to_email: str,
                   subject: str, text: str, html: str,
                   attachments: list[tuple[str, bytes, str]],
                   actor_username: str | None) -> dict:
    """The system-mailbox path, logged to `vendor_outreach_log` like any other
    vendor mail so the vendor's history is complete either way.

    It stops there: `poc_email_messages` and `comm_thread_state` are keyed on a
    provider thread id, which SMTP does not give us, so writing half a row
    there would put a thread in the Communication Agent's queue that it can
    never follow up on.
    """
    message = _email_oauth().build_message(SMTP_USER, to_email, subject, text,
                                           attachments=attachments, html=html)
    status, error = "sent", None
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, [to_email], message.as_string())
    except Exception as exc:
        status, error = "failed", str(exc)
        print(f"[payment-receipt] SMTP send failed: {error}")

    try:
        cur.execute(
            """INSERT INTO vendor_outreach_log
                   (supplier_id, tenant_id, requested_by, email_to, subject, body, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (supplier_id, tenant_id, actor_username or "system", to_email, subject, text, status),
        )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        traceback.print_exc()

    if status == "failed":
        raise RuntimeError(error or "SMTP rejected the receipt")
    return {"status": "sent", "via": "smtp", "from": SMTP_USER}


def _email_oauth():
    from ...integrations import email_oauth
    return email_oauth


# ── Audit ────────────────────────────────────────────────────────────────────

def _open_run(cur, conn, tenant_id: str, supplier_id: str, rows: list[dict],
              actor_username: str | None) -> str:
    """`trigger_type='event'`: a settled payment asked for this, not a person —
    the one thing that distinguishes this run from the Review Hub's
    user-triggered `notify` in the same table."""
    cur.execute(
        """
        INSERT INTO agent_runs
            (tenant_id, agent_name, trigger_type, trigger_ref, subject_type, subject_id, requested_by)
        VALUES (%s, 'communication', 'event', %s, 'vendor', %s, %s)
        RETURNING run_id
        """,
        (tenant_id, str(rows[0]["disbursement_id"]), supplier_id,
         actor_username or "system"),
    )
    run_id = cur.fetchone()["run_id"]
    conn.commit()
    return run_id


def _close_run(cur, conn, run_id: str, status: str, outcome: dict,
               error: str | None = None) -> None:
    try:
        cur.execute(
            """UPDATE agent_runs
                  SET status = %s, outcome = %s::jsonb, error = %s, finished_at = now()
                WHERE run_id = %s""",
            (status, json.dumps(outcome, default=str), str(error)[:500] if error else None, run_id),
        )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        traceback.print_exc()
