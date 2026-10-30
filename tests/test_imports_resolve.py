"""Every relative import in the backend package must point at a real module.

Why this exists
───────────────
Splitting main.py into `routers/` and then grouping the rest into `security/`,
`core/`, `integrations/` and `domain/` rewrote hundreds of relative imports. The
module-level ones were proved correct the moment the app imported. The
*function-local* ones were not: three of them pointed at modules that had moved,
and nothing noticed —

    routers/bills.py          from ..routers.ingest      -> app/routers/ingest
    routers/vendor_banking.py from ..routers.sandbox_kyb  -> app/routers/sandbox_kyb
    routers/vendor_gstins.py  from ..routers.sandbox_kyb  -> app/routers/sandbox_kyb

`from app.main import app` succeeded, all 214 tests passed, and the route-map
snapshots were green, because an import inside a function body does not run until
that function does. The three endpoints behind them — `PUT /api/bills/{id}`,
vendor bank verification and GSTIN re-verification — raised ModuleNotFoundError
on first call. They shipped that way.

A static check is the right shape for this: it needs no database, no request, and
it covers code paths no test exercises. Importing every module (below) is the
weaker half; resolving every import statement is the half that catches the bug.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend_ocr"
sys.path.insert(0, str(_BACKEND))

APP = _BACKEND / "app"


def _modules() -> list[Path]:
    return [p for p in sorted(APP.rglob("*.py")) if "__pycache__" not in str(p)]


def _unresolved(path: Path) -> list[str]:
    """Relative imports in one file whose target does not exist on disk."""
    parts = path.relative_to(APP).with_suffix("").parts
    package = parts[:-1]                       # the module's own package
    problems: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        up = node.level - 1                    # `.` is own package, `..` is parent
        if up > len(package):
            problems.append(f"line {node.lineno}: {'.' * node.level}{node.module or ''} escapes the package")
            continue
        target = list(package[: len(package) - up])
        target += node.module.split(".") if node.module else []
        if not target:
            continue
        if (APP.joinpath(*target).with_suffix(".py").exists()
                or APP.joinpath(*target, "__init__.py").exists()):
            continue
        # `from ..pkg import name` where name is the module, not an attribute
        parent = APP.joinpath(*target)
        if parent.is_dir() and all(
            (parent / f"{a.name}.py").exists() or (parent / a.name / "__init__.py").exists()
            or (parent / "__init__.py").exists()
            for a in node.names
        ):
            continue
        problems.append(
            f"line {node.lineno}: from {'.' * node.level}{node.module or ''} "
            f"-> app/{'/'.join(target)} (no such module)"
        )
    return problems


def test_every_relative_import_resolves():
    """Catches a moved module that only a function-local import references."""
    broken = {
        str(p.relative_to(_BACKEND)): probs
        for p in _modules()
        if (probs := _unresolved(p))
    }
    assert not broken, (
        "These relative imports point at modules that do not exist. An import "
        "inside a function body does not run until the endpoint is called, so the "
        "app will start and the tests will pass while the endpoint 500s:\n"
        + "\n".join(f"  {f}\n    " + "\n    ".join(v) for f, v in broken.items())
    )


def test_every_module_imports():
    """The weaker half, kept because it also catches syntax errors, circular
    imports and bad third-party names that a static scan cannot see."""
    try:
        import app                                     # noqa: F401
    except Exception as exc:                           # noqa: BLE001
        pytest.skip(f"backend package not importable here: {exc}")

    failures = []
    for mod in pkgutil.walk_packages(app.__path__, prefix="app."):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:                       # noqa: BLE001
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")
    assert not failures, "Modules that fail to import:\n  " + "\n  ".join(failures)
