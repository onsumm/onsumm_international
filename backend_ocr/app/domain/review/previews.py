"""What a finding can show you *without* leaving the hub.

A stalled bill raises three questions in the order a controller actually asks
them: who is sitting on it, what is this bill, and does the paper say what we
think it says. Answering any of them used to mean "Open the bill", which ends
the triage session — the queue is gone, the scroll position with it, and in
practice the visit too.

So the drawer offers them as previews instead. Two rules shape this module:

* **The server decides what can be previewed, the UI only renders it.** The hub
  never switches on finding_type (see ReviewHubPage's header), so the menu is
  computed here from what the finding *is about* — a finding whose subject is a
  bill can always show its chain, its key fields and its source page — and
  arrives as a list the drawer turns into buttons. A finding type invented
  tomorrow gets the same previews the moment its subject is a bill.

* **Previews are read fresh, never stored.** A finding row is a snapshot from
  the last scan; an approval that happened twenty minutes ago must show up here,
  or the preview is just a prettier way of being out of date.

Every payload is an ordinary evidence shape (`timeline`, `fields`) so
EvidenceView renders it with no new code.
"""
from __future__ import annotations

from decimal import Decimal

# Steps that ended the bill's progress rather than advancing it. Rendered as
# blocked so a send-back reads differently from an approval at a glance.
_SETBACK_ACTIONS = {"reject", "rejected", "send_back", "sent_back", "revert", "reverted"}


def _humanise(v: str | None) -> str:
    return (v or "").replace("_", " ").strip().title()


def _money(value, currency: str | None):
    """Numbers stay numbers for INR so the client formats and aligns them.

    Anything else is rendered here with its code attached: the client's money
    formatter is rupee-only, and a dollar bill shown as ₹ is worse than no
    preview at all.
    """
    if value is None:
        return None
    cur = (currency or "INR").strip().upper()
    if cur == "INR":
        return float(value)
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{cur} {amount:,}"


def bill_subject_id(row) -> str | None:
    """The bill a finding is about, or None if it isn't about one."""
    if row.get("subject_type") == "bill" and row.get("subject_id"):
        return str(row["subject_id"])
    return None


def vendor_subject_id(row) -> str | None:
    """The vendor a finding is about, or None if it isn't about one."""
    if row.get("subject_type") == "vendor" and row.get("subject_id"):
        return str(row["subject_id"])
    return None


def vendor_row(cur, tenant: str, vendor_id: str):
    """The vendor as its own card shows it, bank account included.

    The account comes from supplier_bank_accounts with the same precedence the
    payment path uses — a verified row first, then the primary — because the
    question a bank finding raises is "what would we actually pay against". The
    dim_suppliers.bank_* columns are the single-account original and are read
    only when no row exists.
    """
    cur.execute(
        """
        SELECT s.name, s.status, s.gstn, s.pan, s.category, s.email,
               s.payment_terms_days, s.source,
               COALESCE(b.account_number, s.bank_account) AS account_number,
               COALESCE(b.ifsc, s.bank_ifsc)              AS ifsc,
               COALESCE(b.bank_name, s.bank_name)         AS bank_name,
               b.name_at_bank,
               COALESCE(b.verified, s.bank_verified)      AS account_verified,
               (SELECT COUNT(*) FROM fact_invoices fi
                 WHERE fi.supplier_id = s.supplier_id AND fi.tenant_id = s.tenant_id
                   AND fi.soft_status = 'active')                       AS total_bills,
               (SELECT COALESCE(SUM(fi.total_amount), 0) FROM fact_invoices fi
                 WHERE fi.supplier_id = s.supplier_id AND fi.tenant_id = s.tenant_id
                   AND fi.soft_status = 'active')                       AS total_billed,
               (SELECT COALESCE(SUM(fi.outstanding_amount), 0) FROM fact_invoices fi
                 WHERE fi.supplier_id = s.supplier_id AND fi.tenant_id = s.tenant_id
                   AND fi.soft_status = 'active'
                   AND fi.payment_status IS DISTINCT FROM 'paid')       AS outstanding
          FROM dim_suppliers s
          LEFT JOIN LATERAL (
              SELECT account_number, ifsc, bank_name, name_at_bank, verified
                FROM supplier_bank_accounts b
               WHERE b.supplier_id = s.supplier_id AND b.tenant_id = s.tenant_id
               ORDER BY b.verified DESC, b.is_primary DESC, b.created_at DESC
               LIMIT 1
          ) b ON TRUE
         WHERE s.supplier_id::text = %s AND s.tenant_id::text = %s
        """,
        (vendor_id, str(tenant)),
    )
    return cur.fetchone()


def bill_row(cur, tenant: str, bill_id: str):
    cur.execute(
        """
        SELECT fi.invoice_number, fi.invoice_date, fi.due_date, fi.status::text AS status,
               fi.currency_code, fi.subtotal, fi.tax_amount, fi.total_amount,
               fi.amount_paid, fi.outstanding_amount, fi.department, fi.category,
               fi.payment_status, fi.upload_id,
               s.name          AS supplier_name,
               sb.gstin_seller AS supplier_gstin,
               sb.tds_amount   AS tds_amount,
               sb.net_payable  AS net_payable
          FROM fact_invoices fi
          LEFT JOIN dim_suppliers s
                 ON s.supplier_id = fi.supplier_id AND s.tenant_id = fi.tenant_id
          LEFT JOIN stg_bills sb
                 ON COALESCE(fi.stg_bill_id, fi.archived_stg_bill_id) = sb.stg_bill_id
                AND sb.tenant_id = fi.tenant_id
         WHERE fi.invoice_id::text = %s AND fi.tenant_id::text = %s
        """,
        (bill_id, str(tenant)),
    )
    return cur.fetchone()


# ── The menu ─────────────────────────────────────────────────────────────────

def previews_for(cur, tenant: str, row, evidence: dict | None) -> list[dict]:
    """The previews this finding can offer, in the order the questions get asked.

    `document` carries its reference inline because the drawer can render it
    straight away; the others name an id the drawer fetches on demand, so a
    finding nobody expands costs nothing.
    """
    out: list[dict] = []
    evidence = evidence if isinstance(evidence, dict) else {}
    bill_id = bill_subject_id(row)
    bill = bill_row(cur, tenant, bill_id) if bill_id else None

    if bill:
        out.append({"id": "chain", "label": "Approval chain", "kind": "approval_chain"})
        out.append({"id": "details", "label": "Key details", "kind": "fields"})

    # A finding about a vendor is usually a question about the vendor record —
    # is this account really unverified, is this GSTIN really the one on file —
    # and the answer lives on a card two clicks away in another screen.
    if vendor_subject_id(row):
        out.append({"id": "vendor", "label": "Vendor", "kind": "fields"})

    # The detector's own reference wins over the one derived from the bill. It
    # is the curated one: it names the page and the string worth finding on it,
    # and for a misread that highlight is most of the value. Falling back to the
    # bill's upload is what gives a finding that never mentioned a document —
    # a stalled approval, say — a source page at all.
    doc = evidence.get("document_ref")
    if not (isinstance(doc, dict) and doc.get("upload_id")):
        doc = (
            {"upload_id": str(bill["upload_id"]),
             "label": bill.get("invoice_number") or "The bill"}
            if bill and bill.get("upload_id")
            else None
        )

    if doc:
        out.append({
            "id": "document",
            # A finding about the bill itself gets the plain name; one about a
            # vendor has to say which document it means and why it is here.
            "label": "Bill PDF" if bill_id else "Preview bill",
            "kind": "document",
            "document": doc,
        })

    return out


# ── The payloads ─────────────────────────────────────────────────────────────

def _chain_preview(cur, tenant: str, bill_id: str, bill) -> dict:
    """The chain exactly as the Bills screen draws it, plus the activity under it.

    Two payloads, because the stepper and the activity list answer different
    questions. `chain` is the raw audit rows the shared ApprovalStepper needs to
    work out which node is done, active or sent back — it owns that logic and
    duplicating it here is how the two drawings would drift apart. `activity` is
    the same rows humanised for reading: who did what, when, and what they said.
    """
    cur.execute(
        """
        SELECT id, step, actor_role, actor_name, action, comment, created_at, target_step
          FROM bill_approval_chain
         WHERE invoice_id::text = %s AND tenant_id::text = %s
         ORDER BY created_at ASC, id ASC
        """,
        (bill_id, str(tenant)),
    )
    rows = cur.fetchall() or []

    steps = []
    for r in rows:
        action = (r.get("action") or "").lower()
        who = r.get("actor_name") or _humanise(r.get("actor_role")) or "System"
        role = _humanise(r.get("actor_role"))
        steps.append({
            "label": _humanise(r.get("action")) or "Step",
            "actor": f"{who} · {role}" if role and role.lower() not in who.lower() else who,
            "at": r["created_at"].isoformat() if r.get("created_at") else None,
            "note": r.get("comment") or None,
            "state": "blocked" if action in _SETBACK_ACTIONS else "done",
        })

    status = (bill.get("status") or "") if bill else ""
    # Where it is sitting right now. Without this the audit ends at the last
    # thing that happened, which reads as though the bill is finished.
    if status.upper().startswith("PENDING"):
        steps.append({
            "label": _humanise(status),
            "actor": None,
            "at": None,
            "note": "Waiting here now.",
            "state": "current",
        })

    note = "" if steps else "Nothing has been recorded against this bill yet."
    return {
        "kind": "approval_chain",
        # Verbatim, for the stepper. Every field it reads, nothing it doesn't.
        "chain": [
            {"id": r.get("id"), "step": r.get("step"),
             "actor_role": r.get("actor_role"), "actor_name": r.get("actor_name"),
             "action": r.get("action"), "comment": r.get("comment"),
             "target_step": r.get("target_step"),
             "created_at": r["created_at"].isoformat() if r.get("created_at") else None}
            for r in rows
        ],
        "status": status,
        # The chain is configured per department, so the stepper needs it to ask
        # for the right one — the default chain would draw the wrong steps.
        "department": (bill or {}).get("department"),
        "activity": {"kind": "timeline", "steps": steps, "note": note},
    }


def _details_preview(bill) -> dict:
    """The bill in one screen — enough to judge the finding without opening it."""
    cur_code = bill.get("currency_code")
    pairs = [
        ("Bill number", bill.get("invoice_number")),
        ("Vendor", bill.get("supplier_name")),
        ("Vendor GSTIN", bill.get("supplier_gstin")),
        ("Bill date", bill.get("invoice_date")),
        ("Due date", bill.get("due_date")),
        ("Status", _humanise(bill.get("status"))),
        ("Payment status", _humanise(bill.get("payment_status"))),
        ("Department", bill.get("department")),
        ("Category", bill.get("category")),
        ("Subtotal", _money(bill.get("subtotal"), cur_code)),
        ("Tax amount", _money(bill.get("tax_amount"), cur_code)),
        ("TDS amount", _money(bill.get("tds_amount"), cur_code)),
        ("Total amount", _money(bill.get("total_amount"), cur_code)),
        ("Paid", _money(bill.get("amount_paid"), cur_code)),
        ("Outstanding", _money(bill.get("outstanding_amount"), cur_code)),
    ]
    fields = [
        {"label": k, "value": v.isoformat() if hasattr(v, "isoformat") else v}
        for k, v in pairs
        if v not in (None, "", "—")
    ]
    return {"kind": "fields", "fields": fields, "note": ""}


def _vendor_preview(vendor) -> dict:
    """The vendor card's substance, in one pane.

    The bank block is here rather than behind a second click because these
    findings are about the bank block: a user reading "never verified" wants to
    see the account that claim is about, in the same glance.
    """
    verified = vendor.get("account_verified")
    pairs = [
        ("Vendor", vendor.get("name")),
        ("KYB status", _humanise(vendor.get("status"))),
        ("GSTIN", vendor.get("gstn")),
        ("PAN", vendor.get("pan")),
        ("Category", vendor.get("category")),
        ("Payment terms", (f"{vendor['payment_terms_days']} days"
                           if vendor.get("payment_terms_days") is not None else None)),
        ("Bank name", vendor.get("bank_name")),
        ("Account number", vendor.get("account_number")),
        ("IFSC", vendor.get("ifsc")),
        ("Name at bank", vendor.get("name_at_bank")),
        ("Account verified",
         "Yes" if verified is True else ("No" if verified is False else "Could not be checked")),
        ("Total bills", vendor.get("total_bills")),
        ("Total billed", _money(vendor.get("total_billed"), "INR")),
        ("Outstanding", _money(vendor.get("outstanding"), "INR")),
    ]
    fields = [{"label": k, "value": v} for k, v in pairs if v not in (None, "", "—")]
    return {"kind": "fields", "fields": fields, "note": ""}


def build_preview(cur, tenant: str, row, evidence: dict | None, preview_id: str) -> dict | None:
    """One preview's payload, or None if this finding doesn't offer it.

    Returning None rather than raising keeps the caller's 404 in one place.
    """
    evidence = evidence if isinstance(evidence, dict) else {}

    if preview_id == "document":
        for p in previews_for(cur, tenant, row, evidence):
            if p["id"] == "document":
                return p
        return None

    if preview_id == "vendor":
        vendor_id = vendor_subject_id(row)
        vendor = vendor_row(cur, tenant, vendor_id) if vendor_id else None
        if not vendor:
            return None
        return {"id": "vendor", "label": "Vendor", "kind": "fields",
                "evidence": _vendor_preview(vendor)}

    bill_id = bill_subject_id(row)
    if not bill_id:
        return None
    bill = bill_row(cur, tenant, bill_id)
    if not bill:
        return None

    if preview_id == "chain":
        return {"id": "chain", "label": "Approval chain", "kind": "approval_chain",
                "evidence": _chain_preview(cur, tenant, bill_id, bill)}
    if preview_id == "details":
        return {"id": "details", "label": "Key details", "kind": "fields",
                "evidence": _details_preview(bill)}
    return None
