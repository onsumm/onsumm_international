"""Moving a bill through the approval chain.

Split out of routers/bills.py: reading and editing a bill is one concern,
advancing it through the configured chain — and the email round-trip that
lets an approver act without signing in — is another. This is also the
security-sensitive half, so it is worth reading on its own.
"""
from __future__ import annotations

from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Query, Request, Depends
from pydantic import BaseModel
from typing import Any, List, Optional
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn, ensure_column
from ..security.auth import auth_context, require_tenant, current_tenant, current_role, current_username, require_role
# The chain helpers and the shared bulk/audit constants live where the rest of
# the app reads them from; this module was split out of routers/bills.py and
# left referencing them as if they were still local, so every approve, send-back,
# submit, reconcile and bulk action raised NameError at the first chain lookup.
from ..core.approval_chain import (
    _build_status_name_map, _build_status_role_map, _chain_statuses,
    _get_next_role_for_status, _get_status_label, _load_chain, _next_status_from_chain,
)
from ..core.notifications import _notif_cache
from .bills import BULK_LIMIT, RECON_AUDIT_STEP, _BULK_ALLOWED_ROLES

from ..core.jurisdiction import get_jurisdiction
from ..core.money import format_money
from fastapi import APIRouter

router = APIRouter()

@router.get("/api/bills/approve-link")
def approve_via_link(token: str):
    from fastapi.responses import HTMLResponse

    data = _verify_action_token(token)
    if not data:
        return HTMLResponse("<h2>Link expired or invalid.</h2>", status_code=400)

    invoice_id = data["invoice_id"]
    action     = data["action"]
    role       = data["role"]

    role_names = {
        "approver": "Approver",
        "accountant": "Accountant",
        "finance_controller": "Finance Controller",
    }
    actor_name = role_names.get(role, role.title())

    # ── Phase 1: read-only fetch to validate (short-lived connection) ──────────
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status, tenant_id FROM fact_invoices WHERE invoice_id=%s", (invoice_id,))
            row = cur.fetchone()
    finally:
        put_conn(conn)

    if not row:
        return HTMLResponse("<h2>Bill not found.</h2>", status_code=404)
    current, bill_tenant_id = row

    # Load department for this bill to get the right chain
    conn2 = get_conn()
    try:
        with conn2.cursor() as cur2:
            cur2.execute("SELECT department FROM fact_invoices WHERE invoice_id=%s", (invoice_id,))
            dept_row = cur2.fetchone()
    finally:
        put_conn(conn2)
    bill_department = dept_row[0] if dept_row else None

    chain = _load_chain(str(bill_tenant_id), bill_department)
    statuses = _chain_statuses(chain)
    # Locked steps (e.g. Submitted) occupy the first stepper positions; approval
    # steps come after. The step index MUST include this offset so it matches the
    # /action endpoint and the frontend stepper (which number all steps, locked
    # included). Without it, email-link approvals landed on the wrong node.
    locked_count = sum(1 for s in chain if s.get("locked"))

    if action == "approve":
        if current not in statuses:
            return HTMLResponse(f"<h2>Nothing to approve — bill is already {current}.</h2>")
        allowed = _build_status_role_map(str(bill_tenant_id), bill_department).get(current, set())
        if role not in allowed:
            return HTMLResponse(f"<h2>Your role '{role}' is not allowed to approve at this step.</h2>", status_code=403)
        new_status = _next_status_from_chain(chain, current)
        step = locked_count + statuses.index(current) + 1
    elif action == "send_back":
        if current not in statuses:
            return HTMLResponse(f"<h2>Cannot send back — bill is {current}.</h2>")
        allowed = _build_status_role_map(str(bill_tenant_id), bill_department).get(current, set())
        if role not in allowed:
            return HTMLResponse(f"<h2>Your role '{role}' is not allowed to send back at this step.</h2>", status_code=403)
        idx = statuses.index(current)
        new_status = statuses[idx - 1] if idx > 0 else "NEEDS_REVISION"
        step = locked_count + idx + 1
    else:
        return HTMLResponse("<h2>Unknown action.</h2>", status_code=400)

    # Prefer the specific person assigned to this step over the generic role label
    # so the activity log records who actually acted. The email link is shared
    # across everyone with the role, so we can only attribute it confidently when
    # the step has exactly one assignee; otherwise fall back to the role label.
    assignees = _build_status_name_map(str(bill_tenant_id), bill_department).get(current) or []
    if len(assignees) == 1:
        actor_name = assignees[0]

    # ── Phase 2: write (connection held only for the actual UPDATE) ──────────
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE fact_invoices SET status=%s, source_updated_at=now() WHERE invoice_id=%s",
                        (new_status, invoice_id))
            cur.execute("""INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (invoice_id, bill_tenant_id, step, role, actor_name, action, "Via email link"))
        conn.commit()
        put_conn(conn)
    except Exception as e:
        conn.rollback()
        put_conn(conn, close=True)
        return HTMLResponse(f"<h2>Error: {e}</h2>", status_code=500)

    bill = _fetch_bill_details(invoice_id)
    status_label = new_status.replace("_", " ").title()
    action_label = "Approved" if action == "approve" else "Sent Back for Revision"
    import threading
    threading.Thread(
        target=_send_approval_email,
        args=(invoice_id, action, actor_name, role, new_status, "Via email link",
              str(bill_tenant_id), bill_department),
        daemon=True,
    ).start()
    return HTMLResponse(f"""
    <html><body style="font-family:Arial,sans-serif;max-width:500px;margin:60px auto;text-align:center;">
      <h2 style="color:#1a56db;">Onsumm</h2>
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:32px;">
        <h3 style="color:#16a34a;">✓ {action_label}</h3>
        <p><b>{bill['vendor_name']}</b> — {bill['invoice_number']}</p>
        <p>New status: <b>{status_label}</b></p>
      </div>
      <p style="color:#888;font-size:13px;margin-top:24px;">You can close this tab.</p>
    </body></html>
    """)

def _get_emails_for_role(tenant_id: str, role: str, department: str | None) -> list[str]:
    """Look up emails of all users with the given role in the tenant (optionally filtered by department)."""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if department:
                    cur.execute(
                        "SELECT email FROM users WHERE company_id=%s AND role=%s AND department=%s AND email IS NOT NULL",
                        (tenant_id, role, department)
                    )
                else:
                    cur.execute(
                        "SELECT email FROM users WHERE company_id=%s AND role=%s AND email IS NOT NULL",
                        (tenant_id, role)
                    )
                return [r[0] for r in cur.fetchall() if r[0]]
        finally:
            put_conn(conn)
    except Exception:
        return []

def _get_emails_for_names(tenant_id: str, names: list[str]) -> list[tuple[str, str]]:
    """Resolve chain-assignee display names → [(name, email)] for this tenant.
    Case-insensitive and whitespace-tolerant match against users.name, so the
    approval email goes to exactly the people configured on the chain step."""
    wanted = [n.strip() for n in (names or []) if n and n.strip()]
    if not wanted:
        return []
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT name, email FROM users WHERE company_id=%s AND email IS NOT NULL",
                    (tenant_id,),
                )
                by_name = {(r[0] or "").strip().lower(): (r[0], r[1]) for r in cur.fetchall()}
        finally:
            put_conn(conn)
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    seen = set()
    for n in wanted:
        hit = by_name.get(n.lower())
        if hit and hit[1] and hit[1] not in seen:
            seen.add(hit[1])
            out.append((hit[0] or n, hit[1]))
    return out

def _get_bill_submitter(invoice_id: str, tenant_id: str) -> tuple[str | None, str | None]:
    """(name, email) of whoever submitted the bill — the earliest approval-chain
    entry — so 'fully approved' / 'sent back' notices reach the submitter instead
    of a blanket admin CC."""
    try:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT actor_name FROM bill_approval_chain
                       WHERE invoice_id=%s ORDER BY created_at ASC LIMIT 1""",
                    (invoice_id,),
                )
                row = cur.fetchone()
        finally:
            put_conn(conn)
    except Exception:
        return None, None
    name = (row[0] if row else None) or None
    if not name:
        return None, None
    pairs = _get_emails_for_names(tenant_id, [name])
    return name, (pairs[0][1] if pairs else None)

def _make_action_token(invoice_id: str, action: str, role: str) -> str:
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(APPROVAL_SECRET)
    return s.dumps({"invoice_id": invoice_id, "action": action, "role": role})

def _verify_action_token(token: str, max_age: int = 7 * 24 * 3600):
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
    s = URLSafeTimedSerializer(APPROVAL_SECRET)
    try:
        return s.loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None

def _fetch_bill_details(invoice_id: str) -> dict:
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """SELECT fi.invoice_number, fi.invoice_date, fi.due_date,
                          fi.total_amount, fi.tax_amount, fi.subtotal,
                          s.name AS vendor_name,
                          COALESCE(br.name, dt.company_name, '') AS buyer_name,
                          fi.tenant_id
                   FROM fact_invoices fi
                   LEFT JOIN dim_suppliers s ON fi.supplier_id = s.supplier_id
                   LEFT JOIN brands br ON fi.brand_id = br.id
                   LEFT JOIN dim_tenants dt ON fi.tenant_id = dt.tenant_id
                   WHERE fi.invoice_id = %s""",
                (invoice_id,),
            )
            row = cur.fetchone()
        put_conn(conn)
        if row:
            # Amounts are in the tenant's books currency (core/jurisdiction.py);
            # "₹" for an Indian tenant, exactly as before.
            ccy = get_jurisdiction(str(row[8])).base_currency if row[8] else "INR"
            return {
                "invoice_number": row[0] or invoice_id,
                "invoice_date":   str(row[1]) if row[1] else "—",
                "due_date":       str(row[2]) if row[2] else "—",
                "total_amount":   format_money(row[3], ccy),
                "tax_amount":     format_money(row[4], ccy),
                "subtotal":       format_money(row[5], ccy),
                "vendor_name":    row[6] or "—",
                "buyer_name":     row[7] or "—",
            }
    except Exception:
        pass
    return {"invoice_number": invoice_id, "invoice_date": "—", "due_date": "—",
            "total_amount": "—", "tax_amount": "—", "subtotal": "—", "vendor_name": "—", "buyer_name": "—"}

def _send_approval_email(invoice_id: str, action: str, actor_name: str,
                          actor_role: str, new_status: str, comment: str | None,
                          tenant_id: str = "", department: str | None = None):
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    bill = _fetch_bill_details(invoice_id)
    display_name = f"{bill['vendor_name']} ({bill['invoice_number']})"

    action_label = {"submit": "Submitted", "approve": "Approved", "send_back": "Sent Back"}.get(action, action.title())
    status_label  = new_status.replace("_", " ").title()

    next_role = _get_next_role_for_status(tenant_id, department, new_status)
    actor_label = actor_role.replace("_", " ").title()

    # Recipients + the names to address come from the CHAIN'S per-step assignees
    # configured for THIS bill (its department) — NOT every holder of the role,
    # and never a blanket admin CC. Fully-approved / sent-back-to-start notices go
    # to whoever submitted the bill.
    if new_status in ("APPROVED", "NEEDS_REVISION"):
        sub_name, sub_email = _get_bill_submitter(invoice_id, tenant_id)
        recipients     = [sub_email] if sub_email else []
        approver_names = [sub_name] if sub_name else []
    else:
        assignee_names = _build_status_name_map(tenant_id, department).get(new_status) or []
        named_pairs    = _get_emails_for_names(tenant_id, assignee_names)
        if named_pairs:
            recipients     = [e for _n, e in named_pairs]
            approver_names = [n for n, _e in named_pairs]
        else:
            # Step has no named assignee on the chain → fall back to role holders.
            recipients     = _get_emails_for_role(tenant_id, next_role, department) if next_role else []
            approver_names = assignee_names
    recipients = list(dict.fromkeys(r for r in recipients if r))   # dedupe, drop blanks
    approver_names = [n.strip() for n in approver_names if n and n.strip()]

    step_label = _get_status_label(tenant_id, department, new_status)
    next_label = (step_label or (next_role.replace("_", " ").title() if next_role else "Next Approver")).strip()
    if approver_names:
        names_joined = ", ".join(approver_names)
        # Avoid "Rishit (Rishit)" when the step was named after the person.
        approver_display = names_joined if names_joined == next_label else f"{names_joined} ({next_label})"
    else:
        approver_display = next_label
    greeting = f"<p style='margin:0 0 12px;'>Hi {', '.join(approver_names)},</p>" if approver_names else ""

    api_url = APP_URL
    approve_token   = _make_action_token(invoice_id, "approve",   next_role or "")
    send_back_token = _make_action_token(invoice_id, "send_back", next_role or "")
    approve_url     = f"{api_url}/api/bills/approve-link?token={approve_token}"
    send_back_url   = f"{api_url}/api/bills/approve-link?token={send_back_token}"

    if new_status == "APPROVED":
        subject     = f"Bill Fully Approved — {display_name} [INV:{invoice_id}]"
        body_intro  = f"Bill <b>{display_name}</b> has been <b>fully approved</b> by {actor_name} ({actor_label})."
        action_buttons = ""
    elif new_status == "NEEDS_REVISION":
        subject     = f"Bill Needs Revision — {display_name} [INV:{invoice_id}]"
        body_intro  = f"Bill <b>{display_name}</b> has been <b>sent back for revision</b> by {actor_name} ({actor_label})."
        action_buttons = ""
    else:
        subject     = f"Action Required ({approver_display}) — {display_name} [INV:{invoice_id}]"
        body_intro  = f"Bill <b>{display_name}</b> was <b>{action_label}</b> by {actor_name} ({actor_label}) and now needs your approval as <b>{approver_display}</b>."
        action_buttons = f"""
        <table style="margin-top:24px;"><tr>
          <td><a href="{approve_url}" style="background:#16a34a;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:15px;">✓ Approve</a></td>
          <td style="width:16px;"></td>
          <td><a href="{send_back_url}" style="background:#dc2626;color:#fff;padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:15px;">✗ Send Back</a></td>
        </tr></table>"""

    comment_block = f"<p><b>Comment:</b> {comment}</p>" if comment else ""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:auto;">
      <h2 style="color:#1a56db;">Onsumm Bill Approval</h2>
      {greeting}
      <p>{body_intro}</p>
      {comment_block}

      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <tr style="background:#f3f4f6;">
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Company</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill.get('buyer_name', '—')}</td>
        </tr>
        <tr>
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Vendor</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['vendor_name']}</td>
        </tr>
        <tr style="background:#f3f4f6;">
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Invoice No.</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['invoice_number']}</td>
        </tr>
        <tr style="background:#f3f4f6;">
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Invoice Date</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['invoice_date']}</td>
        </tr>
        <tr>
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Due Date</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['due_date']}</td>
        </tr>
        <tr style="background:#f3f4f6;">
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Subtotal</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['subtotal']}</td>
        </tr>
        <tr>
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Tax</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{bill['tax_amount']}</td>
        </tr>
        <tr style="background:#f1f5ff;">
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Total Amount</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;font-size:16px;font-weight:bold;">{bill['total_amount']}</td>
        </tr>
        <tr>
          <td style="padding:10px;border:1px solid #e5e7eb;"><b>Current Status</b></td>
          <td style="padding:10px;border:1px solid #e5e7eb;">{status_label}</td>
        </tr>
      </table>

      {action_buttons}

      <hr style="border:none;border-top:1px solid #eee;margin-top:32px;"/>
      <p style="font-size:12px;color:#888;">You can also reply <b>approved</b> to this email to approve directly.</p>
      <p style="font-size:12px;color:#888;">This is an automated notification from Onsumm.</p>
    </body></html>
    """

    if not recipients:
        # No chain assignee / role holder / submitter could be resolved — do NOT
        # fall back to a blanket admin CC (that was the old behaviour). Skip.
        print(f"[email] no recipients resolved for {invoice_id} (status={new_status}) — not sending")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_USER
        msg["To"]      = ", ".join(recipients)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, recipients, msg.as_string())
    except Exception as exc:
        import traceback
        print(f"[email] failed to send notification for {invoice_id}: {exc}")
        traceback.print_exc()

class BillActionRequest(BaseModel):
    action: str        # submit | approve | send_back
    actor_role: str
    actor_name: str
    comment: str | None = None
    target_status: str | None = None  # for send_back: which status to revert to

@router.post("/api/bills/{invoice_id}/action")
def bill_action(invoice_id: str, body: BillActionRequest):
    tenant = require_tenant()

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Scoped to the caller's tenant: the bill's own tenant_id must never
            # be what decides whose bill this is, or any logged-in user could
            # approve any bill in the database by its id.
            cur.execute(
                "SELECT status, tenant_id, department FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")
            current, bill_tenant_id, bill_dept = row[0], row[1], row[2]

            chain = _load_chain(str(bill_tenant_id), bill_dept)
            statuses = _chain_statuses(chain)  # ordered pending statuses for this chain
            # locked steps (e.g. Submitted) occupy the first positions; approval steps come after
            locked_count = sum(1 for s in chain if s.get("locked"))

            if body.action == "submit":
                if current not in ("DRAFT", "NEEDS_REVISION"):
                    raise HTTPException(status_code=400, detail=f"Cannot submit from status {current}")
                new_status = statuses[0] if statuses else "APPROVED"
                step = locked_count  # e.g. step 1 for Submitted

            elif body.action in ("approve", "send_back"):
                if current not in statuses:
                    raise HTTPException(status_code=400, detail=f"Nothing to approve in status {current}")
                step = locked_count + statuses.index(current) + 1
                allowed_roles = _build_status_role_map(str(bill_tenant_id), bill_dept).get(current, set())
                if body.actor_role not in allowed_roles:
                    raise HTTPException(status_code=403, detail=f"Role '{body.actor_role}' is not allowed at this step")
                # If step has specific assignees, enforce them (admins are always allowed)
                allowed_names = _build_status_name_map(str(bill_tenant_id), bill_dept).get(current)
                if allowed_names is not None and body.actor_role != "admin" and (body.actor_name or "").strip() not in allowed_names:
                    raise HTTPException(status_code=403, detail=f"'{body.actor_name}' is not assigned to this step")
                if body.action == "approve":
                    # If this step was reached via a multi-step send-back, skip already-approved
                    # steps and jump back to where the send-back came from.
                    cur.execute(
                        """SELECT action, step FROM bill_approval_chain
                           WHERE invoice_id=%s ORDER BY created_at DESC LIMIT 20""",
                        (invoice_id,)
                    )
                    recent = cur.fetchall()  # (action, step)
                    # Find the most recent send_back and the status it came from
                    jumped_from_status = None
                    for act, act_step in recent:
                        if act in ('approve', 'approved'):
                            break  # previous approve hit — no outstanding send-back context
                        if act == 'send_back':
                            # act_step is the step that sent it back; the status it was in = statuses[act_step - locked_count - 1]
                            origin_idx = act_step - locked_count - 1
                            if 0 <= origin_idx < len(statuses):
                                origin_status = statuses[origin_idx]
                                # Only skip if it's further ahead than the normal next step
                                normal_next = _next_status_from_chain(chain, current)
                                if origin_status != current and statuses.index(origin_status) > statuses.index(current):
                                    jumped_from_status = normal_next  # go directly to what comes after this step
                                    # Actually skip to origin_status (the sender's step status)
                                    jumped_from_status = origin_status
                            break
                    new_status = jumped_from_status if jumped_from_status else _next_status_from_chain(chain, current)
                else:
                    if body.target_status:
                        # Validate target is a real earlier status or NEEDS_REVISION
                        valid_targets = statuses[:statuses.index(current)] + ["NEEDS_REVISION"]
                        if body.target_status not in valid_targets:
                            raise HTTPException(status_code=400, detail=f"Invalid send-back target: {body.target_status}")
                        new_status = body.target_status
                    else:
                        idx = statuses.index(current)
                        new_status = statuses[idx - 1] if idx > 0 else "NEEDS_REVISION"

            elif body.action == "priority_change":
                # Just log it — no status change
                new_status = current
                step = 0

            else:
                raise HTTPException(status_code=400, detail="Unknown action")

            if body.action != "priority_change":
                cur.execute(
                    "UPDATE fact_invoices SET status=%s, source_updated_at=now() WHERE invoice_id=%s",
                    (new_status, invoice_id),
                )
            # Ensure target_step column exists (idempotent)
            ensure_column(cur, "bill_approval_chain", "target_step", "INTEGER DEFAULT NULL")
            # Compute target_step for send_back entries
            target_step_val = None
            if body.action == "send_back":
                if new_status == "NEEDS_REVISION":
                    target_step_val = locked_count  # submission step
                elif new_status in statuses:
                    target_step_val = locked_count + statuses.index(new_status) + 1
            cur.execute(
                """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment, target_step)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (invoice_id, bill_tenant_id, step, body.actor_role, body.actor_name, body.action, body.comment, target_step_val),
            )
        conn.commit()
        # Bust notification cache so next poll reflects the new status
        _notif_cache.clear()
        import threading
        threading.Thread(
            target=_send_approval_email,
            args=(invoice_id, body.action, body.actor_name, body.actor_role, new_status, body.comment,
                  str(bill_tenant_id), bill_dept),
            daemon=True,
        ).start()
        return {"status": new_status}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        import traceback; traceback.print_exc()
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

class ReconActionRequest(BaseModel):
    decision: str  # 'approve' | 'reject'
    actor_role: str
    actor_name: str
    comment: str | None = None

@router.post("/api/bills/{invoice_id}/reconcile-action")
def bill_reconcile_action(invoice_id: str, body: ReconActionRequest):
    """Record an approver's decision on the Nova reconciliation discrepancies.

    - approve → audit only (log who/when), no status change.
    - reject  → carry the bill forward to the next person in the approval chain
                (only when it is currently awaiting approval); otherwise audit only.
    Only the current approver at the active step may act.
    """
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")

    tenant = require_tenant()

    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, tenant_id, department FROM fact_invoices WHERE invoice_id = %s AND tenant_id = %s",
                (invoice_id, tenant),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Bill not found")
            current, bill_tenant_id, bill_dept = row[0], row[1], row[2]

            chain = _load_chain(str(bill_tenant_id), bill_dept)
            statuses = _chain_statuses(chain)          # ordered pending approval statuses
            in_approval = current in statuses          # bill is awaiting approval at an active step

            # Enforce that the actor is the current approver for this step.
            if in_approval:
                allowed_roles = _build_status_role_map(str(bill_tenant_id), bill_dept).get(current, set())
                if body.actor_role not in allowed_roles:
                    raise HTTPException(status_code=403, detail=f"Role '{body.actor_role}' is not allowed at this step")
                allowed_names = _build_status_name_map(str(bill_tenant_id), bill_dept).get(current)
                if allowed_names is not None and body.actor_role != "admin" and (body.actor_name or "").strip() not in allowed_names:
                    raise HTTPException(status_code=403, detail=f"'{body.actor_name}' is not assigned to this step")
            elif body.actor_role != "admin":
                # Not at an active approval step (draft / needs-revision / fully approved):
                # only admins may record a reconciliation decision, and it is audit-only.
                raise HTTPException(status_code=403, detail="This bill is not awaiting your approval")

            new_status = current
            advanced = False
            if body.decision == "reject" and in_approval:
                # Carry forward to the next person in the approval chain.
                new_status = _next_status_from_chain(chain, current)
                if new_status != current:
                    cur.execute(
                        "UPDATE fact_invoices SET status=%s, source_updated_at=now() WHERE invoice_id=%s",
                        (new_status, invoice_id),
                    )
                    advanced = True

            action = "recon_approve" if body.decision == "approve" else "recon_reject"
            cur.execute(
                """INSERT INTO bill_approval_chain (invoice_id, tenant_id, step, actor_role, actor_name, action, comment)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (invoice_id, bill_tenant_id, RECON_AUDIT_STEP, body.actor_role, body.actor_name, action, body.comment),
            )
        conn.commit()
        _notif_cache.clear()
        return {"status": new_status, "decision": body.decision, "advanced": advanced}
    except HTTPException:
        conn.rollback(); raise
    except Exception as e:
        import traceback; traceback.print_exc()
        conn.rollback(); raise HTTPException(status_code=500, detail=str(e))
    finally:
        put_conn(conn)

class BulkActionOptions(BaseModel):
    payment_date: str | None = None
    payment_method: str | None = None

class BulkBillActionRequest(BaseModel):
    bill_ids: List[str]
    action: str  # APPROVE | PUSH_TALLY | MARK_PAID
    actor_name: str
    actor_role: str
    company_id: str | None = None
    options: BulkActionOptions | None = None

@router.post("/api/bills/bulk-action")
def bulk_bill_action(body: BulkBillActionRequest):

    if len(body.bill_ids) > BULK_LIMIT:
        raise HTTPException(status_code=400, detail=f"Max {BULK_LIMIT} bills per bulk action")
    if not body.bill_ids:
        raise HTTPException(status_code=400, detail="bill_ids is empty")
    if body.action not in ("APPROVE", "PUSH_TALLY", "MARK_PAID"):
        raise HTTPException(status_code=400, detail="Unknown action")

    # APPROVE is also allowed for approver role
    if body.action in ("PUSH_TALLY", "MARK_PAID") and body.actor_role not in _BULK_ALLOWED_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient role for this bulk action")

    tenant = require_tenant()  # tenant from JWT; client company_id ignored
    results = []

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Load all requested bills in one query
            placeholders = ",".join(["%s"] * len(body.bill_ids))
            cur.execute(
                f"SELECT invoice_id, status, tenant_id, total_amount, tally_push_pending, payment_status, department FROM fact_invoices WHERE invoice_id IN ({placeholders}) AND tenant_id=%s",
                (*body.bill_ids, tenant),
            )
            rows = {str(r["invoice_id"]): r for r in cur.fetchall()}

        for bill_id in body.bill_ids:
            row = rows.get(bill_id)
            if not row:
                results.append({"bill_id": bill_id, "success": False, "error": "Not found"})
                continue

            current = row["status"]
            bill_tenant = str(row["tenant_id"])
            bill_dept = row.get("department")

            try:
                with conn.cursor() as cur:
                    if body.action == "APPROVE":
                        chain = _load_chain(bill_tenant, bill_dept)
                        statuses = _chain_statuses(chain)
                        if current not in statuses:
                            results.append({"bill_id": bill_id, "success": False, "error": f"Cannot approve from status {current}"})
                            continue
                        allowed = _build_status_role_map(bill_tenant, bill_dept).get(current, set())
                        if body.actor_role not in allowed:
                            results.append({"bill_id": bill_id, "success": False, "error": "Role not allowed at this step"})
                            continue
                        step = statuses.index(current) + 2  # 1-indexed, step 1 = submit
                        new_status = _next_status_from_chain(chain, current)
                        cur.execute("UPDATE fact_invoices SET status=%s, source_updated_at=now() WHERE invoice_id=%s", (new_status, bill_id))
                        cur.execute(
                            "INSERT INTO bill_approval_chain (invoice_id,tenant_id,step,actor_role,actor_name,action,comment) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (bill_id, bill_tenant, step, body.actor_role, body.actor_name, "approve", "Bulk approved"),
                        )
                        results.append({"bill_id": bill_id, "success": True, "status": new_status})

                    elif body.action == "PUSH_TALLY":
                        if row["tally_push_pending"]:
                            results.append({"bill_id": bill_id, "success": True, "status": "already_queued"})
                            continue
                        cur.execute("UPDATE fact_invoices SET tally_push_pending=true WHERE invoice_id=%s", (bill_id,))
                        results.append({"bill_id": bill_id, "success": True, "status": "queued"})

                    elif body.action == "MARK_PAID":
                        if row["payment_status"] == "paid":
                            results.append({"bill_id": bill_id, "success": True, "status": "already_paid"})
                            continue
                        total = float(row["total_amount"]) if row["total_amount"] else 0
                        cur.execute(
                            "UPDATE fact_invoices SET payment_status='paid', amount_paid=%s, outstanding_amount=0 WHERE invoice_id=%s",
                            (total, bill_id),
                        )
                        cur.execute(
                            "INSERT INTO bill_approval_chain (invoice_id,tenant_id,step,actor_role,actor_name,action,comment) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (bill_id, bill_tenant, 99, body.actor_role, body.actor_name, "payment_paid", "Bulk marked as paid"),
                        )
                        results.append({"bill_id": bill_id, "success": True, "status": "paid"})

                conn.commit()
            except Exception as e:
                conn.rollback()
                results.append({"bill_id": bill_id, "success": False, "error": str(e)})

    finally:
        put_conn(conn)

    succeeded = sum(1 for r in results if r["success"])
    failed    = sum(1 for r in results if not r["success"])
    return {"results": results, "succeeded": succeeded, "failed": failed, "total": len(results)}
