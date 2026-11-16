"""4B-10: privileged claim material over real HTTP, real MongoDB and real RBAC.

Plan D-12 / owner review: Super Admin status must not open privileged legal
material, and "explicit grant" must mean explicit. Before 4B-10:

- PolicyService admitted a Super Admin to every permission, including
  ``dms.claim.strategy.read`` and ``dms.claim.determine``;
- any holder of ``dms.admin`` (seeded on orgadmin, projectadmin and
  contractmgr_org) passed every ``dms.*`` check, so the "explicit grant only"
  rule held only at seeding time;
- the release role definitions gave both permissions to orgadmin and
  projectadmin, and role-contract alignment would have pushed them to existing
  role documents.

Each actor below is a real user with real role documents; requests go through
the production ``app`` with no dependency override.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set). Run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

STRATEGY = f"/api/claims/{CLAIM}/strategy-note"
SIMULATIONS = f"/api/claims/{CLAIM}/simulations"
DETERMINE = f"/api/claims/{CLAIM}/determinations"

#: Custom actors, each a user holding exactly the role named.
CUSTOM_ROLES: Dict[str, List[str]] = {
    # The deliberate grant, by name, alongside ordinary claim access.
    "claims_counsel": ["dms.claim.view", "dms.claim.edit", "dms.claim.strategy.read", "dms.claim.determine"],
    # Every other route to the same material.
    "wildcard_role": ["*"],
    "dms_admin_only": ["dms.admin", "dms.claim.view", "dms.claim.edit"],
    "claim_manager_only": ["dms.claim.view", "dms.claim.edit", "dms.claim.manage"],
}


async def _add_actor(db: Any, persona: str, role_id: str, permissions: List[str]) -> None:
    await db.roles.insert_one(
        {"_id": role_id, "name": role_id, "description": "4B-10 test role", "permissions": permissions,
         "scope": "organization", "organization_id": ORG_A, "is_active": True}
    )
    await db.users.insert_one(
        {"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona, "disabled": False,
         "account_type": "client_user", "organizations": [], "roles": [role_id],
         "organization_id": ORG_A, "projects": [PROJ_A1]}
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _prepare(env: Any, capacity: str) -> None:
    await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": capacity}})
    for role_id, permissions in CUSTOM_ROLES.items():
        await _add_actor(env.db, role_id, role_id, permissions)
    opened = await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")
    assert opened.status_code == 200, opened.text


def _superadmin_headers(env: Any) -> Dict[str, str]:
    headers = env.headers("superadmin")
    headers["X-Org-Id"] = ORG_A
    return headers


def test_strategy_material_needs_a_grant_held_by_name(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepare(env, "contractor")
            refused = {}
            for persona in ("org_admin", "project_admin", "wildcard_role", "dms_admin_only", "claim_manager_only"):
                for path in (STRATEGY, SIMULATIONS):
                    response = await env.call("GET", persona, path)
                    refused[(persona, path)] = response.status_code
            for (persona, path), code in refused.items():
                assert code == 403, (persona, path, code)

            super_admin = await env.client.get(STRATEGY, headers=_superadmin_headers(env))
            assert super_admin.status_code == 403, super_admin.text[:200]

            granted = await env.call("GET", "claims_counsel", STRATEGY)
            assert granted.status_code == 200, granted.text[:300]
            assert (await env.call("GET", "claims_counsel", SIMULATIONS)).status_code == 200

            # The refusal is recorded with its reason.
            denials = await env.db.audit_events.count_documents(
                {"action": "policy.authorize", "result": "deny", "reason": "explicit_grant_required",
                 "metadata.permission": "dms.claim.strategy.read"}
            )
            assert denials >= 1

    _run(scenario())


def test_determination_authority_needs_a_grant_held_by_name(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepare(env, "engineer")
            for persona in ("org_admin", "project_admin", "wildcard_role", "dms_admin_only", "claim_manager_only"):
                response = await env.call("POST", persona, DETERMINE, json={"origin": "authored"})
                assert response.status_code == 403, (persona, response.status_code, response.text[:200])
            super_admin = await env.client.post(DETERMINE, json={"origin": "authored"}, headers=_superadmin_headers(env))
            assert super_admin.status_code == 403, super_admin.text[:200]
            assert await env.db.claim_determinations.count_documents({}) == 0

            created = await env.call("POST", "claims_counsel", DETERMINE, json={"origin": "authored"})
            assert created.status_code == 200, created.text[:300]
            assert await env.db.claim_determinations.count_documents({}) == 1

    _run(scenario())


def test_ordinary_claim_work_is_unchanged_for_admins(monkeypatch) -> None:
    """The exclusion is narrow: admins keep every ordinary claim route."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepare(env, "contractor")
            for persona in ("org_admin", "project_admin", "dms_admin_only"):
                response = await env.call("GET", persona, f"/api/claims/{CLAIM}/bench")
                assert response.status_code == 200, (persona, response.status_code)
            super_admin = await env.client.get(f"/api/claims/{CLAIM}/bench", headers=_superadmin_headers(env))
            assert super_admin.status_code == 200, super_admin.text[:200]
            listing = await env.call("GET", "org_admin", DETERMINE)
            assert listing.status_code == 200
            assert listing.json()["can_author"] is False

    _run(scenario())


def test_revoking_the_grant_takes_effect_on_the_next_request(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepare(env, "contractor")
            assert (await env.call("GET", "claims_counsel", STRATEGY)).status_code == 200
            await env.db.roles.update_one(
                {"_id": "claims_counsel"}, {"$pull": {"permissions": "dms.claim.strategy.read"}}
            )
            assert (await env.call("GET", "claims_counsel", STRATEGY)).status_code == 403

    _run(scenario())


def test_a_grant_saved_by_catalogue_id_is_a_grant_by_name(monkeypatch) -> None:
    """4B-49: the Permissions page saves a role's permissions by catalogue id.

    Ticking ``dms.claim.strategy.read`` there stores the permission document's
    ObjectId. That is a deliberate grant on the user's own role and must count as
    one; before 4B-49 it passed ``has_permission`` but failed the explicit check, so
    no grant made through the product could open the strategy note. The id of a
    different permission still grants nothing privileged.
    """

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepare(env, "contractor")

            async def catalogue_id(name: str) -> str:
                document = await env.db.permissions.find_one({"name": name})
                assert document is not None and not isinstance(document["_id"], str), name
                return str(document["_id"])

            ordinary = [await catalogue_id(n) for n in ("dms.claim.view", "dms.claim.edit")]
            await _add_actor(env.db, "counsel_by_id", "counsel_by_id", [*ordinary, await catalogue_id("dms.claim.strategy.read")])
            await _add_actor(env.db, "manager_by_id", "manager_by_id", [*ordinary, await catalogue_id("dms.claim.manage")])

            granted = await env.call("GET", "counsel_by_id", STRATEGY)
            assert granted.status_code == 200, granted.text[:300]
            refused = await env.call("GET", "manager_by_id", STRATEGY)
            assert refused.status_code == 403, refused.text[:300]
            # Determination authority was never ticked for either.
            for persona in ("counsel_by_id", "manager_by_id"):
                response = await env.call("POST", persona, DETERMINE, json={"origin": "authored"})
                assert response.status_code == 403, (persona, response.status_code)

    _run(scenario())


def test_the_release_role_contract_no_longer_carries_the_privileged_permissions() -> None:
    from rbac_backend.services.role_contract_alignment import release_contract

    for role_id in ("orgadmin", "projectadmin"):
        contract = set(release_contract(role_id))
        assert "dms.claim.determine" not in contract
        assert "dms.claim.strategy.read" not in contract
