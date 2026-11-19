"""SEC-1 to SEC-4: custom-role permissions, role creation, role assignment, disabled accounts.

Real HTTP through the production ``app``, real RBAC seeded by the production
initialisers, a disposable MongoDB replica set; nothing is overridden.

- SEC-1: a permission held through a custom role counts only where that role was
  granted (its organisation, its project), never for a member of another
  organisation, never from a platform role held by an organisation's user.
  Built-in roles keep their meaning.
- SEC-2: a non-Super-Admin may only put on a role permissions they hold
  themselves in the role's organisation or project; catalogue ids are resolved
  before the non-delegable check; unknown permissions are refused.
- SEC-3: a role is refused when it does not fit the user it is given to: another
  organisation, no organisation, a project the user is not on, a disabled account,
  or permissions the assigner cannot delegate. Super Admin included.
- SEC-4: a disabled account stops authenticating on its next request.

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _token,
)

pytestmark = pytest.mark.integration

ROLE_A = "sec-role-org-a"  # custom organisation role of A
ROLE_B = "sec-role-org-b"  # custom organisation role of B
ROLE_P = "sec-role-proj-a1"  # custom project role of A1
ROLE_PLATFORM = "sec-role-platform"  # custom role naming no organisation
ROLE_PRIVILEGED = "sec-role-privileged-a"  # organisation role of A with an explicit-grant permission

VIEW = "dms.claim.view"
MANAGE = "dms.claim.manage"
STRATEGY = "dms.claim.strategy.read"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _role(role_id: str, permissions: List[str], *, organization_id: Optional[str], project_id: Optional[str] = None, active: bool = True) -> Dict[str, Any]:
    scope = "project" if project_id else "organization"
    return {
        "_id": role_id, "name": role_id, "description": "SEC test role", "permissions": permissions,
        "scope": scope, "organization_id": organization_id, "project_id": project_id,
        "is_system": False, "is_active": active,
    }


async def _user(db: Any, persona: str, roles: List[str], *, organization_id: Optional[str], projects: Optional[List[str]] = None, **extra: Any) -> str:
    oid = ObjectId()  # the users API takes ObjectId ids
    user_id = str(oid)
    await db.users.insert_one(
        {"_id": oid, "email": f"{persona}@example.com", "username": persona, "disabled": False,
         "account_type": "client_user", "roles": roles, "organization_id": organization_id,
         "organizations": [organization_id] if organization_id else [], "projects": projects or [], **extra}
    )
    return user_id


async def _seed_roles(db: Any) -> None:
    await db.roles.insert_many(
        [
            _role(ROLE_A, [VIEW], organization_id=ORG_A),
            _role(ROLE_B, [MANAGE], organization_id=ORG_B),
            _role(ROLE_P, [MANAGE], organization_id=ORG_A, project_id=PROJ_A1),
            _role(ROLE_PLATFORM, [VIEW], organization_id=None),
            _role(ROLE_PRIVILEGED, [STRATEGY, VIEW], organization_id=ORG_A),
        ]
    )


async def _has(user_id: str, permission: str, **scope: Any) -> bool:
    from rbac_backend.services.permission_service import PermissionService

    return await PermissionService().user_has_permission(user_id, permission, log=False, **scope)


def _step_up(persona: str, user_id: str) -> Dict[str, str]:
    from rbac_backend.services.step_up_service import StepUpService

    token = StepUpService().create_token(user_id=user_id, action="platform.role.manage")
    return {"Authorization": f"Bearer {_token(persona)}", "X-Step-Up-Token": token}


# --------------------------------------------------------------------------- #
# SEC-1: a custom role counts only where it was granted
# --------------------------------------------------------------------------- #


def test_a_custom_role_grants_nothing_across_organisations(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            member_a = await _user(env.db, "sec_member_a", [ROLE_A], organization_id=ORG_A)
            # Legacy or bad data: a member of B holding A's role (SEC-3 now refuses the assignment).
            member_b = await _user(env.db, "sec_member_b", [ROLE_A], organization_id=ORG_B)
            dual = await _user(env.db, "sec_dual", [ROLE_A, ROLE_B], organization_id=ORG_A, organizations=[ORG_A, ORG_B])

            assert await _has(member_a, VIEW)
            assert await _has(member_a, VIEW, organization_id=ORG_A)
            assert not await _has(member_a, VIEW, organization_id=ORG_B), "an A role counted in B"
            assert not await _has(member_b, VIEW), "a role of A granted to a member of B"
            assert not await _has(member_b, VIEW, organization_id=ORG_B)
            # A member of both organisations: each role counts in its own organisation only.
            assert await _has(dual, VIEW, organization_id=ORG_A) and not await _has(dual, VIEW, organization_id=ORG_B)
            assert await _has(dual, MANAGE, organization_id=ORG_B) and not await _has(dual, MANAGE, organization_id=ORG_A)

            # The same rule through PolicyService.authorize, which passes the request's scope.
            from types import SimpleNamespace

            from fastapi import HTTPException

            from rbac_backend.services.policy_service import PolicyService

            policy = PolicyService(env.db)
            dual_principal = SimpleNamespace(id=dual, roles=[ROLE_A, ROLE_B], organization_id=ORG_A,
                                             organizations=[ORG_A, ORG_B], projects=[], account_type="client_user")
            assert await policy.has_permission(dual_principal, MANAGE, organization_id=ORG_B)
            assert not await policy.has_permission(dual_principal, MANAGE, organization_id=ORG_A), "a B role counted in A"
            with pytest.raises(HTTPException) as refused:
                await policy.authorize(dual_principal, MANAGE, resource_type="claims", organization_id=ORG_A, audit=False)
            assert refused.value.status_code == 403

            from rbac_backend.services.permission_service import PermissionService

            advertised_b = set(await PermissionService().get_effective_permission_names(member_b))
            assert VIEW not in advertised_b, "the client is told about a grant the server refuses"

    _run(scenario())


def test_a_project_role_counts_only_in_its_project_and_only_for_its_members(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            on_a1 = await _user(env.db, "sec_on_a1", [ROLE_P], organization_id=ORG_A, projects=[PROJ_A1])
            on_a2 = await _user(env.db, "sec_on_a2", [ROLE_P], organization_id=ORG_A, projects=[PROJ_A2])

            assert await _has(on_a1, MANAGE, organization_id=ORG_A, project_id=PROJ_A1)
            assert not await _has(on_a1, MANAGE, organization_id=ORG_A, project_id=PROJ_A2), "an A1 role counted in A2"
            assert not await _has(on_a1, MANAGE, organization_id=ORG_B, project_id=PROJ_B1)
            assert not await _has(on_a2, MANAGE), "a project role granted to a user not on the project"

            # Membership read from project_memberships counts as well.
            await env.db.project_memberships.insert_one({"user_id": on_a2, "project_id": PROJ_A1, "status": "active"})
            assert await _has(on_a2, MANAGE, project_id=PROJ_A1)
            await env.db.project_memberships.update_one({"user_id": on_a2}, {"$set": {"status": "revoked"}})
            assert not await _has(on_a2, MANAGE, project_id=PROJ_A1), "a revoked membership kept the grant"

    _run(scenario())


def test_platform_revoked_and_mixed_roles(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            platform_holder = await _user(env.db, "sec_platform_holder", [ROLE_PLATFORM], organization_id=ORG_A)
            platform_user = await _user(env.db, "sec_platform_user", [ROLE_PLATFORM], organization_id=None)
            mixed = await _user(env.db, "sec_mixed", ["projectuser", ROLE_B], organization_id=ORG_A, projects=[PROJ_A1])

            assert not await _has(platform_holder, VIEW), "a role naming no organisation granted an organisation's user"
            assert await _has(platform_user, VIEW), "a platform role must still serve a platform user"

            # Built-in semantics unchanged; the foreign custom role adds nothing.
            from rbac_backend.services.permission_service import PermissionService

            builtin_only = await _user(env.db, "sec_builtin_only", ["projectuser"], organization_id=ORG_A, projects=[PROJ_A1])
            service = PermissionService()
            assert set(await service.get_effective_permission_names(mixed)) == set(
                await service.get_effective_permission_names(builtin_only)
            )
            assert not await _has(mixed, MANAGE, organization_id=ORG_B)

            # A deactivated role grants nothing, wherever it is held.
            member = await _user(env.db, "sec_revoked", [ROLE_A], organization_id=ORG_A)
            assert await _has(member, VIEW)
            await env.db.roles.update_one({"_id": ROLE_A}, {"$set": {"is_active": False}})
            assert not await _has(member, VIEW)

            # Explicit-grant permissions follow the same rule.
            holder_b = await _user(env.db, "sec_strategy_b", [ROLE_PRIVILEGED], organization_id=ORG_B)
            holder_a = await _user(env.db, "sec_strategy_a", [ROLE_PRIVILEGED], organization_id=ORG_A)
            assert await service.user_has_explicit_permission(holder_a, STRATEGY, organization_id=ORG_A)
            assert not await service.user_has_explicit_permission(holder_a, STRATEGY, organization_id=ORG_B)
            assert not await service.user_has_explicit_permission(holder_b, STRATEGY)

    _run(scenario())


def test_built_in_roles_keep_their_meaning(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            service = PermissionService()
            for persona in ("org_admin", "org_user", "project_admin", "project_user", "foreign_org_admin"):
                names = await service.get_effective_permission_names(f"user-{persona}")
                assert names, f"{persona} lost every permission"
                assert await _has(f"user-{persona}", "users:read")
            assert await _has("user-org_admin", "users:create")
            assert not await _has("user-org_user", "users:create")

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-2: role creation and editing
# --------------------------------------------------------------------------- #


def test_a_role_carries_only_what_its_creator_may_delegate_there(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            headers = _step_up("org_admin", "user-org_admin")
            created = await env.client.post("/api/roles", headers=headers, json={"name": "Site viewer", "permissions": [VIEW]})
            assert created.status_code == 200, created.text[:300]
            body = created.json()
            assert body["organization_id"] == ORG_A and body["permissions"] == [VIEW]

            # An explicit-grant permission the creator does not hold by name.
            refused = await env.client.post("/api/roles", headers=headers, json={"name": "Strategist", "permissions": [STRATEGY]})
            assert refused.status_code == 403, refused.text[:300]

            # A non-delegable permission given by catalogue id is judged as the name it stands for.
            dms_admin = await env.db.permissions.find_one({"name": "dms.admin"})
            if dms_admin is None:
                inserted = await env.db.permissions.insert_one({"name": "dms.admin", "is_active": True})
                dms_admin = {"_id": inserted.inserted_id}
            by_id = await env.client.post(
                "/api/roles", headers=headers, json={"name": "Sneaky", "permissions": [str(dms_admin["_id"])]}
            )
            assert by_id.status_code == 403, by_id.text[:300]

            unknown = await env.client.post("/api/roles", headers=headers, json={"name": "Odd", "permissions": ["made.up.permission"]})
            assert unknown.status_code == 422, unknown.text[:300]

            role_id = body["id"]
            added = await env.client.post(f"/api/roles/{role_id}/permissions/{STRATEGY}", headers=headers)
            assert added.status_code == 403, added.text[:300]
            edited = await env.client.put(f"/api/roles/{role_id}", headers=headers, json={"permissions": [VIEW, STRATEGY]})
            assert edited.status_code == 403, edited.text[:300]
            stored = await env.db.roles.find_one({"_id": ObjectId(role_id)})
            assert stored["permissions"] == [VIEW], "a refused edit was written"

            assert await env.db.roles.count_documents({"name": {"$in": ["Strategist", "Sneaky", "Odd"]}}) == 0

            # A project admin may not create a role for another project.
            project_headers = _step_up("project_admin", "user-project_admin")
            other = await env.client.post(
                "/api/roles", headers=project_headers, json={"name": "A2 role", "permissions": [VIEW], "project_id": PROJ_A2}
            )
            assert other.status_code == 403, other.text[:300]

            # A Super Admin's organisation role must name an existing organisation.
            super_headers = _step_up("superadmin", "user-superadmin")
            orgless = await env.client.post(
                "/api/roles", headers=super_headers, json={"name": "Orgless", "permissions": [VIEW], "scope": "organization"}
            )
            assert orgless.status_code in {403, 422}, orgless.text[:300]
            assert await env.db.roles.find_one({"name": "Orgless"}) is None

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-3: role assignment
# --------------------------------------------------------------------------- #


async def _assign(env: Any, persona: str, user_id: str, roles: List[str]) -> Any:
    return await env.client.put(
        f"/api/users/{user_id}", headers={"Authorization": f"Bearer {_token(persona)}"}, json={"roles": roles}
    )


def test_a_role_must_fit_the_user_it_is_given_to(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            target_b = await _user(env.db, "sec_target_b", ["orguser"], organization_id=ORG_B)
            target_a = await _user(env.db, "sec_target_a", ["orguser"], organization_id=ORG_A)

            foreign = await _assign(env, "superadmin", target_b, ["orguser", ROLE_A])
            assert foreign.status_code == 403, "Super Admin gave an organisation-A role to a user of B"
            orgless = await _assign(env, "superadmin", target_a, ["orguser", ROLE_PLATFORM])
            assert orgless.status_code == 403, orgless.text[:300]
            off_project = await _assign(env, "org_admin", target_a, ["orguser", ROLE_P])
            assert off_project.status_code == 403, off_project.text[:300]
            escalation = await _assign(env, "org_admin", target_a, ["orguser", ROLE_PRIVILEGED])
            assert escalation.status_code == 403, "an org admin delegated a permission it does not hold"

            for user_id, roles in ((target_b, ["orguser"]), (target_a, ["orguser"])):
                stored = await env.db.users.find_one({"_id": ObjectId(user_id)})
                assert stored["roles"] == roles, "a refused assignment was written"

            granted = await _assign(env, "org_admin", target_a, ["orguser", ROLE_A])
            assert granted.status_code == 200, granted.text[:300]
            assert await _has(target_a, VIEW, organization_id=ORG_A)

            # A disabled account gets no new role; existing roles are left alone.
            await env.db.users.update_one({"_id": ObjectId(target_a)}, {"$set": {"disabled": True}})
            await env.db.roles.insert_one(_role("sec-role-a-second", [VIEW], organization_id=ORG_A))
            disabled = await _assign(env, "org_admin", target_a, ["orguser", ROLE_A, "sec-role-a-second"])
            assert disabled.status_code == 409, disabled.text[:300]

    _run(scenario())


def test_concurrent_valid_and_invalid_assignments(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            target = await _user(env.db, "sec_race_target", ["orguser"], organization_id=ORG_A)
            results = await asyncio.gather(
                *[_assign(env, "superadmin", target, ["orguser", ROLE_B]) for _ in range(4)],
                *[_assign(env, "org_admin", target, ["orguser", ROLE_A]) for _ in range(4)],
            )
            assert all(response.status_code == 403 for response in results[:4]), [r.status_code for r in results[:4]]
            assert all(response.status_code == 200 for response in results[4:]), [r.text[:200] for r in results[4:]]
            stored = await env.db.users.find_one({"_id": ObjectId(target)})
            assert ROLE_B not in stored["roles"] and ROLE_A in stored["roles"]

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-4: disabled accounts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field,value", [("disabled", True), ("is_active", False)])
def test_a_disabled_account_stops_on_its_next_request(monkeypatch, field: str, value: Any) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            headers = {"Authorization": f"Bearer {_token('org_admin')}"}
            claims_before = await env.client.get("/api/claims", headers={**headers, "X-Proj-Id": PROJ_A1})
            assert claims_before.status_code == 200, claims_before.text[:300]
            await env.db.users.update_one({"_id": "user-org_admin"}, {"$set": {field: value}})
            claims = await env.client.get("/api/claims", headers={**headers, "X-Proj-Id": PROJ_A1})
            assert claims.status_code == 401, (claims.status_code, claims.text[:300])

            from rbac_backend.core.security import StoredPrincipalUnavailableError, resolve_stored_principal

            with pytest.raises(StoredPrincipalUnavailableError):
                await resolve_stored_principal(env.db, "user-org_admin")

            # A disabled service account stops too.
            service = await env.client.get("/api/claims", headers={"Authorization": f"Bearer {_token('system_user')}", "X-Proj-Id": PROJ_A1})
            # (Answered before the account is disabled, whatever the route makes of a service account.)
            assert service.status_code != 401, service.text[:300]
            await env.db.users.update_one({"_id": "user-system_user"}, {"$set": {"disabled": True}})
            stopped = await env.client.get("/api/claims", headers={"Authorization": f"Bearer {_token('system_user')}", "X-Proj-Id": PROJ_A1})
            assert stopped.status_code == 401

    _run(scenario())
