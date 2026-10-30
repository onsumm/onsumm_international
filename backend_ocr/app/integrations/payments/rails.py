"""Payment rails: how an approved bill's money actually moves.

Two rails share one disbursement table (`fact_disbursements.rail`), so OTP
release, the in-flight check, the payment history and reports work for both:

  - IMPS: today's bank flow in routers/disbursement.py with the per-bank
    adapters in this package. Unchanged; `ImpsRail` only describes it.
  - USDC: a payout from the client's Safe (domain/crypto/payouts.py).

A rail object answers the questions the router needs before it hands a bill to
one flow or the other; the flows themselves stay where they are.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Rail(Protocol):
    name: str
    currency_kind: str      # "fiat" | "token"

    def available_for(self, is_resident_india: bool, crypto_enabled: bool) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class ImpsRail:
    name: str = "IMPS"
    currency_kind: str = "fiat"
    handled_by: str = "routers/disbursement.py"

    def available_for(self, is_resident_india: bool, crypto_enabled: bool) -> tuple[bool, str]:
        return True, ""


@dataclass(frozen=True)
class UsdcRail:
    name: str = "USDC"
    currency_kind: str = "token"
    handled_by: str = "domain/crypto/payouts.py"

    def available_for(self, is_resident_india: bool, crypto_enabled: bool) -> tuple[bool, str]:
        if is_resident_india:
            return False, "CRYPTO_DISABLED_RESIDENT_INDIA"
        if not crypto_enabled:
            return False, "CRYPTO_NOT_ENABLED"
        return True, ""


RAILS: dict[str, Rail] = {"IMPS": ImpsRail(), "USDC": UsdcRail()}
