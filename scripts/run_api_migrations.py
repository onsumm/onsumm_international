#!/usr/bin/env python3
"""Run the API's ordered schema migrations outside the web-service startup.

Run from the repository root before deploying or restarting the API:

    python3 scripts/run_api_migrations.py

This runner also invokes the Review Hub's dedicated migration scripts in their
required order after the core API migrations: `agent_runtime_migrate` first
(it creates `agent_registry`, which `comm_agent_migrate`'s seed rows insert
into), then `comm_agent_migrate`, then `review_hub_migrate` (independent of
the other two, order doesn't matter relative to them).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
root_path = str(ROOT)
# This script is itself under scripts/, so Python puts that directory first and
# can otherwise resolve an unrelated installed package also named ``scripts``.
# Put the repository root ahead of site-packages deterministically.
if root_path in sys.path:
    sys.path.remove(root_path)
sys.path.insert(0, root_path)

# DDL should never wait forever on an application-held PostgreSQL lock. This is
# a manual operation, so a short, visible failure is safer than silently
# wedging a terminal (or an API startup, if this runner is ever misused).
lock_timeout_ms = os.getenv("MIGRATION_LOCK_TIMEOUT_MS", "10000")
existing_pgoptions = os.getenv("PGOPTIONS", "").strip()
if "lock_timeout" not in existing_pgoptions:
    os.environ["PGOPTIONS"] = (
        f"{existing_pgoptions} -c lock_timeout={lock_timeout_ms}"
    ).strip()

from backend_ocr.app.migrations import MIGRATION_STEPS
# Crypto rail tables, then the wf_audit_log hash chain (docs/crypto-rail).
from backend_ocr.app.crypto_migrations import CRYPTO_MIGRATION_STEPS
from backend_ocr.app.audit_migrations import AUDIT_MIGRATION_STEPS
# Import sibling scripts directly rather than through a package named
# ``scripts``: that name is also used by a third-party package in some local
# Python environments.
import agent_runtime_migrate
import comm_agent_migrate
import review_hub_migrate


async def run() -> None:
    for step in (*MIGRATION_STEPS, *CRYPTO_MIGRATION_STEPS, *AUDIT_MIGRATION_STEPS):
        name = step.__name__
        logging.info("[migration] starting %s", name)
        result = step()
        if inspect.isawaitable(result):
            await result
        logging.info("[migration] completed %s", name)

    logging.info("[migration] starting agent_runtime_migrate")
    agent_runtime_migrate.run()
    logging.info("[migration] completed agent_runtime_migrate")

    # Must run after agent_runtime_migrate: its two seed rows
    # (INSERT INTO agent_registry ...) need that table to already exist.
    logging.info("[migration] starting comm_agent_migrate")
    comm_agent_migrate.run()
    logging.info("[migration] completed comm_agent_migrate")

    logging.info("[migration] starting review_hub_migrate")
    review_hub_migrate.run()
    logging.info("[migration] completed review_hub_migrate")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())
