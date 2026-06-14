import asyncio
import logging
import sys
import os

# Ensure the backend directory is in the path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from rbac_backend.core.database import connect, disconnect, get_database
from rbac_backend.models.permission import DEFAULT_PERMISSIONS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrate_rbac")

LEGACY_TO_CANONICAL = {
    "documents:read": ["dms.document.view", "dms.dashboard.view"],
    "documents:create": ["dms.document.upload"],
    "documents:upload": ["dms.document.upload"],
}

async def run_migration():
    logger.info("Connecting to database...")
    await connect()
    db = await get_database()
    
    logger.info("1. Inserting canonical permissions...")
    for perm in DEFAULT_PERMISSIONS:
        await db.permissions.update_one(
            {"name": perm["name"]},
            {"$set": perm},
            upsert=True
        )
    logger.info("Canonical permissions inserted/updated.")

    logger.info("2. Updating roles collection...")
    cursor = db.roles.find({})
    async for role in cursor:
        role_id = role.get("_id")
        permissions = set(role.get("permissions", []))
        
        # Replace legacy with canonical
        to_add = set()
        to_remove = set()
        for p in permissions:
            if p in LEGACY_TO_CANONICAL:
                to_remove.add(p)
                for canonical in LEGACY_TO_CANONICAL[p]:
                    to_add.add(canonical)
        
        permissions.difference_update(to_remove)
        permissions.update(to_add)
        
        # Strip drafting from non-expert roles
        if role_id not in ["superadmin", "contraclaim_drafting_manager", "contraclaim_expert_drafter", "contraclaim_expert_reviewer"]:
            permissions.discard("drafting.request.create")
            permissions.discard("drafting.request.view")
            permissions.discard("draft.request.create")
            permissions.discard("draft.request.view")
            
        await db.roles.update_one(
            {"_id": role_id},
            {"$set": {"permissions": list(permissions)}}
        )
        logger.info(f"Updated role: {role_id}")
        
    logger.info("3. Cleaning up legacy permissions from permissions collection...")
    for legacy in LEGACY_TO_CANONICAL.keys():
        await db.permissions.delete_one({"name": legacy})
    logger.info("Legacy permissions deleted.")
    
    logger.info("Migration Phase 2 complete.")
    await disconnect()

if __name__ == "__main__":
    asyncio.run(run_migration())
