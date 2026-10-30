"""The buyer's own POC must never be filed as the vendor's.

A two-column insertion order states each party's name, GSTIN, PAN, point of
contact and bank details in identical shapes. Reading the wrong column files
one of our own colleagues as the vendor's contact — and the Communication
Agent would then email them as if they were the vendor. The contract prompt
now names that trap explicitly, but a prompt is a request, not a guarantee,
so the buyer's own email domains are refused in code as well. These tests
cover the code half.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.review.comm_extraction_worker import (
    _FREEMAIL_DOMAINS, _drop_pan_contradicting_gstin, _email_domain, _own_staff_domains,
)

TENANT = "10000000-0000-0000-0000-000000000001"


class FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[tuple] = []

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        return self._rows


def test_email_domain_reads_the_part_after_the_at():
    assert _email_domain("buyer.contact@buyer-co.example") == "buyer-co.example"
    assert _email_domain("  Vendor.Contact@Vendor-Co.Example ") == "vendor-co.example"
    # A plus-addressed or subdomained address still resolves to its host.
    assert _email_domain("ap+vendors@mail.buyer-co.example") == "mail.buyer-co.example"


def test_email_domain_survives_junk_without_raising():
    """The value reaching this has been through a model and a redactor."""
    assert _email_domain("") == ""
    assert _email_domain("not-an-email") == ""
    assert _email_domain(None) == ""  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_own_staff_domains_are_collected_for_this_tenant():
    conn = FakeConn([{"domain": "buyer-co.example"}, {"domain": "buyer-group.example"}])

    out = await _own_staff_domains(conn, TENANT)

    assert out == {"buyer-co.example", "buyer-group.example"}
    sql, args = conn.queries[0]
    assert "FROM users" in sql
    assert args == (TENANT,)  # never cross-tenant


@pytest.mark.asyncio
async def test_a_free_mail_domain_is_never_treated_as_ours():
    """Our staff on gmail would otherwise blacklist every vendor on gmail —
    which is a great many of them."""
    conn = FakeConn([
        {"domain": "gmail.com"}, {"domain": "buyer-co.example"}, {"domain": "outlook.com"},
    ])

    out = await _own_staff_domains(conn, TENANT)

    assert out == {"buyer-co.example"}
    assert not (out & _FREEMAIL_DOMAINS)


@pytest.mark.asyncio
async def test_a_tenant_with_no_staff_emails_blocks_nothing():
    """No signal is no signal: the guard must not start rejecting every
    contact just because the users table has no addresses in it."""
    conn = FakeConn([{"domain": None}, {"domain": ""}])

    assert await _own_staff_domains(conn, TENANT) == set()


def test_a_buyer_side_address_is_refused_and_a_vendor_side_one_kept():
    """The shape of the document this was found on, with the real identifiers
    replaced — the rule is what matters, not the customer."""
    ours = {"buyer-co.example"}
    assert _email_domain("buyer.contact@buyer-co.example") in ours       # the buyer's — refused
    assert _email_domain("vendor.contact@vendor-co.example") not in ours  # the vendor's — kept
    assert _email_domain("second.contact@vendor-co.example") not in ours


# ── A PAN that the GSTIN says cannot be this vendor's ────────────────────────
#
# An insertion order states a PAN on the purchaser's side and often leaves the
# vendor's own PAN row blank; a model filling that gap reaches across the
# table. A PAN is characters 3-12 of a GSTIN, so the GSTIN settles it.

def _claim(scope, value, field=None):
    return {"scope": scope, "field": field or scope.split("_")[-1],
            "new_value": value, "confidence": 0.9, "evidence_quote": "q",
            "status": "PENDING"}


def test_a_pan_from_the_other_party_is_dropped():
    claims = [
        _claim("vendor_gstin", "06AAACV2222D1ZY"),   # the vendor's
        _claim("vendor_pan", "AAACB1111C"),          # the middle of the BUYER's GSTIN
    ]

    kept, dropped = _drop_pan_contradicting_gstin(claims)

    assert dropped == "AAACB1111C"
    assert [c["scope"] for c in kept] == ["vendor_gstin"]


def test_a_pan_that_agrees_with_the_gstin_is_kept():
    claims = [
        _claim("vendor_gstin", "06AAACV2222D1ZY"),
        _claim("vendor_pan", "AAACV2222D"),  # exactly chars 3-12
    ]

    kept, dropped = _drop_pan_contradicting_gstin(claims)

    assert dropped is None
    assert len(kept) == 2


def test_case_and_spacing_do_not_make_a_matching_pan_look_wrong():
    claims = [
        _claim("vendor_gstin", " 06aaacv2222d1zy "),
        _claim("vendor_pan", " aaacv2222d "),
    ]

    _, dropped = _drop_pan_contradicting_gstin(claims)

    assert dropped is None


def test_with_no_gstin_there_is_nothing_to_check_against():
    """A contract stating only a PAN is the one case the claim survives on its
    own — the two-party prompt rule is the guard there."""
    claims = [_claim("vendor_pan", "AAACB1111C")]

    kept, dropped = _drop_pan_contradicting_gstin(claims)

    assert dropped is None
    assert len(kept) == 1


def test_a_malformed_gstin_is_not_used_to_judge_a_pan():
    """Chars 3-12 of a truncated GSTIN is not a PAN, and would drop a good
    claim on the strength of a bad one."""
    claims = [_claim("vendor_gstin", "06AAACV"), _claim("vendor_pan", "AAACB1111C")]

    kept, dropped = _drop_pan_contradicting_gstin(claims)

    assert dropped is None
    assert len(kept) == 2


def test_other_claims_are_untouched():
    claims = [
        _claim("vendor_gstin", "06AAACV2222D1ZY"),
        _claim("vendor_pan", "AAACB1111C"),
        _claim("payment_term", "45", field="term"),
        _claim("vendor_contact", "vendor@vendor-co.example", field="email"),
    ]

    kept, _ = _drop_pan_contradicting_gstin(claims)

    assert [c["scope"] for c in kept] == ["vendor_gstin", "payment_term", "vendor_contact"]
