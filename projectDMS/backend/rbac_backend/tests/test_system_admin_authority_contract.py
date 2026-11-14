"""F-A9B-2: global legal-words administration needs system authority.

`/api/admin/legal-words` administers the platform-wide word list every tenant reads
(`LegalWordService`: no organisation field; `GET /api/legal-words/today` is "the
platform-wide contractual/legal words"). Its only gate is
`require_permission("system:admin")`, and the catalogue defines `system:admin` as
"Full system administration", category `system_administration`, `is_system`, and
non-delegable (`role_service.NON_DELEGABLE_PERMISSIONS`: only a Super Admin can put
it in a role).

Before R-A9D, eight canonical permissions declared `system:admin` as their legacy
name in `LEGACY_PERMISSION_ALIASES`, so one declared hop let every holder of
`dms.admin` (default `orgadmin`, `contractmgr_org`, `projectadmin`),
`billing.plan.manage` or `subscription.*` pass that gate - an organisation admin
could rewrite content shown to every other tenant. Nothing in the legal-words
change (7d5ef56) or the alias table's purpose ("so legacy role strings still
satisfy new router checks") intends that: the alias was a label mapping, not a
grant of system authority.

The contract pinned here: `system:admin` is nobody's legacy alias. It is satisfied
by holding it (a Super Admin grant) or a wildcard, and by the Super Admin principal.
Organisation, billing and subscription administration do not reach it, and it does
not reach them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rbac_backend.core import security as security_module
from rbac_backend.core.permissions import LEGACY_PERMISSION_ALIASES, equivalent_permissions
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.routers import legal_words
from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.utils.audit_logger import AuditLogger

SYSTEM_ADMIN = "system:admin"
#: The eight canonicals that declared `system:admin` before R-A9D.
FORMER_DECLARERS = (
    "dms.admin",
    "billing.plan.manage",
    "subscription.entitlement.manage",
    "subscription.upgrade",
    "subscription.downgrade",
    "subscription.cancel",
    "subscription.trial.manage",
    "subscription.addon.manage",
)


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items() if not isinstance(value, dict)):
                return dict(doc)
        return None

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


class _Words:
    def __init__(self):
        self.listed = 0

    async def list_words(self, **_kwargs):
        self.listed += 1
        return [], 0


@pytest.fixture
def world(monkeypatch):
    state: Dict[str, Any] = {}

    async def _get_db(_self):
        return state["db"]

    async def _no_redis():
        return None

    async def _log_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(PermissionService, "_get_db", _get_db)
    monkeypatch.setattr(
        permission_module, "get_runtime_state", lambda: SimpleNamespace(redis_url=None, get_redis=_no_redis)
    )
    monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)
    monkeypatch.setattr(type(security_module.get_audit_logger()), "log_permission_check", _log_noop, raising=False)

    words = _Words()
    app = FastAPI()
    app.include_router(legal_words.admin_router, prefix="/api")
    app.dependency_overrides[legal_words.get_legal_word_service] = lambda: words

    def _as(role: Dict[str, Any], principal_roles: List[str]):
        state["db"] = SimpleNamespace(
            users=_Collection([{"_id": "u1", "roles": [role["_id"]]}]),
            roles=_Collection([role]),
            permissions=_Collection([]),
        )
        principal = SimpleNamespace(id="u1", roles=principal_roles, disabled=False, organization_id="org1")
        app.dependency_overrides[security_module.get_current_user] = lambda: principal
        return TestClient(app)

    def _holding(*permissions: str):
        return _as({"_id": "r1", "name": "Probe", "permissions": list(permissions)}, [])

    def _satisfies(permission: str, *held: str) -> bool:
        _holding(*held)
        import asyncio

        return asyncio.run(PermissionService().user_has_permission("u1", permission, log=False))

    def _effective(*held: str) -> set:
        _holding(*held)
        import asyncio

        return set(asyncio.run(PermissionService().get_effective_permission_names("u1")))

    return SimpleNamespace(as_role=_as, holding=_holding, satisfies=_satisfies, effective=_effective, words=words)


# --------------------------------------------------------------------------- #
# The canonical layer
# --------------------------------------------------------------------------- #


def test_system_admin_is_nobodys_legacy_alias() -> None:
    declaring = sorted(canonical for canonical, aliases in LEGACY_PERMISSION_ALIASES.items() if SYSTEM_ADMIN in aliases)
    assert declaring == [], f"these canonicals reach system administration by alias: {declaring}"
    assert equivalent_permissions(SYSTEM_ADMIN) == {SYSTEM_ADMIN}


@pytest.mark.parametrize("held", FORMER_DECLARERS)
def test_organisation_billing_and_subscription_administration_do_not_satisfy_system_admin(world, held) -> None:
    assert not world.satisfies(SYSTEM_ADMIN, held), f"{held} reached system administration"
    assert SYSTEM_ADMIN not in world.effective(held), f"{held} is advertised system administration"


@pytest.mark.parametrize("checked", FORMER_DECLARERS)
def test_system_admin_does_not_satisfy_organisation_billing_or_subscription_checks(world, checked) -> None:
    """Symmetric: the alias relation is one relation, and it is gone in both directions."""
    assert not world.satisfies(checked, SYSTEM_ADMIN)


def test_an_explicit_system_admin_grant_is_system_authority(world) -> None:
    """Positive control on the same resolver: only a Super Admin can grant this name."""
    assert world.satisfies(SYSTEM_ADMIN, SYSTEM_ADMIN)
    assert world.satisfies(SYSTEM_ADMIN, "*")


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #


def _default_role(key: str) -> Dict[str, Any]:
    return next(dict(role) for role in DEFAULT_ROLES if role["_id"] == key)


@pytest.mark.parametrize("key", sorted(role["_id"] for role in DEFAULT_ROLES if role["_id"] != "superadmin"))
def test_no_default_role_below_super_admin_administers_legal_words(world, key) -> None:
    client = world.as_role(_default_role(key), [key])
    response = client.get("/api/admin/legal-words")
    assert response.status_code == 403, f"{key}: {response.status_code} {response.text}"
    assert world.words.listed == 0


@pytest.mark.parametrize("held", FORMER_DECLARERS)
def test_a_role_holding_a_former_declarer_is_refused(world, held) -> None:
    response = world.holding(held).get("/api/admin/legal-words")
    assert response.status_code == 403, f"{held}: {response.status_code}"


def test_the_super_admin_administers_legal_words(world) -> None:
    client = world.as_role(_default_role("superadmin"), ["superadmin"])
    response = client.get("/api/admin/legal-words")
    assert response.status_code == 200, response.text
    assert world.words.listed == 1


def test_an_explicit_system_admin_holder_administers_legal_words(world) -> None:
    """Positive control through the same route and resolver as the refusals."""
    response = world.holding(SYSTEM_ADMIN).get("/api/admin/legal-words")
    assert response.status_code == 200, response.text


def _gate_permissions(dependant) -> List[str]:
    found: List[str] = []
    for dependency in dependant.dependencies:
        call = dependency.call
        if getattr(call, "__name__", "") == "permission_checker" and call.__closure__:
            names = dict(zip(call.__code__.co_freevars, (cell.cell_contents for cell in call.__closure__)))
            found.append(names.get("normalized"))
        found.extend(_gate_permissions(dependency))
    return found


def test_every_legal_words_admin_route_is_gated_on_system_admin() -> None:
    routes = [route for route in legal_words.admin_router.routes if hasattr(route, "dependant")]
    assert routes, "the admin router has no routes"
    ungated = [
        f"{sorted(route.methods)} {route.path}"
        for route in routes
        if SYSTEM_ADMIN not in _gate_permissions(route.dependant)
    ]
    assert ungated == []
