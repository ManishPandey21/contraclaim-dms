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

    async def _begin_role_authority_change(self, db, role_id) -> list:
        return []

    async def _complete_role_authority_change(self, user_ids, applied: bool) -> None:
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


# --------------------------------------------------------------------------- #
# A role name must not be another role's legacy spelling (R-A9B v2 review F5/F4)
# --------------------------------------------------------------------------- #


def _super_admin():
    return SimpleNamespace(id="super-admin-user", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


@pytest.mark.parametrize(
    "name,org_admin_status",
    [
        ("project-user", 400),  # resolves one hop to projectuser; a lookalike would leave every holder with nothing
        ("Organization User", 400),
        ("org-admin", 403),  # already a reserved key for non-superadmins
        ("Super Admin", 403),  # a system key: never for non-superadmins
    ],
)
async def test_no_actor_may_create_a_role_named_with_another_roles_legacy_spelling(name, org_admin_status):
    for actor, expected in ((_org_admin(), org_admin_status), (_super_admin(), 400)):
        db = _DB()
        before = len(db.roles.docs)
        with pytest.raises(RoleServiceError) as exc:
            await _RoleService(db).create_role(RoleCreate(name=name, permissions=["dms.document.view"]), actor)
        assert exc.value.status_code == expected, (actor.roles, name, exc.value)
        assert len(db.roles.docs) == before, "a refused role was written"


@pytest.mark.parametrize("name", ["SuperAdmin", "super_user"])
async def test_a_non_superadmin_cannot_create_a_role_named_as_a_system_key(name):
    """Not table spellings, but a role named so reads as Super Admin to the display-name grants."""
    with pytest.raises(RoleServiceError) as exc:
        await _RoleService(_DB()).create_role(RoleCreate(name=name, permissions=["dms.document.view"]), _org_admin())
    assert exc.value.status_code == 403


async def test_no_actor_may_rename_a_role_to_another_roles_legacy_spelling():
    for actor in (_org_admin(), _super_admin()):
        db = _DB()
        with pytest.raises(RoleServiceError) as exc:
            await _RoleService(db).update_role("custom-org-role", RoleUpdate(name="project-user"), actor)
        assert exc.value.status_code == 400
        assert db.roles.docs[0]["name"] == "Custom Org Role"


async def test_a_role_name_that_spells_an_existing_roles_key_is_refused():
    """`Custom-Project-Role` is the key of the existing `custom-project-role` role."""
    db = _DB()
    with pytest.raises(RoleServiceError) as exc:
        await _RoleService(db).create_role(
            RoleCreate(name="Custom-Project-Role", permissions=["dms.document.view"]), _org_admin()
        )
    assert exc.value.status_code == 400


async def test_re_sending_an_unchanged_name_is_not_a_rename():
    """A role that already carries a lookalike name stays editable."""
    db = _DB()
    db.roles.docs[0]["name"] = "project-user"
    await _RoleService(db).update_role(
        "custom-org-role", RoleUpdate(name="project-user", description="edited"), _super_admin()
    )
    assert db.roles.docs[0]["description"] == "edited"


async def test_an_ordinary_role_name_is_still_accepted():
    created = await _RoleService(_DB()).create_role(
        RoleCreate(name="Site Reviewer", permissions=["dms.document.view"]), _org_admin()
    )
    assert created.name == "Site Reviewer"


async def test_the_canonical_role_may_keep_a_name_that_spells_its_own_key():
    db = _DB()
    await _RoleService(db).update_role("orgadmin", RoleUpdate(name="Organization-Admin"), _super_admin())
    assert next(doc for doc in db.roles.docs if doc["_id"] == "orgadmin")["name"] == "Organization-Admin"
