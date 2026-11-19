"""DEF-5: users whose ``_id`` is not an ObjectId (regression tests; fix deferred until P-05).

Reproduced on 2026-11-19 with real login: a user stored under a string id can sign in,
but ``GET /api/users/me`` answers 422 ("Invalid ObjectId format"), and ``GET /api/me``
and ``POST /api/refresh`` answer 401 ("User account is no longer active"), because
``UserService._validate_user_id`` and ``routers.users.validate_object_id`` accept ObjectIds
only. Seeded and API-created users get ObjectIds; whether production holds string ids
is P-05's ``users_with_a_non_objectid_id`` reading, not yet taken.

The owner's instruction (2026-11-19): regression tests and a backwards-compatible design
now; no change to stored users or authentication until P-05 is read. So:

- the tests that state the intended behaviour are ``xfail(strict=True)``: they fail
  today as recorded, and turn red (XPASS) the moment a fix lands, so the fix must
  update them deliberately;
- the baseline tests (ObjectId users unaffected; a string-id user is never resolved to
  anyone else) pass today and must keep passing after the fix.

Design: docs/SECURITY_REMEDIATION_2026-11-19.md, "DEF-5 design".
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env

pytestmark = pytest.mark.integration

PASSWORD = "Correct-Horse-Battery-9"
STRING_ID = "legacy-user-1"
DEF5 = "DEF-5: string user ids are refused (fix deferred until the P-05 reading; see the module docstring)"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _insert(env: Any, label: str, user_id: Any) -> None:
    from rbac_backend.core.security import get_password_hash

    await env.db.users.insert_one(
        {"_id": user_id, "email": f"{label}@example.com", "username": label, "first_name": "Legacy", "last_name": "User",
         "hashed_password": get_password_hash(PASSWORD), "disabled": False, "account_type": "client_user",
         "roles": ["orguser"], "organization_id": "org-a", "organizations": ["org-a"], "projects": []}
    )


async def _login(env: Any, label: str) -> Dict[str, str]:
    response = await env.client.post("/api/login", json={"email": f"{label}@example.com", "password": PASSWORD})
    env.client.cookies.clear()  # bearer-only calls (a stored cookie would bring CSRF in)
    assert response.status_code == 200, response.text[:300]
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _call(env: Any, method: str, path: str, headers: Dict[str, str]) -> Any:
    response = await env.client.request(method, path, headers=headers)
    env.client.cookies.clear()
    return response


def test_object_id_users_are_unaffected(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            oid = ObjectId()
            await _insert(env, "modern", oid)
            headers = await _login(env, "modern")
            for path in ("/api/users/me", "/api/me"):
                response = await _call(env, "GET", path, headers)
                assert response.status_code == 200 and response.json()["id"] == str(oid), (path, response.text[:200])
            refresh = await _call(env, "POST", "/api/refresh", headers)
            assert "no longer active" not in refresh.text, refresh.text[:200]

    _run(scenario())


def test_a_string_id_user_is_never_resolved_to_another_user(monkeypatch) -> None:
    """Whatever the fix, a string id must never match a different stored user, including an
    ObjectId user whose hex happens to equal it."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _insert(env, "legacy", STRING_ID)
            twin_hex = "64b0000000000000000000aa"
            await _insert(env, "twin_object", ObjectId(twin_hex))
            headers = await _login(env, "legacy")
            for path in ("/api/users/me", "/api/me"):
                response = await _call(env, "GET", path, headers)
                if response.status_code == 200:
                    assert response.json()["id"] == STRING_ID, (path, response.json())
            check = await env.client.post("/api/permissions/check", headers=headers, json={"permission": "users:read"})
            assert check.status_code == 200 and check.json()["user_id"] == STRING_ID

    _run(scenario())


def test_def5_signature_today(monkeypatch) -> None:
    """Pins the recorded behaviour, so a change to it is seen (passes today)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _insert(env, "legacy", STRING_ID)
            headers = await _login(env, "legacy")
            me = await _call(env, "GET", "/api/users/me", headers)
            auth_me = await _call(env, "GET", "/api/me", headers)
            refresh = await _call(env, "POST", "/api/refresh", headers)
            assert (me.status_code, auth_me.status_code, refresh.status_code) == (422, 401, 401)
            assert "Invalid ObjectId format" in me.text
            assert "no longer active" in auth_me.text and "no longer active" in refresh.text

    _run(scenario())


@pytest.mark.xfail(strict=True, reason=DEF5)
def test_a_string_id_user_reads_their_profile(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _insert(env, "legacy", STRING_ID)
            headers = await _login(env, "legacy")
            response = await _call(env, "GET", "/api/users/me", headers)
            assert response.status_code == 200 and response.json()["id"] == STRING_ID, response.text[:200]

    _run(scenario())


@pytest.mark.xfail(strict=True, reason=DEF5)
def test_a_string_id_user_is_recognised_by_auth_me(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _insert(env, "legacy", STRING_ID)
            headers = await _login(env, "legacy")
            response = await _call(env, "GET", "/api/me", headers)
            assert response.status_code == 200 and response.json()["id"] == STRING_ID, response.text[:200]

    _run(scenario())


@pytest.mark.xfail(strict=True, reason=DEF5)
def test_a_string_id_user_is_not_called_inactive_on_refresh(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _insert(env, "legacy", STRING_ID)
            headers = await _login(env, "legacy")
            refresh = await _call(env, "POST", "/api/refresh", headers)
            assert "no longer active" not in refresh.text, refresh.text[:200]

    _run(scenario())
