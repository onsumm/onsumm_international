"""The payee account shown on a bill's approval screen.

GET /api/bills/{id} carries the account this bill would actually be paid into,
because an approver deciding on ₹30,090 should be able to see where the money
goes without leaving the screen.

Unlike invoices.py::_lookup_vendor_master — which serves a verified account or
nothing, because what it returns gets written onto a bill — this one displays
and badges. An unverified account is still returned, carrying verified=false:
hiding it would tell the approver "no account on file" when one exists, which
is a worse answer than showing it with its state attached.
"""
from __future__ import annotations

from backend_ocr.app.routers.bills import _bill_payee_bank


def _row(**over):
    base = {
        "bank_account_number": None, "bank_ifsc": None, "bank_name": None,
        "bank_name_at_bank": None, "bank_verified": None,
        "legacy_bank_account": None, "legacy_bank_ifsc": None,
        "legacy_bank_name": None, "legacy_bank_verified": False,
    }
    base.update(over)
    return base


def test_a_verified_supplier_account_is_shown_as_verified():
    out = _bill_payee_bank(_row(
        bank_account_number="5612067465", bank_ifsc="KKBK0004257",
        bank_name="Kotak Mahindra Bank", bank_name_at_bank="Accelerate Media Pvt Ltd",
        bank_verified=True,
    ))

    assert out == {
        "account_number": "5612067465", "ifsc": "KKBK0004257",
        "bank_name": "Kotak Mahindra Bank", "name_at_bank": "Accelerate Media Pvt Ltd",
        "verified": True,
    }


def test_an_unverified_account_is_shown_and_flagged_rather_than_hidden():
    """The badge carries the warning. Withholding the account instead would
    read as "no account on file", which is a different and wronger claim."""
    out = _bill_payee_bank(_row(
        bank_account_number="5612067465", bank_ifsc="KKBK0004257",
        bank_name="Kotak Mahindra Bank", bank_verified=False,
    ))

    assert out["account_number"] == "5612067465"
    assert out["verified"] is False


def test_no_account_at_all_is_none():
    assert _bill_payee_bank(_row()) is None


def test_the_legacy_columns_carry_their_own_verified_state():
    unverified = _bill_payee_bank(_row(
        legacy_bank_account="999888777", legacy_bank_ifsc="HDFC0001234",
        legacy_bank_name="HDFC", legacy_bank_verified=False,
    ))
    assert unverified["account_number"] == "999888777"
    assert unverified["verified"] is False

    verified = _bill_payee_bank(_row(
        legacy_bank_account="999888777", legacy_bank_ifsc="HDFC0001234",
        legacy_bank_name="HDFC", legacy_bank_verified=True,
    ))
    assert verified["verified"] is True


def test_a_supplier_account_wins_over_the_legacy_columns():
    """supplier_bank_accounts is the current home; the dim_suppliers columns are
    the pre-multi-account fallback and must not shadow it."""
    out = _bill_payee_bank(_row(
        bank_account_number="5612067465", bank_ifsc="KKBK0004257", bank_name="Kotak",
        bank_verified=False,
        legacy_bank_account="999888777", legacy_bank_ifsc="HDFC0001234",
        legacy_bank_name="HDFC", legacy_bank_verified=True,
    ))

    assert out["account_number"] == "5612067465"


def test_verified_is_always_a_bool_so_the_badge_never_renders_on_null():
    """`verified` is nullable in the row; the badge branches on it directly."""
    out = _bill_payee_bank(_row(bank_account_number="5612067465", bank_verified=None))

    assert out["verified"] is False


def test_missing_sibling_fields_never_render_as_the_string_none():
    out = _bill_payee_bank(_row(bank_account_number="5612067465", bank_verified=True))

    assert out == {
        "account_number": "5612067465", "ifsc": "", "bank_name": "",
        "name_at_bank": "", "verified": True,
    }
