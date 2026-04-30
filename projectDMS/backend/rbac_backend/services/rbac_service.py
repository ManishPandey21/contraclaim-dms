"""Helpers for resolving notification recipients based on RBAC-style rules."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Set

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from ..models.notification import NotificationContext, NotificationType

# Map various role spellings to unified slugs
_ROLE_ALIASES = {
    "organization-user": "orguser",
    "org-user": "orguser",
    "organization user": "orguser",
    "organizationuser": "orguser",
    "orguser": "orguser",
    "organization-admin": "orgadmin",
    "org-admin": "orgadmin",
    "organization admin": "orgadmin",
    "organizationadmin": "orgadmin",
    "orgadmin": "orgadmin",
    "project-user": "projectuser",
    "project user": "projectuser",
    "projectuser": "projectuser",
    "proj-user": "projectuser",
    "proj user": "projectuser",
    "projuser": "projectuser",
    "project-admin": "projectadmin",
    "project admin": "projectadmin",
    "projectadmin": "projectadmin",
    "proj-admin": "projectadmin",
    "proj admin": "projectadmin",
    "projadmin": "projectadmin",
    "super-admin": "superadmin",
    "super admin": "superadmin",
    "superadministrator": "superadmin",
    "super-user": "superuser",
    "super user": "superuser",
    "superuser": "superuser",
}

_EVENT_ROLE_MAP: dict[NotificationType, Set[str]] = {
    NotificationType.DRAFT_SAVED: {"orgadmin", "orguser", "projectadmin", "projectuser"},
    NotificationType.DRAFT_APPROVED: {"orgadmin", "projectadmin"},
    NotificationType.DRAFT_REJECTED: {"orgadmin", "projectadmin"},
    NotificationType.NEW_UPLOAD: {"orgadmin", "orguser", "projectadmin", "projectuser"},
    NotificationType.BULK_UPLOAD_COMPLETED: {"orgadmin", "orguser", "projectadmin", "projectuser"},
    NotificationType.COMMENT_ADDED: {"orgadmin", "orguser", "projectadmin", "projectuser"},
}

_UPLOAD_EVENTS = {
    NotificationType.NEW_UPLOAD,
    NotificationType.BULK_UPLOAD_COMPLETED,
}


def _normalize_roles(raw_roles: Optional[Sequence[str]]) -> Set[str]:
    roles: Set[str] = set()
    for role in raw_roles or []:
        if not role:
            continue
        slug = str(role).strip().lower()
        roles.add(_ROLE_ALIASES.get(slug, slug))
    return roles


def _coerce_object_id(value: Optional[str]) -> Optional[ObjectId]:
    if not value:
        return None
    try:
        return ObjectId(value)
    except Exception:
        return None


class RBACService:
    """Resolve user ids that should receive notifications for an event."""

    def __init__(self, db: AsyncIOMotorDatabase):
        if db is None:
            raise ValueError("db must not be None")
        self.db = db

    async def get_notifiable_users(
        self,
        event_type: NotificationType,
        resource_id: str,
        resource_type: str,
        context_type: NotificationContext,
        *,
        include_users: Optional[Iterable[str]] = None,
        exclude_users: Optional[Iterable[str]] = None,
    ) -> List[str]:
        """Return user ids that should be notified.

        Args:
            event_type: Type of event being raised.
            resource_id: Identifier for the document/letter.
            resource_type: Collection name ("document", "letter", ...).
            context_type: Scope for RBAC filtering.
            include_users: Optional explicit users to always include (e.g. owner).
            exclude_users: Optional users to filter out (e.g. actor).
        """

        resource = await self.fetch_resource(resource_type, resource_id)
        organization_id = self._resolve_str(resource, "organization_id")
        project_id = self._resolve_str(resource, "project_id")
        if resource_type == "project" and not project_id and resource_id:
            project_id = str(resource_id)

        candidates = await self._query_candidates(
            organization_id=organization_id,
            project_id=project_id,
            context_type=context_type,
        )

        required_roles = _EVENT_ROLE_MAP.get(event_type, set())
        recipients: Set[str] = set()

        for candidate in candidates:
            user_id = str(candidate.get("_id"))
            if not user_id:
                continue

            roles = _normalize_roles(candidate.get("roles"))
            if "superadmin" in roles:
                if event_type not in _UPLOAD_EVENTS:
                    recipients.add(user_id)
                continue

            if required_roles and not (roles & required_roles):
                continue

            recipients.add(user_id)

        # Always include explicit users such as resource owner/assignee
        for user_id in include_users or []:
            if user_id:
                recipients.add(str(user_id))

        for user_id in exclude_users or []:
            if user_id and user_id in recipients:
                recipients.discard(str(user_id))

        return sorted(recipients)

    async def fetch_resource(self, resource_type: str, resource_id: str) -> Optional[dict]:
        collection_name = {
            "document": "documents",
            "letter": "letters",
            "comment": "letters",
            "project": "projects",
        }.get(resource_type, resource_type)
        if not collection_name:
            return None

        oid = _coerce_object_id(resource_id)
        query = {"_id": oid or resource_id}
        try:
            return await self.db[collection_name].find_one(query)
        except Exception:
            return None

    async def _query_candidates(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        context_type: NotificationContext,
    ) -> List[dict]:
        query: dict = {"disabled": {"$ne": True}}
        scope_filters: List[dict] = []

        if organization_id:
            scope_filters.append({"organization_id": organization_id})
            scope_filters.append({"organizations": organization_id})

        if project_id:
            scope_filters.append({"projects": project_id})

        if context_type == NotificationContext.PROJECT and project_id:
            query["$or"] = scope_filters or [{"projects": project_id}]
        elif context_type == NotificationContext.ORGANIZATION and organization_id:
            query["$or"] = scope_filters or [{"organization_id": organization_id}]
        elif scope_filters:
            query["$or"] = scope_filters

        projection = {"_id": 1, "roles": 1}
        cursor = self.db.users.find(query, projection)
        return await cursor.to_list(length=None)

    @staticmethod
    def _resolve_str(resource: Optional[dict], key: str) -> Optional[str]:
        if not resource:
            return None
        value = resource.get(key)
        if value is None:
            return None
        return str(value)
