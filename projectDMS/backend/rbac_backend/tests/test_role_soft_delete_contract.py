"""What `DELETE /api/roles/{id}` means today (R-A8Z observation F-A8Z-B3).

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

That part is disposition **A - intentional soft delete, direct historical lookup
allowed**, pinned by the first three tests.

The fourth test records a defect found while tracing it (F-A9A-1): nothing on the
permission path reads `is_active`. `PermissionService.user_has_permission` and
`get_user_permissions` resolve `users.roles` by `_id` with no activity filter, and
`delete_role` neither detaches the role from users nor invalidates the
`user_perms:{id}` cache. A user who still holds a deleted role keeps its
permissions. It is a strict xfail so it turns red the day the defect is fixed and
the marker must be removed; the fix is a production authorization change and is
out of scope for R-A9A.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.role_service import RoleService
from rbac_backend.utils.audit_logger import AuditLogger

ROLE_ID = ObjectId()
USER_ID = "user-holding-deleted-role"


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs
        self.updates: List[Dict[str, Any]] = []

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items()):
                return dict(doc)
        return None

    async def update_one(self, query, update):
        self.updates.append(update)
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items()):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


def _db() -> SimpleNamespace:
    role = {
        "_id": ROLE_ID,
        "name": "Site Reviewer",
        "scope": "organization",
        "organization_id": "org1",
        "permissions": ["dms.document.view"],
        "is_system": False,
        "is_active": True,
    }
    user = {"_id": USER_ID, "roles": [str(ROLE_ID)], "organization_id": "org1"}
    return SimpleNamespace(roles=_Collection([role]), users=_Collection([user]), permissions=_Collection([]))


@pytest.fixture
def db(monkeypatch) -> SimpleNamespace:
    database = _db()

    async def _get_db(self):
        return database

    async def _no_redis():
        return None

    async def _log_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(RoleService, "_get_db", _get_db)
    monkeypatch.setattr(PermissionService, "_get_db", _get_db)
    monkeypatch.setattr(permission_module, "get_runtime_state", lambda: SimpleNamespace(get_redis=_no_redis))
    monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)
    return database


def _delete(database: SimpleNamespace) -> bool:
    superadmin = SimpleNamespace(id="sa", roles=["superadmin"], organization_id=None, projects=[])
    return asyncio.run(RoleService().delete_role(str(ROLE_ID), superadmin))


def test_delete_is_a_soft_delete_that_keeps_the_document(db) -> None:
    assert _delete(db) is True
    stored = db.roles.docs[0]
    assert stored["is_active"] is False
    assert stored.get("deleted_at") is not None and stored.get("deleted_by") == "sa"
    assert len(db.roles.docs) == 1, "the role document was removed; the contract is a soft delete"


def test_a_deleted_role_is_still_readable_by_id(db) -> None:
    """Disposition A: a direct lookup is a historical read, still behind roles:read + can_view_role."""
    _delete(db)
    role = asyncio.run(RoleService().get_role_by_id(str(ROLE_ID)))
    assert role is not None and role.is_active is False


def test_the_listing_query_excludes_deleted_roles() -> None:
    """`get_roles_paginated` is the only listing path; its query carries the activity filter."""
    import inspect

    source = inspect.getsource(RoleService.get_roles_paginated)
    assert 'query["is_active"] = {"$ne": False}' in source, (
        "the role listing no longer hides soft-deleted roles"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F-A9A-1: permission resolution ignores is_active, so a user holding a "
        "soft-deleted role keeps its permissions. Production authz defect, not fixed in R-A9A."
    ),
)
def test_a_user_holding_only_a_deleted_role_has_none_of_its_permissions(db) -> None:
    assert asyncio.run(PermissionService().user_has_permission(USER_ID, "dms.document.view", log=False))
    _delete(db)
    assert not asyncio.run(PermissionService().user_has_permission(USER_ID, "dms.document.view", log=False))
