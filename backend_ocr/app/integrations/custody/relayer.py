"""GasRelayer: submits a fully signed Safe transaction and pays its gas.

The relayer key (RELAYER_KEY) holds only gas. It cannot move USDC: the Safe
executes `execTransaction` only when the packed owner signatures meet its
threshold, and every Safe transaction Onsumm builds sets gasPrice = 0, so the
Safe never refunds the relayer either. Losing this key costs at most its gas.

A low gas balance is refused before sending, with a clear message, rather than
surfacing as an opaque RPC error mid-payout.
"""
from __future__ import annotations

import os

from eth_account import Account


class RelayerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GasRelayer:
    def __init__(self, chain, private_key: str, min_gas_wei: int | None = None):
        if not private_key:
            raise RelayerError("RELAYER_NOT_CONFIGURED", "RELAYER_KEY is not set.")
        self.chain = chain
        self._key = private_key
        self.address = Account.from_key(private_key).address
        self.min_gas_wei = min_gas_wei if min_gas_wei is not None else int(
            os.getenv("RELAYER_MIN_GAS_WEI", str(20_000_000_000_000)))  # 0.00002 ETH

    def gas_balance(self) -> int:
        return self.chain.native_balance(self.address)

    def submit(self, safe_address: str, exec_calldata: str) -> str:
        balance = self.gas_balance()
        if balance < self.min_gas_wei:
            raise RelayerError(
                "RELAYER_OUT_OF_GAS",
                f"Relayer {self.address} has {balance} wei of gas; needs at least {self.min_gas_wei}. Top it up.",
            )
        try:
            return self.chain.send_tx(self._key, safe_address, exec_calldata)
        except Exception as e:
            raise RelayerError("RELAY_FAILED", f"Safe rejected or RPC failed: {e}")
