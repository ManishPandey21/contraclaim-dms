"""P0-006 regression: Org-Admin Client DMS permission save/retrieve over HTTP.

A prior production issue lost Client DMS permissions on retrieve: a role saved
with the full Client DMS permission set returned a truncated list because
permissions not present in the live catalog were dropped, and/or the
``response_model=List[Permission]`` serialization discarded them.

The service-level round trip is covered by
``test_role_permission_catalog_drift.py``. This test guards the actual HTTP
boundary the production bug surfaced on:

    GET /api/roles/{role_id}/permissions

driven as an Org-Admin, with several Client DMS permissions intentionally
*absent from the catalog* so the synthetic-stub path and the response-model
serialization are both exercised end to end.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.permissions import CLIENT_DMS_PERMISSIONS
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.routers.roles import get_role_service
    from rbac_backend.services import permission_service as permission_module
    from rbac_backend.services.role_service import RoleService

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - env without TestClient/app
    _IMPORTS_OK = False
    _IMPORT_ERR = exc

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self.docs]


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = [dict(doc) for doc in docs]

    async def find_one(self, query: Dict[str, Any]):
        expected_id = query.get("_id")
        for doc in self.docs:
            if doc.get("_id") == expected_id:
                return dict(doc)
        return None

    def find(self, query: Dict[str, Any]):
        ors = query.get("$or") or []
        names: set[str] = set()
        ids: set[Any] = set()
        for clause in ors:
            if "name" in clause:
                names.update(clause["name"].get("$in") or [])
            if "_id" in clause:
                ids.update(clause["_id"].get("$in") or [])
        matched = [
            doc
            for doc in self.docs
            if doc.get("name") in names or doc.get("_id") in ids
        ]
        return _Cursor(matched)


class _DB:
    """Org-Admin role saved with the FULL Client DMS set, but the catalog only
    knows about the first permission. The remaining saved permissions must still
    come back on retrieve (the production failure mode)."""

    def __init__(self):
        now = datetime.utcnow()
        saved = list(CLIENT_DMS_PERMISSIONS)
        self.roles = _Collection(
            [
                {
                    "_id": "orgadmin",
                    "name": "Organization-Admin",
                    "description": "Organization administrator",
                    "permissions": saved,
                    "scope": "organization",
                    "organization_id": "org-A",
                    "is_system": True,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )
        # Only the first Client DMS permission exists in the catalog; the rest are
        # deliberately missing to force the synthetic-stub + serialization path.
        catalog_name = saved[0]
        self.permissions = _Collection(
            [
                {
                    "_id": "perm-0",
                    "id": "perm-0",
                    "name": catalog_name,
                    "description": f"Catalog permission {catalog_name}",
                    "category": "document_management",
                    "resource": catalog_name.rsplit(".", 1)[0],
                    "action": "read",
                    "is_system": True,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )


class _RoleService(RoleService):
    """Real RoleService logic over a fake DB. can_view_role is forced True so the
    test targets retrieval/serialization, not scope authz (covered elsewhere)."""

    def __init__(self, db):
        super().__init__()
        self._db = db

    async def _get_db(self):
        return self._db

    async def can_view_role(self, current_user, role):  # type: ignore[override]
        return True


def _org_admin():
    return SimpleNamespace(
        id="orgadmin-user",
        username="orgadmin",
        email="orgadmin@example.com",
        roles=["orgadmin"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=[],
        disabled=False,
    )


@pytest.fixture
def client(monkeypatch):
    # Org-Admin holds roles:read in production; short-circuit the catalog lookup
    # so require_permission("roles:read") passes without a live permissions DB.
    async def _has_perm(self, *args, **kwargs):
        return True

    monkeypatch.setattr(permission_module.PermissionService, "user_has_permission", _has_perm)

    app.dependency_overrides[get_current_user] = _org_admin
    app.dependency_overrides[get_role_service] = lambda: _RoleService(_DB())
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_org_admin_retrieve_returns_full_client_dms_permission_set(client):
    resp = client.get("/api/roles/orgadmin/permissions")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    returned = {item["name"] for item in body}
    missing = sorted(set(CLIENT_DMS_PERMISSIONS) - returned)

    assert not missing, f"retrieve dropped saved Client DMS permissions: {missing}"


def test_org_admin_retrieve_serializes_catalog_missing_permissions(client):
    """Every returned permission must serialize through response_model=List[Permission]
    with a usable name/id even when it was only a saved name (no catalog row)."""
    resp = client.get("/api/roles/orgadmin/permissions")
    assert resp.status_code == 200, resp.text

    for item in resp.json():
        assert item.get("name"), item
        assert item.get("id"), item
