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
    DRAFT_REQUESTED = "draft_requested"
    APPROVAL_ASSIGNED = "approval_assigned"
    APPROVAL_COMPLETED = "approval_completed"
    APPROVAL_REJECTED = "approval_rejected"
    NEW_UPLOAD = "new_upload"
    BULK_UPLOAD_COMPLETED = "bulk_upload_completed"
    DOCUMENT_METADATA_UPDATED = "document_metadata_updated"
    DOCUMENT_DELETED = "document_deleted"
    DOCUMENT_ARCHIVED = "document_archived"
    DOCUMENT_VERSION_UPLOADED = "document_version_uploaded"
    DOCUMENT_VERSION_ROLLED_BACK = "document_version_rolled_back"
    COMMENT_ADDED = "comment_added"
    MENTION_ADDED = "mention_added"
    REPLY_ADDED = "reply_added"
    THREAD_RESOLVED = "thread_resolved"
    REPLY_REMINDER = "reply_reminder"
    REPLY_ESCALATION = "reply_escalation"
    PERMISSION_CHANGED = "permission_changed"
    UNUSUAL_LOGIN = "unusual_login"
    STORAGE_THRESHOLD_CROSSED = "storage_threshold_crossed"


class NotificationCategory(str, Enum):
    DRAFTING = "drafting"
    APPROVALS = "approvals"
    UPLOADS = "uploads"
    COMMENTS = "comments"
    REMINDERS = "reminders"
    SYSTEM = "system"
    SECURITY = "security"


class NotificationPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class NotificationAction(BaseModel):
    """Client-renderable action metadata.

    Mutating actions are still authorized by backend endpoints before execution.
    """

    key: str
    label: str
    method: str = "navigate"
    href: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


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
    archived_by: List[str] = Field(default_factory=list)
    priority: NotificationPriority = NotificationPriority.NORMAL
    severity: NotificationSeverity = NotificationSeverity.INFO
    title: Optional[str] = None
    message: Optional[str] = None
    actions: List[NotificationAction] = Field(default_factory=list)
    action_state: Dict[str, Any] = Field(default_factory=dict)
    resource_link: Optional[str] = None
    channels_requested: List[str] = Field(default_factory=lambda: ["in_app", "websocket"])
    delivery_summary: Dict[str, Any] = Field(default_factory=dict)
    dedupe_key: Optional[str] = None
    expires_at: Optional[datetime] = None
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
    archived: bool = False
    priority: NotificationPriority = NotificationPriority.NORMAL
    severity: NotificationSeverity = NotificationSeverity.INFO
    title: Optional[str] = None
    message: Optional[str] = None
    actions: List[NotificationAction] = Field(default_factory=list)
    action_state: Dict[str, Any] = Field(default_factory=dict)
    resource_link: Optional[str] = None
    channels_requested: List[str] = Field(default_factory=list)
    delivery_summary: Dict[str, Any] = Field(default_factory=dict)
    dedupe_key: Optional[str] = None
    expires_at: Optional[datetime] = None
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
            archived=user_id in notification.archived_by,
            priority=notification.priority,
            severity=notification.severity,
            title=notification.title or notification.data.get("title"),
            message=notification.message or notification.data.get("message"),
            actions=list(notification.actions or []),
            action_state=dict(notification.action_state or {}),
            resource_link=notification.resource_link,
            channels_requested=list(notification.channels_requested or []),
            delivery_summary=dict(notification.delivery_summary or {}),
            dedupe_key=notification.dedupe_key,
            expires_at=notification.expires_at,
            created_at=notification.created_at,
            created_by=notification.created_by,
            data=dict(notification.data or {}),
        )


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    unread_count: int
    has_more: bool


class NotificationPreference(BaseModel):
    """Per-user notification channel and event settings."""

    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    user_id: str
    organization_id: Optional[str] = None
    default_channels: List[str] = Field(default_factory=lambda: ["in_app", "websocket", "email"])
    event_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    quiet_hours: Dict[str, Any] = Field(default_factory=dict)
    digest_enabled: bool = True
    browser_notifications_enabled: bool = False
    email_notifications_enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            PyObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
        populate_by_name = True
        validate_by_name = True


class NotificationPreferenceResponse(BaseModel):
    user_id: str
    organization_id: Optional[str] = None
    default_channels: List[str]
    event_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    quiet_hours: Dict[str, Any] = Field(default_factory=dict)
    digest_enabled: bool
    browser_notifications_enabled: bool
    email_notifications_enabled: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class NotificationPreferenceUpdate(BaseModel):
    default_channels: Optional[List[str]] = None
    event_settings: Optional[Dict[str, Dict[str, Any]]] = None
    quiet_hours: Optional[Dict[str, Any]] = None
    digest_enabled: Optional[bool] = None
    browser_notifications_enabled: Optional[bool] = None
    email_notifications_enabled: Optional[bool] = None


class ProjectNotificationSubscription(BaseModel):
    """Current user's project-level subscription state."""

    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    project_id: str
    organization_id: Optional[str] = None
    user_id: str
    subscribed: bool = True
    event_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    role_default_source: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            PyObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
        populate_by_name = True
        validate_by_name = True


class ProjectNotificationSubscriptionResponse(BaseModel):
    project_id: str
    organization_id: Optional[str] = None
    user_id: str
    subscribed: bool
    event_settings: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    role_default_source: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProjectNotificationSubscriptionUpdate(BaseModel):
    subscribed: Optional[bool] = None
    event_settings: Optional[Dict[str, Dict[str, Any]]] = None


class NotificationTemplate(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    event_type: NotificationType
    channel: str = "email"
    locale: str = "en"
    subject_template: str
    title_template: str = "{title}"
    body_template: str
    html_template: Optional[str] = None
    required_variables: List[str] = Field(default_factory=list)
    active: bool = True
    created_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            PyObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
        populate_by_name = True
        validate_by_name = True


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class NotificationDeliveryLog(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    notification_id: Optional[str] = None
    user_id: str
    channel: str
    status: NotificationDeliveryStatus
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    attempt_count: int = 0
    last_attempt_at: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        arbitrary_types_allowed = True
        json_encoders = {
            ObjectId: str,
            PyObjectId: str,
            datetime: lambda value: value.isoformat(),
        }
        populate_by_name = True
        validate_by_name = True
