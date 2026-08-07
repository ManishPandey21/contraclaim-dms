"""The seam where an organisation *selection* is separated from a *home org*.

``build_scope_query`` treats ``current_user.organization_id`` as the caller's
active selection: for a global role, a value there narrows the query. That is
only safe because ``get_current_user`` guarantees the field is empty unless the
caller actually selected an organisation -- it clears the account's home
organisation for global roles and re-populates the field only from an
``X-Org-Id`` the tenant-context resolver has validated.

Both halves are load-bearing and neither is visible from the other. These tests
pin the auth-layer half; ``test_progressive_scope_narrowing.py`` pins the query
half. If this file goes red, a Super User's consolidated view has silently
collapsed to their home organisation (or, worse, a home organisation has become
an unvalidated selection).
"""

from __future__ import annotations

import pytest

import rbac_backend.core.tenant_context as tenant_context_mod
import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.config import settings
from rbac_backend.core.security import create_access_token, get_current_user
from rbac_backend.core.tenant_context import TenantContext


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
    """No ``min_iat`` floor configured and every session live."""

    async def get(self, _key):
        return None

    async def exists(self, _key):
        return 1


class _FakeRuntime:
    redis_url = "redis://test-runtime"

    async def get_redis(self):
        return _FakeRedis()


@pytest.fixture
def live_session_store(monkeypatch):
    """Keep the token path off real Redis, which otherwise fails closed."""
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: _FakeRuntime())


def _doc(roles, *, org="org-A", orgs=("org-A", "org-B")):
    return {
        "_id": "user-1",
        "email": "a@example.com",
        "username": "a",
        "roles": list(roles),
        "organization_id": org,
        "organizations": list(orgs),
        "projects": [],
    }


def _dev_headers(roles, org_id=None):
    headers = {"x-user-id": "a@example.com", "x-user-roles": roles}
    if org_id:
        headers["x-org-id"] = org_id
    return headers


# --- token path -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["superuser", "superadmin"])
async def test_token_path_does_not_treat_home_org_as_a_selection(role, live_session_store):
    """A global role signing in selects nothing, whatever their account says."""
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})

    user = await get_current_user(request, _FakeDB(_doc([role])))

    assert user.organization_id is None, (
        "a home organisation must not reach build_scope_query as a selection"
    )


@pytest.mark.asyncio
async def test_token_path_keeps_a_superusers_assigned_organisations(live_session_store):
    """Clearing the selection must not also clear what they may consolidate."""
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})

    user = await get_current_user(request, _FakeDB(_doc(["superuser"])))

    assert set(user.organizations) == {"org-A", "org-B"}


# --- dev-header path ------------------------------------------------------
#
# This path built organization_id as `x_org_id or <home org>`, so a Super User
# who selected nothing was pinned to their home organisation -- the opposite of
# the token path, and invisible until a consolidated view came back short.


@pytest.mark.asyncio
async def test_dev_header_path_matches_the_token_path_when_nothing_is_selected(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", True)
    request = _FakeRequest(headers=_dev_headers("superuser"))

    user = await get_current_user(request, _FakeDB(_doc(["superuser"])))

    assert user.organization_id is None
    assert set(user.organizations) == {"org-A", "org-B"}


@pytest.mark.asyncio
async def test_dev_header_path_still_seeds_orgs_from_a_lone_home_org(monkeypatch):
    """Dropping the selection must not strand a Super User with no reach."""
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", True)
    request = _FakeRequest(headers=_dev_headers("superuser"))

    user = await get_current_user(request, _FakeDB(_doc(["superuser"], orgs=())))

    assert user.organization_id is None
    assert set(user.organizations) == {"org-A"}


@pytest.mark.asyncio
async def test_an_explicit_header_is_a_selection(monkeypatch):
    """X-Org-Id is the only thing that narrows a global role.

    The resolver is stubbed here on purpose: its own validation is covered by
    the tenant-context tests, and what needs pinning is that the sanitising
    step hands the requested organisation through instead of swallowing it.
    """
    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", True)
    seen = {}

    async def _fake_resolve(_self, user, *, organization_id, project_id, **_kwargs):
        seen["requested"] = organization_id
        seen["actor_org"] = user.organization_id
        return TenantContext(organization_id, project_id, "global", True, True)

    monkeypatch.setattr(tenant_context_mod.TenantContextResolver, "resolve", _fake_resolve)
    request = _FakeRequest(headers=_dev_headers("superuser", org_id="org-B"))

    user = await get_current_user(request, _FakeDB(_doc(["superuser"])))

    assert seen["requested"] == "org-B", "the requested organisation must reach the resolver"
    assert user.organization_id == "org-B", "a validated selection must reach the actor"
