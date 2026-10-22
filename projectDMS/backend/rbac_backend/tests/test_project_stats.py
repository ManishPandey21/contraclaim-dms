"""Tests for GET /api/projects/stats — the live counts on the project cards.

Two things must hold and are easy to get wrong:

1. **Scope.** The endpoint must expose counts only for projects the caller can
   already list, using the same ``build_scope_query`` filter as ``GET /projects``.
   A cross-organisation project must not appear at all — not even as a zero.
2. **Membership.** "Team members" mirrors ``build_scope_query``'s access rules:
   a user with explicit project assignments is a member of exactly those
   projects; a user with none but an organisation assignment reaches every
   project in that organisation; superadmins belong to no single project.

Deterministic and infra-free: ``get_current_user``/``get_db`` are overridden and
``ProjectService`` is pointed at the same fake database. No MongoDB required.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.routers import projects as projects_router
    from rbac_backend.services import project_service as project_service_module
    from rbac_backend.services.entitlement_service import EntitlementService
    from rbac_backend.services.permission_service import PermissionService

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - environment without httpx/TestClient
    _IMPORTS_OK = False
    _IMPORT_ERROR = str(exc)

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


SEED_PROJECTS = [
    {"_id": "proj-a1", "name": "Alpha", "organization_id": "org-A"},
    {"_id": "proj-a2", "name": "Gamma", "organization_id": "org-A"},
    {"_id": "proj-b1", "name": "Beta", "organization_id": "org-B"},
]

SEED_DOCUMENTS = [
    # proj-a1: 2 incoming + 1 outgoing counted; contract, deleted and
    # duplicate-held rows must be excluded.
    {"_id": "d1", "project_id": "proj-a1", "uploadType": "incoming"},
    {"_id": "d2", "project_id": "proj-a1", "uploadType": "Incoming"},
    {"_id": "d3", "project_id": "proj-a1", "uploadType": "outgoing", "lifecycle_state": "active"},
    {"_id": "d4", "project_id": "proj-a1", "uploadType": "contract"},
    {"_id": "d5", "project_id": "proj-a1", "uploadType": "incoming", "lifecycle_state": "deleted"},
    {"_id": "d6", "project_id": "proj-a1", "uploadType": "outgoing", "lifecycle_state": "duplicate_review"},
    # proj-a2 has no documents at all -> must report 0, not a missing entry.
    # proj-b1 belongs to the other tenant.
    {"_id": "d7", "project_id": "proj-b1", "uploadType": "incoming"},
    {"_id": "d8", "project_id": "proj-b1", "uploadType": "outgoing"},
]

SEED_USERS = [
    # Org-A org-wide users: no explicit projects -> member of every org-A project.
    {"_id": "u1", "roles": ["orgadmin"], "organization_id": "org-A", "projects": []},
    {"_id": "u2", "roles": ["orguser"], "organization_id": "org-A"},
    # Project-scoped user, assigned to proj-a1 only.
    {"_id": "u3", "roles": ["projectuser"], "organization_id": "org-A", "projects": ["proj-a1"]},
    # Legacy row storing assignments as one comma-joined string.
    {"_id": "u4", "roles": ["projectuser"], "organization_id": "org-A", "projects": ["proj-a1,proj-a2"]},
    # Inactive and disabled users never count.
    {"_id": "u5", "roles": ["projectuser"], "organization_id": "org-A", "projects": ["proj-a1"], "is_active": False},
    {"_id": "u6", "roles": ["orguser"], "organization_id": "org-A", "disabled": True},
    # Superadmin has access everywhere but is not a project team member.
    {"_id": "u7", "roles": ["superadmin"], "projects": []},
    # Other tenant.
    {"_id": "u8", "roles": ["orgadmin"], "organization_id": "org-B", "projects": []},
]


# ---------------------------------------------------------------------------
# Minimal Mongo-ish fakes
# ---------------------------------------------------------------------------

def _matches(doc, query) -> bool:
    """Evaluate the operator subset the endpoint actually emits."""
    for key, value in (query or {}).items():
        if key == "$and":
            if not all(_matches(doc, sub) for sub in value):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, sub) for sub in value):
                return False
            continue

        actual = doc.get(key)
        if isinstance(value, dict):
            for op, operand in value.items():
                if op == "$in":
                    candidates = actual if isinstance(actual, list) else [actual]
                    if not any(item in operand for item in candidates):
                        return False
                elif op == "$nin":
                    if actual in operand:
                        return False
                elif op == "$ne":
                    if actual == operand:
                        return False
                elif op == "$exists":
                    if (key in doc) != operand:
                        return False
                elif op == "$regex":
                    flags = re.I if "i" in value.get("$options", "") else 0
                    if not isinstance(actual, str) or not re.search(operand, actual, flags):
                        return False
                elif op == "$options":
                    continue
                else:  # pragma: no cover - unexpected operator
                    raise AssertionError(f"unsupported operator {op}")
        elif actual != value:
            return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield doc

        return _gen()


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, query=None, projection=None, *_args, **_kwargs):
        return _FakeCursor([d for d in self._docs if _matches(d, query)])

    async def find_one(self, query=None, *_args, **_kwargs):
        return next((d for d in self._docs if _matches(d, query)), None)

    async def count_documents(self, query=None, **_kwargs):
        return len([d for d in self._docs if _matches(d, query)])

    def aggregate(self, pipeline):
        """Run the router's [{$match}, {$group}] pipeline.

        The ``$match`` stage is evaluated faithfully (that is the part carrying
        the uploadType / lifecycle_state rules); the ``$group`` stage is
        simulated to match the router's spec.
        """
        match_stage = pipeline[0]["$match"]
        matched = [d for d in self._docs if _matches(d, match_stage)]

        grouped: dict[str, dict[str, int]] = {}
        for doc in matched:
            key = str(doc.get("project_id"))
            row = grouped.setdefault(key, {"_id": key, "total": 0, "incoming": 0, "outgoing": 0})
            row["total"] += 1
            direction = str(doc.get("uploadType") or "").lower()
            if direction in ("incoming", "outgoing"):
                row[direction] += 1

        return _FakeCursor(list(grouped.values()))


class _FakeDB:
    def __init__(self):
        self.projects = _FakeCollection(SEED_PROJECTS)
        self.documents = _FakeCollection(SEED_DOCUMENTS)
        self.users = _FakeCollection(SEED_USERS)
        self.subscriptions = _FakeCollection([])
        self.organization_memberships = _FakeCollection([])
        self.project_memberships = _FakeCollection([])


def _user(*, org, roles=("orguser",), projects=()):
    return SimpleNamespace(
        id="caller",
        username="caller",
        email="caller@example.com",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
        disabled=False,
    )


def _run(monkeypatch, user):
    """Wire overrides, call GET /api/projects/stats, return {project_id: row}."""
    async def _allow(*_args, **_kwargs):
        return True

    monkeypatch.setattr(PermissionService, "user_has_permission", _allow)

    async def _allow_entitlement(*_args, **_kwargs):
        return True, "test_entitlement"

    monkeypatch.setattr(
        EntitlementService,
        "check_permission_entitlement",
        _allow_entitlement,
    )

    fake_db = _FakeDB()

    # ProjectService resolves its own handle instead of using the get_db
    # dependency, so it has to be pointed at the same fake database.
    async def _fake_get_database():
        return fake_db

    monkeypatch.setattr(project_service_module, "get_database", _fake_get_database)

    async def _override_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)  # no context manager -> startup events do not run
        resp = client.get("/api/projects/stats")
        assert resp.status_code == 200, resp.text
        return {row["project_id"]: row for row in resp.json()}
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_org_user_gets_counts_only_for_own_tenant(monkeypatch):
    stats = _run(monkeypatch, _user(org="org-A", roles=("orguser",)))
    assert set(stats) == {"proj-a1", "proj-a2"}
    assert "proj-b1" not in stats  # never leaks, not even as a zero


def test_project_user_gets_counts_only_for_assigned_projects(monkeypatch):
    stats = _run(
        monkeypatch,
        _user(org="org-A", roles=("projectuser",), projects=("proj-a1",)),
    )
    assert set(stats) == {"proj-a1"}


def test_orphan_user_gets_nothing(monkeypatch):
    user = _user(org=None, roles=("orguser",))
    fake_db = _FakeDB()

    async def _allow(*_args, **_kwargs):
        return True

    async def _allow_entitlement(*_args, **_kwargs):
        return True, "test_entitlement"

    monkeypatch.setattr(PermissionService, "user_has_permission", _allow)
    monkeypatch.setattr(
        EntitlementService,
        "check_permission_entitlement",
        _allow_entitlement,
    )

    async def _fake_get_database():
        return fake_db

    monkeypatch.setattr(project_service_module, "get_database", _fake_get_database)

    async def _override_db():
        yield fake_db

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    try:
        response = TestClient(app).get("/api/projects/stats")
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_superadmin_gets_counts_for_every_project(monkeypatch):
    stats = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    assert set(stats) == {"proj-a1", "proj-a2", "proj-b1"}


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def test_letter_counts_exclude_contracts_and_hidden_lifecycle_states(monkeypatch):
    stats = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    row = stats["proj-a1"]
    # d1, d2 (incoming, mixed case) + d3 (outgoing). d4 contract, d5 deleted and
    # d6 duplicate_review are all excluded.
    assert row["letterCount"] == 3
    assert row["incomingCount"] == 2
    assert row["outgoingCount"] == 1


def test_project_without_documents_reports_zero_rather_than_missing(monkeypatch):
    stats = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    assert stats["proj-a2"]["letterCount"] == 0
    assert stats["proj-a2"]["incomingCount"] == 0
    assert stats["proj-a2"]["outgoingCount"] == 0


def test_team_size_counts_org_wide_and_project_assigned_users(monkeypatch):
    stats = _run(monkeypatch, _user(org=None, roles=("superadmin",)))
    # proj-a1: u1 + u2 (org-wide) + u3 + u4 (comma-joined assignment).
    # u5 inactive, u6 disabled, u7 superadmin, u8 other tenant -> excluded.
    assert stats["proj-a1"]["teamSize"] == 4
    # proj-a2: u1 + u2 (org-wide) + u4. u3 is pinned to proj-a1 only.
    assert stats["proj-a2"]["teamSize"] == 3
    # proj-b1: u8 only.
    assert stats["proj-b1"]["teamSize"] == 1


# ---------------------------------------------------------------------------
# Membership helper
# ---------------------------------------------------------------------------

def test_normalize_id_list_handles_lists_strings_and_comma_joins():
    normalize = projects_router._normalize_id_list
    assert normalize(None) == []
    assert normalize([]) == []
    assert normalize("org-A") == ["org-A"]
    assert normalize(["proj-1", "proj-2"]) == ["proj-1", "proj-2"]
    assert normalize(["proj-1,proj-2"]) == ["proj-1", "proj-2"]
    assert normalize([" proj-1 , proj-1 "]) == ["proj-1"]


@pytest.mark.asyncio
async def test_members_ignore_assignments_to_projects_outside_the_visible_set():
    """A user pinned to an invisible project must not fall back to org-wide."""
    db = _FakeDB()
    db.users = _FakeCollection(
        [
            {
                "_id": "u9",
                "roles": ["projectuser"],
                "organization_id": "org-A",
                "projects": ["proj-hidden"],
            }
        ]
    )
    counts = await projects_router._count_members_by_project(
        db, {"proj-a1": "org-A", "proj-a2": "org-A"}
    )
    assert counts == {"proj-a1": 0, "proj-a2": 0}
