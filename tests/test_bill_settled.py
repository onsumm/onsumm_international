"""A bill marked paid reports what was paid, never "₹0 paid, ₹0 outstanding"."""
from backend_ocr.app.routers.bills import _settled


def test_paid_without_an_amount_reads_as_the_total_paid():
    assert _settled("paid", 0, 0, 30208) == (30208.0, 0.0)
    assert _settled("paid", None, 0, 30208) == (30208.0, 0.0)


def test_a_recorded_amount_is_kept():
    assert _settled("paid", 30208, 0, 30208) == (30208.0, 0.0)
    assert _settled("partial", 10000, 20208, 30208) == (10000.0, 20208.0)
    assert _settled("unpaid", 0, 30208, 30208) == (0.0, 30208.0)
