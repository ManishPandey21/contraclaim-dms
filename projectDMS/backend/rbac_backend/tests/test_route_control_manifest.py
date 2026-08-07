from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from rbac_backend.services.policy_service import PolicyService
from scripts.rbac_phase0_route_inventory import build_route_control_manifest


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "route_control_manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
ROUTES = MANIFEST["routes"]


def _key(item: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    return item["path"], item["name"], tuple(item["methods"])


def test_checked_in_route_control_manifest_matches_live_application() -> None:
    """A route addition/change cannot silently bypass the semantic contract."""

    live = [item.__dict__ for item in build_route_control_manifest()]
    assert ROUTES == json.loads(json.dumps(live))
    assert MANIFEST["summary"]["total_api_routes"] == len(live)


def test_route_control_manifest_has_one_complete_shape_per_live_route() -> None:
    keys = [_key(item) for item in ROUTES]
    assert len(keys) == len(set(keys))

    required_fields = {
        "authentication_mode",
        "active_user_required",
        "permissions",
        "entitlement_requirement",
        "scope_sources",
        "target_load_required",
        "target_load_evidence",
        "step_up_required",
        "audit_required",
        "quota_required",
        "enforcement",
        "wrong_tenant_test",
        "status",
    }
    for item in ROUTES:
        assert required_fields <= set(item), _key(item)
        assert item["authentication_mode"] in {
            "public",
            "provider_signature",
            "service_token",
            "session",
        }
        assert item["entitlement_requirement"] in {
            "required",
            "not_commercial",
            "undeclared",
        }
        if item["status"] == "complete" and item["enforcement"] == "central_policy":
            assert item["permissions"], _key(item)


def test_unsafe_non_policy_routes_are_explicitly_marked_for_migration() -> None:
    for item in ROUTES:
        if not item["unsafe"] or item["status"] != "migration_required":
            continue
        assert item["enforcement"] in {
            "authentication_only",
            "legacy_permission",
        }, _key(item)


class _SelectedPermissionService:
    def __init__(self, allowed: str) -> None:
        self.allowed = allowed

    async def user_has_permission(self, _user_id, permission, **_kwargs) -> bool:
        return permission == self.allowed


class _WrongTenantScopeService:
    @staticmethod
    def is_superadmin(_user: Any) -> bool:
        return False

    async def is_client_scope_allowed(self, *_args, **_kwargs) -> bool:
        return False

    async def has_expert_allocation(self, *_args, **_kwargs) -> bool:
        return False


class _AllowEntitlementService:
    async def check_permission_entitlement(self, **_kwargs) -> tuple[bool, str]:
        return True, "test_entitlement"


class _AuditRecorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, **event: Any) -> None:
        self.events.append(event)


TENANT_POLICY_CASES = [
    pytest.param(item, id=f"{','.join(item['methods'])}:{item['path']}:{item['name']}")
    for item in ROUTES
    if item["status"] == "complete"
    and item["enforcement"] == "central_policy"
    and str(item["wrong_tenant_test"]).startswith("policy_contract:")
]


@pytest.mark.anyio
@pytest.mark.parametrize("contract", TENANT_POLICY_CASES)
async def test_route_policy_contract_denies_wrong_tenant(contract: dict[str, Any]) -> None:
    """Execute the central decision represented by each scoped route contract."""

    current_user = SimpleNamespace(id="contract-user", roles=["projectuser"])

    global_permissions = {
        "dms.admin",
        "drafting.admin",
        "billing.plan.manage",
        "subscription.entitlement.manage",
    }
    candidates = [
        permission
        for permission in contract["permissions"]
        if permission not in global_permissions
    ] or list(contract["permissions"])

    for permission in candidates:
        audit = _AuditRecorder()
        policy = PolicyService(
            permission_service=_SelectedPermissionService(permission),
            scope_service=_WrongTenantScopeService(),
            entitlement_service=_AllowEntitlementService(),
            audit_service=audit,
        )

        with pytest.raises(HTTPException) as exc:
            await policy.authorize(
                current_user,
                permission,
                organization_id="wrong-organization",
                project_id="wrong-project",
                resource_type="route_contract",
                resource_id=contract["name"],
            )

        assert exc.value.status_code == 403
        assert audit.events[-1]["result"] == "deny"
        assert audit.events[-1]["organization_id"] == "wrong-organization"
        assert audit.events[-1]["project_id"] == "wrong-project"
