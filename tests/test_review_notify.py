"""backend_ocr/app/domain/review/notify_actions.py — the `notify` action kind.

Two things under test: resolve_destination() re-derives who a notify body's
destination actually is rather than trusting it (the same discipline
act_merge_misread_vendor uses for its two vendor ids), and execute_notify()
wraps the send in an agent_runs row and is honest — manual_required, not a
fake success — when there is nowhere to actually deliver the message.

No live send happens anywhere here: email_connection.send_vendor_contact_email
and ._load_connection are monkeypatched, never called for real.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend_ocr.app.domain.review import notify_actions as na

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
FINDING = {
    "finding_id": "f-1", "subject_type": "vendor", "subject_id": VENDOR,
}


def _cur(fetchone_side_effect):
    cur = MagicMock()
    cur.fetchone = MagicMock(side_effect=fetchone_side_effect)
    return cur


# ── resolve_destination ──────────────────────────────────────────────────────

def test_vendor_contact_requires_a_contact_id():
    with pytest.raises(HTTPException) as exc:
        na.resolve_destination(_cur([]), TENANT, FINDING, {"destination_type": "vendor_contact"})
    assert exc.value.status_code == 400


def test_vendor_contact_requires_the_finding_to_be_about_a_vendor():
    body = {"destination_type": "vendor_contact", "contact_id": "c-1"}
    with pytest.raises(HTTPException) as exc:
        na.resolve_destination(_cur([]), TENANT, {**FINDING, "subject_type": "bill"}, body)
    assert exc.value.status_code == 400


def test_vendor_contact_not_found_is_404():
    cur = _cur([None])
    body = {"destination_type": "vendor_contact", "contact_id": "c-1"}
    with pytest.raises(HTTPException) as exc:
        na.resolve_destination(cur, TENANT, FINDING, body)
    assert exc.value.status_code == 404


def test_vendor_contact_with_no_email_is_404():
    cur = _cur([{"name": "Priya", "email": None}])
    body = {"destination_type": "vendor_contact", "contact_id": "c-1"}
    with pytest.raises(HTTPException):
        na.resolve_destination(cur, TENANT, FINDING, body)


def test_vendor_contact_resolves_from_the_finding_not_the_body():
    """Even though the body carries no supplier_id, the destination is scoped
    to the finding's own vendor — a notify body cannot be pointed at some
    other vendor's contact."""
    cur = _cur([{"name": "Priya Sharma", "email": "priya@vendor.example"}])
    body = {"destination_type": "vendor_contact", "contact_id": "c-1"}
    dest = na.resolve_destination(cur, TENANT, FINDING, body)
    assert dest == {"kind": "vendor_contact", "supplier_id": VENDOR, "contact_id": "c-1",
                     "name": "Priya Sharma", "email": "priya@vendor.example"}
    # scoped by the finding's vendor, tenant and the given contact id
    args = cur.execute.call_args.args[1]
    assert args == ("c-1", TENANT, VENDOR)


@pytest.mark.parametrize("destination_type,key", [("role", "role"), ("user", "username")])
def test_internal_destination_requires_a_value(destination_type, key):
    with pytest.raises(HTTPException) as exc:
        na.resolve_destination(_cur([]), TENANT, FINDING, {"destination_type": destination_type})
    assert exc.value.status_code == 400


def test_role_destination_resolves_without_touching_the_database():
    cur = _cur([])
    dest = na.resolve_destination(cur, TENANT, FINDING, {"destination_type": "role", "role": "finance_controller"})
    assert dest == {"kind": "role", "value": "finance_controller"}
    cur.execute.assert_not_called()


def test_unknown_destination_type_is_rejected():
    with pytest.raises(HTTPException) as exc:
        na.resolve_destination(_cur([]), TENANT, FINDING, {"destination_type": "carrier_pigeon"})
    assert exc.value.status_code == 400


# ── execute_notify ───────────────────────────────────────────────────────────

def test_notify_requires_a_message():
    cur = _cur([])
    conn = MagicMock()
    with pytest.raises(HTTPException) as exc:
        na.execute_notify(cur, conn, TENANT, "priya", FINDING,
                          {"destination_type": "vendor_contact", "contact_id": "c-1", "message": "  "})
    assert exc.value.status_code == 400
    # Nothing logged for a request this malformed.
    assert not any("agent_runs" in str(c) for c in cur.execute.call_args_list)


def test_notify_to_a_vendor_contact_with_no_mailbox_connected_is_honest_not_fake(monkeypatch):
    """The controller triggering this has no connected mailbox. The action must
    say so, not report success for a message that never sent."""
    cur = _cur([
        {"name": "Priya", "email": "priya@vendor.example"},  # resolve_destination
        {"run_id": "run-1"},                                  # INSERT agent_runs RETURNING
    ])
    conn = MagicMock()
    monkeypatch.setattr(na.email_connection, "_load_connection", lambda *a, **k: None)
    sent = MagicMock()
    monkeypatch.setattr(na.email_connection, "send_vendor_contact_email", sent)

    result = na.execute_notify(
        cur, conn, TENANT, "controller1", FINDING,
        {"destination_type": "vendor_contact", "contact_id": "c-1", "message": "please confirm"},
    )

    assert result["status"] == "manual_required"
    assert "mailbox" in result["message"].lower()
    sent.assert_not_called()  # never claims to have sent anything
    # agent_runs closed as SKIPPED, not SUCCEEDED — nothing actually ran.
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE agent_runs" in c.args[0]]
    assert len(update_calls) == 1
    assert update_calls[0].args[1][0] == "SKIPPED"


def test_notify_to_a_vendor_contact_sends_over_the_actors_own_mailbox(monkeypatch):
    cur = _cur([
        {"name": "Priya", "email": "priya@vendor.example"},
        {"run_id": "run-1"},
    ])
    conn = MagicMock()
    monkeypatch.setattr(na.email_connection, "_load_connection",
                        lambda *a, **k: {"status": "active", "email": "me@onsumm.ai"})
    sent = MagicMock(return_value={"status": "sent", "email_to": "priya@vendor.example",
                                   "from": "me@onsumm.ai", "thread_id": None})
    monkeypatch.setattr(na.email_connection, "send_vendor_contact_email", sent)

    result = na.execute_notify(
        cur, conn, TENANT, "controller1", FINDING,
        {"destination_type": "vendor_contact", "contact_id": "c-1",
         "subject": "Please confirm", "message": "please confirm"},
    )

    assert result["status"] == "ok"
    assert result["email_to"] == "priya@vendor.example"
    assert result["run_id"] == "run-1"
    # sent as the acting user, not some other mailbox
    assert sent.call_args.kwargs["username"] == "controller1"
    assert sent.call_args.kwargs["contact_id"] == "c-1"
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE agent_runs" in c.args[0]]
    assert update_calls[0].args[1][0] == "SUCCEEDED"


def test_notify_send_failure_marks_agent_runs_failed_and_still_raises(monkeypatch):
    cur = _cur([
        {"name": "Priya", "email": "priya@vendor.example"},
        {"run_id": "run-1"},
    ])
    conn = MagicMock()
    monkeypatch.setattr(na.email_connection, "_load_connection",
                        lambda *a, **k: {"status": "active", "email": "me@onsumm.ai"})

    def _boom(*a, **k):
        raise HTTPException(502, "Gmail rejected the message")
    monkeypatch.setattr(na.email_connection, "send_vendor_contact_email", _boom)

    with pytest.raises(HTTPException) as exc:
        na.execute_notify(
            cur, conn, TENANT, "controller1", FINDING,
            {"destination_type": "vendor_contact", "contact_id": "c-1", "message": "please confirm"},
        )
    assert exc.value.status_code == 502
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE agent_runs" in c.args[0]]
    assert len(update_calls) == 1
    assert "FAILED" in update_calls[0].args[0]  # the earlier UPDATE .. status='FAILED' literal


def test_notify_internal_destination_is_manual_required_not_a_fake_success():
    """No persisted notification store exists (§ module docstring) — an
    internal destination must not be reported as delivered."""
    cur = _cur([{"run_id": "run-1"}])
    conn = MagicMock()

    result = na.execute_notify(
        cur, conn, TENANT, "controller1", FINDING,
        {"destination_type": "role", "role": "finance_controller", "message": "heads up"},
    )

    assert result["status"] == "manual_required"
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE agent_runs" in c.args[0]]
    assert update_calls[0].args[1][0] == "SKIPPED"
