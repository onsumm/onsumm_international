"""backend_ocr/app/review/comm_extract.py — the typed-claim schema and prompt.

The schema is exercised through llm.validate() rather than hand-rolled checks,
since that is the exact validator complete_json() runs the model's response
through in production — a schema that "looks right" but fails validate() would
pass code review and then fail every real call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend_ocr.app.ai import llm
from backend_ocr.app.review.comm_extract import (
    CLAIM_SCHEMA, CONTRACT_SYSTEM, PROMPT_VERSION, SYSTEM, build_contract_user,
    build_user, extract_claims_from_contract, extract_claims_from_email,
)
from backend_ocr.app.review.redact import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

# ── Schema ────────────────────────────────────────────────────────────────────

def test_a_normal_claim_passes_the_schema():
    payload = {"claims": [{
        "scope": "vendor_bank", "field": "account_number", "new_value": "1234567890",
        "confidence": 0.9, "evidence_quote": "please pay into account 1234567890",
    }]}
    assert llm.validate(payload, CLAIM_SCHEMA) == []


def test_an_empty_claims_list_is_valid():
    """The single most important case: most emails carry no claim at all."""
    assert llm.validate({"claims": []}, CLAIM_SCHEMA) == []


def test_bill_line_item_is_now_accepted_with_an_optional_target_ref():
    """Phase 3: bill_line_item + target_ref are now read by
    contract_bill_variance.py, so the schema must accept them (it rejected
    bill_line_item in Phase 0+1, when nothing read it yet)."""
    payload = {"claims": [{
        "scope": "bill_line_item", "field": "rate", "target_ref": "Managed hosting",
        "new_value": "100", "confidence": 0.5, "evidence_quote": "the rate is now 100",
    }]}
    assert llm.validate(payload, CLAIM_SCHEMA) == []


def test_target_ref_is_optional_and_may_be_omitted_for_other_scopes():
    payload = {"claims": [{
        "scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
        "confidence": 0.8, "evidence_quote": "our PAN is AABCU9603R",
    }]}
    assert llm.validate(payload, CLAIM_SCHEMA) == []


def test_scope_still_rejects_what_no_adapter_reads():
    """vendor_tds remains out of scope — the schema must still reject it."""
    payload = {"claims": [{
        "scope": "vendor_tds", "field": "section", "new_value": "194C",
        "confidence": 0.5, "evidence_quote": "TDS under section 194C",
    }]}
    problems = llm.validate(payload, CLAIM_SCHEMA)
    assert problems and "vendor_tds" in problems[0]


def test_evidence_quote_is_required():
    payload = {"claims": [{
        "scope": "vendor_pan", "field": "pan", "new_value": "AABCU9603R",
        "confidence": 0.8,
    }]}
    problems = llm.validate(payload, CLAIM_SCHEMA)
    assert any("evidence_quote" in p for p in problems)


def test_a_claim_missing_new_value_is_rejected():
    payload = {"claims": [{
        "scope": "vendor_gstin", "field": "gstin", "confidence": 0.7,
        "evidence_quote": "our new GSTIN is 29AABCU9603R1ZJ",
    }]}
    problems = llm.validate(payload, CLAIM_SCHEMA)
    assert any("new_value" in p for p in problems)


# ── Prompt ────────────────────────────────────────────────────────────────────

def test_system_prompt_rewards_an_empty_answer():
    lowered = SYSTEM.lower()
    assert "empty" in lowered and "correct and expected" in lowered


def test_system_prompt_fences_untrusted_text():
    assert UNTRUSTED_OPEN in SYSTEM and UNTRUSTED_CLOSE in SYSTEM


def test_system_prompt_restricts_to_explicit_statements():
    """Guards against the model inferring a bank account from a signature
    block or letterhead — only an explicit statement should count."""
    assert "explicit" in SYSTEM.lower()


def test_build_user_includes_vendor_subject_and_body():
    user = build_user("Sundaram Logistics", "New bank details", "please use account X")
    assert "Sundaram Logistics" in user
    assert "New bank details" in user
    assert "please use account X" in user


def test_build_user_handles_missing_vendor_name():
    assert "Unknown vendor" in build_user("", "", "")


# ── extract_claims_from_email ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extraction_redacts_and_fences_before_calling_the_model():
    """The account number must never reach the prompt in the clear, and the
    body must be wrapped so the model treats it as data, not instructions."""
    captured = {}

    async def fake_complete_json(*, tenant_id, system, user, **kw):
        captured["user"] = user
        return llm.LLMResult(content={"claims": []}, model=kw.get("model", "gpt-4o-mini"))

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=fake_complete_json)):
        result, red = await extract_claims_from_email(
            "tenant-1", "Acme Traders", "Bank change",
            "Please pay us at account 123456789012 going forward.",
        )

    assert "123456789012" not in captured["user"]
    assert "ACCOUNT_A" in captured["user"]
    assert UNTRUSTED_OPEN in captured["user"]
    assert result.content == {"claims": []}
    assert red.restore("ACCOUNT_A") == "123456789012"


@pytest.mark.asyncio
async def test_extraction_uses_the_pinned_prompt_version_and_schema():
    captured = {}

    async def fake_complete_json(*, schema, **kw):
        captured["schema"] = schema
        return llm.LLMResult(content={"claims": []}, model="gpt-4o-mini")

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=fake_complete_json)):
        await extract_claims_from_email("tenant-1", "Acme", "s", "b")

    assert captured["schema"] is CLAIM_SCHEMA
    assert PROMPT_VERSION == "comm-extract-v1"


@pytest.mark.asyncio
async def test_extraction_propagates_llm_refusal():
    from backend_ocr.app.ai.llm import LLMRefused

    async def refuse(**kw):
        raise LLMRefused("daily cap reached")

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=refuse)):
        with pytest.raises(LLMRefused):
            await extract_claims_from_email("tenant-1", "Acme", "s", "b")


# ── extract_claims_from_contract ──────────────────────────────────────────────

def test_contract_system_prompt_rewards_an_empty_answer():
    lowered = CONTRACT_SYSTEM.lower()
    assert "empty" in lowered and "correct and expected" in lowered


def test_contract_system_prompt_fences_untrusted_text():
    assert UNTRUSTED_OPEN in CONTRACT_SYSTEM and UNTRUSTED_CLOSE in CONTRACT_SYSTEM


def test_contract_system_prompt_covers_line_item_rate_and_quantity():
    assert "bill_line_item" in CONTRACT_SYSTEM
    assert "target_ref" in CONTRACT_SYSTEM
    assert '"rate"' in CONTRACT_SYSTEM and '"quantity"' in CONTRACT_SYSTEM


def test_contract_system_prompt_also_covers_vendor_bank_gstin_pan():
    """Contracts often restate the vendor's own banking/registration details."""
    assert "vendor_bank" in CONTRACT_SYSTEM
    assert "vendor_gstin" in CONTRACT_SYSTEM
    assert "vendor_pan" in CONTRACT_SYSTEM


def test_contract_prompt_tells_the_model_which_of_the_two_parties_it_describes():
    """Caught in testing: an insertion order lists the buyer's and the vendor's
    name, GSTIN, PAN, POC and bank details side by side in identical shapes,
    and the buyer's POC was extracted as the vendor's. The prompt has to name
    the trap, not just ask for "the vendor's" details."""
    assert "TWO PARTIES" in CONTRACT_SYSTEM
    # Names the labels these documents actually use for the side to ignore.
    for word in ("purchaser", "buyer", "client", "customer"):
        assert word in CONTRACT_SYSTEM.lower(), word
    # And carries a worked example, so the rule is shown rather than asserted.
    assert "VENDOR COMPANY PVT LTD" in CONTRACT_SYSTEM


def test_the_party_is_identified_by_label_not_by_position():
    """The vendor is as often on the left as the right, or underneath. A prompt
    that teaches "the right-hand column" is right until the first document that
    flips them."""
    low = CONTRACT_SYSTEM.lower()
    assert "never by where it sits" in low
    assert "left or the right" in low
    # The worked example must not generalise from its own layout either.
    assert "read the heading and not the position" in low


def test_a_blank_on_the_vendors_side_is_taught_as_an_answer():
    """The PAN case: the vendor's row was empty, so the model took the buyer's
    rather than reporting nothing."""
    assert "WHERE THE VENDOR'S SIDE IS BLANK, REPORT NOTHING FOR IT" in CONTRACT_SYSTEM


def test_the_prompt_says_a_pan_lives_inside_the_gstin():
    low = CONTRACT_SYSTEM.lower()
    assert "characters 3-12 of a gstin" in low
    assert "already have their pan and must not report it separately" in low
    assert "never take a pan from the buyer's side" in low


def test_the_prompt_carries_no_real_customer_identifiers():
    """A prompt is sent to a third-party model on every extraction and is shared
    by every tenant, so one customer's names, addresses and GSTINs must not be
    baked into it — and a model shown one real document tends to pattern-match
    on it rather than learn the rule. Examples use RFC 2606 .example domains
    and obviously synthetic ids."""
    leaked = [
        "brightloans", "brinkads", "adperience", "3dot14", "avinash",
        "mohit.bhatnagar", "ashish@", "vaishnavi", "pooja",
        # Real GSTINs/PANs seen on the documents this was built against.
        "07AACCA2775M1ZF", "AACCA2775M", "06AABCZ6304N1Z2", "06AALCM5200G1ZR",
    ]
    for needle in leaked:
        assert needle.lower() not in CONTRACT_SYSTEM.lower(), needle
    for needle in leaked:
        assert needle.lower() not in SYSTEM.lower(), needle


def test_contract_prompt_asks_for_the_ifsc_alongside_the_account_number():
    """An account number with no IFSC cannot be paid, and the contract states
    them together — so both have to come back, as sibling claims."""
    assert '"ifsc"' in CONTRACT_SYSTEM
    assert '"account_number"' in CONTRACT_SYSTEM
    assert '"bank_name"' in CONTRACT_SYSTEM


def test_build_contract_user_includes_vendor_document_and_text():
    user = build_contract_user("Acme Traders", "MSA-2026.pdf", "the rate is 45000")
    assert "Acme Traders" in user
    assert "MSA-2026.pdf" in user
    assert "the rate is 45000" in user


@pytest.mark.asyncio
async def test_contract_extraction_redacts_and_fences_before_calling_the_model():
    captured = {}

    async def fake_complete_json(*, tenant_id, system, user, **kw):
        captured["user"] = user
        captured["system"] = system
        return llm.LLMResult(content={"claims": []}, model=kw.get("model", "gpt-4o-mini"))

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=fake_complete_json)):
        result, red = await extract_claims_from_contract(
            "tenant-1", "Acme Traders", "MSA-2026.pdf",
            "Please pay into account 123456789012 as per this agreement.",
        )

    assert captured["system"] == CONTRACT_SYSTEM
    assert "123456789012" not in captured["user"]
    assert "ACCOUNT_A" in captured["user"]
    assert UNTRUSTED_OPEN in captured["user"]
    assert result.content == {"claims": []}
    assert red.restore("ACCOUNT_A") == "123456789012"


@pytest.mark.asyncio
async def test_contract_extraction_uses_the_pinned_prompt_version_and_shared_schema():
    captured = {}

    async def fake_complete_json(*, schema, **kw):
        captured["schema"] = schema
        return llm.LLMResult(content={"claims": []}, model="gpt-4o-mini")

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=fake_complete_json)):
        await extract_claims_from_contract("tenant-1", "Acme", "doc.pdf", "text")

    # Same schema object as the email path — one claim vocabulary, not two.
    assert captured["schema"] is CLAIM_SCHEMA
    assert PROMPT_VERSION == "comm-extract-v1"


@pytest.mark.asyncio
async def test_contract_extraction_propagates_llm_refusal():
    from backend_ocr.app.ai.llm import LLMRefused

    async def refuse(**kw):
        raise LLMRefused("daily cap reached")

    with patch("backend_ocr.app.ai.llm.complete_json", AsyncMock(side_effect=refuse)):
        with pytest.raises(LLMRefused):
            await extract_claims_from_contract("tenant-1", "Acme", "doc.pdf", "text")
