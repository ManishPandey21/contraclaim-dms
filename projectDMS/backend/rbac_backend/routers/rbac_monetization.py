from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.rbac_monetization import (
    AddOnCreate,
    AddOnUpdate,
    AddOnActionRequest,
    BillingRecordCreate,
    CancelSubscriptionRequest,
    ChangeBillingPeriodRequest,
    ConvertTrialRequest,
    ExpertAllocationCreate,
    ExpertAllocationUpdate,
    PlanSettingsScopeUpdate,
    PlanCreate,
    PlanUpdate,
    StartTrialRequest,
    SubscriptionCreate,
    SubscriptionUpdate,
    UpgradeDowngradeRequest,
    UsageEventCreate,
)
from ..services.allocation_service import AllocationService
from ..services.monetization_service import MonetizationService
from ..services.policy_service import PolicyService
from ..services.scope_service import ScopeService
from ..services.step_up_service import require_step_up
from ..utils.rate_limiter import RateLimiter

router = APIRouter(prefix="/rbac-monetization", tags=["rbac-monetization"])

# Rate limiter for monetization mutations (billing, subscriptions)
monetization_mutation_limiter = RateLimiter(max_requests=30, window_seconds=60, scope="monetization")


async def check_monetization_rate_limit(current_user: CurrentUser = Depends(get_current_user)):
    await monetization_mutation_limiter.check_user_limit(current_user.id)


async def get_policy_service() -> PolicyService:
    return PolicyService()


ORG_SUBSCRIPTION_ADMIN_ROLES = {"orgadmin"}
ORG_SUBSCRIPTION_VIEW_ROLES = {
    "orgadmin",
    "orguser",
    "contractmgr_org",
    "doccontroller",
    "reporter",
    "settings_manager",
}
PROJECT_SUBSCRIPTION_ADMIN_ROLES = {"projectadmin"}
PROJECT_SUBSCRIPTION_VIEW_ROLES = {"projectadmin", "projectuser"}


def _role_names(current_user: CurrentUser) -> set[str]:
    return ScopeService.role_names(current_user)


def _has_org_subscription_view(roles: set[str]) -> bool:
    return bool(roles & ORG_SUBSCRIPTION_VIEW_ROLES)


def _has_project_subscription_view(roles: set[str]) -> bool:
    return bool(roles & PROJECT_SUBSCRIPTION_VIEW_ROLES)


async def _subscription_scope_filters(
    current_user: CurrentUser,
) -> tuple[Optional[List[str]], Optional[List[str]]]:
    """Return organization/project filters for non-superadmin monetization reads.

    ``project_ids is None`` means organization-wide visibility within the
    returned organizations. A concrete project list means project-only visibility,
    used for project admins/users so an organization id on the user profile does
    not accidentally expose sibling project subscriptions.
    """
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return None, None

    roles = _role_names(current_user)
    allowed_orgs = await scope.client_organization_ids(current_user)
    allowed_projects = await scope.client_project_ids(current_user)

    if _has_org_subscription_view(roles) and allowed_orgs:
        return sorted(allowed_orgs), None
    if _has_project_subscription_view(roles) and allowed_projects:
        return sorted(allowed_orgs) if allowed_orgs else None, sorted(allowed_projects)
    if allowed_projects:
        return sorted(allowed_orgs) if allowed_orgs else None, sorted(allowed_projects)
    if allowed_orgs:
        return sorted(allowed_orgs), None
    return [], []


async def _authorize_checkout_scope(
    payload: "CheckoutRequest",
    current_user: CurrentUser,
    policy: PolicyService,
) -> None:
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return

    roles = _role_names(current_user)
    has_subscription_admin_permission = await policy.has_permission(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
    ) or await policy.has_permission(current_user, Permissions.SUBSCRIPTION_UPGRADE)

    if roles & ORG_SUBSCRIPTION_ADMIN_ROLES:
        if await scope.is_client_scope_allowed(
            current_user,
            organization_id=payload.organization_id,
            project_id=payload.project_id,
        ):
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription scope denied")

    if roles & PROJECT_SUBSCRIPTION_ADMIN_ROLES:
        if not payload.project_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project administrators can change only project subscriptions",
            )
        project_ids = await scope.client_project_ids(current_user)
        if str(payload.project_id) in project_ids and await scope.is_client_scope_allowed(
            current_user,
            organization_id=payload.organization_id,
            project_id=payload.project_id,
        ):
            return
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription scope denied")

    if has_subscription_admin_permission:
        await policy.authorize(
            current_user,
            Permissions.SUBSCRIPTION_UPGRADE,
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            resource_type="subscription",
        )
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to change this subscription",
    )


async def _load_subscription_or_404(
    service: MonetizationService,
    subscription_id: str,
) -> Dict[str, Any]:
    subscription = await service.get_subscription_by_id(subscription_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    return subscription


async def _authorize_subscription_scope(
    *,
    policy: PolicyService,
    current_user: CurrentUser,
    permission: str,
    subscription: Dict[str, Any],
    subscription_id: str,
    resource_type: str = "subscription",
) -> None:
    organization_id = str(subscription.get("organization_id") or "").strip()
    project_id = str(subscription.get("project_id") or "").strip() or None
    if not organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscription has no organization scope",
        )
    await policy.authorize(
        current_user,
        permission,
        organization_id=organization_id,
        project_id=project_id,
        resource_type=resource_type,
        resource_id=subscription_id,
    )


# ---------------------------------------------------------------------------
# Expert Allocations (unchanged)
# ---------------------------------------------------------------------------

@router.get("/expert-allocations", response_model=List[Dict[str, Any]])
async def list_expert_allocations(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    expert_user_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await policy.authorize(
        current_user,
        Permissions.DRAFTING_REQUEST_ASSIGN,
        organization_id=organization_id,
        project_id=project_id,
        resource_type="expert_allocation",
    )
    return await AllocationService().list_allocations(
        organization_id=organization_id,
        project_id=project_id,
        expert_user_id=expert_user_id,
        status=status,
        limit=limit,
        skip=skip,
    )


@router.post("/expert-allocations", response_model=Dict[str, Any])
async def create_expert_allocation(
    payload: ExpertAllocationCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="expert_allocation.manage")
    await policy.authorize(
        current_user,
        Permissions.DRAFTING_REQUEST_ASSIGN,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="expert_allocation",
    )
    return await AllocationService().create_allocation(payload, current_user)


@router.put("/expert-allocations/{allocation_id}", response_model=Dict[str, Any])
async def update_expert_allocation(
    allocation_id: str,
    payload: ExpertAllocationUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="expert_allocation.manage")
    await policy.authorize(current_user, Permissions.DRAFTING_REQUEST_ASSIGN, resource_type="expert_allocation", resource_id=allocation_id)
    return await AllocationService().update_allocation(allocation_id, payload, current_user)


# ---------------------------------------------------------------------------
# Plan Catalog (public-ish — requires basic auth only)
# ---------------------------------------------------------------------------

@router.get("/plan-catalog", response_model=Dict[str, Any])
async def get_plan_catalog(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Public plan catalog with pricing tiers and add-on info."""
    return await MonetizationService().get_plan_catalog()


@router.get("/plans", response_model=List[Dict[str, Any]])
async def list_plans(
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await policy.authorize(current_user, Permissions.BILLING_PLAN_VIEW, resource_type="plan")
    return await MonetizationService().list_plans()


@router.get("/plans/{plan_code}", response_model=Dict[str, Any])
async def get_plan_detail(
    plan_code: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get single plan detail by code."""
    plan = await MonetizationService().get_plan_by_code(plan_code)
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return plan


# ---------------------------------------------------------------------------
# Plan Settings (enhanced with billing period / trial / add-on state)
# ---------------------------------------------------------------------------

@router.get("/plan-settings", response_model=Dict[str, Any])
async def get_plan_settings(
    current_user: CurrentUser = Depends(get_current_user),
):
    service = MonetizationService()
    if ScopeService().is_superadmin(current_user):
        return await service.get_plan_settings()

    organization_ids, project_ids = await _subscription_scope_filters(current_user)
    if not organization_ids and not project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No subscription scope available",
        )
    return await service.get_plan_settings(
        organization_ids=organization_ids,
        project_ids=project_ids,
    )


@router.get("/plan-settings/effective-services", response_model=Dict[str, Any])
async def get_effective_plan_services(
    current_user: CurrentUser = Depends(get_current_user),
):
    settings = await MonetizationService().get_plan_settings()
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return {"effective": settings.get("effective", {})}

    if await PolicyService().has_permission(current_user, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE):
        return {"effective": settings.get("effective", {})}

    allowed_orgs = await scope.client_organization_ids(current_user)
    allowed_projects = await scope.client_project_ids(current_user)
    roles = scope.role_names(current_user)
    project_orgs = {
        str(project.get("id") or project.get("_id") or ""): str(
            project.get("organization_id") or project.get("organizationId") or ""
        )
        for project in settings.get("projects", [])
    }
    if roles & {"orgadmin", "orguser"}:
        allowed_projects.update(
            project_id
            for project_id, org_id in project_orgs.items()
            if org_id in allowed_orgs
        )

    effective = settings.get("effective", {})
    return {
        "effective": {
            "organizations": {
                org_id: state
                for org_id, state in (effective.get("organizations") or {}).items()
                if org_id in allowed_orgs
            },
            "projects": {
                project_id: state
                for project_id, state in (effective.get("projects") or {}).items()
                if project_id in allowed_projects
            },
        }
    }


@router.put("/plan-settings/scope", response_model=Dict[str, Any])
async def update_plan_settings_scope(
    payload: PlanSettingsScopeUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superadmins can change plans from Plan Settings",
        )
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="plan_settings",
    )
    try:
        return await MonetizationService().update_plan_settings_scope(payload, current_user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ---------------------------------------------------------------------------
# Plan CRUD (admin)
# ---------------------------------------------------------------------------

@router.post("/plans", response_model=Dict[str, Any])
async def upsert_plan(
    payload: PlanCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(current_user, Permissions.BILLING_PLAN_MANAGE, resource_type="plan", resource_id=payload.code)
    return await MonetizationService().upsert_plan(payload, current_user)


@router.put("/plans/{plan_id}", response_model=Dict[str, Any])
async def update_plan(
    plan_id: str,
    payload: PlanUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(current_user, Permissions.BILLING_PLAN_MANAGE, resource_type="plan", resource_id=plan_id)
    return await MonetizationService().update_plan(plan_id, payload, current_user)


# ---------------------------------------------------------------------------
# Add-On CRUD
# ---------------------------------------------------------------------------

@router.get("/add-ons", response_model=List[Dict[str, Any]])
async def list_addons(
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all available add-ons."""
    return await MonetizationService().list_addons()


@router.post("/add-ons", response_model=Dict[str, Any])
async def create_addon(
    payload: AddOnCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(current_user, Permissions.BILLING_PLAN_MANAGE, resource_type="addon", resource_id=payload.code)
    return await MonetizationService().upsert_addon(payload, current_user)


@router.put("/add-ons/{addon_id}", response_model=Dict[str, Any])
async def update_addon(
    addon_id: str,
    payload: AddOnUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(current_user, Permissions.BILLING_PLAN_MANAGE, resource_type="addon", resource_id=addon_id)
    return await MonetizationService().update_addon(addon_id, payload, current_user)


# ---------------------------------------------------------------------------
# Subscriptions (CRUD + lifecycle)
# ---------------------------------------------------------------------------

@router.get("/subscriptions", response_model=List[Dict[str, Any]])
async def list_subscriptions(
    organization_id: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return await MonetizationService().list_subscriptions(organization_id)

    allowed_orgs, allowed_projects = await _subscription_scope_filters(current_user)
    if organization_id:
        if str(organization_id) not in {str(org_id) for org_id in allowed_orgs or []}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription scope denied")
        return await MonetizationService().list_subscriptions(
            organization_id=organization_id,
            project_ids=allowed_projects,
        )

    if not allowed_orgs and not allowed_projects:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization scope available for subscription usage",
        )
    return await MonetizationService().list_subscriptions(
        organization_ids=allowed_orgs,
        project_ids=allowed_projects,
    )


@router.get("/subscriptions/{subscription_id}", response_model=Dict[str, Any])
async def get_subscription(
    subscription_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get single subscription by ID."""
    svc = MonetizationService()
    sub = await svc.get_subscription_by_id(subscription_id)
    if not sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_USAGE_VIEW,
        organization_id=sub.get("organization_id"),
        project_id=sub.get("project_id"),
        resource_type="subscription",
    )
    return sub


@router.post("/subscriptions", response_model=Dict[str, Any])
async def create_subscription(
    payload: SubscriptionCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="subscription",
    )
    return await MonetizationService().create_subscription(payload, current_user)


class CheckoutRequest(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    plan_code: str
    billing_period: str = "monthly"
    customer_name: Optional[str] = Field(default=None, max_length=200)
    customer_email: Optional[str] = Field(default=None, max_length=200)


@router.post("/subscriptions/checkout", response_model=Dict[str, Any])
async def start_subscription_checkout(
    payload: CheckoutRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Provision the subscription on the payment gateway and return a checkout URL.

    The subscription is created in ``pending`` state; the billing webhook promotes
    it to ``active`` (and enables entitlements) once payment is captured.
    """
    await require_step_up(request, current_user, action="subscription.checkout")
    await _authorize_checkout_scope(payload, current_user, policy)
    try:
        return await MonetizationService().start_checkout(
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            plan_code=payload.plan_code,
            billing_period=payload.billing_period,
            current_user=current_user,
            customer_name=payload.customer_name,
            customer_email=payload.customer_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/subscriptions/{subscription_id}", response_model=Dict[str, Any])
async def update_subscription(
    subscription_id: str,
    payload: SubscriptionUpdate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(check_monetization_rate_limit),
):
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    return await service.update_subscription(subscription_id, payload, current_user)


# ---------------------------------------------------------------------------
# Subscription Lifecycle Endpoints
# ---------------------------------------------------------------------------

@router.post("/subscriptions/{subscription_id}/upgrade", response_model=Dict[str, Any])
async def upgrade_subscription(
    subscription_id: str,
    payload: UpgradeDowngradeRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(check_monetization_rate_limit),
):
    """Upgrade subscription to a higher-tier plan."""
    await require_step_up(request, current_user, action="subscription.upgrade")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_UPGRADE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.upgrade_subscription(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/downgrade", response_model=Dict[str, Any])
async def downgrade_subscription(
    subscription_id: str,
    payload: UpgradeDowngradeRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(check_monetization_rate_limit),
):
    """Downgrade subscription to a lower-tier plan (effective at period end)."""
    await require_step_up(request, current_user, action="subscription.downgrade")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_DOWNGRADE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.downgrade_subscription(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/cancel", response_model=Dict[str, Any])
async def cancel_subscription(
    subscription_id: str,
    payload: CancelSubscriptionRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(check_monetization_rate_limit),
):
    """Cancel a subscription (immediately or at period end)."""
    await require_step_up(request, current_user, action="subscription.cancel")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_CANCEL,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.cancel_subscription(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/reactivate", response_model=Dict[str, Any])
async def reactivate_subscription(
    subscription_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Reactivate a cancelled subscription."""
    await require_step_up(request, current_user, action="subscription.upgrade")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_UPGRADE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.reactivate_subscription(subscription_id, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/change-period", response_model=Dict[str, Any])
async def change_billing_period(
    subscription_id: str,
    payload: ChangeBillingPeriodRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Change subscription billing period (effective at next renewal)."""
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.change_billing_period(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/add-ons/add", response_model=Dict[str, Any])
async def add_subscription_addon(
    subscription_id: str,
    payload: AddOnActionRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Add an add-on to a subscription."""
    await require_step_up(request, current_user, action="subscription.addon.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_ADDON_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.add_addon(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/add-ons/remove", response_model=Dict[str, Any])
async def remove_subscription_addon(
    subscription_id: str,
    payload: AddOnActionRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Remove an add-on from a subscription."""
    await require_step_up(request, current_user, action="subscription.addon.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_ADDON_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.remove_addon(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Subscription History
# ---------------------------------------------------------------------------

@router.get("/subscriptions/{subscription_id}/history", response_model=List[Dict[str, Any]])
async def get_subscription_history(
    subscription_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get the full history of a subscription."""
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_HISTORY_VIEW,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    return await service.get_subscription_history(subscription_id)


@router.get("/subscriptions/{subscription_id}/invoice-preview", response_model=Dict[str, Any])
async def get_invoice_preview(
    subscription_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Preview the next invoice for a subscription."""
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.BILLING_INVOICE_VIEW,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.get_invoice_preview(subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Trial Management
# ---------------------------------------------------------------------------

@router.post("/subscriptions/start-trial", response_model=Dict[str, Any])
async def start_trial(
    payload: StartTrialRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Start a trial subscription for an org or project."""
    await require_step_up(request, current_user, action="subscription.trial.manage")
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_TRIAL_MANAGE,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="subscription",
    )
    try:
        return await MonetizationService().start_trial(payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/subscriptions/{subscription_id}/convert-trial", response_model=Dict[str, Any])
async def convert_trial(
    subscription_id: str,
    payload: ConvertTrialRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Convert a trial subscription into a paid subscription."""
    await require_step_up(request, current_user, action="subscription.trial.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_TRIAL_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    try:
        return await service.convert_trial(subscription_id, payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Billing Summary & Usage
# ---------------------------------------------------------------------------

@router.get("/billing/summary/{organization_id}", response_model=Dict[str, Any])
async def get_billing_summary(
    organization_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get billing summary for an organization."""
    await policy.authorize(
        current_user,
        Permissions.BILLING_PLAN_VIEW,
        organization_id=organization_id,
        resource_type="billing",
    )
    return await MonetizationService().get_billing_summary(organization_id)


@router.get("/billing/history/{organization_id}", response_model=List[Dict[str, Any]])
async def get_organization_billing_history(
    organization_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get all subscription history for an organization."""
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_HISTORY_VIEW,
        organization_id=organization_id,
        resource_type="subscription",
    )
    return await MonetizationService().get_organization_subscription_history(organization_id)


@router.get("/billing/records/{organization_id}", response_model=List[Dict[str, Any]])
async def get_organization_billing_records(
    organization_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Financial billing records (payments / failures / amount-mismatch review)."""
    await policy.authorize(
        current_user,
        Permissions.BILLING_PLAN_VIEW,
        organization_id=organization_id,
        resource_type="billing",
    )
    return await MonetizationService().get_organization_billing_records(organization_id)


@router.get("/billing/review-queue/{organization_id}", response_model=List[Dict[str, Any]])
async def get_billing_review_queue(
    organization_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Failed-payment / amount-mismatch records needing admin attention."""
    await policy.authorize(
        current_user,
        Permissions.BILLING_PLAN_VIEW,
        organization_id=organization_id,
        resource_type="billing",
    )
    return await MonetizationService().get_billing_review_queue(organization_id)


@router.get("/billing/records/{record_id}/receipt")
async def download_billing_receipt(
    record_id: str,
    organization_id: str = Query(..., min_length=1),
    format: str = Query("receipt", pattern="^(receipt|tax_invoice)$"),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Download a print-optimized receipt or GST tax invoice for a paid record."""
    await policy.authorize(
        current_user,
        Permissions.BILLING_PLAN_VIEW,
        organization_id=organization_id,
        resource_type="billing",
    )
    from ..services.billing_receipt import receipt_to_html, tax_invoice_to_html

    svc = MonetizationService()
    try:
        if format == "tax_invoice":
            data = await svc.build_billing_tax_invoice(record_id, organization_id)
            render, prefix = tax_invoice_to_html, "tax-invoice"
        else:
            data = await svc.build_billing_receipt(record_id, organization_id)
            render, prefix = receipt_to_html, "receipt"
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Billing record not found")
    ref = data.get("invoice_number") or data.get("receipt_no") or record_id
    return Response(
        content=render(data),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{prefix}-{ref}.html"'},
    )


# ---------------------------------------------------------------------------
# Usage & Billing Records (unchanged)
# ---------------------------------------------------------------------------

@router.post("/usage-events", response_model=Dict[str, Any])
async def record_usage(
    payload: UsageEventCreate,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    # Usage recording is a write operation — require entitlement manage permission
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="usage_event",
    )
    return await MonetizationService().record_usage(payload, current_user)


@router.post("/billing-records", response_model=Dict[str, Any])
async def create_billing_record(
    payload: BillingRecordCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(
        current_user,
        Permissions.BILLING_PLAN_MANAGE,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        resource_type="billing_record",
    )
    return await MonetizationService().create_billing_record(payload, current_user)
