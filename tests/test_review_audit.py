"""The Review Hub audit trail: what counts as a change, what it is about, and
what is never stored."""
from __future__ import annotations

from backend_ocr.app.review import audit


def test_vendor_and_bill_changes_are_tied_to_their_record():
    v = "04a74f0a-5ddd-4e22-ae33-ad7aaae8f453"
    assert audit._entity(f"/api/vendors/{v}", {}) == ("vendor", v)
    assert audit._entity(f"/api/vendors/{v}/contacts", {}) == ("vendor", v)
    b = "27f582fb-5c17-470f-a4d0-167eb307a632"
    assert audit._entity(f"/api/bills/{b}/action", {}) == ("bill", b)
    # Bill corrections name the bill in the body, not the path.
    assert audit._entity("/api/review/bill-coding/apply", {"invoice_id": b}) == ("bill", b)


def test_secrets_are_masked_and_uploads_summarised():
    body = audit._parse_body(b'{"otp": "123456", "pan": "AALCM5200G", "nested": {"password": "x"}}',
                             "application/json")
    assert body == {"otp": "***", "pan": "AALCM5200G", "nested": {"password": "***"}}
    assert audit._parse_body(b"%PDF-1.7 ...", "multipart/form-data; boundary=x") == {
        "_content_type": "multipart/form-data", "_bytes": 12}


def test_only_fields_that_changed_are_recorded():
    before = {"pan": "AALCM5200G", "name": "Acme", "email": None}
    after = {"pan": "BAWPP4406L", "name": "Acme", "email": None}
    assert audit._diff(before, after) == {"pan": ["AALCM5200G", "BAWPP4406L"]}
    assert audit._diff(before, before) == {}
    assert audit._diff(None, after) == {}


def test_a_finding_id_is_only_kept_when_it_is_one():
    fid = "e2da4cd8-ae82-426a-9a73-11476bf1331d"
    assert audit._finding_id(fid, {}) == fid
    assert audit._finding_id(None, {"finding_id": fid}) == fid
    assert audit._finding_id("not-a-uuid'; drop table", {}) is None
