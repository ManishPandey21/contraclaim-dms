from __future__ import annotations

from typing import Any, Dict, List

from ..services.skills.system_skills import DRAFT_SKILL_KEYS, system_skill_versions
from ..utils.datetime import now_utc
from .runner import MigrationResult


VERSION = "20261115_0001"
NAME = "seed_claim_arbitration_skill_drafts"
DESCRIPTION = (
    "Store the claims, EOT, arbitration-pleadings and arbitration-hearing skill packs as "
    "DRAFT system skill versions at 1.0.0, and index skill approvals. Nothing is published."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []
    now = now_utc()
    drafts = [item for item in system_skill_versions() if item.skill_key in DRAFT_SKILL_KEYS]
    for version in sorted(drafts, key=lambda item: item.skill_key):
        existing = await db.skill_versions.find_one(
            {"skill_key": version.skill_key, "version": version.version}
        )
        if existing:
            # A stored version is never overwritten here: a draft may already carry
            # approvals bound to its text, and a published one is write-once.
            if existing.get("content_hash") != version.content_hash:
                warnings.append(
                    f"{version.skill_key} {version.version} is already stored as "
                    f"{existing.get('status')} with a different content hash; the stored "
                    "version was left untouched. Import the bundled text as a new version."
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
                "status": "draft",
                "content_hash": version.content_hash,
            }
        )
        if dry_run:
            continue
        payload = version.model_dump(by_alias=True, exclude={"id"})
        payload["status"] = "draft"
        payload["created_at"] = now
        await db.skill_versions.insert_one(payload)

    operations.append(
        {
            "operation": "create_index",
            "collection": "skill_approvals",
            "keys": [["skill_key", 1], ["version", 1], ["decided_at", 1]],
        }
    )
    if not dry_run:
        await db.skill_approvals.create_index(
            [("skill_key", 1), ("version", 1), ("decided_at", 1)],
            name="skill_approvals_by_version",
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
        notices=[
            "The four packs are stored as drafts. Each needs its technical and legal "
            "approvals before it can be published; this migration publishes nothing.",
            "The three letter packs published by 20261114_0002 carry a structural seed "
            "evaluation with no signatory. They are unchanged; the approval gate applies "
            "to them from their next version.",
        ],
    )
