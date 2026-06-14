"""Usage metering service for automatic tracking and quota enforcement.

Provides `check_and_record()` — an atomic check-quota-then-record-usage
helper that should be called by every metered operation (AI drafting,
document upload, OCR, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from ..core.database import get_database
from ..services.entitlement_service import EntitlementService

logger = logging.getLogger(__name__)


# Well-known event types that map to plan quota keys
class UsageEventType:
    """Canonical usage event type identifiers."""

    DRAFTED_LETTER = "limit.drafted_letters_month"
    AI_REVIEW = "limit.ai_reviews_month"
    DOCUMENT_UPLOAD = "limit.document_uploads_month"
    OCR_PAGE = "limit.ocr_pages_month"
    ADVANCED_SEARCH = "limit.advanced_searches_month"


class UsageMeteringService:
    """Central service for recording and enforcing metered usage."""

    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.entitlement_service = EntitlementService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def check_and_record(
        self,
        *,
        event_type: str,
        organization_id: str,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
        quantity: int = 1,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check quota availability and atomically record usage.

        Raises:
            QuotaExceededError: when the quota for ``event_type`` is exhausted.

        Returns:
            A dict with ``allowed``, ``reason``, and ``usage_event_id``.
        """
        # 1. Quota check
        allowed, reason = await self.entitlement_service.assert_quota_available(
            quota_key=event_type,
            organization_id=organization_id,
            project_id=project_id,
            quantity=quantity,
        )

        if not allowed:
            logger.warning(
                "Quota exceeded: event_type=%s org=%s project=%s reason=%s",
                event_type,
                organization_id,
                project_id,
                reason,
            )
            raise QuotaExceededError(event_type=event_type, reason=reason)

        # 2. Record the event
        db = await self._get_db()
        now = datetime.utcnow()
        event_doc = {
            "organization_id": str(organization_id),
            "project_id": str(project_id or ""),
            "user_id": str(user_id or ""),
            "event_type": event_type,
            "quantity": quantity,
            "metadata": metadata or {},
            "created_at": now,
        }
        result = await db.usage_events.insert_one(event_doc)

        logger.info(
            "Usage recorded: event_type=%s org=%s project=%s qty=%d",
            event_type,
            organization_id,
            project_id,
            quantity,
        )

        return {
            "allowed": True,
            "reason": reason,
            "usage_event_id": str(result.inserted_id),
        }

    async def get_current_period_usage(
        self,
        *,
        event_type: str,
        organization_id: str,
        project_id: Optional[str] = None,
    ) -> int:
        """Return total quantity used in the current calendar month."""
        db = await self._get_db()
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        query: Dict[str, Any] = {
            "organization_id": str(organization_id),
            "event_type": event_type,
            "created_at": {"$gte": period_start},
        }
        if project_id:
            query["project_id"] = str(project_id)

        pipeline = [
            {"$match": query},
            {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
        ]
        result = await db.usage_events.aggregate(pipeline).to_list(length=1)
        return result[0]["total"] if result else 0

    async def get_usage_summary(
        self,
        *,
        organization_id: str,
        project_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """Return a dict of ``{event_type: total_quantity}`` for the current billing period."""
        db = await self._get_db()
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        query: Dict[str, Any] = {
            "organization_id": str(organization_id),
            "created_at": {"$gte": period_start},
        }
        if project_id:
            query["project_id"] = str(project_id)

        pipeline = [
            {"$match": query},
            {"$group": {"_id": "$event_type", "total": {"$sum": "$quantity"}}},
        ]
        rows = await db.usage_events.aggregate(pipeline).to_list(length=None)
        return {row["_id"]: row["total"] for row in rows}


class QuotaExceededError(Exception):
    """Raised when a metered operation exceeds the plan's quota."""

    def __init__(self, event_type: str, reason: str) -> None:
        self.event_type = event_type
        self.reason = reason
        super().__init__(f"Quota exceeded for {event_type}: {reason}")
