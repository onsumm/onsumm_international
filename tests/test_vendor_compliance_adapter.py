"""backend_ocr/app/review/adapters/vendor_compliance.py — completeness & verification
posture, the "is anything on file at all" complement to vendor_identity.py's
"is what's on file correct".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters.vendor_compliance import VendorComplianceAdapter
from backend_ocr.app.review.types import EVIDENCE_KINDS, FAMILIES, SUBJECT_TYPES

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def _conn(vendor_rows, policy_row=None):
    c = AsyncMock()
    c.fetchrow = AsyncMock(return_value=policy_row)
    c.fetch = AsyncMock(return_value=vendor_rows)
    c.fetchval = AsyncMock(return_value=None)
    c.execute = AsyncMock()
    return c


async def _drain(conn, limit=100):
    return [d async for d in VendorComplianceAdapter().scan(conn, TENANT, limit)]


def _vendor_row(**over):
    base = dict(
        supplier_id=VENDOR, name="Acme Traders", pan="AABCU9603R",
        gstn="29AABCU9603R1ZJ", kyb_status="VERIFIED",
        created_at=NOW - timedelta(days=90),
        primary_gstin="29AABCU9603R1ZJ", primary_gstin_status="VERIFIED",
        has_bank_row=True, has_verified_bank=True, exposure=Decimal("0"),
    )
    base.update(over)
    return base


def test_declares_a_real_service_and_family():
    a = VendorComplianceAdapter()
    from backend_ocr.app.security.services import SERVICE_KEYS
    assert a.source_service in SERVICE_KEYS
    assert a.family in FAMILIES


@pytest.mark.asyncio
async def test_a_fully_compliant_vendor_produces_nothing():
    out = await _drain(_conn([_vendor_row()]))
    assert out == []


@pytest.mark.asyncio
async def test_missing_pan_is_flagged():
    out = await _drain(_conn([_vendor_row(pan=None)]))
    types = [f.finding_type for f in out]
    assert "vendor_pan_missing" in types
    f = next(f for f in out if f.finding_type == "vendor_pan_missing")
    assert f.subject_type in SUBJECT_TYPES
    assert f.evidence["kind"] in EVIDENCE_KINDS
    assert f.key_qualifier == "pan"


@pytest.mark.asyncio
async def test_missing_gstin_is_flagged_when_no_legacy_value_either():
    out = await _drain(_conn([_vendor_row(gstn=None, primary_gstin=None,
                                           primary_gstin_status=None)]))
    assert any(f.finding_type == "vendor_gstin_missing" for f in out)


@pytest.mark.asyncio
async def test_legacy_gstn_with_no_supplier_gstins_row_counts_as_present():
    """A vendor whose only GSTIN lives on the legacy dim_suppliers.gstn column
    (never migrated into supplier_gstins) must not be told it has none."""
    out = await _drain(_conn([_vendor_row(primary_gstin=None, primary_gstin_status=None,
                                           gstn="29AABCU9603R1ZJ", kyb_status="VERIFIED")]))
    assert not any(f.finding_type.startswith("vendor_gstin") for f in out)


@pytest.mark.asyncio
async def test_legacy_gstn_unverified_kyb_status_is_flagged():
    out = await _drain(_conn([_vendor_row(primary_gstin=None, primary_gstin_status=None,
                                           gstn="29AABCU9603R1ZJ", kyb_status="UNVERIFIED")]))
    assert any(f.finding_type == "vendor_gstin_unverified" for f in out)


@pytest.mark.asyncio
async def test_supplier_gstins_row_present_but_not_verified_is_flagged():
    out = await _drain(_conn([_vendor_row(primary_gstin_status="UNVERIFIED")]))
    assert any(f.finding_type == "vendor_gstin_unverified" for f in out)


@pytest.mark.asyncio
async def test_no_bank_row_at_all_is_missing_not_unverified():
    out = await _drain(_conn([_vendor_row(has_bank_row=False, has_verified_bank=False)]))
    types = [f.finding_type for f in out]
    assert "vendor_bank_missing" in types
    assert "vendor_bank_unverified" not in types


@pytest.mark.asyncio
async def test_bank_row_present_but_none_verified_is_unverified_not_missing():
    out = await _drain(_conn([_vendor_row(has_bank_row=True, has_verified_bank=False)]))
    types = [f.finding_type for f in out]
    assert "vendor_bank_unverified" in types
    assert "vendor_bank_missing" not in types


@pytest.mark.asyncio
async def test_severity_is_high_only_when_money_is_actually_blocked():
    clean_money = await _drain(_conn([_vendor_row(pan=None, exposure=Decimal("0"))]))
    blocked_money = await _drain(_conn([_vendor_row(pan=None, exposure=Decimal("50000"))]))
    assert clean_money[0].severity == "MEDIUM"
    assert blocked_money[0].severity == "HIGH"


@pytest.mark.asyncio
async def test_missing_policy_row_falls_back_to_sane_defaults():
    """The adapter must work before scripts/comm_agent_migrate.py has ever run
    — the row it seeds may not exist yet."""
    conn = _conn([_vendor_row(pan=None)], policy_row=None)
    out = await _drain(conn)
    assert any(f.finding_type == "vendor_pan_missing" for f in out)
    # Default grace_days_new_vendor=7 must still reach the query.
    args, _ = conn.fetch.call_args
    assert args[2] == "7"


@pytest.mark.asyncio
async def test_a_disabled_requirement_produces_no_finding_for_it():
    policy_row = {"config": '{"requires_pan": false, "requires_gstin": true, '
                             '"requires_bank": true, "grace_days_new_vendor": 7}'}
    out = await _drain(_conn([_vendor_row(pan=None)], policy_row=policy_row))
    assert not any(f.finding_type == "vendor_pan_missing" for f in out)


@pytest.mark.asyncio
async def test_action_always_navigates_to_the_vendor_page():
    out = await _drain(_conn([_vendor_row(pan=None)]))
    f = out[0]
    assert len(f.actions) == 1
    assert f.actions[0]["kind"] == "navigate"
    assert f"/vendors/{VENDOR}" in f.actions[0]["href"]
