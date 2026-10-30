"""CustodyProvider: who holds the keys that move a tenant's USDC.

Onsumm never holds such a key (00_DECISIONS.md D3). A provider turns a payout
into something the client's own key holders approve, and reports what the
treasury holds. Two implementations:

  - SafeCustody: the client's Safe multisig; owners sign in their browser
    wallets, a gas-only relayer submits once the threshold is met.
  - PartnerCustody: a regulated custody partner (production option B; a stub
    for the hackathon).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Transfer:
    to: str              # recipient address (vendor or tax wallet)
    amount_units: int    # token units


@dataclass(frozen=True)
class SafeInfo:
    address: str
    owners: tuple[str, ...]
    threshold: int
    nonce: int


class CustodyProvider(Protocol):
    def info(self, wallet_address: str) -> SafeInfo: ...

    def balance(self, wallet_address: str) -> int: ...

    def build_payout(self, wallet_address: str, transfers: list[Transfer], nonce: int) -> dict: ...

    def tx_hash(self, wallet_address: str, tx: dict) -> bytes: ...

    def recover_signer(self, wallet_address: str, tx: dict, signature: str) -> str: ...

    def execute(self, wallet_address: str, tx: dict, signatures: list[tuple[str, str]]) -> str: ...
