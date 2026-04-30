import logging
from typing import Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt

from ..core.config import settings
from ..dependencies import get_notification_service
from ..utils.notification_service import NotificationService

router = APIRouter(prefix="/ws", tags=["websockets"])
logger = logging.getLogger(__name__)


@router.websocket("/notifications")
async def websocket_notifications(
    websocket: WebSocket,
    notification_service: NotificationService = Depends(get_notification_service),
):
    """WebSocket endpoint for real-time notifications.

    Authentication strategy:
      * Preferred: 	oken query parameter containing the JWT access token
      * Fallback: user_id query parameter (development/testing only)
    """
    token = websocket.query_params.get("token")
    user_id: Optional[str] = None

    if token:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            user_id = str(payload.get("user_id") or payload.get("sub"))
        except JWTError as exc:
            logger.warning("WebSocket auth failed: %s", exc)
    if not user_id:
        user_id = websocket.query_params.get("user_id")

    if not user_id:
        await websocket.close(code=4401)
        return

    await notification_service.manager.connect(websocket, user_id)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await notification_service.manager.disconnect(user_id, websocket)
    except Exception as exc:  # noqa: BLE001
        logger.error("WebSocket error for %s: %s", user_id, exc)
        await notification_service.manager.disconnect(user_id, websocket)
        await websocket.close(code=1011)
