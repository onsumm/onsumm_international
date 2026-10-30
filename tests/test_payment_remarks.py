"""Payment remarks: the gateway rejects more than 40 characters, and the remark
lands on the vendor's bank statement, so only letters, digits and spaces go."""
from backend_ocr.app.integrations import imps_client as ic


def test_the_failing_remark_now_fits():
    old = "Vendor payment - bill 3e29d71f-415d-46a8-9b6e-0c1d2e3f4a5b"
    assert len(old) > 40
    out = ic.clean_remarks(old)
    assert len(out) <= 40 and out.startswith("Vendor payment bill 3e29d71f")


def test_reference_remark_is_kept_whole():
    assert ic.clean_remarks("Vendor payment PAY3E29D71F415D46") == "Vendor payment PAY3E29D71F415D46"


def test_punctuation_and_spacing():
    assert ic.clean_remarks("Bill OTL/008/2029-27  (Oct)") == "Bill OTL 008 2029 27 Oct"


def test_empty_gets_a_default():
    assert ic.clean_remarks("") == "Vendor payment"
    assert ic.clean_remarks(None) == "Vendor payment"


def test_payloads_carry_clean_remarks(monkeypatch):
    sent = {}
    monkeypatch.setattr(ic, "_post", lambda path, payload: sent.setdefault(path, payload) or {})
    common = dict(lead_id="l", tran_ref_no="PAY1", amount=9.0, beneficiary_account="1", beneficiary_ifsc="X",
                  beneficiary_name="N", beneficiary_mobile="9", remitter_account_number="R", bank_provider="AUBANK",
                  payment_remarks="x" * 80)
    ic.vendor_payment_pay(**common)
    ic.imps_initiate(**common)
    assert all(len(p["paymentRemarks"]) == 40 for p in sent.values())
