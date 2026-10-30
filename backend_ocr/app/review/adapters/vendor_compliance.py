"""Vendor completeness & verification posture — the Compliance Agent's core scan.

`vendor_identity.py` already flags a bank account that failed verification or a
GSTIN the portal rejected — those are *attempted-and-wrong*. This adapter is
the complement: *never attempted, or simply absent*. A vendor with no PAN on
file, no GSTIN at all, or no bank account row would sail through
`vendor_identity.py`'s query untouched, because there is nothing there to be
wrong. Two adapters, two questions ("is what's on file correct?" vs. "is
anything on file at all?"), same family — a controller working the
vendor_identity lane sees both without needing to know they come from
different detectors.

Policy is a single global `agent_registry` row (name='compliance'), not a
per-tenant table — `agent_registry` has no `tenant_id` column (see
scripts/agent_runtime_migrate.py's DDL), so unlike the HLD draft's assumption
there is no per-tenant override surface yet. Every tenant reads the same
policy until one is built; the adapter still degrades to sane hardcoded
defaults if even that row is missing, so it works before the migration seeds
anything and survives the row being deleted.

`grace_days_new_vendor` matters: a vendor onboarded this morning should not
immediately produce a finding — mirrors `triage.baseline_is_usable`'s
philosophy of not asserting things the data can't yet support.
"""
from __future__ import annotations

from .. import links
from ..routing import vendor_audience
from ..types import FindingDraft, action, condition_hash, fields_evidence
from .base import FindingAdapter, tenant_jurisdiction

_DEFAULT_POLICY = {
    "requires_gstin": True,
    "requires_pan": True,
    "requires_bank": True,
    "requires_tds_section": False,
    "grace_days_new_vendor": 7,
}

_TITLES = {
    "vendor_pan_missing":       "Vendor has no PAN on file",
    "vendor_gstin_missing":     "Vendor has no GSTIN on file",
    "vendor_gstin_unverified":  "Vendor's GSTIN has not passed verification",
    "vendor_bank_missing":      "Vendor has no bank account on file",
    "vendor_bank_unverified":   "Vendor has no verified bank account",
}


class VendorComplianceAdapter(FindingAdapter):
    detector = "adapter:vendor_compliance"
    family = "vendor_identity"
    source_service = "kyc_verification"

    async def _policy(self, conn) -> dict:
        row = await conn.fetchrow(
            "SELECT config FROM agent_registry WHERE name = 'compliance'"
        )
        if row is None:
            return dict(_DEFAULT_POLICY)
        from ..types import jsonb
        cfg = jsonb(row["config"], default={}) or {}
        return {**_DEFAULT_POLICY, **cfg}

    async def scan(self, conn, tenant_id: str, limit: int):
        policy = await self._policy(conn)
        # GSTIN, PAN and IFSC bank checks are India rule packs: a tenant outside
        # India is not asked for them (core/jurisdiction.py).
        j = await tenant_jurisdiction(conn, tenant_id)
        policy["requires_pan"] = policy.get("requires_pan") and j.has("pan")
        policy["requires_gstin"] = policy.get("requires_gstin") and j.has("gst")
        policy["requires_bank"] = policy.get("requires_bank") and j.has("bank_india")
        if not (policy["requires_pan"] or policy["requires_gstin"] or policy["requires_bank"]):
            return
        grace_days = int(policy.get("grace_days_new_vendor") or 0)

        rows = await conn.fetch(
            """
            SELECT s.supplier_id, s.name, s.pan, s.gstn, s.status AS kyb_status, s.created_at,
                   pg.gstin AS primary_gstin, pg.status AS primary_gstin_status,
                   -- A bank account lives in one of two places: a supplier_bank_accounts
                   -- row, or the older account fields on dim_suppliers itself (the
                   -- vendor card shows both). Reading only the table flagged vendors
                   -- with a verified account on the record as having none.
                   (EXISTS (
                       SELECT 1 FROM supplier_bank_accounts b
                        WHERE b.supplier_id = s.supplier_id AND b.tenant_id = s.tenant_id
                   ) OR NULLIF(TRIM(s.bank_account), '') IS NOT NULL) AS has_bank_row,
                   (EXISTS (
                       SELECT 1 FROM supplier_bank_accounts b
                        WHERE b.supplier_id = s.supplier_id AND b.tenant_id = s.tenant_id
                          AND b.verified = true
                   ) OR (NULLIF(TRIM(s.bank_account), '') IS NOT NULL
                         AND COALESCE(s.bank_verified, false))) AS has_verified_bank,
                   COALESCE(SUM(fi.outstanding_amount) FILTER (
                       WHERE fi.soft_status = 'active'
                         AND fi.payment_status IS DISTINCT FROM 'paid'), 0) AS exposure
              FROM dim_suppliers s
              LEFT JOIN fact_invoices fi ON fi.supplier_id = s.supplier_id
                                        AND fi.tenant_id   = s.tenant_id
              LEFT JOIN LATERAL (
                  SELECT g.gstin, g.status
                    FROM supplier_gstins g
                   WHERE g.supplier_id = s.supplier_id AND g.tenant_id = s.tenant_id
                     AND g.is_primary
                   LIMIT 1
              ) pg ON TRUE
             WHERE s.tenant_id = $1::uuid
               AND s.is_active
               AND s.created_at < now() - ($2 || ' days')::interval
             GROUP BY s.supplier_id, s.name, s.pan, s.gstn, s.status, s.created_at,
                      pg.gstin, pg.status, has_bank_row, has_verified_bank
             -- A vendor with no bills has nothing to pay; its missing details are
             -- not worth a finding (see NOT_ORPHAN_VENDOR_SQL in review_hub).
            HAVING COUNT(fi.invoice_id) FILTER (
                       WHERE COALESCE(fi.soft_status, 'active') = 'active') > 0
             ORDER BY exposure DESC
             LIMIT $3
            """,
            tenant_id, str(grace_days), limit,
        )

        aud = vendor_audience()

        for r in rows:
            sid = str(r["supplier_id"])
            exposure = r["exposure"] or 0
            # Money actually blocked, per §5.2.1 — same rule vendor_identity.py
            # uses to separate "urgent" from "worth a look".
            severity = "HIGH" if exposure > 0 else "MEDIUM"
            common = dict(
                tenant_id=tenant_id,
                family=self.family,
                subject_type="vendor",
                subject_id=sid,
                severity=severity,
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=exposure or None,
                money_at_risk_inr=exposure or None,
                deep_link=links.vendor(sid, tab="details", focus="compliance"),
                audience_roles=aud.roles,
                required_capability="verify",
                entities=(("vendor", sid, "subject", r["name"]),),
                actions=[
                    action("open_vendor", "Open vendor", kind="navigate",
                           href=links.vendor(sid, tab="details", focus="compliance")),
                ],
            )

            if policy.get("requires_pan") and not (r["pan"] or "").strip():
                yield FindingDraft(
                    finding_type="vendor_pan_missing",
                    title=_TITLES["vendor_pan_missing"],
                    detail="No PAN is recorded for this vendor, so TDS and 26AS "
                           "reconciliation cannot be relied on.",
                    condition_hash=condition_hash("pan_missing", sid),
                    key_qualifier="pan",
                    evidence=fields_evidence([("PAN", "—")]),
                    **common,
                )

            has_primary_gstin = bool((r["primary_gstin"] or r["gstn"] or "").strip())
            if r["primary_gstin"]:
                # A real supplier_gstins row is the source of truth once one
                # exists.
                gstin_verified = r["primary_gstin_status"] == "VERIFIED"
            elif r["gstn"]:
                # No supplier_gstins row yet — the legacy dim_suppliers.gstn is
                # the vendor's only GSTIN, and its own KYB status (`s.status`,
                # the same column vendor_identity.py checks for 'FAILED') is
                # the only verification signal that exists for it. Mirrors
                # vendor_gstins.py's GET handler, which synthesises exactly
                # this mapping for the primary row it injects into the response.
                gstin_verified = r["kyb_status"] == "VERIFIED"
            else:
                gstin_verified = None

            if policy.get("requires_gstin"):
                if not has_primary_gstin:
                    yield FindingDraft(
                        finding_type="vendor_gstin_missing",
                        title=_TITLES["vendor_gstin_missing"],
                        detail="No GSTIN is recorded for this vendor — input credit "
                               "on their bills cannot be claimed.",
                        condition_hash=condition_hash("gstin_missing", sid),
                        key_qualifier="gstin",
                        evidence=fields_evidence([("GSTIN", "—")]),
                        **common,
                    )
                elif gstin_verified is False:
                    gstin_value = r["primary_gstin"] or r["gstn"]
                    status_label = r["primary_gstin_status"] if r["primary_gstin"] else r["kyb_status"]
                    yield FindingDraft(
                        finding_type="vendor_gstin_unverified",
                        title=_TITLES["vendor_gstin_unverified"],
                        detail="The GSTIN on file has not passed verification against "
                               "the GST portal.",
                        condition_hash=condition_hash("gstin_unverified", sid, gstin_value),
                        key_qualifier="gstin",
                        evidence=fields_evidence([
                            ("GSTIN", gstin_value),
                            ("Status", status_label or "UNVERIFIED"),
                        ]),
                        **common,
                    )

            if policy.get("requires_bank"):
                if not r["has_bank_row"]:
                    yield FindingDraft(
                        finding_type="vendor_bank_missing",
                        title=_TITLES["vendor_bank_missing"],
                        detail="No bank account is on file — a bill from this vendor "
                               "cannot be paid until one is added.",
                        condition_hash=condition_hash("bank_missing", sid),
                        key_qualifier="bank",
                        evidence=fields_evidence([("Bank account", "—")]),
                        **common,
                    )
                elif not r["has_verified_bank"]:
                    yield FindingDraft(
                        finding_type="vendor_bank_unverified",
                        title=_TITLES["vendor_bank_unverified"],
                        detail="This vendor has a bank account on file, but none of "
                               "its accounts have passed verification.",
                        condition_hash=condition_hash("bank_unverified", sid),
                        key_qualifier="bank",
                        evidence=fields_evidence([("Verified account", "None")]),
                        **common,
                    )
