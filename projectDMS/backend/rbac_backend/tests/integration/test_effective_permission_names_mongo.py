"""Effective permission names for roles saved by catalogue id, on a real MongoDB.

A role can hold a permission by catalogue id (the permission document's ObjectId):
the role API accepts ids, though the Permissions page saves names. The server's check
honours those ids (`get_user_permissions`),
but `get_effective_permission_names` - what `GET /api/me` sends the client for
its route guards and menu - used to send the ids themselves. So every screen gated on
a permission name stayed hidden for such a role although the server allowed it.

Checked here, on the real seeded catalogue:

- for a role saved by id, the names advertised are exactly what
  `user_has_permission` grants, over every catalogue name and its alias spellings;
- `GET /api/me` (what the client reads at sign-in) carries the names;
- nothing is widened: a user's own `permissions` field is still not resolved, because
  the check does not read it.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set). Run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Set

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

REVIEW = "dms.document.review_extraction"


async def _add_user(db: Any, persona: str, roles: List[str], **extra: Any) -> str:
    # An ObjectId, as real accounts have: `GET /api/me` finds users by ObjectId only.
    user_id = ObjectId()
    await db.users.insert_one(
        {"_id": user_id, "email": f"{persona}@example.com", "username": persona, "disabled": False,
         "account_type": "client_user", "organizations": [], "roles": roles,
         "organization_id": ORG_A, "projects": [PROJ_A1], **extra}
    )
    return str(user_id)


async def _add_role(db: Any, role_id: str, permissions: List[str]) -> None:
    await db.roles.insert_one(
        {"_id": role_id, "name": role_id, "description": "saved by catalogue id", "scope": "organization",
         "organization_id": ORG_A, "is_active": True, "permissions": permissions}
    )


def test_a_role_saved_by_catalogue_id_advertises_the_names_it_is_granted(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.core.permissions import equivalent_permissions
        from rbac_backend.services.permission_service import PermissionService

        async with _env(monkeypatch) as env:
            catalogue = await env.db.permissions.find({}).to_list(length=None)
            service = PermissionService()
            name_of = {str(doc["_id"]): service._build_permission_from_doc(doc).name for doc in catalogue}
            ids = sorted(i for i in name_of if len(i) == 24)
            assert len(ids) == len(catalogue) > 100, "the seeded catalogue is keyed by ObjectId"

            # Every third permission, by catalogue id, as the role API accepts them.
            chosen = ids[::3]
            await _add_role(env.db, "saved_by_id", chosen)
            by_id = await _add_user(env.db, "by_id", ["saved_by_id"])
            # Seed roles hold names; they must be unaffected.
            org_admin = await _add_user(env.db, "named_org_admin", ["orgadmin"])

            universe: Set[str] = set()
            for name in name_of.values():
                universe |= equivalent_permissions(name) or {name}

            violations: List[str] = []
            for user_id in (by_id, org_admin):
                advertised = set(await service.get_effective_permission_names(user_id))
                for name in sorted(universe):
                    granted = await service.user_has_permission(user_id, name, log=False)
                    if granted != (name in advertised):
                        violations.append(f"{user_id} {name}: granted={granted} advertised={name in advertised}")
            assert violations == [], "\n".join(violations[:20])

            advertised = set(await service.get_effective_permission_names(by_id))
            assert {name_of[i] for i in chosen} <= advertised
            unchosen = {name_of[i] for i in ids if i not in chosen} - {
                spelling for i in chosen for spelling in equivalent_permissions(name_of[i])
            }
            assert not (unchosen & advertised), sorted(unchosen & advertised)[:5]

            # What the client actually receives.
            me = await env.call("GET", "by_id", "/api/me")
            assert me.status_code == 200, me.text[:300]
            assert {name_of[i] for i in chosen} <= set(me.json()["permissions"])

    asyncio.run(scenario())


def test_the_extraction_review_menu_permission_reaches_the_client(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            document = await env.db.permissions.find_one({"name": REVIEW})
            view = await env.db.permissions.find_one({"name": "dms.document.view"})
            await _add_role(env.db, "reviewer_by_id", [str(view["_id"]), str(document["_id"])])
            await _add_user(env.db, "reviewer_by_id", ["reviewer_by_id"])
            me = await env.call("GET", "reviewer_by_id", "/api/me")
            assert me.status_code == 200, me.text[:300]
            assert {REVIEW, "dms.document.view"} <= set(me.json()["permissions"])

    asyncio.run(scenario())


def test_a_users_own_permissions_field_is_not_resolved_because_the_check_ignores_it(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.services.permission_service import PermissionService

        async with _env(monkeypatch) as env:
            document = await env.db.permissions.find_one({"name": REVIEW})
            user_id = await _add_user(env.db, "user_level", ["orguser"], permissions=[str(document["_id"])])
            service = PermissionService()
            assert await service.user_has_permission(user_id, REVIEW, log=False) is False
            assert REVIEW not in set(await service.get_effective_permission_names(user_id))

    asyncio.run(scenario())
