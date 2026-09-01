"""Static guard: a backfill real-Mongo suite may not stub out target authority.

Every accepted claim about the legacy backfill — transactionality, idempotency,
concurrency fencing, audit correctness — is made by the real-Mongo integration
suites. Those suites drive the production orchestrator, and the orchestrator
reaches the per-adapter manage/view/freeze permission through exactly one seam:

    DocumentRelationshipService._target
      -> PolicyService.authorize_document(actor, permission, entity, ...)

A test policy double whose `authorize_document` is `return None` removes that
seam entirely. The suite still passes, still writes real rows, still proves the
transaction — and proves nothing at all about who was allowed to do it. That is
the failure this guard exists to make un-mergeable.

This is deliberately narrow. Isolated unit tests may still use permissive
doubles: they are not the evidence for an authority claim. The rule is that
every suite driving the backfill against real Mongo must exercise the real
boundary.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

INTEGRATION_DIR = (
    Path(__file__).resolve().parent / "integration"
)

#: The module every backfill integration suite drives.
BACKFILL_ROUTER_MARKER = "legacy_relationship_backfill"


def _backfill_integration_suites() -> list[Path]:
    """Every real-Mongo suite that drives the backfill control plane.

    Discovered, never hand-listed: a new module suite is covered the day it is
    written, not the day someone remembers to add it here.
    """
    suites = []
    for path in sorted(INTEGRATION_DIR.glob("test_*.py")):
        source = io.open(path, encoding="utf-8").read()
        if BACKFILL_ROUTER_MARKER in source:
            suites.append(path)
    return suites


def _authorize_document_methods(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == "authorize_document"
            ):
                yield node, item


def _is_enforcing(method: ast.AST) -> bool:
    """A method enforces when it can refuse.

    Either it delegates to the double's own permission check, or it raises on
    its own. A body that only returns/passes cannot refuse anything.
    """
    for node in ast.walk(method):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {
                "authorize",
                "authorize_document",
            }:
                return True
    return False


def test_backfill_integration_suites_are_discoverable() -> None:
    """Guard the guard: if discovery silently matched nothing, the real check
    below would pass vacuously."""
    suites = _backfill_integration_suites()
    assert len(suites) >= 5, [path.name for path in suites]


def test_no_backfill_integration_suite_stubs_out_target_authority() -> None:
    offenders: list[str] = []
    for path in _backfill_integration_suites():
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for klass, method in _authorize_document_methods(tree):
            if not _is_enforcing(method):
                offenders.append(f"{path.name}:{method.lineno} {klass.name}")

    assert offenders == [], (
        "These real-Mongo backfill suites stub target authorization to a no-op, "
        "so their authority claims are unproven: " + "; ".join(offenders)
    )


def test_the_delegated_permission_check_can_actually_refuse() -> None:
    """Close the obvious way around the check above.

    `authorize_document` delegating to `self.authorize` proves nothing if that
    `authorize` cannot refuse either — the seam would look enforced while still
    permitting everything. Whichever method the double ultimately relies on has
    to be able to raise.
    """
    offenders: list[str] = []
    for path in _backfill_integration_suites():
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for klass, method in _authorize_document_methods(tree):
            if any(isinstance(node, ast.Raise) for node in ast.walk(method)):
                continue  # refuses directly
            delegate = next(
                (
                    item
                    for item in klass.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "authorize"
                ),
                None,
            )
            if delegate is None or not any(
                isinstance(node, ast.Raise) for node in ast.walk(delegate)
            ):
                offenders.append(f"{path.name}:{method.lineno} {klass.name}")

    assert offenders == [], (
        "These policy doubles delegate target authorization to a check that can "
        "never refuse: " + "; ".join(offenders)
    )
