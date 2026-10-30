"""EVM chain client for the crypto rail: reads, USDC logs, and gas-only sends.

One `EvmChain` per configured chain. It wraps web3 with:

  - RPC failover: every call tries RPC_URL, then RPC_URL_FALLBACK. A slow or
    dead public endpoint must not stall payouts or the chain watcher.
  - USDC `Transfer` log reads for a set of watched addresses, returned as plain
    `TransferLog` records with the block hash, so the watcher can detect reorgs.
  - `send_tx`, used only by the gas relayer and the audit anchor. Both keys hold
    gas only; neither can move USDC (a Safe executes only with owner signatures).

Nothing here knows about tenants, bills or Safes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, TypeVar

from eth_account import Account
from web3 import Web3
from web3.exceptions import TransactionNotFound

from ...domain.crypto.settings import CryptoSettings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]


class ChainNotConfigured(RuntimeError):
    """The crypto rail has no RPC endpoint or token configured in this environment."""


@dataclass(frozen=True)
class TransferLog:
    tx_hash: str
    log_index: int
    block_number: int
    block_hash: str
    token_contract: str
    from_address: str
    to_address: str
    amount_units: int
    removed: bool = False


def _topic_address(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").rjust(64, "0")


def _address_from_topic(topic) -> str:
    raw = topic.hex() if hasattr(topic, "hex") else str(topic)
    raw = raw[2:] if raw.startswith("0x") else raw
    return Web3.to_checksum_address("0x" + raw[-40:])


def _hex(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if hasattr(value, "hex"):
        h = value.hex()
        return h if h.startswith("0x") else "0x" + h
    return str(value)


class EvmChain:
    def __init__(self, settings: Optional[CryptoSettings] = None):
        self.settings = settings or get_settings()
        if not self.settings.configured():
            raise ChainNotConfigured("Set RPC_URL and USDC_ADDRESS to enable the crypto rail.")
        self._clients = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 15})) for u in self.settings.rpc_urls]
        self.chain_id = self.settings.chain_id
        self.usdc_address = Web3.to_checksum_address(self.settings.usdc_address)
        self.confirmations = self.settings.confirmations

    # ── failover ────────────────────────────────────────────────────────────
    def _call(self, fn: Callable[[Web3], T]) -> T:
        last: Optional[Exception] = None
        for i, w3 in enumerate(self._clients):
            try:
                return fn(w3)
            except TransactionNotFound:
                raise
            except Exception as e:  # network error, rate limit, bad gateway
                last = e
                logger.warning("[chain] RPC %d failed: %s", i, e)
        raise last or RuntimeError("no RPC endpoint configured")

    @property
    def w3(self) -> Web3:
        return self._clients[0]

    # ── reads ───────────────────────────────────────────────────────────────
    def latest_block(self) -> int:
        return self._call(lambda w3: w3.eth.block_number)

    def block_hash(self, number: int) -> str:
        return self._call(lambda w3: _hex(w3.eth.get_block(number)["hash"]))

    def block_time(self, number: int) -> int:
        return self._call(lambda w3: int(w3.eth.get_block(number)["timestamp"]))

    def get_receipt(self, tx_hash: str) -> Optional[dict]:
        try:
            return dict(self._call(lambda w3: w3.eth.get_transaction_receipt(tx_hash)))
        except TransactionNotFound:
            return None

    def get_transaction(self, tx_hash: str) -> Optional[dict]:
        try:
            return dict(self._call(lambda w3: w3.eth.get_transaction(tx_hash)))
        except TransactionNotFound:
            return None

    def confirmations_of(self, block_number: Optional[int]) -> int:
        if block_number is None:
            return 0
        return max(0, self.latest_block() - int(block_number) + 1)

    def is_valid_address(self, address: str) -> bool:
        return Web3.is_address(address or "")

    def is_contract(self, address: str) -> bool:
        code = self._call(lambda w3: w3.eth.get_code(Web3.to_checksum_address(address)))
        return len(code) > 0

    def native_balance(self, address: str) -> int:
        return self._call(lambda w3: w3.eth.get_balance(Web3.to_checksum_address(address)))

    def usdc_balance(self, address: str) -> int:
        def _bal(w3: Web3) -> int:
            c = w3.eth.contract(address=self.usdc_address, abi=_ERC20_ABI)
            return int(c.functions.balanceOf(Web3.to_checksum_address(address)).call())
        return self._call(_bal)

    def eth_call(self, to: str, data: str, from_address: Optional[str] = None) -> bytes:
        call = {"to": Web3.to_checksum_address(to), "data": data}
        if from_address:
            call["from"] = Web3.to_checksum_address(from_address)
        return self._call(lambda w3: bytes(w3.eth.call(call)))

    def get_transfer_logs(self, from_block: int, to_block: int, addresses: Iterable[str]) -> list[TransferLog]:
        """USDC transfers in [from_block, to_block] where `from` or `to` is watched."""
        topics = [_topic_address(a) for a in addresses if a]
        if not topics:
            return []
        out: dict[tuple[str, int], TransferLog] = {}
        for position in (1, 2):  # topic1 = from, topic2 = to
            topic_filter: list = [TRANSFER_TOPIC, None, None]
            topic_filter[position] = topics
            params = {"fromBlock": from_block, "toBlock": to_block, "address": self.usdc_address, "topics": topic_filter}
            for lg in self._call(lambda w3: w3.eth.get_logs(params)):
                rec = TransferLog(
                    tx_hash=_hex(lg["transactionHash"]),
                    log_index=int(lg["logIndex"]),
                    block_number=int(lg["blockNumber"]),
                    block_hash=_hex(lg["blockHash"]),
                    token_contract=Web3.to_checksum_address(lg["address"]),
                    from_address=_address_from_topic(lg["topics"][1]),
                    to_address=_address_from_topic(lg["topics"][2]),
                    amount_units=int(_hex(lg["data"]), 16),
                    removed=bool(lg.get("removed", False)),
                )
                out[(rec.tx_hash, rec.log_index)] = rec
        return sorted(out.values(), key=lambda r: (r.block_number, r.log_index))

    # ── writes (gas-only keys) ──────────────────────────────────────────────
    def send_tx(self, private_key: str, to: str, data: str = "0x", value: int = 0, gas: Optional[int] = None) -> str:
        acct = Account.from_key(private_key)

        def _send(w3: Web3) -> str:
            tx = {
                "chainId": self.chain_id,
                "from": acct.address,
                "to": Web3.to_checksum_address(to),
                "value": value,
                "data": data,
                "nonce": w3.eth.get_transaction_count(acct.address, "pending"),
            }
            latest = w3.eth.get_block("latest")
            base_fee = int(latest.get("baseFeePerGas") or w3.eth.gas_price)
            priority = int(w3.eth.max_priority_fee or 0)
            tx["maxPriorityFeePerGas"] = priority
            tx["maxFeePerGas"] = base_fee * 2 + priority
            tx["gas"] = gas or int(w3.eth.estimate_gas(tx) * 1.25)
            signed = acct.sign_transaction(tx)
            raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
            return _hex(w3.eth.send_raw_transaction(raw))

        return self._call(_send)

    # ── links ───────────────────────────────────────────────────────────────
    def explorer_tx(self, tx_hash: str) -> str:
        return f"{self.settings.explorer_url}/tx/{tx_hash}"

    def explorer_address(self, address: str) -> str:
        return f"{self.settings.explorer_url}/address/{address}"


_chain: Optional[EvmChain] = None


def get_chain() -> EvmChain:
    """Shared client. Raises ChainNotConfigured when RPC_URL / USDC_ADDRESS are unset."""
    global _chain
    if _chain is None:
        _chain = EvmChain()
    return _chain


def explorer_tx_url(tx_hash: str) -> str:
    """Explorer link without needing a live RPC connection."""
    return f"{get_settings().explorer_url}/tx/{tx_hash}"
