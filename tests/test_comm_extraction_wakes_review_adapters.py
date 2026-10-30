"""A stored extraction has to wake the Review Hub's adapter worker itself.

`comm_extractions` is not in `pipelines/cdc.py`'s trigger list, so nothing at
the database level tells `ReviewAdapterWorker` a contract's claims have landed.
Without the explicit notify, a contract read at 17:56 produced its Review Hub
findings at 18:01 — the worker's 300s safety-poll — and anyone who opened the
hub in between saw the contract flagged nowhere.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.review.comm_extraction_worker import _wake_review_adapters


class FakeConn:
    def __init__(self, fail: bool = False):
        self.executed: list[str] = []
        self._fail = fail

    async def execute(self, sql, *args):
        if self._fail:
            raise RuntimeError("connection is gone")
        self.executed.append(sql)


@pytest.mark.asyncio
async def test_it_notifies_the_channel_the_adapter_worker_listens_on():
    conn = FakeConn()

    await _wake_review_adapters(conn)

    assert len(conn.executed) == 1
    assert "pg_notify" in conn.executed[0]
    # The channel ReviewAdapterWorker declares — a typo here is a silent
    # five-minute delay, not an error anyone would see.
    assert "ch_review_adapters" in conn.executed[0]


@pytest.mark.asyncio
async def test_a_failed_wakeup_never_fails_the_extraction():
    """The claims are already committed by the time this runs. Raising here
    would mark a perfectly good extraction FAILED to save 300s of latency."""
    conn = FakeConn(fail=True)

    await _wake_review_adapters(conn)  # must not raise
