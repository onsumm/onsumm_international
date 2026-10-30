"""Add a document: deciding what a file is from what it says.

The model's answer is never trusted as-is: a type it names that doesn't exist
becomes a new type, a new name that matches an existing type files there, and
an unknown kind falls back to the keyword guess.
"""
from __future__ import annotations

import io

import pytest

from backend_ocr.app.routers import vendor_doc_classify as dc

TYPES = ["Rate cards", "MSME certificates"]


class TestGuess:
    def test_contract_words(self):
        assert dc.guess("IO_Signzy_Oct.pdf", "This Insertion Order is entered into…", TYPES)["kind"] == "contract"

    def test_invoice_is_a_bill(self):
        assert dc.guess("doc.pdf", "TAX INVOICE  Invoice No: SGL/26-27/0445", TYPES)["kind"] == "bill"

    def test_existing_type_by_name(self):
        g = dc.guess("Signzy rate card 2026.pdf", "", TYPES)
        assert g == {"kind": "existing", "label": "Rate cards", "reason": "Mentions “Rate cards”."}

    def test_otherwise_new_named_from_the_file(self):
        g = dc.guess("cancelled_cheque-2026.jpg", "", TYPES)
        assert g["kind"] == "new" and g["suggested_name"] == "Cancelled cheque"


class TestSettle:
    FB = {"kind": "new", "suggested_name": "Misc", "reason": ""}

    def test_existing_must_be_a_real_type(self):
        out = dc.settle({"kind": "existing", "label": "rate cards"}, TYPES, self.FB)
        assert out["kind"] == "existing" and out["label"] == "Rate cards"
        out = dc.settle({"kind": "existing", "label": "Brochures"}, TYPES, self.FB)
        assert out["kind"] == "new" and out["suggested_name"] == "Brochures"

    def test_new_name_matching_a_type_files_there(self):
        out = dc.settle({"kind": "new", "suggested_name": "MSME Certificates"}, TYPES, self.FB)
        assert out["kind"] == "existing" and out["label"] == "MSME certificates"

    def test_nonsense_falls_back(self):
        out = dc.settle({"kind": "poem"}, TYPES, {"kind": "contract", "reason": "Reads like a contract or IO."})
        assert out["kind"] == "contract" and out["source"] == "guess"

    def test_no_answer_falls_back(self):
        out = dc.settle(None, TYPES, self.FB)
        assert out["kind"] == "new" and out["suggested_name"] == "Misc"

    def test_bill_passes_through(self):
        assert dc.settle({"kind": "bill", "reason": "Tax invoice dated 3 Oct."}, TYPES, self.FB)["kind"] == "bill"


class TestExtract:
    def test_pdf_text(self):
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "MASTER SERVICE AGREEMENT between Acme and Signzy " * 3)
        text, image = dc.extract("msa.pdf", doc.tobytes())
        assert "MASTER SERVICE AGREEMENT" in text and image is None

    def test_scanned_pdf_gives_an_image(self):
        import fitz
        doc = fitz.open()
        doc.new_page()
        text, image = dc.extract("scan.pdf", doc.tobytes())
        assert text.strip() == "" and image and image[:4] == b"\x89PNG"

    def test_spreadsheet_cells(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Ad unit", "Rate (INR)"])
        wb.active.append(["Banner 300x250", 120])
        buf = io.BytesIO()
        wb.save(buf)
        text, _ = dc.extract("rates.xlsx", buf.getvalue())
        assert "Banner 300x250 | 120" in text

    def test_unreadable_is_empty_not_an_error(self):
        assert dc.extract("broken.pdf", b"%PDF-garbage") == ("", None)


class TestJev:
    def _body(self, kind, conf=0.9, name="Cancelled cheques"):
        return {"answers": {"kind": {"type": "choice", "choice": kind, "confidence": conf},
                            "name": {"type": "choice", "choice": name, "confidence": 0.8}}}

    def test_every_option_is_explained(self):
        q = dc.jev_questions(TYPES, {"Rate cards": ["signzy_rates.pdf"]})
        kinds = q["kind"]["criteria"]
        assert {"Contract or IO", "Bill or invoice", "Something else", "type: Rate cards", "type: MSME certificates"} <= set(kinds)
        for opt in ("Contract or IO", "Bill or invoice"):
            assert kinds[opt]["what"] and kinds[opt]["not_for"] and kinds[opt]["examples"]
        # The team's own types carry real file names filed under them.
        assert kinds["type: Rate cards"]["examples"] == ["previously filed here: signzy_rates.pdf"]
        assert "focus" in q["kind"]["instructions"]
        assert all(isinstance(v, str) and v for v in q["name"]["criteria"].values())

    @pytest.mark.parametrize("pick,expect", [
        ("Contract or IO", {"kind": "contract"}),
        ("Bill or invoice", {"kind": "bill"}),
        ("type: Rate cards", {"kind": "existing", "label": "Rate cards"}),
        ("Something else", {"kind": "new", "suggested_name": "Cancelled cheques"}),
    ])
    def test_reads_the_answer(self, pick, expect):
        out = dc.read_jev(self._body(pick))
        assert {k: out[k] for k in expect} == expect and out["confidence"] == 0.9

    def test_other_documents_leaves_the_name_to_the_user(self):
        assert dc.read_jev(self._body("Something else", name="Other documents"))["suggested_name"] == ""

    def test_unsure_is_flagged(self):
        out = dc.settle(dc.read_jev(self._body("Contract or IO", conf=0.3)), TYPES, {"kind": "new", "suggested_name": "x"})
        assert out["kind"] == "contract" and out["sure"] is False
        out = dc.settle(dc.read_jev(self._body("Contract or IO", conf=0.8)), TYPES, {"kind": "new", "suggested_name": "x"})
        assert out["sure"] is True

    def test_no_text_no_call(self, monkeypatch):
        monkeypatch.setattr(dc, "JEV_API_KEY", "k")
        assert dc.ask_jev("V", "scan.pdf", "  ", TYPES, {}) is None

    def test_sends_the_documented_request(self, monkeypatch):
        sent = {}

        class R:
            def raise_for_status(self): pass
            def json(self): return {"answers": {"kind": {"choice": "Contract or IO", "confidence": 1.0}}}

        def post(url, **kw):
            sent.update(url=url, **kw)
            return R()
        monkeypatch.setattr(dc, "JEV_API_KEY", "secret")
        monkeypatch.setattr(dc.httpx, "post", post)
        assert dc.ask_jev("Signzy", "io.pdf", "INSERTION ORDER", TYPES, {})["kind"] == "contract"
        assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
        assert sent["headers"] == {"Authorization": "Bearer secret"}
        assert sent["json"]["model"] == "jev-latest"
        assert sent["json"]["state"] == {"vendor": "Signzy", "file_name": "io.pdf", "text": "INSERTION ORDER"}
