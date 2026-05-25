from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..core.database import get_database
from ..models.rbac_monetization import (
    BillingRecordCreate,
    PlanSettingsMode,
    PlanSettingsScopeUpdate,
    PlanCreate,
    PlanUpdate,
    SubscriptionCreate,
    SubscriptionUpdate,
    UsageEventCreate,
)
from ..services.audit_event_service import AuditEventService


class MonetizationService:
    DEFAULT_PLANS = [
        {
            "code": "dms_starter",
            "name": "DMS Starter",
            "family": "dms_saas",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 2500000,
            "features": {"feature.dms.enabled": True},
            "default_limits": {"limit.users": 10},
            "is_active": True,
        },
        {
            "code": "dms_professional",
            "name": "DMS Professional",
            "family": "dms_saas",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 7500000,
            "features": {"feature.dms.enabled": True, "feature.dms.ocr": True, "feature.dms.advanced_search": True},
            "default_limits": {"limit.users": 30},
            "is_active": True,
        },
        {
            "code": "dms_enterprise",
            "name": "DMS Enterprise",
            "family": "dms_saas",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 20000000,
            "features": {"feature.dms.enabled": True, "feature.dms.ocr": True, "feature.dms.advanced_search": True, "feature.dms.api_webhooks": True},
            "default_limits": {},
            "is_active": True,
        },
        {
            "code": "drafting_support_basic",
            "name": "Drafting Support Basic",
            "family": "drafting_bundle",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 10000000,
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {"limit.drafted_letters_month": 15},
            "is_active": True,
        },
        {
            "code": "contract_correspondence_desk",
            "name": "Contract Correspondence Desk",
            "family": "drafting_bundle",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 25000000,
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {"limit.drafted_letters_month": 40},
            "is_active": True,
        },
        {
            "code": "claims_commercial_desk",
            "name": "Claims & Commercial Desk",
            "family": "drafting_bundle",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 60000000,
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {},
            "is_active": True,
        },
        {
            "code": "dedicated_expert_desk",
            "name": "Dedicated Expert Desk",
            "family": "drafting_bundle",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 0,
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {},
            "is_active": True,
        },
        {
            "code": "archive_read_only",
            "name": "Archive / Read Only",
            "family": "archive",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 1000000,
            "features": {"feature.dms.enabled": True, "mode.archive_read_only": True},
            "default_limits": {},
            "is_active": True,
        },
        {
            "code": "no_service_override",
            "name": "No Service",
            "family": "no_service",
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 0,
            "features": {"feature.dms.enabled": False, "feature.drafting.enabled": False},
            "default_limits": {},
            "is_active": True,
        },
    ]

    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit_service = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    @staticmethod
    def _lookup_id(value: str) -> Any:
        try:
            return ObjectId(str(value))
        except Exception:
            return str(value)

    @staticmethod
    def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, ObjectId):
                return str(value)
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, list):
                return [convert(item) for item in value]
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            return value

        doc = convert(dict(doc))
        if "_id" in doc:
            doc["id"] = str(doc.pop("_id"))
        return doc

    async def seed_default_plans(self) -> None:
        """Seed default plans into the database. Should be called once at startup."""
        db = await self._get_db()
        now = datetime.utcnow()
        for plan in self.DEFAULT_PLANS:
            await db.plans.update_one(
                {"code": plan["code"]},
                {
                    "$setOnInsert": {
                        **plan,
                        "created_at": now,
                        "updated_at": now,
                        "created_by": "system",
                    }
                },
                upsert=True,
            )

    async def list_plans(self) -> List[Dict[str, Any]]:
        db = await self._get_db()
        # Ensure plans are seeded if collection is empty (backward compatibility)
        if await db.plans.count_documents({}, limit=1) == 0:
            await self.seed_default_plans()
        return [self._normalize(row) for row in await db.plans.find({}).sort("code", 1).to_list(length=None)]

    async def validate_plan_code(self, plan_code: str) -> bool:
        """Return True if the plan_code exists and is active."""
        db = await self._get_db()
        plan = await db.plans.find_one({"code": plan_code, "is_active": True})
        if plan:
            return True
        # Might not be seeded yet — check defaults
        return any(p["code"] == plan_code for p in self.DEFAULT_PLANS)


    async def upsert_plan(self, payload: PlanCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        doc = payload.model_dump(mode="json")
        doc.update({"updated_at": now, "updated_by": getattr(current_user, "id", None)})
        await db.plans.update_one(
            {"code": payload.code},
            {"$set": doc, "$setOnInsert": {"created_at": now, "created_by": getattr(current_user, "id", None)}},
            upsert=True,
        )
        saved = await db.plans.find_one({"code": payload.code})
        await self.audit_service.emit(
            action="billing.plan.upserted",
            actor_id=getattr(current_user, "id", None),
            resource_type="plan",
            resource_id=payload.code,
            after=saved,
        )
        return self._normalize(saved)

    async def update_plan(self, plan_id: str, payload: PlanUpdate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        lookup = self._lookup_id(plan_id)
        update_doc = payload.model_dump(mode="json", exclude_unset=True)
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        await db.plans.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.plans.find_one({"_id": lookup})
        return self._normalize(saved)

    async def list_subscriptions(
        self,
        organization_id: Optional[str] = None,
        organization_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = str(organization_id)
        elif organization_ids is not None:
            query["organization_id"] = {"$in": [str(item) for item in organization_ids]}
        return [self._normalize(row) for row in await db.subscriptions.find(query).sort("updated_at", -1).to_list(length=None)]

    async def create_subscription(self, payload: SubscriptionCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        doc = payload.model_dump(mode="json")
        doc.update(
            {
                "created_at": now,
                "updated_at": now,
                "created_by": getattr(current_user, "id", None),
                "updated_by": getattr(current_user, "id", None),
            }
        )
        result = await db.subscriptions.insert_one(doc)
        saved = await db.subscriptions.find_one({"_id": result.inserted_id})
        await self.audit_service.emit(
            action="subscription.created",
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=str(result.inserted_id),
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            after=saved,
        )
        return self._normalize(saved)

    async def update_subscription(self, subscription_id: str, payload: SubscriptionUpdate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        lookup = self._lookup_id(subscription_id)
        before = await db.subscriptions.find_one({"_id": lookup})
        update_doc = payload.model_dump(mode="json", exclude_unset=True)
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        await db.subscriptions.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.subscriptions.find_one({"_id": lookup})
        await self.audit_service.emit(
            action="subscription.updated",
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=subscription_id,
            organization_id=str(saved.get("organization_id")) if saved else None,
            project_id=str(saved.get("project_id")) if saved else None,
            before=before,
            after=saved,
        )
        return self._normalize(saved)

    async def record_usage(self, payload: UsageEventCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = payload.model_dump(mode="json")
        doc.update({"created_at": datetime.utcnow(), "created_by": getattr(current_user, "id", None)})
        result = await db.usage_events.insert_one(doc)
        saved = await db.usage_events.find_one({"_id": result.inserted_id})
        return self._normalize(saved)

    async def create_billing_record(self, payload: BillingRecordCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = payload.model_dump(mode="json")
        doc.update({"created_at": datetime.utcnow(), "created_by": getattr(current_user, "id", None)})
        result = await db.billing_records.insert_one(doc)
        saved = await db.billing_records.find_one({"_id": result.inserted_id})
        await self.audit_service.emit(
            action="billing.record.created",
            actor_id=getattr(current_user, "id", None),
            resource_type="billing_record",
            resource_id=str(result.inserted_id),
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            after=saved,
        )
        return self._normalize(saved)

    @staticmethod
    def _id_values(value: Any) -> List[Any]:
        if value is None or value == "":
            return [None, ""]
        values: List[Any] = [str(value)]
        try:
            oid = ObjectId(str(value))
            values.append(oid)
        except Exception:
            pass
        return values

    @staticmethod
    def _org_id_from_project(project: Dict[str, Any]) -> str:
        return str(project.get("organization_id") or project.get("organizationId") or "")

    @staticmethod
    def _subscription_scope_query(organization_id: str, project_id: Optional[str]) -> Dict[str, Any]:
        query: Dict[str, Any] = {"organization_id": str(organization_id)}
        if project_id:
            query["project_id"] = str(project_id)
        else:
            query["project_id"] = {"$in": [None, ""]}
        query["package_id"] = {"$in": [None, ""]}
        return query

    @staticmethod
    def _active_subscription_match(subscription: Dict[str, Any], organization_id: str, project_id: Optional[str]) -> bool:
        if str(subscription.get("organization_id") or "") != str(organization_id):
            return False
        sub_project = subscription.get("project_id")
        if project_id:
            return str(sub_project or "") == str(project_id)
        return sub_project in (None, "")

    @staticmethod
    def _service_state(
        subscription: Optional[Dict[str, Any]],
        plan_by_code: Dict[str, Dict[str, Any]],
        source: str,
    ) -> Dict[str, Any]:
        if not subscription:
            return {
                "source": "none",
                "subscription_id": None,
                "plan_code": None,
                "plan_name": None,
                "features": {
                    "feature.dms.enabled": False,
                    "feature.drafting.enabled": False,
                },
                "dms_enabled": False,
                "drafting_enabled": False,
            }

        plan_code = str(subscription.get("plan_code") or "")
        plan = plan_by_code.get(plan_code) or {}
        features = dict(plan.get("features") or {})
        features.update(subscription.get("entitlement_overrides") or {})
        return {
            "source": source,
            "subscription_id": str(subscription.get("_id") or subscription.get("id") or ""),
            "plan_code": plan_code,
            "plan_name": plan.get("name") or plan_code,
            "features": features,
            "dms_enabled": bool(features.get("feature.dms.enabled", False)),
            "drafting_enabled": bool(features.get("feature.drafting.enabled", False)),
        }

    async def get_plan_settings(self) -> Dict[str, Any]:
        db = await self._get_db()
        plans = await self.list_plans()
        plan_by_code = {str(plan.get("code")): plan for plan in plans}
        organizations = [
            self._normalize(row)
            for row in await db.organizations.find({}).sort("name", 1).to_list(length=None)
        ]
        projects = [
            self._normalize(row)
            for row in await db.projects.find({}).sort("name", 1).to_list(length=None)
        ]
        subscriptions = [
            self._normalize(row)
            for row in await db.subscriptions.find({}).sort("updated_at", -1).to_list(length=None)
        ]

        active_subscriptions = [
            row
            for row in subscriptions
            if str(row.get("status") or "").lower() in {"trial", "pilot", "active"}
        ]

        effective: Dict[str, Any] = {"organizations": {}, "projects": {}}
        for org in organizations:
            org_id = str(org.get("id") or org.get("_id") or "")
            org_sub = next(
                (
                    sub
                    for sub in active_subscriptions
                    if self._active_subscription_match(sub, org_id, None)
                ),
                None,
            )
            effective["organizations"][org_id] = self._service_state(
                org_sub, plan_by_code, "organization" if org_sub else "none"
            )

        for project in projects:
            project_id = str(project.get("id") or project.get("_id") or "")
            org_id = self._org_id_from_project(project)
            project_sub = next(
                (
                    sub
                    for sub in active_subscriptions
                    if self._active_subscription_match(sub, org_id, project_id)
                ),
                None,
            )
            if project_sub:
                effective["projects"][project_id] = self._service_state(
                    project_sub, plan_by_code, "project"
                )
            else:
                inherited = dict(effective["organizations"].get(org_id) or self._service_state(None, plan_by_code, "none"))
                inherited["source"] = "inherited" if inherited.get("plan_code") else "none"
                inherited["inherited_from_organization_id"] = org_id if inherited.get("plan_code") else None
                effective["projects"][project_id] = inherited

        return {
            "organizations": organizations,
            "projects": projects,
            "plans": plans,
            "subscriptions": subscriptions,
            "effective": effective,
        }

    async def update_plan_settings_scope(
        self,
        payload: PlanSettingsScopeUpdate,
        current_user: Any,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        org_id = str(payload.organization_id)
        project_id = str(payload.project_id) if payload.project_id else None

        if payload.mode == PlanSettingsMode.INHERIT:
            if not project_id:
                raise ValueError("inherit mode is valid only for project scope")
            await db.subscriptions.update_many(
                self._subscription_scope_query(org_id, project_id),
                {
                    "$set": {
                        "status": "cancelled",
                        "billing_status": "inactive",
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(current_user, "id", None),
                    }
                },
            )
            await self.audit_service.emit(
                action="subscription.project_override_removed",
                actor_id=getattr(current_user, "id", None),
                resource_type="subscription",
                organization_id=org_id,
                project_id=project_id,
            )
            return await self.get_plan_settings()

        plan_code = payload.plan_code
        if payload.mode == PlanSettingsMode.NO_SERVICE:
            plan_code = "no_service_override"
        if not plan_code:
            raise ValueError("plan_code is required for plan mode")

        now = datetime.utcnow()
        query = self._subscription_scope_query(org_id, project_id)
        await db.subscriptions.update_many(
            query,
            {
                "$set": {
                    "status": "cancelled",
                    "billing_status": "inactive",
                    "updated_at": now,
                    "updated_by": getattr(current_user, "id", None),
                }
            },
        )
        doc = {
            "organization_id": org_id,
            "project_id": project_id,
            "package_id": None,
            "plan_code": plan_code,
            "status": payload.status.value if hasattr(payload.status, "value") else str(payload.status),
            "billing_status": "active",
            "entitlement_overrides": {},
            "trial": False,
            "pilot": False,
            "created_at": now,
            "updated_at": now,
            "created_by": getattr(current_user, "id", None),
            "updated_by": getattr(current_user, "id", None),
        }
        result = await db.subscriptions.insert_one(doc)
        await self.audit_service.emit(
            action="subscription.scope_plan_set",
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=str(result.inserted_id),
            organization_id=org_id,
            project_id=project_id,
            after=doc,
        )
        return await self.get_plan_settings()
