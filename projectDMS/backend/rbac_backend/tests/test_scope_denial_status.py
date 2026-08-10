"""Scope refusals must reach the client with their own status.

Two independent handlers used to rewrite an authorisation refusal into something
else, and both were invisible from the endpoint that suffered them:

* ``get_current_user`` applied the navbar selection *inside* its credential
  loop, so a ``TenantContextError`` (403 ``context_forbidden`` / 400
  ``selection_required``) was caught by the loop's broad ``except HTTPException``
  and the loop fell through to ``credentials_exception``. A user who selected an
  organisation they may not work in was logged out instead of being told no, and
  the 400 the UI needs to raise its "select an Organisation" prompt could never
  arrive.

* ``controller_list_documents`` re-raised only ``DocumentError``, so the sibling
  ``AuthorizationError`` -- same base class, 403 by construction -- fell to the
  catch-all and was reported as a 500. A deliberate refusal looked like an
  outage.

Both are status-fidelity contracts: the refusal still happens either way, so
nothing here tests whether access is denied. It tests what the client is told.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.security import CurrentUser, create_access_token, get_current_user
from rbac_backend.core.tenant_context import TenantContextError
from rbac_backend.routers.documents import controller_list_documents
from rbac_backend.utils.error_handler import AuthorizationError, DocumentError


# --- fakes ----------------------------------------------------------------


class _FakeUsers:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _query):
        return self._doc


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    async def to_list(self, length=None):
        return list(self._rows)


class _FakeCollection:
    def __init__(self, rows=(), found=None):
        self._rows = rows
        self._found = found

    def find(self, *_a, **_k):
        return _FakeCursor(self._rows)

    async def find_one(self, *_a, **_k):
        return self._found


class _FakeDB:
    def __init__(self, user_doc, *, org_doc=None):
        self.users = _FakeUsers(user_doc)
        self.organization_memberships = _FakeCollection()
        self.project_memberships = _FakeCollection()
        self.organizations = _FakeCollection(found=org_doc)
        self.projects = _FakeCollection(found=None)


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class _FakeRedis:
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
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: _FakeRuntime())


def _doc(roles, *, org="org-A", orgs=("org-A",), projects=()):
    return {
        "_id": "user-1",
        "email": "a@example.com",
        "username": "a",
        "roles": list(roles),
        "organization_id": org,
        "organizations": list(orgs),
        "projects": list(projects),
    }


def _bearer():
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    return {"authorization": f"Bearer {token}"}


# --- ISSUE-01: tenant refusals keep their status --------------------------


@pytest.mark.asyncio
async def test_foreign_organisation_selection_is_403_not_401(live_session_store):
    """The refusal the browser reads as "wrong org", never as "logged out"."""
    headers = {**_bearer(), "x-org-id": "org-B"}
    db = _FakeDB(_doc(["orgadmin"], org="org-A", orgs=("org-A",)))

    with pytest.raises(TenantContextError) as excinfo:
        await get_current_user(_FakeRequest(headers=headers), db)

    assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
    assert excinfo.value.detail["code"] == "context_forbidden"


@pytest.mark.asyncio
async def test_project_without_organisation_is_not_swallowed(live_session_store):
    """A bare X-Proj-Id must not be answered with a credentials error."""
    headers = {**_bearer(), "x-proj-id": "proj-1"}
    db = _FakeDB(_doc(["superadmin"], org=None, orgs=()))

    with pytest.raises(TenantContextError) as excinfo:
        await get_current_user(_FakeRequest(headers=headers), db)

    assert excinfo.value.status_code != status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_valid_selection_still_authenticates(live_session_store):
    """The boundary case beside the rejections: a legitimate scope still works."""
    headers = {**_bearer(), "x-org-id": "org-A"}
    db = _FakeDB(
        _doc(["orgadmin"], org="org-A", orgs=("org-A",)),
        org_doc={"_id": "org-A", "is_active": True},
    )

    user = await get_current_user(_FakeRequest(headers=headers), db)

    assert user.organization_id == "org-A"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_still_401(live_session_store, monkeypatch):
    """Widening the tenant path must not soften a real credential failure."""
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", False)
    request = _FakeRequest(headers={"authorization": "Bearer not-a-real-token"})

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(request, _FakeDB(_doc(["orgadmin"])))

    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_disabled_account_is_still_401(live_session_store, monkeypatch):
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "ALLOW_DEV_HEADERS", False)
    doc = _doc(["orgadmin"])
    doc["disabled"] = True

    with pytest.raises(HTTPException) as excinfo:
        await get_current_user(_FakeRequest(headers=_bearer()), _FakeDB(doc))

    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


# --- ISSUE-02: domain refusals are not reported as outages ----------------


class _RaisingAuthService:
    def __init__(self, exc):
        self._exc = exc

    async def build_document_query(self, *_a, **_k):
        raise self._exc


class _Controller:
    def __init__(self, exc):
        self.auth_service = _RaisingAuthService(exc)
        self.document_service = None


def _user():
    return CurrentUser(
        id="user-1",
        username="a",
        email="a@example.com",
        roles=["orgadmin"],
        organization_id="org-A",
    )


@pytest.mark.asyncio
async def test_authorization_error_is_not_rewritten_as_500():
    """An out-of-context filter is a refusal, not a service outage."""
    ctrl = _Controller(AuthorizationError("No organization scope available"))

    with pytest.raises(AuthorizationError) as excinfo:
        await controller_list_documents(ctrl, {}, {}, _user())

    assert excinfo.value.http_status == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_document_error_still_propagates():
    """The original narrow case must keep working."""
    ctrl = _Controller(DocumentError("bad request", status.HTTP_400_BAD_REQUEST))

    with pytest.raises(DocumentError):
        await controller_list_documents(ctrl, {}, {}, _user())


@pytest.mark.asyncio
async def test_unexpected_error_is_still_a_500():
    """Genuine faults must stay loud; this fix narrows the catch-all, not removes it."""
    ctrl = _Controller(RuntimeError("mongo exploded"))

    with pytest.raises(HTTPException) as excinfo:
        await controller_list_documents(ctrl, {}, {}, _user())

    assert excinfo.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
