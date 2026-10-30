"""An audit trail of every change made from the Review Hub.

Review is where people fix things, and it fixes them in many places: its own
endpoints (status changes, bulk decisions, accepted corrections, one-click
fixes) and the vendor and bill cards it opens, which write through the ordinary
vendor and bill endpoints. Each of those already records *something*, but not in
one place, not always with the actor, and never with what the value was before.

This middleware records one row per change in `rh_audit_log`:

  * every mutating request under /api/review/ ; and
  * every mutating request the frontend marks as coming from a Review card —
    it sends `X-Review-Finding: <finding id>` while a card opened from a
    finding is open (frontend/src/api/reviewContext.ts).

Each row carries who (user, role, tenant from the token), when, the finding, the
request (method, path, the body with secrets masked) and the response status.
For a vendor or a bill it also snapshots the record before and after the
request, so the row says exactly what changed: {"pan": ["AALCM5200G",
"BAWPP4406L"]}.

Recording never gets in the way of the request: any failure here is logged and
the request proceeds untouched.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from anyio import to_thread

log = logging.getLogger(__name__)

# The table is created by scripts/review_hub_migrate.py ("rh_audit_log").

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_UUID = r"[0-9a-fA-F-]{36}"
_SECRET_KEYS = {"password", "otp", "token", "refresh_token", "secret", "access_token"}
_MAX_BODY = 64 * 1024  # bigger bodies (uploads) are summarised, not stored

# What a vendor / bill looked like, for the before → after of a change.
_VENDOR_SNAPSHOT = """
    SELECT s.name, s.legal_name, s.gstn, s.pan, s.email, s.payment_terms_days,
           s.bank_account, s.bank_ifsc, s.bank_verified, s.status,
           s.vendor_category_id::text AS vendor_category_id, s.brand_id::text AS brand_id,
           s.is_active,
           (SELECT string_agg(c.email, ', ' ORDER BY c.email) FROM supplier_contacts c
             WHERE c.supplier_id = s.supplier_id AND c.tenant_id = s.tenant_id) AS contacts,
           (SELECT string_agg(b.account_number || CASE WHEN b.verified THEN ' (verified)' ELSE '' END,
                              ', ' ORDER BY b.account_number)
              FROM supplier_bank_accounts b
             WHERE b.supplier_id = s.supplier_id AND b.tenant_id = s.tenant_id) AS bank_accounts,
           (SELECT string_agg(g.gstin, ', ' ORDER BY g.gstin) FROM supplier_gstins g
             WHERE g.supplier_id = s.supplier_id AND g.tenant_id = s.tenant_id) AS gstins
      FROM dim_suppliers s
     WHERE s.supplier_id = %s::uuid AND s.tenant_id = %s::uuid
"""
_BILL_SNAPSHOT = """
    SELECT fi.status, fi.category, fi.department, fi.due_date::text AS due_date,
           -- Terms are the gap between the dates; a due-date move *is* a change
           -- of terms, and that is how a person describes it.
           (fi.due_date - fi.invoice_date) AS payment_terms_days,
           fi.cost_center_id::text AS cost_center_id, fi.manual_priority, fi.soft_status,
           fi.payment_status, fi.amount_paid::text AS amount_paid,
           fi.total_amount::text AS total_amount, fi.supplier_id::text AS supplier_id
      FROM fact_invoices fi
     WHERE fi.invoice_id = %s::uuid AND fi.tenant_id = %s::uuid
"""


def _entity(path: str, body: dict) -> tuple[str | None, str | None]:
    """Which vendor or bill a request changes, from its path (or body)."""
    m = re.match(rf"^/api/vendors/({_UUID})", path)
    if m:
        return "vendor", m.group(1)
    m = re.match(rf"^/api/bills/(?:pay/)?({_UUID})", path)
    if m:
        return "bill", m.group(1)
    if isinstance(body, dict) and body.get("invoice_id"):
        return "bill", str(body["invoice_id"])
    m = re.match(rf"^/api/review/findings/({_UUID})", path)
    if m:
        return "finding", m.group(1)
    return None, None


def _mask(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: ("***" if k.lower() in _SECRET_KEYS else _mask(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_mask(x) for x in v]
    return v


def _parse_body(raw: bytes, content_type: str) -> dict:
    if not raw:
        return {}
    if "json" not in content_type or len(raw) > _MAX_BODY:
        return {"_content_type": content_type.split(";")[0], "_bytes": len(raw)}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"_unparsed_bytes": len(raw)}
    return _mask(data) if isinstance(data, dict) else {"_value": _mask(data)}


def _identity(headers: dict[str, str]) -> dict:
    """Who is asking, from the bearer token — the same verification the API
    itself uses. Unreadable or absent: recorded as unknown, never rejected here."""
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return {}
    try:
        from ..security.auth import _verify
        claims = _verify(auth.split(" ", 1)[1].strip())
        return {"actor": claims.get("name") or claims.get("sub"), "role": claims.get("role"),
                "tenant": claims.get("company_id") or claims.get("tenant_id")}
    except Exception:
        return {}


def _snapshot(entity_type: str | None, entity_id: str | None, tenant: str | None) -> dict | None:
    if entity_type not in ("vendor", "bill") or not entity_id or not tenant:
        return None
    from ..config import get_conn, put_conn
    from psycopg2.extras import RealDictCursor
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_VENDOR_SNAPSHOT if entity_type == "vendor" else _BILL_SNAPSHOT,
                        (entity_id, tenant))
            row = cur.fetchone()
        conn.rollback()
        return {k: (str(v) if v is not None and not isinstance(v, (bool, int, float, str)) else v)
                for k, v in (row or {}).items()}
    except Exception:
        conn.rollback()
        return None
    finally:
        put_conn(conn)


def _diff(before: dict | None, after: dict | None) -> dict:
    if before is None or after is None:
        return {}
    return {k: [before.get(k), after.get(k)]
            for k in set(before) | set(after) if before.get(k) != after.get(k)}


def _write(row: dict) -> None:
    from ..config import get_conn, put_conn
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO rh_audit_log
                       (tenant_id, actor, actor_role, finding_id, surface, method, path,
                        entity_type, entity_id, request_body, changes, status_code, request_id)
                   VALUES (%s::uuid, %s, %s, %s::uuid, %s, %s, %s, %s, %s,
                           %s::jsonb, %s::jsonb, %s, %s)""",
                (row["tenant"], row["actor"], row["role"], row["finding_id"], row["surface"],
                 row["method"], row["path"], row["entity_type"], row["entity_id"],
                 json.dumps(row["body"], default=str), json.dumps(row["changes"], default=str),
                 row["status"], row["request_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        log.warning("[review-audit] could not record %s %s", row["method"], row["path"], exc_info=True)
    finally:
        put_conn(conn)


def _finding_id(value: str | None, body: dict) -> str | None:
    cand = value or (body.get("finding_id") if isinstance(body, dict) else None)
    return cand if cand and re.fullmatch(_UUID, str(cand)) else None


class ReviewAuditMiddleware:
    """Pure ASGI, so the request body can be read on its way to the handler
    without being consumed, and the response status seen on its way out."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _MUTATING:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        path = scope.get("path", "")
        from_card = headers.get("x-review-finding")
        if not (path.startswith("/api/review/") or from_card):
            return await self.app(scope, receive, send)

        # Small bodies (every JSON edit) are read up front and replayed to the
        # handler unchanged, so the record a change is about is known — and
        # snapshotted — before the change runs. Large ones (uploads) stream
        # straight through and are summarised by size.
        declared = int(headers.get("content-length") or 0)
        buffered = declared <= _MAX_BODY
        raw = b""
        if buffered:
            more = True
            while more:
                msg = await receive()
                if msg.get("type") != "http.request":
                    break
                raw += msg.get("body", b"")
                more = msg.get("more_body", False)
            replayed = False

            async def recv():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": raw, "more_body": False}
                return await receive()
        else:
            recv = receive

        status = {"code": None}

        async def snd(msg):
            if msg.get("type") == "http.response.start":
                status["code"] = msg.get("status")
            await send(msg)

        try:
            who = _identity(headers)
            body = _parse_body(raw, headers.get("content-type", "")) if buffered else {"_bytes": declared}
            etype, eid = _entity(path, body)
            before = await to_thread.run_sync(_snapshot, etype, eid, who.get("tenant"))
        except Exception:
            log.warning("[review-audit] could not prepare %s %s", scope.get("method"), path, exc_info=True)
            who, body, etype, eid, before = {}, {}, None, None, None

        try:
            await self.app(scope, recv, snd)
        finally:
            try:
                after = (await to_thread.run_sync(_snapshot, etype, eid, who.get("tenant"))
                         if before is not None else None)
                row = {
                    "tenant": who.get("tenant"), "actor": who.get("actor"), "role": who.get("role"),
                    "finding_id": _finding_id(from_card, body),
                    "surface": headers.get("x-review-surface") or ("card" if from_card else "hub"),
                    "method": scope.get("method"), "path": path,
                    "entity_type": etype, "entity_id": eid, "body": body,
                    "changes": _diff(before, after), "status": status["code"],
                    "request_id": headers.get("x-request-id"),
                }
                await to_thread.run_sync(_write, row)
            except Exception:
                log.warning("[review-audit] failed for %s %s", scope.get("method"), path, exc_info=True)
