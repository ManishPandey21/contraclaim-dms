from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, status

from ..core.permissions import Permissions, equivalent_permissions, permission_domain
from ..services.audit_event_service import AuditEventService
from ..services.entitlement_service import EntitlementService
from ..services.permission_service import PermissionService
from ..services.scope_service import ScopeService
from ..services.usage_metering_service import QuotaExceededError, UsageMeteringService


class PolicyService:
    """Central deny-by-default authorization policy."""

    CLIENT_DRAFTING_PERMISSIONS = {Permissions.DRAFTING_REQUEST_CREATE}
    DRAFTING_ADMIN_PERMISSIONS = {
        Permissions.DRAFTING_REQUEST_ASSIGN,
        Permissions.DRAFTING_WORKFLOW_CHECKPOINTS,
        Permissions.DRAFTING_WORKFLOW_FORCE_V2,
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

        try:
            if self.scope_service.is_superadmin(current_user):
                return await _allow("superadmin")

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
                    current_user, Permissions.DRAFTING_ADMIN
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
                if self.scope_service.is_superadmin(current_user):
                    return await _allow("superadmin")
                if domain == "billing":
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
