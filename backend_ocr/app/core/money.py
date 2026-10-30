"""Server-side money formatting in a tenant's own currency.

Text the server writes for people — approval pages, audit comments, prompts —
used to hardcode "₹". For an INR tenant this returns exactly what was there
before ("₹1,234.50"); other currencies get their own symbol ("$4,200.00",
"AED 1,000.00"). The tenant's books currency comes from core/jurisdiction.py.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

_SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "SGD": "S$", "AUD": "A$", "CAD": "C$"}

Number = Union[int, float, Decimal]


def currency_symbol(currency: Optional[str]) -> str:
    code = (currency or "INR").strip().upper()
    return _SYMBOLS.get(code, f"{code} ")


def format_money(amount: Optional[Number], currency: Optional[str] = "INR", decimals: int = 2,
                 fallback: str = "—") -> str:
    """`format_money(1234.5)` → "₹1,234.50"; `format_money(4200, "USD")` → "$4,200.00"."""
    if amount is None:
        return fallback
    return f"{currency_symbol(currency)}{float(amount):,.{decimals}f}"
