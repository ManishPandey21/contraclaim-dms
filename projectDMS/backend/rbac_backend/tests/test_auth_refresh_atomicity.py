"""F-A8M-6 - a successful token refresh must not be reported as a 500.

R-A8M's Gate 3 bullet 1 measured ``POST /api/refresh`` returning 500 for every
user while the refresh had in fact succeeded: the access token was created and
the session extended, and only then did the audit call raise ``TypeError``
because ``AuthController.refresh_token`` passed one argument to a two-argument
helper. The broad ``except Exception`` below it turned that into
``500 "Token refresh service temporarily unavailable"``.

Two properties are pinned here, and they are different properties:

1. **The call is correct.** A valid refresh returns a token and emits exactly
   one ``token_refresh`` audit event carrying the caller's user id *and the
   session id being extended*. This is the instance.

2. **The class cannot come back.** ``AuditLogger`` is a wide surface of
   convenience wrappers called positionally from routers, so a second arity
   drift is a matter of time. ``test_audit_logger_call_arity.py`` binds every
   audit call site in the tree against the real signature.

**Audit-failure semantics.** ``AuditLogger._write_record`` already catches
persistence errors, logs a warning and returns ``False`` - "do not break request
flow" is the architecture's own stated policy, and no caller in the tree treats
a ``False`` return as fatal. So the required behaviour is (B): the refresh
succeeds, and an audit failure is recorded as an operational failure. What is
forbidden in every case is the observed shape - mutate the session, then answer
with an ambiguous 500.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import jwt
import pytest
from fastapi import HTTPException

from rbac_backend.core.config import settings
from rbac_backend.core.security import CurrentUser, create_access_token
from rbac_backend.routers.auth import AuthController
from rbac_backend.utils.audit_logger import AuditLogger
from rbac_backend.utils.error_handler import AuthenticationError

SESSION_ID = "session-a8m6"
OTHER_SESSION_ID = "session-someone-else"
USER_ID = "user-a8m6"


# --- Fakes ----------------------------------------------------------------


class _RecordingAuditLogger(AuditLogger):
    """The real logger with its one persistence seam replaced.

    Subclassing rather than duck-typing is deliberate: a stand-in with its own
    ``log_token_refreshed`` would accept whatever the caller passed and the
    arity defect would be invisible, which is exactly how it reached staging.
    """

    def __init__(self, *, write_result: bool = True, write_raises: Optional[BaseException] = None):
        super().__init__()
        self.records: list = []
        self._write_result = write_result
        self._write_raises = write_raises

    async def _write_record(self, record):  # type: ignore[override]
        self.records.append(record)
        if self._write_raises is not None:
            raise self._write_raises
        return self._write_result

    def events(self, event_type: str) -> list:
        return [r for r in self.records if r.event_type == event_type]


class _FakeRateLimiter:
    def __init__(self):
        self.calls = []

    async def check_user_limit(self, user_id, cost=1, **_kwargs):
        self.calls.append((user_id, cost))


class _FakeAuthService:
    def __init__(self, *, active_sessions=(SESSION_ID,)):
        self._active = set(active_sessions)
        self.extended: list = []

    async def is_session_active(self, session_id):
        return session_id in self._active

    async def extend_session(self, session_id, expires_delta):
        self.extended.append((session_id, expires_delta))
        return True


class _FakeUserService:
    def __init__(self, user=None):
        self._user = user

    async def get_user_by_id(self, user_id):
        if self._user is None:
            return None
        return self._user if str(self._user.id) == str(user_id) else None


def _user(disabled: bool = False):
    return SimpleNamespace(
        id=USER_ID,
        email="a8m6@example.com",
        roles=["orguser"],
        disabled=disabled,
    )


def _current_user() -> CurrentUser:
    return CurrentUser(
        id=USER_ID,
        username="a8m6",
        email="a8m6@example.com",
        roles=["orguser"],
        organization_id="org-A",
    )


def _token(session_id: Optional[str] = SESSION_ID) -> str:
    data = {"sub": "a8m6@example.com", "user_id": USER_ID, "type": "access"}
    if session_id is not None:
        data["session_id"] = session_id
    return create_access_token(data=data)


def _controller(
    *,
    audit=None,
    auth_service=None,
    user_service=None,
    rate_limiter=None,
):
    return AuthController(
        auth_service or _FakeAuthService(),
        user_service or _FakeUserService(_user()),
        SimpleNamespace(),
        rate_limiter or _FakeRateLimiter(),
        audit or _RecordingAuditLogger(),
    )


def _status_of(error) -> Optional[int]:
    return getattr(error, "http_status", None) or getattr(error, "status_code", None)


# --- 1. The success path is atomic ----------------------------------------


async def test_valid_refresh_returns_a_token_and_does_not_raise():
    """RED before the fix: the audit call raises TypeError, which the broad
    ``except Exception`` converts into HTTP 500 with a mutated session."""
    auth_service = _FakeAuthService()
    controller = _controller(auth_service=auth_service)

    result = await controller.refresh_token(_current_user(), _token())

    assert result.access_token
    assert result.token_type == "bearer"
    assert result.expires_in == 3600
    # the mutation happened exactly once, and the caller was told so
    assert [s for s, _ in auth_service.extended] == [SESSION_ID]


async def test_valid_refresh_emits_exactly_one_audit_event_with_the_right_identity():
    audit = _RecordingAuditLogger()
    controller = _controller(audit=audit)

    await controller.refresh_token(_current_user(), _token())

    events = audit.events("token_refresh")
    assert len(events) == 1, f"expected exactly one token_refresh event, got {len(events)}"
    assert events[0].user_id == USER_ID
    assert events[0].resource_id == SESSION_ID, (
        "the audit event must name the session that was actually extended"
    )
    assert audit.events("token_refresh_failed") == []


async def test_the_audited_session_is_the_extended_session_not_another():
    """Negative control: an event that names a different session is a wrong
    audit identity even though the refresh itself is correct."""
    auth_service = _FakeAuthService()
    audit = _RecordingAuditLogger()
    controller = _controller(audit=audit, auth_service=auth_service)

    await controller.refresh_token(_current_user(), _token())

    extended_session = auth_service.extended[0][0]
    assert audit.events("token_refresh")[0].resource_id == extended_session
    assert audit.events("token_refresh")[0].resource_id != OTHER_SESSION_ID


async def test_the_new_token_carries_the_same_session_and_user():
    """Session fixation control: refresh extends the caller's own session; it
    neither mints a new session id nor adopts one from anywhere else."""
    controller = _controller()
    result = await controller.refresh_token(_current_user(), _token())

    payload = jwt.decode(
        result.access_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
    )
    assert payload["session_id"] == SESSION_ID
    assert payload["user_id"] == USER_ID
    assert payload["sub"] == "a8m6@example.com"


# --- 2. Audit failure must not un-do a completed refresh -------------------


async def test_audit_persistence_failure_does_not_turn_a_completed_refresh_into_500():
    """Policy (B): ``_write_record`` already swallows persistence errors by
    design. A store that is merely unavailable must not fail the request."""
    audit = _RecordingAuditLogger(write_result=False)
    auth_service = _FakeAuthService()
    controller = _controller(audit=audit, auth_service=auth_service)

    result = await controller.refresh_token(_current_user(), _token())

    assert result.access_token
    assert [s for s, _ in auth_service.extended] == [SESSION_ID]


async def test_an_exception_from_the_audit_path_never_becomes_a_500_after_mutation():
    """The load-bearing property. Whatever the audit layer throws - the original
    ``TypeError``, or a driver error - the session has already been extended, so
    answering 500 tells the client the opposite of the truth."""
    auth_service = _FakeAuthService()
    audit = _RecordingAuditLogger(write_raises=RuntimeError("audit store exploded"))
    controller = _controller(audit=audit, auth_service=auth_service)

    result = await controller.refresh_token(_current_user(), _token())

    assert result.access_token
    assert [s for s, _ in auth_service.extended] == [SESSION_ID]


async def test_a_typeerror_from_the_audit_call_is_not_reported_as_service_unavailable():
    """The exact R-A8M shape, reconstructed: an audit helper that rejects the
    call it is given. Before the fix this is what production did on every
    refresh."""

    class _WrongAritySurface(_RecordingAuditLogger):
        async def log_token_refreshed(self, user_id, session_id, **details):  # type: ignore[override]
            raise TypeError(
                "log_token_refreshed() missing 1 required positional argument: 'session_id'"
            )

    auth_service = _FakeAuthService()
    controller = _controller(audit=_WrongAritySurface(), auth_service=auth_service)

    result = await controller.refresh_token(_current_user(), _token())

    assert result.access_token, "a refresh that extended the session must return the token"
    assert [s for s, _ in auth_service.extended] == [SESSION_ID]


# --- 3. Negative controls: refusal still refuses ---------------------------


async def _refusal(controller, token, current_user=None):
    with pytest.raises((AuthenticationError, HTTPException)) as excinfo:
        await controller.refresh_token(current_user or _current_user(), token)
    return excinfo.value


async def test_a_token_with_no_session_claim_is_refused_401_and_nothing_is_extended():
    auth_service = _FakeAuthService()
    audit = _RecordingAuditLogger()
    controller = _controller(audit=audit, auth_service=auth_service)

    error = await _refusal(controller, _token(session_id=None))

    assert _status_of(error) == 401
    assert auth_service.extended == []
    assert audit.events("token_refresh") == []
    assert len(audit.events("token_refresh_failed")) == 1


async def test_an_expired_or_revoked_session_is_refused_and_nothing_is_extended():
    auth_service = _FakeAuthService(active_sessions=())
    audit = _RecordingAuditLogger()
    controller = _controller(audit=audit, auth_service=auth_service)

    error = await _refusal(controller, _token())

    assert _status_of(error) == 401
    assert auth_service.extended == []
    assert audit.events("token_refresh") == []
    assert len(audit.events("token_refresh_failed")) == 1


async def test_a_disabled_user_is_refused_and_nothing_is_extended():
    auth_service = _FakeAuthService()
    audit = _RecordingAuditLogger()
    controller = _controller(
        audit=audit,
        auth_service=auth_service,
        user_service=_FakeUserService(_user(disabled=True)),
    )

    error = await _refusal(controller, _token())

    assert _status_of(error) == 401
    assert auth_service.extended == []
    assert audit.events("token_refresh") == []


async def test_a_principal_with_no_stored_user_is_refused():
    auth_service = _FakeAuthService()
    controller = _controller(auth_service=auth_service, user_service=_FakeUserService(None))

    error = await _refusal(controller, _token())

    assert _status_of(error) == 401
    assert auth_service.extended == []


async def test_a_token_that_is_not_ours_is_refused_rather_than_trusted():
    """The session id is read from a signed token. A token signed with another
    key yields no session claim, so the request is refused - it must never fall
    through to the caller's own session."""
    foreign = jwt.encode(
        {"sub": "a8m6@example.com", "user_id": USER_ID, "session_id": OTHER_SESSION_ID},
        "not-the-signing-key",
        algorithm="HS256",
    )
    auth_service = _FakeAuthService()
    controller = _controller(auth_service=auth_service)

    error = await _refusal(controller, foreign)

    assert _status_of(error) == 401
    assert auth_service.extended == []


async def test_rate_limiting_runs_before_any_session_work():
    """Replay control: the refresh path is rate limited per user, and the limit
    is consulted before the session is looked at, so a replay storm cannot
    extend a session once per attempt."""

    class _Refusing(_FakeRateLimiter):
        async def check_user_limit(self, user_id, cost=1, **_kwargs):
            raise HTTPException(status_code=429, detail="rate limited")

    auth_service = _FakeAuthService()
    controller = _controller(auth_service=auth_service, rate_limiter=_Refusing())

    error = await _refusal(controller, _token())

    assert _status_of(error) == 429
    assert auth_service.extended == []


async def test_the_refresh_is_rate_limited_at_the_documented_cost():
    limiter = _FakeRateLimiter()
    controller = _controller(rate_limiter=limiter)

    await controller.refresh_token(_current_user(), _token())

    assert limiter.calls == [(USER_ID, 2)]


# --- 4. Replay, and the status the client actually sees --------------------


async def test_a_second_refresh_on_the_same_session_still_succeeds():
    """Contract item 7: repeated refresh behaviour is unchanged.

    Refresh is idempotent by design - it extends the session it was given and
    issues a token for it. Two refreshes must therefore both succeed, extend the
    same session, and emit one audit event each. Anything else would be a
    behaviour change smuggled in beside the fix.
    """
    auth_service = _FakeAuthService()
    audit = _RecordingAuditLogger()
    controller = _controller(audit=audit, auth_service=auth_service)

    first = await controller.refresh_token(_current_user(), _token())
    second = await controller.refresh_token(_current_user(), _token())

    assert first.access_token and second.access_token
    assert [s for s, _ in auth_service.extended] == [SESSION_ID, SESSION_ID]
    assert len(audit.events("token_refresh")) == 2
    assert {event.resource_id for event in audit.events("token_refresh")} == {SESSION_ID}


async def test_a_replayed_refresh_after_logout_is_refused():
    """The other half of replay: the same token, presented after the session was
    invalidated, must not extend anything."""
    auth_service = _FakeAuthService()
    controller = _controller(auth_service=auth_service)
    replayed = _token()

    await controller.refresh_token(_current_user(), replayed)

    auth_service._active.clear()  # what logout does
    error = await _refusal(controller, replayed)

    assert _status_of(error) == 401
    assert len(auth_service.extended) == 1, "a replay after logout extended the session"


def test_the_route_returns_200_and_not_500_for_the_controllers_result():
    """Contract item 1 is about the HTTP status, and the handler is where a
    500 was manufactured. The route body is bound and inspected rather than
    executed, because executing it needs the whole dependency stack - what it
    has to show is that the controller's return value is the response and that
    nothing between them can turn a value into a failure.
    """
    import inspect

    from rbac_backend.routers import auth as auth_router

    source = inspect.getsource(auth_router.refresh_token)

    assert "await controller.refresh_token(current_user, token)" in source
    assert "return token_response" in source
    # Only the controller and the cookie helper stand between the result and the
    # client; neither raises on a successful refresh.
    assert "_set_auth_cookie(response, token_response.access_token" in source
    assert "500" not in source


def test_the_controller_cannot_reach_its_own_500_on_a_successful_refresh():
    """The mechanism, pinned where it lives.

    Everything that may fail the request happens before `extend_session`; the
    only call after it goes through `_audit_token_refreshed`, which cannot
    raise. A future edit that puts a raising call between the mutation and the
    return re-opens F-A8M-6, and this is what notices.
    """
    import inspect

    source = inspect.getsource(AuthController.refresh_token)
    after_mutation = source.split("extend_session", 1)[1]
    awaited = [
        line.strip()
        for line in after_mutation.splitlines()
        if line.strip().startswith("await ")
    ]

    assert awaited == ["await self._audit_token_refreshed(current_user.id, session_id)"], (
        "a new call was added between the session mutation and the response; "
        "if it can raise, a completed refresh answers 500 again"
    )
