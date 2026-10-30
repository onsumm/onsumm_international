"""Vendor card revamp: typed vendor documents and the vendor inbox.

- vendor_documents._clean_type: what a document upload may be filed as.
- email_connection._summarize_headers / _thread_involves_vendor: who started a
  thread, whether it needs a reply, and refusing threads that aren't with the
  vendor (the inbox reads the user's whole mailbox, so this is the guard).
- email_connection._caller: mailbox routes act as the signed-in user, not
  whoever the query string names.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend_ocr.app.routers import email_connection as ec
from backend_ocr.app.routers import vendor_documents as vd


class TestCleanType:
    def test_defaults_to_contract(self):
        assert vd._clean_type(None, None) == ("contract", None)

    def test_contract_drops_any_label(self):
        assert vd._clean_type("contract", "Rate cards") == ("contract", None)

    def test_other_keeps_a_tidied_label(self):
        assert vd._clean_type("Other", "  Rate   cards ") == ("other", "Rate cards")

    def test_other_needs_a_name(self):
        with pytest.raises(HTTPException) as e:
            vd._clean_type("other", "   ")
        assert e.value.status_code == 422

    def test_unknown_type_is_refused(self):
        with pytest.raises(HTTPException):
            vd._clean_type("bill", None)

    def test_label_is_capped(self):
        assert len(vd._clean_type("other", "x" * 200)[1]) == 80


CONTACTS = {"priya@acme.in": {"id": "c1", "name": "Priya"}}
DOMAINS = {"acme.in"}
ME = "ananya@buyer.com"


def msg(frm, to, ms, attach=False):
    return {"id": f"m{ms}", "from": frm, "to": to, "cc": "", "subject": "Rate card", "date": "",
            "snippet": "hi", "internal_date": ms, "has_attachment": attach}


class TestThreadSummary:
    def test_vendor_started_and_waiting_on_us(self):
        msgs = [msg("Priya Nair <priya@acme.in>", ME, 1000, attach=True)]
        t = ec._summarize_headers("t1", "", msgs, ME, CONTACTS, DOMAINS)
        assert t["started_by"] == "vendor"
        assert t["needs_reply"] is True
        assert t["contact_id"] == "c1"
        assert t["has_attachments"] is True
        assert t["started_by_name"] == "Priya Nair"

    def test_we_started_and_they_replied(self):
        msgs = [msg(f"Ananya <{ME}>", "priya@acme.in", 1000), msg("priya@acme.in", ME, 2000)]
        t = ec._summarize_headers("t2", "", msgs, ME, CONTACTS, DOMAINS)
        assert t["started_by"] == "us"
        assert t["needs_reply"] is True
        assert t["message_count"] == 2

    def test_we_replied_last(self):
        msgs = [msg("priya@acme.in", ME, 1000), msg(ME, "priya@acme.in", 2000)]
        assert ec._summarize_headers("t3", "", msgs, ME, CONTACTS, DOMAINS)["needs_reply"] is False

    def test_new_person_at_the_vendor_is_flagged(self):
        msgs = [msg("Ravi <ravi@acme.in>", ME, 1000)]
        t = ec._summarize_headers("t4", "", msgs, ME, CONTACTS, DOMAINS)
        assert t["unknown_senders"] == ["ravi@acme.in"]
        assert t["contact_id"] is None


class TestThreadBelongsToVendor:
    def test_contact_address_matches(self):
        assert ec._thread_involves_vendor([msg("priya@acme.in", ME, 1)], CONTACTS, DOMAINS)

    def test_company_domain_matches(self):
        assert ec._thread_involves_vendor([msg(ME, "ops@acme.in", 1)], CONTACTS, DOMAINS)

    def test_unrelated_thread_is_refused(self):
        assert not ec._thread_involves_vendor([msg("boss@buyer.com", ME, 1)], CONTACTS, DOMAINS)


class TestCaller:
    def test_jwt_user_wins_over_the_query_string(self):
        with patch.object(ec, "current_username", return_value="ananya"):
            assert ec._caller("someone-else") == "ananya"

    def test_falls_back_to_the_parameter_without_a_subject(self):
        with patch.object(ec, "current_username", return_value=None):
            assert ec._caller("ananya") == "ananya"

    def test_nobody_is_refused(self):
        with patch.object(ec, "current_username", return_value=None):
            with pytest.raises(HTTPException):
                ec._caller(None)


class TestInboxPolish:
    """The inbox names the vendor person (not us), tracks done/waiting, and parses recipients."""

    def test_counterparty_is_the_vendor_side_never_us(self):
        msgs = [msg(f"Ananya <{ME}>", "Priya Nair <priya@acme.in>", 1000), msg(f"Ananya <{ME}>", "priya@acme.in", 2000)]
        t = ec._summarize_headers("t5", "", msgs, ME, CONTACTS, DOMAINS)
        assert t["counterparty"] == {"email": "priya@acme.in", "name": "Priya Nair"}
        names = {p["email"]: p for p in t["participants"]}
        assert names[ME]["me"] is True and names["priya@acme.in"]["vendor"] is True

    def test_status_needs_reply_waiting_and_done(self):
        from datetime import datetime, timezone, timedelta
        base = {"needs_reply": True, "last_at": "2026-10-10T10:00:00+00:00"}
        assert ec._apply_status(dict(base), None)["status"] == "needs_reply"
        assert ec._apply_status({**base, "needs_reply": False}, None)["status"] == "waiting"
        later = {"status": "resolved", "updated_at": datetime(2026, 10, 10, 11, tzinfo=timezone.utc)}
        done = ec._apply_status(dict(base), later)
        assert done["status"] == "done" and done["needs_reply"] is False
        # A message after it was marked done reopens it.
        earlier = {**later, "updated_at": datetime(2026, 10, 10, 9, tzinfo=timezone.utc)}
        assert ec._apply_status(dict(base), earlier)["status"] == "needs_reply"

    def test_address_list_parses_names_and_rejects_junk(self):
        assert ec._address_list("Priya <Priya@Acme.in>, ops@acme.in, priya@acme.in") == ["priya@acme.in", "ops@acme.in"]
        assert ec._address_list("") == []
        with pytest.raises(HTTPException):
            ec._address_list("not-an-email")
