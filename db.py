"""
Asyncpg connection pool — single shared pool for all pipeline workers.
"""
from __future__ import annotations

import asyncpg
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import config

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool

    ssl_ctx: ssl.SSLContext | None = None
    if config.DB_SSL in ("require", "verify-full", "verify-ca"):
        ssl_ctx = ssl.create_default_context()
        if config.DB_SSL in ("verify-full", "verify-ca"):
            cert = Path(config.DB_SSL_CERT)
            if not cert.is_absolute():
                cert = Path(__file__).resolve().parent / cert
            if cert.exists():
                ssl_ctx.load_verify_locations(cafile=str(cert))
            ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        else:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    _pool = await asyncpg.create_pool(
        dsn=config.DATABASE_URL,
        ssl=ssl_ctx,
        min_size=2,
        max_size=10,
        command_timeout=60,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        return await init_pool()
    return _pool


@asynccontextmanager
async def transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
