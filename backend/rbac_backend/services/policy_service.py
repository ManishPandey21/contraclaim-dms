from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, status

from ..core.permissions import Permissions, equivalent_permissions, permission_domain
from ..services.audit_event_service import AuditEventService
from ..services.entitlement_service import EntitlementService
from ..services.permission_service import PermissionService
from ..services.scope_service import ScopeService


class PolicyService:
    """Central deny-by-default authorization policy."""

    CLIENT_DRAFTING_PERMISSIONS = {Permissions.DRAFTING_REQUEST_CREATE}
    DRAFTING_ADMIN_PERMISSIONS = {Permissions.DRAFTING_REQUEST_ASSIGN, Permissions.DRAFTING_ADMIN}

    def __init__(
        self,
        db: Any = None,
        *,
        permission_service: Optional[PermissionService] = None,
        scope_service: Optional[ScopeService] = None,
        entitlement_service: Optional[EntitlementService] = None,
        audit_service: Optional[AuditEventService] = None,
    ) -> None:
        self.db = db
        self.permission_service = permission_service or PermissionService()
        self.scope_service = scope_service or ScopeService(db)
        self.entitlement_service = entitlement_service or EntitlementService(db)
        self.audit_service = audit_service or AuditEventService(db)

    async def has_permission(self, current_user: Any, permission: str) -> bool:
        if self.scope_service.is_superadmin(current_user):
            return True
        candidates = set(equivalent_permissions(permission))
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
        audit: bool = True,
    ) -> None:
        actor_id = getattr(current_user, "id", None)
        if current_user is None or not actor_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

        granted = False
        reason = "denied"
        try:
            if self.scope_service.is_superadmin(current_user):
                granted = True
                reason = "superadmin"
                return

            if not await self.has_permission(current_user, permission):
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
                    granted = True
                    reason = "superadmin"
                    return
                if permission in self.CLIENT_DRAFTING_PERMISSIONS:
                    if await self.scope_service.is_client_scope_allowed(
                        current_user,
                        organization_id=organization_id,
                        project_id=project_id,
                    ):
                        granted = True
                        reason = "client_scope"
                        return
                    reason = "scope_denied"
                    return await self._deny(reason)
                if permission in self.DRAFTING_ADMIN_PERMISSIONS and await self.has_permission(
                    current_user, Permissions.DRAFTING_ADMIN
                ):
                    granted = True
                    reason = "drafting_admin"
                    return
                if await self.scope_service.has_expert_allocation(
                    current_user,
                    permission=permission,
                    organization_id=organization_id,
                    project_id=project_id,
                    package_id=package_id,
                    letter_id=letter_id,
                    drafting_request_id=drafting_request_id,
                ):
                    granted = True
                    reason = "expert_allocation"
                    return
                reason = "missing_expert_allocation"
                return await self._deny(reason)

            if domain in {"client_dms", "billing", "system"}:
                if self.scope_service.is_superadmin(current_user):
                    granted = True
                    reason = "superadmin"
                    return
                if domain == "billing":
                    if permission in {Permissions.BILLING_PLAN_MANAGE, Permissions.SUBSCRIPTION_ENTITLEMENT_MANAGE}:
                        granted = True
                        reason = "billing_admin"
                        return
                    if permission == Permissions.BILLING_PLAN_VIEW:
                        granted = True
                        reason = "billing_plan_view"
                        return
                    if organization_id or project_id:
                        if await self.scope_service.is_client_scope_allowed(
                            current_user,
                            organization_id=organization_id,
                            project_id=project_id,
                        ):
                            granted = True
                            reason = "billing_scope"
                            return
                        reason = "scope_denied"
                        return await self._deny(reason)
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
                    granted = True
                    reason = "client_scope"
                    return
                reason = "scope_denied"
                return await self._deny(reason)

            reason = "unsupported_permission_domain"
            return await self._deny(reason)
        finally:
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
                    metadata={"permission": permission},
                )

    async def _deny(self, reason: str) -> None:
        details = {
            "drafting_entitlement": "Drafting service is not active for this project or organization",
            "dms_entitlement": "DMS service is not active for this project or organization",
            "archive_read_only": "This project or organization is in archive/read-only mode",
            "no_active_subscription": "No active subscription is configured for this project or organization",
            "no_subscription_records": "No subscription records are configured for this project or organization",
            "offboarding_export_only": "Only offboarding export actions are allowed for this project or organization",
            "scope_required": "A valid organization or project scope is required for this action",
            "platform_admin_required": "Platform administration permission is required for this action",
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
