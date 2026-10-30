"""Everything miscoded on one bill, as one finding with several answers.

Two things carry the weight here.

The GST rule, because it is the one proposal derived from law rather than from
habit: CGST+SGST within one state, IGST across two, and place of supply beats
the buyer's own address when the invoice prints one. Getting that backwards
would tell people to "correct" bills that were already right.

And partial acceptance. The finding bundles four unrelated kinds of wrong so
they can be judged separately; if the last step were all-or-nothing, someone
who agrees about the category and not the cost centre would take neither.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend_ocr.app.review.adapters.bill_coding import (
    BillCodingAdapter, _gst_proposal, _is_habit, _state,
)
from backend_ocr.app.routers import review_hub as rh

TENANT = "10000000-0000-0000-0000-000000000001"
BILL = "22222222-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
STG = "44444444-0000-0000-0000-000000000001"


def _row(**over):
    """One bill as the adapter's query returns it. Maharashtra vendor, Delhi
    buyer — the case from the Signzy invoice."""
    base = {
        "invoice_id": BILL, "invoice_number": "SIGNZY2627/02991",
        "supplier_id": VENDOR, "stg_bill_id": STG, "upload_id": None,
        "category": "Telecom", "department": "Marketing", "cost_center_id": None,
        "supplier_name": "Signzy Technologies Pvt. Ltd.",
        "vendor_gstn": "27AAWCS3552Q1ZA", "buyer_gstn": "07AACCA2775M1ZF",
        "cgst_amount": Decimal("203935.93"), "sgst_amount": Decimal("203935.92"),
        "igst_amount": None, "cess_amount": None,
        "cgst_rate": Decimal("9"), "sgst_rate": Decimal("9"), "igst_rate": None,
        "tax_amount": Decimal("407871.85"), "place_of_supply": None,
        "usual_category": "Software / SaaS", "cat_same_n": 11, "cat_total": 12,
        "usual_department": "Finance", "dept_same_n": 3, "dept_total": 4,
        # 07 Sept -> 07 Oct is Net 30; the vendor master says 45.
        "invoice_date": date(2026, 9, 7), "due_date": date(2026, 10, 7),
        "payment_terms_days": 45,
        "usual_cc": None, "cc_same_n": None, "cc_total": None,
        "usual_cc_name": None, "bill_cc_name": None,
    }
    base.update(over)
    return base


# ── The GST rule ──────────────────────────────────────────────────────────────

def test_a_gstin_names_its_state():
    assert _state("27AAWCS3552Q1ZA") == "27"
    assert _state("07AACCA2775M1ZF") == "07"
    assert _state("") is None
    assert _state("GSTIN") is None          # not digits, not a state code
    assert _state(None) is None


def test_cgst_sgst_across_two_states_is_wrong():
    """The reported case: vendor 27, buyer 07, and the invoice still charged
    CGST+SGST."""
    p = _gst_proposal(_row())

    assert p["id"] == "gst_split"
    assert "CGST + SGST" in p["current"]
    assert p["proposed"].startswith("IGST @ 18%")
    assert "27 → 07" in p["why"]
    # The rupees move between columns; they are not re-taxed.
    assert p["amount"] == pytest.approx(407871.85)
    assert p["rate"] == 18


def test_igst_within_one_state_is_equally_wrong():
    p = _gst_proposal(_row(
        buyer_gstn="27AACCA2775M1ZF",
        cgst_amount=None, sgst_amount=None, cgst_rate=None, sgst_rate=None,
        igst_amount=Decimal("407871.85"), igst_rate=Decimal("18"),
    ))
    assert "IGST" in p["current"]
    assert p["proposed"].startswith("CGST + SGST @ 9% each")
    assert p["half"] == pytest.approx(203935.925)


def test_a_correctly_taxed_bill_proposes_nothing():
    same_state = _row(buyer_gstn="27AACCA2775M1ZF")
    assert _gst_proposal(same_state) is None

    interstate_igst = _row(
        cgst_amount=None, sgst_amount=None,
        igst_amount=Decimal("407871.85"), igst_rate=Decimal("18"),
    )
    assert _gst_proposal(interstate_igst) is None


def test_place_of_supply_beats_the_buyers_own_state():
    """Goods delivered to another state are taxed where they land. A rule that
    reads only the buyer's GSTIN calls that bill wrong when it is right — which
    is why place_of_supply is extracted at all."""
    # Vendor 27, buyer 27, but supplied to 06: inter-state after all, so
    # CGST+SGST really is wrong here.
    p = _gst_proposal(_row(buyer_gstn="27AACCA2775M1ZF",
                           place_of_supply="Haryana (06)"))
    assert p is not None
    assert "27 → 06" in p["why"]

    # And the mirror: vendor 27, buyer 07, supplied back into 27.
    assert _gst_proposal(_row(place_of_supply="27 Maharashtra")) is None


def test_no_gst_charged_is_not_a_gst_problem():
    assert _gst_proposal(_row(
        cgst_amount=None, sgst_amount=None, igst_amount=None,
    )) is None


def test_an_unreadable_gstin_makes_no_claim():
    """Half the state pair is no basis for calling a bill wrong."""
    assert _gst_proposal(_row(vendor_gstn=None)) is None
    assert _gst_proposal(_row(buyer_gstn="")) is None


def test_the_gst_row_says_what_accepting_it_does_not_do():
    p = _gst_proposal(_row())
    assert "revised invoice" in p["caveat"]
    # Offered all the same — the user asked for one click, and our books are
    # ours to correct.
    assert p["writable"] is True


# ── The habit rule ───────────────────────────────────────────────────────────

def test_a_habit_needs_a_floor_and_a_majority():
    assert _is_habit(11, 12) is True
    assert _is_habit(3, 4) is True
    assert _is_habit(2, 3) is False          # below the floor
    assert _is_habit(4, 6) is False          # no clear majority
    assert _is_habit(None, None) is False


# ── One finding, several answers ─────────────────────────────────────────────

def _conn(rows, lines=()):
    c = AsyncMock()
    c.fetch = AsyncMock(side_effect=[list(rows)] + [list(lines)] * max(len(rows), 1))
    return c


async def _drain(conn):
    return [d async for d in BillCodingAdapter().scan(conn, TENANT, 100)]


@pytest.mark.asyncio
async def test_four_kinds_of_wrong_arrive_as_one_finding():
    lines = [{"stg_bill_id": STG, "line_number": 3, "product_name": "AWS Support",
              "account_id": "55555555-0000-0000-0000-000000000001",
              "account_name": "Cloud hosting", "uses": 9}]
    [f] = await _drain(_conn([_row()], lines))

    assert f.subject_type == "bill" and f.subject_id == BILL
    ids = [p["id"] for p in f.evidence["proposals"]]
    assert ids == ["gst_split", "category", "department", "payment_terms", "ledger:3"]
    assert f.evidence["kind"] == "proposals"
    assert "5 things to correct" in f.title


@pytest.mark.asyncio
async def test_a_wrong_tax_split_lifts_the_whole_finding():
    """A miscategorised bill is a filing nuisance; an unclaimable credit is
    not."""
    [with_gst] = await _drain(_conn([_row()]))
    assert with_gst.severity == "HIGH"

    [coding_only] = await _drain(_conn([_row(buyer_gstn="27AACCA2775M1ZF")]))
    assert coding_only.severity == "MEDIUM"


@pytest.mark.asyncio
async def test_a_bill_with_nothing_wrong_raises_nothing():
    clean = _row(
        buyer_gstn="27AACCA2775M1ZF",       # correctly intra-state
        category="Software / SaaS",          # matches the habit
        department="Finance",
        due_date=date(2026, 10, 22),         # 45 days, as agreed
    )
    assert await _drain(_conn([clean])) == []


@pytest.mark.asyncio
async def test_the_proposal_set_is_the_condition():
    """Re-raises when what we propose changes, stays quiet while it is the same
    set — otherwise every pass would look like new news."""
    [a] = await _drain(_conn([_row()]))
    [b] = await _drain(_conn([_row()]))
    assert a.condition_hash == b.condition_hash

    [c] = await _drain(_conn([_row(usual_category="Cloud")]))
    assert c.condition_hash != a.condition_hash


# ── Applying some of them ────────────────────────────────────────────────────

class FakeCursor:
    def __init__(self, finding_evidence, fail_on=None):
        self._finding = {
            "finding_id": "f1", "evidence_jsonb": {"proposals": finding_evidence},
        }
        self._fail_on = fail_on
        self.calls: list[tuple] = []
        self.rowcount = 1
        self._next = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self._fail_on and self._fail_on in str(params):
            raise RuntimeError("boom")
        if "FROM rh_findings" in sql:
            self._next = self._finding
        elif "FROM fact_invoices" in sql:
            self._next = {"invoice_id": BILL, "stg_bill_id": STG}
        elif "FROM cost_centers" in sql:
            self._next = {"id": "66666666-0000-0000-0000-000000000001"}
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass


PROPOSALS = [
    {"id": "payment_terms", "label": "Payment terms", "current": "Net 30 days",
     "proposed": "Net 45 days", "writable": True, "days": 45},
    {"id": "category", "label": "Category", "current": "Telecom",
     "proposed": "Software / SaaS", "writable": True},
    {"id": "department", "label": "Department", "current": "Marketing",
     "proposed": "Finance", "writable": True},
    {"id": "cost_centre", "label": "Cost centre", "current": None,
     "proposed": "CC-07 Support", "writable": True},
    {"id": "gst_split", "label": "Tax charged", "current": "CGST + SGST",
     "proposed": "IGST @ 18%", "writable": True,
     "amount": 407871.85, "rate": 18.0},
]


def _apply(accept, proposals=PROPOSALS, fail_on=None):
    cur = FakeCursor(proposals, fail_on)
    with patch.object(rh, "get_conn", return_value=FakeConn(cur)), \
         patch.object(rh, "put_conn", MagicMock()), \
         patch.object(rh, "require_capability", MagicMock()), \
         patch.object(rh, "current_username", return_value="vibhanshu"), \
         patch.object(rh, "visibility_sql", return_value=("TRUE", {})), \
         patch.object(rh, "_write_event", MagicMock()):
        out = rh.apply_bill_coding(
            rh.BillCodingApply(finding_id="f1", invoice_id=BILL, accept=accept),
            TENANT,
        )
    return out, cur


def test_taking_one_correction_leaves_the_others_alone():
    out, cur = _apply(["category"])

    assert out["applied"] == ["category"]
    assert out["counts"] == {"applied": 1, "failed": 0}
    writes = " ".join(sql for sql, _ in cur.calls)
    assert "UPDATE fact_invoices SET category" in writes
    assert "cost_center_id" not in writes
    assert "igst_amount" not in writes


def test_the_gst_fix_moves_the_rupees_between_columns_and_does_not_re_tax():
    out, cur = _apply(["gst_split"])

    assert out["applied"] == ["gst_split"]
    sql, params = next((s, p) for s, p in cur.calls if "igst_amount = %s" in s)
    assert "cgst_amount = NULL" in sql and "sgst_amount = NULL" in sql
    assert params[0] == pytest.approx(407871.85)
    # tax_amount is the total, and the total has not changed.
    assert "tax_amount" not in sql


def test_a_cost_centre_is_resolved_by_name_under_the_tenant():
    """Never by an id from the request body — a proposal is data we sent out,
    and what comes back is not evidence of anything."""
    _out, cur = _apply(["cost_centre"])

    lookup, params = next((s, p) for s, p in cur.calls if "FROM cost_centers" in s)
    assert "tenant_id = %s" in lookup
    assert params[0] == TENANT


def test_one_failure_costs_only_itself():
    out, cur = _apply(["category", "cost_centre"], fail_on="Software / SaaS")

    assert out["applied"] == ["cost_centre"]
    assert [f["id"] for f in out["failed"]] == ["category"]
    savepoints = [s for s, _ in cur.calls if "SAVEPOINT" in s]
    assert "ROLLBACK TO SAVEPOINT coding_item" in savepoints
    assert savepoints.count("RELEASE SAVEPOINT coding_item") == 1


def test_a_proposal_the_finding_no_longer_makes_is_refused():
    """The finding is re-read from the database; the request does not get to
    say what was proposed."""
    out, _cur = _apply(["ledger:9"])
    assert out["applied"] == []
    assert out["failed"][0]["error"] == "no longer proposed"


def test_an_empty_selection_is_a_bad_request_not_a_no_op():
    with pytest.raises(HTTPException) as exc:
        _apply([])
    assert exc.value.status_code == 400


def test_applying_wakes_the_adapter_so_the_finding_re_scans_now():
    """The user is watching the drawer; waiting for the next unrelated write
    to fact_invoices would look like nothing happened."""
    _out, cur = _apply(["category"])
    assert any("pg_notify" in s and "ch_review_adapters" in s for s, _ in cur.calls)


def test_the_finding_is_not_closed_here():
    """scan() yields the current truth, so the adapter decides. Closing it from
    both ends is how a list and its badge start disagreeing."""
    _out, cur = _apply(["category"])
    writes = " ".join(s for s, _ in cur.calls)
    assert "resolved_at" not in writes
    assert "UPDATE rh_findings" not in writes


@pytest.mark.asyncio
async def test_no_second_apply_button_is_offered_from_the_drawer():
    """The drawer renders `actions` as buttons and the proposals panel carries
    its own Apply, so declaring one put two Apply buttons on the same screen.

    The declared one could not have worked either: which corrections to take is
    a decision the panel holds, and an action body cannot carry it — it would
    have posted an empty selection and 400'd.
    """
    [f] = await _drain(_conn([_row()]))

    assert [a["kind"] for a in f.actions] == ["navigate"]
    assert not any(a["kind"] == "safe" for a in f.actions)


@pytest.mark.asyncio
async def test_department_drift_moved_here_from_the_retired_detector():
    """vendor_field_drift raised one finding per field, each with its own
    "File it as Finance" button. Department was the only column it covered that
    bill_coding did not, so it moves rather than disappearing."""
    [f] = await _drain(_conn([_row(buyer_gstn="27AACCA2775M1ZF",
                                   category="Software / SaaS",
                                   due_date=date(2026, 10, 22))]))

    [dept] = [p for p in f.evidence["proposals"] if p["id"] == "department"]
    assert dept["current"] == "Marketing"
    assert dept["proposed"] == "Finance"
    assert dept["why"] == "3 of this vendor's last 4 bills went to the Finance department"


def test_a_department_is_written_to_the_bill():
    out, cur = _apply(["department"])

    assert out["applied"] == ["department"]
    sql, params = next((s, p) for s, p in cur.calls if "SET department" in s)
    assert params[0] == "Finance"
    # Scoped to the tenant, like every other write in this endpoint.
    assert "tenant_id = %s" in sql


def test_a_bill_carrying_all_three_taxes_is_flagged_and_repairable():
    """The state a re-upload used to leave behind: CGST and SGST and IGST all
    populated, GST total double the truth. Ingest no longer produces it, but
    bills already in it must still be reachable — and the fix that applies is
    the right one, because setting IGST NULLs the other two."""
    p = _gst_proposal(_row(igst_amount=Decimal("407871.85"), igst_rate=Decimal("18")))

    assert p is not None
    assert p["proposed"].startswith("IGST @ 18%")
    # The pair's total, not the pair plus the stale IGST.
    assert p["amount"] == pytest.approx(407871.85)


def test_terms_are_read_off_the_bills_own_dates_not_a_column():
    """A bill has no payment-terms column; its real term is the gap between
    its dates, which is how the Bills screen computes the "Net 30 days" it
    shows. The vendor default is the agreement to compare that against."""
    from backend_ocr.app.review.adapters.bill_coding import _terms_proposal

    p = _terms_proposal(_row())
    assert p["current"] == "Net 30 days"
    assert p["proposed"] == "Net 45 days"
    assert p["days"] == 45
    # The one proposal that changes when money leaves, so it says so.
    assert "when it falls due" in p["caveat"]


def test_terms_that_already_match_the_agreement_propose_nothing():
    from backend_ocr.app.review.adapters.bill_coding import _terms_proposal

    assert _terms_proposal(_row(due_date=date(2026, 10, 22))) is None


def test_terms_need_both_an_agreement_and_two_dates():
    """Nothing is inferred from half the information."""
    from backend_ocr.app.review.adapters.bill_coding import _terms_proposal

    assert _terms_proposal(_row(payment_terms_days=None)) is None
    assert _terms_proposal(_row(due_date=None)) is None
    assert _terms_proposal(_row(invoice_date=None)) is None


def test_due_on_receipt_reads_as_words_not_net_0():
    from backend_ocr.app.review.adapters.bill_coding import _terms_proposal

    p = _terms_proposal(_row(payment_terms_days=0))
    assert p["proposed"] == "Due on receipt"


def test_accepting_terms_moves_the_due_date_off_the_bills_own_date():
    """Counted in SQL from the bill's invoice_date rather than from a date the
    request supplied — the request is not evidence of when the bill is dated."""
    out, cur = _apply(["payment_terms"])

    assert out["applied"] == ["payment_terms"]
    sql, params = next((s, p) for s, p in cur.calls if "SET due_date" in s and "fact_invoices" in s)
    assert "invoice_date + (%s || ' days')::interval" in sql
    assert params[0] == "45"
    assert "tenant_id = %s" in sql
