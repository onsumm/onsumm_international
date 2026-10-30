"""Withholding rule packs: one jurisdiction's rules for tax withheld from a payout.

A pack only answers "which rule, which rate, on what base". Turning that into
token units at the payout's frozen FX snapshot is the engine's job, so every
pack is converted and rounded the same way (04_LLD.md §6h).

Packs (00_DECISIONS.md D5):

  SG-DEMO      ConfigurablePack. A client-jurisdiction pack whose rules are
               entered by the client's tax adviser in
               dim_tenants.settings_jsonb->'crypto_withholding'. Onsumm does not
               assert any country's tax law; with no rules entered the demo
               default is 0% and *requires adviser sign-off*.
  IN-SERVICES  Indian services TDS, as already computed by the TDS engine for
               the bill (fact_bills.tds_*). Indian payers, bank rail.
  IN-195       Section 195: an Indian payer paying a non-resident. 20% default
               (plus surcharge and cess, which the adviser confirms); the treaty
               rate applies only when the vendor has a TRC and Form 10F on file.
  IN-VDA       Section 194S / s.393(1) of the Income-tax Act 2025: 1% on paying a
               resident for a VDA, 20% without PAN, above ₹50,000 a year for a
               specified person or ₹10,000 for others.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol


@dataclass(frozen=True)
class Rule:
    code: str
    rate: Decimal                 # 0.20 = 20%
    base_amount: Decimal          # in base_currency
    base_currency: str
    requires_signoff: bool
    note: str = ""


@dataclass
class WithholdingInput:
    rail: str                             # USDC | IMPS
    tenant_settings: dict                 # dim_tenants.settings_jsonb
    tenant_is_resident_india: bool
    supplier_category: Optional[str]      # vendor_category_id or category text
    supplier_pan: Optional[str]
    supplier_tax_docs: dict               # dim_suppliers.tax_docs
    supplier_is_resident_india: bool
    bill_amount: Decimal                  # the amount being paid, bill currency
    bill_currency: str
    bill_tds_amount: Optional[Decimal] = None   # from the TDS engine (IN-SERVICES)
    bill_tds_rate: Optional[Decimal] = None
    bill_tds_section: Optional[str] = None
    is_vda_purchase: bool = False
    vda_payer_specified_person: bool = False
    vda_year_to_date_inr: Decimal = Decimal(0)
    amount_inr: Optional[Decimal] = None        # bill amount in INR, for INR thresholds


class RulePack(Protocol):
    code: str

    def rule_for(self, inp: WithholdingInput) -> Rule: ...


def _d(v, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    return Decimal(str(v))


class ConfigurablePack:
    """Client-jurisdiction rules entered by the client's adviser.

    settings_jsonb->'crypto_withholding' shape:
      {"pack": "SG-DEMO",
       "default": {"code": "SG-DEMO-DEFAULT", "rate": "0", "rate_no_tax_id": "0", "requires_signoff": true},
       "rules": {"<vendor category>": {"code": "...", "rate": "0.10", "requires_signoff": false}}}
    """

    def __init__(self, code: str = "SG-DEMO"):
        self.code = code

    def rule_for(self, inp: WithholdingInput) -> Rule:
        cfg = (inp.tenant_settings or {}).get("crypto_withholding") or {}
        default = cfg.get("default") or {}
        rules = cfg.get("rules") or {}
        rule = rules.get(str(inp.supplier_category)) if inp.supplier_category else None
        chosen = rule or default
        has_tax_id = bool((inp.supplier_tax_docs or {}).get("tax_id") or inp.supplier_pan
                          or (inp.supplier_tax_docs or {}).get("W-8BEN-E"))
        rate_key = "rate" if has_tax_id or "rate_no_tax_id" not in chosen else "rate_no_tax_id"
        configured = bool(chosen)
        return Rule(
            code=chosen.get("code") or f"{self.code}-DEFAULT",
            rate=_d(chosen.get(rate_key), "0"),
            base_amount=Decimal(inp.bill_amount),
            base_currency=inp.bill_currency,
            # With nothing configured, the 0% default must be confirmed by an adviser.
            requires_signoff=bool(chosen.get("requires_signoff", True)) if configured else True,
            note=("Configured by the client's tax adviser." if configured
                  else "Demo default: no withholding rule entered; requires adviser sign-off."),
        )


class IndiaServicesPack:
    """Indian services TDS, as the existing TDS engine already worked it out for the bill."""
    code = "IN-SERVICES"

    def rule_for(self, inp: WithholdingInput) -> Rule:
        if inp.bill_tds_rate is None and inp.bill_tds_amount is None:
            return Rule("IN-SERVICES-NONE", Decimal(0), Decimal(inp.bill_amount), inp.bill_currency, True,
                        "No TDS computed for this bill yet; run the TDS engine first.")
        rate = _d(inp.bill_tds_rate)
        if rate > 1:          # stored as a percent in some rows
            rate = rate / 100
        return Rule(f"IN-SERVICES-{inp.bill_tds_section or 'TDS'}", rate, Decimal(inp.bill_amount),
                    inp.bill_currency, False, "From the bill's TDS engine result.")


class IndiaNonResidentPack:
    """Section 195: an Indian payer paying a non-resident vendor."""
    code = "IN-195"

    def rule_for(self, inp: WithholdingInput) -> Rule:
        cfg = ((inp.tenant_settings or {}).get("crypto_withholding") or {}).get("in195") or {}
        docs = inp.supplier_tax_docs or {}
        has_treaty_docs = bool(docs.get("TRC")) and bool(docs.get("FORM_10F"))
        if has_treaty_docs:
            rate = _d(docs.get("treaty_rate") or cfg.get("treaty_rate"), "0.15")
            return Rule("IN-195-TREATY", rate, Decimal(inp.bill_amount), inp.bill_currency, True,
                        "Treaty rate with TRC and Form 10F on file; adviser confirms the article and rate.")
        rate = _d(cfg.get("default_rate"), "0.20")
        return Rule("IN-195-DEFAULT", rate, Decimal(inp.bill_amount), inp.bill_currency, True,
                    "Section 195 default rate; surcharge and 4% cess may apply. Adviser confirms.")


class IndiaVdaPack:
    """Section 194S / s.393(1): 1% when an Indian resident pays a resident for a VDA."""
    code = "IN-VDA"
    RATE = Decimal("0.01")
    RATE_NO_PAN = Decimal("0.20")
    THRESHOLD_SPECIFIED = Decimal("50000")
    THRESHOLD_OTHERS = Decimal("10000")

    def rule_for(self, inp: WithholdingInput) -> Rule:
        amount_inr = Decimal(inp.amount_inr if inp.amount_inr is not None else inp.bill_amount)
        threshold = self.THRESHOLD_SPECIFIED if inp.vda_payer_specified_person else self.THRESHOLD_OTHERS
        if inp.vda_year_to_date_inr + amount_inr <= threshold:
            return Rule("IN-VDA-BELOW-THRESHOLD", Decimal(0), Decimal(inp.bill_amount), inp.bill_currency, False,
                        f"Year-to-date VDA consideration stays within ₹{threshold:,.0f}.")
        if not inp.supplier_pan:
            return Rule("IN-VDA-NO-PAN", self.RATE_NO_PAN, Decimal(inp.bill_amount), inp.bill_currency, False,
                        "20% because the payee has no PAN.")
        return Rule("IN-VDA", self.RATE, Decimal(inp.bill_amount), inp.bill_currency, False,
                    "1% TDS on transfer of a virtual digital asset.")


PACKS = {
    "SG-DEMO": ConfigurablePack("SG-DEMO"),
    "IN-SERVICES": IndiaServicesPack(),
    "IN-195": IndiaNonResidentPack(),
    "IN-VDA": IndiaVdaPack(),
}
