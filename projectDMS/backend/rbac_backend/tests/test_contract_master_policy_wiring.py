"""Contract Master v1: the real policy dependency, and the runtime scope token.

Two defects, one blind spot. ``contract_master_api.get_policy`` imported
``PolicyService`` from ``rbac_backend.core.policy`` - a module that has never
existed - so every ``/api/contract-master/*`` route failed with
``ModuleNotFoundError`` while resolving its dependencies, before any route logic
ran. The route suite (``integration/test_contract_master_routes_mongo.py``)
overrides ``get_policy`` with a recording fake, so the real dependency was never
resolved by any test and the defect was invisible to the whole suite.

These tests therefore never override ``get_policy``. They override ``get_db`` and
``get_current_user`` only, and stub ``PolicyService.authorize`` on the *class* so
that the instance FastAPI builds through the real dependency is the one asked.

The evidence-search scope token is pinned in
``test_contract_master_evidence_scope.py``; opt-in real-Mongo, real-RBAC coverage
of the same routes lives in ``integration/test_contract_master_policy_mongo.py``.
"""

from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from rbac_backend.core.security import CurrentUser, get_current_user
from rbac_backend.routers import contract_master_api
from rbac_backend.services.policy_service import PolicyService

PACKAGE = pathlib.Path(__file__).resolve().parents[1]

ORG = "org-wiring"
PROJECT = "project-wiring"
CONTRACT = "contract-wiring"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self._docs)


class _Collection:
    """Answers the pre-authorization instrument lookups; records any write."""

    def __init__(self, name: str, writes: List[str]) -> None:
        self._name = name
        self._writes = writes

    async def find_one(self, query: Dict[str, Any], *args: Any, **kwargs: Any):
        if self._name == "contract_documents":
            return {"_id": query.get("_id"), "organization_id": ORG, "project_id": None}
        if self._name == "projects":
            # The one project, in the one organisation: lets
            # authorize_contract_scope's pair check pass, so the policy is asked.
            return {"_id": PROJECT, "organization_id": ORG}
        return None

    def find(self, *args: Any, **kwargs: Any) -> _Cursor:
        return _Cursor([])

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        return 0

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _write(*args: Any, **kwargs: Any):
            self._writes.append(f"{self._name}.{name}")
            return SimpleNamespace(inserted_id=None, deleted_count=0, modified_count=0)

        return _write


class _FakeDb:
    def __init__(self) -> None:
        self.writes: List[str] = []

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(name, self.writes)

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return _Collection(name, self.writes)


def _user(organization_id: Optional[str] = ORG) -> CurrentUser:
    return CurrentUser(
        id="actor-wiring",
        username="actor",
        email="actor@example.com",
        roles=["orgadmin"],
        organization_id=organization_id,
        projects=[PROJECT],
    )


def _app(db: _FakeDb, user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.include_router(contract_master_api.router, prefix="/api")
    app.dependency_overrides[contract_master_api.get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    # Deliberately NOT overriding contract_master_api.get_policy.
    assert contract_master_api.get_policy not in app.dependency_overrides
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def refusing_policy(monkeypatch):
    """Every authorize call refuses, and records the instance that was asked."""
    asked: List[Dict[str, Any]] = []

    async def authorize(self, current_user, permission, **kwargs):
        asked.append({"policy": self, "permission": str(permission), **kwargs})
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="refused by test"
        )

    monkeypatch.setattr(PolicyService, "authorize", authorize)
    return asked


# --------------------------------------------------------------------------- #
# the dependency itself
# --------------------------------------------------------------------------- #


def test_get_policy_builds_a_policy_service_bound_to_the_request_database():
    import asyncio

    db = _FakeDb()
    policy = asyncio.run(contract_master_api.get_policy(db=db))

    assert isinstance(policy, PolicyService)
    assert policy.db is db
    # The services that read tenancy, entitlement and audit state must read the
    # same database the route writes to, not whichever global happens to be set.
    assert policy.scope_service.db is db
    assert policy.entitlement_service.db is db
    assert policy.audit_service.db is db


def test_there_is_no_core_policy_compatibility_shim():
    """The fix is the import, not a module that makes the wrong import work."""
    assert not (PACKAGE / "core" / "policy.py").exists()
    assert not (PACKAGE / "core" / "policy").exists()


# --------------------------------------------------------------------------- #
# every route resolves the real dependency
# --------------------------------------------------------------------------- #

#: (method, path template) -> (concrete request path, json body). Every route the
#: router declares must be listed; the coverage test below fails otherwise.
ROUTE_MATRIX: Dict[tuple, tuple] = {
    ("GET", "/api/contract-master/catalogue"): (
        f"/api/contract-master/catalogue?organization_id={ORG}",
        None,
    ),
    ("GET", "/api/contract-master/instruments/{contract_document_id}"): (
        "/api/contract-master/instruments/cd-1",
        None,
    ),
    ("GET", "/api/contract-master/instruments/{contract_document_id}/projection"): (
        "/api/contract-master/instruments/cd-1/projection",
        None,
    ),
    (
        "POST",
        "/api/contract-master/instruments/{contract_document_id}/classification",
    ): (
        "/api/contract-master/instruments/cd-1/classification",
        {"contract_document_type": "amendment", "expected_revision": 1},
    ),
    ("POST", "/api/contract-master/instruments/{contract_document_id}/applicability"): (
        "/api/contract-master/instruments/cd-1/applicability",
        {"project_id": PROJECT, "contract_id": CONTRACT, "kind": "APPLIED"},
    ),
    ("POST", "/api/contract-master/evidence/search"): (
        "/api/contract-master/evidence/search",
        {
            "project_id": PROJECT,
            "contract_id": CONTRACT,
            "query": "variation",
            "query_mode": "current_state",
        },
    ),
    ("GET", "/api/contract-master/reconciliation/inventory"): (
        f"/api/contract-master/reconciliation/inventory?organization_id={ORG}",
        None,
    ),
    ("POST", "/api/contract-master/reconciliation/materialise"): (
        "/api/contract-master/reconciliation/materialise",
        {"organization_id": ORG},
    ),
    ("GET", "/api/contract-master/reconciliation/candidates"): (
        f"/api/contract-master/reconciliation/candidates?organization_id={ORG}",
        None,
    ),
    ("POST", "/api/contract-master/reconciliation/candidates/{candidate_id}/claim"): (
        f"/api/contract-master/reconciliation/candidates/c-1/claim?organization_id={ORG}",
        None,
    ),
    (
        "POST",
        "/api/contract-master/reconciliation/candidates/{candidate_id}/adjudicate",
    ): (
        "/api/contract-master/reconciliation/candidates/c-1/adjudicate"
        f"?owner_token=t-1&organization_id={ORG}",
        {"scope_state": "ORG_SCOPE_CONFIRMED", "reason": "wiring smoke"},
    ),
    ("POST", "/api/contract-master/reconciliation/candidates/{candidate_id}/promote"): (
        f"/api/contract-master/reconciliation/candidates/c-1/promote?organization_id={ORG}",
        {},
    ),
    ("GET", "/api/contract-master/capabilities"): (
        f"/api/contract-master/capabilities?organization_id={ORG}",
        None,
    ),
}


def _declared_routes() -> set:
    return {
        (method, f"/api{route.path}")
        for route in contract_master_api.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def test_the_smoke_matrix_covers_every_contract_master_route():
    assert _declared_routes() == set(ROUTE_MATRIX)


@pytest.mark.parametrize("route", sorted(ROUTE_MATRIX), ids=lambda r: f"{r[0]} {r[1]}")
def test_every_route_resolves_the_real_policy_dependency(route, refusing_policy):
    db = _FakeDb()
    client = _app(db, _user())
    path, body = ROUTE_MATRIX[route]

    response = client.request(route[0], path, json=body)

    # Before the fix every one of these was a 500 raised while FastAPI resolved
    # `get_policy`: ModuleNotFoundError: No module named 'rbac_backend.core.policy'.
    assert response.status_code != 500, response.text
    assert refusing_policy, "the route never reached PolicyService.authorize"
    for call in refusing_policy:
        assert isinstance(call["policy"], PolicyService)
        assert call["policy"].db is db
    if route == ("GET", "/api/contract-master/capabilities"):
        # A denial is an answer at this endpoint, not an error.
        assert response.status_code == 200
        assert not any(response.json().values())
    else:
        assert response.status_code == 403, response.text
    # A refused request writes nothing - including no claim row.
    assert db.writes == []


# --------------------------------------------------------------------------- #
# static guards
# --------------------------------------------------------------------------- #


def _production_files():
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.relative_to(PACKAGE).parts:
            continue
        yield path


def _resolve(path: pathlib.Path, node: ast.ImportFrom) -> Optional[pathlib.Path]:
    """The package-relative module an import names, or None if it is external."""
    if node.level:
        base = path.parent
        for _ in range(node.level - 1):
            base = base.parent
        parts = node.module.split(".") if node.module else []
    elif node.module and node.module.split(".")[0] == "rbac_backend":
        base = PACKAGE
        parts = node.module.split(".")[1:]
    else:
        return None
    try:
        base.relative_to(PACKAGE)
    except ValueError:
        return None  # climbs out of the package; not ours to judge
    return base.joinpath(*parts) if parts else base


def test_every_package_import_names_a_module_that_exists():
    """The class of the ``get_policy`` defect: an import nobody executes.

    ``from ..core.policy import PolicyService`` sat inside a function body, so
    importing the router succeeded and only the first request failed. Every
    package-internal ``from ... import`` - at module level or deferred inside a
    function - must name a module or package that exists on disk.
    """
    missing = []
    for path in _production_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve(path, node)
            if target is None:
                continue
            if not (
                target.with_suffix(".py").exists() or (target / "__init__.py").exists()
            ):
                missing.append(
                    f"{path.relative_to(PACKAGE)}:{node.lineno} "
                    f"{'.' * node.level}{node.module or ''}"
                )
    assert missing == [], missing
