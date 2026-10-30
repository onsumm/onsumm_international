"""SafeCustody: USDC payouts from the client's own Safe multisig.

What Onsumm does, and what it cannot do:

  1. Build the Safe transaction (04_LLD.md §6a): a single USDC `transfer`, or —
     when tax is withheld — one `MultiSend` of two transfers (net to the vendor,
     tax to the tenant's TAX wallet), so both legs settle or neither does.
  2. Hash it with EIP-712 exactly as the Safe contract does, and hand the typed
     data to each owner's browser wallet (eth_signTypedData_v4).
  3. Recover each signer, keep only signatures from current owners (read live
     from the Safe, not from our copy), and pack them in ascending owner order.
  4. Submit `execTransaction` through the gas-only relayer.

The Safe checks every signature itself. A forged or missing signature makes
`execTransaction` revert, so the relayer key cannot move funds on its own.

Safety rules enforced here, not just documented:
  - operation must be CALL (0), except DELEGATECALL (1) to MultiSendCallOnly —
    the call-only variant cannot make further delegate calls.
  - gasPrice = 0 and refundReceiver = 0: the Safe never refunds gas, so a
    relayer cannot drain it through gas refunds.

Domain: Safe >= 1.3.0 hashes {chainId, verifyingContract}. Older Safes are not
supported.
"""
from __future__ import annotations

from typing import Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import _hash_eip191_message, encode_typed_data
from web3 import Web3

from .base import SafeInfo, Transfer

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
TRANSFER_SELECTOR = Web3.keccak(text="transfer(address,uint256)")[:4]
MULTISEND_SELECTOR = Web3.keccak(text="multiSend(bytes)")[:4]
_GET_OWNERS = "0x" + Web3.keccak(text="getOwners()")[:4].hex()
_GET_THRESHOLD = "0x" + Web3.keccak(text="getThreshold()")[:4].hex()
_NONCE = "0x" + Web3.keccak(text="nonce()")[:4].hex()

SAFE_TX_TYPES = [
    {"name": "to", "type": "address"},
    {"name": "value", "type": "uint256"},
    {"name": "data", "type": "bytes"},
    {"name": "operation", "type": "uint8"},
    {"name": "safeTxGas", "type": "uint256"},
    {"name": "baseGas", "type": "uint256"},
    {"name": "gasPrice", "type": "uint256"},
    {"name": "gasToken", "type": "address"},
    {"name": "refundReceiver", "type": "address"},
    {"name": "nonce", "type": "uint256"},
]
_DOMAIN_TYPES = [
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

_EXEC_ABI = [{
    "name": "execTransaction", "type": "function", "stateMutability": "payable",
    "inputs": [
        {"name": "to", "type": "address"}, {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"}, {"name": "operation", "type": "uint8"},
        {"name": "safeTxGas", "type": "uint256"}, {"name": "baseGas", "type": "uint256"},
        {"name": "gasPrice", "type": "uint256"}, {"name": "gasToken", "type": "address"},
        {"name": "refundReceiver", "type": "address"}, {"name": "signatures", "type": "bytes"},
    ],
    "outputs": [{"name": "success", "type": "bool"}],
}]


class SafeTxRefused(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _b(data) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    s = str(data or "0x")
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


# ── Transaction building (pure) ───────────────────────────────────────────────

def usdc_transfer_calldata(to: str, amount_units: int) -> bytes:
    if amount_units <= 0:
        raise SafeTxRefused("INVALID_AMOUNT", "Transfer amount must be positive.")
    return TRANSFER_SELECTOR + abi_encode(["address", "uint256"], [Web3.to_checksum_address(to), int(amount_units)])


def build_safe_tx(token: str, to: str, amount_units: int, nonce: int) -> dict:
    return {
        "to": Web3.to_checksum_address(token), "value": 0,
        "data": usdc_transfer_calldata(to, amount_units),
        "operation": 0,
        "safeTxGas": 0, "baseGas": 0, "gasPrice": 0,
        "gasToken": ZERO_ADDRESS, "refundReceiver": ZERO_ADDRESS,
        "nonce": int(nonce),
    }


def pack_multisend(token: str, transfers: list[Transfer]) -> bytes:
    packed = b""
    token_bytes = _b(Web3.to_checksum_address(token))
    for t in transfers:
        data = usdc_transfer_calldata(t.to, t.amount_units)
        packed += (
            (0).to_bytes(1, "big")           # inner operation: CALL
            + token_bytes                    # to: the USDC contract, 20 bytes
            + (0).to_bytes(32, "big")        # value
            + len(data).to_bytes(32, "big")  # data length
            + data
        )
    return packed


def build_multisend(token: str, transfers: list[Transfer], nonce: int, multisend_address: str) -> dict:
    tx = build_safe_tx(token, transfers[0].to, transfers[0].amount_units, nonce)
    tx.update({
        "to": Web3.to_checksum_address(multisend_address),
        "data": MULTISEND_SELECTOR + abi_encode(["bytes"], [pack_multisend(token, transfers)]),
        "operation": 1,
    })
    return tx


def build_payout_tx(token: str, transfers: list[Transfer], nonce: int, multisend_address: str) -> dict:
    """One transfer -> plain CALL; two (vendor + tax) -> MultiSend."""
    transfers = [t for t in transfers if t.amount_units > 0]
    if not transfers:
        raise SafeTxRefused("INVALID_AMOUNT", "Nothing to transfer.")
    if len(transfers) == 1:
        return build_safe_tx(token, transfers[0].to, transfers[0].amount_units, nonce)
    return build_multisend(token, transfers, nonce, multisend_address)


def assert_safe_operation(tx: dict, multisend_address: str) -> None:
    op = int(tx["operation"])
    if op == 0:
        pass
    elif op == 1:
        if tx["to"].lower() != multisend_address.lower():
            raise SafeTxRefused("DELEGATECALL_REFUSED", "DELEGATECALL is allowed only to MultiSendCallOnly.")
    else:
        raise SafeTxRefused("BAD_OPERATION", f"Unknown Safe operation {op}.")
    if int(tx["gasPrice"]) != 0 or tx["refundReceiver"] != ZERO_ADDRESS or tx["gasToken"] != ZERO_ADDRESS:
        raise SafeTxRefused("REFUND_REFUSED", "Safe transactions must not refund gas.")


# ── EIP-712 ───────────────────────────────────────────────────────────────────

def typed_data(safe: str, chain_id: int, tx: dict) -> dict:
    """The exact payload for eth_signTypedData_v4 (JSON-safe: bytes as 0x hex, big ints as strings)."""
    message = {
        "to": Web3.to_checksum_address(tx["to"]),
        "value": str(int(tx["value"])),
        "data": "0x" + _b(tx["data"]).hex(),
        "operation": int(tx["operation"]),
        "safeTxGas": str(int(tx["safeTxGas"])),
        "baseGas": str(int(tx["baseGas"])),
        "gasPrice": str(int(tx["gasPrice"])),
        "gasToken": Web3.to_checksum_address(tx["gasToken"]),
        "refundReceiver": Web3.to_checksum_address(tx["refundReceiver"]),
        "nonce": str(int(tx["nonce"])),
    }
    return {
        "types": {"EIP712Domain": _DOMAIN_TYPES, "SafeTx": SAFE_TX_TYPES},
        "primaryType": "SafeTx",
        "domain": {"chainId": int(chain_id), "verifyingContract": Web3.to_checksum_address(safe)},
        "message": message,
    }


def _signable(safe: str, chain_id: int, tx: dict):
    td = typed_data(safe, chain_id, tx)
    msg = dict(td["message"])
    for k in ("value", "safeTxGas", "baseGas", "gasPrice", "nonce"):
        msg[k] = int(msg[k])
    msg["data"] = _b(msg["data"])
    return encode_typed_data(full_message={**td, "message": msg})


def safe_tx_hash(safe: str, chain_id: int, tx: dict) -> bytes:
    return bytes(_hash_eip191_message(_signable(safe, chain_id, tx)))


def normalise_signature(signature: str) -> bytes:
    sig = _b(signature)
    if len(sig) != 65:
        raise SafeTxRefused("BAD_SIGNATURE", "Signature must be 65 bytes.")
    v = sig[64]
    if v in (0, 1):            # some wallets return 0/1; Safe's ECDSA path needs 27/28
        sig = sig[:64] + bytes([v + 27])
    elif v not in (27, 28):
        raise SafeTxRefused("BAD_SIGNATURE", "Only plain ECDSA owner signatures (v = 27/28) are accepted.")
    return sig


def recover_signer(safe: str, chain_id: int, tx: dict, signature: str) -> str:
    sig = normalise_signature(signature)
    return Account.recover_message(_signable(safe, chain_id, tx), signature=sig)


def pack_signatures(signatures: list[tuple[str, str]]) -> bytes:
    """Concatenate 65-byte signatures sorted by owner address, ascending, as the Safe requires."""
    ordered = sorted(signatures, key=lambda s: int(s[0], 16))
    return b"".join(normalise_signature(sig) for _, sig in ordered)


def exec_transaction_calldata(tx: dict, packed_signatures: bytes) -> str:
    c = Web3().eth.contract(abi=_EXEC_ABI)
    return c.encode_abi("execTransaction", args=[
        Web3.to_checksum_address(tx["to"]), int(tx["value"]), _b(tx["data"]), int(tx["operation"]),
        int(tx["safeTxGas"]), int(tx["baseGas"]), int(tx["gasPrice"]),
        Web3.to_checksum_address(tx["gasToken"]), Web3.to_checksum_address(tx["refundReceiver"]),
        packed_signatures,
    ])


# ── On-chain reads and execution ──────────────────────────────────────────────

class SafeCustody:
    """CustodyProvider for a client's Safe. Reads go through EvmChain (RPC failover)."""

    def __init__(self, chain, multisend_address: str, relayer=None):
        self.chain = chain
        self.multisend_address = Web3.to_checksum_address(multisend_address)
        self.relayer = relayer

    def info(self, wallet_address: str) -> SafeInfo:
        safe = Web3.to_checksum_address(wallet_address)
        try:
            owners = abi_decode(["address[]"], self.chain.eth_call(safe, _GET_OWNERS))[0]
            threshold = abi_decode(["uint256"], self.chain.eth_call(safe, _GET_THRESHOLD))[0]
            nonce = abi_decode(["uint256"], self.chain.eth_call(safe, _NONCE))[0]
        except Exception as e:
            raise SafeTxRefused("NOT_A_SAFE", f"{safe} did not answer as a Safe ({e}).")
        return SafeInfo(address=safe, owners=tuple(Web3.to_checksum_address(o) for o in owners),
                        threshold=int(threshold), nonce=int(nonce))

    def balance(self, wallet_address: str) -> int:
        return self.chain.usdc_balance(wallet_address)

    def build_payout(self, wallet_address: str, transfers: list[Transfer], nonce: int) -> dict:
        tx = build_payout_tx(self.chain.usdc_address, transfers, nonce, self.multisend_address)
        assert_safe_operation(tx, self.multisend_address)
        return tx

    def tx_hash(self, wallet_address: str, tx: dict) -> bytes:
        return safe_tx_hash(wallet_address, self.chain.chain_id, tx)

    def recover_signer(self, wallet_address: str, tx: dict, signature: str) -> str:
        return recover_signer(wallet_address, self.chain.chain_id, tx, signature)

    def simulate(self, wallet_address: str, tx: dict, signatures: list[tuple[str, str]],
                 from_address: Optional[str] = None) -> None:
        """Dry-run execTransaction against the real Safe with eth_call.

        The Safe contract itself checks the owner signatures against its current
        nonce and threshold, and runs the USDC transfer(s) — so a pass here means
        the transaction would succeed if broadcast. Nothing is sent and no gas is
        spent. Raises SafeTxRefused with the Safe's revert reason otherwise.
        """
        assert_safe_operation(tx, self.multisend_address)
        data = exec_transaction_calldata(tx, pack_signatures(signatures))
        caller = from_address or (self.relayer.address if self.relayer is not None else None)
        try:
            out = self.chain.eth_call(wallet_address, data, from_address=caller)
        except Exception as e:  # web3 raises ContractLogicError("execution reverted: GS026") and friends
            raise SafeTxRefused("SIMULATION_REVERTED", f"The Safe rejected the transaction: {e}")
        if not out or int.from_bytes(out[-32:], "big") != 1:
            raise SafeTxRefused("SIMULATION_REVERTED", "The Safe returned failure for the transaction.")

    def execute(self, wallet_address: str, tx: dict, signatures: list[tuple[str, str]]) -> str:
        if self.relayer is None:
            raise SafeTxRefused("RELAYER_NOT_CONFIGURED", "No relayer configured.")
        assert_safe_operation(tx, self.multisend_address)
        data = exec_transaction_calldata(tx, pack_signatures(signatures))
        return self.relayer.submit(wallet_address, data)


def get_safe_custody(chain=None) -> SafeCustody:
    from ...domain.crypto.settings import get_settings
    from ..chains.evm import get_chain
    from .relayer import GasRelayer

    chain = chain or get_chain()
    settings = get_settings()
    relayer: Optional[GasRelayer] = GasRelayer(chain, settings.relayer_key) if settings.relayer_key else None
    return SafeCustody(chain, settings.multisend_call_only_address, relayer)
