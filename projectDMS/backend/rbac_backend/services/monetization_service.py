from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from ..core.config import settings
from ..core.database import get_database
from .payment_gateway import get_payment_gateway
from ..models.rbac_monetization import (
    AddOnCreate,
    AddOnUpdate,
    BillingPeriod,
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


#: A subscription's scope: preserved by every ordinary update (L3, owner decision 2026-11-20;
#: ``package_id`` added by F3, owner decision 2026-11-20 - entitlement resolution matches on
#: organisation, project and package, so a package change moves the entitlement).
SUBSCRIPTION_SCOPE_FIELDS: Tuple[str, str, str] = ("organization_id", "project_id", "package_id")


class SubscriptionScopeChangeRefused(ValueError):
    """An ordinary update tried to change a subscription's organisation, project or package."""


class SubscriptionScopeConflict(Exception):
    """The subscription's stored scope is not the one the caller authorised against."""


#: The subscription's payment identity (owner decision 2026-11-20). The billing webhook finds
#: the subscription a payment event applies to by ``payment_gateway_subscription_id``, so
#: whoever can write it can redirect another subscription's payment events (activation
#: included). Only checkout binds it, from the gateway's own response; ordinary create and
#: update never set, replace or clear it.
PROTECTED_GATEWAY_FIELDS: Tuple[str, str, str] = (
    "payment_gateway_subscription_id",
    "payment_gateway_customer_id",
    "payment_provider",
)


class GatewayIdentityChangeRefused(ValueError):
    """An ordinary create or update tried to set, replace or clear a gateway binding."""


class GatewayIdentityConflict(Exception):
    """The gateway subscription id is already bound to another subscription."""


class TrialConversionRefused(Exception):
    """A trial conversion that would grant paid service without payment, or convert a trial
    that does not govern its scope (R6-5)."""


class SubscriptionReactivationRefused(Exception):
    """A reactivation that would revive an ended, replaced or ineligible subscription (R6-6)."""


class GatewayOperationFailed(Exception):
    """The payment gateway did not complete an operation; nothing was changed locally."""


class SubscriptionNotGoverning(Exception):
    """A plan, billing-period or add-on change aimed at a subscription that does not govern
    its scope (entitlement re-review N1/N2): it would change the plan, and possibly the
    gateway price, without changing what the customer is entitled to."""


#: Subscription date fields stored as BSON dates, never as ISO strings (entitlement review F3:
#: ``model_dump(mode="json")`` stored strings, which date range queries never matched).
SUBSCRIPTION_DATE_FIELDS = (
    "starts_at", "ends_at", "current_period_start", "current_period_end", "trial_ends_at", "cancelled_at",
)


def as_stored_dates(doc: Dict[str, Any]) -> Dict[str, Any]:
    for field in SUBSCRIPTION_DATE_FIELDS:
        value = doc.get(field)
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue  # left as given: the resolver treats it as an invalid period (fails closed)
            doc[field] = parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return doc


def scope_text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def subscription_scope(document: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """(organisation, project, package) of a stored or normalised subscription, as text
    (``None`` and the empty string are the same "none", as entitlement resolution reads them)."""
    return tuple(scope_text(document.get(field)) for field in SUBSCRIPTION_SCOPE_FIELDS)  # type: ignore[return-value]


def _dotted(doc: Dict[str, Any], path: str) -> Any:
    """``doc[a][b]`` for ``"a.b"`` (as a MongoDB filter reads it); ``None`` when absent."""
    value: Any = doc
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


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

    def __init__(self, db: Any = None, gateway: Any = None) -> None:
        self.db = db
        self.audit_service = AuditEventService(db)
        self._gateway_override = gateway

    def _gateway(self) -> Any:
        return self._gateway_override or get_payment_gateway()

    async def _gateway_cancel(self, sub: Dict[str, Any], *, immediate: bool) -> None:
        """Propagate a cancel to the payment gateway so it stops billing.

        No-op when the subscription has no gateway id (trial / manually created /
        noop provider). A gateway failure propagates and aborts the local change.
        """
        gid = sub.get("payment_gateway_subscription_id")
        if not gid:
            return
        await self._gateway().cancel_subscription(gateway_subscription_id=gid, immediate=immediate)

    async def _gateway_reactivate(self, sub: Dict[str, Any]) -> None:
        gid = sub.get("payment_gateway_subscription_id")
        if not gid:
            return
        await self._gateway().reactivate_subscription(gateway_subscription_id=gid)

    async def _gateway_change(self, sub: Dict[str, Any], *, new_plan_code: Optional[str] = None,
                              new_billing_period: Optional[str] = None) -> None:
        gid = sub.get("payment_gateway_subscription_id")
        if not gid:
            return
        await self._gateway().update_subscription(
            gateway_subscription_id=gid,
            new_plan_code=new_plan_code,
            new_billing_period=new_billing_period,
        )

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
        plan = await db.plans.find_one({"code": plan_code})
        if plan is not None:
            return bool(plan.get("is_active"))
        return any(
            p["code"] == plan_code and bool(p.get("is_active"))
            for p in self.DEFAULT_PLANS
        )

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
        before = await db.plans.find_one({"_id": lookup})
        if before is None:
            raise LookupError("Plan not found")
        await db.plans.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.plans.find_one({"_id": lookup})
        await self.audit_service.emit(
            action="billing.plan.updated",
            actor_id=getattr(current_user, "id", None),
            resource_type="plan",
            resource_id=str(plan_id),
            before=before,
            after=saved,
        )
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
        await self.audit_service.emit(
            action="billing.addon.upserted",
            actor_id=getattr(current_user, "id", None),
            resource_type="addon",
            resource_id=payload.code,
            after=saved,
        )
        return self._normalize(saved)

    async def update_addon(self, addon_id: str, payload: AddOnUpdate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        lookup = self._lookup_id(addon_id)
        update_doc = payload.model_dump(mode="json", exclude_unset=True)
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        before = await db.addons.find_one({"_id": lookup})
        if before is None:
            raise LookupError("Add-on not found")
        await db.addons.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.addons.find_one({"_id": lookup})
        await self.audit_service.emit(
            action="billing.addon.updated",
            actor_id=getattr(current_user, "id", None),
            resource_type="addon",
            resource_id=str(addon_id),
            before=before,
            after=saved,
        )
        return self._normalize(saved)

    # ------------------------------------------------------------------
    # Subscription CRUD
    # ------------------------------------------------------------------

    async def list_subscriptions(
        self,
        organization_id: Optional[str] = None,
        organization_ids: Optional[List[str]] = None,
        project_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = str(organization_id)
        elif organization_ids is not None:
            query["organization_id"] = {"$in": [str(item) for item in organization_ids]}
        if project_ids is not None:
            query["project_id"] = {"$in": [str(item) for item in project_ids]}
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
        if any(scope_text(doc.get(field)) for field in PROTECTED_GATEWAY_FIELDS):
            # Gateway identity is bound only by checkout, from the gateway's response.
            raise GatewayIdentityChangeRefused("A payment gateway binding cannot be set when creating a subscription")
        for field in PROTECTED_GATEWAY_FIELDS:
            doc.pop(field, None)
        doc = as_stored_dates(doc)
        from .subscription_resolution import GOVERNING_STATUSES

        becomes_governing = str(doc.get("status") or "").strip().lower() in GOVERNING_STATUSES
        if becomes_governing:
            doc["governing_since"] = now

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
        converts_trial_id: Optional[str] = None,
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
            metadata={"organization_id": str(organization_id), "project_id": str(project_id or ""),
                      **({"converts_trial_id": str(converts_trial_id)} if converts_trial_id else {})},
        )

        now = datetime.utcnow()
        gateway_id = scope_text(gateway_sub.gateway_subscription_id)
        if gateway_id and await db.subscriptions.find_one({"payment_gateway_subscription_id": gateway_id}, {"_id": 1}):
            # One gateway subscription, one local subscription: never a second binding.
            raise GatewayIdentityConflict("The gateway subscription is already bound to a subscription")
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
            # Provenance of the binding: the webhook trusts only checkout-bound identities.
            "payment_gateway_bound_by": "checkout",
            "payment_gateway_bound_at": now,
            **({"converts_trial_id": str(converts_trial_id)} if converts_trial_id else {}),
            "created_at": now,
            "updated_at": now,
            "created_by": getattr(current_user, "id", None),
            "updated_by": getattr(current_user, "id", None),
        }
        try:
            result = await db.subscriptions.insert_one(doc)
        except DuplicateKeyError as exc:  # the unique index (migration 20261124_0001), when present
            raise GatewayIdentityConflict("The gateway subscription is already bound to a subscription") from exc

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

    async def update_subscription(
        self,
        subscription_id: str,
        payload: SubscriptionUpdate,
        current_user: Any,
        *,
        expected_scope: Optional[Tuple[Optional[str], ...]] = None,
        expected_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update a subscription; its organisation, project and package never change here
        (L3; package: F3).

        ``expected_scope``: the (organisation, project, package) the caller authorised against.
        If the stored scope differs now, or changes between this read and the write
        (compare-and-set on all three fields), the update is refused with
        ``SubscriptionScopeConflict``. A scope
        field in ``payload`` equal to the stored value is a no-op; any other value, explicit
        null included, is refused with ``SubscriptionScopeChangeRefused``. A transfer between
        scopes is a separate, not yet designed, operation.
        """
        db = await self._get_db()
        lookup = self._lookup_id(subscription_id)
        before = await db.subscriptions.find_one({"_id": lookup})
        if before is None:
            raise LookupError("Subscription not found")
        stored_scope = subscription_scope(before)
        if expected_scope is not None and len(expected_scope) != len(SUBSCRIPTION_SCOPE_FIELDS):
            raise TypeError("expected_scope must name the organisation, project and package")
        if expected_scope is not None and stored_scope != tuple(scope_text(value) for value in expected_scope):
            raise SubscriptionScopeConflict("The subscription's scope changed; reload and try again")
        if expected_state is not None and any(_dotted(before, f) != v for f, v in expected_state.items()):
            # A lifecycle change decided on a state that has since changed (R6-3).
            raise SubscriptionNotGoverning("The subscription changed while this change was being made; reload and try again")
        update_doc = payload.model_dump(mode="json", exclude_unset=True)
        for index, field in enumerate(SUBSCRIPTION_SCOPE_FIELDS):
            if field in update_doc:
                if scope_text(update_doc[field]) != stored_scope[index]:
                    raise SubscriptionScopeChangeRefused(
                        "The organisation, project and package of a subscription cannot be changed by an update"
                    )
                update_doc.pop(field)
        for field in PROTECTED_GATEWAY_FIELDS:
            if field in update_doc:
                if scope_text(update_doc[field]) != scope_text(before.get(field)):
                    raise GatewayIdentityChangeRefused(
                        "The payment gateway binding of a subscription cannot be set, replaced or cleared by an update"
                    )
                update_doc.pop(field)
        update_doc = as_stored_dates(update_doc)
        from .subscription_resolution import GOVERNING_STATUSES

        becomes_governing = (
            str(update_doc.get("status") or "").strip().lower() in GOVERNING_STATUSES
            and str(before.get("status") or "").strip().lower() not in GOVERNING_STATUSES
        )
        if becomes_governing:
            update_doc["governing_since"] = datetime.utcnow()
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        # Compare-and-set: the write applies only while the stored scope is the one read
        # (and authorised) above, so a concurrent change cannot be overwritten into a scope
        # the caller was never authorised for.
        result = await db.subscriptions.update_one(
            {
                "_id": lookup,
                **{field: before.get(field) for field in SUBSCRIPTION_SCOPE_FIELDS},
                "payment_gateway_subscription_id": before.get("payment_gateway_subscription_id"),
                **(expected_state or {}),
            },
            {"$set": update_doc},
        )
        if result.matched_count == 0 and expected_state is not None:
            raise SubscriptionNotGoverning("The subscription changed while this change was being made; reload and try again")
        if result.matched_count == 0:
            raise SubscriptionScopeConflict("The subscription's scope changed; reload and try again")
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

    #: Fields a lifecycle change is decided on; the write is compare-and-set on them (R6-3).
    LIFECYCLE_STATE_FIELDS = ("status", "plan_code", "billing_period", "active_add_ons", "governing_since")

    async def _assert_governs_its_scope(self, sub: Dict[str, Any], operation: str) -> Dict[str, Any]:
        """Refuse a lifecycle change on a row that is not the one enforcement selects.

        Several governing rows may coexist in a tier and the most recently activated governs
        (``services.subscription_resolution``). Changing the plan of a row that does not
        govern would bill (``_gateway_change``) for a plan the customer does not receive
        (re-review N1), and upgrading a cancelled row used to revive it (N2). The row must be
        a working subscription (eligible status: an upgrade must not lift a payment block or
        an archive/offboarding restriction, review R6-4) and must be the subscription
        enforcement itself selects for its scope (``EntitlementService.resolve_subscription``,
        the same query and rule, R6-2/R6-9). A row not yet started is judged at its start
        (R6-1). Refused before the gateway is touched. Returns the stored state the decision
        was made on, for the compare-and-set write (R6-3).
        """
        from .entitlement_service import EntitlementService
        from .subscription_resolution import ELIGIBLE_STATUSES, classify

        status = str(sub.get("status") or "").strip().lower()
        if status not in ELIGIBLE_STATUSES:
            raise SubscriptionNotGoverning(
                f"Cannot {operation} a subscription in status '{status or 'unknown'}'; "
                "only an active, pilot or trial subscription can be changed (reactivate or settle it first)"
            )
        db = await self._get_db()
        stored = await db.subscriptions.find_one({"_id": self._lookup_id(str(sub.get("id") or sub.get("_id") or ""))})
        if not stored:
            raise ValueError("Subscription not found")
        moment = datetime.utcnow()
        if classify(stored, moment) == "not_started":
            starts_at = stored.get("starts_at")
            if isinstance(starts_at, datetime):
                moment = starts_at.replace(tzinfo=None) if starts_at.tzinfo is None else starts_at.astimezone(timezone.utc).replace(tzinfo=None)
        resolution = await EntitlementService(db).resolve_subscription(
            organization_id=scope_text(stored.get("organization_id")),
            project_id=scope_text(stored.get("project_id")),
            package_id=scope_text(stored.get("package_id")),
            now=moment,
        )
        selected = resolution.subscription or {}
        selected_id = str(selected.get("_id") or selected.get("id") or "")
        if selected_id and selected_id == str(stored.get("_id")):
            return {field: stored.get(field) for field in self.LIFECYCLE_STATE_FIELDS}
        if resolution.reason == "ambiguous_subscriptions":
            raise SubscriptionNotGoverning(
                f"Cannot {operation}: the scope holds several governing subscriptions that cannot be ordered; resolve them first"
            )
        if selected_id:
            raise SubscriptionNotGoverning(
                f"Cannot {operation}: this subscription is outranked by subscription {selected_id}, which governs the same scope"
            )
        raise SubscriptionNotGoverning(
            f"Cannot {operation}: this subscription does not govern its scope ({classify(stored, moment)})"
        )

    async def upgrade_subscription(self, subscription_id: str, request: UpgradeDowngradeRequest, current_user: Any) -> Dict[str, Any]:
        """Upgrade subscription to a higher-tier plan."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")
        state = await self._assert_governs_its_scope(sub, "upgrade")

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
        # Propagate to the gateway first; if it fails, the local state is untouched.
        await self._gateway_change(sub, new_plan_code=request.new_plan_code, new_billing_period=billing_period)
        result = await self.update_subscription(subscription_id, update, current_user, expected_state=state)

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
        state = await self._assert_governs_its_scope(sub, "downgrade")

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
        await self._gateway_change(sub, new_plan_code=request.new_plan_code, new_billing_period=billing_period)
        result = await self.update_subscription(subscription_id, update, current_user, expected_state=state)

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
        state = await self._assert_governs_its_scope(sub, "change the billing period of")

        update = SubscriptionUpdate(billing_period=request.new_billing_period)
        await self._gateway_change(sub, new_billing_period=request.new_billing_period)
        result = await self.update_subscription(subscription_id, update, current_user, expected_state=state)

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

        # Cancel on the gateway so it stops billing; abort locally if that fails.
        await self._gateway_cancel(sub, immediate=bool(request.immediate))
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

    #: Gateway statuses that confirm a resumed subscription (R6-6).
    GATEWAY_ACTIVE_STATUSES = frozenset({"active", "authenticated"})

    async def reactivate_subscription(self, subscription_id: str, current_user: Any) -> Dict[str, Any]:
        """Resume a cancelled subscription inside the period it already paid for (R6-6).

        Rules (owner decision 2026-11-21), all checked before the gateway is asked:

        - only a ``cancelled`` subscription, and not a trial closed by its paid conversion;
        - its paid period is known and has not ended (``ends_at`` and ``current_period_end``
          readable, the earliest still in the future); otherwise the customer buys anew
          through checkout - an expired subscription is never revived;
        - nothing else governs its own scope and no replacement checkout is pending there
          (reviving it would bill two subscriptions for one entitlement);
        - one reactivation at a time (claimed by compare-and-set on the cancelled row);
        - a gateway-bound subscription resumes only when the gateway confirms an active
          subscription; a gateway failure or any other status changes nothing locally (502 /
          409, audited for review). An unbound subscription (billed outside the gateway)
          resumes with ``billing_status = "manual"``.

        Whether an *older* subscription resumes when the governing one is cancelled: one that
        was never cancelled still governs by latest activation (it never stopped billing - the
        overlap P-05 counts); a cancelled one resumes only through this operation and these
        rules. Archive and offboarding are not ``cancelled`` and are refused.
        """
        import uuid

        from .entitlement_service import EntitlementService
        from .subscription_resolution import _UNREADABLE, _instant

        db = await self._get_db()
        lookup = self._lookup_id(subscription_id)
        stored = await db.subscriptions.find_one({"_id": lookup})
        if not stored:
            raise ValueError("Subscription not found")
        sub_id = str(stored["_id"])
        status = str(stored.get("status") or "").strip().lower()
        if status != "cancelled":
            raise SubscriptionReactivationRefused(f"Only a cancelled subscription can be reactivated (status '{status or 'unknown'}')")
        if stored.get("cancellation_reason") == "converted_to_paid":
            raise SubscriptionReactivationRefused("This trial was converted to a paid subscription; it cannot be reactivated")
        if (stored.get("reactivation") or {}).get("state") == "starting":
            raise SubscriptionReactivationRefused("A reactivation of this subscription is already in progress")
        now = datetime.utcnow()
        bounds = [_instant(stored.get("ends_at")), _instant(stored.get("current_period_end"))]
        if any(bound is _UNREADABLE for bound in bounds):
            raise SubscriptionReactivationRefused("The paid period of this subscription cannot be read; start a new purchase")
        known = [bound for bound in bounds if bound is not None]
        if not known:
            raise SubscriptionReactivationRefused("No paid period is recorded for this subscription; start a new purchase")
        if min(known) <= now:
            raise SubscriptionReactivationRefused("The paid period of this subscription has ended; start a new purchase")

        org, project, package = (scope_text(stored.get(field)) for field in SUBSCRIPTION_SCOPE_FIELDS)
        resolution = await EntitlementService(db).resolve_subscription(
            organization_id=org, project_id=project, package_id=package
        )
        own_tier = "package" if package else ("project" if project else "organization")
        if resolution.tier == own_tier and resolution.reason == "ambiguous_subscriptions":
            raise SubscriptionReactivationRefused("Several subscriptions govern this scope; resolve them first")
        if resolution.tier == own_tier and resolution.subscription is not None:
            raise SubscriptionReactivationRefused(
                f"Subscription {resolution.subscription.get('_id')} governs this scope; reactivating this one would bill both"
            )
        same_scope = {"organization_id": stored.get("organization_id"), "project_id": stored.get("project_id"),
                      "package_id": stored.get("package_id"), "_id": {"$ne": stored["_id"]}}
        if await db.subscriptions.find_one({**same_scope, "status": "pending",
                                             "payment_gateway_subscription_id": {"$type": "string", "$gt": ""}}, {"_id": 1}):
            raise SubscriptionReactivationRefused("A replacement purchase for this scope is awaiting payment")

        attempt = uuid.uuid4().hex
        claimed = await db.subscriptions.find_one_and_update(
            {"_id": stored["_id"], "status": stored.get("status"), "reactivation.state": {"$ne": "starting"}},
            {"$set": {"reactivation": {"state": "starting", "attempt": attempt, "requested_at": now,
                                       "requested_by": getattr(current_user, "id", None)}}},
        )
        if claimed is None:
            raise SubscriptionReactivationRefused("This subscription changed or is being reactivated; reload")

        async def release(state: str, **details: Any) -> None:
            await db.subscriptions.update_one(
                {"_id": stored["_id"], "reactivation.attempt": attempt},
                {"$set": {"reactivation.state": state, "reactivation.finished_at": datetime.utcnow(),
                          **{f"reactivation.{key}": value for key, value in details.items()}}},
            )

        bound = bool(scope_text(stored.get("payment_gateway_subscription_id")))
        if bound:
            try:
                resumed = await self._gateway().reactivate_subscription(
                    gateway_subscription_id=stored["payment_gateway_subscription_id"]
                )
            except Exception as exc:
                await release("failed", error=type(exc).__name__)
                await self._review_event(stored, "subscription.reactivation_gateway_failed", current_user,
                                         reason=type(exc).__name__)
                raise GatewayOperationFailed("The payment gateway did not resume the subscription; nothing was changed") from exc
            gateway_status = str(getattr(resumed, "status", "") or "").strip().lower()
            if gateway_status not in self.GATEWAY_ACTIVE_STATUSES:
                await release("refused_by_gateway", gateway_status=gateway_status)
                await self._review_event(stored, "subscription.reactivation_gateway_unconfirmed", current_user,
                                         reason=gateway_status or "unknown")
                raise SubscriptionReactivationRefused(
                    f"The payment gateway reports the subscription as '{gateway_status or 'unknown'}'; it was not reactivated"
                )

        update = SubscriptionUpdate(
            status="active",
            billing_status="active" if bound else "manual",
            cancelled_at=None,
            cancellation_reason=None,
            auto_renew=True,
        )
        expected = {field: stored.get(field) for field in self.LIFECYCLE_STATE_FIELDS}
        expected["reactivation.attempt"] = attempt
        try:
            result = await self.update_subscription(sub_id, update, current_user, expected_state=expected)
        except SubscriptionNotGoverning as exc:
            await release("failed", error="local_state_changed")
            if bound:
                # The gateway resumed but the local write was refused: surfaced for a person.
                await self._review_event(stored, "subscription.reactivation_needs_reconciliation", current_user,
                                         reason="gateway_resumed_local_refused")
            raise SubscriptionReactivationRefused(str(exc)) from exc
        await release("completed")
        await self._record_history(
            subscription_id=sub_id,
            organization_id=stored["organization_id"],
            project_id=stored.get("project_id"),
            change_type=SubscriptionChangeType.REACTIVATION,
            from_status="cancelled",
            to_status="active",
            changed_by=getattr(current_user, "id", None),
            metadata={"gateway_confirmed": bound, "billing_status": "active" if bound else "manual"},
        )
        return result

    async def _review_event(self, sub: Dict[str, Any], action: str, current_user: Any, *, reason: str) -> None:
        """An audited event an administrator must review (billing review queue)."""
        await self.audit_service.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=str(sub.get("_id") or sub.get("id")),
            organization_id=str(sub.get("organization_id") or ""),
            project_id=scope_text(sub.get("project_id")),
            result="failure",
            reason=reason,
            metadata={"requires_admin_review": True,
                      "gateway_subscription_id": sub.get("payment_gateway_subscription_id")},
        )

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

    #: R6-5: what a trial conversion may produce without a gateway payment.
    NO_CHARGE_BILLING_STATUS = "no_charge_override"
    FREE_PLAN_BILLING_STATUS = "not_billed"
    NO_CHARGE_REASON_MIN_LENGTH = 10

    def _period_price(self, plan: Dict[str, Any], billing_period: str) -> int:
        return int((plan.get("pricing_tiers") or {}).get(billing_period, plan.get("base_price_minor", 0)) or 0)

    async def convert_trial(
        self,
        subscription_id: str,
        request: ConvertTrialRequest,
        current_user: Any,
        *,
        no_charge_authorised: bool = False,
    ) -> Dict[str, Any]:
        """Convert a trial (R6-5, owner decision 2026-11-21).

        A trial never becomes a paid, active subscription through a local update:

        - the trial must be a running trial (not expired, ended, not started or with an
          unreadable period) that enforcement itself selects for its scope (not outranked);
        - the plan must exist and be active, the period supported, and every add-on must exist,
          be active, be compatible with the plan and appear once;
        - **priced plan:** a checkout is started for the trial's scope (``converts_trial_id``)
          and the trial keeps governing until the billing webhook confirms payment; the paid
          subscription then governs (latest activation) and the trial is closed. One checkout
          per trial (claimed by compare-and-set; a repeat returns the same checkout; a gateway
          failure releases the claim for a retry). Priced add-ons are refused here: checkout
          does not carry them to the gateway, so they are added after conversion;
        - **free plan** (zero price for the period, no priced add-on): converted in place with
          ``billing_status = "not_billed"``; no payment is invented;
        - **no-charge override** of a priced plan: only with separate authority (Super Admin,
          decided by the route) and a written reason, audited as such, with
          ``billing_status = "no_charge_override"``.

        ``billing_status`` is never set to ``active`` here: only payment confirmation does that.
        """
        from .entitlement_service import EntitlementService
        from .subscription_resolution import classify

        db = await self._get_db()
        lookup = self._lookup_id(subscription_id)
        stored = await db.subscriptions.find_one({"_id": lookup})
        if not stored:
            raise ValueError("Subscription not found")
        trial_id = str(stored["_id"])
        status = str(stored.get("status") or "").strip().lower()
        if status != "trial":
            raise TrialConversionRefused(f"Only a running trial can be converted (status '{status or 'unknown'}')")
        conversion = stored.get("conversion") or {}
        if conversion.get("state") in {"starting", "pending_payment"}:
            return self._conversion_outcome(trial_id, conversion)
        now = datetime.utcnow()
        period_class = classify(stored, now)
        if period_class != "eligible":
            raise TrialConversionRefused(f"This trial cannot be converted ({period_class}); start a new purchase instead")
        resolution = await EntitlementService(db).resolve_subscription(
            organization_id=scope_text(stored.get("organization_id")),
            project_id=scope_text(stored.get("project_id")),
            package_id=scope_text(stored.get("package_id")),
        )
        selected = str((resolution.subscription or {}).get("_id") or "")
        if selected != trial_id:
            raise TrialConversionRefused(
                "This trial does not govern its scope"
                + (f" (subscription {selected} does)" if selected else f" ({resolution.reason})")
                + "; it cannot be converted"
            )

        plan_code = str(stored.get("plan_code") or "")
        plan = await self.get_plan_by_code(plan_code)
        if not plan or not plan.get("is_active", True):
            raise TrialConversionRefused(f"Plan '{plan_code}' is not available for conversion")
        billing_period = str(request.billing_period or "monthly")
        if billing_period not in {period.value for period in BillingPeriod}:
            raise ValueError(f"Unsupported billing period '{billing_period}'")
        add_on_codes = list(request.add_on_codes or [])
        if len(set(add_on_codes)) != len(add_on_codes):
            raise ValueError("An add-on is named more than once")
        add_on_prices = 0
        for code in add_on_codes:
            addon = await self.get_addon_by_code(code)
            if not addon or not addon.get("is_active", True):
                raise ValueError(f"Add-on '{code}' not found")
            compatible = addon.get("compatible_plans") or []
            if compatible and plan_code not in compatible:
                raise ValueError(f"Add-on '{code}' is not compatible with plan '{plan_code}'")
            add_on_prices += int(addon.get("price_minor") or 0)
        price = self._period_price(plan, billing_period)
        reason = (request.no_charge_reason or "").strip()

        if reason:
            if not no_charge_authorised:
                raise TrialConversionRefused("A no-charge conversion needs Super Admin authority")
            if len(reason) < self.NO_CHARGE_REASON_MIN_LENGTH:
                raise ValueError(f"A no-charge conversion needs a reason of at least {self.NO_CHARGE_REASON_MIN_LENGTH} characters")
            return await self._convert_trial_in_place(
                stored, plan_code, billing_period, add_on_codes, self.NO_CHARGE_BILLING_STATUS, current_user,
                audit_action="subscription.trial_converted_no_charge",
                audit_metadata={"reason": reason, "authority": "superadmin", "list_price_minor": price + add_on_prices},
            )
        if price + add_on_prices == 0:
            return await self._convert_trial_in_place(
                stored, plan_code, billing_period, add_on_codes, self.FREE_PLAN_BILLING_STATUS, current_user,
                audit_action="subscription.trial_converted_free", audit_metadata={"list_price_minor": 0},
            )
        if add_on_prices:
            raise TrialConversionRefused(
                "Priced add-ons are not charged by checkout; convert the trial first, then add them"
            )
        return await self._convert_trial_by_checkout(stored, plan_code, billing_period, current_user)

    @staticmethod
    def _conversion_outcome(trial_id: str, conversion: Dict[str, Any]) -> Dict[str, Any]:
        if conversion.get("state") == "starting":
            raise TrialConversionRefused("A conversion of this trial is already being started; retry shortly")
        return {
            "status": "pending_payment",
            "converts_trial_id": trial_id,
            "subscription_id": conversion.get("subscription_id"),
            "gateway_subscription_id": conversion.get("gateway_subscription_id"),
            "checkout_url": conversion.get("checkout_url"),
            "repeated": True,
        }

    async def _convert_trial_by_checkout(self, stored: Dict[str, Any], plan_code: str, billing_period: str,
                                         current_user: Any) -> Dict[str, Any]:
        import uuid

        db = await self._get_db()
        trial_id = str(stored["_id"])
        attempt = uuid.uuid4().hex
        claimed = await db.subscriptions.find_one_and_update(
            {"_id": stored["_id"], "status": stored.get("status"),
             "$or": [{"conversion": {"$exists": False}}, {"conversion": None}, {"conversion.state": "failed"}]},
            {"$set": {"conversion": {"state": "starting", "attempt": attempt, "plan_code": plan_code,
                                     "billing_period": billing_period, "requested_by": getattr(current_user, "id", None),
                                     "requested_at": datetime.utcnow()}}},
        )
        if claimed is None:
            # Another request claimed it (or the trial changed): answer with its state.
            current = await db.subscriptions.find_one({"_id": stored["_id"]}) or {}
            if str(current.get("status") or "").strip().lower() != "trial":
                raise TrialConversionRefused("This trial changed while the conversion was being started; reload")
            return self._conversion_outcome(trial_id, current.get("conversion") or {"state": "starting"})
        try:
            checkout = await self.start_checkout(
                organization_id=str(stored.get("organization_id")),
                project_id=scope_text(stored.get("project_id")),
                plan_code=plan_code,
                billing_period=billing_period,
                current_user=current_user,
                converts_trial_id=trial_id,
            )
        except Exception as exc:
            await db.subscriptions.update_one(
                {"_id": stored["_id"], "conversion.attempt": attempt},
                {"$set": {"conversion.state": "failed", "conversion.failed_at": datetime.utcnow(),
                          "conversion.error": type(exc).__name__}},
            )
            await self.audit_service.emit(
                action="subscription.trial_conversion_failed",
                actor_id=getattr(current_user, "id", None),
                resource_type="subscription",
                resource_id=trial_id,
                organization_id=str(stored.get("organization_id")),
                project_id=scope_text(stored.get("project_id")),
                result="failure",
                reason=type(exc).__name__,
            )
            if isinstance(exc, (ValueError, GatewayIdentityConflict)):
                raise
            raise GatewayOperationFailed("The payment gateway did not start the checkout; nothing was changed") from exc
        await db.subscriptions.update_one(
            {"_id": stored["_id"], "conversion.attempt": attempt},
            {"$set": {"conversion.state": "pending_payment", "conversion.subscription_id": checkout["subscription_id"],
                      "conversion.gateway_subscription_id": checkout.get("gateway_subscription_id"),
                      "conversion.checkout_url": checkout.get("checkout_url")}},
        )
        await self.audit_service.emit(
            action="subscription.trial_conversion_checkout_started",
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=trial_id,
            organization_id=str(stored.get("organization_id")),
            project_id=scope_text(stored.get("project_id")),
            metadata={"paid_subscription_id": checkout["subscription_id"], "plan_code": plan_code,
                      "billing_period": billing_period},
        )
        return {**checkout, "status": "pending_payment", "converts_trial_id": trial_id}

    async def _convert_trial_in_place(self, stored: Dict[str, Any], plan_code: str, billing_period: str,
                                      add_on_codes: List[str], billing_status: str, current_user: Any, *,
                                      audit_action: str, audit_metadata: Dict[str, Any]) -> Dict[str, Any]:
        trial_id = str(stored["_id"])
        now = datetime.utcnow()
        update = SubscriptionUpdate(
            status="active",
            billing_status=billing_status,
            billing_period=billing_period,
            trial=False,
            trial_ends_at=None,
            auto_renew=False,
            starts_at=now,
            ends_at=None,
            current_period_start=now,
            current_period_end=self._compute_period_end(now, billing_period),
            active_add_ons=add_on_codes or None,
        )
        # Compare-and-set on what this decision was made on: a concurrent conversion, cancel
        # or checkout claim refuses this write instead of being overwritten.
        expected = {field: stored.get(field) for field in self.LIFECYCLE_STATE_FIELDS}
        expected["conversion"] = stored.get("conversion")
        try:
            result = await self.update_subscription(trial_id, update, current_user, expected_state=expected)
        except SubscriptionNotGoverning as exc:
            raise TrialConversionRefused(str(exc)) from exc
        await self.audit_service.emit(
            action=audit_action,
            actor_id=getattr(current_user, "id", None),
            resource_type="subscription",
            resource_id=trial_id,
            organization_id=str(stored.get("organization_id")),
            project_id=scope_text(stored.get("project_id")),
            metadata={**audit_metadata, "plan_code": plan_code, "billing_period": billing_period,
                      "billing_status": billing_status, "add_on_codes": add_on_codes},
        )
        await self._record_history(
            subscription_id=trial_id,
            organization_id=stored["organization_id"],
            project_id=stored.get("project_id"),
            change_type=SubscriptionChangeType.TRIAL_CONVERT,
            from_status="trial",
            to_status="active",
            to_billing_period=billing_period,
            changed_by=getattr(current_user, "id", None),
            metadata={"billing_status": billing_status, **audit_metadata},
        )
        return result

    async def add_addon(self, subscription_id: str, request: AddOnActionRequest, current_user: Any) -> Dict[str, Any]:
        """Attach an add-on to a subscription."""
        sub = await self.get_subscription_by_id(subscription_id)
        if not sub:
            raise ValueError("Subscription not found")
        state = await self._assert_governs_its_scope(sub, "add an add-on to")

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
        result = await self.update_subscription(subscription_id, update, current_user, expected_state=state)

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
        state = await self._assert_governs_its_scope(sub, "remove an add-on from")

        current_addons = list(sub.get("active_add_ons") or [])
        if request.add_on_code not in current_addons:
            raise ValueError(f"Add-on '{request.add_on_code}' is not active on this subscription")

        current_addons.remove(request.add_on_code)
        update = SubscriptionUpdate(active_add_ons=current_addons)
        result = await self.update_subscription(subscription_id, update, current_user, expected_state=state)

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

    async def get_billing_review_queue(self, organization_id: str) -> List[Dict[str, Any]]:
        """Billing records that need admin attention — failed payments and
        amount/currency mismatches (the rows the webhook flags but never activates
        on). Enriched with the subscription's plan + current billing status so the
        admin can see whether it's still unresolved. Newest first."""
        db = await self._get_db()
        rows = await db.billing_records.find(
            {"organization_id": str(organization_id),
             "record_status": {"$in": ["failed", "amount_mismatch"]}}
        ).sort("created_at", -1).to_list(length=500)

        sub_cache: Dict[str, Any] = {}
        out: List[Dict[str, Any]] = []
        for row in rows:
            sub_id = row.get("subscription_id")
            sub = sub_cache.get(sub_id) if sub_id else None
            if sub_id and sub_id not in sub_cache:
                try:
                    sub = await db.subscriptions.find_one({"_id": self._lookup_id(sub_id)})
                except Exception:  # pragma: no cover - defensive
                    sub = None
                sub_cache[sub_id] = sub
            item = self._normalize(row)
            item["plan_code"] = (sub or {}).get("plan_code")
            item["subscription_billing_status"] = (sub or {}).get("billing_status")
            out.append(item)
        return out

    async def _paid_record_or_raise(self, db: Any, record_id: str, organization_id: str,
                                    *, kind: str) -> Optional[Dict[str, Any]]:
        record = await db.billing_records.find_one(
            {"_id": self._lookup_id(record_id), "organization_id": str(organization_id)}
        )
        if not record:
            return None
        if record.get("record_status") != "paid":
            raise ValueError(f"A {kind} is only available for a successful payment")
        return record

    async def _receipt_context(self, db: Any, record: Dict[str, Any], organization_id: str):
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
        return subscription, plan, organization

    async def build_billing_receipt(self, record_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        """Assemble the receipt for a paid billing record (scoped to the org).

        Returns ``None`` if the record doesn't exist in the org; raises
        ``ValueError`` if it isn't a successful payment (receipts only for paid).
        """
        from .billing_receipt import build_receipt

        db = await self._get_db()
        record = await self._paid_record_or_raise(db, record_id, organization_id, kind="receipt")
        if not record:
            return None
        subscription, plan, organization = await self._receipt_context(db, record, organization_id)
        return build_receipt(record, subscription, plan, organization)

    async def build_billing_tax_invoice(self, record_id: str, organization_id: str) -> Optional[Dict[str, Any]]:
        """Assemble a GST tax invoice for a paid record, allocating a stable,
        financial-year-scoped invoice number on first generation (idempotent —
        re-downloads reuse the same number)."""
        from pymongo import ReturnDocument

        from .billing_receipt import build_tax_invoice, financial_year, format_invoice_number

        db = await self._get_db()
        record = await self._paid_record_or_raise(db, record_id, organization_id, kind="tax invoice")
        if not record:
            return None

        invoice_number = record.get("invoice_number")
        if not invoice_number:
            fy = financial_year(record.get("created_at"))
            counter = await db.billing_invoice_counters.find_one_and_update(
                {"_id": fy}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER,
            )
            invoice_number = format_invoice_number(fy, (counter or {}).get("seq", 1))
            await db.billing_records.update_one(
                {"_id": record["_id"]}, {"$set": {"invoice_number": invoice_number}}
            )

        subscription, plan, organization = await self._receipt_context(db, record, organization_id)
        return build_tax_invoice(record, subscription, plan, organization, invoice_number=invoice_number)

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

    async def _validate_plan_settings_scope(
        self,
        db: Any,
        organization_id: str,
        project_id: Optional[str],
    ) -> None:
        org = await db.organizations.find_one(
            {"_id": {"$in": self._id_values(organization_id)}}
        )
        if not org:
            org = await db.organizations.find_one({"id": str(organization_id)})
        if not org:
            raise ValueError("Organization not found for subscription scope")

        if not project_id:
            return

        project = await db.projects.find_one(
            {"_id": {"$in": self._id_values(project_id)}}
        )
        if not project:
            project = await db.projects.find_one({"id": str(project_id)})
        if not project:
            raise ValueError("Project not found for subscription scope")

        project_org_id = str(
            project.get("organization_id") or project.get("organizationId") or ""
        )
        if project_org_id != str(organization_id):
            raise ValueError("Project does not belong to the selected organization")

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
                "status": "none",
                "billing_status": None,
                "billing_period": None,
                "current_period_start": None,
                "current_period_end": None,
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
            "status": subscription.get("status") or "none",
            "billing_status": subscription.get("billing_status"),
            "billing_period": subscription.get("billing_period", "monthly"),
            "current_period_start": subscription.get("current_period_start"),
            "current_period_end": subscription.get("current_period_end"),
            "trial": bool(subscription.get("trial")),
            "trial_ends_at": subscription.get("trial_ends_at"),
            "active_add_ons": subscription.get("active_add_ons") or [],
            "features": features,
            "dms_enabled": bool(features.get("feature.dms.enabled", False)),
            "drafting_enabled": bool(features.get("feature.drafting.enabled", False)),
        }

    async def get_plan_settings(
        self,
        *,
        organization_ids: Optional[List[str]] = None,
        project_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
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

        scoped_org_ids = (
            {str(org_id) for org_id in organization_ids}
            if organization_ids is not None
            else None
        )
        scoped_project_ids = (
            {str(project_id) for project_id in project_ids}
            if project_ids is not None
            else None
        )

        if scoped_org_ids is not None or scoped_project_ids is not None:
            if scoped_project_ids is not None and scoped_org_ids is None:
                # Review F1: project-only scope means the organisations of those projects;
                # none resolved is none, never every organisation.
                scoped_org_ids = set()
            if scoped_project_ids is not None:
                for project in projects:
                    project_id = str(project.get("id") or project.get("_id") or "")
                    if project_id in scoped_project_ids:
                        org_id = self._org_id_from_project(project)
                        if org_id:
                            if scoped_org_ids is None:
                                scoped_org_ids = set()
                            scoped_org_ids.add(str(org_id))

            if scoped_org_ids is not None:
                organizations = [
                    org
                    for org in organizations
                    if str(org.get("id") or org.get("_id") or "") in scoped_org_ids
                ]

            if scoped_project_ids is not None:
                projects = [
                    project
                    for project in projects
                    if str(project.get("id") or project.get("_id") or "") in scoped_project_ids
                ]
            elif scoped_org_ids is not None:
                projects = [
                    project
                    for project in projects
                    if self._org_id_from_project(project) in scoped_org_ids
                ]

        # The same selection enforcement uses (services.subscription_resolution): status-aware,
        # most specific tier first, never the most recently updated row; an ambiguous scope
        # is shown as such, not as one arbitrary plan.
        from .subscription_resolution import select_subscription

        now = datetime.utcnow()

        def state_for(resolution: Any, source: str) -> Dict[str, Any]:
            if resolution.reason == "ambiguous_subscriptions":
                state = self._service_state(None, plan_by_code, "none")
                state["status"] = "ambiguous"
                return state
            subscription = resolution.subscription
            state = self._service_state(subscription, plan_by_code, source if subscription else "none")
            if subscription is not None and resolution.status_class != "eligible":
                # Enforcement refuses (blocking, unknown) or restricts (archive, offboarding):
                # the display does not claim the service is available.
                state["drafting_enabled"] = False
                if resolution.status_class in {"blocking", "unknown"}:
                    state["dms_enabled"] = False
            return state

        effective: Dict[str, Any] = {"organizations": {}, "projects": {}}
        for org in organizations:
            org_id = str(org.get("id") or org.get("_id") or "")
            resolution = select_subscription(subscriptions, organization_id=org_id, now=now)
            effective["organizations"][org_id] = state_for(resolution, "organization")

        for project in projects:
            project_id = str(project.get("id") or project.get("_id") or "")
            org_id = self._org_id_from_project(project)
            resolution = select_subscription(subscriptions, organization_id=org_id, project_id=project_id, now=now)
            if resolution.tier == "project" or resolution.reason == "ambiguous_subscriptions":
                effective["projects"][project_id] = state_for(resolution, "project")
            else:
                inherited = state_for(resolution, "organization")
                inherited["source"] = "inherited" if inherited.get("plan_code") else "none"
                inherited["inherited_from_organization_id"] = org_id if inherited.get("plan_code") else None
                effective["projects"][project_id] = inherited

        visible_subscriptions = subscriptions
        if scoped_org_ids is not None:
            visible_subscriptions = [
                sub
                for sub in visible_subscriptions
                if str(sub.get("organization_id") or "") in scoped_org_ids
            ]
        if scoped_project_ids is not None:
            visible_subscriptions = [
                sub
                for sub in visible_subscriptions
                if str(sub.get("project_id") or "") in scoped_project_ids
            ]

        return {
            "organizations": organizations,
            "projects": projects,
            "plans": plans,
            "subscriptions": visible_subscriptions,
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
        await self._validate_plan_settings_scope(db, org_id, project_id)

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
        if not await self.validate_plan_code(plan_code):
            raise ValueError(f"Plan '{plan_code}' not found or inactive")

        billing_period = payload.billing_period or "monthly"
        allowed_periods = {period.value for period in BillingPeriod}
        if billing_period not in allowed_periods:
            raise ValueError(f"Unsupported billing period '{billing_period}'")
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
        from .subscription_resolution import GOVERNING_STATUSES

        if str(doc.get("status") or "").strip().lower() in GOVERNING_STATUSES:
            doc["governing_since"] = now
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
