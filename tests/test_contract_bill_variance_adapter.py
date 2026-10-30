"""backend_ocr/app/review/adapters/contract_bill_variance.py — contracted rate
vs. what a bill actually charged. The rule under test: only the RATE is
compared; quantity varies with usage and never raises a finding.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters.contract_bill_variance import ContractBillVarianceAdapter
from backend_ocr.app.review.types import EVIDENCE_KINDS, FAMILIES, SUBJECT_TYPES

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
EXTRACTION = "44444444-0000-0000-0000-000000000001"
INVOICE = "55555555-0000-0000-0000-000000000001"


def _claim(scope="bill_line_item", field="rate", new_value="100", target_ref="Widget A",
           status="PENDING", confidence=0.8):
    return {"scope": scope, "field": field, "new_value": new_value, "target_ref": target_ref,
            "status": status, "confidence": confidence, "evidence_quote": "at INR 100 per unit"}


def _extraction_row(claims):
    return {"extraction_id": EXTRACTION, "supplier_id": VENDOR, "claims": json.dumps(claims)}


def _bill_line(product_name="Widget A", quantity="10", unit_price="100", invoice_id=INVOICE,
                invoice_number="INV-001"):
    return {"invoice_id": invoice_id, "invoice_number": invoice_number,
            "product_name": product_name, "quantity": quantity, "unit_price": unit_price}


def _conn(extraction_rows, bill_lines=None, vendor_name="Acme Traders"):
    c = AsyncMock()
    # Call order: the main comm_extractions SELECT, then (only if a rate+qty
    # pair survives grouping) one _vendor_name + one _load_bill_lines call per
    # supplier encountered. Tests here use a single supplier, so a 2-item
    # side_effect list covers it; simple cases with no complete pair never
    # reach the second call and the extra item is simply unused.
    c.fetch = AsyncMock(side_effect=[extraction_rows, bill_lines or []])
    c.fetchrow = AsyncMock(return_value={"name": vendor_name})
    c.execute = AsyncMock()
    return c


async def _drain(conn, limit=100):
    return [d async for d in ContractBillVarianceAdapter().scan(conn, TENANT, limit)]


def test_declares_a_real_service_and_family():
    a = ContractBillVarianceAdapter()
    from backend_ocr.app.security.services import SERVICE_KEYS
    assert a.source_service in SERVICE_KEYS
    assert a.family in FAMILIES
    assert a.family == "reconciliation"


@pytest.mark.asyncio
async def test_no_open_claims_produces_nothing():
    out = await _drain(_conn([]))
    assert out == []


@pytest.mark.asyncio
async def test_a_contract_rate_alone_is_enough_to_compare():
    """The rate is the term; a contract need not state a quantity."""
    claims = [_claim(field="rate", new_value="100")]
    out = await _drain(_conn([_extraction_row(claims)], [_bill_line(unit_price="150")]))
    assert len(out) == 1
    assert "rate" in out[0].title.lower()


@pytest.mark.asyncio
async def test_a_quantity_claim_alone_is_never_compared():
    claims = [_claim(field="quantity", new_value="10")]
    conn = _conn([_extraction_row(claims)])
    assert await _drain(conn) == []
    assert conn.fetch.await_count == 1  # bill lines never even loaded


@pytest.mark.asyncio
async def test_matching_rate_and_quantity_produces_no_finding():
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="100")]
    out = await _drain(_conn([_extraction_row(claims)], bill_lines))
    assert out == []


@pytest.mark.asyncio
async def test_rate_mismatch_beyond_tolerance_is_flagged():
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="150")]
    out = await _drain(_conn([_extraction_row(claims)], bill_lines))
    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "contract_bill_variance"
    assert f.subject_type in SUBJECT_TYPES
    assert f.subject_type == "bill"
    assert f.subject_id == INVOICE
    assert f.evidence["kind"] in EVIDENCE_KINDS
    assert "rate" in f.title.lower()


@pytest.mark.asyncio
async def test_a_different_quantity_at_the_contracted_rate_is_not_flagged():
    """Usage billing: "Route Distance API" at the contracted 0.79, 167726 units
    against a contract figure of 160000, is a correct bill."""
    claims = [_claim(field="rate", new_value="0.79", target_ref="Route Distance API"),
              _claim(field="quantity", new_value="160000", target_ref="Route Distance API")]
    bill_lines = [_bill_line(product_name="Route Distance API", quantity="167726.0000",
                             unit_price="0.7900")]
    assert await _drain(_conn([_extraction_row(claims)], bill_lines)) == []


@pytest.mark.asyncio
async def test_small_variance_within_tolerance_is_not_flagged():
    # 1% off the contracted rate — within the shared 2% default tolerance.
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="101")]
    out = await _drain(_conn([_extraction_row(claims)], bill_lines))
    assert out == []


@pytest.mark.asyncio
async def test_no_matching_bill_line_yet_leaves_claims_pending_no_finding():
    claims = [_claim(field="rate", new_value="100", target_ref="Widget A"),
              _claim(field="quantity", new_value="10", target_ref="Widget A")]
    bill_lines = [_bill_line(product_name="Completely Different Item")]
    conn = _conn([_extraction_row(claims)], bill_lines)
    out = await _drain(conn)
    assert out == []
    conn.execute.assert_not_called()  # nothing resolved, nothing to write back


@pytest.mark.asyncio
async def test_billed_line_missing_unit_price_skips_that_line_not_the_whole_claim():
    """Brief's rule applied on the billed side too: if the bill itself doesn't
    carry both values for a matched line, that line is skipped, not treated as
    a mismatch."""
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(unit_price=None)]
    out = await _drain(_conn([_extraction_row(claims)], bill_lines))
    assert out == []


@pytest.mark.asyncio
async def test_resolved_claims_are_written_back_to_comm_extractions():
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="150")]
    conn = _conn([_extraction_row(claims)], bill_lines)
    await _drain(conn)
    conn.execute.assert_awaited_once()
    written = json.loads(conn.execute.await_args.args[2])
    assert all(c["status"] == "VARIANCE" for c in written)


@pytest.mark.asyncio
async def test_a_matched_claim_is_marked_applied_not_conflicted():
    """VARIANCE/APPLIED, never CONFLICTED — this is a reconciliation exception,
    not a master-data overwrite choice."""
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="100")]
    conn = _conn([_extraction_row(claims)], bill_lines)
    await _drain(conn)
    written = json.loads(conn.execute.await_args.args[2])
    assert all(c["status"] == "APPLIED" for c in written)
    assert all(c["status"] != "CONFLICTED" for c in written)


@pytest.mark.asyncio
async def test_malformed_claim_value_is_never_fabricated_into_a_comparison():
    claims = [_claim(field="rate", new_value="not-a-number"), _claim(field="quantity", new_value="10")]
    conn = _conn([_extraction_row(claims)])
    out = await _drain(conn)
    assert out == []
    assert conn.fetch.await_count == 1  # never even loaded bill lines


@pytest.mark.asyncio
async def test_action_offered_is_navigate_only_no_write_action():
    """Unlike master_data_conflict.py's conflicts, a variance has no
    keep-new/keep-existing choice — only a link to go look at the bill."""
    claims = [_claim(field="rate", new_value="100"), _claim(field="quantity", new_value="10")]
    bill_lines = [_bill_line(quantity="10", unit_price="150")]
    out = await _drain(_conn([_extraction_row(claims)], bill_lines))
    f = out[0]
    assert len(f.actions) == 1
    assert f.actions[0]["kind"] == "navigate"


@pytest.mark.asyncio
async def test_non_contract_line_item_scopes_are_ignored():
    claims = [_claim(scope="vendor_pan", field="pan", new_value="AABCU9603R")]
    out = await _drain(_conn([_extraction_row(claims)]))
    assert out == []
