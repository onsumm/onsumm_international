"""Penny-less bank verification: `account_exists` is the verdict, not the message.

The provider returns a human-readable `message` on SUCCESS as well as on failure:

    {"account_exists": true,
     "message": "Bank Account details verified successfully.",
     "name_at_bank": "SIGNZY TECHNOLOGIES PRIVATE LIMITED"}

Treating a non-empty message as failure (`if not exists or msg`) therefore rejected
every good account and surfaced the success text as the error — "Bank account
verification failed: Bank Account details verified successfully." Nothing could reach
bank_verified through this path, which is why vendors sat unverified indefinitely.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))


def _kyb():
    try:
        from app.integrations import sandbox_kyb
    except Exception as exc:                        # noqa: BLE001
        pytest.skip(f"backend not importable here: {exc}")
    return sandbox_kyb


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


def _patch(monkeypatch, payload, status=200):
    kyb = _kyb()
    monkeypatch.setattr(kyb, "_get_token", lambda force=False: "tok")
    monkeypatch.setattr(kyb.httpx, "get", lambda *a, **k: _Resp(payload, status))
    return kyb


OK_PAYLOAD = {
    "code": 200,
    "data": {
        "account_exists": True,
        "message": "Bank Account details verified successfully.",
        "name_at_bank": "SIGNZY TECHNOLOGIES PRIVATE LIMITED",
    },
}


class TestPennyLessVerdict:
    def test_success_message_does_not_mean_failure(self, monkeypatch):
        kyb = _patch(monkeypatch, OK_PAYLOAD)
        got = kyb.verify_bank_account("915020057637889", "UTIB0000086")
        assert got["valid"] is True
        assert got.get("error") is None

    def test_beneficiary_name_is_returned_for_the_review_screen(self, monkeypatch):
        kyb = _patch(monkeypatch, OK_PAYLOAD)
        got = kyb.verify_bank_account("915020057637889", "UTIB0000086")
        assert got["name_at_bank"] == "SIGNZY TECHNOLOGIES PRIVATE LIMITED"

    def test_a_nonexistent_account_still_fails(self, monkeypatch):
        kyb = _patch(monkeypatch, {
            "code": 200,
            "data": {"account_exists": False, "message": "Invalid account number"},
        })
        got = kyb.verify_bank_account("000", "UTIB0000086")
        assert got["valid"] is False
        assert got["error"] == "Invalid account number"

    def test_a_missing_account_exists_flag_is_not_treated_as_success(self, monkeypatch):
        # Absent flag defaults to False: never assume an account is good.
        kyb = _patch(monkeypatch, {"code": 200, "data": {}})
        got = kyb.verify_bank_account("000", "UTIB0000086")
        assert got["valid"] is False
        assert got["error"] == "Account does not exist"

    def test_provider_outage_is_flagged_skipped_not_rejected(self, monkeypatch):
        # A caller must be able to tell "we could not check" from "the account is
        # bad" — showing both as a rejection teaches users to ignore real failures.
        kyb = _patch(monkeypatch, {"code": 503, "message": "Service unavailable"}, status=503)
        got = kyb.verify_bank_account("915020057637889", "UTIB0000086")
        assert got["valid"] is False
        assert got["skipped"] is True

    def test_insufficient_credits_is_also_skipped_rather_than_a_rejection(self, monkeypatch):
        kyb = _patch(monkeypatch, {"code": 402, "message": "Insufficient credit balance"}, status=402)
        got = kyb.verify_bank_account("915020057637889", "UTIB0000086")
        assert got["skipped"] is True
