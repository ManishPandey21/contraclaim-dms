"""Gate 4 bullet 8 row 12: a project re-parent refusal counts only behind a passing control.

R-A8Z Stage B left row 12 INCONCLUSIVE (F-A8Z-B2). The probe account got 403 on
its OWN project, read and write, so the 403 on the re-parent could have come from
the permission gate rather than the foreign-organisation check the row exists to
measure. Re-seeding the probe role with `projects:read` / `projects:update`
changed nothing.

The mechanism, traced from source rather than guessed:

* `users.roles` holds role **ids**. `PermissionService.user_has_permission` and
  `get_user_permissions` resolve each entry with `db.roles.find_one({"_id": ...})`.
  The seed wrote the probe role's NAME into `users.roles` while the role was
  inserted with a generated ObjectId, so no role resolved and the account held no
  permission at all - which is why adding permissions to the role did nothing.
  The default roles work because their `_id` IS the role key (`"orgadmin"`).
* The routes check more than one name. `GET /api/projects/{id}` needs
  `projects:read` at the dependency (satisfied by `dms.dashboard.view` through
  `LEGACY_PERMISSION_ALIASES`) and `PolicyService.authorize("dms.dashboard.view")`
  in project scope. `PUT` needs `projects:update` (satisfied by
  `dms.project.manage`) and `authorize("dms.project.manage")`, then
  `check_resource_access` membership. Scope passes when the project is in the
  caller's projects, or the caller is `orgadmin`/`orguser` in its organisation.
* The default `orgadmin` role receives the full `CLIENT_DMS_PERMISSIONS` merge,
  which includes both `dms.dashboard.view` and `dms.project.manage`. So the spec's
  own org-admin account is a valid control; no extra probe account is needed.

These tests drive the real dependency chain - `require_permission`,
`_ensure_project_access` (PolicyService + ScopeService + resource membership) and
the route handlers - over an in-memory database. Only the subscription
entitlement and the audit writers are stubbed: row 12 is about permission and
scope, and the R-A8Z seed held an active subscription for both organisations.
Nothing here earns the bullet; it proves the control the staging run must show.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.core import security as security_module
from rbac_backend.core.permissions import equivalent_permissions
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.models.project import Project
from rbac_backend.routers import projects as projects_router
from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.audit_event_service import AuditEventService
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.utils.audit_logger import AuditLogger

OWN_ORG = "org1"
FOREIGN_ORG = "org2"
OWN_PROJECT = "proj1"
FOREIGN_PROJECT = "proj2"
ADMIN_ID = "user-orgadmin"
REPARENT_REFUSAL = "Moving a project to another organization requires a superadmin"


def _default_role(key: str) -> Dict[str, Any]:
    for role in DEFAULT_ROLES:
        if role.get("_id") == key:
            return dict(role)
    raise AssertionError(f"default role {key!r} is gone")


# --------------------------------------------------------------------------- #
# In-memory database
# --------------------------------------------------------------------------- #


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if str(actual) not in {str(item) for item in expected["$in"]}:
                return False
        elif isinstance(expected, dict):
            # Operators this chain does not use against these collections match nothing.
            return False
        elif str(actual) != str(expected):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self._docs]


class _Collection:
    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self.docs = [dict(doc) for doc in (docs or [])]
        self.writes: List[Dict[str, Any]] = []

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query=None, *_args, **_kwargs):
        # Membership collections are empty here; the permission catalogue lookup
        # is a fallback this chain never needs when the role resolves.
        return _Cursor([doc for doc in self.docs if _matches(doc, query or {})])

    async def find_one_and_update(self, query, update, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                self.writes.append(update)
                doc.update(update.get("$set", {}))
                return dict(doc)
        return None


class _DB:
    def __init__(self, *, user: Dict[str, Any], roles: List[Dict[str, Any]]):
        self.users = _Collection([user])
        self.roles = _Collection(roles)
        self.projects = _Collection(
            [
                {"_id": OWN_PROJECT, "organization_id": OWN_ORG, "name": "Own project"},
                {"_id": FOREIGN_PROJECT, "organization_id": FOREIGN_ORG, "name": "Foreign project"},
            ]
        )
        self.organizations = _Collection([{"_id": OWN_ORG}, {"_id": FOREIGN_ORG}])
        self.organization_memberships = _Collection()
        self.project_memberships = _Collection()
        self.permissions = _Collection()


@pytest.fixture
def decisions(monkeypatch) -> List[Dict[str, Any]]:
    """Wire the real services to the in-memory database; record policy decisions."""
    recorded: List[Dict[str, Any]] = []

    async def _no_redis():
        return None

    async def _log_noop(*_args, **_kwargs):
        return None

    async def _emit(self, **kwargs):
        if kwargs.get("action") == "policy.authorize":
            recorded.append(kwargs)

    async def _entitled(self, **_kwargs):
        return True, "entitlement_stubbed"

    monkeypatch.setattr(permission_module, "get_runtime_state", lambda: SimpleNamespace(get_redis=_no_redis))
    monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)
    monkeypatch.setattr(AuditEventService, "emit", _emit)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)
    return recorded


def _wire(monkeypatch, db: _DB) -> None:
    async def _get_db(self):
        return db

    monkeypatch.setattr(PermissionService, "_get_db", _get_db)


def _actor(roles: List[str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=ADMIN_ID,
        roles=roles,
        organization_id=OWN_ORG,
        organizations=[OWN_ORG],
        projects=[OWN_PROJECT],
        account_type="client_user",
        disabled=False,
    )


def _user_doc(role_refs: List[str]) -> Dict[str, Any]:
    return {"_id": ADMIN_ID, "roles": role_refs, "organization_id": OWN_ORG, "projects": [OWN_PROJECT]}


# The order FastAPI runs them: the route's permission dependency, then the handler.
async def _get(db: _DB, actor, project_id: str):
    await security_module.require_permission("projects:read")(current_user=actor)
    return await projects_router.read_project(project_id, db=db, current_user=actor, _=None)


async def _put(db: _DB, actor, project_id: str, organization_id: str):
    await security_module.require_permission("projects:update")(current_user=actor)
    body = Project(name="Own project", organization_id=organization_id)
    return await projects_router.update_project(project_id, body, db=db, current_user=actor, _=None)


def _refusal(coro) -> HTTPException:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(coro)
    return caught.value


def _org_admin_db() -> _DB:
    return _DB(user=_user_doc(["orgadmin"]), roles=[_default_role("orgadmin")])


# --------------------------------------------------------------------------- #
# The required permission, read from the source of truth
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "checked",
    ["projects:read", "projects:update", "dms.dashboard.view", "dms.project.manage"],
)
def test_the_default_org_admin_role_holds_every_name_the_project_routes_check(checked: str) -> None:
    granted = set(_default_role("orgadmin")["permissions"])
    assert granted & equivalent_permissions(checked), (
        f"the default orgadmin role holds nothing that satisfies {checked}; the Gate 4 row 12 "
        "control would be refused at the gate and the re-parent refusal would measure nothing"
    )


# --------------------------------------------------------------------------- #
# OWN-ORG CONTROL must pass; FOREIGN-ORG TARGET must refuse
# --------------------------------------------------------------------------- #


def test_the_own_organisation_control_passes(monkeypatch, decisions) -> None:
    db = _org_admin_db()
    _wire(monkeypatch, db)
    actor = _actor(["orgadmin"])

    read = asyncio.run(_get(db, actor, OWN_PROJECT))
    assert read.organization_id == OWN_ORG

    saved = asyncio.run(_put(db, actor, OWN_PROJECT, OWN_ORG))
    assert saved.organization_id == OWN_ORG
    assert len(db.projects.writes) == 1, "the unchanged own-project update was not written"
    allowed = [(d["metadata"]["permission"], d["result"], d["reason"]) for d in decisions]
    assert ("dms.dashboard.view", "allow", "client_scope") in allowed
    assert ("dms.project.manage", "allow", "client_scope") in allowed


def test_the_foreign_organisation_re_parent_is_refused_by_its_own_check(monkeypatch, decisions) -> None:
    db = _org_admin_db()
    _wire(monkeypatch, db)
    actor = _actor(["orgadmin"])

    refused = _refusal(_put(db, actor, OWN_PROJECT, FOREIGN_ORG))
    assert refused.status_code == 403
    # The refusal is the re-parent check, not the gate: the policy allowed the
    # same caller on the same project a moment earlier.
    assert refused.detail == REPARENT_REFUSAL
    assert ("dms.project.manage", "allow") in [(d["metadata"]["permission"], d["result"]) for d in decisions]
    assert db.projects.writes == []
    assert asyncio.run(db.projects.find_one({"_id": OWN_PROJECT}))["organization_id"] == OWN_ORG


def test_a_foreign_project_addressed_directly_is_refused_on_scope(monkeypatch, decisions) -> None:
    db = _org_admin_db()
    _wire(monkeypatch, db)
    actor = _actor(["orgadmin"])

    for call in (_get(db, actor, FOREIGN_PROJECT), _put(db, actor, FOREIGN_PROJECT, FOREIGN_ORG)):
        assert _refusal(call).status_code == 403
    denied = [(d["organization_id"], d["result"], d["reason"]) for d in decisions]
    assert denied and all(entry == (FOREIGN_ORG, "deny", "scope_denied") for entry in denied)
    assert db.projects.writes == []


# --------------------------------------------------------------------------- #
# Mutation controls: remove the precondition and the control goes invalid
# --------------------------------------------------------------------------- #


def _held_names_satisfying(monkeypatch, permission: str, held: List[str]) -> List[str]:
    """Every held permission that ON ITS OWN satisfies `permission` in the live resolver.

    Measured, not derived from the alias table: the resolver expands a shared
    legacy alias in both directions (F-A9A-2), so the set is far wider than
    `equivalent_permissions(permission)`.
    """
    satisfying = []
    for name in held:
        probe = _DB(user=_user_doc(["probe"]), roles=[{"_id": "probe", "permissions": [name]}])
        _wire(monkeypatch, probe)
        if asyncio.run(PermissionService().user_has_permission(ADMIN_ID, permission, log=False)):
            satisfying.append(name)
    return satisfying


def test_without_the_project_permission_the_control_is_refused_at_the_gate(monkeypatch, decisions) -> None:
    role = _default_role("orgadmin")
    stripped = set(_held_names_satisfying(monkeypatch, "projects:update", role["permissions"]))
    assert "dms.project.manage" in stripped
    role["permissions"] = [p for p in role["permissions"] if p not in stripped]
    db = _DB(user=_user_doc(["orgadmin"]), roles=[role])
    _wire(monkeypatch, db)

    refused = _refusal(_put(db, _actor(["orgadmin"]), OWN_PROJECT, OWN_ORG))
    assert refused.status_code == 403
    assert refused.detail == "Missing required permission: projects:update"
    # A re-parent 403 from this account would be indistinguishable from this one.
    assert _refusal(_put(db, _actor(["orgadmin"]), OWN_PROJECT, FOREIGN_ORG)).detail != REPARENT_REFUSAL


@pytest.mark.parametrize(
    "permissions",
    [["dms.project.manage"], ["dms.project.manage", "projects:read", "projects:update"]],
    ids=["R-A8Z-seed", "R-A8Z-23c-reprobe"],
)
def test_a_role_referenced_by_name_grants_nothing_which_was_F_A8Z_B2(
    monkeypatch, decisions, permissions: List[str]
) -> None:
    """The R-A8Z probe shape: `users.roles` held the name, the role had an ObjectId."""
    role_name = "g3-ra8z-projmgr"
    role = {
        "_id": ObjectId(),
        "name": role_name,
        "scope": "organization",
        "organization_id": OWN_ORG,
        "permissions": permissions,
        "is_active": True,
    }
    db = _DB(user=_user_doc([role_name]), roles=[role])
    _wire(monkeypatch, db)
    actor = _actor([role_name])

    assert _refusal(_get(db, actor, OWN_PROJECT)).detail == "Missing required permission: projects:read"
    assert _refusal(_put(db, actor, OWN_PROJECT, OWN_ORG)).detail == "Missing required permission: projects:update"

    # The same role referenced by its id resolves, so the permissions were never the defect.
    db.users.docs[0]["roles"] = [str(role["_id"])]
    assert asyncio.run(PermissionService().user_has_permission(ADMIN_ID, "projects:update", log=False))
