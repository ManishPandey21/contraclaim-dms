from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from ..core.config import settings
from ..core.database import get_database
from ..core.permissions import CLIENT_DMS_PERMISSIONS


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

DMS_FEATURE_PERMISSIONS = set(CLIENT_DMS_PERMISSIONS)

BASE_DMS_FEATURE = "feature.dms.enabled"
BASE_DRAFTING_FEATURE = "feature.drafting.enabled"

PERMISSION_FEATURE_REQUIREMENTS: Dict[str, Tuple[str, ...]] = {
    # DMS commercial modules. All DMS permissions still require the base DMS
    # feature; these keys add module-level subscription gates.
    "dms.claim.view": ("feature.dms.claims",),
    "dms.claim.create": ("feature.dms.claims",),
    "dms.claim.edit": ("feature.dms.claims",),
    "dms.claim.delete": ("feature.dms.claims",),
    "dms.claim.manage": ("feature.dms.claims",),
    "dms.claim.assess": ("feature.dms.claims",),
    "dms.contract.appraisal.view": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.generate": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.edit": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.approve": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.reject": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.export": ("feature.dms.contract_appraisal",),
    "dms.contract.appraisal.create_registers": ("feature.dms.contract_appraisal",),
    "dms.task.view": ("feature.dms.tasks",),
    "dms.task.create": ("feature.dms.tasks",),
    "dms.task.edit": ("feature.dms.tasks",),
    "dms.task.delete": ("feature.dms.tasks",),
    "dms.task.manage": ("feature.dms.tasks",),
    "dms.keydate.view": ("feature.dms.key_dates",),
    "dms.keydate.create": ("feature.dms.key_dates",),
    "dms.keydate.edit": ("feature.dms.key_dates",),
    "dms.keydate.delete": ("feature.dms.key_dates",),
    "dms.keydate.eot_submit": ("feature.dms.key_dates",),
    "dms.keydate.eot_approve": ("feature.dms.key_dates",),
    "dms.keydate.achievement": ("feature.dms.key_dates",),
    "dms.keydate.export": ("feature.dms.key_dates",),
    "dms.keydate.manage": ("feature.dms.key_dates",),
    "dms.variation.view": ("feature.dms.variations",),
    "dms.variation.create": ("feature.dms.variations",),
    "dms.variation.edit": ("feature.dms.variations",),
    "dms.variation.delete": ("feature.dms.variations",),
    "dms.variation.approve": ("feature.dms.variations",),
    "dms.variation.export": ("feature.dms.variations",),
    "dms.bankguarantee.view": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.create": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.edit": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.delete": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.extend": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.release": ("feature.dms.bank_guarantees",),
    "dms.bankguarantee.export": ("feature.dms.bank_guarantees",),
    "dms.insurance.view": ("feature.dms.insurance",),
    "dms.insurance.create": ("feature.dms.insurance",),
    "dms.insurance.edit": ("feature.dms.insurance",),
    "dms.insurance.delete": ("feature.dms.insurance",),
    "dms.insurance.export": ("feature.dms.insurance",),
    "dms.insurance.manage_types": ("feature.dms.insurance",),
    "dms.contract.master.view": ("feature.dms.contract_master",),
    "dms.contract.master.manage": ("feature.dms.contract_master",),
    "dms.contract.read": ("feature.dms.contract_master",),
    "dms.contract.update": ("feature.dms.contract_master",),
    "dms.contract.clause.create": ("feature.dms.contract_processing",),
    "dms.contract.clause.read": ("feature.dms.contract_processing",),
    "dms.ai.contract_processing.run": ("feature.dms.contract_processing",),
    "dms.ipc.view": ("feature.dms.ipc",),
    "dms.ipc.create": ("feature.dms.ipc",),
    "dms.ipc.edit": ("feature.dms.ipc",),
    "dms.ipc.delete": ("feature.dms.ipc",),
    "dms.ipc.approve": ("feature.dms.ipc",),
    "dms.ipc.export": ("feature.dms.ipc",),
    "dms.evidence_graph.view": ("feature.dms.evidence_graph",),
    "dms.evidence_graph.verify": ("feature.dms.evidence_graph",),
    "dms.evidence_graph.manage": ("feature.dms.evidence_graph",),
    # The register rides the evidence-graph feature it grew out of, so enabling
    # it widens no plan: exactly the plans that carried delay events carry it.
    "dms.hindrance.view": ("feature.dms.evidence_graph",),
    "dms.hindrance.create": ("feature.dms.evidence_graph",),
    "dms.hindrance.edit": ("feature.dms.evidence_graph",),
    "dms.hindrance.archive": ("feature.dms.evidence_graph",),
    "dms.contract.timeline.view": ("feature.dms.contract_timeline",),
    "dms.chronology.view": ("feature.dms.chronology",),
    "dms.chronology.create": ("feature.dms.chronology",),
    "dms.chronology.edit": ("feature.dms.chronology",),
    "dms.chronology.verify": ("feature.dms.chronology",),
    "dms.chronology.export": ("feature.dms.chronology",),
    "dms.chronology.admin": ("feature.dms.chronology",),
    "dms.arbitration.view": ("feature.dms.arbitration",),
    "dms.arbitration.create": ("feature.dms.arbitration",),
    "dms.arbitration.edit": ("feature.dms.arbitration",),
    "dms.arbitration.generate": ("feature.dms.arbitration",),
    "dms.arbitration.export": ("feature.dms.arbitration",),
    "dms.arbitration.approve": ("feature.dms.arbitration",),
    "dms.arbitration.audit": ("feature.dms.arbitration",),
    "dms.arbitration.admin": ("feature.dms.arbitration",),
    # Drafting sub-capabilities.
    "drafting.request.create": ("feature.drafting.requests",),
    "drafting.request.view": ("feature.drafting.requests",),
    "drafting.request.accept": ("feature.drafting.requests",),
    "drafting.request.assign": ("feature.drafting.assignment",),
    "drafting.draft.create": ("feature.drafting.ai_drafts",),
    "drafting.draft.edit": ("feature.drafting.ai_drafts",),
    "drafting.draft.submit_for_review": ("feature.drafting.review",),
    "drafting.review.perform": ("feature.drafting.review",),
    "drafting.review.approve": ("feature.drafting.review",),
    "drafting.review.return_for_revision": ("feature.drafting.review",),
    "drafting.final.view": ("feature.drafting.final",),
    "drafting.audit.view": ("feature.drafting.audit",),
    "drafting.admin": ("feature.drafting.admin",),
}

FULL_DMS_MODULE_FEATURES = {
    "feature.dms.claims": True,
    "feature.dms.contract_appraisal": True,
    "feature.dms.tasks": True,
    "feature.dms.key_dates": True,
    "feature.dms.variations": True,
    "feature.dms.bank_guarantees": True,
    "feature.dms.insurance": True,
    "feature.dms.contract_master": True,
    "feature.dms.contract_processing": True,
    "feature.dms.ipc": True,
    "feature.dms.evidence_graph": True,
    "feature.dms.contract_timeline": True,
    "feature.dms.chronology": True,
    "feature.dms.arbitration": True,
}

FULL_DRAFTING_MODULE_FEATURES = {
    "feature.drafting.requests": True,
    "feature.drafting.assignment": True,
    "feature.drafting.ai_drafts": True,
    "feature.drafting.review": True,
    "feature.drafting.final": True,
    "feature.drafting.audit": True,
    "feature.drafting.admin": True,
}

DEFAULT_PLAN_FEATURE_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "dms_enterprise": {BASE_DMS_FEATURE: True, **FULL_DMS_MODULE_FEATURES},
    "drafting_support_basic": {
        BASE_DMS_FEATURE: True,
        BASE_DRAFTING_FEATURE: True,
        "feature.drafting.requests": True,
        "feature.drafting.ai_drafts": True,
    },
    "contract_correspondence_desk": {
        BASE_DMS_FEATURE: True,
        BASE_DRAFTING_FEATURE: True,
        "feature.dms.tasks": True,
        "feature.dms.key_dates": True,
        "feature.dms.variations": True,
        "feature.dms.bank_guarantees": True,
        "feature.dms.insurance": True,
        "feature.dms.contract_master": True,
        "feature.dms.contract_processing": True,
        "feature.dms.contract_timeline": True,
        "feature.dms.chronology": True,
        **FULL_DRAFTING_MODULE_FEATURES,
    },
    "claims_commercial_desk": {
        BASE_DMS_FEATURE: True,
        BASE_DRAFTING_FEATURE: True,
        **FULL_DMS_MODULE_FEATURES,
        **FULL_DRAFTING_MODULE_FEATURES,
    },
    "dedicated_expert_desk": {
        BASE_DMS_FEATURE: True,
        BASE_DRAFTING_FEATURE: True,
        **FULL_DMS_MODULE_FEATURES,
        **FULL_DRAFTING_MODULE_FEATURES,
        "feature.dms.api_webhooks": True,
        "feature.dms.advanced_search": True,
        "feature.dms.ocr": True,
    },
}

DMS_READ_ONLY_ACTIONS = {
    "view",
    "download",
    "bulk_download",
    "export",
}


def _permission_action(permission: str) -> str:
    return (permission or "").replace(":", ".").split(".")[-1].lower()


def _is_write_or_admin_permission(permission: str) -> bool:
    if permission in WRITE_PERMISSIONS:
        return True
    if permission.startswith("drafting."):
        return True
    if permission.startswith("dms."):
        return _permission_action(permission) not in DMS_READ_ONLY_ACTIONS
    return False


def required_feature_keys(permission: str) -> Tuple[str, ...]:
    """Return subscription feature keys required by a permission."""
    if permission in DMS_FEATURE_PERMISSIONS:
        return (BASE_DMS_FEATURE, *PERMISSION_FEATURE_REQUIREMENTS.get(permission, ()))
    if permission in DRAFTING_FEATURE_PERMISSIONS:
        return (
            BASE_DRAFTING_FEATURE,
            *PERMISSION_FEATURE_REQUIREMENTS.get(permission, ()),
        )
    return PERMISSION_FEATURE_REQUIREMENTS.get(permission, ())


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
        features = dict(DEFAULT_PLAN_FEATURE_FALLBACKS.get(str(plan_code), {}))
        db = await self._get_db()
        plan = await db.plans.find_one({"code": str(plan_code)})
        if not plan:
            from .monetization_service import MonetizationService

            if not features:
                await MonetizationService(db).list_plans()
                plan = await db.plans.find_one({"code": str(plan_code)})
        if not plan:
            return features
        features.update(plan.get("features") or {})
        return features

    async def _addon_features(self, addon_codes: list) -> Dict[str, Any]:
        """Merge features from all active add-ons."""
        if not addon_codes:
            return {}
        db = await self._get_db()
        merged: Dict[str, Any] = {}
        cursor = db.addons.find({"code": {"$in": addon_codes}, "is_active": True})
        async for addon in cursor:
            merged.update(addon.get("features") or {})
        return merged

    async def _addon_limits(self, addon_codes: list) -> Dict[str, int]:
        """Merge additional limits/capacity from active add-ons."""
        if not addon_codes:
            return {}
        db = await self._get_db()
        merged: Dict[str, int] = {}
        cursor = db.addons.find({"code": {"$in": addon_codes}, "is_active": True})
        async for addon in cursor:
            for key, value in (addon.get("limits") or {}).items():
                try:
                    merged[key] = merged.get(key, 0) + int(value)
                except (TypeError, ValueError):
                    pass
        return merged

    async def _subscription_features(self, subscription: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve effective feature flags for one subscription.

        Precedence is: code-level default fallbacks < plan document features <
        active add-on features < subscription overrides.
        """
        features = await self._plan_features(subscription.get("plan_code"))
        addon_codes = subscription.get("active_add_ons") or []
        features.update(await self._addon_features(addon_codes))
        features.update(subscription.get("entitlement_overrides") or {})
        return features

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

        addon_codes = subscription.get("active_add_ons") or []
        features = await self._subscription_features(subscription)
        return {
            "source": "project"
            if subscription.get("project_id") not in (None, "")
            else "organization",
            "subscription_id": str(subscription.get("_id") or ""),
            "plan_code": subscription.get("plan_code"),
            "billing_period": subscription.get("billing_period", "monthly"),
            "trial": bool(subscription.get("trial")),
            "trial_ends_at": subscription.get("trial_ends_at"),
            "active_add_ons": addon_codes,
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
        features = await self._subscription_features(subscription)

        if status in self.ARCHIVE_STATUSES:
            if _is_write_or_admin_permission(permission):
                return False, "archive_read_only"
            return True, "archive_read_only"

        if status in self.OFFBOARDING_STATUSES:
            if permission in {"subscription.offboarding_export", "dms.document.download", "dms.document.bulk_download"}:
                return True, "offboarding_export"
            return False, "offboarding_export_only"

        if status not in self.ACTIVE_STATUSES:
            return False, f"subscription_{status or 'inactive'}"

        if permission in DMS_FEATURE_PERMISSIONS:
            if not bool(features.get(BASE_DMS_FEATURE, False)):
                return False, "dms_entitlement"
            for feature_key in required_feature_keys(permission)[1:]:
                if not bool(features.get(feature_key, False)):
                    return False, f"feature_disabled:{feature_key}"
            return True, "dms_entitlement"

        if permission in DRAFTING_FEATURE_PERMISSIONS:
            if not bool(features.get(BASE_DRAFTING_FEATURE, False)):
                return False, "drafting_entitlement"
            for feature_key in required_feature_keys(permission)[1:]:
                if not bool(features.get(feature_key, False)):
                    return False, f"feature_disabled:{feature_key}"
            return True, "drafting_entitlement"

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

        # Add capacity from active add-ons
        addon_codes = subscription.get("active_add_ons") or []
        addon_limits = await self._addon_limits(addon_codes)
        addon_boost = addon_limits.get(quota_key, 0)
        limit_int += addon_boost

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

    async def get_subscription_summary(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return a rich summary of the current subscription for dashboard display."""
        effective = await self.effective_features(
            organization_id=organization_id,
            project_id=project_id,
        )
        subscription = await self._active_subscription(
            organization_id=organization_id,
            project_id=project_id,
        )
        plan_info: Dict[str, Any] = {}
        if subscription and subscription.get("plan_code"):
            db = await self._get_db()
            plan = await db.plans.find_one({"code": str(subscription["plan_code"])})
            if plan:
                plan_info = {
                    "name": plan.get("name"),
                    "description": plan.get("description"),
                    "tier": plan.get("tier"),
                    "family": plan.get("family"),
                    "max_users": plan.get("max_users"),
                    "max_storage_gb": plan.get("max_storage_gb"),
                    "pricing_tiers": plan.get("pricing_tiers", {}),
                }

        return {
            **effective,
            "plan_info": plan_info,
            "status": str(subscription.get("status", "")) if subscription else "none",
            "billing_period": subscription.get("billing_period", "monthly") if subscription else None,
            "current_period_start": subscription.get("current_period_start") if subscription else None,
            "current_period_end": subscription.get("current_period_end") if subscription else None,
            "auto_renew": subscription.get("auto_renew", True) if subscription else False,
            "cancelled_at": subscription.get("cancelled_at") if subscription else None,
        }
