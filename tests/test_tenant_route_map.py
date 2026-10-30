"""Guard tests for tenant scoping on every route.

`auth.py` pins the authenticated tenant for the request, but acting on it is
opt-in: a handler has to call `require_tenant()` and put the value in its WHERE
clause. Eighteen handlers never did. They looked a row up by its UUID alone and
read `tenant_id` out of the row they had just fetched, which made the tenant of
the *record* the tenant of the *operation* — so any logged-in user of any tenant
could act on any record whose id they held. `POST /api/bills/{invoice_id}/action`
was the worst of them: approve, reject or send back any bill in the database.

Nothing about that is visible in a diff that adds one more endpoint, so this file
is the backstop. `test_every_route_is_tenant_scoped` fails when a route reaches
neither a `tenant: Tenant` parameter, `require_tenant()`/`current_tenant()`, nor one of the ownership helpers in
`app/tenancy.py`, walking through same-module helper functions so that indirect
scoping (`_resolve_tenant_for_supplier`, `_tid`) counts.

Reachability is a static approximation, so it is deliberately paired with the
snapshot in `test_scoping_snapshot`: the analyser proves nothing about *which*
column a handler filters on, but pinning the whole table means any change to a
route's scoping status has to show up as a reviewable diff. Same idiom as
tests/test_license_route_map.py and frontend/src/routes/paths.test.ts.

Regenerate the fixture deliberately, never reflexively:
    python3.12 tests/test_tenant_route_map.py --write
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend_ocr"))

FIXTURE = Path(__file__).parent / "fixtures" / "tenant_route_map.txt"

# Calling any of these — directly, or through a helper in the same module —
# counts as scoping the request to the authenticated tenant.
TENANT_PRIMITIVES = {
    "require_tenant",       # auth.py: the tenant from the verified JWT
    "current_tenant",       # same, nullable form
    "_tid",                 # disbursement_api's thin wrapper over require_tenant
    "assert_owned",         # tenancy.py: ownership check on a tenant-owned table
    "owned_bill",
    "owned_vendor",
    "owned_attachment",
}

# Routes that legitimately carry no tenant, each with the reason it is safe.
# Anything added here is a deliberate, reviewed exemption — not a to-do.
# Everything under /api/admin is exempt by prefix (PLATFORM_PREFIX below), not by
# an entry here: it is the platform surface, authenticated with PLATFORM_JWT_SECRET
# and carrying no tenant at all by design.
EXEMPT: dict[str, str] = {
    "/agent/version": "public agent update manifest",
    "/api/tally/pair": "pairing token, not a login token",
    "/api/tally/ingest": "authenticated by the agent API key (_require_agent)",
    "/api/bills/approve-link": "email approval — its own APPROVAL_SECRET-signed token",
    "/api/oauth/email/callback": "OAuth callback — tenant carried in the signed state param",
    # dim_fx_rates has no tenant_id: it is market reference data (INR per unit by
    # date) shared by every tenant. Note that /api/fx/agent-note is NOT here —
    # fx_agent_notes does carry tenant_id, because a note quotes the bill's own
    # exposure, so that route filters on the caller's tenant.
    "/api/fx/rates": "dim_fx_rates is global market reference data, not tenant-owned",
    "/api/fx/rate": "dim_fx_rates is global market reference data, not tenant-owned",
    "/api/fx/history": "dim_fx_rates is global market reference data, not tenant-owned",
    "/docs/oauth2-redirect": "FastAPI's built-in Swagger OAuth redirect shim",
    # Prometheus scrape target. Reads process-level counters, never tenant rows,
    # and authenticates with METRICS_TOKEN — a scraper carries no tenant JWT.
    "/metrics": "operational metrics, gated on METRICS_TOKEN; touches no tenant data",
    # Crypto rail (docs/crypto-rail). The vendor portal carries no login: its
    # tenant and vendor come from an HMAC-signed, 72-hour link token checked in
    # every handler (domain/crypto/wallets.py). Proof bundles and anchor roots are
    # meant to be checked by an auditor without an Onsumm account; a bundle is
    # reached only through an HMAC-signed share token, never a guessable id.
    "/api/vendor-portal/{token}": "signed, expiring vendor portal link token",
    "/api/vendor-portal/{token}/wallet/challenge": "signed, expiring vendor portal link token",
    "/api/vendor-portal/{token}/wallet/verify": "signed, expiring vendor portal link token",
    "/api/vendor-portal/{token}/tax-docs": "signed, expiring vendor portal link token",
    "/api/public/proofs/{share_token}": "HMAC-signed proof share token; public verification by design",
    "/api/public/anchors/{anchor_id}": "anchor roots are public on-chain data; no tenant rows returned",
}

PLATFORM_PREFIX = "/api/admin"


def _load_app():
    # pytest is imported lazily so `--write` works on a bare 3.12 interpreter.
    try:
        from app.main import app
    except Exception as exc:                     # noqa: BLE001
        import pytest
        pytest.skip(f"backend app not importable here: {exc}")
    return app


_MODULE_FUNCS: dict[str, dict[str, ast.AST]] = {}


def _module_funcs(module_name: str) -> dict[str, ast.AST]:
    """Every top-level function in a module, by name, for call-graph walking."""
    if module_name in _MODULE_FUNCS:
        return _MODULE_FUNCS[module_name]
    out: dict[str, ast.AST] = {}
    mod = sys.modules.get(module_name)
    src_file = getattr(mod, "__file__", None)
    if src_file and Path(src_file).exists():
        tree = ast.parse(Path(src_file).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.setdefault(node.name, node)
    _MODULE_FUNCS[module_name] = out
    return out


def _called_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            fn = child.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.add(fn.attr)
    return names


# The other way a handler can be scoped: `def handler(..., tenant: Tenant)`,
# where Tenant is Annotated[str, Depends(require_tenant)] from security/tenancy.py.
# FastAPI resolves it before the body runs, so it is strictly harder to get wrong
# than a call — a call can sit inside a branch that some path skips, an annotated
# parameter cannot. Matched on the annotation name because that is what the AST
# sees; tenancy.py owns the binding to require_tenant.
TENANT_ANNOTATIONS = {"Tenant"}


def _annotation_names(node: ast.AST) -> set[str]:
    """Every bare name used as a parameter annotation on a function definition."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    args = node.args
    everything = list(args.args) + list(args.posonlyargs) + list(args.kwonlyargs)
    names: set[str] = set()
    for a in everything:
        ann = a.annotation
        if isinstance(ann, ast.Name):
            names.add(ann.id)
        elif isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
            names.add(ann.value.id)
    return names


def _reaches_tenant(node: ast.AST, funcs: dict[str, ast.AST], seen: set[str] | None = None) -> bool:
    seen = seen if seen is not None else set()
    if _annotation_names(node) & TENANT_ANNOTATIONS:
        return True
    called = _called_names(node)
    if called & TENANT_PRIMITIVES:
        return True
    for name in called:
        if name in funcs and name not in seen:
            seen.add(name)
            if _reaches_tenant(funcs[name], funcs, seen):
                return True
    return False


def _endpoint_node(endpoint) -> ast.AST | None:
    """The AST node for a route's handler, matched by name and line number."""
    funcs = _module_funcs(getattr(endpoint, "__module__", "") or "")
    node = funcs.get(endpoint.__name__)
    if node is not None:
        return node
    try:                                          # decorated/wrapped handlers
        return ast.parse(inspect.getsource(endpoint).lstrip()).body[0]
    except Exception:                             # noqa: BLE001
        return None


def _classify() -> list[tuple[str, str, str]]:
    """(method, path, status) for every route. Status is one of
    scoped / exempt / public / UNSCOPED."""
    from app.security.auth import PUBLIC_PATHS

    app = _load_app()
    rows: list[tuple[str, str, str]] = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        path = getattr(route, "path", None)
        if endpoint is None or path is None:
            continue
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            if method in ("HEAD", "OPTIONS"):
                continue
            if path in PUBLIC_PATHS:
                rows.append((method, path, "public"))
            elif path in EXEMPT or path.startswith(PLATFORM_PREFIX):
                rows.append((method, path, "exempt"))
            else:
                node = _endpoint_node(endpoint)
                funcs = _module_funcs(getattr(endpoint, "__module__", "") or "")
                ok = node is not None and _reaches_tenant(node, funcs)
                rows.append((method, path, "scoped" if ok else "UNSCOPED"))
    return sorted(set(rows))


def test_every_route_is_tenant_scoped():
    unscoped = [f"{m:7} {p}" for m, p, status in _classify() if status == "UNSCOPED"]
    assert not unscoped, (
        "These routes never reach the authenticated tenant, so they act on whatever\n"
        "row the id resolves to — including another tenant's. Add the tenant to the\n"
        "WHERE clause (see backend_ocr/app/tenancy.py), or, if the route genuinely\n"
        "owns no tenant data, add it to EXEMPT here with the reason.\n  "
        + "\n  ".join(unscoped)
    )


def test_exemptions_still_exist():
    """An EXEMPT entry for a route that no longer exists is stale — and hides the
    next route that lands on that path."""
    live = {p for _, p, _ in _classify()}
    stale = sorted(set(EXEMPT) - live)
    assert not stale, "EXEMPT lists paths that are no longer routes:\n  " + "\n  ".join(stale)


def test_public_paths_are_all_real_routes():
    """A typo in PUBLIC_PATHS fails open in the other direction: the path stays
    authenticated, but the entry gives false comfort that it was reviewed."""
    from app.security.auth import PUBLIC_PATHS

    # FastAPI mounts the doc routes itself, and "/" is listed defensively for the
    # bare-host probe even though no handler claims it — none of the four is a typo.
    not_declared_here = {"/", "/docs", "/redoc", "/openapi.json"}
    live = {p for _, p, _ in _classify()} | not_declared_here
    stale = sorted(PUBLIC_PATHS - live)
    assert not stale, "PUBLIC_PATHS lists paths that are not routes:\n  " + "\n  ".join(stale)


def test_scoping_snapshot():
    import pytest

    current = [f"{m:7} {p:72} {s}" for m, p, s in _classify()]
    if not FIXTURE.exists():
        pytest.skip(f"no snapshot yet — create it with: python3.12 {__file__} --write")
    expected = FIXTURE.read_text().strip().splitlines()
    assert current == expected, (
        "A route's tenant-scoping status changed.\n"
        "If intentional, review the diff carefully (it changes who can reach whose "
        f"data), then regenerate:\n    python3.12 {__file__} --write"
    )


if __name__ == "__main__":
    if "--write" in sys.argv:
        rows = [f"{m:7} {p:72} {s}" for m, p, s in _classify()]
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text("\n".join(rows) + "\n")
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.rsplit(" ", 1)[-1]] = counts.get(r.rsplit(" ", 1)[-1], 0) + 1
        print(f"wrote {FIXTURE} ({len(rows)} rows) {counts}")
    else:
        print(__doc__)
