from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..core.config import settings
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
from ..services.subscription_operations import IdempotencyKeyReused, OperationConflict
from ..services.monetization_service import (
    PROTECTED_GATEWAY_FIELDS,
    GatewayIdentityChangeRefused,
    GatewayIdentityConflict,
    MonetizationService,
    SubscriptionScopeChangeRefused,
    GatewayOperationFailed,
    PurchaseInProgress,
    SubscriptionNotGoverning,
    SubscriptionOperationIncomplete,
    SubscriptionReactivationRefused,
    TrialConversionRefused,
    SubscriptionScopeConflict,
    scope_text,
    subscription_scope,
)
from ..services.permission_service import PermissionService
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


# ---------------------------------------------------------------------------
# DEF-4 target policy (BILLING_EXPLICIT_PERMISSIONS). Off: none of this runs.
# ---------------------------------------------------------------------------

#: A subscription as operational status: what is on, until when. No billing status,
#: billing period, payment-gateway ids, overrides or audit fields (Decision 1, item 3).
SUBSCRIPTION_STATUS_FIELDS = (
    "id", "organization_id", "project_id", "plan_code", "status", "starts_at", "ends_at",
    "current_period_end", "trial", "trial_ends_at", "active_add_ons",
)
#: Service availability a user needs to use the application (Decision 2), with expiry
#: (decision 4, 2026-11-20). ``features`` is reduced to boolean indicators.
OPERATIONAL_SERVICE_FIELDS = (
    "source", "status", "dms_enabled", "drafting_enabled", "features", "current_period_end", "trial", "trial_ends_at",
)
#: Plus what a subscription-status holder sees (Decision 1: subscription, entitlements, expiry).
#: A project-tier caller is cut further to the operational view (decision 4).
STATUS_SERVICE_FIELDS = OPERATIONAL_SERVICE_FIELDS + (
    "plan_code", "plan_name", "active_add_ons", "inherited_from_organization_id",
)


def _explicit_billing_policy() -> bool:
    return bool(settings.BILLING_EXPLICIT_PERMISSIONS)


def _only(row: Dict[str, Any], fields: tuple) -> Dict[str, Any]:
    return {key: row.get(key) for key in fields if key in row}


# ---------------------------------------------------------------------------
# Operational minimum disclosure (owner decision 4, 2026-11-20). Applies in both
# BILLING_EXPLICIT_PERMISSIONS states: it narrows what is shown, it activates nothing.
#
# A Project Admin or Project User (a project-tier role without organisation-wide scope in
# the organisation concerned) sees, for their own projects only: service status, enabled
# feature indicators and expiry. Never prices, billing status or period, payment-gateway
# ids, raw entitlement_overrides, add-on or plan commercial detail, audit fields, or any
# other organisation's or project's data.
# ---------------------------------------------------------------------------

#: A subscription row, as a project-tier caller sees it.
PROJECT_TIER_SUBSCRIPTION_FIELDS = (
    "id", "organization_id", "project_id", "status", "starts_at", "ends_at",
    "current_period_end", "trial", "trial_ends_at",
)
#: An effective service state, as a project-tier caller sees it (``features`` is added,
#: reduced to boolean indicators by ``feature_indicators``).
PROJECT_TIER_SERVICE_FIELDS = (
    "source", "status", "dms_enabled", "drafting_enabled", "current_period_end", "trial", "trial_ends_at",
)


def feature_indicators(features: Any) -> Dict[str, bool]:
    """Feature flags as booleans only. The merged plan features carry the subscription's
    raw ``entitlement_overrides``, which may hold limits, prices or negotiated terms: a
    non-boolean value is dropped, never shown."""
    if not isinstance(features, dict):
        return {}
    return {str(key): value for key, value in features.items() if isinstance(value, bool)}


def _operational_state(state: Dict[str, Any]) -> Dict[str, Any]:
    reduced = _only(state, PROJECT_TIER_SERVICE_FIELDS)
    reduced["features"] = feature_indicators(state.get("features"))
    return reduced


def _operational_subscription(row: Dict[str, Any]) -> Dict[str, Any]:
    return _only(row, PROJECT_TIER_SUBSCRIPTION_FIELDS)


async def _project_tier_in(current_user: CurrentUser, organization_id: Optional[str]) -> bool:
    """Whether the caller gets only the operational view in this organisation (decision 4).

    Flag off: anyone without organisation-level standing there
    (``PolicyService.has_organisation_level_standing``): Project Admin / User, a limited or
    plain user, a custom project role. Decided by standing, never by the presence of a
    project role name, so an unlisted project-level role fails closed (independent review).
    Flag on: grants already cut every list; Project Admin / User are cut further, as
    decision 4 requires even with subscription.status.view. Super Admin never."""
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return False
    if _explicit_billing_policy():
        if not (scope.role_names(current_user) & PROJECT_SUBSCRIPTION_VIEW_ROLES):
            return False
        if await scope.has_organization_wide_scope(current_user, organization_id=organization_id):
            return False
        # Review F4: a custom organisation role granting the financial view stands for the
        # organisation (the N1 rule), even for a holder of a project role.
        return not (
            organization_id
            and await PermissionService().holds_organisation_level_custom_role(
                str(getattr(current_user, "id", "") or ""), str(organization_id), Permissions.SUBSCRIPTION_HISTORY_VIEW
            )
        )
    return not await PolicyService().has_organisation_level_standing(current_user, organization_id)


async def _minimise_rows(current_user: CurrentUser, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cache: Dict[str, bool] = {}
    result = []
    for row in rows:
        org = str(row.get("organization_id") or "")
        if org not in cache:
            cache[org] = await _project_tier_in(current_user, org or None)
        result.append(_operational_subscription(row) if cache[org] else row)
    return result


async def _minimise_effective(
    current_user: CurrentUser, effective: Dict[str, Any], project_orgs: Dict[str, str]
) -> Dict[str, Any]:
    cache: Dict[str, bool] = {}

    async def tier(org: str) -> bool:
        if org not in cache:
            cache[org] = await _project_tier_in(current_user, org or None)
        return cache[org]

    organizations = {}
    for org, state in (effective.get("organizations") or {}).items():
        organizations[org] = _operational_state(state) if await tier(str(org)) else state
    projects = {}
    for project, state in (effective.get("projects") or {}).items():
        projects[project] = _operational_state(state) if await tier(project_orgs.get(str(project), "")) else state
    return {**effective, "organizations": organizations, "projects": projects}


def _project_orgs(settings_payload: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(project.get("id") or project.get("_id") or ""): str(
            project.get("organization_id") or project.get("organizationId") or ""
        )
        for project in settings_payload.get("projects", [])
    }


async def _granted_in(
    policy: PolicyService, current_user: CurrentUser, permission: str, *, organizations: set, projects: set
) -> tuple[set, set]:
    """The organisations and projects where ``permission`` is held under the flag-on rule.

    One evaluation for all scopes (``PolicyService.explicit_billing_scopes``), which also
    applies the project-tier rule: a Project Admin / Project User counts at organisation level
    only with organisation-wide scope, so their grant never shows the organisation's or
    sibling projects' subscriptions.
    """
    return await policy.explicit_billing_scopes(
        current_user, permission, organizations=organizations, projects=projects
    )


async def _visible_scope(current_user: CurrentUser, project_orgs: Dict[str, str]) -> tuple[set, set]:
    """Member organisations, and projects: assigned ones, plus every project of an
    organisation where the user has organisation-wide scope."""
    scope = ScopeService()
    organizations = set(await scope.client_organization_ids(current_user))
    projects = set(await scope.client_project_ids(current_user))
    for org in organizations:
        if await scope.has_organization_wide_scope(current_user, organization_id=org):
            projects.update(project for project, owner in project_orgs.items() if owner == org)
    return organizations, projects


async def _authorize_checkout_scope(
    payload: "CheckoutRequest",
    current_user: CurrentUser,
    policy: PolicyService,
) -> None:
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return
    if _explicit_billing_policy():
        # Decision 1: a commercially binding change needs explicit scoped authority, never a
        # role name (today Project Admin may check out for their project by role alone).
        await policy.authorize(
            current_user,
            Permissions.SUBSCRIPTION_UPGRADE,
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            resource_type="subscription",
        )
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
    if _explicit_billing_policy():
        # The full settings carry financial subscription detail: only organisations where the
        # caller holds subscription.history.view. Feature availability: effective-services.
        organizations = set(await ScopeService().client_organization_ids(current_user))
        policy = PolicyService()
        full_orgs, _ = await _granted_in(
            policy, current_user, Permissions.SUBSCRIPTION_HISTORY_VIEW, organizations=organizations, projects=set()
        )
        if not full_orgs:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription history permission required")
        return await _minimise_plan_settings(
            current_user, await service.get_plan_settings(organization_ids=sorted(full_orgs), project_ids=None)
        )

    organization_ids, project_ids = await _subscription_scope_filters(current_user)
    if not organization_ids and not project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No subscription scope available",
        )
    payload = await service.get_plan_settings(
        organization_ids=organization_ids,
        project_ids=project_ids,
    )
    return await _minimise_plan_settings(current_user, payload)


async def _minimise_plan_settings(current_user: CurrentUser, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Decision 4 for /plan-settings: every organisation, project, subscription row and service
    state is judged in its own organisation (review F1: no shortcut on the organisations
    listed, so an orphan project or a missing organisation fails closed). Where the caller
    has no standing: subscriptions and states cut to the operational view, organisations and
    projects to identity."""
    project_orgs = _project_orgs(payload)
    cache: Dict[str, bool] = {}

    async def cut_in(org: str) -> bool:
        if org not in cache:
            cache[org] = await _project_tier_in(current_user, org or None)
        return cache[org]

    identity = ("id", "name", "organization_id", "is_active")
    organizations = []
    for org in payload.get("organizations", []) or []:
        org_id = str(org.get("id") or org.get("_id") or "")
        organizations.append(_only(org, identity) if await cut_in(org_id) else org)
    projects = []
    for project in payload.get("projects", []) or []:
        org_id = project_orgs.get(str(project.get("id") or project.get("_id") or ""), "")
        projects.append(_only(project, identity) if await cut_in(org_id) else project)
    return {
        **payload,
        "organizations": organizations,
        "projects": projects,
        "subscriptions": await _minimise_rows(current_user, list(payload.get("subscriptions", []) or [])),
        "effective": await _minimise_effective(current_user, payload.get("effective", {}) or {}, project_orgs),
    }


@router.get("/plan-settings/effective-services", response_model=Dict[str, Any])
async def get_effective_plan_services(
    current_user: CurrentUser = Depends(get_current_user),
):
    settings = await MonetizationService().get_plan_settings()
    scope = ScopeService()
    if scope.is_superadmin(current_user):
        return {"effective": settings.get("effective", {})}
    if _explicit_billing_policy():
        return {"effective": await _minimal_effective_services(current_user, settings)}

    # A holder of subscription.entitlement.manage (the tenant billing administrator) used to
    # get every organisation's full service state here. It now sees its own organisations
    # and their projects, like an organisation administrator (decision 4: the feature-status
    # endpoint is never a route to another tenant's billing; M4: the tenant billing
    # administrator acts within their organisation). Super Admin is unchanged, above.
    entitlement_manager = await PolicyService().has_permission(current_user, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE)

    allowed_orgs = await scope.client_organization_ids(current_user)
    allowed_projects = await scope.client_project_ids(current_user)
    roles = scope.role_names(current_user)
    project_orgs = {
        str(project.get("id") or project.get("_id") or ""): str(
            project.get("organization_id") or project.get("organizationId") or ""
        )
        for project in settings.get("projects", [])
    }
    # Review F2: widen to an organisation's projects only where the caller stands for that
    # organisation (organisation-wide role there, or the billing administrator role / a
    # custom role of that organisation granting entitlement management), never to every
    # member organisation because a role somewhere grants entitlement management.
    caller_id = str(getattr(current_user, "id", "") or "")
    for org_id in sorted(allowed_orgs):
        widen = bool(roles & {"orgadmin", "orguser"}) and await scope.has_organization_wide_scope(current_user, organization_id=org_id)
        if not widen and entitlement_manager:
            widen = "contraclaim_billing_admin" in roles or await PermissionService().holds_organisation_level_custom_role(
                caller_id, org_id, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE
            )
        if widen:
            allowed_projects.update(project_id for project_id, owner in project_orgs.items() if owner == org_id)

    effective = settings.get("effective", {})
    scoped = {
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
    return {"effective": await _minimise_effective(current_user, scoped, project_orgs)}


async def _minimal_effective_services(current_user: CurrentUser, settings_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Decision 2: each member scope's service state, cut to what the caller may see there.

    Never an unscoped view (before, any holder of subscription.entitlement.manage, the
    billing administrator role included, saw every organisation). Per organisation and
    project: the full state with subscription.history.view, the status fields with
    subscription.status.view, otherwise only the availability needed to use the application.
    """
    effective = settings_payload.get("effective", {}) or {}
    project_orgs = {
        str(project.get("id") or project.get("_id") or ""): str(
            project.get("organization_id") or project.get("organizationId") or ""
        )
        for project in settings_payload.get("projects", [])
    }
    organizations, projects = await _visible_scope(current_user, project_orgs)
    policy = PolicyService()
    full_orgs, full_projects = await _granted_in(
        policy, current_user, Permissions.SUBSCRIPTION_HISTORY_VIEW, organizations=organizations, projects=projects
    )
    status_orgs, status_projects = await _granted_in(
        policy, current_user, Permissions.SUBSCRIPTION_STATUS_VIEW, organizations=organizations, projects=projects
    )

    def cut(state: Dict[str, Any], full: bool, status_view: bool) -> Dict[str, Any]:
        if full:
            return dict(state)
        reduced = _only(state, STATUS_SERVICE_FIELDS if status_view else OPERATIONAL_SERVICE_FIELDS)
        # Decision 4: indicators only; raw entitlement_overrides values never leave here.
        reduced["features"] = feature_indicators(state.get("features"))
        return reduced

    minimised = await _minimise_effective(current_user, {
        "organizations": {
            org: cut(state, org in full_orgs, org in status_orgs)
            for org, state in (effective.get("organizations") or {}).items()
            if org in organizations
        },
        "projects": {
            project: cut(state, project in full_projects, project in status_projects)
            for project, state in (effective.get("projects") or {}).items()
            if project in projects
        },
    }, project_orgs)
    return minimised


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
    key = (request.headers.get("Idempotency-Key") or "").strip() or None
    if key is not None and len(key) > 128:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is longer than 128 characters")
    try:
        return await MonetizationService().update_plan_settings_scope(payload, current_user, idempotency_key=key)
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SubscriptionOperationIncomplete as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": str(exc), "operation_id": exc.operation_id},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/plan-settings/operations", response_model=List[Dict[str, Any]])
async def list_open_subscription_operations(
    organization_id: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """R6-7: unfinished gateway-consistent operations (cancellations the gateway has not
    confirmed, subscriptions changed meanwhile, operations interrupted) for review. Super
    Admin only, as Plan Settings changes are."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can review plan operations")
    return await MonetizationService().list_open_subscription_operations(organization_id)


@router.post("/plan-settings/operations/{operation_id}/retry", response_model=Dict[str, Any])
async def retry_subscription_operation(
    operation_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """R6-7: continue an unfinished plan-settings operation where it stopped (confirmed
    gateway cancellations are not repeated). Same authority as the change itself."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can retry plan operations")
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    service = MonetizationService()
    stored = await service.get_subscription_operation(operation_id)
    if not stored:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    await policy.authorize(
        current_user,
        Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        organization_id=stored["organization_id"],
        project_id=stored.get("project_id"),
        resource_type="plan_settings",
    )
    if stored.get("kind") == "replacement" and (stored.get("items") or [{}])[0].get("gateway") != "outcome_unknown":
        # Workstream 1: continue a replacement whose source cancellation did not complete.
        if stored.get("status") != "needs_review":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This replacement is not awaiting review")
        return await service.complete_replacement(operation_id, actor=str(current_user.id))
    if stored.get("kind") in {"checkout", "replacement"}:
        # Workstream 4: an unanswered checkout create is reconciled by its reference at the
        # provider - never created again.
        if stored.get("status") != "needs_review":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This checkout is not awaiting review")
        if not await service.reconcile_checkout_operation(None, stored, current_user):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The provider shows no subscription for this checkout yet; it stays listed. "
                       "If the provider confirms none was created, resolve it explicitly",
            )
        return {"operation_id": operation_id, "status": "completed", "reconciled": True}
    if stored.get("kind") not in {"plan_settings_replacement", "plan_settings_inherit"}:
        # Review 7 R7-3: a lifecycle operation is resumed by repeating its own request with the
        # same Idempotency-Key (its route re-checks authority and state); not here.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is not a plan-settings operation; repeat the original request with its Idempotency-Key",
        )
    asked = stored.get("request") or {}
    try:
        payload = PlanSettingsScopeUpdate(
            organization_id=stored["organization_id"], project_id=stored.get("project_id"), mode=asked.get("mode"),
            plan_code=None if asked.get("mode") != "plan" else asked.get("plan_code"),
            billing_period=asked.get("billing_period"), status=asked.get("status") or "active",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="The stored operation cannot be retried") from exc
    try:
        return await service.update_plan_settings_scope(payload, current_user, resume_operation_id=operation_id)
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SubscriptionOperationIncomplete as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": str(exc), "operation_id": exc.operation_id},
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class CheckoutResolution(BaseModel):
    """Closing an unanswered checkout as never created at the provider (workstream 4)."""

    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(pattern="^not_created_at_provider$")
    reason: str = Field(min_length=10, max_length=500)
    provider_checked_manually: bool = False


@router.post("/plan-settings/operations/{operation_id}/resolve", response_model=Dict[str, Any])
async def resolve_unknown_checkout(
    operation_id: str,
    payload: CheckoutResolution,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Workstream 4: a Super Admin closes a checkout whose create the provider never
    answered, once the provider shows none was created (a fresh search runs first; where the
    provider cannot be searched, the administrator must state they checked it). Audited.
    Nothing is created, cancelled or refunded here."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can resolve plan operations")
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    service = MonetizationService()
    stored = await service.get_subscription_operation(operation_id)
    if not stored:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    await policy.authorize(
        current_user, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE, organization_id=stored["organization_id"],
        project_id=stored.get("project_id"), resource_type="plan_settings",
    )
    try:
        return await service.resolve_unknown_checkout(
            stored, current_user, reason=payload.reason, provider_checked_manually=payload.provider_checked_manually)
    except (OperationConflict, PurchaseInProgress) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/plan-settings/reconciliation/cancellations", response_model=Dict[str, Any])
async def reconcile_gateway_cancellations(
    organization_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Workstream 4 / R9-11: read-only comparison of the provider's state with locally
    cancelled, gateway-bound subscriptions (a scheduled cycle-end cancellation is not a
    failure). Super Admin only; reads the provider, writes nothing."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can reconcile billing")
    return await MonetizationService().reconcile_gateway_cancellations(organization_id, limit=limit)


class DiscrepancyResolution(BaseModel):
    """Workstream 2: a Super Admin's decision on one payment discrepancy."""

    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(settled|written_off|escalated)$")
    reason: str = Field(min_length=10, max_length=500)


@router.get("/billing/discrepancies", response_model=List[Dict[str, Any]])
async def list_payment_discrepancies(
    organization_id: Optional[str] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Workstream 2: open and escalated payment discrepancies (Super Admin only)."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can review payment discrepancies")
    from ..services import payment_discrepancy_service

    return await payment_discrepancy_service.list_open(await MonetizationService()._get_db(), organization_id)


@router.post("/billing/discrepancies/{discrepancy_id}/resolve", response_model=Dict[str, Any])
async def resolve_payment_discrepancy(
    discrepancy_id: str,
    payload: DiscrepancyResolution,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Workstream 2: settle, write off or escalate a payment discrepancy (Super Admin, step-up,
    audited). Nothing is refunded or charged; escalation never extends the grace period."""
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only superadmins can resolve payment discrepancies")
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    from ..services import payment_discrepancy_service
    from ..services.audit_event_service import AuditEventService

    db = await MonetizationService()._get_db()
    stored = await db[payment_discrepancy_service.COLLECTION].find_one({"_id": discrepancy_id})
    if not stored:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Discrepancy not found")
    await policy.authorize(current_user, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
                           organization_id=stored.get("organization_id"), project_id=stored.get("project_id"),
                           resource_type="payment_discrepancy")
    try:
        result = await payment_discrepancy_service.resolve(db, discrepancy_id, outcome=payload.outcome,
                                                           reason=payload.reason, actor=str(current_user.id))
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await AuditEventService(db).emit(
        action=f"billing.payment_discrepancy_{payload.outcome}", actor_id=str(current_user.id),
        resource_type="payment_discrepancy", resource_id=discrepancy_id,
        organization_id=stored.get("organization_id"), project_id=stored.get("project_id"),
        reason=payload.outcome, metadata={"subscription_id": stored.get("subscription_id"),
                                          "review_closed": result.get("review_closed")})
    return result


# ---------------------------------------------------------------------------
# Plan CRUD (admin)
#
# M4 (owner decision 2026-11-20): the plan, price and add-on catalogue is global, shared by
# every tenant. Writes need Super Admin or platform.pricing.manage on a platform role, never
# billing.plan.manage: a tenant billing administrator keeps that for their own organisation's
# billing operations (for example POST /billing-records, which is scoped). Step-up is
# unchanged (action "billing.plan.manage", the token the client already requests), and the
# policy decision and the write are both audited.
# ---------------------------------------------------------------------------

@router.post("/plans", response_model=Dict[str, Any])
async def upsert_plan(
    payload: PlanCreate,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await require_step_up(request, current_user, action="billing.plan.manage")
    await policy.authorize(current_user, Permissions.PLATFORM_PRICING_MANAGE, resource_type="plan", resource_id=payload.code)
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
    await policy.authorize(current_user, Permissions.PLATFORM_PRICING_MANAGE, resource_type="plan", resource_id=plan_id)
    try:
        return await MonetizationService().update_plan(plan_id, payload, current_user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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
    await policy.authorize(current_user, Permissions.PLATFORM_PRICING_MANAGE, resource_type="addon", resource_id=payload.code)
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
    await policy.authorize(current_user, Permissions.PLATFORM_PRICING_MANAGE, resource_type="addon", resource_id=addon_id)
    try:
        return await MonetizationService().update_addon(addon_id, payload, current_user)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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
    if _explicit_billing_policy():
        return await _explicit_subscription_list(current_user, policy, organization_id)

    allowed_orgs, allowed_projects = await _subscription_scope_filters(current_user)
    if organization_id:
        if str(organization_id) not in {str(org_id) for org_id in allowed_orgs or []}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription scope denied")
        return await _minimise_rows(current_user, await MonetizationService().list_subscriptions(
            organization_id=organization_id,
            project_ids=allowed_projects,
        ))

    if not allowed_orgs and not allowed_projects:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization scope available for subscription usage",
        )
    return await _minimise_rows(current_user, await MonetizationService().list_subscriptions(
        organization_ids=allowed_orgs,
        project_ids=allowed_projects,
    ))


async def _explicit_subscription_list(
    current_user: CurrentUser, policy: PolicyService, organization_id: Optional[str]
) -> List[Dict[str, Any]]:
    """DEF-4: full rows where subscription.history.view is held, status rows where only
    subscription.status.view is, nothing elsewhere; never by role name."""
    scope = ScopeService()
    organizations = set(await scope.client_organization_ids(current_user))
    projects = set(await scope.client_project_ids(current_user))
    if organization_id and str(organization_id) not in organizations:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription scope denied")
    if organization_id:
        organizations = {str(organization_id)}
    full_orgs, full_projects = await _granted_in(
        policy, current_user, Permissions.SUBSCRIPTION_HISTORY_VIEW, organizations=organizations, projects=projects
    )
    status_orgs, status_projects = await _granted_in(
        policy, current_user, Permissions.SUBSCRIPTION_STATUS_VIEW, organizations=organizations, projects=projects
    )
    if not (full_orgs or full_projects or status_orgs or status_projects):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Subscription view permission required")
    rows = await MonetizationService().list_subscriptions(organization_ids=sorted(organizations), project_ids=None)
    visible: List[Dict[str, Any]] = []
    for row in rows:
        org = str(row.get("organization_id") or "")
        project = str(row.get("project_id") or "") or None
        if org not in organizations:
            continue
        if project:
            full = project in full_projects or org in full_orgs
            status_view = project in status_projects or org in status_orgs
        else:
            full = org in full_orgs
            status_view = org in status_orgs
        if full:
            visible.append(row)
        elif status_view:
            visible.append(_only(row, SUBSCRIPTION_STATUS_FIELDS))
    return await _minimise_rows(current_user, visible)


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
    if _explicit_billing_policy() and not ScopeService().is_superadmin(current_user):
        # subscription.usage.view admits the read; what is shown follows the explicit grants
        # in this scope, as in the list (independent review: a custom project role holding
        # only the usage view received the full row).
        org = str(sub.get("organization_id") or "")
        project = str(sub.get("project_id") or "") or None
        projects = {project} if project else set()
        full_orgs, full_projects = await _granted_in(
            policy, current_user, Permissions.SUBSCRIPTION_HISTORY_VIEW, organizations={org}, projects=projects
        )
        status_orgs, status_projects = await _granted_in(
            policy, current_user, Permissions.SUBSCRIPTION_STATUS_VIEW, organizations={org}, projects=projects
        )
        if not (org in full_orgs or (project and project in full_projects)):
            if org in status_orgs or (project and project in status_projects):
                sub = _only(sub, SUBSCRIPTION_STATUS_FIELDS)
            else:
                sub = _operational_subscription(sub)
    return (await _minimise_rows(current_user, [sub]))[0]


class SubscriptionCreateRequest(SubscriptionCreate):
    """The create body, with unknown keys captured (not ignored) so a gateway binding sent
    under any spelling is seen and refused (owner decision 2026-11-20)."""

    model_config = ConfigDict(extra="allow")


def _gateway_keys_attempted(payload: BaseModel, stored: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the body tries to set, replace or clear a gateway binding: a protected model
    field that differs from the stored value (or is set at all, on create), or any other key
    that names a gateway identity, whatever its value (mass assignment is refused, never
    silently dropped)."""
    for field in PROTECTED_GATEWAY_FIELDS:
        if field in payload.model_fields_set:
            value = getattr(payload, field, None)
            if stored is None:
                if scope_text(value):
                    return True
            elif scope_text(value) != scope_text(stored.get(field)):
                return True
    for key in (payload.model_extra or {}):
        normalised = _scope_key(key)
        if "gateway" in normalised or normalised in {"paymentprovider", "provider"}:
            return True
    return False


def _unknown_keys(payload: BaseModel) -> List[str]:
    """Keys that are neither fields of the body nor a recognised spelling of a scope field.
    They are refused, never dropped: a client must not believe a change it sent was applied
    (independent review F5: razorpay_subscription_id, rzp_sub_id ... were dropped with 200)."""
    recognised = set().union(*_SCOPE_FIELD_SPELLINGS.values())
    return sorted(key for key in (payload.model_extra or {}) if _scope_key(key) not in recognised)


_GATEWAY_CHANGE_REFUSED = (
    "A subscription's payment gateway binding is set only by checkout; it cannot be set, "
    "replaced or cleared here"
)


@router.post("/subscriptions", response_model=Dict[str, Any])
async def create_subscription(
    payload: SubscriptionCreateRequest,
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
    if _gateway_keys_attempted(payload):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GATEWAY_CHANGE_REFUSED)
    if payload.model_extra:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown fields: {', '.join(sorted(payload.model_extra))}")
    # Review-7 F1: creating a subscription directly is an administrative act, not a purchase.
    # A tenant buys through checkout (or starts a trial); only a Super Admin creates one here,
    # and even then "active" billing is never written without payment confirmation.
    if not ScopeService().is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Subscriptions are bought through checkout or started as a trial; only a Super Admin creates one directly",
        )
    if "billing_status" in payload.model_fields_set and str(payload.billing_status).strip().lower() == "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="billing_status 'active' is set only by payment confirmation; use 'manual' for a manually invoiced subscription",
        )
    if not await MonetizationService().validate_plan_code(payload.plan_code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Plan '{payload.plan_code}' not found or inactive")
    create = SubscriptionCreate(**payload.model_dump(include=set(SubscriptionCreate.model_fields)))
    if "billing_status" not in payload.model_fields_set:
        create.billing_status = "manual"
    try:
        return await MonetizationService().create_subscription(create, current_user)
    except GatewayIdentityChangeRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GATEWAY_CHANGE_REFUSED) from exc


class CheckoutRequest(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    plan_code: str
    billing_period: str = "monthly"
    customer_name: Optional[str] = Field(default=None, max_length=200)
    customer_email: Optional[str] = Field(default=None, max_length=200)
    # Workstream 1 (behind CHECKOUT_REPLACEMENT_ENABLED): the gateway-billed subscription of
    # this scope that the new plan replaces at the end of its paid period.
    replaces_subscription_id: Optional[str] = Field(default=None, max_length=100)


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
            manual_billing_authorised=ScopeService().is_superadmin(current_user),
            replaces_subscription_id=payload.replaces_subscription_id,
            idempotency_key=_idempotency_key(request),
        )
    except (GatewayIdentityConflict, PurchaseInProgress, SubscriptionNotGoverning, OperationConflict,
            IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class SubscriptionUpdateRequest(SubscriptionUpdate):
    """The ordinary update body. ``organization_id`` and any other key are captured (not
    ignored, as SubscriptionUpdate would) so an attempted scope change is seen and refused
    (L3); they are never written."""

    model_config = ConfigDict(extra="allow")

    organization_id: Optional[str] = None


@router.put("/subscriptions/{subscription_id}", response_model=Dict[str, Any])
async def update_subscription(
    subscription_id: str,
    payload: SubscriptionUpdateRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(check_monetization_rate_limit),
):
    await require_step_up(request, current_user, action="subscription.entitlement.manage")
    service = MonetizationService()
    subscription = await _load_subscription_or_404(service, subscription_id)
    # Authorised against the stored scope, never a scope named in the body (L3).
    await _authorize_subscription_scope(
        policy=policy,
        current_user=current_user,
        permission=Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE,
        subscription=subscription,
        subscription_id=subscription_id,
    )
    stored_scope = subscription_scope(subscription)
    # An organisation, project or package (F3) named in the body, under any spelling, must
    # equal the stored one. Omitted is unchanged; the same value is a no-op; anything else,
    # explicit null included, is refused.
    extra = payload.model_extra or {}
    for index, field in enumerate(("organization_id", "project_id", "package_id")):
        attempted = [getattr(payload, field)] if field in payload.model_fields_set else []
        attempted += [value for key, value in extra.items() if _scope_key(key) in _SCOPE_FIELD_SPELLINGS[field]]
        if any(scope_text(value) != stored_scope[index] for value in attempted):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_SCOPE_CHANGE_REFUSED)
    if _gateway_keys_attempted(payload, subscription):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GATEWAY_CHANGE_REFUSED)
    # R6-8: commercial, lifecycle and activation-precedence fields go through their own
    # workflows (gateway, payment, lifecycle checks), never through this update - for every
    # caller, under every spelling, even when the value is unchanged.
    refused = _commercial_fields_attempted(payload)
    if refused:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_commercial_refusal(refused))
    unknown = _unknown_keys(payload)
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown fields: {', '.join(unknown)}")
    if "entitlement_overrides" in payload.model_fields_set and not ScopeService().is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Entitlement overrides grant features and quotas beyond the plan; only a Super Admin can set them",
        )
    update = SubscriptionUpdate(**payload.model_dump(include=set(PUT_EDITABLE_FIELDS) & payload.model_fields_set))
    try:
        return await service.update_subscription(
            subscription_id, update, current_user, expected_scope=stored_scope
        )
    except GatewayIdentityChangeRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_GATEWAY_CHANGE_REFUSED) from exc
    except SubscriptionScopeChangeRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_SCOPE_CHANGE_REFUSED) from exc
    except SubscriptionScopeConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found") from exc


#: R6-8 (owner decision 2026-11-21): the only fields ``PUT /subscriptions/{id}`` may change.
#: ``admin_note`` and ``cancellation_reason`` are non-commercial text; ``entitlement_overrides``
#: (features and quotas beyond the plan) is an administrative grant kept for Super Admin only.
#: Everything else in ``SubscriptionUpdate`` is commercial or lifecycle state and has a
#: dedicated workflow (below); scope (L3/F3) and the gateway binding are refused above.
PUT_EDITABLE_FIELDS = frozenset({"admin_note", "cancellation_reason", "entitlement_overrides"})

#: Commercial and lifecycle fields, the workflow that owns each, and other spellings (compared
#: after ``_scope_key``) under which they are refused rather than reported as unknown.
_COMMERCIAL_FIELDS: Dict[str, Tuple[str, frozenset]] = {
    "plan_code": ("POST /subscriptions/{id}/upgrade or /downgrade", frozenset({"plancode", "plan", "planid", "plancodeid"})),
    "billing_period": ("POST /subscriptions/{id}/change-period", frozenset({"billingperiod", "period", "billingcycle", "cycle"})),
    "active_add_ons": ("POST /subscriptions/{id}/add-ons/add or /add-ons/remove",
                       frozenset({"activeaddons", "addons", "addoncodes", "addon"})),
    "status": ("checkout, cancel, reactivate or convert-trial",
               frozenset({"status", "subscriptionstatus", "lifecyclestatus", "state"})),
    "billing_status": ("payment confirmation by the billing webhook", frozenset({"billingstatus", "paymentstatus"})),
    "starts_at": ("checkout, start-trial or convert-trial", frozenset({"startsat", "startdate", "start"})),
    "ends_at": ("cancel or a new purchase", frozenset({"endsat", "enddate", "end", "expiresat"})),
    "current_period_start": ("payment confirmation by the billing webhook", frozenset({"currentperiodstart"})),
    "current_period_end": ("payment confirmation by the billing webhook", frozenset({"currentperiodend", "periodend"})),
    "trial": ("start-trial or convert-trial", frozenset({"trial", "istrial"})),
    "trial_ends_at": ("start-trial or convert-trial", frozenset({"trialendsat", "trialend"})),
    "pilot": ("plan settings", frozenset({"pilot", "ispilot"})),
    "auto_renew": ("cancel (at period end) or reactivate, which reach the gateway", frozenset({"autorenew", "autorenewal", "renew"})),
    "cancelled_at": ("cancel or reactivate", frozenset({"cancelledat", "canceledat"})),
    "governing_since": ("activation (checkout payment, reactivation, plan settings)",
                        frozenset({"governingsince", "activatedat", "activationdate", "activationprecedence"})),
}


def _commercial_fields_attempted(payload: BaseModel) -> List[str]:
    """Commercial fields named in the body, as model fields or under another spelling."""
    attempted = {field for field in _COMMERCIAL_FIELDS if field in payload.model_fields_set}
    for key in (payload.model_extra or {}):
        normalised = _scope_key(key)
        for field, (_, spellings) in _COMMERCIAL_FIELDS.items():
            if normalised == _scope_key(field) or normalised in spellings:
                attempted.add(field)
    return sorted(attempted)


def _commercial_refusal(fields: List[str]) -> str:
    owners = "; ".join(f"{field}: {_COMMERCIAL_FIELDS[field][0]}" for field in fields)
    return (
        "Commercial and lifecycle fields cannot be changed by an ordinary update "
        f"(use the dedicated workflow - {owners})"
    )


#: Other spellings of the scope fields a client might send, compared after ``_scope_key``
#: (case, "_", "-", "." and spaces ignored: ``package_ID``, ``PackageId``, ``Org-Id`` ...);
#: each is refused unless it equals the stored value (earlier review Low 4; F3 review 1).
_SCOPE_FIELD_SPELLINGS = {
    "organization_id": frozenset({"organizationid", "organisationid", "orgid", "organization", "organisation", "org"}),
    "project_id": frozenset({"projectid", "project"}),
    "package_id": frozenset({"packageid", "package"}),
}


def _scope_key(key: Any) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())

_SCOPE_CHANGE_REFUSED = (
    "The organisation, project and package of a subscription cannot be changed by an update; "
    "a transfer is a separate, explicitly authorised operation"
)


# ---------------------------------------------------------------------------
# Subscription Lifecycle Endpoints
# ---------------------------------------------------------------------------


def _idempotency_key(request: Request) -> Optional[str]:
    """The caller's ``Idempotency-Key`` (gateway/database consistency design): a retried
    lifecycle change with the same key is not applied twice."""
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if len(key) > 128:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key is longer than 128 characters")
    return key or None


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
        return await service.upgrade_subscription(subscription_id, payload, current_user, idempotency_key=_idempotency_key(request),
            manual_billing_authorised=ScopeService().is_superadmin(current_user))
    except SubscriptionNotGoverning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        return await service.downgrade_subscription(subscription_id, payload, current_user, idempotency_key=_idempotency_key(request),
            manual_billing_authorised=ScopeService().is_superadmin(current_user))
    except SubscriptionNotGoverning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        return await service.cancel_subscription(subscription_id, payload, current_user, idempotency_key=_idempotency_key(request))
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        return await service.reactivate_subscription(
            subscription_id, current_user, manual_billing_authorised=ScopeService().is_superadmin(current_user))
    except SubscriptionReactivationRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        return await service.change_billing_period(subscription_id, payload, current_user, idempotency_key=_idempotency_key(request),
            manual_billing_authorised=ScopeService().is_superadmin(current_user))
    except SubscriptionNotGoverning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (OperationConflict, IdempotencyKeyReused) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        return await service.add_addon(subscription_id, payload, current_user,
                                       manual_billing_authorised=ScopeService().is_superadmin(current_user))
    except SubscriptionNotGoverning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    except SubscriptionNotGoverning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        return await MonetizationService().start_trial(
            payload, current_user, extended_trial_authorised=ScopeService().is_superadmin(current_user))
    except TrialConversionRefused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        return await service.convert_trial(
            subscription_id, payload, current_user,
            # R6-5: a no-charge conversion is a separate authority from trial management.
            no_charge_authorised=ScopeService().is_superadmin(current_user),
        )
    except (TrialConversionRefused, GatewayIdentityConflict) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except GatewayOperationFailed as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
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
        Permissions.BILLING_RECORD_VIEW if _explicit_billing_policy() else Permissions.BILLING_PLAN_VIEW,
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
        Permissions.BILLING_RECORD_VIEW if _explicit_billing_policy() else Permissions.BILLING_PLAN_VIEW,
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
        Permissions.BILLING_RECORD_VIEW if _explicit_billing_policy() else Permissions.BILLING_PLAN_VIEW,
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
        Permissions.BILLING_INVOICE_DOWNLOAD if _explicit_billing_policy() else Permissions.BILLING_PLAN_VIEW,
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
