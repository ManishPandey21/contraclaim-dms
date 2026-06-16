"""HTTP-level multi-tenant isolation tests (Phase 2 / H10).

Where ``test_tenant_isolation.py`` exercises the authorization *primitives*
(ScopeService / PolicyService / build_scope_query) directly, this suite drives a
real endpoint through the FastAPI app with TestClient and dependency overrides,
proving the *route* applies tenant scope end-to-end — i.e. an Org-A user can
never receive Org-B rows from ``GET /api/projects1`` (the C1-hardened endpoint).

Deterministic and infra-free: ``get_current_user`` and ``get_db`` are overridden,
and a fake Mongo-like collection applies the route's ``build_scope_query`` filter
to a seeded two-tenant dataset. No real MongoDB is required.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.services.permission_service import PermissionService

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - environment without httpx/TestClient
    _IMPORTS_OK = False
    _IMPORT_ERROR = str(exc)

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


# Two tenants' projects sharing one collection.
SEED_PROJECTS = [
    {"_id": "proj-a1", "name": "Alpha (Org A)", "organization_id": "org-A"},
    {"_id": "proj-a2", "name": "Gamma (Org A)", "organization_id": "org-A"},
    {"_id": "proj-b1", "name": "Beta (Org B)", "organization_id": "org-B"},
]


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, _length=None):
        return list(self._docs)


class _FakeProjects:
    """Applies a Mongo-style equality filter (incl. the deny-all {'_id': {'$in': []}})."""

    def __init__(self, docs):
        self._docs = docs

    def find(self, query=None, *_args, **_kwargs):
        q = query or {}

        def _matches(doc):
            for key, value in q.items():
                if isinstance(value, dict) and "$in" in value:
                    if doc.get(key) not in value["$in"]:
                        return False
                elif doc.get(key) != value:
                    return False
            return True

        return _FakeCursor([d for d in self._docs if _matches(d)])


class _FakeDB:
    def __init__(self, projects):
        self.projects = _FakeProjects(projects)


def _user(*, org, roles=("orguser",), projects=()):
    return SimpleNamespace(
        id="user-1",
        username="user-1",
        email="user-1@example.com",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
        disabled=False,
    )


def _run(monkeypatch, user):
    """Wire overrides, call GET /api/projects1, return the parsed JSON list."""
    async def _allow(*_args, **_kwargs):  # permission gate passes; scope is what we test
        return True

    monkeypatch.setattr(PermissionService, "user_has_permission", _allow)

    async def _override_db():
        yield _FakeDB(list(SEED_PROJECTS))

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)  # no context manager -> app startup events do not run
        resp = client.get("/api/projects1")
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        app.dependency_overrides.clear()


def test_projects1_org_user_sees_only_own_tenant(monkeypatch):
    data = _run(monkeypatch, _user(org="org-A", roles=("orguser",)))
    orgs = {p["organization_id"] for p in data}
    names = {p["name"] for p in data}
    assert orgs == {"org-A"}                 # never Org B
    assert "Beta (Org B)" not in names
    assert names == {"Alpha (Org A)", "Gamma (Org A)"}


def test_projects1_project_user_scoped_to_org(monkeypatch):
    # project-scoped user in org-A still must not see org-B projects.
    data = _run(monkeypatch, _user(org="org-A", roles=("projectuser",), projects=("proj-a1",)))
    assert all(p["organization_id"] == "org-A" for p in data)
    assert "Beta (Org B)" not in {p["name"] for p in data}


def test_projects1_orphan_user_sees_nothing(monkeypatch):
    # No org / no projects -> build_scope_query denies all -> empty result.
    data = _run(monkeypatch, _user(org=None, roles=("orguser",)))
    assert data == []


def test_projects1_superadmin_sees_all(monkeypatch):
    data = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    assert {p["organization_id"] for p in data} == {"org-A", "org-B"}
