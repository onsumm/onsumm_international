"""The receipt a vendor gets when a payment settles.

Two halves, matching the two modules: what the document says
(app/domain/payments/receipt.py — pure rendering, no DB, no mail) and how the
Communication Agent delivers it (app/domain/payments/notify.py — claimed once,
sent over the right mailbox, honest when it cannot send at all).

Nothing here sends: Gmail, SMTP and the database are all monkeypatched or
MagicMocks, never reached.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend_ocr.app.domain.payments import notify as pn
from backend_ocr.app.domain.payments import receipt as rc

TENANT = "10000000-0000-0000-0000-000000000001"
VENDOR = "33333333-0000-0000-0000-000000000001"
DISB = "44444444-0000-0000-0000-000000000001"
CONTACT = {"id": "c-1", "name": "Priya Sharma", "email": "priya@vendor.example"}


def _receipt(**over) -> rc.Receipt:
    base = dict(
        payer_name="Acme Foods Pvt Ltd",
        vendor_name="Bharat Logistics",
        beneficiary_name="Bharat Logistics",
        beneficiary_account=rc.mask_account("50100123456789"),
        beneficiary_ifsc="ICIC0000101",
        beneficiary_bank="ICICI Bank",
        paid_at="18 Sep 2026, 14:05 IST",
        contact_name="Priya Sharma",
        bills=(rc.ReceiptBill(number="INV-2026-0912", amount=125000.0,
                              date="12 Sep 2026", reference="526012345678"),),
    )
    base.update(over)
    return rc.Receipt(**base)


# ── What the document says ───────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (0, "0.00"),
    (999, "999.00"),
    (1000, "1,000.00"),
    (125000, "1,25,000.00"),
    (1234567.5, "12,34,567.50"),
    (-100000, "-1,00,000.00"),
])
def test_amounts_are_grouped_the_indian_way(value, expected):
    """A vendor reading a receipt for a bill they raised should see the digits
    fall where en-IN puts them, not where en-US does."""
    assert rc.group_inr(value) == expected


def test_the_pdf_spells_out_rupees_because_helvetica_has_no_symbol():
    assert rc.rupees_ascii(1000) == "Rs. 1,000.00"
    assert rc.inr(1000) == "₹1,000.00"


def test_subject_names_the_bill_only_when_there_is_one_to_name():
    one = rc.subject(_receipt())
    assert "₹1,25,000.00" in one and "Acme Foods Pvt Ltd" in one
    assert "INV-2026-0912" in one

    many = rc.subject(_receipt(bills=(
        rc.ReceiptBill(number="INV-1", amount=1000.0),
        rc.ReceiptBill(number="INV-2", amount=500.0),
    )))
    assert "INV-1" not in many
    assert "₹1,500.00" in many


def test_the_total_is_what_the_bills_add_up_to():
    r = _receipt(bills=(rc.ReceiptBill(number="INV-1", amount=1000.0),
                        rc.ReceiptBill(number="INV-2", amount=500.5)))
    assert r.total == 1500.5


def test_the_receipt_never_names_the_account_the_money_left():
    """This document leaves the building. It names the payer company; the
    remitter's account number is nowhere on it, and the vendor's own account is
    masked to the last four."""
    rows = dict(rc.detail_rows(_receipt()))
    assert rows["Paid by"] == "Acme Foods Pvt Ltd"
    assert "**********6789" in rows["Bank account"]
    assert "50100123456789" not in str(rows)


def test_one_reference_is_a_detail_row_and_several_are_per_bill():
    single = dict(rc.detail_rows(_receipt()))
    assert single["Bank reference (UTR)"] == "526012345678"

    multi = _receipt(bills=(rc.ReceiptBill(number="INV-1", amount=10.0, reference="A1"),
                            rc.ReceiptBill(number="INV-2", amount=20.0, reference="B2")))
    assert "Bank reference (UTR)" not in dict(rc.detail_rows(multi))
    text = rc.render_text(multi)
    assert "A1" in text and "B2" in text


def test_html_escapes_whatever_the_vendor_master_holds():
    """Vendor and contact names come from OCR and from users. They land inside
    a mail body, so they get escaped, not interpolated."""
    html = rc.render_html(_receipt(vendor_name='Bad <script>alert(1)</script> Co',
                                   beneficiary_name='Bad <script>alert(1)</script> Co'))
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_mail_always_has_a_readable_plain_text_half():
    text = rc.render_text(_receipt())
    assert text.startswith("Hi Priya,")
    assert "₹1,25,000.00" in text
    assert "INV-2026-0912" in text
    assert "<" not in text


def test_an_open_balance_is_stated_rather_than_left_to_be_noticed():
    r = _receipt(bills=(rc.ReceiptBill(number="INV-1", amount=1000.0, outstanding=250.0),))
    assert "₹250.00" in rc.render_text(r)
    assert "250.00" in rc.render_html(r)


def test_the_pdf_renders():
    pdf = rc.render_pdf(_receipt(bills=(
        rc.ReceiptBill(number="INV-1", amount=1000.0, date="01 Sep 2026", reference="A1"),
        rc.ReceiptBill(number="INV-2", amount=500.0, outstanding=99.0),
    )))
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_the_attachment_is_named_so_a_vendor_can_find_it_again():
    name = rc.file_name(_receipt())
    assert name.startswith("payment-receipt-bharat-logistics-18-sep-2026")
    assert name.endswith(".pdf")


def test_the_time_is_stated_in_the_tenants_own_zone():
    moment = datetime(2026, 9, 18, 8, 35, tzinfo=timezone.utc)
    assert rc.format_moment(moment, "Asia/Kolkata") == "18 Sep 2026, 14:05 IST"
    # An unknown zone must still produce a receipt, not an exception.
    assert "2026" in rc.format_moment(moment, "Not/AZone")


# ── How it is delivered ──────────────────────────────────────────────────────

def _conn_cur():
    conn = MagicMock()
    cur = MagicMock()
    return conn, cur


def test_only_a_settled_unsent_payment_is_claimable():
    """The whole no-double-receipt guarantee is this one UPDATE: it matches
    SUCCESS rows whose receipt state is still NULL, and locks each row it
    matches, so of two racing callers exactly one comes away with it."""
    conn, cur = _conn_cur()
    cur.fetchall.return_value = []
    assert pn._claim(cur, conn, TENANT, [DISB]) == []

    sql = " ".join(cur.execute.call_args.args[0].split())
    assert "SET receipt_email_status = 'SENDING'" in sql
    assert "AND status = 'SUCCESS'" in sql
    assert "AND receipt_email_status IS NULL" in sql


def test_the_poc_is_the_primary_contact_that_has_an_email():
    conn, cur = _conn_cur()
    cur.fetchone.return_value = CONTACT
    assert pn._primary_contact(cur, TENANT, VENDOR) == CONTACT
    sql = " ".join(cur.execute.call_args.args[0].split())
    assert "ORDER BY is_primary DESC, created_at ASC" in sql
    # Not filtered on agent_enabled: that flag picks whose replies the agent
    # watches, not who is owed a receipt.
    assert "agent_enabled" not in sql
    assert "email IS NOT NULL" in sql


def test_a_vendor_with_no_contact_is_reported_not_guessed_at(monkeypatch):
    conn, cur = _conn_cur()
    cur.fetchone.return_value = None
    opened = MagicMock()
    monkeypatch.setattr(pn, "_open_run", opened)

    out = pn._send_for_vendor(cur, conn, TENANT, VENDOR, [{"disbursement_id": DISB}], "amit")

    assert out["status"] == "skipped" and out["reason"] == "no_contact"
    # No agent run is opened for work that never started.
    opened.assert_not_called()
    statuses = [c.args[1][0] for c in cur.execute.call_args_list
                if "fact_disbursements" in c.args[0]]
    assert statuses == ["no_contact"]


def test_delivery_prefers_the_acting_users_own_mailbox(monkeypatch):
    """So the receipt threads, is logged against the vendor like every other
    message, and a vendor replying 'this is short by 400' reaches the person
    who released the payment."""
    from backend_ocr.app.routers import email_connection

    monkeypatch.setattr(email_connection, "_load_connection",
                        lambda cur, t, u: {"status": "active", "email": "amit@acme.example"})
    seen = {}

    def _send(cur, conn, **kw):
        seen.update(kw)
        return {"status": "sent", "from": "amit@acme.example", "thread_id": "t-1"}

    monkeypatch.setattr(email_connection, "send_vendor_contact_email", _send)
    conn, cur = _conn_cur()

    out = pn._deliver(cur, conn, tenant_id=TENANT, supplier_id=VENDOR,
                      contact=CONTACT, doc=_receipt(), actor_username="amit")

    assert out == {"status": "sent", "via": "mailbox", "from": "amit@acme.example",
                   "thread_id": "t-1"}
    assert seen["contact_id"] == "c-1"
    assert seen["html"].lstrip().startswith("<!DOCTYPE html>")
    assert "<" not in seen["message"]                      # the plain-text half
    name, data, ctype = seen["attachments"][0]
    assert ctype == "application/pdf" and data.startswith(b"%PDF-")
    assert name.endswith(".pdf")


def test_no_mailbox_falls_back_to_system_mail_rather_than_dropping_it(monkeypatch):
    """A receipt is transactional mail that has to arrive, and the admin who
    verified the OTP may never have connected a mailbox."""
    from backend_ocr.app.routers import email_connection

    monkeypatch.setattr(email_connection, "_load_connection", lambda cur, t, u: None)
    monkeypatch.setattr(pn, "SMTP_USER", "noreply@acme.example")
    monkeypatch.setattr(pn, "SMTP_PASSWORD", "secret")
    sent = {}

    class _Server:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): pass
        def sendmail(self, frm, to, raw): sent.update(frm=frm, to=to, raw=raw)

    monkeypatch.setattr(pn.smtplib, "SMTP_SSL", lambda *a, **k: _Server())
    conn, cur = _conn_cur()

    out = pn._deliver(cur, conn, tenant_id=TENANT, supplier_id=VENDOR,
                      contact=CONTACT, doc=_receipt(), actor_username="amit")

    assert out["status"] == "sent" and out["via"] == "smtp"
    assert sent["to"] == ["priya@vendor.example"]
    assert "text/html" in sent["raw"] and "application/pdf" in sent["raw"]
    # Logged against the vendor like any other outreach.
    logged = [c for c in cur.execute.call_args_list if "vendor_outreach_log" in c.args[0]]
    assert len(logged) == 1


def test_nowhere_to_send_from_says_so_instead_of_claiming_success(monkeypatch):
    from backend_ocr.app.routers import email_connection

    monkeypatch.setattr(email_connection, "_load_connection", lambda cur, t, u: None)
    monkeypatch.setattr(pn, "SMTP_USER", "")
    monkeypatch.setattr(pn, "SMTP_PASSWORD", "")
    conn, cur = _conn_cur()

    out = pn._deliver(cur, conn, tenant_id=TENANT, supplier_id=VENDOR,
                      contact=CONTACT, doc=_receipt(), actor_username="amit")

    assert out["status"] == "manual_required"
    assert "yourself" in out["reason"]


def test_a_send_is_wrapped_in_an_event_triggered_agent_run():
    conn, cur = _conn_cur()
    cur.fetchone.return_value = {"run_id": "r-1"}
    assert pn._open_run(cur, conn, TENANT, VENDOR, [{"disbursement_id": DISB}], "amit") == "r-1"

    sql = " ".join(cur.execute.call_args.args[0].split())
    # 'communication' + 'event': the Communication Agent, asked by a settled
    # payment rather than by a person — the one thing separating this run from
    # the Review Hub's user-triggered notify in the same table.
    assert "'communication', 'event'" in sql
    assert cur.execute.call_args.args[1] == (TENANT, DISB, VENDOR, "amit")


def test_one_email_per_vendor_not_one_per_bill(monkeypatch):
    """A bulk payment of four bills to one vendor is one receipt listing four."""
    conn, cur = _conn_cur()
    monkeypatch.setattr(pn, "get_conn", lambda: conn)
    monkeypatch.setattr(pn, "put_conn", lambda c, **k: None)
    conn.cursor.return_value = cur

    other = "33333333-0000-0000-0000-000000000002"
    monkeypatch.setattr(pn, "_claim", lambda *a: [
        {"disbursement_id": "d-1", "supplier_id": VENDOR},
        {"disbursement_id": "d-2", "supplier_id": VENDOR},
        {"disbursement_id": "d-3", "supplier_id": other},
    ])
    calls = []
    monkeypatch.setattr(pn, "_send_for_vendor",
                        lambda cur, conn, t, sid, rows, actor: calls.append((sid, len(rows))) or
                        {"status": "sent", "supplier_id": sid})

    out = pn.send_payment_receipt(TENANT, ["d-1", "d-2", "d-3"], "amit")

    assert sorted(calls) == [(VENDOR, 2), (other, 1)]
    assert len(out) == 2


def test_a_payment_with_no_vendor_on_it_is_recorded_not_mailed(monkeypatch):
    """Money can move against beneficiary details alone; there is no contact
    list to reach, and that is a reportable outcome, not an error."""
    conn, cur = _conn_cur()
    monkeypatch.setattr(pn, "get_conn", lambda: conn)
    monkeypatch.setattr(pn, "put_conn", lambda c, **k: None)
    conn.cursor.return_value = cur
    monkeypatch.setattr(pn, "_claim", lambda *a: [{"disbursement_id": "d-1", "supplier_id": None}])
    monkeypatch.setattr(pn, "_send_for_vendor", MagicMock(side_effect=AssertionError("must not send")))

    out = pn.send_payment_receipt(TENANT, ["d-1"], "amit")
    assert out == [{"status": "skipped", "reason": "no_vendor", "disbursement_ids": ["d-1"]}]


def test_nothing_claimable_means_nothing_happens(monkeypatch):
    """The second caller of a race, and every re-run of an already-emailed
    payment, lands here."""
    conn, cur = _conn_cur()
    monkeypatch.setattr(pn, "get_conn", lambda: conn)
    monkeypatch.setattr(pn, "put_conn", lambda c, **k: None)
    conn.cursor.return_value = cur
    monkeypatch.setattr(pn, "_claim", lambda *a: [])
    monkeypatch.setattr(pn, "_send_for_vendor", MagicMock(side_effect=AssertionError("must not send")))

    assert pn.send_payment_receipt(TENANT, [DISB], "amit") == []


def test_an_outlook_mailbox_falls_through_to_smtp_rather_than_failing(monkeypatch):
    """email_oauth.send_email is the Gmail API and there is no Graph send yet,
    so a user whose linked mailbox is Microsoft cannot send this — the receipt
    goes out over system mail instead of failing."""
    from backend_ocr.app.routers import email_connection

    monkeypatch.setattr(email_connection, "_load_connection",
                        lambda cur, t, u: {"status": "active", "provider": "microsoft",
                                           "email": "amit@acme.example"})
    monkeypatch.setattr(email_connection, "send_vendor_contact_email",
                        MagicMock(side_effect=AssertionError("Gmail cannot send this")))
    monkeypatch.setattr(pn, "SMTP_USER", "noreply@acme.example")
    monkeypatch.setattr(pn, "SMTP_PASSWORD", "secret")

    class _Server:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, *a): pass
        def sendmail(self, *a): pass

    monkeypatch.setattr(pn.smtplib, "SMTP_SSL", lambda *a, **k: _Server())
    conn, cur = _conn_cur()

    out = pn._deliver(cur, conn, tenant_id=TENANT, supplier_id=VENDOR,
                      contact=CONTACT, doc=_receipt(), actor_username="amit")
    assert out["via"] == "smtp"
