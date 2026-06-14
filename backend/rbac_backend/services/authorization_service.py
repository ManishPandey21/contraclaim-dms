import logging
import re
from datetime import date, datetime, time
from typing import Any, Dict, Optional
from bson import ObjectId
from ..utils.error_handler import AuthorizationError
from ..core.security import (
    validate_role_assignment as core_validate_role_assignment,
    authorize_scope,
)
from ..core.database import get_database
from ..services.permission_service import PermissionService
from ..utils.audit_logger import get_audit_logger

logger = logging.getLogger(__name__)

ROLE_ALIASES = {
    "super-admin": "superadmin",
    "super admin": "superadmin",
    "superadministrator": "superadmin",
    "organization-admin": "orgadmin",
    "organization admin": "orgadmin",
    "organizationadmin": "orgadmin",
    "organization-user": "orguser",
    "organization user": "orguser",
    "organizationuser": "orguser",
    "project-admin": "projectadmin",
    "project admin": "projectadmin",
    "project-user": "projectuser",
    "project user": "projectuser",
}


def _normalize_role_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return ROLE_ALIASES.get(text, ROLE_ALIASES.get(re.sub(r"[^a-z0-9]", "", text), text))


class AuthorizationService:
    """Lightweight authorization helper used by routers.

    The implementation intentionally errs on the side of allowing the request so that
    flows continue to work while the real RBAC logic is rebuilt.  All methods accept
    the shapes expected by routers/services and either return sanitized filters or
    raise :class:`AuthorizationError` when the caller explicitly requires it.
    """

    def __init__(self) -> None:
        self.permission_service = PermissionService()
        self.audit_logger = get_audit_logger()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _extract_role_names(self, current_user: Any) -> set[str]:
        if current_user is None:
            return set()
        role_names: set[str] = set()
        roles = getattr(current_user, "roles", []) or []
        for role in roles:
            if isinstance(role, str):
                role_names.add(_normalize_role_name(role))
            elif isinstance(role, dict):
                name = role.get("name") or role.get("role") or role.get("slug")
                if isinstance(name, str):
                    role_names.add(_normalize_role_name(name))
            else:
                name = (
                    getattr(role, "name", None)
                    or getattr(role, "role", None)
                    or getattr(role, "slug", None)
                )
                if isinstance(name, str):
                    role_names.add(_normalize_role_name(name))
        return role_names

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

    # ------------------------------------------------------------------
    # Letters
    # ------------------------------------------------------------------
    async def build_letter_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        filters = dict(filters or {})
        query: Dict[str, Any] = {}
        for key in ("status", "organization_id", "project_id"):
            value = filters.get(key)
            if value:
                query[key] = value
        role_names = self._extract_role_names(current_user)
        if self._is_contract_letter_drafter(role_names):
            user_id = getattr(current_user, "id", None) or getattr(current_user, "_id", None)
            if user_id:
                query["assigned_to"] = str(user_id)
        return query

    async def check_letter_access(
        self, current_user: Any, letter: Any, action: str
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for letter access")

        org_id = getattr(letter, "organization_id", None)
        proj_id = getattr(letter, "project_id", None)
        if org_id is None and isinstance(letter, dict):
            org_id = letter.get("organization_id") or letter.get("organizationId")
        if proj_id is None and isinstance(letter, dict):
            proj_id = letter.get("project_id") or letter.get("projectId")

        authorize_scope(current_user, organization_id=org_id, project_id=proj_id)
        role_names = self._extract_role_names(current_user)
        if self._is_contract_letter_drafter(role_names):
            assigned_to = getattr(letter, "assigned_to", None)
            if assigned_to is None and isinstance(letter, dict):
                assigned_to = letter.get("assigned_to") or letter.get("assignedTo")
            user_id = getattr(current_user, "id", None) or getattr(current_user, "_id", None)
            if str(assigned_to or "") != str(user_id or ""):
                raise AuthorizationError("Contract drafters can access only assigned letters", 403)

        action_normalized = (action or "read").lower()
        perm_map = {
            "read": "documents:read",
            "view": "documents:read",
            "create": "documents:create",
            "write": "documents:create",
            "update": "documents:update",
            "edit": "documents:update",
            "delete": "documents:delete",
            "admin": "documents:approve",
        }
        perm = perm_map.get(action_normalized, "documents:read")
        await self.require_permission(current_user, perm)

    @staticmethod
    def _is_contract_letter_drafter(role_names: set[str]) -> bool:
        drafter_roles = {
            "contraclaim_expert_drafter",
            "contract_letter_drafter",
            "contract letter drafter",
            "letter_drafter",
            "letter drafter",
            "contract_drafter",
            "contract drafter",
        }
        manager_roles = {
            "superadmin",
            "contraclaim_drafting_manager",
            "contract_manager",
            "contract manager",
            "headcontract",
            "contractmgr_org",
            "contractmgr_proj",
        }
        return bool(role_names & drafter_roles) and not bool(role_names & manager_roles)

    async def check_letter_creation_permission(
        self, current_user: Any, letter_data: Any
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for letter creation")
        org_id = getattr(letter_data, "organization_id", None)
        proj_id = getattr(letter_data, "project_id", None)
        if org_id is None and isinstance(letter_data, dict):
            org_id = letter_data.get("organization_id") or letter_data.get("organizationId")
        if proj_id is None and isinstance(letter_data, dict):
            proj_id = letter_data.get("project_id") or letter_data.get("projectId")

        authorize_scope(current_user, organization_id=org_id, project_id=proj_id)
        await self.require_permission(current_user, "documents:create")

    # ------------------------------------------------------------------
    # Roles / permissions
    # ------------------------------------------------------------------
    async def require_role(self, current_user: Any, required_roles: Any) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for role enforcement")

        if isinstance(required_roles, (list, tuple, set)):
            expected = {str(role).lower() for role in required_roles if role}
        else:
            expected = {str(required_roles).lower()}

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return
        if role_names & expected:
            return
        friendly = ", ".join(sorted(expected))
        raise AuthorizationError(f"Required role ({friendly}) not granted", 403)

    async def has_permission(
        self,
        current_user: Any,
        permission: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            if current_user is None:
                return False
            role_names = self._extract_role_names(current_user)
            if "superadmin" in role_names or getattr(current_user, "is_superadmin", False):
                try:
                    await self.audit_logger.log_permission_check(
                        getattr(current_user, "id", None),
                        permission,
                        True,
                        resource_type=context.get("resource_type") if context else None,
                        resource_id=context.get("resource_id") if context else None,
                    )
                except Exception:
                    pass
                return True

            user_id = getattr(current_user, "id", None) or getattr(current_user, "_id", None)
            granted = await self.permission_service.user_has_permission(
                str(user_id),
                permission,
                log=False,
                resource_type=context.get("resource_type") if context else None,
                resource_id=context.get("resource_id") if context else None,
            )
            try:
                await self.audit_logger.log_permission_check(
                    user_id,
                    permission,
                    granted,
                    resource_type=context.get("resource_type") if context else None,
                    resource_id=context.get("resource_id") if context else None,
                )
            except Exception:
                pass
            return granted
        except Exception:
            logger.debug(
                "AuthorizationService.has_permission encountered an error",
                exc_info=True,
            )
            return False

    async def require_permission(
        self,
        current_user: Any,
        permission: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not await self.has_permission(current_user, permission, context):
            raise AuthorizationError(f"Permission '{permission}' required", 403)

    async def check_user_permission(
        self, user_id: str, permission: str, context: Optional[Dict[str, Any]] = None
    ) -> bool:
        return await self.permission_service.user_has_permission(
            user_id,
            permission,
            resource_type=context.get("resource_type") if context else None,
            resource_id=context.get("resource_id") if context else None,
        )

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------
    async def build_organization_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if current_user is None:
            raise AuthorizationError("Authentication required for organization access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)
        sanitized: Dict[str, Any] = {}
        for key in ("search", "name", "city", "state"):
            value = filters.get(key)
            if value:
                sanitized[key] = value

        if "superadmin" in role_names:
            return sanitized

        allowed_ids = self._collect_user_org_ids(current_user)
        if not allowed_ids:
            raise AuthorizationError("No organization scope assigned to the current user")

        sanitized["allowed_ids"] = sorted(allowed_ids)
        return sanitized

    async def check_organization_access(
        self, current_user: Any, organization_id: Optional[str], action: str
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for organization access")
        if not organization_id:
            raise AuthorizationError("Organization identifier is required", 403)

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        allowed_orgs = self._collect_user_org_ids(current_user)
        if str(organization_id) not in allowed_orgs:
            raise AuthorizationError("Access denied to this organization")

        if action and action.lower() in {"create", "update", "delete", "admin", "write"}:
            if action.lower() == "create":
                raise AuthorizationError(
                    f"{action} requires superadmin privileges", 403
                )
            if {"orgadmin"} & role_names:
                return
            raise AuthorizationError(f"{action} requires organization admin privileges", 403)

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    async def build_user_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build an authorized Mongo-style query for listing users.
        Supports filters: search (username/email), role, organization_id, disabled.
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for user access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        query: Dict[str, Any] = {}

        # Apply simple pass-through filters
        disabled = filters.get("disabled")
        if disabled is not None:
            query["disabled"] = bool(disabled)

        role = filters.get("role")
        if role:
            query["roles"] = str(role).lower()

        org_id = filters.get("organization_id")
        if org_id:
            query["organization_id"] = str(org_id)

        search = filters.get("search")
        if search:
            try:
                pattern = str(search)
                query["$or"] = [
                    {"username": {"$regex": pattern, "$options": "i"}},
                    {"email": {"$regex": pattern, "$options": "i"}},
                ]
            except Exception:
                # ignore invalid regex patterns
                pass

        # Scope enforcement
        if "superadmin" in role_names:
            return query

        allowed_orgs = self._collect_user_org_ids(current_user)

        if org_id:
            # When an explicit org filter is provided, validate it
            if allowed_orgs and str(org_id) not in allowed_orgs:
                raise AuthorizationError("Access denied to this organization")
        else:
            # Apply implicit org scoping where applicable
            if allowed_orgs:
                query["organization_id"] = {"$in": sorted(allowed_orgs)}
            else:
                # org/project scoped roles must have organization context
                raise AuthorizationError("No organization scope available for user queries")

        # Project-scoped roles: restrict to own organization for safety
        if {"projectadmin", "projectuser"} & role_names:
            org = getattr(current_user, "organization_id", None)
            if org:
                query["organization_id"] = str(org)
            # Limit to users whose project list intersects current user's projects
            allowed_projects = self._collect_user_project_ids(current_user)
            if allowed_projects:
                query["projects"] = {"$in": sorted(allowed_projects)}

        return query

    async def check_user_access(
        self, current_user: Any, user: Any, action: str
    ) -> None:
        """
        Check access to a specific user resource.
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for user access")

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        action_normalized = (action or "read").lower()
        if action_normalized in {"create", "update", "delete", "assign"}:
            if {"orgadmin", "projectadmin"} & role_names:
                pass
            else:
                raise AuthorizationError("Not authorized to modify users", 403)

        # Target user's organization resolution
        target_org = getattr(user, "organization_id", None)
        if target_org is None and isinstance(user, dict):
            target_org = user.get("organization_id")

        allowed_orgs = self._collect_user_org_ids(current_user)

        if {"orgadmin", "orguser"} & role_names:
            if target_org is None or str(target_org) != str(getattr(current_user, "organization_id", None)):
                raise AuthorizationError("Access denied to this user")
            return

        if {"projectadmin", "projectuser"} & role_names:
            if target_org is None or str(target_org) != str(getattr(current_user, "organization_id", None)):
                raise AuthorizationError("Access denied to this user")
            # Enforce project overlap
            allowed_projects = self._collect_user_project_ids(current_user)
            target_projects = set()
            if isinstance(user, dict):
                target_projects.update({str(p) for p in user.get("projects", []) if p})
            else:
                target_projects.update({str(p) for p in getattr(user, "projects", []) or [] if p})
            if allowed_projects and not (allowed_projects & target_projects):
                raise AuthorizationError("Access denied to this user")
            return

        # Permissive default
        return

    async def validate_role_assignment(
        self, current_user: Any, roles: Optional[Any]
    ) -> None:
        """
        Wrapper around core.security.validate_role_assignment used by routers.
        """
        try:
            roles_list = list(roles or [])
        except Exception:
            roles_list = []
        try:
            core_validate_role_assignment(current_user, roles_list)
        except Exception as e:
            status_code = getattr(e, "status_code", 403)
            detail = getattr(e, "detail", str(e))
            raise AuthorizationError(detail, status_code)

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    async def check_document_access(
        self,
        current_user: Any,
        organization_id: Optional[str],
        project_id: Optional[str],
        action: str,
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for document access")

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        action_normalized = (action or "read").lower()
        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        if organization_id and (organization_id not in allowed_orgs):
            raise AuthorizationError("Access denied to documents within this organization")

        if project_id and (
            project_id not in allowed_projects
            and not ({"orgadmin", "orguser"} & role_names)
        ):
            raise AuthorizationError("Access denied to documents within this project")

        if action_normalized in {"create", "update", "delete", "write", "admin"}:
            if {"orgadmin", "projectadmin"} & role_names:
                return
            raise AuthorizationError(f"Access denied to {action_normalized} document", 403)

    async def _expand_lookup_filter_values(
        self, collection_name: str, values: Any
    ) -> list[str]:
        raw_values = (
            list(values)
            if isinstance(values, (list, tuple, set))
            else [values]
        )
        normalized = {
            str(value).strip()
            for value in raw_values
            if value is not None and str(value).strip()
        }
        if not normalized:
            return []

        try:
            db = await get_database()
            collection = getattr(db, collection_name)
            object_ids = []
            names = []
            for value in normalized:
                try:
                    object_ids.append(ObjectId(value))
                except Exception:
                    names.append(value)

            if object_ids:
                async for record in collection.find({"_id": {"$in": object_ids}}):
                    record_id = record.get("_id")
                    record_name = record.get("name")
                    if record_id is not None:
                        normalized.add(str(record_id))
                    if record_name:
                        normalized.add(str(record_name))

            if names:
                async for record in collection.find({"name": {"$in": names}}):
                    record_id = record.get("_id")
                    record_name = record.get("name")
                    if record_id is not None:
                        normalized.add(str(record_id))
                    if record_name:
                        normalized.add(str(record_name))
        except Exception:
            logger.debug(
                "Unable to expand %s filter values for document query",
                collection_name,
                exc_info=True,
            )

        return sorted(normalized)

    def _coerce_document_date_boundary(
        self, value: Any, *, end_of_day: bool = False
    ) -> Optional[datetime]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time.max if end_of_day else time.min)
        try:
            text = str(value).strip()
            if not text:
                return None
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.time() == time.min:
                return datetime.combine(
                    parsed.date(), time.max if end_of_day else time.min
                )
            return parsed
        except Exception:
            logger.debug("Ignoring invalid document date filter value: %r", value)
            return None

    async def build_document_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if current_user is None:
            raise AuthorizationError("Authentication required for document access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)
        query: Dict[str, Any] = {}

        # Simple pass-through fields
        for key in ("organization_id", "project_id", "uploadType", "status"):
            value = filters.get(key)
            if value:
                query[key] = value

        search = str(filters.get("search") or "").strip()
        if search:
            pattern = re.escape(search)
            query["$or"] = [
                {"filename": {"$regex": pattern, "$options": "i"}},
                {"subject": {"$regex": pattern, "$options": "i"}},
                {"letterNo": {"$regex": pattern, "$options": "i"}},
                {"from": {"$regex": pattern, "$options": "i"}},
                {"from_": {"$regex": pattern, "$options": "i"}},
                {"to": {"$regex": pattern, "$options": "i"}},
            ]

        date_query: Dict[str, datetime] = {}
        date_from = self._coerce_document_date_boundary(filters.get("date_from"))
        date_to = self._coerce_document_date_boundary(
            filters.get("date_to"), end_of_day=True
        )
        if date_from:
            date_query["$gte"] = date_from
        if date_to:
            date_query["$lte"] = date_to
        if date_query:
            query["date"] = date_query

        # Support search by letter number (exact or list)
        letter_no = filters.get("letterNo")
        if letter_no:
            if isinstance(letter_no, (list, tuple, set)):
                values = [str(v) for v in letter_no if v]
                if values:
                    query["letterNo"] = {"$in": values}
            else:
                try:
                    # Case-insensitive exact match
                    pattern = f"^{re.escape(str(letter_no))}$"
                    query["letterNo"] = {"$regex": pattern, "$options": "i"}
                except Exception:
                    query["letterNo"] = str(letter_no)

        # Subject contains (case-insensitive)
        subject = filters.get("subject")
        if subject:
            try:
                query["subject"] = {"$regex": str(subject), "$options": "i"}
            except Exception:
                query["subject"] = str(subject)

        # Tags and SubTags inclusion
        tags = filters.get("tags")
        if tags:
            expanded_tags = await self._expand_lookup_filter_values("tags", tags)
            if expanded_tags:
                query["tags"] = {"$in": expanded_tags}
        sub_tags = filters.get("subTags")
        if sub_tags:
            expanded_sub_tags = await self._expand_lookup_filter_values(
                "subtags", sub_tags
            )
            if expanded_sub_tags:
                query["subTags"] = {"$in": expanded_sub_tags}

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        if "superadmin" not in role_names:
            if query.get("organization_id"):
                if query["organization_id"] not in allowed_orgs:
                    raise AuthorizationError(
                        "Access denied to documents for this organization"
                    )
            elif allowed_orgs:
                query["organization_id"] = {"$in": sorted(allowed_orgs)}
            else:
                raise AuthorizationError(
                    "No organization scope available for document queries"
                )

            project_scoped_roles = {"projectadmin", "projectuser"}
            if query.get("project_id"):
                if query["project_id"] not in allowed_projects and not (
                    {"orgadmin", "orguser"} & role_names
                ):
                    raise AuthorizationError(
                        "Access denied to documents for this project"
                    )
            elif project_scoped_roles & role_names:
                if not allowed_projects:
                    raise AuthorizationError(
                        "No project scope assigned to the current user"
                    )
                query["project_id"] = {"$in": sorted(allowed_projects)}

        return query

    async def build_authorized_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return await self.build_document_query(current_user, filters)

    # ------------------------------------------------------------------
    # Parties / projects convenience stubs
    # ------------------------------------------------------------------
    async def check_party_access(
        self, current_user: Any, party: Any, action: str
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for party access")
        org_id = getattr(party, "organization_id", None)
        if org_id is None and isinstance(party, dict):
            org_id = party.get("organization_id") or party.get("organizationId")
        proj_ids = getattr(party, "projects", None)
        if proj_ids is None and isinstance(party, dict):
            proj_ids = party.get("projects")

        # If a specific project is referenced, enforce it, otherwise fall back to org scope
        if proj_ids:
            target_proj = None
            if isinstance(proj_ids, list) and proj_ids:
                target_proj = proj_ids[0]
            authorize_scope(current_user, organization_id=org_id, project_id=target_proj)
        else:
            authorize_scope(current_user, organization_id=org_id, project_id=None)

        action_normalized = (action or "read").lower()
        perm = {
            "read": "parties:read",
            "view": "parties:read",
            "create": "parties:create",
            "write": "parties:create",
            "update": "parties:update",
            "edit": "parties:update",
            "delete": "parties:delete",
            "admin": "parties:delete",
        }.get(action_normalized, "parties:read")
        await self.require_permission(current_user, perm)

    async def check_project_access(
        self, current_user: Any, project: Any, action: str
    ) -> None:
        if current_user is None:
            raise AuthorizationError("Authentication required for project access")
        org_id = getattr(project, "organization_id", None)
        if org_id is None and isinstance(project, dict):
            org_id = project.get("organization_id") or project.get("organizationId")
        proj_id = getattr(project, "_id", None) or getattr(project, "id", None)
        if proj_id is None and isinstance(project, dict):
            proj_id = project.get("_id") or project.get("id")

        authorize_scope(current_user, organization_id=org_id, project_id=proj_id)

        action_normalized = (action or "read").lower()
        perm = {
            "read": "projects:read",
            "view": "projects:read",
            "create": "projects:create",
            "write": "projects:create",
            "update": "projects:update",
            "edit": "projects:update",
            "delete": "projects:delete",
            "admin": "projects:delete",
        }.get(action_normalized, "projects:read")
        await self.require_permission(current_user, perm)

    # ------------------------------------------------------------------
    # Email Groups
    # ------------------------------------------------------------------
    async def build_email_group_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Build an authorized Mongo-style query for listing email groups.

        This mirrors the permissive behavior used elsewhere:
        - superadmin: unrestricted
        - org/project scoped roles: constrained to assigned scopes unless an allowed id is provided
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for email group access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        query: Dict[str, Any] = {}
        # pass-through filters when provided
        for key in ("organization_id", "project_id", "search", "name"):
            val = filters.get(key)
            if val:
                query[key] = val

        if "superadmin" in role_names:
            return query

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        # constrain organization if not specified
        org_id = query.get("organization_id")
        if org_id:
            if allowed_orgs and str(org_id) not in allowed_orgs:
                raise AuthorizationError("Access denied to this organization")
        elif allowed_orgs:
            query["organization_id"] = {"$in": sorted(allowed_orgs)}

        # constrain project if not specified
        proj_id = query.get("project_id")
        if proj_id:
            if (
                allowed_projects
                and str(proj_id) not in allowed_projects
                and not ({"orgadmin", "orguser"} & role_names)
            ):
                raise AuthorizationError("Access denied to this project")
        elif allowed_projects and ({"projectadmin", "projectuser"} & role_names):
            query["project_id"] = {"$in": sorted(allowed_projects)}

        return query

    async def check_email_group_access(
        self, current_user: Any, group: Any, action: str
    ) -> None:
        """Check access for a specific email group instance."""
        if current_user is None:
            raise AuthorizationError("Authentication required for email group access")

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        # group may be a Pydantic model or a dict
        org_id = getattr(group, "organization_id", None)
        if org_id is None and isinstance(group, dict):
            org_id = group.get("organization_id")

        proj_id = getattr(group, "project_id", None)
        if proj_id is None and isinstance(group, dict):
            proj_id = group.get("project_id")

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        if org_id and (str(org_id) not in allowed_orgs):
            raise AuthorizationError("Access denied to this organization")

        if proj_id and (str(proj_id) not in allowed_projects) and not (
            {"orgadmin", "orguser"} & role_names
        ):
            raise AuthorizationError("Access denied to this project")

        # write/admin actions require stronger roles; permissive otherwise
        if action and action.lower() in {"create", "update", "delete", "admin", "write"}:
            if {"orgadmin", "projectadmin"} & role_names:
                return
            raise AuthorizationError(f"{action} requires elevated privileges", 403)

    # ------------------------------------------------------------------
    # Parties
    # ------------------------------------------------------------------
    async def build_party_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build an authorized Mongo-style query for listing parties.
        Supported filters: type, project_id, search
        Scope rules (permissive defaults similar to other builders):
          - superadmin: unrestricted
          - orgadmin/orguser: constrained to user's organization
          - projectadmin/projectuser: constrained to user's organization and projects
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for party access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        and_clauses: list[Dict[str, Any]] = []

        # type filter (store values as strings)
        ptype = filters.get("type")
        if ptype:
            try:
                # Enum or str
                type_val = getattr(ptype, "value", str(ptype))
            except Exception:
                type_val = str(ptype)
            and_clauses.append({"type": type_val})

        # project filter
        proj_id = filters.get("project_id")
        if proj_id:
            and_clauses.append({"projects": {"$in": [str(proj_id)]}})

        # search filter (name/email/phone)
        search = filters.get("search")
        if search:
            pattern = str(search)
            and_clauses.append(
                {
                    "$or": [
                        {"name": {"$regex": pattern, "$options": "i"}},
                        {"contactEmail": {"$regex": pattern, "$options": "i"}},
                        {"contact_email": {"$regex": pattern, "$options": "i"}},
                        {"contactPhone": {"$regex": pattern, "$options": "i"}},
                        {"contact_phone": {"$regex": pattern, "$options": "i"}},
                    ]
                }
            )

        # Scope enforcement
        if "superadmin" in role_names:
            # no additional constraints
            pass
        else:
            allowed_orgs = self._collect_user_org_ids(current_user)
            allowed_projects = self._collect_user_project_ids(current_user)

            # organization constraint
            if allowed_orgs:
                and_clauses.append(
                    {
                        "$or": [
                            {"organization_id": {"$in": sorted(allowed_orgs)}},
                            {"organizationId": {"$in": sorted(allowed_orgs)}},
                        ]
                    }
                )
            else:
                if {"orgadmin", "orguser", "projectadmin", "projectuser"} & role_names:
                    # constrained roles must have an org context
                    org_id = getattr(current_user, "organization_id", None)
                    if not org_id:
                        raise AuthorizationError("No organization scope available for party queries")
                    and_clauses.append(
                        {
                            "$or": [
                                {"organization_id": str(org_id)},
                                {"organizationId": str(org_id)},
                            ]
                        }
                    )

            # project-scoped roles: optionally constrain by assigned projects
            if {"projectadmin", "projectuser"} & role_names:
                if proj_id:
                    # if explicit project filter provided, ensure it's allowed
                    if allowed_projects and str(proj_id) not in allowed_projects:
                        raise AuthorizationError("Access denied to this project")
                elif allowed_projects:
                    and_clauses.append({"projects": {"$in": sorted(allowed_projects)}})

        if not and_clauses:
            return {}
        if len(and_clauses) == 1:
            return and_clauses[0]
        return {"$and": and_clauses}

    async def build_external_party_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build query for external parties (not tied to specific organizations).
        Supported filters: search, party_type
        Enforces role-based scope similarly but targets records with no organization.
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for party access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        and_clauses: list[Dict[str, Any]] = []

        # Party type (if provided)
        ptype = filters.get("party_type")
        if ptype:
            try:
                type_val = getattr(ptype, "value", str(ptype))
            except Exception:
                type_val = str(ptype)
            and_clauses.append({"type": type_val})

        # Free-text search
        search = filters.get("search")
        if search:
            pattern = str(search)
            and_clauses.append(
                {
                    "$or": [
                        {"name": {"$regex": pattern, "$options": "i"}},
                        {"contactEmail": {"$regex": pattern, "$options": "i"}},
                        {"contact_email": {"$regex": pattern, "$options": "i"}},
                    ]
                }
            )

        # External means no organization id
        and_clauses.append(
            {
                "$or": [
                    {"organization_id": {"$in": [None], "$exists": True}},
                    {"organizationId": {"$in": [None], "$exists": True}},
                    {"organization_id": {"$exists": False}},
                    {"organizationId": {"$exists": False}},
                ]
            }
        )

        # Scope: for external parties, keep permissive but still allow superadmin bypass
        if "superadmin" not in role_names:
            # For org/project scoped roles, we still allow reading external records
            # No additional constraints necessary; permission checks handled elsewhere.
            pass

        if not and_clauses:
            return {}
        if len(and_clauses) == 1:
            return and_clauses[0]
        return {"$and": and_clauses}

    # ------------------------------------------------------------------
    # Letter Templates
    # ------------------------------------------------------------------
    async def build_letter_template_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build an authorized query for letter templates with scoped visibility.

        Visibility rules:
          - superadmin: can see global, organization, and project templates
          - orgadmin/orguser: can see organization and project templates within their org(s)
          - projectadmin/projectuser: can see project templates within assigned projects
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for template access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        query: Dict[str, Any] = {}

        for key in ("status", "category", "visibility"):
            value = filters.get(key)
            if value:
                query[key] = value

        if filters.get("search"):
            query["search"] = filters["search"]

        org_id = filters.get("organization_id")
        proj_id = filters.get("project_id")

        if "superadmin" in role_names:
            if org_id:
                query["organization_id"] = str(org_id)
            if proj_id:
                query["project_id"] = str(proj_id)
            return query

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        scope_conditions = []

        if {"orgadmin", "orguser"} & role_names:
            if allowed_orgs:
                scope_conditions.append(
                    {
                        "visibility": "organization",
                        "organization_id": {"$in": sorted(allowed_orgs)},
                    }
                )
                scope_conditions.append(
                    {
                        "visibility": "project",
                        "organization_id": {"$in": sorted(allowed_orgs)},
                    }
                )
            else:
                org = getattr(current_user, "organization_id", None)
                if org:
                    scope_conditions.append(
                        {"visibility": "organization", "organization_id": str(org)}
                    )
                    scope_conditions.append(
                        {"visibility": "project", "organization_id": str(org)}
                    )

        if {"projectadmin", "projectuser"} & role_names:
            if allowed_projects:
                scope_conditions.append(
                    {
                        "visibility": "project",
                        "project_id": {"$in": sorted(allowed_projects)},
                    }
                )
            else:
                raise AuthorizationError("No project scope assigned to the current user")

        if scope_conditions:
            query["$or"] = scope_conditions
        else:
            query["visibility"] = "__none__"

        if org_id:
            if allowed_orgs and str(org_id) not in allowed_orgs:
                raise AuthorizationError("Access denied to this organization")
            query["organization_id"] = str(org_id)

        if proj_id:
            if allowed_projects and str(proj_id) not in allowed_projects and not (
                {"orgadmin", "orguser"} & role_names
            ):
                raise AuthorizationError("Access denied to this project")
            query["project_id"] = str(proj_id)

        return query

    async def check_letter_template_access(
        self, current_user: Any, template: Any, action: str
    ) -> None:
        """
        Check access for a specific template instance.

        - global: superadmin only
        - organization: orgadmin/orguser within org (writes require orgadmin)
        - project: project roles within project (writes allowed), orgadmin within org (writes allowed)
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for template access")

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            return

        org_id = getattr(template, "organization_id", None)
        if org_id is None and isinstance(template, dict):
            org_id = template.get("organization_id") or template.get("organizationId")

        proj_id = getattr(template, "project_id", None)
        if proj_id is None and isinstance(template, dict):
            proj_id = template.get("project_id") or template.get("projectId")

        visibility = getattr(template, "visibility", None)
        if visibility is None and isinstance(template, dict):
            visibility = template.get("visibility")

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        action_normalized = (action or "read").lower()
        write_actions = {"create", "update", "delete", "admin", "write"}

        if visibility == "global" or visibility is None:
            raise AuthorizationError("Access denied to global templates")

        if visibility == "organization":
            if not org_id or str(org_id) not in allowed_orgs:
                raise AuthorizationError("Access denied to this organization")
            if action_normalized in write_actions:
                if "orgadmin" in role_names:
                    return
                raise AuthorizationError("Organization admin privileges required", 403)
            if {"orgadmin", "orguser"} & role_names:
                return
            raise AuthorizationError("Access denied to organization templates", 403)

        if visibility == "project":
            if proj_id and str(proj_id) in allowed_projects and (
                {"projectadmin", "projectuser"} & role_names
            ):
                return

            if org_id and str(org_id) in allowed_orgs:
                if action_normalized in write_actions:
                    if "orgadmin" in role_names:
                        return
                    raise AuthorizationError("Organization admin privileges required", 403)
                if {"orgadmin", "orguser"} & role_names:
                    return

            raise AuthorizationError("Access denied to this project")

        raise AuthorizationError("Access denied")

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------
    async def build_tag_query(
        self, current_user: Any, filters: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build an authorized Mongo-style query for listing tags with visibility rules.

        Supported filters:
          - organization_id
          - project_id
          - color
          - search (applies case-insensitive regex across name and description)
        Visibility rules:
          - superadmin: can see all tags (global, organization, project)
          - orgadmin/orguser: can see global and organization tags for their org(s)
          - projectadmin/projectuser: can see global, organization tags for their org(s), and project tags for their projects
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for tag access")

        filters = dict(filters or {})
        role_names = self._extract_role_names(current_user)

        query: Dict[str, Any] = {}

        # Pass-through filters when provided
        org_id = filters.get("organization_id")
        proj_id = filters.get("project_id")
        color = filters.get("color")
        search = filters.get("search")

        if color:
            query["color"] = color

        search_clause: Optional[Dict[str, Any]] = None
        search_text = str(search or "").strip()
        if search_text:
            pattern = re.escape(search_text)
            search_clause = {
                "$or": [
                    {"name": {"$regex": pattern, "$options": "i"}},
                    {"description": {"$regex": pattern, "$options": "i"}},
                ]
            }

        # Visibility and scope enforcement
        if "superadmin" in role_names:
            # superadmin can see all tags, apply org/project filter if provided
            if org_id:
                query["organization_id"] = str(org_id)
            if proj_id:
                query["project_id"] = str(proj_id)
            if search_clause:
                query["$and"] = [search_clause]
            return query

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        visibility_conditions = []

        # Global tags are visible to all roles
        visibility_conditions.append({"visibility": "global"})

        # Organization tags visible to orgadmin/orguser/projectadmin/projectuser for their orgs
        if allowed_orgs:
            visibility_conditions.append(
                {
                    "visibility": "organization",
                    "organization_id": {"$in": sorted(allowed_orgs)},
                }
            )
        # If project-scoped user lacks explicit org assignment, allow organization-level tags (permissive stub)
        elif {"projectadmin", "projectuser"} & role_names:
            visibility_conditions.append({"visibility": "organization"})

        # Project tags visible to projectadmin/projectuser for their projects
        if allowed_projects and (
            {"projectadmin", "projectuser"} & role_names
        ):
            visibility_conditions.append(
                {
                    "visibility": "project",
                    "project_id": {"$in": sorted(allowed_projects)},
                }
            )

        # Also allow orgadmin/orguser to see all project-level tags within their organizations
        if allowed_orgs and ({"orgadmin", "orguser"} & role_names):
            visibility_conditions.append(
                {
                    "visibility": "project",
                    "organization_id": {"$in": sorted(allowed_orgs)},
                }
            )

        if visibility_conditions:
            visibility_clause: Dict[str, Any] = {"$or": visibility_conditions}
        else:
            # If no visibility conditions, restrict to empty result
            visibility_clause = {"visibility": "__none__"}

        and_clauses = [visibility_clause]
        if search_clause:
            and_clauses.append(search_clause)

        if len(and_clauses) == 1:
            query.update(and_clauses[0])
        else:
            query["$and"] = and_clauses

        # Additional filters for org_id and proj_id if provided
        if org_id:
            if allowed_orgs and str(org_id) not in allowed_orgs:
                raise AuthorizationError("Access denied to this organization")
            query["organization_id"] = str(org_id)

        if proj_id:
            if allowed_projects and str(proj_id) not in allowed_projects and not (
                {"orgadmin", "orguser"} & role_names
            ):
                raise AuthorizationError("Access denied to this project")
            query["project_id"] = str(proj_id)

        return query

    async def check_tag_access(
        self, current_user: Any, tag: Any, action: str
    ) -> None:
        """
        Check access for a specific tag instance with visibility hierarchy.

        Visibility rules:
          - global: readable by all roles; writes restricted to superadmin
          - organization: visible within the organization; writes require orgadmin
          - project: visible only within the project; writes require projectadmin for that project
        """
        if current_user is None:
            raise AuthorizationError("Authentication required for tag access")

        role_names = self._extract_role_names(current_user)
        if "superadmin" in role_names:
            # Superadmin bypass checks
            return

        # tag may be a Pydantic model or a dict
        org_id = getattr(tag, "organization_id", None)
        if org_id is None and isinstance(tag, dict):
            org_id = tag.get("organization_id") or tag.get("organizationId")

        proj_id = getattr(tag, "project_id", None)
        if proj_id is None and isinstance(tag, dict):
            proj_id = tag.get("project_id") or tag.get("projectId")

        visibility = getattr(tag, "visibility", None)
        if visibility is None and isinstance(tag, dict):
            visibility = tag.get("visibility")

        allowed_orgs = self._collect_user_org_ids(current_user)
        allowed_projects = self._collect_user_project_ids(current_user)

        action_normalized = (action or "read").lower()
        write_actions = {"create", "update", "delete", "admin", "write", "create_subtag", "update_subtag"}

        # Enforce visibility-aware access
        if action_normalized in write_actions:
            # Write permissions by visibility
            if visibility == "global":
                # Only superadmin (already bypassed), others denied
                raise AuthorizationError(f"{action_normalized} requires superadmin for global tags", 403)

            if visibility == "organization":
                if (org_id and str(org_id) in allowed_orgs) and ("orgadmin" in role_names):
                    return
                raise AuthorizationError(f"{action_normalized} requires organization admin privileges", 403)

            if visibility == "project":
                if (proj_id and str(proj_id) in allowed_projects) and ("projectadmin" in role_names):
                    return
                raise AuthorizationError(f"{action_normalized} requires project admin privileges for this project", 403)

            # Fallback (legacy tags without visibility) => treat as organization-level
            if org_id and (str(org_id) in allowed_orgs) and ("orgadmin" in role_names):
                return
            raise AuthorizationError(f"{action_normalized} requires elevated privileges", 403)

        # Read permissions by visibility
        if visibility == "global" or visibility is None:
            # globally visible (or legacy without visibility)
            return

        if visibility == "organization":
            if org_id and (str(org_id) in allowed_orgs):
                return
            raise AuthorizationError("Access denied to this organization")

        if visibility == "project":
            if proj_id and (str(proj_id) in allowed_projects) and (
                {"projectadmin", "projectuser"} & role_names
            ):
                return
            if org_id and (str(org_id) in allowed_orgs) and (
                {"orgadmin", "orguser"} & role_names
            ):
                return
            raise AuthorizationError("Access denied to this project")

        # Default deny (should not reach here)
        raise AuthorizationError("Access denied")
