"""Subscription lifecycle background service.

Handles time-based subscription state transitions that run on a schedule:
- Trial expiration
- Renewal processing
- Usage counter resets
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from ..core.database import get_database

logger = logging.getLogger(__name__)


class SubscriptionLifecycleService:
    """Manages automated subscription lifecycle transitions."""

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    # ------------------------------------------------------------------
    # Trial Expiration
    # ------------------------------------------------------------------

    async def process_trial_expirations(self) -> List[str]:
        """Find and expire trial subscriptions whose trial_ends_at has passed.

        Returns a list of subscription IDs that were expired.
        """
        db = await self._get_db()
        now = datetime.utcnow()

        cursor = db.subscriptions.find({
            "status": "trial",
            "trial": True,
            "trial_ends_at": {"$lte": now},
        })

        expired_ids: List[str] = []
        async for sub in cursor:
            sub_id = str(sub["_id"])
            try:
                await db.subscriptions.update_one(
                    {"_id": sub["_id"]},
                    {
                        "$set": {
                            "status": "cancelled",
                            "billing_status": "inactive",
                            "trial": False,
                            "updated_at": now,
                            "updated_by": "system:lifecycle",
                        },
                        "$unset": {"current_scope_key": ""},
                    },
                )
                # Record history
                await db.subscription_history.insert_one({
                    "subscription_id": sub_id,
                    "organization_id": str(sub.get("organization_id", "")),
                    "project_id": sub.get("project_id"),
                    "change_type": "trial_expire",
                    "from_status": "trial",
                    "to_status": "cancelled",
                    "metadata": {"reason": "trial_period_ended"},
                    "changed_by": "system:lifecycle",
                    "changed_at": now,
                })
                expired_ids.append(sub_id)
                logger.info("Expired trial subscription %s for org %s",
                            sub_id, sub.get("organization_id"))
            except Exception as exc:
                logger.error("Failed to expire trial %s: %s", sub_id, exc)

        if expired_ids:
            logger.info("Expired %d trial subscriptions", len(expired_ids))
        return expired_ids

    # ------------------------------------------------------------------
    # Renewal Processing
    # ------------------------------------------------------------------

    async def process_upcoming_renewals(self, days_ahead: int = 7) -> List[Dict[str, Any]]:
        """Find subscriptions expiring within `days_ahead` days that have auto_renew enabled.

        Returns metadata about upcoming renewals for notification purposes.
        """
        db = await self._get_db()
        now = datetime.utcnow()
        from datetime import timedelta
        cutoff = now + timedelta(days=days_ahead)

        cursor = db.subscriptions.find({
            "status": {"$in": ["active", "pilot"]},
            "auto_renew": True,
            "current_period_end": {"$lte": cutoff, "$gte": now},
        })

        renewals: List[Dict[str, Any]] = []
        async for sub in cursor:
            sub_id = str(sub["_id"])
            renewals.append({
                "subscription_id": sub_id,
                "organization_id": str(sub.get("organization_id", "")),
                "project_id": sub.get("project_id"),
                "plan_code": sub.get("plan_code"),
                "billing_period": sub.get("billing_period", "monthly"),
                "current_period_end": sub.get("current_period_end"),
                "days_until_renewal": max(0, (sub.get("current_period_end", now) - now).days),
            })

        if renewals:
            logger.info("Found %d subscriptions renewing within %d days",
                        len(renewals), days_ahead)
        return renewals

    async def execute_renewals(self) -> List[str]:
        """Auto-renew subscriptions whose current_period_end has passed.

        Returns list of renewed subscription IDs.
        """
        db = await self._get_db()
        now = datetime.utcnow()

        cursor = db.subscriptions.find({
            "status": {"$in": ["active", "pilot"]},
            "auto_renew": True,
            "current_period_end": {"$lte": now},
        })

        renewed_ids: List[str] = []
        async for sub in cursor:
            sub_id = str(sub["_id"])
            try:
                billing_period = sub.get("billing_period", "monthly")
                new_start = sub.get("current_period_end", now)
                new_end = self._compute_period_end(new_start, billing_period)

                await db.subscriptions.update_one(
                    {"_id": sub["_id"]},
                    {
                        "$set": {
                            "current_period_start": new_start,
                            "current_period_end": new_end,
                            "updated_at": now,
                            "updated_by": "system:lifecycle",
                        }
                    },
                )
                await db.subscription_history.insert_one({
                    "subscription_id": sub_id,
                    "organization_id": str(sub.get("organization_id", "")),
                    "project_id": sub.get("project_id"),
                    "change_type": "renewal",
                    "from_status": str(sub.get("status", "")),
                    "to_status": str(sub.get("status", "")),
                    "to_billing_period": billing_period,
                    "metadata": {
                        "new_period_start": new_start.isoformat(),
                        "new_period_end": new_end.isoformat(),
                    },
                    "changed_by": "system:lifecycle",
                    "changed_at": now,
                })
                renewed_ids.append(sub_id)
                logger.info("Renewed subscription %s for org %s (period: %s to %s)",
                            sub_id, sub.get("organization_id"),
                            new_start.isoformat(), new_end.isoformat())
            except Exception as exc:
                logger.error("Failed to renew subscription %s: %s", sub_id, exc)

        if renewed_ids:
            logger.info("Renewed %d subscriptions", len(renewed_ids))
        return renewed_ids

    # ------------------------------------------------------------------
    # Usage Counter Reset
    # ------------------------------------------------------------------

    async def reset_monthly_usage_counters(self) -> int:
        """Archive and clear monthly usage counters for the previous period.

        Returns the number of counter documents archived.
        """
        db = await self._get_db()
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Archive current counters
        counters = await db.usage_counters.find({
            "period_start": {"$lt": period_start}
        }).to_list(length=None)

        if not counters:
            return 0

        for counter in counters:
            counter["archived_at"] = now
            counter.pop("_id", None)

        await db.usage_counters_archive.insert_many(counters)
        result = await db.usage_counters.delete_many({
            "period_start": {"$lt": period_start}
        })

        logger.info("Archived %d usage counter documents", result.deleted_count)
        return result.deleted_count

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_period_end(start: datetime, billing_period: str) -> datetime:
        from datetime import timedelta
        if billing_period == "quarterly":
            return start + timedelta(days=90)
        elif billing_period == "semi_annual":
            return start + timedelta(days=182)
        elif billing_period == "annual":
            return start + timedelta(days=365)
        return start + timedelta(days=30)
