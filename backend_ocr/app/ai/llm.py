"""One LLM client: retries, timeouts, schema validation, token and cost accounting.

Every existing call site in this repo is a bare `create()` with no retry, no
timeout and no accounting — a 429 or a transient 500 is silent data loss (see
domain/fact_graph.py, which catches everything and returns None). This module is
the replacement.

Three deliberate properties:

* **Cost is counted before it is spent.** `check_budget` runs ahead of the call,
  so a runaway loop stops costing money rather than being discovered on an
  invoice. Usage is recorded even when the caller then rejects the content.
* **A refusal is not an exception.** Budget exhaustion, a kill switch, or a
  missing key return a typed `LLMRefused`, because "we chose not to call" and
  "the call failed" want different handling and different logs.
* **The schema is a real schema.** Callers pass a JSON Schema and get validated
  output or an error — not prose in the prompt and a hand-rolled `.get()` chain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..config import OPENAI_API_KEY, get_conn, put_conn

logger = logging.getLogger(__name__)

# USD per 1M tokens. Pinned here rather than fetched, so a price change is a
# reviewed diff and historical cost_usd stays reproducible.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":       (2.50, 10.00),
    "gpt-4o-mini":  (0.15,  0.60),
    "text-embedding-3-small": (0.02, 0.0),
}
DEFAULT_PRICING = (0.15, 0.60)

REQUEST_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "60"))
MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "3"))
# Global off switch. Flipping this to "1" stops every agent call in the process
# without a deploy.
KILL_SWITCH = os.getenv("LLM_KILL_SWITCH", "").strip() in ("1", "true", "yes")
DEFAULT_DAILY_CAP_USD = float(os.getenv("AGENT_DAILY_CAP_USD", "5"))


class LLMError(RuntimeError):
    """The call was attempted and failed."""


class LLMRefused(RuntimeError):
    """The call was deliberately not attempted (budget, kill switch, no key)."""


@dataclass
class LLMResult:
    content: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 1
    raw_text: str = ""
    steps: list[dict] = field(default_factory=list)


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICING.get(model, DEFAULT_PRICING)
    return (input_tokens / 1_000_000) * pin + (output_tokens / 1_000_000) * pout


# ── Budget ────────────────────────────────────────────────────────────────────

def _tenant_cap(cur, tenant_id: str) -> float:
    try:
        cur.execute(
            "SELECT agent_daily_cap_usd FROM dim_tenants WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return DEFAULT_DAILY_CAP_USD


def check_budget(tenant_id: str) -> tuple[bool, str]:
    """(allowed, reason). Fails CLOSED — the opposite of licensing.

    licensing.py fails open on a DB error because locking a paying tenant out of
    their own bills is worse than a brief over-grant. Here the asymmetry runs the
    other way: not spending money we cannot account for is the safe direction.
    """
    if KILL_SWITCH:
        return False, "kill switch is on"
    if not OPENAI_API_KEY:
        return False, "no API key configured"

    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cap = _tenant_cap(cur, tenant_id)
            cur.execute(
                "SELECT spent_usd FROM agent_budgets WHERE tenant_id = %s AND day = %s",
                (tenant_id, date.today()),
            )
            row = cur.fetchone()
            spent = float(row[0]) if row else 0.0
        put_conn(conn)
        if spent >= cap:
            return False, f"daily cap reached (${spent:.2f} of ${cap:.2f})"
        return True, ""
    except Exception as exc:
        if conn:
            put_conn(conn, close=True)
        logger.warning("[llm] budget check failed, refusing: %s", exc)
        return False, "budget could not be checked"


def record_usage(tenant_id: str, cost_usd: float) -> None:
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_budgets (tenant_id, day, spent_usd, calls)
                VALUES (%s, %s, %s, 1)
                ON CONFLICT (tenant_id, day) DO UPDATE
                    SET spent_usd = agent_budgets.spent_usd + EXCLUDED.spent_usd,
                        calls     = agent_budgets.calls + 1
                """,
                (tenant_id, date.today(), cost_usd),
            )
        conn.commit()
        put_conn(conn)
    except Exception as exc:
        if conn:
            put_conn(conn, close=True)
        # Never fail the caller over accounting, but say so loudly: silent
        # accounting loss is how the cap stops meaning anything.
        logger.error("[llm] could not record $%.6f for tenant %s: %s", cost_usd, tenant_id, exc)


# ── Client ────────────────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI
        # Async, with retries disabled at the SDK layer: we own the retry loop so
        # that every attempt is counted and logged. Note the sync client used in
        # domain/fact_graph.py must never be imported into the pipeline event
        # loop — one blocking call there stalls all thirteen workers.
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=REQUEST_TIMEOUT_S, max_retries=0)
    return _client


_RETRYABLE = ("rate_limit", "timeout", "connection", "overloaded", "503", "502", "500", "429")


def _is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(tag in text for tag in _RETRYABLE)


async def complete_json(
    *,
    tenant_id: str,
    system: str,
    user: str,
    model: str = "gpt-4o-mini",
    schema: dict | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
    purpose: str = "unspecified",
) -> LLMResult:
    """One JSON completion, accounted for and validated.

    Raises LLMRefused when we chose not to call, LLMError when the call failed.
    """
    allowed, reason = check_budget(tenant_id)
    if not allowed:
        raise LLMRefused(reason)

    client = _get_client()
    started = time.monotonic()
    steps: list[dict] = []
    last: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", 0) or 0
            out_tok = getattr(usage, "completion_tokens", 0) or 0
            cost = price(model, in_tok, out_tok)

            # Recorded before the content is inspected: the tokens were spent
            # whether or not we end up liking the answer.
            record_usage(tenant_id, cost)

            choice = resp.choices[0]
            text = choice.message.content or ""
            if not text:
                raise LLMError(
                    f"empty response (finish_reason={choice.finish_reason}, "
                    f"refusal={getattr(choice.message, 'refusal', None)})"
                )

            try:
                content = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError(f"model did not return JSON: {exc}") from exc

            if schema:
                problems = validate(content, schema)
                if problems:
                    raise LLMError(f"response failed its schema: {'; '.join(problems[:3])}")

            steps.append({"attempt": attempt, "ok": True, "tokens": in_tok + out_tok})
            return LLMResult(
                content=content, model=model,
                input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
                latency_ms=int((time.monotonic() - started) * 1000),
                attempts=attempt, raw_text=text, steps=steps,
            )

        except LLMRefused:
            raise
        except Exception as exc:
            last = exc
            steps.append({"attempt": attempt, "ok": False, "error": str(exc)[:200]})
            if attempt >= MAX_ATTEMPTS or not _is_retryable(exc):
                break
            # Exponential backoff with jitter, so a rate limit hit by several
            # workers at once does not have them all retry in lockstep.
            delay = min(2 ** (attempt - 1), 8) * (0.5 + random.random())
            logger.warning("[llm] %s attempt %d failed (%s) — retrying in %.1fs",
                           purpose, attempt, exc, delay)
            await asyncio.sleep(delay)

    raise LLMError(f"{purpose}: {last}") from last


# ── Minimal JSON Schema validation ────────────────────────────────────────────
# Enough of the spec for the shapes we actually ask for, and no dependency. The
# point is to reject a malformed response before it reaches business logic, not
# to be a conformant validator.

def validate(value: Any, schema: dict, path: str = "$") -> list[str]:
    problems: list[str] = []
    expected = schema.get("type")

    if expected == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{path}.{key}: required")
        for key, sub in (schema.get("properties") or {}).items():
            if key in value:
                problems += validate(value[key], sub, f"{path}.{key}")

    elif expected == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value[:50]):
                problems += validate(item, item_schema, f"{path}[{i}]")

    elif expected == "string":
        if not isinstance(value, str):
            problems.append(f"{path}: expected string")
        elif schema.get("enum") and value not in schema["enum"]:
            problems.append(f"{path}: {value!r} not one of {schema['enum']}")

    elif expected == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{path}: expected number")

    elif expected == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            problems.append(f"{path}: expected integer")

    elif expected == "boolean":
        if not isinstance(value, bool):
            problems.append(f"{path}: expected boolean")

    return problems
