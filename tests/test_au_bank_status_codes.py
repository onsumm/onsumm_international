"""AU Bank payout status mapping — the invariants that keep a payment from
being recorded as delivered, or sent twice, when the bank has not said so.
"""
from __future__ import annotations

import json

from backend_ocr.app.integrations.payments import au_bank as au


def _table() -> dict:
    return json.loads(au._DATA_PATH.read_text(encoding="utf-8"))


# ── acceptance is not settlement ─────────────────────────────────────────────

def test_accepted_on_initiation_is_pending_not_success():
    """responseCode 00 / ACCEPTED means a batchNo was issued, nothing more.
    Treating it as SUCCESS would mark every initiated payment as delivered."""
    info = au.describe_initiation("00")
    assert info["status"] == "INTERIM"
    assert info["terminal"] is False


def test_rejected_on_initiation_is_terminal_failure():
    info = au.describe_initiation("99")
    assert info["status"] == "REJECTED"
    assert info["terminal"] is True


# ── the compound key ─────────────────────────────────────────────────────────

def test_batch_code_alone_cannot_resolve_a_transaction():
    """batchStatusCode 00 spans success, failure and processing, so it carries
    no outcome by itself."""
    assert au.describe_enquiry("00") is None
    assert au.to_txn_status("00") is None


def test_each_documented_combination_resolves():
    for row in _table()["enquiry"]:
        got = au.to_txn_status(row["batch_status_code"], row["status_code"])
        assert got in ("SUCCESS", "FAILED", "PENDING"), row


def test_undocumented_combination_resolves_to_nothing():
    assert au.to_txn_status("00", "42") is None
    assert au.to_txn_status("77", "00") is None
    assert au.is_terminal("00", "42") is False


def test_failed_batch_resolves_without_a_transaction_status():
    """batchStatusCode 99 has no statusCode at all — 'NA' is the real value."""
    assert au.to_txn_status("99", "NA") == "FAILED"
    assert au.to_txn_status("99") == "FAILED"          # absent reads as NA
    assert au.is_terminal("99", "NA") is True


# ── PROCESSING must never be re-initiated ────────────────────────────────────

def test_processing_is_pending_and_not_terminal():
    for code in ("100", "101"):
        assert au.to_txn_status("00", code) == "PENDING"
        assert au.is_terminal("00", code) is False


def test_only_a_failed_transaction_may_be_re_initiated():
    """AU's document is explicit that a PROCESSING payment must not be re-sent;
    re-initiating risks paying twice."""
    assert au.may_reinitiate("00", "99") is True        # definitively failed
    assert au.may_reinitiate("99", "NA") is True        # batch failed
    assert au.may_reinitiate("00", "100") is False      # processing
    assert au.may_reinitiate("00", "101") is False      # processing
    assert au.may_reinitiate("00", "00") is False       # already paid
    assert au.may_reinitiate("00", "42") is False       # unknown: never assume


# ── only PAID means credited ────────────────────────────────────────────────

def test_only_paid_is_terminal_among_the_textual_statuses():
    assert au.describe_status("PAID")["terminal"] is True
    for stage in ("OPEN", "TOBEPROCESSED", "SENTTOCORE"):
        assert au.describe_status(stage)["terminal"] is False
        assert au.to_txn_status(None, None, stage) == "PENDING"


def test_textual_status_is_used_when_no_codes_are_available():
    assert au.to_txn_status(None, None, "PAID") == "SUCCESS"
    assert au.is_terminal(None, None, "PAID") is True


# ── robustness ──────────────────────────────────────────────────────────────

def test_unknown_and_missing_input_resolve_to_nothing():
    for args in ((None, None), ("", ""), ("NOPE", "NOPE")):
        assert au.to_txn_status(*args) is None
        assert au.is_terminal(*args) is False


def test_lookup_tolerates_case_and_whitespace():
    assert au.to_txn_status(" 00 ", " 00 ") == "SUCCESS"
    assert au.to_txn_status(None, None, " paid ") == "SUCCESS"


def test_explain_falls_back_to_the_gateways_own_message():
    assert au.explain("00", "42", fallback_message="raw AU text") == "raw AU text"
    assert "not final" in au.explain("00", "100")


# ── the reference data the integration will rely on ─────────────────────────

def test_imps_limits_match_the_bank_document():
    assert au.limits("IMPS") == {"min": 1, "max": 500000, "settlement": "real-time"}


def test_http_errors_mark_only_server_side_codes_retryable():
    for code in ("500", "503", "504"):
        assert au.describe_http(code)["retryable"] is True
    for code in ("400", "401", "404"):
        assert au.describe_http(code)["retryable"] is False
