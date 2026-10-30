"""
Real Nova API client — confirmed contract (2026-08-23, live call against
api.lendix.ai with a real tenant key):

    GET {base_url}/billing/monthly?vendor=<name>&month=<YYYY-MM>
    X-API-Key: <tenant's own key>          (NOT Authorization: Bearer — that
                                             returns a real 401 Invalid API key)

Response body matches models.NovaApiResponse:
    {success, data: {vendor, month, modules: [...], totals}, timestamp, referenceId}
"""
from __future__ import annotations

import httpx

TIMEOUT_SECONDS = 20.0


class NovaApiError(Exception):
    """Wraps a non-2xx or malformed response from the live Nova API. Callers
    (orchestrator.py) catch this and mark the bill ERROR rather than crash the
    whole sweep — one tenant's bad response shouldn't block everyone else's."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


def fetch_billing_monthly(base_url: str, api_key: str, vendor: str, month: str) -> dict:
    url = f"{base_url.rstrip('/')}/billing/monthly"
    try:
        resp = httpx.get(
            url,
            params={"vendor": vendor, "month": month},
            headers={"X-API-Key": api_key},
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        raise NovaApiError(f"Nova API request failed: {e}") from e

    if resp.status_code != 200:
        raise NovaApiError(f"Nova API returned {resp.status_code}: {resp.text[:300]}", resp.status_code)

    try:
        body = resp.json()
    except ValueError as e:
        raise NovaApiError(f"Nova API returned non-JSON body: {e}") from e

    if not body.get("success"):
        raise NovaApiError(f"Nova API responded success=false: {body}")

    return body
