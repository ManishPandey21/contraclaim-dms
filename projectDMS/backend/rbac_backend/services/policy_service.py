from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException, status

from ..core.config import settings
from ..core.permissions import (
    LEGACY_BILLING_VIEW_ALIASES,
    PLATFORM_GRANT_ONLY_PERMISSIONS,
    SUPERADMIN_EXCLUDED_PERMISSIONS,
    Permissions,
    is_billing_permission,
    permission_domain,
)
from ..services.audit_event_service import AuditEventService
from ..services.entitlement_service import EntitlementService
from ..services.permission_service import PermissionService
from ..services.scope_service import ScopeService
from ..services.usage_metering_service import QuotaExceededError, UsageMeteringService
from . import role_scope_shadow

#: Roles whose authority is bound to assigned projects, never to a whole organisation.
PROJECT_TIER_ROLE_KEYS = frozenset({"projectadmin", "projectuser"})

#: Built-in roles that stand for the whole of a member organisation for billing visibility
#: (decision 4, 2026-11-20). Anyone else (Project Admin / User, limited user, a plain user,
#: a custom project role) is project-level there.
ORGANISATION_LEVEL_BILLING_ROLE_KEYS = frozenset(
    {"orgadmin", "orguser", "contractmgr_org", "doccontroller", "reporter", "settings_manager", "contraclaim_billing_admin"}
)

#: Financial reads that a project-level caller never receives (decision 4), whatever alias
#: would otherwise grant them with BILLING_EXPLICIT_PERMISSIONS off.
ORGANISATION_FINANCIAL_VIEW_PERMISSIONS = frozenset(
    {
        Permissions.BILLING_PLAN_VIEW,
        Permissions.BILLING_RECORD_VIEW,
        Permissions.BILLING_INVOICE_VIEW,
        Permissions.BILLING_INVOICE_DOWNLOAD,
        Permissions.SUBSCRIPTION_HISTORY_VIEW,
    }
)

logger = logging.getLogger(__name__)


class PolicyService:
    """Central deny-by-default authorization policy."""

    # A holder of drafting.skill.read may read the skill registry within their
    # own client scope, like starting a request. The role must still hold the
    # permission: has_permission() is checked before this rule. Drafters do not
    # need it; the Letter Bench lists skills through a letter-scoped route.
    CLIENT_DRAFTING_PERMISSIONS = {
        Permissions.DRAFTING_REQUEST_CREATE,
        Permissions.DRAFTING_SKILL_READ,
    }
    DRAFTING_ADMIN_PERMISSIONS = {
        Permissions.DRAFTING_REQUEST_ASSIGN,
        Permissions.DRAFTING_WORKFLOW_CHECKPOINTS,
        Permissions.DRAFTING_WORKFLOW_FORCE_V2,
        Permissions.DRAFTING_SKILL_PUBLISH,
        Permissions.DRAFTING_SKILL_BIND,
        Permissions.DRAFTING_ADMIN,
    }

    def __init__(
        self,
        db: Any = None,
        *,
        permission_service: Optional[PermissionService] = None,
        scope_service: Optional[ScopeService] = None,
        entitlement_service: Optional[EntitlementService] = None,
        audit_service: Optional[AuditEventService] = None,
        usage_metering_service: Optional[UsageMeteringService] = None,
    ) -> None:
        self.db = db
        self.permission_service = permission_service or PermissionService()
        self.scope_service = scope_service or ScopeService(db)
        self.entitlement_service = entitlement_service or EntitlementService(db)
        self.audit_service = audit_service or AuditEventService(db)
        self.usage_metering_service = usage_metering_service or UsageMeteringService(db)

    async def has_explicit_grant(
        self,
        current_user: Any,
        permission: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """A grant held by name on the user's own roles: no Super Admin, wildcard or dms.admin shortcut."""
        return await self.permission_service.user_has_explicit_permission(
            str(getattr(current_user, "id", "") or ""),
            permission,
            organization_id=organization_id,
            project_id=project_id,
        )

    async def has_organisation_level_standing(
        self,
        current_user: Any,
        organization_id: Optional[str],
        permission: str = Permissions.SUBSCRIPTION_HISTORY_VIEW,
    ) -> bool:
        """Decision 4: whether the caller stands for the whole of ``organization_id`` for billing
        visibility: a member holding an organisation-level built-in role
        (ORGANISATION_LEVEL_BILLING_ROLE_KEYS), or a custom role of that organisation with no
        project that itself grants ``permission`` (an unrelated organisation role does not,
        re-review N1). Super Admin always. Built-in roles are global unless
        ``BUILT_IN_ROLE_ASSIGNMENTS=on`` (M1), which counts them only where assigned."""
        if self.scope_service.is_superadmin(current_user):
            return True
        if not organization_id:
            return False
        if str(organization_id) not in await self.scope_service.client_organization_ids(current_user):
            return False
        # M1 (on): the organisation-level role must be assigned in this organisation.
        if (await self.scope_service.role_names_in(current_user, str(organization_id))) & ORGANISATION_LEVEL_BILLING_ROLE_KEYS:
            return True
        return await self.permission_service.holds_organisation_level_custom_role(
            str(getattr(current_user, "id", "") or ""), str(organization_id), permission
        )

    async def _organisation_of(self, organization_id: Optional[str], project_id: Optional[str]) -> Optional[str]:
        if organization_id:
            return str(organization_id)
        if not project_id:
            return None
        db = self.db if self.db is not None else await self.permission_service._get_db()
        resolved, _ = await self.permission_service.resolve_request_scope(db, None, project_id)
        return resolved

    async def _may_answer_at_organisation_level(
        self, current_user: Any, organization_id: Optional[str], permission: Optional[str] = None
    ) -> bool:
        """DEF-4: a project-tier user (Project Admin / Project User) answers a billing check
        at organisation level, or unscoped, only with organisation-wide scope there. A built-in
        project role's grant is not bound to a project, so without this a project
        administrator's billing grant would reach the organisation's records and sibling
        projects (review M2: the rule lives here, so every route applies it)."""
        if not (self.scope_service.role_names(current_user) & PROJECT_TIER_ROLE_KEYS):
            return True
        if await self.scope_service.has_organization_wide_scope(current_user, organization_id=organization_id):
            return True
        # Review F4: a custom role of that organisation (no project) granting this permission
        # itself answers for the organisation, whatever project role the user also holds -
        # the same rule as organisation-level standing with the flag off (N1).
        return bool(
            permission
            and organization_id
            and await self.permission_service.holds_organisation_level_custom_role(
                str(getattr(current_user, "id", "") or ""), str(organization_id), permission
            )
        )

    async def explicit_billing_scopes(
        self, current_user: Any, permission: str, *, organizations: Any, projects: Any
    ) -> tuple[set, set]:
        """The flag-on billing rule over many scopes at once (review M3): the organisations and
        projects where ``permission`` is held. Super Admin: all given."""
        organizations = {str(org) for org in organizations or [] if org}
        projects = {str(project) for project in projects or [] if project}
        if self.scope_service.is_superadmin(current_user):
            return organizations, projects
        held_orgs, held_projects = await self.permission_service.explicitly_held_scopes(
            str(getattr(current_user, "id", "") or ""),
            permission,
            organizations=organizations,
            projects=projects,
            exclude=LEGACY_BILLING_VIEW_ALIASES,
        )
        held_orgs = {org for org in held_orgs if await self._may_answer_at_organisation_level(current_user, org, permission)}
        return held_orgs, held_projects

    async def has_permission(
        self,
        current_user: Any,
        permission: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """Whether the user holds ``permission``; with a scope, through a role that applies there (SEC-1)."""
        if permission in SUPERADMIN_EXCLUDED_PERMISSIONS:
            return await self.has_explicit_grant(
                current_user, permission, organization_id=organization_id, project_id=project_id
            )
        if self.scope_service.is_superadmin(current_user):
            return True
        if permission in PLATFORM_GRANT_ONLY_PERMISSIONS:
            # M4: a platform-scoped grant only; never a tenant role, alias, wildcard or
            # administrator name, and never through billing.plan.manage.
            return await self.permission_service.user_holds_platform_grant(
                str(getattr(current_user, "id", "") or ""), permission
            )
        if settings.BILLING_EXPLICIT_PERMISSIONS and is_billing_permission(permission):
            # DEF-4 target policy: the name itself, granted on a role that applies here. Not
            # through organizations:read / orgs:view / reports:view, a wildcard, or an
            # administrator name: viewing never implies a financial mutation, nor the reverse.
            if not project_id and not await self._may_answer_at_organisation_level(current_user, organization_id, permission):
                return False
            return await self.permission_service.user_has_explicit_permission(
                str(getattr(current_user, "id", "") or ""),
                permission,
                organization_id=organization_id,
                project_id=project_id,
                exclude=LEGACY_BILLING_VIEW_ALIASES,
            )
        # `user_has_permission` applies the alias contract itself. Expanding the
        # name here first made resolution two hops deep - through a shared legacy
        # alias, `billing.plan.manage` reached `dms.admin` (see
        # `equivalent_permissions`).
        candidates = {permission}
        if permission.startswith("drafting."):
            candidates.add(Permissions.DRAFTING_ADMIN)
        if permission.startswith("dms."):
            candidates.add(Permissions.DMS_ADMIN)
        if permission.startswith("billing.") or permission.startswith("subscription."):
            candidates.update({Permissions.BILLING_PLAN_MANAGE, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE})
        for candidate in candidates:
            if await self.permission_service.user_has_permission(
                getattr(current_user, "id", ""),
                candidate,
                log=False,
                organization_id=organization_id,
                project_id=project_id,
            ):
                return True
        return False

    async def authorize(
        self,
        current_user: Any,
        permission: str,
        *,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
        letter_id: Optional[str] = None,
        drafting_request_id: Optional[str] = None,
        meter_event_type: Optional[str] = None,
        meter_quantity: int = 1,
        meter_metadata: Optional[dict[str, Any]] = None,
        audit: bool = True,
    ) -> None:
        actor_id = getattr(current_user, "id", None)
        if current_user is None or not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        granted = False
        reason = "denied"
        if hasattr(self.entitlement_service, "last_resolution"):
            self.entitlement_service.last_resolution = None  # never a previous call's evidence
        try:
            metered_quantity = max(1, int(meter_quantity))
        except Exception:
            metered_quantity = 1

        async def _allow(allow_reason: str) -> None:
            nonlocal granted, reason
            reason = allow_reason
            if meter_event_type:
                if not organization_id:
                    reason = "quota_scope_required"
                    return await self._deny(reason)

                usage_metadata: dict[str, Any] = {
                    "permission": permission,
                    "resource_type": resource_type,
                }
                if resource_id:
                    usage_metadata["resource_id"] = resource_id
                if package_id:
                    usage_metadata["package_id"] = package_id
                if letter_id:
                    usage_metadata["letter_id"] = letter_id
                if drafting_request_id:
                    usage_metadata["drafting_request_id"] = drafting_request_id
                if meter_metadata:
                    usage_metadata.update(meter_metadata)

                try:
                    await self.usage_metering_service.check_and_record(
                        event_type=meter_event_type,
                        organization_id=str(organization_id),
                        project_id=str(project_id) if project_id else None,
                        user_id=str(actor_id),
                        quantity=metered_quantity,
                        metadata=usage_metadata,
                    )
                except QuotaExceededError as exc:
                    reason = exc.reason
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Usage quota is not available for {exc.event_type}: {exc.reason}",
                    ) from exc

            granted = True
            reason = allow_reason
            return None

        # Privileged legal material (plan D-12): no status confers it.
        explicit_only = permission in SUPERADMIN_EXCLUDED_PERMISSIONS
        try:
            if permission in PLATFORM_GRANT_ONLY_PERMISSIONS:
                # M4: global (platform) administration. Not a tenant action: no organisation or
                # project scope, entitlement or tenant role applies, and a scoped call is refused
                # rather than read as a tenant grant.
                if organization_id or project_id:
                    reason = "platform_permission_unscoped"
                    return await self._deny(reason)
                if self.scope_service.is_superadmin(current_user):
                    return await _allow("superadmin")
                if await self.permission_service.user_holds_platform_grant(str(actor_id), permission):
                    return await _allow("platform_grant")
                reason = "platform_admin_required"
                return await self._deny(reason)
            if explicit_only:
                if not await self.has_explicit_grant(
                    current_user, permission, organization_id=organization_id, project_id=project_id
                ):
                    reason = "explicit_grant_required"
                    return await self._deny(reason)
            elif self.scope_service.is_superadmin(current_user):
                return await _allow("superadmin")

            if not explicit_only and not await self.has_permission(
                current_user, permission, organization_id=organization_id, project_id=project_id
            ):
                reason = "missing_permission"
                return await self._deny(reason)

            entitlement_ok, entitlement_reason = await self.entitlement_service.check_permission_entitlement(
                permission=permission,
                organization_id=organization_id,
                project_id=project_id,
                package_id=package_id,
            )
            if not entitlement_ok:
                reason = entitlement_reason
                return await self._deny(reason)

            domain = permission_domain(permission)
            if domain == "drafting":
                if self.scope_service.is_superadmin(current_user):
                    return await _allow("superadmin")
                if permission in self.CLIENT_DRAFTING_PERMISSIONS:
                    if await self.scope_service.is_client_scope_allowed(
                        current_user,
                        organization_id=organization_id,
                        project_id=project_id,
                    ):
                        return await _allow("client_scope")
                    reason = "scope_denied"
                    return await self._deny(reason)
                if permission in self.DRAFTING_ADMIN_PERMISSIONS and await self.has_permission(
                    current_user, Permissions.DRAFTING_ADMIN, organization_id=organization_id, project_id=project_id
                ):
                    return await _allow("drafting_admin")
                if await self.scope_service.has_expert_allocation(
                    current_user,
                    permission=permission,
                    organization_id=organization_id,
                    project_id=project_id,
                    package_id=package_id,
                    letter_id=letter_id,
                    drafting_request_id=drafting_request_id,
                ):
                    return await _allow("expert_allocation")
                reason = "missing_expert_allocation"
                return await self._deny(reason)

            if domain == "role_management":
                if organization_id or project_id:
                    if await self.scope_service.is_client_scope_allowed(
                        current_user,
                        organization_id=organization_id,
                        project_id=project_id,
                    ):
                        return await _allow("role_management_scope")
                    reason = "scope_denied"
                    return await self._deny(reason)
                if permission in {"roles:read", "permissions:read"}:
                    return await _allow("role_management_catalog")
                reason = "scope_required"
                return await self._deny(reason)

            if domain in {"client_dms", "billing", "system"}:
                if not explicit_only and self.scope_service.is_superadmin(current_user):
                    return await _allow("superadmin")
                if domain == "billing":
                    if (
                        (organization_id or project_id)
                        and not settings.BILLING_EXPLICIT_PERMISSIONS
                        and permission in ORGANISATION_FINANCIAL_VIEW_PERMISSIONS
                        and not await self.has_organisation_level_standing(
                            current_user, await self._organisation_of(organization_id, project_id), permission
                        )
                    ):
                        # Decision 4: a project-level caller (Project Admin / User and the like)
                        # never receives financial detail. The legacy alias still grants the
                        # name; the organisation-level billing views are refused here.
                        reason = "organisation_billing_standing_required"
                        return await self._deny(reason)
                    if organization_id or project_id:
                        if await self.scope_service.is_client_scope_allowed(
                            current_user,
                            organization_id=organization_id,
                            project_id=project_id,
                        ):
                            return await _allow("billing_scope")
                        reason = "scope_denied"
                        return await self._deny(reason)
                    if permission in {Permissions.BILLING_PLAN_MANAGE, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE}:
                        return await _allow("billing_admin")
                    if permission == Permissions.BILLING_PLAN_VIEW:
                        return await _allow("billing_plan_view")
                    reason = "scope_required"
                    return await self._deny(reason)
                if domain == "system":
                    reason = "platform_admin_required"
                    return await self._deny(reason)
                if await self.scope_service.is_client_scope_allowed(
                    current_user,
                    organization_id=organization_id,
                    project_id=project_id,
                ):
                    return await _allow("client_scope")
                reason = "scope_denied"
                return await self._deny(reason)

            reason = "unsupported_permission_domain"
            return await self._deny(reason)
        finally:
            if granted and role_scope_shadow.enabled() and not self.scope_service.is_superadmin(current_user):
                # M1 shadow (workstream 5): counted beside the decision, never changing it.
                try:
                    shadow_db = self.db if self.db is not None else await self.permission_service._get_db()
                    await role_scope_shadow.observe(shadow_db, current_user, permission,
                                                    organization_id=organization_id, project_id=project_id)
                except Exception:  # never affects the response
                    logger.warning("M1 shadow evaluation skipped", exc_info=True)
            if audit:
                await self.audit_service.emit(
                    action="policy.authorize",
                    actor_id=actor_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    organization_id=organization_id,
                    project_id=project_id,
                    package_id=package_id,
                    result="allow" if granted else "deny",
                    reason=reason,
                    metadata={
                        "permission": permission,
                        "meter_event_type": meter_event_type,
                        "meter_quantity": metered_quantity if meter_event_type else None,
                        # The subscription that decided the entitlement, and why (2026-11-20).
                        "entitlement": getattr(self.entitlement_service, "last_resolution", None),
                    },
                )

    async def _deny(self, reason: str) -> None:
        if reason.startswith("feature_disabled:"):
            feature_key = reason.split(":", 1)[1]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Subscription feature is not enabled: {feature_key}",
            )
        details = {
            "drafting_entitlement": "Drafting service is not active for this project or organization",
            "dms_entitlement": "DMS service is not active for this project or organization",
            "archive_read_only": "This project or organization is in archive/read-only mode",
            "no_active_subscription": "No active subscription is configured for this project or organization",
            "no_subscription_records": "No subscription records are configured for this project or organization",
            "offboarding_export_only": "Only offboarding export actions are allowed for this project or organization",
            "scope_required": "A valid organization or project scope is required for this action",
            "quota_scope_required": "A valid organization scope is required for metered usage",
            "quota_exceeded": "Usage quota is exhausted for this project or organization",
            "platform_admin_required": "Platform administration permission is required for this action",
            "platform_permission_unscoped": "This platform action does not take an organization or project scope",
            "organisation_billing_standing_required": "Billing detail is available to organisation-level roles only",
            "unsupported_permission_domain": "Permission is not supported by the central authorization policy",
        }
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=details.get(reason, f"Not authorized: {reason}"),
        )

    async def authorize_document(
        self,
        current_user: Any,
        permission: str,
        document: Any,
        *,
        resource_type: str = "document",
    ) -> None:
        organization_id = getattr(document, "organization_id", None)
        project_id = getattr(document, "project_id", None)
        resource_id = getattr(document, "id", None) or getattr(document, "_id", None)
        if isinstance(document, dict):
            organization_id = document.get("organization_id") or document.get("organizationId")
            project_id = document.get("project_id") or document.get("projectId")
            resource_id = document.get("_id") or document.get("id") or resource_id
        await self.authorize(
            current_user,
            permission,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            organization_id=str(organization_id) if organization_id else None,
            project_id=str(project_id) if project_id else None,
        )
