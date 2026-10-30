"""HDFC IMPS status-code mapping — the invariants that keep a payment from
being recorded as delivered when the bank has not said so.

The table itself is data (hdfc_status_codes.json); what is worth pinning here
is the lookup's behaviour on the cases where guessing would cost money.
"""
from __future__ import annotations

import json

from backend_ocr.app.integrations.payments import hdfc as h


def _codes() -> dict:
    return json.loads(h._DATA_PATH.read_text(encoding="utf-8"))["codes"]


# ── the ambiguous code ───────────────────────────────────────────────────────
# TXSETT means two different things: "Settled" is still in flight, "Completed"
# is done. Resolving it from the code alone would mark a disbursement paid on
# the bank's own interim state.

def test_txsett_is_unresolvable_without_the_reason():
    assert h.describe("TXSETT") is None
    assert h.to_txn_status("TXSETT") is None
    assert h.is_terminal("TXSETT") is False


def test_txsett_resolves_both_ways_when_given_the_reason():
    assert h.to_txn_status("TXSETT", "Settled") == "PENDING"
    assert h.is_terminal("TXSETT", "Settled") is False
    assert h.to_txn_status("TXSETT", "Completed") == "SUCCESS"
    assert h.is_terminal("TXSETT", "Completed") is True


def test_ambiguous_code_with_unknown_reason_never_reports_success():
    assert h.to_txn_status("TXSETT", "some message HDFC did not document") is None


# ── the bank's own counter-intuitive classification ──────────────────────────

def test_txcomp_is_interim_despite_being_named_payment_completed():
    """Going by the name rather than the table would settle this early."""
    assert h.to_txn_status("TXCOMP") == "PENDING"
    assert h.is_terminal("TXCOMP") is False


# ── unknown input falls back rather than inventing an outcome ────────────────

def test_unknown_and_missing_codes_resolve_to_nothing():
    for code in ("NOT_A_CODE", "", None):
        assert h.to_txn_status(code) is None
        assert h.is_terminal(code) is False
        assert h.describe(code) is None


def test_explain_falls_back_to_the_gateways_own_message():
    assert h.explain("NOT_A_CODE", fallback_message="raw gateway text") == "raw gateway text"
    # a reason we were given beats a generic fallback
    assert h.explain("NOT_A_CODE", "bank said this", "raw") == "bank said this"


# ── lookup robustness against how the code arrives on the wire ──────────────

def test_lookup_tolerates_case_and_whitespace():
    assert h.to_txn_status("txsett", "completed") == "SUCCESS"
    assert h.to_txn_status("  TXSETT  ", "  Completed  ") == "SUCCESS"


# ── the table as a whole stays resolvable ───────────────────────────────────

def test_every_code_resolves_from_the_code_alone_except_txsett():
    unresolved = sorted(c for c in _codes() if h.to_txn_status(c) is None)
    assert unresolved == ["TXSETT"]


def test_every_code_reason_pair_in_the_sheet_resolves():
    unresolved = [
        (code, r["reason"])
        for code, entry in _codes().items()
        for r in entry["reasons"]
        if h.to_txn_status(code, r["reason"]) is None
    ]
    assert unresolved == []


def test_statuses_map_onto_the_disbursement_vocabulary():
    allowed = {"SUCCESS", "FAILED", "PENDING"}
    for code, entry in _codes().items():
        for r in entry["reasons"]:
            assert h.to_txn_status(code, r["reason"]) in allowed


def test_terminal_flag_agrees_with_status():
    """Only a PROCESSED or REJECTED state is final; INTERIM never is."""
    for code, entry in _codes().items():
        for r in entry["reasons"]:
            terminal = h.is_terminal(code, r["reason"])
            assert terminal is (r["status"] in ("PROCESSED", "REJECTED"))
