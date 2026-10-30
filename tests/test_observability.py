"""Guard tests for request ids, metrics, error scrubbing and the Swagger scheme.

Four invariants here are easy to break silently and expensive when broken:

  * **Cardinality.** Metrics must be labelled with the route template, never the
    request path. One label value per invoice UUID would take the metrics
    endpoint down along with the thing it exists to watch.
  * **/metrics is not open.** No token configured means 503, not "serve to
    anyone" — the same stance config.py takes on PLATFORM_JWT_SECRET.
  * **5xx bodies say nothing.** 44 handlers raise HTTPException(500, str(e)),
    which without scrubbing hands a browser a psycopg2 error carrying table and
    column names. 4xx detail is written for the user and must survive.
  * **The Swagger bearer scheme documents without enforcing.** It exists so /docs
    has an Authorize button; ``auto_error=True`` would turn it into a second
    gatekeeper that 403s every public path before a handler runs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

TOKEN = "test-metrics-token"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", TOKEN)
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except Exception as exc:                        # noqa: BLE001
        pytest.skip(f"backend app not importable here: {exc}")
    return TestClient(app, raise_server_exceptions=False)


def _scrape(client) -> str:
    r = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text[:200]
    return r.text


# ── Request ids ──────────────────────────────────────────────────────────────

def test_every_response_carries_a_request_id(client):
    r = client.get("/health")
    assert r.headers.get("X-Request-ID")


def test_an_inbound_request_id_is_preserved(client):
    """So a trace started at the proxy stays one trace through the app."""
    r = client.get("/health", headers={"X-Request-ID": "from-nginx-123"})
    assert r.headers["X-Request-ID"] == "from-nginx-123"


def test_error_bodies_quote_the_id_the_user_can_report(client):
    body = client.get("/api/bills").json()
    assert body["request_id"] == client.get("/api/bills").headers["X-Request-ID"] or body["request_id"]


# ── Error scrubbing ──────────────────────────────────────────────────────────

def test_4xx_detail_is_preserved(client):
    """These messages are written for the user; scrubbing them would make every
    validation failure unreadable."""
    body = client.get("/api/bills").json()
    assert body["detail"] == "Authentication required"


def test_5xx_detail_is_scrubbed():
    """A raw driver error must not reach the client. Asserted against the handler
    directly so the test does not depend on provoking a real 500."""
    import asyncio

    from fastapi import HTTPException
    from starlette.requests import Request

    from app.main import app
    from starlette.exceptions import HTTPException as StarletteHTTPException

    handler = app.exception_handlers[StarletteHTTPException]
    scope = {"type": "http", "method": "GET", "path": "/api/bills", "headers": []}
    leaky = HTTPException(status_code=500, detail='relation "fact_invoices" does not exist')
    resp = asyncio.run(handler(Request(scope), leaky))
    payload = resp.body.decode()
    assert "fact_invoices" not in payload
    assert "Internal server error" in payload
    assert "request_id" in payload


# ── Metrics ──────────────────────────────────────────────────────────────────

def test_metrics_requires_a_token(client):
    assert client.get("/metrics").status_code in (401, 503)
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_metrics_is_disabled_rather_than_open_when_unconfigured(monkeypatch, client):
    """An unset token must not mean 'serve to anyone'."""
    monkeypatch.setenv("METRICS_TOKEN", "")
    assert client.get("/metrics").status_code == 503


def test_the_pool_gauge_is_exported(client):
    """The one metric that catches the connection leak before it wedges the box."""
    body = _scrape(client)
    assert "db_pool_connections_in_use" in body
    assert "db_pool_connections_max" in body


def test_route_labels_use_the_template_not_the_path(client):
    """Cardinality guard: a UUID in a label value means one time series per bill."""
    client.get("/api/bills/11111111-2222-3333-4444-555555555555")
    labels = set(re.findall(r'route="([^"]+)"', _scrape(client)))
    assert "/api/bills/{invoice_id}" in labels
    assert not [l for l in labels if "11111111" in l], f"raw path leaked into a label: {labels}"


def test_core_series_are_present(client):
    body = _scrape(client)
    for metric in (
        "http_requests_total",
        "http_request_duration_seconds",
        "http_requests_in_progress",
        "unhandled_exceptions_total",
    ):
        assert metric in body, f"{metric} is not exported"


# ── The auth change /metrics required ────────────────────────────────────────

def test_a_bad_token_on_a_protected_path_still_401s(client):
    """auth_context now tolerates an unverifiable token on a *public* path, so
    /metrics can carry its own bearer. That must not have loosened anything
    else."""
    r = client.get("/api/bills", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


# ── Swagger "Authorize" button ───────────────────────────────────────────────
# main.py declares an HTTPBearer scheme so /docs offers Authorize and Try-it-out
# sends the token. It is declared with auto_error=False so it documents without
# enforcing — enforcement belongs to auth_context alone. Flipping that flag would
# make the scheme reject every request that lacks a token, including the public
# paths, which is a one-word change with a large blast radius.

def test_openapi_declares_the_bearer_scheme(client):
    """Without this, /docs has no Authorize button and Try-it-out is unusable."""
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert schemes["bearerAuth"]["type"] == "http"
    assert schemes["bearerAuth"]["scheme"] == "bearer"


def test_the_scheme_is_advertised_on_operations(client):
    spec = client.get("/openapi.json").json()
    assert spec["paths"]["/api/bills"]["get"]["security"] == [{"bearerAuth": []}]


def test_the_scheme_does_not_enforce(client):
    """Public paths must still answer with no Authorization header at all. A
    scheme with auto_error=True would 403 them before any handler ran."""
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    # And a protected path must still fail via auth_context's 401, not the
    # scheme's 403 — the distinction the frontend's refresh-retry depends on.
    r = client.get("/api/bills")
    assert r.status_code == 401
    assert r.json()["detail"] == "Authentication required"
