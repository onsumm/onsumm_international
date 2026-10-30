"""
sync_audit_logger.py - structured JSONL sync audit logging
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

_LOG_DIR = "logs"
_LOG_FILE = "sync_audit.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any):
    return str(value)


def write_sync_event(
    *,
    direction: str,
    entity: str,
    action: str,
    tenant_id: str,
    connection_id: str | None = None,
    status: str = "SUCCESS",
    details: dict | None = None,
):
    """
    Write one JSON line audit event for sync operations.

    direction: PULL | PUSH
    entity: LEDGER | INVOICE | BILL | PAYMENT | VOUCHER_BATCH ...
    action: FETCH | UPSERT | IMPORT | SUMMARY ...
    status: SUCCESS | ERROR | SKIPPED
    """
    os.makedirs(_LOG_DIR, exist_ok=True)
    payload = {
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": _now_iso(),
        "direction": direction,
        "entity": entity,
        "action": action,
        "status": status,
        "tenant_id": tenant_id,
        "connection_id": connection_id,
        "details": details or {},
    }
    path = os.path.join(_LOG_DIR, _LOG_FILE)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=_json_default) + "\n")
