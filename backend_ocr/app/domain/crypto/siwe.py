"""SiweVerifier: prove a vendor controls a wallet, with an EIP-4361 message.

This is the wallet equivalent of a penny-drop test. The vendor signs a
human-readable message naming the payer and payee; the server recovers the
signer and accepts the wallet only if it equals the address in the message.

Built here rather than with the `siwe` package (one less dependency for one
message format). Only the fields Onsumm issues are parsed, and the message must
round-trip exactly: the server rebuilds it from the parsed fields and compares,
so any edit to the text — even whitespace — is rejected before the signature is
even checked. Replay protection (a nonce accepted once) lives with the caller,
which owns the nonce table.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

CHALLENGE_TTL = timedelta(minutes=10)


class SiweError(ValueError):
    """The message or signature is not acceptable; `code` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SiweMessage:
    domain: str
    address: str
    statement: str
    uri: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime

    def text(self) -> str:
        return (
            f"{self.domain} wants you to sign in with your Ethereum account:\n"
            f"{self.address}\n\n"
            f"{self.statement}\n\n"
            f"URI: {self.uri}\n"
            f"Version: 1\n"
            f"Chain ID: {self.chain_id}\n"
            f"Nonce: {self.nonce}\n"
            f"Issued At: {_iso(self.issued_at)}\n"
            f"Expiration Time: {_iso(self.expiration_time)}"
        )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


_PATTERN = re.compile(
    r"^(?P<domain>[^\n]+) wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[0-9a-fA-F]{40})\n\n"
    r"(?P<statement>[^\n]+)\n\n"
    r"URI: (?P<uri>[^\n]+)\n"
    r"Version: 1\n"
    r"Chain ID: (?P<chain_id>\d+)\n"
    r"Nonce: (?P<nonce>[A-Za-z0-9]{8,})\n"
    r"Issued At: (?P<issued_at>[0-9TZ:\-]+)\n"
    r"Expiration Time: (?P<expiration>[0-9TZ:\-]+)$"
)


class SiweVerifier:
    def __init__(self, domain: str, uri: str, chain_id: int, ttl: timedelta = CHALLENGE_TTL):
        self.domain = domain
        self.uri = uri
        self.chain_id = chain_id
        self.ttl = ttl

    def build_message(self, address: str, statement: str, now: Optional[datetime] = None,
                      nonce: Optional[str] = None) -> SiweMessage:
        if not Web3.is_address(address or ""):
            raise SiweError("INVALID_ADDRESS", "Not a valid EVM address.")
        now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        if "\n" in statement:
            raise SiweError("INVALID_STATEMENT", "Statement must be one line.")
        return SiweMessage(
            domain=self.domain, address=Web3.to_checksum_address(address), statement=statement,
            uri=self.uri, chain_id=self.chain_id, nonce=nonce or secrets.token_hex(8),
            issued_at=now, expiration_time=now + self.ttl,
        )

    @staticmethod
    def parse(message: str) -> SiweMessage:
        m = _PATTERN.match(message or "")
        if not m:
            raise SiweError("MALFORMED_MESSAGE", "Message is not a valid sign-in message.")
        parsed = SiweMessage(
            domain=m["domain"], address=m["address"], statement=m["statement"], uri=m["uri"],
            chain_id=int(m["chain_id"]), nonce=m["nonce"],
            issued_at=_parse_iso(m["issued_at"]), expiration_time=_parse_iso(m["expiration"]),
        )
        if parsed.text() != message:
            raise SiweError("MALFORMED_MESSAGE", "Message text was altered.")
        return parsed

    def verify(self, message: str, signature: str, now: Optional[datetime] = None) -> SiweMessage:
        """Return the parsed message if `signature` is the address's own signature over it."""
        parsed = self.parse(message)
        now = now or datetime.now(timezone.utc)
        if parsed.domain != self.domain or parsed.uri != self.uri:
            raise SiweError("WRONG_DOMAIN", "Message was issued for a different site.")
        if parsed.chain_id != self.chain_id:
            raise SiweError("WRONG_CHAIN", f"Message is for chain {parsed.chain_id}, expected {self.chain_id}.")
        if now >= parsed.expiration_time:
            raise SiweError("EXPIRED", "Sign-in message has expired; request a new one.")
        try:
            recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
        except Exception:
            raise SiweError("BAD_SIGNATURE", "Signature could not be read.")
        if recovered.lower() != parsed.address.lower():
            raise SiweError("SIGNER_MISMATCH", "Signature was not made by the wallet in the message.")
        return parsed
