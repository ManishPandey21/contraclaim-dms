from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.security import CurrentUser, get_current_user
from ..dependencies import get_notification_service
from ..models.notification import NotificationListResponse
from ..utils.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(False),
    category: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> NotificationListResponse:
    return await notification_service.list_notifications(
        current_user.id,
        unread_only=unread_only,
        category=category,
        limit=limit,
        skip=skip,
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
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    updated = await notification_service.mark_all_as_read(current_user.id)
    return {"updated": updated}


@router.get("/unread-count")
async def unread_count(
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    count = await notification_service.count_unread(current_user.id)
    return {"unread_count": count}
