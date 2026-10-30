"""PolicyEngine: each refusal code, in the documented order (06_EDGE_CASES.md §2.9)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend_ocr.app.domain.crypto.policy import PayoutContext, PolicyEngine

NOW = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
POLICY = {"crypto_enabled": True, "allowed_chains": ["base-sepolia"], "allowed_tokens": ["USDC"],
          "per_tx_limit_usd": "10000", "daily_limit_usd": "20000", "cooling_minutes": 2}
WALLET = {"status": "ACTIVE", "cooling_until": NOW - timedelta(minutes=5)}


def ok_ctx(**kw) -> PayoutContext:
    base = PayoutContext(
        tenant_id="lumen", is_resident_india=False, policy=dict(POLICY), chain="base-sepolia", token="USDC",
        wallet=dict(WALLET), screening_result="CLEAR", amount_usd=Decimal("3600"), daily_total_usd=Decimal("0"),
        gross_units=3_600_000_000, treasury_balance_units=20_000_000_000, outstanding=Decimal("4200"),
        amount=Decimal("3600"), inflight_amount=Decimal(0), inflight_rails=(), now=NOW,
    )
    return replace(base, **kw)


def codes(ctx) -> list[str]:
    return PolicyEngine().check_payout(ctx).codes


def test_happy_path_allowed():
    res = PolicyEngine().check_payout(ok_ctx())
    assert res.allowed and res.codes == []


def test_each_refusal_code():
    cases = [
        (dict(is_resident_india=True), "CRYPTO_DISABLED_RESIDENT_INDIA"),
        (dict(policy={**POLICY, "crypto_enabled": False}), "CRYPTO_NOT_ENABLED"),
        (dict(chain="ethereum"), "CHAIN_NOT_ALLOWED"),
        (dict(token="USDT"), "TOKEN_NOT_ALLOWED"),
        (dict(wallet=None), "WALLET_NOT_ACTIVE"),
        (dict(wallet={"status": "FROZEN"}), "WALLET_NOT_ACTIVE"),
        (dict(wallet={"status": "COOLING", "cooling_until": NOW + timedelta(minutes=1)}), "WALLET_COOLING"),
        (dict(wallet={"status": "COOLING", "cooling_until": NOW - timedelta(minutes=1)}), "WALLET_NOT_ACTIVE"),
        (dict(screening_result="HIT"), "SCREENING_HIT"),
        (dict(screening_result="ERROR"), "SCREENING_UNAVAILABLE"),
        (dict(screening_result=None), "SCREENING_UNAVAILABLE"),
        (dict(amount_usd=Decimal("10000.01")), "OVER_PER_TX_LIMIT"),
        (dict(daily_total_usd=Decimal("17000")), "OVER_DAILY_LIMIT"),
        (dict(treasury_balance_units=3_599_999_999), "INSUFFICIENT_TREASURY_BALANCE"),
        (dict(treasury_balance_units=None), "INSUFFICIENT_TREASURY_BALANCE"),
        (dict(inflight_amount=Decimal("1000"), inflight_rails=("IMPS",)), "ALREADY_IN_FLIGHT"),
    ]
    for kw, expected in cases:
        assert codes(ok_ctx(**kw)) == [expected], (kw, expected)


def test_first_refusal_wins():
    # Resident India outranks everything else that is also wrong.
    ctx = ok_ctx(is_resident_india=True, token="USDT", wallet=None, screening_result="HIT")
    assert codes(ctx) == ["CRYPTO_DISABLED_RESIDENT_INDIA"]
    ctx = ok_ctx(wallet={"status": "COOLING", "cooling_until": NOW + timedelta(minutes=1)}, screening_result="HIT")
    assert codes(ctx) == ["WALLET_COOLING"]


def test_part_payment_with_room_left_is_allowed():
    ctx = ok_ctx(amount=Decimal("600"), amount_usd=Decimal("600"), gross_units=600_000_000,
                 inflight_amount=Decimal("3600"), inflight_rails=("USDC",))
    assert codes(ctx) == []


def test_no_limits_configured_means_no_limit_refusals():
    ctx = ok_ctx(policy={**POLICY, "per_tx_limit_usd": None, "daily_limit_usd": None},
                 amount_usd=Decimal("1000000"), gross_units=1, treasury_balance_units=1)
    assert codes(ctx) == []


def test_check_tenant_and_dual_control():
    eng = PolicyEngine()
    assert eng.check_tenant(True, {"crypto_enabled": True}).codes == ["CRYPTO_DISABLED_RESIDENT_INDIA"]
    assert eng.check_tenant(False, {"crypto_enabled": False}).codes == ["CRYPTO_NOT_ENABLED"]
    assert eng.check_tenant(False, {"crypto_enabled": True}).allowed
    assert eng.needs_dual_control({"dual_control_above_usd": "10000"}, Decimal("10000.01"))
    assert not eng.needs_dual_control({"dual_control_above_usd": "10000"}, Decimal("10000"))
