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
    from rbac_backend.services.entitlement_service import EntitlementService
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

    async def to_list(self, _length=None, *, length=None):
        # Motor accepts the length both positionally and as a keyword.
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
        # ScopeService resolves tenant scope from membership collections as well
        # as from the user document. These are intentionally empty so the users
        # built by ``_user`` are scoped solely by their own organization_id /
        # organizations / projects fields, which is what this suite varies.
        self.organization_memberships = _FakeProjects([])
        self.project_memberships = _FakeProjects([])


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


def _run(monkeypatch, user, *, expect_status=200):
    """Wire overrides and call GET /api/projects1.

    Returns the parsed JSON list for the 200 case, or ``(status_code, body)``
    when a non-200 status is expected.
    """
    async def _allow(*_args, **_kwargs):  # permission gate passes; scope is what we test
        return True

    monkeypatch.setattr(PermissionService, "user_has_permission", _allow)

    # Entitlement is a separate control with its own suite (see
    # test_rbac_monetization / test_entitlement_context_endpoint). Neutralize it
    # here for the same reason the permission gate is neutralized: this suite
    # asserts that *tenant scope* is applied, and must fail only for scope bugs.
    async def _entitled(*_args, **_kwargs):
        return True, "test_entitlement"

    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)

    async def _override_db():
        yield _FakeDB(list(SEED_PROJECTS))

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)  # no context manager -> app startup events do not run
        resp = client.get("/api/projects1")
        assert resp.status_code == expect_status, resp.text
        if expect_status != 200:
            return resp.status_code, resp.text
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


def test_projects1_project_user_sees_only_assigned_projects(monkeypatch):
    """A collection read with nothing selected is the consolidated view.

    Three behaviours are possible here and only one is right. The endpoint once
    returned an **org-filtered** 200, which leaked Gamma -- a project in the
    caller's organisation that they are not assigned to. That was then fixed by
    denying project-tier callers outright, which overshot: it left a project
    user unable to list their own projects at all.

    The contract is progressive narrowing (see docs/AUTHZ.md): no project
    selected means consolidated across the caller's *assignments*, bounded by
    ``build_scope_query``. So Alpha appears, Gamma does not, and Org B never
    does.
    """
    data = _run(
        monkeypatch,
        _user(org="org-A", roles=("projectuser",), projects=("proj-a1",)),
    )
    names = {p["name"] for p in data}
    assert names == {"Alpha (Org A)"}, "only the assigned project may appear"
    assert "Gamma (Org A)" not in names, "an unassigned same-org project is still out of scope"
    assert "Beta (Org B)" not in names


def test_projects1_project_user_without_assignments_denied(monkeypatch):
    """The fail-closed edge of the consolidated view.

    Allowing project-tier collection reads is only safe because there is
    something to consolidate over. With no assignment there is no reach at all,
    so the scope gate must refuse rather than fall through to an org-wide read.
    """
    status_code, body = _run(
        monkeypatch,
        _user(org="org-A", roles=("projectuser",), projects=()),
        expect_status=403,
    )
    assert status_code == 403
    assert "scope_denied" in str(body)


def test_projects1_orphan_user_denied(monkeypatch):
    """No org and no projects must fail closed, not return an empty list.

    An empty 200 is indistinguishable from "this tenant has no projects"; the
    scope gate now refuses the request outright.
    """
    status_code, body = _run(
        monkeypatch, _user(org=None, roles=("orguser",)), expect_status=403
    )
    assert status_code == 403
    assert "scope_denied" in str(body)


def test_projects1_superadmin_sees_all(monkeypatch):
    data = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    assert {p["organization_id"] for p in data} == {"org-A", "org-B"}
