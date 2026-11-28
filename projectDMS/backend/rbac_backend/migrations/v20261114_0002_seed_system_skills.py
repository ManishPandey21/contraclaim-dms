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


def _summary(version: Any, now: Any) -> Dict[str, Any]:
    return {
        "owner_scope": version.owner_scope,
        "skill_key": version.skill_key,
        "name": version.name,
        "description": version.description,
        "kind": version.kind,
        "status": "active",
        "latest_published_version": version.version,
        "updated_at": now,
    }


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []
    now = now_utc()
    for version in system_skill_versions():
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
            # A run killed between the version insert and the summary upsert left
            # the version without its summary row; this rerun is the repair. It
            # only inserts a missing row, so a newer summary is never rewound.
            summary_key = {"owner_scope": version.owner_scope, "skill_key": version.skill_key}
            if await db.skills.find_one(summary_key) is None:
                operations.append(
                    {"operation": "insert_missing_summary", "collection": "skills", "skill_key": version.skill_key}
                )
                if not dry_run:
                    await db.skills.update_one(
                        summary_key,
                        {"$setOnInsert": {**_summary(version, now), "created_at": now}},
                        upsert=True,
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
            {"$set": _summary(version, now), "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )
