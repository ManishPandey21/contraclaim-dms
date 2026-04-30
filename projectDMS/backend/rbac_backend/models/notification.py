"""Pydantic models for persisted notifications and API responses."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field

from .user import PyObjectId


class NotificationType(str, Enum):
    DRAFT_SAVED = "draft_saved"
    DRAFT_APPROVED = "draft_approved"
    DRAFT_REJECTED = "draft_rejected"
    NEW_UPLOAD = "new_upload"
    BULK_UPLOAD_COMPLETED = "bulk_upload_completed"
    COMMENT_ADDED = "comment_added"


class NotificationCategory(str, Enum):
    DRAFTING = "drafting"
    APPROVALS = "approvals"
    UPLOADS = "uploads"
    COMMENTS = "comments"


class NotificationContext(str, Enum):
    GLOBAL = "global"
    ORGANIZATION = "organization"
    PROJECT = "project"


class Notification(BaseModel):
    """Stored notification document."""

    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    type: NotificationType
    category: NotificationCategory
    resource_id: str
    resource_type: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    context: NotificationContext = NotificationContext.GLOBAL
    recipients: List[str] = Field(default_factory=list)
    read_by: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            PyObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
        populate_by_name = True
        validate_by_name = True


class NotificationResponse(BaseModel):
    """API response shape for a single notification."""

    id: str
    type: NotificationType
    category: NotificationCategory
    resource_id: str
    resource_type: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    context: NotificationContext = NotificationContext.GLOBAL
    unread: bool
    created_at: datetime
    created_by: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_notification(
        cls, notification: Notification, user_id: str
    ) -> "NotificationResponse":
        return cls(
            id=str(notification.id),
            type=notification.type,
            category=notification.category,
            resource_id=notification.resource_id,
            resource_type=notification.resource_type,
            organization_id=notification.organization_id,
            project_id=notification.project_id,
            context=notification.context,
            unread=user_id not in notification.read_by,
            created_at=notification.created_at,
            created_by=notification.created_by,
            data=dict(notification.data or {}),
        )


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    unread_count: int
    has_more: bool
