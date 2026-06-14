from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.models.rbac_monetization import AccountType
from rbac_backend.routers.storage_sync import _require_superadmin_user
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.services.step_up_service import StepUpService, require_step_up
from rbac_backend.utils.rate_limiter import RateLimiter


class _PermissionService:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed

    async def user_has_permission(self, *_args, **_kwargs) -> bool:
        return self.allowed


class _EntitlementService:
    def __init__(self, allowed: bool = True, reason: str = "ok") -> None:
        self.allowed = allowed
        self.reason = reason
        self.calls = 0

    async def check_permission_entitlement(self, **_kwargs):
        self.calls += 1
        return self.allowed, self.reason


class _ScopeService:
    def __init__(self, *, superadmin: bool = False, scope_allowed: bool = True) -> None:
        self.superadmin = superadmin
        self.scope_allowed = scope_allowed

    def is_superadmin(self, _user) -> bool:
        return self.superadmin

    async def is_client_scope_allowed(self, *_args, **_kwargs) -> bool:
        return self.scope_allowed

    async def has_expert_allocation(self, *_args, **_kwargs) -> bool:
        return False


class _AuditService:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


def _user(*, roles=None, account_type=AccountType.CLIENT_USER.value):
    return SimpleNamespace(
        id="user-1",
        roles=roles or ["projectuser"],
        account_type=account_type,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permission,entitlement_ok,scope_ok,should_allow",
    [
        ("dms.document.upload", True, True, True),
        ("dms.document.upload", False, True, False),
        ("dms.document.upload", True, False, False),
        ("drafting.request.create", True, True, True),
        ("drafting.request.create", False, True, False),
        ("subscription.usage.view", True, True, True),
        ("subscription.usage.view", True, False, False),
    ],
)
async def test_role_service_scope_direct_policy_matrix(
    permission: str,
    entitlement_ok: bool,
    scope_ok: bool,
    should_allow: bool,
) -> None:
    policy = PolicyService(
        permission_service=_PermissionService(True),
        scope_service=_ScopeService(scope_allowed=scope_ok),
        entitlement_service=_EntitlementService(entitlement_ok, "matrix_denied"),
        audit_service=_AuditService(),
    )

    if should_allow:
        await policy.authorize(
            _user(),
            permission,
            organization_id="org-1",
            project_id="project-1",
        )
    else:
        with pytest.raises(HTTPException):
            await policy.authorize(
                _user(),
                permission,
                organization_id="org-1",
                project_id="project-1",
            )


@pytest.mark.asyncio
async def test_superadmin_bypasses_entitlement_but_is_audited() -> None:
    entitlement = _EntitlementService(False, "no_active_subscription")
    audit = _AuditService()
    policy = PolicyService(
        permission_service=_PermissionService(False),
        scope_service=_ScopeService(superadmin=True),
        entitlement_service=entitlement,
        audit_service=audit,
    )

    await policy.authorize(_user(roles=["superadmin"]), "drafting.review.approve")

    assert entitlement.calls == 0
    assert audit.events[-1]["result"] == "allow"
    assert audit.events[-1]["reason"] == "superadmin"


@pytest.mark.asyncio
async def test_billing_usage_requires_scope_for_non_admin() -> None:
    policy = PolicyService(
        permission_service=_PermissionService(True),
        scope_service=_ScopeService(scope_allowed=True),
        entitlement_service=_EntitlementService(True),
        audit_service=_AuditService(),
    )

    with pytest.raises(HTTPException) as exc:
        await policy.authorize(_user(), "subscription.usage.view")

    assert exc.value.status_code == 403
    assert "scope" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_billing_usage_allows_valid_organization_scope() -> None:
    audit = _AuditService()
    policy = PolicyService(
        permission_service=_PermissionService(True),
        scope_service=_ScopeService(scope_allowed=True),
        entitlement_service=_EntitlementService(True),
        audit_service=audit,
    )

    await policy.authorize(
        _user(),
        "subscription.usage.view",
        organization_id="org-1",
        resource_type="subscription",
    )

    assert audit.events[-1]["result"] == "allow"
    assert audit.events[-1]["reason"] == "billing_scope"


@pytest.mark.asyncio
async def test_policy_denies_platform_permission_for_non_superadmin() -> None:
    policy = PolicyService(
        permission_service=_PermissionService(True),
        scope_service=_ScopeService(scope_allowed=True),
        entitlement_service=_EntitlementService(True),
        audit_service=_AuditService(),
    )

    with pytest.raises(HTTPException) as exc:
        await policy.authorize(_user(), "platform.admin")

    assert exc.value.status_code == 403
    assert "platform" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_document_policy_denies_foreign_document_scope() -> None:
    policy = PolicyService(
        permission_service=_PermissionService(True),
        scope_service=_ScopeService(scope_allowed=False),
        entitlement_service=_EntitlementService(True),
        audit_service=_AuditService(),
    )

    with pytest.raises(HTTPException) as exc:
        await policy.authorize_document(
            _user(),
            Permissions.DOCUMENT_VIEW,
            {
                "_id": "doc-1",
                "organization_id": "other-org",
                "project_id": "other-project",
            },
        )

    assert exc.value.status_code == 403
    assert "scope" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_storage_admin_dependency_rejects_non_superadmin() -> None:
    with pytest.raises(HTTPException) as exc:
        await _require_superadmin_user(_user(roles=["orgadmin"]))

    assert exc.value.status_code == 403
    assert "superadmin" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_entitlement_does_not_require_subscription_for_non_service_permission() -> None:
    service = EntitlementService(db=None)

    allowed, reason = await service.check_permission_entitlement(
        permission="billing.plan.manage",
        organization_id=None,
        project_id=None,
    )

    assert allowed is True
    assert reason == "not_entitlement_scoped"


@pytest.mark.asyncio
async def test_org_level_expert_allocation_covers_project_work() -> None:
    scope = ScopeService(db=None)

    async def _allocations(*_args, **_kwargs):
        return [
            {
                "assignment_role": "reviewer",
                "organization_id": "org-1",
                "project_id": "",
                "permissions_granted": [],
            }
        ]

    scope.active_expert_allocations = _allocations

    allowed = await scope.has_expert_allocation(
        _user(account_type=AccountType.CONTRACLAIM_STAFF.value),
        permission="drafting.review.perform",
        organization_id="org-1",
        project_id="project-1",
    )

    assert allowed is True


def test_step_up_token_is_action_bound() -> None:
    service = StepUpService()
    token = service.create_token(user_id="user-1", action="subscription.entitlement.manage")

    service.verify_token(
        token=token,
        user_id="user-1",
        action="subscription.entitlement.manage",
    )
    with pytest.raises(HTTPException) as exc:
        service.verify_token(token=token, user_id="user-1", action="billing.plan.manage")

    assert exc.value.status_code == 403


def test_step_up_token_is_user_bound() -> None:
    service = StepUpService()
    token = service.create_token(user_id="user-1", action="*")

    with pytest.raises(HTTPException) as exc:
        service.verify_token(token=token, user_id="user-2", action="billing.plan.manage")

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_step_up_missing_token_is_denied() -> None:
    request = SimpleNamespace(headers={})

    with pytest.raises(HTTPException) as exc:
        await require_step_up(request, _user(), action="smtp.manage")

    assert exc.value.status_code == 403
    assert "step-up" in str(exc.value.detail).lower()


@pytest.mark.asyncio
async def test_public_share_token_rate_limit_bucket_is_enforced() -> None:
    RateLimiter._shared_buckets.clear()
    limiter = RateLimiter(requests_per_minute=2, window_seconds=60)

    await limiter.check_client_limit("share-token:test", max_requests=2, window_seconds=60)
    await limiter.check_client_limit("share-token:test", max_requests=2, window_seconds=60)
    with pytest.raises(HTTPException) as exc:
        await limiter.check_client_limit("share-token:test", max_requests=2, window_seconds=60)

    assert exc.value.status_code == 429
