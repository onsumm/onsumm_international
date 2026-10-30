"""Who the hub offers an Approve button to.

The gate matters more than the button. routers/bill_approvals.bill_action is
what actually enforces approval, and this module's job is to predict its answer
exactly: offer the button to everyone it would accept, and to nobody it would
refuse. Offering it too widely is the worse failure — the user clicks, gets a
403, and concludes the hub does not understand their permissions.

The chain helpers read the tenant's configuration from the database, so they are
stubbed here; what is under test is the decision made from what they return.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

from backend_ocr.app.domain.review import bill_actions as ba  # noqa: E402

TENANT = "10000000-0000-0000-0000-000000000001"

BILL = {
    "invoice_number": "LSP/MAY/01", "status": "PENDING_APPROVER", "department": "Finance",
    "currency_code": "INR", "total_amount": 1833167, "supplier_name": "Unifinz Capital",
}

CHAIN = [
    {"step_order": 0, "label": "Submitted", "role": "submitter", "locked": True},
    {"step_order": 1, "label": "Approver", "role": "approver", "locked": False},
]


class FakeCursor:
    """Answers the one query this module makes: the caller's display name."""

    def __init__(self, name="Mohd Salman"):
        self._name = name

    def execute(self, sql, params=None):
        assert "FROM users" in sql, sql

    def fetchone(self):
        return {"name": self._name}


@pytest.fixture
def chain(monkeypatch):
    """Stub the chain config: one locked Submitted step, one Approver step."""
    def setup(roles=None, names=None, statuses=("PENDING_APPROVER",)):
        monkeypatch.setattr(ba, "_load_chain", lambda *a, **k: CHAIN)
        monkeypatch.setattr(ba, "_chain_statuses", lambda *a, **k: list(statuses))
        monkeypatch.setattr(ba, "_build_status_role_map",
                            lambda *a, **k: {"PENDING_APPROVER": roles
                                             if roles is not None else {"approver", "admin"}})
        monkeypatch.setattr(ba, "_build_status_name_map",
                            lambda *a, **k: {"PENDING_APPROVER": names})
    return setup


@pytest.fixture
def actor(monkeypatch):
    def setup(role, username="msalman"):
        monkeypatch.setattr(ba, "current_role", lambda: role)
        monkeypatch.setattr(ba, "current_username", lambda: username)
    return setup


# ── The gate ─────────────────────────────────────────────────────────────────

def test_the_assigned_approver_may_approve(chain, actor):
    chain(); actor("approver")
    assert ba.can_approve(FakeCursor(), TENANT, BILL) is True


def test_a_role_that_is_not_on_this_step_may_not(chain, actor):
    chain(); actor("accountant")
    assert ba.can_approve(FakeCursor(), TENANT, BILL) is False


def test_a_bill_that_is_not_pending_offers_nothing(chain, actor):
    chain(); actor("approver")
    for status in ("APPROVED", "DRAFT", "NEEDS_REVISION", ""):
        assert ba.can_approve(FakeCursor(), TENANT, {**BILL, "status": status}) is False


def test_a_status_outside_this_chain_is_refused(chain, actor):
    """The role map is keyed by status; a bill sitting at a status this chain
    does not contain must not fall through to a permissive default."""
    chain(statuses=("PENDING_FC",)); actor("approver")
    assert ba.can_approve(FakeCursor(), TENANT, BILL) is False


def test_a_named_step_admits_only_the_people_named_on_it(chain, actor):
    chain(names=["Mohd Salman"]); actor("approver")
    assert ba.can_approve(FakeCursor("Mohd Salman"), TENANT, BILL) is True
    assert ba.can_approve(FakeCursor("Someone Else"), TENANT, BILL) is False


def test_an_admin_bypasses_the_name_gate_here_because_it_does_there(chain, actor):
    """bill_action lets an admin act on a name-gated step. Predicting otherwise
    would hide a button from someone the server would have accepted."""
    chain(names=["Mohd Salman"]); actor("admin")
    assert ba.can_approve(FakeCursor("Someone Else"), TENANT, BILL) is True


def test_the_display_name_is_used_not_the_username(chain, actor):
    """Assignees are configured by display name. Matching on the username would
    403 every correctly-assigned approver whose login differs from their name."""
    chain(names=["Mohd Salman"]); actor("approver", username="msalman")
    assert ba.can_approve(FakeCursor("Mohd Salman"), TENANT, BILL) is True


# ── The action ───────────────────────────────────────────────────────────────

def test_the_action_confirms_and_names_what_is_being_approved(chain, actor):
    chain(); actor("approver")
    a = ba.approve_action(FakeCursor(), TENANT, "bill-1", BILL)
    assert a["kind"] == "approve"
    assert a["capability"] == "approve"
    # Never a bare click in a queue: the server always sends a confirmation.
    assert a["confirm"] is not None
    impact = {i["label"]: i["value"] for i in a["confirm"]["impact"]}
    assert impact["Vendor"] == "Unifinz Capital"
    assert impact["Amount"] == "INR 1,833,167.00"
    assert impact["Currently at"] == "Pending Approver"


def test_a_foreign_currency_bill_says_which_currency(chain, actor):
    chain(); actor("approver")
    a = ba.approve_action(FakeCursor(), TENANT, "bill-1", {**BILL, "currency_code": "USD"})
    impact = {i["label"]: i["value"] for i in a["confirm"]["impact"]}
    assert impact["Amount"].startswith("USD ")


def test_no_action_at_all_when_they_may_not_approve(chain, actor):
    chain(); actor("accountant")
    assert ba.approve_action(FakeCursor(), TENANT, "bill-1", BILL) is None
