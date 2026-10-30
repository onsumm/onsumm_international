"""POST /api/vendors/{id}/gstins — the `make_primary` addition.

This is the endpoint the Review Hub's master_data_conflict "keep new" action
calls directly for a vendor_gstin conflict (never a bespoke resolution
endpoint). Before this, the endpoint could only ever add a *secondary* GSTIN —
useless for a conflict finding, whose whole point is that the vendor's
*primary* GSTIN needs to change. `make_primary` closes that gap by extending
the endpoint that already owns writing supplier_gstins, rather than writing a
second path to the same table.

No live GSTIN verification or database happens here: psycopg2.connect and the
sandbox KYB call are both monkeypatched.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend_ocr.app.integrations import sandbox_kyb
from backend_ocr.app.routers import vendor_gstins as vg

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"


def _fake_conn(fetchone_side_effect):
    cur = MagicMock()
    cur.fetchone = MagicMock(side_effect=fetchone_side_effect)
    cur_ctx = MagicMock()
    cur_ctx.__enter__ = MagicMock(return_value=cur)
    cur_ctx.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur_ctx
    return conn, cur


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(vg, "require_tenant", lambda: TENANT)
    monkeypatch.setattr(
        sandbox_kyb, "verify_gstin",
        lambda gstin: {"valid": True, "status": "active", "trade_name": "Acme",
                       "legal_name": "Acme Traders"},
    )


def test_make_primary_demotes_the_old_primary_before_inserting_the_new_one(stubbed):
    sup_row = {"gstn": "07AABCU9603R1Z5", "pan": "AABCU9603R"}  # same PAN as the new GSTIN
    conn, cur = _fake_conn(fetchone_side_effect=[
        sup_row,                                              # dim_suppliers lookup
        None,                                                 # no existing duplicate row
        {"id": 1, "gstin": "29AABCU9603R1ZJ", "pan": "AABCU9603R",
         "is_primary": True, "status": "VERIFIED", "trade_name": "Acme",
         "legal_name": "Acme Traders", "gstin_status": "active",
         "error_message": None, "verified_at": None, "created_at": None},
    ])
    import backend_ocr.app.routers.vendor_gstins as vg_mod
    monkeypatch_connect = MagicMock(return_value=conn)
    orig_connect = vg_mod.psycopg2.connect
    vg_mod.psycopg2.connect = monkeypatch_connect
    try:
        body = vg.AddGstinRequest(gstin="29AABCU9603R1ZJ", make_primary=True)
        result = vg.add_supplier_gstin(VENDOR, body)
    finally:
        vg_mod.psycopg2.connect = orig_connect

    assert result["gstin"] == "29AABCU9603R1ZJ"
    assert result["is_primary"] is True

    stmts = [c.args[0] for c in cur.execute.call_args_list]
    demote_idx = next(i for i, s in enumerate(stmts) if "SET is_primary=false" in s)
    insert_idx = next(i for i, s in enumerate(stmts) if s.strip().startswith("INSERT INTO supplier_gstins"))
    assert demote_idx < insert_idx, "must demote the old primary before the new row exists"

    # The new row is inserted as primary — the whole point of make_primary.
    insert_params = cur.execute.call_args_list[insert_idx].args[1]
    assert insert_params[4] is True  # is_primary positional slot


def test_without_make_primary_no_demotion_happens_and_the_row_is_secondary(stubbed):
    sup_row = {"gstn": "07AABCU9603R1Z5", "pan": "AABCU9603R"}  # same PAN as the new GSTIN
    conn, cur = _fake_conn(fetchone_side_effect=[
        sup_row, None,
        {"id": 2, "gstin": "27AABCU9603R1ZK", "pan": "AABCU9603R", "is_primary": False,
         "status": "VERIFIED", "trade_name": None, "legal_name": None,
         "gstin_status": "active", "error_message": None, "verified_at": None,
         "created_at": None},
    ])
    import backend_ocr.app.routers.vendor_gstins as vg_mod
    orig_connect = vg_mod.psycopg2.connect
    vg_mod.psycopg2.connect = MagicMock(return_value=conn)
    try:
        body = vg.AddGstinRequest(gstin="27AABCU9603R1ZK")  # make_primary defaults False
        result = vg.add_supplier_gstin(VENDOR, body)
    finally:
        vg_mod.psycopg2.connect = orig_connect

    assert result["is_primary"] is False
    stmts = [c.args[0] for c in cur.execute.call_args_list]
    assert not any("SET is_primary=false" in s for s in stmts)
