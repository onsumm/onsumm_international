"""Vendor names the GST portal silently replaced.

pipelines/gst_verification.py:_decide has a branch for a GSTIN that is valid and
active with a matching PAN but a *different* legal name. It accepts the portal's
name as authoritative, overwrites dim_suppliers.name, and marks the vendor
VERIFIED. The only record is a wf_audit_log row carrying name_updated_from /
name_updated_to. Nobody is told.

That is exactly the "name mismatch" case the hub is supposed to catch, and it is
currently invisible by design. This adapter reads the audit trail and turns a
silent data mutation into something a human can confirm or correct — without
touching the verification logic, which is behaving reasonably.
"""
from __future__ import annotations

from .. import links
from ..routing import vendor_audience
from ..types import FindingDraft, action, condition_hash, diff_evidence
from .base import FindingAdapter, tenant_jurisdiction

# Only surface recent rewrites; an overwrite from two years ago that nobody
# objected to is settled, and raising it now is noise.
_WINDOW_DAYS = 90


class GstNameOverwriteAdapter(FindingAdapter):
    detector = "adapter:gst_name_overwrite"
    family = "vendor_identity"
    source_service = "gst_tax_verification"

    async def scan(self, conn, tenant_id: str, limit: int):
        # India rule pack 'gst' (core/jurisdiction.py): names replaced from the GST portal only exist where GST verification runs.
        if not (await tenant_jurisdiction(conn, tenant_id)).has("gst"):
            return
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (a.entity_id)
                   a.entity_id AS supplier_id,
                   a.created_at,
                   a.new_values_jsonb ->> 'name_updated_from' AS name_from,
                   a.new_values_jsonb ->> 'name_updated_to'   AS name_to,
                   a.new_values_jsonb ->> 'api_legal_name'    AS legal_name,
                   a.new_values_jsonb ->> 'state'             AS state,
                   s.name AS current_name, s.gstn
              FROM wf_audit_log a
              JOIN dim_suppliers s ON s.supplier_id = a.entity_id
                                  AND s.tenant_id   = a.tenant_id
             WHERE a.tenant_id  = $1::uuid
               AND a.table_name = 'dim_suppliers'
               AND a.new_values_jsonb ? 'name_updated_to'
               AND a.created_at > now() - ($3 || ' days')::interval
               AND s.is_active
             ORDER BY a.entity_id, a.created_at DESC
             LIMIT $2
            """,
            tenant_id, limit, str(_WINDOW_DAYS),
        )

        aud = vendor_audience()

        for r in rows:
            sid = str(r["supplier_id"])
            name_from, name_to = r["name_from"], r["name_to"]
            # If a human has since renamed it back or to something else, the
            # overwrite has been dealt with — stop asserting it.
            if not name_from or not name_to or r["current_name"] != name_to:
                continue

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="vendor_name_replaced_by_gst_portal",
                family=self.family,
                subject_type="vendor",
                subject_id=sid,
                severity="MEDIUM",
                title="Vendor name was replaced with the GST portal's legal name",
                detail=(
                    "Confirm it is the same company — a matching PAN under a different name "
                    "can also mean the bill went to the wrong vendor."
                ),
                deep_link=links.vendor(sid, tab="details", focus="name"),
                condition_hash=condition_hash(name_from, name_to),
                detector=self.detector,
                source_service=self.source_service,
                evidence=diff_evidence(
                    "Name we had", "Name on the GST portal",
                    [
                        ("Vendor name", name_from, name_to),
                        ("GSTIN", r["gstn"] or "—", r["gstn"] or "—"),
                        ("State", "—", r["state"] or "—"),
                    ],
                ),
                entities=(("vendor", sid, "subject", r["current_name"]),),
                audience_roles=aud.roles,
                required_capability="verify",
                actions=[
                    # Usually what happened: a trading name and a proprietor's
                    # registered name. Keep both, each in its place.
                    action("keep_both_names",
                           f"Keep {name_to} as legal name, {name_from} as brand name",
                           kind="safe", capability="edit",
                           endpoint=f"/api/vendors/{sid}/names",
                           body={"name": name_from, "legal_name": name_to},
                           success={"ok": True},
                           preview=f"Bills and reports show “{name_from}”; the GST "
                                   f"portal's “{name_to}” is kept as the legal name."),
                    action("open_vendor", "Open vendor record", kind="navigate",
                           href=links.vendor(sid, tab="details", focus="name")),
                ],
            )
