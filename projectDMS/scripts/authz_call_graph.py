"""Static authorization call-graph analysis for the route control contract.

The Phase 0 inventory classified a route by concatenating its endpoint source
with the source of every application callable reachable from it and then
substring-matching for markers such as ``policy.authorize``. That is not
evidence: the blob also contains the *definition* of the gate (constructing a
``PolicyService`` pulls the whole class source, which naturally mentions
``authorize``), so a route could be labelled ``policy_service`` without ever
calling a gate.

This module answers the stricter question the route contract actually needs:

    Is there a real call path from this route's endpoint (or one of its
    FastAPI dependencies) to an authorization sink?

It works on resolved *call expressions* rather than text, and it computes
``gated`` as a least fixed point over the application call graph:

    gated(f)  <=>  f contains a Call resolving to a sink, or to a gated g

Because gate membership is derived from calls only, defining a sink (or merely
importing/constructing the class that owns it) never makes a caller gated.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from fastapi import params as fastapi_params
from fastapi.routing import APIRoute

try:  # Support both ``scripts.authz_call_graph`` and top-level script imports.
    from rbac_phase0_route_inventory import (
        _callable_key,
        _is_application_callable,
        _resolve_annotation,
        _resolve_expression,
        _safe_source,
    )
except ImportError:  # pragma: no cover - depends on sys.path shape
    from scripts.rbac_phase0_route_inventory import (
        _callable_key,
        _is_application_callable,
        _resolve_annotation,
        _resolve_expression,
        _safe_source,
    )

# Terminal authorization sinks, identified by (module, qualname). A call that
# resolves to one of these is proof of an enforcement point, not a hint.
GATE_SINKS: frozenset[tuple[str, str]] = frozenset(
    {
        ("rbac_backend.services.policy_service", "PolicyService.authorize"),
        ("rbac_backend.services.policy_service", "PolicyService.authorize_document"),
        ("rbac_backend.services.authorization_service", "AuthorizationService.require_permission"),
        ("rbac_backend.core.security", "require_permission"),
        ("rbac_backend.services.step_up_service", "require_step_up"),
        ("rbac_backend.services.step_up_service", "step_up_dependency"),
        # Reusable platform-administrator gates. These do not go through
        # PolicyService but are unconditional deny-by-default role checks that
        # raise 403, so a call to one is genuine enforcement evidence.
        ("rbac_backend.routers.ai_assistant", "_require_platform_admin"),
        ("rbac_backend.routers.storage_sync", "_require_superadmin"),
        ("rbac_backend.routers.storage_sync", "_require_superadmin_user"),
    }
)

# Sinks that additionally establish step-up authentication.
STEP_UP_SINKS: frozenset[tuple[str, str]] = frozenset(
    {
        ("rbac_backend.services.step_up_service", "require_step_up"),
        ("rbac_backend.services.step_up_service", "step_up_dependency"),
    }
)

# Attribute names that denote an authorization decision when *called*. Static
# receiver resolution fails whenever a helper takes an unannotated parameter
# (``async def _load(..., policy)``), which is common here. Matching the called
# attribute name keeps the evidence call-site based -- unlike blob substring
# matching, a mention in a docstring or an uninvoked class body never counts.
GATE_METHOD_NAMES: frozenset[str] = frozenset(
    {
        "authorize",
        "authorize_document",
        "require_permission",
        "require_step_up",
    }
)

STEP_UP_METHOD_NAMES: frozenset[str] = frozenset({"require_step_up"})

# Tenant-scoping helpers. These constrain the query to the caller's tenant but
# do not by themselves decide permission, so they are tracked separately.
SCOPE_SINKS: frozenset[tuple[str, str]] = frozenset(
    {
        ("rbac_backend.core.security", "build_scope_query"),
    }
)

SCOPE_METHOD_NAMES: frozenset[str] = frozenset(
    {"build_scope_query", "check_organization_access", "check_project_access"}
)

# Source-derived sinks: (declared receiver class name, called method). These are
# matched against text written in the source, so monkeypatching cannot change
# them and the analysis is independent of test execution order.
GATE_SINK_CLASS_METHODS: frozenset[tuple[str, str]] = frozenset(
    {
        ("PolicyService", "authorize"),
        ("PolicyService", "authorize_document"),
        ("AuthorizationService", "require_permission"),
    }
)

STEP_UP_CLASS_METHODS: frozenset[tuple[str, str]] = frozenset()

SCOPE_CLASS_METHODS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ScopeService", "client_organization_ids"),
        ("ScopeService", "client_project_ids"),
    }
)

MAX_DEPTH = 8



def _annotation_name(node: ast.expr | None) -> str:
    """Return the bare class name written in the source, without resolving it."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    return ""


@dataclass
class GateEvidence:
    """Why a route is (or is not) considered gated."""

    gated: bool = False
    step_up: bool = False
    scoped: bool = False
    #: ordered call path from endpoint to the sink, as "module:qualname" strings
    path: list[str] = field(default_factory=list)
    #: the sink that terminated the path
    sink: str = ""
    #: how enforcement is reached: "endpoint", "dependency", or ""
    via: str = ""
    #: "resolved" when the receiver resolved to a known gate class,
    #: "call_name" when matched on the called attribute name
    strength: str = ""


def _callee_targets(
    target: Any,
) -> tuple[tuple[Any, ...], tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Resolve the application callables invoked by ``target``.

    Unlike the inventory's helper this returns only callables that appear as the
    *function* of a Call node, so a class referenced but never invoked as a gate
    contributes nothing.
    """

    source = _safe_source(target)
    if not source:
        return (), (), (), ()
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return (), (), (), ()

    unwrapped = inspect.unwrap(target)
    namespace = dict(getattr(unwrapped, "__globals__", {}) or {})
    local_types: dict[str, Any] = {}

    qualname = str(getattr(unwrapped, "__qualname__", ""))
    owner_name = qualname.split(".")[-2] if "." in qualname else ""
    owner = namespace.get(owner_name)
    if inspect.isclass(owner):
        local_types["self"] = owner

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]:
                resolved = _resolve_annotation(argument.annotation, namespace)
                if inspect.isclass(resolved):
                    local_types[argument.arg] = resolved
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            resolved = _resolve_expression(value.func, namespace, local_types)
            if not inspect.isclass(resolved):
                continue
            names: list[str] = []
            if isinstance(node, ast.Assign):
                names = [item.id for item in node.targets if isinstance(item, ast.Name)]
            elif isinstance(node.target, ast.Name):
                names = [node.target.id]
            for name in names:
                local_types[name] = resolved

    # Purely source-derived receiver types: parameter annotations, local
    # assignments from a constructor call, and ``self.x = X(...)`` in __init__.
    source_types: dict[str, str] = {}
    if "." in qualname:
        source_types["self"] = qualname.split(".")[-2]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]:
                name = _annotation_name(argument.annotation)
                if name:
                    source_types[argument.arg] = name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            constructed = _annotation_name(value.func)
            if not constructed:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for item in targets:
                if isinstance(item, ast.Name):
                    source_types[item.id] = constructed
                elif (
                    isinstance(item, ast.Attribute)
                    and isinstance(item.value, ast.Name)
                    and item.value.id == "self"
                ):
                    source_types[f"self.{item.attr}"] = constructed

    callees: list[Any] = []
    named_gates: list[str] = []
    named_scopes: list[str] = []
    structural: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Call-site name evidence, independent of receiver resolution.
        called_name = ""
        if isinstance(node.func, ast.Attribute):
            called_name = node.func.attr
            # Structural sink identity, derived from *source text only*: the
            # declared annotation/class name of the receiver plus the called
            # attribute. Resolving live objects here (getattr, module globals)
            # made the result depend on test execution order, because suites
            # monkeypatch both PolicyService methods and the PolicyService name
            # in router namespaces. Source text cannot be monkeypatched.
            receiver = node.func.value
            owner_name = ""
            if isinstance(receiver, ast.Name):
                owner_name = source_types.get(receiver.id, "")
            elif isinstance(receiver, ast.Attribute) and isinstance(receiver.value, ast.Name):
                if receiver.value.id == "self":
                    owner_name = source_types.get(f"self.{receiver.attr}", "")
            if owner_name:
                structural.append((owner_name, called_name))
        elif isinstance(node.func, ast.Name):
            called_name = node.func.id
        if called_name in GATE_METHOD_NAMES:
            named_gates.append(called_name)
        if called_name in SCOPE_METHOD_NAMES:
            named_scopes.append(called_name)

        resolved = _resolve_expression(node.func, namespace, local_types)
        if resolved is None or not callable(resolved):
            continue
        if not _is_application_callable(resolved):
            continue
        callees.append(resolved)
    return tuple(callees), tuple(named_gates), tuple(named_scopes), tuple(structural)


@lru_cache(maxsize=None)
def _scan(
    key: tuple[str, str], target: Any
) -> tuple[tuple[Any, ...], tuple[str, ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
    return _callee_targets(target)


def _cached_callees(key: tuple[str, str], target: Any) -> tuple[Any, ...]:
    return _scan(key, target)[0]


def _search(target: Any, depth: int, seen: frozenset[tuple[str, str]]) -> GateEvidence:
    """Depth-first search for a call path from ``target`` to a gate sink."""

    key = _callable_key(target)
    if key in seen or depth > MAX_DEPTH:
        return GateEvidence()
    seen = seen | {key}

    label = f"{key[0]}:{key[1]}"
    callees, named_gates, named_scopes, structural = _scan(key, target)

    # Step-up and scope are properties of the whole function body, not of
    # whichever sink happens to be found first. A route commonly calls
    # ``require_permission`` *and* ``require_step_up``; returning on the first
    # resolved sink would report step_up=False for it.
    step_up_here = (
        any(_callable_key(c) in STEP_UP_SINKS for c in callees)
        or any(name in STEP_UP_METHOD_NAMES for name in named_gates)
        or any(item in STEP_UP_CLASS_METHODS for item in structural)
    )
    scoped_here = (
        bool(named_scopes)
        or any(_callable_key(c) in SCOPE_SINKS for c in callees)
        or any(item in SCOPE_CLASS_METHODS for item in structural)
    )

    # Strongest evidence first: a call whose declared owner is a known sink.
    for item in structural:
        if item in GATE_SINK_CLASS_METHODS:
            sink = f"{item[0]}.{item[1]}()"
            return GateEvidence(
                gated=True,
                step_up=step_up_here,
                scoped=scoped_here,
                path=[label, sink],
                sink=sink,
                via="endpoint",
                strength="resolved",
            )

    for callee in callees:
        callee_key = _callable_key(callee)
        if callee_key in GATE_SINKS:
            sink = f"{callee_key[0]}:{callee_key[1]}"
            return GateEvidence(
                gated=True,
                step_up=step_up_here,
                scoped=scoped_here,
                path=[label, sink],
                sink=sink,
                via="endpoint",
                strength="resolved",
            )

    # Next: a call to a gate-named method whose receiver did not resolve.
    if named_gates:
        return GateEvidence(
            gated=True,
            step_up=step_up_here,
            scoped=scoped_here,
            path=[label, f"<call>.{named_gates[0]}()"],
            sink=f"<call>.{named_gates[0]}()",
            via="endpoint",
            strength="call_name",
        )

    # Otherwise recurse into application callees.
    best = GateEvidence(scoped=scoped_here)
    for callee in callees:
        found = _search(callee, depth + 1, seen)
        if not found.gated:
            if found.scoped:
                best.scoped = True
            continue
        found.path = [label, *found.path]
        found.scoped = found.scoped or scoped_here
        if not best.gated or len(found.path) < len(best.path):
            best = found
        if found.step_up:
            best = found
            break
    return best


def _dependency_callables(route: APIRoute) -> Iterable[Any]:
    """Yield callables attached to the endpoint via FastAPI ``Depends``."""

    try:
        signature = inspect.signature(route.endpoint)
    except (TypeError, ValueError):
        return
    for parameter in signature.parameters.values():
        default = parameter.default
        if isinstance(default, fastapi_params.Depends) and default.dependency is not None:
            yield default.dependency
        elif isinstance(default, fastapi_params.Security) and default.dependency is not None:
            yield default.dependency
    for dependency in getattr(route, "dependencies", []) or []:
        candidate = getattr(dependency, "dependency", None)
        if candidate is not None:
            yield candidate


def analyze_route(route: APIRoute) -> GateEvidence:
    """Return call-path evidence that ``route`` reaches an authorization sink."""

    evidence = _search(route.endpoint, 0, frozenset())
    if evidence.gated:
        if not evidence.step_up:
            # Step-up may be applied purely as a FastAPI dependency
            # (``Depends(step_up_dependency("x"))``) while the body carries the
            # permission gate, so dependencies still have to be inspected.
            for dependency in _dependency_callables(route):
                if not callable(dependency):
                    continue
                if _callable_key(dependency) in STEP_UP_SINKS:
                    evidence.step_up = True
                    break
                if _is_application_callable(dependency) and _search(
                    dependency, 0, frozenset()
                ).step_up:
                    evidence.step_up = True
                    break
        return evidence

    for dependency in _dependency_callables(route):
        if not callable(dependency):
            continue
        dep_key = _callable_key(dependency)
        if dep_key in GATE_SINKS:
            return GateEvidence(
                gated=True,
                step_up=dep_key in STEP_UP_SINKS,
                path=[f"{dep_key[0]}:{dep_key[1]}"],
                sink=f"{dep_key[0]}:{dep_key[1]}",
                via="dependency",
                strength="resolved",
            )
        if not _is_application_callable(dependency):
            continue
        found = _search(dependency, 0, frozenset())
        if found.gated:
            found.via = "dependency"
            return found
    return GateEvidence()


def analyze_app(app: Any) -> dict[tuple[str, str, tuple[str, ...]], GateEvidence]:
    """Analyze every mounted ``/api`` route and return evidence keyed by route."""

    results: dict[tuple[str, str, tuple[str, ...]], GateEvidence] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        path = str(getattr(route, "path", ""))
        if not path.startswith("/api"):
            continue
        methods = tuple(sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"}))
        results[(path, str(route.name), methods)] = analyze_route(route)
    return results
