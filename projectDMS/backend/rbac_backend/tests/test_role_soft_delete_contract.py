"""What `DELETE /api/roles/{id}` means, and what a deleted role may still do.

R-A8Z Stage B saw `DELETE /api/roles/{id}` answer 200 and `GET /api/roles/{id}`
still answer 200 for the same role. The contract, read from source:

* `RoleService.delete_role` is documented and implemented as a SOFT delete: it
  sets `is_active=False`, `deleted_at`, `deleted_by` and keeps the document.
* `get_roles_paginated` filters `is_active != False`, so a deleted role leaves
  every listing.
* `get_role_by_id` does not filter, and `GET /api/roles/{id}` is still guarded by
  `roles:read` plus `can_view_role` (tenant scope), so a direct lookup of a
  deleted role is a historical read inside the caller's own scope. The router
  emits a `role.deleted` audit event with the before-image.

That is disposition **A - intentional soft delete, direct historical lookup
allowed**, pinned by the first three tests.

Being readable is not the same as granting authority. R-A9A found (F-A9A-1) that
nothing on the permission path read `is_active`, and `delete_role` neither
detached the role from users nor invalidated their permission cache, so a user
still holding a deleted role kept its permissions. R-A9B closed it in
`PermissionService`, the one place effective permissions are computed: a role
whose `is_active` is `False` contributes no permission, no wildcard and no role
name to any resolver, and `delete_role` now drops the cached grant of every
holder. The lifecycle flag is the existing `is_active` (absent means active, the
same reading the listing uses); there is no second flag.

There is no API that reactivates a role: `RoleUpdate` has no `is_active` field.
The resolver reads the flag's current value, so a role reactivated in the database
grants again - the filter is state, not a tombstone.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.models.role import RoleUpdate
from rbac_backend.services import permission_service as permission_module
from rbac_backend.services import runtime_state as runtime_state_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.role_service import RoleService
from rbac_backend.utils.audit_logger import AuditLogger

ROLE_ID = ObjectId()
OTHER_ROLE_ID = ObjectId()
USER_ID = "user-holding-deleted-role"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# In-memory database and Redis
# --------------------------------------------------------------------------- #


def _values(value: Any) -> List[Any]:
    return value if isinstance(value, list) else [value]


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in condition):
                return False
            continue
        value = doc.get(key)
        if isinstance(condition, dict):
            if "$in" in condition:
                wanted = {str(item) for item in condition["$in"]}
                if not any(str(item) in wanted for item in _values(value)):
                    return False
            if "$ne" in condition and value == condition["$ne"]:
                return False
        elif not any(str(item) == str(condition) for item in _values(value)):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self._docs]


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query=None, *_args, **_kwargs):
        return _Cursor([doc for doc in self.docs if _matches(doc, query or {})])

    async def update_one(self, query, update):
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Redis:
    def __init__(self):
        self.store: Dict[str, Any] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


def _role(role_id, permissions: List[str], *, name: str = "Site Reviewer", active: bool = True) -> Dict[str, Any]:
    return {
        "_id": role_id,
        "name": name,
        "scope": "organization",
        "organization_id": "org1",
        "permissions": permissions,
        "is_system": False,
        "is_active": active,
    }


def _catalogue(*names: str) -> List[Dict[str, Any]]:
    return [{"_id": ObjectId(), "name": name, "is_active": True} for name in names]


class _Harness:
    def __init__(self, monkeypatch, *, redis: Optional[_Redis] = None):
        self.monkeypatch = monkeypatch
        self.redis = redis
        self.db = SimpleNamespace()

        async def _get_db(_self):
            return self.db

        async def _get_redis():
            return self.redis

        async def _log_noop(*_args, **_kwargs):
            return None

        runtime = SimpleNamespace(get_redis=_get_redis)
        monkeypatch.setattr(RoleService, "_get_db", _get_db)
        monkeypatch.setattr(PermissionService, "_get_db", _get_db)
        monkeypatch.setattr(permission_module, "get_runtime_state", lambda: runtime)
        monkeypatch.setattr(runtime_state_module, "get_runtime_state", lambda: runtime)
        monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)

    def seed(self, roles: List[Dict[str, Any]], *, role_refs: List[str], catalogue: List[str]) -> None:
        self.db = SimpleNamespace(
            roles=_Collection(roles),
            users=_Collection([{"_id": USER_ID, "roles": role_refs, "organization_id": "org1", "projects": []}]),
            permissions=_Collection(_catalogue(*catalogue)),
            projects=_Collection([{"_id": "proj-foreign", "organization_id": "org-foreign"}]),
        )

    def delete(self, role_id) -> bool:
        superadmin = SimpleNamespace(id="sa", roles=["superadmin"], organization_id=None, projects=[])
        return asyncio.run(RoleService().delete_role(str(role_id), superadmin))

    # Every resolver that turns role references into authority.
    def has(self, permission: str) -> bool:
        return asyncio.run(PermissionService().user_has_permission(USER_ID, permission, log=False))

    def listed(self) -> set:
        return {p.name for p in asyncio.run(PermissionService().get_user_permissions(USER_ID))}

    def effective(self) -> set:
        return set(asyncio.run(PermissionService().get_effective_permission_names(USER_ID)))

    def policy(self, permission: str) -> bool:
        policy = PolicyService(
            permission_service=PermissionService(),
            scope_service=SimpleNamespace(is_superadmin=lambda _user: False),
            entitlement_service=object(),
            audit_service=object(),
            usage_metering_service=object(),
        )
        return asyncio.run(policy.has_permission(SimpleNamespace(id=USER_ID, roles=[]), permission))


@pytest.fixture
def harness(monkeypatch) -> _Harness:
    h = _Harness(monkeypatch)
    h.seed([_role(ROLE_ID, ["dms.document.view"])], role_refs=[str(ROLE_ID)], catalogue=["dms.document.view"])
    return h


# --------------------------------------------------------------------------- #
# Disposition A: soft delete, historical read, hidden from listings
# --------------------------------------------------------------------------- #


def test_delete_is_a_soft_delete_that_keeps_the_document(harness) -> None:
    assert harness.delete(ROLE_ID) is True
    stored = harness.db.roles.docs[0]
    assert stored["is_active"] is False
    assert stored.get("deleted_at") is not None and stored.get("deleted_by") == "sa"
    assert len(harness.db.roles.docs) == 1, "the role document was removed; the contract is a soft delete"


def test_a_deleted_role_is_still_readable_by_id(harness) -> None:
    """Disposition A: a direct lookup is a historical read, still behind roles:read + can_view_role."""
    harness.delete(ROLE_ID)
    role = asyncio.run(RoleService().get_role_by_id(str(ROLE_ID)))
    assert role is not None and role.is_active is False
    assert role.permissions == ["dms.document.view"], "the historical record lost its permissions"


def test_the_listing_query_excludes_deleted_roles() -> None:
    """`get_roles_paginated` is the only listing path; its query carries the activity filter."""
    import inspect

    source = inspect.getsource(RoleService.get_roles_paginated)
    assert 'query["is_active"] = {"$ne": False}' in source, (
        "the role listing no longer hides soft-deleted roles"
    )


# --------------------------------------------------------------------------- #
# F-A9A-1: a deleted role grants nothing, on every resolver
# --------------------------------------------------------------------------- #


def test_an_active_role_grants_its_permissions_on_every_resolver(harness) -> None:
    assert harness.has("dms.document.view")
    assert "dms.document.view" in harness.listed()
    assert "dms.document.view" in harness.effective()
    assert harness.policy("dms.document.view")


def test_a_user_holding_only_a_deleted_role_has_none_of_its_permissions(harness) -> None:
    assert harness.has("dms.document.view"), "positive control: the role granted before deletion"
    harness.delete(ROLE_ID)

    assert not harness.has("dms.document.view")
    assert harness.listed() == set()
    assert harness.effective() == set()
    assert not harness.policy("dms.document.view")


def test_with_one_deleted_and_one_active_role_only_the_active_role_grants(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.seed(
        [_role(ROLE_ID, ["dms.document.delete"]), _role(OTHER_ROLE_ID, ["dms.document.view"], name="Viewer")],
        role_refs=[str(ROLE_ID), str(OTHER_ROLE_ID)],
        catalogue=["dms.document.delete", "dms.document.view"],
    )
    assert h.has("dms.document.delete") and h.has("dms.document.view")

    h.delete(ROLE_ID)

    assert not h.has("dms.document.delete")
    assert h.has("dms.document.view"), "deleting one role revoked the other role's permissions"
    assert h.listed() == {"dms.document.view"}
    assert h.effective() == {"dms.document.view"}
    assert not h.policy("dms.document.delete") and h.policy("dms.document.view")


def test_a_deleted_wildcard_role_grants_nothing(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.seed([_role(ROLE_ID, ["*"])], role_refs=[str(ROLE_ID)], catalogue=["dms.document.view"])
    assert h.has("dms.document.view") and h.effective() == {"*"}

    h.delete(ROLE_ID)

    assert not h.has("dms.document.view")
    assert h.listed() == set()
    assert h.effective() == set()


def test_a_deleted_role_confers_no_role_name_grant(monkeypatch) -> None:
    """`users:read` is granted by an org-admin role NAME, so the name must also be revoked."""
    h = _Harness(monkeypatch)
    h.seed(
        [_role(ROLE_ID, [], name="Organization Admin")],
        role_refs=[str(ROLE_ID)],
        catalogue=[],
    )
    assert h.has("users:read"), "positive control: the org-admin role name grants users:read"
    assert "users:read" in h.effective()

    h.delete(ROLE_ID)

    assert not h.has("users:read")
    assert "users:read" not in h.effective()


def test_a_deleted_role_named_superadmin_confers_no_resource_bypass(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    h.seed(
        [_role(ROLE_ID, [], name="Super Admin"), _role(OTHER_ROLE_ID, ["dms.document.view"], name="Viewer")],
        role_refs=[str(ROLE_ID), str(OTHER_ROLE_ID)],
        catalogue=["dms.document.view"],
    )

    def _foreign_project_access() -> bool:
        return asyncio.run(
            PermissionService().check_resource_access(USER_ID, "dms.document.view", "project", "proj-foreign")
        )

    assert _foreign_project_access(), "positive control: the super-admin role name bypasses membership"
    h.delete(ROLE_ID)
    assert not _foreign_project_access(), "a deleted super-admin role still bypassed resource membership"


def test_reactivation_is_not_an_api_and_the_filter_is_state_not_a_tombstone(harness) -> None:
    assert "is_active" not in RoleUpdate.model_fields, "a role reactivation API now exists; pin its contract"
    harness.delete(ROLE_ID)
    assert not harness.has("dms.document.view")

    harness.db.roles.docs[0]["is_active"] = True

    assert harness.has("dms.document.view")


def test_a_role_without_the_lifecycle_flag_is_active() -> None:
    """Documents that predate soft delete carry no `is_active`; the listing treats them as active."""
    assert permission_module.role_is_active({"_id": "legacy", "permissions": ["x"]})
    assert permission_module.role_is_active({"is_active": True})
    assert not permission_module.role_is_active({"is_active": False})


# --------------------------------------------------------------------------- #
# Cache: deletion must not survive in an already-cached grant
# --------------------------------------------------------------------------- #


def test_deleting_a_role_revokes_a_grant_already_in_the_permission_cache(monkeypatch) -> None:
    h = _Harness(monkeypatch, redis=_Redis())
    h.seed([_role(ROLE_ID, ["dms.document.view"])], role_refs=[str(ROLE_ID)], catalogue=["dms.document.view"])

    assert h.has("dms.document.view")
    cache_key = permission_module.permission_cache_key(USER_ID)
    assert cache_key in h.redis.store, "precondition: the grant is now served from the cache"
    assert "dms.document.view" in json.loads(h.redis.store[cache_key])["raw_permissions"]

    h.delete(ROLE_ID)

    assert cache_key not in h.redis.store, "delete_role left the holder's cached grant in place"
    assert f"user_jwt_min_iat:{USER_ID}" in h.redis.store, "holders' sessions were not asked to re-authenticate"
    assert not h.has("dms.document.view")


def test_a_grant_written_by_an_in_flight_check_after_deletion_is_not_served(monkeypatch) -> None:
    """The race the R-A9B review reproduced: read roles, delete_role invalidates, then the
    in-flight check writes its (pre-deletion) entry back."""
    h = _Harness(monkeypatch, redis=_Redis())
    h.seed([_role(ROLE_ID, ["dms.document.view"])], role_refs=[str(ROLE_ID)], catalogue=["dms.document.view"])
    assert h.has("dms.document.view")
    cache_key = permission_module.permission_cache_key(USER_ID)
    stale_entry = h.redis.store[cache_key]

    h.delete(ROLE_ID)
    h.redis.store[cache_key] = stale_entry  # the late write lands after the invalidation

    assert not h.has("dms.document.view"), "a cache entry computed before the deletion was served"


def test_a_cache_entry_without_a_computation_stamp_is_never_served(monkeypatch) -> None:
    h = _Harness(monkeypatch, redis=_Redis())
    h.seed([_role(ROLE_ID, [])], role_refs=[str(ROLE_ID)], catalogue=[])
    h.redis.store[permission_module.permission_cache_key(USER_ID)] = json.dumps(
        {"raw_permissions": ["dms.document.delete"], "role_names": [], "perm_names": []}
    )
    assert not h.has("dms.document.delete")


# --------------------------------------------------------------------------- #
# Name-based authority: system roles are referenced by their key
# --------------------------------------------------------------------------- #


def _principal(monkeypatch, role_refs: List[str], roles: List[Dict[str, Any]]):
    from rbac_backend.core import security as security_module

    db = SimpleNamespace(
        users=_Collection([{"_id": USER_ID, "email": "held@example.com", "username": "held", "roles": role_refs}]),
        roles=_Collection(roles),
    )
    return asyncio.run(security_module.resolve_stored_principal(db, USER_ID))


@pytest.mark.parametrize("key", ["superadmin", "orgadmin"])
def test_a_deleted_system_role_is_dropped_from_the_principal(monkeypatch, key: str) -> None:
    """`CurrentUser.roles` feeds the superadmin bypass, scope and role manageability by NAME."""
    from rbac_backend.services.scope_service import ScopeService

    system_role = {"_id": key, "name": key, "permissions": ["*"], "is_system": True, "is_active": True}
    other = _role(OTHER_ROLE_ID, ["dms.document.view"], name="Viewer")
    active = _principal(monkeypatch, [key, str(OTHER_ROLE_ID)], [system_role, other])
    assert key in active.roles, "positive control: the active system role is on the principal"

    system_role["is_active"] = False
    revoked = _principal(monkeypatch, [key, str(OTHER_ROLE_ID)], [system_role, other])

    assert key not in revoked.roles
    assert str(OTHER_ROLE_ID) in revoked.roles, "an active role was dropped with the deleted one"
    assert not ScopeService.is_superadmin(ScopeService(), revoked)


def test_a_role_reference_without_a_document_is_kept_on_the_principal(monkeypatch) -> None:
    """Unchanged behaviour: bare keys with no role document still reach the principal."""
    principal = _principal(monkeypatch, ["orguser"], [])
    assert principal.roles == ["orguser"]


def test_get_current_user_drops_a_deleted_system_role(monkeypatch) -> None:
    from rbac_backend.core import security as security_module

    monkeypatch.setattr(
        runtime_state_module, "get_runtime_state", lambda: SimpleNamespace(redis_url=None, get_redis=None)
    )
    db = SimpleNamespace(
        users=_Collection([{"_id": USER_ID, "email": "held@example.com", "roles": ["superadmin"]}]),
        roles=_Collection([{"_id": "superadmin", "name": "superadmin", "is_active": False}]),
    )
    token = security_module.create_access_token({"sub": "held@example.com"})
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"}, cookies={})

    principal = asyncio.run(security_module.get_current_user(request, db))

    assert principal.roles == []
    assert principal.email == "held@example.com"


def test_a_role_store_failure_while_authenticating_is_an_outage_not_a_logout(monkeypatch) -> None:
    """A 401 makes the client clear the session; a role lookup that cannot run is a 503."""
    from fastapi import HTTPException

    from rbac_backend.core import security as security_module

    class _BrokenRoles:
        def find(self, *_args, **_kwargs):
            raise RuntimeError("roles collection unreachable")

    monkeypatch.setattr(
        runtime_state_module, "get_runtime_state", lambda: SimpleNamespace(redis_url=None, get_redis=None)
    )
    db = SimpleNamespace(
        users=_Collection([{"_id": USER_ID, "email": "held@example.com", "roles": ["orguser"]}]),
        roles=_BrokenRoles(),
    )
    token = security_module.create_access_token({"sub": "held@example.com"})
    request = SimpleNamespace(headers={"authorization": f"Bearer {token}"}, cookies={})

    with pytest.raises(HTTPException) as caught:
        asyncio.run(security_module.get_current_user(request, db))

    assert caught.value.status_code == 503


def test_the_permission_cache_key_is_single_sourced_and_not_the_pre_fix_key() -> None:
    """Entries written before R-A9B were computed with deleted roles; they must never be read."""
    assert permission_module.permission_cache_key("u1") != "user_perms:u1"
    offenders = [
        str(path.relative_to(BACKEND_ROOT))
        for path in BACKEND_ROOT.rglob("*.py")
        if "tests" not in path.parts
        and path.name != "permission_service.py"
        and "user_perms:" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], f"permission cache keys built outside permission_cache_key: {offenders}"
