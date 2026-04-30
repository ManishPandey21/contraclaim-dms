"""Centralised audit logging helpers used across routers/services."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

from ..core.database import get_database

logger = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    """Structured payload for persisted audit entries."""

    event_type: str
    user_id: Optional[str]
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    level: str = "info"
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def model_dump(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


class AuditLogger:
    """Async-friendly audit logger with graceful fallbacks."""

    def __init__(self) -> None:
        self._collection = None
        # Dedicated entry point for structured action logging; avoids repeated initialization
        self._action_event_type = "action"

    async def _get_collection(self):
        if self._collection is not None:
            return self._collection
        try:
            db = await get_database()
            self._collection = getattr(db, "audit_logs", None)
        except Exception:  # pragma: no cover - best effort only
            logger.debug("AuditLogger could not acquire database connection", exc_info=True)
            self._collection = None
        return self._collection

    async def _write_record(self, record: AuditRecord) -> bool:
        payload = record.model_dump()
        logger.info(
            "AUDIT %s [%s]: %s | %s",
            record.event_type,
            record.level,
            record.message,
            record.details,
        )
        collection = await self._get_collection()
        if collection is None:
            return True
        try:
            await collection.insert_one(payload)
            return True
        except Exception:  # pragma: no cover - do not break request flow
            logger.warning("AuditLogger failed to persist event", exc_info=True)
            return False

    async def log_action(
        self,
        user_id: Optional[str],
        action: str,
        *,
        resource: Optional[str] = None,
        status: str = "success",
        level: str = "info",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        **details: Any,
    ) -> bool:
        """
        Generic action logger used by permission checks and service events.
        """
        message = f"{action} {status}".strip()
        payload_details = dict(details)
        payload_details["status"] = status
        if resource:
            payload_details["resource"] = resource
        return await self.log_event(
            self._action_event_type,
            user_id,
            message,
            level=level,
            resource_type=resource_type or resource or "action",
            resource_id=resource_id or resource,
            **payload_details,
        )

    async def log_permission_check(
        self,
        user_id: Optional[str],
        permission: str,
        granted: bool,
        *,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        **details: Any,
    ) -> bool:
        """
        Convenience wrapper to capture permission enforcement outcomes.
        """
        status = "granted" if granted else "denied"
        level = "info" if granted else "warning"
        return await self.log_action(
            user_id,
            action="permission_check",
            resource=permission,
            status=status,
            level=level,
            resource_type=resource_type or "permission",
            resource_id=resource_id or permission,
            **details,
        )

    async def log_event(
        self,
        event_type: str,
        user_id: Optional[str],
        message: str,
        *,
        level: str = "info",
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        **details: Any,
    ) -> bool:
        record = AuditRecord(
            event_type=event_type,
            user_id=user_id,
            message=message,
            details=details,
            level=level,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        return await self._write_record(record)

    # Convenience wrappers -------------------------------------------------

    async def log_login_failed(self, user_identifier: str, reason: str, **details: Any) -> bool:
        return await self.log_event(
            "login_failed",
            user_identifier,
            f"Login failed: {reason}",
            level="warning",
            resource_type="auth",
            **details,
        )

    async def log_login_successful(self, user_id: str, email: str, **details: Any) -> bool:
        return await self.log_event(
            "login_success",
            user_id,
            f"User {email} authenticated successfully",
            resource_type="auth",
            **details,
        )

    async def log_user_logged_out(self, user_id: str, email: str, **details: Any) -> bool:
        return await self.log_event(
            "logout",
            user_id,
            f"User {email} logged out",
            resource_type="auth",
            **details,
        )

    async def log_token_refreshed(self, user_id: str, session_id: str, **details: Any) -> bool:
        return await self.log_event(
            "token_refresh",
            user_id,
            "Access token refreshed",
            resource_type="session",
            resource_id=session_id,
            **details,
        )

    async def log_token_refresh_failed(self, user_id: Optional[str], reason: str, **details: Any) -> bool:
        return await self.log_event(
            "token_refresh_failed",
            user_id,
            f"Token refresh failed: {reason}",
            level="warning",
            resource_type="session",
            **details,
        )

    async def log_user_created(self, actor_id: str, user_id: str, email: str, **details: Any) -> bool:
        return await self.log_event(
            "user_created",
            actor_id,
            f"User {email} ({user_id}) created",
            resource_type="user",
            resource_id=user_id,
            **details,
        )

    async def log_user_updated(self, actor_id: str, user_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "user_updated",
            actor_id,
            f"User {user_id} updated fields: {', '.join(changed_fields)}",
            resource_type="user",
            resource_id=user_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_user_deleted(self, actor_id: str, user_id: str, email: str, **details: Any) -> bool:
        return await self.log_event(
            "user_deleted",
            actor_id,
            f"User {email} ({user_id}) deleted",
            level="warning",
            resource_type="user",
            resource_id=user_id,
            **details,
        )

    async def log_user_account_locked(self, actor_id: str, user_id: str, **details: Any) -> bool:
        return await self.log_event(
            "user_locked",
            actor_id,
            f"User {user_id} account locked",
            level="warning",
            resource_type="user",
            resource_id=user_id,
            **details,
        )

    async def log_user_account_unlocked(self, actor_id: str, user_id: str, **details: Any) -> bool:
        return await self.log_event(
            "user_unlocked",
            actor_id,
            f"User {user_id} account unlocked",
            resource_type="user",
            resource_id=user_id,
            **details,
        )

    async def log_role_created(self, actor_id: str, role_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "role_created",
            actor_id,
            f"Role '{name}' ({role_id}) created",
            resource_type="role",
            resource_id=role_id,
            **details,
        )

    async def log_role_updated(self, actor_id: str, role_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "role_updated",
            actor_id,
            f"Role {role_id} updated fields: {', '.join(changed_fields)}",
            resource_type="role",
            resource_id=role_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_role_deleted(self, actor_id: str, role_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "role_deleted",
            actor_id,
            f"Role '{name}' ({role_id}) deleted",
            level="warning",
            resource_type="role",
            resource_id=role_id,
            **details,
        )

    async def log_role_permissions_updated(self, actor_id: str, role_id: str, added: list[str], removed: list[str], **details: Any) -> bool:
        return await self.log_event(
            "role_permissions_updated",
            actor_id,
            f"Role {role_id} permissions updated",
            resource_type="role",
            resource_id=role_id,
            added=added,
            removed=removed,
            **details,
        )

    async def log_permission_added_to_role(self, actor_id: str, role_id: str, permission: str, **details: Any) -> bool:
        return await self.log_event(
            "role_permission_added",
            actor_id,
            f"Permission {permission} added to role {role_id}",
            resource_type="role",
            resource_id=role_id,
            permission=permission,
            **details,
        )

    async def log_permission_removed_from_role(self, actor_id: str, role_id: str, permission: str, **details: Any) -> bool:
        return await self.log_event(
            "role_permission_removed",
            actor_id,
            f"Permission {permission} removed from role {role_id}",
            resource_type="role",
            resource_id=role_id,
            permission=permission,
            level="warning",
            **details,
        )

    async def log_tag_created(self, actor_id: str, tag_id: str, name: str, organization_id: Optional[str], **details: Any) -> bool:
        return await self.log_event(
            "tag_created",
            actor_id,
            f"Tag '{name}' ({tag_id}) created",
            resource_type="tag",
            resource_id=tag_id,
            organization_id=organization_id,
            **details,
        )

    async def log_tag_updated(self, actor_id: str, tag_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "tag_updated",
            actor_id,
            f"Tag {tag_id} updated fields: {', '.join(changed_fields)}",
            resource_type="tag",
            resource_id=tag_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_tag_deleted(self, actor_id: str, tag_id: str, tag_name: str, subtags_deleted: int = 0, **details: Any) -> bool:
        message = f"Tag '{tag_name}' ({tag_id}) deleted"
        if subtags_deleted:
            message += f" with {subtags_deleted} subtags"
        return await self.log_event(
            "tag_deleted",
            actor_id,
            message,
            level="warning",
            resource_type="tag",
            resource_id=tag_id,
            subtags_deleted=subtags_deleted,
            **details,
        )

    async def log_subtag_created(self, actor_id: str, subtag_id: str, name: str, tag_id: str, **details: Any) -> bool:
        return await self.log_event(
            "subtag_created",
            actor_id,
            f"Subtag '{name}' ({subtag_id}) created for tag {tag_id}",
            resource_type="subtag",
            resource_id=subtag_id,
            tag_id=tag_id,
            **details,
        )

    async def log_subtag_updated(self, actor_id: str, subtag_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "subtag_updated",
            actor_id,
            f"Subtag {subtag_id} updated fields: {', '.join(changed_fields)}",
            resource_type="subtag",
            resource_id=subtag_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_subtag_deleted(self, actor_id: str, subtag_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "subtag_deleted",
            actor_id,
            f"Subtag '{name}' ({subtag_id}) deleted",
            level="warning",
            resource_type="subtag",
            resource_id=subtag_id,
            **details,
        )

    async def log_email_sent(self, user_id: str, subject: str, **details: Any) -> bool:
        return await self.log_event(
            "email_sent",
            user_id,
            f"Email sent: {subject}",
            resource_type="email",
            **details,
        )

    async def log_email_delivered(self, user_id: str, subject: str, **details: Any) -> bool:
        return await self.log_event(
            "email_delivered",
            user_id,
            f"Email delivered: {subject}",
            resource_type="email",
            **details,
        )

    async def log_email_failed(self, user_id: Optional[str], subject: str, reason: str, **details: Any) -> bool:
        return await self.log_event(
            "email_failed",
            user_id,
            f"Email '{subject}' failed: {reason}",
            level="warning",
            resource_type="email",
            **details,
        )

    async def log_template_email_sent(self, user_id: str, template_name: str, **details: Any) -> bool:
        return await self.log_event(
            "template_email_sent",
            user_id,
            f"Template email '{template_name}' sent",
            resource_type="email",
            **details,
        )

    async def log_email_group_created(self, actor_id: str, group_id: str, name: str, email_count: int, **details: Any) -> bool:
        return await self.log_event(
            "email_group_created",
            actor_id,
            f"Email group '{name}' ({group_id}) created",
            resource_type="email_group",
            resource_id=group_id,
            email_count=email_count,
            **details,
        )

    async def log_email_group_updated(self, actor_id: str, group_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "email_group_updated",
            actor_id,
            f"Email group {group_id} updated fields: {', '.join(changed_fields)}",
            resource_type="email_group",
            resource_id=group_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_email_group_deleted(self, actor_id: str, group_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "email_group_deleted",
            actor_id,
            f"Email group '{name}' ({group_id}) deleted",
            level="warning",
            resource_type="email_group",
            resource_id=group_id,
            **details,
        )

    async def log_organization_created(self, actor_id: str, organization_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "organization_created",
            actor_id,
            f"Organization '{name}' ({organization_id}) created",
            resource_type="organization",
            resource_id=organization_id,
            **details,
        )

    async def log_organization_updated(self, actor_id: str, organization_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "organization_updated",
            actor_id,
            f"Organization {organization_id} updated fields: {', '.join(changed_fields)}",
            resource_type="organization",
            resource_id=organization_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_organization_deleted(self, actor_id: str, organization_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "organization_deleted",
            actor_id,
            f"Organization '{name}' ({organization_id}) deleted",
            level="warning",
            resource_type="organization",
            resource_id=organization_id,
            **details,
        )

    async def log_party_created(self, actor_id: str, party_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "party_created",
            actor_id,
            f"Party '{name}' ({party_id}) created",
            resource_type="party",
            resource_id=party_id,
            **details,
        )

    async def log_party_updated(self, actor_id: str, party_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "party_updated",
            actor_id,
            f"Party {party_id} updated fields: {', '.join(changed_fields)}",
            resource_type="party",
            resource_id=party_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_party_deleted(self, actor_id: str, party_id: str, name: str, **details: Any) -> bool:
        return await self.log_event(
            "party_deleted",
            actor_id,
            f"Party '{name}' ({party_id}) deleted",
            level="warning",
            resource_type="party",
            resource_id=party_id,
            **details,
        )

    async def log_party_project_associated(self, actor_id: str, party_id: str, project_id: str, **details: Any) -> bool:
        return await self.log_event(
            "party_project_associated",
            actor_id,
            f"Party {party_id} associated with project {project_id}",
            resource_type="party",
            resource_id=party_id,
            project_id=project_id,
            **details,
        )

    async def log_representative_created(self, actor_id: str, representative_id: str, level: str, **details: Any) -> bool:
        return await self.log_event(
            "representative_created",
            actor_id,
            f"Representative {representative_id} created ({level})",
            resource_type="representative",
            resource_id=representative_id,
            level=level,
            **details,
        )

    async def log_representative_updated(self, actor_id: str, representative_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "representative_updated",
            actor_id,
            f"Representative {representative_id} updated fields: {', '.join(changed_fields)}",
            resource_type="representative",
            resource_id=representative_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_representative_deleted(self, actor_id: str, representative_id: str, **details: Any) -> bool:
        return await self.log_event(
            "representative_deleted",
            actor_id,
            f"Representative {representative_id} deleted",
            level="warning",
            resource_type="representative",
            resource_id=representative_id,
            **details,
        )

    async def log_concern_created(self, actor_id: str, concern_id: str, **details: Any) -> bool:
        return await self.log_event(
            "concern_created",
            actor_id,
            f"Concern {concern_id} created",
            resource_type="concern",
            resource_id=concern_id,
            **details,
        )

    async def log_concern_updated(self, actor_id: str, concern_id: str, changed_fields: list[str], **details: Any) -> bool:
        return await self.log_event(
            "concern_updated",
            actor_id,
            f"Concern {concern_id} updated fields: {', '.join(changed_fields)}",
            resource_type="concern",
            resource_id=concern_id,
            changed_fields=changed_fields,
            field_count=len(changed_fields),
            **details,
        )

    async def log_concern_deleted(self, actor_id: str, concern_id: str, **details: Any) -> bool:
        return await self.log_event(
            "concern_deleted",
            actor_id,
            f"Concern {concern_id} deleted",
            level="warning",
            resource_type="concern",
            resource_id=concern_id,
            **details,
        )

    async def log_email_group_event(self, *args: Any, **kwargs: Any) -> bool:  # backward compatibility helper
        return await self.log_event(*args, **kwargs)

    async def log_input_request_created(self, actor_id: str, request_id: str, letter_id: str, **details: Any) -> bool:
        return await self.log_event(
            "input_request_created",
            actor_id,
            f"Input request {request_id} created for letter {letter_id}",
            resource_type="input_request",
            resource_id=request_id,
            letter_id=letter_id,
            **details,
        )

    async def log_input_request_responded(self, actor_id: str, request_id: str, **details: Any) -> bool:
        return await self.log_event(
            "input_request_responded",
            actor_id,
            f"Input request {request_id} responded",
            resource_type="input_request",
            resource_id=request_id,
            **details,
        )

    async def log_input_request_closed(self, actor_id: str, request_id: str, **details: Any) -> bool:
        return await self.log_event(
            "input_request_closed",
            actor_id,
            f"Input request {request_id} closed",
            resource_type="input_request",
            resource_id=request_id,
            **details,
        )

    async def log_performance_metrics_accessed(self, actor_id: str, hours: int, **details: Any) -> bool:
        return await self.log_event(
            "performance_metrics_accessed",
            actor_id,
            f"Performance metrics viewed for last {hours} hours",
            resource_type="performance",
            **details,
        )

    async def log_job_cancelled(self, actor_id: str, job_id: str, **details: Any) -> bool:
        return await self.log_event(
            "job_cancelled",
            actor_id,
            f"Background job {job_id} cancelled",
            level="warning",
            resource_type="job",
            resource_id=job_id,
            **details,
        )

    async def log_cache_cleared(self, actor_id: str, **details: Any) -> bool:
        return await self.log_event(
            "cache_cleared",
            actor_id,
            "Application cache cleared",
            resource_type="cache",
            **details,
        )


_audit_logger_instance: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger_instance
    if _audit_logger_instance is None:
        _audit_logger_instance = AuditLogger()
    return _audit_logger_instance


__all__ = ["AuditLogger", "get_audit_logger"]
