"""POST /api/vendors/{id}/documents/{doc_id}/claims/{index}/approve —
backend_ocr/app/routers/vendor_documents.py::approve_document_claim.

A human approving one extracted claim directly from the document panel,
independent of whether master_data_conflict.py's own periodic sweep has
already turned it into a Review Hub finding. Reuses that adapter's pure
validation (`_resolve_single_valid_claim` / `_resolve_bank_claim`) and calls
the same real endpoints (update_vendor, add_supplier_gstin,
add_vendor_bank_account, add_vendor_contact) a finding's `keep_new` action
would — this suite checks the right one gets called with the right body, and
that nothing is written when validation fails.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_ocr.app.routers import vendor_documents as vd

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
DOC = "55555555-0000-0000-0000-000000000001"
EXTRACTION = "44444444-0000-0000-0000-000000000001"

VENDOR_ROW = {"name": "Acme", "pan": None, "gstin": None, "payment_terms_days": None}


class FakeCursor:
    """Replays one `fetchone`/`fetchall`-able result per `execute()` call, in
    the order the endpoint under test makes them — SELECTs and the trailing
    UPDATE alike (the UPDATE's "result" is never read, but still consumes a
    slot so the list stays call-for-call with the endpoint)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self._last = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last = self._results.pop(0) if self._results else None

    def fetchone(self):
        return self._last[0] if self._last else None

    def fetchall(self):
        return self._last or []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cur

    def commit(self):
        self.committed = True


def _extraction_row(claims):
    return {"extraction_id": EXTRACTION, "claims": claims}


def _pan_claim(**over):
    base = {"scope": "vendor_pan", "field": "pan", "new_value": "aabcu9603r",
            "confidence": 0.9, "evidence_quote": "PAN: AABCU9603R", "status": "PENDING"}
    base.update(over)
    return base


def _run(coro):
    return asyncio.run(coro)


def _patched(cur, **write_mocks):
    """Context manager stack patching get_conn/put_conn/require_tenant and
    whichever real write functions the scope under test would call."""
    conn = FakeConn(cur)
    patches = [
        patch.object(vd, "get_conn", return_value=conn),
        patch.object(vd, "put_conn", MagicMock()),
        patch.object(vd, "require_tenant", return_value=TENANT),
    ]
    for name, mock in write_mocks.items():
        patches.append(patch.object(vd, name, mock))
    return conn, patches


def _enter_all(patches):
    for p in patches:
        p.start()


def _exit_all(patches):
    for p in patches:
        p.stop()


# ── vendor_pan ────────────────────────────────────────────────────────────

def test_pan_with_none_on_file_writes_and_marks_applied():
    claims = [_pan_claim()]
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW], None])  # extraction, vendor snapshot, UPDATE
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    update_vendor.assert_called_once()
    args, _ = update_vendor.call_args
    assert args[0] == VENDOR
    assert args[1].pan == "AABCU9603R"
    written = json.loads(cur.calls[-1][1][0])
    assert written[0]["status"] == "APPLIED"
    assert conn.committed


def test_pan_matching_existing_value_is_a_noop():
    claims = [_pan_claim(new_value="AABCU9603R")]
    vendor = {**VENDOR_ROW, "pan": "aabcu 9603r"}
    cur = FakeCursor([[_extraction_row(claims)], [vendor], None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    update_vendor.assert_not_called()


def test_malformed_pan_fails_without_writing():
    claims = [_pan_claim(new_value="not-a-pan")]
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW], None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "FAILED"
    update_vendor.assert_not_called()


def test_a_write_that_raises_marks_the_claim_failed_not_a_500():
    from fastapi import HTTPException
    claims = [_pan_claim()]
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW], None])
    update_vendor = MagicMock(side_effect=HTTPException(status_code=409, detail="conflict"))
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "FAILED"
    assert "conflict" in out["message"]


def test_already_resolved_claim_short_circuits():
    claims = [_pan_claim(status="APPLIED")]
    cur = FakeCursor([[_extraction_row(claims)]])  # only the extraction lookup
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    assert len(cur.calls) == 1  # never reached the vendor snapshot or a write


# ── vendor_bank ───────────────────────────────────────────────────────────

def _bank_claims(**over):
    account = {"scope": "vendor_bank", "field": "account_number", "new_value": "1234567890",
               "confidence": 0.9, "evidence_quote": "a/c 1234567890", "status": "PENDING"}
    ifsc = {"scope": "vendor_bank", "field": "ifsc", "new_value": "HDFC0001234",
            "confidence": 0.9, "evidence_quote": "IFSC HDFC0001234", "status": "PENDING"}
    account.update(over)
    return [account, ifsc]


def test_new_bank_account_writes_via_the_real_endpoint_and_carries_the_sibling():
    claims = _bank_claims()
    cur = FakeCursor([
        [_extraction_row(claims)],   # extraction
        [VENDOR_ROW],                 # vendor snapshot
        [],                            # existing bank accounts (none)
        None,                          # UPDATE
    ])
    add_bank = AsyncMock(return_value={"id": "acct-1"})
    conn, patches = _patched(cur, add_vendor_bank_account=add_bank)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    add_bank.assert_awaited_once()
    args, _ = add_bank.call_args
    assert args[0] == VENDOR
    assert args[1].account_number == "1234567890"
    assert args[1].ifsc == "HDFC0001234"
    written = json.loads(cur.calls[-1][1][0])
    assert all(c["status"] == "APPLIED" for c in written)  # sibling ifsc claim rides along


def test_an_account_already_on_file_is_a_noop():
    claims = _bank_claims()
    cur = FakeCursor([
        [_extraction_row(claims)],
        [VENDOR_ROW],
        [{"account_number": "1234567890"}],  # already on file
        None,
    ])
    add_bank = AsyncMock()
    conn, patches = _patched(cur, add_vendor_bank_account=add_bank)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    add_bank.assert_not_awaited()


def test_approving_a_sibling_field_directly_is_rejected():
    claims = _bank_claims()
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW]])
    add_bank = AsyncMock()
    conn, patches = _patched(cur, add_vendor_bank_account=add_bank)
    _enter_all(patches)
    try:
        with pytest.raises(Exception) as exc_info:
            _run(vd.approve_document_claim(VENDOR, DOC, 1))  # index 1 = the ifsc sibling
    finally:
        _exit_all(patches)
    assert "sibling" in str(exc_info.value.detail).lower()
    add_bank.assert_not_awaited()


# ── vendor_contact ────────────────────────────────────────────────────────

def _contact_claims(**over):
    email = {"scope": "vendor_contact", "field": "email", "new_value": "poc@vendor.com",
             "confidence": 0.9, "evidence_quote": "poc@vendor.com", "status": "PENDING"}
    name = {"scope": "vendor_contact", "field": "name", "new_value": "Jane Doe",
            "confidence": 0.9, "evidence_quote": "Jane Doe", "status": "PENDING"}
    email.update(over)
    return [email, name]


def test_new_contact_is_added_via_the_real_endpoint():
    claims = _contact_claims()
    cur = FakeCursor([
        [_extraction_row(claims)],
        [VENDOR_ROW],
        [],  # no existing contacts
        None,
    ])
    add_contact = AsyncMock(return_value={"id": "c-1"})
    conn, patches = _patched(cur, add_vendor_contact=add_contact)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    add_contact.assert_awaited_once()
    args, _ = add_contact.call_args
    assert args[1].email == "poc@vendor.com"
    assert args[1].name == "Jane Doe"
    written = json.loads(cur.calls[-1][1][0])
    assert all(c["status"] == "APPLIED" for c in written)


def test_contact_already_in_supplier_contacts_is_a_noop():
    claims = _contact_claims()
    cur = FakeCursor([
        [_extraction_row(claims)],
        [VENDOR_ROW],
        [{"email": "poc@vendor.com"}],
        None,
    ])
    add_contact = AsyncMock()
    conn, patches = _patched(cur, add_vendor_contact=add_contact)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    add_contact.assert_not_awaited()


# ── approve-all: the single button for the whole panel ──────────────────────

def test_approve_all_applies_every_eligible_claim_in_one_call():
    """A PAN and a payment term, both pending — one approve-all call should
    resolve both and skip nothing."""
    claims = [
        _pan_claim(),
        {"scope": "payment_term", "field": "term", "new_value": "45", "unit": "days",
         "confidence": 0.9, "evidence_quote": "net 45", "status": "PENDING"},
    ]
    cur = FakeCursor([
        [_extraction_row(claims)],  # extraction
        [VENDOR_ROW],                # vendor snapshot
        [VENDOR_ROW],                # re-read after the PAN write
        [VENDOR_ROW],                # re-read after the payment-term write
        None,                         # UPDATE
    ])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_all_document_claims(VENDOR, DOC))
    finally:
        _exit_all(patches)

    assert out == {"applied": 2, "failed": 0, "deferred": 0}
    assert update_vendor.call_count == 2
    written = json.loads(cur.calls[-1][1][0])
    assert all(c["status"] == "APPLIED" for c in written)


def test_approve_all_resolves_pan_before_gstin():
    """POST /gstins refuses a GSTIN whose embedded PAN doesn't match the
    vendor's, so a matching PAN + GSTIN pair has to apply PAN-first to land in
    one click — regardless of the order the extraction happened to emit them."""
    claims = [
        {"scope": "vendor_gstin", "field": "gstin", "new_value": "09BAWPP4406L1ZY",
         "confidence": 0.9, "evidence_quote": "GSTIN/UIN: 09BAWPP4406L1ZY", "status": "PENDING"},
        {"scope": "vendor_pan", "field": "pan", "new_value": "BAWPP4406L",
         "confidence": 0.9, "evidence_quote": "PAN: BAWPP4406L", "status": "PENDING"},
    ]
    cur = FakeCursor([
        [_extraction_row(claims)],
        [VENDOR_ROW],                                  # nothing on file yet
        [{**VENDOR_ROW, "pan": "BAWPP4406L"}],         # re-read after the PAN write
        [{**VENDOR_ROW, "pan": "BAWPP4406L"}],         # re-read after the GSTIN write
        None,
    ])
    order: list[str] = []
    update_vendor = MagicMock(side_effect=lambda *a, **k: order.append('pan'))
    add_gstin = MagicMock(side_effect=lambda *a, **k: order.append('gstin'))
    conn, patches = _patched(cur, update_vendor=update_vendor, add_supplier_gstin=add_gstin)
    _enter_all(patches)
    try:
        out = _run(vd.approve_all_document_claims(VENDOR, DOC))
    finally:
        _exit_all(patches)

    assert out == {"applied": 2, "failed": 0, "deferred": 0}
    assert order == ['pan', 'gstin']  # dependency order, not extraction order


def test_approve_all_skips_sibling_claims_and_already_resolved_ones():
    claims = _bank_claims()  # account_number (anchor) + ifsc (sibling)
    claims.append(_pan_claim(status="APPLIED"))  # already resolved
    cur = FakeCursor([
        [_extraction_row(claims)],
        [VENDOR_ROW],
        [],            # existing bank accounts
        [VENDOR_ROW],  # re-read after the bank write
        None,
    ])
    add_bank = AsyncMock(return_value={"id": "acct-1"})
    conn, patches = _patched(cur, add_vendor_bank_account=add_bank)
    _enter_all(patches)
    try:
        out = _run(vd.approve_all_document_claims(VENDOR, DOC))
    finally:
        _exit_all(patches)

    assert out == {"applied": 1, "failed": 0, "deferred": 0}  # only the bank anchor counted
    add_bank.assert_awaited_once()


def test_approve_all_counts_a_failed_claim_without_stopping_the_rest():
    claims = [
        _pan_claim(new_value="not-a-pan"),
        {"scope": "payment_term", "field": "term", "new_value": "45", "unit": "days",
         "confidence": 0.9, "evidence_quote": "net 45", "status": "PENDING"},
    ]
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW], [VENDOR_ROW], None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_all_document_claims(VENDOR, DOC))
    finally:
        _exit_all(patches)

    assert out == {"applied": 1, "failed": 1, "deferred": 0}
    update_vendor.assert_called_once()  # only for the valid payment-term claim


# ── a genuine conflict is never silently overwritten from here ──────────────

def test_a_pan_that_differs_from_the_one_on_file_is_deferred_not_written():
    """The failure this exists for: a contract whose PAN belongs to a
    different legal entity than the vendor record it's filed against. One
    click on a panel-wide Approve must not replace an existing PAN — that
    decision belongs in the Review Hub, with its before/after diff."""
    claims = [_pan_claim(new_value="BAWPP4406L")]
    vendor = {**VENDOR_ROW, "pan": "ACJFA3124E"}  # a different entity entirely
    cur = FakeCursor([[_extraction_row(claims)], [vendor], None, None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_all_document_claims(VENDOR, DOC))
    finally:
        _exit_all(patches)

    assert out == {"applied": 0, "failed": 0, "deferred": 1}
    update_vendor.assert_not_called()  # the existing PAN survives
    written = json.loads(cur.calls[-1][1][0])
    assert written[0]["status"] == "CONFLICTED"
    assert "ACJFA3124E" in written[0]["error"]
    assert "Review Hub" in written[0]["error"]
    # The hub only helps if the finding is actually there — nudge the sweep.
    assert any("pg_notify" in sql and "ch_review_adapters" in sql for sql, _ in cur.calls)


def test_a_single_claim_approve_also_defers_a_genuine_conflict():
    claims = [_pan_claim(new_value="BAWPP4406L")]
    vendor = {**VENDOR_ROW, "pan": "ACJFA3124E"}
    cur = FakeCursor([[_extraction_row(claims)], [vendor], None, None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "CONFLICTED"
    assert "Review Hub" in out["message"]
    update_vendor.assert_not_called()
    # Stamped so master_data_conflict.py raises the finding as VERIFIED — an
    # UNVERIFIED one is routed to 'suspected' and never reaches the compliance
    # lane a vendor-identity conflict belongs in.
    written = json.loads(cur.calls[-1][1][0])
    assert written[0]["verified_by"]


def test_keep_the_contracts_value_overrides_the_conflict_and_writes():
    """The primary half of the either/or: the user has read both values and
    chosen the contract's, so the write goes through rather than deferring."""
    claims = [_pan_claim(new_value="BAWPP4406L")]
    vendor = {**VENDOR_ROW, "pan": "ACJFA3124E"}
    cur = FakeCursor([[_extraction_row(claims)], [vendor], None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0, override=True))
    finally:
        _exit_all(patches)

    assert out["status"] == "APPLIED"
    update_vendor.assert_called_once()
    assert update_vendor.call_args[0][1].pan == "BAWPP4406L"


def test_keep_current_discards_the_claim_and_leaves_the_vendor_alone():
    claims = [_pan_claim(new_value="BAWPP4406L", status="CONFLICTED")]
    cur = FakeCursor([[_extraction_row(claims)], None, None])
    update_vendor = MagicMock()
    conn, patches = _patched(cur, update_vendor=update_vendor)
    _enter_all(patches)
    try:
        out = vd.discard_document_claim(VENDOR, DOC, 0)
    finally:
        _exit_all(patches)

    assert out["status"] == "DISCARDED"
    update_vendor.assert_not_called()
    written = json.loads(cur.calls[-2][1][0])
    assert written[0]["status"] == "DISCARDED"
    assert written[0]["decided_by"]
    # DISCARDED is terminal, so the adapter stops asserting it and the sweep
    # closes the finding — nudged rather than waited for.
    assert any("pg_notify" in sql and "ch_review_adapters" in sql for sql, _ in cur.calls)


def test_keep_current_carries_sibling_claims_with_it():
    claims = _bank_claims()  # account_number anchor + ifsc sibling
    cur = FakeCursor([[_extraction_row(claims)], None, None])
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        vd.discard_document_claim(VENDOR, DOC, 0)
    finally:
        _exit_all(patches)

    written = json.loads(cur.calls[-2][1][0])
    assert all(c["status"] == "DISCARDED" for c in written)


def test_keep_current_refuses_on_a_claim_already_written():
    from fastapi import HTTPException
    claims = [_pan_claim(status="APPLIED")]
    cur = FakeCursor([[_extraction_row(claims)]])
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        with pytest.raises(HTTPException) as exc_info:
            vd.discard_document_claim(VENDOR, DOC, 0)
    finally:
        _exit_all(patches)
    assert exc_info.value.status_code == 400


def test_a_write_rejected_by_its_endpoint_keeps_the_real_reason():
    """A GSTIN can read perfectly and still be refused (its PAN doesn't match
    the vendor's). That must not be reported as an unreadable value."""
    from fastapi import HTTPException
    claims = [{"scope": "vendor_gstin", "field": "gstin", "new_value": "09BAWPP4406L1ZY",
               "confidence": 0.9, "evidence_quote": "GSTIN/UIN: 09BAWPP4406L1ZY", "status": "PENDING"}]
    cur = FakeCursor([[_extraction_row(claims)], [VENDOR_ROW], None])
    add_gstin = MagicMock(side_effect=HTTPException(status_code=409, detail="PAN mismatch: new GSTIN has PAN BAWPP4406L but vendor PAN is ACJFA3124E."))
    conn, patches = _patched(cur, add_supplier_gstin=add_gstin)
    _enter_all(patches)
    try:
        out = _run(vd.approve_document_claim(VENDOR, DOC, 0))
    finally:
        _exit_all(patches)

    assert out["status"] == "FAILED"
    assert "PAN mismatch" in out["message"]
    written = json.loads(cur.calls[-1][1][0])
    assert "PAN mismatch" in written[0]["error"]


# ── editing an extracted value before approving ─────────────────────────────

def test_editing_a_claim_updates_its_value_and_resets_it_to_pending():
    claims = [_pan_claim(new_value="not-a-pan", status="FAILED")]
    cur = FakeCursor([[_extraction_row(claims)], None])  # extraction, UPDATE
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        out = vd.edit_document_claim(VENDOR, DOC, 0, vd.ClaimEditRequest(new_value="AABCU9603R"))
    finally:
        _exit_all(patches)

    assert out["status"] == "PENDING"
    written = json.loads(cur.calls[-1][1][0])
    assert written[0]["new_value"] == "AABCU9603R"
    assert written[0]["status"] == "PENDING"


def test_editing_an_already_applied_claim_is_rejected():
    from fastapi import HTTPException
    claims = [_pan_claim(status="APPLIED")]
    cur = FakeCursor([[_extraction_row(claims)]])
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        with pytest.raises(HTTPException) as exc_info:
            vd.edit_document_claim(VENDOR, DOC, 0, vd.ClaimEditRequest(new_value="AABCU9603R"))
    finally:
        _exit_all(patches)
    assert exc_info.value.status_code == 400


def test_editing_to_an_empty_value_is_rejected():
    from fastapi import HTTPException
    claims = [_pan_claim()]
    cur = FakeCursor([[_extraction_row(claims)]])
    conn, patches = _patched(cur)
    _enter_all(patches)
    try:
        with pytest.raises(HTTPException):
            vd.edit_document_claim(VENDOR, DOC, 0, vd.ClaimEditRequest(new_value="   "))
    finally:
        _exit_all(patches)
