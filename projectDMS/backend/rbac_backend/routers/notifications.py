from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.effective_scope import EffectiveScope
from ..core.security import CurrentUser, get_current_user
from ..dependencies import get_email_service, get_notification_service
from ..models.notification import (
    NotificationListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
)
from ..services.email_service import EmailService
from ..utils.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])
test_router = APIRouter(prefix="/notification-test", tags=["notifications"])


class MarkAllReadRequest(BaseModel):
    category: Optional[str] = None
    event_type: Optional[str] = None
    project_id: Optional[str] = None


class NotificationTestEmailRequest(BaseModel):
    target_user_id: Optional[str] = None
    event_type: str = "new_upload"


class NotificationActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = {}


def _is_notification_admin(current_user: CurrentUser) -> bool:
    roles = {str(role).lower() for role in current_user.roles or []}
    return bool(roles & {"superadmin", "orgadmin", "projectadmin"})


def _preference_response(doc: dict[str, Any]) -> NotificationPreferenceResponse:
    payload = dict(doc)
    payload.pop("_id", None)
    return NotificationPreferenceResponse(**payload)


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    category: str | None = Query(None),
    event_type: str | None = Query(None),
    project_id: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationListResponse:
    return await notification_service.list_notifications(
        current_user.id,
        unread_only=unread_only,
        category=category,
        event_type=event_type,
        project_id=project_id,
        search=search,
        limit=limit,
        skip=skip,
        # Recipient membership decides what was addressed to this user; the
        # effective scope decides which of it belongs to the organisation and
        # project they are currently working in.
        scope=EffectiveScope.resolve(current_user),
    )


@router.patch("/{notification_id}/read", status_code=status.HTTP_200_OK)
async def mark_notification_read(
    notification_id: str,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    updated = await notification_service.mark_as_read(notification_id, current_user.id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    return {"status": "ok"}


@router.post("/read-all", status_code=status.HTTP_200_OK)
async def mark_all_notifications_read(
    filters: MarkAllReadRequest | None = None,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    updated = await notification_service.mark_all_as_read(
        current_user.id,
        category=filters.category if filters else None,
        event_type=filters.event_type if filters else None,
        project_id=filters.project_id if filters else None,
    )
    return {"updated": updated}


@router.get("/unread-count")
async def unread_count(
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    count = await notification_service.count_unread(current_user.id)
    return {"unread_count": count}


@router.post("/{notification_id}/action", status_code=status.HTTP_200_OK)
async def execute_notification_action(
    notification_id: str,
    request: NotificationActionRequest,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    return await notification_service.execute_action(
        notification_id,
        current_user.id,
        request.action,
        payload=request.payload,
        current_user=current_user,
    )


@router.get("/preferences", response_model=NotificationPreferenceResponse)
async def get_preferences(
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationPreferenceResponse:
    existing = await db.notification_preferences.find_one({"user_id": current_user.id})
    if existing:
        return _preference_response(existing)

    now = datetime.utcnow()
    doc = {
        "user_id": current_user.id,
        "organization_id": current_user.organization_id,
        "default_channels": ["in_app", "websocket", "email"],
        "event_settings": {},
        "quiet_hours": {},
        "digest_enabled": True,
        "browser_notifications_enabled": False,
        "email_notifications_enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    await db.notification_preferences.update_one(
        {"user_id": current_user.id},
        {"$setOnInsert": doc},
        upsert=True,
    )
    return _preference_response(doc)


@router.patch("/preferences", response_model=NotificationPreferenceResponse)
async def update_preferences(
    payload: NotificationPreferenceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationPreferenceResponse:
    existing = await get_preferences(db=db, current_user=current_user)
    update_doc = payload.model_dump(exclude_unset=True, exclude_none=True)
    update_doc["updated_at"] = datetime.utcnow()
    await db.notification_preferences.update_one(
        {"user_id": current_user.id},
        {
            "$set": update_doc,
            "$setOnInsert": {
                "user_id": current_user.id,
                "organization_id": current_user.organization_id,
                "created_at": datetime.utcnow(),
                "event_settings": existing.event_settings,
                "quiet_hours": existing.quiet_hours,
            },
        },
        upsert=True,
    )
    saved = await db.notification_preferences.find_one({"user_id": current_user.id})
    return _preference_response(saved or {})


@router.get("/delivery-logs")
async def list_my_delivery_logs(
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    cursor = (
        db.notification_delivery_logs.find({"user_id": current_user.id})
        .sort("created_at", -1)
        .limit(limit)
    )
    logs = await cursor.to_list(length=limit)
    for item in logs:
        if "_id" in item:
            item["_id"] = str(item["_id"])
    return {"delivery_logs": logs}


@test_router.post("/email")
async def send_notification_test_email(
    payload: NotificationTestEmailRequest,
    email_service: EmailService = Depends(get_email_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    target_user_id = payload.target_user_id or current_user.id
    if target_user_id != current_user.id and not _is_notification_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only notification admins can send test emails to another user",
        )
    return await email_service.send_test_notification_email(
        target_user_id=target_user_id,
        actor_id=current_user.id,
        event_type=payload.event_type,
    )
