"""backend_ocr/app/review/verifier.py — the gate between a model's output and a human.

The most important file in the agent, so these are adversarial on purpose. The
bar is: a wrong number never reaches a user, an unprovable observation is
published quietly rather than confidently, and money on a published finding comes
from the database rather than from the model's prose.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.verifier import (
    REFUTED, UNVERIFIABLE, VERIFIED, log_reject, verify_claims,
)

TENANT = "10000000-0000-0000-0000-000000000001"
BILL = "22222222-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"


def _conn(*values):
    """asyncpg conn whose fetchval returns each value in turn."""
    c = AsyncMock()
    c.fetchval = AsyncMock(side_effect=list(values))
    c.execute = AsyncMock()
    return c


def claim(op="==", left_tool="bill_total", right_tool="bill_line_sum", **extra):
    c = {
        "kind": "numeric_compare",
        "left": {"tool": left_tool, "args": {"invoice_id": BILL}},
        "op": op,
        "right": {"tool": right_tool, "args": {"invoice_id": BILL}},
    }
    c.update(extra)
    return c


# ── The happy path ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_true_claim_verifies():
    # Line items sum to more than the invoice total — a real, checkable problem.
    v = await verify_claims(_conn(Decimal("100000"), Decimal("118400")),
                            TENANT, [claim(op="<")])
    assert v.status == VERIFIED and v.checked == 1


@pytest.mark.asyncio
async def test_a_false_claim_is_refuted_and_never_published():
    v = await verify_claims(_conn(Decimal("118400"), Decimal("100000")),
                            TENANT, [claim(op="<")])
    assert v.status == REFUTED
    assert v.refuted and "do not satisfy" in v.refuted[0]["reason"]


@pytest.mark.asyncio
async def test_a_quoted_value_the_database_does_not_hold_is_refuted():
    """The sharpest hallucination signal available: the model cited a number it
    did not read. The relation can even hold and this still refutes."""
    v = await verify_claims(
        _conn(Decimal("100000"), Decimal("118400")),
        TENANT,
        [claim(op="<", stated_left="999999", stated_right="118400")],
    )
    assert v.status == REFUTED
    assert any("does not hold" in r["reason"] for r in v.refuted)


@pytest.mark.asyncio
async def test_a_correctly_quoted_value_passes():
    v = await verify_claims(
        _conn(Decimal("100000"), Decimal("118400")),
        TENANT,
        [claim(op="<", stated_left="1,00,000", stated_right="₹118400")],
    )
    assert v.status == VERIFIED


# ── Unverifiable ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_claims_at_all_is_unverifiable_not_verified():
    """"I could not check this" must never collapse into "this is fine"."""
    v = await verify_claims(_conn(), TENANT, [])
    assert v.status == UNVERIFIABLE


@pytest.mark.asyncio
async def test_a_qualitative_claim_is_unverifiable():
    v = await verify_claims(_conn(), TENANT, [
        {"kind": "observation", "text": "this vendor's invoice format changed abruptly"},
    ])
    assert v.status == UNVERIFIABLE


@pytest.mark.asyncio
async def test_an_unknown_tool_is_unverifiable_rather_than_assumed_true():
    """A claim naming a tool we do not have must not be waved through."""
    v = await verify_claims(_conn(), TENANT, [
        claim(left_tool="some_tool_we_never_built"),
    ])
    assert v.status == UNVERIFIABLE


@pytest.mark.asyncio
async def test_two_literals_check_nothing():
    """Comparing two of the model's own numbers is the model agreeing with
    itself, which is not evidence."""
    v = await verify_claims(_conn(), TENANT, [{
        "kind": "numeric_compare",
        "left": {"literal": 100}, "op": "<", "right": {"literal": 200},
    }])
    assert v.status == UNVERIFIABLE


@pytest.mark.asyncio
async def test_a_tool_that_errors_degrades_to_unverifiable():
    c = AsyncMock()
    c.fetchval = AsyncMock(side_effect=RuntimeError("column does not exist"))
    v = await verify_claims(c, TENANT, [claim()])
    assert v.status == UNVERIFIABLE


@pytest.mark.asyncio
async def test_a_null_from_the_database_does_not_refute():
    """A bill with no subtotal recorded means "cannot check", not "the model lied"."""
    v = await verify_claims(_conn(None, Decimal("100")), TENANT, [claim()])
    assert v.status == UNVERIFIABLE


# ── Mixed ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_refuted_claim_poisons_the_whole_finding():
    """Publishing the good half next to a wrong number costs more trust than
    dropping the item entirely."""
    v = await verify_claims(
        _conn(Decimal("100"), Decimal("200"), Decimal("500"), Decimal("100")),
        TENANT,
        [claim(op="<"), claim(op="<")],
    )
    assert v.status == REFUTED


# ── Tolerance ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rounding_is_agreement_not_disagreement():
    v = await verify_claims(_conn(Decimal("100000.00"), Decimal("100000.40")),
                            TENANT, [claim(op="==")])
    assert v.status == VERIFIED


@pytest.mark.asyncio
async def test_a_material_difference_still_refutes_an_equality():
    v = await verify_claims(_conn(Decimal("100000"), Decimal("118400")),
                            TENANT, [claim(op="==")])
    assert v.status == REFUTED


# ── String comparison ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gstin_mismatch_verifies_as_a_value_compare():
    v = await verify_claims(
        _conn("27AABCU9603R1ZX", "29AABCU9603R1ZJ"),
        TENANT,
        [{
            "kind": "value_compare",
            "left": {"tool": "bill_field", "args": {"invoice_id": BILL, "field": "invoice_number"}},
            "op": "!=",
            "right": {"tool": "vendor_field", "args": {"supplier_id": VENDOR, "field": "gstn"}},
        }],
    )
    assert v.status == VERIFIED


@pytest.mark.asyncio
async def test_vendor_field_rejects_a_column_not_on_the_allowlist():
    """The field name reaches an f-string, so the allowlist is the injection
    guard — a model-supplied column must never be interpolated."""
    from backend_ocr.app.review.verifier import _vendor_field
    c = _conn("never used")
    assert await _vendor_field(c, TENANT, {"supplier_id": VENDOR,
                                           "field": "name; DROP TABLE users"}) is None
    c.fetchval.assert_not_called()


# ── The ledger ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejects_are_logged_with_the_model_and_prompt_version():
    """This table is the hallucination rate per model and prompt version — the
    only honest read on whether open-ended detection is working."""
    c = AsyncMock()
    await log_reject(
        c, TENANT, agent_run_id=None, subject_type="bill", subject_id=BILL,
        proposed_type="invented_thing", claim={"kind": "numeric_compare"},
        reason="numeric_mismatch", model="gpt-4o-mini", prompt_version="v1",
    )
    sql = c.execute.call_args[0][0]
    assert "rh_finding_rejects" in sql
    assert c.execute.call_args[0][8] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_logging_a_reject_never_raises():
    c = AsyncMock()
    c.execute = AsyncMock(side_effect=RuntimeError("db gone"))
    await log_reject(c, TENANT, agent_run_id=None, subject_type=None, subject_id=None,
                     proposed_type=None, claim={}, reason="x", model=None, prompt_version=None)
