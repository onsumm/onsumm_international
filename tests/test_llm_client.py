"""backend_ocr/app/ai/llm.py — the plumbing every agent call goes through.

The behaviours pinned here are the ones that cost money or lose data when they
regress: the cost arithmetic, the budget gate running *before* the call, usage
being recorded even on a rejected response, and the retry loop distinguishing
"try again" from "this will never work".
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_ocr.app.ai import llm


# ── Pricing ───────────────────────────────────────────────────────────────────

def test_cost_arithmetic_is_per_million_tokens():
    # gpt-4o is $2.50 in / $10.00 out per 1M.
    assert llm.price("gpt-4o", 1_000_000, 0) == pytest.approx(2.50)
    assert llm.price("gpt-4o", 0, 1_000_000) == pytest.approx(10.00)
    assert llm.price("gpt-4o", 8_000, 2_000) == pytest.approx(0.04)


def test_house_model_is_cheap_enough_for_full_coverage():
    """~8k in / 2k out is a realistic dossier. If this ever stops being fractions
    of a cent, the triage gate has to tighten before coverage widens."""
    assert llm.price("gpt-4o-mini", 8_000, 2_000) < 0.005


def test_unknown_model_falls_back_rather_than_charging_zero():
    assert llm.price("some-future-model", 1_000_000, 0) > 0


# ── Budget gate ───────────────────────────────────────────────────────────────

def test_kill_switch_refuses_before_any_network_call(monkeypatch):
    monkeypatch.setattr(llm, "KILL_SWITCH", True)
    allowed, reason = llm.check_budget("t")
    assert not allowed and "kill switch" in reason


def test_missing_key_refuses(monkeypatch):
    monkeypatch.setattr(llm, "KILL_SWITCH", False)
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "")
    allowed, reason = llm.check_budget("t")
    assert not allowed and "API key" in reason


def test_budget_check_fails_closed(monkeypatch):
    """The opposite of licensing.py, deliberately: not spending money we cannot
    account for is the safe direction."""
    monkeypatch.setattr(llm, "KILL_SWITCH", False)
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "get_conn", MagicMock(side_effect=RuntimeError("db down")))
    allowed, reason = llm.check_budget("t")
    assert not allowed and "could not be checked" in reason


def _conn_with(spent, cap):
    cur = MagicMock()
    cur.fetchone = MagicMock(side_effect=[(cap,), (spent,)])
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)
    return conn


def test_spending_over_the_cap_refuses(monkeypatch):
    monkeypatch.setattr(llm, "KILL_SWITCH", False)
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "get_conn", MagicMock(return_value=_conn_with(9.0, 5.0)))
    monkeypatch.setattr(llm, "put_conn", MagicMock())
    allowed, reason = llm.check_budget("t")
    assert not allowed and "daily cap" in reason


def test_spending_under_the_cap_allows(monkeypatch):
    monkeypatch.setattr(llm, "KILL_SWITCH", False)
    monkeypatch.setattr(llm, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(llm, "get_conn", MagicMock(return_value=_conn_with(1.0, 5.0)))
    monkeypatch.setattr(llm, "put_conn", MagicMock())
    allowed, _ = llm.check_budget("t")
    assert allowed


# ── The call ──────────────────────────────────────────────────────────────────

def _resp(text, in_tok=100, out_tok=50, finish="stop"):
    msg = MagicMock(content=text, refusal=None)
    return MagicMock(
        choices=[MagicMock(message=msg, finish_reason=finish)],
        usage=MagicMock(prompt_tokens=in_tok, completion_tokens=out_tok),
    )


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(llm, "check_budget", lambda _t: (True, ""))
    rec = MagicMock()
    monkeypatch.setattr(llm, "record_usage", rec)
    return rec


@pytest.mark.asyncio
async def test_refusal_is_typed_and_never_reaches_the_network(monkeypatch):
    monkeypatch.setattr(llm, "check_budget", lambda _t: (False, "daily cap reached"))
    client = MagicMock()
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    with pytest.raises(llm.LLMRefused):
        await llm.complete_json(tenant_id="t", system="s", user="u")
    client.chat.completions.create.assert_not_called()


@pytest.mark.asyncio
async def test_successful_call_returns_parsed_content_and_costs(allowed, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_resp('{"findings": []}'))
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    res = await llm.complete_json(tenant_id="t", system="s", user="u", model="gpt-4o-mini")
    assert res.content == {"findings": []}
    assert res.input_tokens == 100 and res.output_tokens == 50
    assert res.cost_usd == pytest.approx(llm.price("gpt-4o-mini", 100, 50))
    allowed.assert_called_once()


@pytest.mark.asyncio
async def test_usage_is_recorded_even_when_the_content_is_rejected(allowed, monkeypatch):
    """The tokens were spent whether or not we liked the answer. Recording only
    on success is how a cap silently stops binding."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_resp('not json at all'))
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "MAX_ATTEMPTS", 1)

    with pytest.raises(llm.LLMError):
        await llm.complete_json(tenant_id="t", system="s", user="u")
    allowed.assert_called_once()


@pytest.mark.asyncio
async def test_empty_response_is_diagnosable(allowed, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_resp("", finish="content_filter"))
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "MAX_ATTEMPTS", 1)

    with pytest.raises(llm.LLMError) as exc:
        await llm.complete_json(tenant_id="t", system="s", user="u")
    assert "content_filter" in str(exc.value)


@pytest.mark.asyncio
async def test_rate_limits_are_retried(allowed, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=[RuntimeError("rate_limit exceeded"), _resp('{"ok": true}')]
    )
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "MAX_ATTEMPTS", 3)

    res = await llm.complete_json(tenant_id="t", system="s", user="u")
    assert res.content == {"ok": True} and res.attempts == 2


@pytest.mark.asyncio
async def test_a_permanent_error_is_not_retried(allowed, monkeypatch):
    """Burning three attempts on an invalid API key wastes ten seconds per call
    across every worker."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("invalid_api_key"))
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "MAX_ATTEMPTS", 3)

    with pytest.raises(llm.LLMError):
        await llm.complete_json(tenant_id="t", system="s", user="u")
    assert client.chat.completions.create.await_count == 1


# ── Schema validation ─────────────────────────────────────────────────────────

SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "severity"],
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}


def test_valid_payload_passes():
    assert llm.validate(
        {"findings": [{"title": "x", "severity": "HIGH", "confidence": 0.8}]}, SCHEMA
    ) == []


def test_missing_required_key_is_reported_with_a_path():
    problems = llm.validate({}, SCHEMA)
    assert problems and "$.findings" in problems[0]


def test_wrong_enum_value_is_caught():
    problems = llm.validate({"findings": [{"title": "x", "severity": "SEVERE"}]}, SCHEMA)
    assert any("SEVERE" in p for p in problems)


def test_wrong_type_is_caught():
    assert llm.validate({"findings": "not a list"}, SCHEMA)


def test_booleans_are_not_numbers():
    """`isinstance(True, int)` is True in Python; a model returning `true` for a
    confidence score must not slip through as 1."""
    assert llm.validate({"findings": [{"title": "x", "severity": "HIGH",
                                       "confidence": True}]}, SCHEMA)


@pytest.mark.asyncio
async def test_a_schema_violation_fails_the_call(allowed, monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_resp(json.dumps({"findings": [{"title": "x"}]}))
    )
    monkeypatch.setattr(llm, "_get_client", lambda: client)
    monkeypatch.setattr(llm, "MAX_ATTEMPTS", 1)

    with pytest.raises(llm.LLMError) as exc:
        await llm.complete_json(tenant_id="t", system="s", user="u", schema=SCHEMA)
    assert "schema" in str(exc.value)
