"""
Service-local config — deliberately separate from the repo-root config.py and
from backend_ocr/app/config.py (NOVA_RECONCILIATION_LOGIC.md §1: this service
is deployed, scaled, and redeployed independently of everything else).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_root = Path(__file__).resolve().parents[3]  # services/nova_reconciliation/app/config.py -> repo root
load_dotenv(_root / ".env")


def _build_db_url() -> str:
    # NOVA_RECONCILIATION_DB_URL lets this service point at its own database;
    # falls back to the shared platform DB_URL for local/dev, where it reads
    # dim_tenants / dim_cost_centres directly (NOVA_RECONCILIATION_LOGIC.md §9.1).
    # DEV_DB_URL is this repo's .env convention for that shared dev database.
    url = os.getenv("NOVA_RECONCILIATION_DB_URL") or os.getenv("DB_URL") or os.getenv("DEV_DB_URL", "")
    if url:
        return url
    host = os.getenv("DB_HOST", "")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "postgres")
    user = os.getenv("DB_USER", "")
    pwd = os.getenv("DB_PASSWORD", "")
    ssl = os.getenv("DB_SSL", "require")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}?sslmode={ssl}"


DB_URL = _build_db_url()
PORT = int(os.getenv("NOVA_RECONCILIATION_PORT", "8100"))
CORS_ALLOW_ORIGINS = [o.strip() for o in os.getenv("NOVA_RECONCILIATION_CORS_ORIGINS", "*").split(",") if o.strip()]

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        from psycopg2 import pool as _pgpool
        _pool = _pgpool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=DB_URL, connect_timeout=10)
    return _pool


def get_conn():
    return get_pool().getconn()


def put_conn(conn, close=False):
    if conn is None:
        return
    try:
        get_pool().putconn(conn, close=close)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
