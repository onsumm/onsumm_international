"""WithholdingEngine: picks the rule pack for a payout and turns its rule into token units.

Pack choice follows who pays (00_DECISIONS.md D5):

  - A tenant incorporated outside India -> its client-jurisdiction pack
    (`ConfigurablePack`, code from settings, `SG-DEMO` by default).
  - An India-resident tenant -> India packs, and only on the bank rail: crypto
    is refused for them by PolicyEngine. The preview still shows what they would
    withhold (IN-195 for a non-resident vendor, IN-SERVICES otherwise), which is
    the "guardrail moment" in the demo.
  - IN-VDA applies only to an Indian resident buying a VDA (`is_vda_purchase`).

Amounts (04_LLD.md §6h): the tax is fixed in fiat first (rounded half-up to the
cent), then converted at the payout's frozen snapshot and rounded DOWN to whole
token units, so Onsumm never over-withholds.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from ..fx import FxSnapshot
from .packs import PACKS, ConfigurablePack, Rule, WithholdingInput


@dataclass(frozen=True)
class WithholdingResult:
    rule_pack: str
    rule_code: str
    rate: Decimal
    base_amount: Decimal
    base_currency: str
    tax_amount: Decimal          # fiat, base currency
    amount_units: int            # token units withheld
    gross_units: int
    net_units: int
    requires_signoff: bool
    note: str

    def as_dict(self) -> dict:
        return {
            "rule_pack": self.rule_pack, "rule_code": self.rule_code, "rate": str(self.rate),
            "base_amount": f"{self.base_amount:.2f}", "base_currency": self.base_currency,
            "tax_amount": f"{self.tax_amount:.2f}", "amount_units": str(self.amount_units),
            "net_units": str(self.net_units), "requires_signoff": self.requires_signoff, "note": self.note,
        }


class WithholdingEngine:
    def select_pack(self, inp: WithholdingInput):
        if inp.is_vda_purchase and inp.tenant_is_resident_india:
            return PACKS["IN-VDA"]
        if inp.tenant_is_resident_india:
            return PACKS["IN-SERVICES"] if inp.supplier_is_resident_india else PACKS["IN-195"]
        code = ((inp.tenant_settings or {}).get("crypto_withholding") or {}).get("pack") or "SG-DEMO"
        return PACKS.get(code) or ConfigurablePack(code)

    def compute(self, inp: WithholdingInput, snapshot: Optional[FxSnapshot], gross_units: int) -> WithholdingResult:
        pack = self.select_pack(inp)
        rule: Rule = pack.rule_for(inp)
        tax_fiat = (rule.base_amount * rule.rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        units = snapshot.fiat_to_units(tax_fiat, rule.base_currency) if (snapshot and tax_fiat > 0) else 0
        units = min(units, gross_units)
        return WithholdingResult(
            rule_pack=pack.code, rule_code=rule.code, rate=rule.rate, base_amount=rule.base_amount,
            base_currency=rule.base_currency, tax_amount=tax_fiat, amount_units=units,
            gross_units=gross_units, net_units=gross_units - units,
            requires_signoff=rule.requires_signoff, note=rule.note,
        )
