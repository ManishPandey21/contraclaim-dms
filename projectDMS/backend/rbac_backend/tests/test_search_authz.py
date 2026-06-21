"""Negative authz / tenant-scope tests for search.py (Phase 1 hardening).

Guards two leaks: GET /search/suggestions drew document names with no tenant
scope (cross-tenant name leak), and GET /search/analytics documented "admin only"
but enforced nothing (any user could read global analytics).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_database
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.services import scope_service as scope_module
    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - env without TestClient/app
    _IMPORTS_OK = False
    _IMPORT_ERR = exc

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, _length=None):
        return list(self._docs)


class _CapturingDocs:
    """Captures the aggregate pipeline so a test can assert the scope $match."""

    def __init__(self, holder):
        self._holder = holder

    def aggregate(self, pipeline, *_a, **_k):
        self._holder["pipeline"] = pipeline
        return _Cursor([])


class _EmptyColl:
    def aggregate(self, *_a, **_k):
        return _Cursor([])


class _FakeDB:
    def __init__(self, holder):
        self.documents = _CapturingDocs(holder)
        self.search_analytics = _EmptyColl()


def _user(*, roles, org="org-A"):
    return SimpleNamespace(
        id="user-1", username="u1", email="u1@example.com",
        roles=list(roles), organization_id=org,
        organizations=[org] if org else [], projects=[],
    )


def _client(user, holder):
    async def _override_db():
        yield _FakeDB(holder)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_database] = _override_db
    return TestClient(app)


def test_suggestions_scoped_to_caller_org(monkeypatch):
    # An org user's name suggestions must be filtered to their allowed orgs.
    async def _orgs(_self, _user):
        return {"org-A"}

    async def _projects(_self, _user):
        return set()

    monkeypatch.setattr(scope_module.ScopeService, "client_organization_ids", _orgs)
    monkeypatch.setattr(scope_module.ScopeService, "client_project_ids", _projects)

    holder: dict = {}
    client = _client(_user(roles=("orguser",)), holder)
    try:
        resp = client.get("/api/search/suggestions", params={"q": "alpha"})
        assert resp.status_code == 200, resp.text
        match = holder["pipeline"][0]["$match"]
        assert match.get("organization_id") == {"$in": ["org-A"]}
    finally:
        app.dependency_overrides.clear()


def test_suggestions_orphan_user_sees_nothing(monkeypatch):
    async def _empty(_self, _user):
        return set()

    monkeypatch.setattr(scope_module.ScopeService, "client_organization_ids", _empty)
    monkeypatch.setattr(scope_module.ScopeService, "client_project_ids", _empty)

    holder: dict = {}
    client = _client(_user(roles=("orguser",), org=None), holder)
    try:
        resp = client.get("/api/search/suggestions", params={"q": "alpha"})
        assert resp.status_code == 200
        assert resp.json() == {"suggestions": []}
        assert "pipeline" not in holder  # never queried documents
    finally:
        app.dependency_overrides.clear()


def test_analytics_forbidden_for_non_admin():
    holder: dict = {}
    client = _client(_user(roles=("orgadmin",)), holder)
    try:
        resp = client.get("/api/search/analytics")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_analytics_allowed_for_superadmin():
    holder: dict = {}
    client = _client(_user(roles=("superadmin",)), holder)
    try:
        resp = client.get("/api/search/analytics")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
