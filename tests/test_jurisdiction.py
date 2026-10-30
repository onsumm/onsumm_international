"""Jurisdiction profile (backend_ocr/app/core/jurisdiction.py) and the rules
that depend on it: an Indian tenant must behave exactly as before, a tenant
outside India must not be held to Indian rules."""
from __future__ import annotations

from datetime import date

import pytest

from backend_ocr.app.core.jurisdiction import INDIA, PACKS, build, pack_enabled_sql
from backend_ocr.app.core.money import format_money
from backend_ocr.app.domain import ingest
from backend_ocr.app.domain.vendor_verification import derive_verification

LUMEN = {
    "jurisdiction_profile": "international", "country_code": "SG", "base_currency": "USD",
    "is_resident_india": False, "timezone": "Asia/Singapore", "settings_jsonb": {},
}
INDIAN = {
    "jurisdiction_profile": "india", "country_code": "IN", "base_currency": "INR",
    "is_resident_india": True, "timezone": "Asia/Kolkata", "settings_jsonb": {},
}


# ── build() ──────────────────────────────────────────────────────────────────

def test_unknown_tenant_is_india():
    assert build(None) == INDIA
    assert all(INDIA.has(p) for p in PACKS)
    assert INDIA.rails == ("IMPS",)


def test_indian_tenant_has_every_india_pack():
    j = build(INDIAN)
    assert j.profile == "india" and j.base_currency == "INR" and j.locale == "en-IN"
    assert j.fiscal_year_start_month == 4 and j.tax_id_label == "GSTIN"
    assert all(j.has(p) for p in PACKS)
    assert j.rails == ("IMPS",)


def test_international_tenant_has_no_india_pack():
    j = build(LUMEN)
    assert j.profile == "international" and j.country_code == "SG" and j.base_currency == "USD"
    assert not any(j.has(p) for p in PACKS)
    assert j.rails == ("USDC",)
    assert j.timezone == "Asia/Singapore" and j.fiscal_year_start_month == 1
    assert j.is_resident_india is False


def test_column_default_timezone_is_ignored_abroad():
    j = build({**LUMEN, "timezone": "Asia/Kolkata"})  # dim_tenants' column default
    assert j.timezone == "Asia/Singapore"


def test_per_tenant_pack_override_turns_one_rule_back_on():
    j = build({**LUMEN, "settings_jsonb": {"jurisdiction": {"packs": {"gst": True}}}})
    assert j.has("gst") and not j.has("tds_india")


def test_override_accepts_json_text_settings():
    j = build({**LUMEN, "settings_jsonb": '{"jurisdiction": {"packs": {"imps": true}}}'})
    assert j.has("imps") and "IMPS" in j.rails


def test_override_can_switch_a_pack_off_for_india():
    j = build({**INDIAN, "settings_jsonb": {"jurisdiction": {"packs": {"imps": False}}}})
    assert not j.has("imps") and "IMPS" not in j.rails and j.has("gst")


# ── pack_enabled_sql() ───────────────────────────────────────────────────────

def test_pack_sql_reads_override_then_profile_default():
    sql = pack_enabled_sql("gst", "s.tenant_id")
    assert "'jurisdiction'->'packs'->>'gst'" in sql
    assert "COALESCE(_jur.jurisdiction_profile, 'india') = 'india'" in sql
    assert "_jur.tenant_id = s.tenant_id" in sql
    assert sql.endswith(", true)")  # a tenant with no dim_tenants row counts as India


def test_pack_sql_requires_a_qualified_tenant_column():
    # Unqualified, `tenant_id` would bind to dim_tenants inside the subquery and
    # the predicate would always be true.
    with pytest.raises(ValueError):
        pack_enabled_sql("gst", "tenant_id")


def test_pack_sql_rejects_unknown_pack():
    with pytest.raises(ValueError):
        pack_enabled_sql("vat", "s.tenant_id")


# ── vendor verification badge ────────────────────────────────────────────────

@pytest.mark.parametrize("gst,bank,status", [
    ("VERIFIED", True, "VERIFIED"),
    ("VERIFIED", False, "PENDING"),
    ("UNVERIFIED", True, "PENDING"),
    ("UNVERIFIED", False, "NOT_STARTED"),
])
def test_india_verification_is_gstin_plus_bank(gst, bank, status):
    v = derive_verification(build(INDIAN), gst_status=gst, bank_verified=bank)
    assert v["status"] == status


def test_india_ignores_wallet_and_tax_docs():
    v = derive_verification(build(INDIAN), gst_status="UNVERIFIED", bank_verified=False,
                            has_active_wallet=True, tax_docs={"W-8BEN-E": "on file"})
    assert v["status"] == "NOT_STARTED"


def test_international_verified_by_wallet_and_tax_docs():
    v = derive_verification(build(LUMEN), gst_status="UNVERIFIED", bank_verified=False,
                            has_active_wallet=True, tax_docs={"W-8BEN-E": "on file"})
    assert v == {"status": "VERIFIED", "basis": ["wallet", "tax_docs"], "label": "Verified"}


@pytest.mark.parametrize("wallet,bank,docs,status", [
    (False, True, {"W-8BEN-E": "x"}, "VERIFIED"),
    (True, False, {}, "PENDING"),
    (False, False, {"W-8BEN-E": "x"}, "PENDING"),
    (False, False, {"W-8BEN-E": ""}, "NOT_STARTED"),
    (False, False, None, "NOT_STARTED"),
])
def test_international_verification_states(wallet, bank, docs, status):
    v = derive_verification(build(LUMEN), gst_status="UNVERIFIED", bank_verified=bank,
                            has_active_wallet=wallet, tax_docs=docs)
    assert v["status"] == status


def test_international_never_needs_a_gstin():
    v = derive_verification(build(LUMEN), gst_status=None, bank_verified=False,
                            has_active_wallet=True, tax_docs={"tax_id": "EIN 88-1234567"})
    assert v["status"] == "VERIFIED" and "gstin" not in v["basis"]


# ── fiscal year, bill currency, OCR prompt, money ────────────────────────────

def test_india_fiscal_year_unchanged():
    old = {4: 1, 5: 1, 6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3, 1: 4, 2: 4, 3: 4}
    for m in range(1, 13):
        d = date(2026, m, 15)
        assert ingest._indian_fiscal(d) == (d.year if m >= 4 else d.year - 1, old[m], m)


def test_calendar_fiscal_year_for_january_start():
    assert ingest._fiscal(date(2026, 3, 1), 1) == (2026, 1, 3)
    assert ingest._fiscal(date(2026, 12, 31), 1) == (2026, 4, 12)


def test_bill_currency_india_rule_unchanged(monkeypatch):
    monkeypatch.setattr(ingest, "_tenant_base_currency", lambda cur, tid: "INR")
    got = [ingest._bill_currency(None, {"currency_code": c}, "t") for c in (None, "usd", "SGD", "INR")]
    assert got == ["INR", "USD", "INR", "INR"]


def test_bill_currency_defaults_to_books_currency_abroad(monkeypatch):
    monkeypatch.setattr(ingest, "_tenant_base_currency", lambda cur, tid: "USD")
    got = [ingest._bill_currency(None, {"currency_code": c}, "t") for c in (None, "SGD", "XYZ")]
    assert got == ["USD", "SGD", "USD"]


def test_no_conversion_when_bill_is_in_books_currency():
    assert ingest._fx_convert(None, "USD", 4200, 4200, 0, 4200, base_currency="USD") == (4200, 4200, 0, 4200, None)


def test_ocr_prompt_unchanged_for_india():
    import utils.ocr_engine as ocr
    ocr.set_ocr_jurisdiction("india", "IN")
    assert ocr._with_jurisdiction(ocr.SYSTEM_PROMPT) == ocr.SYSTEM_PROMPT
    ocr.set_ocr_jurisdiction("international", "SG")
    try:
        prompt = ocr._with_jurisdiction(ocr.SYSTEM_PROMPT)
        assert prompt.startswith(ocr.SYSTEM_PROMPT) and "outside India (SG)" in prompt
    finally:
        ocr.set_ocr_jurisdiction("india", "IN")


def test_format_money_keeps_rupee_text():
    assert format_money(1234.5) == "₹1,234.50"
    assert format_money(4200, "USD") == "$4,200.00"
    assert format_money(1000, "AED") == "AED 1,000.00"
    assert format_money(None) == "—"
