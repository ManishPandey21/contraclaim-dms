from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..core.config import settings
from ..core.database import get_database


WRITE_PERMISSIONS = {
    "dms.document.upload",
    "dms.document.edit_metadata",
    "dms.document.delete",
    "dms.document.link_reference",
    "dms.status.update",
    "dms.comment.add",
    "dms.user.manage",
    "dms.project.manage",
    "dms.admin",
}

DMS_FEATURE_PERMISSIONS = {
    "dms.document.view",
    "dms.document.upload",
    "dms.document.edit_metadata",
    "dms.document.delete",
    "dms.document.download",
    "dms.document.bulk_download",
    "dms.document.link_reference",
    "dms.status.update",
    "dms.comment.add",
    "dms.dashboard.view",
    "dms.report.view",
    "dms.user.manage",
    "dms.project.manage",
    "dms.audit.view",
    "dms.admin",
}

DRAFTING_FEATURE_PERMISSIONS = {
    "drafting.request.create",
    "drafting.request.view",
    "drafting.request.accept",
    "drafting.request.assign",
    "drafting.draft.create",
    "drafting.draft.edit",
    "drafting.draft.submit_for_review",
    "drafting.review.perform",
    "drafting.review.approve",
    "drafting.review.return_for_revision",
    "drafting.final.view",
    "drafting.audit.view",
    "drafting.admin",
}


class EntitlementService:
    """Evaluates subscription and plan state for authorization decisions."""

    ACTIVE_STATUSES = {"trial", "pilot", "active"}
    ARCHIVE_STATUSES = {"archive"}
    OFFBOARDING_STATUSES = {"offboarding"}

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def _active_subscription(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not organization_id:
            return None
        db = await self._get_db()
        now = datetime.utcnow()
        scope_or = [
            {"organization_id": str(organization_id), "project_id": str(project_id or ""), "package_id": str(package_id or "")},
            {"organization_id": str(organization_id), "project_id": str(project_id or ""), "package_id": {"$in": [None, ""]}},
            {"organization_id": str(organization_id), "project_id": {"$in": [None, ""]}, "package_id": {"$in": [None, ""]}},
        ]
        query = {
            "$or": scope_or,
            "$and": [
                {"$or": [{"starts_at": {"$exists": False}}, {"starts_at": None}, {"starts_at": {"$lte": now}}]},
                {"$or": [{"ends_at": {"$exists": False}}, {"ends_at": None}, {"ends_at": {"$gte": now}}]},
            ],
        }
        return await db.subscriptions.find_one(query, sort=[("project_id", -1), ("updated_at", -1)])

    async def _plan_features(self, plan_code: Optional[str]) -> Dict[str, Any]:
        if not plan_code:
            return {}
        db = await self._get_db()
        plan = await db.plans.find_one({"code": str(plan_code)})
        if not plan:
            from .monetization_service import MonetizationService

            await MonetizationService(db).list_plans()
            plan = await db.plans.find_one({"code": str(plan_code)})
        if not plan:
            return {}
        return dict(plan.get("features") or {})

    async def effective_features(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        subscription = await self._active_subscription(
            organization_id=organization_id,
            project_id=project_id,
            package_id=package_id,
        )
        if not subscription:
            return {
                "source": "none",
                "subscription_id": None,
                "plan_code": None,
                "features": {
                    "feature.dms.enabled": False,
                    "feature.drafting.enabled": False,
                },
                "dms_enabled": False,
                "drafting_enabled": False,
            }

        features = await self._plan_features(subscription.get("plan_code"))
        features.update(subscription.get("entitlement_overrides") or {})
        return {
            "source": "project"
            if subscription.get("project_id") not in (None, "")
            else "organization",
            "subscription_id": str(subscription.get("_id") or ""),
            "plan_code": subscription.get("plan_code"),
            "features": features,
            "dms_enabled": bool(features.get("feature.dms.enabled", False)),
            "drafting_enabled": bool(features.get("feature.drafting.enabled", False)),
        }

    async def is_feature_enabled(
        self,
        feature_key: str,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
    ) -> bool:
        effective = await self.effective_features(
            organization_id=organization_id,
            project_id=project_id,
            package_id=package_id,
        )
        return bool((effective.get("features") or {}).get(feature_key, False))

    async def _collection_has_subscriptions(self) -> bool:
        db = await self._get_db()
        return await db.subscriptions.count_documents({}, limit=1) > 0

    async def check_permission_entitlement(
        self,
        *,
        permission: str,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        service_scoped_permissions = (
            DMS_FEATURE_PERMISSIONS
            | DRAFTING_FEATURE_PERMISSIONS
            | {"subscription.archive_access", "subscription.offboarding_export"}
        )
        if permission not in service_scoped_permissions:
            return True, "not_entitlement_scoped"
        if permission in {"drafting.request.assign", "drafting.admin"} and not organization_id and not project_id:
            return True, "drafting_admin_global"

        has_subscription_config = await self._collection_has_subscriptions()
        if not has_subscription_config:
            if settings.RBAC_ENTITLEMENT_FAIL_OPEN:
                return True, "legacy_no_subscription_records"
            return False, "no_subscription_records"

        subscription = await self._active_subscription(
            organization_id=organization_id,
            project_id=project_id,
            package_id=package_id,
        )
        if not subscription:
            return False, "no_active_subscription"

        status = str(subscription.get("status") or "").lower()
        features = await self._plan_features(subscription.get("plan_code"))
        features.update(subscription.get("entitlement_overrides") or {})

        if status in self.ARCHIVE_STATUSES:
            if permission in WRITE_PERMISSIONS or permission.startswith("drafting."):
                return False, "archive_read_only"
            return True, "archive_read_only"

        if status in self.OFFBOARDING_STATUSES:
            if permission in {"subscription.offboarding_export", "dms.document.download", "dms.document.bulk_download"}:
                return True, "offboarding_export"
            return False, "offboarding_export_only"

        if status not in self.ACTIVE_STATUSES:
            return False, f"subscription_{status or 'inactive'}"

        if permission in DMS_FEATURE_PERMISSIONS:
            return bool(features.get("feature.dms.enabled", False)), "dms_entitlement"

        if permission in DRAFTING_FEATURE_PERMISSIONS:
            return bool(features.get("feature.drafting.enabled", False)), "drafting_entitlement"

        return True, "not_entitlement_scoped"

    async def assert_quota_available(
        self,
        *,
        quota_key: str,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        quantity: int = 1,
    ) -> tuple[bool, str]:
        if not await self._collection_has_subscriptions():
            if settings.RBAC_ENTITLEMENT_FAIL_OPEN:
                return True, "legacy_no_subscription_records"
            return False, "no_subscription_records"
        subscription = await self._active_subscription(organization_id=organization_id, project_id=project_id)
        if not subscription:
            return False, "no_active_subscription"

        # Resolve limit: subscription overrides > plan defaults > unlimited
        overrides = subscription.get("entitlement_overrides") or {}
        limit_value = overrides.get(quota_key)

        if limit_value is None:
            # Fall back to plan default_limits
            plan_code = subscription.get("plan_code")
            if plan_code:
                db = await self._get_db()
                plan = await db.plans.find_one({"code": str(plan_code)})
                if plan:
                    limit_value = (plan.get("default_limits") or {}).get(quota_key)

        if limit_value is None:
            return True, "quota_unlimited"
        try:
            limit_int = int(limit_value)
        except Exception:
            return True, "quota_invalid_unlimited"

        # Count current-period usage from usage_events
        db = await self._get_db()
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        usage_query: dict = {
            "organization_id": str(organization_id),
            "event_type": quota_key,
            "created_at": {"$gte": period_start},
        }
        if project_id:
            usage_query["project_id"] = str(project_id)

        pipeline = [
            {"$match": usage_query},
            {"$group": {"_id": None, "total": {"$sum": "$quantity"}}},
        ]
        result = await db.usage_events.aggregate(pipeline).to_list(length=1)
        current_usage = result[0]["total"] if result else 0

        if current_usage + quantity > limit_int:
            return False, "quota_exceeded"
        return True, "quota_available"

