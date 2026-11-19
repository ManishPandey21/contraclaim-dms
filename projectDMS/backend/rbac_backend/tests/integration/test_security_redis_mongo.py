"""SEC-1 to SEC-4 with a real runtime Redis: sessions, refresh, and the permission cache.

Real HTTP through the production ``app``, real RBAC, a disposable MongoDB replica set
and a disposable Redis (``SECURITY_REDIS_URI``; CI starts one). Each scenario that
depends on Redis runs three ways where it matters:

- ``available``: the runtime Redis answers (sessions, revocation floor, permission cache);
- ``unavailable``: a Redis is configured but unreachable (fail-closed by default, and
  fail-open when ``AUTH_SESSION_FAIL_CLOSED`` is off);
- ``none``: no runtime Redis is configured (no sessions, no cache).

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` and ``SECURITY_REDIS_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import (
    MANAGE,
    ROLE_A,
    VIEW,
    _role,
    _seed_roles,
    _step_up,
    _user,
)
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1, _token

pytestmark = pytest.mark.integration

REDIS_URI_ENV = "SECURITY_REDIS_URI"
PASSWORD = "Correct-Horse-Battery-9"
DEAD_REDIS = "redis://127.0.0.1:1/0"  # nothing listens on port 1


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _redis_uri() -> str:
    uri = os.getenv(REDIS_URI_ENV)
    if not uri:
        pytest.skip(f"set {REDIS_URI_ENV} to a disposable Redis")
    return uri


@asynccontextmanager
async def _redis_env(monkeypatch: pytest.MonkeyPatch, mode: str, *, fail_closed: bool = True) -> AsyncIterator[Any]:
    """``_env`` plus a runtime Redis in the given mode; the Redis is flushed first."""
    from redis.asyncio import Redis

    from rbac_backend.core.config import settings
    from rbac_backend.services.runtime_state import get_runtime_state

    url = {"available": _redis_uri(), "unavailable": DEAD_REDIS, "none": None}[mode]
    if url and mode == "available":
        client = Redis.from_url(url)
        await client.flushdb()
        await client.aclose()
    runtime = get_runtime_state()
    async with _env(monkeypatch) as env:
        monkeypatch.setattr(settings, "RUNTIME_STATE_REDIS_URL", url)
        monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", fail_closed)
        await runtime.close()  # never reuse a client bound to another test's loop
        try:
            yield env
        finally:
            await runtime.close()


async def _login_user(env: Any, persona: str, roles: List[str]) -> str:
    from rbac_backend.core.security import get_password_hash

    oid = ObjectId()
    await env.db.users.insert_one(
        {"_id": oid, "email": f"{persona}@example.com", "username": persona, "first_name": "Sec", "last_name": "Four",
         "hashed_password": get_password_hash(PASSWORD), "disabled": False, "account_type": "client_user",
         "roles": roles, "organization_id": ORG_A, "organizations": [ORG_A], "projects": [PROJ_A1]}
    )
    return str(oid)


async def _login(env: Any, persona: str) -> Any:
    response = await env.client.post("/api/login", json={"email": f"{persona}@example.com", "password": PASSWORD})
    # Bearer-token calls only: a stored auth cookie would make later POSTs cookie-authenticated (CSRF).
    env.client.cookies.clear()
    return response


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Proj-Id": PROJ_A1}


async def _claims(env: Any, token: str) -> int:
    return (await env.client.get("/api/claims", headers=_bearer(token))).status_code


async def _refresh(env: Any, token: str) -> Any:
    response = await env.client.post("/api/refresh", headers=_bearer(token))
    env.client.cookies.clear()  # the refresh sets the auth cookie too; keep the calls bearer-only
    return response


# --------------------------------------------------------------------------- #
# SEC-4: access and refresh tokens after disabling, with and without Redis
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("how", ["directly_in_the_store", "through_the_users_api"])
def test_with_redis_a_disabled_account_loses_access_and_refresh_tokens(monkeypatch, how: str) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            user_id = await _login_user(env, "sec4_live", ["orgadmin"])
            login = await _login(env, "sec4_live")
            assert login.status_code == 200, login.text[:300]
            access = login.json()["access_token"]
            assert await _claims(env, access) == 200
            refreshed = await _refresh(env, access)
            assert refreshed.status_code == 200, refreshed.text[:300]
            second = refreshed.json()["access_token"]
            assert await _claims(env, second) == 200

            if how == "directly_in_the_store":
                # The session keys in Redis are untouched: the account check alone must stop it.
                await env.db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"disabled": True}})
            else:
                disabled = await env.client.put(
                    f"/api/users/{user_id}", headers={"Authorization": f"Bearer {_token('superadmin')}"},
                    json={"disabled": True},
                )
                assert disabled.status_code == 200, disabled.text[:300]

            for token in (access, second):
                assert await _claims(env, token) == 401
                assert (await _refresh(env, token)).status_code == 401
            relogin = await _login(env, "sec4_live")
            assert relogin.status_code == 401

            if how == "through_the_users_api":
                # Re-enabling does not revive the sessions the disable ended.
                await env.db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"disabled": False}})
                assert await _claims(env, access) == 401

    _run(scenario())


@pytest.mark.parametrize("fail_closed", [True, False])
def test_with_redis_unreachable_a_disabled_account_is_never_admitted(monkeypatch, fail_closed: bool) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "unavailable", fail_closed=fail_closed) as env:
            await _login_user(env, "sec4_down", ["orgadmin"])
            token = _token("sec4_down")
            before = await _claims(env, token)
            # Fail-closed: the outage is a 503; fail-open: the request proceeds.
            assert before == (503 if fail_closed else 200), before
            await env.db.users.update_one({"email": "sec4_down@example.com"}, {"$set": {"disabled": True}})
            after = await _claims(env, token)
            assert after in {401, 503} and after != 200, after
            if not fail_closed:
                assert after == 401, "with the store down and fail-open, the account check alone must refuse"

    _run(scenario())


def test_without_redis_a_disabled_account_loses_access_and_refresh_tokens(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "none") as env:
            user_id = await _login_user(env, "sec4_noredis", ["orgadmin"])
            login = await _login(env, "sec4_noredis")
            assert login.status_code == 200, login.text[:300]
            access = login.json()["access_token"]
            assert await _claims(env, access) == 200
            await env.db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"is_active": False}})
            assert await _claims(env, access) == 401
            assert (await _refresh(env, access)).status_code == 401

    _run(scenario())


def test_a_disabled_service_account_and_worker_principal_stop(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            from rbac_backend.core.security import StoredPrincipalUnavailableError, resolve_stored_principal

            token = _token("system_user")
            assert await _claims(env, token) != 401
            await resolve_stored_principal(env.db, "user-system_user")
            await env.db.users.update_one({"_id": "user-system_user"}, {"$set": {"disabled": True}})
            assert await _claims(env, token) == 401
            with pytest.raises(StoredPrincipalUnavailableError):
                await resolve_stored_principal(env.db, "user-system_user")

    _run(scenario())


# --------------------------------------------------------------------------- #
# SEC-1 / SEC-2 / SEC-3: the permission cache never serves stale or cross-tenant authority
# --------------------------------------------------------------------------- #


async def _cached(user_id: str) -> Optional[Dict[str, Any]]:
    import json

    from rbac_backend.services.permission_service import permission_cache_key
    from rbac_backend.services.runtime_state import get_runtime_state

    redis = await get_runtime_state().get_redis()
    raw = await redis.get(permission_cache_key(user_id))
    return json.loads(raw) if raw else None


async def _has(user_id: str, permission: str, **scope: Any) -> bool:
    from rbac_backend.services.permission_service import PermissionService

    return await PermissionService().user_has_permission(user_id, permission, log=False, **scope)


def test_a_cached_grant_never_crosses_the_project_boundary(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            await _seed_roles(env.db)
            dual = await _user(env.db, "sec_cache_dual", [ROLE_A], organization_id=ORG_A,
                               organizations=[ORG_A, ORG_B], projects=[PROJ_A1, PROJ_B1])
            assert await _has(dual, VIEW, project_id=PROJ_A1)
            entry = await _cached(dual)
            assert entry is not None, "the cache was not written: this test would prove nothing"
            assert all(grant["scope"] is None or grant["scope"].get("organization_id") for grant in entry["grants"])
            # Served from the cache now: still no project of B, no mismatched pair, no unknown project.
            assert not await _has(dual, VIEW, project_id=PROJ_B1)
            assert not await _has(dual, VIEW, organization_id=ORG_A, project_id=PROJ_B1)
            assert not await _has(dual, VIEW, project_id="no-such-project")
            # A project re-parented to B after caching is judged by where it is now.
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"organization_id": ORG_B}})
            assert not await _has(dual, VIEW, project_id=PROJ_A1)

    _run(scenario())


def test_revocation_and_membership_changes_reach_the_cache(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            await _seed_roles(env.db)
            super_headers = _step_up("superadmin", "user-superadmin")
            target = await _user(env.db, "sec_cache_target", ["orguser", ROLE_A], organization_id=ORG_A)
            assert await _has(target, VIEW) and await _cached(target) is not None

            # Role edited: the cached grant goes.
            edited = await env.client.put(f"/api/roles/{ROLE_A}", headers=super_headers, json={"permissions": [MANAGE]})
            assert edited.status_code == 200, edited.text[:300]
            assert not await _has(target, VIEW) and await _has(target, MANAGE)

            # Role revoked from the user (through the users API): gone on the next check.
            revoked = await env.client.put(f"/api/users/{target}", headers={"Authorization": f"Bearer {_token('superadmin')}"},
                                           json={"roles": ["orguser"]})
            assert revoked.status_code == 200, revoked.text[:300]
            assert not await _has(target, MANAGE)

            # Membership moved out of A: an A role held again grants nothing.
            await env.db.users.update_one({"_id": ObjectId(target)}, {"$set": {"roles": ["orguser", ROLE_A]}})
            from rbac_backend.services.permission_service import complete_authority_change

            await complete_authority_change([target])
            assert await _has(target, MANAGE)
            # A move that does not say which memberships the user keeps is refused (DEF-3).
            unclear = await env.client.put(f"/api/users/{target}", headers={"Authorization": f"Bearer {_token('superadmin')}"},
                                           json={"organization_id": ORG_B})
            assert unclear.status_code == 422, unclear.text[:300]
            assert await _has(target, MANAGE)
            moved = await env.client.put(f"/api/users/{target}", headers={"Authorization": f"Bearer {_token('superadmin')}"},
                                         json={"organization_id": ORG_B, "organizations": [ORG_B], "projects": []})
            assert moved.status_code == 200, moved.text[:300]
            stored = await env.db.users.find_one({"_id": ObjectId(target)})
            assert stored["organizations"] == [ORG_B], "DEF-3: the move must apply the memberships it names"
            assert not await _has(target, MANAGE), "a membership change left a cached grant"

    _run(scenario())


def test_concurrent_assignment_and_revocation_end_consistent(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            await _seed_roles(env.db)
            await env.db.roles.insert_one(_role("sec-race-b", [MANAGE], organization_id=ORG_B))
            target = await _user(env.db, "sec_cache_race", ["orguser"], organization_id=ORG_A)
            headers = {"Authorization": f"Bearer {_token('org_admin')}"}

            async def assign(roles: List[str]) -> int:
                return (await env.client.put(f"/api/users/{target}", headers=headers, json={"roles": roles})).status_code

            async def check() -> bool:
                return await _has(target, VIEW)

            results = await asyncio.gather(
                *[assign(["orguser", ROLE_A]) for _ in range(3)],
                *[assign(["orguser"]) for _ in range(3)],
                *[assign(["orguser", "sec-race-b"]) for _ in range(2)],
                *[check() for _ in range(6)],
            )
            assert all(code == 200 for code in results[:6]), results[:6]
            assert all(code == 403 for code in results[6:8]), results[6:8]
            stored = await env.db.users.find_one({"_id": ObjectId(target)})
            assert "sec-race-b" not in stored["roles"]
            # Whatever order the writes landed in, the next check agrees with the stored roles.
            assert await _has(target, VIEW) == (ROLE_A in stored["roles"])

    _run(scenario())


def test_deactivating_a_project_clears_its_members_cached_grants(monkeypatch) -> None:
    async def scenario() -> None:
        async with _redis_env(monkeypatch, "available") as env:
            from rbac_backend.services.project_service import ProjectService

            await env.db.roles.insert_one(_role("sec-cache-p", [VIEW], organization_id=ORG_A, project_id=PROJ_A1))
            member = await _user(env.db, "sec_cache_project", ["sec-cache-p"], organization_id=ORG_A, projects=[PROJ_A1])
            assert await _has(member, VIEW, project_id=PROJ_A1)
            assert await _cached(member) is not None
            service = ProjectService()
            service.db = env.db
            assert await service.delete_project(PROJ_A1, "user-superadmin")
            assert await _cached(member) is None, "the member's cached grant outlived the deactivation"
            assert not await _has(member, VIEW, project_id=PROJ_A1)
            # Members stay signed in: deactivating a project is not a credential change.
            from rbac_backend.services.runtime_state import get_runtime_state

            redis = await get_runtime_state().get_redis()
            assert await redis.get(f"user_jwt_min_iat:{member}") is None

    _run(scenario())
