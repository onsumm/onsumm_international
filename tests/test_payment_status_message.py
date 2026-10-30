"""A status check's message comes from the bank that processed the payment.

The gateway's data.statusMessage ("SUCCESS") is about the enquiry call itself;
AU's answer about the money is nested in TransactionEnquiryResponse. Shown
as-is, a payment still queued at AU read "Processing at the bank · SUCCESS".
"""
from backend_ocr.app.routers import disbursement as db


def au_body(status="TOBEPROCESSED", code="101", batch="00", error=""):
    return {
        "actCode": None,
        "data": {"status": "PENDING", "statusMessage": "SUCCESS"},
        "vendorInfo": {"vendorResponse": {"apiCalls": [{"response": {"TransactionEnquiryResponse": {
            "batchStatus": "SUCCESS", "batchStatusCode": batch,
            "transactionEnquiryDetails": [{"status": status, "statusCode": code, "errorMessage": error}],
        }}}]}},
    }


def test_reads_aus_own_codes():
    assert db._bank_enquiry(au_body()) == {
        "batch_status_code": "00", "status_code": "101", "status": "TOBEPROCESSED", "error_message": ""}


def test_queued_at_au_is_not_called_success():
    msg = db.status_explanation("AUBANK", au_body(), "PENDING", None, "SUCCESS")
    assert "SUCCESS" not in msg.upper().replace("PROCESSING", "")
    assert msg.startswith("PROCESSING") and "not final" in msg


def test_pending_without_bank_detail_says_processing():
    assert db.status_explanation("AUBANK", {"data": {}}, "PENDING", None, "SUCCESS") == "Processing at the bank"
    assert db.status_explanation("ICICI", {"data": {}}, "PENDING", None, "") == "Processing at the bank"


def test_no_enquiry_in_body():
    assert db._bank_enquiry({"vendorInfo": {}}) == {}
