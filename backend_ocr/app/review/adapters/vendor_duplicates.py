"""Vendors that exist twice because the extraction misread a GSTIN.

The worked example this was built from, and it is worth keeping in view:

    Vodafone Idea Limited   07AAACB2100P1ZZ   VERIFIED   entered by hand   2 bills
    Vodafone Idea Limited   07AACCA2800P1Z2   FAILED     from an upload    1 bill

One vendor, two records. The OCR read four characters wrong, the wrong GSTIN
naturally failed verification at the portal, and because the bill carried an
unrecognised GSTIN the ingest created a second vendor for it. The user now has
a split ledger, a vendor that can never be paid, and a bill attached to the
wrong record.

Nothing in the platform connects those three facts today. The GST worker knows
the verification failed. The dedup worker matches on bank account or exact name
with *different* GSTINs — but it flags vendor duplication generically, and
crucially it never auto-merges. Neither knows the failure and the duplicate are
the same event.

This detector is deliberately narrow, because the fix it offers is a write:
  * same tenant, both active, identical normalised name
  * different GSTIN
  * exactly one of them verified
  * the unverified one is the newer / upload-sourced record

That combination is close to unambiguous. Anything looser gets reported by the
existing duplicate-vendor adapter instead, which only ever asks a human to look.
"""
from __future__ import annotations

from .. import links
from ..routing import vendor_audience
from ..types import (
    FindingDraft, action, code_diff_evidence, condition_hash, document_ref,
)
from .base import FindingAdapter, tenant_jurisdiction


def gstin_diff(wrong: str, right: str) -> list[int]:
    """Positions where two GSTINs disagree — the evidence that this is a misread.

    Four scattered character substitutions in a fifteen-character code is an OCR
    signature. Two completely different registrations are not that.
    """
    if not wrong or not right or len(wrong) != len(right):
        return []
    return [i for i, (a, b) in enumerate(zip(wrong, right)) if a != b]


class VendorDuplicatesAdapter(FindingAdapter):
    detector = "adapter:vendor_duplicates"
    family = "vendor_identity"
    source_service = "kyc_verification"

    async def scan(self, conn, tenant_id: str, limit: int):
        # India rule pack 'gst' (core/jurisdiction.py): misread-GSTIN duplicates only arise where GSTINs are captured.
        if not (await tenant_jurisdiction(conn, tenant_id)).has("gst"):
            return
        aud = vendor_audience()

        rows = await conn.fetch(
            """
            SELECT bad.supplier_id  AS bad_id,  bad.name  AS bad_name,
                   bad.gstn         AS bad_gstn, bad.status AS bad_status,
                   bad.source       AS bad_source, bad.created_at AS bad_created,
                   good.supplier_id AS good_id, good.gstn AS good_gstn,
                   good.pan         AS good_pan, good.created_at AS good_created,
                   (SELECT count(*) FROM fact_invoices fi
                     WHERE fi.supplier_id = bad.supplier_id
                       AND fi.tenant_id = bad.tenant_id) AS bad_bills,
                   (SELECT COALESCE(SUM(fi.total_amount), 0) FROM fact_invoices fi
                     WHERE fi.supplier_id = bad.supplier_id
                       AND fi.tenant_id = bad.tenant_id) AS bad_value,
                   (SELECT count(*) FROM fact_invoices fi
                     WHERE fi.supplier_id = good.supplier_id
                       AND fi.tenant_id = good.tenant_id) AS good_bills,
                   -- The document the wrong GSTIN was read off. Without it the
                   -- user cannot tell a misread from a real second registration
                   -- without leaving the hub, which is the whole point.
                   (SELECT fi.upload_id FROM fact_invoices fi
                     WHERE fi.supplier_id = bad.supplier_id
                       AND fi.tenant_id = bad.tenant_id
                       AND fi.upload_id IS NOT NULL
                     ORDER BY fi.invoice_date DESC NULLS LAST LIMIT 1) AS bad_upload,
                   (SELECT fi.invoice_number FROM fact_invoices fi
                     WHERE fi.supplier_id = bad.supplier_id
                       AND fi.tenant_id = bad.tenant_id
                       AND fi.upload_id IS NOT NULL
                     ORDER BY fi.invoice_date DESC NULLS LAST LIMIT 1) AS bad_invoice_no
              FROM dim_suppliers bad
              JOIN dim_suppliers good
                ON good.tenant_id = bad.tenant_id
               AND lower(btrim(good.name)) = lower(btrim(bad.name))
               AND good.supplier_id <> bad.supplier_id
               AND good.is_active
               AND good.status = 'VERIFIED'
             WHERE bad.tenant_id = $1::uuid
               AND bad.is_active
               AND bad.status IS DISTINCT FROM 'VERIFIED'
               AND bad.gstn IS NOT NULL
               AND good.gstn IS NOT NULL
               AND bad.gstn <> good.gstn
             ORDER BY bad_value DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        for r in rows:
            bad_id, good_id = str(r["bad_id"]), str(r["good_id"])
            bad_gstn = (r["bad_gstn"] or "").strip()
            good_gstn = (r["good_gstn"] or "").strip()
            positions = gstin_diff(bad_gstn, good_gstn)

            # Two genuinely different registrations for one trading name is a real
            # thing (a company with branches in two states). Leave those to the
            # generic duplicate-vendor finding, which only asks a human to look.
            if not positions or len(positions) > 6:
                continue
            # A differing state code means two registrations, not a misread.
            if bad_gstn[:2] != good_gstn[:2]:
                continue

            n_bills = int(r["bad_bills"] or 0)
            value = r["bad_value"]

            yield FindingDraft(
                tenant_id=tenant_id,
                finding_type="vendor_duplicated_by_misread_gstin",
                family=self.family,
                subject_type="vendor",
                subject_id=bad_id,
                severity="HIGH" if n_bills else "MEDIUM",
                title="This vendor is a duplicate created from a misread GSTIN",
                # One line. The comparison below already shows both GSTINs, which
                # characters differ, and which record is verified — repeating that
                # in prose is the reader doing the work twice.
                detail=(
                    f"{n_bills} bill{'s' if n_bills != 1 else ''} "
                    f"{'are' if n_bills != 1 else 'is'} stranded on this record and "
                    f"cannot be paid."
                    if n_bills else "No bills are attached to this record yet."
                ),
                deep_link=links.vendor(bad_id, tab="gstins", focus="gstin"),
                condition_hash=condition_hash("misread", bad_gstn, good_gstn, n_bills),
                detector=self.detector,
                source_service=self.source_service,
                money_at_risk=value or None,
                money_at_risk_inr=value or None,
                evidence=code_diff_evidence(
                    left_label="Read from the bill",
                    left_value=bad_gstn,
                    left_status="failed",
                    right_label="On the verified vendor",
                    right_value=good_gstn,
                    right_status="verified",
                    facts=[
                        ("Origin", (r["bad_source"] or "—").lower(), "vendor master"),
                        ("Bills attached", str(n_bills), str(r["good_bills"] or 0)),
                    ],
                    document=document_ref(
                        r["bad_upload"],
                        label=r["bad_invoice_no"] or "the uploaded bill",
                        highlight=bad_gstn,
                    ),
                ),
                entities=(
                    ("vendor", bad_id, "subject", r["bad_name"]),
                    ("vendor", good_id, "counterparty", r["bad_name"]),
                ),
                audience_roles=aud.roles,
                required_capability="edit",
                actions=[
                    # The one write the hub performs on a vendor, and it is offered
                    # only because the detector above is narrow enough to be sure.
                    action(
                        "merge_misread_vendor",
                        f"Move {n_bills} bill{'s' if n_bills != 1 else ''} to the verified vendor"
                        if n_bills else "Retire this duplicate record",
                        kind="safe",
                        endpoint="/api/review/act/merge-misread-vendor",
                        capability="edit",
                        # The confirmation shows the GSTIN change itself, so this
                        # only has to say what else moves.
                        preview=(
                            f"{n_bills} bill{'s' if n_bills != 1 else ''} and everything else "
                            "on this record move to the verified vendor; this one is retired."
                            if n_bills else "This duplicate record is retired."
                        ),
                        # Shown before anything is written. The change is the one
                        # thing the user has to agree to, so it is rendered as the
                        # same before/after they were just looking at rather than
                        # described again in prose.
                        confirm={
                            "title": "Move to the verified vendor",
                            "change": {
                                "label": "Vendor GSTIN",
                                "from": bad_gstn,
                                "to": good_gstn,
                            },
                            "impact": [
                                {"label": "Bills moved", "value": str(n_bills)},
                                {"label": "Vendor", "value": r["bad_name"]},
                                {"label": "This record", "value": "Deactivated"},
                            ],
                            "items": ([f"{r['bad_invoice_no']}"] if r["bad_invoice_no"] else []),
                        },
                        body={"source_supplier_id": bad_id, "target_supplier_id": good_id},
                    ),
                    action("open_vendor", "Compare the two records", kind="navigate",
                           href=links.vendor(bad_id, tab="gstins", focus="gstin")),
                ],
            )
