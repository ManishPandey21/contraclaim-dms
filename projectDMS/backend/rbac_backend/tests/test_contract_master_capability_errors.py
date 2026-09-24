"""Contract Master capabilities: a refusal is an answer, an outage is not.

``GET /contract-master/capabilities`` asks the policy the question each route
asks. A 403 from it means "you may not" and becomes ``false``. Anything else - a
Mongo outage, a programming error, a 409, a 401 - is not an answer about the
caller's authority, and must surface as the error it is instead of greying out
the UI as though the caller lacked permission.

The real ``get_policy`` is resolved here (``PolicyService.authorize`` is stubbed on
the class); the app registers the production domain-error handler so a
``BaseDomainError`` renders at its own status.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rbac_backend.core.errors import register_domain_error_handler
from rbac_backend.core.security import get_current_user
from rbac_backend.routers import contract_master_api
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.tests.selection_fixtures import pin_selection
from rbac_backend.tests.test_contract_master_policy_wiring import (
    ORG,
    PROJECT,
    _FakeDb,
    _user,
)
from rbac_backend.utils.error_handler import BaseDomainError

PATH = f"/api/contract-master/capabilities?organization_id={ORG}"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(contract_master_api.router, prefix="/api")
    register_domain_error_handler(app)
    db = _FakeDb()
    app.dependency_overrides[contract_master_api.get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _user()
    pin_selection(app, db, ORG, PROJECT)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _organisation_wide(monkeypatch):
    async def wide(self, user, *, organization_id):
        return True

    monkeypatch.setattr(ScopeService, "has_organization_wide_scope", wide)


def _authorize_raising(monkeypatch, error: BaseException) -> None:
    async def authorize(self, current_user, permission, **kwargs: Any):
        raise error

    monkeypatch.setattr(PolicyService, "authorize", authorize)


@pytest.mark.parametrize(
    "refusal",
    [
        HTTPException(status_code=403, detail="refused"),
        BaseDomainError("refused", 403),
    ],
    ids=["http-403", "domain-403"],
)
def test_a_403_refusal_is_a_false_capability(monkeypatch, refusal):
    _authorize_raising(monkeypatch, refusal)
    response = _client().get(PATH)
    assert response.status_code == 200, response.text
    assert not any(response.json().values())


@pytest.mark.parametrize(
    "failure,status",
    [
        (RuntimeError("mongo unavailable"), 500),
        (HTTPException(status_code=500, detail="policy store down"), 500),
        (HTTPException(status_code=409, detail="conflict"), 409),
        (HTTPException(status_code=401, detail="expired"), 401),
        (BaseDomainError("upstream failed", 503), 503),
        (BaseDomainError("conflict", 409), 409),
    ],
    ids=["runtime", "http-500", "http-409", "http-401", "domain-503", "domain-409"],
)
def test_anything_but_a_403_surfaces_instead_of_reading_as_no_permission(
    monkeypatch, failure, status
):
    _authorize_raising(monkeypatch, failure)
    response = _client().get(PATH)
    assert response.status_code == status, response.text


def test_an_organisation_scope_outage_surfaces(monkeypatch):
    async def authorize(self, current_user, permission, **kwargs: Any):
        return None

    async def down(self, user, *, organization_id):
        raise RuntimeError("scope store unavailable")

    monkeypatch.setattr(PolicyService, "authorize", authorize)
    monkeypatch.setattr(ScopeService, "has_organization_wide_scope", down)
    response = _client().get(PATH)
    assert response.status_code == 500, response.text


def test_the_helper_names_no_broad_handler():
    """Guard: ``may()`` must not reacquire an ``except Exception``."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(contract_master_api.get_capabilities))
    broad = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and (
            node.type is None
            or (
                isinstance(node.type, ast.Name)
                and node.type.id in {"Exception", "BaseException"}
            )
        )
    ]
    assert broad == []
