"""What the Nova reconciliation panel says on a bill, and when it says nothing.

The panel sits on the approval screen, above the Approve button. Anything it
shows is read as a finding by whoever is about to approve the bill, so a state
that needs no action from them must render nothing at all.

`_skip_status` turns the orchestrator's free-text SKIPPED reason into the state
the panel switches on. The mapping is by substring, which is fragile in exactly
one direction: two different reasons name nova_vendor_mapping.json and they mean
opposite things.

  * "tenant X has no entry in nova_vendor_mapping.json" — this tenant is not a
    Nova customer. Nothing is wrong, nothing to do, show nothing.
  * "entity_type 'unowned' has no certified vendor_rates in
    nova_vendor_mapping.json — ... until one is assigned" — this tenant IS on
    Nova and its configuration is incomplete. Someone has to assign rates, so
    this stays visible.

Matching on the filename would collapse the two and silence the real one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))


@pytest.fixture()
def skip_status():
    try:
        from app.routers.billing import _skip_status
    except Exception as exc:                        # noqa: BLE001
        pytest.skip(f"backend not importable here: {exc}")
    return _skip_status


def test_a_tenant_not_on_nova_shows_nothing(skip_status):
    """The case from the screenshot: a warning naming an internal JSON file and a
    raw tenant UUID, on every bill of every tenant that does not use Nova."""
    reason = "tenant d0e00000-0000-0000-0000-000000000002 has no entry in nova_vendor_mapping.json"
    assert skip_status(reason) == "NOT_APPLICABLE"


def test_an_unconfigured_nova_entity_still_warns(skip_status):
    """Same file named, opposite meaning — this one is a real configuration gap."""
    reason = ("entity_type 'unowned' has no certified vendor_rates in "
              "nova_vendor_mapping.json — this vendor tenant cannot be reconciled "
              "until one is assigned")
    assert skip_status(reason) == "BLOCKED"


@pytest.mark.parametrize("reason,expected", [
    ("ambiguous cost centre between Brand 1 and Brand 2", "NEEDS_COST_CENTRE"),
    ("could not resolve cost-centre", "NEEDS_COST_CENTRE"),
    ("no service month on the bill", "NEEDS_MONTH"),
    ("no line items", "NEEDS_MONTH"),
    ("nova api key missing", "BLOCKED"),
    ("", "BLOCKED"),
    (None, "BLOCKED"),
])
def test_the_other_states_are_unchanged(skip_status, reason, expected):
    """These each tell the user a different lever to pull, which is why they were
    split out in the first place; suppressing the not-a-customer case must not
    have disturbed them."""
    assert skip_status(reason) == expected


def test_not_applicable_is_a_state_the_panel_renders_nothing_for():
    """Pins the contract between this module and the panel: BillsPage returns null
    for NOT_APPLICABLE, so routing a reason there is what makes it invisible."""
    panel = (_ROOT / "frontend" / "src" / "pages" / "BillsPage.tsx").read_text()
    assert "data.status === 'NOT_APPLICABLE') return null" in panel, (
        "BillsPage no longer renders nothing for NOT_APPLICABLE — _skip_status "
        "routes the not-a-Nova-customer case there expecting exactly that."
    )
