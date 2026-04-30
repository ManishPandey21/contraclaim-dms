"""Notification service handling persistence, WebSocket broadcast, and email fan-out."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId
from fastapi import WebSocket, WebSocketDisconnect
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.notification import (
    Notification,
    NotificationCategory,
    NotificationContext,
    NotificationListResponse,
    NotificationResponse,
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


_TYPE_DEFAULT_CATEGORY: dict[NotificationType, NotificationCategory] = {
    NotificationType.DRAFT_SAVED: NotificationCategory.DRAFTING,
    NotificationType.DRAFT_APPROVED: NotificationCategory.APPROVALS,
    NotificationType.DRAFT_REJECTED: NotificationCategory.APPROVALS,
    NotificationType.NEW_UPLOAD: NotificationCategory.UPLOADS,
    NotificationType.BULK_UPLOAD_COMPLETED: NotificationCategory.UPLOADS,
    NotificationType.COMMENT_ADDED: NotificationCategory.COMMENTS,
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
    ) -> Notification:
        await self._ensure_indexes()

        resolved_category = category or _TYPE_DEFAULT_CATEGORY.get(event_type, NotificationCategory.DRAFTING)
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

        notification = Notification(
            type=event_type,
            category=resolved_category,
            resource_id=resource_id,
            resource_type=resource_type,
            organization_id=self._extract_field(resource, "organization_id"),
            project_id=self._extract_field(resource, "project_id"),
            context=context,
            recipients=recipients,
            read_by=[actor_id] if actor_id and actor_id in recipients else [],
            created_by=actor_id,
            data=data or {},
        )

        if recipients:
            doc = notification.model_dump(by_alias=True, exclude_none=True)
            result = await self.db.notifications.insert_one(doc)
            notification.id = result.inserted_id  # type: ignore[assignment]

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
                    "created_at": notification.created_at.isoformat(),
                    "created_by": notification.created_by,
                    "data": notification.data,
                    "unread": True,
                },
            }
            await self.manager.broadcast(recipients, payload)

            if self.email_service and event_type in _IMMEDIATE_EMAIL_EVENTS:
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

        cursor = (
            self.db.notifications.find(query)
            .sort("created_at", -1)
            .skip(max(skip, 0))
            .limit(max(limit, 1))
        )
        raw_items = await cursor.to_list(length=None)
        notifications = [Notification(**item) for item in raw_items]
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

    async def mark_all_as_read(self, user_id: str) -> int:
        await self._ensure_indexes()
        result = await self.db.notifications.update_many(
            {"recipients": user_id, "read_by": {"$ne": user_id}},
            {"$addToSet": {"read_by": user_id}},
        )
        return result.modified_count

    async def count_unread(self, user_id: str) -> int:
        await self._ensure_indexes()
        return await self.db.notifications.count_documents(
            {"recipients": user_id, "read_by": {"$ne": user_id}}
        )

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
        return [Notification(**item) for item in items]

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        async with self._index_lock:
            if self._indexes_ready:
                return
            await self.db.notifications.create_index("recipients", background=True)
            await self.db.notifications.create_index("read_by", background=True)
            await self.db.notifications.create_index([("created_at", -1)])
            await self.db.notifications.create_index([("category", 1), ("created_at", -1)])
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
