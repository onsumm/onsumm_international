"""Unit tests for pipelines/base.py — exponential backoff logic."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from pipelines.base import BaseWorker


class _LimitedWorker(BaseWorker):
    """Concrete subclass that stops after N calls via CancelledError."""
    name           = "test_worker"
    skip_backfill  = True   # jump straight to _run_poll so all effects go through it
    poll_interval  = 1.0
    _BACKOFF_BASE  = 1.0
    _BACKOFF_CAP   = 4.0

    def __init__(self, effects):
        self._effects = list(effects)
        self._idx = 0
        self.sleep_calls: list[float] = []

    async def process_batch(self) -> int:
        if self._idx >= len(self._effects):
            raise asyncio.CancelledError()
        effect = self._effects[self._idx]
        self._idx += 1
        if isinstance(effect, Exception):
            raise effect
        return effect


async def _run_worker(effects):
    """Run worker, collecting sleep values; stops when CancelledError exits run()."""
    sleep_calls: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    worker = _LimitedWorker(effects)
    with patch("asyncio.sleep", fake_sleep):
        try:
            await worker.run()
        except asyncio.CancelledError:
            pass

    return sleep_calls


class TestExponentialBackoff:
    """C2: idle and error backoff must double on repeated calls, cap at _BACKOFF_CAP."""

    @pytest.mark.asyncio
    async def test_idle_backoff_doubles_on_empty_batches(self):
        # 3 empty batches then stop
        sleep_calls = await _run_worker([0, 0, 0])
        assert len(sleep_calls) == 3
        assert sleep_calls[0] == pytest.approx(1.0)
        assert sleep_calls[1] == pytest.approx(2.0)
        assert sleep_calls[2] == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_idle_backoff_caps_at_backoff_cap(self):
        # 5 empty batches — should never exceed 4.0
        sleep_calls = await _run_worker([0, 0, 0, 0, 0])
        assert max(sleep_calls) == pytest.approx(4.0)

    @pytest.mark.asyncio
    async def test_error_backoff_doubles_on_repeated_exceptions(self):
        sleep_calls = await _run_worker([RuntimeError("e1"), RuntimeError("e2")])
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == pytest.approx(1.0)   # _BACKOFF_BASE
        assert sleep_calls[1] == pytest.approx(2.0)   # doubled

    @pytest.mark.asyncio
    async def test_idle_backoff_resets_after_work(self):
        # empty → empty → work(5 items) → empty
        sleep_calls = await _run_worker([0, 0, 5, 0])
        # [0]=1.0, [1]=2.0, no sleep on work, [2] should reset to 1.0
        assert sleep_calls[2] == pytest.approx(1.0), (
            f"backoff should reset to poll_interval after work; got {sleep_calls}"
        )

    @pytest.mark.asyncio
    async def test_error_backoff_resets_after_success(self):
        # error → error → success(1) → empty
        sleep_calls = await _run_worker([RuntimeError("e"), RuntimeError("e"), 1, 0])
        # errors produce sleeps 1.0, 2.0; then work resets; then idle sleep is 1.0
        assert sleep_calls[-1] == pytest.approx(1.0), (
            f"idle backoff should reset to poll_interval after success; got {sleep_calls}"
        )

    @pytest.mark.asyncio
    async def test_no_sleep_when_work_found(self):
        # 3 batches with work — no sleep should happen
        sleep_calls = await _run_worker([10, 20, 5])
        assert sleep_calls == []
