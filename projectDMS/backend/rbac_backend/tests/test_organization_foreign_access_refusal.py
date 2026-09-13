"""An org admin addressing a foreign organisation is refused, never answered 500.

R-A8W Stage B, F-A8W-B3, measured against a deployment: `GET
/api/organizations/{foreign}` as an org admin returned **500**. The policy layer
had denied it (`scope_denied`) and no row leaked, but
`OrganizationController.get_organization` re-raised only `(OrganizationError,
HTTPException)`, so the `AuthorizationError` that
`AuthorizationService.check_organization_access` raised fell to `except
Exception` and was rewritten as "Organization service temporarily unavailable".

The client reads 403 as "you may not" and 5xx as an outage, so the refusal
contract - 403, or 404 where the source conceals existence - is the property
under test, over HTTP, through the real controller, the real authorisation
service and the real `handle_exceptions` decorator. Only the persistence and the
rate-limit store are faked.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.routers.organizations import OrganizationController, get_organization_controller
    from rbac_backend.services import permission_service as permission_module
    from rbac_backend.services.authorization_service import AuthorizationService

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - env without TestClient/app
    _IMPORTS_OK = False
    _IMPORT_ERR = exc

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")

OWN = "org-own"
FOREIGN = "org-foreign"
#: Values that exist only on the foreign organisation. None may appear in any
#: response the org admin receives.
FOREIGN_SECRETS = ("Foreign Holdings Ltd", "foreign-admin@example.com", "AAAPF9999F")

ORGANIZATIONS: Dict[str, Dict[str, Any]] = {
    OWN: {"_id": OWN, "name": "Own Works Pvt", "email": "own@example.com", "is_active": True},
    FOREIGN: {
        "_id": FOREIGN,
        "name": FOREIGN_SECRETS[0],
        "adminEmail": FOREIGN_SECRETS[1],
        "panNumber": FOREIGN_SECRETS[2],
        "is_active": True,
    },
}


class _OrgService:
    async def get_organization_by_id(self, organization_id: str) -> Optional[Dict[str, Any]]:
        row = ORGANIZATIONS.get(organization_id)
        return dict(row) if row else None

    async def get_organizations_paginated(self, query: Dict[str, Any], pagination: Dict[str, int]):
        allowed = query.get("_id", {}).get("$in") if isinstance(query.get("_id"), dict) else None
        rows = [dict(row) for key, row in ORGANIZATIONS.items() if allowed is None or key in allowed]
        return rows, len(rows)

    async def update_organization(self, *args: Any, **kwargs: Any):  # pragma: no cover - must not run
        raise AssertionError("a foreign organisation reached the update write")


class _RateLimiter:
    async def check_user_limit(self, user_id: str, cost: int = 1) -> bool:
        return True


class _Audit:
    def __getattr__(self, name: str):
        async def _record(*args: Any, **kwargs: Any) -> None:
            return None

        return _record


class _RefusingAuthorization(AuthorizationService):
    """The real service, whose list query refuses the way a scope check does."""

    async def build_organization_query(self, current_user: Any, filters: Any = None):
        from rbac_backend.utils.error_handler import AuthorizationError

        raise AuthorizationError("Organization listing is not permitted in this scope")


def _org_admin():
    return SimpleNamespace(
        id="orgadmin-user",
        username="orgadmin",
        email="orgadmin@example.com",
        roles=["orgadmin"],
        organization_id=OWN,
        organizations=[OWN],
        projects=[],
        disabled=False,
    )


def _client(monkeypatch, auth_service: AuthorizationService) -> TestClient:
    async def _has_perm(self, *args, **kwargs):
        return True

    monkeypatch.setattr(permission_module.PermissionService, "user_has_permission", _has_perm)
    controller = OrganizationController(_OrgService(), auth_service, _RateLimiter(), _Audit())
    app.dependency_overrides[get_current_user] = _org_admin
    app.dependency_overrides[get_organization_controller] = lambda: controller
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(monkeypatch):
    try:
        yield _client(monkeypatch, AuthorizationService())
    finally:
        app.dependency_overrides.clear()


def _assert_refused_without_disclosure(response, allowed: List[int]) -> None:
    assert response.status_code in allowed, (
        f"expected a refusal in {allowed}, got {response.status_code}: {response.text}"
    )
    for secret in FOREIGN_SECRETS:
        assert secret not in response.text, f"the refusal disclosed foreign data: {secret!r}"


def test_own_organization_is_readable(client) -> None:
    """The still-works half: the fix must not refuse the admin's own tenant."""
    response = client.get(f"/api/organizations/{OWN}")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Own Works Pvt"


def test_foreign_organization_read_is_refused_not_500(client) -> None:
    response = client.get(f"/api/organizations/{FOREIGN}")
    _assert_refused_without_disclosure(response, [403, 404])
    assert response.status_code != 401, "a scope refusal arriving as 401 forces a logout"


def test_foreign_organization_update_is_refused_not_500(client) -> None:
    response = client.put(f"/api/organizations/{FOREIGN}", json={"name": "Renamed by a foreigner"})
    _assert_refused_without_disclosure(response, [403, 404])


def test_foreign_organization_stats_are_refused(client) -> None:
    """Already outside any try block before this fix; pinned so it stays so."""
    response = client.get(f"/api/organizations/{FOREIGN}/stats")
    _assert_refused_without_disclosure(response, [403, 404])


def test_the_application_renders_a_domain_error_without_the_decorator() -> None:
    """The backstop behind the widened re-raises.

    A router may re-raise `BaseDomainError` from a route that carries no
    `handle_exceptions`. The application must still answer at the error's own
    status. Measured on a fresh app with the same registration the real app
    uses, and on the real app's handler table.
    """
    from fastapi import FastAPI

    from rbac_backend.core.errors import register_domain_error_handler
    from rbac_backend.utils.error_handler import AuthorizationError, BaseDomainError

    probe = FastAPI()
    register_domain_error_handler(probe)

    from rbac_backend.utils.error_handler import DocumentError

    @probe.get("/refuse")
    async def refuse():
        raise AuthorizationError("Access denied to this organization")

    @probe.get("/fail")
    async def fail():
        raise DocumentError("CSV processing failed: /srv/uploads/secret.csv", 500)

    client = TestClient(probe, raise_server_exceptions=False)
    response = client.get("/refuse")
    assert response.status_code == 403, response.text
    # The envelope `handle_exceptions` produces and the client reads.
    assert response.json()["detail"]["error"] == "AuthorizationError"
    assert response.json()["detail"]["message"] == "Access denied to this organization"

    failed = client.get("/fail")
    assert failed.status_code == 500
    assert "/srv/uploads" not in failed.text, "a 5xx domain error's internal text reached the client"
    assert BaseDomainError in app.exception_handlers, "the real application has no domain-error handler"


def test_a_refusal_from_the_list_query_keeps_its_status(monkeypatch) -> None:
    """The same class on a sibling method: `get_organizations` re-raised the
    same narrow tuple, so a scope refusal from its query builder was a 500 too."""
    try:
        client = _client(monkeypatch, _RefusingAuthorization())
        response = client.get("/api/organizations")
        _assert_refused_without_disclosure(response, [403])
    finally:
        app.dependency_overrides.clear()
