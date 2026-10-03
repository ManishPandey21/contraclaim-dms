"""A test that scales the source-lease self-fence down must not renew for real.

The self-fence tests shrink ``SOURCE_LEASE_RENEW_EVERY`` to tens of milliseconds
and ``SOURCE_LEASE_SELF_FENCE`` to a few hundred, then decide when renewals start
failing. When a renewal the scenario did not mean to fail goes to MongoDB, one
replica-set round trip can outlast the scaled renew bound; the ingest then
fences itself before it writes anything, and the untouched source is -
correctly - put back as settled and never tainted. The tests read that as a
product failure (about half the runs, more under load) while the product
behaved exactly as specified.

The scaled bound is the test's own construction, so the fix is in the tests: a
renewal the scenario does not mean to fail succeeds at once (the 10-minute lease
taken at acquisition has not moved toward expiry). This walks every test and
fails, with file:line, when one that scales the fence also reaches the real
``renew``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, List, Tuple

TESTS = Path(__file__).resolve().parent


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef]:
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name.startswith("test_"):
            yield node  # type: ignore[misc]


def _scales_the_fence(function: ast.AST) -> bool:
    for node in ast.walk(function):
        # monkeypatch.setattr(source_lease, "SOURCE_LEASE_SELF_FENCE", ...)
        if isinstance(node, ast.Constant) and node.value == "SOURCE_LEASE_SELF_FENCE":
            return True
        # source_lease.SOURCE_LEASE_SELF_FENCE = ...
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "SOURCE_LEASE_SELF_FENCE"
            and isinstance(node.ctx, ast.Store)
        ):
            return True
    return False


def _stubs_renew(function: ast.AST) -> bool:
    """The test replaces ``renew`` (``monkeypatch.setattr(<module>, "renew", ...)``);
    otherwise every renewal is the real one. A docstring that mentions it is not
    a stub."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "renew"
        for node in ast.walk(function)
    )


def _real_renew_references(function: ast.AST) -> List[int]:
    return [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and node.attr == "renew"
        and isinstance(node.value, ast.Name)
        and node.value.id in {"source_lease", "contract_source_lease"}
        and isinstance(node.ctx, ast.Load)
    ]


def offenders(root: Path = TESTS) -> List[Tuple[str, int]]:
    found: List[Tuple[str, int]] = []
    for path in sorted(root.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for function in _functions(tree):
            if not _scales_the_fence(function):
                continue
            name = f"{path.relative_to(root).as_posix()}::{function.name}"
            if not _stubs_renew(function):
                found.append((name, function.lineno))  # renews for real throughout
            found.extend((name, line) for line in _real_renew_references(function))
    return found


def test_no_scaled_self_fence_test_renews_for_real():
    found = offenders()
    assert not found, (
        "these tests scale SOURCE_LEASE_SELF_FENCE down and still call the real "
        "source_lease.renew, so a slow replica-set round trip fences the ingest "
        "before it writes: "
        + ", ".join(f"{name} (line {line})" for name, line in found)
    )


def test_the_guard_finds_the_shape_it_forbids(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(source_lease, 'SOURCE_LEASE_SELF_FENCE', 1)\n"
        "    monkeypatch.setattr(source_lease, 'renew', object())\n"
        "    real_renew = source_lease.renew\n",
        encoding="utf-8",
    )
    assert offenders(tmp_path) == [("test_sample.py::test_x", 4)]


def test_the_guard_finds_a_scaled_test_that_never_stubs_renew(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "def test_x():\n    source_lease.SOURCE_LEASE_SELF_FENCE = 1\n",
        encoding="utf-8",
    )
    assert offenders(tmp_path) == [("test_sample.py::test_x", 1)]


def test_the_guard_ignores_an_unscaled_real_renew(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(source_lease, 'SOURCE_LEASE_RENEW_EVERY', 1)\n"
        "    real_renew = source_lease.renew\n",
        encoding="utf-8",
    )
    assert offenders(tmp_path) == []
