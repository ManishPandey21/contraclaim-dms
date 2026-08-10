"""Three surfaces that enforced less than the ones beside them.

ISSUE-05: the notifications WebSocket decoded a JWT signature and connected on
the user id alone -- no account re-read, no revocation check, no token-type
check. A deactivated, deleted or logged-out user's unexpired token kept
receiving live notifications, and a ``step_up`` token, which HTTP rejects
outright, could open a stream. ``verify_access_token`` is now the single
statement of "is this credential currently good" so a non-HTTP transport cannot
quietly enforce less.

ISSUE-08: detail-by-id routes authorised against *raw entitlement* while every
list, dashboard, search and export surface applied Entitlement n Selection. An
org-admin listed in two organisations, working in the first, could read the
second's record by id. Worse, ``_ensure_project_access`` derived the
organisation it checked *from the requested project*, so the object supplied its
own authorisation context -- a confused deputy.

ISSUE-09: the notification inbox sat outside the permission system entirely, so
a principal resolving to zero permissions -- refused by every other module --
still read its feed.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi import HTTPException

import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.security import CurrentUser, create_access_token, verify_access_token
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.utils.error_handler import AuthorizationError

ORG_A = "org-a"
ORG_B = "org-b"


class _FakeUsers:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _query):
        return self._doc


class _FakeDB:
    def __init__(self, user_doc):
        self.users = _FakeUsers(user_doc)


class _NoStoreRuntime:
    """No revocation store configured, so those checks are skipped by design."""

    redis_url = None

    async def get_redis(self):
        return None


@pytest.fixture(autouse=True)
def _no_session_store(monkeypatch):
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: _NoStoreRuntime())


def _user_doc(**overrides):
    doc = {
        "_id": "user-1",
        "email": "a@example.com",
        "username": "a",
        "roles": ["orgadmin"],
        "organization_id": ORG_A,
        "organizations": [ORG_A],
        "projects": [],
    }
    doc.update(overrides)
    return doc


# --- ISSUE-05: the socket is held to the HTTP contract ---------------------


@pytest.mark.asyncio
async def test_a_valid_access_token_is_accepted():
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    user = await verify_access_token(token, _FakeDB(_user_doc()))
    assert user is not None and user["_id"] == "user-1"


@pytest.mark.asyncio
async def test_a_disabled_account_is_refused():
    """Deactivation must take effect immediately, not at token expiry."""
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    assert await verify_access_token(token, _FakeDB(_user_doc(disabled=True))) is None


@pytest.mark.asyncio
async def test_a_deleted_account_is_refused():
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    assert await verify_access_token(token, _FakeDB(None)) is None


@pytest.mark.asyncio
async def test_a_step_up_token_cannot_open_a_session():
    token = create_access_token({"sub": "a@example.com", "typ": "step_up"})
    assert await verify_access_token(token, _FakeDB(_user_doc())) is None


@pytest.mark.asyncio
async def test_a_non_access_token_type_is_refused():
    token = create_access_token({"sub": "a@example.com", "type": "refresh"})
    assert await verify_access_token(token, _FakeDB(_user_doc())) is None


@pytest.mark.asyncio
async def test_a_forged_token_is_refused():
    assert await verify_access_token("not-a-real-token", _FakeDB(_user_doc())) is None


def test_the_socket_no_longer_decodes_tokens_itself():
    """Guard: a second, weaker auth path is how this drifted the first time."""
    import pathlib

    from rbac_backend.routers import ws

    src = pathlib.Path(ws.__file__).read_text(encoding="utf-8")
    assert "verify_access_token" in src
    assert "jwt.decode" not in src, (
        "the socket must not verify credentials on its own; that is what let it "
        "skip the disabled, revocation and token-type checks"
    )


# --- ISSUE-08: detail routes obey the working context ----------------------


def _actor(roles, org, orgs):
    return CurrentUser(
        id="user-1",
        username="a",
        email="a@example.com",
        roles=roles,
        organization_id=org,
        organizations=orgs,
    )


@pytest.mark.asyncio
async def test_org_detail_allows_the_organisation_being_worked_in():
    actor = _actor(["orgadmin"], ORG_A, [ORG_A, ORG_B])
    await AuthorizationService().check_organization_access(actor, ORG_A, "read")


@pytest.mark.asyncio
async def test_org_detail_refuses_an_entitled_but_unselected_organisation():
    """The exact production observation: entitled to both, working in one."""
    actor = _actor(["orgadmin"], ORG_A, [ORG_A, ORG_B])
    with pytest.raises(AuthorizationError):
        await AuthorizationService().check_organization_access(actor, ORG_B, "read")


@pytest.mark.asyncio
async def test_org_detail_still_refuses_an_unrelated_organisation():
    actor = _actor(["orgadmin"], ORG_A, [ORG_A])
    with pytest.raises(AuthorizationError):
        await AuthorizationService().check_organization_access(actor, "org-z", "read")


@pytest.mark.asyncio
async def test_platform_administration_is_unaffected():
    """superadmin returns before the narrowing, so admin surfaces keep working."""
    actor = _actor(["superadmin"], None, [])
    await AuthorizationService().check_organization_access(actor, ORG_B, "read")


def test_project_access_no_longer_takes_its_context_from_the_target():
    """Guard against the confused deputy returning."""
    import pathlib

    from rbac_backend.routers import projects

    src = pathlib.Path(projects.__file__).read_text(encoding="utf-8")
    assert "permits_within_selection" in src, (
        "the caller's working scope must decide, not the requested object's own "
        "organisation"
    )


# --- ISSUE-09: the inbox participates in the permission system -------------


@pytest.mark.asyncio
async def test_zero_permission_principal_is_refused_the_inbox(monkeypatch):
    from rbac_backend.routers import notifications

    class _NoPerms:
        async def get_effective_permission_names(self, _uid):
            return []

    monkeypatch.setattr(
        "rbac_backend.services.permission_service.PermissionService",
        lambda: _NoPerms(),
    )

    with pytest.raises(HTTPException) as excinfo:
        await notifications.require_any_granted_permission(
            _actor(["superuser"], ORG_A, [ORG_A])
        )
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_a_principal_with_permissions_keeps_its_inbox(monkeypatch):
    from rbac_backend.routers import notifications

    class _SomePerms:
        async def get_effective_permission_names(self, _uid):
            return ["dms.document.view"]

    monkeypatch.setattr(
        "rbac_backend.services.permission_service.PermissionService",
        lambda: _SomePerms(),
    )

    actor = _actor(["orgadmin"], ORG_A, [ORG_A])
    assert await notifications.require_any_granted_permission(actor) is actor


@pytest.mark.asyncio
async def test_superadmin_bypasses_the_inbox_gate():
    from rbac_backend.routers import notifications

    actor = _actor(["superadmin"], None, [])
    assert await notifications.require_any_granted_permission(actor) is actor
