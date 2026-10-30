"""Guard tests for the licensing route map.

The map in backend_ocr/app/licensing.py decides which service each endpoint
belongs to. Two ways it can silently go wrong:

  1. Someone adds an endpoint and forgets the map — it ships ungated.
  2. Someone reorders or widens a rule and quietly re-licenses an existing
     endpoint — e.g. all of /api/bills becomes nova_billing.

`test_every_route_is_mapped` catches (1). `test_resolution_snapshot` catches (2)
by pinning the whole table to a checked-in fixture, so any change to the licensing
of an existing endpoint has to show up as a reviewable diff. This is the same
idiom frontend/src/routes/paths.test.ts uses to guard the URL registry.

Regenerate the fixture deliberately, never reflexively:
    python3.12 tests/test_license_route_map.py --write
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

FIXTURE = Path(__file__).parent / "fixtures" / "license_route_map.txt"


def _load_app():
    # pytest is imported lazily so `--write` can regenerate the fixture with a
    # bare interpreter — the backend runs on 3.12, where pytest may not be installed.
    try:
        from app.main import app
    except Exception as exc:                     # noqa: BLE001
        import pytest
        pytest.skip(f"backend app not importable here: {exc}")
    return app


def _table() -> list[str]:
    """Every (method, route-template) -> service_key, sorted and stable."""
    from app.security.licensing import service_for_route

    app = _load_app()
    rows = []
    for route in app.routes:
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            if method in ("HEAD", "OPTIONS"):
                continue
            key = service_for_route(method, route.path)
            rows.append(f"{method:7} {route.path:70} {key}")
    return sorted(set(rows))


def test_every_route_is_mapped():
    from app.security.licensing import service_for_route

    app = _load_app()
    unmapped = [
        f"{m} {r.path}"
        for r in app.routes
        for m in sorted(getattr(r, "methods", None) or {"GET"})
        if m not in ("HEAD", "OPTIONS") and service_for_route(m, r.path) is None
    ]
    assert not unmapped, (
        "These endpoints resolve to no service and would ship ungated.\n"
        "Add a rule to _RULES in backend_ocr/app/licensing.py:\n  "
        + "\n  ".join(unmapped)
    )


def test_no_route_maps_to_an_unknown_service():
    from app.security.licensing import UNLICENSED, service_for_route
    from app.security.services import SERVICE_KEYS

    app = _load_app()
    bogus = [
        f"{m} {r.path} -> {service_for_route(m, r.path)}"
        for r in app.routes
        for m in sorted(getattr(r, "methods", None) or {"GET"})
        if m not in ("HEAD", "OPTIONS")
        and service_for_route(m, r.path) not in SERVICE_KEYS
        and service_for_route(m, r.path) not in (UNLICENSED, None)
    ]
    assert not bogus, "Routes mapped to keys absent from services.SERVICES:\n  " + "\n  ".join(bogus)


def test_specific_rules_beat_the_namespace_they_sit_inside():
    """The reason the map keys on route templates rather than URL paths.

    disbursement_api mounts at /api and owns a route inside the core /api/vendors
    namespace; fx owns one inside /api/bills. Longest-match has to win, or these
    get licensed as plain vendors/bills.
    """
    from app.security.licensing import service_for_route as r

    assert r("GET", "/api/vendors/{supplier_id}/payment-profile") == "disbursement"
    assert r("GET", "/api/vendors/{supplier_id}/contacts") == "vendors"
    assert r("POST", "/api/vendors/{supplier_id}/contacts/{contact_id}/email") == "email_sending"
    assert r("POST", "/api/bills/{bill_id}/fx-payment") == "fx"
    assert r("POST", "/api/bills/{invoice_id}/payment") == "disbursement"
    assert r("GET", "/api/bills") == "bills"
    assert r("POST", "/api/settings/tds/sections") == "tds"
    assert r("GET", "/api/settings/chain") == "settings"


def test_prefix_matching_respects_segment_boundaries():
    """'/api/bills' must not claim '/api/billsomething' — the same prefix trap the
    frontend paths test guards against."""
    from app.security.licensing import _matches

    assert _matches("/api/bills", "/api/bills")
    assert _matches("/api/bills/{id}/action", "/api/bills")
    assert not _matches("/api/billsomething", "/api/bills")
    # '/' as a prefix would swallow every unmapped route and disable the alarm.
    assert _matches("/", "/")
    assert not _matches("/api/bills", "/")


def test_resolution_snapshot():
    import pytest

    current = _table()
    if not FIXTURE.exists():
        pytest.skip(f"no snapshot yet — create it with: python3.12 {__file__} --write")
    expected = FIXTURE.read_text().strip().splitlines()
    assert current == expected, (
        "The route -> service map changed.\n"
        "If intentional, review the diff carefully (it changes who can call what), "
        f"then regenerate:\n    python3.12 {__file__} --write"
    )


if __name__ == "__main__":
    if "--write" in sys.argv:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text("\n".join(_table()) + "\n")
        print(f"wrote {FIXTURE} ({len(_table())} rows)")
    else:
        print(__doc__)
