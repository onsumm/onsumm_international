"""Unit tests for the ownership primitives in backend_ocr/app/tenancy.py.

These are the behaviours that are easy to regress in a refactor and expensive to
get wrong: the tenant predicate must reach the SQL, and a row owned by someone
else must be indistinguishable from a row that does not exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

from fastapi import HTTPException                       # noqa: E402
from app.security import tenancy                                 # noqa: E402


class FakeCursor:
    """Records the SQL and params it was given; returns rows from a script."""

    def __init__(self, rows: list | None = None):
        self._rows = list(rows or [])
        self.sql: str = ""
        self.params: tuple = ()

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        self.params = tuple(params or ())

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"


def test_tenant_predicate_reaches_the_sql():
    cur = FakeCursor(rows=[(1,)])
    tenancy.assert_owned(cur, "fact_invoices", "invoice_id", "bill-1", tenant=TENANT_A)
    assert "WHERE invoice_id = %s AND tenant_id = %s" in cur.sql
    assert cur.params == ("bill-1", TENANT_A)


def test_returns_the_authenticated_tenant_for_the_caller_to_reuse():
    cur = FakeCursor(rows=[(1,)])
    assert tenancy.assert_owned(cur, "fact_invoices", "invoice_id", "b", tenant=TENANT_A) == TENANT_A


def test_foreign_row_and_missing_row_are_indistinguishable():
    """Both must be 404. A 403 on the foreign case would confirm the record is
    real and turn the endpoint into an existence oracle for other tenants' ids."""
    # A row owned by tenant B: scoped to tenant A the query returns nothing.
    foreign = FakeCursor(rows=[])
    with pytest.raises(HTTPException) as foreign_exc:
        tenancy.assert_owned(foreign, "fact_invoices", "invoice_id", "b", tenant=TENANT_A)

    missing = FakeCursor(rows=[])
    with pytest.raises(HTTPException) as missing_exc:
        tenancy.assert_owned(missing, "fact_invoices", "invoice_id", "nope", tenant=TENANT_A)

    assert foreign_exc.value.status_code == missing_exc.value.status_code == 404
    assert foreign_exc.value.detail == missing_exc.value.detail


def test_label_names_the_thing_the_user_asked_for():
    cur = FakeCursor(rows=[])
    with pytest.raises(HTTPException) as exc:
        tenancy.owned_bill(cur, "b", tenant=TENANT_A)
    assert exc.value.detail == "Bill not found"


def test_attachment_is_scoped_by_tenant_and_by_parent_bill():
    """An attachment id alone is not enough — it must also belong to the invoice
    in the URL, or one bill's attachment could be read through another's path."""
    cur = FakeCursor(rows=[(1,)])
    tenancy.owned_attachment(cur, "inv-1", "att-1", tenant=TENANT_A)
    assert cur.params == ("att-1", "inv-1", TENANT_A)
    assert "tenant_id = %s" in cur.sql and "invoice_id = %s" in cur.sql


@pytest.mark.parametrize("bad", ["fact_invoices; DROP TABLE users", "fact invoices", "1_table", ""])
def test_identifiers_are_restricted_to_bare_names(bad):
    """Table/column names are interpolated, so they are gated on an identifier
    pattern — a guard against a future caller passing one through from a request."""
    cur = FakeCursor(rows=[(1,)])
    with pytest.raises(ValueError):
        tenancy.assert_owned(cur, bad, "invoice_id", "b", tenant=TENANT_A)
