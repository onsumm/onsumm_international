"""A bill failing mid-batch must not erase the bills already paid before it.

Deliberately runs against a fake connection rather than a database. The
invariant here is transaction *sequencing* — that each bill's outcome is
committed before the next bill is attempted — which needs no SQL to prove, and
an earlier version of this check ran the real settlement path and credited a
live invoice before being reverted by hand. A test of money-moving code should
not be able to move money.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.routers import disbursement as dsb


class _FakeCursor:
    """Answers the few reads verify_batch_otp makes; records every write."""

    def __init__(self, conn, rows):
        self._conn = conn
        self._rows = rows
        self._last = ""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, params=None):
        self._last = " ".join(str(sql).split())
        self._conn.log.append(("execute", self._last[:60], params))

    def fetchall(self):
        return list(self._rows.values())

    def fetchone(self):
        if "FROM fact_invoices" in self._last:
            return {"total_amount": 100, "amount_paid": 0, "bill_number": "B1",
                    "invoice_date": None, "due_date": None, "supplier_id": "s1"}
        return None

    @property
    def rowcount(self):
        return 1


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.log: list = []
        self.autocommit = False

    def cursor(self, *_a, **_k):
        return _FakeCursor(self, self.rows)

    def commit(self):
        self.log.append(("commit", None, None))

    def rollback(self):
        self.log.append(("rollback", None, None))


def _disbursement(did: str):
    return {
        "disbursement_id": did, "tenant_id": "t1", "invoice_id": f"inv-{did}",
        "supplier_id": "s1", "amount": 100, "beneficiary_name": "V",
        "beneficiary_account": "1", "beneficiary_ifsc": "SBIN0001470",
        "beneficiary_mobile": "9", "remitter_account_number": "2602201113751781",
        "bank_provider": "AUBANK", "nbfc_name": "EXUDE", "tran_ref_no": f"REF-{did}",
        "status": "PENDING_OTP", "otp_code_hash": dsb._hash_otp("123456"),
        "otp_expires_at": 9e12, "otp_attempts": 0, "initiated_by": "tester",
    }


@pytest.fixture
def batch(monkeypatch):
    rows = {"d1": _disbursement("d1"), "d2": _disbursement("d2")}
    conn = _FakeConn(rows)
    applied: list[str] = []

    monkeypatch.setattr(dsb, "get_conn", lambda: conn)
    monkeypatch.setattr(dsb, "put_conn", lambda *_a, **_k: None)
    monkeypatch.setattr(dsb, "_dictcur", lambda c: c.cursor())
    monkeypatch.setattr(dsb, "require_role", lambda *_a, **_k: None)
    monkeypatch.setattr(dsb, "_tid", lambda *_a, **_k: "t1")
    monkeypatch.setattr(dsb, "_actor", lambda: "tester")
    monkeypatch.setattr(dsb, "_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(dsb, "_get_vendor_name_and_month", lambda *_a, **_k: ("V", "2026-06"))
    monkeypatch.setattr(dsb, "DISBURSEMENT_FORCE_SUCCESS", False)
    monkeypatch.setattr(dsb.payment_notify, "enqueue_payment_receipt", lambda *_a, **_k: None)
    monkeypatch.setattr(dsb, "_apply_success",
                        lambda _c, _t, d, _r: applied.append(d["disbursement_id"]))
    return conn, applied


def _ok(d):
    return {"success": True, "actCode": "0",
            "data": {"status": "SUCCESS", "statusMessage": "ok", "bankRRN": "RRN1",
                     "tranRefNo": d["tran_ref_no"], "beneficiaryName": "V",
                     "remitterAccountNumber": d["remitter_account_number"]}}


def _call(ids):
    return dsb.verify_batch_otp(
        dsb.BatchVerifyOtpBody(disbursement_ids=ids, otp="123456", company_id="t1"))


def test_a_bill_failing_midbatch_does_not_undo_an_earlier_paid_bill(batch, monkeypatch):
    conn, applied = batch
    seen = {"n": 0}

    def flaky(_c, d, _v, _m):
        seen["n"] += 1
        if seen["n"] == 1:
            return _ok(d)
        raise RuntimeError("boom after the bank was called")

    monkeypatch.setattr(dsb, "_real_imps_initiate", flaky)
    res = _call(["d1", "d2"])

    by_id = {r["disbursement_id"]: r for r in res["results"]}
    assert by_id["d1"]["status"] == "SUCCESS"
    assert by_id["d2"]["status"] == "PENDING"

    # The sequencing that matters: d1 is committed BEFORE d2 is attempted, so
    # d2's rollback cannot reach back and undo it.
    kinds = [k for k, *_ in conn.log]
    first_commit = kinds.index("commit")
    first_rollback = kinds.index("rollback")
    assert first_commit < first_rollback, conn.log


def test_the_interrupted_bill_is_pending_not_failed(batch, monkeypatch):
    """Its money may or may not have moved. FAILED would leave the bill payable
    and invite a second transfer for the same debt."""
    conn, _ = batch
    monkeypatch.setattr(dsb, "_real_imps_initiate",
                        lambda _c, d, _v, _m: (_ for _ in ()).throw(RuntimeError("x")))
    res = _call(["d1", "d2"])
    assert [r["status"] for r in res["results"]] == ["PENDING", "PENDING"]
    assert all("outcome is unknown" in r["status_message"] for r in res["results"])


def test_settlement_only_follows_a_real_success(batch, monkeypatch):
    """_apply_success marks the bill paid and draws down the remitter balance.
    It must not run for a bill the bank did not confirm."""
    conn, applied = batch

    def mixed(_c, d, _v, _m):
        if d["disbursement_id"] == "d1":
            return _ok(d)
        out = _ok(d)
        out["success"] = False
        out["data"]["status"] = "FAILED"
        return out

    monkeypatch.setattr(dsb, "_real_imps_initiate", mixed)
    _call(["d1", "d2"])
    assert applied == ["d1"], "only the confirmed bill may settle"


def test_every_bill_is_reported_even_when_one_blows_up(batch, monkeypatch):
    """A caller must never be left guessing what happened to a bill it sent."""
    conn, _ = batch
    monkeypatch.setattr(dsb, "_real_imps_initiate",
                        lambda _c, d, _v, _m: _ok(d) if d["disbursement_id"] == "d1"
                        else (_ for _ in ()).throw(RuntimeError("x")))
    res = _call(["d1", "d2"])
    assert {r["disbursement_id"] for r in res["results"]} == {"d1", "d2"}


def test_this_test_module_cannot_touch_a_database(batch):
    """The guard that keeps this file honest: get_conn is replaced, so any real
    connection attempt would surface here rather than in production data."""
    conn, _ = batch
    assert dsb.get_conn() is conn
    assert isinstance(conn, _FakeConn)
