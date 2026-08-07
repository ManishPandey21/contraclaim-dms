from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from bson import ObjectId

from ..core.database import get_database
from ..models.rbac_monetization import AccountType


class ScopeService:
    """Computes client membership and ContraClaim expert allocation scopes."""

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    @staticmethod
    def role_names(user: Any) -> Set[str]:
        return {str(role).lower() for role in (getattr(user, "roles", []) or []) if role}

    @staticmethod
    def account_type(user: Any) -> str:
        return str(getattr(user, "account_type", None) or AccountType.CLIENT_USER.value)

    def is_superadmin(self, user: Any) -> bool:
        return "superadmin" in self.role_names(user)

    async def client_organization_ids(self, user: Any) -> Set[str]:
        org_ids = {str(getattr(user, "organization_id"))} if getattr(user, "organization_id", None) else set()
        org_ids.update({str(org) for org in (getattr(user, "organizations", []) or []) if org})

        db = await self._get_db()
        cursor = db.organization_memberships.find(
            {
                "user_id": str(getattr(user, "id", "")),
                "status": {"$in": ["active", None]},
            }
        )
        for membership in await cursor.to_list(length=None):
            if membership.get("organization_id"):
                org_ids.add(str(membership["organization_id"]))
        return org_ids

    async def client_project_ids(self, user: Any) -> Set[str]:
        project_ids = {str(project) for project in (getattr(user, "projects", []) or []) if project}

        db = await self._get_db()
        cursor = db.project_memberships.find(
            {
                "user_id": str(getattr(user, "id", "")),
                "status": {"$in": ["active", None]},
            }
        )
        for membership in await cursor.to_list(length=None):
            if membership.get("project_id"):
                project_ids.add(str(membership["project_id"]))
        return project_ids

    async def project_belongs_to_organization(self, *, project_id: str, organization_id: str) -> bool:
        """Return whether a project exists under the supplied organization.

        This is a tenant-isolation guard, not a convenience lookup. If the
        projects collection is unavailable, the project is missing, or the
        project document has no organization, the safe answer is ``False``.
        """
        if not project_id or not organization_id:
            return False

        db = await self._get_db()
        projects = getattr(db, "projects", None)
        if projects is None or not hasattr(projects, "find_one"):
            return False

        project = await projects.find_one({"_id": self.object_id_query(str(project_id))})
        if not project:
            return False

        project_org = project.get("organization_id") or project.get("organizationId")
        if not project_org:
            return False
        return str(project_org) == str(organization_id)

    async def is_client_scope_allowed(
        self,
        user: Any,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        if self.is_superadmin(user):
            return True

        org_ids = await self.client_organization_ids(user)
        project_ids = await self.client_project_ids(user)
        roles = self.role_names(user)

        if roles & {"projectadmin", "projectuser"} and not project_id and not project_ids:
            # No assignment means no reach, so a collection-level read has
            # nothing to consolidate over and is denied.
            #
            # With assignments it is allowed: "no project selected" is the
            # caller's consolidated view over their own projects, not a request
            # to widen. Denying here instead broke every collection-level read
            # for project-tier users (project stats, task listings) even though
            # build_scope_query already bounds those queries to `projects`.
            # Result-bounding is the caller's job -- see docs/AUTHZ.md; this
            # function answers membership, not row visibility.
            return False

        if organization_id and str(organization_id) not in org_ids:
            return False

        if project_id:
            if organization_id and not await self.project_belongs_to_organization(
                project_id=str(project_id),
                organization_id=str(organization_id),
            ):
                return False
            if str(project_id) in project_ids:
                return True
            if roles & {"orgadmin", "orguser"} and organization_id and str(organization_id) in org_ids:
                return True
            return False

        return bool(org_ids)

    async def active_expert_allocations(
        self,
        user: Any,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
        letter_id: Optional[str] = None,
        drafting_request_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        now = datetime.utcnow()
        query: Dict[str, Any] = {
            "expert_user_id": str(getattr(user, "id", "")),
            "status": "active",
            "$and": [
                {"$or": [{"start_date": {"$exists": False}}, {"start_date": None}, {"start_date": {"$lte": now}}]},
                {"$or": [{"end_date": {"$exists": False}}, {"end_date": None}, {"end_date": {"$gte": now}}]},
            ],
        }
        if organization_id:
            query["organization_id"] = str(organization_id)
        if project_id:
            query.setdefault("$and", []).append(
                {
                    "$or": [
                        {"project_id": None},
                        {"project_id": ""},
                        {"project_id": str(project_id)},
                    ]
                }
            )
        if package_id:
            query.setdefault("$and", []).append(
                {
                    "$or": [
                        {"package_id": None},
                        {"package_id": ""},
                        {"package_id": str(package_id)},
                    ]
                }
            )
        if letter_id:
            query.setdefault("$and", []).append(
                {"$or": [{"letter_id": None}, {"letter_id": ""}, {"letter_id": str(letter_id)}]}
            )
        if drafting_request_id:
            query.setdefault("$and", []).append(
                {
                    "$or": [
                        {"drafting_request_id": None},
                        {"drafting_request_id": ""},
                        {"drafting_request_id": str(drafting_request_id)},
                    ]
                }
            )
        return await db.expert_allocations.find(query).to_list(length=None)

    async def has_expert_allocation(
        self,
        user: Any,
        *,
        permission: str,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
        letter_id: Optional[str] = None,
        drafting_request_id: Optional[str] = None,
    ) -> bool:
        if self.is_superadmin(user):
            return True
        if self.account_type(user) != AccountType.CONTRACLAIM_STAFF.value:
            return False
        allocations = await self.active_expert_allocations(
            user,
            organization_id=organization_id,
            project_id=project_id,
            package_id=package_id,
            letter_id=letter_id,
            drafting_request_id=drafting_request_id,
        )
        for allocation in allocations:
            granted = {str(item) for item in (allocation.get("permissions_granted") or [])}
            if "*" in granted or permission in granted:
                return True
            if not granted:
                role = str(allocation.get("assignment_role") or "")
                if role == "drafter" and permission.startswith("drafting.draft."):
                    return True
                if role in {"reviewer", "senior_reviewer"} and permission.startswith("drafting.review."):
                    return True
                if role == "drafting_manager" and permission.startswith("drafting."):
                    return True
        return False

    @staticmethod
    def object_id_query(value: str) -> Dict[str, Any]:
        candidates: list[Any] = [str(value)]
        try:
            candidates.append(ObjectId(str(value)))
        except Exception:
            pass
        return {"$in": candidates}
