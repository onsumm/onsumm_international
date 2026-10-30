"""Executing a `notify` action from the Review Hub (§5.3.1 of the HLD).

Same rule as bill_actions.py and vendor_merge.py: a fix a finding offers must
call the logic that already owns the operation. Sending a vendor email is
owned by `email_connection.send_vendor_contact_email` — the same function
`POST /api/vendors/{id}/contacts/{id}/email` sends through — so this module
adds only what a notify action needs on top of that: re-deriving *who* the
destination in the request body actually is rather than trusting it at face
value, wrapping the send in an `agent_runs` row for audit, and being honest
when it cannot do the thing it was asked.

Internal (role/user) destinations are the one deliberate, reported gap here:
the platform has no persisted notification store to write into.
`/api/notifications/count` (backend_ocr/app/core/notifications.py) is a
30-second cache over a `COUNT(*)` against `fact_invoices`, not a table a
specific message can be inserted into, and building one is explicitly out of
scope for this phase ("don't invent a new notifications table"). Rather than
fake success, an internal destination returns `manual_required` — the same
honest non-success a vendor destination returns when nobody's mailbox is
connected — so the hub can say so instead of a controller believing a message
went out that never did.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from ...routers import email_connection

_DESTINATION_KINDS = ("vendor_contact", "role", "user")


def resolve_destination(cur, tenant_id: str, finding: dict, body: dict) -> dict:
    """Validate the notify body's destination against real data.

    The body started life as a server-built `action()`, but it is a plain POST
    payload by the time it reaches here — client-controlled like any other. So
    this re-derives who the destination actually is rather than trusting
    `body["contact_id"]` at face value, the same discipline
    `act_merge_misread_vendor` uses for the two vendor ids it is handed:
    a vendor_contact must belong to the finding's own vendor, not just to the
    tenant.
    """
    destination_type = (body.get("destination_type") or "").strip()

    if destination_type == "vendor_contact":
        contact_id = body.get("contact_id")
        if not contact_id:
            raise HTTPException(400, "contact_id is required for a vendor_contact notification")
        supplier_id = str(finding.get("subject_id") or "")
        if finding.get("subject_type") != "vendor" or not supplier_id:
            raise HTTPException(400, "This finding is not about a vendor")
        cur.execute(
            "SELECT name, email FROM supplier_contacts "
            " WHERE id=%s AND tenant_id=%s AND supplier_id=%s LIMIT 1",
            (contact_id, tenant_id, supplier_id),
        )
        row = cur.fetchone()
        if not row or not row["email"]:
            raise HTTPException(404, "Vendor contact not found or has no email on file")
        return {"kind": "vendor_contact", "supplier_id": supplier_id, "contact_id": contact_id,
                "name": row["name"], "email": row["email"]}

    if destination_type in ("role", "user"):
        value = ((body.get("role") if destination_type == "role" else body.get("username")) or "").strip()
        if not value:
            raise HTTPException(400, f"{destination_type} is required for a {destination_type} notification")
        return {"kind": destination_type, "value": value}

    raise HTTPException(
        400, f"Unknown destination_type {destination_type!r}; expected one of {_DESTINATION_KINDS}"
    )


def execute_notify(cur, conn, tenant_id: str, actor_username: str, finding: dict, body: dict) -> dict:
    """Run one `notify` action end to end: `agent_runs` before, the send (or
    the honest "can't, here's why" answer) in the middle, `agent_runs` after —
    the same INSERT-before/UPDATE-after shape comm_extraction_worker.py uses
    around its own LLM call, so any agent's cost and outcome land in the one
    place agent_runs exists to answer that from.

    Returns the dict the route hands straight back as the response body.
    Raises HTTPException only for the request itself being malformed or
    pointed at something that does not exist — never for "nobody's mailbox is
    connected" or "there is nowhere to put an internal notification yet",
    which are real, expected outcomes a human needs to see, not server errors.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")
    subject = (body.get("subject") or "").strip()

    # Raises before anything is logged if the destination doesn't check out —
    # a malformed request should not leave a stray agent_runs row behind.
    destination = resolve_destination(cur, tenant_id, finding, body)

    cur.execute(
        """
        INSERT INTO agent_runs
            (tenant_id, agent_name, trigger_type, trigger_ref, subject_type, subject_id, requested_by)
        VALUES (%s, 'communication', 'user', %s, %s, %s, %s)
        RETURNING run_id
        """,
        (tenant_id, str(finding["finding_id"]), finding.get("subject_type"),
         finding.get("subject_id"), actor_username),
    )
    run_id = cur.fetchone()["run_id"]
    conn.commit()

    try:
        if destination["kind"] == "vendor_contact":
            result = _notify_vendor_contact(cur, conn, tenant_id, actor_username,
                                            destination, subject, message)
        else:
            result = _notify_internal(destination)
    except HTTPException as exc:
        cur.execute(
            "UPDATE agent_runs SET status='FAILED', error=%s, finished_at=now() WHERE run_id=%s",
            (str(exc.detail)[:500], run_id),
        )
        conn.commit()
        raise
    except Exception as exc:
        cur.execute(
            "UPDATE agent_runs SET status='FAILED', error=%s, finished_at=now() WHERE run_id=%s",
            (str(exc)[:500], run_id),
        )
        conn.commit()
        raise

    # manual_required is a real, human-actionable outcome, not a system
    # failure, but it is also not "the agent did the thing" — SKIPPED says so
    # without pretending it was a bug in either direction.
    final_status = "SUCCEEDED" if result.get("status") == "ok" else "SKIPPED"
    cur.execute(
        "UPDATE agent_runs SET status=%s, finished_at=now(), outcome=%s::jsonb WHERE run_id=%s",
        (final_status,
         json.dumps({"destination_kind": destination.get("kind"), "result": result.get("status")}),
         run_id),
    )
    conn.commit()

    result["run_id"] = str(run_id)
    result["destination"] = destination
    return result


def _notify_vendor_contact(cur, conn, tenant_id: str, actor_username: str,
                           destination: dict, subject: str, message: str) -> dict:
    """Send over the acting user's own connected mailbox — never someone
    else's, and never a shared/system mailbox the user never agreed to send
    from."""
    row = email_connection._load_connection(cur, tenant_id, actor_username)
    if not row or row["status"] != "active":
        return {
            "status": "manual_required",
            "message": "You don't have a mailbox connected — send this to the vendor yourself.",
        }
    sent = email_connection.send_vendor_contact_email(
        cur, conn, tenant_id=tenant_id, supplier_id=destination["supplier_id"],
        contact_id=destination["contact_id"], username=actor_username,
        subject=subject or f"Regarding {destination.get('name') or 'your account'}",
        message=message,
    )
    return {**sent, "status": "ok"}


def _notify_internal(destination: dict) -> dict:
    """The honest gap: no notifications table exists to write an internal
    message into (see module docstring). Reported, not faked."""
    return {
        "status": "manual_required",
        "message": (
            "There's no in-app notification inbox yet — reach "
            f"{destination.get('value') or 'this person'} directly."
        ),
    }
