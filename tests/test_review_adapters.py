"""Every deterministic adapter, one fake source row at a time.

The deep links are snapshotted deliberately. A finding whose link lands nowhere
is worse than no finding at all — the user believes the hub, clicks, and finds
an empty page — and a route rename in frontend/src/routes/paths.ts is exactly
the change that would break them silently.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters import ALL_ADAPTERS
from backend_ocr.app.review.types import EVIDENCE_KINDS, FAMILIES, SUBJECT_TYPES

TENANT = "10000000-0000-0000-0000-000000000001"
BILL = "22222222-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
NOW = datetime.now(timezone.utc)


def _conn(*fetches):
    c = AsyncMock()
    c.fetch = AsyncMock(side_effect=list(fetches))
    c.fetchval = AsyncMock(return_value=None)
    c.fetchrow = AsyncMock(return_value=None)
    c.execute = AsyncMock()
    return c


async def _drain(adapter, conn):
    return [d async for d in adapter.scan(conn, TENANT, 100)]


def _vendor_row(**over):
    """One row as vendor_identity's query returns it, overridable per test."""
    return {
        "supplier_id": VENDOR, "name": "Sundaram Logistics", "gstn": "29AABCU9603R1ZJ",
        "pan": "AABCU9603R", "status": "VERIFIED", "bank_verified": False,
        "has_account_row": False, "account_verified": None,
        "bank_account": "0123456789", "bank_ifsc": "HDFC0001234",
        "pan_dup_deferred": False, "exposure": Decimal("840000"), "open_bills": 3,
        "gstin_status": "ACTIVE", "error_message": None, "gstin": "29AABCU9603R1ZJ",
        **over,
    }


# ── Registry-wide invariants ──────────────────────────────────────────────────

def test_every_adapter_declares_a_unique_detector():
    dets = [a.detector for a in ALL_ADAPTERS()]
    assert len(dets) == len(set(dets)), "detector ids must be unique — the sweep keys on them"


def test_every_adapter_family_is_in_the_closed_spine():
    for a in ALL_ADAPTERS():
        assert a.family in FAMILIES


def test_every_adapter_names_a_real_service():
    from backend_ocr.app.security.services import SERVICE_KEYS
    for a in ALL_ADAPTERS():
        assert a.source_service in SERVICE_KEYS, (
            f"{a.detector} claims service {a.source_service!r}, which is not in "
            "services.py — its findings would be filtered out for every tenant"
        )


# ── ai_anomalies ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anomalies_adapter_maps_a_duplicate_bill():
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
        "anomaly_type": "DUPLICATE_BILL", "severity": "HIGH",
        "description": "same vendor, number, date and amount",
        "features_jsonb": {"duplicate_of": "INV-3391"}, "detected_at": NOW,
        "invoice_number": "INV-3402", "total_amount": Decimal("260000"),
        "supplier_id": VENDOR, "due_date": None, "supplier_name": "Kaveri Traders",
    }
    out = await _drain(AnomaliesAdapter(), _conn([row]))
    assert len(out) == 1
    f = out[0]
    assert f.finding_type == "duplicate_bill_exact"
    assert f.family == "duplication"
    assert f.deep_link == f"/bills/{BILL}?focus=total_amount"
    assert f.money_at_risk_inr == Decimal("260000")
    assert ("vendor", VENDOR, "counterparty", "Kaveri Traders") in f.entities


@pytest.mark.asyncio
async def test_anomalies_adapter_survives_an_unmapped_anomaly_type():
    """deduplication.py can add a type without this adapter knowing. Surfacing it
    generically beats dropping real signal on the floor."""
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
        "anomaly_type": "SOME_BRAND_NEW_CHECK", "severity": "MEDIUM",
        "description": "something new", "features_jsonb": {}, "detected_at": NOW,
        "invoice_number": "INV-1", "total_amount": None, "supplier_id": None,
        "due_date": None, "supplier_name": None,
    }
    out = await _drain(AnomaliesAdapter(), _conn([row]))
    assert out[0].finding_type == "some_brand_new_check"
    assert out[0].family in FAMILIES


@pytest.mark.asyncio
async def test_duplicate_vendor_points_at_the_vendor_not_the_bill():
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "SUPPLIER", "entity_id": VENDOR,
        "anomaly_type": "DUPLICATE_VENDOR", "severity": "HIGH",
        "description": "shares a bank account",
        "features_jsonb": {"match_signal": "bank_account",
                           "duplicate_of_supplier_id": "other"},
        "detected_at": NOW, "invoice_number": None, "total_amount": None,
        "supplier_id": None, "due_date": None, "supplier_name": "Deccan Supplies",
    }
    f = (await _drain(AnomaliesAdapter(), _conn([row])))[0]
    assert f.subject_type == "vendor"
    assert f.deep_link == f"/vendors/{VENDOR}/details?focus=pan_duplicate"
    assert f.required_capability == "verify"


# ── vendor_identity ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bank_tri_state_produces_two_different_findings():
    """NULL means verification has permanently given up; False means retryable.
    Telling a user to 'retry' something the system abandoned is a wrong answer."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    unverified = await _drain(VendorIdentityAdapter(), _conn([_vendor_row(bank_verified=False)]))
    abandoned = await _drain(VendorIdentityAdapter(), _conn([_vendor_row(bank_verified=None)]))

    assert unverified[0].finding_type == "vendor_bank_unverified"
    assert abandoned[0].finding_type == "vendor_bank_unverifiable"
    # The retryable one offers a retry. The abandoned one must not — re-running
    # what the system already gave up on is the wrong instruction — but it does
    # offer the remedy its own detail names: new details from the vendor.
    assert any(a["id"] == "verify_bank" for a in unverified[0].actions)
    assert not any(a["id"] == "verify_bank" for a in abandoned[0].actions)
    assert any(a["id"] == "replace_bank" for a in abandoned[0].actions)


@pytest.mark.asyncio
async def test_the_bank_action_is_editable_and_prefilled():
    """An IFSC column is free text and has held things like "New Friends Colony
    (110025) & IDFB0020102", which fails verification every time without saying
    why. The fix is one character deep, so the action carries the values rather
    than only re-sending what is already stored."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    drafts = await _drain(VendorIdentityAdapter(), _conn([_vendor_row(
        bank_verified=False, bank_account="78387375298",
        bank_ifsc="New Friends Colony (110025) & IDFB0020102")]))
    verify = next(a for a in drafts[0].actions if a["id"] == "verify_bank")

    fields = {f["key"]: f for f in verify["fields"]}
    assert fields["account_number"]["value"] == "78387375298"
    # Prefilled with the broken value, which is the thing being corrected.
    assert fields["ifsc"]["value"] == "New Friends Colony (110025) & IDFB0020102"

    import re
    pattern = fields["ifsc"]["pattern"]
    assert not re.match(pattern, fields["ifsc"]["value"])
    assert re.match(pattern, "IDFB0020102")


@pytest.mark.asyncio
async def test_the_bank_action_posts_to_the_endpoint_that_owns_verification():
    """Never a second copy of the penny-drop: the hub sends the corrected values
    to the route that verifies and records the account."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    drafts = await _drain(VendorIdentityAdapter(), _conn([_vendor_row(bank_verified=False)]))
    verify = next(a for a in drafts[0].actions if a["id"] == "verify_bank")
    assert verify["endpoint"] == f"/api/vendors/{VENDOR}/verify-bank"
    assert verify["kind"] == "safe"
    assert verify["capability"] == "verify"


@pytest.mark.asyncio
async def test_a_verified_account_row_silences_the_legacy_column():
    """dim_suppliers.bank_verified is the single-account original;
    supplier_bank_accounts superseded it and is what the vendor card shows and
    what disbursement pays from. A vendor whose Bank tab reads "verified" must
    not be told its account has never been verified."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    row = _vendor_row(bank_verified=False, has_account_row=True, account_verified=True,
                      bank_account="922020055841604", bank_ifsc="UTIB0005039")
    drafts = await _drain(VendorIdentityAdapter(), _conn([row]))
    assert not [d for d in drafts if d.finding_type.startswith("vendor_bank_")]


@pytest.mark.asyncio
async def test_an_unverified_account_row_is_retryable_never_abandoned():
    """The NULL "gave up" state belongs to the legacy column alone: once a real
    account exists it can always be penny-dropped again."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    row = _vendor_row(bank_verified=None, has_account_row=True, account_verified=False,
                      bank_account="922020055841604", bank_ifsc="UTIB0005039")
    drafts = await _drain(VendorIdentityAdapter(), _conn([row]))
    bank = [d for d in drafts if d.finding_type.startswith("vendor_bank_")][0]
    assert bank.finding_type == "vendor_bank_unverified"
    # And it shows the real account, not the em dash the legacy columns give.
    assert any("922020055841604" in str(f.get("value"))
               for f in bank.evidence["fields"])


@pytest.mark.asyncio
async def test_exposure_drives_severity():
    """A vendor with money waiting is blocking; the same vendor with no open
    bills is housekeeping."""
    from backend_ocr.app.review.adapters.vendor_identity import VendorIdentityAdapter

    def row(exposure):
        return _vendor_row(name="X", pan="P", status="FAILED", bank_verified=True,
                           bank_account=None, bank_ifsc=None, exposure=exposure,
                           open_bills=1 if exposure else 0, gstin_status="INVALID",
                           error_message="not found")

    hot = await _drain(VendorIdentityAdapter(), _conn([row(Decimal("500000"))]))
    cold = await _drain(VendorIdentityAdapter(), _conn([row(Decimal("0"))]))
    assert hot[0].severity == "HIGH"
    assert cold[0].severity == "MEDIUM"


# ── gst_name_overwrite ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_name_overwrite_is_reported_as_a_diff():
    from backend_ocr.app.review.adapters.gst_name_overwrite import GstNameOverwriteAdapter
    row = {
        "supplier_id": VENDOR, "created_at": NOW,
        "name_from": "Bharat Freight", "name_to": "Bharat Freight Carriers Pvt Ltd",
        "legal_name": "Bharat Freight Carriers Pvt Ltd", "state": "Karnataka",
        "current_name": "Bharat Freight Carriers Pvt Ltd", "gstn": "29AABCU9603R1ZJ",
    }
    f = (await _drain(GstNameOverwriteAdapter(), _conn([row])))[0]
    assert f.finding_type == "vendor_name_replaced_by_gst_portal"
    assert f.evidence["kind"] == "diff"
    assert f.evidence["rows"][0]["left"] == "Bharat Freight"


@pytest.mark.asyncio
async def test_name_overwrite_stops_once_a_human_has_renamed_it():
    """The overwrite has been dealt with; continuing to assert it would make the
    finding un-resolvable."""
    from backend_ocr.app.review.adapters.gst_name_overwrite import GstNameOverwriteAdapter
    row = {
        "supplier_id": VENDOR, "created_at": NOW,
        "name_from": "Bharat Freight", "name_to": "Bharat Freight Carriers Pvt Ltd",
        "legal_name": "x", "state": "Karnataka",
        "current_name": "Something A Human Chose", "gstn": "29AABCU9603R1ZJ",
    }
    assert await _drain(GstNameOverwriteAdapter(), _conn([row])) == []


# ── approval_stalls ───────────────────────────────────────────────────────────

def _pending_bill(n, amount, dept=None, status="PENDING_APPROVER", days=6.0):
    return {
        "invoice_id": f"2222222{n}-0000-0000-0000-00000000000{n % 10}",
        "invoice_number": f"INV-{4000 + n}", "status": status,
        "total_amount": Decimal(str(amount)), "due_date": None, "department": dept,
        "supplier_id": VENDOR, "source_updated_at": NOW - timedelta(days=days),
        "currency_code": "INR", "supplier_name": "Acme Industries", "days_waiting": days,
    }


BROKEN_CHAIN = [
    {"step_order": 0, "label": "Submitted", "role": "member", "locked": True, "assignees": None},
    {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
     "assignees": ["Praveen Pandey"]},
]


@pytest.mark.asyncio
async def test_a_broken_step_is_ONE_finding_no_matter_how_many_bills_queue_behind_it():
    """The lesson from the first real run: raising this per bill produced 271
    identical CRITICAL cards, which is the exact alert fatigue the hub exists to
    prevent. One chain step is misconfigured; that is one problem."""
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    bills = [_pending_bill(i, 100000 + i) for i in range(40)]
    conn = _conn(bills, [{"name": "Praveen Raj"}], *([BROKEN_CHAIN] * 200))

    out = await _drain(ApprovalStallsAdapter(), conn)
    orphan = [f for f in out if f.finding_type == "approval_step_has_no_valid_assignee"]
    assert len(orphan) == 1, f"expected one finding for one broken step, got {len(orphan)}"

    f = orphan[0]
    assert f.severity == "MEDIUM"  # every approval finding is P2
    assert f.required_capability == "admin"
    # The fix is in the chain config, not on any bill — so that is where it links.
    assert f.deep_link.startswith("/settings/approval-chain")
    # All the money queued behind the step, so it ranks by real consequence.
    assert f.money_at_risk_inr == sum(b["total_amount"] for b in bills)
    assert "Praveen Pandey" in f.detail


@pytest.mark.asyncio
async def test_a_near_miss_name_is_not_fuzzy_matched_away():
    """"Praveen Pandey" vs the real user "Praveen Raj" is the failure being
    reported. bill_approvals.py gates on an exact name, so this must too."""
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    conn = _conn([_pending_bill(1, 500)], [{"name": "Praveen Raj"}],
                 *([BROKEN_CHAIN] * 20))
    out = await _drain(ApprovalStallsAdapter(), conn)
    assert any(f.finding_type == "approval_step_has_no_valid_assignee" for f in out)


@pytest.mark.asyncio
async def test_two_broken_departments_are_two_findings():
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    bills = [_pending_bill(1, 100, dept="Legal"), _pending_bill(2, 200, dept="Marketing")]
    conn = _conn(bills, [{"name": "Nobody Relevant"}], *([BROKEN_CHAIN] * 40))
    out = await _drain(ApprovalStallsAdapter(), conn)
    orphan = [f for f in out if f.finding_type == "approval_step_has_no_valid_assignee"]
    assert len(orphan) == 2
    assert len({f.finding_key for f in orphan}) == 2, "the two must not collide on one key"


@pytest.mark.asyncio
async def test_a_step_with_a_real_assignee_produces_per_bill_stalls_instead():
    """"Nobody has acted" is genuinely per-bill: each is one person's decision."""
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    good_chain = [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True, "assignees": None},
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
         "assignees": ["Rhea Nair"]},
    ]
    conn = _conn([_pending_bill(1, 900)], [{"name": "Rhea Nair"}], *([good_chain] * 20))
    out = await _drain(ApprovalStallsAdapter(), conn)
    assert [f.finding_type for f in out] == ["approval_stalled"]
    assert out[0].subject_type == "bill"


@pytest.mark.asyncio
async def test_per_bill_stalls_are_capped():
    """Fifty cards saying "someone has not approved this yet" is wallpaper."""
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter, MAX_PER_BILL_STALLS
    good_chain = [
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
         "assignees": ["Rhea Nair"]},
    ]
    bills = [_pending_bill(i, 1000 + i) for i in range(80)]
    conn = _conn(bills, [{"name": "Rhea Nair"}], *([good_chain] * 400))
    out = await _drain(ApprovalStallsAdapter(), conn)
    assert len(out) == MAX_PER_BILL_STALLS


@pytest.mark.asyncio
async def test_timeline_marks_earlier_steps_done_and_the_current_one_current():
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    bill = {
        "invoice_id": BILL, "invoice_number": "INV-1", "status": "PENDING_ACCOUNTANT",
        "total_amount": Decimal("100"), "due_date": None, "department": None,
        "supplier_id": None, "source_updated_at": NOW - timedelta(days=4),
        "currency_code": "INR", "supplier_name": None, "days_waiting": 4.0,
    }
    chain = [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True, "assignees": None},
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False, "assignees": None},
        {"step_order": 2, "label": "Accountant", "role": "accountant", "locked": False, "assignees": None},
        {"step_order": 3, "label": "FC", "role": "finance_controller", "locked": False, "assignees": None},
    ]
    conn = _conn([bill], [{"name": "R"}], chain, chain)
    f = (await _drain(ApprovalStallsAdapter(), conn))[0]
    states = [s["state"] for s in f.evidence["steps"]]
    assert states == ["done", "done", "current", "pending"]


# ── staging_validation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_fx_rate_is_reported_as_fx_not_data_quality():
    """A parked foreign-currency bill is invisible everywhere else in the app —
    staging_promotion sets REVIEW and moves on, silently."""
    from backend_ocr.app.review.adapters.staging_validation import StagingValidationAdapter
    row = {
        "stg_bill_id": "s1", "bill_number": "CF-99", "bill_date": "2026-08-01",
        "due_date": None, "total_amount": Decimal("350000"), "currency_code": "USD",
        "original_amount": Decimal("4200"), "fx_rate_id": None, "vflag": "REVIEW",
        "sync_status": "PENDING", "ocr_confidence": None, "supplier_id": VENDOR,
        "upload_id": None, "supplier_name": "Cloudflare Inc", "invoice_id": None,
    }
    f = (await _drain(StagingValidationAdapter(), _conn([row])))[0]
    assert f.finding_type == "fx_rate_missing_bill_parked"
    assert f.family == "fx"
    assert f.deep_link == "/settings/fx?focus=fx.rates"


@pytest.mark.asyncio
async def test_low_confidence_extraction_is_its_own_finding():
    from backend_ocr.app.review.adapters.staging_validation import StagingValidationAdapter
    row = {
        "stg_bill_id": "s1", "bill_number": "B-1", "bill_date": "2026-08-01",
        "due_date": None, "total_amount": Decimal("1000"), "currency_code": "INR",
        "original_amount": None, "fx_rate_id": None, "vflag": "OK",
        "sync_status": "SYNCED", "ocr_confidence": Decimal("0.45"),
        "supplier_id": None, "upload_id": None, "supplier_name": None,
        "invoice_id": BILL,
    }
    out = await _drain(StagingValidationAdapter(), _conn([row]))
    assert [f.finding_type for f in out] == ["extraction_low_confidence"]
    assert out[0].key_qualifier == "ocr"


# ── Cross-cutting output contract ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_finding_has_a_renderable_evidence_shape():
    """The UI dispatches on evidence.kind. A payload without one falls back to a
    raw JSON dump, which is exactly the 'AI slop' impression to avoid."""
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
        "anomaly_type": "DUPLICATE_BILL", "severity": "HIGH", "description": "d",
        "features_jsonb": {"a": 1}, "detected_at": NOW, "invoice_number": "I",
        "total_amount": None, "supplier_id": None, "due_date": None,
        "supplier_name": None,
    }
    for f in await _drain(AnomaliesAdapter(), _conn([row])):
        assert f.evidence.get("kind") in EVIDENCE_KINDS


@pytest.mark.asyncio
async def test_no_adapter_offers_an_executable_money_action():
    """Releasing a payment or approving a bill must stay on the screen that has
    the OTP gate and the audit trail. A triage queue must never become a second,
    thinner path to an irreversible operation."""
    from backend_ocr.app.review.adapters.payment_failures import PaymentFailuresAdapter
    row = {
        "disbursement_id": "d1", "invoice_id": BILL, "supplier_id": VENDOR,
        "amount": Decimal("840000"), "status": "FAILED",
        "status_message": "beneficiary account closed", "act_code": "ACT-12",
        "beneficiary_name": "Sundaram Logistics", "beneficiary_account": "01234",
        "is_overridden": False, "created_at": NOW, "invoice_number": "INV-9",
        "due_date": None, "supplier_name": "Sundaram Logistics",
    }
    out = await _drain(PaymentFailuresAdapter(), _conn([row], []))
    assert out, "expected a finding"
    for f in out:
        for a in f.actions:
            assert a["kind"] == "navigate", f"{f.finding_type} offers an executable money action"



# ── Types the database actually hands back ────────────────────────────────────
# Every one of these was a production crash on the first real run, and every one
# passed the mocked tests above, because the mocks used friendlier types than
# asyncpg does.

@pytest.mark.asyncio
async def test_jsonb_arrives_as_a_string_not_a_dict():
    """This pool registers no jsonb codec, so asyncpg returns text. The mocks
    handed over a dict and the adapter died on .get() against real data."""
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
        "anomaly_type": "DUPLICATE_BILL", "severity": "HIGH", "description": "d",
        "features_jsonb": '{"duplicate_of": "INV-3391"}',   # a STRING
        "detected_at": NOW, "invoice_number": "INV-1", "total_amount": None,
        "supplier_id": None, "due_date": None, "supplier_name": None,
    }
    out = await _drain(AnomaliesAdapter(), _conn([row]))
    assert len(out) == 1


@pytest.mark.asyncio
async def test_malformed_jsonb_does_not_kill_the_pass():
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    row = {
        "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
        "anomaly_type": "DUPLICATE_BILL", "severity": "HIGH", "description": "d",
        "features_jsonb": "not json at all", "detected_at": NOW,
        "invoice_number": "I", "total_amount": None, "supplier_id": None,
        "due_date": None, "supplier_name": None,
    }
    assert len(await _drain(AnomaliesAdapter(), _conn([row]))) == 1


def test_a_date_due_column_does_not_crash_the_ranking():
    """fact_invoices.due_date is DATE, so asyncpg returns datetime.date, which has
    no tzinfo at all. Reading .tzinfo on one took down two whole adapters."""
    from datetime import date
    from decimal import Decimal as D
    from backend_ocr.app.review.ranking import rank_score
    score = rank_score(
        severity="HIGH", confidence=1.0, verification="VERIFIED",
        money_at_risk_inr=D("1000"), due_at=date(2026, 1, 1),
        first_detected_at=date(2026, 1, 1),
    )
    assert 0 <= float(score) <= 1


def test_an_overdue_date_still_reads_as_urgent():
    from datetime import date, datetime, timezone
    from decimal import Decimal as D
    from backend_ocr.app.review.ranking import rank_score
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    common = dict(severity="HIGH", confidence=1.0, verification="VERIFIED",
                  money_at_risk_inr=D("1000"), first_detected_at=now, now=now)
    overdue = rank_score(due_at=date(2026, 1, 1), **common)
    future = rank_score(due_at=date(2027, 1, 1), **common)
    assert overdue > future


# ── Misread GSTIN producing a duplicate vendor ────────────────────────────────
# Built from a real case: "Vodafone Idea Limited" existed twice because the OCR
# read 07AACCA2800P1Z2 for 07AAACB2100P1ZZ. The wrong GSTIN failed verification,
# and because the bill carried an unrecognised GSTIN the ingest created a second
# vendor for it — a split ledger, an unpayable vendor, and a bill on the wrong
# record. Nothing connected those three facts before.

from backend_ocr.app.review.adapters.vendor_duplicates import VendorDuplicatesAdapter, gstin_diff

WRONG = "07AACCA2800P1Z2"
RIGHT = "07AAACB2100P1ZZ"


def _dup_row(bad_gstn=WRONG, good_gstn=RIGHT, bills=1, name="Vodafone Idea Limited"):
    return {
        "bad_id": VENDOR, "bad_name": name, "bad_gstn": bad_gstn, "bad_status": "FAILED",
        "bad_source": "UPLOAD", "bad_created": NOW,
        "good_id": "44444444-0000-0000-0000-000000000001", "good_gstn": good_gstn,
        "good_pan": "AAACB2100P", "good_created": NOW - timedelta(days=100),
        "bad_bills": bills, "bad_value": Decimal("368248") if bills else Decimal("0"),
        "good_bills": 2,
        "bad_upload": "55555555-0000-0000-0000-000000000001" if bills else None,
        "bad_invoice_no": "DLSO030426607371" if bills else None,
    }


def test_gstin_diff_finds_the_substituted_characters():
    # C->A, A->B, 8->1, 2->Z: four OCR confusions in fifteen characters.
    assert gstin_diff(WRONG, RIGHT) == [4, 6, 8, 14]
    assert gstin_diff(RIGHT, RIGHT) == []
    assert gstin_diff("short", RIGHT) == []


@pytest.mark.asyncio
async def test_the_misread_duplicate_is_detected_and_explained():
    f = (await _drain(VendorDuplicatesAdapter(), _conn([_dup_row()])))[0]
    assert f.finding_type == "vendor_duplicated_by_misread_gstin"
    assert f.severity == "HIGH"           # a bill is stranded on it
    # The prose says only the consequence. Restating the GSTINs here would make
    # the reader parse in words what the comparison below shows at a glance.
    assert f.detail == "1 bill is stranded on this record and cannot be paid."
    # A character-level comparison, so the UI can mark exactly which four differ
    # rather than printing a sentence about them.
    assert f.evidence["kind"] == "code_diff"
    assert f.evidence["left"]["value"] == WRONG
    assert f.evidence["left"]["status"] == "failed"
    assert f.evidence["right"]["value"] == RIGHT
    assert f.evidence["right"]["status"] == "verified"


@pytest.mark.asyncio
async def test_it_offers_an_executable_fix_naming_both_vendors():
    f = (await _drain(VendorDuplicatesAdapter(), _conn([_dup_row()])))[0]
    fix = [a for a in f.actions if a["kind"] == "safe"]
    assert len(fix) == 1
    assert fix[0]["body"]["source_supplier_id"] == VENDOR
    assert fix[0]["body"]["target_supplier_id"] == "44444444-0000-0000-0000-000000000001"
    # Nothing writes without asking, and the ask shows the change itself rather
    # than spelling it out again in the preview line.
    confirm = fix[0]["confirm"]
    assert confirm["change"] == {"label": "Vendor GSTIN", "from": WRONG, "to": RIGHT}
    assert any(i["label"] == "Bills moved" for i in confirm["impact"])


@pytest.mark.asyncio
async def test_a_different_state_code_is_two_registrations_not_a_misread():
    """A company registered in two states legitimately has two GSTINs. Offering
    to merge those would destroy real data."""
    out = await _drain(VendorDuplicatesAdapter(),
                       _conn([_dup_row(bad_gstn="29AACCA2800P1Z2")]))
    assert out == []


@pytest.mark.asyncio
async def test_wholly_different_gstins_are_left_to_the_generic_duplicate_finding():
    out = await _drain(VendorDuplicatesAdapter(),
                       _conn([_dup_row(bad_gstn="07ZZZZZ9999Z9ZZ")]))
    assert out == []


@pytest.mark.asyncio
async def test_a_duplicate_with_no_bills_is_lower_severity():
    f = (await _drain(VendorDuplicatesAdapter(), _conn([_dup_row(bills=0)])))[0]
    assert f.severity == "MEDIUM"
    assert "No bills are attached" in f.detail


# ── Missing qty/rate: a suspicion, never a blocker ────────────────────────────

from backend_ocr.app.review.adapters.ocr_quality import OcrQualityAdapter


@pytest.mark.asyncio
async def test_a_line_with_no_qty_or_rate_is_suspected_not_asserted():
    """Plenty of real invoices bill a lump sum — the Kashti referral commissions
    in this tenant do exactly that. It is also what a misread looks like. So it
    is a prompt to look, and it must never hold up a bill."""
    row = {
        "invoice_id": BILL, "invoice_number": "KFPL/JUL16/26-27",
        "total_amount": Decimal("29428"), "due_date": None, "supplier_id": VENDOR,
        "supplier_name": "Kashti Finserv Private Limited",
        "upload_id": "55555555-0000-0000-0000-000000000001",
        "bare_lines": 1, "bare_total": Decimal("24939"),
        "lines": [{"line": 1, "description": "KASHTI REFERRAL COMISSION", "total": 24939}],
    }
    f = (await _drain(OcrQualityAdapter(), _conn([row])))[0]
    assert f.finding_type == "line_amount_without_qty_or_rate"
    assert f.severity == "LOW"
    # UNVERIFIABLE routes it to the Suspected lane and out of the badge counts.
    assert f.verification == "UNVERIFIABLE"
    # Nothing here says the amount is wrong, only that it cannot be recomputed.
    assert f.money_at_risk is None
    assert all(a["kind"] == "navigate" for a in f.actions)


@pytest.mark.asyncio
async def test_the_suspected_lane_is_where_it_lands():
    from backend_ocr.app.review.ranking import lane_for
    row = {
        "invoice_id": BILL, "invoice_number": "X", "total_amount": Decimal("1"),
        "due_date": None, "supplier_id": None, "supplier_name": None,
        "upload_id": None, "bare_lines": 2, "bare_total": Decimal("1"), "lines": [],
    }
    f = (await _drain(OcrQualityAdapter(), _conn([row])))[0]
    assert lane_for(f.family, f.verification) == "suspected"


@pytest.mark.asyncio
async def test_it_reports_once_per_bill_not_once_per_line():
    """102 such lines exist in one tenant. One card per line would bury the queue."""
    rows = [{
        "invoice_id": BILL, "invoice_number": "X", "total_amount": Decimal("100"),
        "due_date": None, "supplier_id": None, "supplier_name": None,
        "upload_id": None, "bare_lines": 8, "bare_total": Decimal("100"), "lines": [],
    }]
    out = await _drain(OcrQualityAdapter(), _conn(rows))
    assert len(out) == 1
    assert "8 lines" in out[0].detail



# ── The source document travels with the finding ──────────────────────────────
# For anything that turns on "did we read this right?", the user has to be able
# to look at the page. Without it they leave the hub to judge, which is the one
# thing the hub exists to avoid.

@pytest.mark.asyncio
async def test_the_misread_finding_carries_the_document_it_was_read_from():
    f = (await _drain(VendorDuplicatesAdapter(), _conn([_dup_row()])))[0]
    doc = f.evidence.get("document_ref")
    assert doc, "no document to compare the GSTIN against"
    assert doc["upload_id"] == "55555555-0000-0000-0000-000000000001"
    # The drawer shows this beside the preview so the eye knows what to look for.
    assert doc["highlight"] == WRONG


@pytest.mark.asyncio
async def test_no_document_when_no_bill_is_attached():
    f = (await _drain(VendorDuplicatesAdapter(), _conn([_dup_row(bills=0)])))[0]
    assert f.evidence.get("document_ref") is None


@pytest.mark.asyncio
async def test_the_qty_rate_finding_carries_its_bill():
    row = {
        "invoice_id": BILL, "invoice_number": "KFPL/JUL16/26-27",
        "total_amount": Decimal("29428"), "due_date": None, "supplier_id": VENDOR,
        "supplier_name": "Kashti", "upload_id": "55555555-0000-0000-0000-000000000001",
        "bare_lines": 1, "bare_total": Decimal("24939"), "lines": [],
    }
    f = (await _drain(OcrQualityAdapter(), _conn([row])))[0]
    assert f.evidence["document_ref"]["upload_id"] == "55555555-0000-0000-0000-000000000001"


def test_document_ref_is_none_without_an_upload():
    from backend_ocr.app.review.types import document_ref
    assert document_ref(None) is None
    assert document_ref("")  is None



# ── Findings say one thing ────────────────────────────────────────────────────

def _words(text: str) -> int:
    import re
    return len(re.findall(r"[\w'’%₹.-]+", text or ""))


@pytest.mark.asyncio
async def test_no_finding_explains_itself_in_a_paragraph():
    """The card and the evidence carry the facts; the detail line carries the
    consequence. When both do the job, the user reads everything twice — which is
    what makes a queue feel like work rather than a list of decisions.

    One sentence, ~25 words. If a finding genuinely needs more, it belongs in the
    evidence payload where it can be *shown*."""
    from backend_ocr.app.review.adapters.anomalies import AnomaliesAdapter
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter

    cases = [
        (VendorDuplicatesAdapter(), [_dup_row()]),
        (VendorDuplicatesAdapter(), [_dup_row(bills=0)]),
        (OcrQualityAdapter(), [{
            "invoice_id": BILL, "invoice_number": "X", "total_amount": Decimal("1"),
            "due_date": None, "supplier_id": None, "supplier_name": None,
            "upload_id": None, "bare_lines": 3, "bare_total": Decimal("1"), "lines": [],
        }]),
        (AnomaliesAdapter(), [{
            "anomaly_id": "a1", "entity_type": "BILL", "entity_id": BILL,
            "anomaly_type": "DUPLICATE_BILL", "severity": "HIGH", "description": "d",
            "features_jsonb": {}, "detected_at": NOW, "invoice_number": "I",
            "total_amount": None, "supplier_id": None, "due_date": None,
            "supplier_name": None,
        }]),
    ]

    for adapter, rows in cases:
        for f in await _drain(adapter, _conn(rows)):
            n = _words(f.detail)
            assert n <= 25, f"{f.finding_type}: detail is {n} words — {f.detail!r}"
            assert _words(f.title) <= 14, f"{f.finding_type}: title too long"


@pytest.mark.asyncio
async def test_the_orphaned_step_finding_is_one_line_too():
    from backend_ocr.app.review.adapters.approval_stalls import ApprovalStallsAdapter
    bills = [_pending_bill(i, 1000 + i) for i in range(5)]
    conn = _conn(bills, [{"name": "Praveen Raj"}], *([BROKEN_CHAIN] * 60))
    orphan = [f for f in await _drain(ApprovalStallsAdapter(), conn)
              if f.finding_type == "approval_step_has_no_valid_assignee"]
    assert orphan
    assert _words(orphan[0].detail) <= 25, orphan[0].detail
