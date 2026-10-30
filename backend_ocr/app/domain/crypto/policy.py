"""PolicyEngine: every check a USDC payout must pass before anything is signed.

The checks run in the order of docs/crypto-rail/06_EDGE_CASES.md §2.9 and stop
at the first refusal, so the person sees the most fundamental problem first
(there is no point telling an India-resident tenant that the vendor's wallet is
still cooling):

    resident India -> crypto enabled -> chain -> token -> wallet ACTIVE ->
    cooling ended -> screened CLEAR in the last 24 h -> per-tx limit ->
    daily limit -> treasury balance -> nothing else in flight for the bill

The engine itself is pure: callers gather a `PayoutContext` (rows, a fresh
screening result, the live Safe balance) and the engine only decides. That
keeps every refusal code unit-testable without a database or a chain.

This module also carries the few helpers every crypto module needs and that
have no better home: the refusal HTTP shape from 08_API_CONTRACT.md, the
wf_audit_log writer, and a Review Hub finding hook that tolerates the findings
module not being present.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

SCREENING_MAX_AGE = timedelta(hours=24)

# Refusal codes (00_DECISIONS.md §2.3).
CRYPTO_DISABLED_RESIDENT_INDIA = "CRYPTO_DISABLED_RESIDENT_INDIA"
CRYPTO_NOT_ENABLED = "CRYPTO_NOT_ENABLED"
CHAIN_NOT_ALLOWED = "CHAIN_NOT_ALLOWED"
TOKEN_NOT_ALLOWED = "TOKEN_NOT_ALLOWED"
WALLET_NOT_ACTIVE = "WALLET_NOT_ACTIVE"
WALLET_COOLING = "WALLET_COOLING"
SCREENING_HIT = "SCREENING_HIT"
SCREENING_UNAVAILABLE = "SCREENING_UNAVAILABLE"
OVER_PER_TX_LIMIT = "OVER_PER_TX_LIMIT"
OVER_DAILY_LIMIT = "OVER_DAILY_LIMIT"
INSUFFICIENT_TREASURY_BALANCE = "INSUFFICIENT_TREASURY_BALANCE"
ALREADY_IN_FLIGHT = "ALREADY_IN_FLIGHT"
DUPLICATE_ACROSS_RAILS = "DUPLICATE_ACROSS_RAILS"

DEFAULT_POLICY = {
    "crypto_enabled": False,
    "allowed_chains": ["base-sepolia"],
    "allowed_tokens": ["USDC"],
    "per_tx_limit_usd": None,
    "daily_limit_usd": None,
    "dual_control_above_usd": Decimal("10000"),
    "cooling_minutes": 1440,
    "require_test_transfer": True,
}


@dataclass
class PolicyResult:
    allowed: bool = True
    codes: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def refuse(self, code: str, message: str) -> "PolicyResult":
        self.allowed = False
        self.codes.append(code)
        self.messages.append(message)
        return self

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "codes": list(self.codes), "messages": list(self.messages)}


@dataclass
class PayoutContext:
    """Everything the engine decides on. Gathered by the payout service."""
    tenant_id: str
    is_resident_india: bool
    policy: dict
    chain: str
    token: str
    wallet: Optional[dict]              # supplier_wallets row, or None
    screening_result: Optional[str]     # CLEAR | HIT | ERROR, fresh within 24 h; None = not screened
    amount_usd: Decimal
    daily_total_usd: Decimal            # today's non-failed USDC payouts, excluding this one
    gross_units: int
    treasury_balance_units: Optional[int]
    outstanding: Decimal                # bill outstanding, in bill currency
    amount: Decimal                     # this payout, in bill currency
    inflight_amount: Decimal            # other non-terminal payouts for the bill, any rail
    inflight_rails: tuple = ()
    now: Optional[datetime] = None


def _dec(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _fmt_usd(v: Decimal) -> str:
    return f"USD {v:,.2f}"


class PolicyEngine:
    def check_tenant(self, is_resident_india: bool, policy: dict) -> PolicyResult:
        """The two tenant-level gates; also used to hide the USDC option in the UI."""
        result = PolicyResult()
        if is_resident_india:
            return result.refuse(
                CRYPTO_DISABLED_RESIDENT_INDIA,
                "Crypto payouts are not available to India-resident companies (FEMA). "
                "Use the bank rail.",
            )
        if not policy.get("crypto_enabled"):
            return result.refuse(CRYPTO_NOT_ENABLED, "The crypto rail is not enabled in this company's rail policy.")
        return result

    def check_payout(self, ctx: PayoutContext) -> PolicyResult:
        now = ctx.now or datetime.now(timezone.utc)
        policy = {**DEFAULT_POLICY, **(ctx.policy or {})}
        result = self.check_tenant(ctx.is_resident_india, policy)
        if not result.allowed:
            return result

        if ctx.chain not in (policy.get("allowed_chains") or []):
            return result.refuse(CHAIN_NOT_ALLOWED, f"Chain {ctx.chain} is not allowed by the rail policy.")
        if ctx.token.upper() not in [t.upper() for t in (policy.get("allowed_tokens") or [])]:
            return result.refuse(TOKEN_NOT_ALLOWED, f"Token {ctx.token} is not allowed by the rail policy.")

        wallet = ctx.wallet
        if not wallet or wallet.get("status") not in ("ACTIVE", "COOLING"):
            state = (wallet or {}).get("status") or "missing"
            return result.refuse(WALLET_NOT_ACTIVE, f"The vendor has no active payout wallet (status: {state}).")
        cooling_until = wallet.get("cooling_until")
        if wallet.get("status") == "COOLING" or (cooling_until and cooling_until > now):
            when = cooling_until.strftime("%H:%M UTC") if cooling_until else "admin approval"
            if wallet.get("status") == "COOLING" and (not cooling_until or cooling_until <= now):
                return result.refuse(WALLET_NOT_ACTIVE, "The vendor wallet has finished cooling and is waiting for admin approval.")
            return result.refuse(WALLET_COOLING, f"Vendor wallet is cooling until {when}.")

        if ctx.screening_result == "ERROR" or ctx.screening_result is None:
            return result.refuse(SCREENING_UNAVAILABLE, "Wallet screening is unavailable. Try again shortly.")
        if ctx.screening_result == "HIT":
            return result.refuse(SCREENING_HIT, "The vendor wallet failed sanctions screening and is frozen.")

        per_tx = _dec(policy.get("per_tx_limit_usd"))
        if per_tx is not None and ctx.amount_usd > per_tx:
            return result.refuse(OVER_PER_TX_LIMIT, f"{_fmt_usd(ctx.amount_usd)} is above the per-payment limit of {_fmt_usd(per_tx)}.")
        daily = _dec(policy.get("daily_limit_usd"))
        if daily is not None and ctx.daily_total_usd + ctx.amount_usd > daily:
            left = max(Decimal(0), daily - ctx.daily_total_usd)
            return result.refuse(OVER_DAILY_LIMIT, f"Daily limit {_fmt_usd(daily)} would be exceeded; {_fmt_usd(left)} left today.")

        if ctx.treasury_balance_units is None or ctx.treasury_balance_units < ctx.gross_units:
            have = Decimal(ctx.treasury_balance_units or 0) / Decimal(10**6)
            need = Decimal(ctx.gross_units) / Decimal(10**6)
            return result.refuse(
                INSUFFICIENT_TREASURY_BALANCE,
                f"Treasury holds {have:,.2f} USDC; this payout needs {need:,.2f} USDC.",
            )

        if ctx.inflight_amount and ctx.inflight_amount + ctx.amount > ctx.outstanding + Decimal("0.01"):
            rails = ", ".join(sorted(set(ctx.inflight_rails))) or "another rail"
            return result.refuse(
                ALREADY_IN_FLIGHT,
                f"This bill already has {ctx.inflight_amount:,.2f} in flight ({rails}); "
                f"adding {ctx.amount:,.2f} would exceed the outstanding {ctx.outstanding:,.2f}.",
            )
        return result

    def needs_dual_control(self, policy: dict, amount_usd: Decimal) -> bool:
        limit = _dec((policy or {}).get("dual_control_above_usd"))
        return limit is not None and amount_usd > limit


# ── Shared helpers for the crypto modules ─────────────────────────────────────

def refusal(result: PolicyResult, status_code: int = 403) -> HTTPException:
    """The refusal shape from 08_API_CONTRACT.md, inside FastAPI's `detail`."""
    return HTTPException(status_code=status_code, detail={"error": "POLICY_REFUSED", **{
        "codes": result.codes, "messages": result.messages}})


def error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": code, "codes": [code], "messages": [message]})


def chain_not_configured() -> HTTPException:
    return error(503, "CHAIN_NOT_CONFIGURED",
                 "The crypto rail has no chain configured in this environment (set RPC_URL).")


def load_policy(cur, tenant_id: str) -> dict:
    cur.execute("SELECT * FROM tenant_rail_policy WHERE tenant_id=%s", (tenant_id,))
    row = cur.fetchone()
    policy = dict(DEFAULT_POLICY)
    if row:
        policy.update({k: row[k] for k in DEFAULT_POLICY if k in row})
    return policy


def audit(cur, tenant_id: str, table: str, entity_id: str, old: Optional[dict], new: dict,
          actor: Optional[str], action: Optional[str] = None) -> None:
    """One wf_audit_log row, same shape as routers/disbursement.py `_audit`."""
    cur.execute(
        """INSERT INTO wf_audit_log (tenant_id, table_name, entity_id, action, old_values_jsonb, new_values_jsonb)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            tenant_id, table, entity_id, action or ("UPDATE" if old else "INSERT"),
            json.dumps(old, default=str) if old else None,
            json.dumps({**new, "actor": actor}, default=str),
        ),
    )


def file_finding(cur, tenant_id: str, code: str, severity: str, entity_type: str, entity_id: str,
                 title: str, details: dict) -> None:
    """Raise a Review Hub finding if the findings module is installed.

    Never fails the caller: a finding is a notification about the payment, and
    losing one must not stop or roll back the payment decision itself. A
    savepoint keeps a failed insert from poisoning the caller's transaction.
    """
    try:
        from .findings import raise_finding
    except ImportError:
        logger.info("[crypto] finding %s for %s %s (findings module not installed)", code, entity_type, entity_id)
        return
    try:
        cur.execute("SAVEPOINT crypto_finding")
        raise_finding(cur, tenant_id, code, severity, entity_type, entity_id, title, details)
        cur.execute("RELEASE SAVEPOINT crypto_finding")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT crypto_finding")
        logger.exception("[crypto] could not file finding %s", code)


def jsonable(row: dict) -> dict:
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, Decimal):
            out[k] = str(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, memoryview):
            out[k] = "0x" + bytes(v).hex()
        else:
            out[k] = v
    return out
