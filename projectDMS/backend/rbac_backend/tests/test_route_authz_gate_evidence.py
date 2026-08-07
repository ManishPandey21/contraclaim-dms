"""Route authorization evidence gate (H-04 / L-15).

``test_route_control_manifest`` proves every live route *has* a contract entry.
It does not prove the entry is true: the ``enforcement`` field it checks is
derived from substring matching over the endpoint source concatenated with the
source of every callable reachable from it. Constructing a ``PolicyService``
pulls that class's whole source into the blob, so a route could be labelled
``central_policy`` while never calling a gate.

This suite closes that hole with two independent kinds of evidence:

* **Static** -- ``scripts.authz_call_graph`` resolves *call expressions* and
  computes a least fixed point over the application call graph, so a route
  counts as gated only when a real call path reaches an authorization sink.
  Any route without such a path must carry a written, reviewed justification.
* **Behavioural** -- every mounted route is driven through the ASGI app with no
  credentials and must not answer 2xx.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute

from rbac_backend.main import app
from scripts.authz_call_graph import analyze_app, analyze_route
from scripts.rbac_phase0_route_inventory import (
    INLINE_GUARD_ROUTES,
    NON_TENANT_ROUTE_RATIONALES,
    PUBLIC_OR_EXTERNAL_ROUTES,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "route_control_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
ROUTES = MANIFEST["routes"]


def _api_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and str(route.path).startswith("/api")
    ]


# --------------------------------------------------------------------------
# Static evidence
# --------------------------------------------------------------------------


def test_every_route_reaches_a_gate_or_carries_a_written_exception() -> None:
    """No route may be unexplained: it is gated, public, non-tenant, or excused."""

    unexplained: list[str] = []
    for item in ROUTES:
        if item["gate_evidence"] != "none":
            continue
        if item["gate_exception_reason"]:
            continue
        if item["public_or_non_tenant_reason"]:
            continue
        unexplained.append(f"{','.join(item['methods'])} {item['path']} [{item['name']}]")

    assert unexplained == [], (
        "routes with no authorization call path and no written justification:\n  "
        + "\n  ".join(unexplained)
    )


def test_inline_guard_exceptions_are_live_and_not_stale() -> None:
    """An exception must name a real route that still needs one."""

    live_keys = {(str(route.path), str(route.name)) for route in _api_routes()}
    evidence = {
        (path, name): value for (path, name, _methods), value in analyze_app(app).items()
    }

    for key in INLINE_GUARD_ROUTES:
        assert key in live_keys, f"exception names a route that no longer exists: {key}"
        found = evidence.get(key)
        assert found is not None and not found.gated, (
            f"stale exception: {key} now reaches a reusable gate "
            f"({getattr(found, 'sink', '')}) -- delete the INLINE_GUARD_ROUTES entry"
        )

    for key, reason in INLINE_GUARD_ROUTES.items():
        assert len(reason) > 40, f"exception for {key} needs a substantive justification"


def test_manifest_gate_evidence_matches_live_analysis() -> None:
    """Adding or weakening a route's authorization must fail CI."""

    live = analyze_app(app)
    manifest_by_key = {
        (item["path"], item["name"], tuple(item["methods"])): item for item in ROUTES
    }

    drift: list[str] = []
    for key, found in live.items():
        entry = manifest_by_key.get(key)
        if entry is None:
            drift.append(f"route missing from manifest: {key}")
            continue
        expected = found.strength if found.gated else "none"
        if entry["gate_evidence"] != expected:
            drift.append(
                f"{key}: manifest says {entry['gate_evidence']!r}, live analysis says {expected!r}"
            )
    for key in manifest_by_key:
        if key not in live:
            drift.append(f"manifest entry for a route that no longer exists: {key}")

    assert drift == [], "regenerate route_control_manifest.json:\n  " + "\n  ".join(drift)


def test_step_up_routes_have_step_up_call_evidence() -> None:
    """A route declaring step-up must actually call the step-up gate."""

    missing: list[str] = []
    for route in _api_routes():
        methods = tuple(sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"}))
        entry = next(
            (
                item
                for item in ROUTES
                if item["path"] == str(route.path)
                and item["name"] == str(route.name)
                and tuple(item["methods"]) == methods
            ),
            None,
        )
        if entry is None or not entry["step_up_required"]:
            continue
        if not analyze_route(route).step_up:
            missing.append(f"{','.join(methods)} {route.path} [{route.name}]")

    assert missing == [], (
        "routes declare step_up_required but no call path reaches the step-up gate:\n  "
        + "\n  ".join(missing)
    )


# --------------------------------------------------------------------------
# Behavioural evidence
# --------------------------------------------------------------------------

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_database, get_db

    _CLIENT_OK = True
except Exception as exc:  # pragma: no cover - environment without httpx
    _CLIENT_OK = False
    _CLIENT_ERROR = str(exc)


PUBLIC_KEYS = {*PUBLIC_OR_EXTERNAL_ROUTES}

#: Placeholder used for every path parameter. It must not match any seeded id,
#: so a correctly-authorized route can never answer 2xx for it.
_PLACEHOLDER = "authz-sweep-nonexistent-id"


def _concrete_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", _PLACEHOLDER, path)


class _UnusableDB:
    """Stands in for Mongo so the sweep never opens a socket.

    Authentication must reject the request before any collection is touched; if
    a route reaches this object the attribute access raises and the request
    surfaces as a 5xx, which the sweep still counts as "not 2xx" but which is
    visible in the failure report.
    """

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - defensive
        raise RuntimeError(f"unauthenticated request reached the database ({item})")


@pytest.mark.skipif(not _CLIENT_OK, reason="TestClient unavailable")
def test_unauthenticated_requests_never_succeed() -> None:
    """Drive every mounted route with no credentials; none may answer 2xx."""

    async def _override_db():
        yield _UnusableDB()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_database] = _override_db

    leaked: list[str] = []
    # No context manager: startup events (and their connections) do not run.
    client = TestClient(app, raise_server_exceptions=False)
    try:
        for route in _api_routes():
            key = (str(route.path), str(route.name))
            if key in PUBLIC_KEYS:
                continue
            methods = sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"})
            for method in methods:
                url = _concrete_path(str(route.path))
                response = client.request(method, url, json={})
                if 200 <= response.status_code < 300:
                    leaked.append(f"{method} {route.path} [{route.name}] -> {response.status_code}")
    finally:
        app.dependency_overrides.clear()

    assert leaked == [], (
        "routes answered 2xx without credentials:\n  " + "\n  ".join(leaked)
    )


@pytest.mark.skipif(not _CLIENT_OK, reason="TestClient unavailable")
def test_unauthenticated_sweep_mostly_returns_401_or_403() -> None:
    """The sweep is only meaningful if requests are rejected by the auth layer.

    A 422 means validation rejected the request before authentication was
    reached, which proves nothing about authorization. This test pins how much
    of the surface is genuinely auth-rejected so that number cannot silently
    fall.
    """

    async def _override_db():
        yield _UnusableDB()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_database] = _override_db

    auth_rejected = 0
    total = 0
    client = TestClient(app, raise_server_exceptions=False)
    try:
        for route in _api_routes():
            key = (str(route.path), str(route.name))
            if key in PUBLIC_KEYS or key in NON_TENANT_ROUTE_RATIONALES:
                continue
            for method in sorted(set(route.methods or set()) - {"HEAD", "OPTIONS"}):
                total += 1
                response = client.request(method, _concrete_path(str(route.path)), json={})
                if response.status_code in (401, 403):
                    auth_rejected += 1
    finally:
        app.dependency_overrides.clear()

    assert total > 0
    ratio = auth_rejected / total
    assert ratio >= 0.95, (
        f"only {auth_rejected}/{total} ({ratio:.1%}) of unauthenticated requests were "
        "rejected by the authentication layer"
    )
