from __future__ import annotations

from typing import Any, Dict, List

from ..services.skills.system_skills import system_skill_versions
from ..utils.datetime import now_utc
from .runner import MigrationResult


VERSION = "20261114_0002"
NAME = "seed_system_skills"
DESCRIPTION = (
    "Seed the three bundled Indian-infrastructure skill packs as published system skills "
    "at 1.0.0 with their manifests."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []
    now = now_utc()
    # Only the packs bundled as published are seeded here. Packs bundled as
    # drafts are stored, unpublished, by 20261115_0001; seeding them here would
    # give unreviewed text a publication date and a "latest published" pointer.
    for version in system_skill_versions():
        if version.status != "published":
            continue
        existing = await db.skill_versions.find_one(
            {"skill_key": version.skill_key, "version": version.version}
        )
        if existing:
            # Published versions are write-once. A differing hash means the bundled
            # text changed without a version bump: report it, never overwrite.
            if existing.get("content_hash") != version.content_hash:
                warnings.append(
                    f"{version.skill_key} {version.version} is already stored with a different "
                    "content hash; the stored version was left untouched. Bump the version."
                )
            operations.append(
                {"operation": "skip_existing", "collection": "skill_versions",
                 "skill_key": version.skill_key, "version": version.version}
            )
            continue
        operations.append(
            {
                "operation": "insert_one",
                "collection": "skill_versions",
                "skill_key": version.skill_key,
                "version": version.version,
                "content_hash": version.content_hash,
            }
        )
        operations.append(
            {"operation": "upsert_one", "collection": "skills", "skill_key": version.skill_key}
        )
        if dry_run:
            continue
        payload = version.model_dump(by_alias=True, exclude={"id"})
        payload["published_at"] = now
        payload["created_at"] = now
        await db.skill_versions.insert_one(payload)
        await db.skills.update_one(
            {"owner_scope": version.owner_scope, "skill_key": version.skill_key},
            {
                "$set": {
                    "owner_scope": version.owner_scope,
                    "skill_key": version.skill_key,
                    "name": version.name,
                    "description": version.description,
                    "kind": version.kind,
                    "status": "active",
                    "latest_published_version": version.version,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )
