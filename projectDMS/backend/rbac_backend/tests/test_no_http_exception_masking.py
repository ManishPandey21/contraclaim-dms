"""Static lint: routers must not mask HTTPException as 500 "unavailable".

Regression guard for the intermittent fake-outage bug class: an
``except Exception`` handler that re-raises as ``HTTPException(500 ...)``
swallows in-flight ``HTTPException``s (rate-limit 429s, scope 403s, 404s)
unless an earlier handler in the same ``try`` re-raises them. This test walks
every router's AST and fails when the pattern reappears, naming the file and
line so the fix is obvious: add ``except HTTPException: raise`` above the
catch-all (or widen an existing ``except FooError: raise`` to a tuple).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTERS_DIR = Path(__file__).resolve().parents[1] / "routers"


def _is_5xx_http_raise(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
        return False
    func = node.exc.func
    name = getattr(func, "id", None) or getattr(func, "attr", None)
    if name != "HTTPException":
        return False
    candidates = [kw.value for kw in node.exc.keywords if kw.arg == "status_code"]
    if not candidates and node.exc.args:
        candidates = [node.exc.args[0]]
    for value in candidates:
        if isinstance(value, ast.Constant) and isinstance(value.value, int):
            return 500 <= value.value < 600
        attr = getattr(value, "attr", "")
        if "INTERNAL_SERVER_ERROR" in attr or attr.startswith("HTTP_5"):
            return True
    return False


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    t = handler.type
    if t is None:
        return {"<bare>"}
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    return {getattr(e, "id", None) or getattr(e, "attr", "") for e in elts}


def _masking_offences(tree: ast.AST) -> list[int]:
    offences: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        guarded = False
        for handler in node.handlers:
            names = _handler_names(handler)
            if "Exception" not in names and "<bare>" not in names:
                if "HTTPException" in names:
                    guarded = True
                continue
            if not guarded and any(_is_5xx_http_raise(n) for n in ast.walk(handler)):
                offences.append(handler.lineno)
            break
    return offences


def test_routers_do_not_mask_http_exceptions_as_500():
    offences: list[str] = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno in _masking_offences(tree):
            offences.append(f"{path.name}:{lineno}")
    assert not offences, (
        "except-Exception handlers that rewrap as HTTP 500 without an "
        "`except HTTPException: raise` guard above them (they turn 429/403/404 "
        "into fake outages):\n  " + "\n  ".join(offences)
    )
