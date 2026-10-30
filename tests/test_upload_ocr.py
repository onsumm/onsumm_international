"""Unit tests for pipelines/upload_ocr.py helpers — no DB required."""
from __future__ import annotations

from datetime import date

import pytest


class TestGstnClean:
    def test_valid_gstin_uppercase(self):
        from pipelines.upload_ocr import _gstn_clean
        assert _gstn_clean("27AAACR5055K1Z5") == "27AAACR5055K1Z5"

    def test_lowercase_normalised(self):
        from pipelines.upload_ocr import _gstn_clean
        result = _gstn_clean("27aaacr5055k1z5")
        assert result == "27AAACR5055K1Z5"

    def test_too_short_returns_none(self):
        from pipelines.upload_ocr import _gstn_clean
        assert _gstn_clean("SHORT") is None

    def test_none_input_returns_none(self):
        from pipelines.upload_ocr import _gstn_clean
        assert _gstn_clean(None) is None

    def test_whitespace_stripped(self):
        from pipelines.upload_ocr import _gstn_clean
        result = _gstn_clean("  27AAACR5055K1Z5  ")
        assert result == "27AAACR5055K1Z5"


class TestParseOcrDate:
    def test_iso_format(self):
        from pipelines.upload_ocr import _parse_ocr_date
        assert _parse_ocr_date("2025-06-15") == date(2025, 6, 15)

    def test_dd_mm_yyyy(self):
        from pipelines.upload_ocr import _parse_ocr_date
        assert _parse_ocr_date("15/06/2025") == date(2025, 6, 15)

    def test_dd_mon_yyyy(self):
        from pipelines.upload_ocr import _parse_ocr_date
        assert _parse_ocr_date("15 Jun 2025") == date(2025, 6, 15)

    def test_invalid_returns_none(self):
        from pipelines.upload_ocr import _parse_ocr_date
        assert _parse_ocr_date("not-a-date") is None

    def test_none_returns_none(self):
        from pipelines.upload_ocr import _parse_ocr_date
        assert _parse_ocr_date(None) is None


class TestIsBill:
    def test_bill_document_type(self):
        from pipelines.upload_ocr import _is_bill
        assert _is_bill({"document_type": "bill"}) is True

    def test_purchase_order_document_type(self):
        from pipelines.upload_ocr import _is_bill
        assert _is_bill({"document_type": "purchase_order"}) is True

    def test_invoice_is_not_bill(self):
        from pipelines.upload_ocr import _is_bill
        assert _is_bill({"document_type": "invoice"}) is False

    def test_missing_type_is_not_bill(self):
        from pipelines.upload_ocr import _is_bill
        assert _is_bill({}) is False


class TestEstimateConfidence:
    def test_full_fields_high_confidence(self):
        from pipelines.upload_ocr import _estimate_confidence
        ocr = {
            "invoice_number": "INV-001",
            "invoice_date": "2025-06-01",
            "total_amount": 11800,
            "gstin_seller": "27AAACR5055K1Z5",
            "vendor_name": "Acme Corp",
            "line_items": [{"desc": "item"}],
        }
        conf = _estimate_confidence(ocr)
        assert conf >= 0.7

    def test_empty_dict_low_confidence(self):
        from pipelines.upload_ocr import _estimate_confidence
        assert _estimate_confidence({}) < 0.5
