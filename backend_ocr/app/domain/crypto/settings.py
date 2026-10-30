"""Crypto rail settings, read once from the environment.

Every value here is a deployment setting, not a tenant setting: which chain the
rail runs on, how to reach it, and which gas-only keys submit transactions.
Tenant choices (allowed tokens, limits, cooling period) live in
`tenant_rail_policy` instead. See docs/crypto-rail/00_DECISIONS.md.

Nothing in this module talks to the chain. `configured()` tells callers whether
the rail can run at all, so an environment without RPC or keys degrades to
"crypto rail not configured" instead of failing at import time.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class CryptoSettings:
    chain: str
    chain_id: int
    rpc_urls: tuple[str, ...]
    explorer_url: str
    usdc_address: str
    usdc_decimals: int
    multisend_call_only_address: str
    relayer_key: str
    anchor_signer_key: str
    anchor_contract: str
    confirmations: int
    watch_poll_seconds: int
    screening_list_path: str
    vendor_portal_token_ttl_hours: int
    portal_base_url: str
    fx_source: str
    # 'chain' broadcasts signed Safe transactions through the gas relayer.
    # 'demo' runs them as a dry run (eth_call) against the real Safe contract —
    # signatures, nonce and USDC balance are all checked on-chain — and records
    # the transfer without broadcasting it. For demos without testnet gas.
    settlement_mode: str = "chain"

    def demo_settlement(self) -> bool:
        return self.settlement_mode == "demo"

    def configured(self) -> bool:
        """True when payouts can run: an RPC endpoint and the USDC contract are known."""
        return bool(self.rpc_urls and self.usdc_address)


_DEFAULT_SCREENING_LIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "integrations", "screening", "sanctioned_addresses.json",
)


@lru_cache(maxsize=1)
def get_settings() -> CryptoSettings:
    rpc = [u.strip() for u in (os.getenv("RPC_URL", ""), os.getenv("RPC_URL_FALLBACK", "")) if u.strip()]
    return CryptoSettings(
        chain=os.getenv("CHAIN", "base-sepolia"),
        chain_id=_int("CHAIN_ID", 84532),
        rpc_urls=tuple(rpc),
        explorer_url=os.getenv("EXPLORER_URL", "https://sepolia.basescan.org").rstrip("/"),
        # Circle's test USDC on Base Sepolia (6 decimals). Verify against Circle's
        # docs before relying on it; override with USDC_ADDRESS.
        usdc_address=os.getenv("USDC_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e"),
        usdc_decimals=_int("USDC_DECIMALS", 6),
        # Safe v1.4.1 MultiSendCallOnly. Override if the chain's deployment differs.
        multisend_call_only_address=os.getenv(
            "MULTISEND_CALL_ONLY_ADDRESS", "0x9641d764fc13c8B624c04430C7356C1C7C8102e2"
        ),
        relayer_key=os.getenv("RELAYER_KEY", ""),
        anchor_signer_key=os.getenv("ANCHOR_SIGNER_KEY", ""),
        anchor_contract=os.getenv("ANCHOR_CONTRACT", ""),
        confirmations=_int("CONFIRMATIONS", 3),
        watch_poll_seconds=_int("WATCH_POLL_SECONDS", 4),
        screening_list_path=os.getenv("SCREENING_LIST_PATH", _DEFAULT_SCREENING_LIST),
        vendor_portal_token_ttl_hours=_int("VENDOR_PORTAL_TOKEN_TTL_HOURS", 72),
        portal_base_url=os.getenv("APP_URL", "http://127.0.0.1:5173").rstrip("/"),
        fx_source=os.getenv("FX_SOURCE", "fixed"),
        settlement_mode=os.getenv("CRYPTO_SETTLEMENT", "chain").strip().lower() or "chain",
    )
