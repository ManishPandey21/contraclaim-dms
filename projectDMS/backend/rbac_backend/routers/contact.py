"""Public contact endpoint for landing-page enquiries."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core.config import settings
from ..core.database import get_database
from ..dependencies import get_email_service
from ..services.email_service import EmailService
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contact", tags=["contact"])
rate_limiter = RateLimiter(requests_per_minute=5, window_seconds=3600)


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=5, max_length=254)
    organization: Optional[str] = Field(default=None, max_length=160)
    phone: Optional[str] = Field(default=None, max_length=60)
    message: str = Field(..., min_length=10, max_length=4000)

    @field_validator("name", "message", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("organization", "phone", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("Invalid email address")
        return value


@router.post("")
async def submit_contact_request(
    payload: ContactRequest,
    request: Request,
    db: AsyncIOMotorDatabase = Depends(get_database),
    email_service: EmailService = Depends(get_email_service),
) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    await rate_limiter.check_ip_limit(
        client_ip,
        cost=1,
        window_seconds=3600,
        max_requests=5,
    )

    recipient = str(settings.CONTACT_RECIPIENT_EMAIL or "").strip()
    if not recipient:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Contact recipient is not configured",
        )

    now = datetime.utcnow()
    submission = payload.model_dump()
    submission.update(
        {
            "recipient": recipient,
            "client_ip": client_ip,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
    )

    insert_result = await db.contact_submissions.insert_one(submission)
    sent = await email_service.send_contact_email(
        recipient=recipient,
        name=payload.name,
        email=str(payload.email),
        organization=payload.organization,
        phone=payload.phone,
        message_body=payload.message,
    )

    await db.contact_submissions.update_one(
        {"_id": insert_result.inserted_id},
        {
            "$set": {
                "status": "sent" if sent else "failed",
                "updated_at": datetime.utcnow(),
            }
        },
    )

    if not sent:
        logger.warning("Contact request email failed for submission %s", insert_result.inserted_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Contact email could not be sent. Please try again later.",
        )

    return {"message": "Contact request sent"}
