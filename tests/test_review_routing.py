"""The Review Hub's audience computation must agree with what the API enforces.

This is the test that matters most in this file. backend_ocr/app/core/approval_chain.py
decides who may act on a bill at a given pending status, and
backend_ocr/app/routers/bill_approvals.py 403s anyone else. backend_ocr/app/review/routing.py
is the asyncpg twin used to address findings. If the two ever disagree, the hub
tells someone to approve a bill the API then refuses them for — and the user
concludes the hub is wrong rather than that the chain config is.

Both are pure given a chain, so they can be compared directly with no database.
"""
from __future__ import annotations

import pytest

from backend_ocr.app.core import approval_chain as api_chain
from backend_ocr.app.review import routing as pipe

# Chains worth pinning: the default, a renamed/reordered one, one with named
# assignees, one with more steps than there are canonical status names, and one
# where two steps share a role (the case the "index-based" comments call out).
CHAINS = {
    "default": list(pipe.DEFAULT_CHAIN),
    "no_locked_step": [
        {"step_order": 0, "label": "Approver", "role": "approver", "locked": False},
        {"step_order": 1, "label": "FC", "role": "finance_controller", "locked": False},
    ],
    "with_assignees": [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True},
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
         "assignees": ["Rhea Nair", " Vikram Singh "]},
        {"step_order": 2, "label": "FC", "role": "finance_controller", "locked": False,
         "assignees": []},
    ],
    "overflow_past_named_statuses": [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True},
        {"step_order": 1, "label": "A", "role": "approver", "locked": False},
        {"step_order": 2, "label": "B", "role": "accountant", "locked": False},
        {"step_order": 3, "label": "C", "role": "finance_controller", "locked": False},
        {"step_order": 4, "label": "D", "role": "finance_controller", "locked": False},
        {"step_order": 5, "label": "E", "role": "admin", "locked": False},
    ],
    "duplicate_roles": [
        {"step_order": 0, "label": "One", "role": "approver", "locked": False},
        {"step_order": 1, "label": "Two", "role": "approver", "locked": False},
    ],
}


@pytest.fixture
def pinned_chain(monkeypatch):
    """Pin approval_chain's DB loader so both sides see the same chain."""
    def _pin(chain):
        monkeypatch.setattr(api_chain, "_load_chain", lambda *a, **k: chain)
    return _pin


@pytest.mark.parametrize("name", sorted(CHAINS))
def test_role_map_matches_the_api(name, pinned_chain):
    chain = CHAINS[name]
    pinned_chain(chain)
    assert pipe.status_role_map(chain) == api_chain._build_status_role_map("t"), (
        f"chain {name!r}: the hub would address a different set of roles than "
        f"bill_approvals.py authorises"
    )


@pytest.mark.parametrize("name", sorted(CHAINS))
def test_name_map_matches_the_api(name, pinned_chain):
    chain = CHAINS[name]
    pinned_chain(chain)
    assert pipe.status_name_map(chain) == api_chain._build_status_name_map("t"), (
        f"chain {name!r}: the hub would address different named assignees"
    )


def test_status_naming_matches_the_api():
    for idx in range(8):
        assert pipe.step_idx_to_status(idx) == api_chain._step_idx_to_status(idx)


def test_default_chain_matches_the_apis_default():
    """Duplicated rather than imported, because tenant_defaults pulls in bcrypt and
    the pipeline has no reason to. This is what keeps the copy honest."""
    from backend_ocr.app.domain.tenant_defaults import DEFAULT_APPROVAL_CHAIN
    assert [dict(s) for s in pipe.DEFAULT_CHAIN] == [dict(s) for s in DEFAULT_APPROVAL_CHAIN]


def test_assignee_names_are_whitespace_normalised():
    """A stray trailing space in tenant_chain_config must not stop a correctly
    named actor matching — the API strips, so the hub must strip identically."""
    chain = CHAINS["with_assignees"]
    names = pipe.status_name_map(chain)["PENDING_APPROVER"]
    assert names == ["Rhea Nair", "Vikram Singh"]


def test_admin_is_always_in_the_audience():
    for chain in CHAINS.values():
        for roles in pipe.status_role_map(chain).values():
            assert "admin" in roles


def test_locked_steps_never_produce_a_status():
    """A locked step is 'Submitted' — nobody is waiting on it, so it must not
    generate a pending status or the hub invents work that does not exist."""
    chain = CHAINS["with_assignees"]
    statuses = set(pipe.status_role_map(chain))
    assert statuses == {"PENDING_APPROVER", "PENDING_ACCOUNTANT"}


# ── Capability model ──────────────────────────────────────────────────────────

def test_auditor_sees_everything_and_can_change_nothing():
    from backend_ocr.app.security.capabilities import can, GLOBAL_VIEW_ROLES
    assert "auditor" in GLOBAL_VIEW_ROLES
    assert can("view", "auditor")
    for verb in ("edit", "approve", "verify", "pay", "admin"):
        assert not can(verb, "auditor"), f"auditor must not have {verb}"


def test_member_can_view_but_not_fix():
    from backend_ocr.app.security.capabilities import can
    assert can("view", "member")
    assert not can("edit", "member")
    assert not can("pay", "member")


def test_only_admin_and_fc_can_pay():
    from backend_ocr.app.security.capabilities import CAPABILITY_ROLES
    assert CAPABILITY_ROLES["pay"] == frozenset({"admin", "finance_controller"})


def test_unknown_role_gets_nothing():
    from backend_ocr.app.security.capabilities import can, capabilities_for
    assert not can("view", "not_a_real_role")
    assert capabilities_for("not_a_real_role") == []


# ── The jsonb boundary ────────────────────────────────────────────────────────
# asyncpg hands back jsonb as TEXT where psycopg2 hands back a list. That
# difference caused three separate bugs, the worst of which was
# `", ".join(assignees)` iterating an assignee list character by character —
# which made every approval step look unassigned and produced a page of false
# CRITICAL findings on real data. Decoding happens once, in load_chain.

def test_jsonb_decodes_a_string_the_way_psycopg2_would():
    from backend_ocr.app.review.types import jsonb
    assert jsonb('["Mohd Salman"]') == ["Mohd Salman"]
    assert jsonb('{"a": 1}') == {"a": 1}
    # Already decoded (psycopg2, or a test fixture) passes straight through.
    assert jsonb(["Mohd Salman"]) == ["Mohd Salman"]


def test_jsonb_falls_back_rather_than_raising():
    from backend_ocr.app.review.types import jsonb
    assert jsonb(None, default=[]) == []
    assert jsonb("not json at all", default={}) == {}
    assert jsonb("", default=[]) == []


def test_a_json_encoded_assignee_list_is_not_iterated_as_characters():
    """The exact production failure: a step assigned to a real, active user was
    reported as having no valid assignee, because the name was compared one
    letter at a time."""
    from backend_ocr.app.review.routing import _decode, status_name_map

    row = {"step_order": 1, "label": "Approver", "role": "approver",
           "locked": False, "assignees": '["Mohd Salman"]'}
    chain = [_decode(row)]
    names = status_name_map(chain)["PENDING_APPROVER"]
    assert names == ["Mohd Salman"], f"got {names!r}"
    # An active user with that name must match.
    assert set(names) & {"Mohd Salman"}


def test_decode_handles_a_null_assignee_list():
    from backend_ocr.app.review.routing import _decode, status_name_map
    chain = [_decode({"step_order": 1, "label": "A", "role": "approver",
                      "locked": False, "assignees": None})]
    # None means "any holder of the role", not "nobody".
    assert status_name_map(chain)["PENDING_APPROVER"] is None


# ── default_notify_target (§5.3.2) ──────────────────────────────────────────
# A suggestion only — the human still confirms the destination before anything
# sends — but it has to pick the *right* suggestion in the documented order:
# named assignee on a blocked approval step, else the first audience role with
# an active user, else finance_controller.

from unittest.mock import AsyncMock  # noqa: E402


def _fake_conn(*, chain_rows=(), user_rows_by_role=None):
    """asyncpg-shaped mock: .fetch for load_chain, .fetchrow for the user
    lookup, keyed by the role passed as the second bind parameter."""
    user_rows_by_role = user_rows_by_role or {}

    async def _fetchrow(sql, tenant, role):
        return user_rows_by_role.get(role)

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=list(chain_rows))
    conn.fetchrow = AsyncMock(side_effect=_fetchrow)
    return conn


@pytest.mark.asyncio
async def test_notify_target_prefers_the_named_assignee_on_a_blocked_step():
    chain_rows = [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True,
         "assignees": None},
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
         "assignees": '["Rhea Nair"]'},
    ]
    conn = _fake_conn(chain_rows=chain_rows)

    target = await pipe.default_notify_target(
        conn, "t", audience_roles=("finance_controller",), bill_status="PENDING_APPROVER",
    )

    assert target == {"kind": "user", "value": "Rhea Nair", "label": "Rhea Nair"}
    conn.fetchrow.assert_not_called()  # never falls through once a name is found


@pytest.mark.asyncio
async def test_notify_target_falls_through_when_the_step_names_nobody():
    chain_rows = [
        {"step_order": 0, "label": "Approver", "role": "approver", "locked": False,
         "assignees": None},
    ]
    conn = _fake_conn(
        chain_rows=chain_rows,
        user_rows_by_role={"accountant": {"username": "rverma", "name": "Rahul Verma"}},
    )

    target = await pipe.default_notify_target(
        conn, "t", audience_roles=("accountant",), bill_status="PENDING_APPROVER",
    )

    assert target == {"kind": "user", "value": "rverma", "label": "Rahul Verma"}


@pytest.mark.asyncio
async def test_notify_target_uses_the_first_audience_role_with_an_active_user():
    conn = _fake_conn(
        user_rows_by_role={"finance_controller": {"username": "priya", "name": "Priya Sharma"}},
    )

    target = await pipe.default_notify_target(
        conn, "t", audience_roles=("accountant", "finance_controller"),
    )

    # accountant had nobody; finance_controller did.
    assert target == {"kind": "user", "value": "priya", "label": "Priya Sharma"}


@pytest.mark.asyncio
async def test_notify_target_falls_back_to_finance_controller_role():
    conn = _fake_conn()

    target = await pipe.default_notify_target(conn, "t", audience_roles=("accountant",))

    assert target == {"kind": "role", "value": "finance_controller",
                       "label": "Finance Controller"}


@pytest.mark.asyncio
async def test_notify_target_falls_back_when_a_user_row_has_no_display_name():
    conn = _fake_conn(
        user_rows_by_role={"admin": {"username": "svc_admin", "name": None}},
    )

    target = await pipe.default_notify_target(conn, "t", audience_roles=("admin",))

    assert target == {"kind": "user", "value": "svc_admin", "label": "svc_admin"}


def test_decoded_chains_still_agree_with_the_api(pinned_chain):
    """The consistency guarantee has to survive the decode step."""
    from backend_ocr.app.review.routing import _decode
    raw = [
        {"step_order": 0, "label": "Submitted", "role": "member", "locked": True,
         "assignees": None},
        {"step_order": 1, "label": "Approver", "role": "approver", "locked": False,
         "assignees": '["Rhea Nair", " Vikram Singh "]'},
    ]
    chain = [_decode(r) for r in raw]
    pinned_chain(chain)
    assert pipe.status_name_map(chain) == api_chain._build_status_name_map("t")
    assert pipe.status_name_map(chain)["PENDING_APPROVER"] == ["Rhea Nair", "Vikram Singh"]
