from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.rbac_monetization import (
    BillingRecordCreate,
    ExpertAllocationCreate,
    ExpertAllocationUpdate,
    PlanSettingsScopeUpdate,
    PlanCreate,
    PlanUpdate,
    SubscriptionCreate,
    SubscriptionUpdate,
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
monetization_mutation_limiter = RateLimiter(requests_per_minute=30, window_seconds=60)


async def check_monetization_rate_limit(current_user: CurrentUser = Depends(get_current_user)):
    await monetization_mutation_limiter.check_user_limit(current_user.id)


async def get_policy_service() -> PolicyService:
    return PolicyService()


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


@router.get("/plans", response_model=List[Dict[str, Any]])
async def list_plans(
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await policy.authorize(current_user, Permissions.BILLING_PLAN_VIEW, resource_type="plan")
    return await MonetizationService().list_plans()


@router.get("/plan-settings", response_model=Dict[str, Any])
async def get_plan_settings(
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        resource_type="plan_settings",
    )
    return await MonetizationService().get_plan_settings()


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


@router.get("/subscriptions", response_model=List[Dict[str, Any]])
async def list_subscriptions(
    organization_id: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    scope = ScopeService()
    if scope.is_superadmin(current_user) or await policy.has_permission(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
    ):
        return await MonetizationService().list_subscriptions(organization_id)

    allowed_orgs = await scope.client_organization_ids(current_user)
    if organization_id:
        await policy.authorize(
            current_user,
            Permissions.SUBSCRIPTION_USAGE_VIEW,
            organization_id=organization_id,
            resource_type="subscription",
        )
        return await MonetizationService().list_subscriptions(organization_id)

    if not allowed_orgs:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization scope available for subscription usage",
        )
    return await MonetizationService().list_subscriptions(
        organization_ids=sorted(allowed_orgs)
    )


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
    await policy.authorize(current_user, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE, resource_type="subscription", resource_id=subscription_id)

    # Tenant isolation: verify the subscription belongs to the caller's authorized scope
    from ..core.database import get_database
    db = await get_database()
    from bson import ObjectId
    try:
        lookup = ObjectId(subscription_id)
    except Exception:
        lookup = subscription_id
    existing_sub = await db.subscriptions.find_one({"_id": lookup})
    if not existing_sub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    scope = ScopeService()
    if not scope.is_superadmin(current_user):
        sub_org = str(existing_sub.get("organization_id") or "")
        if not await scope.is_client_scope_allowed(current_user, organization_id=sub_org):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this subscription")

    return await MonetizationService().update_subscription(subscription_id, payload, current_user)


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
