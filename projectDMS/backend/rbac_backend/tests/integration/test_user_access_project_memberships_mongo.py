"""T-1: the user-read rule for project-tier users counts active project memberships.

``AuthorizationService.check_user_access`` (``GET/PUT/DELETE /api/users/{id}``, and the
other-user path of ``POST /api/permissions/check``) used to read projects from
``users.projects`` only, and skipped the overlap test when the actor's list was empty: a
project administrator assigned only by a ``project_memberships`` row could read every user
of the organisation. Now both sides count ``users.projects`` and active membership rows,
only for existing, active projects of the actor's organisation, and a project-tier actor
with no such project reaches nobody.

Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


async def _member(env: Any, persona: str, roles: List[str], *, org: str = ORG_A, projects: Optional[List[str]] = None,
                  rows: Optional[List[Dict[str, Any]]] = None) -> str:
    user_id = await _user(env.db, persona, roles, organization_id=org, projects=projects or [])
    for row in rows or []:
        await env.db.project_memberships.insert_one({"user_id": user_id, **row})
    return user_id


async def _reads(env: Any, actor: str, target: str) -> int:
    return (await env.client.get(f"/api/users/{target}", headers=_headers(actor))).status_code


def test_a_project_administrator_by_membership_row_reads_only_their_projects_users(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _member(env, "t1_row_admin", ["projectadmin"], rows=[{"project_id": PROJ_A1, "status": "active"}])
            listed_a1 = await _member(env, "t1_listed_a1", ["projectuser"], projects=[PROJ_A1])
            row_a1 = await _member(env, "t1_row_a1", ["projectuser"], rows=[{"project_id": PROJ_A1, "status": "active"}])
            only_a2 = await _member(env, "t1_only_a2", ["projectuser"], projects=[PROJ_A2])
            no_project = await _member(env, "t1_no_project", ["orguser"])
            other_org = await _member(env, "t1_other_org", ["projectuser"], org=ORG_B, projects=[PROJ_B1])
            assert await _reads(env, "t1_row_admin", listed_a1) == 200
            assert await _reads(env, "t1_row_admin", row_a1) == 200, "membership-only target on the same project"
            # Before T-1 every one of these was readable: the empty users.projects skipped the test.
            assert await _reads(env, "t1_row_admin", only_a2) == 403
            assert await _reads(env, "t1_row_admin", no_project) == 403
            assert await _reads(env, "t1_row_admin", other_org) == 403

    _run(scenario())


def test_revoked_and_inactive_assignments_do_not_count(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            target = await _member(env, "t1_target", ["projectuser"], projects=[PROJ_A1])
            await _member(env, "t1_revoked_admin", ["projectadmin"], rows=[{"project_id": PROJ_A1, "status": "revoked"}])
            assert await _reads(env, "t1_revoked_admin", target) == 403, "a revoked membership is no assignment"
            await _member(env, "t1_live_admin", ["projectadmin"], rows=[{"project_id": PROJ_A1, "status": "active"}])
            assert await _reads(env, "t1_live_admin", target) == 200
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"is_active": False}})
            assert await _reads(env, "t1_live_admin", target) == 403, "an inactive project confers nothing"
            # A target whose only tie is a revoked row is not on the project either.
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"is_active": True}})
            ghost = await _member(env, "t1_ghost", ["projectuser"], rows=[{"project_id": PROJ_A1, "status": "revoked"}])
            assert await _reads(env, "t1_live_admin", ghost) == 403

    _run(scenario())


def test_a_membership_row_naming_another_organisations_project_reaches_nobody(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _member(env, "t1_cross_admin", ["projectadmin"], rows=[{"project_id": PROJ_B1, "status": "active"}])
            b_user = await _member(env, "t1_b_user", ["projectuser"], org=ORG_B, projects=[PROJ_B1])
            a_user = await _member(env, "t1_a_user", ["projectuser"], rows=[{"project_id": PROJ_B1, "status": "active"}])
            assert await _reads(env, "t1_cross_admin", b_user) == 403, "another organisation's user"
            assert await _reads(env, "t1_cross_admin", a_user) == 403, "a project of B confers nothing in A"

    _run(scenario())


def test_a_project_tier_user_with_no_project_reaches_nobody(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _member(env, "t1_unassigned_admin", ["projectadmin"])
            for persona, projects in (("t1_x1", [PROJ_A1]), ("t1_x2", [PROJ_A2]), ("t1_x3", [])):
                target = await _member(env, persona, ["projectuser"], projects=projects)
                assert await _reads(env, "t1_unassigned_admin", target) == 403, persona

    _run(scenario())


def test_organisation_tier_access_is_unchanged(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            in_a = await _member(env, "t1_in_a", ["projectuser"], projects=[PROJ_A2])
            in_b = await _member(env, "t1_in_b", ["projectuser"], org=ORG_B, projects=[PROJ_B1])
            org_admin = {"Authorization": _headers("org_admin")["Authorization"]}
            assert (await env.client.get(f"/api/users/{in_a}", headers=org_admin)).status_code == 200
            assert (await env.client.get(f"/api/users/{in_b}", headers=org_admin)).status_code == 403

    _run(scenario())
