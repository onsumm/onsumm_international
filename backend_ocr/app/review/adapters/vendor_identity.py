"""Vendor master problems: GST verification, bank verification, PAN duplicates.

Bank state is resolved the way a payment resolves it, not from
dim_suppliers.bank_verified alone. Those columns are the single-account
original; supplier_bank_accounts superseded them and is what the vendor card
shows and what disbursement._get_verified_vendor_account pays from. Reading only
the legacy column told users their vendor had "never been verified" while the
vendor's own Bank tab showed a verified primary account two clicks away — a
finding that is not merely useless but actively wrong, which is the fastest way
to lose the queue's credibility.

Note the tri-state on dim_suppliers.bank_verified, documented in
pipelines/bank_verification.py: false means "not attempted or retryable", true
means verified, and **NULL means definitively not found and no more retries**.
Those are different findings with different remedies, so they are different
finding types — treating NULL as "unverified" would tell a user to retry
something the system has already given up on. The tri-state applies to the
legacy column only: once a real account row exists, "gave up" is not a state it
can be in.
"""
from __future__ import annotations

from .. import links
from ..routing import vendor_audience
from ..types import (
    FindingDraft, action, condition_hash, field, fields_evidence, text_evidence,
)
from .base import FindingAdapter, tenant_jurisdiction


# An IFSC is four letters, a zero, then six alphanumerics. Worth checking in the
# drawer: the field is a free-text column and has been found holding things like
# "New Friends Colony (110025) & IDFB0020102", which fails verification every
# time without ever saying why.
_IFSC_PATTERN = "^[A-Za-z]{4}0[A-Za-z0-9]{6}$"


def _bank_fields(account: str | None, ifsc: str | None) -> list[dict]:
    """The two values verification runs on, prefilled with what we hold."""
    return [
        field("account_number", "Account number", value=account or "",
              placeholder="As printed on the cheque or statement"),
        field("ifsc", "IFSC", value=ifsc or "", placeholder="IDFB0020102",
              pattern=_IFSC_PATTERN, hint="Eleven characters, e.g. IDFB0020102"),
    ]


class VendorIdentityAdapter(FindingAdapter):
    detector = "adapter:vendor_identity"
    family = "vendor_identity"
    source_service = "kyc_verification"

    async def scan(self, conn, tenant_id: str, limit: int):
        # GST portal, IFSC bank and PAN checks are India rule packs
        # (core/jurisdiction.py); off for a tenant outside India.
        j = await tenant_jurisdiction(conn, tenant_id)
        gst_on, bank_on, pan_on = j.has("gst"), j.has("bank_india"), j.has("pan")
        if not (gst_on or bank_on or pan_on):
            return
        rows = await conn.fetch(
            """
            SELECT s.supplier_id, s.name, s.gstn, s.pan, s.status,
                   s.bank_verified, s.pan_dup_deferred,
                   -- Prefer the account the vendor card shows and payments use;
                   -- fall back to the legacy columns for vendors that predate it.
                   sba.id IS NOT NULL                          AS has_account_row,
                   sba.verified                                AS account_verified,
                   COALESCE(sba.account_number, s.bank_account) AS bank_account,
                   COALESCE(sba.ifsc, s.bank_ifsc)              AS bank_ifsc,
                   COALESCE(SUM(fi.outstanding_amount) FILTER (
                       WHERE fi.soft_status = 'active'
                         AND fi.payment_status IS DISTINCT FROM 'paid'), 0) AS exposure,
                   COUNT(fi.invoice_id) FILTER (WHERE fi.soft_status = 'active') AS open_bills,
                   g.gstin_status, g.error_message, g.gstin
              FROM dim_suppliers s
              LEFT JOIN fact_invoices fi ON fi.supplier_id = s.supplier_id
                                        AND fi.tenant_id   = s.tenant_id
              LEFT JOIN supplier_gstins g ON g.supplier_id = s.supplier_id
                                         AND g.tenant_id   = s.tenant_id
                                         AND g.is_primary
              -- One account per vendor: a verified one if there is one, else the
              -- primary. Same precedence as the payment path.
              LEFT JOIN LATERAL (
                  SELECT b.id, b.account_number, b.ifsc, b.verified
                    FROM supplier_bank_accounts b
                   WHERE b.supplier_id = s.supplier_id
                     AND b.tenant_id   = s.tenant_id
                   ORDER BY b.verified DESC, b.is_primary DESC, b.created_at DESC
                   LIMIT 1
              ) sba ON TRUE
             WHERE s.tenant_id = $1::uuid
               AND s.is_active
               AND (s.status = 'FAILED'
                 OR s.bank_verified IS NOT TRUE
                 OR s.pan_dup_deferred
                 OR g.gstin_status IN ('INVALID','CANCELLED','SUSPENDED'))
             GROUP BY s.supplier_id, g.gstin_status, g.error_message, g.gstin,
                      sba.id, sba.verified, sba.account_number, sba.ifsc
             -- No bills, nothing to pay: not a finding (NOT_ORPHAN_VENDOR_SQL).
            HAVING COUNT(fi.invoice_id) FILTER (
                       WHERE COALESCE(fi.soft_status, 'active') = 'active') > 0
             ORDER BY exposure DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        aud = vendor_audience()

        for r in rows:
            sid = str(r["supplier_id"])
            exposure = r["exposure"] or 0
            blocking = exposure > 0
            common = dict(
                tenant_id=tenant_id,
                family=self.family,
                subject_type="vendor",
                subject_id=sid,
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=exposure or None,
                money_at_risk_inr=exposure or None,
                audience_roles=aud.roles,
                required_capability="verify",
                entities=(("vendor", sid, "subject", r["name"]),),
            )

            if gst_on and r["status"] == "FAILED":
                yield FindingDraft(
                    finding_type="vendor_gstin_verification_failed",
                    severity="HIGH" if blocking else "MEDIUM",
                    title="GSTIN verification failed for this vendor",
                    detail=(r["error_message"]
                            or "The GST portal rejected this GSTIN, so input credit is at risk."),
                    deep_link=links.vendor(sid, tab="gstins", focus="gstin"),
                    condition_hash=condition_hash("FAILED", r["gstin_status"], r["gstn"], exposure),
                    evidence=fields_evidence([
                        ("GSTIN on file", r["gstn"] or "—"),
                        ("Portal status", r["gstin_status"] or "unknown"),
                        ("Portal message", r["error_message"] or "—"),
                        ("Open bills", r["open_bills"]),
                    ]),
                    actions=[
                        action("reverify_gstin", "Re-run verification", kind="safe",
                               endpoint=f"/api/vendors/{sid}/gstins/primary/reverify",
                               capability="verify",
                               queued="Queued with the GST portal. This finding will clear on "
                                      "its own once the portal answers.",
                               preview="Asks the GST portal again. The answer arrives in the "
                                       "background, usually within a few minutes."),
                        action("open_vendor", "Open vendor", kind="navigate",
                               href=links.vendor(sid, tab="gstins", focus="gstin")),
                    ],
                    **common,
                )

            # The state a payment would see. An account row that exists can
            # always be retried, so the "gave up" NULL belongs to the legacy
            # column alone.
            # No account anywhere is "missing" (vendor_compliance raises that, with
            # the right fix: add one), not "never verified". Without this, the
            # legacy bank_verified flag — false by default — reported a vendor with
            # no account at all as having an unverified one.
            has_any_account = r["has_account_row"] or bool((r["bank_account"] or "").strip())
            bank_state = (
                (r["account_verified"] if r["has_account_row"] else r["bank_verified"])
                if has_any_account else "missing"
            )

            if bank_on and bank_state is None:
                yield FindingDraft(
                    finding_type="vendor_bank_unverifiable",
                    severity="HIGH" if blocking else "LOW",
                    title="Bank account could not be found and will not be retried",
                    detail="Verification has given up on this account — it needs new details "
                           "from the vendor, not a retry.",
                    deep_link=links.vendor(sid, tab="bank", focus="bank"),
                    condition_hash=condition_hash("bank_null", r["bank_account"], r["bank_ifsc"], exposure),
                    evidence=fields_evidence([
                        ("Account", r["bank_account"] or "—"),
                        ("IFSC", r["bank_ifsc"] or "—"),
                        ("Open bills", r["open_bills"]),
                    ], note="Payments to this vendor are blocked while the account is unverified."),
                    actions=[
                        action("replace_bank", "Enter new details", kind="safe",
                               endpoint=f"/api/vendors/{sid}/verify-bank", capability="verify",
                               fields=_bank_fields(r["bank_account"], r["bank_ifsc"]),
                               success={"verified": True, "persisted": True},
                               preview="Verifies the details you enter and replaces what is on "
                                       "file if they check out."),
                        action("open_vendor_bank", "Open bank details", kind="navigate",
                               href=links.vendor(sid, tab="bank", focus="bank")),
                    ],
                    **common,
                )
            elif bank_on and bank_state is False:
                yield FindingDraft(
                    finding_type="vendor_bank_unverified",
                    severity="HIGH" if blocking else "MEDIUM",
                    title="Vendor bank account has never been verified",
                    detail="Payment is blocked until the account passes a penny-drop check.",
                    deep_link=links.vendor(sid, tab="bank", focus="bank"),
                    condition_hash=condition_hash("bank_false", r["bank_account"], r["bank_ifsc"], exposure),
                    evidence=fields_evidence([
                        ("Account", r["bank_account"] or "—"),
                        ("IFSC", r["bank_ifsc"] or "—"),
                        ("Amount waiting", str(exposure)),
                    ]),
                    actions=[
                        action("verify_bank", "Check and verify", kind="safe",
                               endpoint=f"/api/vendors/{sid}/verify-bank", capability="verify",
                               fields=_bank_fields(r["bank_account"], r["bank_ifsc"]),
                               success={"verified": True, "persisted": True},
                               preview="Runs a penny-drop check on these details and records "
                                       "the name the bank returns."),
                        action("open_vendor_bank", "Open bank details", kind="navigate",
                               href=links.vendor(sid, tab="bank", focus="bank")),
                    ],
                    **common,
                )

            if pan_on and r["pan_dup_deferred"]:
                yield FindingDraft(
                    finding_type="vendor_pan_duplicate_deferred",
                    severity="LOW",
                    title="Possible duplicate vendor was set aside and never resolved",
                    detail="A PAN-match duplicate was set aside here and never resolved.",
                    deep_link=links.vendor(sid, tab="details", focus="pan_duplicate"),
                    condition_hash=condition_hash("pan_dup", r["pan"]),
                    evidence=text_evidence(
                        "Another active vendor shares this PAN.",
                        [f"PAN {r['pan'] or '—'}", f"{r['open_bills']} open bills on this record"],
                    ),
                    actions=[action("open_dedup", "Review the duplicate", kind="navigate",
                                    href=links.vendor(sid, tab="details", focus="pan_duplicate"))],
                    **common,
                )
