"""Notification service handling persistence, WebSocket broadcast, and email fan-out."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.notification import (
    Notification,
    NotificationAction,
    NotificationCategory,
    NotificationContext,
    NotificationListResponse,
    NotificationPriority,
    NotificationResponse,
    NotificationSeverity,
    NotificationType,
)
from ..services.email_service import EmailService
from ..services.rbac_service import RBACService

logger = logging.getLogger(__name__)


def _coerce_object_id(value: str) -> ObjectId | str:
    try:
        return ObjectId(value)
    except Exception:  # noqa: BLE001
        return value


def _notification_from_doc(doc: Dict[str, Any]) -> Notification:
    raw = dict(doc)
    if "_id" in raw:
        raw["_id"] = str(raw["_id"])
    return Notification(**raw)


_TYPE_DEFAULT_CATEGORY: dict[NotificationType, NotificationCategory] = {
    NotificationType.DRAFT_SAVED: NotificationCategory.DRAFTING,
    NotificationType.DRAFT_APPROVED: NotificationCategory.APPROVALS,
    NotificationType.DRAFT_REJECTED: NotificationCategory.APPROVALS,
    NotificationType.DRAFT_REQUESTED: NotificationCategory.DRAFTING,
    NotificationType.APPROVAL_ASSIGNED: NotificationCategory.APPROVALS,
    NotificationType.APPROVAL_COMPLETED: NotificationCategory.APPROVALS,
    NotificationType.APPROVAL_REJECTED: NotificationCategory.APPROVALS,
    NotificationType.NEW_UPLOAD: NotificationCategory.UPLOADS,
    NotificationType.BULK_UPLOAD_COMPLETED: NotificationCategory.UPLOADS,
    NotificationType.DOCUMENT_METADATA_UPDATED: NotificationCategory.UPLOADS,
    NotificationType.DOCUMENT_DELETED: NotificationCategory.UPLOADS,
    NotificationType.DOCUMENT_ARCHIVED: NotificationCategory.UPLOADS,
    NotificationType.DOCUMENT_VERSION_UPLOADED: NotificationCategory.UPLOADS,
    NotificationType.DOCUMENT_VERSION_ROLLED_BACK: NotificationCategory.UPLOADS,
    NotificationType.COMMENT_ADDED: NotificationCategory.COMMENTS,
    NotificationType.MENTION_ADDED: NotificationCategory.COMMENTS,
    NotificationType.REPLY_ADDED: NotificationCategory.COMMENTS,
    NotificationType.THREAD_RESOLVED: NotificationCategory.COMMENTS,
    NotificationType.REPLY_REMINDER: NotificationCategory.REMINDERS,
    NotificationType.REPLY_ESCALATION: NotificationCategory.REMINDERS,
    NotificationType.PERMISSION_CHANGED: NotificationCategory.SECURITY,
    NotificationType.UNUSUAL_LOGIN: NotificationCategory.SECURITY,
    NotificationType.STORAGE_THRESHOLD_CROSSED: NotificationCategory.SYSTEM,
    NotificationType.CLAIM_DEADLINE_APPROACHING: NotificationCategory.REMINDERS,
    NotificationType.CLAIM_DEADLINE_BREACHED: NotificationCategory.REMINDERS,
    NotificationType.KEYDATE_DUE: NotificationCategory.REMINDERS,
    NotificationType.KEYDATE_OVERDUE: NotificationCategory.REMINDERS,
}

_IMMEDIATE_EMAIL_EVENTS = {
    NotificationType.DRAFT_APPROVED,
    NotificationType.DRAFT_REJECTED,
    NotificationType.NEW_UPLOAD,
    NotificationType.BULK_UPLOAD_COMPLETED,
}

_UPLOAD_EVENTS = {
    NotificationType.NEW_UPLOAD,
    NotificationType.BULK_UPLOAD_COMPLETED,
}


def build_resource_link(resource_type: str, resource_id: str, event_type: NotificationType) -> Optional[str]:
    if resource_type == "document":
        return f"/documentviewer/{resource_id}"
    if resource_type == "letter":
        if event_type in {NotificationType.APPROVAL_ASSIGNED, NotificationType.APPROVAL_REJECTED}:
            return f"/letters/{resource_id}/approval"
        if event_type == NotificationType.DRAFT_REQUESTED:
            return f"/letters/{resource_id}/draft"
        return f"/letters/{resource_id}/input"
    if resource_type == "project":
        return "/documents" if event_type == NotificationType.BULK_UPLOAD_COMPLETED else f"/projects/{resource_id}"
    return None


class ConnectionManager:
    """Track active WebSocket connections keyed by user id."""

    def __init__(self) -> None:
        self._connections: defaultdict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)

    async def disconnect(self, user_id: str, websocket: Optional[WebSocket] = None) -> None:
        async with self._lock:
            if websocket is not None:
                self._connections[user_id].discard(websocket)
            else:
                self._connections.pop(user_id, None)
            if not self._connections.get(user_id):
                self._connections.pop(user_id, None)

    async def broadcast(self, recipients: Iterable[str], message: Dict[str, Any]) -> None:
        stale: List[tuple[str, WebSocket]] = []
        for user_id in recipients:
            sockets = list(self._connections.get(user_id, ()))
            for socket in sockets:
                try:
                    await socket.send_json(message)
                except WebSocketDisconnect:
                    stale.append((user_id, socket))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("WebSocket send failed for user %s: %s", user_id, exc)
                    stale.append((user_id, socket))
        for user_id, socket in stale:
            await self.disconnect(user_id, socket)


class NotificationService:
    """High level orchestration for notifications."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        *,
        manager: Optional[ConnectionManager] = None,
        email_service: Optional[EmailService] = None,
    ) -> None:
        if db is None:
            raise ValueError("db must not be None")
        self.db = db
        self.manager = manager or ConnectionManager()
        self.email_service = email_service
        self.rbac_service = RBACService(db)
        self._index_lock = asyncio.Lock()
        self._indexes_ready = False

    async def emit(
        self,
        event_type: NotificationType,
        resource_id: str,
        resource_type: str,
        *,
        category: Optional[NotificationCategory] = None,
        context: NotificationContext = NotificationContext.GLOBAL,
        actor_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        include_users: Optional[Iterable[str]] = None,
        priority: NotificationPriority | str = NotificationPriority.NORMAL,
        severity: NotificationSeverity | str = NotificationSeverity.INFO,
        actions: Optional[Iterable[NotificationAction | Dict[str, Any]]] = None,
        resource_link: Optional[str] = None,
        channels_requested: Optional[Iterable[str]] = None,
        delivery_summary: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> Notification:
        await self._ensure_indexes()

        resolved_category = category or _TYPE_DEFAULT_CATEGORY.get(event_type, NotificationCategory.DRAFTING)
        if dedupe_key:
            existing = await self.db.notifications.find_one({"dedupe_key": dedupe_key})
            if existing:
                return _notification_from_doc(existing)

        resource = await self.rbac_service.fetch_resource(resource_type, resource_id)
        include_set = {str(user) for user in (include_users or []) if user}
        if event_type not in _UPLOAD_EVENTS:
            include_set.update(self._resolve_owners(resource))

        recipients = await self.rbac_service.get_notifiable_users(
            event_type,
            resource_id,
            resource_type,
            context,
            include_users=include_set,
            exclude_users=[actor_id] if actor_id else None,
        )
        organization_id = self._extract_field(resource, "organization_id")
        project_id = self._extract_field(resource, "project_id")
        if resource_type == "project" and not project_id:
            project_id = str(resource_id)

        requested_channels = list(channels_requested or ["in_app", "websocket", "email"])
        recipients, requested_channels = await self._apply_preferences(
            recipients,
            event_type=event_type,
            organization_id=organization_id,
            project_id=project_id,
            requested_channels=requested_channels,
        )

        notification = Notification(
            type=event_type,
            category=resolved_category,
            resource_id=resource_id,
            resource_type=resource_type,
            organization_id=organization_id,
            project_id=project_id,
            context=context,
            recipients=recipients,
            read_by=[actor_id] if actor_id and actor_id in recipients else [],
            priority=NotificationPriority(priority),
            severity=NotificationSeverity(severity),
            title=(data or {}).get("title"),
            message=(data or {}).get("message"),
            actions=[self._coerce_action(action) for action in (actions or [])],
            resource_link=resource_link or build_resource_link(resource_type, resource_id, event_type),
            channels_requested=requested_channels,
            delivery_summary=delivery_summary or {},
            dedupe_key=dedupe_key,
            expires_at=expires_at,
            created_by=actor_id,
            data=data or {},
        )

        if recipients:
            doc = notification.model_dump(by_alias=True, exclude_none=True, exclude={"id"})
            result = await self.db.notifications.insert_one(doc)
            notification.id = str(result.inserted_id)  # type: ignore[assignment]

            payload = {
                "type": "notification",
                "notification": {
                    "id": str(notification.id),
                    "type": notification.type.value,
                    "category": notification.category.value,
                    "resource_id": notification.resource_id,
                    "resource_type": notification.resource_type,
                    "organization_id": notification.organization_id,
                    "project_id": notification.project_id,
                    "context": notification.context.value,
                    "priority": notification.priority.value,
                    "severity": notification.severity.value,
                    "title": notification.title,
                    "message": notification.message,
                    "actions": [action.model_dump() for action in notification.actions],
                    "action_state": notification.action_state,
                    "resource_link": notification.resource_link,
                    "channels_requested": notification.channels_requested,
                    "delivery_summary": notification.delivery_summary,
                    "dedupe_key": notification.dedupe_key,
                    "expires_at": notification.expires_at.isoformat() if notification.expires_at else None,
                    "created_at": notification.created_at.isoformat(),
                    "created_by": notification.created_by,
                    "data": notification.data,
                    "unread": True,
                },
            }
            await self.manager.broadcast(recipients, payload)

            if self.email_service and event_type in _IMMEDIATE_EMAIL_EVENTS and "email" in requested_channels:
                await self._fanout_emails(notification, recipients)
        else:
            logger.debug("No recipients resolved for %s on %s", event_type, resource_id)

        return notification

    async def list_notifications(
        self,
        user_id: str,
        *,
        unread_only: bool = False,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        project_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
    ) -> NotificationListResponse:
        await self._ensure_indexes()

        query: Dict[str, Any] = {"recipients": user_id}
        if unread_only:
            query["read_by"] = {"$ne": user_id}
        if category:
            try:
                query["category"] = NotificationCategory(category).value
            except ValueError:
                query["category"] = category
        if event_type:
            try:
                query["type"] = NotificationType(event_type).value
            except ValueError:
                query["type"] = event_type
        if project_id:
            query["project_id"] = project_id
        if search:
            pattern = search.strip()
            if pattern:
                query["$or"] = [
                    {"title": {"$regex": pattern, "$options": "i"}},
                    {"message": {"$regex": pattern, "$options": "i"}},
                    {"data.title": {"$regex": pattern, "$options": "i"}},
                    {"data.message": {"$regex": pattern, "$options": "i"}},
                    {"data.subject": {"$regex": pattern, "$options": "i"}},
                    {"data.filename": {"$regex": pattern, "$options": "i"}},
                    {"resource_id": {"$regex": pattern, "$options": "i"}},
                ]

        cursor = (
            self.db.notifications.find(query)
            .sort("created_at", -1)
            .skip(max(skip, 0))
            .limit(max(limit, 1))
        )
        raw_items = await cursor.to_list(length=None)
        notifications = [_notification_from_doc(item) for item in raw_items]
        responses = [NotificationResponse.from_notification(item, user_id) for item in notifications]

        total = await self.db.notifications.count_documents(query)
        unread_query = dict(query)
        unread_query["read_by"] = {"$ne": user_id}
        unread_count = await self.db.notifications.count_documents(unread_query)
        has_more = skip + len(responses) < total

        return NotificationListResponse(
            notifications=responses,
            total=total,
            unread_count=unread_count,
            has_more=has_more,
        )

    async def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        await self._ensure_indexes()
        result = await self.db.notifications.update_one(
            {"_id": _coerce_object_id(notification_id), "recipients": user_id},
            {"$addToSet": {"read_by": user_id}},
        )
        return result.matched_count > 0

    async def mark_all_as_read(
        self,
        user_id: str,
        *,
        category: Optional[str] = None,
        event_type: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> int:
        await self._ensure_indexes()
        query: Dict[str, Any] = {"recipients": user_id, "read_by": {"$ne": user_id}}
        if category:
            query["category"] = category
        if event_type:
            query["type"] = event_type
        if project_id:
            query["project_id"] = project_id
        result = await self.db.notifications.update_many(
            query,
            {"$addToSet": {"read_by": user_id}},
        )
        return result.modified_count

    async def count_unread(self, user_id: str) -> int:
        await self._ensure_indexes()
        return await self.db.notifications.count_documents(
            {"recipients": user_id, "read_by": {"$ne": user_id}}
        )

    async def execute_action(
        self,
        notification_id: str,
        user_id: str,
        action: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        current_user: Optional[Any] = None,
    ) -> Dict[str, Any]:
        await self._ensure_indexes()
        notification_doc = await self.db.notifications.find_one(
            {"_id": _coerce_object_id(notification_id), "recipients": user_id}
        )
        if not notification_doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

        notification = _notification_from_doc(notification_doc)
        action_key = (action or "").strip().lower()
        if not action_key:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Action is required")

        known_actions = {item.key for item in notification.actions or []}
        builtin_actions = {"view", "view_letter", "start_draft", "approve", "reject", "mark_done", "assign_to_me"}
        if known_actions and action_key not in known_actions and action_key not in builtin_actions:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Action is not available")

        state = dict(notification.action_state or {})
        if state.get(action_key, {}).get("status") == "completed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Action already completed")

        if notification.resource_type != "letter":
            if action_key.startswith("view"):
                return await self._record_action_result(notification_id, user_id, action_key, "completed", {"resource_link": notification.resource_link})
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Action is not supported for this resource")

        from ..services.authorization_service import AuthorizationService
        from ..services.letter_service import LetterService

        letter_service = LetterService(self.db, notification_service=self)
        letter = await letter_service.get_letter(notification.resource_id)
        if not letter:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letter not found")

        auth_action = "read" if action_key.startswith("view") else "update"
        try:
            await AuthorizationService().check_letter_access(current_user, letter, auth_action)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        status_target = self._status_for_action(action_key, getattr(letter, "status", None))
        result_payload: Dict[str, Any] = {"resource_id": notification.resource_id, "resource_link": notification.resource_link}
        if status_target:
            comment = None
            if payload:
                comment = payload.get("comment") or payload.get("message")
            changed = await letter_service.change_status(
                notification.resource_id,
                status_target,
                comment=comment,
                user_id=user_id,
            )
            if not changed:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Letter action could not be applied")
            result_payload["new_status"] = status_target
        elif action_key == "assign_to_me":
            await self._assign_letter_to_user(notification.resource_id, user_id)
            result_payload["assigned_to"] = user_id
        elif action_key.startswith("view"):
            pass
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported notification action")

        return await self._record_action_result(notification_id, user_id, action_key, "completed", result_payload)

    async def unread_since(
        self,
        user_id: str,
        *,
        category: Optional[NotificationCategory],
        since: datetime,
    ) -> List[Notification]:
        await self._ensure_indexes()
        query: Dict[str, Any] = {
            "recipients": user_id,
            "created_at": {"$gte": since},
            "read_by": {"$ne": user_id},
        }
        if category:
            query["category"] = category.value
        cursor = self.db.notifications.find(query).sort("created_at", -1)
        items = await cursor.to_list(length=None)
        return [_notification_from_doc(item) for item in items]

    def _status_for_action(self, action_key: str, current_status: Optional[str]) -> Optional[str]:
        normalized = (current_status or "").strip().lower()
        allowed_for_approval = {"approval", "review", "draft", "strategy"}
        if action_key == "approve":
            if normalized and normalized not in allowed_for_approval:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Letter is no longer awaiting approval")
            return "Approved"
        if action_key == "reject":
            if normalized and normalized in {"approved", "completed"}:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Completed letters cannot be rejected")
            return "Rejected"
        if action_key in {"mark_done", "complete"}:
            return "Completed"
        if action_key == "start_draft":
            return "Draft"
        return None

    async def _assign_letter_to_user(self, letter_id: str, user_id: str) -> None:
        result = await self.db.letters.update_one(
            {"_id": _coerce_object_id(letter_id)},
            {"$set": {"assigned_to": user_id, "updatedAt": datetime.utcnow()}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letter not found")

    async def _record_action_result(
        self,
        notification_id: str,
        user_id: str,
        action_key: str,
        action_status: str,
        result_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        state_key = f"action_state.{action_key}"
        action_record = {
            "status": action_status,
            "executed_by": user_id,
            "executed_at": now,
            "result": result_payload,
        }
        await self.db.notifications.update_one(
            {"_id": _coerce_object_id(notification_id), "recipients": user_id},
            {
                "$set": {state_key: action_record},
                "$addToSet": {"read_by": user_id},
            },
        )
        try:
            await self.db.notification_action_logs.insert_one(
                {
                    "notification_id": notification_id,
                    "user_id": user_id,
                    "action": action_key,
                    "status": action_status,
                    "result": result_payload,
                    "created_at": now,
                }
            )
        except Exception:  # noqa: BLE001
            logger.debug("Unable to write notification action audit log", exc_info=True)
        return {"status": action_status, "action": action_key, "result": result_payload}

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        async with self._index_lock:
            if self._indexes_ready:
                return
            await self.db.notifications.create_index("recipients", background=True)
            await self.db.notifications.create_index("read_by", background=True)
            await self.db.notifications.create_index("project_id", background=True)
            await self.db.notifications.create_index("type", background=True)
            await self.db.notifications.create_index([("created_at", -1)])
            await self.db.notifications.create_index([("category", 1), ("created_at", -1)])
            # MongoDB cannot build a compound multikey index over two array
            # fields. Keep each array indexed separately and combine only one
            # array field with created_at for common list/count queries.
            await self.db.notifications.create_index(
                [("recipients", 1), ("created_at", -1)],
                background=True,
            )
            await self.db.notifications.create_index(
                [("dedupe_key", 1)],
                unique=True,
                sparse=True,
                background=True,
            )
            await self.db.notification_preferences.create_index("user_id", unique=True, background=True)
            await self.db.notification_preferences.create_index("organization_id", background=True)
            await self.db.project_notification_subscriptions.create_index(
                [("project_id", 1), ("user_id", 1)],
                unique=True,
                background=True,
            )
            await self.db.notification_templates.create_index(
                [("event_type", 1), ("channel", 1), ("locale", 1), ("active", 1)],
                background=True,
            )
            await self.db.notification_delivery_logs.create_index(
                [("notification_id", 1), ("user_id", 1), ("channel", 1), ("status", 1)],
                background=True,
            )
            await self.db.notification_action_logs.create_index(
                [("notification_id", 1), ("user_id", 1), ("action", 1), ("created_at", -1)],
                background=True,
            )
            self._indexes_ready = True

    def _resolve_owners(self, resource: Optional[dict]) -> List[str]:
        owners: List[str] = []
        if not resource:
            return owners
        for field in ("createdBy", "created_by", "owner_id", "assigned_to"):
            raw = resource.get(field)
            if raw:
                owners.append(str(raw))
        return owners

    @staticmethod
    def _extract_field(resource: Optional[dict], key: str) -> Optional[str]:
        if not resource:
            return None
        value = resource.get(key)
        return str(value) if value is not None else None

    async def _apply_preferences(
        self,
        recipients: List[str],
        *,
        event_type: NotificationType,
        organization_id: Optional[str],
        project_id: Optional[str],
        requested_channels: List[str],
    ) -> Tuple[List[str], List[str]]:
        if not recipients:
            return [], requested_channels

        filtered: List[str] = []
        channels_seen: set[str] = set()
        event_key = event_type.value

        for user_id in recipients:
            preference = await self._get_user_preference(
                user_id,
                organization_id=organization_id,
            )
            if not self._event_enabled(preference, event_key):
                continue
            if project_id:
                subscription = await self._get_project_subscription(
                    user_id,
                    project_id=project_id,
                    organization_id=organization_id,
                )
                if subscription and not subscription.get("subscribed", True):
                    continue
                if subscription and not self._event_enabled(subscription, event_key):
                    continue

            user_channels = self._resolve_channels(preference, event_key, requested_channels)
            if not user_channels:
                continue
            filtered.append(user_id)
            channels_seen.update(user_channels)

        return sorted(set(filtered)), sorted(channels_seen or set(requested_channels))

    async def _get_user_preference(
        self,
        user_id: str,
        *,
        organization_id: Optional[str],
    ) -> Dict[str, Any]:
        existing = await self.db.notification_preferences.find_one({"user_id": user_id})
        if existing:
            return existing

        user = await self.db.users.find_one({"_id": _coerce_object_id(user_id)}) or {}
        legacy = user.get("preferences") if isinstance(user, dict) else {}
        email_enabled = True
        if isinstance(legacy, dict):
            email_enabled = bool(legacy.get("emailNotifications", True))

        now = datetime.utcnow()
        preference = {
            "user_id": user_id,
            "organization_id": organization_id or (str(user.get("organization_id")) if user.get("organization_id") else None),
            "default_channels": ["in_app", "websocket", "email"] if email_enabled else ["in_app", "websocket"],
            "event_settings": {},
            "quiet_hours": {},
            "digest_enabled": True,
            "browser_notifications_enabled": False,
            "email_notifications_enabled": email_enabled,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.notification_preferences.update_one(
            {"user_id": user_id},
            {"$setOnInsert": preference},
            upsert=True,
        )
        return preference

    async def _get_project_subscription(
        self,
        user_id: str,
        *,
        project_id: str,
        organization_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        existing = await self.db.project_notification_subscriptions.find_one(
            {"project_id": project_id, "user_id": user_id}
        )
        if existing:
            return existing
        now = datetime.utcnow()
        subscription = {
            "project_id": project_id,
            "organization_id": organization_id,
            "user_id": user_id,
            "subscribed": True,
            "event_settings": {},
            "role_default_source": None,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.project_notification_subscriptions.update_one(
            {"project_id": project_id, "user_id": user_id},
            {"$setOnInsert": subscription},
            upsert=True,
        )
        return subscription

    @staticmethod
    def _event_enabled(settings_doc: Optional[Dict[str, Any]], event_key: str) -> bool:
        if not settings_doc:
            return True
        event_settings = settings_doc.get("event_settings") or {}
        current = event_settings.get(event_key) if isinstance(event_settings, dict) else None
        if isinstance(current, dict) and current.get("enabled") is False:
            return False
        return True

    @staticmethod
    def _resolve_channels(
        preference: Dict[str, Any],
        event_key: str,
        requested_channels: List[str],
    ) -> List[str]:
        event_settings = preference.get("event_settings") or {}
        current = event_settings.get(event_key) if isinstance(event_settings, dict) else None
        if isinstance(current, dict) and current.get("channels"):
            channels = [str(channel) for channel in current.get("channels") or []]
        else:
            channels = [str(channel) for channel in preference.get("default_channels") or requested_channels]

        if not preference.get("email_notifications_enabled", True):
            channels = [channel for channel in channels if channel != "email"]
        if not preference.get("browser_notifications_enabled", False):
            channels = [channel for channel in channels if channel != "browser"]

        requested = set(requested_channels)
        return [channel for channel in channels if channel in requested]

    @staticmethod
    def _coerce_action(action: NotificationAction | Dict[str, Any]) -> NotificationAction:
        if isinstance(action, NotificationAction):
            return action
        return NotificationAction(**action)

    async def _fanout_emails(self, notification: Notification, recipients: Iterable[str]) -> None:
        if not self.email_service:
            return
        tasks = [
            self.email_service.send_immediate_notification(user_id, notification)
            for user_id in recipients
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def daily_digest_window() -> datetime:
    return datetime.utcnow() - timedelta(days=1)


async def weekly_digest_window() -> datetime:
    return datetime.utcnow() - timedelta(days=7)


class NotificationEventService:
    """Canonical domain-event facade over persisted notifications.

    New domain services should depend on this facade. Existing code can keep using
    NotificationService.emit while callers are migrated incrementally.
    """

    def __init__(self, notification_service: NotificationService) -> None:
        self.notification_service = notification_service

    async def publish(
        self,
        event_type: NotificationType,
        resource_id: str,
        resource_type: str,
        **kwargs: Any,
    ) -> Notification:
        return await self.notification_service.emit(
            event_type,
            resource_id,
            resource_type,
            **kwargs,
        )
