"""One-command demo seed (Week 3.7).

Creates a realistic EPC/infra demo tenant so a pilot or sales demo has data to
explore immediately:

    python -m rbac_backend.initial_data.demo_seed

Idempotent: re-running upserts the same fixed records. The demo subscription uses
``entitlement_overrides`` so DMS + drafting are enabled regardless of which plans
are configured.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEMO_ORG_ID = "demo-org-metro"
DEMO_PROJECT_ID = "demo-proj-mrp3"
DEMO_SUBSCRIPTION_ID = "demo-sub-metro"

DEMO_ORGANIZATION: Dict[str, Any] = {
    "_id": DEMO_ORG_ID,
    "name": "ContraClaim Demo — Metro Rail Contractors Ltd",
    "description": "Demonstration tenant for a metro rail EPC contractor.",
    "city": "Pune",
    "state": "Maharashtra",
    "billingOption": "Yes",
    "is_demo": True,
}

DEMO_PROJECT: Dict[str, Any] = {
    "_id": DEMO_PROJECT_ID,
    "name": "Metro Rail Package 3 — Viaduct & Stations",
    "description": "Design & construction of elevated viaduct and 4 stations.",
    "organization_id": DEMO_ORG_ID,
    "is_demo": True,
}


def _demo_documents(now: datetime) -> list[Dict[str, Any]]:
    base = {
        "organization_id": DEMO_ORG_ID,
        "project_id": DEMO_PROJECT_ID,
        "file_type": "application/pdf",
        "filetype": "application/pdf",
        "lifecycle_state": "active",
        "is_demo": True,
        "tags": ["demo"],
    }
    docs = [
        {
            "_id": "demo-doc-eot-001",
            "filename": "EOT-Claim-Monsoon-2025.pdf",
            "subject": "Extension of Time claim — monsoon delays (Clause 8.4)",
            "letterNo": "MRP3/CON/EOT/001",
            "uploadType": "outgoing",
            "status": "issued",
        },
        {
            "_id": "demo-doc-ei-045",
            "filename": "Engineers-Instruction-EI-045.pdf",
            "subject": "Engineer's Instruction EI-045 — revised pier reinforcement",
            "letterNo": "MRP3/ENG/EI/045",
            "uploadType": "incoming",
            "status": "received",
        },
        {
            "_id": "demo-doc-var-012",
            "filename": "Variation-Proposal-VP-012.pdf",
            "subject": "Variation proposal VP-012 — additional utility diversion",
            "letterNo": "MRP3/CON/VAR/012",
            "uploadType": "outgoing",
            "status": "draft",
        },
    ]
    for doc in docs:
        doc.update(base)
        normalized = str(doc["letterNo"]).replace("/", "").replace("-", "").lower()
        doc["letterNoNormalized"] = normalized
        doc["createdAt"] = now
        doc["updatedAt"] = now
    return docs


def _demo_subscription(now: datetime) -> Dict[str, Any]:
    end = now + timedelta(days=30)
    return {
        "_id": DEMO_SUBSCRIPTION_ID,
        "organization_id": DEMO_ORG_ID,
        "project_id": None,
        "plan_code": "demo",
        "billing_period": "monthly",
        "status": "trial",
        "billing_status": "active",
        "trial": True,
        "trial_ends_at": end,
        "starts_at": now,
        "ends_at": end,
        "current_period_start": now,
        "current_period_end": end,
        "auto_renew": False,
        "entitlement_overrides": {
            "feature.dms.enabled": True,
            "feature.drafting.enabled": True,
        },
        "is_demo": True,
        "created_at": now,
        "updated_at": now,
        "created_by": "system:demo-seed",
        "updated_by": "system:demo-seed",
    }


async def seed_demo_data(db: Any = None) -> Dict[str, int]:
    """Upsert the demo tenant. Returns a summary of how many records were written."""
    if db is None:
        from ..core.database import get_database

        db = await get_database()

    now = datetime.utcnow()

    await db.organizations.replace_one({"_id": DEMO_ORG_ID}, DEMO_ORGANIZATION, upsert=True)
    await db.projects.replace_one({"_id": DEMO_PROJECT_ID}, DEMO_PROJECT, upsert=True)
    await db.subscriptions.replace_one(
        {"_id": DEMO_SUBSCRIPTION_ID}, _demo_subscription(now), upsert=True
    )

    documents = _demo_documents(now)
    for doc in documents:
        await db.documents.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    summary = {
        "organizations": 1,
        "projects": 1,
        "subscriptions": 1,
        "documents": len(documents),
    }
    logger.info("Demo data seeded: %s", summary)
    return summary


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    from ..core.database import connect, disconnect

    await connect()
    try:
        summary = await seed_demo_data()
        print(
            "Demo seed complete:",
            ", ".join(f"{key}={value}" for key, value in summary.items()),
        )
        print(f"  organization_id = {DEMO_ORG_ID}")
        print(f"  project_id      = {DEMO_PROJECT_ID}")
    finally:
        await disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
