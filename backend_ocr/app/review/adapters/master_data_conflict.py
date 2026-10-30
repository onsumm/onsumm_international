"""Comm-extracted claims vs. the vendor master — always a Review Hub finding,
never a silent write.

Nothing this adapter reads off an email or a contract ever touches
`dim_suppliers` / `supplier_gstins` / `supplier_bank_accounts` / `supplier_contacts`
on its own. Every actionable claim becomes a finding with a `keep_new` action
that calls the endpoint already owning that write — an admin has to click it,
here in the Review Hub or from the document's own claims panel, which fetches
the same finding and executes the same action. This replaced an earlier
Phase 0+1 version that auto-applied an empty PAN/GSTIN or a brand-new bank
account with no human in the loop — safe on paper (never overwrites a
verified value), but a misread phone number auto-filed as a bank account is
exactly the failure mode "safe" was supposed to rule out. Nothing here writes
until someone says so.

`vendor_bank`, `vendor_pan`, `vendor_gstin` and `payment_term` are all
single-claim-per-value fields for this purpose (a bank account is "multi-valid"
in the sense that a vendor can have more than one on file, but a given claim
still resolves to one finding: "add this account" or "it's already there").
`vendor_contact` behaves the same way, keyed on email instead of a regex-shaped
value. `bill_line_item` stays out of this adapter's scope — contract_bill_variance.py
owns that comparison.

`CLAIM_SCHEMA` (comm_extract.py) has no `target_ref` to link sibling claims
from one extraction together, so a bank change or a contact that arrives as
several claims — one `field="account_number"`, one `field="ifsc"`, one
`field="name"` beside a `field="email"` — has to be re-associated here by
scanning the same extraction's claims for a matching `scope`. That is the one
place this adapter looks sideways within an extraction rather than at one
claim in isolation.

Every claim-status transition writes back into `comm_extractions.claims`
(the whole jsonb array, mutated in Python and written back once per
extraction) rather than a child table — same reasoning as the table's own
docstring: a partially-applied extraction needs no join to render. A claim
sits at status `CONFLICTED` for as long as it is awaiting an admin's approval
— re-used from the genuine-mismatch case rather than a new status, since both
mean the same thing here: "this does not already match what's on file, and
nothing has written it yet." Once the admin clicks `keep_new`, the endpoint it
calls updates the real row; the next `scan()` pass re-reads that row, sees it
now agrees with the claim, and flips the claim to `APPLIED` — the same
self-resolving loop `sweep_autoresolve` already relies on elsewhere, not a
callback bolted on for this feature.
"""
from __future__ import annotations

import json
import re

from .. import links
from ..routing import vendor_audience
from ..types import FindingDraft, action, condition_hash, diff_evidence, jsonb
from .base import FindingAdapter
from .vendor_identity import _IFSC_PATTERN

# Same shape vendor_gstins.py's POST handler validates against, reused here so
# a malformed extraction never gets as far as offering a write.
_PAN_PATTERN = r"^[A-Z]{5}[0-9]{4}[A-Z]$"
_GSTIN_PATTERN = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$"
_ACCOUNT_PATTERN = r"^[0-9A-Za-z]{6,20}$"
_PAYMENT_TERM_PATTERN = r"^[0-9]{1,4}$"

# One entry per single-valid scope: which dim_suppliers column it maps to, how
# to validate/normalise the claimed value, which real endpoint already owns
# writing it, and how to build that endpoint's request body. Adding a new
# single-valid scope is adding one entry here, not a new branch of if/else.
_SINGLE_VALID_CONFIG = {
    "vendor_pan": {
        "field": "pan", "pattern": _PAN_PATTERN, "label": "PAN", "upper": True, "unit": "",
        "endpoint": lambda sid: f"/api/vendors/{sid}", "method": "PATCH",
        "body": lambda v: {"pan": v}, "tab": "details", "focus": "pan",
    },
    "vendor_gstin": {
        "field": "gstin", "pattern": _GSTIN_PATTERN, "label": "Primary GSTIN", "upper": True, "unit": "",
        "endpoint": lambda sid: f"/api/vendors/{sid}/gstins", "method": "POST",
        "body": lambda v: {"gstin": v, "make_primary": True}, "tab": "gstins", "focus": "gstin",
    },
    "payment_term": {
        "field": "payment_terms_days", "pattern": _PAYMENT_TERM_PATTERN, "label": "Payment terms",
        "upper": False, "unit": " days",
        "endpoint": lambda sid: f"/api/vendors/{sid}", "method": "PATCH",
        "body": lambda v: {"payment_terms_days": int(v)}, "tab": "details", "focus": "payment_terms",
    },
}
SINGLE_VALID_SCOPES = tuple(_SINGLE_VALID_CONFIG)
MULTI_VALID_SCOPES = ("vendor_bank",)
CONTACT_SCOPES = ("vendor_contact",)


def _verification_for(claim: dict) -> str:
    """VERIFIED once a human has confirmed the extracted value.

    `lane_for()` routes anything UNVERIFIED to the 'suspected' lane regardless
    of family, and the hub's lane filter excludes UNVERIFIED from every named
    lane — so while this was hardcoded UNVERIFIED, a vendor-identity conflict
    could never appear in the compliance lane it belongs to. UNVERIFIED is
    still right for a claim nobody has looked at: whether to trust the
    *message* is what claim verification exists to decide. But once someone
    approved the reading in the document panel and the value still disagrees
    with the master record, the disagreement is a plain comparison of two
    values we hold — a fact, not a guess.
    """
    return "VERIFIED" if claim.get("verified_by") else "UNVERIFIED"


def _norm(value) -> str:
    """Whitespace- and case-insensitive comparison, per the brief: 'ABC 123' and
    'abc123' are the same value, not a conflict. Accepts non-strings (e.g. an
    int payment_terms_days) so a single normaliser works for every scope."""
    return "".join(str(value if value is not None else "").split()).upper()


class MasterDataConflictAdapter(FindingAdapter):
    detector = "adapter:master_data_conflict"
    family = "vendor_identity"
    source_service = "email_sending"

    async def scan(self, conn, tenant_id: str, limit: int):
        rows = await conn.fetch(
            """
            SELECT extraction_id, supplier_id, contact_id, thread_id, source_type,
                   source_id, claims
              FROM comm_extractions
             WHERE tenant_id = $1::uuid
               AND EXISTS (
                   SELECT 1 FROM jsonb_array_elements(claims) c
                    WHERE c->>'status' IN ('PENDING', 'CONFLICTED')
               )
             ORDER BY created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

        aud = vendor_audience(capability="edit")
        # Several extractions in one pass can name the same vendor; re-reading
        # dim_suppliers per claim would be wasted work, and — more importantly —
        # a second claim in the same pass must see the first claim's write
        # (e.g. two extractions both claiming an empty PAN) rather than both
        # racing to read the same stale snapshot.
        vendor_cache: dict[str, dict | None] = {}

        for row in rows:
            extraction_id = str(row["extraction_id"])
            supplier_id = str(row["supplier_id"]) if row["supplier_id"] else None
            contact_id = str(row["contact_id"]) if row["contact_id"] else None
            is_contract = row["source_type"] == "contract"
            source_label = "contract" if is_contract else "email"
            # The hub filters findings by the tenant's licensed services
            # (_entitlement_clause in routers/review_hub.py). A conflict read
            # off a contract document has nothing to do with email sending, and
            # tagging it that way let an unrelated entitlement hide it from the
            # hub entirely — which is exactly how a real PAN/GSTIN conflict
            # ended up flagged nowhere.
            source_service = "vendors" if is_contract else self.source_service
            claims = jsonb(row["claims"], default=[]) or []
            if not supplier_id or not claims:
                continue

            if supplier_id not in vendor_cache:
                vendor_cache[supplier_id] = await self._load_vendor(conn, tenant_id, supplier_id)
            vendor = vendor_cache[supplier_id]
            if vendor is None:
                continue  # vendor merged/deleted since the message arrived

            dirty = False
            bank_accounts_norm: set[str] | None = None
            contact_emails_norm: set[str] | None = None

            for idx, claim in enumerate(claims):
                if not isinstance(claim, dict):
                    continue
                status = claim.get("status")
                if status not in ("PENDING", "CONFLICTED"):
                    continue
                scope = claim.get("scope")

                # ── vendor_bank: multi-valid — a new account always needs a
                # click, an account already on file is a silent no-op. ───────
                if scope in MULTI_VALID_SCOPES:
                    if claim.get("field") not in (None, "account_number"):
                        continue  # handled together with its account_number anchor below
                    if bank_accounts_norm is None:
                        bank_accounts_norm = await self._bank_accounts_norm(
                            conn, tenant_id, supplier_id
                        )
                    siblings = [
                        c for c in claims
                        if isinstance(c, dict) and c is not claim
                        and c.get("scope") == "vendor_bank"
                    ]
                    ifsc = next((c.get("new_value") for c in siblings
                                 if c.get("field") == "ifsc"), None)
                    bank_name = next((c.get("new_value") for c in siblings
                                       if c.get("field") == "bank_name"), None)

                    account, new_status = self._resolve_bank_claim(
                        claim, ifsc, bank_name, bank_accounts_norm,
                    )
                    if new_status != status:
                        claim["status"] = new_status
                        dirty = True
                        for sib in siblings:
                            if sib.get("status") in ("PENDING", "CONFLICTED"):
                                sib["status"] = new_status
                                dirty = True

                    if new_status != "CONFLICTED":
                        continue

                    ifsc_norm = (ifsc or "").strip().upper() or None
                    vendor_name = vendor.get("name") or "this vendor"
                    actions = [
                        action(
                            "keep_new", "Add this bank account", kind="safe",
                            endpoint=f"/api/vendors/{supplier_id}/bank-accounts", method="POST",
                            body={"account_number": account, "ifsc": ifsc_norm,
                                  "bank_name": (bank_name or "").strip() or None},
                            confirm={
                                "title": f"Add a bank account for {vendor_name}",
                                "change": {"label": "Bank account", "from": "—",
                                           "to": f"{account}" + (f" (IFSC {ifsc_norm})" if ifsc_norm else "")},
                            },
                        ),
                        action("open_vendor", "Review on vendor page", kind="navigate",
                               href=links.vendor(supplier_id, tab="details", focus="bank")),
                    ]
                    if contact_id:
                        quote = claim.get("evidence_quote") or ""
                        actions.append(action(
                            "notify_vendor", "Ask the vendor to confirm", kind="notify",
                            endpoint="/api/review/act/notify", method="POST",
                            body={
                                "destination_type": "vendor_contact",
                                "contact_id": contact_id,
                                "subject": f"Confirming the bank account on file for {vendor_name}",
                                "message": (
                                    f"Hi,\n\nWe received a {source_label} stating your bank "
                                    f"account for payments is {account}"
                                    f"{f' (IFSC {ifsc_norm})' if ifsc_norm else ''}. This is a "
                                    f"new account for us — could you confirm this is correct "
                                    f"before we add it?\n\nQuoted: \"{quote}\"\n\nThanks."
                                ),
                            },
                            confirm={
                                "title": "Ask the vendor to confirm",
                                "impact": [{"label": "About", "value": f"{vendor_name}'s bank account"}],
                                "items": [quote] if quote else [],
                            },
                        ))

                    yield FindingDraft(
                        tenant_id=tenant_id,
                        finding_type="vendor_bank_new",
                        family=self.family,
                        subject_type="vendor",
                        subject_id=supplier_id,
                        severity="HIGH",  # a wrong payee account is a fraud vector, not a data-quality nit
                        title=f"A {source_label} names a new bank account for {vendor_name}",
                        detail=(
                            f'Quoted: "{claim.get("evidence_quote") or ""}". This account is '
                            "not yet on file for this vendor — nothing has been added."
                        ),
                        deep_link=links.vendor(supplier_id, tab="details", focus="bank"),
                        condition_hash=condition_hash("new_bank_account", account, ifsc_norm or ""),
                        detector=self.detector,
                        source_service=source_service,
                        verification=_verification_for(claim),
                        confidence=float(claim.get("confidence") or 0.6),
                        key_qualifier=f"{extraction_id}:{idx}",
                        evidence=diff_evidence(
                            "On file", f"Claimed by {source_label}",
                            [("Bank account", "— not on file —",
                              account + (f" (IFSC {ifsc_norm})" if ifsc_norm else ""))],
                        ),
                        actions=actions,
                        audience_roles=aud.roles,
                        required_capability="edit",
                        entities=(("vendor", supplier_id, "subject", vendor.get("name")),),
                    )
                    continue

                # ── vendor_contact: a named POC not already in our contacts. ─
                if scope in CONTACT_SCOPES:
                    if claim.get("field") != "email":
                        continue  # handled together with its email anchor below
                    email = (claim.get("new_value") or "").strip().lower()
                    if not email or "@" not in email:
                        new_status = "FAILED"
                    else:
                        if contact_emails_norm is None:
                            contact_emails_norm = await self._contact_emails_norm(
                                conn, tenant_id, supplier_id
                            )
                        new_status = "APPLIED" if email in contact_emails_norm else "CONFLICTED"

                    siblings = [
                        c for c in claims
                        if isinstance(c, dict) and c is not claim
                        and c.get("scope") == "vendor_contact"
                    ]
                    if new_status != status:
                        claim["status"] = new_status
                        dirty = True
                        for sib in siblings:
                            if sib.get("status") in ("PENDING", "CONFLICTED"):
                                sib["status"] = new_status
                                dirty = True

                    if new_status != "CONFLICTED":
                        continue

                    name = next((c.get("new_value") for c in siblings
                                 if c.get("field") == "name"), None) or "Vendor contact"
                    phone = next((c.get("new_value") for c in siblings
                                  if c.get("field") == "phone"), None)
                    vendor_name = vendor.get("name") or "this vendor"

                    yield FindingDraft(
                        tenant_id=tenant_id,
                        finding_type="vendor_contact_new",
                        family=self.family,
                        subject_type="vendor",
                        subject_id=supplier_id,
                        severity="LOW",
                        title=f"A new point of contact was found for {vendor_name}",
                        detail=(
                            f'"{name}" <{email}> was named in a vendor {source_label} and is '
                            "not yet in your contacts for this vendor."
                        ),
                        deep_link=links.vendor(supplier_id, tab="contacts"),
                        condition_hash=condition_hash("new_contact", email),
                        detector=self.detector,
                        source_service=source_service,
                        verification=_verification_for(claim),
                        confidence=float(claim.get("confidence") or 0.6),
                        key_qualifier=f"{extraction_id}:{idx}",
                        evidence=diff_evidence(
                            "On file", f"Found in {source_label}",
                            [("Contact", "— not on file —",
                              f"{name} <{email}>" + (f" · {phone}" if phone else ""))],
                        ),
                        actions=[
                            action(
                                "keep_new", "Add this contact", kind="safe",
                                endpoint=f"/api/vendors/{supplier_id}/contacts", method="POST",
                                body={"name": name, "role": "Contract contact",
                                      "email": email, "phone": phone},
                                confirm={
                                    "title": f"Add a contact for {vendor_name}",
                                    "change": {"label": "Contact", "from": "—",
                                               "to": f"{name} <{email}>"},
                                },
                            ),
                            action("open_vendor", "Review on vendor page", kind="navigate",
                                   href=links.vendor(supplier_id, tab="contacts")),
                        ],
                        audience_roles=aud.roles,
                        required_capability="edit",
                        entities=(("vendor", supplier_id, "subject", vendor.get("name")),),
                    )
                    continue

                if scope not in SINGLE_VALID_SCOPES:
                    continue  # bill_line_item — contract_bill_variance.py's job

                new_status, new_value = self._resolve_single_valid_claim(scope, claim, vendor)
                if new_status != status:
                    claim["status"] = new_status
                    dirty = True

                if new_status != "CONFLICTED":
                    continue

                cfg = _SINGLE_VALID_CONFIG[scope]
                field_label = cfg["label"]
                current_value = vendor.get(cfg["field"])
                unit = cfg["unit"]
                has_current = current_value not in (None, "")
                current_display = f"{current_value}{unit}" if has_current else "—"
                new_display = f"{new_value}{unit}"
                vendor_name = vendor.get("name") or "this vendor"

                # Only a claim REPLACING something is an either/or. Stamped on
                # the claim so the document panel can tell the two apart:
                # CONFLICTED alone cannot, since it also marks a brand-new bank
                # account or contact as awaiting approval, and offering "keep
                # current" against a value that does not exist is nonsense.
                stamped = current_display if has_current else None
                if claim.get("current_value") != stamped:
                    claim["current_value"] = stamped
                    dirty = True

                # Name both sides of the choice. "Use this payment terms" left
                # the reader working out for themselves which value that meant
                # and what happened to the other one; a conflict is an
                # either/or, so it reads as one.
                keep_new = action(
                    "keep_new", f"Keep the {source_label}'s {field_label.lower()} ({new_display})",
                    kind="safe",
                    endpoint=cfg["endpoint"](supplier_id), method=cfg["method"],
                    body=cfg["body"](new_value),
                    confirm={
                        "title": f"Update {vendor_name}'s {field_label.lower()}",
                        "change": {"label": field_label, "from": current_display, "to": new_display},
                    },
                )
                actions = [keep_new]
                # The other half of the either/or, and only offered where it can
                # actually be honoured: discarding the claim needs the document
                # it came from. Without this the only way to say "keep what we
                # have" was the hub's generic dismiss, which reads as filing the
                # question away rather than answering it.
                if is_contract and row["source_id"]:
                    actions.append(action(
                        "keep_current",
                        f"Keep current {field_label.lower()} ({current_display})",
                        kind="safe", emphasis="secondary",
                        endpoint=(f"/api/vendors/{supplier_id}/documents/"
                                  f"{row['source_id']}/claims/{idx}/discard"),
                        method="POST",
                        confirm={
                            "title": f"Keep {vendor_name}'s {field_label.lower()} as it is",
                            "change": {"label": field_label,
                                       "from": current_display, "to": current_display},
                        },
                    ))
                actions.append(
                    action("open_vendor", "Review on vendor page", kind="navigate",
                           href=links.vendor(supplier_id, tab=cfg["tab"], focus=cfg["focus"])),
                )
                if contact_id:
                    quote = claim.get("evidence_quote") or ""
                    actions.append(action(
                        "notify_vendor", "Ask the vendor to confirm", kind="notify",
                        endpoint="/api/review/act/notify", method="POST",
                        body={
                            "destination_type": "vendor_contact",
                            "contact_id": contact_id,
                            "subject": f"Confirming the {field_label.lower()} on file for {vendor_name}",
                            "message": (
                                f"Hi,\n\nWe received a {source_label} stating your "
                                f"{field_label.lower()} is {new_display}, which is different "
                                f"from what we currently have on file ({current_display}). "
                                f"Could you confirm this is correct before we update our "
                                f"records?\n\nQuoted: \"{quote}\"\n\nThanks."
                            ),
                        },
                        confirm={
                            "title": "Ask the vendor to confirm",
                            "impact": [{"label": "About", "value": f"{vendor_name}'s {field_label.lower()}"}],
                            "items": [quote] if quote else [],
                        },
                    ))

                has_current = current_value not in (None, "")
                yield FindingDraft(
                    tenant_id=tenant_id,
                    finding_type=f"{scope}_conflict",
                    family=self.family,
                    subject_type="vendor",
                    subject_id=supplier_id,
                    severity="MEDIUM",
                    title=(
                        f"A vendor {source_label} states a different {field_label.lower()} "
                        "than what's on file"
                        if has_current else
                        f"A vendor {source_label} states a {field_label.lower()} we don't have on file yet"
                    ),
                    detail=(
                        f'Quoted from the {source_label}: "{claim.get("evidence_quote") or ""}". '
                        + ("This does not match what is currently recorded for this vendor."
                           if has_current else "Nothing has been added yet — this needs a look first.")
                    ),
                    deep_link=links.vendor(supplier_id, tab=cfg["tab"], focus=cfg["focus"]),
                    condition_hash=condition_hash("conflict", scope, current_value, claim.get("new_value")),
                    detector=self.detector,
                    source_service=source_service,
                    # UNVERIFIED, not VERIFIED: the diff itself is a plain
                    # string comparison, but whether the *message* should be
                    # trusted at all is exactly what claim verification
                    # (§5.2.3 of the HLD, not built in this phase) exists to
                    # check — defaulting to VERIFIED here would overstate it.
                    verification=_verification_for(claim),
                    confidence=float(claim.get("confidence") or 0.6),
                    key_qualifier=f"{extraction_id}:{idx}",
                    evidence=diff_evidence(
                        "Current", f"Claimed by {source_label}",
                        [(field_label, current_display, new_display)],
                    ),
                    actions=actions,
                    audience_roles=aud.roles,
                    required_capability="edit",
                    entities=(("vendor", supplier_id, "subject", vendor.get("name")),),
                )

            if dirty:
                await conn.execute(
                    "UPDATE comm_extractions SET claims = $2::jsonb WHERE extraction_id = $1::uuid",
                    extraction_id, json.dumps(claims),
                )

    # ── Vendor snapshot ──────────────────────────────────────────────────────

    async def _load_vendor(self, conn, tenant_id: str, supplier_id: str) -> dict | None:
        row = await conn.fetchrow(
            """
            SELECT s.name, s.pan, s.payment_terms_days, COALESCE(pg.gstin, s.gstn) AS gstin
              FROM dim_suppliers s
              LEFT JOIN LATERAL (
                  SELECT gstin FROM supplier_gstins
                   WHERE supplier_id = s.supplier_id AND tenant_id = s.tenant_id
                     AND is_primary
                   LIMIT 1
              ) pg ON TRUE
             WHERE s.supplier_id = $1::uuid AND s.tenant_id = $2::uuid
            """,
            supplier_id, tenant_id,
        )
        if row is None:
            return None
        return {
            "name": row["name"], "pan": row["pan"], "gstin": row["gstin"],
            "payment_terms_days": row["payment_terms_days"],
        }

    # ── vendor_bank (multi-valid) ────────────────────────────────────────────

    async def _bank_accounts_norm(self, conn, tenant_id: str, supplier_id: str) -> set[str]:
        rows = await conn.fetch(
            "SELECT account_number FROM supplier_bank_accounts "
            " WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid",
            tenant_id, supplier_id,
        )
        return {_norm(r["account_number"]) for r in rows}

    def _resolve_bank_claim(
        self, claim: dict, ifsc: str | None, bank_name: str | None, existing_norm: set[str],
    ) -> tuple[str, str]:
        """Validate only — never writes. Returns (normalised account, status):
        FAILED (malformed), APPLIED (already on file, no-op) or CONFLICTED
        (new — needs an admin's click before anything is added)."""
        account = (claim.get("new_value") or "").strip()
        if not account:
            return account, "FAILED"
        if _norm(account) in existing_norm:
            return account, "APPLIED"

        ifsc = (ifsc or "").strip().upper() or None
        if ifsc and not re.match(_IFSC_PATTERN, ifsc):
            return account, "FAILED"
        if not re.match(_ACCOUNT_PATTERN, account):
            return account, "FAILED"
        return account, "CONFLICTED"

    # ── vendor_contact ───────────────────────────────────────────────────────

    async def _contact_emails_norm(self, conn, tenant_id: str, supplier_id: str) -> set[str]:
        rows = await conn.fetch(
            "SELECT email FROM supplier_contacts "
            " WHERE tenant_id = $1::uuid AND supplier_id = $2::uuid AND email IS NOT NULL",
            tenant_id, supplier_id,
        )
        return {(r["email"] or "").strip().lower() for r in rows}

    # ── vendor_pan / vendor_gstin / payment_term (single-valid) ─────────────

    def _resolve_single_valid_claim(
        self, scope: str, claim: dict, vendor: dict
    ) -> tuple[str, str]:
        """Validate and compare only — never writes. Returns (status,
        normalised new_value): FAILED (malformed), APPLIED (already matches,
        no-op) or CONFLICTED (empty or different on file — needs a click)."""
        cfg = _SINGLE_VALID_CONFIG[scope]
        raw = (claim.get("new_value") or "").strip()
        new_value = raw.upper() if cfg["upper"] else raw
        if not new_value:
            return "FAILED", new_value
        if cfg["pattern"] and not re.match(cfg["pattern"], new_value):
            return "FAILED", new_value

        current = vendor.get(cfg["field"])
        if current not in (None, "") and _norm(current) == _norm(new_value):
            return "APPLIED", new_value
        return "CONFLICTED", new_value
