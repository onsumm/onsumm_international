"""A vendor keeps a brand name and a legal name.

The GST portal's name, when it differs, is the vendor's legal name — it no longer
replaces the name the business uses. For vendors it already replaced, Review
offers one click to put the brand name back and keep the portal's as legal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend_ocr.app.review.adapters.gst_name_overwrite import GstNameOverwriteAdapter

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "20000000-0000-0000-0000-000000000002"


def _conn(rows):
    c = AsyncMock()
    c.fetch = AsyncMock(return_value=rows)
    return c


def _row(**over):
    return {
        "supplier_id": VENDOR, "created_at": datetime.now(timezone.utc),
        "name_from": "RANA ENTERPRISES", "name_to": "RAJ KUMAR",
        "legal_name": "RAJ KUMAR", "state": "Uttar Pradesh",
        "current_name": "RAJ KUMAR", "gstn": "09ANCPK2890H1ZU", **over,
    }


@pytest.mark.asyncio
async def test_a_replaced_name_offers_to_keep_both_names():
    [f] = [d async for d in GstNameOverwriteAdapter().scan(_conn([_row()]), TENANT, 10)]
    keep = next(a for a in f.actions if a["id"] == "keep_both_names")
    assert keep["kind"] == "safe"
    assert keep["endpoint"] == f"/api/vendors/{VENDOR}/names"
    # Brand name back to what the business uses; the portal's kept as legal.
    assert keep["body"] == {"name": "RANA ENTERPRISES", "legal_name": "RAJ KUMAR"}
    assert "RAJ KUMAR as legal name" in keep["label"] and "RANA ENTERPRISES as brand name" in keep["label"]


@pytest.mark.asyncio
async def test_once_the_brand_name_is_back_the_finding_is_not_raised():
    """Choosing "keep both" restores the name, so the overwrite is no longer
    there and the finding auto-resolves on the next pass."""
    out = [d async for d in GstNameOverwriteAdapter().scan(
        _conn([_row(current_name="RANA ENTERPRISES")]), TENANT, 10)]
    assert out == []
