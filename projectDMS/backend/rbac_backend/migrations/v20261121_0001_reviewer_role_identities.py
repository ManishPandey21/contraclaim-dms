"""M-02 (plan 4B-06, D-06, AQ-3): seed the reviewer role identities.

Nine role documents, each an identity with no permissions: the four that sign
off skill packs and five that answer arbitration gates. Holding one confers
nothing by itself. A person holds a reviewer role through a
``reviewer_role_assignments`` record (M-03), never through this document.

Skip-existing. A role document that already uses one of these keys, or one of
these names under another key, is left exactly as it is and reported as a
warning, because what it means in that database is for a person to decide
(reading P-05). Nothing is overwritten, merged or deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..core.permissions import REVIEWER_ROLE_KEYS
from ..models.reviewer_assignment import REVIEWER_ROLE_LABELS
from .runner import MigrationResult

VERSION = "20261121_0001"
NAME = "reviewer_role_identities"
DESCRIPTION = "Seed reviewer role identities with no permissions (skip-existing; conflicts are reported, not overwritten)."


def identity_document(role_key: str, now: datetime) -> Dict[str, Any]:
    return {
        "_id": role_key,
        "name": REVIEWER_ROLE_LABELS[role_key],
        "description": "Reviewer identity. Held through a reviewer assignment; carries no permissions.",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "kind": "reviewer_identity",
        "permissions": [],
        "created_at": now,
        "updated_at": now,
    }


def _is_the_identity(doc: Dict[str, Any]) -> bool:
    return doc.get("kind") == "reviewer_identity" and not (doc.get("permissions") or [])


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []
    now = datetime.utcnow()
    for role_key in sorted(REVIEWER_ROLE_KEYS):
        label = REVIEWER_ROLE_LABELS[role_key]
        existing = await db.roles.find_one({"_id": role_key})
        if existing is not None:
            if _is_the_identity(existing):
                operations.append({"operation": "skip_existing", "collection": "roles", "role": role_key})
            else:
                count = len(existing.get("permissions") or [])
                warnings.append(
                    f"roles: a document with key {role_key} already exists and is not the seeded identity "
                    f"({count} permission(s)); it is left unchanged"
                )
            continue
        same_name = await db.roles.count_documents(
            {"_id": {"$ne": role_key}, "name": {"$regex": f"^({role_key}|{label})$", "$options": "i"}}
        )
        if same_name:
            warnings.append(
                f"roles: {same_name} document(s) already use the name of reviewer role {role_key} under another key; "
                "the identity is not seeded until that is resolved"
            )
            continue
        operations.append({"operation": "insert_role", "collection": "roles", "role": role_key})
        if not dry_run:
            await db.roles.insert_one(identity_document(role_key, now))
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
        notices=[
            "A reviewer identity carries no permissions. A person holds one only through a reviewer assignment.",
            "An existing role document with one of these keys or names is never overwritten.",
        ],
    )
