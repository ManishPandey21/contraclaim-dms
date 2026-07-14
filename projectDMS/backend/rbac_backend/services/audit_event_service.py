from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId

from ..core.database import get_database
from .observability import observability_registry


REVIEWABLE_POLICY_DENY_REASONS = {
    "archive_read_only",
    "dms_entitlement",
    "drafting_entitlement",
    "missing_expert_allocation",
    "no_active_subscription",
    "no_subscription_records",
    "offboarding_export_only",
    "platform_admin_required",
    "quota_exceeded",
    "quota_scope_required",
    "scope_denied",
    "unsupported_permission_domain",
}

ADMIN_REVIEW_STATUSES = {"open", "acknowledged", "resolved", "dismissed"}


class AuditEventService:
    """Writes normalized security, RBAC, billing, and drafting audit events."""

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def emit(
        self,
        *,
        action: str,
        actor_id: Optional[str],
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
        result: str = "success",
        reason: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        db = await self._get_db()
        now = datetime.utcnow()
        event = {
            "action": action,
            "actor_id": actor_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "organization_id": organization_id,
            "project_id": project_id,
            "package_id": package_id,
            "result": result,
            "reason": reason,
            "before": before,
            "after": after,
            "metadata": metadata or {},
            "correlation_id": correlation_id,
            "created_at": now,
        }
        audit_events = getattr(db, "audit_events", None)
        inserted_id: Any = None
        if audit_events is not None:
            inserted = await audit_events.insert_one(event)
            inserted_id = getattr(inserted, "inserted_id", None)

        await observability_registry.record_audit_event(
            action=action,
            result=result,
            resource_type=resource_type,
        )

        if self._requires_admin_review(event):
            await self._upsert_admin_review_item(db, event, inserted_id=inserted_id, now=now)

    def _requires_admin_review(self, event: Dict[str, Any]) -> bool:
        metadata = event.get("metadata") or {}
        if metadata.get("requires_admin_review"):
            return True
        action = str(event.get("action") or "")
        result = str(event.get("result") or "")
        reason = str(event.get("reason") or "")
        if action == "policy.authorize" and result == "deny":
            return reason.startswith("feature_disabled:") or reason in REVIEWABLE_POLICY_DENY_REASONS
        return False

    def _review_severity(self, event: Dict[str, Any]) -> str:
        reason = str(event.get("reason") or "")
        if reason in {"scope_denied", "platform_admin_required", "unsupported_permission_domain"}:
            return "critical"
        if reason.startswith("feature_disabled:") or reason in {
            "archive_read_only",
            "amount_mismatch",
            "no_active_subscription",
            "no_subscription_records",
            "payment_failed",
            "quota_exceeded",
            "quota_scope_required",
        }:
            return "high"
        return "medium"

    def _review_source(self, event: Dict[str, Any]) -> str:
        if event.get("action") == "policy.authorize":
            return "authorization"
        return "audit_event"

    def _review_key(self, event: Dict[str, Any]) -> str:
        metadata = event.get("metadata") or {}
        permission = metadata.get("permission") or ""
        parts = [
            str(event.get("action") or ""),
            str(event.get("reason") or ""),
            str(event.get("organization_id") or ""),
            str(event.get("project_id") or ""),
            str(event.get("resource_type") or ""),
            str(event.get("resource_id") or ""),
            str(permission),
        ]
        return "|".join(parts)

    def _review_title(self, event: Dict[str, Any]) -> str:
        metadata = event.get("metadata") or {}
        permission = metadata.get("permission")
        if event.get("action") == "policy.authorize" and permission:
            return f"Review denied authorization: {permission}"
        return f"Review audit event: {event.get('action') or 'unknown'}"

    def _review_summary(self, event: Dict[str, Any]) -> str:
        metadata = event.get("metadata") or {}
        permission = metadata.get("permission")
        reason = event.get("reason") or "unknown"
        actor_id = event.get("actor_id") or "unknown"
        resource_type = event.get("resource_type") or "unknown"
        resource_id = event.get("resource_id") or ""
        if permission:
            return (
                f"Actor {actor_id} was denied {permission} on {resource_type}"
                f"{':' + str(resource_id) if resource_id else ''} because {reason}."
            )
        return f"Audit event {event.get('action') or 'unknown'} requires admin review because {reason}."

    async def _upsert_admin_review_item(
        self,
        db: Any,
        event: Dict[str, Any],
        *,
        inserted_id: Any,
        now: datetime,
    ) -> None:
        admin_review_items = getattr(db, "admin_review_items", None)
        if admin_review_items is None:
            return
        severity = self._review_severity(event)
        source = self._review_source(event)
        metadata = event.get("metadata") or {}
        review_key = str(metadata.get("admin_review_key") or self._review_key(event))
        latest_audit_event_id = str(inserted_id) if inserted_id is not None else None
        await admin_review_items.update_one(
            {"review_key": review_key},
            {
                "$setOnInsert": {
                    "review_key": review_key,
                    "source": source,
                    "severity": severity,
                    "first_seen_at": now,
                    "created_at": now,
                    "created_by": "system:audit",
                },
                "$set": {
                    "status": "open",
                    "title": self._review_title(event),
                    "summary": self._review_summary(event),
                    "action": event.get("action"),
                    "reason": event.get("reason"),
                    "result": event.get("result"),
                    "actor_id": event.get("actor_id"),
                    "organization_id": event.get("organization_id"),
                    "project_id": event.get("project_id"),
                    "package_id": event.get("package_id"),
                    "resource_type": event.get("resource_type"),
                    "resource_id": event.get("resource_id"),
                    "metadata": metadata,
                    "latest_audit_event_id": latest_audit_event_id,
                    "last_seen_at": now,
                    "updated_at": now,
                },
                "$inc": {"occurrence_count": 1},
            },
            upsert=True,
        )
        await observability_registry.record_admin_review_item(
            status="open",
            severity=severity,
            source=source,
        )

    async def query_events(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[Dict[str, Any]]:
        """Return audit events for a tenant scope, newest first.

        Always constrained by ``organization_id`` (and optionally ``project_id``);
        the caller is responsible for authorizing that scope before calling.
        """
        db = await self._get_db()
        audit_events = getattr(db, "audit_events", None)
        if audit_events is None:
            return []
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = str(organization_id)
        if project_id:
            query["project_id"] = str(project_id)
        if action:
            query["action"] = action
        if actor_id:
            query["actor_id"] = str(actor_id)
        if start or end:
            created: Dict[str, Any] = {}
            if start:
                created["$gte"] = start
            if end:
                created["$lte"] = end
            query["created_at"] = created

        cursor = audit_events.find(query).sort("created_at", -1).limit(max(1, int(limit)))
        events: list[Dict[str, Any]] = []
        async for event in cursor:
            event["_id"] = str(event.get("_id", ""))
            created_at = event.get("created_at")
            if isinstance(created_at, datetime):
                event["created_at"] = created_at.isoformat()
            events.append(event)
        return events

    async def query_admin_review_items(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        status: Optional[str] = "open",
        severity: Optional[str] = None,
        reason: Optional[str] = None,
        limit: int = 500,
    ) -> list[Dict[str, Any]]:
        """Return tenant-scoped admin review items, newest first."""
        db = await self._get_db()
        admin_review_items = getattr(db, "admin_review_items", None)
        if admin_review_items is None:
            return []
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = str(organization_id)
        if project_id:
            query["project_id"] = str(project_id)
        if status:
            query["status"] = str(status)
        if severity:
            query["severity"] = str(severity)
        if reason:
            query["reason"] = str(reason)

        cursor = admin_review_items.find(query).sort("updated_at", -1).limit(max(1, int(limit)))
        items: list[Dict[str, Any]] = []
        async for item in cursor:
            items.append(self._normalize_admin_review_item(item))
        return items

    async def update_admin_review_item(
        self,
        review_id: str,
        *,
        organization_id: str,
        status: str,
        reviewer_id: Optional[str],
        note: Optional[str] = None,
    ) -> bool:
        """Acknowledge, resolve, dismiss, or reopen a tenant-scoped review item."""
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in ADMIN_REVIEW_STATUSES:
            raise ValueError("Invalid admin review status")
        db = await self._get_db()
        admin_review_items = getattr(db, "admin_review_items", None)
        if admin_review_items is None:
            return False
        now = datetime.utcnow()
        result = await admin_review_items.update_one(
            {"_id": self._lookup_id(review_id), "organization_id": str(organization_id)},
            {
                "$set": {
                    "status": normalized_status,
                    "reviewed_by": reviewer_id,
                    "review_note": note,
                    "reviewed_at": now,
                    "updated_at": now,
                }
            },
        )
        return bool(getattr(result, "matched_count", 0))

    def _normalize_admin_review_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(item)
        normalized["_id"] = str(normalized.get("_id", ""))
        for field in ("first_seen_at", "last_seen_at", "created_at", "updated_at", "reviewed_at"):
            value = normalized.get(field)
            if isinstance(value, datetime):
                normalized[field] = value.isoformat()
        return normalized

    def _lookup_id(self, value: str) -> Any:
        try:
            if ObjectId.is_valid(value):
                return ObjectId(value)
        except Exception:
            pass
        return value
