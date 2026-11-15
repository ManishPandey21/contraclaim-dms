"""Every role written to a user resolves to exactly one active role document.

System-role review, 2026-09-15. Super Admin authority attaches to the role NAME
(ADR 0001): `core/security.py` normalises `users.roles` into the principal, so
`super-admin`, `super admin` and `superadministrator` all become `superadmin`.
The assignment paths did not share that reading:

* `_validate_user_input` / `_validate_user_update` only lowercased the strings;
* `_resolve_role_scope` looked a string up by `_id` or exact name and, finding no
  document, fell back to `scope = "organization"` for anything that was not
  literally `superadmin`/`superuser`.

So an org admin could `PUT /api/users/{id}` - itself included - with
`roles: ["super-admin"]`: no document, organisation scope, allowed, stored, and on
the next request the principal carried `superadmin`. The create path was already
refused by `validate_role_assignment` (it runs the principal's normaliser); the
update path never called it. Unknown strings (`xyzadmin`) were stored on both.

Owner decision Q12(b): normalise with the principal's normaliser, require a role
document for the result, store that document's `_id`, and derive system scope from
the document. A string that resolves to nothing, to more than one document, or to
a deactivated role is refused with 400 before anything is written.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.models.user import UserUpdate
from rbac_backend.routers.users import UserController, UserCreatePayload
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.services.role_service import RoleService
from rbac_backend.utils.error_handler import BaseDomainError

OWN = "org-own"
PASSWORD = "Str0ng!Passw0rd#2026"
REVIEWER_ID = ObjectId()
RETIRED_ID = ObjectId()
LOOKALIKE_ID = ObjectId()

SUPER_ADMIN_ALIASES = ["super-admin", "superadministrator", "super admin"]


# --------------------------------------------------------------------------- #
# In-memory roles collection
# --------------------------------------------------------------------------- #


def _role_docs() -> List[Dict[str, Any]]:
    return [
        # Production shape (2026-09-15): no is_system / is_active / scope fields.
        {"_id": "superadmin", "name": "Super Admin", "permissions": []},
        {"_id": "orgadmin", "name": "Organization Admin", "is_system": True, "scope": "organization",
         "is_active": True, "permissions": []},
        {"_id": "orguser", "name": "Organization - User", "is_system": True, "scope": "organization",
         "is_active": True, "permissions": []},
        {"_id": REVIEWER_ID, "name": "Site Reviewer", "scope": "organization", "organization_id": OWN,
         "is_system": False, "is_active": True, "permissions": []},
        {"_id": RETIRED_ID, "name": "Retired Reviewer", "scope": "organization", "organization_id": OWN,
         "is_system": False, "is_active": False, "permissions": []},
    ]


class _Roles:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def find_one(self, query: Dict[str, Any], *_args, **_kwargs):
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items()):
                return dict(doc)
        return None


@pytest.fixture
def roles(monkeypatch) -> _Roles:
    collection = _Roles(_role_docs())
    db = SimpleNamespace(roles=collection)

    async def _get_db(_self):
        return db

    monkeypatch.setattr(RoleService, "_get_db", _get_db)
    return collection


# --------------------------------------------------------------------------- #
# Controller with recording fakes
# --------------------------------------------------------------------------- #


class _UserService:
    def __init__(self, target=None):
        self.target = target
        self.created: List[Any] = []
        self.updated: List[Any] = []

    async def check_user_exists(self, *_args, **_kwargs):
        return None

    async def get_user_by_id(self, _user_id):
        return self.target

    async def create_user(self, data, _password):
        self.created.append(data)
        return SimpleNamespace(id=str(ObjectId()), email=data.email, roles=list(data.roles))

    async def update_user(self, _user_id, update):
        self.updated.append(update)
        return self.target

    def __getattr__(self, name):
        async def _noop(*_args, **_kwargs):
            return None

        return _noop


class _Auth(AuthorizationService):
    async def require_permission(self, current_user, permission):
        return None


class _Quiet:
    def __getattr__(self, name):
        async def _noop(*_args, **_kwargs):
            return True

        return _noop


def _controller(service: _UserService) -> UserController:
    controller = UserController.__new__(UserController)
    controller.user_service = service
    controller.auth_service = _Auth()
    controller.authentication_service = _Quiet()
    controller.rate_limiter = _Quiet()
    controller.audit_logger = _Quiet()
    controller.notification_service = _Quiet()
    controller.role_service = RoleService()

    async def _echo(user, **_kwargs):
        return user

    controller._build_user_response = _echo
    return controller


ORG_ADMIN = SimpleNamespace(id="org-admin-1", roles=["orgadmin"], organization_id=OWN, organizations=[OWN], projects=[])
SUPER_ADMIN = SimpleNamespace(id="super-admin-1", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def _target() -> SimpleNamespace:
    return SimpleNamespace(
        id=str(ObjectId()),
        email="member@example.com",
        username="member",
        first_name="Member",
        last_name="Person",
        roles=["orguser"],
        organization_id=OWN,
        organizations=[OWN],
        projects=[],
        disabled=False,
    )


def _create(service: _UserService, actor, roles: List[str], organization_id=OWN):
    payload = UserCreatePayload(
        username="newmember",
        email="new.member@example.com",
        first_name="New",
        last_name="Member",
        password=PASSWORD,
        roles=roles,
        organization_id=organization_id,
    )
    return asyncio.run(_controller(service).create_user(payload, actor))


def _update(service: _UserService, actor, roles: List[str]):
    return asyncio.run(_controller(service).update_user(service.target.id, UserUpdate(roles=roles), actor))


def _refused(call) -> int:
    with pytest.raises(BaseDomainError) as refused:
        call()
    return refused.value.http_status


# --------------------------------------------------------------------------- #
# An org admin cannot reach Super Admin through an alias
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("alias", SUPER_ADMIN_ALIASES)
def test_an_org_admin_cannot_create_a_super_admin_through_an_alias(roles, alias) -> None:
    service = _UserService()
    assert _refused(lambda: _create(service, ORG_ADMIN, [alias])) == 403
    assert service.created == [], "the user was written before the refusal"


@pytest.mark.parametrize("alias", SUPER_ADMIN_ALIASES)
def test_an_org_admin_cannot_update_a_user_to_super_admin_through_an_alias(roles, alias) -> None:
    service = _UserService(_target())
    assert _refused(lambda: _update(service, ORG_ADMIN, [alias])) == 403
    assert service.updated == [], "the escalated roles were written before the refusal"


# --------------------------------------------------------------------------- #
# A role that resolves to nothing, to two documents, or to a retired role
# --------------------------------------------------------------------------- #


def test_an_unknown_role_is_refused_on_create(roles) -> None:
    service = _UserService()
    assert _refused(lambda: _create(service, ORG_ADMIN, ["xyzadmin"])) == 400
    assert service.created == []


def test_an_unknown_role_is_refused_on_update(roles) -> None:
    service = _UserService(_target())
    assert _refused(lambda: _update(service, ORG_ADMIN, ["xyzadmin"])) == 400
    assert service.updated == []


def test_an_unknown_role_is_refused_even_for_a_super_admin(roles) -> None:
    service = _UserService(_target())
    assert _refused(lambda: _update(service, SUPER_ADMIN, ["xyzadmin"])) == 400
    assert service.updated == []


def test_a_deactivated_role_cannot_be_assigned(roles) -> None:
    service = _UserService(_target())
    assert _refused(lambda: _update(service, ORG_ADMIN, [str(RETIRED_ID)])) == 400
    assert service.updated == []


def test_a_string_matching_two_role_documents_is_refused(roles) -> None:
    """`organization-admin` normalises to the `orgadmin` document but is also another role's exact name."""
    roles.docs.append({"_id": LOOKALIKE_ID, "name": "organization-admin", "scope": "organization",
                       "organization_id": OWN, "is_system": False, "is_active": True, "permissions": []})
    service = _UserService(_target())
    assert _refused(lambda: _update(service, SUPER_ADMIN, ["organization-admin"])) == 400
    assert service.updated == []


# --------------------------------------------------------------------------- #
# Still works: aliases resolve and are stored under the canonical role id
# --------------------------------------------------------------------------- #


def test_a_super_admin_assigning_an_org_admin_alias_stores_the_canonical_id_on_create(roles) -> None:
    service = _UserService()
    _create(service, SUPER_ADMIN, ["organization-admin"])
    assert [created.roles for created in service.created] == [["orgadmin"]]


def test_a_super_admin_assigning_an_org_admin_alias_stores_the_canonical_id_on_update(roles) -> None:
    service = _UserService(_target())
    _update(service, SUPER_ADMIN, ["organization-admin"])
    assert [update.roles for update in service.updated] == [["orgadmin"]]


def test_a_super_admin_may_still_assign_super_admin_and_it_is_stored_canonically(roles) -> None:
    service = _UserService()
    _create(service, SUPER_ADMIN, ["super admin"], organization_id=None)
    assert [created.roles for created in service.created] == [["superadmin"]]


def test_an_org_admin_assigning_an_org_user_alias_stores_the_canonical_id(roles) -> None:
    created = _UserService()
    _create(created, ORG_ADMIN, ["organization-user"])
    assert [user.roles for user in created.created] == [["orguser"]]

    updated = _UserService(_target())
    _update(updated, ORG_ADMIN, ["organization-user"])
    assert [update.roles for update in updated.updated] == [["orguser"]]


def test_canonical_keys_and_custom_role_ids_still_assign_unchanged(roles) -> None:
    service = _UserService(_target())
    _update(service, ORG_ADMIN, ["orguser", str(REVIEWER_ID)])
    assert [update.roles for update in service.updated] == [["orguser", str(REVIEWER_ID)]]


def test_a_custom_role_assigned_by_its_exact_name_is_stored_by_id(roles) -> None:
    service = _UserService(_target())
    _update(service, ORG_ADMIN, ["Site Reviewer"])
    assert [update.roles for update in service.updated] == [[str(REVIEWER_ID)]]
