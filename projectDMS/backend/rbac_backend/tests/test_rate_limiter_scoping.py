"""Rate limiter scoping + organization 429 propagation.

Regression tests for the intermittent "Organization service temporarily
unavailable" dropdown failures: all routers used to share one per-user rate
bucket, and the organization controller masked the resulting 429 as a 500.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from rbac_backend.routers.organizations import OrganizationController
from rbac_backend.utils.rate_limiter import RateLimiter


class _NoRedisState:
    async def get_redis(self):
        return None


def _limiter(scope: str | None, max_requests: int = 3, window_seconds: int = 60) -> RateLimiter:
    limiter = RateLimiter(max_requests=max_requests, window_seconds=window_seconds, scope=scope)
    limiter._runtime_state = _NoRedisState()  # force deterministic in-memory path
    return limiter


# --- scoping ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_scopes_do_not_share_budget():
    user = f"user-{uuid4().hex}"
    orgs = _limiter("organizations", max_requests=3)
    tags = _limiter("tags", max_requests=3)

    for _ in range(3):
        await orgs.check_user_limit(user)

    # The organizations budget is exhausted...
    with pytest.raises(HTTPException) as exc:
        await orgs.check_user_limit(user)
    assert exc.value.status_code == 429

    # ...but an unrelated scope still has its full budget.
    for _ in range(3):
        await tags.check_user_limit(user)


@pytest.mark.asyncio
async def test_same_scope_instances_share_budget():
    user = f"user-{uuid4().hex}"
    a = _limiter("organizations", max_requests=2)
    b = _limiter("organizations", max_requests=2)

    await a.check_user_limit(user)
    await b.check_user_limit(user)
    with pytest.raises(HTTPException) as exc:
        await a.check_user_limit(user)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_unscoped_limiter_keeps_legacy_shared_key():
    user = f"user-{uuid4().hex}"
    legacy = _limiter(None, max_requests=1)
    await legacy.check_user_limit(user)
    with pytest.raises(HTTPException):
        await legacy.check_user_limit(user)


def test_requests_per_minute_alias_still_supported():
    limiter = RateLimiter(requests_per_minute=42, window_seconds=60)
    assert limiter.max_requests == 42
    assert limiter.requests_per_minute == 42
    explicit = RateLimiter(requests_per_minute=99, window_seconds=60, max_requests=7)
    assert explicit.max_requests == 7


@pytest.mark.asyncio
async def test_rate_limit_error_is_429_not_503():
    user = f"user-{uuid4().hex}"
    limiter = _limiter("x", max_requests=1)
    await limiter.check_user_limit(user)
    with pytest.raises(HTTPException) as exc:
        await limiter.check_user_limit(user)
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded"


# --- organization controller must not mask HTTP errors ----------------------


class _RateLimited:
    async def check_user_limit(self, *_a, **_k):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


class _Boom:
    def __getattr__(self, name):
        raise AssertionError(f"should not reach {name} when rate-limited")


@pytest.mark.asyncio
async def test_get_organizations_propagates_429():
    controller = OrganizationController(_Boom(), _Boom(), _RateLimited(), _Boom())
    user = SimpleNamespace(id="u-1", role="superadmin")
    with pytest.raises(HTTPException) as exc:
        await controller.get_organizations({"skip": 0, "limit": 50}, {}, user)
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded"


@pytest.mark.asyncio
async def test_get_organization_propagates_429():
    controller = OrganizationController(_Boom(), _Boom(), _RateLimited(), _Boom())
    user = SimpleNamespace(id="u-1", role="superadmin")
    with pytest.raises(HTTPException) as exc:
        await controller.get_organization("org-1", user)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_get_organizations_unexpected_error_still_masked_as_500():
    class _FailingAuth:
        async def build_organization_query(self, *_a, **_k):
            raise RuntimeError("db exploded")

    class _Ok:
        async def check_user_limit(self, *_a, **_k):
            return True

    controller = OrganizationController(_Boom(), _FailingAuth(), _Ok(), _Boom())
    user = SimpleNamespace(id="u-1", role="superadmin")
    with pytest.raises(HTTPException) as exc:
        await controller.get_organizations({"skip": 0, "limit": 50}, {}, user)
    assert exc.value.status_code == 500
    assert "temporarily unavailable" in str(exc.value.detail)
