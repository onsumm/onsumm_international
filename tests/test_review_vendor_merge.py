"""The one write the Review Hub performs on a vendor.

It folds a record created from a misread GSTIN into the verified one. The PAN
guard used by /api/vendors/{id}/merge cannot apply here — the PANs differ
*because* the GSTIN was misread, which is the entire finding — so the endpoint
re-derives a narrower guard server-side rather than trusting the finding that
proposed it. A client can post any two vendor ids; these tests are about what
happens when it does.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend_ocr.app.domain.vendor_merge import repoint_and_retire

WRONG = "07AACCA2800P1Z2"
RIGHT = "07AAACB2100P1ZZ"


def _cur(tables=("fact_invoices", "stg_bills"), dict_rows=False):
    cur = MagicMock()
    rows = [{"table_name": t} for t in tables] if dict_rows else [(t,) for t in tables]
    cur.fetchall = MagicMock(return_value=rows)
    cur.rowcount = 1
    return cur


def test_repoint_handles_a_plain_cursor():
    """routers/vendors.py passes tuple rows."""
    moved = repoint_and_retire(_cur(), "src", "tgt", source_gstn=None, source_pan=None)
    assert moved == {"fact_invoices": 1, "stg_bills": 1}


def test_repoint_handles_a_dict_cursor():
    """routers/review_hub.py passes a RealDictCursor. Reading the column by
    position raised KeyError here the first time this ran for real."""
    moved = repoint_and_retire(_cur(dict_rows=True), "src", "tgt",
                               source_gstn=None, source_pan=None)
    assert moved == {"fact_invoices": 1, "stg_bills": 1}


def test_the_table_list_is_discovered_not_hardcoded():
    """A table that gains a supplier_id is covered the day it is added, rather
    than quietly orphaning rows."""
    cur = _cur(tables=("fact_invoices", "some_new_table"))
    repoint_and_retire(cur, "src", "tgt", source_gstn=None, source_pan=None)
    sql = cur.execute.call_args_list[0][0][0]
    assert "information_schema.columns" in sql
    assert "supplier_id" in sql


def test_a_colliding_row_is_dropped_rather_than_failing_the_merge():
    """Where the source's row duplicates one the target already has, the source's
    is redundant by definition. A negative count records that it was deleted."""
    cur = _cur(tables=("supplier_gstns",))
    calls = {"n": 0}

    def execute(sql, *_a):
        calls["n"] += 1
        if sql.strip().startswith("UPDATE supplier_gstns"):
            raise Exception("duplicate key")

    cur.execute = MagicMock(side_effect=execute)
    cur.fetchall = MagicMock(return_value=[("supplier_gstns",)])
    moved = repoint_and_retire(cur, "src", "tgt", source_gstn=None, source_pan=None)
    assert moved["supplier_gstns"] < 0
    assert any("ROLLBACK TO SAVEPOINT" in str(c) for c in cur.execute.call_args_list)


def test_the_duplicate_is_retired_not_deleted():
    """The row stays for audit, hidden from the vendor list."""
    cur = _cur()
    repoint_and_retire(cur, "src", "tgt", source_gstn=None, source_pan=None)
    stmts = " ".join(str(c[0][0]) for c in cur.execute.call_args_list)
    assert "is_active = false" in stmts
    assert "DELETE FROM dim_suppliers" not in stmts


# ── The guard the endpoint applies ────────────────────────────────────────────
# Mirrored here as a pure function so the decision table is testable without a
# database. The endpoint runs the same comparisons.

def guard(src_gstn, tgt_gstn, src_status, tgt_status, same_name=True):
    if not same_name:
        raise HTTPException(400, "These two vendors do not have the same name")
    if tgt_status != "VERIFIED":
        raise HTTPException(400, "The surviving vendor must be a verified one")
    if src_status == "VERIFIED":
        raise HTTPException(400, "Refusing to retire a verified vendor record")
    if len(src_gstn) != 15 or len(tgt_gstn) != 15:
        raise HTTPException(400, "Both records need a full GSTIN to compare")
    if src_gstn[:2] != tgt_gstn[:2]:
        raise HTTPException(400, "The state codes differ")
    diff = sum(1 for a, b in zip(src_gstn, tgt_gstn) if a != b)
    if diff == 0 or diff > 6:
        raise HTTPException(400, f"The GSTINs differ in {diff} characters")
    return True


def test_the_real_vodafone_case_passes_the_guard():
    assert guard(WRONG, RIGHT, "FAILED", "VERIFIED")


@pytest.mark.parametrize("case,args", [
    ("a different name",        dict(src_gstn=WRONG, tgt_gstn=RIGHT, src_status="FAILED",
                                     tgt_status="VERIFIED", same_name=False)),
    ("target not verified",     dict(src_gstn=WRONG, tgt_gstn=RIGHT, src_status="FAILED",
                                     tgt_status="UNVERIFIED")),
    ("retiring a verified one", dict(src_gstn=WRONG, tgt_gstn=RIGHT, src_status="VERIFIED",
                                     tgt_status="VERIFIED")),
    ("a different state",       dict(src_gstn="29AACCA2800P1Z2", tgt_gstn=RIGHT,
                                     src_status="FAILED", tgt_status="VERIFIED")),
    ("wholly different codes",  dict(src_gstn="07ZZZZZ9999Z9ZZ", tgt_gstn=RIGHT,
                                     src_status="FAILED", tgt_status="VERIFIED")),
    ("identical GSTINs",        dict(src_gstn=RIGHT, tgt_gstn=RIGHT, src_status="FAILED",
                                     tgt_status="VERIFIED")),
    ("a truncated GSTIN",       dict(src_gstn="07AACC", tgt_gstn=RIGHT, src_status="FAILED",
                                     tgt_status="VERIFIED")),
])
def test_the_guard_refuses(case, args):
    with pytest.raises(HTTPException):
        guard(**args)
