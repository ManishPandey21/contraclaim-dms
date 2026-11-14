"""C2: session revocation fails closed when the session store is unreachable.

Revocation state (logout, forced JWT invalidation, lockout) lives in the
runtime Redis. Before this change, an unreachable Redis silently skipped those
checks, so a revoked session kept authenticating until token expiry. These
tests pin the policy: store configured + unreachable -> 503 (default), or an
explicit fail-open via AUTH_SESSION_FAIL_CLOSED=false; store not configured ->
legacy behavior (checks skipped) so Redis-less dev setups keep working.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core import security
from rbac_backend.core.config import settings
from rbac_backend.core.security import create_access_token, get_current_user
from rbac_backend.services import runtime_state as runtime_state_module


USER_DOC = {
    "_id": "64b7f0c2a1b2c3d4e5f60718",
    "email": "ada@example.com",
    "username": "ada",
    "roles": ["orguser"],
    "organization_id": "org-A",
    "projects": [],
    "disabled": False,
}


class _FakeUsers:
    async def find_one(self, query):
        if query.get("email") == USER_DOC["email"]:
            return dict(USER_DOC)
        return None


class _NoRevokedRoles:
    """`get_current_user` asks for soft-deleted role references (R-A9B); none here."""

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


class _FakeDB:
    users = _FakeUsers()
    roles = _NoRevokedRoles()


def _request_with_token(token: str):
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"}, cookies={})


def _token(**claims) -> str:
    return create_access_token({"sub": USER_DOC["email"], **claims})


class _DownRedisState:
    """Configured store that cannot produce a client (outage)."""

    redis_url = "redis://session-store:6379/1"

    async def get_redis(self):
        return None


class _ExplodingRedis:
    async def get(self, *_a, **_k):
        raise ConnectionError("redis died mid-call")


class _FlakyRedisState:
    """Configured store whose client fails mid-call (stale connection)."""

    redis_url = "redis://session-store:6379/1"

    async def get_redis(self):
        return _ExplodingRedis()


class _UnconfiguredState:
    redis_url = None

    async def get_redis(self):
        return None


def _use_state(monkeypatch, state):
    monkeypatch.setattr(runtime_state_module, "get_runtime_state", lambda: state)


@pytest.fixture(autouse=True)
def _known_secret(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret-key-for-session-fail-closed-tests")
    yield


# --- fail-closed (default) ----------------------------------------------------


@pytest.mark.asyncio
async def test_store_configured_but_down_denies_with_503(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
    _use_state(monkeypatch, _DownRedisState())
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request_with_token(_token(session_id="s-1")), db=_FakeDB())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_mid_call_redis_failure_also_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
    _use_state(monkeypatch, _FlakyRedisState())
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request_with_token(_token(session_id="s-1")), db=_FakeDB())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_tokens_without_session_id_also_fail_closed(monkeypatch):
    # min-iat forced invalidation applies to ALL tokens, so legacy tokens
    # without a session_id must not slip through during an outage either.
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
    _use_state(monkeypatch, _DownRedisState())
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_request_with_token(_token()), db=_FakeDB())
    assert exc.value.status_code == 503


# --- explicit fail-open ---------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_open_flag_authenticates_with_checks_skipped(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", False)
    _use_state(monkeypatch, _DownRedisState())
    user = await get_current_user(_request_with_token(_token(session_id="s-1")), db=_FakeDB())
    assert user.email == USER_DOC["email"]


# --- store not configured: legacy behavior --------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_store_keeps_legacy_behavior(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
    _use_state(monkeypatch, _UnconfiguredState())
    user = await get_current_user(_request_with_token(_token(session_id="s-1")), db=_FakeDB())
    assert user.email == USER_DOC["email"]


@pytest.mark.asyncio
async def test_no_credentials_is_still_401_not_503(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", False)
    _use_state(monkeypatch, _DownRedisState())
    request = SimpleNamespace(headers={}, cookies={})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, db=_FakeDB())
    assert exc.value.status_code == 401
