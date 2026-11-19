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


def _no_role_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests make more role changes a minute than the per-process limit (20) allows."""
    from rbac_backend.routers import roles as roles_router

    monkeypatch.setattr(roles_router.role_mutation_limiter, "max_requests", 10_000)


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
    _no_role_rate_limit(monkeypatch)
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


# --------------------------------------------------------------------------- #
# JG-7(c): custom-role row visibility, opted in on the claims list
# --------------------------------------------------------------------------- #


async def _claims(env: Any, persona: str, project: Optional[str] = None) -> Any:
    headers = {"Authorization": f"Bearer {_token(persona)}"}
    if project:
        headers["X-Proj-Id"] = project
    return await env.client.get("/api/claims", headers=headers)


def test_custom_role_holders_see_claims_only_where_they_hold_the_permission(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.claims.insert_many(
                [
                    {"_id": "sec-claim-a2", "claim_ref": "CLM-A2", "title": "A2", "type": "eot", "status": "draft",
                     "organization_id": ORG_A, "project_id": PROJ_A2, "evidence_frozen_at": None},
                    {"_id": "sec-claim-b1", "claim_ref": "CLM-B1", "title": "B1", "type": "eot", "status": "draft",
                     "organization_id": ORG_B, "project_id": PROJ_B1, "evidence_frozen_at": None},
                ]
            )
            await env.db.roles.insert_many(
                [
                    _role("sec-claims-org-a", [VIEW], organization_id=ORG_A),
                    _role("sec-claims-a2", [VIEW], organization_id=ORG_A, project_id=PROJ_A2),
                    _role("sec-claims-nothing", ["dms.document.view"], organization_id=ORG_A),
                    _role("sec-claims-org-b", [VIEW], organization_id=ORG_B),
                ]
            )
            await _user(env.db, "sec_cv_org", ["sec-claims-org-a"], organization_id=ORG_A)
            await _user(env.db, "sec_cv_a2", ["sec-claims-a2"], organization_id=ORG_A, projects=[PROJ_A2])
            await _user(env.db, "sec_cv_none", ["sec-claims-nothing"], organization_id=ORG_A)
            await _user(env.db, "sec_cv_foreign", ["sec-claims-org-b"], organization_id=ORG_A)

            def ids(response: Any) -> set:
                assert response.status_code == 200, response.text[:300]
                return {row["_id"] for row in response.json()}

            org_wide = ids(await _claims(env, "sec_cv_org"))
            assert org_wide == {"claim-bench-a1", "sec-claim-a2"}, org_wide
            # Selecting a project needs an explicit assignment to it (the tenant gate), even
            # with an organisation-wide custom role.
            assert (await _claims(env, "sec_cv_org", PROJ_A1)).status_code == 403
            await env.db.users.update_one({"email": "sec_cv_org@example.com"}, {"$set": {"projects": [PROJ_A1]}})
            assert ids(await _claims(env, "sec_cv_org", PROJ_A1)) == {"claim-bench-a1"}
            await env.db.users.update_one({"email": "sec_cv_org@example.com"}, {"$set": {"projects": []}})
            assert ids(await _claims(env, "sec_cv_a2", PROJ_A2)) == {"sec-claim-a2"}
            # Review R-3: a project role counts only where its project is named, so a project-role
            # holder lists claims by selecting the project; without one the list is refused.
            assert (await _claims(env, "sec_cv_a2")).status_code == 403
            # A project role reaches no other project's rows.
            other = await _claims(env, "sec_cv_a2", PROJ_A1)
            assert other.status_code in {403} or ids(other) == set()
            # Holding a custom role is not enough: it must carry the permission.
            assert (await _claims(env, "sec_cv_none")).status_code == 403
            # A role of B held by a member of A grants nothing anywhere.
            assert (await _claims(env, "sec_cv_foreign")).status_code == 403
            # Built-in roles unchanged.
            assert ids(await _claims(env, "org_admin")) >= {"claim-bench-a1", "sec-claim-a2"}
            assert "sec-claim-b1" not in ids(await _claims(env, "org_admin"))

            # Revoking the role, or disabling the account, ends the visibility.
            await env.db.roles.update_one({"_id": "sec-claims-org-a"}, {"$set": {"is_active": False}})
            assert (await _claims(env, "sec_cv_org")).status_code == 403
            await env.db.users.update_one({"email": "sec_cv_a2@example.com"}, {"$set": {"disabled": True}})
            assert (await _claims(env, "sec_cv_a2", PROJ_A2)).status_code == 401

    _run(scenario())


def test_the_custom_role_filter_never_widens_and_always_names_the_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.security import CurrentUser, build_scope_query, permission_scope_query

            await env.db.roles.insert_many(
                [_role("sec-q-org", [VIEW], organization_id=ORG_A), _role("sec-q-p", [VIEW], organization_id=ORG_A, project_id=PROJ_A2)]
            )
            org_user = await _user(env.db, "sec_q_org", ["sec-q-org"], organization_id=ORG_A)
            project_user = await _user(env.db, "sec_q_p", ["sec-q-p"], organization_id=ORG_A, projects=[PROJ_A2])

            def principal(user_id: str, roles: List[str], projects: List[str]) -> CurrentUser:
                return CurrentUser(id=user_id, username="u", email="u@example.com", roles=roles,
                                   organization_id=ORG_A, organizations=[ORG_A], projects=projects)

            deny = {"_id": {"$in": []}}
            org_principal = principal(org_user, ["sec-q-org"], [])
            project_principal = principal(project_user, ["sec-q-p"], [PROJ_A2])
            for query in (
                await permission_scope_query(org_principal, VIEW, db=env.db),
                await permission_scope_query(project_principal, VIEW, db=env.db),
                await permission_scope_query(org_principal, VIEW, project_id=PROJ_A1, db=env.db),
            ):
                assert query != {} and "$or" not in query and query.get("organization_id") == ORG_A, query
            assert await permission_scope_query(org_principal, VIEW, organization_id=ORG_B, db=env.db) == deny
            assert await permission_scope_query(org_principal, VIEW, project_id=PROJ_B1, db=env.db) == deny
            assert await permission_scope_query(project_principal, VIEW, project_id=PROJ_A1, db=env.db) == deny
            assert await permission_scope_query(org_principal, MANAGE, db=env.db) == deny
            # A wildcard is not a grant by name: it gives no rows.
            await env.db.roles.insert_one(_role("sec-q-star", ["*"], organization_id=ORG_A))
            star_user = await _user(env.db, "sec_q_star", ["sec-q-star"], organization_id=ORG_A)
            assert await permission_scope_query(principal(star_user, ["sec-q-star"], []), VIEW, db=env.db) == deny
            # Built-in principals: exactly build_scope_query.
            builtin = principal("user-org_admin", ["orgadmin"], [])
            assert await permission_scope_query(builtin, VIEW, db=env.db) == build_scope_query(builtin)

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-1 boundary (review 2026-11-19): a project names its organisation
# --------------------------------------------------------------------------- #


def test_an_organisation_role_never_reaches_a_project_of_another_organisation(monkeypatch) -> None:
    """A member of A and B holding only A's organisation role asks about a project of B,
    with the organisation omitted, with the wrong organisation, and for a project that
    does not exist. The project's own organisation decides; anything unresolvable fails."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from types import SimpleNamespace

            from fastapi import HTTPException

            from rbac_backend.services.permission_service import PermissionService
            from rbac_backend.services.policy_service import PolicyService

            await _seed_roles(env.db)
            dual = await _user(env.db, "sec_boundary", [ROLE_A], organization_id=ORG_A,
                               organizations=[ORG_A, ORG_B], projects=[PROJ_A1, PROJ_B1])
            service = PermissionService()
            for _ in range(2):  # the second pass is served from whatever the first cached
                assert await _has(dual, VIEW, project_id=PROJ_A1)
                assert not await _has(dual, VIEW, project_id=PROJ_B1), "A's role reached a project of B"
                assert not await _has(dual, VIEW, organization_id=ORG_A, project_id=PROJ_B1), "mismatched pair"
                # The other mismatch: the organisation is one the user has no grant in, the project
                # is one of A. A request that contradicts itself is refused, not reinterpreted.
                assert not await _has(dual, VIEW, organization_id=ORG_B, project_id=PROJ_A1), "contradictory pair"
                assert not await _has(dual, VIEW, project_id="no-such-project"), "unresolvable project"
                assert not await service.user_has_explicit_permission(dual, VIEW, project_id=PROJ_B1)
                assert VIEW not in set(await service.get_effective_permission_names(dual, project_id=PROJ_B1))

            policy = PolicyService(env.db)
            principal = SimpleNamespace(id=dual, roles=[ROLE_A], organization_id=ORG_A, organizations=[ORG_A, ORG_B],
                                        projects=[PROJ_A1, PROJ_B1], account_type="client_user")
            assert await policy.has_permission(principal, VIEW, project_id=PROJ_A1)
            assert not await policy.has_permission(principal, VIEW, project_id=PROJ_B1)
            for organisation in (None, ORG_A, ORG_B):
                with pytest.raises(HTTPException) as refused:
                    await policy.authorize(principal, VIEW, resource_type="claims", organization_id=organisation,
                                           project_id=PROJ_B1, audit=False)
                assert refused.value.status_code == 403, organisation

            # Row visibility resolves the project's organisation too: a project role of B1 held by a
            # user whose home organisation is A, and who is a member of B, sees B1's rows (named as B).
            from rbac_backend.core.security import CurrentUser, permission_scope_query

            await env.db.roles.insert_one(_role("sec-role-b1", [VIEW], organization_id=ORG_B, project_id=PROJ_B1))
            b1_holder = await _user(env.db, "sec_b1_holder", ["sec-role-b1"], organization_id=ORG_A,
                                    organizations=[ORG_A, ORG_B], projects=[PROJ_B1])
            holder = CurrentUser(id=b1_holder, username="u", email="u@example.com", roles=["sec-role-b1"],
                                 organization_id=ORG_A, organizations=[ORG_A, ORG_B], projects=[PROJ_B1])
            rows = await permission_scope_query(holder, VIEW, project_id=PROJ_B1, db=env.db)
            assert rows.get("organization_id") == ORG_B, rows
            assert await permission_scope_query(holder, VIEW, project_id=PROJ_A1, db=env.db) == {"_id": {"$in": []}}
            assert await _has(b1_holder, VIEW, project_id=PROJ_B1)

            # Over HTTP: the claims list for B1 is refused; for A1 it answers.
            await env.db.claims.insert_one({"_id": "sec-b1", "claim_ref": "B1", "title": "B1", "type": "eot", "status": "draft",
                                            "organization_id": ORG_B, "project_id": PROJ_B1, "evidence_frozen_at": None})
            b1 = await _claims(env, "sec_boundary", PROJ_B1)
            assert b1.status_code == 403 or b1.json() == [], b1.text[:300]
            a1 = await _claims(env, "sec_boundary", PROJ_A1)
            assert a1.status_code == 200 and "sec-b1" not in {row["_id"] for row in a1.json()}

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-2 / SEC-3 adversarial review (2026-11-19)
# --------------------------------------------------------------------------- #


def test_no_spelling_of_a_privileged_permission_reaches_a_role(monkeypatch) -> None:
    _no_role_rate_limit(monkeypatch)
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            headers = _step_up("org_admin", "user-org_admin")
            # A catalogue document whose display name hides drafting.admin in its code.
            hidden = ObjectId()
            await env.db.permissions.insert_one({"_id": hidden, "name": "Drafting admin label", "code": "drafting.admin"})
            attempts = {
                "catalogue id": str(hidden),
                "catalogue label": "Drafting admin label",
                "wildcard": "*",
                "role management": "roles:assign",
                "platform": "platform.role.manage",
                # DEF-4: ordinary-looking legacy names that pass non-delegable billing checks.
                "alias of billing views": "organizations:read",
                "label of that alias": "orgs:view",
                "alias of subscription usage": "reports:view",
            }
            for label, entry in attempts.items():
                response = await env.client.post("/api/roles", headers=headers, json={"name": f"Try {label}", "permissions": [entry]})
                assert response.status_code == 403, (label, response.status_code, response.text[:200])
            assert await env.db.roles.count_documents({"name": {"$regex": "^Try "}}) == 0

    _run(scenario())


def test_no_role_impersonates_a_built_in_or_system_role(monkeypatch) -> None:
    _no_role_rate_limit(monkeypatch)
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            headers = _step_up("org_admin", "user-org_admin")
            for name in ("Super Admin", "SUPERADMIN", "super_user", "Org Admin", "organization-admin", "Project User", "orguser"):
                response = await env.client.post("/api/roles", headers=headers, json={"name": name, "permissions": [VIEW]})
                assert response.status_code in {400, 403, 409}, (name, response.status_code)  # 409: the name is taken
            for body in ({"name": "Sys", "permissions": [VIEW], "is_system": True}, {"name": "Sys2", "permissions": [VIEW], "scope": "system"}):
                response = await env.client.post("/api/roles", headers=headers, json=body)
                stored = await env.db.roles.find_one({"name": body["name"]})
                assert response.status_code == 403 or (stored and stored.get("scope") != "system" and not stored.get("is_system")), body

            # Legacy spellings of built-in roles are judged as the role they become.
            target = await _user(env.db, "sec_alias_target", ["orguser"], organization_id=ORG_A)
            for spelling in ("org-admin", "Organization Admin", "super-admin"):
                response = await _assign(env, "org_admin", target, ["orguser", spelling])
                assert response.status_code in {400, 403}, (spelling, response.status_code, response.text[:200])
            stored = await env.db.users.find_one({"_id": ObjectId(target)})
            assert stored["roles"] == ["orguser"]

            # A stored custom role that merely carries a system name confers no bypass.
            await env.db.roles.insert_one({"_id": "sec-fake-sa", "name": "Super Admin Lookalike", "permissions": [],
                                           "scope": "organization", "organization_id": ORG_A, "is_active": True})
            pretender = await _user(env.db, "sec_pretender", ["sec-fake-sa"], organization_id=ORG_A)
            assert not await _has(pretender, VIEW)
            from types import SimpleNamespace

            from fastapi import HTTPException

            from rbac_backend.services.policy_service import PolicyService

            principal = SimpleNamespace(id=pretender, roles=["sec-fake-sa"], organization_id=ORG_A, organizations=[ORG_A],
                                        projects=[], account_type="client_user")
            with pytest.raises(HTTPException):
                await PolicyService(env.db).authorize(principal, VIEW, resource_type="claims", organization_id=ORG_B, audit=False)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Independent security review (2026-11-19): findings R-1 to R-3
# --------------------------------------------------------------------------- #


def test_r1_a_custom_role_named_like_a_built_in_key_is_still_a_custom_role(monkeypatch) -> None:
    _no_role_rate_limit(monkeypatch)

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            headers = _step_up("org_admin", "user-org_admin")
            for name in ("legal_reviewer", "Legal_Reviewer", "contract_expert", "chief_contract_expert", "Quantum Reviewer"):
                response = await env.client.post("/api/roles", headers=headers, json={"name": name, "permissions": [VIEW]})
                assert response.status_code in {400, 403, 409}, (name, response.status_code, response.text[:200])
            # A stored one (legacy, or written around the API) is judged by its id, not its name.
            await env.db.roles.insert_one(_role("x", [VIEW], organization_id=ORG_A) | {"_id": ObjectId("65f0000000000000000000aa"), "name": "legal_reviewer"})
            holder_b = await _user(env.db, "sec_r1_b", ["65f0000000000000000000aa"], organization_id=ORG_B)
            assert not await _has(holder_b, VIEW), "a name made a custom role built-in"
            target = await _user(env.db, "sec_r1_target", ["orguser"], organization_id=ORG_B)
            assigned = await _assign(env, "superadmin", target, ["orguser", "65f0000000000000000000aa"])
            assert assigned.status_code == 403, assigned.text[:200]

    _run(scenario())


def test_r2_an_alias_cannot_carry_permissions_its_creator_does_not_hold(monkeypatch) -> None:
    _no_role_rate_limit(monkeypatch)

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            # A project admin creates a project role with the legacy projects:update (and its label
            # projects:edit), which passes 23 canonical checks including org-tier ones it lacks.
            headers = _step_up("project_admin", "user-project_admin")
            for entry in ("projects:update", "projects:edit"):
                response = await env.client.post("/api/roles", headers=headers,
                                                 json={"name": f"Alias {entry}", "permissions": [entry], "project_id": PROJ_A1})
                assert response.status_code == 403, (entry, response.status_code, response.text[:200])
            assert await env.db.roles.count_documents({"name": {"$regex": "^Alias "}}) == 0

    _run(scenario())


def test_r3_a_project_role_never_counts_at_organisation_level(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            await env.db.roles.insert_one(_role("sec-r3-p", [VIEW, STRATEGY], organization_id=ORG_A, project_id=PROJ_A1))
            holder = await _user(env.db, "sec_r3", ["sec-r3-p"], organization_id=ORG_A, projects=[PROJ_A1])
            assert await _has(holder, VIEW, project_id=PROJ_A1)
            assert not await _has(holder, VIEW, organization_id=ORG_A), "a project role counted for the whole organisation"
            # Re-review: nor on a permission-only gate (nothing named), primary organisation or not.
            assert not await _has(holder, VIEW), "a project role passed an unscoped gate"
            service = PermissionService()
            assert await service.user_has_explicit_permission(holder, STRATEGY, organization_id=ORG_A, project_id=PROJ_A1)
            assert not await service.user_has_explicit_permission(holder, STRATEGY, organization_id=ORG_A)
            # A project role stops counting once its project is inactive.
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"is_active": False}})
            assert not await _has(holder, VIEW, project_id=PROJ_A1)

    _run(scenario())


def test_r4_an_unscoped_check_counts_custom_roles_only_in_the_primary_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_roles(env.db)
            # Primary organisation B, still listed in A, holding A's custom role.
            moved = await _user(env.db, "sec_r4", [ROLE_A], organization_id=ORG_B, organizations=[ORG_A, ORG_B])
            assert not await _has(moved, VIEW), "an unscoped check counted a role outside the primary organisation"
            assert await _has(moved, VIEW, organization_id=ORG_A), "named explicitly, the A membership still counts"

    _run(scenario())
