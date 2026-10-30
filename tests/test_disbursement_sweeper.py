"""The background reconciler for payments the bank has not finished.

These are deliberately DB-free and gateway-free. An earlier test in this area
ran the real settlement path and credited a live invoice, so every collaborator
here is a stub: a test for money-moving code must not be able to move money.

What is worth pinning is the behaviour that decides whether a payment is
recorded correctly without anyone watching — the enquire-never-initiate
contract, the claim that stops two callers settling the same payment, and the
refusal to close a payment the bank never answered about.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.domain.payments import sweeper


# ── the contract that keeps this worker safe ────────────────────────────────

def test_the_sweeper_never_initiates_a_payment():
    """AU's document is explicit for statusCode 100/101: re-enquire, DO NOT
    re-initiate. A sweeper that could initiate would turn an unanswered
    payment into a second transfer, unattended and on a timer."""
    import inspect
    src = inspect.getsource(sweeper)
    for forbidden in ("vendor_payment_pay", "_real_imps_initiate", "imps_initiate",
                      "_forced_success_initiate"):
        assert forbidden not in src, f"sweeper must not reference {forbidden}"


def test_the_cadence_matches_what_the_bank_asked_for():
    assert sweeper._SWEEP_INTERVAL_S == 300      # AU: "re-enquire every 5 minutes"
    assert sweeper._MIN_AGE_S > 0                # don't race the receipt card
    assert sweeper._BATCH > 0                    # one pass is bounded


def test_it_identifies_itself_in_the_audit_trail():
    """A status change nobody was present for still needs an actor; 'who
    changed this' is the first question asked of a payment record."""
    assert sweeper._ACTOR.startswith("system:")


# ── which rows a pass picks up ──────────────────────────────────────────────

class _Cur:
    def __init__(self, rows=()):
        self.rows, self.sql, self.params = list(rows), None, None

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_only_pending_payments_are_enquired_about():
    cur = _Cur()
    sweeper._due_rows(cur)
    sql = " ".join(cur.sql.split())
    assert "status = 'PENDING'" in sql
    # A settled or failed payment is finished; asking again would spend a
    # gateway call to be told what the row already says.
    assert "LIMIT" in sql


def test_a_payment_too_young_to_have_moved_is_left_alone():
    cur = _Cur()
    sweeper._due_rows(cur)
    sql = " ".join(cur.sql.split())
    assert "created_at < now() - make_interval(secs => %s)" in sql
    assert cur.params[0] == sweeper._MIN_AGE_S


def test_a_payment_the_bank_never_answered_about_ages_out_of_the_sweep():
    cur = _Cur()
    sweeper._due_rows(cur)
    sql = " ".join(cur.sql.split())
    assert "created_at > now() - %s" in sql
    assert cur.params[1] == sweeper._GIVE_UP_AFTER


def test_the_oldest_unanswered_payment_is_asked_about_first():
    cur = _Cur()
    sweeper._due_rows(cur)
    assert "ORDER BY updated_at ASC" in " ".join(cur.sql.split())


def test_an_aged_out_payment_keeps_its_status_rather_than_being_closed():
    """It is still in flight as far as anyone knows. Writing FAILED would
    assert something the bank never said, and would release the bill for a
    second payment attempt."""
    import inspect
    src = inspect.getsource(sweeper._report_abandoned)
    assert "UPDATE" not in src.upper()
    assert "count(*)" in src


# ── one pass, with the gateway and the database stubbed out ─────────────────

class _Conn:
    def __init__(self):
        self.committed = self.rolled_back = False
        self.autocommit = True

    def cursor(self):
        return _Cur()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def stub(monkeypatch):
    """Replaces every collaborator that could reach the database or the bank."""
    import backend_ocr.app.config as cfg
    import backend_ocr.app.routers.disbursement as dsb
    from backend_ocr.app.domain.payments import notify

    state = {"reconciled": [], "receipts": [], "results": {}}

    monkeypatch.setattr(cfg, "get_conn", lambda: _Conn())
    monkeypatch.setattr(cfg, "put_conn", lambda *a, **k: None)
    monkeypatch.setattr(sweeper, "_due_rows", lambda cur: state.get("due", []))

    def _fake_reconcile(conn, tenant_id, disbursement_id, **kw):
        state["reconciled"].append((disbursement_id, kw.get("actor"), kw.get("event")))
        if disbursement_id in state.get("raises", ()):
            raise RuntimeError("gateway exploded")
        return state["results"][disbursement_id]

    monkeypatch.setattr(dsb, "reconcile_disbursement", _fake_reconcile)
    monkeypatch.setattr(notify, "enqueue_payment_receipt",
                        lambda t, ids, actor: state["receipts"].extend(ids))
    return state


def _result(status, settled=False):
    return {"status": status, "settled": settled, "bank_rrn": "R1", "status_message": status}


def test_a_payment_that_settled_is_counted_and_receipted(stub):
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("SUCCESS", settled=True)}
    assert sweeper.sweep_once() == {"checked": 1, "settled": 1, "failed": 0,
                                    "still_pending": 0, "errors": 0}
    assert stub["receipts"] == ["d1"]


def test_a_payment_that_failed_sends_no_receipt(stub):
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("FAILED")}
    stats = sweeper.sweep_once()
    assert (stats["failed"], stats["settled"]) == (1, 0)
    assert stub["receipts"] == []


def test_a_payment_still_in_flight_is_left_for_the_next_pass(stub):
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("PENDING")}
    stats = sweeper.sweep_once()
    assert stats["still_pending"] == 1
    assert stub["receipts"] == []


def test_a_receipt_goes_out_only_on_the_transition_not_on_every_pass(stub):
    """reconcile_disbursement reports settled=True only for the caller whose
    UPDATE claimed the still-PENDING row. A sweep that merely observes an
    already-settled payment must not mail the vendor again."""
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("SUCCESS", settled=False)}
    stats = sweeper.sweep_once()
    assert stats["settled"] == 0
    assert stub["receipts"] == []


def test_one_payment_blowing_up_does_not_abandon_the_rest(stub):
    """Each payment commits on its own connection: the pass must survive a
    bank call that fails on one row, or a single unreachable payment would
    stall every other payment behind it indefinitely."""
    stub["due"] = [("d1", "t1"), ("boom", "t1"), ("d3", "t1")]
    stub["raises"] = ("boom",)
    stub["results"] = {"d1": _result("SUCCESS", settled=True), "d3": _result("FAILED")}
    stats = sweeper.sweep_once()
    assert stats == {"checked": 2, "settled": 1, "failed": 1,
                     "still_pending": 0, "errors": 1}
    assert stub["receipts"] == ["d1"]


def test_a_receipt_that_cannot_be_sent_does_not_undo_the_settlement(stub, monkeypatch):
    """The money has moved and the bill is credited by this point. Letting a
    mail failure raise would count the payment as an error and leave the
    sweeper to find it again."""
    from backend_ocr.app.domain.payments import notify
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("SUCCESS", settled=True)}

    def _boom(*a, **k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notify, "enqueue_payment_receipt", _boom)
    stats = sweeper.sweep_once()
    assert stats["settled"] == 1 and stats["errors"] == 0


def test_nothing_due_means_no_work_and_no_gateway_calls(stub):
    stub["due"] = []
    assert sweeper.sweep_once()["checked"] == 0
    assert stub["reconciled"] == []


def test_every_enquiry_is_attributed_to_the_sweeper(stub):
    stub["due"] = [("d1", "t1")]
    stub["results"] = {"d1": _result("PENDING")}
    sweeper.sweep_once()
    _id, actor, event = stub["reconciled"][0]
    assert actor == sweeper._ACTOR
    assert event == "status_swept"


# ── the shared reconcile path claims the transition ─────────────────────────

def test_reconcile_claims_the_pending_row_before_settling():
    """Two callers can see the same PENDING payment and both get SUCCESS back
    from the bank. Only the one whose UPDATE matched a still-PENDING row may
    credit the bill and send the receipt — otherwise the vendor is credited
    twice for one transfer."""
    import inspect
    from backend_ocr.app.routers import disbursement as dsb
    src = inspect.getsource(dsb.reconcile_disbursement)
    assert "WHERE disbursement_id=%s AND status='PENDING'" in src
    assert "claimed = cur.rowcount == 1" in src
    # settlement and the audit row both hang off that claim
    assert "if claimed and new_status == \"SUCCESS\":" in src
