"""What a finding offers to show you, and what comes back when you open it.

The menu is computed server-side precisely so the hub never switches on
finding_type, which makes these tests the only place that check the rule holds:
the previews a finding gets follow from *what it is about*, never from which
detector raised it.

No database — both functions take a cursor, so a fake one pinned to the two
queries they make is enough and keeps this runnable anywhere.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

from backend_ocr.app.domain.review import previews as pv  # noqa: E402

TENANT = "10000000-0000-0000-0000-000000000001"
BILL = "22222222-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
UPLOAD = "44444444-0000-0000-0000-000000000001"
NOW = datetime(2026, 6, 25, 7, 8, 0, tzinfo=timezone.utc)

BILL_ROW = {
    "invoice_number": "VTPL/26-27/006", "invoice_date": "2026-05-07",
    "due_date": "2026-06-06", "status": "PENDING_APPROVER", "currency_code": "INR",
    "subtotal": 100, "tax_amount": 18, "total_amount": 118, "amount_paid": 0,
    "outstanding_amount": 118, "department": "Finance", "category": "Software / SaaS",
    "payment_status": "UNPAID", "upload_id": UPLOAD, "supplier_name": "Vimanotech",
    "supplier_gstin": "09AAKCP4645B1ZE", "tds_amount": None, "net_payable": None,
}


VENDOR_ROW = {
    "name": "A V INDUSTRIES", "status": "VERIFIED", "gstn": "07AAACO1234F1Z5",
    "pan": "AAACO1234F", "category": "Services", "email": None,
    "payment_terms_days": 30, "source": "MANUAL",
    "account_number": "922020055841604", "ifsc": "UTIB0005039",
    "bank_name": "AXIS BANK", "name_at_bank": "A.V. INDUSTRIES",
    "account_verified": True, "total_bills": 5,
    "total_billed": 664773, "outstanding": 664773,
}


class FakeCursor:
    """Answers the queries this module makes, and nothing else."""

    def __init__(self, bill=None, chain=(), vendor=None):
        self._bill = bill
        self._chain = list(chain)
        self._vendor = vendor
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql

    def fetchone(self):
        # Both queries mention both tables — the bill query joins dim_suppliers
        # for the supplier name, the vendor query counts fact_invoices in
        # subselects — so they are told apart by what they select FROM.
        if "FROM dim_suppliers" in self._last:
            return self._vendor
        assert "fact_invoices" in self._last, self._last
        return self._bill

    def fetchall(self):
        assert "bill_approval_chain" in self._last, self._last
        return self._chain


def finding(subject_type="bill", subject_id=BILL):
    return {"subject_type": subject_type, "subject_id": subject_id}


# ── The menu ─────────────────────────────────────────────────────────────────

def test_a_bill_offers_its_chain_its_fields_and_its_page():
    menu = pv.previews_for(FakeCursor(BILL_ROW), TENANT, finding(), {})
    assert [p["id"] for p in menu] == ["chain", "details", "document"]
    assert [p["kind"] for p in menu] == ["approval_chain", "fields", "document"]
    assert menu[2]["document"]["upload_id"] == UPLOAD


def test_the_detectors_own_document_wins_over_the_one_derived_from_the_bill():
    """The curated reference names the string worth finding on the page, and for
    a misread that highlight is most of the value. Losing it to a generic
    reference is a silent downgrade, so it is pinned here."""
    curated = {"upload_id": "other", "label": "PRU/079/2026-27",
               "highlight": "09AAKCP6454B1ZE"}
    menu = pv.previews_for(FakeCursor(BILL_ROW), TENANT, finding(),
                           {"kind": "code_diff", "document_ref": curated})
    doc = next(p for p in menu if p["id"] == "document")
    assert doc["document"] == curated


def test_a_vendor_finding_offers_its_card_and_the_page_it_was_read_from():
    curated = {"upload_id": UPLOAD, "label": "PRU/079/2026-27", "highlight": "09AAKCP6454B1ZE"}
    menu = pv.previews_for(FakeCursor(None), TENANT, finding("vendor", VENDOR),
                           {"document_ref": curated})
    assert [p["id"] for p in menu] == ["vendor", "document"]
    # Never the bill-only pair: a vendor has no chain and no key details of the
    # kind a bill has, and offering empty panes teaches people not to click.
    assert "chain" not in [p["id"] for p in menu]
    # Named differently from a bill's own page. The exact wording is a copy
    # decision and may change; that the two are distinguishable is the contract,
    # because on a vendor finding the document belongs to a different record and
    # saying "Bill PDF" would imply this finding is about that bill.
    bill_menu = pv.previews_for(FakeCursor(BILL_ROW), TENANT, finding(), {})
    bill_label = next(p["label"] for p in bill_menu if p["id"] == "document")
    doc_label = next(p["label"] for p in menu if p["id"] == "document")
    assert doc_label != bill_label


def test_a_bill_with_no_stored_page_offers_no_document():
    menu = pv.previews_for(FakeCursor({**BILL_ROW, "upload_id": None}), TENANT, finding(), {})
    assert [p["id"] for p in menu] == ["chain", "details"]


def test_a_finding_about_neither_offers_nothing():
    assert pv.previews_for(FakeCursor(None), TENANT, finding("tenant", None), {}) == []


# ── The payloads ─────────────────────────────────────────────────────────────

CHAIN = [
    {"id": 1, "step": 0, "actor_role": "approver", "actor_name": "Javed", "action": "submit",
     "comment": "Invoice for April", "created_at": NOW, "target_step": None},
    {"id": 2, "step": 1, "actor_role": "finance_controller", "actor_name": "Asha",
     "action": "send_back", "comment": "PO missing", "created_at": NOW, "target_step": 0},
]


def test_the_chain_carries_the_raw_rows_the_stepper_draws_from():
    """The shared ApprovalStepper works out which node is done, active or sent
    back from these rows. Shaping them here instead would give the hub a second
    opinion on where a bill is, and two answers to that is worse than one."""
    out = pv.build_preview(FakeCursor(BILL_ROW, CHAIN), TENANT, finding(), {}, "chain")
    ev = out["evidence"]
    assert ev["kind"] == "approval_chain"
    assert ev["status"] == "PENDING_APPROVER"
    # Per-department chains differ; without this the stepper draws the default.
    assert ev["department"] == "Finance"
    assert [r["step"] for r in ev["chain"]] == [0, 1]
    assert ev["chain"][1]["target_step"] == 0
    assert ev["chain"][0]["created_at"] == NOW.isoformat()


def test_the_activity_reads_as_an_audit_with_who_when_and_what_they_said():
    out = pv.build_preview(FakeCursor(BILL_ROW, CHAIN), TENANT, finding(), {}, "chain")
    steps = out["evidence"]["activity"]["steps"]
    assert out["evidence"]["activity"]["kind"] == "timeline"
    assert steps[0]["label"] == "Submit"
    assert steps[0]["actor"] == "Javed · Approver"
    assert steps[0]["note"] == "Invoice for April"
    assert steps[0]["at"] == NOW.isoformat()
    # A send-back has to read differently from an approval at a glance.
    assert steps[1]["state"] == "blocked"
    # Where it sits now, or the audit ends at the last thing that happened and
    # reads as though the bill is finished.
    assert steps[-1]["state"] == "current"
    assert steps[-1]["label"] == "Pending Approver"


def test_a_settled_bill_gets_no_waiting_step():
    out = pv.build_preview(FakeCursor({**BILL_ROW, "status": "APPROVED"}, []),
                           TENANT, finding(), {}, "chain")
    assert out["evidence"]["activity"]["steps"] == []
    assert "Nothing has been recorded" in out["evidence"]["activity"]["note"]


def test_details_keep_rupees_numeric_and_label_every_other_currency():
    inr = pv.build_preview(FakeCursor(BILL_ROW), TENANT, finding(), {}, "details")
    total = next(f for f in inr["evidence"]["fields"] if f["label"] == "Total amount")
    assert total["value"] == 118.0

    usd = pv.build_preview(FakeCursor({**BILL_ROW, "currency_code": "USD"}),
                           TENANT, finding(), {}, "details")
    total = next(f for f in usd["evidence"]["fields"] if f["label"] == "Total amount")
    # The client's money formatter is rupee-only; a dollar bill shown as ₹ is
    # worse than no preview at all.
    assert total["value"] == "USD 118.00"


def test_empty_fields_are_dropped_rather_than_shown_as_dashes():
    thin = {**BILL_ROW, "department": None, "category": "", "supplier_gstin": None}
    out = pv.build_preview(FakeCursor(thin), TENANT, finding(), {}, "details")
    labels = [f["label"] for f in out["evidence"]["fields"]]
    assert "Department" not in labels and "Category" not in labels
    assert "Vendor GSTIN" not in labels


@pytest.mark.parametrize("preview_id", ["chain", "details"])
def test_a_bill_only_preview_is_refused_on_a_vendor_finding(preview_id):
    """The endpoint turns None into a 404, so this is what stops a vendor
    finding answering for a bill it has no relationship to."""
    out = pv.build_preview(FakeCursor(None), TENANT, finding("vendor", VENDOR), {}, preview_id)
    assert out is None


def test_an_unknown_preview_id_is_refused():
    assert pv.build_preview(FakeCursor(BILL_ROW), TENANT, finding(), {}, "nonsense") is None


# ── The vendor card, in the drawer ───────────────────────────────────────────

def test_a_vendor_finding_offers_the_vendor_card():
    menu = pv.previews_for(FakeCursor(None), TENANT, finding("vendor", VENDOR), {})
    assert [p["id"] for p in menu] == ["vendor"]
    assert menu[0]["kind"] == "fields"


def test_the_vendor_preview_shows_the_account_the_card_shows():
    """The whole point on a bank finding: the user reading "never verified"
    must be able to see the account that claim is about without leaving."""
    out = pv.build_preview(FakeCursor(vendor=VENDOR_ROW), TENANT,
                           finding("vendor", VENDOR), {}, "vendor")
    fields = {f["label"]: f["value"] for f in out["evidence"]["fields"]}
    assert fields["Account number"] == "922020055841604"
    assert fields["IFSC"] == "UTIB0005039"
    assert fields["Name at bank"] == "A.V. INDUSTRIES"
    assert fields["Account verified"] == "Yes"
    assert fields["Outstanding"] == 664773.0


@pytest.mark.parametrize("verified,expected", [
    (True, "Yes"),
    (False, "No"),
    # The tri-state: verification gave up rather than failed, and saying "No"
    # would send the user to retry something that will not be retried.
    (None, "Could not be checked"),
])
def test_the_three_bank_states_read_differently(verified, expected):
    out = pv.build_preview(FakeCursor(vendor={**VENDOR_ROW, "account_verified": verified}),
                           TENANT, finding("vendor", VENDOR), {}, "vendor")
    fields = {f["label"]: f["value"] for f in out["evidence"]["fields"]}
    assert fields["Account verified"] == expected


def test_a_bill_finding_is_not_offered_a_vendor_card():
    """The bill's own vendor is a different record from the finding's subject;
    offering it here would put a card in front of the user for something the
    finding is not about."""
    menu = pv.previews_for(FakeCursor(BILL_ROW), TENANT, finding(), {})
    assert "vendor" not in [p["id"] for p in menu]
    assert pv.build_preview(FakeCursor(BILL_ROW), TENANT, finding(), {}, "vendor") is None
