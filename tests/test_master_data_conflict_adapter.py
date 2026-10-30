"""backend_ocr/app/review/adapters/master_data_conflict.py — comm-extracted claims vs.
the vendor master.

Nothing here writes on its own: every actionable claim — a new bank account,
an empty or conflicting PAN/GSTIN/payment term, a POC not already in
contacts — becomes a Review Hub finding with a `keep_new` action an admin has
to click. The only claims that resolve without a finding are genuine no-ops:
a value that already matches what's on file, or a bank account/contact email
already present.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters.master_data_conflict import MasterDataConflictAdapter
from backend_ocr.app.review.types import EVIDENCE_KINDS, FAMILIES, SUBJECT_TYPES

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
EXTRACTION = "44444444-0000-0000-0000-000000000001"
DOC = "55555555-0000-0000-0000-000000000001"


def _conn(fetch_side_effects, fetchrow_return=None):
    c = AsyncMock()
    c.fetch = AsyncMock(side_effect=fetch_side_effects)
    c.fetchrow = AsyncMock(return_value=fetchrow_return)
    c.execute = AsyncMock()
    return c


def _extraction_row(claims, supplier_id=VENDOR, source_type="email", contact_id=None):
    return {
        "extraction_id": EXTRACTION, "supplier_id": supplier_id,
        "contact_id": contact_id, "thread_id": "t-1", "source_type": source_type,
        "source_id": DOC, "claims": claims,
    }


def _bank_claim(**over):
    base = {"scope": "vendor_bank", "field": "account_number",
            "new_value": "1234567890", "confidence": 0.9,
            "evidence_quote": "please pay into 1234567890", "status": "PENDING"}
    base.update(over)
    return base


async def _drain(conn, limit=100):
    return [d async for d in MasterDataConflictAdapter().scan(conn, TENANT, limit)]


def _executed_sql(conn, needle):
    return [c for c in conn.execute.call_args_list if needle in c.args[0]]


def _written_claims(conn):
    calls = _executed_sql(conn, "comm_extractions")
    assert len(calls) == 1, "exactly one write-back per dirty extraction"
    return json.loads(calls[0].args[2])


VENDOR_ROW = {"name": "Acme Traders", "pan": None, "gstin": None, "payment_terms_days": None}


def test_declares_a_real_service_and_family():
    a = MasterDataConflictAdapter()
    from backend_ocr.app.security.services import SERVICE_KEYS
    assert a.source_service in SERVICE_KEYS
    assert a.family in FAMILIES


# ── vendor_bank: multi-valid, but a new account now needs a click ──────────

@pytest.mark.asyncio
async def test_a_brand_new_bank_account_becomes_a_finding_and_writes_nothing():
    row = _extraction_row([_bank_claim()])
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)  # [] = no existing accounts

    out = await _drain(conn)

    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "vendor_bank_new"
    assert f.severity == "HIGH"
    assert _executed_sql(conn, "supplier_bank_accounts") == []  # never writes on read alone
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


@pytest.mark.asyncio
async def test_new_bank_account_finding_offers_keep_new_via_the_real_endpoint():
    row = _extraction_row([_bank_claim(new_value="1234567890")])
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    keep_new = next(a for a in out[0].actions if a["id"] == "keep_new")
    assert keep_new["kind"] == "safe"
    assert keep_new["endpoint"] == f"/api/vendors/{VENDOR}/bank-accounts"
    assert keep_new["method"] == "POST"
    assert keep_new["body"]["account_number"] == "1234567890"


@pytest.mark.asyncio
async def test_an_account_that_already_exists_is_a_silent_no_op():
    row = _extraction_row([_bank_claim(new_value="1234 5678 90")])
    conn = _conn([[row], [{"account_number": "1234567890"}]], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    assert out == []
    assert _executed_sql(conn, "supplier_bank_accounts") == []
    assert _written_claims(conn)[0]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_a_malformed_ifsc_fails_rather_than_becoming_a_finding():
    claims = [
        _bank_claim(),
        _bank_claim(field="ifsc", new_value="NOT-AN-IFSC"),
    ]
    row = _extraction_row(claims)
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    assert out == []
    assert _executed_sql(conn, "supplier_bank_accounts") == []
    written = _written_claims(conn)
    # Both the account_number claim and its ifsc sibling share the outcome —
    # they describe one account, not two independent facts.
    assert all(c["status"] == "FAILED" for c in written)


@pytest.mark.asyncio
async def test_sibling_ifsc_and_bank_name_claims_ride_along_with_the_account():
    claims = [
        _bank_claim(),
        _bank_claim(field="ifsc", new_value="HDFC0001234"),
        _bank_claim(field="bank_name", new_value="HDFC Bank"),
    ]
    row = _extraction_row(claims)
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    assert len(out) == 1
    keep_new = next(a for a in out[0].actions if a["id"] == "keep_new")
    assert keep_new["body"]["ifsc"] == "HDFC0001234"
    assert keep_new["body"]["bank_name"] == "HDFC Bank"
    written = _written_claims(conn)
    assert all(c["status"] == "CONFLICTED" for c in written)


@pytest.mark.asyncio
async def test_new_bank_account_offers_notify_only_when_a_contact_is_known():
    row = _extraction_row([_bank_claim()], contact_id="contact-9")
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    notify = next(a for a in out[0].actions if a["kind"] == "notify")
    assert notify["body"]["contact_id"] == "contact-9"


# ── vendor_pan: single-valid, empty-or-mismatched both need a click ────────

@pytest.mark.asyncio
async def test_pan_with_none_on_file_becomes_a_finding_and_writes_nothing():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "aabcu9603r",
              "confidence": 0.9, "evidence_quote": "our PAN is AABCU9603R",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert len(out) == 1
    assert out[0].finding_type == "vendor_pan_conflict"
    assert "don't have on file yet" in out[0].title
    assert _executed_sql(conn, "dim_suppliers") == []
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


@pytest.mark.asyncio
async def test_pan_matching_the_existing_value_is_a_no_op():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "aabcu 9603r", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out == []
    assert _executed_sql(conn, "dim_suppliers") == []
    assert _written_claims(conn)[0]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_pan_conflicting_with_the_existing_value_becomes_a_finding():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "our new PAN is AABCU9603R",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "ZZZZZ1111Z", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "vendor_pan_conflict"
    assert "different" in f.title
    assert f.family in FAMILIES
    assert f.subject_type in SUBJECT_TYPES
    assert f.severity == "MEDIUM"
    assert f.verification == "UNVERIFIED"
    assert f.key_qualifier == f"{EXTRACTION}:0"
    assert f.evidence["kind"] in EVIDENCE_KINDS
    # No contact_id on this extraction, so no notify action — only the real
    # keep_new write and the navigate fallback.
    kinds = {a["kind"] for a in f.actions}
    assert kinds == {"safe", "navigate"}
    assert _executed_sql(conn, "dim_suppliers") == []  # never writes on conflict
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


@pytest.mark.asyncio
async def test_pan_conflict_offers_keep_new_via_the_real_vendor_endpoint():
    """'Keep new' must call PATCH /api/vendors/{id} — the endpoint that already
    owns writing dim_suppliers.pan — never a bespoke resolution endpoint."""
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "aabcu9603r",
              "confidence": 0.9, "evidence_quote": "our new PAN is AABCU9603R",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "ZZZZZ1111Z", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    keep_new = next(a for a in out[0].actions if a["id"] == "keep_new")
    assert keep_new["kind"] == "safe"
    assert keep_new["endpoint"] == f"/api/vendors/{VENDOR}"
    assert keep_new["method"] == "PATCH"
    assert keep_new["body"] == {"pan": "AABCU9603R"}  # normalised, matching the diff shown
    assert keep_new["confirm"]["change"] == {"label": "PAN", "from": "ZZZZZ1111Z", "to": "AABCU9603R"}


@pytest.mark.asyncio
async def test_gstin_conflict_offers_keep_new_via_the_real_vendor_endpoint():
    """'Keep new' must call POST /api/vendors/{id}/gstins — the endpoint that
    already owns writing supplier_gstins — with make_primary so it replaces
    the vendor's primary GSTIN rather than sitting beside it as a second one."""
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29AABCU9603R1ZJ",
              "confidence": 0.9, "evidence_quote": "our GSTIN is now 29AABCU9603R1ZJ",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": "07AAACT2727Q1ZW",
                                            "payment_terms_days": None})

    out = await _drain(conn)

    keep_new = next(a for a in out[0].actions if a["id"] == "keep_new")
    assert keep_new["kind"] == "safe"
    assert keep_new["endpoint"] == f"/api/vendors/{VENDOR}/gstins"
    assert keep_new["method"] == "POST"
    assert keep_new["body"] == {"gstin": "29AABCU9603R1ZJ", "make_primary": True}
    assert keep_new["confirm"]["change"] == {
        "label": "Primary GSTIN", "from": "07AAACT2727Q1ZW", "to": "29AABCU9603R1ZJ",
    }


@pytest.mark.asyncio
async def test_conflict_offers_notify_only_when_a_contact_is_known():
    """An extraction with no contact_id has nobody to ask — no notify action,
    and no error either."""
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim])  # contact_id=None by default
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "ZZZZZ1111Z", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert not any(a["kind"] == "notify" for a in out[0].actions)


@pytest.mark.asyncio
async def test_conflict_notify_action_targets_the_named_contact_and_quotes_the_evidence():
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29AABCU9603R1ZJ",
              "confidence": 0.9, "evidence_quote": "our GSTIN is now 29AABCU9603R1ZJ",
              "status": "PENDING"}
    row = _extraction_row([claim], contact_id="contact-9")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": "07AAACT2727Q1ZW",
                                            "payment_terms_days": None})

    out = await _drain(conn)

    notify = next(a for a in out[0].actions if a["kind"] == "notify")
    assert notify["endpoint"] == "/api/review/act/notify"
    assert notify["method"] == "POST"
    assert notify["body"]["destination_type"] == "vendor_contact"
    assert notify["body"]["contact_id"] == "contact-9"
    assert "29AABCU9603R1ZJ" in notify["body"]["message"]
    assert "our GSTIN is now 29AABCU9603R1ZJ" in notify["body"]["message"]
    assert notify["confirm"] is not None


@pytest.mark.asyncio
async def test_a_malformed_pan_fails_rather_than_conflicts():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "not-a-pan",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out == []
    assert _executed_sql(conn, "dim_suppliers") == []
    assert _written_claims(conn)[0]["status"] == "FAILED"


# ── vendor_gstin: single-valid ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gstin_with_none_on_file_becomes_a_finding_and_writes_nothing():
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29aabcu9603r1zj",
              "confidence": 0.9, "evidence_quote": "our new GSTIN is 29AABCU9603R1ZJ",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert len(out) == 1
    assert out[0].finding_type == "vendor_gstin_conflict"
    assert _executed_sql(conn, "supplier_gstins") == []
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


@pytest.mark.asyncio
async def test_gstin_conflicting_with_the_existing_value_becomes_a_finding():
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29AABCU9603R1ZJ",
              "confidence": 0.9, "evidence_quote": "our GSTIN is now 29AABCU9603R1ZJ",
              "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": "07AAACT2727Q1ZW",
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert len(out) == 1
    assert out[0].finding_type == "vendor_gstin_conflict"
    kinds = {a["kind"] for a in out[0].actions}
    assert kinds == {"safe", "navigate"}  # no contact_id on this extraction
    assert _executed_sql(conn, "supplier_gstins") == []
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


# ── payment_term: single-valid, compared against dim_suppliers.payment_terms_days ─

@pytest.mark.asyncio
async def test_payment_term_with_none_on_file_becomes_a_finding():
    claim = {"scope": "payment_term", "field": "term", "new_value": "45", "unit": "days",
              "confidence": 0.9, "evidence_quote": "payment within 45 days of invoice receipt",
              "status": "PENDING"}
    row = _extraction_row([claim], source_type="contract")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "payment_term_conflict"
    assert "contract" in f.title
    keep_new = next(a for a in f.actions if a["id"] == "keep_new")
    assert keep_new["endpoint"] == f"/api/vendors/{VENDOR}"
    assert keep_new["method"] == "PATCH"
    assert keep_new["body"] == {"payment_terms_days": 45}
    assert _executed_sql(conn, "dim_suppliers") == []
    assert _written_claims(conn)[0]["status"] == "CONFLICTED"


@pytest.mark.asyncio
async def test_payment_term_matching_the_existing_value_is_a_no_op():
    claim = {"scope": "payment_term", "field": "term", "new_value": "30",
              "confidence": 0.9, "evidence_quote": "net 30", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": 30})

    out = await _drain(conn)

    assert out == []
    assert _written_claims(conn)[0]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_a_non_numeric_payment_term_fails_rather_than_conflicts():
    claim = {"scope": "payment_term", "field": "term", "new_value": "net thirty",
              "confidence": 0.9, "evidence_quote": "net thirty", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out == []
    assert _written_claims(conn)[0]["status"] == "FAILED"


# ── vendor_contact: a named POC not already in supplier_contacts ───────────

def _contact_claims(**over):
    email = {"scope": "vendor_contact", "field": "email", "new_value": "poc@vendor.com",
              "confidence": 0.9, "evidence_quote": "contact: Jane Doe, poc@vendor.com",
              "status": "PENDING"}
    name = {"scope": "vendor_contact", "field": "name", "new_value": "Jane Doe",
             "confidence": 0.9, "evidence_quote": "contact: Jane Doe, poc@vendor.com",
             "status": "PENDING"}
    email.update(over)
    return [email, name]


@pytest.mark.asyncio
async def test_a_poc_not_already_in_contacts_becomes_a_finding():
    row = _extraction_row(_contact_claims(), source_type="contract")
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)  # [] = no existing contacts

    out = await _drain(conn)

    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "vendor_contact_new"
    assert f.severity == "LOW"
    assert _executed_sql(conn, "supplier_contacts") == []  # never writes on read alone
    written = _written_claims(conn)
    assert all(c["status"] == "CONFLICTED" for c in written)


@pytest.mark.asyncio
async def test_poc_finding_offers_add_contact_via_the_real_endpoint():
    row = _extraction_row(_contact_claims())
    conn = _conn([[row], []], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    keep_new = next(a for a in out[0].actions if a["id"] == "keep_new")
    assert keep_new["kind"] == "safe"
    assert keep_new["endpoint"] == f"/api/vendors/{VENDOR}/contacts"
    assert keep_new["method"] == "POST"
    assert keep_new["body"] == {"name": "Jane Doe", "role": "Contract contact",
                                 "email": "poc@vendor.com", "phone": None}


@pytest.mark.asyncio
async def test_a_poc_already_in_contacts_is_a_silent_no_op():
    row = _extraction_row(_contact_claims())
    conn = _conn([[row], [{"email": "poc@vendor.com"}]], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    assert out == []
    assert _executed_sql(conn, "supplier_contacts") == []
    assert _written_claims(conn)[0]["status"] == "APPLIED"


@pytest.mark.asyncio
async def test_a_poc_with_no_usable_email_fails():
    row = _extraction_row(_contact_claims(new_value="not-an-email"))
    conn = _conn([[row]], fetchrow_return=VENDOR_ROW)

    out = await _drain(conn)

    assert out == []
    assert _written_claims(conn)[0]["status"] == "FAILED"


# ── A conflict reads as the either/or it is ───────────────────────────────

@pytest.mark.asyncio
async def test_a_conflict_names_both_values_in_its_two_actions():
    """"Use this payment terms" told the reader neither which value it meant
    nor what became of the other. Both sides are named, and the one that
    changes something leads."""
    claim = {"scope": "payment_term", "field": "term", "new_value": "45", "unit": "days",
              "confidence": 0.9, "evidence_quote": "net 45", "status": "PENDING"}
    row = _extraction_row([claim], source_type="contract")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": 30})

    out = await _drain(conn)

    acts = {a["id"]: a for a in out[0].actions}
    assert acts["keep_new"]["label"] == "Keep the contract's payment terms (45 days)"
    assert acts["keep_current"]["label"] == "Keep current payment terms (30 days)"
    # The change leads; the status quo is the muted one.
    assert acts["keep_new"]["emphasis"] == "primary"
    assert acts["keep_current"]["emphasis"] == "secondary"
    assert out[0].actions[0]["id"] == "keep_new"


@pytest.mark.asyncio
async def test_keep_current_discards_the_claim_rather_than_writing():
    claim = {"scope": "payment_term", "field": "term", "new_value": "45",
              "confidence": 0.9, "evidence_quote": "net 45", "status": "PENDING"}
    row = _extraction_row([claim], source_type="contract")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": 30})

    out = await _drain(conn)

    keep_current = next(a for a in out[0].actions if a["id"] == "keep_current")
    assert keep_current["kind"] == "safe"
    assert keep_current["method"] == "POST"
    assert keep_current["endpoint"] == f"/api/vendors/{VENDOR}/documents/{DOC}/claims/0/discard"


@pytest.mark.asyncio
async def test_an_email_claim_offers_no_keep_current_because_there_is_no_document():
    """Discarding a claim addresses it by the document it came from; an email
    extraction has none, so offering the button would be offering a dead end."""
    claim = {"scope": "payment_term", "field": "term", "new_value": "45",
              "confidence": 0.9, "evidence_quote": "net 45", "status": "PENDING"}
    row = _extraction_row([claim], source_type="email")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": 30})

    out = await _drain(conn)

    assert not any(a["id"] == "keep_current" for a in out[0].actions)
    assert any(a["id"] == "keep_new" for a in out[0].actions)


# ── Reaching the hub at all: entitlement tag and lane ─────────────────────

@pytest.mark.asyncio
async def test_a_contract_sourced_finding_is_not_tagged_as_email_sending():
    """The hub filters findings by the tenant's licensed services. A conflict
    read off a contract has nothing to do with email sending, and tagging it
    that way let an unrelated entitlement hide it from the hub entirely."""
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29AABCU9603R1ZJ",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim], source_type="contract")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": "07AAACT2727Q1ZW",
                                            "payment_terms_days": None})

    out = await _drain(conn)

    from backend_ocr.app.security.services import SERVICE_KEYS
    assert out[0].source_service == "vendors"
    assert out[0].source_service in SERVICE_KEYS


@pytest.mark.asyncio
async def test_an_email_sourced_finding_keeps_the_email_service():
    claim = {"scope": "vendor_gstin", "field": "gstin", "new_value": "29AABCU9603R1ZJ",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim], source_type="email")
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": "07AAACT2727Q1ZW",
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out[0].source_service == "email_sending"


@pytest.mark.asyncio
async def test_a_claim_nobody_reviewed_stays_unverified():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "ZZZZZ1111Z", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out[0].verification == "UNVERIFIED"


@pytest.mark.asyncio
async def test_a_human_verified_conflict_lands_in_the_compliance_lane():
    """lane_for() sends anything UNVERIFIED to 'suspected', and the hub's lane
    filter excludes UNVERIFIED from every named lane — so a conflict a human
    already confirmed has to come through VERIFIED or it is unreachable in the
    compliance lane it belongs to."""
    from backend_ocr.app.review.ranking import lane_for
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "CONFLICTED",
              "verified_by": "subash@1buy.ai"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": "ZZZZZ1111Z", "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out[0].verification == "VERIFIED"
    assert lane_for(out[0].family, out[0].verification) == "compliance"


# ── Housekeeping ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_claim_with_no_supplier_is_skipped_entirely():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim], supplier_id=None)
    conn = _conn([[row]])

    out = await _drain(conn)

    assert out == []
    conn.fetchrow.assert_not_called()
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_a_vendor_that_no_longer_exists_is_skipped():
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "PENDING"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return=None)  # vendor merged/deleted

    out = await _drain(conn)

    assert out == []
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_a_terminal_claim_is_never_reprocessed():
    """DONE/APPLIED/DISCARDED/FAILED claims are outside the PENDING/CONFLICTED
    set the adapter re-litigates — a resolved claim must stay resolved."""
    claim = {"scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
              "confidence": 0.9, "evidence_quote": "q", "status": "APPLIED"}
    row = _extraction_row([claim])
    conn = _conn([[row]], fetchrow_return={"name": "Acme", "pan": None, "gstin": None,
                                            "payment_terms_days": None})

    out = await _drain(conn)

    assert out == []
    conn.execute.assert_not_called()  # nothing dirty, nothing to write back
