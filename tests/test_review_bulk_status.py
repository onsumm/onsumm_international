"""POST /api/review/findings/bulk — the queue's own bulk bar.

The bug this exists for: psycopg2 renders a Python list of str as ARRAY['…'],
which types as text[], and Postgres has no `uuid = ANY(text[])` operator. So
every by-id bulk call 500'd with "operator does not exist: uuid = text" —
snooze, dismiss, all of it — while the by-type path (the cluster drill-in,
which compares a text column) worked fine. The parameter is cast to uuid[]
rather than the column to text, so the primary-key index still applies.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend_ocr.app.routers import review_hub as rh

TENANT = "10000000-0000-0000-0000-000000000001"
F1 = "50b4161a-db45-4e7a-b602-e2ee652cddc9"
F2 = "ef21c8cc-c845-4845-b52b-128207563ee5"


class FakeCursor:
    def __init__(self, select_rows):
        self._select_rows = select_rows
        self.calls: list[tuple] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self._select_rows

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.autocommit = True
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def _run(body, select_rows):
    cur = FakeCursor(select_rows)
    conn = FakeConn(cur)
    with patch.object(rh, "get_conn", return_value=conn), \
         patch.object(rh, "put_conn", MagicMock()), \
         patch.object(rh, "require_capability", MagicMock()), \
         patch.object(rh, "current_username", return_value="vibhanshu"), \
         patch.object(rh, "visibility_sql", return_value=("TRUE", {})), \
         patch.object(rh, "_write_event", MagicMock()):
        out = rh.bulk_status(body, TENANT)
    return out, cur


def _select_sql(cur):
    return next(sql for sql, _ in cur.calls if "SELECT f.finding_id" in sql)


def test_the_id_list_is_cast_so_postgres_can_compare_it_to_a_uuid_column():
    body = rh.BulkRequest(finding_ids=[F1], status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])

    sql, params = next((s, p) for s, p in cur.calls if "SELECT f.finding_id" in s)
    assert "ANY(%(ids)s::uuid[])" in sql, "a bare ANY() here is uuid = text[], which has no operator"
    # Cast the parameter, not the column: f.finding_id::text would work but
    # would also throw away the primary-key index.
    assert "f.finding_id::text" not in sql
    assert params["ids"] == [F1]
    assert out["counts"] == {"updated": 1, "failed": 0}


def test_snooze_sets_a_future_snoozed_until_and_never_resolves():
    body = rh.BulkRequest(finding_ids=[F1], status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])

    update_sql, params = next((s, p) for s, p in cur.calls if s.strip().startswith("UPDATE rh_findings"))
    status, reason, actor, snooze_until, terminal, tenant, fid = params
    assert status == "SNOOZED"
    assert snooze_until is not None          # a snooze with no until is just an ack
    assert terminal is False                 # and it must not set resolved_at
    assert fid == F1 and tenant == TENANT
    assert out["updated"] == [F1]


def test_a_malformed_id_is_reported_against_itself_rather_than_failing_the_batch():
    """Casting to uuid[] would 500 the whole call on one bad id; the endpoint's
    own contract is that partial failure is visible per member."""
    body = rh.BulkRequest(finding_ids=[F1, "not-a-uuid"], status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])

    assert out["updated"] == [F1]
    assert {f["finding_id"] for f in out["failed"]} == {"not-a-uuid"}
    # The good id still went to the database, unquoted by the bad one.
    _, params = next((s, p) for s, p in cur.calls if "SELECT f.finding_id" in s)
    assert params["ids"] == [F1]


def test_all_ids_malformed_never_reaches_the_database():
    body = rh.BulkRequest(finding_ids=["nope", ""], status="SNOOZED")

    out, cur = _run(body, [])

    assert out["counts"] == {"updated": 0, "failed": 2}
    assert not cur.calls  # no query built from an empty/invalid list


def test_an_id_that_is_gone_or_invisible_is_reported_not_silently_dropped():
    body = rh.BulkRequest(finding_ids=[F1, F2], status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])  # F2 resolved, or not visible

    assert out["updated"] == [F1]
    assert [f["finding_id"] for f in out["failed"]] == [F2]


def test_ids_are_canonicalised_so_case_does_not_look_like_a_missing_finding():
    """A caller sending an upper-case uuid must not have it reported as
    missing just because Postgres returns the canonical lower-case form."""
    body = rh.BulkRequest(finding_ids=[F1.upper()], status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])

    assert out["updated"] == [F1]
    assert out["failed"] == []


def test_a_dismissal_still_demands_one_of_the_fixed_reasons():
    from fastapi import HTTPException
    body = rh.BulkRequest(finding_ids=[F1], status="DISMISSED", reason="because")
    with pytest.raises(HTTPException) as exc:
        _run(body, [{"finding_id": F1}])
    assert exc.value.status_code == 400


def test_by_type_still_needs_no_cast_because_finding_type_is_text():
    body = rh.BulkRequest(finding_type="payment_term_conflict", status="SNOOZED", snooze_days=7)

    out, cur = _run(body, [{"finding_id": F1}])

    sql = _select_sql(cur)
    assert "f.finding_type = %(ft)s" in sql
    assert out["updated"] == [F1]


# ── Un-snooze, and the two bugs it surfaced ───────────────────────────────────

F3 = "3ef8c6ab-3ec3-422d-a54d-5a963ff34edb"


def test_open_unsnoozes_without_resolving():
    """OPEN is the way back out of the Snoozed tab.

    The UPDATE writes snoozed_until for every status, so OPEN clearing it is
    not incidental — it is the whole mechanism.
    """
    body = rh.BulkRequest(finding_ids=[F1], status="OPEN")

    out, cur = _run(body, [{"finding_id": F1}])

    _, params = next((s, p) for s, p in cur.calls if s.strip().startswith("UPDATE rh_findings"))
    status, _reason, _actor, snooze_until, terminal, _tenant, _fid = params
    assert status == "OPEN"
    assert snooze_until is None    # back in the queue, not asleep until Friday
    assert terminal is False       # and emphatically not resolved
    assert out["updated"] == [F1]


def test_every_status_the_api_accepts_is_a_legal_event_type():
    """The bug: _write_event records the status verbatim as the event type, and
    the rh_finding_events CHECK did not list OPEN. Taking the un-snooze wrote an
    event row that violated the constraint, so the action 500'd in a way no
    amount of reading the endpoint would explain.

    Asserted against the DDL rather than a hand-kept list, so widening one
    pattern without widening the other cannot pass again.
    """
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "review_hub_migrate",
        Path(__file__).resolve().parent.parent / "scripts" / "review_hub_migrate.py",
    )
    migrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate)
    events_ddl = " ".join(sql for label, sql in migrate.DDL if "rh_finding_events" in sql)

    for model in (rh.StatusRequest, rh.BulkRequest):
        pattern = next(m.pattern for m in model.model_fields["status"].metadata
                       if getattr(m, "pattern", None))
        for status in pattern.strip("^$()").split("|"):
            assert f"'{status}'" in events_ddl, (
                f"{model.__name__} accepts {status}, but rh_finding_events' "
                f"event_type CHECK would reject the row _write_event writes for it"
            )


class FailingCursor(FakeCursor):
    """Postgres after a failed statement: everything until the transaction is
    unwound comes back "current transaction is aborted"."""

    def __init__(self, select_rows, fail_on: str):
        super().__init__(select_rows)
        self._fail_on = fail_on
        self._aborted = False

    def execute(self, sql, params=None):
        super().execute(sql, params)
        stripped = sql.strip()
        if stripped.startswith("ROLLBACK TO SAVEPOINT"):
            self._aborted = False
            return
        if self._aborted:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until "
                "end of transaction block"
            )
        if stripped.startswith("UPDATE rh_findings") and params and self._fail_on in params:
            self._aborted = True
            raise RuntimeError('violates check constraint "some_check"')


def test_one_bad_member_costs_only_itself():
    """Before savepoints this endpoint reported 1 real error and 15 copies of
    "current transaction is aborted" — the per-member reporting it promises was
    fiction the moment any member raised, because the whole request runs in one
    transaction.
    """
    body = rh.BulkRequest(finding_ids=[F3, F1, F2], status="OPEN")

    cur = FailingCursor(
        [{"finding_id": F3}, {"finding_id": F1}, {"finding_id": F2}], fail_on=F3,
    )
    conn = FakeConn(cur)
    with patch.object(rh, "get_conn", return_value=conn), \
         patch.object(rh, "put_conn", MagicMock()), \
         patch.object(rh, "require_capability", MagicMock()), \
         patch.object(rh, "current_username", return_value="vibhanshu"), \
         patch.object(rh, "visibility_sql", return_value=("TRUE", {})), \
         patch.object(rh, "_write_event", MagicMock()):
        out = rh.bulk_status(body, TENANT)

    assert out["updated"] == [F1, F2]
    assert [f["finding_id"] for f in out["failed"]] == [F3]
    # The one failure names what actually went wrong, not the aftermath.
    assert "check constraint" in out["failed"][0]["error"]
    assert "transaction is aborted" not in out["failed"][0]["error"]

    savepoints = [sql for sql, _ in cur.calls if "SAVEPOINT" in sql]
    assert savepoints.count("ROLLBACK TO SAVEPOINT bulk_member") == 1
    assert savepoints.count("RELEASE SAVEPOINT bulk_member") == 2


def test_a_clean_batch_releases_every_savepoint():
    """Savepoints that are never released accumulate for the life of the
    transaction; a 500-member batch would hold 500 of them."""
    body = rh.BulkRequest(finding_ids=[F1, F2], status="OPEN")

    out, cur = _run(body, [{"finding_id": F1}, {"finding_id": F2}])

    assert out["counts"] == {"updated": 2, "failed": 0}
    assert [sql for sql, _ in cur.calls].count("RELEASE SAVEPOINT bulk_member") == 2
