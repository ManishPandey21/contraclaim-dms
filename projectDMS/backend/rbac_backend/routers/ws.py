import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect

from ..core.config import settings
from ..core.database import get_db
from ..core.security import principal_from_access_token
from ..dependencies import get_notification_service
from ..services.account_status import account_is_active
from ..utils.notification_service import NotificationService

router = APIRouter(prefix="/ws", tags=["websockets"])
logger = logging.getLogger(__name__)

#: 4401: not authenticated. 1013 ("try again later"): the session or role store
#: is unavailable, the WebSocket counterpart of the HTTP path's 503.
UNAUTHENTICATED_CLOSE = 4401
TRY_AGAIN_LATER_CLOSE = 1013

#: SEC-4: an open socket re-checks its account on every message and at least this
#: often, so disabling an account closes its notification sockets too.
ACCOUNT_RECHECK_SECONDS = 30.0


async def _account_still_active(db: Any, user_id: str) -> Optional[bool]:
    """True/False from the user record; None when it cannot be read (the caller fails closed)."""
    candidates: list = [user_id]
    try:
        from bson import ObjectId

        candidates.append(ObjectId(user_id))
    except Exception:
        pass
    try:
        user = await db.users.find_one({"_id": {"$in": candidates}}, {"disabled": 1, "is_active": 1})
    except Exception as exc:  # noqa: BLE001
        logger.error("WebSocket account re-check failed for %s: %s", user_id, exc)
        return None
    return account_is_active(user)


@router.websocket("/notifications")
async def websocket_notifications(
    websocket: WebSocket,
    notification_service: NotificationService = Depends(get_notification_service),
    db=Depends(get_db),
):
    """WebSocket endpoint for real-time notifications.

    Authentication strategy:
      * Preferred: token query parameter containing the JWT access token
      * Production: HttpOnly auth cookie set by the API login flow
      * Development only: user_id query parameter when ALLOW_DEV_HEADERS=True

    A token opens the socket exactly when it would authenticate an HTTP request:
    ``principal_from_access_token`` is the check ``get_current_user`` runs (token
    type, revocation floor, session liveness, existing account). A disabled
    account is refused as ``get_current_active_user`` refuses it. The connection
    is keyed by the resolved principal's id, never by an unchecked token claim.
    """
    token = websocket.query_params.get("token") or websocket.cookies.get(settings.AUTH_COOKIE_NAME)
    user_id: Optional[str] = None

    if token:
        try:
            principal = await principal_from_access_token(token, db)
        except HTTPException as exc:
            logger.error("WebSocket auth unavailable: %s", exc.detail)
            await websocket.close(code=TRY_AGAIN_LATER_CLOSE)
            return
        if principal is not None and principal.id and not principal.disabled:
            user_id = str(principal.id)
        else:
            logger.warning("WebSocket auth failed: token does not authenticate an active account")
    if not user_id and settings.ALLOW_DEV_HEADERS:
        user_id = websocket.query_params.get("user_id")

    if not user_id:
        await websocket.close(code=UNAUTHENTICATED_CLOSE)
        return

    await notification_service.manager.connect(websocket, user_id)
    try:
        while True:
            try:
                message: Optional[str] = await asyncio.wait_for(
                    websocket.receive_text(), timeout=ACCOUNT_RECHECK_SECONDS
                )
            except asyncio.TimeoutError:
                message = None
            active = await _account_still_active(db, user_id)
            if active is not True:
                logger.warning("WebSocket closed for %s: the account is no longer active", user_id)
                await notification_service.manager.disconnect(user_id, websocket)
                await websocket.close(code=UNAUTHENTICATED_CLOSE if active is False else TRY_AGAIN_LATER_CLOSE)
                return
            if message == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await notification_service.manager.disconnect(user_id, websocket)
    except Exception as exc:  # noqa: BLE001
        logger.error("WebSocket error for %s: %s", user_id, exc)
        await notification_service.manager.disconnect(user_id, websocket)
        await websocket.close(code=1011)
