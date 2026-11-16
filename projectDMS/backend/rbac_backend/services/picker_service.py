"""People a user may pick for a role on one project (plan 4B-11, C-21).

Pickers used to call ``/users``, which lists every user of the caller's
organisations with no authority filter, so people from other projects, or
without the authority the action needs, were offered and then refused.

``eligible_users`` answers one question: which active members of this project
hold this permission? It uses the two canonical rules and no new one:

- membership: ``ScopeService.is_client_scope_allowed`` (assigned projects, or an
  organisation-wide role in the project's organisation);
- authority: ``PolicyService.has_permission``, so a privileged permission
  (``SUPERADMIN_EXCLUDED_PERMISSIONS``) counts only when held by name.

Global accounts (Super Admin, Super User) and service accounts are never
offered: they are not project members. Only a short allow-list of permissions
can be asked about, so the route cannot be used to map who holds what.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, Field

from ..core.permissions import Permissions
from .policy_service import PolicyService
from .scope_service import ScopeService

#: Permissions a picker may filter on, and the permission the caller needs to ask.
PICKER_PERMISSIONS: Dict[str, str] = {
    Permissions.CLAIM_VIEW: Permissions.CLAIM_VIEW,
    Permissions.CLAIM_EDIT: Permissions.CLAIM_VIEW,
    Permissions.CLAIM_MANAGE: Permissions.CLAIM_VIEW,
    Permissions.CLAIM_DETERMINE: Permissions.CLAIM_VIEW,
    Permissions.ARBITRATION_VIEW: Permissions.ARBITRATION_VIEW,
    Permissions.ARBITRATION_EDIT: Permissions.ARBITRATION_VIEW,
    Permissions.ARBITRATION_APPROVE: Permissions.ARBITRATION_VIEW,
}

#: Roles that are not project membership, whatever their scope says.
GLOBAL_ROLE_KEYS: FrozenSet[str] = frozenset({"superadmin", "superuser"})
NON_PERSON_ACCOUNT_TYPES: FrozenSet[str] = frozenset({"system_service", "service_account"})

#: Upper bound on members examined per request; each needs a permission check.
MAX_CANDIDATES = 500


class PickerUser(BaseModel):
    id: str
    name: str


class PickerUsersResponse(BaseModel):
    items: List[PickerUser] = Field(default_factory=list)
    #: More people match than were returned: narrow the search.
    truncated: bool = False


def display_name(doc: Dict[str, Any]) -> str:
    for key in ("full_name", "name", "display_name", "username"):
        value = doc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    first, last = doc.get("first_name"), doc.get("last_name")
    joined = " ".join(str(part).strip() for part in (first, last) if part)
    return joined or "Unnamed user"


def _as_user(doc: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(doc.get("_id")),
        roles=[str(role) for role in (doc.get("roles") or []) if role],
        organization_id=doc.get("organization_id"),
        organizations=list(doc.get("organizations") or []),
        projects=list(doc.get("projects") or []),
        account_type=doc.get("account_type"),
    )


class PickerService:
    def __init__(self, db: Any, policy: Optional[PolicyService] = None) -> None:
        self.db = db
        self.policy = policy or PolicyService(db=db)
        self.scope = ScopeService(db)

    async def eligible_users(
        self,
        *,
        organization_id: str,
        project_id: str,
        permission: str,
        q: Optional[str] = None,
        limit: int = 25,
    ) -> PickerUsersResponse:
        member_ids = set()
        async for row in self.db.project_memberships.find(
            {"project_id": str(project_id), "status": {"$in": ["active", None]}}, {"user_id": 1}
        ):
            if row.get("user_id"):
                member_ids.add(str(row["user_id"]))
        async for row in self.db.organization_memberships.find(
            {"organization_id": str(organization_id), "status": {"$in": ["active", None]}}, {"user_id": 1}
        ):
            if row.get("user_id"):
                member_ids.add(str(row["user_id"]))

        query: Dict[str, Any] = {
            "disabled": {"$ne": True},
            "$or": [
                {"organization_id": str(organization_id)},
                {"organizations": str(organization_id)},
                {"projects": str(project_id)},
                {"_id": {"$in": sorted(member_ids)}},
            ],
        }
        text = (q or "").strip()
        if text:
            pattern = re.escape(text[:80])
            query["$and"] = [
                {"$or": [{key: {"$regex": pattern, "$options": "i"}} for key in ("full_name", "name", "username", "email")]}
            ]

        cursor = self.db.users.find(
            query,
            {"full_name": 1, "name": 1, "display_name": 1, "username": 1, "first_name": 1, "last_name": 1,
             "roles": 1, "organization_id": 1, "organizations": 1, "projects": 1, "account_type": 1},
        ).limit(MAX_CANDIDATES + 1)
        docs = await cursor.to_list(length=MAX_CANDIDATES + 1)
        examined_all = len(docs) <= MAX_CANDIDATES

        eligible: List[PickerUser] = []
        for doc in docs[:MAX_CANDIDATES]:
            user = _as_user(doc)
            if {role.lower() for role in user.roles} & GLOBAL_ROLE_KEYS:
                continue
            if str(user.account_type or "").lower() in NON_PERSON_ACCOUNT_TYPES:
                continue
            if not await self.scope.is_client_scope_allowed(
                user, organization_id=str(organization_id), project_id=str(project_id)
            ):
                continue
            if not await self.policy.has_permission(user, permission):
                continue
            eligible.append(PickerUser(id=user.id, name=display_name(doc)))

        eligible.sort(key=lambda person: (person.name.lower(), person.id))
        return PickerUsersResponse(items=eligible[:limit], truncated=len(eligible) > limit or not examined_all)


__all__ = ["PICKER_PERMISSIONS", "PickerService", "PickerUser", "PickerUsersResponse", "display_name"]
