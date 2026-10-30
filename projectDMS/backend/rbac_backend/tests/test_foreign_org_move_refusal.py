"""An update may not move a user or a project into an organisation the caller does not hold.

R-A8X adversarial review, traced end to end:

* `PUT /api/users/{id}`: `check_user_access` authorised the user's CURRENT
  organisation, then `_validate_user_update` copied `organization_id` and
  `projects` through and `update_user` wrote them. An org admin could send its own
  id with a foreign `organization_id`; on the next request `get_current_user`
  built it with the foreign organisation, and scope followed. No step-up, and no
  session invalidation, because roles, disabled and password were unchanged.
* `PUT /api/projects/{id}`: `_ensure_project_access` authorised the project's
  current organisation, the body's `organization_id` was only checked to exist,
  and `$set` wrote it - with a 400 "not found" that answered whether any foreign
  organisation id exists.

The create paths already refuse a foreign organisation; these are the update-side
equivalents. Refusal, not clamping, because the caller asked for something it may
not have, and a silent success that means something else is worse.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.models.project import Project
from rbac_backend.routers import projects as projects_router
from rbac_backend.routers.users import UserController
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.utils.error_handler import UserError

OWN = "org-own"
FOREIGN = "org-foreign"
OWN_PROJECT = ObjectId()
FOREIGN_PROJECT = ObjectId()


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows

    async def to_list(self, length=None):
        return list(self.rows)


class _Projects:
    rows = [
        {"_id": OWN_PROJECT, "organization_id": OWN, "name": "Own project"},
        {"_id": FOREIGN_PROJECT, "organization_id": FOREIGN, "name": "Foreign project"},
    ]

    def find(self, query: Dict[str, Any], projection=None):
        wanted = {str(value) for value in query["_id"]["$in"]}
        return _Cursor([row for row in self.rows if str(row["_id"]) in wanted])


class _UserService:
    def __init__(self, target):
        self.db = SimpleNamespace(projects=_Projects())
        self.target = target
        self.writes: List[Any] = []

    async def get_user_by_id(self, user_id):
        return self.target

    async def update_user(self, user_id, update):
        self.writes.append(update)
        return self.target

    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return None

        return _noop


class _Auth(AuthorizationService):
    async def require_permission(self, current_user, permission):
        return None


class _Quiet:
    def __getattr__(self, name):
        async def _noop(*args, **kwargs):
            return True

        return _noop


def _actor(roles, org=OWN):
    return SimpleNamespace(id="actor-1", roles=roles, organization_id=org, organizations=[org], projects=[])


def _target():
    return SimpleNamespace(
        id=str(ObjectId()),
        email="member@example.com",
        username="member",
        roles=["orguser"],
        organization_id=OWN,
        organizations=[OWN],
        projects=[],
        disabled=False,
    )


def _controller(service):
    controller = UserController.__new__(UserController)
    controller.user_service = service
    controller.auth_service = _Auth()
    controller.authentication_service = _Quiet()
    controller.rate_limiter = _Quiet()
    controller.audit_logger = _Quiet()
    controller.notification_service = _Quiet()
    return controller


def _update(**fields):
    return SimpleNamespace(**{"organization_id": None, "organizations": None, "projects": None, **fields})


def _scope(controller, actor, target, update):
    return asyncio.run(controller._enforce_update_stays_in_scope(actor, target, update))


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def test_an_org_admin_cannot_move_a_user_into_a_foreign_organization() -> None:
    target = _target()
    service = _UserService(target)
    controller = _controller(service)

    with pytest.raises(UserError) as refused:
        _scope(controller, _actor(["orgadmin"]), target, _update(organization_id=FOREIGN))
    assert refused.value.http_status == 403


def test_the_refusal_comes_before_any_write_through_the_real_update_path() -> None:
    target = _target()
    service = _UserService(target)
    controller = _controller(service)
    from rbac_backend.models.user_models import UserUpdate

    with pytest.raises(UserError) as refused:
        asyncio.run(
            controller.update_user(str(ObjectId()), UserUpdate(organization_id=FOREIGN), _actor(["orgadmin"]))
        )
    assert refused.value.http_status == 403
    assert service.writes == [], "the foreign organisation was written before the refusal"


def test_an_org_admin_cannot_grant_a_foreign_organization_or_project() -> None:
    target = _target()
    controller = _controller(_UserService(target))

    with pytest.raises(UserError):
        _scope(controller, _actor(["orgadmin"]), target, _update(organizations=[OWN, FOREIGN]))
    with pytest.raises(UserError):
        _scope(controller, _actor(["orgadmin"]), target, _update(projects=[str(FOREIGN_PROJECT)]))
    with pytest.raises(UserError):
        _scope(controller, _actor(["orgadmin"]), target, _update(projects=[str(ObjectId())]))


def test_updates_inside_the_organization_still_work() -> None:
    target = _target()
    controller = _controller(_UserService(target))
    admin = _actor(["orgadmin"])

    _scope(controller, admin, target, _update())
    _scope(controller, admin, target, _update(organization_id=OWN))
    _scope(controller, admin, target, _update(organizations=[OWN]))
    _scope(controller, admin, target, _update(projects=[str(OWN_PROJECT)]))


def test_a_superadmin_may_still_move_a_user() -> None:
    target = _target()
    controller = _controller(_UserService(target))
    _scope(controller, _actor(["superadmin"], org=None), target, _update(organization_id=FOREIGN))


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #


class _ProjectDB:
    def __init__(self):
        self.project = {"_id": OWN_PROJECT, "organization_id": OWN, "name": "Own project"}
        self.organizations = SimpleNamespace()
        self.writes: List[Any] = []
        self.projects = self

    async def find_one_and_update(self, query, update, return_document=None):
        self.writes.append(update)
        return {**self.project, **update["$set"]}


@pytest.fixture
def project_db(monkeypatch):
    db = _ProjectDB()
    lookups: List[str] = []

    async def _allowed(*args, **kwargs):
        return None

    async def _find_by_id(collection, identifier):
        if collection is db.projects:
            return dict(db.project)
        lookups.append(str(identifier))
        return {"_id": identifier}

    monkeypatch.setattr(projects_router, "_ensure_project_access", _allowed)
    monkeypatch.setattr(projects_router, "_find_by_id", _find_by_id)
    db.org_lookups = lookups
    return db


def _put_project(db, actor, organization_id):
    body = Project(name="Own project", organization_id=organization_id)
    return asyncio.run(projects_router.update_project(str(OWN_PROJECT), body, db=db, current_user=actor, _=None))


def test_an_org_admin_cannot_move_a_project_into_a_foreign_organization(project_db) -> None:
    with pytest.raises(HTTPException) as refused:
        _put_project(project_db, _actor(["orgadmin"]), FOREIGN)
    assert refused.value.status_code == 403
    assert project_db.writes == []
    assert project_db.org_lookups == [], (
        "the foreign organisation was looked up before the refusal, so its existence is observable"
    )


def test_a_project_update_inside_its_organization_still_works(project_db) -> None:
    saved = _put_project(project_db, _actor(["orgadmin"]), OWN)
    assert saved.organization_id == OWN
    assert len(project_db.writes) == 1


def test_a_superadmin_may_still_move_a_project(project_db) -> None:
    saved = _put_project(project_db, _actor(["superadmin"], org=None), FOREIGN)
    assert saved.organization_id == FOREIGN
    assert project_db.org_lookups == [FOREIGN]
