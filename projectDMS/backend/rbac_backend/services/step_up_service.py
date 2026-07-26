from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
import jwt
from jwt import PyJWTError as JWTError

from ..core.config import settings
from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user, verify_password
from ..services.audit_event_service import AuditEventService


STEP_UP_TTL_MINUTES = 10


class StepUpService:
    """Issues and verifies short-lived tokens for dangerous actions."""

    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit_service = AuditEventService(db)

    def create_token(self, *, user_id: str, action: str) -> str:
        now = datetime.utcnow()
        payload = {
            "sub": str(user_id),
            "typ": "step_up",
            "action": str(action),
            "iat": int(now.timestamp()),
            "exp": now + timedelta(minutes=STEP_UP_TTL_MINUTES),
        }
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    def verify_token(self, *, token: str, user_id: str, action: str) -> None:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        except JWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Step-up verification is required for this action",
            ) from exc
        if payload.get("typ") != "step_up" or str(payload.get("sub")) != str(user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Step-up token is not valid for this user",
            )
        token_action = str(payload.get("action") or "")
        if token_action not in {str(action), "*"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Step-up token is not valid for this action",
            )

    async def issue_after_password(
        self,
        *,
        current_user: CurrentUser,
        password: str,
        action: str,
    ) -> str:
        if self.db is None:
            raise HTTPException(status_code=500, detail="Database is required for step-up")
        user_doc = await self.db.users.find_one({"_id": self._id_lookup(current_user.id)})
        if not user_doc:
            user_doc = await self.db.users.find_one({"email": current_user.email})
        hashed = (user_doc or {}).get("hashed_password") or (user_doc or {}).get("passwordHash")
        if not hashed or not verify_password(password, hashed):
            await self.audit_service.emit(
                action="security.step_up_failed",
                actor_id=current_user.id,
                resource_type="step_up",
                result="deny",
                reason="invalid_password",
                metadata={"step_up_action": action},
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid password")
        token = self.create_token(user_id=current_user.id, action=action)
        await self.audit_service.emit(
            action="security.step_up_issued",
            actor_id=current_user.id,
            resource_type="step_up",
            result="success",
            metadata={"step_up_action": action, "ttl_minutes": STEP_UP_TTL_MINUTES},
        )
        return token

    @staticmethod
    def _id_lookup(value: str) -> Any:
        try:
            from bson import ObjectId

            return ObjectId(str(value))
        except Exception:
            return str(value)


async def require_step_up(
    request: Request,
    current_user: CurrentUser,
    *,
    action: str,
    db: Any = None,
) -> None:
    token: Optional[str] = request.headers.get("x-step-up-token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Step-up verification is required for this action",
        )
    StepUpService(db).verify_token(token=token, user_id=current_user.id, action=action)


def step_up_dependency(action: str):
    """FastAPI dependency factory for routes that require recent step-up auth."""

    async def _dependency(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
        db: Any = Depends(get_db),
    ) -> None:
        await require_step_up(request, current_user, action=action, db=db)

    safe_name = str(action).replace(".", "_").replace(":", "_").replace("-", "_")
    _dependency.__name__ = f"require_step_up_{safe_name}"
    return _dependency
