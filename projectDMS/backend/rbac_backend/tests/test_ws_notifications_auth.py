"""Authentication of the notifications WebSocket, ``/ws/notifications``.

The socket used to check only the JWT signature and expiry, then key the
connection on ``str(payload.get("user_id") or payload.get("sub"))``. It never ran
the checks the HTTP path (``core.security.get_current_user``) applies to an
access token:

* token type - a step-up token or any other signed non-access token connected;
* revocation - a token below the ``user_jwt_min_iat`` floor, or one whose session
  was logged out or invalidated, kept connecting until it expired;
* account - no user lookup at all, so an unknown, deleted or disabled account
  connected;
* identity - a signed token with neither claim connected under the key ``"None"``.

The contract pinned here: a token the HTTP path would refuse cannot open the
socket, an account the HTTP path marks disabled cannot either, and the connection
is keyed by the resolved principal's id. A refusal closes with 4401 before the
socket is accepted and registers nothing.

Every case runs through the real route, the real token checks and the real
``ConnectionManager``; only Mongo and the runtime Redis are faked.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import rbac_backend.services.authentication_service as auth_mod
import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.config import settings
from rbac_backend.core.database import get_db
from rbac_backend.core.security import create_access_token
from rbac_backend.dependencies import get_notification_service
from rbac_backend.main import app
from rbac_backend.services.step_up_service import StepUpService
from rbac_backend.utils.notification_service import ConnectionManager

USER_ID = "user-ws-1"
EMAIL = "ws-user@example.com"
UNAUTHENTICATED = 4401

_ACTIVE_USER: Dict[str, Any] = {
    "_id": USER_ID,
    "email": EMAIL,
    "username": "ws-user",
    "roles": ["orguser"],
    "organization_id": "org-a",
    "projects": [],
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _Users:
    def __init__(self, docs):
        self._docs = list(docs)

    async def find_one(self, query):
        for doc in self._docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None


class _NoRoleDocuments:
    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


class _Db:
    def __init__(self, users):
        self.users = _Users(users)
        self.roles = _NoRoleDocuments()


class _Redis:
    def __init__(self, *, min_iat: Optional[int] = None, live_sessions=()):
        self._min_iat = min_iat
        self._live = set(live_sessions)

    async def get(self, key):
        if "user_jwt_min_iat" in str(key) and self._min_iat is not None:
            return str(self._min_iat)
        return None

    async def exists(self, key):
        return 1 if any(str(key).endswith(s) for s in self._live) else 0


class _Runtime:
    def __init__(self, redis):
        self._redis = redis

    @property
    def redis_url(self):
        return "redis://test-runtime"

    async def get_redis(self):
        return self._redis


class _Notifications:
    def __init__(self):
        self.manager = ConnectionManager()


@contextmanager
def _socket_app(monkeypatch, *, users=(_ACTIVE_USER,), redis: Optional[_Redis] = None) -> Iterator[_Notifications]:
    runtime = _Runtime(redis) if redis is not None else type("NoRedis", (), {"redis_url": None})()
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(auth_mod, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", False)
    notifications = _Notifications()
    db = _Db(users)
    app.dependency_overrides[get_notification_service] = lambda: notifications
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield notifications
    finally:
        app.dependency_overrides.pop(get_notification_service, None)
        app.dependency_overrides.pop(get_db, None)


def _access_token(**extra) -> str:
    data = {"sub": EMAIL, "user_id": USER_ID, "roles": ["orguser"]}
    data.update(extra)
    return create_access_token(data)


def _signed(payload: Dict[str, Any]) -> str:
    now = int(time.time())
    body = {"iat": now, "exp": now + 300}
    body.update(payload)
    return jwt.encode(body, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def _connects(notifications: _Notifications, url: str, cookies=None) -> bool:
    """Open the socket, prove it is live with ping/pong, return whether it was accepted."""
    client = TestClient(app)
    if cookies:
        client.cookies.update(cookies)
    with client.websocket_connect(url) as socket:
        socket.send_text("ping")
        assert socket.receive_text() == "pong"
        return True


def _refused(notifications: _Notifications, url: str, cookies=None) -> None:
    client = TestClient(app)
    if cookies:
        client.cookies.update(cookies)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(url) as socket:
            socket.send_text("ping")
            socket.receive_text()
    assert exc.value.code == UNAUTHENTICATED
    assert dict(notifications.manager._connections) == {}


# ---------------------------------------------------------------------------
# Valid authentication keeps working
# ---------------------------------------------------------------------------
def test_valid_access_token_connects_under_the_principals_id(monkeypatch) -> None:
    with _socket_app(monkeypatch) as notifications:
        client = TestClient(app)
        with client.websocket_connect(f"/ws/notifications?token={_access_token()}") as socket:
            socket.send_text("ping")
            assert socket.receive_text() == "pong"
            assert set(notifications.manager._connections) == {USER_ID}
        # Disconnect cleans the entry up.
        assert dict(notifications.manager._connections) == {}


def test_auth_cookie_path_still_connects(monkeypatch) -> None:
    with _socket_app(monkeypatch) as notifications:
        assert _connects(
            notifications, "/ws/notifications", cookies={settings.AUTH_COOKIE_NAME: _access_token()}
        )


def test_active_session_connects(monkeypatch) -> None:
    with _socket_app(monkeypatch, redis=_Redis(live_sessions={"sess-live"})) as notifications:
        assert _connects(notifications, f"/ws/notifications?token={_access_token(session_id='sess-live')}")


def test_token_without_user_id_claim_keys_on_the_resolved_principal(monkeypatch) -> None:
    """Older tokens carry only ``sub`` (the email); the key must still be the user id."""
    token = _signed({"sub": EMAIL, "type": "access"})
    with _socket_app(monkeypatch) as notifications:
        client = TestClient(app)
        with client.websocket_connect(f"/ws/notifications?token={token}") as socket:
            socket.send_text("ping")
            assert socket.receive_text() == "pong"
            assert set(notifications.manager._connections) == {USER_ID}


def test_repeated_connections_share_one_key_and_clean_up(monkeypatch) -> None:
    with _socket_app(monkeypatch) as notifications:
        client = TestClient(app)
        url = f"/ws/notifications?token={_access_token()}"
        with client.websocket_connect(url) as first, client.websocket_connect(url) as second:
            for socket in (first, second):
                socket.send_text("ping")
                assert socket.receive_text() == "pong"
            assert len(notifications.manager._connections[USER_ID]) == 2
        assert dict(notifications.manager._connections) == {}


# ---------------------------------------------------------------------------
# Token type
# ---------------------------------------------------------------------------
def test_step_up_token_is_refused(monkeypatch) -> None:
    step_up = StepUpService().create_token(user_id=USER_ID, action="*")
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={step_up}")


@pytest.mark.parametrize("token_type", ["refresh", "password_reset", "invite"])
def test_signed_non_access_token_is_refused(monkeypatch, token_type) -> None:
    token = _signed({"sub": EMAIL, "user_id": USER_ID, "type": token_type})
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")


def test_step_up_token_in_the_cookie_is_refused(monkeypatch) -> None:
    step_up = StepUpService().create_token(user_id=USER_ID, action="*")
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, "/ws/notifications", cookies={settings.AUTH_COOKIE_NAME: step_up})


# ---------------------------------------------------------------------------
# Revocation and session state
# ---------------------------------------------------------------------------
def test_token_below_the_revocation_floor_is_refused(monkeypatch) -> None:
    floor = int(time.time()) + 3600
    with _socket_app(monkeypatch, redis=_Redis(min_iat=floor)) as notifications:
        _refused(notifications, f"/ws/notifications?token={_access_token()}")


def test_logged_out_session_is_refused(monkeypatch) -> None:
    with _socket_app(monkeypatch, redis=_Redis(live_sessions=())) as notifications:
        _refused(notifications, f"/ws/notifications?token={_access_token(session_id='sess-gone')}")


# ---------------------------------------------------------------------------
# Account validity
# ---------------------------------------------------------------------------
def test_unknown_or_deleted_account_is_refused(monkeypatch) -> None:
    with _socket_app(monkeypatch, users=()) as notifications:
        _refused(notifications, f"/ws/notifications?token={_access_token()}")


def test_disabled_account_is_refused(monkeypatch) -> None:
    disabled = dict(_ACTIVE_USER, disabled=True)
    with _socket_app(monkeypatch, users=(disabled,)) as notifications:
        _refused(notifications, f"/ws/notifications?token={_access_token()}")


# ---------------------------------------------------------------------------
# Identity claim
# ---------------------------------------------------------------------------
def test_token_naming_nobody_is_refused_and_never_keyed_none(monkeypatch) -> None:
    token = _signed({"type": "access"})
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")
        assert "None" not in notifications.manager._connections


def test_token_with_only_a_user_id_claim_is_refused(monkeypatch) -> None:
    """HTTP builds the principal from ``sub``; a bare ``user_id`` names nobody there."""
    token = _signed({"user_id": USER_ID, "type": "access"})
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
def test_missing_or_malformed_token_is_refused(monkeypatch, token) -> None:
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")


def test_expired_token_is_refused(monkeypatch) -> None:
    now = int(time.time())
    token = jwt.encode(
        {"sub": EMAIL, "user_id": USER_ID, "type": "access", "iat": now - 600, "exp": now - 300},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")


def test_token_signed_with_another_key_is_refused(monkeypatch) -> None:
    now = int(time.time())
    token = jwt.encode(
        {"sub": EMAIL, "user_id": USER_ID, "type": "access", "iat": now, "exp": now + 300},
        "a-different-key-that-is-long-enough-123",
        algorithm=settings.ALGORITHM,
    )
    with _socket_app(monkeypatch) as notifications:
        _refused(notifications, f"/ws/notifications?token={token}")


# ---------------------------------------------------------------------------
# Session-store outage: an outage, not a silent pass and not a 4401
# ---------------------------------------------------------------------------
class _UnreachableRuntime:
    redis_url = "redis://test-runtime"

    async def get_redis(self):
        return None


def test_session_store_outage_closes_try_again_later(monkeypatch) -> None:
    with _socket_app(monkeypatch) as notifications:
        runtime = _UnreachableRuntime()
        monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
        monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", True)
        client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/notifications?token={_access_token()}") as socket:
                socket.send_text("ping")
                socket.receive_text()
        assert exc.value.code == 1013
        assert dict(notifications.manager._connections) == {}
