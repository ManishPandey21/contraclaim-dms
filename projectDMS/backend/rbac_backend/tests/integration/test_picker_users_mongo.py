"""4B-11 / C-21: the user picker offers only project members holding the authority.

Over real HTTP, MongoDB and RBAC with the production ``app`` and no dependency
override. Before 4B-11 the claim approval dialog called ``/users``, which lists
everyone in the caller's organisations with no authority filter.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Set

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env, _never_leaks
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2

pytestmark = pytest.mark.integration

PICK = "/api/pickers/users"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _person(db: Any, persona: str, permissions: List[str], *, projects: List[str], org: str = ORG_A,
                  disabled: bool = False, full_name: str | None = None) -> None:
    role_id = f"role-{persona}"
    await db.roles.insert_one({"_id": role_id, "name": role_id, "permissions": permissions, "scope": "project",
                               "organization_id": org, "is_active": True})
    await db.users.insert_one(
        {"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona,
         "full_name": full_name or persona.replace("_", " ").title(), "disabled": disabled,
         "account_type": "client_user", "organizations": [], "roles": [role_id], "organization_id": org,
         "projects": projects}
    )


async def _people(env: Any) -> None:
    await _person(env.db, "claims_counsel", ["dms.claim.view", "dms.claim.determine"], projects=[PROJ_A1])
    await _person(env.db, "counsel_elsewhere", ["dms.claim.view", "dms.claim.determine"], projects=[PROJ_A2])
    await _person(env.db, "counsel_left", ["dms.claim.view", "dms.claim.determine"], projects=[PROJ_A1], disabled=True)
    await _person(env.db, "dms_admin_only", ["dms.admin", "dms.claim.view"], projects=[PROJ_A1])
    await _person(env.db, "viewer_only", ["dms.document.view"], projects=[PROJ_A1])
    # A global account that happens to carry the organisation and the project: the scope rule
    # admits Super Admin everywhere, so only the global-role exclusion keeps it off the list.
    await env.db.users.insert_one(
        {"_id": "user-platform_admin", "email": "platform@example.com", "username": "platform_admin",
         "full_name": "Platform Admin", "disabled": False, "account_type": "client_user", "organizations": [ORG_A],
         "roles": ["superadmin"], "organization_id": ORG_A, "projects": [PROJ_A1]}
    )


async def _ids(env: Any, persona: str, permission: str, project: str = PROJ_A1, **params: Any) -> Set[str]:
    response = await env.call("GET", persona, PICK, project=project,
                              params={"project_id": project, "permission": permission, **params})
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    for item in body["items"]:
        assert set(item) == {"id", "name"}, item  # no email, no roles
    return {item["id"] for item in body["items"]}


def test_determination_authority_lists_only_named_holders_on_the_project(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _people(env)
            ids = await _ids(env, "org_admin", "dms.claim.determine")
            # Held by name on this project; not on another project, not disabled, not via dms.admin,
            # not by an admin role, and never Super Admin or Super User.
            assert ids == {"user-claims_counsel"}, ids

    _run(scenario())


def test_claim_access_lists_project_and_organisation_members_only(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _people(env)
            ids = await _ids(env, "org_admin", "dms.claim.view")
            assert {"user-claims_counsel", "user-dms_admin_only", "user-org_admin", "user-project_admin"} <= ids, ids
            for absent in ("user-counsel_elsewhere", "user-other_project_admin", "user-foreign_org_admin",
                           "user-counsel_left", "user-viewer_only", "user-superadmin", "user-superuser", "user-platform_admin",
                           "user-system_user"):
                assert absent not in ids, absent

    _run(scenario())


def test_search_narrows_by_name_and_is_not_a_pattern(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _people(env)
            assert await _ids(env, "org_admin", "dms.claim.view", q="counsel") == {"user-claims_counsel"}
            assert await _ids(env, "org_admin", "dms.claim.view", q=".*") == set()

    _run(scenario())


def test_callers_outside_the_project_or_without_access_are_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _people(env)
            params = {"project_id": PROJ_A1, "permission": "dms.claim.determine"}
            _never_leaks(await env.call("GET", "foreign_org_admin", PICK, params=params))
            _never_leaks(await env.call("GET", "other_project_admin", PICK, project=PROJ_A2, params=params))
            _never_leaks(await env.call("GET", "viewer_only", PICK, params=params))
            unknown = await env.call("GET", "org_admin", PICK, params={"project_id": PROJ_A1, "permission": "dms.admin"})
            assert unknown.status_code == 422
            assert unknown.json()["detail"]["code"] == "picker_permission_not_supported"
            missing = await env.call("GET", "org_admin", PICK, params={"project_id": "nope", "permission": "dms.claim.view"})
            assert missing.status_code == 404

    _run(scenario())


def test_another_organisation_sees_only_its_own_people(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _people(env)
            from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import PROJ_B1

            response = await env.call("GET", "foreign_org_admin", PICK, project=PROJ_B1,
                                      params={"project_id": PROJ_B1, "permission": "dms.claim.view"})
            assert response.status_code == 200, response.text[:300]
            ids = {item["id"] for item in response.json()["items"]}
            assert ids and all(not person.startswith("user-claims") for person in ids)
            assert "user-org_admin" not in ids and "user-foreign_org_admin" in ids
            assert ORG_B  # the organisation the response is bounded to

    _run(scenario())
