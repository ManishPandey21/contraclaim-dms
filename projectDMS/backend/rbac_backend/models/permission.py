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
    DRAFTING_MANAGEMENT = "drafting_management"
    BILLING_MANAGEMENT = "billing_management"
    SUBSCRIPTION_MANAGEMENT = "subscription_management"


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
        colon_parts = stripped.split(":")
        dot_parts = stripped.split(".")
        valid_legacy = len(colon_parts) == 2 and all(part.strip() for part in colon_parts)
        valid_canonical = len(dot_parts) >= 2 and all(part.strip() for part in dot_parts)
        if not valid_legacy and not valid_canonical:
            raise ValueError("Permission name must follow 'resource:action' or canonical dotted format")
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
    {"name": "dms.document.view", "description": "View DMS documents", "category": "document_management", "resource": "dms.document", "action": "read", "is_system": True},
    {"name": "dms.document.upload", "description": "Upload DMS documents", "category": "document_management", "resource": "dms.document", "action": "create", "is_system": True},
    {"name": "dms.document.edit_metadata", "description": "Edit DMS document metadata", "category": "document_management", "resource": "dms.document", "action": "update", "is_system": True},
    {"name": "dms.document.delete", "description": "Delete DMS documents", "category": "document_management", "resource": "dms.document", "action": "delete", "is_system": True},
    {"name": "dms.document.download", "description": "Download DMS documents", "category": "document_management", "resource": "dms.document", "action": "read", "is_system": True},
    {"name": "dms.document.bulk_download", "description": "Bulk download DMS documents", "category": "document_management", "resource": "dms.document", "action": "read", "is_system": True},
    {"name": "dms.document.share", "description": "Share DMS documents", "category": "document_management", "resource": "dms.document", "action": "admin", "is_system": True},
    {"name": "dms.document.link_reference", "description": "Link DMS document references", "category": "document_management", "resource": "dms.document", "action": "update", "is_system": True},
    {"name": "dms.status.update", "description": "Update DMS document status", "category": "document_management", "resource": "dms.status", "action": "update", "is_system": True},
    {"name": "dms.comment.add", "description": "Add DMS document comments", "category": "document_management", "resource": "dms.comment", "action": "create", "is_system": True},
    {"name": "dms.dashboard.view", "description": "View DMS dashboard", "category": "document_management", "resource": "dms.dashboard", "action": "read", "is_system": True},
    {"name": "draft.request.view", "description": "View drafting requests", "category": "drafting_management", "resource": "draft.request", "action": "read", "is_system": True},
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
    {"name": "drafting.request.create", "description": "Create drafting requests", "category": "drafting_management", "resource": "drafting.request", "action": "create", "is_system": True},
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

    # --- Contract controls registers (Contract Master, Variation, BG, IPC, Key Dates) ---
    {"name": "dms.contract.master.view", "description": "View Contract Master", "category": "document_management", "resource": "dms.contract.master", "action": "read", "is_system": True},
    {"name": "dms.contract.master.manage", "description": "Manage Contract Master", "category": "document_management", "resource": "dms.contract.master", "action": "admin", "is_system": True},
    {"name": "dms.contract.applicability.manage", "description": "Manage Contract Document applicability and legal effects", "category": "document_management", "resource": "dms.contract.applicability", "action": "admin", "is_system": True},
    {"name": "dms.contract.catalogue.browse", "description": "Browse the organisation Contract Document catalogue", "category": "document_management", "resource": "dms.contract.catalogue", "action": "read", "is_system": True},
    {"name": "dms.variation.view", "description": "View Variations", "category": "document_management", "resource": "dms.variation", "action": "read", "is_system": True},
    {"name": "dms.variation.create", "description": "Create Variations", "category": "document_management", "resource": "dms.variation", "action": "create", "is_system": True},
    {"name": "dms.variation.edit", "description": "Edit Variations", "category": "document_management", "resource": "dms.variation", "action": "update", "is_system": True},
    {"name": "dms.variation.delete", "description": "Delete Variations", "category": "document_management", "resource": "dms.variation", "action": "delete", "is_system": True},
    {"name": "dms.variation.approve", "description": "Approve Variations", "category": "document_management", "resource": "dms.variation", "action": "admin", "is_system": True},
    {"name": "dms.variation.export", "description": "Export Variations", "category": "document_management", "resource": "dms.variation", "action": "admin", "is_system": True},
    {"name": "dms.bankguarantee.view", "description": "View Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "read", "is_system": True},
    {"name": "dms.bankguarantee.create", "description": "Create Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "create", "is_system": True},
    {"name": "dms.bankguarantee.edit", "description": "Edit Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "update", "is_system": True},
    {"name": "dms.bankguarantee.delete", "description": "Delete Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "delete", "is_system": True},
    {"name": "dms.bankguarantee.export", "description": "Export Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "admin", "is_system": True},
    {"name": "dms.bankguarantee.extend", "description": "Extend Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "admin", "is_system": True},
    {"name": "dms.bankguarantee.release", "description": "Release Bank Guarantees", "category": "document_management", "resource": "dms.bankguarantee", "action": "admin", "is_system": True},
    {"name": "dms.ipc.view", "description": "View IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "read", "is_system": True},
    {"name": "dms.ipc.create", "description": "Create IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "create", "is_system": True},
    {"name": "dms.ipc.edit", "description": "Edit IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "update", "is_system": True},
    {"name": "dms.ipc.delete", "description": "Delete IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "delete", "is_system": True},
    {"name": "dms.ipc.approve", "description": "Approve IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "admin", "is_system": True},
    {"name": "dms.ipc.export", "description": "Export IPC / Bills", "category": "document_management", "resource": "dms.ipc", "action": "admin", "is_system": True},
    {"name": "dms.keydate.view", "description": "View Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "read", "is_system": True},
    {"name": "dms.keydate.create", "description": "Create Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "create", "is_system": True},
    {"name": "dms.keydate.edit", "description": "Edit Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "update", "is_system": True},
    {"name": "dms.keydate.delete", "description": "Delete Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "delete", "is_system": True},
    {"name": "dms.keydate.manage", "description": "Manage Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "admin", "is_system": True},
    {"name": "dms.keydate.export", "description": "Export Key Dates", "category": "document_management", "resource": "dms.keydate", "action": "admin", "is_system": True},
    {"name": "dms.keydate.eot_submit", "description": "Submit EOT applications", "category": "document_management", "resource": "dms.keydate", "action": "admin", "is_system": True},
    {"name": "dms.keydate.eot_approve", "description": "Approve EOT applications", "category": "document_management", "resource": "dms.keydate", "action": "admin", "is_system": True},
    {"name": "dms.keydate.achievement", "description": "Record key-date achievement", "category": "document_management", "resource": "dms.keydate", "action": "admin", "is_system": True},

    # --- Claims register ---
    {"name": "dms.claim.view", "description": "View Claims", "category": "document_management", "resource": "dms.claim", "action": "read", "is_system": True},
    {"name": "dms.claim.create", "description": "Create Claims", "category": "document_management", "resource": "dms.claim", "action": "create", "is_system": True},
    {"name": "dms.claim.edit", "description": "Edit Claims", "category": "document_management", "resource": "dms.claim", "action": "update", "is_system": True},
    {"name": "dms.claim.delete", "description": "Delete Claims", "category": "document_management", "resource": "dms.claim", "action": "delete", "is_system": True},
    {"name": "dms.claim.manage", "description": "Manage Claims", "category": "document_management", "resource": "dms.claim", "action": "admin", "is_system": True},
    {"name": "dms.claim.assess", "description": "Assess Claims (AI)", "category": "document_management", "resource": "dms.claim", "action": "admin", "is_system": True},

    # --- Contract appraisal ---
    {"name": "dms.contract.appraisal.view", "description": "View Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "read", "is_system": True},
    {"name": "dms.contract.appraisal.generate", "description": "Generate Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "admin", "is_system": True},
    {"name": "dms.contract.appraisal.edit", "description": "Edit Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "update", "is_system": True},
    {"name": "dms.contract.appraisal.approve", "description": "Approve Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "admin", "is_system": True},
    {"name": "dms.contract.appraisal.reject", "description": "Reject Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "admin", "is_system": True},
    {"name": "dms.contract.appraisal.export", "description": "Export Contract Appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "admin", "is_system": True},
    {"name": "dms.contract.appraisal.create_registers", "description": "Create registers from appraisal", "category": "document_management", "resource": "dms.contract.appraisal", "action": "admin", "is_system": True},

    # --- Evidence graph / Contract Intelligence Timeline ---
    {"name": "dms.evidence_graph.view", "description": "View evidence graph links", "category": "document_management", "resource": "dms.evidence_graph", "action": "read", "is_system": True},
    {"name": "dms.evidence_graph.verify", "description": "Verify or reject evidence graph links", "category": "document_management", "resource": "dms.evidence_graph", "action": "update", "is_system": True},
    {"name": "dms.evidence_graph.manage", "description": "Manage evidence graph events and links", "category": "document_management", "resource": "dms.evidence_graph", "action": "admin", "is_system": True},
    {"name": "dms.contract.timeline.view", "description": "View Contract Intelligence Timeline", "category": "document_management", "resource": "dms.contract.timeline", "action": "read", "is_system": True},

    # --- Chronology Builder ---
    {"name": "dms.chronology.view", "description": "View Chronology Builder", "category": "document_management", "resource": "dms.chronology", "action": "read", "is_system": True},
    {"name": "dms.chronology.create", "description": "Create Chronologies", "category": "document_management", "resource": "dms.chronology", "action": "create", "is_system": True},
    {"name": "dms.chronology.edit", "description": "Edit Chronologies", "category": "document_management", "resource": "dms.chronology", "action": "update", "is_system": True},
    {"name": "dms.chronology.verify", "description": "Verify Chronology Events", "category": "document_management", "resource": "dms.chronology", "action": "update", "is_system": True},
    {"name": "dms.chronology.export", "description": "Export Chronologies", "category": "document_management", "resource": "dms.chronology", "action": "admin", "is_system": True},
    {"name": "dms.chronology.admin", "description": "Administer Chronology Builder", "category": "document_management", "resource": "dms.chronology", "action": "admin", "is_system": True},

    # --- Arbitration pleadings drafting ---
    {"name": "dms.arbitration.view", "description": "View Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "read", "is_system": True},
    {"name": "dms.arbitration.create", "description": "Create Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "create", "is_system": True},
    {"name": "dms.arbitration.edit", "description": "Edit Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "update", "is_system": True},
    {"name": "dms.arbitration.generate", "description": "Generate Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "admin", "is_system": True},
    {"name": "dms.arbitration.export", "description": "Export Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "admin", "is_system": True},
    {"name": "dms.arbitration.approve", "description": "Approve Arbitration Drafts", "category": "document_management", "resource": "dms.arbitration", "action": "admin", "is_system": True},
    {"name": "dms.arbitration.audit", "description": "View Arbitration Draft Audit", "category": "document_management", "resource": "dms.arbitration", "action": "read", "is_system": True},
    {"name": "dms.arbitration.admin", "description": "Administer Arbitration Drafting", "category": "document_management", "resource": "dms.arbitration", "action": "admin", "is_system": True},

    # --- Tasks ---
    {"name": "dms.task.view", "description": "View Tasks", "category": "project_management", "resource": "dms.task", "action": "read", "is_system": True},
    {"name": "dms.task.create", "description": "Create Tasks", "category": "project_management", "resource": "dms.task", "action": "create", "is_system": True},
    {"name": "dms.task.edit", "description": "Edit Tasks", "category": "project_management", "resource": "dms.task", "action": "update", "is_system": True},
    {"name": "dms.task.delete", "description": "Delete Tasks", "category": "project_management", "resource": "dms.task", "action": "delete", "is_system": True},
    {"name": "dms.task.manage", "description": "Manage Tasks", "category": "project_management", "resource": "dms.task", "action": "admin", "is_system": True},
]


def _permission_action_for_name(permission_name: str) -> str:
    token = (permission_name or "").replace(":", ".").split(".")[-1].lower()
    if token in {"create", "upload", "add", "submit"}:
        return PermissionLevel.CREATE.value
    if token in {"update", "edit", "edit_metadata", "link_reference", "verify"}:
        return PermissionLevel.UPDATE.value
    if token == "delete":
        return PermissionLevel.DELETE.value
    if token in {
        "admin",
        "approve",
        "assess",
        "cancel",
        "download",
        "download_all",
        "downgrade",
        "eot_approve",
        "eot_submit",
        "export",
        "extend",
        "generate",
        "manage",
        "release",
        "reject",
        "trial",
        "upgrade",
        "achievement",
    }:
        return PermissionLevel.ADMIN.value
    return PermissionLevel.READ.value


def _permission_category_for_name(permission_name: str) -> str:
    if permission_name.startswith("dms."):
        return PermissionCategory.DOCUMENT_MANAGEMENT.value
    if permission_name.startswith("drafting."):
        return PermissionCategory.DRAFTING_MANAGEMENT.value
    if permission_name.startswith("billing."):
        return PermissionCategory.BILLING_MANAGEMENT.value
    if permission_name.startswith("subscription."):
        return PermissionCategory.SUBSCRIPTION_MANAGEMENT.value
    if permission_name.startswith("users:"):
        return PermissionCategory.USER_MANAGEMENT.value
    if permission_name.startswith(("roles:", "permissions:")):
        return PermissionCategory.ROLE_MANAGEMENT.value
    return PermissionCategory.SYSTEM_ADMINISTRATION.value


def _canonical_permission_entry(permission_name: str) -> Dict[str, Any]:
    resource = (
        permission_name.split(":", 1)[0]
        if ":" in permission_name
        else ".".join(permission_name.split(".")[:-1])
    )
    return {
        "name": permission_name,
        "description": permission_name.replace(".", " ").replace(":", " ").replace("_", " ").title(),
        "category": _permission_category_for_name(permission_name),
        "resource": resource,
        "action": _permission_action_for_name(permission_name),
        "is_system": True,
    }


def _append_missing_canonical_permissions() -> None:
    from ..core.permissions import CANONICAL_PERMISSIONS

    existing = {entry["name"] for entry in DEFAULT_PERMISSIONS}
    for permission_name in CANONICAL_PERMISSIONS:
        if permission_name not in existing:
            DEFAULT_PERMISSIONS.append(_canonical_permission_entry(permission_name))
            existing.add(permission_name)


_append_missing_canonical_permissions()
