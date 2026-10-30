"""SafeCustody building blocks: EIP-712 hash, MultiSend packing, signatures, execTransaction.

The Safe tx hash is computed two ways — eth_account's typed-data encoder and a
hand-rolled EIP-712 struct hash using the Safe contract's own type hashes — so a
mistake in either shows up as a mismatch rather than as a payout the Safe rejects.
"""
from __future__ import annotations

import pytest
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from backend_ocr.app.integrations.custody.base import Transfer
from backend_ocr.app.integrations.custody.safe import (
    MULTISEND_SELECTOR, TRANSFER_SELECTOR, ZERO_ADDRESS, SafeTxRefused, assert_safe_operation, build_payout_tx,
    exec_transaction_calldata, pack_multisend, pack_signatures, recover_signer, safe_tx_hash, typed_data,
)

USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
MULTISEND = "0x9641d764fc13c8B624c04430C7356C1C7C8102e2"
SAFE = "0x4b1E3f4bE0a7d8c3b2E1f0A9b8C7d6E5f4A3b2C1"
VENDOR = "0x7A5c1d2E3f4A5b6C7d8E9f0A1b2C3d4E5f6a7B8c"
TAX = "0x1111111111111111111111111111111111111111"
CHAIN_ID = 84532

# Constants from the Safe contracts (v1.3.0+).
SAFE_TX_TYPEHASH = "bb8310d486368db6bd6f849402fdd73ad53d316b5a4b2644ad6efe0f941286d8"
DOMAIN_SEPARATOR_TYPEHASH = "47e79534a245952e8b16893a336b85a3d9ea9fa8c573f3d803afb92a79469218"


def _manual_hash(safe: str, chain_id: int, tx: dict) -> bytes:
    type_hash = Web3.keccak(text=(
        "SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,uint256 baseGas,"
        "uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)"))
    domain_hash = Web3.keccak(text="EIP712Domain(uint256 chainId,address verifyingContract)")
    assert type_hash.hex().removeprefix("0x") == SAFE_TX_TYPEHASH
    assert domain_hash.hex().removeprefix("0x") == DOMAIN_SEPARATOR_TYPEHASH
    domain = Web3.keccak(abi_encode(["bytes32", "uint256", "address"], [domain_hash, chain_id, safe]))
    struct = Web3.keccak(abi_encode(
        ["bytes32", "address", "uint256", "bytes32", "uint8", "uint256", "uint256", "uint256", "address", "address",
         "uint256"],
        [type_hash, tx["to"], tx["value"], Web3.keccak(tx["data"]), tx["operation"], tx["safeTxGas"],
         tx["baseGas"], tx["gasPrice"], tx["gasToken"], tx["refundReceiver"], tx["nonce"]]))
    return bytes(Web3.keccak(b"\x19\x01" + domain + struct))


def _sign(key, tx: dict) -> str:
    td = typed_data(SAFE, CHAIN_ID, tx)
    msg = dict(td["message"])
    for k in ("value", "safeTxGas", "baseGas", "gasPrice", "nonce"):
        msg[k] = int(msg[k])
    msg["data"] = bytes.fromhex(msg["data"][2:])
    return "0x" + Account.sign_typed_data(key, full_message={**td, "message": msg}).signature.hex().removeprefix("0x")


def test_single_transfer_is_a_plain_call_to_usdc():
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 3_600_000_000)], 7, MULTISEND)
    assert tx["operation"] == 0 and tx["to"] == USDC and tx["nonce"] == 7
    assert tx["data"][:4] == TRANSFER_SELECTOR == bytes.fromhex("a9059cbb")
    to, amount = abi_decode(["address", "uint256"], tx["data"][4:])
    assert to.lower() == VENDOR.lower() and amount == 3_600_000_000
    assert tx["gasPrice"] == 0 and tx["refundReceiver"] == ZERO_ADDRESS


def test_safe_tx_hash_matches_manual_eip712():
    for tx in (build_payout_tx(USDC, [Transfer(VENDOR, 3_600_000_000)], 7, MULTISEND),
               build_payout_tx(USDC, [Transfer(VENDOR, 3_240_000_000), Transfer(TAX, 360_000_000)], 8, MULTISEND)):
        assert safe_tx_hash(SAFE, CHAIN_ID, tx) == _manual_hash(SAFE, CHAIN_ID, tx)


def test_hash_changes_with_nonce_chain_and_safe():
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 1_000_000)], 1, MULTISEND)
    base = safe_tx_hash(SAFE, CHAIN_ID, tx)
    assert base != safe_tx_hash(SAFE, CHAIN_ID, {**tx, "nonce": 2})
    assert base != safe_tx_hash(SAFE, 8453, tx)
    assert base != safe_tx_hash(TAX, CHAIN_ID, tx)


def test_multisend_packs_two_usdc_transfers():
    transfers = [Transfer(VENDOR, 3_240_000_000), Transfer(TAX, 360_000_000)]
    tx = build_payout_tx(USDC, transfers, 9, MULTISEND)
    assert tx["operation"] == 1 and tx["to"] == MULTISEND
    assert tx["data"][:4] == MULTISEND_SELECTOR == bytes.fromhex("8d80ff0a")
    packed = abi_decode(["bytes"], tx["data"][4:])[0]
    assert packed == pack_multisend(USDC, transfers)
    one = 1 + 20 + 32 + 32 + 68
    assert len(packed) == 2 * one
    for i, t in enumerate(transfers):
        chunk = packed[i * one:(i + 1) * one]
        assert chunk[0] == 0                                   # inner CALL
        assert chunk[1:21] == bytes.fromhex(USDC[2:].lower())  # to = USDC
        assert int.from_bytes(chunk[21:53], "big") == 0         # value
        assert int.from_bytes(chunk[53:85], "big") == 68        # data length
        to, amount = abi_decode(["address", "uint256"], chunk[89:])
        assert to.lower() == t.to.lower() and amount == t.amount_units


def test_zero_leg_collapses_to_single_transfer():
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 5_000_000), Transfer(TAX, 0)], 1, MULTISEND)
    assert tx["operation"] == 0


def test_delegatecall_only_to_multisend_and_no_refunds():
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 1), Transfer(TAX, 1)], 1, MULTISEND)
    assert_safe_operation(tx, MULTISEND)
    with pytest.raises(SafeTxRefused) as e:
        assert_safe_operation({**tx, "to": TAX}, MULTISEND)
    assert e.value.code == "DELEGATECALL_REFUSED"
    plain = build_payout_tx(USDC, [Transfer(VENDOR, 1)], 1, MULTISEND)
    with pytest.raises(SafeTxRefused) as e:
        assert_safe_operation({**plain, "gasPrice": 1}, MULTISEND)
    assert e.value.code == "REFUND_REFUSED"
    with pytest.raises(SafeTxRefused):
        build_payout_tx(USDC, [Transfer(VENDOR, 0)], 1, MULTISEND)


def test_signature_round_trip_and_owner_ordering():
    owners = [Account.create() for _ in range(3)]
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 3_600_000_000)], 7, MULTISEND)
    sigs = []
    for o in owners:
        sig = _sign(o.key, tx)
        assert recover_signer(SAFE, CHAIN_ID, tx, sig) == o.address
        sigs.append((o.address, sig))
    packed = pack_signatures(list(reversed(sigs)))
    assert len(packed) == 65 * 3
    expected_order = sorted(sigs, key=lambda s: int(s[0], 16))
    for i, (addr, sig) in enumerate(expected_order):
        assert packed[i * 65:(i + 1) * 65] == bytes.fromhex(sig[2:])


def test_signature_v_0_1_is_normalised_and_tampering_detected():
    owner = Account.create()
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 1_000_000)], 3, MULTISEND)
    sig = bytes.fromhex(_sign(owner.key, tx)[2:])
    low_v = sig[:64] + bytes([sig[64] - 27])
    assert recover_signer(SAFE, CHAIN_ID, tx, "0x" + low_v.hex()) == owner.address
    # A signature over a different amount recovers someone else.
    other = build_payout_tx(USDC, [Transfer(VENDOR, 2_000_000)], 3, MULTISEND)
    assert recover_signer(SAFE, CHAIN_ID, other, "0x" + sig.hex()) != owner.address
    with pytest.raises(SafeTxRefused):
        recover_signer(SAFE, CHAIN_ID, tx, "0x" + sig[:64].hex())


def test_exec_transaction_calldata():
    owner = Account.create()
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 1_000_000)], 3, MULTISEND)
    sig = _sign(owner.key, tx)
    data = exec_transaction_calldata(tx, pack_signatures([(owner.address, sig)]))
    assert data.startswith("0x6a761202")
    decoded = abi_decode(["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address",
                          "address", "bytes"], bytes.fromhex(data[10:]))
    assert decoded[0].lower() == USDC.lower() and decoded[2] == tx["data"] and decoded[9] == bytes.fromhex(sig[2:])


def test_typed_data_is_json_safe_for_wallets():
    import json
    tx = build_payout_tx(USDC, [Transfer(VENDOR, 1), Transfer(TAX, 1)], 12, MULTISEND)
    td = json.loads(json.dumps(typed_data(SAFE, CHAIN_ID, tx)))
    assert td["primaryType"] == "SafeTx" and td["domain"] == {"chainId": CHAIN_ID, "verifyingContract": Web3.to_checksum_address(SAFE)}
    assert td["message"]["operation"] == 1 and td["message"]["nonce"] == "12"
    assert set(td["types"]) == {"EIP712Domain", "SafeTx"}
    assert encode_typed_data(full_message={**td, "message": {**td["message"],
                                                              "data": bytes.fromhex(td["message"]["data"][2:])}})
