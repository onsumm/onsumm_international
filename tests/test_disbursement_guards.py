"""Guards on the multi-bank disbursement path.

These pin the cases where the failure is a real transfer of money, not an error
message: paying a bill twice, and sending the gateway an amount or batch it will
reject after a tran_ref_no has already been spent on it.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.integrations import payments as bank_payments


# ── per-bank transaction bounds ──────────────────────────────────────────────

@pytest.mark.parametrize("bank,amount,rejected", [
    ("ICICI",  500_001, True),    # ICICI's documented hard cap is Rs 5,00,000
    ("ICICI",  500_000, False),
    ("ICICI",        0.5, True),  # below the Rs 1 minimum
    ("AUBANK", 500_001, True),
    ("AUBANK",      10, False),   # the UAT contract's own test amount
    ("AUBANK",       0.5, True),
])
def test_bank_transaction_bounds(bank, amount, rejected):
    why = bank_payments.for_bank(bank).check_amount(amount)
    assert (why is not None) is rejected, why


def test_a_bank_with_no_documented_cap_enforces_none():
    """HDFC's sheet documents status codes only. Borrowing another bank's
    ceiling would block payments the bank would have accepted."""
    adapter = bank_payments.for_bank("HDFC")
    assert adapter.max_amount is None
    assert adapter.check_amount(90_000_000) is None


def test_au_batch_cap_matches_its_document():
    au = bank_payments.for_bank("AUBANK")
    assert au.bulk_max == 50
    assert au.check_batch(50) is None
    assert au.check_batch(51) is not None


# ── the double-payment guard ────────────────────────────────────────────────

class _Cur:
    """Minimal cursor stub: the guard issues one SUM query and reads the row."""

    def __init__(self, inflight: float, n: int = 1):
        self._row = {"inflight": inflight, "n": n}

    def execute(self, *_a, **_k):
        return None

    def fetchone(self):
        return self._row


def _guard(inflight, amount, outstanding, n=1):
    from backend_ocr.app.routers.disbursement import _assert_not_already_in_flight
    return _assert_not_already_in_flight(_Cur(inflight, n), "t", "inv", amount, outstanding)


def test_nothing_in_flight_is_allowed():
    assert _guard(0, 10_000, 10_000) is None


def test_second_full_payment_while_one_is_in_flight_is_refused():
    """The case that pays a vendor twice: outstanding_amount is not reduced
    until the bank confirms, so without this the bill still reads as fully
    payable while a payment for it is mid-flight."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _guard(10_000, 10_000, 10_000)
    assert e.value.status_code == 409
    assert "in flight" in e.value.detail


def test_part_payments_that_still_fit_are_allowed():
    """Amount-based, not one-at-a-time — splitting a bill is legitimate."""
    assert _guard(4_000, 6_000, 10_000) is None


def test_part_payments_that_would_overpay_are_refused():
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _guard(4_000, 6_001, 10_000)


# ── bank selection cannot be forged ─────────────────────────────────────────

def test_an_unsupported_bank_is_not_silently_accepted():
    assert bank_payments.normalise("BARCLAYS") is None
    assert bank_payments.normalise("'; DROP TABLE--") is None


def test_an_unknown_bank_falls_back_rather_than_crashing():
    """fact_disbursements.bank_provider defaults to ICICI in the schema, so an
    unrecognised stored value is legacy data, not a new bank."""
    assert bank_payments.for_bank("whatever").code == bank_payments.DEFAULT_BANK


def test_gateway_profile_cannot_be_steered_by_a_crafted_bank_name(monkeypatch):
    """The bank code becomes part of an env var name, so it is stripped to
    alphanumerics before the lookup — otherwise a crafted value could reach
    os.getenv as-is and select a different bank's credentials."""
    from backend_ocr.app.config import gateway_profile
    monkeypatch.setenv("DISBURSEMENT_TESTBANK_NBFC_NAME", "PICKED")

    assert gateway_profile("TESTBANK")["nbfc_name"] == "PICKED"
    # punctuation and spacing are stripped, so these name the same bank
    for crafted in ("test-bank", "TEST BANK", "../../TESTBANK", "TEST.BANK"):
        assert gateway_profile(crafted)["nbfc_name"] == "PICKED", crafted
    # a genuinely different bank does not pick it up
    assert gateway_profile("OTHERBANK")["nbfc_name"] != "PICKED"


# ── tranRefNo is sent at most once ──────────────────────────────────────────

def test_a_sent_ref_is_never_sent_again(monkeypatch):
    """AU requires tranRefNo unique per transaction. The tempting fix — mint a
    fresh ref per attempt — would turn AU's duplicate rejection (a protection)
    into a second transfer when the first outcome was merely unknown. The ref
    stays fixed per payment; this stops it going out twice."""
    from backend_ocr.app.routers import disbursement as dsb

    sent = {"called": False}

    def _fake_pay(**_k):
        sent["called"] = True
        return {"success": True, "data": {}}

    monkeypatch.setattr(dsb, "_already_sent_to_gateway", lambda _id: True)
    monkeypatch.setattr(dsb.imps_client, "vendor_payment_pay", _fake_pay)

    res = dsb._real_imps_initiate(
        None,
        {"disbursement_id": "d1", "tran_ref_no": "REF1", "beneficiary_name": "V",
         "remitter_account_number": "260220", "beneficiary_ifsc": "sbin0001470",
         "beneficiary_account": "1", "beneficiary_mobile": "9", "amount": 10,
         "invoice_id": "i1", "bank_provider": "AUBANK", "tenant_id": "t"},
        None, None,
    )
    assert sent["called"] is False, "the bank must not be called a second time"
    assert res["success"] is False
    # PENDING, not FAILED: the first attempt's outcome is unknown, not known-bad.
    # FAILED would leave the bill payable and invite the double transfer.
    assert res["data"]["status"] == "PENDING"


def test_an_unsent_payment_still_goes_through(monkeypatch):
    from backend_ocr.app.routers import disbursement as dsb

    monkeypatch.setattr(dsb, "_already_sent_to_gateway", lambda _id: False)
    monkeypatch.setattr(dsb, "_log_gateway_call", lambda *a, **k: None)
    monkeypatch.setattr(dsb.imps_client, "vendor_payment_pay",
                        lambda **k: {"success": True, "data": {"status": "SUCCESS",
                                                               "tranRefNo": k["tran_ref_no"]},
                                     "actCode": "0"})
    res = dsb._real_imps_initiate(
        None,
        {"disbursement_id": "d2", "tran_ref_no": "REF2", "beneficiary_name": "V",
         "remitter_account_number": "260220", "beneficiary_ifsc": "sbin0001470",
         "beneficiary_account": "1", "beneficiary_mobile": "9", "amount": 10,
         "invoice_id": "i1", "bank_provider": "AUBANK", "tenant_id": "t"},
        None, None,
    )
    assert res["success"] is True
    assert res["data"]["status"] == "SUCCESS"


def test_generated_refs_are_unique_and_fit_the_banks_field():
    """AU and ICICI both cap tranRefNo at 20 characters."""
    from backend_ocr.app.routers.disbursement import _gen_tran_ref_no
    refs = [_gen_tran_ref_no() for _ in range(500)]
    assert len(set(refs)) == 500
    assert all(len(r) <= 20 for r in refs)


# ── gateway statuses are normalised before they are stored ──────────────────

@pytest.mark.parametrize("bank,raw,act,expected", [
    # AU's enquiry does not speak SUCCESS/PENDING/FAILED. Stored verbatim these
    # read as "not in flight" to the double-payment guard.
    ("AUBANK", "PAID",          None, "SUCCESS"),
    ("AUBANK", "SENTTOCORE",    None, "PENDING"),
    ("AUBANK", "TOBEPROCESSED", None, "PENDING"),
    ("AUBANK", "OPEN",          None, "PENDING"),
    ("AUBANK", "PROCESSING",    None, "PENDING"),
    ("ICICI",  "SUCCESS",       None, "SUCCESS"),
    ("ICICI",  "FAILED",        None, "FAILED"),
    ("ICICI",  None,            "51", "FAILED"),
    ("ICICI",  None,            "11", "PENDING"),   # deemed approved, not settled
    ("HDFC",   "TXCOMP",        None, "PENDING"),   # named "completed", is interim
])
def test_gateway_status_is_normalised(bank, raw, act, expected):
    from backend_ocr.app.routers.disbursement import _canonical_status
    assert _canonical_status(bank, raw, act) == expected


def test_an_unrecognised_status_is_pending_never_success():
    """'We cannot tell' must leave the payment open to a status check, not
    record money as delivered."""
    from backend_ocr.app.routers.disbursement import _canonical_status
    for raw in ("WHO_KNOWS", "", None, "weird-new-state"):
        assert _canonical_status("AUBANK", raw) == "PENDING"


def test_only_success_and_failed_count_as_settled():
    """The in-flight guard inverts this set, so anything missing from it — a
    status a gateway invents included — is treated as still moving."""
    from backend_ocr.app.routers.disbursement import _TERMINAL_STATUSES
    assert set(_TERMINAL_STATUSES) == {"SUCCESS", "FAILED"}


# ── the stored message cannot contradict the stored status ──────────────────
# AU answers an enquiry at two levels: the batch was accepted ("SUCCESS")
# while the transaction inside it is still SENTTOCORE. Lendix flattens the
# batch word into statusMessage, so the receipt showed "Processing at the
# bank" with "SUCCESS" printed directly underneath it.

@pytest.mark.parametrize("canonical,raw,expected", [
    ("PENDING", "SUCCESS",   "Processing at the bank — not yet confirmed"),
    ("PENDING", "success",   "Processing at the bank — not yet confirmed"),
    ("PENDING", "PAID",      "Processing at the bank — not yet confirmed"),
    ("PENDING", "NA",        "Processing at the bank — not yet confirmed"),
    ("FAILED",  "SUCCESS",   "Transaction failed"),
    ("SUCCESS", "PENDING",   "Transaction successful"),
    ("PENDING", "",          "Processing at the bank — not yet confirmed"),
    ("PENDING", None,        "Processing at the bank — not yet confirmed"),
])
def test_a_bare_outcome_word_never_contradicts_the_status(canonical, raw, expected):
    from backend_ocr.app.routers.disbursement import _display_message
    assert _display_message(canonical, raw) == expected


@pytest.mark.parametrize("canonical,raw", [
    ("FAILED",  "Insufficient balance in remitter account"),
    ("FAILED",  "Beneficiary account closed"),
    ("PENDING", "Sent to core banking"),
    ("SUCCESS", "Transaction Successful"),
    ("PENDING", "Interrupted after the payment was handed to the bank; "
                "its outcome is unknown. Check status before any retry."),
])
def test_a_message_with_real_content_is_kept_verbatim(canonical, raw):
    """The bank's own explanation is the most useful line on the card — the
    guard must only strip words that say nothing the status does not."""
    from backend_ocr.app.routers.disbursement import _display_message
    assert _display_message(canonical, raw) == raw


def test_a_bare_word_that_agrees_with_the_status_is_left_alone():
    """"ACCEPTED" is what AU's pay response says while the transfer is in
    flight — it agrees with PENDING and is more informative than a stock
    phrase, so replacing it would lose something."""
    from backend_ocr.app.routers.disbursement import _display_message
    assert _display_message("SUCCESS", "SUCCESS") == "SUCCESS"
    assert _display_message("FAILED", "FAILED") == "FAILED"
    assert _display_message("PENDING", "ACCEPTED") == "ACCEPTED"
    assert _display_message("PENDING", "PROCESSING") == "PROCESSING"


# ── the demo badge describes the payment path, not a constant ───────────────

def test_the_demo_badge_is_not_hardcoded():
    """Hardcoded True, the badge would keep promising "no real funds move"
    unchanged on the day the base URL is switched to production."""
    import backend_ocr.app.routers.disbursement as dsb
    import inspect
    src = inspect.getsource(dsb.disbursement_stats)
    assert '"demo": is_demo_gateway(' in src
    assert '"demo": True' not in src


@pytest.mark.parametrize("url", [
    "https://api-dev.lendix.ai",
    "https://api.aubankuat.in/CNBPaymentService/paymentCreation",
    "https://sandbox.example-bank.com",
    "https://staging.lendix.ai",
    "http://localhost:9000",
])
def test_a_non_production_gateway_earns_the_demo_badge(url, monkeypatch):
    from backend_ocr.app import config
    monkeypatch.setattr(config, "DISBURSEMENT_FORCE_SUCCESS", False)
    assert config.is_demo_gateway(url) is True


@pytest.mark.parametrize("url", [
    "https://api.lendix.ai",
    "https://api.aubank.in/CNBPaymentService/paymentCreation",
    "",
    "https://some-gateway-we-cannot-place.example",
])
def test_anything_we_cannot_place_is_treated_as_production(url, monkeypatch):
    """Wrongly promising "no real funds move" over a live rail is the costly
    direction, so an endpoint with no non-production marker gets no badge."""
    from backend_ocr.app import config
    monkeypatch.setattr(config, "DISBURSEMENT_FORCE_SUCCESS", False)
    monkeypatch.setattr(config, "DISBURSEMENT_BASE_URL", "https://api.lendix.ai")
    assert config.is_demo_gateway(url) is False


def test_forced_success_is_a_demo_whatever_the_url(monkeypatch):
    from backend_ocr.app import config
    monkeypatch.setattr(config, "DISBURSEMENT_FORCE_SUCCESS", True)
    assert config.is_demo_gateway("https://api.lendix.ai") is True


# ── "This account so far" counts that account, not every account ────────────

def test_stats_scope_the_tally_to_the_chosen_bank():
    """The card is headed "This account so far" and sits under one bank's
    account and balance. Counting the whole tenant showed AU "29 payments,
    25 failed" when 23 of those were ICICI's and HDFC's."""
    import inspect
    import backend_ocr.app.routers.disbursement as dsb
    src = inspect.getsource(dsb.disbursement_stats)
    assert 'where += " AND bank_provider=%s"' in src
    # and the aggregate must use that predicate, not a bare tenant filter
    assert "FROM fact_disbursements WHERE {where}" in src
    assert "FROM fact_disbursements WHERE tenant_id=%s" not in src
