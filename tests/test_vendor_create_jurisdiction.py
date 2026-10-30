"""POST /api/vendors — GSTIN is required only where the GST pack applies.

An Indian tenant's vendor is identified by its GSTIN, so the endpoint still
refuses one without it. An international tenant has no GSTIN to give: the
vendor is created without one and placed by country instead. No database:
get_conn is monkeypatched with a fake cursor.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend_ocr.app.routers import vendors as vr

TENANT = "10000000-0000-0000-0000-000000000001"
INDIA = {"jurisdiction_profile": "india", "country_code": "IN"}
INTL = {"jurisdiction_profile": "international", "country_code": "SG"}


def _fake_conn(tenant_row):
    cur = MagicMock()
    cur.fetchone = MagicMock(side_effect=[tenant_row, ("33333333-0000-0000-0000-000000000001",)])
    cur.description = None
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cur)
    ctx.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = ctx
    return conn, cur


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(vr, "require_tenant", lambda: TENANT)
    monkeypatch.setattr(vr, "put_conn", lambda c: None)

    def _run(tenant_row, **body):
        conn, cur = _fake_conn(tenant_row)
        monkeypatch.setattr(vr, "get_conn", lambda: conn)
        res = vr.create_vendor(vr.VendorCreateRequest(**body))
        insert = cur.execute.call_args_list[-1].args
        return res, insert, conn
    return _run


def test_india_still_requires_gstin(run):
    with pytest.raises(HTTPException) as e:
        run(INDIA, name="Acme Pvt Ltd")
    assert e.value.status_code == 422 and e.value.detail == "GSTN is required"


def test_india_vendor_is_placed_in_india(run):
    res, (sql, params), _ = run(INDIA, name="Acme Pvt Ltd", gstn="29aabct1332l1zu")
    assert res["status"] == "created"
    assert params[2] == "29AABCT1332L1ZU" and params[5] == "29AABCT1332L1ZU"
    assert params[-2:] == ("IN", "IN")


def test_international_vendor_needs_no_gstin(run):
    res, (sql, params), conn = run(INTL, name="Acme Inc", country_code="us")
    assert res["status"] == "created"
    assert params[2] is None                       # no GSTIN stored
    assert params[5].startswith("MANUAL_acme_inc_")  # still a unique source id
    assert params[-2:] == ("US", "US")
    conn.commit.assert_called_once()


def test_bad_country_is_a_422_not_a_500(run):
    with pytest.raises(HTTPException) as e:
        run(INTL, name="Acme Inc", country_code="USA")
    assert e.value.status_code == 422
