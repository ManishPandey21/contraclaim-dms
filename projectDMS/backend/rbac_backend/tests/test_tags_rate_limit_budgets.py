"""Tags rate-limit budgets: reads and writes must not share one bucket.

Regression for the production /tags page showing "Rate limit exceeded" plus an
empty "No Tags Yet": every Tags operation drew on one ``tags`` bucket
(120 per hour), so ordinary browsing -- a GET per search keystroke, and the
Documents page fetching subtags per tag -- exhausted it, after which even
opening the page answered 429.

All tests use the in-memory limiter path (no Redis) except the ones that
drive a fake Redis client to pin the production key shape and Retry-After.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rbac_backend.core.config import settings
from rbac_backend.core.errors import register_domain_error_handler
from rbac_backend.core.security import get_current_user
from rbac_backend.routers import tags as tags_router
from rbac_backend.routers.tags import (
    TAGS_READ_RATE_LIMIT_SCOPE,
    TAGS_WRITE_RATE_LIMIT_SCOPE,
    TagController,
    get_tag_controller,
)
from rbac_backend.utils.rate_limiter import RateLimiter

TAGS_ROUTER_PATH = Path(tags_router.__file__)

READ_METHODS = {"get_tags", "get_tag", "get_subtags", "get_subtags_batch"}
WRITE_METHODS = {
    "create_tag",
    "update_tag",
    "delete_tag",
    "create_subtag",
    "update_subtag",
    "delete_subtag",
}


class _NoRedisState:
    async def get_redis(self):
        return None


class _BrokenRedisState:
    async def get_redis(self):
        raise ConnectionError("redis unreachable")


class _SentinelAuth:
    """First collaborator every controller method calls after the limiter.

    Raising a distinctive HTTPException (418) proves the limiter admitted the
    request without needing a database behind the controller.
    """

    async def require_permission(self, *_a, **_k):
        raise HTTPException(status_code=418, detail="passed limiter")

    def __getattr__(self, name):
        async def _raise(*_a, **_k):
            raise HTTPException(status_code=418, detail="passed limiter")

        return _raise


class _SentinelTagService:
    def __getattr__(self, name):
        async def _raise(*_a, **_k):
            raise HTTPException(status_code=418, detail="passed limiter")

        return _raise


def _no_redis(limiter: RateLimiter) -> RateLimiter:
    limiter._runtime_state = cast(Any, _NoRedisState())
    return limiter


def _fresh_controller(read_max: int = 3, write_max: int = 6) -> TagController:
    """A controller whose limiters use unique scopes, so tests never collide."""
    suffix = uuid4().hex
    read = _no_redis(
        RateLimiter(max_requests=read_max, window_seconds=600, scope=f"{TAGS_READ_RATE_LIMIT_SCOPE}:{suffix}")
    )
    write = _no_redis(
        RateLimiter(max_requests=write_max, window_seconds=3600, scope=f"{TAGS_WRITE_RATE_LIMIT_SCOPE}:{suffix}")
    )
    return TagController(cast(Any, _SentinelTagService()), cast(Any, _SentinelAuth()), read, write, cast(Any, None))


def _user(user_id: str | None = None):
    return SimpleNamespace(
        id=user_id or f"user-{uuid4().hex}",
        organization_id="org-a",
        roles=["orgadmin"],
    )


async def _status(coro) -> int:
    try:
        await coro
    except HTTPException as exc:
        return exc.status_code
    raise AssertionError("controller call returned without raising")


async def _read(controller: TagController, user) -> int:
    return await _status(controller.get_tags({"skip": 0, "limit": 10}, {}, user))


async def _write(controller: TagController, user) -> int:
    # create_tag costs 2 against the write budget.
    return await _status(controller.create_tag(cast(Any, SimpleNamespace(name="t", organization_id=None)), user))


# --- the factory (never overridden here: overrides hide broken factories) ----


@pytest.mark.asyncio
async def test_factory_builds_separate_read_and_write_limiters_from_settings():
    controller = await get_tag_controller()

    assert controller.read_limiter is not controller.write_limiter
    assert controller.read_limiter.scope == TAGS_READ_RATE_LIMIT_SCOPE == "tags:read"
    assert controller.write_limiter.scope == TAGS_WRITE_RATE_LIMIT_SCOPE == "tags:write"
    assert controller.read_limiter.max_requests == settings.TAGS_READ_RATE_LIMIT_REQUESTS
    assert controller.read_limiter.window_seconds == settings.TAGS_READ_RATE_LIMIT_WINDOW
    assert controller.write_limiter.max_requests == settings.TAGS_WRITE_RATE_LIMIT_REQUESTS
    assert controller.write_limiter.window_seconds == settings.TAGS_WRITE_RATE_LIMIT_WINDOW


def test_default_tag_budgets():
    fields = type(settings).model_fields
    assert fields["TAGS_READ_RATE_LIMIT_REQUESTS"].default == 300
    assert fields["TAGS_READ_RATE_LIMIT_WINDOW"].default == 600
    assert fields["TAGS_WRITE_RATE_LIMIT_REQUESTS"].default == 120
    assert fields["TAGS_WRITE_RATE_LIMIT_WINDOW"].default == 3600


@pytest.mark.parametrize(
    "name",
    [
        "TAGS_READ_RATE_LIMIT_REQUESTS",
        "TAGS_READ_RATE_LIMIT_WINDOW",
        "TAGS_WRITE_RATE_LIMIT_WINDOW",
    ],
)
def test_tag_budget_settings_refuse_non_positive_values(name):
    # A zero budget would answer 429 to every request; it must fail at startup.
    field = type(settings).model_fields[name]
    assert any(getattr(meta, "gt", None) == 0 for meta in field.metadata), name


def _write_budget_floor() -> int:
    field = type(settings).model_fields["TAGS_WRITE_RATE_LIMIT_REQUESTS"]
    return next(meta.ge for meta in field.metadata if getattr(meta, "ge", None) is not None)


# --- A/B: read and write budgets are independent ----------------------------


@pytest.mark.asyncio
async def test_exhausting_read_budget_does_not_block_writes():
    controller = _fresh_controller(read_max=3, write_max=6)
    user = _user()

    assert [await _read(controller, user) for _ in range(3)] == [418, 418, 418]
    assert await _read(controller, user) == 429

    assert await _write(controller, user) == 418


@pytest.mark.asyncio
async def test_exhausting_write_budget_does_not_block_reads():
    controller = _fresh_controller(read_max=3, write_max=4)
    user = _user()

    assert [await _write(controller, user) for _ in range(2)] == [418, 418]
    assert await _write(controller, user) == 429

    assert await _read(controller, user) == 418


# --- C/D: per-user isolation, shared scope ------------------------------------


@pytest.mark.asyncio
async def test_users_have_independent_read_budgets():
    controller = _fresh_controller(read_max=2)
    alice, bob = _user(), _user()

    assert [await _read(controller, alice) for _ in range(2)] == [418, 418]
    assert await _read(controller, alice) == 429
    assert await _read(controller, bob) == 418


@pytest.mark.asyncio
async def test_controllers_built_per_request_share_one_budget():
    # get_tag_controller builds new limiter instances for every request; the
    # budget must survive that, or the limit would never bind.
    first = _fresh_controller(read_max=2)
    second = TagController(
        _SentinelTagService(),
        _SentinelAuth(),
        _no_redis(RateLimiter(max_requests=2, window_seconds=600, scope=first.read_limiter.scope)),
        _no_redis(RateLimiter(max_requests=6, window_seconds=3600, scope=first.write_limiter.scope)),
        None,
    )
    user = _user()

    assert await _read(first, user) == 418
    assert await _read(second, user) == 418
    assert await _read(first, user) == 429


# --- E/F: genuine 429 stays 429, infrastructure failure is 503 -------------


@pytest.mark.asyncio
async def test_exceeded_budget_is_429_with_retry_after():
    limiter = _no_redis(RateLimiter(max_requests=1, window_seconds=600, scope=f"t:{uuid4().hex}"))
    user_id = uuid4().hex
    await limiter.check_user_limit(user_id)

    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit(user_id)

    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded"
    retry_after = int(exc.value.headers["Retry-After"])
    assert 1 <= retry_after <= 600


@pytest.mark.asyncio
async def test_limiter_infrastructure_failure_is_503_not_429():
    limiter = RateLimiter(max_requests=10, window_seconds=600, scope=TAGS_READ_RATE_LIMIT_SCOPE)
    limiter._runtime_state = _BrokenRedisState()

    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit(uuid4().hex)

    assert exc.value.status_code == 503


class _FakeRedis:
    def __init__(self, ttl: int = 437):
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self._ttl = ttl

    async def incrby(self, key, amount):
        self.counts[key] = self.counts.get(key, 0) + int(amount)
        return self.counts[key]

    async def expire(self, key, seconds):
        self.ttls[key] = int(seconds)
        return True

    async def ttl(self, key):
        return self._ttl if key in self.ttls else -1


class _FakeRedisState:
    def __init__(self, redis):
        self._redis = redis

    async def get_redis(self):
        return self._redis


@pytest.mark.asyncio
async def test_redis_keys_are_scoped_per_budget_and_user():
    redis = _FakeRedis()
    read = RateLimiter(max_requests=5, window_seconds=600, scope=TAGS_READ_RATE_LIMIT_SCOPE)
    write = RateLimiter(max_requests=5, window_seconds=3600, scope=TAGS_WRITE_RATE_LIMIT_SCOPE)
    read._runtime_state = write._runtime_state = _FakeRedisState(redis)

    await read.check_user_limit("u1")
    await write.check_user_limit("u1", cost=2)

    assert redis.counts == {"rate:tags:read:user:u1": 1, "rate:tags:write:user:u1": 2}
    assert redis.ttls == {"rate:tags:read:user:u1": 600, "rate:tags:write:user:u1": 3600}


@pytest.mark.parametrize("ttl", [0, -2])
@pytest.mark.asyncio
async def test_redis_window_resetting_now_says_retry_in_one_second(ttl):
    # Redis rounds TTL down (0 = under a second left) or the key expired
    # between INCRBY and TTL (-2); telling the user to wait a full window
    # would lock them out of an already-empty bucket.
    redis = _FakeRedis(ttl=ttl)
    limiter = RateLimiter(max_requests=1, window_seconds=600, scope=TAGS_READ_RATE_LIMIT_SCOPE)
    limiter._runtime_state = cast(Any, _FakeRedisState(redis))

    await limiter.check_user_limit("u1")
    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit("u1")

    assert exc.value.headers == {"Retry-After": "1"}


@pytest.mark.asyncio
async def test_redis_key_without_ttl_is_healed_and_reports_full_window():
    redis = _FakeRedis()
    redis.counts["rate:tags:read:user:u1"] = 1  # counter orphaned without a TTL
    limiter = RateLimiter(max_requests=1, window_seconds=600, scope=TAGS_READ_RATE_LIMIT_SCOPE)
    limiter._runtime_state = cast(Any, _FakeRedisState(redis))

    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit("u1")

    assert redis.ttls == {"rate:tags:read:user:u1": 600}
    assert exc.value.headers == {"Retry-After": "600"}


@pytest.mark.asyncio
async def test_redis_429_carries_remaining_ttl_as_retry_after():
    redis = _FakeRedis(ttl=437)
    limiter = RateLimiter(max_requests=1, window_seconds=600, scope=TAGS_READ_RATE_LIMIT_SCOPE)
    limiter._runtime_state = _FakeRedisState(redis)

    await limiter.check_user_limit("u1")
    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit("u1")

    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "437"}


# --- G: static guard ----------------------------------------------------------


def _tags_tree() -> ast.Module:
    return ast.parse(TAGS_ROUTER_PATH.read_text(encoding="utf-8"))


def test_every_tags_rate_limiter_has_an_explicit_scope():
    calls = [
        node
        for node in ast.walk(_tags_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RateLimiter"
    ]
    assert len(calls) == 2, "expected exactly one read and one write limiter"
    for call in calls:
        scope = next((kw.value for kw in call.keywords if kw.arg == "scope"), None)
        assert scope is not None, f"RateLimiter at line {call.lineno} has no scope="
        assert not (isinstance(scope, ast.Constant) and scope.value is None), call.lineno


def _limiter_attrs_used(method: ast.AsyncFunctionDef) -> set[str]:
    used = set()
    for node in ast.walk(method):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "check_user_limit"
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
        ):
            used.add(node.func.value.attr)
    return used


def test_each_tag_operation_draws_on_the_matching_budget():
    controller = next(
        node for node in _tags_tree().body if isinstance(node, ast.ClassDef) and node.name == "TagController"
    )
    methods = {
        node.name: node
        for node in controller.body
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    }
    assert set(methods) == READ_METHODS | WRITE_METHODS, "new Tag operation: classify it as read or write"
    for name in READ_METHODS:
        assert _limiter_attrs_used(methods[name]) == {"read_limiter"}, name
    for name in WRITE_METHODS:
        assert _limiter_attrs_used(methods[name]) == {"write_limiter"}, name


def _limiter_costs(method: ast.AsyncFunctionDef) -> list[int]:
    costs = []
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "check_user_limit":
            cost = next((kw.value for kw in node.keywords if kw.arg == "cost"), None)
            costs.append(1 if cost is None else ast.literal_eval(cost))
    return costs


def test_operation_costs_are_pinned_and_fit_the_write_budget():
    controller = next(
        node for node in _tags_tree().body if isinstance(node, ast.ClassDef) and node.name == "TagController"
    )
    costs = {
        node.name: _limiter_costs(node)
        for node in controller.body
        if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_")
    }
    assert costs == {
        "get_tags": [1],
        "get_tag": [1],
        "get_subtags": [1],
        "get_subtags_batch": [1],
        "create_tag": [2],
        "update_tag": [2],
        "delete_tag": [5],
        "create_subtag": [2],
        "update_subtag": [2],
        "delete_subtag": [3],
    }
    assert max(max(costs[name]) for name in WRITE_METHODS) <= _write_budget_floor()


# --- Phase 4: 429 is never masked as 500 on any route -------------------------


def _client(controller: TagController, monkeypatch) -> TestClient:
    async def _allow(*_a, **_k):
        return None

    monkeypatch.setattr(tags_router.PolicyService, "authorize", _allow)
    app = FastAPI()
    register_domain_error_handler(app)  # as main.py does; HTTPException uses the FastAPI default
    app.include_router(tags_router.router, prefix="/api")
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_tag_controller] = lambda: controller
    return TestClient(app, raise_server_exceptions=False)


class _AlwaysLimited:
    def __init__(self, status_code: int = 429, detail: str = "Rate limit exceeded", headers=None):
        self.status_code = status_code
        self.detail = detail
        self.headers = headers

    async def check_user_limit(self, *_a, **_k):
        raise HTTPException(status_code=self.status_code, detail=self.detail, headers=self.headers)


class _Unreachable:
    def __getattr__(self, name):
        raise AssertionError(f"reached {name} after the limiter refused")


class _PreflightOnlyTagService(_Unreachable):
    """PUT/DELETE /tags/{id} read the tag for its audit ``before`` first."""

    async def get_tag_by_id(self, _tag_id):
        return None


def _limited_controller(status_code: int = 429, headers=None) -> TagController:
    limited = _AlwaysLimited(status_code=status_code, headers=headers)
    limiter = cast(Any, limited)
    return TagController(cast(Any, _PreflightOnlyTagService()), cast(Any, _Unreachable()), limiter, limiter, cast(Any, None))


_OID = "65f1c0ffee0ddba11c0ffee0"
ROUTE_CASES = [
    ("get", "/api/tags", None),
    ("get", f"/api/tags/{_OID}", None),
    ("get", f"/api/tags/{_OID}/subtags", None),
    ("get", f"/api/tags/subtags/batch?tag_ids={_OID}", None),
    ("post", "/api/tags", {"name": "Payment"}),
    ("put", f"/api/tags/{_OID}", {"name": "Payments"}),
    ("delete", f"/api/tags/{_OID}", None),
    ("post", f"/api/tags/{_OID}/subtags", {"name": "Late"}),
    ("put", f"/api/subtags/{_OID}", {"name": "Later"}),
    ("delete", f"/api/subtags/{_OID}", None),
]


@pytest.mark.parametrize("method,path,body", ROUTE_CASES)
def test_route_keeps_429_and_retry_after(method, path, body, monkeypatch):
    client = _client(_limited_controller(headers={"Retry-After": "42"}), monkeypatch)

    response = client.request(method, path, json=body)

    assert response.status_code == 429, response.text
    assert response.json()["detail"] == "Rate limit exceeded"
    assert response.headers.get("retry-after") == "42"


@pytest.mark.parametrize("method,path,body", ROUTE_CASES)
def test_route_keeps_limiter_outage_as_503(method, path, body, monkeypatch):
    client = _client(_limited_controller(status_code=503), monkeypatch)

    response = client.request(method, path, json=body)

    assert response.status_code == 503, response.text
