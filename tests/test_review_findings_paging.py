"""GET /api/review/findings — the flat queue's page, sort and total.

The queue used to collapse findings by kind, so it was never more than a
handful of rows and keyset pagination was enough. One row per finding turns
636 findings into 26 pages, and a cursor cannot answer "page 4 of 26" or
"showing 76-100 of 636" — both of which a flat queue is unusable without.

The two things worth pinning here are the ones that are silently wrong rather
than loudly broken: an ORDER BY built from user input, and a keyset cursor
honoured under a sort it was not issued for.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend_ocr.app.routers import review_hub as rh

TENANT = "10000000-0000-0000-0000-000000000001"


class FakeCursor:
    def __init__(self, rows, total=0):
        self._rows = rows
        self._total = total
        self.calls: list[tuple] = []
        self._last = ""

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last = sql

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return {"n": self._total}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.autocommit = True

    def cursor(self, cursor_factory=None):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass


def _run(rows=(), total=0, **kwargs):
    cur = FakeCursor(list(rows), total)
    conn = FakeConn(cur)
    params = dict(
        tenant=TENANT, lane=None, scope="actionable", family=None, severity=None,
        finding_type=None, group=False, subject_type=None, subject_id=None,
        status="open", min_money_inr=None, q=None, cursor=None, limit=25,
        offset=0, sort="rank", direction="desc",
    )
    params.update(kwargs)
    with patch.object(rh, "get_conn", return_value=conn), \
         patch.object(rh, "put_conn", MagicMock()), \
         patch.object(rh, "capabilities_for", return_value=["edit"]), \
         patch.object(rh, "visibility_sql", return_value=("TRUE", {})), \
         patch.object(rh, "_entitlement_clause", return_value=("TRUE", {})), \
         patch.object(rh, "_serialise", side_effect=lambda r, **kw: dict(r)):
        out = rh.list_findings(**params)
    return out, cur


def _list_sql(cur):
    return next(sql for sql, _ in cur.calls
                if "FROM rh_findings f" in sql and "COUNT(DISTINCT" not in sql)


def _count_sql(cur):
    # The flat queue counts rollups (one per card), not raw findings.
    return next((sql for sql, _ in cur.calls if "COUNT(DISTINCT" in sql), None)


def test_a_page_carries_the_offset_and_the_total_it_is_a_page_of():
    out, cur = _run(rows=[{"finding_id": "a", "rank_score": 1}], total=636, offset=75)

    sql, params = next((s, p) for s, p in cur.calls if "OFFSET" in s)
    assert params["offset"] == 75
    assert out["total"] == 636
    assert out["offset"] == 75
    assert _count_sql(cur) is not None, "without a count the pager cannot say '76-100 of 636'"


def test_the_grouped_path_reports_no_total_rather_than_a_wrong_one():
    """Grouping is a page of kinds; there is no page 4 to be on."""
    out, cur = _run(
        rows=[{"finding_id": "a", "finding_type": "vendor_no_bank", "grp_rank": 1, "grp_count": 3}],
        group=True,
    )

    assert out["total"] is None
    assert out["offset"] is None
    assert _count_sql(cur) is None   # and no count query was paid for


# The flat queue sorts rolled-up rows (a vendor's failed payments, every FX
# item, are one card), so each key orders by the rolled row's own column: the
# card's total, top rank and worst priority.
@pytest.mark.parametrize(
    "key,fragment",
    [
        ("money", "roll_money"),
        ("age", "first_detected_at"),
        ("type", "family"),
        ("vendor", "vendor_label"),
        ("status", "status"),
    ],
)
def test_each_sortable_column_orders_by_its_own_column(key, fragment):
    _out, cur = _run(sort=key)
    sql = _list_sql(cur)
    assert f"ORDER BY {fragment} DESC" in sql


def test_priority_sorts_by_an_explicit_scale_not_the_enum_order():
    """severity_level is a Postgres enum created outside this repo, so its
    declaration order is not something this code can see — and DESC has to mean
    "worst first" here exactly as it does on money."""
    _out, cur = _run(sort="priority")
    sql = _list_sql(cur)
    assert "CASE f.severity::text" in sql
    assert "'CRITICAL' THEN 3" in sql      # top of the scale, so DESC puts P0 first
    # A card sorts by its worst member's priority, on that same scale.
    assert "max(sev_rank)" in sql and "ORDER BY roll_sev" in sql


def test_the_sort_whitelist_is_closed_and_nothing_else_reaches_the_order_by():
    """The sort key lands in an ORDER BY, which no amount of parameterisation
    makes safe. FastAPI's pattern is the first lock and this dict is the
    second: a key that is not a key of it cannot produce SQL at all."""
    assert set(rh.SORT_COLUMNS) == {
        "rank", "type", "vendor", "money", "priority", "status", "age",
    }
    assert set(rh.ROLLED_SORT_COLUMNS) == set(rh.SORT_COLUMNS)
    for bad in ("money; DROP TABLE rh_findings", "rank_score", "", "1=1"):
        assert bad not in rh.SORT_COLUMNS

    # And the endpoint's own pattern rejects them before the lookup is
    # reached. Read off the signature rather than restated, so the two locks
    # cannot drift apart without this failing.
    import inspect
    import re

    query = inspect.signature(rh.list_findings).parameters["sort"].default
    pattern = next(m.pattern for m in query.metadata if getattr(m, "pattern", None))
    for key in rh.SORT_COLUMNS:
        assert re.fullmatch(pattern, key), f"{key} is sortable but the endpoint would reject it"
    for bad in ("money; DROP TABLE rh_findings", "rank_score", "", "1=1"):
        assert re.fullmatch(pattern, bad) is None


def test_a_direction_flips_the_order_and_keeps_nulls_last_either_way():
    _out, asc = _run(sort="money", direction="asc")
    _out, desc = _run(sort="money", direction="desc")
    assert "ORDER BY roll_money ASC NULLS LAST" in _list_sql(asc)
    assert "ORDER BY roll_money DESC NULLS LAST" in _list_sql(desc)


def test_every_sort_breaks_ties_on_a_unique_column():
    """Without a total order, OFFSET pagination can show one finding twice and
    skip another, which is the worst kind of wrong: it looks fine."""
    for key in rh.SORT_COLUMNS:
        _out, cur = _run(sort=key)
        assert "roll_rank DESC, finding_id DESC" in _list_sql(cur), key


def test_a_rank_cursor_is_ignored_under_a_sort_it_was_not_issued_for():
    """A keyset cursor means "everything ranked below this point". Under a
    money sort that sentence is meaningless, and honouring it drops rows
    silently."""
    token = rh._encode_cursor(0.5, "50b4161a-db45-4e7a-b602-e2ee652cddc9")

    _out, ranked = _run(cursor=token, sort="rank")
    assert "cur_rank" in _list_sql(ranked)

    _out, by_money = _run(cursor=token, sort="money")
    assert "cur_rank" not in _list_sql(by_money)


def test_a_paged_response_hands_back_no_cursor_to_be_misread_as_a_keyset():
    rows = [{"finding_id": f"f{i}", "rank_score": 1} for i in range(26)]

    out, _cur = _run(rows=rows, total=636, offset=25)
    assert out["next_cursor"] is None

    # Browse still pages this endpoint by cursor, and must keep getting one.
    out, _cur = _run(rows=rows, total=636, offset=0, sort="rank")
    assert out["next_cursor"] is not None


def test_a_rolled_up_row_carries_its_card_count_and_total():
    """A vendor's failed payments are one card. The row says how many lines it
    stands for and their total, so the queue and pager count cards, not lines."""
    rows = [
        {"finding_id": "a", "rank_score": 1, "roll_rank": 1, "rollup_key": "pay:v1",
         "roll_count": 6, "roll_money": 26432},
        {"finding_id": "b", "rank_score": 1, "roll_rank": 1, "rollup_key": "b"},
    ]
    out, cur = _run(rows=rows, total=2)
    pay, plain = out["items"]
    assert pay["rollup"] == {"key": "pay:v1", "count": 6, "money_at_risk_inr": 26432}
    assert "rollup" not in plain          # a finding that rolls up to itself is just a row
    assert "WHERE rn = 1" in _list_sql(cur)
    assert "COUNT(DISTINCT" in _count_sql(cur)


def test_only_failed_payments_and_fx_roll_up():
    sql = rh.ROLLUP_KEY_SQL
    assert "'disbursement_failed' THEN 'pay:'" in sql      # by vendor
    assert "f.family = 'fx' THEN 'fx'" in sql               # every FX item together
    assert "ELSE f.finding_id::text" in sql                 # everything else alone
