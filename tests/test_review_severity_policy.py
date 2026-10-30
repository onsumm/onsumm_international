"""Every approval finding is stored at P2 (MEDIUM), whatever its detector said."""
from __future__ import annotations

from types import SimpleNamespace

from backend_ocr.app.review.store import FAMILY_SEVERITY, policy_severity


def _draft(family: str, severity: str):
    # policy_severity reads only these two fields.
    return SimpleNamespace(family=family, severity=severity)


def test_approval_family_is_always_p2():
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        assert policy_severity(_draft("approval", sev)) == "MEDIUM"


def test_other_families_keep_their_severity():
    for family in ("payment", "tax", "vendor_identity"):
        assert family not in FAMILY_SEVERITY
        assert policy_severity(_draft(family, "CRITICAL")) == "CRITICAL"
