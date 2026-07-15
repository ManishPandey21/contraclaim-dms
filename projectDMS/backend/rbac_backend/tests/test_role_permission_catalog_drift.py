from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from backend.rbac_backend.services.permission_service import PermissionService
from backend.rbac_backend.services.role_service import RoleService
from backend.rbac_backend.core.permissions import CLIENT_DMS_PERMISSIONS

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


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

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        expected_id = query.get("_id")
        matched = 0
        modified = 0
        for doc in self.docs:
            if doc.get("_id") != expected_id:
                continue
            matched = 1
            set_values = update.get("$set") or {}
            if set_values:
                doc.update(set_values)
                modified = 1
            break

        class _Result:
            matched_count = matched
            modified_count = modified

        return _Result()


class _DB:
    def __init__(self):
        now = datetime.utcnow()
        self.roles = _Collection(
            [
                {
                    "_id": "orgadmin",
                    "name": "Organization-Admin",
                    "description": "Organization administrator",
                    "permissions": [
                        "dms.document.view",
                        "dms.document.delete",
                        "dms.admin",
                    ],
                    "scope": "organization",
                    "is_system": True,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )
        self.users = _Collection(
            [
                {
                    "_id": "user-1",
                    "email": "project-admin@example.test",
                    "roles": ["orgadmin"],
                    "permissions": ["custom:direct"],
                }
            ]
        )
        self.permissions = _Collection(
            [
                {
                    "_id": "perm-view",
                    "id": "perm-view",
                    "name": "dms.document.view",
                    "description": "View DMS documents",
                    "category": "document_management",
                    "resource": "dms.document",
                    "action": "read",
                    "is_system": True,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        )


class _RoleService(RoleService):
    def __init__(self, db):
        super().__init__()
        self._db = db

    async def _get_db(self):
        return self._db


class _PermissionService(PermissionService):
    def __init__(self, db):
        super().__init__()
        self._db = db

    async def _get_db(self):
        return self._db


async def test_role_service_returns_saved_permission_names_missing_from_catalog():
    service = _RoleService(_DB())

    permissions = await service.get_role_permissions("orgadmin")
    names = {permission.name for permission in permissions}

    assert names == {
        "dms.document.view",
        "dms.document.delete",
        "dms.admin",
    }
    assert {permission.id for permission in permissions} >= {
        "perm-view",
        "dms.document.delete",
        "dms.admin",
    }


async def test_permission_service_returns_saved_permission_names_missing_from_catalog():
    service = _PermissionService(_DB())

    permissions = await service.get_permissions_for_role("orgadmin")
    names = {permission.name for permission in permissions}

    assert names == {
        "dms.document.view",
        "dms.document.delete",
        "dms.admin",
    }


async def test_permission_service_returns_effective_permission_names_for_current_user_profile():
    service = _PermissionService(_DB())

    names = set(await service.get_effective_permission_names("user-1"))

    assert {
        "custom:direct",
        "dms.document.view",
        "dms.document.delete",
        "dms.admin",
        "users:read",
    }.issubset(names)


async def test_user_has_permission_handles_raw_role_permissions_without_catalog_match():
    service = _PermissionService(_DB())

    assert await service.user_has_permission("user-1", "dms.document.delete", log=False)


async def test_role_service_saves_and_retrieves_full_client_dms_permissions():
    service = _RoleService(_DB())

    updated = await service.update_role_permissions(
        "orgadmin",
        list(CLIENT_DMS_PERMISSIONS),
        updated_by=SimpleNamespace(id="superadmin-user", roles=["superadmin"]),
    )
    assert set(updated.permissions) == set(CLIENT_DMS_PERMISSIONS)

    permissions = await service.get_role_permissions("orgadmin")
    names = {permission.name for permission in permissions}
    missing = sorted(set(CLIENT_DMS_PERMISSIONS) - names)
    assert not missing
