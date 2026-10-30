"""Connection helpers over the psycopg2 pool.

Moved verbatim out of app/main.py; see docs/BACKEND_STRUCTURE.md.
"""
from __future__ import annotations

import psycopg2
from ..config import DB_URL, TENANT_ID, OPENAI_API_KEY, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_ADMIN_EMAIL, APPROVAL_SECRET, JWT_SECRET, APP_URL, ACCESS_TOKEN_TTL_MIN, REFRESH_TOKEN_TTL_DAYS, get_conn, put_conn

def _safe_connect():
    """Return a connection from the pool, or None if DB is unavailable.
    Startup handlers should return early when this returns None so the server
    can still start even during a DB outage / quota pause."""
    try:
        return psycopg2.connect(DB_URL, connect_timeout=5)
    except Exception as exc:
        import logging
        logging.warning("[startup] DB unavailable — skipping migration (%s)", exc)
        return None

class _PoolConn:
    """Context manager that borrows a connection from the pool and returns it on exit.
    Use instead of psycopg2.connect() so connections are reused across requests.

    Usage:
        with _PoolConn() as conn:
            with conn.cursor() as cur:
                ...
    """
    def __enter__(self):
        self._conn = get_conn()
        self._conn.autocommit = False
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try: self._conn.rollback()
            except Exception: pass
            put_conn(self._conn, close=True)
        else:
            put_conn(self._conn)
        return False
