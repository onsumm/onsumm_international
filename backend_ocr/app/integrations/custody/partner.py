"""PartnerCustody: production option B, a regulated custody and payouts partner.

Not built for the hackathon. It exists so the CustodyProvider seam is real: a
partner (Bridge, Fireblocks, Circle or BVNK) would hold an MPC wallet under the
client's own policy, and Onsumm would request transfers through its API with
the disbursement id as the idempotency key. See docs/crypto-rail/01_BUSINESS.md
§7 for the choice between options A and B.
"""
from __future__ import annotations

from .base import SafeInfo, Transfer

_MESSAGE = ("Partner custody is the production option and is not implemented in this build. "
            "Register the tenant's Safe as its treasury wallet instead.")


class PartnerCustody:
    def __init__(self, partner: str, api_key_ref: str = ""):
        self.partner = partner
        self.api_key_ref = api_key_ref

    def info(self, wallet_address: str) -> SafeInfo:
        raise NotImplementedError(_MESSAGE)

    def balance(self, wallet_address: str) -> int:
        raise NotImplementedError(_MESSAGE)

    def build_payout(self, wallet_address: str, transfers: list[Transfer], nonce: int) -> dict:
        raise NotImplementedError(_MESSAGE)

    def tx_hash(self, wallet_address: str, tx: dict) -> bytes:
        raise NotImplementedError(_MESSAGE)

    def recover_signer(self, wallet_address: str, tx: dict, signature: str) -> str:
        raise NotImplementedError(_MESSAGE)

    def execute(self, wallet_address: str, tx: dict, signatures: list[tuple[str, str]]) -> str:
        raise NotImplementedError(_MESSAGE)
