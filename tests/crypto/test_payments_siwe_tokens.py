"""Vendor wallet proof (EIP-4361) and the vendor portal link token."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from backend_ocr.app.domain.crypto.siwe import SiweError, SiweVerifier
from backend_ocr.app.domain.crypto.wallets import PortalLinkInvalid, make_portal_token, read_portal_token

V = SiweVerifier(domain="app.onsumm.ai", uri="https://app.onsumm.ai/vendor-portal", chain_id=84532)
STATEMENT = "I confirm this wallet receives payments for Nova Design LLC from Lumen Labs Pte Ltd."


def _signed(acct, msg_text: str) -> str:
    return "0x" + Account.sign_message(encode_defunct(text=msg_text), acct.key).signature.hex().removeprefix("0x")


def test_build_parse_verify_round_trip():
    acct = Account.create()
    msg = V.build_message(acct.address, STATEMENT)
    text = msg.text()
    assert text.startswith("app.onsumm.ai wants you to sign in with your Ethereum account:\n" + acct.address)
    assert "Chain ID: 84532" in text and f"Nonce: {msg.nonce}" in text
    assert V.parse(text) == msg
    assert V.verify(text, _signed(acct, text)).address == acct.address


def test_signature_from_another_wallet_is_rejected():
    acct, other = Account.create(), Account.create()
    text = V.build_message(acct.address, STATEMENT).text()
    with pytest.raises(SiweError) as e:
        V.verify(text, _signed(other, text))
    assert e.value.code == "SIGNER_MISMATCH"


def test_tampered_message_is_rejected():
    acct = Account.create()
    text = V.build_message(acct.address, STATEMENT).text()
    sig = _signed(acct, text)
    with pytest.raises(SiweError) as e:
        V.verify(text.replace("Nova Design LLC", "Evil Corp"), sig)
    assert e.value.code == "SIGNER_MISMATCH"   # still parses, but the signature no longer matches
    with pytest.raises(SiweError) as e:
        V.verify(text + "\n", sig)
    assert e.value.code == "MALFORMED_MESSAGE"


def test_expired_wrong_chain_wrong_domain():
    acct = Account.create()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    text = V.build_message(acct.address, STATEMENT, now=past).text()
    with pytest.raises(SiweError) as e:
        V.verify(text, _signed(acct, text))
    assert e.value.code == "EXPIRED"

    mainnet = SiweVerifier(domain="app.onsumm.ai", uri="https://app.onsumm.ai/vendor-portal", chain_id=8453)
    text = mainnet.build_message(acct.address, STATEMENT).text()
    with pytest.raises(SiweError) as e:
        V.verify(text, _signed(acct, text))
    assert e.value.code == "WRONG_CHAIN"

    phishing = SiweVerifier(domain="onsumm-pay.io", uri="https://onsumm-pay.io/vendor-portal", chain_id=84532)
    text = phishing.build_message(acct.address, STATEMENT).text()
    with pytest.raises(SiweError) as e:
        V.verify(text, _signed(acct, text))
    assert e.value.code == "WRONG_DOMAIN"


def test_replayed_message_parses_to_the_same_nonce():
    # Replay protection is the nonce table (each nonce accepted once, see
    # WalletService.verify); here we only pin that the nonce survives a round trip.
    acct = Account.create()
    msg = V.build_message(acct.address, STATEMENT)
    assert V.verify(msg.text(), _signed(acct, msg.text())).nonce == msg.nonce
    assert V.build_message(acct.address, STATEMENT).nonce != msg.nonce


def test_invalid_address_refused():
    with pytest.raises(SiweError):
        V.build_message("0x123", STATEMENT)


def test_portal_token_round_trip_expiry_and_tamper():
    token, exp = make_portal_token("t-1", "s-1", ttl_hours=72, now=1_000_000)
    assert exp == datetime.fromtimestamp(1_000_000 + 72 * 3600, tz=timezone.utc)
    claims = read_portal_token(token, now=1_000_000 + 3600)
    assert claims["tenant_id"] == "t-1" and claims["supplier_id"] == "s-1"
    with pytest.raises(PortalLinkInvalid):
        read_portal_token(token, now=1_000_000 + 72 * 3600 + 1)
    payload, sig = token.split(".")
    forged, _ = make_portal_token("t-2", "s-1", ttl_hours=72, now=1_000_000)
    with pytest.raises(PortalLinkInvalid):
        read_portal_token(forged.split(".")[0] + "." + sig, now=1_000_000)
    with pytest.raises(PortalLinkInvalid):
        read_portal_token("garbage", now=1_000_000)
