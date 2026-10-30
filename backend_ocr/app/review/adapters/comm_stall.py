"""comm_stall.py — a vendor thread that has gone quiet on their side.

§5.1.2/§7 of the HLD. `comm_thread_state` (populated by
`comm_extraction_worker.py`'s inbound path and `email_connection.py`'s
outbound send) already flips `status` to `awaiting_vendor` the moment our
side sends and to `awaiting_internal` the moment they reply — this adapter
only has to notice a thread stuck at `awaiting_vendor` for longer than the
tenant's patience allows. No new state, no new write path.

Policy lives on the same `agent_registry` row (`name='communication'`)
`comm_extraction_worker.py` already reads for its model choice — one row per
concern that agent owns, not a new table — with the same
fall-back-to-a-hardcoded-default shape `vendor_compliance.py` uses when the
row (or the key) is missing, so this works before any migration seeds
anything and keeps working if the row is ever deleted.

The primary action is a `notify` re-nudging the vendor, built exactly like
`master_data_conflict.py`'s `notify_vendor` action — same endpoint, same
`destination_type: "vendor_contact"` body shape — because both are "ask the
Communication Agent to send this message," not two different mechanisms.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import links
from ..routing import vendor_audience
from ..types import FindingDraft, action, condition_hash, jsonb, timeline_evidence
from .base import FindingAdapter

DEFAULT_STALL_DAYS = 3


class CommStallAdapter(FindingAdapter):
    detector = "adapter:comm_stall"
    family = "data_quality"
    source_service = "email_sending"

    async def _stall_days(self, conn) -> int:
        row = await conn.fetchrow(
            "SELECT config FROM agent_registry WHERE name = 'communication'"
        )
        if row is None:
            return DEFAULT_STALL_DAYS
        cfg = jsonb(row["config"], default={}) or {}
        try:
            return int(cfg.get("stall_days") or DEFAULT_STALL_DAYS)
        except (TypeError, ValueError):
            return DEFAULT_STALL_DAYS

    async def scan(self, conn, tenant_id: str, limit: int):
        stall_days = await self._stall_days(conn)

        rows = await conn.fetch(
            """
            SELECT t.thread_id, t.supplier_id, t.contact_id, t.purpose,
                   t.last_message_at, s.name AS vendor_name,
                   c.name AS contact_name
              FROM comm_thread_state t
              LEFT JOIN dim_suppliers s ON s.supplier_id = t.supplier_id
                                       AND s.tenant_id   = t.tenant_id
              LEFT JOIN supplier_contacts c ON c.id = t.contact_id
                                            AND c.tenant_id = t.tenant_id
             WHERE t.tenant_id = $1::uuid
               AND t.status = 'awaiting_vendor'
               AND t.last_message_at < now() - ($2 || ' days')::interval
             ORDER BY t.last_message_at ASC
             LIMIT $3
            """,
            tenant_id, str(stall_days), limit,
        )

        aud = vendor_audience(capability="edit")

        for r in rows:
            thread_id = r["thread_id"]
            supplier_id = str(r["supplier_id"]) if r["supplier_id"] else None
            contact_id = str(r["contact_id"]) if r["contact_id"] else None
            vendor_name = r["vendor_name"] or "This vendor"
            last_at = r["last_message_at"]
            days_silent = (datetime.now(timezone.utc) - _as_utc(last_at)).days if last_at else None

            actions = [
                action("open_thread", "Open the conversation", kind="navigate",
                       href=links.vendor(supplier_id, tab="contacts") if supplier_id
                       else links.bills_list()),
            ]
            # Same reasoning as master_data_conflict.py's notify_vendor: a
            # thread with no attributed contact has nobody to notify, and the
            # navigate action above is still offered so the thread is never
            # unreachable, just not re-nudgeable in one click.
            if contact_id and supplier_id:
                actions.append(action(
                    "notify_vendor", "Send a follow-up", kind="notify",
                    endpoint="/api/review/act/notify", method="POST",
                    body={
                        "destination_type": "vendor_contact",
                        "contact_id": contact_id,
                        "subject": f"Following up — {r['purpose'].replace('_', ' ')}"
                                   if r["purpose"] and r["purpose"] != "general"
                                   else "Following up on our last message",
                        "message": (
                            f"Hi,\n\nJust following up — we haven't heard back in "
                            f"{days_silent or stall_days} days. Could you take a look "
                            f"when you get a chance?\n\nThanks."
                        ),
                    },
                    confirm={
                        "title": f"Send a follow-up to {vendor_name}",
                        "impact": [{"label": "To", "value": r["contact_name"] or vendor_name}],
                    },
                ))

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="comm_thread_stalled",
                family=self.family,
                subject_type="vendor",
                subject_id=supplier_id,
                severity="LOW",
                title=f"No reply from {vendor_name} in "
                      f"{days_silent if days_silent is not None else stall_days}+ days",
                detail=f"We're waiting on a reply for this thread"
                       + (f" about {r['purpose'].replace('_', ' ')}" if r["purpose"] and r["purpose"] != "general" else "")
                       + " and none has come in.",
                deep_link=(links.vendor(supplier_id, tab="contacts") if supplier_id
                           else links.bills_list()),
                condition_hash=condition_hash("comm_stall", tenant_id, thread_id),
                detector=self.detector,
                source_service=self.source_service,
                verification="VERIFIED",
                confidence=1.0,
                key_qualifier=thread_id,
                evidence=timeline_evidence([
                    {"label": "We sent the last message", "at": str(last_at) if last_at else None,
                     "actor": "us", "state": "done"},
                    {"label": "Waiting on a reply", "at": None, "actor": vendor_name, "state": "blocked"},
                ]),
                actions=actions,
                audience_roles=aud.roles,
                required_capability="edit",
                entities=(("vendor", supplier_id, "subject", vendor_name),) if supplier_id else (),
            )


def _as_utc(value) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
