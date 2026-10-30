"""A vendor's Activity timeline: database rows in, plain-English events out.

Everything here is pure (no DB, no request context) so the wording, masking,
kind mapping and paging can be unit-tested on their own. The SQL that feeds
these functions lives in routers/vendor_activity.py; the writer for the
actions that had no audit trail of their own is core/vendor_activity_log.py.

Event shape (the frontend codes against this exactly):

    {"id", "at", "kind", "action", "title", "detail", "actor", "actor_role",
     "invoice_id", "bill_no", "amount", "changes"}
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from .payments.receipt import group_inr

log = logging.getLogger(__name__)

MAX_LIMIT = 200
DEFAULT_LIMIT = 100


# ── Formatting ───────────────────────────────────────────────────────────────

def inr(amount: Any) -> str | None:
    """₹2,53,686 — Indian grouping, paise only when there are any."""
    if amount is None:
        return None
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None
    text = group_inr(value)
    if text.endswith(".00"):
        text = text[:-3]
    return "₹" + text


def payout_money(amount: Any, currency: Any = None, rail: Any = None) -> str | None:
    """A payout in what it was paid in: ₹ for INR, '1 USDC' for a USDC payout of
    a USD bill, 'EUR 12.50' otherwise. The amount is in the bill's currency."""
    cur = (_s(currency) or "INR").upper()
    if cur == "INR":
        return inr(amount)
    value = _num(amount)
    if value is None:
        return None
    text = f"{value:,.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    if cur == "USD" and (_s(rail) or "").upper() == "USDC":
        return f"{text} USDC"
    return f"{cur} {text}"


def mask_account(number: Any) -> str:
    """'50100123456789' -> '••••6789'. Never shows more than the last four."""
    digits = re.sub(r"\s+", "", str(number or ""))
    if not digits:
        return "••••"
    return "••••" + digits[-4:]


# Indian bank account numbers run 9-18 digits. Not after ₹ or a separator, so
# an amount ('₹1000000', '10,20,30,405') is left alone.
_LONG_NUMBER = re.compile(r"(?<![₹\d.,])\d{9,}(?![.,]?\d)")


def mask_numbers(text: Any) -> Any:
    """Mask every account-number-like run (9+ digits) in free text to its last four.

    Used on values that may carry an account number (a Review snapshot's
    bank_account / bank_accounts, a reason someone typed)."""
    if not isinstance(text, str):
        return text
    return _LONG_NUMBER.sub(lambda m: mask_account(m.group(0)), text)


def iso(value: Any) -> str | None:
    """ISO-8601 with a timezone; a naive timestamp is taken as UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


_SPACED_OFFSET = re.compile(r" (\d{2}:?\d{2})$")


def parse_ts(value: Any) -> datetime | None:
    """A timestamp from a row or a query string. An unencoded '+' in a URL
    arrives as a space ('…10:31:00 00:00'), so that is read back as '+'."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = _SPACED_OFFSET.sub(r"+\1", str(value).strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _s(v: Any) -> str | None:
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def _humanise(code: Any) -> str:
    """'per_bill_threshold' -> 'Per bill threshold'."""
    t = str(code or "").replace("_", " ").strip()
    return t[:1].upper() + t[1:] if t else ""


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ── Kinds ────────────────────────────────────────────────────────────────────

_APPROVAL_ACTIONS = {"bill_approved", "bill_rejected", "bill_sent_back", "bill_edited", "bill_submitted"}


def kind_for(action: str) -> str:
    if action in _APPROVAL_ACTIONS:
        return "approval"
    if action.startswith("bill_"):
        return "bill"
    if action.startswith("payment_"):
        return "payment"
    if action.startswith(("gst_", "bank_", "gstin_", "tds_")):
        return "compliance"
    if action in ("vendor_updated", "review_change"):
        return "change"
    if action.startswith("document_"):
        return "document"
    if action.startswith("contact_"):
        return "contact"
    if action.startswith("email_"):
        return "email"
    return "change"


def make_event(source: str, pk: Any, at: Any, action: str, title: str, *,
               detail: Any = None, actor: Any = None, actor_role: Any = None,
               invoice_id: Any = None, bill_no: Any = None, amount: Any = None,
               changes: list | None = None, dedup_key: tuple | None = None) -> dict | None:
    stamp = iso(at)
    if stamp is None:
        return None
    ev = {
        "id": f"src:{source}:{pk}",
        "at": stamp,
        "kind": kind_for(action),
        "action": action,
        "title": title,
        "detail": _s(detail),
        "actor": _s(actor),
        "actor_role": _s(actor_role),
        "invoice_id": str(invoice_id) if invoice_id else None,
        "bill_no": _s(bill_no),
        "amount": _num(amount),
        "changes": changes or None,
    }
    if dedup_key:
        ev["_dedup"] = dedup_key
    return ev


def _bill_label(bill_no: Any) -> str:
    return f"bill {bill_no}" if _s(bill_no) else "a bill"


def _bill_amount(row: dict) -> float | None:
    """INR amount of a bill: the stored INR value for a foreign-currency bill."""
    cur = (row.get("currency_code") or "INR").strip().upper()
    if cur != "INR":
        return _num(row.get("inr_amount_current"))
    return _num(row.get("total_amount"))


# ── Field labels for change diffs ────────────────────────────────────────────

FIELD_LABELS = {
    "name": "Name",
    "legal_name": "Legal name",
    "email": "Email",
    "payment_terms_days": "Payment terms",
    "pan": "PAN",
    "gstn": "GSTIN",
    "vendor_category_id": "Category",
    "category": "Category",
    "bank_account": "Bank account",
    "bank_ifsc": "IFSC",
    "bank_verified": "Bank verified",
    "status": "Verification status",
    "brand_id": "Brand",
    "is_active": "Active",
    "contacts": "Contacts",
    "bank_accounts": "Bank accounts",
    "gstins": "GSTINs",
}

_MASKED_FIELDS = {"bank_account", "bank_accounts", "account_number"}


def display_value(field: str, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if field in _MASKED_FIELDS:
        return mask_numbers(str(value)) if field == "bank_accounts" else mask_account(value)
    if field == "payment_terms_days":
        try:
            n = int(value)
            return f"{n} day" if n == 1 else f"{n} days"
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def diff_fields(old: dict, new: dict, labels: dict | None = None,
                value_fmt: Callable[[str, Any], Any] | None = None) -> list[dict]:
    """Per-field before → after of what actually changed, display-ready."""
    labels = labels or FIELD_LABELS
    fmt = value_fmt or display_value
    out = []
    for field, after in new.items():
        before = old.get(field)
        if _norm_cmp(before) == _norm_cmp(after):
            continue
        out.append({"field": labels.get(field, _humanise(field)),
                    "from": fmt(field, before), "to": fmt(field, after)})
    return out


_ACRONYMS = {"PAN", "GSTIN", "GSTINs", "IFSC", "TDS"}


def _lower_label(label: str) -> str:
    first = label.split(" ", 1)[0]
    return label if first in _ACRONYMS else label[:1].lower() + label[1:]


def change_title(changes: list[dict] | None, subject: str = "vendor details") -> str:
    """'Changed payment terms from 30 days to 45 days' / 'Changed name, email and PAN'."""
    if not changes:
        return f"Updated {subject}"
    if len(changes) == 1:
        c = changes[0]
        label = _lower_label(c["field"])
        before, after = c.get("from"), c.get("to")
        if after is None:
            return f"Cleared {label}"
        if before is not None and len(str(before)) + len(str(after)) <= 60:
            return f"Changed {label} from {before} to {after}"
        return f"Set {label} to {after}" if len(str(after)) <= 60 else f"Changed {label}"
    labels = [_lower_label(c["field"]) for c in changes]
    return "Changed " + ", ".join(labels[:-1]) + " and " + labels[-1]


def _norm_cmp(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    return str(v)


def _changes_from_pairs(changes: Any) -> list[dict] | None:
    """{"pan": ["A", "B"]} (Review audit snapshot diff) -> display list."""
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            return None
    if not isinstance(changes, dict) or not changes:
        return None
    out = []
    for field, pair in changes.items():
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            before, after = pair
        else:
            before, after = None, pair
        out.append({"field": FIELD_LABELS.get(field, _humanise(field)),
                    "from": display_value(field, before), "to": display_value(field, after)})
    return out or None


def _changes_list(changes: Any) -> list[dict] | None:
    """A stored [{"field","from","to"}] list (vendor_activity_log). The writer
    masks account numbers; a Bank account value is masked again in case not."""
    if isinstance(changes, str):
        try:
            changes = json.loads(changes)
        except ValueError:
            return None
    if isinstance(changes, dict):
        return _changes_from_pairs(changes)
    if not isinstance(changes, list):
        return None
    out = []
    for c in changes:
        if not isinstance(c, dict):
            continue
        field = str(c.get("field") or "")
        guard = mask_numbers if field.lower().startswith("bank account") else (lambda v: v)
        out.append({"field": field,
                    "from": guard(c.get("from")) if c.get("from") is not None else None,
                    "to": guard(c.get("to")) if c.get("to") is not None else None})
    return out or None


# ── Source normalisers (rows -> events) ──────────────────────────────────────

def bills_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        no = r.get("invoice_number")
        amount = _bill_amount(r)
        money = inr(amount)
        title = f"Received {_bill_label(no)}" + (f" · {money}" if money else "")
        soft = (r.get("soft_status") or "active").lower()
        detail = None
        if soft == "deleted":
            detail = "This bill was later deleted"
        elif soft == "on_hold":
            detail = "This bill is on hold"
        out.append(make_event(
            "bill", r.get("invoice_id"), r.get("created_at"), "bill_received", title,
            detail=detail, actor=r.get("uploaded_by_name"), actor_role=r.get("uploaded_by_role"),
            invoice_id=r.get("invoice_id"), bill_no=no, amount=amount))
    return [e for e in out if e]


_EDIT_PAIR = re.compile(r"^(?P<field>[^:]+):\s*(?P<old>.*?)\s*→\s*(?P<new>.*)$")


def parse_edit_comment(comment: Any) -> tuple[list[dict] | None, str | None]:
    """'vendor GST: (none) → 07AA…; line 2 removed: abc — 40' ->
    ([{"field": "Vendor GST", "from": None, "to": "07AA…"}], 'line 2 removed: abc — 40')."""
    if not comment:
        return None, None
    changes, rest = [], []
    for part in str(comment).split("; "):
        part = part.strip()
        if not part:
            continue
        m = _EDIT_PAIR.match(part)
        if m:
            old, new = m.group("old"), m.group("new")
            changes.append({"field": _humanise(m.group("field")),
                            "from": None if old in ("(none)", "", "None") else old,
                            "to": None if new in ("(none)", "", "None") else new})
        else:
            rest.append(part[:1].upper() + part[1:])
    return (changes or None), ("; ".join(rest) or None)


_APPROVAL_MAP = {
    "submit": ("bill_submitted", "Submitted {bill} for approval"),
    "approve": ("bill_approved", "Approved {bill}"),
    "reject": ("bill_rejected", "Rejected {bill}"),
    "send_back": ("bill_sent_back", "Sent {bill} back for changes"),
    "recon_approve": ("bill_approved", "Approved the reconciliation of {bill}"),
    "recon_reject": ("bill_rejected", "Rejected the reconciliation of {bill}"),
    "priority_change": ("bill_priority_changed", "Changed the priority of {bill}"),
    "payment_paid": ("payment_marked_paid", "Marked {no} as paid"),
    "payment_partial": ("payment_marked_partial", "Marked {no} as partly paid"),
    "payment_unpaid": ("payment_marked_unpaid", "Marked {no} as unpaid"),
}

_BORING_COMMENTS = {"marked as fully paid", "marked as unpaid"}


def approvals_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        action_raw = (r.get("action") or "").strip()
        no = r.get("invoice_number")
        bill = _bill_label(no)
        changes, detail = None, _s(r.get("comment"))
        if action_raw == "edited":
            action, title = "bill_edited", f"Edited {bill}"
            changes, detail = parse_edit_comment(r.get("comment"))
        elif action_raw in _APPROVAL_MAP:
            action, tpl = _APPROVAL_MAP[action_raw]
            title = tpl.format(bill=bill, no=_s(no) or "a bill")
        else:
            action = "bill_" + re.sub(r"[^a-z0-9]+", "_", action_raw.lower()).strip("_") if action_raw else "bill_updated"
            title = f"{_humanise(action_raw) or 'Updated'} {bill}"
        if detail and detail.lower() in _BORING_COMMENTS:
            detail = None
        out.append(make_event(
            "approval", r.get("id"), r.get("created_at"), action, title,
            detail=detail, actor=r.get("actor_name"), actor_role=r.get("actor_role"),
            invoice_id=r.get("invoice_id"), bill_no=no,
            amount=_bill_amount(r) if action.startswith("payment_") else None,
            changes=changes))
    return [e for e in out if e]


def disbursements_to_events(rows: Iterable[dict]) -> list[dict]:
    """One payment can produce: initiated, its outcome, and a receipt email."""
    out = []
    for r in rows:
        did = r.get("disbursement_id")
        no = r.get("invoice_number")
        amount = _num(r.get("amount"))
        money = payout_money(amount, r.get("currency_code"), r.get("rail"))
        bill = _bill_label(no)
        to_acct = f" to {mask_account(r.get('beneficiary_account'))}" if r.get("beneficiary_account") else ""
        common = dict(invoice_id=r.get("invoice_id"), bill_no=no, amount=amount)
        out.append(make_event(
            "disbursement", did, r.get("created_at"), "payment_initiated",
            f"Started a payment of {money or 'an amount'} for {bill}{to_acct}",
            actor=r.get("initiated_by"), **common))
        status = (r.get("status") or "").strip().upper()
        when = r.get("updated_at") or r.get("created_at")
        outcome_actor = r.get("updated_by")
        if status == "SUCCESS":
            out.append(make_event(
                "disbursement_outcome", did, when, "payment_succeeded",
                f"Payment of {money or 'an amount'} for {bill} went through",
                detail=mask_numbers(r.get("status_message")), actor=outcome_actor, **common))
        elif status == "FAILED":
            out.append(make_event(
                "disbursement_outcome", did, when, "payment_failed",
                f"Payment of {money or 'an amount'} for {bill} failed",
                detail=mask_numbers(r.get("status_message")), actor=outcome_actor, **common))
        elif status and r.get("updated_at") and r.get("created_at") and r["updated_at"] != r["created_at"]:
            out.append(make_event(
                "disbursement_outcome", did, when, "payment_pending",
                f"Payment of {money or 'an amount'} for {bill} is still pending",
                detail=mask_numbers(r.get("status_message")), actor=outcome_actor, **common))
        if r.get("receipt_emailed_at"):
            out.append(make_event(
                "disbursement_receipt", did, r.get("receipt_emailed_at"), "email_sent",
                f"Emailed the payment receipt for {bill}", **common))
    return [e for e in out if e]


def overrides_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        no = r.get("invoice_number")
        acct = r.get("override_account_number")
        title = f"Paid {_bill_label(no)} to a different account" + (f" ({mask_account(acct)})" if acct else "")
        changes = None
        if r.get("original_account_number") or acct:
            changes = [{"field": "Bank account",
                        "from": mask_account(r.get("original_account_number")) if r.get("original_account_number") else None,
                        "to": mask_account(acct) if acct else None}]
        out.append(make_event(
            "override", r.get("override_id"), r.get("created_at"), "payment_account_override", title,
            detail=mask_numbers(r.get("reason")), actor=r.get("overridden_by"),
            actor_role=r.get("overridden_by_role"), invoice_id=r.get("invoice_id"), bill_no=no,
            changes=changes))
    return [e for e in out if e]


_GST_FAIL_REASONS = {
    "trade_name_mismatch": "name mismatch",
    "name_mismatch": "name mismatch",
    "pan_mismatch": "PAN mismatch",
    "inactive": "GSTIN is not active",
}


def _jsonb(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


def wf_audit_to_events(rows: Iterable[dict], log_started_at: Any = None) -> list[dict]:
    """GST and bank verification results recorded by the pipelines.

    A manual GST re-verify also writes here (with no actor); once the Activity
    log exists that action is logged there with its actor, so the copy here is
    dropped from then on."""
    started = parse_ts(log_started_at)
    out = []
    for r in rows:
        new = _jsonb(r.get("new_values_jsonb"))
        table = r.get("table_name")
        at = r.get("created_at")
        pk = r.get("log_id")
        if table == "supplier_bank_accounts" or "bank_verified" in new:
            ok = new.get("bank_verified", new.get("verified"))
            acct = r.get("account_number")
            label = f" {mask_account(acct)}" if acct else ""
            if ok is True or str(ok).lower() == "true":
                name = _s(new.get("name_at_bank"))
                out.append(make_event("wf_audit", pk, at, "bank_verified",
                                      f"Bank account{label} verified" + (f" · name at bank: {name}" if name else "")))
            else:
                err = _s(new.get("error")) or ("account not found" if new.get("account_exists") is False else None)
                title = (f"Bank verification failed: {err[:1].lower() + err[1:]}" if err
                         else f"Bank account{label} could not be verified")
                out.append(make_event("wf_audit", pk, at, "bank_failed", title))
            continue
        status = (new.get("status") or "").upper()
        if status == "VERIFIED":
            detail = None
            if new.get("name_updated_to"):
                detail = f"Name updated from {new.get('name_updated_from') or 'blank'} to {new['name_updated_to']} (as registered)"
            elif new.get("api_legal_name"):
                detail = f"Registered as {new['api_legal_name']}"
            out.append(make_event("wf_audit", pk, at, "gst_verified", "GST verified", detail=detail))
        elif status == "FAILED":
            reason = _s(new.get("fail_reason"))
            text = _GST_FAIL_REASONS.get(reason or "", _humanise(reason).lower() if reason else None) \
                or _s(new.get("error"))
            detail = None
            if reason in ("trade_name_mismatch", "name_mismatch") and new.get("api_legal_name"):
                detail = f"Registered as {new['api_legal_name']}" + (f", on file as {new['db_name']}" if new.get("db_name") else "")
            out.append(make_event("wf_audit", pk, at, "gst_failed",
                                  f"GST verification failed: {text[:1].lower() + text[1:]}" if text else "GST verification failed",
                                  detail=detail))
        elif status == "UNVERIFIED" and new.get("reason") == "manual_reverify":
            ts = parse_ts(at)
            if started and ts and ts >= started:
                continue
            out.append(make_event("wf_audit", pk, at, "gstin_reverify_requested", "Asked to re-check the GSTIN"))
    return [e for e in out if e]


def _doc_name(r: dict) -> str:
    t = (r.get("doc_type") or "contract").lower()
    kind = "contract" if t == "contract" else (_s(r.get("doc_label")) or "document")
    name = _s(r.get("file_name"))
    return f"{kind} {name}" if name else kind


def documents_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        did = r.get("id")
        out.append(make_event("document", did, r.get("uploaded_at"), "document_uploaded",
                              f"Uploaded {_doc_name(r)}", actor=r.get("uploaded_by")))
        if r.get("deleted_at"):
            out.append(make_event("document_deleted", did, r.get("deleted_at"), "document_deleted",
                                  f"Deleted {_doc_name(r)}", actor=r.get("deleted_by")))
    return [e for e in out if e]


def doc_edits_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        diff = r.get("diff_json")
        if isinstance(diff, str):
            try:
                diff = json.loads(diff)
            except ValueError:
                diff = None
        n = len(diff) if isinstance(diff, list) else 0
        detail = f"{n} cell{'s' if n != 1 else ''} changed" if n else None
        out.append(make_event("doc_edit", r.get("id"), r.get("edited_at"), "document_edited",
                              f"Edited {_doc_name(r)}", detail=detail, actor=r.get("edited_by")))
    return [e for e in out if e]


def contacts_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        who = _s(r.get("name")) or _s(r.get("email")) or "a contact"
        extra = ", ".join(x for x in (_s(r.get("role")), _s(r.get("email"))) if x and x != who)
        out.append(make_event("contact", r.get("id"), r.get("created_at"), "contact_added",
                              f"Added contact {who}" + (f" ({extra})" if extra else ""),
                              dedup_key=("contact_added", str(r.get("id")))))
    return [e for e in out if e]


def gstins_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        gid, gstin = r.get("id"), _s(r.get("gstin")) or "a GSTIN"
        status = (r.get("status") or "").upper()
        created, verified = parse_ts(r.get("created_at")), parse_ts(r.get("verified_at"))
        same_moment = created and verified and abs((verified - created).total_seconds()) < 300
        suffix = " · verified" if status == "VERIFIED" and same_moment else (
            " · verification failed" if status == "FAILED" else "")
        out.append(make_event("gstin", gid, r.get("created_at"), "gstin_added", f"Added GSTIN {gstin}{suffix}",
                              detail=r.get("error_message") if status == "FAILED" else None,
                              dedup_key=("gstin_added", str(gid))))
        if status == "VERIFIED" and verified and not same_moment:
            out.append(make_event("gstin_verified", gid, r.get("verified_at"), "gst_verified",
                                  f"GSTIN {gstin} verified"))
    return [e for e in out if e]


def bank_accounts_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        bank = _s(r.get("bank_name"))
        out.append(make_event("bank_account", r.get("id"), r.get("created_at"), "bank_added",
                              f"Added bank account {mask_account(r.get('account_number'))}" + (f" ({bank})" if bank else ""),
                              dedup_key=("bank_added", str(r.get("id")))))
    return [e for e in out if e]


def outreach_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        subject = _s(r.get("subject"))
        failed = (r.get("status") or "").lower() == "failed"
        to = _s(r.get("email_to"))
        title = ("Email to the vendor failed" if failed else "Emailed the vendor") + (f": {subject}" if subject else "")
        out.append(make_event("outreach", r.get("id"), r.get("sent_at") or r.get("requested_at"),
                              "email_failed" if failed else "email_sent", title,
                              detail=f"To {to}" if to else None,
                              actor=r.get("sent_by") or r.get("requested_by")))
    return [e for e in out if e]


_TDS_FIELDS = {
    "tds_mode": "TDS mode",
    "bill_tds_override": "TDS on a bill",
    "override_rate": "TDS rate",
    "lower_cert_rate": "Lower-deduction certificate rate",
    "tds_section_id": "TDS section",
}


def tds_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        field = r.get("field_changed") or ""
        label = _TDS_FIELDS.get(field, _humanise(field) or "TDS setting")
        old, new = _s(r.get("old_value")), _s(r.get("new_value"))
        if field == "bill_tds_override":
            fmt = lambda v: inr(v) or v
        elif field == "tds_mode":
            fmt = _humanise
        else:
            fmt = lambda v: v
        old_d, new_d = (fmt(old) if old else None), (fmt(new) if new else None)
        if field == "bill_tds_override":
            title = f"Set TDS on a bill to {new_d}" if new_d else "Cleared the TDS override on a bill"
        else:
            title = f"Changed {label}" + (f" to {new_d}" if new_d else "")
        out.append(make_event("tds", r.get("log_id"), r.get("changed_at"), "tds_changed", title,
                              detail=r.get("reason"),
                              changes=[{"field": label, "from": old_d, "to": new_d}]))
    return [e for e in out if e]


# Review Hub requests that, since the Activity log began, write their own log
# row with the actor and a diff — their Review copy would be a duplicate.
_LOGGED_PATHS = re.compile(
    r"^/api/vendors/[0-9a-fA-F-]{36}"
    r"(?:/names|/bank-accounts(?:/.*)?|/reverify-bank|/contacts(?:/.*)?|/gstins(?:/.*)?|/documents/[^/]+/type)?/?$"
)

_REVIEW_TITLES = (
    (re.compile(r"/names/?$"), "Set the vendor's brand and legal names from Review"),
    (re.compile(r"/reverify-bank/?$"), "Asked to re-verify the bank account from Review"),
    (re.compile(r"/verify-bank/?$"), "Verified a bank account from Review"),
    (re.compile(r"/bank-accounts"), "Changed bank accounts from Review"),
    (re.compile(r"/contacts"), "Changed contacts from Review"),
    (re.compile(r"/gstins"), "Changed GSTINs from Review"),
    (re.compile(r"/documents"), "Changed documents from Review"),
    (re.compile(r"/merge/?$"), "Merged a duplicate vendor into this one from Review"),
    (re.compile(r"/request-info/?$"), "Asked the vendor for missing details from Review"),
    (re.compile(r"/brand/?$"), "Moved the vendor to another brand from Review"),
)


def review_to_events(rows: Iterable[dict], log_started_at: Any = None) -> list[dict]:
    started = parse_ts(log_started_at)
    out = []
    for r in rows:
        path = r.get("path") or ""
        method = (r.get("method") or "").upper()
        ts = parse_ts(r.get("created_at"))
        if started and ts and ts >= started and _LOGGED_PATHS.match(path):
            continue
        code = r.get("status_code")
        if isinstance(code, int) and code >= 400:
            continue
        title = None
        for pattern, text in _REVIEW_TITLES:
            if pattern.search(path):
                title = text
                break
        if title is None:
            title = "Changed vendor details from Review" if method == "PATCH" else "Updated the vendor from Review"
        out.append(make_event("review", r.get("audit_id"), r.get("created_at"), "review_change", title,
                              actor=r.get("actor"), actor_role=r.get("actor_role"),
                              changes=_changes_from_pairs(r.get("changes"))))
    return [e for e in out if e]


def agent_drafts_to_events(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        sender = _s(r.get("from_email"))
        status = (r.get("status") or "").lower()
        detail_bits = [b for b in (_s(r.get("subject")),
                                   "Waiting for review" if status == "pending" else None) if b]
        out.append(make_event("agent_draft", r.get("id"), r.get("created_at"), "bill_picked_up_by_agent",
                              "Email agent picked up a bill" + (f" from {sender}" if sender else ""),
                              detail=" · ".join(detail_bits) or None))
    return [e for e in out if e]


def activity_log_to_events(rows: Iterable[dict]) -> list[dict]:
    """vendor_activity_log rows were written display-ready (and masked) by the writer."""
    out = []
    for r in rows:
        action = r.get("action") or "vendor_updated"
        meta = _jsonb(r.get("meta"))
        entity = meta.get("entity_id")
        out.append(make_event(
            "log", r.get("id"), r.get("created_at"), action, _s(r.get("title")) or _humanise(action),
            detail=meta.get("detail"), actor=r.get("actor"), actor_role=r.get("actor_role"),
            invoice_id=meta.get("invoice_id"), bill_no=meta.get("bill_no"), amount=meta.get("amount"),
            changes=_changes_list(r.get("changes")),
            dedup_key=(action, str(entity)) if entity else None))
    return [e for e in out if e]


# ── Merge / page ─────────────────────────────────────────────────────────────

def merge_events(results: dict[str, list[dict] | BaseException], limit: Any = DEFAULT_LIMIT,
                 before: Any = None) -> dict:
    """Merge per-source events into one newest-first page.

    `results` maps a source name to its events, or to the exception it raised;
    any failed source sets partial (the rest still render). Current-state
    "added" events whose row was also written to the Activity log are dropped
    in favour of the log's copy, which has the actor.
    """
    limit = clamp_limit(limit)
    cutoff = parse_ts(before)
    partial = False
    events: list[dict] = []
    for name, res in results.items():
        if isinstance(res, BaseException):
            partial = True
            log.warning("[vendor_activity] source %s failed: %s", name, res)
            continue
        events.extend(e for e in (res or []) if e)

    logged = {e["_dedup"] for e in events if e.get("_dedup") and e["id"].startswith("src:log:")}
    seen_ids: set[str] = set()
    kept = []
    for e in events:
        key = e.get("_dedup")
        if key and key in logged and not e["id"].startswith("src:log:"):
            continue
        if e["id"] in seen_ids:
            continue
        if cutoff is not None:
            ts = parse_ts(e["at"])
            if ts is None or ts >= cutoff:
                continue
        seen_ids.add(e["id"])
        kept.append(e)

    kept.sort(key=lambda e: (parse_ts(e["at"]), e["id"]), reverse=True)
    page = kept[:limit]
    more = len(kept) > limit
    if more and page:
        # `before` is exclusive, so a run of events sharing the boundary
        # timestamp would lose its tail on the next page. Hold that run back.
        boundary = page[-1]["at"]
        if kept[limit]["at"] == boundary:
            trimmed = [e for e in page if e["at"] != boundary]
            if trimmed:
                page = trimmed
    for e in page:
        e.pop("_dedup", None)
    return {
        "events": page,
        "next_before": page[-1]["at"] if more and page else None,
        "partial": partial,
    }


def clamp_limit(limit: Any) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))
