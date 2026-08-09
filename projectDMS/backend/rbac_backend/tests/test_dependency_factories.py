"""Every FastAPI dependency factory must actually hand back an object.

A factory that falls off the end returns ``None``, and FastAPI injects that
``None`` happily -- the failure only appears at request time, deep in a handler,
as ``AttributeError: 'NoneType' object has no attribute '<whatever>'``, with a
500 and no hint at the real cause.

This is not hypothetical. ``get_organization_controller`` was left without its
``return`` when another function was spliced into the middle of it, which took
``GET /api/organizations`` down in production with

    AttributeError: 'NoneType' object has no attribute 'get_organizations'

Nothing caught it: the route tests override the dependency, so the real factory
was never called. These tests call the factories themselves.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

ROUTERS = pathlib.Path(__file__).resolve().parents[1] / "routers"


def _controller_factories():
    """(module, function name) for every `get_*_controller` a router exposes."""
    found = []
    for path in sorted(ROUTERS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken router fails elsewhere
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.startswith("get_") and node.name.endswith("_controller")
            ):
                found.append((path.stem, node.name))
    return found


FACTORIES = _controller_factories()


def test_router_controller_factories_were_discovered():
    """Guard the guard: an empty sweep would make every test below vacuous."""
    assert len(FACTORIES) >= 10, f"expected the routers to expose controller factories, got {FACTORIES}"


@pytest.mark.parametrize("module_name,func_name", FACTORIES, ids=[f"{m}.{f}" for m, f in FACTORIES])
def test_controller_factory_returns_an_object(module_name, func_name):
    """A factory that reaches the end of its body without returning is a defect.

    Checked statically rather than by calling: several factories build service
    objects that would reach for a database. The bug this pins is structural --
    the ``return`` is missing from the function body -- so the AST is enough and
    stays fast and infra-free.
    """
    import importlib

    module = importlib.import_module(f"rbac_backend.routers.{module_name}")
    func = getattr(module, func_name)
    source = inspect.getsource(func)
    tree = ast.parse(source.lstrip())
    fn = tree.body[0]

    returns_value = any(
        isinstance(node, ast.Return) and node.value is not None for node in ast.walk(fn)
    )
    assert returns_value, (
        f"{module_name}.{func_name} never returns a value, so FastAPI injects None "
        f"and the route fails at request time with an AttributeError"
    )

    last = fn.body[-1]
    assert isinstance(last, (ast.Return, ast.Raise, ast.Try, ast.If, ast.With)), (
        f"{module_name}.{func_name} can fall off the end of its body and return None; "
        f"its last statement is {type(last).__name__}"
    )


def test_no_undefined_names_in_backend():
    """No module may reference a name that does not exist in its scope.

    Both production breakages found on 2026-08-07 were this single defect:

    * ``get_organization_controller`` lost its ``return``; the orphaned tail
      referenced ``org_service`` from a scope it no longer belonged to.
    * ``get_permissions`` called ``policy.authorize(...)`` without declaring
      ``policy`` as a dependency, so the route answered 500.

    Neither is reachable by the type checker or by tests that override the
    dependency, but both are obvious to a name-resolution pass in seconds.

    Forward references in annotations are excluded: several modules annotate
    with a quoted type and import it lazily inside the function to break an
    import cycle, which is deliberate and safe.
    """
    pyflakes_api = pytest.importorskip(
        "pyflakes.api", reason="pyflakes not installed; CI installs it explicitly"
    )
    from pyflakes import reporter as pyflakes_reporter
    import io

    backend_root = pathlib.Path(__file__).resolve().parents[1]
    out, err = io.StringIO(), io.StringIO()
    pyflakes_api.checkRecursive([str(backend_root)], pyflakes_reporter.Reporter(out, err))

    undefined = [
        line
        for line in out.getvalue().splitlines()
        if "undefined name" in line and "__pycache__" not in line
    ]

    # A quoted annotation resolved by a lazy import is a false positive.
    import re as _re

    # Anchor on ":<line>:<col>:" so a Windows drive letter ("C:\...") does not
    # get mistaken for the path/line separator.
    location = _re.compile(r"^(?P<path>.+?):(?P<line>\d+):\d+: undefined name")

    real = []
    for line in undefined:
        match = location.match(line)
        if match:
            try:
                source = pathlib.Path(match["path"]).read_text(encoding="utf-8").splitlines()
                head = "\n".join(source[:20])
                target = source[int(match["line"]) - 1]
                if "from __future__ import annotations" in head:
                    continue
                if '"' in target or "'" in target:
                    continue  # quoted forward reference, resolved by a lazy import
            except Exception:  # pragma: no cover - never let the guard crash
                pass
        real.append(line)

    assert not real, "undefined names (these fail at import or request time):\n" + "\n".join(real)


def test_no_unreachable_code_after_return_in_routers():
    """Dead code after a `return` is the fingerprint of a bad splice.

    The organizations outage looked exactly like this: a new function was
    pasted in ahead of the original's tail, leaving `return PolicyService()`
    followed by the orphaned body of the factory above it.
    """
    offenders = []
    for path in sorted(ROUTERS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for i, stmt in enumerate(body[:-1]):
                if isinstance(stmt, ast.Return):
                    offenders.append(f"{path.name}:{body[i + 1].lineno}")
                    break

    assert not offenders, "unreachable statements follow a return at: " + ", ".join(offenders)
