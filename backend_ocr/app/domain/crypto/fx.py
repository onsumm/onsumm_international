"""FxService: the frozen rate behind every USDC payout (`fx_snapshots`).

A payout converts a bill amount (bill currency) into USDC token units once, at
preparation, and every later figure — withholding, books, the INR reference an
Indian parent consolidates with — uses that same snapshot. A rate that moved
between preview and settlement would otherwise change what the vendor gets
after an approver has already seen the number.

FX_SOURCE=fixed (the hackathon default) takes rates from the environment and
records `source='fixed'` on the row, so nobody mistakes a demo rate for a
market quote:

    FX_TOKEN_USD   USD per 1 USDC            default 1.0
    FX_USD_INR     INR per 1 USD             default 84.10
    FX_USD_<CCY>   <CCY> per 1 USD           USD is always 1

A currency with no rate is refused rather than guessed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Optional

from .settings import get_settings

UNITS_PER_TOKEN = Decimal(10**6)   # USDC has 6 decimals


class FxRateUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class FxSnapshot:
    id: Optional[str]
    token: str
    token_usd: Decimal          # USD per token
    usd_functional: Decimal     # functional-currency units per USD
    usd_inr: Decimal            # INR per USD (reference for an Indian parent)
    bill_currency: str
    usd_bill: Decimal           # bill-currency units per USD
    source: str

    def bill_to_units(self, amount: Decimal) -> int:
        """Bill-currency amount -> token units, rounded down (never overpays)."""
        usd = Decimal(amount) / self.usd_bill
        return int((usd / self.token_usd * UNITS_PER_TOKEN).to_integral_value(ROUND_DOWN))

    def rate_for(self, currency: str) -> Decimal:
        """Units of `currency` per USD from this frozen snapshot; env only as a last resort."""
        ccy = (currency or "USD").strip().upper()
        if ccy in ("USD", "USDC"):
            return Decimal(1)
        if ccy == self.bill_currency:
            return self.usd_bill
        if ccy == "INR":
            return self.usd_inr
        return FxService.usd_rate(ccy)

    def fiat_to_units(self, amount: Decimal, currency: str) -> int:
        usd = Decimal(amount) / self.rate_for(currency)
        return int((usd / self.token_usd * UNITS_PER_TOKEN).to_integral_value(ROUND_DOWN))

    def bill_to_usd(self, amount: Decimal) -> Decimal:
        return (Decimal(amount) / self.usd_bill).quantize(Decimal("0.01"))

    def as_dict(self) -> dict:
        return {
            "fx_snapshot_id": self.id, "token_usd": str(self.token_usd),
            "usd_inr": str(self.usd_inr), "usd_functional": str(self.usd_functional),
            "bill_currency": self.bill_currency, "source": self.source,
        }


class FxService:
    @staticmethod
    def usd_rate(currency: str) -> Decimal:
        """Units of `currency` per 1 USD."""
        ccy = (currency or "USD").strip().upper()
        if ccy in ("USD", "USDC"):
            return Decimal(1)
        default = "84.10" if ccy == "INR" else None
        raw = os.getenv(f"FX_USD_{ccy}", default)
        if raw is None:
            raise FxRateUnavailable(f"No FX rate configured for {ccy} (set FX_USD_{ccy}).")
        rate = Decimal(str(raw))
        if rate <= 0:
            raise FxRateUnavailable(f"FX rate for {ccy} must be positive.")
        return rate

    def quote(self, bill_currency: str, functional_currency: str, token: str = "USDC") -> FxSnapshot:
        source = get_settings().fx_source
        if source != "fixed":
            raise FxRateUnavailable(f"FX_SOURCE={source} is not supported yet; use 'fixed'.")
        return FxSnapshot(
            id=None, token=token.upper(),
            token_usd=Decimal(os.getenv("FX_TOKEN_USD", "1.0")),
            usd_functional=self.usd_rate(functional_currency),
            usd_inr=self.usd_rate("INR"),
            bill_currency=(bill_currency or "USD").upper(),
            usd_bill=self.usd_rate(bill_currency),
            source=source,
        )

    def snapshot(self, cur, bill_currency: str, functional_currency: str, token: str = "USDC") -> FxSnapshot:
        """Quote and store; the returned snapshot carries its row id."""
        q = self.quote(bill_currency, functional_currency, token)
        cur.execute(
            """INSERT INTO fx_snapshots (token, token_usd, usd_functional, usd_inr, bill_currency, usd_bill, source)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (q.token, q.token_usd, q.usd_functional, q.usd_inr, q.bill_currency, q.usd_bill, q.source),
        )
        row = cur.fetchone()
        snap_id = str(row["id"] if isinstance(row, dict) else row[0])
        return FxSnapshot(**{**q.__dict__, "id": snap_id})

    def load(self, cur, snapshot_id: str) -> FxSnapshot:
        cur.execute("SELECT * FROM fx_snapshots WHERE id=%s", (snapshot_id,))
        r = cur.fetchone()
        return FxSnapshot(
            id=str(r["id"]), token=r["token"], token_usd=Decimal(r["token_usd"]),
            usd_functional=Decimal(r["usd_functional"]), usd_inr=Decimal(r["usd_inr"] or 0),
            bill_currency=(r["bill_currency"] or "USD").strip(), usd_bill=Decimal(r["usd_bill"] or 1),
            source=r["source"],
        )


def units_to_str(units: int) -> str:
    """Token units -> human USDC string with 2 decimals (display only)."""
    return f"{(Decimal(units) / UNITS_PER_TOKEN):,.2f}"
