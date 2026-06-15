"""One-time migration: Upgrade plan and subscription documents to v2 schema.

Run from project root:
    python -m backend.scripts.migrate_plans_v2

What this script does:
1. Adds new fields to existing plan documents (pricing_tiers, trial_config, etc.)
2. Adds new fields to existing subscription documents (billing_period, trial_ends_at, etc.)
3. Creates MongoDB indexes for new query patterns
4. Seeds the default add-ons collection
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run_migration():
    # Import here to avoid circular issues when running standalone
    from backend.rbac_backend.core.database import get_database
    from backend.rbac_backend.services.monetization_service import MonetizationService

    db = await get_database()
    svc = MonetizationService(db)
    now = datetime.utcnow()

    # ------------------------------------------------------------------
    # 1. Migrate existing plans: add missing v2 fields with safe defaults
    # ------------------------------------------------------------------
    logger.info("Step 1: Migrating existing plan documents...")
    plan_defaults = {
        "description": None,
        "tier": 0,
        "pricing_tiers": {},
        "discount_percentages": {},
        "available_add_ons": [],
        "trial_config": {"enabled": False},
        "display_order": 0,
        "highlight": False,
        "max_users": None,
        "max_storage_gb": None,
    }

    plan_count = 0
    async for plan in db.plans.find({}):
        update_fields = {}
        for key, default in plan_defaults.items():
            if key not in plan:
                update_fields[key] = default

        # Auto-generate pricing_tiers from base_price_minor if missing
        if "pricing_tiers" not in plan or not plan.get("pricing_tiers"):
            base = plan.get("base_price_minor", 0)
            if base > 0:
                update_fields["pricing_tiers"] = {
                    "monthly": base,
                    "quarterly": int(base * 3 * 0.933),  # ~6.7% discount
                    "annual": int(base * 12 * 0.833),     # ~16.7% discount
                }
                update_fields["discount_percentages"] = {
                    "quarterly": 6.7,
                    "annual": 16.7,
                }

        if update_fields:
            update_fields["updated_at"] = now
            await db.plans.update_one(
                {"_id": plan["_id"]},
                {"$set": update_fields},
            )
            plan_count += 1

    logger.info("  Migrated %d plan documents", plan_count)

    # Upsert all default plans to ensure complete catalog
    logger.info("  Seeding/updating default plans...")
    await svc.seed_default_plans()

    # ------------------------------------------------------------------
    # 2. Migrate existing subscriptions: add missing v2 fields
    # ------------------------------------------------------------------
    logger.info("Step 2: Migrating existing subscription documents...")
    sub_count = 0
    async for sub in db.subscriptions.find({}):
        update_fields = {}

        if "billing_period" not in sub:
            update_fields["billing_period"] = "monthly"

        if "current_period_start" not in sub:
            starts = sub.get("starts_at") or sub.get("created_at") or now
            update_fields["current_period_start"] = starts

        if "current_period_end" not in sub:
            start = sub.get("starts_at") or sub.get("created_at") or now
            update_fields["current_period_end"] = start + timedelta(days=30)

        if "auto_renew" not in sub:
            is_trial = sub.get("trial", False)
            update_fields["auto_renew"] = not is_trial

        if "active_add_ons" not in sub:
            update_fields["active_add_ons"] = []

        if "trial_ends_at" not in sub:
            if sub.get("trial") and sub.get("ends_at"):
                update_fields["trial_ends_at"] = sub["ends_at"]
            else:
                update_fields["trial_ends_at"] = None

        if "cancelled_at" not in sub:
            update_fields["cancelled_at"] = None

        if "cancellation_reason" not in sub:
            update_fields["cancellation_reason"] = None

        if "payment_gateway_subscription_id" not in sub:
            update_fields["payment_gateway_subscription_id"] = None

        if update_fields:
            update_fields["updated_at"] = now
            await db.subscriptions.update_one(
                {"_id": sub["_id"]},
                {"$set": update_fields},
            )
            sub_count += 1

    logger.info("  Migrated %d subscription documents", sub_count)

    # ------------------------------------------------------------------
    # 3. Create MongoDB indexes
    # ------------------------------------------------------------------
    logger.info("Step 3: Creating indexes...")

    # Plans
    await db.plans.create_index("code", unique=True, name="idx_plans_code")
    await db.plans.create_index("family", name="idx_plans_family")
    await db.plans.create_index("is_active", name="idx_plans_active")

    # Subscriptions
    await db.subscriptions.create_index(
        [("organization_id", 1), ("project_id", 1), ("status", 1)],
        name="idx_subs_org_proj_status",
    )
    await db.subscriptions.create_index(
        [("status", 1), ("trial_ends_at", 1)],
        name="idx_subs_trial_expiry",
    )
    await db.subscriptions.create_index(
        [("status", 1), ("auto_renew", 1), ("current_period_end", 1)],
        name="idx_subs_renewal",
    )

    # Subscription history
    await db.subscription_history.create_index(
        [("subscription_id", 1), ("changed_at", -1)],
        name="idx_subhist_sub_date",
    )
    await db.subscription_history.create_index(
        [("organization_id", 1), ("changed_at", -1)],
        name="idx_subhist_org_date",
    )

    # Add-ons
    await db.addons.create_index("code", unique=True, name="idx_addons_code")

    # Usage events (for quota queries)
    await db.usage_events.create_index(
        [("organization_id", 1), ("event_type", 1), ("created_at", 1)],
        name="idx_usage_org_type_date",
    )

    logger.info("  All indexes created")

    # ------------------------------------------------------------------
    # 4. Seed default add-ons
    # ------------------------------------------------------------------
    logger.info("Step 4: Seeding default add-ons...")
    await svc.seed_default_addons()
    addon_count = await db.addons.count_documents({})
    logger.info("  %d add-ons in database", addon_count)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_plans = await db.plans.count_documents({})
    total_subs = await db.subscriptions.count_documents({})
    logger.info("=" * 60)
    logger.info("Migration complete!")
    logger.info("  Plans in DB:         %d", total_plans)
    logger.info("  Subscriptions in DB: %d", total_subs)
    logger.info("  Add-ons in DB:       %d", addon_count)
    logger.info("=" * 60)


def main():
    logger.info("Starting plan/subscription v2 migration...")
    try:
        asyncio.run(run_migration())
    except Exception as e:
        logger.error("Migration failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
