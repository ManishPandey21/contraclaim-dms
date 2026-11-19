"""The advertised permission list says exactly what the checks grant (2026-11-19 reconciliation).

``PermissionService.get_effective_permission_names`` feeds ``permissions`` on login and
``/api/users/me``, which the client uses to show and hide actions. Before:

- the user document's own ``permissions`` field was advertised, though no check reads it,
  and the routes fell back to that field whenever the effective list was empty;
- ``users:create`` (and its aliases) was advertised from any role naming it, though the
  check grants it only to the built-in administrator roles;
- the self-check (``POST /permissions/check``) answered the explicit-grant permissions
  from a wildcard role, which the gates refuse.

Now a name is advertised exactly when both kinds of gate grant it: ``require_permission``
(the resolver) and ``PolicyService.has_permission`` (the explicit rule for the
explicit-grant permissions). The policy's ``dms.admin`` widening is therefore not listed.
``["*"]`` is kept for Super Admin only; it does not cover the explicit-grant permissions,
which the client asks the server about. A wildcard role is listed from the catalogue.

Real MongoDB through the production services and ``app``. Opt-in:
``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, List, Set

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import (
    MANAGE,
    ROLE_A,
    ROLE_B,
    ROLE_P,
    ROLE_PLATFORM,
    ROLE_PRIVILEGED,
    STRATEGY,
    VIEW,
    _role,
    _seed_roles,
    _user,
)
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1

pytestmark = pytest.mark.integration

ROLE_CREATE_USERS = "eff-role-users-create"  # custom organisation role naming users:create
ROLE_WILDCARD = "eff-role-wildcard"  # custom organisation role holding "*"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _universe(extra: Iterable[str] = ()) -> Set[str]:
    from rbac_backend.core.permissions import (
        CANONICAL_PERMISSIONS,
        SUPERADMIN_EXCLUDED_PERMISSIONS,
        equivalent_permissions,
    )

    names = set(CANONICAL_PERMISSIONS) | set(SUPERADMIN_EXCLUDED_PERMISSIONS) | {"users:read", "users:create"} | set(extra)
    for name in list(names):
        names |= set(equivalent_permissions(name) or set())
    return names


async def _enforced(user_id: str, permission: str, **scope: Any) -> bool:
    """Both kinds of gate grant it: ``require_permission`` (Super Admin, else the resolver)
    and ``PolicyService.has_permission`` (explicit rule, administrator widening)."""
    from types import SimpleNamespace

    from rbac_backend.core.database import get_database
    from rbac_backend.services.permission_service import PermissionService
    from rbac_backend.services.policy_service import PolicyService

    db = await get_database()
    try:
        key: Any = ObjectId(user_id)
    except Exception:
        key = user_id
    doc = await db.users.find_one({"_id": key}) or {}
    roles = list(doc.get("roles") or [])
    principal = SimpleNamespace(id=user_id, roles=roles, organization_id=doc.get("organization_id"),
                                organizations=list(doc.get("organizations") or []), projects=list(doc.get("projects") or []))
    gate = "superadmin" in {str(r).lower() for r in roles} or await PermissionService().user_has_permission(
        user_id, permission, log=False, **scope
    )
    return gate and await PolicyService().has_permission(principal, permission, **scope)


async def _advertised(user_id: str, **scope: Any) -> List[str]:
    from rbac_backend.services.permission_service import PermissionService

    return await PermissionService().get_effective_permission_names(user_id, **scope)


async def _assert_agree(user_id: str, *, extra: Iterable[str] = (), **scope: Any) -> List[str]:
    """Advertised == granted, over the catalogue and everything the user's roles name."""
    from rbac_backend.core.permissions import SUPERADMIN_EXCLUDED_PERMISSIONS

    advertised = await _advertised(user_id, **scope)
    universe = _universe(extra)
    if advertised == ["*"]:
        for name in universe - set(SUPERADMIN_EXCLUDED_PERMISSIONS):
            assert await _enforced(user_id, name, **scope), f"* advertised but {name} refused"
        return advertised
    for name in advertised:
        assert await _enforced(user_id, name, **scope), f"advertised but refused: {name}"
    for name in universe:
        if await _enforced(user_id, name, **scope):
            assert name in advertised, f"granted but not advertised: {name}"
    return advertised


async def _seed(db: Any) -> None:
    await _seed_roles(db)
    await db.roles.insert_many(
        [
            _role(ROLE_CREATE_USERS, ["users:create", VIEW], organization_id=ORG_A),
            _role(ROLE_WILDCARD, ["*"], organization_id=ORG_A),
        ]
    )


def test_the_user_documents_own_permissions_field_is_never_advertised(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env.db)
            bare = await _user(env.db, "eff_bare", [], organization_id=ORG_A, permissions=[MANAGE, "roles:delete"])
            mixed = await _user(env.db, "eff_mixed", [ROLE_A], organization_id=ORG_A, permissions=[MANAGE])
            assert await _assert_agree(bare, extra=[MANAGE, "roles:delete"]) == []
            advertised = await _assert_agree(mixed, extra=[MANAGE])
            assert VIEW in advertised and MANAGE not in advertised
            assert not await _enforced(mixed, MANAGE), "the field is not a grant"

    _run(scenario())


def test_users_me_shows_no_permission_from_the_stored_field(monkeypatch) -> None:
    """The route used to fall back to the stored field when the effective list was empty."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings
            import time

            import jwt  # type: ignore[import-untyped]

            user_id = await _user(env.db, "eff_route", [], organization_id=ORG_A, permissions=[MANAGE])
            now = int(time.time())
            token = jwt.encode(
                {"sub": "eff_route@example.com", "type": "access", "iat": now, "exp": now + 3600},
                settings.SECRET_KEY, algorithm=settings.ALGORITHM,
            )
            response = await env.client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200, response.text
            assert response.json()["permissions"] == [], response.json()["permissions"]
            assert user_id

    _run(scenario())


def test_users_create_is_advertised_only_where_the_check_grants_it(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env.db)
            custom = await _user(env.db, "eff_creator", [ROLE_CREATE_USERS], organization_id=ORG_A)
            advertised = await _assert_agree(custom)
            assert "users:create" not in advertised, "a custom role cannot confer users:create"
            assert VIEW in advertised
            admin = await _user(env.db, "eff_admin", ["orgadmin"], organization_id=ORG_A)
            assert "users:create" in await _assert_agree(admin)
            member = await _user(env.db, "eff_member", ["orguser"], organization_id=ORG_A)
            member_list = await _assert_agree(member)
            assert "users:read" in member_list and "users:create" not in member_list

    _run(scenario())


def test_explicit_grant_permissions_follow_the_explicit_rule(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env.db)
            privileged = await _user(env.db, "eff_privileged", [ROLE_PRIVILEGED], organization_id=ORG_A)
            assert STRATEGY in await _assert_agree(privileged)
            wildcard = await _user(env.db, "eff_wildcard", [ROLE_WILDCARD], organization_id=ORG_A)
            # Listed from the catalogue, not as "*": a custom wildcard confers neither users:create
            # (built-in administrators only) nor the explicit-grant permissions.
            wildcard_list = await _assert_agree(wildcard)
            assert wildcard_list != ["*"] and VIEW in wildcard_list and MANAGE in wildcard_list
            assert "users:create" not in wildcard_list and STRATEGY not in wildcard_list
            assert not await _enforced(wildcard, STRATEGY), "a wildcard is not an explicit grant"
            # The client's self-check for the explicit permission follows the same rule.
            for persona, user_id, expected in [("eff_wildcard", wildcard, False), ("eff_privileged", privileged, True)]:
                await env.db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"email": f"{persona}@example.com"}})
            from rbac_backend.core.config import settings
            import time

            import jwt  # type: ignore[import-untyped]

            for persona, expected in [("eff_wildcard", False), ("eff_privileged", True)]:
                now = int(time.time())
                token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                                   settings.SECRET_KEY, algorithm=settings.ALGORITHM)
                response = await env.client.post(
                    "/api/permissions/check", headers={"Authorization": f"Bearer {token}"}, json={"permission": STRATEGY}
                )
                assert response.status_code == 200 and response.json()["granted"] is expected, (persona, response.text)

    _run(scenario())


def test_custom_aliases_and_scopes_agree(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env.db)
            member_a = await _user(env.db, "eff_a", [ROLE_A], organization_id=ORG_A)
            foreign = await _user(env.db, "eff_foreign", [ROLE_A], organization_id=ORG_B)
            dual = await _user(env.db, "eff_dual", [ROLE_A, ROLE_B], organization_id=ORG_A, organizations=[ORG_A, ORG_B])
            project = await _user(env.db, "eff_project", [ROLE_P], organization_id=ORG_A, projects=[PROJ_A1])
            platform_held = await _user(env.db, "eff_platform", [ROLE_PLATFORM], organization_id=ORG_A)
            for user_id in (member_a, foreign, dual, project, platform_held):
                for scope in ({}, {"organization_id": ORG_A}, {"organization_id": ORG_B}, {"project_id": PROJ_A1}):
                    await _assert_agree(user_id, **scope)
            assert await _advertised(foreign) == [], "A's role held in B advertises nothing"
            assert await _advertised(platform_held) == [], "a platform role held by an organisation's user"
            assert MANAGE in await _advertised(dual, organization_id=ORG_B)
            assert MANAGE not in await _advertised(dual, organization_id=ORG_A)
            assert MANAGE not in await _advertised(project), "a project role needs its project named"
            assert MANAGE in await _advertised(project, project_id=PROJ_A1)
            # A catalogue id entry advertises the name it stands for, never the id.
            permission_oid = ObjectId()
            await env.db.permissions.insert_one({"_id": permission_oid, "name": MANAGE, "resource": "claim", "action": "manage"})
            await env.db.roles.update_one({"_id": ROLE_A}, {"$push": {"permissions": str(permission_oid)}})
            advertised = await _assert_agree(member_a)
            assert MANAGE in advertised and str(permission_oid) not in advertised

    _run(scenario())


def test_built_in_personas_agree(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch):
            for persona in ("org_admin", "org_user", "project_admin", "project_user", "superadmin", "foreign_org_admin"):
                advertised = await _assert_agree(f"user-{persona}")
                if persona == "superadmin":
                    assert advertised == ["*"]

    _run(scenario())
