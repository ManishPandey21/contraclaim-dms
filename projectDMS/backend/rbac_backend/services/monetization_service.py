from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database
from .payment_gateway import get_payment_gateway
from ..models.rbac_monetization import (
    AddOnCreate,
    AddOnUpdate,
    BillingRecordCreate,
    PlanSettingsMode,
    PlanSettingsScopeUpdate,
    PlanCreate,
    PlanUpdate,
    SubscriptionCreate,
    SubscriptionUpdate,
    SubscriptionStatus,
    SubscriptionChangeType,
    SubscriptionHistoryCreate,
    UsageEventCreate,
    UpgradeDowngradeRequest,
    ChangeBillingPeriodRequest,
    AddOnActionRequest,
    StartTrialRequest,
    ConvertTrialRequest,
    CancelSubscriptionRequest,
)
from ..services.audit_event_service import AuditEventService

logger = logging.getLogger(__name__)


class MonetizationService:
    DEFAULT_PLANS = [
        {
            "code": "dms_starter",
            "name": "DMS Starter",
            "description": "Essential document management for small teams",
            "family": "dms_saas",
            "tier": 1,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 2500000,
            "pricing_tiers": {"monthly": 2500000, "quarterly": 7000000, "annual": 25000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True},
            "default_limits": {"limit.users": 10, "limit.storage_gb": 50},
            "trial_config": {"enabled": True, "days": 14},
            "available_add_ons": [],
            "is_active": True,
            "display_order": 1,
            "highlight": False,
            "max_users": 10,
            "max_storage_gb": 50,
        },
        {
            "code": "dms_professional",
            "name": "DMS Professional",
            "description": "Advanced document management with OCR and smart search",
            "family": "dms_saas",
            "tier": 2,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 7500000,
            "pricing_tiers": {"monthly": 7500000, "quarterly": 21000000, "annual": 75000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "feature.dms.ocr": True, "feature.dms.advanced_search": True},
            "default_limits": {"limit.users": 30, "limit.storage_gb": 200},
            "trial_config": {"enabled": True, "days": 14},
            "available_add_ons": ["addon_api_webhooks", "addon_priority_support"],
            "is_active": True,
            "display_order": 2,
            "highlight": True,
            "max_users": 30,
            "max_storage_gb": 200,
        },
        {
            "code": "dms_enterprise",
            "name": "DMS Enterprise",
            "description": "Unlimited document management with full API access and dedicated support",
            "family": "dms_saas",
            "tier": 3,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 20000000,
            "pricing_tiers": {"monthly": 20000000, "quarterly": 56000000, "annual": 200000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "feature.dms.ocr": True, "feature.dms.advanced_search": True, "feature.dms.api_webhooks": True},
            "default_limits": {},
            "trial_config": {"enabled": True, "days": 30},
            "available_add_ons": ["addon_priority_support"],
            "is_active": True,
            "display_order": 3,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "drafting_support_basic",
            "name": "Drafting Support Basic",
            "description": "Basic AI-assisted letter drafting with DMS access",
            "family": "drafting_bundle",
            "tier": 1,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 10000000,
            "pricing_tiers": {"monthly": 10000000, "quarterly": 28000000, "annual": 100000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {"limit.drafted_letters_month": 15},
            "trial_config": {"enabled": True, "days": 14},
            "available_add_ons": ["addon_extra_drafts"],
            "is_active": True,
            "display_order": 4,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "contract_correspondence_desk",
            "name": "Contract Correspondence Desk",
            "description": "Full contract correspondence management with AI drafting",
            "family": "drafting_bundle",
            "tier": 2,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 25000000,
            "pricing_tiers": {"monthly": 25000000, "quarterly": 70000000, "annual": 250000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {"limit.drafted_letters_month": 40},
            "trial_config": {"enabled": True, "days": 14},
            "available_add_ons": ["addon_extra_drafts", "addon_priority_support"],
            "is_active": True,
            "display_order": 5,
            "highlight": True,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "claims_commercial_desk",
            "name": "Claims & Commercial Desk",
            "description": "Enterprise-grade claims and commercial correspondence desk",
            "family": "drafting_bundle",
            "tier": 3,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 60000000,
            "pricing_tiers": {"monthly": 60000000, "quarterly": 168000000, "annual": 600000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {},
            "trial_config": {"enabled": True, "days": 30},
            "available_add_ons": ["addon_priority_support"],
            "is_active": True,
            "display_order": 6,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "dedicated_expert_desk",
            "name": "Dedicated Expert Desk",
            "description": "White-glove service with dedicated ContraClaim experts",
            "family": "drafting_bundle",
            "tier": 4,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 0,
            "pricing_tiers": {},
            "discount_percentages": {},
            "features": {"feature.dms.enabled": True, "feature.drafting.enabled": True},
            "default_limits": {},
            "trial_config": {"enabled": False},
            "available_add_ons": [],
            "is_active": True,
            "display_order": 7,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "archive_read_only",
            "name": "Archive / Read Only",
            "description": "Read-only archive access for completed projects",
            "family": "archive",
            "tier": 0,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 1000000,
            "pricing_tiers": {"monthly": 1000000, "quarterly": 2800000, "annual": 10000000},
            "discount_percentages": {"quarterly": 6.7, "annual": 16.7},
            "features": {"feature.dms.enabled": True, "mode.archive_read_only": True},
            "default_limits": {},
            "trial_config": {"enabled": False},
            "available_add_ons": [],
            "is_active": True,
            "display_order": 8,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
        {
            "code": "no_service_override",
            "name": "No Service",
            "description": "All services disabled",
            "family": "no_service",
            "tier": 0,
            "billing_cadence": "monthly",
            "currency": "INR",
            "base_price_minor": 0,
            "pricing_tiers": {},
            "discount_percentages": {},
            "features": {"feature.dms.enabled": False, "feature.drafting.enabled": False},
            "default_limits": {},
            "trial_config": {"enabled": False},
            "available_add_ons": [],
            "is_active": True,
            "display_order": 99,
            "highlight": False,
            "max_users": None,
            "max_storage_gb": None,
        },
    ]

    DEFAULT_ADDONS = [
        {
            "code": "addon_api_webhooks",
            "name": "API & Webhooks",
            "description": "REST API access and webhook integrations",
            "add_on_type": "feature",
            "price_minor": 2000000,
            "billing_cadence": "monthly",
            "currency": "INR",
            "features": {"feature.dms.api_webhooks": True},
            "limits": {},
            "compatible_plans": ["dms_professional", "dms_enterprise"],
            "is_active": True,
        },
        {
            "code": "addon_priority_support",
            "name": "Priority Support",
            "description": "Dedicated support channel with 4-hour SLA",
            "add_on_type": "support",
            "price_minor": 5000000,
            "billing_cadence": "monthly",
            "currency": "INR",
            "features": {"feature.priority_support": True},
            "limits": {},
            "compatible_plans": ["dms_professional", "dms_enterprise", "contract_correspondence_desk", "claims_commercial_desk"],
            "is_active": True,
        },
        {
            "code": "addon_extra_drafts",
            "name": "Extra Drafts Pack",
            "description": "Additional 25 AI drafts per month",
            "add_on_type": "capacity",
            "price_minor": 3000000,
            "billing_cadence": "monthly",
            "currency": "INR",
            "features": {},
            "limits": {"limit.drafted_letters_month_addon": 25},
            "compatible_plans": ["drafting_support_basic", "contract_correspondence_desk"],
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

    # ------------------------------------------------------------------
    # Plan CRUD
    # ------------------------------------------------------------------

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

    async def seed_default_addons(self) -> None:
        """Seed default add-ons into the database."""
        db = await self._get_db()
        now = datetime.utcnow()
        for addon in self.DEFAULT_ADDONS:
            await db.addons.update_one(
                {"code": addon["code"]},
                {
                    "$setOnInsert": {
                        **addon,
                        "created_at": now,
                        "updated_at": now,
                        "created_by": "system",
                    }
                },
                upsert=True,
            )

    async def list_plans(self) -> List[Dict[str, Any]]:
        db = await self._get_db()
        if await db.plans.count_documents({}, limit=1) == 0:
            await self.seed_default_plans()
        return [self._normalize(row) for row in await db.plans.find({}).sort("display_order", 1).to_list(length=None)]

    async def get_plan_by_code(self, plan_code: str) -> Optional[Dict[str, Any]]:
        """Return a single plan by its code."""
        db = await self._get_db()
        plan = await db.plans.find_one({"code": plan_code})
        if not plan:
            # Try seeding first
            await self.seed_default_plans()
            plan = await db.plans.find_one({"code": plan_code})
        return self._normalize(plan) if plan else None

    async def get_plan_catalog(self) -> Dict[str, Any]:
        """Return public-facing plan catalog with pricing and add-on info."""
        plans = await self.list_plans()
        addons = await self.list_addons()
        active_plans = [p for p in plans if p.get("is_active") and p.get("code") != "no_service_override"]
        active_addons = [a for a in addons if a.get("is_active")]
        return {
            "plans": active_plans,
            "add_ons": active_addons,
            "billing_periods": ["monthly", "quarterly", "annual"],
            "currency": "INR",
        }

    async def validate_plan_code(self, plan_code: str) -> bool:
        """Return True if the plan_code exists and is active."""
        db = await self._get_db()
        plan = await db.plans.find_one({"code": plan_code, "is_active": True})
        if plan:
            return True
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

    # ------------------------------------------------------------------
    # Add-On CRUD
    # ------------------------------------------------------------------

    async def list_addons(self) -> List[Dict[str, Any]]:
        db = await self._get_db()
        if await db.addons.count_documents({}, limit=1) == 0:
            await self.seed_default_addons()
        return [self._normalize(row) for row in await db.addons.find({}).sort("code", 1).to_list(length=None)]

    async def get_addon_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        addon = await db.addons.find_one({"code": code})
        if not addon:
            await self.seed_default_addons()
            addon = await db.addons.find_one({"code": code})
        return self._normalize(addon) if addon else None

    async def upsert_addon(self, payload: AddOnCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        doc = payload.model_dump(mode="json")
        doc.update({"updated_at": now, "updated_by": getattr(current_user, "id", None)})
        await db.addons.update_one(
            {"code": payload.code},
            {"$set": doc, "$setOnInsert": {"created_at": now, "created_by": getattr(current_user, "id", None)}},
            upsert=True,
        )
        saved = await db.addons.find_one({"code": payload.code})
        return self._normalize(saved)

    async def update_addon(self, addon_id: str, payload: AddOnUpdate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        lookup = self._lookup_id(addon_id)
        update_doc = payload.model_dump(mode="json", exclude_unset=True)
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        await db.addons.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.addons.find_one({"_id": lookup})
        return self._normalize(saved)

    # ------------------------------------------------------------------
    # Subscription CRUD
    # ------------------------------------------------------------------

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

    async def get_subscription_by_id(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        lookup = self._lookup_id(subscription_id)
        sub = await db.subscriptions.find_one({"_id": lookup})
        return self._normalize(sub) if sub else None

    async def create_subscription(self, payload: SubscriptionCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        doc = payload.model_dump(mode="json")

        # Compute current billing period boundaries
        if not doc.get("current_period_start"):
            doc["current_period_start"] = now
        if not doc.get("current_period_end"):
            doc["current_period_end"] = self._compute_period_end(now, doc.get("billing_period", "monthly"))

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

        # Record history
        await self._record_history(
            subscription_id=str(result.inserted_id),
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            change_type=SubscriptionChangeType.CREATED,
            to_plan_code=payload.plan_code,
            to_status=payload.status.value if hasattr(payload.status, "value") else str(payload.status),
            to_billing_period=doc.get("billing_period", "monthly"),
            changed_by=getattr(current_user, "id", None),
        )

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

    async def start_checkout(
        self,
        *,
        organization_id: str,
        project_id: Optional[str],
        plan_code: str,
        billing_period: str,
        current_user: Any,
        customer_name: Optional[str] = None,
        customer_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Provision a customer + subscription on the payment gateway and create a
        local subscription in ``pending`` state. The webhook flips it to ``active``
        once payment is captured, which in turn enables entitlements.
        """
        db = await self._get_db()
        if not await self.validate_plan_code(plan_code):
            raise ValueError(f"Plan '{plan_code}' not found")

        gateway = get_payment_gateway()
        customer = await gateway.create_customer(
            organization_id=str(organization_id),
            name=customer_name or str(organization_id),
            email=customer_email,
        )
        gateway_sub = await gateway.create_subscription(
            gateway_customer_id=customer.gateway_customer_id,
            plan_code=plan_code,
            billing_period=billing_period,
            metadata={"organization_id": str(organization_id), "project_id": str(project_id or "")},
        )

        now = datetime.utcnow()
        doc = {
            "organization_id": str(organization_id),
            "project_id": str(project_id) if project_id else None,
            "plan_code": plan_code,
            "billing_period": billing_period,
            "status": SubscriptionStatus.PENDING.value,
            "billing_status": "pending",
            "auto_renew": True,
            "payment_provider": str(settings.PAYMENT_PROVIDER),
            "payment_gateway_customer_id": customer.gateway_customer_id,
            "payment_gateway_subscription_id": gateway_sub.gateway_subscription_id,
            "created_at": now,
            "updated_at": now,
            "created_by": getattr(current_user, "id", None),
            "updated_by": getattr(current_user, "id", None),
        }
        result = await db.subscriptions.insert_one(doc)

        await self.audit_service.emit(
            action="subscription.checkout_started",
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=str(result.inserted_id),
            organization_id=str(organization_id),
            project_id=str(project_id) if project_id else None,
            metadata={"plan_code": plan_code, "provider": str(settings.PAYMENT_PROVIDER)},
        )
        return {
            "subscription_id": str(result.inserted_id),
            "provider": str(settings.PAYMENT_PROVIDER),
            "gateway_subscription_id": gateway_sub.gateway_subscription_id,
            "checkout_url": (gateway_sub.metadata or {}).get("short_url"),
            "status": SubscriptionStatus.PENDING.value,
        }

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

    # ------------------------------------------------------------------
    # Subscription Lifecycle
    # ------------------------------------------------------------------

    async def upgrade_subscription(self, subscription_id: str, request: UpgradeDowngradeRequest, current_user: Any) -> Dict[str, Any]:
        """Upgrade subscription to a higher-tier plan."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        old_plan = await self.get_plan_by_code(sub["plan_code"])
        new_plan = await self.get_plan_by_code(request.new_plan_code)
        if not new_plan:
            raise ValueError(f"Plan '{request.new_plan_code}' not found")
        if not old_plan or new_plan.get("tier", 0) <= old_plan.get("tier", 0):
            raise ValueError("Target plan must be a higher tier for upgrade")

        proration = self._calculate_proration(sub, old_plan, new_plan, request.billing_period)
        billing_period = request.billing_period or sub.get("billing_period", "monthly")
        now = datetime.utcnow()

        update = SubscriptionUpdate(
            plan_code=request.new_plan_code,
            billing_period=billing_period,
            status="active",
            billing_status="active",
            current_period_start=now if request.effective_immediately else None,
            current_period_end=self._compute_period_end(now, billing_period) if request.effective_immediately else None,
        )
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.UPGRADE,
            from_plan_code=sub["plan_code"],
            to_plan_code=request.new_plan_code,
            from_billing_period=sub.get("billing_period"),
            to_billing_period=billing_period,
            proration_amount_minor=proration,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def downgrade_subscription(self, subscription_id: str, request: UpgradeDowngradeRequest, current_user: Any) -> Dict[str, Any]:
        """Downgrade subscription to a lower-tier plan (effective at period end)."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        old_plan = await self.get_plan_by_code(sub["plan_code"])
        new_plan = await self.get_plan_by_code(request.new_plan_code)
        if not new_plan:
            raise ValueError(f"Plan '{request.new_plan_code}' not found")
        if old_plan and new_plan.get("tier", 0) >= old_plan.get("tier", 0):
            raise ValueError("Target plan must be a lower tier for downgrade")

        billing_period = request.billing_period or sub.get("billing_period", "monthly")

        # Downgrades take effect at end of current period
        update = SubscriptionUpdate(
            plan_code=request.new_plan_code,
            billing_period=billing_period,
        )
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.DOWNGRADE,
            from_plan_code=sub["plan_code"],
            to_plan_code=request.new_plan_code,
            from_billing_period=sub.get("billing_period"),
            to_billing_period=billing_period,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def change_billing_period(self, subscription_id: str, request: ChangeBillingPeriodRequest, current_user: Any) -> Dict[str, Any]:
        """Switch billing cadence (takes effect at next renewal)."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        update = SubscriptionUpdate(billing_period=request.new_billing_period)
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.PERIOD_CHANGE,
            from_billing_period=sub.get("billing_period"),
            to_billing_period=request.new_billing_period,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def cancel_subscription(self, subscription_id: str, request: CancelSubscriptionRequest, current_user: Any) -> Dict[str, Any]:
        """Cancel a subscription (immediately or at period end)."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        now = datetime.utcnow()
        update = SubscriptionUpdate(
            status="cancelled",
            billing_status="inactive",
            cancelled_at=now,
            cancellation_reason=request.reason,
            auto_renew=False,
        )
        if request.immediate:
            update.ends_at = now

        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.CANCELLATION,
            from_status=sub.get("status"),
            to_status="cancelled",
            changed_by=getattr(current_user, "id", None),
            metadata={"reason": request.reason, "immediate": request.immediate},
        )
        return result

    async def reactivate_subscription(self, subscription_id: str, current_user: Any) -> Dict[str, Any]:
        """Reactivate a cancelled subscription (if still within billing period)."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")
        if sub.get("status") != "cancelled":
            raise ValueError("Only cancelled subscriptions can be reactivated")

        update = SubscriptionUpdate(
            status="active",
            billing_status="active",
            cancelled_at=None,
            cancellation_reason=None,
            auto_renew=True,
        )
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.REACTIVATION,
            from_status="cancelled",
            to_status="active",
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def start_trial(self, request: StartTrialRequest, current_user: Any) -> Dict[str, Any]:
        """Create a trial subscription for an org/project."""
        plan = await self.get_plan_by_code(request.plan_code)
        if not plan:
            raise ValueError(f"Plan '{request.plan_code}' not found")

        trial_config = plan.get("trial_config") or {}
        if not trial_config.get("enabled"):
            raise ValueError(f"Plan '{request.plan_code}' does not support trials")

        trial_days = request.trial_days or trial_config.get("days", 14)
        now = datetime.utcnow()

        payload = SubscriptionCreate(
            organization_id=request.organization_id,
            project_id=request.project_id,
            plan_code=request.plan_code,
            billing_period="monthly",
            status="trial",
            billing_status="trial",
            starts_at=now,
            ends_at=now + timedelta(days=trial_days),
            trial=True,
            trial_ends_at=now + timedelta(days=trial_days),
            auto_renew=False,
        )
        return await self.create_subscription(payload, current_user)

    async def convert_trial(self, subscription_id: str, request: ConvertTrialRequest, current_user: Any) -> Dict[str, Any]:
        """Convert a trial subscription into a paid subscription."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")
        if sub.get("status") != "trial":
            raise ValueError("Only trial subscriptions can be converted")

        now = datetime.utcnow()
        update = SubscriptionUpdate(
            status="active",
            billing_status="active",
            billing_period=request.billing_period,
            trial=False,
            trial_ends_at=None,
            auto_renew=True,
            starts_at=now,
            ends_at=None,
            current_period_start=now,
            current_period_end=self._compute_period_end(now, request.billing_period),
            active_add_ons=request.add_on_codes if request.add_on_codes else None,
        )
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.TRIAL_CONVERT,
            from_status="trial",
            to_status="active",
            to_billing_period=request.billing_period,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def add_addon(self, subscription_id: str, request: AddOnActionRequest, current_user: Any) -> Dict[str, Any]:
        """Attach an add-on to a subscription."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        addon = await self.get_addon_by_code(request.add_on_code)
        if not addon:
            raise ValueError(f"Add-on '{request.add_on_code}' not found")

        compatible = addon.get("compatible_plans") or []
        if compatible and sub["plan_code"] not in compatible:
            raise ValueError(f"Add-on '{request.add_on_code}' is not compatible with plan '{sub['plan_code']}'")

        current_addons = list(sub.get("active_add_ons") or [])
        if request.add_on_code in current_addons:
            raise ValueError(f"Add-on '{request.add_on_code}' is already active")

        current_addons.append(request.add_on_code)
        update = SubscriptionUpdate(active_add_ons=current_addons)
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.ADDON_ADD,
            add_on_code=request.add_on_code,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    async def remove_addon(self, subscription_id: str, request: AddOnActionRequest, current_user: Any) -> Dict[str, Any]:
        """Detach an add-on from a subscription."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        current_addons = list(sub.get("active_add_ons") or [])
        if request.add_on_code not in current_addons:
            raise ValueError(f"Add-on '{request.add_on_code}' is not active on this subscription")

        current_addons.remove(request.add_on_code)
        update = SubscriptionUpdate(active_add_ons=current_addons)
        result = await self.update_subscription(subscription_id, update, current_user)

        await self._record_history(
            subscription_id=subscription_id,
            organization_id=sub["organization_id"],
            project_id=sub.get("project_id"),
            change_type=SubscriptionChangeType.ADDON_REMOVE,
            add_on_code=request.add_on_code,
            changed_by=getattr(current_user, "id", None),
        )
        return result

    # ------------------------------------------------------------------
    # Subscription History
    # ------------------------------------------------------------------

    async def _record_history(
        self,
        *,
        subscription_id: str,
        organization_id: str,
        project_id: Optional[str] = None,
        change_type: SubscriptionChangeType,
        from_plan_code: Optional[str] = None,
        to_plan_code: Optional[str] = None,
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        from_billing_period: Optional[str] = None,
        to_billing_period: Optional[str] = None,
        proration_amount_minor: Optional[int] = None,
        add_on_code: Optional[str] = None,
        changed_by: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert a subscription history record."""
        try:
            db = await self._get_db()
            doc = {
                "subscription_id": subscription_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "change_type": change_type.value if hasattr(change_type, "value") else str(change_type),
                "from_plan_code": from_plan_code,
                "to_plan_code": to_plan_code,
                "from_status": from_status,
                "to_status": to_status,
                "from_billing_period": from_billing_period,
                "to_billing_period": to_billing_period,
                "proration_amount_minor": proration_amount_minor,
                "add_on_code": add_on_code,
                "metadata": metadata or {},
                "changed_by": changed_by,
                "changed_at": datetime.utcnow(),
            }
            await db.subscription_history.insert_one(doc)
        except Exception as exc:
            logger.error("Failed to record subscription history: %s", exc)

    async def get_subscription_history(self, subscription_id: str) -> List[Dict[str, Any]]:
        """Return the full history of a subscription."""
        db = await self._get_db()
        rows = await db.subscription_history.find(
            {"subscription_id": subscription_id}
        ).sort("changed_at", -1).to_list(length=200)
        return [self._normalize(row) for row in rows]

    async def get_organization_subscription_history(self, organization_id: str) -> List[Dict[str, Any]]:
        """Return all subscription history for an organization."""
        db = await self._get_db()
        rows = await db.subscription_history.find(
            {"organization_id": str(organization_id)}
        ).sort("changed_at", -1).to_list(length=500)
        return [self._normalize(row) for row in rows]

    async def get_organization_billing_records(self, organization_id: str) -> List[Dict[str, Any]]:
        """Return the financial billing records (payments / failures / mismatches)
        for an organization, newest first. These are the webhook-written rows that
        back the billing-history UI and the failed-payment / mismatch review queue."""
        db = await self._get_db()
        rows = await db.billing_records.find(
            {"organization_id": str(organization_id)}
        ).sort("created_at", -1).to_list(length=500)
        return [self._normalize(row) for row in rows]

    async def build_billing_receipt(self, record_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        """Assemble the receipt for a paid billing record (scoped to the org).

        Returns ``None`` if the record doesn't exist in the org; raises
        ``ValueError`` if it isn't a successful payment (receipts only for paid).
        """
        from .billing_receipt import build_receipt

        db = await self._get_db()
        record = await db.billing_records.find_one(
            {"_id": self._lookup_id(record_id), "organization_id": str(organization_id)}
        )
        if not record:
            return None
        if record.get("record_status") != "paid":
            raise ValueError("A receipt is only available for a successful payment")

        subscription = None
        if record.get("subscription_id"):
            try:
                subscription = await db.subscriptions.find_one(
                    {"_id": self._lookup_id(record["subscription_id"])}
                )
            except Exception:  # pragma: no cover - defensive
                subscription = None
        plan = None
        if subscription and subscription.get("plan_code"):
            plan = await self.get_plan_by_code(subscription["plan_code"])
        organization = None
        try:
            organization = await db.organizations.find_one({"_id": self._lookup_id(organization_id)})
        except Exception:  # pragma: no cover - defensive
            organization = None

        return build_receipt(record, subscription, plan, organization)

    # ------------------------------------------------------------------
    # Invoice / Proration helpers
    # ------------------------------------------------------------------

    def _calculate_proration(
        self,
        subscription: Dict[str, Any],
        old_plan: Optional[Dict[str, Any]],
        new_plan: Dict[str, Any],
        new_billing_period: Optional[str] = None,
    ) -> int:
        """Calculate prorated amount for a mid-cycle plan change (in minor units)."""
        if not old_plan:
            return 0

        billing_period = new_billing_period or subscription.get("billing_period", "monthly")
        old_price = (old_plan.get("pricing_tiers") or {}).get(billing_period, old_plan.get("base_price_minor", 0))
        new_price = (new_plan.get("pricing_tiers") or {}).get(billing_period, new_plan.get("base_price_minor", 0))

        period_start_str = subscription.get("current_period_start")
        period_end_str = subscription.get("current_period_end")
        if not period_start_str or not period_end_str:
            return max(0, new_price - old_price)

        now = datetime.utcnow()
        if isinstance(period_start_str, str):
            period_start = datetime.fromisoformat(period_start_str)
        else:
            period_start = period_start_str
        if isinstance(period_end_str, str):
            period_end = datetime.fromisoformat(period_end_str)
        else:
            period_end = period_end_str

        total_days = max((period_end - period_start).days, 1)
        remaining_days = max((period_end - now).days, 0)
        fraction = remaining_days / total_days

        credit = int(old_price * fraction)
        charge = int(new_price * fraction)
        return max(0, charge - credit)

    async def get_invoice_preview(self, subscription_id: str) -> Dict[str, Any]:
        """Generate an invoice preview for the current subscription."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")

        plan = await self.get_plan_by_code(sub["plan_code"])
        billing_period = sub.get("billing_period", "monthly")
        plan_price = 0
        if plan:
            plan_price = (plan.get("pricing_tiers") or {}).get(billing_period, plan.get("base_price_minor", 0))

        # Add-on costs
        addon_total = 0
        addon_line_items = []
        for addon_code in (sub.get("active_add_ons") or []):
            addon = await self.get_addon_by_code(addon_code)
            if addon:
                addon_price = addon.get("price_minor", 0)
                addon_total += addon_price
                addon_line_items.append({
                    "description": addon.get("name", addon_code),
                    "code": addon_code,
                    "amount_minor": addon_price,
                })

        return {
            "subscription_id": subscription_id,
            "plan_code": sub["plan_code"],
            "plan_name": plan.get("name") if plan else sub["plan_code"],
            "billing_period": billing_period,
            "line_items": [
                {"description": f"Plan: {plan.get('name', sub['plan_code'])}", "amount_minor": plan_price},
                *addon_line_items,
            ],
            "subtotal_minor": plan_price + addon_total,
            "currency": plan.get("currency", "INR") if plan else "INR",
            "period_start": sub.get("current_period_start"),
            "period_end": sub.get("current_period_end"),
        }

    # ------------------------------------------------------------------
    # Billing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_period_end(start: datetime, billing_period: str) -> datetime:
        """Compute end-of-period date from a start date and billing cadence."""
        if billing_period == "quarterly":
            return start + timedelta(days=90)
        elif billing_period == "semi_annual":
            return start + timedelta(days=182)
        elif billing_period == "annual":
            return start + timedelta(days=365)
        else:  # monthly
            return start + timedelta(days=30)

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

    async def get_billing_summary(self, organization_id: str) -> Dict[str, Any]:
        """Get billing summary for an organization."""
        db = await self._get_db()
        subs = await db.subscriptions.find(
            {"organization_id": str(organization_id)}
        ).sort("updated_at", -1).to_list(length=100)

        active_subs = [s for s in subs if str(s.get("status", "")).lower() in {"trial", "pilot", "active"}]
        now = datetime.utcnow()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        usage_pipeline = [
            {"$match": {"organization_id": str(organization_id), "created_at": {"$gte": period_start}}},
            {"$group": {"_id": "$event_type", "total": {"$sum": "$quantity"}}},
        ]
        usage_rows = await db.usage_events.aggregate(usage_pipeline).to_list(length=None)
        usage_by_type = {row["_id"]: row["total"] for row in usage_rows}

        return {
            "organization_id": organization_id,
            "total_subscriptions": len(subs),
            "active_subscriptions": len(active_subs),
            "subscriptions": [self._normalize(s) for s in active_subs],
            "current_period_usage": usage_by_type,
            "period_start": period_start.isoformat(),
        }

    # ------------------------------------------------------------------
    # Plan Settings (preserved from original)
    # ------------------------------------------------------------------

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
                "billing_period": None,
                "trial": False,
                "trial_ends_at": None,
                "active_add_ons": [],
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
            "billing_period": subscription.get("billing_period", "monthly"),
            "trial": bool(subscription.get("trial")),
            "trial_ends_at": subscription.get("trial_ends_at"),
            "active_add_ons": subscription.get("active_add_ons") or [],
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

        billing_period = payload.billing_period or "monthly"
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
            "billing_period": billing_period,
            "status": payload.status.value if hasattr(payload.status, "value") else str(payload.status),
            "billing_status": "active",
            "entitlement_overrides": {},
            "trial": False,
            "pilot": False,
            "auto_renew": True,
            "active_add_ons": [],
            "current_period_start": now,
            "current_period_end": self._compute_period_end(now, billing_period),
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
