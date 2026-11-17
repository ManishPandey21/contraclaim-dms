"""Test data for reviewer role assignments (plan 4B-07).

A reviewer role is held through an assignment, not through a name on the
user. Tests that need a reviewer state that precondition with ``hold_platform``
or ``hold_scoped``; a test that needs someone *without* the role simply does not
call them. The helpers write the stored shape directly, so they also serve
tests of code that only reads assignments.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from ..core.permissions import REVIEWER_ROLE_KEYS
from ..models.reviewer_assignment import REVIEWER_ROLE_ASSIGNMENTS


def assignment_document(
    user_id: str,
    role_key: str,
    *,
    scope_type: str = "platform",
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    granted_by: str = "another-super-admin",
    expires_at: Optional[datetime] = None,
    revoked_at: Optional[datetime] = None,
) -> dict:
    doc = {
        "_id": uuid.uuid4().hex,
        "user_id": str(user_id),
        "role_key": role_key,
        "scope_type": scope_type,
        "organization_id": organization_id,
        "project_id": project_id,
        "granted_by": granted_by,
        "reason": "test precondition",
        "granted_at": datetime.utcnow() - timedelta(minutes=1),
        "expires_at": expires_at,
        "revoked_at": revoked_at,
        "revoked_by": None,
        "revoke_reason": None,
    }
    if revoked_at is None:
        doc["active"] = True
    return doc


async def hold_platform(db: Any, *actors: Any) -> None:
    """Give each actor a live platform assignment for every reviewer role on its role list."""
    collection = db[REVIEWER_ROLE_ASSIGNMENTS]
    for actor in actors:
        read = actor.get if isinstance(actor, dict) else lambda name, default=None: getattr(actor, name, default)
        user_id = str(read("id", "") or "")
        for role in read("roles", None) or []:
            key = str(role).strip().lower()
            if key not in REVIEWER_ROLE_KEYS:
                continue
            existing = await collection.find_one(
                {"user_id": user_id, "role_key": key, "scope_type": "platform", "revoked_at": None}
            )
            if existing is None:
                await collection.insert_one(assignment_document(user_id, key))


async def hold_scoped(
    db: Any, user_id: str, role_key: str, *, organization_id: str, project_id: Optional[str] = None, **fields: Any
) -> dict:
    doc = assignment_document(
        user_id,
        role_key,
        scope_type="project" if project_id else "organization",
        organization_id=organization_id,
        project_id=project_id,
        expires_at=fields.pop("expires_at", datetime.utcnow() + timedelta(days=30)),
        **fields,
    )
    await db[REVIEWER_ROLE_ASSIGNMENTS].insert_one(doc)
    return doc
