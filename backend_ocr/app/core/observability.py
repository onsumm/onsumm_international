"""Request IDs, structured logs, and Prometheus metrics.

Before this module the only instrumentation was a middleware commented
``TEMP: request timing middleware`` that printed a line per request to stdout,
against 41 ``print()`` calls and 129 bare ``except Exception:`` blocks. When
something went wrong in production there was no way to correlate the user's
report with a log line, no way to see error rates, and — the expensive one — no
way to watch the connection pool drain.

What is here
────────────
``install(app)`` wires four things:

1. **A request id** on every request, taken from an inbound ``X-Request-ID`` if
   the proxy set one and generated otherwise. It is returned on the response, put
   on every log record emitted while handling the request, and included in error
   bodies — so a user can quote the id from a failed page and it leads straight
   to the traceback.

2. **Structured logs.** ``LOG_FORMAT=json`` emits one JSON object per line for
   ingestion; anything else keeps the human-readable format with the request id
   appended, which is what you want when tailing a file.

3. **Metrics** at ``/metrics``. The important one is ``db_pool_connections_in_use``.
   Connections are still acquired by hand at 85 sites with no ``finally``, so a
   burst of errors leaks them until the pool is empty and the box wedges without
   crashing — the failure mode is silent by construction. This gauge is what turns
   that from a mystery outage into an alert, and it is the reason to ship metrics
   before fixing the leaks rather than after.

4. **An unhandled-exception handler** that logs the traceback against the request
   id and returns a generic body. Handlers that raise ``HTTPException(500,
   str(e))`` explicitly — there are 44 — are also scrubbed, because a psycopg2
   error carrying table and column names is not something to hand a browser.
   4xx details are untouched: those are written for the user.

Cardinality
───────────
Every metric labelled with a path uses the *route template*
(``/api/bills/{invoice_id}``), never the request path. Labelling with the real
path would mint a new time series per invoice UUID and take the scrape endpoint
down with the thing it is meant to be watching.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import traceback
import uuid
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# ── Request-scoped id ────────────────────────────────────────────────────────
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str:
    """The id of the request being handled, or '-' outside a request."""
    return _request_id.get()


# ── Metrics ──────────────────────────────────────────────────────────────────
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    _PROM = True
except Exception:                                    # noqa: BLE001
    _PROM = False

if _PROM:
    REGISTRY = CollectorRegistry(auto_describe=True)

    REQUESTS = Counter(
        "http_requests_total", "HTTP requests handled.",
        ["method", "route", "status"], registry=REGISTRY,
    )
    LATENCY = Histogram(
        "http_request_duration_seconds", "Wall time per request.",
        ["method", "route"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
        registry=REGISTRY,
    )
    IN_PROGRESS = Gauge(
        "http_requests_in_progress", "Requests currently being handled.",
        registry=REGISTRY,
    )
    UNHANDLED = Counter(
        "unhandled_exceptions_total", "Exceptions that reached the ASGI boundary.",
        ["route"], registry=REGISTRY,
    )
    POOL_IN_USE = Gauge(
        "db_pool_connections_in_use",
        "Connections checked out of the psycopg2 pool. Climbing and never "
        "returning to baseline is the connection-leak signature.",
        registry=REGISTRY,
    )
    POOL_MAX = Gauge(
        "db_pool_connections_max", "Pool ceiling (maxconn).", registry=REGISTRY,
    )


def _sample_pool() -> None:
    """Read the psycopg2 pool's occupancy.

    Deliberately does NOT call config.get_pool(): that would build the pool — and
    open connections — the first time anything scraped /metrics, which is not a
    thing a monitoring endpoint should do. If the pool has not been created yet
    there is nothing to report.
    """
    if not _PROM:
        return
    try:
        from .. import config
        pool = getattr(config, "_pool", None)
        if pool is None:
            return
        # Private attributes: psycopg2 exposes no public accessor for occupancy.
        POOL_IN_USE.set(len(getattr(pool, "_used", ()) or ()))
        POOL_MAX.set(getattr(pool, "maxconn", 0) or 0)
    except Exception:                                # noqa: BLE001
        pass                                         # never let a gauge break a request


# ── Logging ──────────────────────────────────────────────────────────────────
class _RequestIdFilter(logging.Filter):
    """Puts the current request id on every record, so any logger in the app
    gains correlation without its call sites changing."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key in ("method", "path", "status", "duration_ms", "tenant"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """One root handler, with the request id available to every logger."""
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    if os.getenv("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


log = logging.getLogger("app.access")
err_log = logging.getLogger("app.error")


# ── Wiring ───────────────────────────────────────────────────────────────────
def _route_template(request: Request) -> str:
    """The matched route's template. See the cardinality note in the docstring."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "<unmatched>"


def install(app: FastAPI) -> None:
    configure_logging()
    _install_sentry()

    @app.middleware("http")
    async def _observe(request: Request, call_next: Callable) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        token = _request_id.set(rid)
        started = time.perf_counter()
        if _PROM:
            IN_PROGRESS.inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(request)
            if _PROM:
                IN_PROGRESS.dec()
                REQUESTS.labels(request.method, route, str(status)).inc()
                LATENCY.labels(request.method, route).observe(elapsed)
                _sample_pool()
            # One access line per request, with the tenant when there is one.
            tenant = None
            try:
                from ..security.auth import current_tenant
                tenant = current_tenant()
            except Exception:                        # noqa: BLE001
                pass
            log.info(
                "%s %s %s %.1fms", request.method, request.url.path, status, elapsed * 1000,
                extra={"method": request.method, "path": request.url.path,
                       "status": status, "duration_ms": round(elapsed * 1000, 1),
                       "tenant": tenant},
            )
            _request_id.reset(token)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = get_request_id()
        if _PROM:
            UNHANDLED.labels(_route_template(request)).inc()
        err_log.exception("unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error.", "request_id": rid},
            headers={REQUEST_ID_HEADER: rid},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """4xx bodies are written for the user and pass through untouched.

        5xx bodies do not: 44 handlers raise HTTPException(500, str(e)), which
        hands the client a raw psycopg2 error — table names, column names and
        fragments of the failing SQL. The real detail goes to the log against the
        request id instead, and the caller gets that id to quote.
        """
        rid = get_request_id()
        headers = dict(getattr(exc, "headers", None) or {})
        headers[REQUEST_ID_HEADER] = rid
        if exc.status_code >= 500:
            err_log.error(
                "5xx on %s %s: %s", request.method, request.url.path, exc.detail,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": "Internal server error.", "request_id": rid},
                headers=headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": rid},
            headers=headers,
        )

    _install_metrics_route(app)


def _install_metrics_route(app: FastAPI) -> None:
    """Expose /metrics, gated on a token.

    No token configured means the endpoint answers 503 rather than serving
    openly — the same stance config.py takes on PLATFORM_JWT_SECRET, and for the
    same reason: a default that silently works is how an internal surface ends up
    public. Scrapers send `Authorization: Bearer $METRICS_TOKEN`.
    """
    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request) -> Response:
        token = os.getenv("METRICS_TOKEN", "").strip()
        if not token:
            return Response("metrics disabled: METRICS_TOKEN is not set\n",
                            status_code=503, media_type="text/plain")
        supplied = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if supplied != token:
            return Response("unauthorized\n", status_code=401, media_type="text/plain")
        if not _PROM:
            return Response("prometheus_client is not installed\n",
                            status_code=503, media_type="text/plain")
        _sample_pool()
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


def _install_sentry() -> None:
    """Optional. Enabled only when SENTRY_DSN is set and the SDK is installed, so
    the dependency stays out of the default install."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger(__name__).warning(
            "SENTRY_DSN is set but sentry-sdk is not installed — skipping."
        )
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "unknown"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
    )
