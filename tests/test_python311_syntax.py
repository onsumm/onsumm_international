"""Nothing in this repo may use syntax the deployed interpreter rejects.

The servers run Python 3.11. Development here runs 3.12, and the two disagree
about one thing that is easy to write by accident: PEP 701 let f-string
*expressions* contain backslashes in 3.12, and 3.11 raises

    SyntaxError: f-string expression part cannot include a backslash

at import time. So a file can pass every test locally and stop the API from
booting on the box — which is exactly what happened to
routers/review_hub.py::apply_bill_coding, over a `\\u2014` inside an f-string.

`ast.parse(..., feature_version=(3, 11))` does NOT catch this; the 3.12 parser
ignores feature_version for f-strings. Hence the scan below.

This is a backstop, not a substitute for running the suite on 3.11.
"""
from __future__ import annotations

import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build"}

def _sources() -> list[Path]:
    return [
        p for p in ROOT.rglob("*.py")
        if not (SKIP & set(p.parts))
    ]


def _offending_fstrings(path: Path) -> list[tuple[int, str]]:
    """Line numbers of f-strings with a backslash inside a {...} expression.

    3.12 tokenises an f-string into FSTRING_START, the expression tokens, the
    literal text as FSTRING_MIDDLE, and FSTRING_END — there is no single token
    holding the whole thing, which is what a first attempt at this scan assumed
    and why it found nothing. So: anything between START and END that is not
    FSTRING_MIDDLE is expression, and a backslash there is the error.

    Nested f-strings are counted rather than flagged, because 3.12 permits them
    and the depth is what says whether we are still inside one.
    """
    out: list[tuple[int, str]] = []
    try:
        with open(path, "rb") as fh:
            tokens = list(tokenize.tokenize(fh.readline))
    except (tokenize.TokenError, SyntaxError):
        # A file 3.12 cannot even tokenize is a different problem, and one the
        # rest of the suite reports far more clearly.
        return out

    depth = 0
    for tok in tokens:
        name = tokenize.tok_name[tok.type]
        if name == "FSTRING_START":
            depth += 1
            continue
        if name == "FSTRING_END":
            depth = max(depth - 1, 0)
            continue
        if depth and name != "FSTRING_MIDDLE" and "\\" in tok.string:
            out.append((tok.start[0], tok.line.strip()))
    return out


def test_no_backslash_inside_an_fstring_expression():
    offenders = [
        f"{p.relative_to(ROOT)}:{line}  {text[:80]}"
        for p in _sources()
        for line, text in _offending_fstrings(p)
    ]
    assert not offenders, (
        "Python 3.11 rejects a backslash inside an f-string expression, and the "
        "servers run 3.11 — these would fail at import:\n  "
        + "\n  ".join(offenders)
    )


def test_the_scan_would_have_caught_the_one_that_got_through():
    """The line that took the dev API down, so the guard is checked against a
    real failure rather than a hypothetical one."""
    bad = 'f"{p.get(\'current\') or \'\\u2014\'} x"'
    src = f"y = {bad}\n"
    tmp = ROOT / "tests" / "_tmp_fstring_probe.py"
    tmp.write_text(src, encoding="utf-8")
    try:
        assert _offending_fstrings(tmp), "the guard would not have caught it"
    finally:
        tmp.unlink()

    # And it does not cry wolf over the ordinary cases.
    ok = ROOT / "tests" / "_tmp_fstring_ok.py"
    ok.write_text(
        'a = f"line\\n{x}"\n'          # backslash outside the expression
        'b = f"{{literal}} {y}"\n'      # escaped braces
        'c = f"{z!r:>10}"\n',           # conversions and format specs
        encoding="utf-8",
    )
    try:
        assert not _offending_fstrings(ok)
    finally:
        ok.unlink()


def test_every_file_parses():
    """Cheap catch-all for anything else that stops an import dead."""
    import ast

    broken = []
    for p in _sources():
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            broken.append(f"{p.relative_to(ROOT)}:{exc.lineno} {exc.msg}")
    assert not broken, "\n  ".join(broken)
