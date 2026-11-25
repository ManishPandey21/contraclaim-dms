"""Dependency helpers for FastAPI routers."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends
from fastapi.params import Depends as DependsMarker
from motor.motor_asyncio import AsyncIOMotorDatabase

from .core.database import get_database
from .services.email_service import EmailService
from .utils.notification_service import ConnectionManager, NotificationService

_connection_manager = ConnectionManager()
_notification_service: Optional[NotificationService] = None
_email_service: Optional[EmailService] = None


async def _resolve_db(db: Optional[AsyncIOMotorDatabase]) -> AsyncIOMotorDatabase:
    if isinstance(db, DependsMarker) or db is None:
        return await get_database()
    return db


async def get_notification_service(
    db: AsyncIOMotorDatabase | DependsMarker | None = Depends(get_database),
) -> NotificationService:
    """Return the notification service bound to the current database handle.

    One service per process while the handle is unchanged (the WebSocket connection
    manager is shared regardless). If the handle changes, through ``disconnect`` and
    ``connect`` or a test harness pointing the process at another database, the service
    is rebuilt for the new handle instead of writing through a closed client (DEF-6;
    before, the first handle was kept for the life of the process).
    """
    global _notification_service, _email_service

    db_obj = await _resolve_db(db)
    if _notification_service is None or _notification_service.db is not db_obj:
        if _email_service is None or _email_service.db is not db_obj:
            _email_service = EmailService(db_obj)
        _notification_service = NotificationService(
            db_obj,
            manager=_connection_manager,
            email_service=_email_service,
        )
        _email_service.notification_service = _notification_service
    return _notification_service


async def get_email_service(
    db: AsyncIOMotorDatabase | DependsMarker | None = Depends(get_database),
) -> EmailService:
    global _email_service, _notification_service
    db_obj = await _resolve_db(db)
    if _email_service is None or _email_service.db is not db_obj:
        _email_service = EmailService(db_obj)
        if _notification_service is not None and _notification_service.db is db_obj:
            _email_service.notification_service = _notification_service
    return _email_service
