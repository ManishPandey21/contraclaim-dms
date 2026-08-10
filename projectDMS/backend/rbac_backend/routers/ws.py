import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from ..core.config import settings
from ..core.database import get_database
from ..core.security import verify_access_token
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
      * Preferred: token query parameter containing the JWT access token
      * Production: HttpOnly auth cookie set by the API login flow
      * Development only: user_id query parameter when ALLOW_DEV_HEADERS=True
    """
    token = websocket.query_params.get("token") or websocket.cookies.get(settings.AUTH_COOKIE_NAME)
    user_id: Optional[str] = None

    if token:
        # Held to the same contract as HTTP. Previously this decoded the
        # signature and connected on the user id alone, so a deactivated,
        # deleted or logged-out user's unexpired token -- and a step-up token,
        # which HTTP rejects outright -- could still open a live stream.
        try:
            user = await verify_access_token(token, await get_database())
        except HTTPException as exc:
            # Session store configured but unreachable under the fail-closed
            # policy: refuse the socket rather than serve unverifiable state.
            logger.warning("WebSocket auth unavailable: %s", exc.detail)
            await websocket.close(code=4503)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("WebSocket auth failed: %s", exc)
            user = None
        if user:
            user_id = str(user["_id"])
        else:
            logger.warning("WebSocket auth rejected a token that HTTP would also refuse")

    if not user_id and settings.ALLOW_DEV_HEADERS:
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
