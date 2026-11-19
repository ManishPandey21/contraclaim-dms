# services/permission_service.py

import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from uuid import uuid4
import json

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from ..core.config import settings
from ..core.database import get_database
from ..core.permissions import (
    CANONICAL_PERMISSIONS,
    LEGACY_BILLING_VIEW_ALIASES,
    SUPERADMIN_EXCLUDED_PERMISSIONS,
    equivalent_permissions,
    is_billing_permission,
)
from ..core.role_reference import (
    RoleResolution,
    normalize_role_key,
    reference_text,
    resolve_role_reference,
    role_is_active as role_is_active,  # re-exported: the soft-delete contract reads it from here
)
from ..models.permission import (
    Permission,
    PermissionCreate,
    PermissionUpdate,
    PermissionGroup,
    PermissionCategory,
    PermissionLevel,
    DEFAULT_PERMISSIONS,
)
from ..utils.error_handler import BaseDomainError, ValidationError
from ..utils.audit_logger import AuditLogger
from .runtime_state import get_runtime_state
from .role_grants import applicable_names, grant_applies, role_grant_scope, role_is_built_in, user_memberships

logger = logging.getLogger(__name__)

_OBJECT_ID_TEXT = re.compile(r"[0-9a-fA-F]{24}")

ROLE_ALIASES = {
    "organization-user": "orguser",
    "org-user": "orguser",
    "organization user": "orguser",
    "organizationuser": "orguser",
    "orguser": "orguser",
    "organisation-user": "orguser",
    "organisation user": "orguser",
    "organisationuser": "orguser",
    "organization-admin": "orgadmin",
    "org-admin": "orgadmin",
    "organization admin": "orgadmin",
    "organizationadmin": "orgadmin",
    "organization_admin": "orgadmin",
    "organisation-admin": "orgadmin",
    "organisation admin": "orgadmin",
    "organisationadmin": "orgadmin",
    "organisation_admin": "orgadmin",
    "orgadmin": "orgadmin",
    "project-user": "projectuser",
    "project user": "projectuser",
    "projectuser": "projectuser",
    "project-admin": "projectadmin",
    "project admin": "projectadmin",
    "projectadmin": "projectadmin",
    "project_admin": "projectadmin",
    "project administrator": "projectadmin",
    "projectadministrator": "projectadmin",
    "super-admin": "superadmin",
    "super admin": "superadmin",
    "superadministrator": "superadmin",
    "superadmin": "superadmin",
    "super-user": "superuser",
    "super user": "superuser",
    "superuser": "superuser",
}

def _normalize_role_name(value: Any) -> str:
    """Normalise a role DOCUMENT's display name for the name-based grants.

    Never apply it to a `users.roles` reference: a reference contributes exactly the key
    `core.role_reference` gives the principal (`RoleResolution.key`), so a spelling only
    this wider table knows cannot grant by name what the principal does not carry.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return ROLE_ALIASES.get(raw, raw)


#: Moved off `user_perms:{id}` in R-A9B, and off `v2` when legacy role spellings
#: started resolving one hop. Entries under an old key were computed while
#: soft-deleted roles and transitive aliases still granted (v1), or with a legacy
#: spelling's role names but not its document (v2); under a new key no process
#: reads them, and they expire unread on their own TTL.
#: v4 (SEC-1): an entry holds each role's names with the scope they apply in, never
#: a pooled set; a v3 entry (pooled across roles and organisations) is never read.
PERMISSION_CACHE_KEY_PREFIX = "user_perms:v4:"


def permission_cache_key(user_id: Any) -> str:
    """The one spelling of a user's cached-permission key; invalidation must use it too."""
    return f"{PERMISSION_CACHE_KEY_PREFIX}{user_id}"


#: How long a computed grant may be served from `permission_cache_key`.
PERMISSION_CACHE_TTL_SECONDS = 3600
#: A pending authority change must outlive every cache entry that could predate it,
#: so a change whose invalidation failed costs cache bypass, never stale authority.
AUTHORITY_CHANGE_PENDING_TTL_SECONDS = PERMISSION_CACHE_TTL_SECONDS + 300
AUTHORITY_CHANGE_PENDING_PREFIX = "authz_change_pending:"


def authority_change_pending_key(user_id: Any) -> str:
    return f"{AUTHORITY_CHANGE_PENDING_PREFIX}{user_id}"


class AuthorityChangeUnavailableError(BaseDomainError):
    """An authority change could not be announced to the permission cache, so it was not made."""

    def __init__(self, message: str) -> None:
        super().__init__(message, 503)


def _distinct_user_ids(user_ids: Any) -> List[str]:
    return list(dict.fromkeys(str(user_id) for user_id in user_ids or [] if str(user_id)))


async def begin_authority_change(user_ids: Any) -> None:
    """Announce an authority change for these users BEFORE it is written (D4-B).

    From here until `complete_authority_change`, no cached grant is served or
    written for them. Raises `AuthorityChangeUnavailableError` when the
    announcement cannot be made - the caller must then not make the change,
    because nothing could stop an entry computed from the old authority being
    served for the whole cache TTL. A deployment without a runtime Redis has no
    permission cache, so there is nothing to announce.
    """
    ids = _distinct_user_ids(user_ids)
    if not ids:
        return
    runtime = get_runtime_state()
    try:
        redis = await runtime.get_redis()
        if redis is None:
            if getattr(runtime, "redis_url", None):
                raise ConnectionError("runtime Redis is configured but unreachable")
            return
        for user_id in ids:
            await redis.set(authority_change_pending_key(user_id), "1", ex=AUTHORITY_CHANGE_PENDING_TTL_SECONDS)
    except Exception as exc:
        logger.error("Authority change refused: the permission cache could not be told about it: %s", exc)
        raise AuthorityChangeUnavailableError(
            "Authorization cache unavailable; the authority change was not made"
        ) from exc


async def complete_authority_change(user_ids: Any) -> bool:
    """Invalidate after the write: re-authentication marker, cached grant, then the pending marker.

    The pending marker is removed last, and only once the rest succeeded. On any
    failure it stays until it expires, which outlasts every entry that could
    predate the change, so the failure is logged, never silently trusted.
    """
    ids = _distinct_user_ids(user_ids)
    if not ids:
        return True
    runtime = get_runtime_state()
    try:
        redis = await runtime.get_redis()
    except Exception as exc:
        logger.error("Authority change applied; invalidation deferred to the pending marker: %s", exc)
        return False
    if redis is None:
        if getattr(runtime, "redis_url", None):
            logger.error("Authority change applied; Redis unreachable, invalidation deferred to the pending marker")
            return False
        return True
    now_ts = int(datetime.utcnow().timestamp())
    complete = True
    for user_id in ids:
        try:
            await redis.set(f"user_jwt_min_iat:{user_id}", now_ts)
            await redis.delete(permission_cache_key(user_id))
            await redis.delete(authority_change_pending_key(user_id))
        except Exception as exc:
            complete = False
            logger.error(
                "Authority change for user %s applied but invalidation failed; its cached grants stay bypassed "
                "until the pending marker expires: %s",
                user_id,
                exc,
            )
    return complete


async def complete_grant_change(user_ids: Any) -> bool:
    """After a change to what users' roles grant, but not to the users themselves.

    Like ``complete_authority_change`` without the re-authentication marker: a project
    being deactivated or deleted changes where its project roles count, not who the
    members are, so their sessions stay valid. Pair it with ``begin_authority_change``
    before the write (no cached grant is served or written in between).
    """
    ids = _distinct_user_ids(user_ids)
    if not ids:
        return True
    try:
        redis = await get_runtime_state().get_redis()
    except Exception as exc:
        logger.error("Grant change applied; invalidation deferred to the pending marker: %s", exc)
        return False
    if redis is None:
        return True
    complete = True
    for user_id in ids:
        try:
            await redis.delete(permission_cache_key(user_id))
            await redis.delete(authority_change_pending_key(user_id))
        except Exception as exc:
            complete = False
            logger.error("Grant change for user %s applied but its cache was not cleared: %s", user_id, exc)
    return complete


async def project_member_ids(db: Any, project_id: Any) -> List[str]:
    """Users assigned to a project, directly or through an active project membership."""
    text = str(project_id)
    candidates: List[Any] = [text]
    try:
        candidates.append(ObjectId(text))
    except Exception:
        pass
    ids = {str(row["_id"]) for row in await db.users.find({"projects": {"$in": candidates}}, {"_id": 1}).to_list(length=None)}
    rows = await db.project_memberships.find({"project_id": {"$in": candidates}}, {"user_id": 1}).to_list(length=None)
    ids.update(str(row["user_id"]) for row in rows if row.get("user_id"))
    return sorted(ids)


async def cancel_authority_change(user_ids: Any) -> None:
    """The announced change was not made. Best effort: a marker left behind only bypasses the cache."""
    ids = _distinct_user_ids(user_ids)
    if not ids:
        return
    try:
        redis = await get_runtime_state().get_redis()
        if redis is None:
            return
        for user_id in ids:
            await redis.delete(authority_change_pending_key(user_id))
    except Exception as exc:
        logger.warning("Could not clear a pending authority change marker: %s", exc)


def _id_candidates(text: str) -> List[Any]:
    candidates: List[Any] = []
    try:
        candidates.append(ObjectId(text))
    except Exception:
        pass
    candidates.append(text)
    return candidates


async def load_role_resolution(db: Any, reference: Any) -> RoleResolution:
    """Fetch what `resolve_role_reference` needs for ONE reference, then resolve it.

    The direct document first; only when there is none, the canonical document the
    key names and any other document carrying the reference as its exact name (the
    ambiguity check). Store errors propagate: a lookup that cannot run is not "no role".
    """
    text = reference_text(reference)
    if not text:
        return resolve_role_reference(text, [])
    for candidate in _id_candidates(text):
        direct = await db.roles.find_one({"_id": candidate})
        if direct is not None:
            return resolve_role_reference(text, [direct])
    key = normalize_role_key(text)
    if key == text:
        return resolve_role_reference(text, [])
    target = await db.roles.find_one({"_id": key})
    if target is None:
        return resolve_role_reference(text, [])
    documents = [target]
    lookalike = await db.roles.find_one({"name": text, "_id": {"$ne": target.get("_id")}})
    if lookalike is not None:
        documents.append(lookalike)
    return resolve_role_reference(text, documents)


async def fetch_role_documents(db: Any, references: List[Any]) -> List[Dict[str, Any]]:
    """Every role document `resolve_role_reference` may need for these references, in one query."""
    ids: List[Any] = []
    names: List[str] = []
    for reference in references or []:
        text = reference_text(reference)
        if not text:
            continue
        names.append(text)
        for candidate in dict.fromkeys([text, normalize_role_key(text)]):
            ids.extend(_id_candidates(candidate))
    if not names:
        return []
    return await db.roles.find(
        {"$or": [{"_id": {"$in": ids}}, {"name": {"$in": names}}]},
        {"_id": 1, "name": 1, "is_active": 1, "scope": 1},
    ).to_list(length=None)

class PermissionServiceError(Exception):
    """Custom exception for permission service errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class PermissionService:
    """Service for managing permissions."""

    _RESOURCE_CATEGORY_MAP = {
        "users": PermissionCategory.USER_MANAGEMENT,
        "user": PermissionCategory.USER_MANAGEMENT,
        "documents": PermissionCategory.DOCUMENT_MANAGEMENT,
        "document": PermissionCategory.DOCUMENT_MANAGEMENT,
        "letters": PermissionCategory.DOCUMENT_MANAGEMENT,
        "letter_templates": PermissionCategory.DOCUMENT_MANAGEMENT,
        "letter_template": PermissionCategory.DOCUMENT_MANAGEMENT,
        "projects": PermissionCategory.PROJECT_MANAGEMENT,
        "project": PermissionCategory.PROJECT_MANAGEMENT,
        "roles": PermissionCategory.ROLE_MANAGEMENT,
        "role": PermissionCategory.ROLE_MANAGEMENT,
        "emails": PermissionCategory.EMAIL_MANAGEMENT,
        "email": PermissionCategory.EMAIL_MANAGEMENT,
        "system": PermissionCategory.SYSTEM_ADMINISTRATION,
        "audit": PermissionCategory.AUDIT_MANAGEMENT,
        "dms": PermissionCategory.DOCUMENT_MANAGEMENT,
        "drafting": PermissionCategory.DRAFTING_MANAGEMENT,
        "billing": PermissionCategory.BILLING_MANAGEMENT,
        "subscription": PermissionCategory.SUBSCRIPTION_MANAGEMENT,
    }

    _VALID_ACTIONS = {level.value for level in PermissionLevel}
    _ACTION_SYNONYMS = {
        "view": PermissionLevel.READ.value,
        "list": PermissionLevel.READ.value,
        "get": PermissionLevel.READ.value,
        "fetch": PermissionLevel.READ.value,
        "add": PermissionLevel.CREATE.value,
        "create": PermissionLevel.CREATE.value,
        "new": PermissionLevel.CREATE.value,
        "draft": PermissionLevel.CREATE.value,
        "edit": PermissionLevel.UPDATE.value,
        "update": PermissionLevel.UPDATE.value,
        "modify": PermissionLevel.UPDATE.value,
        "change": PermissionLevel.UPDATE.value,
        "delete": PermissionLevel.DELETE.value,
        "remove": PermissionLevel.DELETE.value,
        "destroy": PermissionLevel.DELETE.value,
        "approve": PermissionLevel.ADMIN.value,
        "administer": PermissionLevel.ADMIN.value,
        "manage": PermissionLevel.ADMIN.value,
    }

    def __init__(self):
        self.db = None
        self.audit_logger = AuditLogger()
        # Legacy names resolve through `equivalent_permissions` only (one hop,
        # never through a shared alias). Document access is canonical-only and
        # enforced by PolicyService.

    async def _get_db(self):
        """The database handle: the one injected, else the process database, read each time.

        Not cached: ``require_permission`` keeps one PermissionService per route for the
        life of the process, so a cached handle outlives a database swap (and, under
        tests, the event loop it was created on).
        """
        if self.db is not None:
            return self.db
        return await get_database()

    @staticmethod
    async def _cache_entry_is_current(redis: Any, user_id: Any, entry: Dict[str, Any]) -> bool:
        """Whether a cached grant post-dates the user's last authority change.

        Role mutations set `user_jwt_min_iat:{id}` (whole seconds) and delete the
        cache key, but a check that read the roles just before the mutation can
        write its entry just after the delete. Such an entry is stamped before the
        mutation, so it is refused here instead of being served for the whole TTL.
        The comparison is on whole seconds and refuses ties: an entry computed in
        the same second as the invalidation is recomputed, never trusted.
        """
        computed_at = entry.get("computed_at")
        if not isinstance(computed_at, (int, float)):
            return False
        if await redis.get(authority_change_pending_key(user_id)) is not None:
            return False
        min_iat = await redis.get(f"user_jwt_min_iat:{user_id}")
        if min_iat is None:
            return True
        return int(computed_at) > int(min_iat)

    async def _load_role(self, db: Any, role_ref: Any) -> RoleResolution:
        """Resolve one `users.roles` entry through `core.role_reference`.

        Every resolver goes through here, so wherever authority is computed a
        reference contributes role names only when `keeps_role_key` (a soft-deleted
        role contributes nothing - F-A9A-1) and permissions only from
        `document`, which is set only when the reference resolves to one active
        document: its own `_id`, or a legacy spelling one hop to its canonical role.
        """
        return await load_role_resolution(db, role_ref)

    async def get_permission_by_id(self, permission_id: str) -> Optional[Permission]:
        """Get permission by ID."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(permission_id)
            except:
                query_id = permission_id

            permission_doc = await db.permissions.find_one({"_id": query_id})

            if not permission_doc:
                return None

            # Convert ObjectId to string
            return self._build_permission_from_doc(permission_doc)

        except Exception as e:
            logger.error(f"Failed to get permission {permission_id}: {str(e)}")
            return None

    async def get_permission_by_name(self, name: str) -> Optional[Permission]:
        """Get permission by name."""
        try:
            db = await self._get_db()

            permission_doc = await db.permissions.find_one({"name": name})

            if not permission_doc:
                return None

            # Convert ObjectId to string
            return self._build_permission_from_doc(permission_doc)

        except Exception as e:
            logger.error(f"Failed to get permission by name {name}: {str(e)}")
            return None

    async def get_permissions_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Permission], int]:
        """Get permissions with pagination and filtering."""
        try:
            db = await self._get_db()

            # Build query
            query = {}

            # Search filter
            if filters.get("search"):
                query["$or"] = [
                    {"name": {"$regex": filters["search"], "$options": "i"}},
                    {"description": {"$regex": filters["search"], "$options": "i"}}
                ]

            # Category filter
            if filters.get("category"):
                query["category"] = filters["category"]

            # System permission filter
            if filters.get("is_system") is not None:
                query["is_system"] = filters["is_system"]

            # Active filter (treat documents without the flag as active)
            query["is_active"] = {"$ne": False}

            # Get total count
            total_count = await db.permissions.count_documents(query)

            # Get paginated results
            cursor = db.permissions.find(query).skip(pagination["skip"]).limit(pagination["limit"])
            permission_docs = await cursor.to_list(length=pagination["limit"])

            # Convert to Permission objects
            permissions = []
            for doc in permission_docs:
                permissions.append(self._build_permission_from_doc(doc))

            return permissions, total_count

        except Exception as e:
            logger.error(f"Failed to get permissions: {str(e)}")
            return [], 0

    async def create_permission(
        self,
        permission_data: PermissionCreate,
        created_by: Any
    ) -> Permission:
        """Create a new permission."""
        try:
            db = await self._get_db()

            # Check for duplicate permission name
            existing = await self.get_permission_by_name(permission_data.name)
            if existing:
                raise PermissionServiceError(
                    f"Permission '{permission_data.name}' already exists", 409
                )

            # Create permission document
            permission_doc = {
                "name": permission_data.name,
                "description": permission_data.description or "",
                "category": permission_data.category.value,
                "resource": permission_data.resource,
                "action": permission_data.action.value,
                "is_system": permission_data.is_system or False,
                "is_active": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "created_by": getattr(created_by, 'id', str(created_by))
            }

            # Insert permission
            try:
                result = await db.permissions.insert_one(permission_doc)
            except DuplicateKeyError as exc:
                # The read above and this write are not one operation, so a
                # concurrent creator can land between them. `uq_permissions_name`
                # is what turns that into an error instead of a second row; it
                # must surface as the same 409 the read-path duplicate does, or a
                # caller cannot tell "already exists" from "creation broke".
                raise PermissionServiceError(
                    f"Permission '{permission_data.name}' already exists", 409
                ) from exc
            permission_id = str(result.inserted_id)

            # Return created permission
            return self._build_permission_from_doc(permission_doc)

        except PermissionServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create permission: {str(e)}")
            raise PermissionServiceError("Permission creation failed")

    async def update_permission(
        self,
        permission_id: str,
        update_data: PermissionUpdate,
        updated_by: Any
    ) -> Permission:
        """Update permission."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(permission_id)
            except:
                query_id = permission_id

            # Build update document
            update_doc = {
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(updated_by, 'id', str(updated_by))
            }

            # Add fields that are being updated
            if update_data.description is not None:
                update_doc["description"] = update_data.description
            if update_data.category is not None:
                update_doc["category"] = update_data.category.value
            if update_data.is_active is not None:
                update_doc["is_active"] = update_data.is_active

            # Update permission
            result = await db.permissions.update_one(
                {"_id": query_id},
                {"$set": update_doc}
            )

            if result.matched_count == 0:
                raise PermissionServiceError("Permission not found", 404)

            # Return updated permission
            return await self.get_permission_by_id(permission_id)

        except PermissionServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update permission {permission_id}: {str(e)}")
            raise PermissionServiceError("Permission update failed")

    async def delete_permission(self, permission_id: str, deleted_by: Any) -> bool:
        """Soft delete permission."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(permission_id)
            except:
                query_id = permission_id

            # Check if permission is system permission
            permission = await self.get_permission_by_id(permission_id)
            if permission and permission.is_system:
                raise PermissionServiceError("Cannot delete system permission", 400)

            # Soft delete (mark as inactive)
            result = await db.permissions.update_one(
                {"_id": query_id},
                {
                    "$set": {
                        "is_active": False,
                        "deleted_at": datetime.utcnow(),
                        "deleted_by": getattr(deleted_by, 'id', str(deleted_by))
                    }
                }
            )

            return result.modified_count > 0

        except PermissionServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete permission {permission_id}: {str(e)}")
            return False

    async def get_permissions_by_category(self) -> List[PermissionGroup]:
        """Get permissions grouped by category."""
        try:
            db = await self._get_db()

            # Get all active permissions
            cursor = db.permissions.find({"is_active": {"$ne": False}})
            permission_docs = await cursor.to_list(length=None)

            # Group by category
            category_groups = {}
            for doc in permission_docs:
                permission = self._build_permission_from_doc(doc)

                category = permission.category
                if category not in category_groups:
                    category_groups[category] = []
                category_groups[category].append(permission)

            # Create permission groups
            groups = []
            for category, permissions in category_groups.items():
                groups.append(PermissionGroup(
                    category=category,
                    permissions=permissions,
                    count=len(permissions)
                ))

            return groups

        except Exception as e:
            logger.error(f"Failed to get permissions by category: {str(e)}")
            return []

    async def get_permissions_by_ids(self, permission_ids: List[str]) -> List[Permission]:
        """Get multiple permissions by their IDs."""
        try:
            db = await self._get_db()

            # Convert to ObjectIds if needed
            query_ids = []
            for pid in permission_ids:
                try:
                    query_ids.append(ObjectId(pid))
                except:
                    query_ids.append(pid)

            # Query permissions
            cursor = db.permissions.find({"_id": {"$in": query_ids}})
            permission_docs = await cursor.to_list(length=None)

            # Convert to Permission objects
            permissions = []
            for doc in permission_docs:
                permissions.append(self._build_permission_from_doc(doc))

            return permissions

        except Exception as e:
            logger.error(f"Failed to get permissions by IDs: {str(e)}")
            return []

    async def create_default_permissions(self) -> bool:
        """Create default system permissions if they don't exist.

        Two seeders can run against one database at once - a rolling restart, or
        the web tier and a worker starting together - and the read-then-write this
        used to be has nothing between the two halves. With
        `uq_permissions_name` in place the loser of that race now gets a
        `DuplicateKeyError` instead of writing a second row.

        That error is absorbed *per entry*, and only that error. It means the
        catalogue entry exists, which is the outcome this method wanted; treating
        it as fatal would abort the remaining entries and leave the catalogue
        short of exactly the permissions added most recently, with the whole
        method returning False into a caller that does not check.
        """
        try:
            created_count = 0
            existing_count = 0

            for perm_data in DEFAULT_PERMISSIONS:
                existing = await self.get_permission_by_name(perm_data["name"])
                if existing:
                    existing_count += 1
                    continue
                permission_create = PermissionCreate(**perm_data)
                try:
                    await self.create_permission(permission_create, "system")
                    created_count += 1
                except (DuplicateKeyError, PermissionServiceError) as exc:
                    if isinstance(exc, PermissionServiceError) and getattr(exc, "status_code", None) != 409:
                        raise
                    existing_count += 1

            logger.info(
                "Permission catalog seeded: %s created, %s already present",
                created_count,
                existing_count,
            )
            return True

        except Exception as e:
            logger.error(f"Failed to create default permissions: {str(e)}")
            return False

    async def get_user_permissions(self, user_id: str) -> List[Permission]:
        """The catalogue permissions the user's roles grant where each role applies (SEC-1)."""
        try:
            db = await self._get_db()
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id
            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                return []
            grants, _ = await self.scoped_grants(db, user_doc)
            names = applicable_names(grants, primary_organization_id=self.primary_organisation(user_doc))
            if not names:
                return []
            if "*" in names:
                docs = await db.permissions.find({"is_active": {"$ne": False}}).to_list(length=None)
            else:
                ors: List[Dict[str, Any]] = [{"name": {"$in": sorted(names)}}]
                ids: List[ObjectId] = []
                for name in names:
                    try:
                        ids.append(ObjectId(name))
                    except Exception:
                        pass
                if ids:
                    ors.append({"_id": {"$in": ids}})
                docs = await db.permissions.find({"$or": ors}).to_list(length=None)
            return [self._build_permission_from_doc(doc) for doc in docs]
        except Exception as e:
            logger.error(f"Failed to get permissions for user {user_id}: {str(e)}")
            return []

    async def _catalogue_names(self, db: Any, entries: set[str]) -> set[str]:
        """The permission names `get_user_permissions` reads role `entries` as.

        The same lookup: an entry that parses as an ObjectId is a catalogue id, any
        other is matched against documents' `name`; each document found is named by
        `_build_permission_from_doc`. An entry that is already a canonical name adds
        nothing new.
        """
        perm_ids: List[ObjectId] = []
        perm_names: List[str] = []
        for entry in entries:
            if entry == "*":
                continue
            try:
                perm_ids.append(ObjectId(entry))
            except Exception:
                perm_names.append(entry)
        ors: List[Dict[str, Any]] = []
        if perm_ids:
            ors.append({"_id": {"$in": perm_ids}})
        if perm_names:
            ors.append({"name": {"$in": perm_names}})
        if not ors:
            return set()
        documents = await db.permissions.find({"$or": ors}).to_list(length=None)
        return {self._build_permission_from_doc(document).name for document in documents}

    async def get_effective_permission_names(
        self,
        user_id: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> List[str]:
        """
        Return the current user's effective permission names without requiring
        the caller to read the role catalog.

        This is intentionally name-based so frontend route guards can work even
        when role documents contain permissions that have not yet been seeded in
        the permissions collection. Every name listed passes the evaluator the checks
        use (2026-11-19 reconciliation): ``_decide`` as in ``user_has_permission``,
        and for the explicit-grant permissions the explicit rule of
        ``user_has_explicit_permission``. Each role counts only where it applies
        (SEC-1). A name is listed only if both kinds of gate grant it (``require_permission``
        and ``PolicyService``), so the policy's ``dms.admin`` widening is not listed. Super
        Admin alone is listed as ``["*"]``; the explicit-grant permissions are not covered
        by it, and the client asks the server about those (``POST /permissions/check``).
        """
        try:
            db = await self._get_db()
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id

            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                return []

            grants, role_names = await self.scoped_grants(db, user_doc)
            if "superadmin" in role_names:
                return ["*"]
            if organization_id or project_id:
                organization_id, project_id = await self.resolve_request_scope(db, organization_id, project_id)
            primary = self.primary_organisation(user_doc)
            scope = {"organization_id": organization_id, "project_id": project_id, "primary_organization_id": primary}
            held = applicable_names(grants, **scope)

            def passes(name: str) -> bool:
                return self._decide(grants, role_names, name, self.checks_satisfied_by(name), **scope)

            # Candidates: every check a held name passes (the alias relation is symmetric)
            # and the two role-name rules. The user document's own ``permissions`` field is
            # not a candidate: no evaluator reads it (2026-11-19 reconciliation).
            candidates: set[str] = {"users:read", "users:create"}
            for name in held - {"*"}:
                candidates.update(self.checks_satisfied_by(name))
            if "*" in held:
                # A wildcard passes checks it does not name: list them from the catalogue
                # rather than as "*", which would also claim users:create (built-in
                # administrators only) and the explicit-grant permissions.
                catalogue = set(CANONICAL_PERMISSIONS)
                catalogue.update(
                    str(document.get("name"))
                    for document in await db.permissions.find({}, {"name": 1}).to_list(length=None)
                    if document.get("name")
                )
                for name in list(catalogue):
                    candidates.update(self.checks_satisfied_by(name))
            advertised: set[str] = set()
            for candidate in candidates:
                if _OBJECT_ID_TEXT.fullmatch(candidate):
                    continue  # a catalogue id entry; the name it stands for is a candidate too
                # Both kinds of gate must grant it: ``require_permission`` (this resolver,
                # ``_decide``) and ``PolicyService`` (the explicit rule for the
                # explicit-grant permissions). The policy's administrator widening
                # (``dms.admin`` passing ``dms.*``) is not advertised, because a
                # ``require_permission`` gate does not apply it.
                granted = passes(candidate)
                if granted and candidate in SUPERADMIN_EXCLUDED_PERMISSIONS:
                    lookup_keys = self.checks_satisfied_by(candidate)
                    named = lookup_keys | await self._catalogue_spellings(db, lookup_keys)
                    granted = self._explicitly_held(grants, named, **scope)
                elif granted and settings.BILLING_EXPLICIT_PERMISSIONS and is_billing_permission(candidate):
                    # DEF-4 target policy, as PolicyService applies it: the name itself, never
                    # through the legacy view aliases or a wildcard.
                    lookup_keys = self.checks_satisfied_by(candidate) - LEGACY_BILLING_VIEW_ALIASES
                    named = lookup_keys | await self._catalogue_spellings(db, lookup_keys)
                    granted = self._explicitly_held(grants, named, **scope)
                if granted:
                    advertised.add(candidate)
            return sorted(advertised)
        except Exception as e:
            logger.error(f"Failed to get effective permission names for user {user_id}: {str(e)}")
            return []

    async def build_permission_matrix(self, organization_id: Optional[str], current_user: Any) -> "PermissionMatrix":  # type: ignore[name-defined]
        """
        Build a simple permission matrix placeholder. Returns empty grid for now to avoid import errors.
        """
        try:
            # Lazy import to avoid circulars and only for typing
            from ..models.permission import PermissionMatrix  # type: ignore
            return PermissionMatrix(resources=[], actions=[], roles=[], matrix={})
        except Exception as e:
            logger.error(f"Failed to build permission matrix: {str(e)}")
            # Fallback minimal structure
            from ..models.permission import PermissionMatrix  # type: ignore
            return PermissionMatrix(resources=[], actions=[], roles=[], matrix={})

    async def check_permission_exists(self, permission_name: str) -> bool:
        """Check if a permission exists by name."""
        try:
            permission = await self.get_permission_by_name(permission_name)
            return permission is not None

        except Exception as e:
            logger.error(f"Failed to check permission existence: {str(e)}")
            return False

    async def get_permissions_for_role(self, role_id: str) -> List[Permission]:
        """Get all permissions assigned to a specific role. Supports wildcard '*' and name/ID mix."""
        try:
            db = await self._get_db()

            # Get role
            try:
                role_query_id = ObjectId(role_id)
            except Exception:
                role_query_id = role_id

            role_doc = await db.roles.find_one({"_id": role_query_id})
            if not role_doc:
                return []

            perms = role_doc.get("permissions", []) or []
            if not perms:
                return []

            names: List[str] = []

            # Wildcard: return all active permissions
            if "*" in perms:
                cursor = db.permissions.find({"is_active": {"$ne": False}})
                docs = await cursor.to_list(length=None)
            else:
                object_ids = []
                for p in perms:
                    if isinstance(p, str):
                        try:
                            object_ids.append(ObjectId(p))
                        except Exception:
                            names.append(p)
                ors = []
                if object_ids:
                    ors.append({"_id": {"$in": object_ids}})
                if names:
                    ors.append({"name": {"$in": names}})
                if not ors:
                    return []
                cursor = db.permissions.find({"$or": ors})
                docs = await cursor.to_list(length=None)

            permissions: List[Permission] = []
            found_keys: set[str] = set()
            for doc in docs:
                permission = self._build_permission_from_doc(doc)
                permissions.append(permission)
                found_keys.update(
                    {
                        permission.name,
                        permission.id,
                        str(doc.get("_id") or ""),
                        str(doc.get("id") or ""),
                    }
                )
            for name in names:
                if isinstance(name, str) and name not in found_keys:
                    permissions.append(self._build_permission_from_doc({"_id": name, "name": name}))
            return permissions

        except Exception as e:
            logger.error(f"Failed to get permissions for role {role_id}: {str(e)}")
            return []

    async def _catalogue_spellings(self, db: Any, lookup_keys: set) -> set:
        """Other role entries that name one of `lookup_keys` through its permission document.

        A role can hold a permission by catalogue id (an ObjectId string): the role
        API accepts ids, though the Permissions page saves names. `get_user_permissions`
        reads such an entry, or a document's own `name` where that is a label, as the
        permission the document stands for. Both are the permission held by name on
        the role (4B-49): a grant an administrator made is deliberate whichever
        spelling was stored.
        """
        keys = sorted(lookup_keys)
        cursor = db.permissions.find(
            {"$or": [{"name": {"$in": keys}}, {"id": {"$in": keys}}, {"code": {"$in": keys}}, {"_id": {"$in": keys}}]}
        )
        spellings: set = set()
        for document in await cursor.to_list(length=None):
            if self._build_permission_from_doc(document).name not in lookup_keys:
                continue
            raw_id = document.get("_id")
            if isinstance(raw_id, ObjectId):
                spellings.add(str(raw_id))
            name = document.get("name")
            if isinstance(name, str) and name.strip():
                spellings.add(name.strip())
        spellings.discard("*")
        return spellings

    async def user_has_explicit_permission(
        self,
        user_id: str,
        permission_name: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        exclude: frozenset = frozenset(),
    ) -> bool:
        """Whether one of the user's own roles names ``permission_name`` (or a label spelling of it).

        ``exclude``: held names that must not count even though the alias contract makes them
        equivalent (DEF-4: the legacy view aliases under BILLING_EXPLICIT_PERMISSIONS).

        For permissions that must be granted deliberately (core.permissions
        SUPERADMIN_EXCLUDED_PERMISSIONS). Unlike ``user_has_permission`` it does
        not honour the ``*`` wildcard or the Super Admin role key, does not
        widen through ``dms.admin``, and never reads or writes the permission
        cache, so a revocation takes effect on the next request. A role counts
        only where it applies (SEC-1).
        """
        permission_key = (permission_name or "").strip()
        if not permission_key or not user_id:
            return False
        lookup_keys = (set(equivalent_permissions(permission_key) or set()) | {permission_key}) - set(exclude)
        try:
            db = await self._get_db()
            try:
                user_query_id: Any = ObjectId(user_id)
            except Exception:
                user_query_id = user_id
            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                return False
            named = set(lookup_keys) | await self._catalogue_spellings(db, lookup_keys)
            organization_id, project_id = await self.resolve_request_scope(db, organization_id, project_id)
            grants, _ = await self.scoped_grants(db, user_doc)
            if self._explicitly_held(
                grants, named, organization_id=organization_id, project_id=project_id,
                primary_organization_id=self.primary_organisation(user_doc),
            ):
                return True
        except Exception as exc:
            # Fail closed: an error is never a grant of privileged material.
            logger.error(f"Explicit permission check failed for user {user_id}: {exc}")
            return False
        return False

    @staticmethod
    def primary_organisation(user_doc: Dict[str, Any]) -> Optional[str]:
        """The organisation a permission-only gate is judged in for custom roles (review R-4)."""
        value = user_doc.get("organization_id")
        return str(value) if value else None

    #: Stands for the organisation of a request whose project cannot be resolved, or
    #: whose organisation and project disagree: no custom grant names it.
    UNRESOLVED_ORGANISATION = "\x00unresolved-request-scope"

    async def resolve_request_scope(
        self, db: Any, organization_id: Optional[str], project_id: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """The request's organisation as the stored project states it (SEC-1 boundary).

        No project: the organisation as given. A project: its own organisation, read
        from ``projects`` every time (a project can be re-parented, so it is never
        cached). A project that does not exist, has no organisation, or belongs to an
        organisation other than the one given resolves to ``UNRESOLVED_ORGANISATION``,
        which no custom grant matches. Built-in roles are unaffected (their scope is
        checked by ScopeService, as before).
        """
        org = str(organization_id) if organization_id else None
        if not project_id:
            return org, None
        project = str(project_id)
        candidates: List[Any] = [project]
        try:
            candidates.append(ObjectId(project))
        except Exception:
            pass
        document = await db.projects.find_one({"_id": {"$in": candidates}}, {"organization_id": 1, "organizationId": 1})
        owner = str((document or {}).get("organization_id") or (document or {}).get("organizationId") or "") or None
        if owner is None or (org is not None and org != owner):
            return self.UNRESOLVED_ORGANISATION, project
        return owner, project

    async def scoped_grants(self, db: Any, user_doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], set]:
        """Each of the user's roles as ``{"names", "scope"}`` (SEC-1), and the built-in role names.

        A role contributes only where it was granted (``services.role_grants``): a
        custom role of another organisation, of a project the user is not on, or a
        platform role held by an organisation's user contributes nothing. Names are
        the role's entries plus the catalogue names its ids and labels stand for, so
        an ObjectId entry is the permission it names. Only built-in roles contribute
        role names (the name-based ``users:create`` / ``users:read`` rules): a custom
        role named like a built-in one confers nothing by its name.
        """
        memberships = None
        project_cache: Dict[str, Optional[str]] = {}
        grants: List[Dict[str, Any]] = []
        role_names: set = set()
        for rid in user_doc.get("roles", []) or []:
            resolution = await self._load_role(db, rid)
            if not resolution.keeps_role_key:
                continue
            role = resolution.document
            if not role:
                # A bare built-in key with no document keeps its name, as before.
                if resolution.key:
                    role_names.add(resolution.key)
                continue
            if not role_is_built_in(role) and memberships is None:
                memberships = await user_memberships(db, user_doc)
            applies, scope = await role_grant_scope(db, role, memberships or (set(), set()), project_cache)
            if not applies:
                continue
            if role_is_built_in(role):
                if resolution.key:
                    role_names.add(resolution.key)
                role_name = str(role.get("name", "")).strip().lower()
                if role_name:
                    role_names.add(role_name)
                    normalized_name = _normalize_role_name(role_name)
                    if normalized_name:
                        role_names.add(normalized_name)
            entries = {str(p).strip() for p in (role.get("permissions") or []) if p is not None and str(p).strip()}
            names = set(entries) | await self._catalogue_names(db, entries)
            grants.append({"names": sorted(names), "scope": scope})
        return grants, role_names

    @staticmethod
    def checks_satisfied_by(held: str) -> set:
        """The permission checks a held name passes: itself and its one-hop equivalents."""
        return set(equivalent_permissions(held) or set()) | {held}

    @staticmethod
    def grants_naming(grants: List[Dict[str, Any]], permission: str) -> List[Dict[str, Any]]:
        """The grants that hold ``permission`` by name (its one-hop equivalents count).

        For row visibility (``core.security.permission_scope_query``, JG-7(c)): names
        are compared, so a ``*`` entry never matches and a wildcard role gives no rows.
        """
        wanted = set(equivalent_permissions(permission) or set()) | {permission}
        return [grant for grant in grants if wanted & set(grant.get("names") or [])]

    @staticmethod
    def _explicitly_held(
        grants: List[Dict[str, Any]],
        named: set,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        primary_organization_id: Optional[str],
    ) -> bool:
        """A role that applies here names one of ``named``; the ``*`` wildcard never counts."""
        for grant in grants:
            if not grant_applies(
                grant.get("scope"),
                organization_id=organization_id,
                project_id=project_id,
                primary_organization_id=primary_organization_id,
            ):
                continue
            held = set(grant.get("names") or [])
            held.discard("*")
            if held & named:
                return True
        return False

    @staticmethod
    def _decide(
        grants: List[Dict[str, Any]],
        role_names: set,
        permission_key: str,
        lookup_keys: set,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        primary_organization_id: Optional[str] = None,
    ) -> bool:
        names = applicable_names(
            grants,
            organization_id=organization_id,
            project_id=project_id,
            primary_organization_id=primary_organization_id,
        )
        granted = "*" in names or bool(lookup_keys & names)
        if permission_key == "users:create":
            granted = bool(role_names & {"superadmin", "orgadmin", "projectadmin"})
        if not granted and permission_key == "users:read":
            granted = bool(role_names & {"orgadmin", "orguser", "projectadmin", "projectuser"})
        return granted

    async def user_has_permission(
        self,
        user_id: str,
        permission_name: str,
        *,
        log: bool = True,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """Whether the user holds ``permission_name`` through a role that applies here (SEC-1).

        Honours the ``*`` wildcard, catalogue ids and the one-hop alias contract, per
        role. ``organization_id`` / ``project_id`` are the request's scope when the
        caller knows it (``PolicyService.authorize``): a custom role then counts only
        in its own organisation and project. Without them a custom role still counts
        only for a member of the organisation (and project) it was granted in.
        """
        permission_key = (permission_name or "").strip()
        if not permission_key:
            return False

        # Held names that satisfy this check: the name, its label spellings, and
        # names it declares or that declare it - one hop, never through a shared
        # legacy alias (F-A9A-2).
        lookup_keys = set(equivalent_permissions(permission_key) or set()) | {permission_key}
        try:
            request_org, request_project = await self.resolve_request_scope(
                await self._get_db(), organization_id, project_id
            )
        except Exception as exc:  # an unreadable project is not a grant
            logger.error(f"Request scope unresolvable for user {user_id}: {exc}")
            request_org, request_project = self.UNRESOLVED_ORGANISATION, (str(project_id) if project_id else None)

        async def _logged(granted: bool, error: Optional[str] = None) -> bool:
            if log:
                try:
                    extra = {"error": error} if error else {}
                    await self.audit_logger.log_permission_check(
                        user_id, permission_key, granted, resource_type=resource_type, resource_id=resource_id, **extra
                    )
                except Exception:
                    pass
            return granted

        redis = None
        cache_key = permission_cache_key(user_id)
        try:
            runtime = get_runtime_state()
            redis = await runtime.get_redis()
            if redis is not None:
                cached_data = await redis.get(cache_key)
                cache_parsed = json.loads(cached_data) if cached_data else None
                if cache_parsed is not None and not await self._cache_entry_is_current(redis, user_id, cache_parsed):
                    cache_parsed = None
                if (
                    cache_parsed is not None
                    and isinstance(cache_parsed.get("grants"), list)
                    and "primary_organization_id" in cache_parsed
                ):
                    return await _logged(
                        self._decide(
                            cache_parsed["grants"],
                            set(cache_parsed.get("role_names", [])),
                            permission_key,
                            lookup_keys,
                            organization_id=request_org,
                            project_id=request_project,
                            primary_organization_id=cache_parsed.get("primary_organization_id"),
                        )
                    )
        except Exception as e:
            logger.warning(f"Failed to read permission cache for {user_id}: {e}")

        try:
            # Stamped BEFORE the read: an entry computed from roles read before an
            # invalidation must be recognisably older than that invalidation.
            computed_at = datetime.utcnow().timestamp()
            db = await self._get_db()
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id

            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                return await _logged(False)
            grants, role_names = await self.scoped_grants(db, user_doc)
            primary = self.primary_organisation(user_doc)
            granted = self._decide(
                grants,
                role_names,
                permission_key,
                lookup_keys,
                organization_id=request_org,
                project_id=request_project,
                primary_organization_id=primary,
            )
            if redis is not None:
                try:
                    # Never cache a decision computed while an authority change
                    # is pending: it may have read the authority being replaced.
                    if await redis.get(authority_change_pending_key(user_id)) is None:
                        await redis.set(cache_key, json.dumps({
                            "grants": grants,
                            "role_names": sorted(role_names),
                            "primary_organization_id": primary,
                            "computed_at": computed_at,
                        }), ex=PERMISSION_CACHE_TTL_SECONDS)
                except Exception as e:
                    logger.warning(f"Failed to write permission cache for {user_id}: {e}")
        except Exception as exc:
            logger.error(f"Failed permission check for user {user_id}: {exc}")
            return await _logged(False, str(exc))
        return await _logged(granted)

    async def check_resource_access(
        self,
        user_id: str,
        permission_name: str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """
        Check permission + ownership/membership for resource-level access control.
        Super Admin bypasses membership checks.
        """
        resource_key = str(resource_id)
        has_permission = await self.user_has_permission(
            user_id,
            permission_name,
            log=False,
            resource_type=resource_type,
            resource_id=resource_key,
        )
        if not has_permission:
            await self.audit_logger.log_permission_check(
                user_id,
                permission_name,
                False,
                resource_type=resource_type,
                resource_id=resource_key,
            )
            return False

        try:
            db = await self._get_db()
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id

            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                await self.audit_logger.log_permission_check(
                    user_id,
                    permission_name,
                    False,
                    resource_type=resource_type,
                    resource_id=resource_key,
                    reason="user_not_found",
                )
                return False

            # Check if user is Super Admin - they bypass membership checks
            role_ids = user_doc.get("roles", []) or []
            is_super_admin = False
            for rid in role_ids:
                resolution = await self._load_role(db, rid)
                if not resolution.keeps_role_key:
                    continue
                role = resolution.document
                role_id_str = str(rid).lower()
                if role_id_str == "superadmin" or role_id_str == "super admin":
                    is_super_admin = True
                    break
                # Also check by the role's name
                if role:
                    role_name = str(role.get("name", "")).lower()
                    if role_name == "super admin" or role_name == "superadmin":
                        is_super_admin = True
                        break

            # Super Admin has access to all resources
            if is_super_admin:
                await self.audit_logger.log_permission_check(
                    user_id,
                    permission_name,
                    True,
                    resource_type=resource_type,
                    resource_id=resource_key,
                    reason="super_admin_access",
                )
                return True

            user_orgs = {str(user_doc.get("organization_id"))} if user_doc.get("organization_id") else set()
            user_orgs.update({str(o) for o in user_doc.get("organizations", []) if o})
            user_projects = {str(p) for p in user_doc.get("projects", []) if p}

            def _convert_identifier(value: Any):
                try:
                    return ObjectId(value)
                except Exception:
                    return value

            query_id = _convert_identifier(resource_id)
            membership = False

            if resource_type in ("document", "documents"):
                doc = await db.documents.find_one({"_id": query_id}) or await db.documents.find_one({"_id": str(resource_id)})
                if not doc:
                    await self.audit_logger.log_permission_check(
                        user_id,
                        permission_name,
                        False,
                        resource_type=resource_type,
                        resource_id=resource_key,
                        reason="resource_not_found",
                    )
                    return False
                owner = doc.get("createdBy") or doc.get("created_by") or doc.get("uploadedBy")
                org_id = doc.get("organization_id") or doc.get("organizationId")
                proj_id = doc.get("project_id") or doc.get("projectId")
                membership = (
                    str(owner) == str(user_id)
                    or (org_id and str(org_id) in user_orgs)
                    or (proj_id and str(proj_id) in user_projects)
                )
            elif resource_type in ("project", "projects"):
                proj = await db.projects.find_one({"_id": query_id}) or await db.projects.find_one({"_id": str(resource_id)})
                if not proj:
                    await self.audit_logger.log_permission_check(
                        user_id,
                        permission_name,
                        False,
                        resource_type=resource_type,
                        resource_id=resource_key,
                        reason="resource_not_found",
                    )
                    return False
                owner = proj.get("created_by") or proj.get("owner_id")
                org_id = proj.get("organization_id") or proj.get("organizationId")
                proj_id = proj.get("_id") or proj.get("id")
                membership = (
                    str(owner) == str(user_id)
                    or (org_id and str(org_id) in user_orgs)
                    or (proj_id and str(proj_id) in user_projects)
                )
            elif resource_type in ("organization", "organizations"):
                org = await db.organizations.find_one({"_id": query_id}) or await db.organizations.find_one({"_id": str(resource_id)})
                if not org:
                    await self.audit_logger.log_permission_check(
                        user_id,
                        permission_name,
                        False,
                        resource_type=resource_type,
                        resource_id=resource_key,
                        reason="resource_not_found",
                    )
                    return False
                owner = org.get("owner_id") or org.get("admin_id")
                org_id = org.get("_id") or org.get("id") or resource_id
                membership = str(owner) == str(user_id) or (org_id and str(org_id) in user_orgs)
            else:
                # Unknown resource types: rely on permission alone
                membership = True

            granted = has_permission and membership
            await self.audit_logger.log_permission_check(
                user_id,
                permission_name,
                granted,
                resource_type=resource_type,
                resource_id=resource_key,
            )
            return granted
        except Exception as exc:
            logger.error(f"Resource access check failed for {resource_type}:{resource_id}: {exc}")
            try:
                await self.audit_logger.log_permission_check(
                    user_id,
                    permission_name,
                    False,
                    resource_type=resource_type,
                    resource_id=resource_key,
                    error=str(exc),
                )
            except Exception:
                pass
            return False

    def _coerce_datetime(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value)
            except Exception:
                return datetime.utcnow()
        if isinstance(value, str):
            for parser in (datetime.fromisoformat,):
                try:
                    return parser(value)
                except Exception:
                    continue
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.utcnow()
        return datetime.utcnow()

    def _normalize_action(self, raw_action: Optional[str]) -> str:
        if not raw_action:
            return PermissionLevel.READ.value
        key = raw_action.strip().lower()
        if not key:
            return PermissionLevel.READ.value
        if key in self._VALID_ACTIONS:
            return key
        if key in self._ACTION_SYNONYMS:
            return self._ACTION_SYNONYMS[key]
        return PermissionLevel.READ.value

    def _normalize_category(self, resource: str, category_value: Optional[Any]) -> str:
        if isinstance(category_value, PermissionCategory):
            return category_value.value
        if isinstance(category_value, str):
            try:
                return PermissionCategory(category_value).value
            except ValueError:
                pass
        if resource.startswith("dms."):
            return PermissionCategory.DOCUMENT_MANAGEMENT.value
        if resource.startswith("drafting."):
            return PermissionCategory.DRAFTING_MANAGEMENT.value
        if resource.startswith("billing."):
            return PermissionCategory.BILLING_MANAGEMENT.value
        if resource.startswith("subscription."):
            return PermissionCategory.SUBSCRIPTION_MANAGEMENT.value
        category_enum = self._RESOURCE_CATEGORY_MAP.get(
            resource, PermissionCategory.SYSTEM_ADMINISTRATION
        )
        return category_enum.value

    def _build_permission_from_doc(self, document: Dict[str, Any]) -> Permission:
        """Normalize legacy permission documents into the current schema."""
        doc = dict(document)
        raw_db_id = doc.get("_id")
        legacy_identifier = doc.get("id")
        fallback_identifier = (
            doc.get("code") or doc.get("permission") or doc.get("name") or f"legacy-{uuid4().hex}"
        )
        resolved_id = raw_db_id or legacy_identifier or fallback_identifier
        doc["id"] = str(resolved_id)
        doc.pop("_id", None)

        raw_name = doc.get("name")
        alt_identifier = (
            legacy_identifier if isinstance(legacy_identifier, str) else None
        ) or (doc.get("code") if isinstance(doc.get("code"), str) else None)
        canonical_name = None
        raw_db_identifier = raw_db_id if isinstance(raw_db_id, str) else None
        for candidate in (raw_name, alt_identifier, raw_db_identifier, fallback_identifier):
            if isinstance(candidate, str) and (":" in candidate or "." in candidate):
                canonical_name = candidate.strip()
                break
        if not canonical_name:
            base_value = raw_name or alt_identifier or "system:read"
            base_str = str(base_value).strip()
            base_sanitized = base_str.lower().replace(" ", "_")
            canonical_name = base_sanitized if ":" in base_sanitized or "." in base_sanitized else f"{base_sanitized}:read"
        doc["name"] = canonical_name

        original_label = doc.get("label") or doc.get("display_name")
        if not doc.get("description"):
            if original_label and original_label != canonical_name:
                doc["description"] = original_label
            else:
                doc["description"] = canonical_name.replace(":", " ").title()

        resource_part = doc.get("resource")
        action_part = doc.get("action")
        if not resource_part or not isinstance(resource_part, str):
            resource_part = canonical_name.split(":", 1)[0] if ":" in canonical_name else ".".join(canonical_name.split(".")[:-1])
        resource_part = resource_part.strip().lower() or "system"
        doc["resource"] = resource_part

        if not action_part or not isinstance(action_part, str):
            action_part = canonical_name.split(":", 1)[1] if ":" in canonical_name else canonical_name.split(".")[-1]
        doc["action"] = self._normalize_action(action_part)

        doc["category"] = self._normalize_category(resource_part, doc.get("category"))
        doc["is_system"] = bool(doc.get("is_system", resource_part == "system"))
        is_active_value = doc.get("is_active")
        doc["is_active"] = True if is_active_value is None else bool(is_active_value)

        created_at = doc.get("created_at")
        updated_at = doc.get("updated_at")
        coerced_created = self._coerce_datetime(created_at)
        doc["created_at"] = coerced_created
        doc["updated_at"] = self._coerce_datetime(updated_at) if updated_at else coerced_created

        return Permission(**doc)
