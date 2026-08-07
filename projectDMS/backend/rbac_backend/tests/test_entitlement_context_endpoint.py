from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import PERMISSION_CONTRACT_VERSION
from rbac_backend.core.security import CurrentUser
from rbac_backend.routers import rbac_monetization as router_module


def _user(*, roles, organization_id=None, project_id=None, organizations=None, projects=None):
    return CurrentUser(
        id="user-1",
        username="user-1",
        email="user-1@example.com",
        roles=list(roles),
        organization_id=organization_id,
        project_id=project_id,
        organizations=list(organizations or []),
        projects=list(projects or []),
    )


class _Scope:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.checked = []

    @staticmethod
    def role_names(user):
        return {str(role).lower() for role in user.roles}

    async def client_organization_ids(self, user):
        return set(user.organizations)

    async def is_client_scope_allowed(self, user, *, organization_id=None, project_id=None):
        self.checked.append((organization_id, project_id))
        return self.allowed


class _Entitlements:
    def __init__(self, states):
        self.states = states
        self.calls = []

    async def effective_features(self, *, organization_id, project_id=None, package_id=None):
        self.calls.append((organization_id, project_id))
        return dict(self.states[organization_id])


@pytest.mark.asyncio
async def test_selected_project_entitlements_are_scope_checked(monkeypatch) -> None:
    scope = _Scope(allowed=True)
    service = _Entitlements(
        {
            "org-A": {
                "source": "project",
                "plan_code": "dms_pro",
                "features": {"feature.dms.enabled": True, "feature.dms.claims": True},
                "dms_enabled": True,
                "drafting_enabled": False,
            }
        }
    )
    monkeypatch.setattr(router_module, "ScopeService", lambda: scope)
    monkeypatch.setattr(router_module, "EntitlementService", lambda: service)

    result = await router_module.get_my_effective_entitlements(
        _user(
            roles=["projectuser"],
            organization_id="org-A",
            project_id="proj-A1",
            organizations=["org-A"],
            projects=["proj-A1"],
        )
    )

    assert result["contract_version"] == PERMISSION_CONTRACT_VERSION
    assert result["scope_mode"] == "project"
    assert scope.checked == [("org-A", "proj-A1")]
    assert service.calls == [("org-A", "proj-A1")]
    assert result["features"]["feature.dms.claims"] is True


@pytest.mark.asyncio
async def test_selected_scope_denial_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(router_module, "ScopeService", lambda: _Scope(allowed=False))
    monkeypatch.setattr(router_module, "EntitlementService", lambda: _Entitlements({}))

    with pytest.raises(HTTPException) as exc:
        await router_module.get_my_effective_entitlements(
            _user(
                roles=["orguser"],
                organization_id="org-A",
                organizations=["org-A"],
            )
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_superuser_consolidated_features_require_every_assigned_org(monkeypatch) -> None:
    service = _Entitlements(
        {
            "org-A": {
                "source": "organization",
                "features": {"feature.dms.enabled": True, "feature.dms.claims": True},
            },
            "org-B": {
                "source": "organization",
                "features": {"feature.dms.enabled": True, "feature.dms.claims": False},
            },
        }
    )
    monkeypatch.setattr(router_module, "ScopeService", lambda: _Scope())
    monkeypatch.setattr(router_module, "EntitlementService", lambda: service)

    result = await router_module.get_my_effective_entitlements(
        _user(roles=["superuser"], organizations=["org-A", "org-B"])
    )

    assert result["scope_mode"] == "assigned_organizations"
    assert result["features"]["feature.dms.enabled"] is True
    assert result["features"]["feature.dms.claims"] is False
    assert service.calls == [("org-A", None), ("org-B", None)]


@pytest.mark.asyncio
async def test_superuser_missing_subscription_is_reported_and_fail_closed(monkeypatch) -> None:
    service = _Entitlements(
        {
            "org-A": {
                "source": "organization",
                "features": {"feature.dms.enabled": True},
            },
            "org-B": {
                "source": "none",
                "features": {"feature.dms.enabled": False},
            },
        }
    )
    monkeypatch.setattr(router_module, "ScopeService", lambda: _Scope())
    monkeypatch.setattr(router_module, "EntitlementService", lambda: service)

    result = await router_module.get_my_effective_entitlements(
        _user(roles=["superuser"], organizations=["org-A", "org-B"])
    )

    assert result["features"]["feature.dms.enabled"] is False
    assert result["unavailable_reason"] == "incomplete_subscription_coverage"


@pytest.mark.asyncio
async def test_unscoped_regular_user_is_denied(monkeypatch) -> None:
    monkeypatch.setattr(router_module, "ScopeService", lambda: _Scope())
    with pytest.raises(HTTPException) as exc:
        await router_module.get_my_effective_entitlements(_user(roles=["orguser"]))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_entitlements_are_explicitly_unrestricted(monkeypatch) -> None:
    monkeypatch.setattr(router_module, "ScopeService", lambda: _Scope())
    result = await router_module.get_my_effective_entitlements(_user(roles=["superadmin"]))
    assert result["features"] == {"*": True}
    assert result["contract_version"] == PERMISSION_CONTRACT_VERSION
