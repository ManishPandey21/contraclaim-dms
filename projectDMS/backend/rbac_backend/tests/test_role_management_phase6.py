from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from rbac_backend.models.role import RoleCreate, RoleUpdate
from rbac_backend.services.role_service import RoleService, RoleServiceError

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Result:
    def __init__(self, *, inserted_id: str | None = None, matched_count: int = 0, modified_count: int = 0):
        self.inserted_id = inserted_id
        self.matched_count = matched_count
        self.modified_count = modified_count


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]] | None = None):
        self.docs = [dict(doc) for doc in (docs or [])]

    async def find_one(self, query: Dict[str, Any]):
        for doc in self.docs:
            if self._matches(doc, query):
                return dict(doc)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        new_doc = dict(doc)
        new_doc["_id"] = f"role-{len(self.docs) + 1}"
        self.docs.append(new_doc)
        return _Result(inserted_id=new_doc["_id"])

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            if "$set" in update:
                doc.update(update["$set"])
            if "$addToSet" in update:
                for key, value in update["$addToSet"].items():
                    values = doc.setdefault(key, [])
                    if value not in values:
                        values.append(value)
            if "$pull" in update:
                for key, value in update["$pull"].items():
                    doc[key] = [item for item in doc.get(key, []) if item != value]
            return _Result(matched_count=1, modified_count=1)
        return _Result(matched_count=0, modified_count=0)

    def _matches(self, doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for key, expected in (query or {}).items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"] and str(actual) not in {str(item) for item in expected["$in"]}:
                    return False
            elif actual != expected and str(actual) != str(expected):
                return False
        return True


class _DB:
    def __init__(self):
        now = datetime.utcnow()
        self.roles = _Collection(
            [
                {
                    "_id": "custom-org-role",
                    "name": "Custom Org Role",
                    "description": "Custom role",
                    "permissions": ["dms.document.view"],
                    "scope": "organization",
                    "organization_id": "org-A",
                    "project_id": None,
                    "is_system": False,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "_id": "custom-project-role",
                    "name": "Custom Project Role",
                    "description": "Custom project role",
                    "permissions": ["dms.document.view"],
                    "scope": "project",
                    "organization_id": "org-A",
                    "project_id": "proj-A",
                    "is_system": False,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "_id": "orgadmin",
                    "name": "Organization Admin",
                    "description": "Built-in admin role",
                    "permissions": ["*"],
                    "scope": "organization",
                    "organization_id": "org-A",
                    "project_id": None,
                    "is_system": True,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                },
            ]
        )


class _RoleService(RoleService):
    def __init__(self, db):
        super().__init__()
        self._db = db

    async def _get_db(self):
        return self._db

    async def _invalidate_role_caches(self, role_id: str) -> None:
        return None


def _org_admin():
    return SimpleNamespace(
        id="org-admin-user",
        roles=["orgadmin"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=[],
    )


def _project_admin():
    return SimpleNamespace(
        id="project-admin-user",
        roles=["projectadmin"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )


async def test_phase6_org_admin_can_create_scoped_role_but_not_privileged_permission():
    service = _RoleService(_DB())

    created = await service.create_role(
        RoleCreate(name="Reviewer", permissions=["dms.document.view", "dms.document.view"]),
        _org_admin(),
    )

    assert created.scope == "organization"
    assert created.organization_id == "org-A"
    assert created.permissions == ["dms.document.view"]

    with pytest.raises(RoleServiceError) as exc:
        await service.create_role(
            RoleCreate(name="Billing Manager", permissions=["billing.plan.manage"]),
            _org_admin(),
        )

    assert exc.value.status_code == 403
    assert "privileged permission" in str(exc.value)


async def test_phase6_non_superadmin_cannot_escalate_role_shape_or_reserved_roles():
    service = _RoleService(_DB())

    for update in (
        RoleUpdate(is_system=True),
        RoleUpdate(scope="system"),
        RoleUpdate(organization_id="org-B"),
        RoleUpdate(name="orgadmin"),
    ):
        with pytest.raises(RoleServiceError) as exc:
            await service.update_role("custom-org-role", update, _org_admin())
        assert exc.value.status_code == 403

    with pytest.raises(RoleServiceError) as exc:
        await service.update_role("orgadmin", RoleUpdate(description="takeover"), _org_admin())

    assert exc.value.status_code == 403


async def test_phase6_direct_permission_update_and_add_validate_assignment():
    service = _RoleService(_DB())

    updated = await service.update_role_permissions(
        "custom-org-role",
        ["dms.document.view", "dms.document.upload"],
        _org_admin(),
    )
    assert updated.permissions == ["dms.document.view", "dms.document.upload"]

    for permission in ("*", "platform.role.manage", "roles:assign", "subscription.entitlement.manage"):
        with pytest.raises(RoleServiceError) as exc:
            await service.add_permission_to_role("custom-org-role", permission, _org_admin())
        assert exc.value.status_code == 403

    with pytest.raises(RoleServiceError) as exc:
        await service.update_role_permissions(
            "custom-org-role",
            ["dms.document.view", "permissions:read"],
            _org_admin(),
        )

    assert exc.value.status_code == 403


async def test_phase6_delete_unauthorized_role_surfaces_forbidden():
    service = _RoleService(_DB())

    with pytest.raises(RoleServiceError) as exc:
        await service.delete_role("custom-org-role", _project_admin())

    assert exc.value.status_code == 403

    assert await service.delete_role("custom-project-role", _project_admin()) is True
