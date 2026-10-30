"""Withholding: pack selection, rates, rounding to 6-decimal USDC units."""
from __future__ import annotations

from decimal import Decimal

from backend_ocr.app.domain.crypto.fx import FxSnapshot
from backend_ocr.app.domain.crypto.withholding import WithholdingEngine, WithholdingInput


def snap(bill_currency="USD", usd_bill="1", token_usd="1") -> FxSnapshot:
    return FxSnapshot(id="s", token="USDC", token_usd=Decimal(token_usd), usd_functional=Decimal(1),
                      usd_inr=Decimal("84.10"), bill_currency=bill_currency, usd_bill=Decimal(usd_bill), source="fixed")


def inp(**kw) -> WithholdingInput:
    base = dict(rail="USDC", tenant_settings={}, tenant_is_resident_india=False, supplier_category=None,
                supplier_pan=None, supplier_tax_docs={}, supplier_is_resident_india=False,
                bill_amount=Decimal("3600.00"), bill_currency="USD")
    base.update(kw)
    return WithholdingInput(**base)


E = WithholdingEngine()


def test_sg_demo_default_is_zero_and_needs_signoff():
    s = snap()
    gross = s.bill_to_units(Decimal("3600"))
    r = E.compute(inp(), s, gross)
    assert (r.rule_pack, r.rate, r.amount_units, r.net_units) == ("SG-DEMO", Decimal(0), 0, 3_600_000_000)
    assert r.requires_signoff and r.rule_code == "SG-DEMO-DEFAULT"


def test_configured_rule_by_category_and_rounding_down():
    settings = {"crypto_withholding": {"pack": "SG-DEMO", "rules": {
        "design": {"code": "SG-DESIGN", "rate": "0.1", "requires_signoff": False}}}}
    s = snap("SGD", usd_bill="1.3")
    amount = Decimal("1000.00")
    gross = s.bill_to_units(amount)            # 1000 / 1.3 = 769.230769...
    assert gross == 769_230_769
    r = E.compute(inp(tenant_settings=settings, supplier_category="design", bill_amount=amount, bill_currency="SGD"),
                  s, gross)
    assert r.rule_code == "SG-DESIGN" and not r.requires_signoff
    assert r.tax_amount == Decimal("100.00")
    assert r.amount_units == 76_923_076       # 100 / 1.3 = 76.9230769 -> rounded down
    assert r.net_units == gross - r.amount_units


def test_in195_default_and_treaty_for_indian_payer():
    s = snap()
    gross = s.bill_to_units(Decimal("3600"))
    r = E.compute(inp(tenant_is_resident_india=True, rail="IMPS"), s, gross)
    assert (r.rule_pack, r.rule_code, r.rate) == ("IN-195", "IN-195-DEFAULT", Decimal("0.20"))
    assert r.tax_amount == Decimal("720.00") and r.amount_units == 720_000_000 and r.requires_signoff
    docs = {"TRC": {"reference": "x"}, "FORM_10F": {"reference": "y"}, "treaty_rate": "0.10"}
    r = E.compute(inp(tenant_is_resident_india=True, supplier_tax_docs=docs), s, gross)
    assert r.rule_code == "IN-195-TREATY" and r.rate == Decimal("0.10") and r.amount_units == 360_000_000


def test_in_services_uses_bill_tds_for_resident_vendor():
    r = E.compute(inp(tenant_is_resident_india=True, supplier_is_resident_india=True, bill_currency="INR",
                      bill_amount=Decimal("100000"), bill_tds_rate=Decimal("10"), bill_tds_section="194J"), None, 0)
    assert r.rule_pack == "IN-SERVICES" and r.rule_code == "IN-SERVICES-194J"
    assert r.rate == Decimal("0.10") and r.tax_amount == Decimal("10000.00") and r.amount_units == 0


def test_in_vda_thresholds_and_no_pan():
    base = dict(tenant_is_resident_india=True, is_vda_purchase=True, bill_currency="INR")
    r = E.compute(inp(**base, bill_amount=Decimal("9000"), supplier_pan="ABCDE1234F"), None, 0)
    assert r.rule_code == "IN-VDA-BELOW-THRESHOLD" and r.tax_amount == 0
    r = E.compute(inp(**base, bill_amount=Decimal("40000"), vda_payer_specified_person=True,
                      supplier_pan="ABCDE1234F"), None, 0)
    assert r.rule_code == "IN-VDA-BELOW-THRESHOLD"
    r = E.compute(inp(**base, bill_amount=Decimal("20000"), supplier_pan="ABCDE1234F"), None, 0)
    assert r.rule_code == "IN-VDA" and r.rate == Decimal("0.01") and r.tax_amount == Decimal("200.00")
    r = E.compute(inp(**base, bill_amount=Decimal("20000")), None, 0)
    assert r.rule_code == "IN-VDA-NO-PAN" and r.rate == Decimal("0.20")
    r = E.compute(inp(**base, bill_amount=Decimal("5000"), vda_year_to_date_inr=Decimal("8000"),
                      supplier_pan="ABCDE1234F"), None, 0)
    assert r.rule_code == "IN-VDA"


def test_withholding_never_exceeds_gross():
    settings = {"crypto_withholding": {"default": {"code": "ALL", "rate": "1.5"}}}
    s = snap()
    r = E.compute(inp(tenant_settings=settings, bill_amount=Decimal("10")), s, 10_000_000)
    assert r.amount_units == 10_000_000 and r.net_units == 0


def test_fx_snapshot_conversions():
    s = snap("INR", usd_bill="84.10")
    assert s.bill_to_units(Decimal("84.10")) == 1_000_000
    assert s.bill_to_usd(Decimal("841.00")) == Decimal("10.00")
    assert snap(token_usd="0.999").bill_to_units(Decimal("1")) == 1_001_001
