"""Multi-tenant isolation suite (Week 2.1).

Proves that a user from Org A can never read/act on Org B's resources, exercising
the three enforcement primitives every endpoint now funnels through after the
Week-1 consolidation:

- ``ScopeService.is_client_scope_allowed`` — runtime membership check
- ``PolicyService.authorize``            — the orchestrating gate
- ``build_scope_query``                  — list-endpoint result filtering

These run deterministically without external infrastructure (a fake Mongo-like db
backs the real ScopeService; permission/entitlement are stubbed allow=True to
isolate the *scope* decision).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core.security import build_scope_query
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- Fakes ----------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._docs)


class _FakeDB:
    def __init__(self, org_memberships=(), project_memberships=()):
        self.organization_memberships = _FakeCollection(org_memberships)
        self.project_memberships = _FakeCollection(project_memberships)


class _PermAllow:
    async def user_has_permission(self, *_args, **_kwargs) -> bool:
        return True


class _EntAllow:
    async def check_permission_entitlement(self, **_kwargs):
        return True, "ok"


class _Audit:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


def _policy(db: _FakeDB | None = None) -> PolicyService:
    return PolicyService(
        permission_service=_PermAllow(),
        scope_service=ScopeService(db=db or _FakeDB()),
        entitlement_service=_EntAllow(),
        audit_service=_Audit(),
    )


def _user(org="org-A", projects=("proj-A",), roles=("orguser",), account_type="client_user"):
    return SimpleNamespace(
        id="user-1",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type=account_type,
    )


# --- ScopeService.is_client_scope_allowed ---------------------------------


@pytest.mark.asyncio
async def test_org_user_denied_cross_org():
    svc = ScopeService(db=_FakeDB())
    user = _user(org="org-A", projects=("proj-A",), roles=("orguser",))
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-A") is True
    assert await svc.is_client_scope_allowed(user, organization_id="org-B", project_id="proj-B") is False
    assert await svc.is_client_scope_allowed(user, organization_id="org-B", project_id="proj-A") is False


@pytest.mark.asyncio
async def test_project_user_denied_cross_project():
    svc = ScopeService(db=_FakeDB())
    user = _user(org="org-A", projects=("proj-A",), roles=("projectuser",))
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-A") is True
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-B") is False


@pytest.mark.asyncio
async def test_orphan_user_denied_everywhere():
    svc = ScopeService(db=_FakeDB())
    user = _user(org=None, projects=(), roles=("orguser",))
    assert await svc.is_client_scope_allowed(user, organization_id="org-X", project_id="proj-X") is False
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-A") is False


@pytest.mark.asyncio
async def test_superadmin_allowed_anywhere():
    svc = ScopeService(db=_FakeDB())
    user = _user(org="org-A", roles=("superadmin",))
    assert await svc.is_client_scope_allowed(user, organization_id="org-ANYTHING", project_id="proj-ANYTHING") is True


@pytest.mark.asyncio
async def test_membership_from_collection_grants_scope():
    db = _FakeDB(
        org_memberships=[{"user_id": "user-1", "organization_id": "org-A", "status": "active"}],
        project_memberships=[{"user_id": "user-1", "project_id": "proj-A", "status": "active"}],
    )
    svc = ScopeService(db=db)
    user = _user(org=None, projects=(), roles=("projectuser",))
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-A") is True
    assert await svc.is_client_scope_allowed(user, organization_id="org-A", project_id="proj-B") is False


# --- PolicyService.authorize (cross-tenant denial) ------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission",
    [
        "dms.document.view",
        "dms.document.upload",
        "dms.document.download",
        "dms.document.delete",
        "dms.status.update",
    ],
)
async def test_policy_authorize_denies_cross_tenant(permission: str):
    policy = _policy()
    user = _user(org="org-A", projects=("proj-A",), roles=("orguser",))

    # In-scope: allowed (no raise)
    await policy.authorize(user, permission, organization_id="org-A", project_id="proj-A")

    # Cross-tenant: denied
    with pytest.raises(HTTPException) as exc:
        await policy.authorize(user, permission, organization_id="org-B", project_id="proj-B")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_policy_authorize_document_denies_foreign_document():
    policy = _policy()
    user = _user(org="org-A", projects=("proj-A",), roles=("orguser",))
    foreign_doc = {"_id": "doc-9", "organization_id": "org-B", "project_id": "proj-B"}
    with pytest.raises(HTTPException) as exc:
        await policy.authorize_document(user, "dms.document.view", foreign_doc)
    assert exc.value.status_code == 403


# --- build_scope_query (list filtering) -----------------------------------


def test_build_scope_query_org_user_restricts_to_org():
    user = _user(org="org-A", projects=(), roles=("orguser",))
    q = build_scope_query(user)
    assert q.get("organization_id") == "org-A"


def test_build_scope_query_cross_org_filter_denies_all():
    user = _user(org="org-A", roles=("orguser",))
    q = build_scope_query(user, organization_id="org-B")
    assert q == {"_id": {"$in": []}}


def test_build_scope_query_orphan_denies_all():
    user = _user(org=None, projects=(), roles=("orguser",))
    assert build_scope_query(user) == {"_id": {"$in": []}}


def test_build_scope_query_project_user_restricts_to_assigned_projects():
    user = _user(org="org-A", projects=("proj-A",), roles=("projectuser",))
    q = build_scope_query(user)
    assert q.get("organization_id") == "org-A"
    assert "project_id" in q
    # proj-A must be part of the allowed set; proj-B must not.
    allowed = q["project_id"]["$in"]
    assert "proj-A" in allowed
    assert "proj-B" not in allowed


def test_build_scope_query_project_user_no_assignments_denies_all():
    user = _user(org="org-A", projects=(), roles=("projectuser",))
    assert build_scope_query(user) == {"_id": {"$in": []}}


def test_build_scope_query_superadmin_unrestricted():
    user = _user(org="org-A", roles=("superadmin",))
    assert build_scope_query(user) == {}
