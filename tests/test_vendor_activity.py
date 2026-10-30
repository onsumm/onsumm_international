"""Vendor Activity timeline: rows -> events, merge, paging, masking.

Pure functions only (backend_ocr/app/domain/vendor_activity.py), plus the
per-source savepoint isolation in routers/vendor_activity.py with a fake cursor.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from backend_ocr.app.domain import vendor_activity as va  # noqa: E402

T0 = datetime(2026, 10, 20, 10, 31, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


CONTRACT_KEYS = {"id", "at", "kind", "action", "title", "detail", "actor", "actor_role",
                 "invoice_id", "bill_no", "amount", "changes"}


# ── formatting ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("amount,text", [
    (253686, "₹2,53,686"),
    (9497, "₹9,497"),
    (999, "₹999"),
    (1234567.5, "₹12,34,567.50"),
    (0, "₹0"),
    (None, None),
])
def test_inr_indian_grouping(amount, text):
    assert va.inr(amount) == text


def test_mask_account_keeps_last_four_only():
    assert va.mask_account("50100123456789") == "••••6789"
    assert va.mask_account(" 5010 0123 4567 89 ") == "••••6789"
    assert va.mask_numbers("from 50100123456789 to 9988776655") == "from ••••6789 to ••••6655"
    assert va.mask_numbers("PAN AALCM5200G") == "PAN AALCM5200G"
    # Amounts are not account numbers.
    assert va.mask_numbers("Amount ₹10207000 exceeds the limit") == "Amount ₹10207000 exceeds the limit"


def test_iso_always_has_timezone():
    assert va.iso(datetime(2026, 10, 20, 10, 31)) == "2026-10-20T10:31:00+00:00"
    assert va.parse_ts("2026-10-20T10:31:00 00:00") == T0  # '+' lost in an unencoded URL
    assert va.parse_ts("2026-10-20T10:31:00Z") == T0
    assert va.parse_ts("not a date") is None


# ── kinds ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("action,kind", [
    ("bill_received", "bill"), ("bill_picked_up_by_agent", "bill"), ("bill_deleted", "bill"),
    ("bill_approved", "approval"), ("bill_rejected", "approval"),
    ("bill_sent_back", "approval"), ("bill_edited", "approval"),
    ("payment_marked_paid", "payment"), ("payment_failed", "payment"),
    ("payment_account_override", "payment"),
    ("gst_verified", "compliance"), ("bank_added", "compliance"),
    ("gstin_deleted", "compliance"), ("tds_changed", "compliance"),
    ("vendor_updated", "change"), ("review_change", "change"),
    ("document_uploaded", "document"), ("document_type_changed", "document"),
    ("contact_added", "contact"), ("email_sent", "email"),
])
def test_kind_mapping(action, kind):
    assert va.kind_for(action) == kind


# ── titles ───────────────────────────────────────────────────────────────────

def test_bill_received_title_and_fields():
    [ev] = va.bills_to_events([{
        "invoice_id": "11111111-1111-1111-1111-111111111111", "invoice_number": "CMPL/26-27/016",
        "total_amount": 9497, "currency_code": "INR", "soft_status": "active",
        "created_at": T0, "uploaded_by_name": "Asha", "uploaded_by_role": "accounts"}])
    assert set(ev) == CONTRACT_KEYS
    assert ev["title"] == "Received bill CMPL/26-27/016 · ₹9,497"
    assert ev["id"] == "src:bill:11111111-1111-1111-1111-111111111111"
    assert ev["kind"] == "bill" and ev["action"] == "bill_received"
    assert ev["amount"] == 9497.0 and ev["bill_no"] == "CMPL/26-27/016"
    assert ev["actor"] == "Asha" and ev["at"] == "2026-10-20T10:31:00+00:00"


def test_deleted_bill_is_marked_and_foreign_bill_uses_inr_amount():
    [ev] = va.bills_to_events([{
        "invoice_id": "x", "invoice_number": "INV-9", "total_amount": 100, "currency_code": "USD",
        "inr_amount_current": 8400, "soft_status": "deleted", "created_at": T0}])
    assert ev["amount"] == 8400.0
    assert ev["detail"] == "This bill was later deleted"
    assert ev["actor"] is None


def test_approval_titles():
    base = {"invoice_id": "b1", "invoice_number": "CMPL/26-27/016", "created_at": T0,
            "actor_name": "Ravi", "actor_role": "approver", "total_amount": 9497, "currency_code": "INR"}
    rows = [
        {**base, "id": 1, "action": "approve", "comment": None},
        {**base, "id": 2, "action": "send_back", "comment": "Wrong GST rate"},
        {**base, "id": 3, "action": "payment_paid", "comment": "Marked as fully paid"},
        {**base, "id": 4, "action": "edited", "comment": "vendor GST: (none) → 07AAACS2977B1ZI; line 2 removed: abc — 40"},
    ]
    evs = {e["id"]: e for e in va.approvals_to_events(rows)}
    assert evs["src:approval:1"]["title"] == "Approved bill CMPL/26-27/016"
    assert evs["src:approval:1"]["kind"] == "approval"
    assert evs["src:approval:2"]["title"] == "Sent bill CMPL/26-27/016 back for changes"
    assert evs["src:approval:2"]["detail"] == "Wrong GST rate"
    paid = evs["src:approval:3"]
    assert paid["title"] == "Marked CMPL/26-27/016 as paid"
    assert paid["action"] == "payment_marked_paid" and paid["kind"] == "payment"
    assert paid["detail"] is None and paid["amount"] == 9497.0
    edit = evs["src:approval:4"]
    assert edit["action"] == "bill_edited" and edit["kind"] == "approval"
    assert edit["changes"] == [{"field": "Vendor GST", "from": None, "to": "07AAACS2977B1ZI"}]
    assert edit["detail"] == "Line 2 removed: abc — 40"


def test_gst_failure_title():
    [ev] = va.wf_audit_to_events([{
        "log_id": "w1", "table_name": "dim_suppliers", "created_at": T0,
        "new_values_jsonb": {"status": "FAILED", "fail_reason": "trade_name_mismatch",
                             "api_legal_name": "ACME PVT LTD", "db_name": "Acme"}}])
    assert ev["title"] == "GST verification failed: name mismatch"
    assert ev["action"] == "gst_failed" and ev["kind"] == "compliance"
    assert ev["actor"] is None


def test_bank_verification_titles_are_masked():
    evs = va.wf_audit_to_events([
        {"log_id": "w2", "table_name": "supplier_bank_accounts", "created_at": T0,
         "account_number": "50100123456789",
         "new_values_jsonb": {"verified": True, "name_at_bank": "ACME"}},
        {"log_id": "w3", "table_name": "dim_suppliers", "created_at": T0,
         "new_values_jsonb": {"bank_verified": False, "error": "Insufficient credits"}},
    ])
    assert evs[0]["title"] == "Bank account ••••6789 verified · name at bank: ACME"
    assert evs[1]["title"] == "Bank verification failed: insufficient credits"
    assert all("50100123456789" not in e["title"] for e in evs)


def test_manual_reverify_dropped_once_log_exists():
    row = {"log_id": "w4", "table_name": "dim_suppliers", "created_at": at(10),
           "new_values_jsonb": {"status": "UNVERIFIED", "reason": "manual_reverify"}}
    assert va.wf_audit_to_events([row])[0]["action"] == "gstin_reverify_requested"
    assert va.wf_audit_to_events([row], log_started_at=at(0)) == []
    assert len(va.wf_audit_to_events([row], log_started_at=at(20))) == 1


def test_disbursement_events_and_masking():
    evs = va.disbursements_to_events([{
        "disbursement_id": "d1", "invoice_id": "b1", "invoice_number": "INV-1", "amount": 253686,
        "status": "FAILED", "status_message": "Beneficiary bank down", "beneficiary_account": "50100123456789",
        "initiated_by": "admin", "updated_by": None, "created_at": at(0), "updated_at": at(2),
        "receipt_emailed_at": None}])
    actions = [e["action"] for e in evs]
    assert actions == ["payment_initiated", "payment_failed"]
    assert evs[0]["title"] == "Started a payment of ₹2,53,686 for bill INV-1 to ••••6789"
    assert evs[1]["title"] == "Payment of ₹2,53,686 for bill INV-1 failed"
    assert len({e["id"] for e in evs}) == 2


def test_usdc_payout_is_not_shown_in_rupees():
    evs = va.disbursements_to_events([{
        "disbursement_id": "d2", "invoice_id": "b2", "invoice_number": "NOVA-1042", "amount": 1,
        "rail": "USDC", "currency_code": "USD", "status": "SUCCESS", "status_message": None,
        "beneficiary_account": "0xabc3881", "initiated_by": "Asha", "updated_by": None,
        "created_at": at(0), "updated_at": at(1), "receipt_emailed_at": None}])
    assert evs[0]["title"].startswith("Started a payment of 1 USDC for bill NOVA-1042")
    assert evs[1]["title"] == "Payment of 1 USDC for bill NOVA-1042 went through"
    assert va.payout_money(0.7, "USD", "USDC") == "0.70 USDC"
    assert va.payout_money(12.5, "EUR", "USDC") == "EUR 12.50"
    assert va.payout_money(253686, None, None) == "₹2,53,686"


def test_override_masks_both_accounts():
    [ev] = va.overrides_to_events([{
        "override_id": "o1", "invoice_id": "b1", "invoice_number": "INV-1", "overridden_by": "admin",
        "reason": "Vendor changed bank, new a/c 112233445566", "original_account_number": "50100123456789",
        "override_account_number": "112233445566", "created_at": T0}])
    assert ev["title"] == "Paid bill INV-1 to a different account (••••5566)"
    assert ev["changes"] == [{"field": "Bank account", "from": "••••6789", "to": "••••5566"}]
    assert "112233445566" not in ev["detail"]


def test_review_change_masks_bank_snapshot_and_skips_logged_paths():
    rows = [
        {"audit_id": 1, "actor": "Vik", "actor_role": "admin", "method": "PATCH",
         "path": "/api/vendors/29a92167-456e-4272-ae5b-1ca9767088b8", "status_code": 200,
         "changes": {"payment_terms_days": [30, 45], "bank_account": ["50100123456789", "112233445566"]},
         "created_at": at(5)},
        {"audit_id": 2, "actor": "Vik", "actor_role": "admin", "method": "POST",
         "path": "/api/vendors/29a92167-456e-4272-ae5b-1ca9767088b8/merge", "status_code": 200,
         "changes": {}, "created_at": at(6)},
    ]
    evs = va.review_to_events(rows)
    assert evs[0]["changes"] == [
        {"field": "Payment terms", "from": "30 days", "to": "45 days"},
        {"field": "Bank account", "from": "••••6789", "to": "••••5566"},
    ]
    assert evs[1]["title"] == "Merged a duplicate vendor into this one from Review"
    # After the Activity log began, the PATCH is logged there; merge still comes from here.
    later = va.review_to_events(rows, log_started_at=at(0))
    assert [e["id"] for e in later] == ["src:review:2"]


def test_vendor_diff_and_change_title():
    changes = va.diff_fields({"payment_terms_days": 30, "email": "a@x.in", "name": "Acme"},
                             {"payment_terms_days": 45, "email": "a@x.in", "name": "Acme "})
    assert changes == [{"field": "Payment terms", "from": "30 days", "to": "45 days"}]
    assert va.change_title(changes) == "Changed payment terms from 30 days to 45 days"
    two = va.diff_fields({"name": "A", "pan": None}, {"name": "B", "pan": "AALCM5200G"})
    assert va.change_title(two) == "Changed name and PAN"


def test_tds_and_email_titles():
    [tds] = va.tds_to_events([{"log_id": "t1", "field_changed": "tds_mode", "old_value": "always_deduct",
                               "new_value": "per_bill_threshold", "reason": "Switched", "changed_at": T0}])
    assert tds["title"] == "Changed TDS mode to Per bill threshold"
    assert tds["kind"] == "compliance" and tds["detail"] == "Switched"
    [mail] = va.outreach_to_events([{"id": "e1", "requested_by": "Asha", "requested_at": T0,
                                     "email_to": "v@x.in", "subject": "Need your PAN", "status": "sent"}])
    assert mail["title"] == "Emailed the vendor: Need your PAN" and mail["kind"] == "email"


# ── merge / dedup / paging ───────────────────────────────────────────────────

def _ev(n: int, minutes: int, source="bill"):
    return va.make_event(source, n, at(minutes), "bill_received", f"Received bill {n}")


def test_sort_newest_first_and_limit_cursor():
    events = [_ev(i, i) for i in range(10)]
    page = va.merge_events({"bills": events}, limit=4)
    assert [e["id"] for e in page["events"]] == ["src:bill:9", "src:bill:8", "src:bill:7", "src:bill:6"]
    assert page["next_before"] == page["events"][-1]["at"]
    assert page["partial"] is False

    page2 = va.merge_events({"bills": [_ev(i, i) for i in range(10)]}, limit=4, before=page["next_before"])
    assert [e["id"] for e in page2["events"]] == ["src:bill:5", "src:bill:4", "src:bill:3", "src:bill:2"]

    last = va.merge_events({"bills": [_ev(i, i) for i in range(10)]}, limit=4, before=at(2))
    assert [e["id"] for e in last["events"]] == ["src:bill:1", "src:bill:0"]
    assert last["next_before"] is None


def test_page_does_not_split_a_timestamp_tie():
    events = [_ev(1, 5), _ev(2, 3), _ev(3, 3), _ev(4, 1)]
    page = va.merge_events({"bills": events}, limit=2)
    # Events 2 and 3 share a timestamp; with an exclusive cursor one would be lost.
    assert [e["id"] for e in page["events"]] == ["src:bill:1"]
    nxt = va.merge_events({"bills": events}, limit=2, before=page["next_before"])
    assert {e["id"] for e in nxt["events"]} == {"src:bill:2", "src:bill:3"}


def test_limit_is_capped():
    events = [_ev(i, i) for i in range(250)]
    assert len(va.merge_events({"bills": events}, limit=1000)["events"]) == 200
    assert va.clamp_limit("abc") == 100


def test_failing_source_sets_partial_and_others_still_render():
    page = va.merge_events({"bills": [_ev(1, 1)], "tds": RuntimeError("relation does not exist")})
    assert page["partial"] is True
    assert [e["id"] for e in page["events"]] == ["src:bill:1"]


def test_current_state_add_dropped_when_log_has_it():
    log_rows = [{"id": "L1", "actor": "asha", "actor_role": "admin", "action": "contact_added",
                 "title": "Added contact Raj (Accounts, raj@x.in)", "changes": None,
                 "meta": {"entity_id": "c1"}, "created_at": T0}]
    contact_rows = [{"id": "c1", "name": "Raj", "role": "Accounts", "email": "raj@x.in", "created_at": T0},
                    {"id": "c0", "name": "Old", "role": None, "email": None, "created_at": at(-60)}]
    page = va.merge_events({
        "activity_log": va.activity_log_to_events(log_rows),
        "contacts": va.contacts_to_events(contact_rows),
    })
    ids = [e["id"] for e in page["events"]]
    assert ids == ["src:log:L1", "src:contact:c0"]
    assert page["events"][0]["actor"] == "asha"
    assert all("_dedup" not in e for e in page["events"])
    assert all(set(e) == CONTRACT_KEYS for e in page["events"])


def test_collect_activity_isolates_a_broken_source():
    """routers.vendor_activity runs each source in a savepoint; one failing
    query rolls back to it and the response is partial, not a 500."""
    from backend_ocr.app.routers import vendor_activity as route

    class FakeCursor:
        def __init__(self):
            self.statements: list[str] = []
            self._rows: list = []

        def execute(self, sql, params=None):
            self.statements.append(" ".join(sql.split()))
            if "FROM vendor_tds_config_log" in sql:
                raise RuntimeError('relation "vendor_tds_config_log" does not exist')
            if "FROM fact_invoices fi" in sql and "LEFT JOIN raw_invoice_uploads" in sql:
                self._rows = [{"invoice_id": "b1", "invoice_number": "INV-1", "total_amount": 100,
                               "currency_code": "INR", "soft_status": "active", "created_at": T0,
                               "uploaded_by_name": None, "uploaded_by_role": None}]
            elif "to_regclass" in sql:
                self._rows = [{"present": False}]
            elif "min(created_at)" in sql:
                self._rows = [{"started": None}]
            else:
                self._rows = []

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    cur = FakeCursor()
    result = route.collect_activity(cur, "tenant", "11111111-1111-1111-1111-111111111111", 50, None)
    assert result["partial"] is True
    assert [e["id"] for e in result["events"]] == ["src:bill:b1"]
    assert "ROLLBACK TO SAVEPOINT va_tds" in cur.statements


def test_log_helper_swallows_a_failed_insert():
    """Logging must never break the action: a missing table rolls back to the
    savepoint and the caller's transaction carries on."""
    from backend_ocr.app.core import vendor_activity_log as vlog

    class FakeCursor:
        def __init__(self, fail):
            self.fail, self.statements = fail, []

        def execute(self, sql, params=None):
            self.statements.append(sql.split()[0] + (" " + sql.split()[1] if len(sql.split()) > 1 else ""))
            if self.fail and sql.lstrip().startswith("INSERT"):
                raise RuntimeError('relation "vendor_activity_log" does not exist')

    bad = FakeCursor(fail=True)
    assert vlog.log_vendor_activity(bad, "s1", "bank_added", "Added bank account ••••6789", tenant_id="t1") is False
    assert "ROLLBACK TO" in bad.statements

    good = FakeCursor(fail=False)
    assert vlog.log_vendor_activity(good, "s1", "bank_added", "Added bank account ••••6789", tenant_id="t1") is True
    assert good.statements == ["SAVEPOINT vendor_activity_log_sp", "INSERT INTO", "RELEASE SAVEPOINT"]
