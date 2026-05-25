# services/permission_service.py

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from uuid import uuid4
import json

from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database
from ..core.permissions import LEGACY_PERMISSION_ALIASES, equivalent_permissions
from ..models.permission import (
    Permission,
    PermissionCreate,
    PermissionUpdate,
    PermissionGroup,
    PermissionCategory,
    PermissionLevel,
    DEFAULT_PERMISSIONS,
)
from ..utils.error_handler import ValidationError
from ..utils.audit_logger import AuditLogger
from .runtime_state import get_runtime_state

logger = logging.getLogger(__name__)

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
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return ROLE_ALIASES.get(raw, raw)

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
        # Backward-compatible permission aliases so legacy role strings still satisfy new router checks.
        self._permission_aliases = {
            **LEGACY_PERMISSION_ALIASES,
            # Organizations
            "organizations:read": ["orgs:view"],
            "organizations:create": ["orgs:create"],
            "organizations:update": ["orgs:edit"],
            "organizations:delete": ["orgs:delete"],
            # Projects
            "projects:read": ["projects:view"],
            "projects:update": ["projects:edit"],
            # Documents / Letters
            "documents:read": ["docs:view", "letters:view"],
            "documents:create": ["docs:create", "letters:create"],
            "documents:update": ["docs:edit", "letters:edit"],
            "documents:delete": ["docs:delete", "letters:delete"],
            "documents:upload": ["docs:upload", "letters:upload"],
            "documents:comment": ["docs:comment", "letters:comment"],
            "documents:share": ["docs:share"],
            "documents:approve": ["docs:approve"],
            "documents:download_all": ["docs:download_all", "docs:download-all"],
            # Tags
            "tags:read": ["docs:view", "documents:read"],
        }
        for canonical, aliases in LEGACY_PERMISSION_ALIASES.items():
            for alias in aliases:
                self._permission_aliases.setdefault(alias, []).append(canonical)
        
    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db
    
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
            result = await db.permissions.insert_one(permission_doc)
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
        """Create default system permissions if they don't exist."""
        try:
            created_count = 0
            
            for perm_data in DEFAULT_PERMISSIONS:
                existing = await self.get_permission_by_name(perm_data["name"])
                if not existing:
                    permission_create = PermissionCreate(**perm_data)
                    await self.create_permission(permission_create, "system")
                    created_count += 1
            
            logger.info(f"Created {created_count} default permissions")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create default permissions: {str(e)}")
            return False

    async def get_user_permissions(self, user_id: str) -> List[Permission]:
        """
        Get all permissions effective for a given user by aggregating across roles.
        Supports wildcard '*' and role permissions as names/IDs.
        """
        try:
            db = await self._get_db()
            # Load user
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id
            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                return []

            role_ids = user_doc.get("roles", []) or []
            if not role_ids:
                return []

            # Collect unique permission identifiers and detect wildcard
            has_wildcard = False
            perm_names: List[str] = []
            perm_ids: List[ObjectId] = []

            for rid in role_ids:
                try:
                    role_qid = ObjectId(rid)
                except Exception:
                    role_qid = rid
                role = await db.roles.find_one({"_id": role_qid})
                if not role:
                    continue
                rperms = role.get("permissions", []) or []
                if "*" in rperms:
                    has_wildcard = True
                for rp in rperms:
                    if isinstance(rp, str):
                        try:
                            perm_ids.append(ObjectId(rp))
                        except Exception:
                            perm_names.append(rp)

            if has_wildcard:
                cursor = db.permissions.find({"is_active": {"$ne": False}})
                docs = await cursor.to_list(length=None)
            else:
                ors = []
                if perm_ids:
                    ors.append({"_id": {"$in": perm_ids}})
                if perm_names:
                    ors.append({"name": {"$in": perm_names}})
                if not ors:
                    return []
                cursor = db.permissions.find({"$or": ors})
                docs = await cursor.to_list(length=None)

            results: List[Permission] = []
            for doc in docs:
                results.append(self._build_permission_from_doc(doc))
            return results
        except Exception as e:
            logger.error(f"Failed to get permissions for user {user_id}: {str(e)}")
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

            # Wildcard: return all active permissions
            if "*" in perms:
                cursor = db.permissions.find({"is_active": {"$ne": False}})
                docs = await cursor.to_list(length=None)
            else:
                object_ids = []
                names = []
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
            for doc in docs:
                permissions.append(self._build_permission_from_doc(doc))
            return permissions

        except Exception as e:
            logger.error(f"Failed to get permissions for role {role_id}: {str(e)}")
            return []

    async def user_has_permission(
        self,
        user_id: str,
        permission_name: str,
        *,
        log: bool = True,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> bool:
        """
        Check whether a user has a specific permission, honoring wildcard roles and raw permission strings.
        """
        permission_key = (permission_name or "").strip()
        if not permission_key:
            return False

        # Build a set of equivalent permission keys (requested key + aliases + canonical names)
        lookup_keys = equivalent_permissions(permission_key) or {permission_key}
        aliases = self._permission_aliases.get(permission_key, [])
        lookup_keys.update(aliases)
        # If caller passes an alias, also add its canonical target for matching
        for canonical, alias_list in self._permission_aliases.items():
            if permission_key in alias_list:
                lookup_keys.add(canonical)
                lookup_keys.update(alias_list)

        granted = False
        redis = None
        cache_key = f"user_perms:{user_id}"
        
        try:
            runtime = get_runtime_state()
            redis = await runtime.get_redis()
            if redis is not None:
                cached_data = await redis.get(cache_key)
                if cached_data:
                    cache_parsed = json.loads(cached_data)
                    raw_permissions = cache_parsed.get("raw_permissions", [])
                    role_names = set(cache_parsed.get("role_names", []))
                    perm_names = set(cache_parsed.get("perm_names", []))
                    
                    if "*" in raw_permissions or permission_key in raw_permissions:
                        granted = True
                    elif lookup_keys and set(raw_permissions) & lookup_keys:
                        granted = True
                    else:
                        granted = bool(lookup_keys & perm_names) or "*" in perm_names

                    if permission_key == "users:create":
                        if role_names & {"superadmin", "orgadmin", "projectadmin"}:
                            granted = True
                        else:
                            granted = False

                    if not granted and permission_key == "users:read":
                        if role_names & {"orgadmin", "orguser", "projectadmin", "projectuser"}:
                            granted = True
                    
                    if log:
                        try:
                            await self.audit_logger.log_permission_check(
                                user_id, permission_key, granted, resource_type=resource_type, resource_id=resource_id
                            )
                        except Exception:
                            pass
                    return granted
        except Exception as e:
            logger.warning(f"Failed to read permission cache for {user_id}: {e}")

        try:
            db = await self._get_db()
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id

            user_doc = await db.users.find_one({"_id": user_query_id})
            if not user_doc:
                granted = False
            else:
                role_ids = user_doc.get("roles", []) or []
                raw_permissions: List[str] = []
                role_names: set[str] = set()
                for rid in role_ids:
                    rid_str = str(rid).lower()
                    if rid_str:
                        role_names.add(rid_str)
                        normalized_rid = _normalize_role_name(rid_str)
                        if normalized_rid:
                            role_names.add(normalized_rid)
                    try:
                        role_qid = ObjectId(rid)
                    except Exception:
                        role_qid = rid
                    role = await db.roles.find_one({"_id": role_qid})
                    if not role:
                        continue
                    role_name = str(role.get("name", "")).lower()
                    if role_name:
                        role_names.add(role_name)
                        normalized_name = _normalize_role_name(role_name)
                        if normalized_name:
                            role_names.add(normalized_name)
                    perms = role.get("permissions", []) or []
                    raw_permissions.extend([str(p) for p in perms if p is not None])

                if "*" in raw_permissions or permission_key in raw_permissions:
                    granted = True
                elif lookup_keys and set(raw_permissions) & lookup_keys:
                    granted = True
                else:
                    user_permissions = await self.get_user_permissions(user_id)
                    perm_names = {p.name for p in user_permissions}
                    granted = bool(lookup_keys & perm_names) or "*" in perm_names

                if permission_key == "users:create":
                    if role_names & {"superadmin", "orgadmin", "projectadmin"}:
                        granted = True
                    else:
                        granted = False

                if not granted and permission_key == "users:read":
                    if role_names & {"orgadmin", "orguser", "projectadmin", "projectuser"}:
                        granted = True
                        
                if redis is not None:
                    try:
                        await redis.set(cache_key, json.dumps({
                            "raw_permissions": raw_permissions,
                            "role_names": list(role_names),
                            "perm_names": list(perm_names)
                        }), ex=3600)
                    except Exception as e:
                        logger.warning(f"Failed to write permission cache for {user_id}: {e}")
        except Exception as exc:
            logger.error(f"Failed permission check for user {user_id}: {exc}")
            granted = False
            if log:
                try:
                    await self.audit_logger.log_permission_check(
                        user_id,
                        permission_key,
                        False,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        error=str(exc),
                    )
                except Exception:
                    pass
            return False

        if log:
            try:
                await self.audit_logger.log_permission_check(
                    user_id,
                    permission_key,
                    granted,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
            except Exception:
                pass
        return granted

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
                role_id_str = str(rid).lower()
                if role_id_str == "superadmin" or role_id_str == "super admin":
                    is_super_admin = True
                    break
                # Also check by querying the role
                try:
                    role_qid = ObjectId(rid)
                except Exception:
                    role_qid = rid
                role = await db.roles.find_one({"_id": role_qid})
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
