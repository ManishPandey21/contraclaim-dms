from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PermissionCategory(str, Enum):
    USER_MANAGEMENT = "user_management"
    DOCUMENT_MANAGEMENT = "document_management"
    PROJECT_MANAGEMENT = "project_management"
    ROLE_MANAGEMENT = "role_management"
    SYSTEM_ADMINISTRATION = "system_administration"
    EMAIL_MANAGEMENT = "email_management"
    AUDIT_MANAGEMENT = "audit_management"


class PermissionLevel(str, Enum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ADMIN = "admin"


class PermissionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    category: PermissionCategory
    resource: str = Field(..., min_length=1, max_length=50)
    action: PermissionLevel

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Permission name cannot be empty")
        parts = stripped.split(":")
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError("Permission name must follow format 'resource:action'")
        return stripped


class PermissionCreate(PermissionBase):
    is_system: Optional[bool] = False


class PermissionUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=500)
    category: Optional[PermissionCategory] = None
    is_active: Optional[bool] = None


class Permission(PermissionBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    is_system: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None


class PermissionResponse(Permission):
    role_count: Optional[int] = None


class PermissionListResponse(BaseModel):
    permissions: List[PermissionResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "PermissionListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class PermissionGroup(BaseModel):
    category: PermissionCategory
    permissions: List[Permission]
    count: int


class PermissionMatrix(BaseModel):
    resources: List[str]
    actions: List[str]
    roles: List[str]
    matrix: Dict[str, Dict[str, bool]]


class PermissionCheck(BaseModel):
    user_id: str
    permission: str
    granted: bool
    context: Optional[Dict[str, Any]] = None
    checked_at: datetime
    permission_details: Optional[Permission] = None


class RolePermission(BaseModel):
    role_id: str
    permission_id: str
    assigned_at: datetime
    assigned_by: str


class UserRole(BaseModel):
    user_id: str
    role_id: str
    assigned_at: datetime
    assigned_by: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


DEFAULT_PERMISSIONS = [
    {"name": "users:read", "description": "View users", "category": "user_management", "resource": "users", "action": "read", "is_system": True},
    {"name": "users:create", "description": "Create users", "category": "user_management", "resource": "users", "action": "create", "is_system": True},
    {"name": "users:update", "description": "Update users", "category": "user_management", "resource": "users", "action": "update", "is_system": True},
    {"name": "users:delete", "description": "Delete users", "category": "user_management", "resource": "users", "action": "delete", "is_system": True},
    {"name": "users:lock", "description": "Lock user accounts", "category": "user_management", "resource": "users", "action": "admin", "is_system": True},
    {"name": "users:unlock", "description": "Unlock user accounts", "category": "user_management", "resource": "users", "action": "admin", "is_system": True},
    {"name": "permissions:read", "description": "View permissions", "category": "role_management", "resource": "permissions", "action": "read", "is_system": True},
    {"name": "documents:read", "description": "View documents", "category": "document_management", "resource": "documents", "action": "read", "is_system": True},
    {"name": "documents:create", "description": "Create documents", "category": "document_management", "resource": "documents", "action": "create", "is_system": True},
    {"name": "documents:update", "description": "Update documents", "category": "document_management", "resource": "documents", "action": "update", "is_system": True},
    {"name": "documents:delete", "description": "Delete documents", "category": "document_management", "resource": "documents", "action": "delete", "is_system": True},
    {"name": "documents:approve", "description": "Approve documents", "category": "document_management", "resource": "documents", "action": "admin", "is_system": True},
    {"name": "documents:share", "description": "Share documents internally or externally", "category": "document_management", "resource": "documents", "action": "admin", "is_system": True},
    {"name": "documents:upload", "description": "Upload single or bulk documents", "category": "document_management", "resource": "documents", "action": "create", "is_system": True},
    {"name": "documents:comment", "description": "Comment on documents", "category": "document_management", "resource": "documents", "action": "create", "is_system": True},
    {"name": "tags:read", "description": "View tags", "category": "document_management", "resource": "tags", "action": "read", "is_system": True},
    {"name": "tags:create", "description": "Create tags", "category": "document_management", "resource": "tags", "action": "create", "is_system": True},
    {"name": "tags:update", "description": "Update tags", "category": "document_management", "resource": "tags", "action": "update", "is_system": True},
    {"name": "tags:delete", "description": "Delete tags", "category": "document_management", "resource": "tags", "action": "delete", "is_system": True},
    {"name": "letter_templates:read", "description": "View letter templates", "category": "document_management", "resource": "letter_templates", "action": "read", "is_system": True},
    {"name": "letter_templates:create", "description": "Create letter templates", "category": "document_management", "resource": "letter_templates", "action": "create", "is_system": True},
    {"name": "letter_templates:update", "description": "Update letter templates", "category": "document_management", "resource": "letter_templates", "action": "update", "is_system": True},
    {"name": "letter_templates:delete", "description": "Delete letter templates", "category": "document_management", "resource": "letter_templates", "action": "delete", "is_system": True},
    {"name": "projects:read", "description": "View projects", "category": "project_management", "resource": "projects", "action": "read", "is_system": True},
    {"name": "projects:create", "description": "Create projects", "category": "project_management", "resource": "projects", "action": "create", "is_system": True},
    {"name": "projects:update", "description": "Update projects", "category": "project_management", "resource": "projects", "action": "update", "is_system": True},
    {"name": "projects:delete", "description": "Delete projects", "category": "project_management", "resource": "projects", "action": "delete", "is_system": True},
    {"name": "projects:assign", "description": "Assign users and responsibilities to projects", "category": "project_management", "resource": "projects", "action": "update", "is_system": True},
    {"name": "organizations:read", "description": "View organizations", "category": "system_administration", "resource": "organizations", "action": "read", "is_system": True},
    {"name": "organizations:create", "description": "Create organizations", "category": "system_administration", "resource": "organizations", "action": "create", "is_system": True},
    {"name": "organizations:update", "description": "Update organizations", "category": "system_administration", "resource": "organizations", "action": "update", "is_system": True},
    {"name": "organizations:delete", "description": "Delete organizations", "category": "system_administration", "resource": "organizations", "action": "delete", "is_system": True},
    {"name": "roles:read", "description": "View roles", "category": "role_management", "resource": "roles", "action": "read", "is_system": True},
    {"name": "roles:create", "description": "Create roles", "category": "role_management", "resource": "roles", "action": "create", "is_system": True},
    {"name": "roles:update", "description": "Update roles", "category": "role_management", "resource": "roles", "action": "update", "is_system": True},
    {"name": "roles:delete", "description": "Delete roles", "category": "role_management", "resource": "roles", "action": "delete", "is_system": True},
    {"name": "roles:assign", "description": "Assign permissions to roles", "category": "role_management", "resource": "roles", "action": "update", "is_system": True},
    {"name": "roles:superuser", "description": "Manage role and permission system", "category": "role_management", "resource": "roles", "action": "admin", "is_system": True},
    {"name": "emails:send", "description": "Send emails", "category": "email_management", "resource": "emails", "action": "create", "is_system": True},
    {"name": "emails:send_notifications", "description": "Send notification emails", "category": "email_management", "resource": "emails", "action": "create", "is_system": True},
    {"name": "emails:view_templates", "description": "View email templates", "category": "email_management", "resource": "emails", "action": "read", "is_system": True},
    {"name": "emails:view_history", "description": "View email history", "category": "email_management", "resource": "emails", "action": "read", "is_system": True},
    {"name": "profile:read", "description": "View own profile", "category": "user_management", "resource": "profile", "action": "read", "is_system": True},
    {"name": "profile:update", "description": "Update own profile", "category": "user_management", "resource": "profile", "action": "update", "is_system": True},
    {"name": "system:admin", "description": "Full system administration", "category": "system_administration", "resource": "system", "action": "admin", "is_system": True},
    {"name": "parties:read", "description": "View parties", "category": "project_management", "resource": "parties", "action": "read", "is_system": True},
    {"name": "parties:create", "description": "Create parties", "category": "project_management", "resource": "parties", "action": "create", "is_system": True},
    {"name": "parties:update", "description": "Update parties", "category": "project_management", "resource": "parties", "action": "update", "is_system": True},
    {"name": "parties:delete", "description": "Delete parties", "category": "project_management", "resource": "parties", "action": "delete", "is_system": True},
    {"name": "concerns:read", "description": "View concerns", "category": "project_management", "resource": "concerns", "action": "read", "is_system": True},
    {"name": "concerns:create", "description": "Create concerns", "category": "project_management", "resource": "concerns", "action": "create", "is_system": True},
    {"name": "concerns:update", "description": "Update concerns", "category": "project_management", "resource": "concerns", "action": "update", "is_system": True},
    {"name": "concerns:delete", "description": "Delete concerns", "category": "project_management", "resource": "concerns", "action": "delete", "is_system": True},
    {"name": "email_groups:read", "description": "View email groups", "category": "email_management", "resource": "email_groups", "action": "read", "is_system": True},
    {"name": "email_groups:create", "description": "Create email groups", "category": "email_management", "resource": "email_groups", "action": "create", "is_system": True},
    {"name": "email_groups:update", "description": "Update email groups", "category": "email_management", "resource": "email_groups", "action": "update", "is_system": True},
    {"name": "email_groups:delete", "description": "Delete email groups", "category": "email_management", "resource": "email_groups", "action": "delete", "is_system": True},
    {"name": "input_requests:read", "description": "View input requests", "category": "document_management", "resource": "input_requests", "action": "read", "is_system": True},
    {"name": "input_requests:create", "description": "Create input requests", "category": "document_management", "resource": "input_requests", "action": "create", "is_system": True},
    {"name": "input_requests:update", "description": "Update input requests", "category": "document_management", "resource": "input_requests", "action": "update", "is_system": True},
    {"name": "input_requests:respond", "description": "Respond to input requests", "category": "document_management", "resource": "input_requests", "action": "update", "is_system": True},
    {"name": "input_requests:admin", "description": "Administer input requests", "category": "document_management", "resource": "input_requests", "action": "admin", "is_system": True},
    {"name": "performance:admin", "description": "Access performance admin data", "category": "system_administration", "resource": "performance", "action": "admin", "is_system": True},
    {"name": "performance:superadmin", "description": "Access performance superadmin data", "category": "system_administration", "resource": "performance", "action": "admin", "is_system": True},
    {"name": "representatives:read", "description": "View representatives", "category": "project_management", "resource": "representatives", "action": "read", "is_system": True},
    {"name": "representatives:create", "description": "Create representatives", "category": "project_management", "resource": "representatives", "action": "create", "is_system": True},
    {"name": "representatives:update", "description": "Update representatives", "category": "project_management", "resource": "representatives", "action": "update", "is_system": True},
    {"name": "representatives:delete", "description": "Delete representatives", "category": "project_management", "resource": "representatives", "action": "delete", "is_system": True},
    {"name": "reports:view", "description": "View reports", "category": "audit_management", "resource": "reports", "action": "read", "is_system": True},
    {"name": "reports:create", "description": "Create reports", "category": "audit_management", "resource": "reports", "action": "create", "is_system": True},
    {"name": "settings:view", "description": "View settings", "category": "system_administration", "resource": "settings", "action": "read", "is_system": True},
    {"name": "settings:edit", "description": "Edit settings", "category": "system_administration", "resource": "settings", "action": "update", "is_system": True},
]
