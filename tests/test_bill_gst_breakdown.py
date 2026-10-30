"""The CGST/SGST/IGST split on the bill detail.

Extraction reads every component and its rate; ingest used to add them up and
keep only the sum, so from the Bills screen onward the split existed nowhere a
person could see it. These columns and this serialiser are where it lands.

`reconciles` is the part worth pinning. The split is written once, at ingest,
from what the document said. PUT /api/bills can change tax_amount afterwards
and has no way to know how the new figure divides — so the components stay
true of the document and stop adding up to the bill. Saying so beats letting
someone add three numbers and wonder which is wrong.
"""
from __future__ import annotations

from decimal import Decimal

from backend_ocr.app.routers.bills import _tax_breakdown


def _row(**over):
    base = {
        "cgst_amount": Decimal("432.11"), "sgst_amount": Decimal("432.11"),
        "igst_amount": None, "cess_amount": None,
        "cgst_rate": Decimal("9"), "sgst_rate": Decimal("9"), "igst_rate": None,
        "taxable_amount": Decimal("4801.20"), "tax_amount": Decimal("864.22"),
        "place_of_supply": "Haryana (06)",
    }
    base.update(over)
    return base


def test_the_components_and_their_rates_both_survive():
    out = _tax_breakdown(_row())
    # The state whose GST applies, as the invoice printed it. It is what says
    # whether CGST+SGST or IGST is the correct split, so the split is only
    # checkable with it.
    assert out["place_of_supply"] == "Haryana (06)"
    assert out["cgst"] == 432.11
    assert out["sgst"] == 432.11
    assert out["cgst_rate"] == 9
    assert out["taxable_amount"] == 4801.20
    assert out["components_total"] == 864.22


def test_a_bill_from_before_the_split_was_kept_returns_nothing():
    """Not zeroes. Every bill ingested before this existed has NULL components,
    and rendering those as ₹0 would assert a split nobody recorded."""
    assert _tax_breakdown(_row(
        cgst_amount=None, sgst_amount=None, igst_amount=None, cess_amount=None,
    )) is None


def test_both_halves_present_means_one_state():
    assert _tax_breakdown(_row())["is_interstate"] is False


def test_igst_alone_means_two_states():
    out = _tax_breakdown(_row(
        cgst_amount=None, sgst_amount=None,
        igst_amount=Decimal("864.22"), igst_rate=Decimal("18"),
    ))
    assert out["is_interstate"] is True


def test_a_few_paise_of_rounding_still_reconciles():
    """Components are rounded to paise on the invoice; their sum legitimately
    drifts from the stated total. A rupee of slack, not an exact match."""
    out = _tax_breakdown(_row(tax_amount=Decimal("864.61")))
    assert out["reconciles"] is True


def test_an_edited_tax_total_stops_reconciling_and_says_so():
    out = _tax_breakdown(_row(tax_amount=Decimal("1000.00")))
    assert out["reconciles"] is False
    # The components are unchanged — they are still what the document said.
    assert out["components_total"] == 864.22


def test_a_bill_with_no_tax_total_is_not_called_a_mismatch():
    """A null total is missing information, not a contradiction."""
    assert _tax_breakdown(_row(tax_amount=None))["reconciles"] is True


def test_cess_counts_towards_the_total():
    out = _tax_breakdown(_row(cess_amount=Decimal("50.00"), tax_amount=Decimal("914.22")))
    assert out["cess"] == 50.0
    assert out["components_total"] == 914.22
    assert out["reconciles"] is True


def test_ingest_writes_every_component_it_reads():
    """The parts were being computed and then discarded — this pins that the
    INSERT actually carries them."""
    from pathlib import Path

    src = Path("backend_ocr/app/domain/ingest.py").read_text()
    insert = src[src.index("INSERT INTO stg_bills"):src.index("def _upsert_fact_invoice")]
    for col in ("taxable_amount", "cgst_amount", "sgst_amount", "igst_amount",
                "cess_amount", "cgst_rate", "sgst_rate", "igst_rate"):
        assert col in insert, f"{col} is read from the document and then dropped"


# ── What extraction promises, and what ingest does with it ────────────────────

def test_the_extraction_schema_asks_for_every_component_and_its_rate():
    from utils.ocr_engine import _SCHEMA_AND_RULES

    for key in ("taxable_amount", "cgst_rate", "cgst_amount", "sgst_rate",
                "sgst_amount", "igst_rate", "igst_amount", "cess_amount",
                "total_tax_amount"):
        assert f'"{key}"' in _SCHEMA_AND_RULES


def test_the_model_is_told_not_to_invent_a_split():
    """An 18% GST line is CGST 9 + SGST 9 only when the document says so.
    Splitting it ourselves would put a number on a GST return that nobody
    printed."""
    from utils.ocr_engine import _SCHEMA_AND_RULES

    # Whitespace-normalised: the prompt is wrapped prose and gets rewrapped.
    rules = " ".join(_SCHEMA_AND_RULES.lower().split())
    assert "do not invent a split" in rules
    assert "never return all three" in rules
    # Rates are the printed number, not a fraction — 9, never 0.09.
    assert "never 0.09" in rules


def test_a_stated_tax_total_is_kept_rather_than_recomputed():
    from utils.ocr_engine import gst_amount

    assert gst_amount({"total_tax_amount": 864.22}) == 864.22
    # Both present and in agreement.
    assert gst_amount({"total_tax_amount": 864.22,
                       "cgst_amount": 432.11, "sgst_amount": 432.11}) == 864.22
    # Both present and disagreeing: the printed total wins. It is one number a
    # human typeset, against three the extractor had to find separately — and
    # the bill detail flags the disagreement rather than hiding it here.
    assert gst_amount({"total_tax_amount": 900, "cgst_amount": 432.11}) == 900


def test_a_combined_gst_line_no_longer_posts_as_untaxed():
    """The bug: summing the components was the only rule, so an invoice
    printing "GST @ 18% ... 864.22" and no split posted with zero tax."""
    from utils.ocr_engine import gst_amount

    assert gst_amount({"total_tax_amount": 864.22, "cgst_amount": None,
                       "sgst_amount": None, "igst_amount": None}) == 864.22


def test_components_still_carry_a_bill_with_no_printed_total():
    from utils.ocr_engine import gst_amount

    assert gst_amount({"cgst_amount": 432.11, "sgst_amount": 432.11}) == 864.22
    assert gst_amount({"igst_amount": "864.22"}) == 864.22      # model output is loose
    assert gst_amount({}) == 0
    assert gst_amount(None) == 0


def test_both_ingest_paths_go_through_the_one_helper():
    """Two modules compute this — backend_ocr on upload review, pipelines on
    the worker path — and they must not drift."""
    from pathlib import Path

    for path in ("backend_ocr/app/domain/ingest.py", "pipelines/upload_ocr.py"):
        src = Path(path).read_text()
        assert "gst_amount(taxes)" in src, path
        assert 'for k in ("cgst_amount", "sgst_amount", "igst_amount", "cess_amount")' not in src, (
            f"{path} still adds the components up by hand"
        )


def _reingest(payload_taxes):
    """Drive _upsert_bill down its UPDATE branch and return the SQL it built."""
    from backend_ocr.app.domain import ingest

    calls: list[tuple] = []

    class Cur:
        def execute(self, sql, params=None):
            calls.append((sql, params))

        def fetchone(self):
            # The UPDATE matched, so _upsert_bill returns without inserting.
            return ("stg-1", None, None, 0, 0, 0, 0, "sup-1", "up-1")

    payload = {
        "invoice_number": "SIGNZY2627/02991",
        "total_amount": 2673826.56,
        "taxes": payload_taxes,
        "vendor": {"gstin": "27AAWCS3552Q1ZA"},
    }
    ingest._upsert_bill(Cur(), payload, "sup-1", "up-1", "x.pdf", None, "ten-1")
    return next((sql, p) for sql, p in calls if sql.strip().startswith("UPDATE stg_bills"))


DOC_SPLIT = {
    "taxable_amount": 2265954.71,
    "cgst_amount": 203935.93, "cgst_rate": 9,
    "sgst_amount": 203935.92, "sgst_rate": 9,
}


def test_re_uploading_a_document_restores_the_split_it_prints():
    """The bug, exactly: accepting "this should be IGST" clears cgst/sgst and
    sets igst. Re-uploading the same invoice then filled the two NULLs from the
    document and left the corrected IGST in place — so the bill carried CGST
    and SGST and IGST, and its GST total was double the truth.

    Re-reading a document is a statement about what the document says, so its
    tax lines replace what is stored rather than merging with it.
    """
    sql, params = _reingest(DOC_SPLIT)

    # Set outright, not COALESCEd — that is what clears the stale IGST.
    assert "cgst_amount = %s" in sql
    assert "igst_amount = %s" in sql
    assert "COALESCE(cgst_amount" not in sql
    # The document prints no IGST, so IGST goes back to NULL.
    assert None in params


def test_a_payload_with_no_split_leaves_the_stored_one_alone():
    """A re-ingest carrying less information is not a document saying the tax
    is gone."""
    sql, _params = _reingest({"taxable_amount": 2265954.71})

    assert "cgst_amount" not in sql
    assert "igst_amount" not in sql
    # Everything else still fills blanks as before.
    assert "taxable_amount = COALESCE(%s, taxable_amount)" in sql


def test_a_re_upload_never_rewrites_the_amounts_a_human_may_have_fixed():
    """The reason this branch leaves the totals alone at all. Only the split is
    authoritative from the document."""
    sql, _params = _reingest(DOC_SPLIT)
    # The SET clause only — RETURNING names these columns, and reading them
    # back is not writing them.
    sets = sql[sql.index("SET"):sql.index("WHERE")]

    assert "tax_amount" not in sets
    assert "total_amount" not in sets
    assert "subtotal" not in sets
