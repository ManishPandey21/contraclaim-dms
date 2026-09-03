# services/role_service.py

import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database
from ..models.role import Role, RoleCreate, RoleUpdate
from ..models.permission import Permission, PermissionCategory, PermissionLevel
from ..utils.error_handler import RoleError, ValidationError
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

SYSTEM_ROLE_NAMES = {"superadmin", "superuser"}
ORG_ADMIN_KEYS = {"orgadmin", "organizationadmin"}
PROJECT_ADMIN_KEYS = {"projectadmin", "projectadministrator"}
RESERVED_ROLE_KEYS = {
    "organizationadmin",
    "organisationadmin",
    "orgadmin",
    "projectadmin",
    "projectadministrator",
}
NON_DELEGABLE_PERMISSION_PREFIXES = (
    "platform.",
    "billing.",
    "subscription.",
    "permissions:",
    "roles:",
)
NON_DELEGABLE_PERMISSIONS = {
    "*",
    "system:admin",
    "dms.admin",
    "drafting.admin",
}

class RoleServiceError(Exception):
    """Custom exception for role service errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class RoleService:
    """Service for managing roles and their permissions."""

    def __init__(self):
        self.db = None
        self.audit_logger = AuditLogger()

    @staticmethod
    def _infer_permission_action(permission_name: str) -> PermissionLevel:
        token = (permission_name or "").replace(":", ".").split(".")[-1].lower()
        if token in {"create", "upload", "add", "submit"}:
            return PermissionLevel.CREATE
        if token in {"update", "edit", "edit_metadata", "link_reference", "manage"}:
            return PermissionLevel.UPDATE if token != "manage" else PermissionLevel.ADMIN
        if token == "delete":
            return PermissionLevel.DELETE
        if token in {"admin", "approve", "export", "generate", "reject", "assess", "extend", "release", "eot_submit", "eot_approve", "achievement"}:
            return PermissionLevel.ADMIN
        return PermissionLevel.READ

    @staticmethod
    def _infer_permission_category(permission_name: str) -> PermissionCategory:
        if permission_name.startswith("dms."):
            return PermissionCategory.DOCUMENT_MANAGEMENT
        if permission_name.startswith("drafting."):
            return PermissionCategory.DRAFTING_MANAGEMENT
        if permission_name.startswith("billing."):
            return PermissionCategory.BILLING_MANAGEMENT
        if permission_name.startswith("subscription."):
            return PermissionCategory.SUBSCRIPTION_MANAGEMENT
        if permission_name.startswith("roles:") or permission_name.startswith("permissions:"):
            return PermissionCategory.ROLE_MANAGEMENT
        if permission_name.startswith("users:"):
            return PermissionCategory.USER_MANAGEMENT
        return PermissionCategory.SYSTEM_ADMINISTRATION

    def _synthetic_permission(self, permission_name: str) -> Permission:
        resource = permission_name
        if "." in permission_name:
            parts = permission_name.split(".")
            resource = ".".join(parts[:-1]) or permission_name
        elif ":" in permission_name:
            resource = permission_name.split(":", 1)[0]
        now = datetime.utcnow()
        return Permission(
            id=permission_name,
            name=permission_name,
            description=permission_name.replace(".", " ").replace(":", " ").replace("_", " ").title(),
            category=self._infer_permission_category(permission_name),
            resource=resource,
            action=self._infer_permission_action(permission_name),
            is_system=False,
            is_active=True,
            created_at=now,
            updated_at=now,
            created_by="system",
        )

    def _extract_role_names(self, current_user: Any) -> set[str]:
        role_names: set[str] = set()
        for role in getattr(current_user, "roles", []) or []:
            role_names.add(str(role).lower())
        return role_names

    def _normalize_role_key(self, value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        return re.sub(r"[^a-z0-9]", "", raw)

    def _get_role_field(self, role: Any, key: str) -> Any:
        if isinstance(role, dict):
            return role.get(key)
        return getattr(role, key, None)

    def _get_role_org_id(self, role: Any) -> Optional[str]:
        return (
            self._get_role_field(role, "organization_id")
            or self._get_role_field(role, "organizationId")
        )

    def _get_role_project_id(self, role: Any) -> Optional[str]:
        return (
            self._get_role_field(role, "project_id")
            or self._get_role_field(role, "projectId")
        )

    def _role_identity_keys(self, role: Any) -> set[str]:
        keys: set[str] = set()
        for field in ("_id", "id", "name"):
            raw = self._get_role_field(role, field)
            normalized = self._normalize_role_key(raw)
            if normalized:
                keys.add(normalized)
        return keys

    def _role_matches_keys(self, role: Any, keys: set[str]) -> bool:
        if not keys:
            return False
        return bool(self._role_identity_keys(role) & keys)

    def _is_superadmin_actor(self, actor: Any) -> bool:
        return "superadmin" in self._extract_role_names(actor)

    def _normalize_permission_list(self, permissions: Optional[List[str]]) -> List[str]:
        seen: set[str] = set()
        normalized: List[str] = []
        for permission in permissions or []:
            value = str(permission or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    def _ensure_permissions_assignable(self, actor: Any, permissions: Optional[List[str]]) -> List[str]:
        normalized = self._normalize_permission_list(permissions)
        if self._is_superadmin_actor(actor):
            return normalized

        for permission in normalized:
            lowered = permission.lower()
            if lowered in NON_DELEGABLE_PERMISSIONS or lowered.startswith(NON_DELEGABLE_PERMISSION_PREFIXES):
                raise RoleServiceError(
                    f"Not authorized to assign privileged permission: {permission}",
                    403,
                )
        return normalized

    def _field_changed(self, existing: Dict[str, Any], field: str, value: Any) -> bool:
        current = existing.get(field)
        if current is None and value in (None, ""):
            return False
        return str(current) != str(value)

    def _collect_user_org_ids(self, current_user: Any) -> set[str]:
        org_ids: set[str] = set()
        if current_user is None:
            return org_ids
        org_id = getattr(current_user, "organization_id", None)
        if org_id:
            org_ids.add(str(org_id))
        for org in getattr(current_user, "organizations", []) or []:
            if org:
                org_ids.add(str(org))
        return org_ids

    def _collect_user_project_ids(self, current_user: Any) -> set[str]:
        project_ids: set[str] = set()
        if current_user is None:
            return project_ids
        for project in getattr(current_user, "projects", []) or []:
            if project:
                project_ids.add(str(project))
        return project_ids

    async def _project_belongs_to_org(self, project_id: Any, org_ids: set[str]) -> bool:
        if not project_id or not org_ids:
            return False
        db = await self._get_db()
        candidates: list[Any] = [str(project_id)]
        try:
            candidates.append(ObjectId(str(project_id)))
        except Exception:
            pass
        project = await db.projects.find_one({"_id": {"$in": candidates}})
        if not project:
            return False
        project_org = project.get("organization_id") or project.get("organizationId")
        if not project_org:
            return False
        return str(project_org) in org_ids

    async def _role_matches_org_scope(self, role: Any, org_ids: set[str]) -> bool:
        if not org_ids:
            return False
        role_org = self._get_role_org_id(role)
        if role_org:
            return str(role_org) in org_ids
        role_project = self._get_role_project_id(role)
        if role_project:
            return await self._project_belongs_to_org(role_project, org_ids)
        return bool(self._get_role_field(role, "is_system"))

    async def _role_matches_project_scope(
        self, role: Any, project_ids: set[str], org_ids: set[str]
    ) -> bool:
        if not project_ids:
            return False
        role_org = self._get_role_org_id(role)
        if role_org and org_ids and str(role_org) not in org_ids:
            return False
        role_project = self._get_role_project_id(role)
        if role_project:
            return str(role_project) in project_ids
        if self._get_role_field(role, "is_system"):
            return True
        if role_org and org_ids and str(role_org) in org_ids:
            return True
        return False

    def can_view_roles(self, current_user: Any) -> bool:
        role_names = self._extract_role_names(current_user)
        return bool(role_names & {"superadmin", "orgadmin", "projectadmin"})

    async def can_view_role(self, current_user: Any, role: Any) -> bool:
        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return True

        role_doc = role if isinstance(role, dict) else {
            "_id": self._get_role_field(role, "id"),
            "id": self._get_role_field(role, "id"),
            "name": self._get_role_field(role, "name"),
            "scope": self._get_role_field(role, "scope"),
            "organization_id": self._get_role_field(role, "organization_id"),
            "project_id": self._get_role_field(role, "project_id"),
            "is_system": self._get_role_field(role, "is_system"),
        }
        scope = self._derive_scope_from_doc(role_doc)
        if self._get_role_field(role_doc, "is_system") or self._role_matches_keys(
            role_doc, SYSTEM_ROLE_NAMES | RESERVED_ROLE_KEYS
        ):
            return False

        if "orgadmin" in role_names:
            if scope not in {"organization", "project"}:
                return False
            if self._role_matches_keys(role_doc, ORG_ADMIN_KEYS):
                return False
            org_ids = self._collect_user_org_ids(current_user)
            return await self._role_matches_org_scope(role_doc, org_ids)

        if "projectadmin" in role_names:
            if scope != "project":
                return False
            if self._role_matches_keys(role_doc, PROJECT_ADMIN_KEYS):
                return False
            org_ids = self._collect_user_org_ids(current_user)
            project_ids = self._collect_user_project_ids(current_user)
            return await self._role_matches_project_scope(role_doc, project_ids, org_ids)

        return False

    async def filter_roles_for_user(
        self, current_user: Any, roles: List[Role]
    ) -> List[Role]:
        filtered: List[Role] = []
        for role in roles or []:
            if await self.can_view_role(current_user, role):
                filtered.append(role)
        return filtered

    def _derive_scope_from_doc(self, doc: Dict[str, Any]) -> str:
        scope = doc.get("scope")
        if isinstance(scope, str) and scope.strip():
            return scope.strip().lower()

        role_id = str(doc.get("_id") or doc.get("id") or "")
        role_name = str(doc.get("name") or "")
        role_key = role_id.lower() or role_name.lower()
        if role_key in SYSTEM_ROLE_NAMES or role_name.lower() in SYSTEM_ROLE_NAMES:
            return "system"

        if doc.get("project_id"):
            return "project"
        if doc.get("organization_id"):
            return "organization"

        if "project" in role_key:
            return "project"
        if "org" in role_key:
            return "organization"
        return "organization"

    def _ensure_role_manageable(self, current_user: Any, role_doc: Dict[str, Any]) -> None:
        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        scope = self._derive_scope_from_doc(role_doc)
        if scope == "system":
            raise RoleServiceError("Not authorized to modify system roles", 403)
        if role_doc.get("is_system") or self._role_matches_keys(role_doc, SYSTEM_ROLE_NAMES | RESERVED_ROLE_KEYS):
            raise RoleServiceError("Not authorized to modify reserved or system roles", 403)

        org_id = str(getattr(current_user, "organization_id", "") or "")
        project_ids = {str(p) for p in (getattr(current_user, "projects", []) or []) if p}

        if "orgadmin" in role_names:
            if scope != "organization":
                raise RoleServiceError("Organization admins may only manage organization roles", 403)
            role_org = role_doc.get("organization_id")
            if not role_org or str(role_org) != org_id:
                raise RoleServiceError("Not authorized to manage roles for another organization", 403)
            return

        if "projectadmin" in role_names:
            if scope != "project":
                raise RoleServiceError("Project admins may only manage project roles", 403)
            role_project = role_doc.get("project_id")
            if not role_project or str(role_project) not in project_ids:
                raise RoleServiceError("Not authorized to manage roles for another project", 403)
            return

        raise RoleServiceError("Not authorized to manage roles", 403)

    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db

    async def _invalidate_role_caches(self, role_id: str) -> None:
        """Invalidate permission caches and JWTs for all users with this role."""
        try:
            from .runtime_state import get_runtime_state
            runtime = get_runtime_state()
            redis = await runtime.get_redis()
            if not redis:
                return

            db = await self._get_db()

            # Find all users with this role ID (handle both string and ObjectId references)
            try:
                role_oid = ObjectId(role_id)
            except:
                role_oid = role_id

            cursor = db.users.find(
                {"roles": {"$in": [role_id, role_oid, str(role_id)]}},
                {"_id": 1}
            )
            users = await cursor.to_list(length=None)

            now_ts = int(datetime.utcnow().timestamp())
            for user in users:
                uid_str = str(user["_id"])
                await redis.delete(f"user_perms:{uid_str}")
                await redis.set(f"user_jwt_min_iat:{uid_str}", now_ts)
        except Exception as e:
            logger.error(f"Failed to invalidate cache for role {role_id}: {e}")

    async def create_role(
        self,
        role_data: RoleCreate,
        created_by: Any
    ) -> Role:
        """Create a new role."""
        try:
            db = await self._get_db()

            # Check for duplicate role name
            existing_role = await db.roles.find_one({"name": role_data.name})
            if existing_role:
                raise RoleServiceError(f"Role '{role_data.name}' already exists", 409)

            role_names = self._extract_role_names(created_by)
            is_superadmin = "superadmin" in role_names
            org_id = getattr(created_by, "organization_id", None)
            project_ids = [str(p) for p in (getattr(created_by, "projects", []) or []) if p]

            role_name_key = self._normalize_role_key(role_data.name)
            if role_name_key in RESERVED_ROLE_KEYS and not is_superadmin:
                raise RoleServiceError("Not authorized to create reserved roles", 403)

            if not is_superadmin and role_data.is_system:
                raise RoleServiceError("Not authorized to create system roles", 403)

            scope = (role_data.scope or "").strip().lower() if role_data.scope else None
            organization_id = role_data.organization_id
            project_id = role_data.project_id

            if is_superadmin:
                if role_data.is_system or scope == "system":
                    scope = "system"
                    role_data.is_system = True
                else:
                    if scope not in {"organization", "project"}:
                        if project_id:
                            scope = "project"
                        else:
                            scope = "organization"
            elif "orgadmin" in role_names:
                if not org_id:
                    raise RoleServiceError("Organization context required for role creation", 403)
                scope = "organization"
                organization_id = str(org_id)
                project_id = None
            elif "projectadmin" in role_names:
                if not org_id or not project_ids:
                    raise RoleServiceError("Project context required for role creation", 403)
                scope = "project"
                organization_id = str(org_id)
                if project_id and str(project_id) in project_ids:
                    project_id = str(project_id)
                else:
                    project_id = project_ids[0]
            else:
                raise RoleServiceError("Not authorized to create roles", 403)

            permissions = self._ensure_permissions_assignable(created_by, role_data.permissions)

            # Create role document
            role_doc = {
                "name": role_data.name,
                "description": role_data.description or "",
                "permissions": permissions,
                "is_system": bool(role_data.is_system) if is_superadmin else False,
                "scope": scope,
                "organization_id": str(organization_id) if organization_id else None,
                "project_id": str(project_id) if project_id else None,
                "is_active": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "created_by": getattr(created_by, 'id', str(created_by))
            }

            # Insert role
            result = await db.roles.insert_one(role_doc)
            role_id = str(result.inserted_id)

            # Return created role
            role_doc["id"] = role_id
            role_doc.pop("_id", None)

            return Role(**role_doc)

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create role: {str(e)}")
            raise RoleServiceError("Role creation failed")

    async def get_role_by_id(self, role_id: str) -> Optional[Role]:
        """Get role by ID."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            role_doc = await db.roles.find_one({"_id": query_id})

            if not role_doc:
                return None

            # Convert ObjectId to string
            role_doc["id"] = str(role_doc["_id"])
            role_doc.pop("_id", None)
            role_doc["scope"] = self._derive_scope_from_doc(role_doc)

            # Ensure timestamps exist for backward compatibility
            if "created_at" not in role_doc:
                role_doc["created_at"] = datetime.utcnow()
            if "updated_at" not in role_doc:
                role_doc["updated_at"] = datetime.utcnow()

            return Role(**role_doc)

        except Exception as e:
            logger.error(f"Failed to get role {role_id}: {str(e)}")
            return None

    async def get_role_by_name(self, name: str) -> Optional[Role]:
        """Get role by name."""
        try:
            db = await self._get_db()

            role_doc = await db.roles.find_one({"name": name})

            if not role_doc:
                return None

            # Convert ObjectId to string
            role_doc["id"] = str(role_doc["_id"])
            role_doc.pop("_id", None)
            role_doc["scope"] = self._derive_scope_from_doc(role_doc)

            # Ensure timestamps exist for backward compatibility
            if "created_at" not in role_doc:
                role_doc["created_at"] = datetime.utcnow()
            if "updated_at" not in role_doc:
                role_doc["updated_at"] = datetime.utcnow()

            return Role(**role_doc)

        except Exception as e:
            logger.error(f"Failed to get role by name {name}: {str(e)}")
            return None

    async def get_roles_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Role], int]:
        """Get roles with pagination and filtering."""
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

            # System role filter
            if filters.get("is_system") is not None:
                query["is_system"] = filters["is_system"]

            scope = filters.get("scope")
            if scope:
                query["scope"] = scope
            scope_in = filters.get("scope_in")
            if scope_in:
                query["scope"] = {"$in": list(scope_in)}
            if filters.get("organization_id"):
                query["organization_id"] = filters["organization_id"]
            if filters.get("project_id"):
                query["project_id"] = filters["project_id"]

            # Active filter (treat missing flag as active)
            query["is_active"] = {"$ne": False}

            # Get total count
            total_count = await db.roles.count_documents(query)

            # Get paginated results
            cursor = db.roles.find(query).skip(pagination["skip"]).limit(pagination["limit"])
            role_docs = await cursor.to_list(length=pagination["limit"])

            # Convert to Role objects
            roles = []
            for doc in role_docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                doc["scope"] = self._derive_scope_from_doc(doc)

                # Ensure timestamps exist for backward compatibility
                if "created_at" not in doc:
                    doc["created_at"] = datetime.utcnow()
                if "updated_at" not in doc:
                    doc["updated_at"] = datetime.utcnow()

                roles.append(Role(**doc))

            return roles, total_count

        except Exception as e:
            logger.error(f"Failed to get roles: {str(e)}")
            return [], 0

    async def update_role(
        self,
        role_id: str,
        update_data: RoleUpdate,
        updated_by: Any
    ) -> Role:
        """Update role."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            existing = await db.roles.find_one({"_id": query_id})
            if not existing:
                raise RoleServiceError("Role not found", 404)

            self._ensure_role_manageable(updated_by, existing)
            if not self._is_superadmin_actor(updated_by):
                if update_data.name is not None and self._normalize_role_key(update_data.name) in RESERVED_ROLE_KEYS:
                    raise RoleServiceError("Not authorized to rename roles to reserved names", 403)
                if update_data.is_system is not None and self._field_changed(existing, "is_system", bool(update_data.is_system)):
                    raise RoleServiceError("Not authorized to change system role status", 403)
                if update_data.scope is not None and update_data.scope != self._derive_scope_from_doc(existing):
                    raise RoleServiceError("Not authorized to change role scope", 403)
                if update_data.organization_id is not None and self._field_changed(
                    existing, "organization_id", update_data.organization_id
                ):
                    raise RoleServiceError("Not authorized to move roles across organizations", 403)
                if update_data.project_id is not None and self._field_changed(existing, "project_id", update_data.project_id):
                    raise RoleServiceError("Not authorized to move roles across projects", 403)

            # Build update document
            update_doc = {
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(updated_by, 'id', str(updated_by))
            }

            # Add fields that are being updated
            if update_data.name is not None:
                duplicate = await db.roles.find_one({"name": update_data.name})
                if duplicate and str(duplicate.get("_id")) != str(query_id):
                    raise RoleServiceError(f"Role '{update_data.name}' already exists", 409)
                update_doc["name"] = update_data.name
            if update_data.description is not None:
                update_doc["description"] = update_data.description
            if update_data.permissions is not None:
                update_doc["permissions"] = self._ensure_permissions_assignable(updated_by, update_data.permissions)
            if update_data.is_system is not None:
                update_doc["is_system"] = update_data.is_system
            if update_data.scope is not None:
                update_doc["scope"] = update_data.scope
            if update_data.organization_id is not None:
                update_doc["organization_id"] = update_data.organization_id
            if update_data.project_id is not None:
                update_doc["project_id"] = update_data.project_id

            # Update role
            result = await db.roles.update_one(
                {"_id": query_id},
                {"$set": update_doc}
            )

            if result.matched_count == 0:
                raise RoleServiceError("Role not found", 404)

            # Return updated role
            updated_role = await self.get_role_by_id(role_id)
            await self._invalidate_role_caches(role_id)
            return updated_role

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update role {role_id}: {str(e)}")
            raise RoleServiceError("Role update failed")

    async def delete_role(self, role_id: str, deleted_by: Any) -> bool:
        """Soft delete role."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            existing = await db.roles.find_one({"_id": query_id})
            if not existing:
                return False

            self._ensure_role_manageable(deleted_by, existing)

            # Soft delete (mark as inactive)
            result = await db.roles.update_one(
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

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete role {role_id}: {str(e)}")
            return False

    async def count_users_with_role(self, role_id: str) -> int:
        """Count users assigned to this role."""
        try:
            db = await self._get_db()

            # Count users with this role
            count = await db.users.count_documents({
                "roles": role_id,
                "is_active": True
            })

            return count

        except Exception as e:
            logger.error(f"Failed to count users with role {role_id}: {str(e)}")
            return 0

    async def get_role_permissions(self, role_id: str) -> List[Permission]:
        """Get all permissions for a role."""
        try:
            db = await self._get_db()

            # Get role
            role = await self.get_role_by_id(role_id)
            if not role:
                return []

            perms_list = role.permissions or []
            if not perms_list:
                return []

            names: List[str] = []

            # Wildcard means all active permissions
            if "*" in perms_list:
                cursor = db.permissions.find({"is_active": True})
                permission_docs = await cursor.to_list(length=None)
            else:
                object_ids = []
                for pid in perms_list:
                    if isinstance(pid, str):
                        try:
                            object_ids.append(ObjectId(pid))
                        except Exception:
                            names.append(pid)
                    else:
                        # ignore non-str unknown types
                        pass

                query = {}
                ors = []
                if object_ids:
                    ors.append({"_id": {"$in": object_ids}})
                if names:
                    ors.append({"name": {"$in": names}})
                if ors:
                    cursor = db.permissions.find({"$or": ors})
                    permission_docs = await cursor.to_list(length=None)
                else:
                    permission_docs = []

            permissions = []
            found_keys: set[str] = set()
            for doc in permission_docs:
                original_id = str(doc.get("_id") or doc.get("id") or "")
                doc["id"] = original_id
                doc.pop("_id", None)
                permission = Permission(**doc)
                permissions.append(permission)
                found_keys.update({permission.name, permission.id, original_id})

            # Catalog drift must not make saved role permissions disappear from
            # the UI. Return synthetic stubs for saved name-based permissions
            # that are not present in the permissions collection.
            for name in names:
                if isinstance(name, str) and name not in found_keys:
                    permissions.append(self._synthetic_permission(name))

            return permissions

        except Exception as e:
            logger.error(f"Failed to get role permissions for {role_id}: {str(e)}")
            return []

    async def update_role_permissions(
        self,
        role_id: str,
        permission_ids: List[str],
        updated_by: Any
    ) -> Role:
        """Update role permissions."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            existing = await db.roles.find_one({"_id": query_id})
            if not existing:
                raise RoleServiceError("Role not found", 404)

            self._ensure_role_manageable(updated_by, existing)
            permission_ids = self._ensure_permissions_assignable(updated_by, permission_ids)

            # Update role permissions
            result = await db.roles.update_one(
                {"_id": query_id},
                {
                    "$set": {
                        "permissions": permission_ids,
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(updated_by, 'id', str(updated_by))
                    }
                }
            )

            if result.matched_count == 0:
                raise RoleServiceError("Role not found", 404)

            # Return updated role
            await self._invalidate_role_caches(role_id)
            return await self.get_role_by_id(role_id)

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update role permissions: {str(e)}")
            raise RoleServiceError("Role permission update failed")

    async def add_permission_to_role(
        self,
        role_id: str,
        permission_id: str,
        updated_by: Any
    ) -> Role:
        """Add a permission to role."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            existing = await db.roles.find_one({"_id": query_id})
            if not existing:
                raise RoleServiceError("Role not found", 404)

            self._ensure_role_manageable(updated_by, existing)
            permission_id = self._ensure_permissions_assignable(updated_by, [permission_id])[0]

            # Add permission to role (if not already present)
            result = await db.roles.update_one(
                {"_id": query_id},
                {
                    "$addToSet": {"permissions": permission_id},
                    "$set": {
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(updated_by, 'id', str(updated_by))
                    }
                }
            )

            if result.matched_count == 0:
                raise RoleServiceError("Role not found", 404)

            # Return updated role
            await self._invalidate_role_caches(role_id)
            return await self.get_role_by_id(role_id)

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to add permission to role: {str(e)}")
            raise RoleServiceError("Add permission to role failed")

    async def remove_permission_from_role(
        self,
        role_id: str,
        permission_id: str,
        updated_by: Any
    ) -> Role:
        """Remove a permission from role."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(role_id)
            except:
                query_id = role_id

            existing = await db.roles.find_one({"_id": query_id})
            if not existing:
                raise RoleServiceError("Role not found", 404)

            self._ensure_role_manageable(updated_by, existing)

            # Remove permission from role
            result = await db.roles.update_one(
                {"_id": query_id},
                {
                    "$pull": {"permissions": permission_id},
                    "$set": {
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(updated_by, 'id', str(updated_by))
                    }
                }
            )

            if result.matched_count == 0:
                raise RoleServiceError("Role not found", 404)

            # Return updated role
            await self._invalidate_role_caches(role_id)
            return await self.get_role_by_id(role_id)

        except RoleServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to remove permission from role: {str(e)}")
            raise RoleServiceError("Remove permission from role failed")

    async def get_system_roles(self) -> List[Role]:
        """Get all system roles."""
        try:
            filters = {"is_system": True}
            pagination = {"skip": 0, "limit": 1000}
            roles, _ = await self.get_roles_paginated(filters, pagination)
            return roles

        except Exception as e:
            logger.error(f"Failed to get system roles: {str(e)}")
            return []

    async def get_all_roles(self) -> List[Role]:
        """Get all active roles."""
        try:
            db = await self._get_db()
            cursor = db.roles.find({"is_active": {"$ne": False}})
            role_docs = await cursor.to_list(length=None)
            roles: List[Role] = []
            for doc in role_docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                doc["scope"] = self._derive_scope_from_doc(doc)

                # Ensure timestamps exist for backward compatibility
                if "created_at" not in doc:
                    doc["created_at"] = datetime.utcnow()
                if "updated_at" not in doc:
                    doc["updated_at"] = datetime.utcnow()

                roles.append(Role(**doc))
            return roles
        except Exception as e:
            logger.error(f"Failed to get all roles: {str(e)}")
            return []

    async def get_roles_with_permission(self, permission_id_or_name: str) -> List[Role]:
        """Find roles that include a given permission (by id or name). Includes wildcard roles."""
        try:
            db = await self._get_db()
            or_terms: List[Dict[str, Any]] = [{"permissions": permission_id_or_name}, {"permissions": "*"}]
            # If looks like ObjectId, include as ObjectId too
            try:
                oid = ObjectId(permission_id_or_name)
                or_terms.append({"permissions": oid})
            except Exception:
                pass

            query = {"$or": or_terms, "is_active": {"$ne": False}}
            cursor = db.roles.find(query)
            role_docs = await cursor.to_list(length=None)
            roles: List[Role] = []
            for doc in role_docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                doc["scope"] = self._derive_scope_from_doc(doc)

                # Ensure timestamps exist for backward compatibility
                if "created_at" not in doc:
                    doc["created_at"] = datetime.utcnow()
                if "updated_at" not in doc:
                    doc["updated_at"] = datetime.utcnow()

                roles.append(Role(**doc))
            return roles
        except Exception as e:
            logger.error(f"Failed to find roles with permission {permission_id_or_name}: {str(e)}")
            return []

    async def assign_permission(self, role_id: str, permission_id: str, updated_by: Any) -> Role:
        """Assign a permission to a role (wrapper)."""
        return await self.add_permission_to_role(role_id, permission_id, updated_by)

    async def create_default_roles(self) -> bool:
        """Create default system roles if they don't exist."""
        try:
            default_roles = [
                {
                    "name": "superadmin",
                    "description": "Super Administrator with full system access",
                    "permissions": ["*"],  # All permissions
                    "is_system": True
                },
                {
                    "name": "orgadmin",
                    "description": "Organization Administrator",
                    "permissions": [
                        "users:read", "users:create", "users:update",
                        "dms.document.view", "dms.document.upload",
                        "dms.document.edit_metadata", "dms.document.delete",
                        "dms.document.download", "dms.document.share",
                        "dms.status.update", "dms.comment.add",
                        "projects:read", "projects:create", "projects:update", "projects:delete"
                    ],
                    "is_system": True
                },
                {
                    "name": "user",
                    "description": "Standard User",
                    "permissions": [
                        "dms.document.view", "dms.document.upload",
                        "dms.document.edit_metadata", "dms.comment.add",
                        "profile:read", "profile:update"
                    ],
                    "is_system": True
                }
            ]

            created_count = 0
            for role_data in default_roles:
                existing = await self.get_role_by_name(role_data["name"])
                if not existing:
                    role_create = RoleCreate(**role_data)
                    await self.create_role(role_create, "system")
                    created_count += 1

            logger.info(f"Created {created_count} default roles")
            return True

        except Exception as e:
            logger.error(f"Failed to create default roles: {str(e)}")
            return False
