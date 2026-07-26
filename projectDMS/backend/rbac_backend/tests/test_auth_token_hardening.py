"""Access-token hardening (Week 2.4 + 2.5).

W2.4 — every access token carries ``type="access"``; tokens that are not access
tokens (notably step-up tokens, which share the signing key) must never
authenticate a session.

W2.5 — a logged-out / invalidated session must stop authenticating immediately,
even within the token TTL. ``get_current_user`` checks session liveness when a
Redis session store is present.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import rbac_backend.services.authentication_service as auth_mod
import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.security import CurrentUser, create_access_token, get_current_user
from rbac_backend.services.authentication_service import AuthenticationService
from rbac_backend.services.step_up_service import StepUpService
import jwt
from rbac_backend.core.config import settings


# --- Fakes ----------------------------------------------------------------


class _FakeUsers:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _query):
        return self._doc


class _FakeDB:
    def __init__(self, user_doc):
        self.users = _FakeUsers(user_doc)


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class _FakeRedis:
    def __init__(self, *, session_exists: bool):
        self._session_exists = session_exists

    async def get(self, _key):
        return None  # no min_iat floor configured

    async def exists(self, _key):
        return 1 if self._session_exists else 0


class _FakeRuntime:
    def __init__(self, redis):
        self._redis = redis

    @property
    def redis_url(self):
        return "redis://test-runtime"

    async def get_redis(self):
        return self._redis


_USER_DOC = {
    "_id": "user-1",
    "email": "a@example.com",
    "username": "a",
    "roles": ["orguser"],
    "organization_id": "org-A",
    "projects": ["proj-A"],
}


def _patch_runtime(monkeypatch, redis):
    runtime = _FakeRuntime(redis)
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(auth_mod, "get_runtime_state", lambda: runtime)


# --- W2.4: JWT type claim -------------------------------------------------


def test_access_token_is_stamped_with_type_access():
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert payload["type"] == "access"


# --- Regression: min_iat invalidation must not nuke freshly-minted tokens -----


class _MinIatRedis:
    """Redis fake exposing a ``user_jwt_min_iat`` floor and an active session."""

    def __init__(self, min_iat: int):
        self._min_iat = str(int(min_iat)).encode()

    async def get(self, key):
        return self._min_iat if "user_jwt_min_iat" in str(key) else None

    async def exists(self, _key):
        return 1  # session is active; isolate the min_iat behaviour


def test_access_token_is_stamped_with_iat():
    # Without an iat claim, get_current_user reads iat=0 and any min_iat marker
    # rejects every token. The token must carry an issued-at timestamp.
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert "iat" in payload
    assert abs(int(payload["iat"]) - int(time.time())) < 60


@pytest.mark.asyncio
async def test_fresh_token_survives_past_min_iat_floor(monkeypatch):
    # A min_iat marker set an hour ago must NOT reject a token minted now.
    past_floor = int(time.time()) - 3600
    _patch_runtime(monkeypatch, _MinIatRedis(past_floor))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)


@pytest.mark.asyncio
async def test_token_predating_min_iat_floor_is_rejected(monkeypatch):
    # Invalidation still works: a token issued before the floor is rejected.
    future_floor = int(time.time()) + 3600
    _patch_runtime(monkeypatch, _MinIatRedis(future_floor))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_step_up_token_cannot_authenticate_session():
    step_up = StepUpService().create_token(user_id="user-1", action="*")
    request = _FakeRequest(headers={"authorization": f"Bearer {step_up}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_access_type_token_is_rejected():
    forged = jwt.encode(
        {"sub": "a@example.com", "type": "refresh"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {forged}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_access_token_authenticates_without_redis(monkeypatch):
    runtime = SimpleNamespace(redis_url=None)
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"]})
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    # No Redis configured -> session/min_iat checks are skipped.
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)
    assert result.email == "a@example.com"


# --- W2.5: session invalidation -------------------------------------------


@pytest.mark.asyncio
async def test_session_lifecycle_invalidate_makes_inactive():
    svc = AuthenticationService()
    svc._runtime_state = _FakeRuntime(_InMemoryRedis())
    token = await svc.create_user_session("user-1", "1.2.3.4", "agent", timedelta(minutes=60))
    assert await svc.is_session_active(token) is True
    await svc.invalidate_session(token)
    assert await svc.is_session_active(token) is False


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalidated_session(monkeypatch):
    _patch_runtime(monkeypatch, _FakeRedis(session_exists=False))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_accepts_active_session(monkeypatch):
    _patch_runtime(monkeypatch, _FakeRedis(session_exists=True))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)


class _InMemoryRedis:
    """A fuller fake used for the session-lifecycle test."""

    def __init__(self):
        self.store = {}
        self.sets = {}

    async def setex(self, key, _ttl, val):
        self.store[key] = val

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def sadd(self, key, *vals):
        self.sets.setdefault(key, set()).update(vals)

    async def expire(self, _key, _ttl):
        return None

    async def srem(self, key, *vals):
        self.sets.get(key, set()).difference_update(vals)
