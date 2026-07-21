"""Canonical RBAC permissions."""

from __future__ import annotations

from typing import Dict, List


CLIENT_DMS_PERMISSIONS: List[str] = [
    "dms.document.view",
    "dms.document.upload",
    "dms.document.edit_metadata",
    "dms.document.delete",
    "dms.document.download",
    "dms.document.bulk_download",
    "dms.document.share",
    "dms.document.link_reference",
    "dms.status.update",
    "dms.comment.add",
    "dms.dashboard.view",
    "dms.report.view",
    "dms.user.manage",
    "dms.project.manage",
    "dms.audit.view",
    "dms.claim.view",
    "dms.claim.create",
    "dms.claim.edit",
    "dms.claim.delete",
    "dms.claim.manage",
    "dms.claim.assess",
    "dms.contract.appraisal.view",
    "dms.contract.appraisal.generate",
    "dms.contract.appraisal.edit",
    "dms.contract.appraisal.approve",
    "dms.contract.appraisal.reject",
    "dms.contract.appraisal.export",
    "dms.contract.appraisal.create_registers",
    "dms.task.view",
    "dms.task.create",
    "dms.task.edit",
    "dms.task.delete",
    "dms.task.manage",
    "dms.keydate.view",
    "dms.keydate.create",
    "dms.keydate.edit",
    "dms.keydate.delete",
    "dms.keydate.eot_submit",
    "dms.keydate.eot_approve",
    "dms.keydate.achievement",
    "dms.keydate.export",
    "dms.keydate.manage",
    "dms.variation.view",
    "dms.variation.create",
    "dms.variation.edit",
    "dms.variation.delete",
    "dms.variation.approve",
    "dms.variation.export",
    "dms.bankguarantee.view",
    "dms.bankguarantee.create",
    "dms.bankguarantee.edit",
    "dms.bankguarantee.delete",
    "dms.bankguarantee.extend",
    "dms.bankguarantee.release",
    "dms.bankguarantee.export",
    "dms.insurance.view",
    "dms.insurance.create",
    "dms.insurance.edit",
    "dms.insurance.delete",
    "dms.insurance.export",
    "dms.insurance.manage_types",
    "dms.contract.master.view",
    "dms.contract.master.manage",
    "dms.contract.read",
    "dms.contract.update",
    "dms.contract.clause.create",
    "dms.contract.clause.read",
    "dms.ai.contract_processing.run",
    "dms.ipc.view",
    "dms.ipc.create",
    "dms.ipc.edit",
    "dms.ipc.delete",
    "dms.ipc.approve",
    "dms.ipc.export",
    "dms.evidence_graph.view",
    "dms.evidence_graph.verify",
    "dms.evidence_graph.manage",
    "dms.contract.timeline.view",
    "dms.chronology.view",
    "dms.chronology.create",
    "dms.chronology.edit",
    "dms.chronology.verify",
    "dms.chronology.export",
    "dms.chronology.admin",
    "dms.arbitration.view",
    "dms.arbitration.create",
    "dms.arbitration.edit",
    "dms.arbitration.generate",
    "dms.arbitration.export",
    "dms.arbitration.approve",
    "dms.arbitration.audit",
    "dms.arbitration.admin",
    "dms.admin",
]

DRAFTING_PERMISSIONS: List[str] = [
    "drafting.request.create",
    "drafting.request.view",
    "drafting.request.accept",
    "drafting.request.assign",
    "drafting.draft.create",
    "drafting.draft.edit",
    "drafting.draft.submit_for_review",
    "drafting.review.perform",
    "drafting.review.approve",
    "drafting.review.return_for_revision",
    "drafting.final.view",
    "drafting.audit.view",
    "drafting.workflow.state",
    "drafting.workflow.resume",
    "drafting.workflow.cancel",
    "drafting.workflow.checkpoints",
    "drafting.workflow.force_v2",
    "drafting.admin",
]

BILLING_PERMISSIONS: List[str] = [
    "billing.plan.view",
    "billing.plan.manage",
    "billing.invoice.view",
    "billing.invoice.download",
    "subscription.entitlement.manage",
    "subscription.upgrade",
    "subscription.downgrade",
    "subscription.cancel",
    "subscription.trial.manage",
    "subscription.addon.manage",
    "subscription.history.view",
    "subscription.usage.view",
    "subscription.archive_access",
    "subscription.offboarding_export",
]

ROLE_MANAGEMENT_PERMISSIONS: List[str] = [
    "roles:read",
    "roles:create",
    "roles:update",
    "roles:delete",
    "roles:assign",
    "permissions:read",
]

CANONICAL_PERMISSIONS: List[str] = [
    *CLIENT_DMS_PERMISSIONS,
    *DRAFTING_PERMISSIONS,
    *BILLING_PERMISSIONS,
    *ROLE_MANAGEMENT_PERMISSIONS,
]


class Permissions:
    """Central permission constants used by routers and services."""

    DOCUMENT_VIEW = "dms.document.view"
    DOCUMENT_UPLOAD = "dms.document.upload"
    DOCUMENT_EDIT_METADATA = "dms.document.edit_metadata"
    DOCUMENT_DELETE = "dms.document.delete"
    DOCUMENT_DOWNLOAD = "dms.document.download"
    DOCUMENT_BULK_DOWNLOAD = "dms.document.bulk_download"
    DOCUMENT_SHARE = "dms.document.share"
    DOCUMENT_LINK_REFERENCE = "dms.document.link_reference"
    STATUS_UPDATE = "dms.status.update"
    COMMENT_ADD = "dms.comment.add"
    DASHBOARD_VIEW = "dms.dashboard.view"
    REPORT_VIEW = "dms.report.view"
    USER_MANAGE = "dms.user.manage"
    PROJECT_MANAGE = "dms.project.manage"
    AUDIT_VIEW = "dms.audit.view"
    CLAIM_VIEW = "dms.claim.view"
    CLAIM_CREATE = "dms.claim.create"
    CLAIM_EDIT = "dms.claim.edit"
    CLAIM_DELETE = "dms.claim.delete"
    CLAIM_MANAGE = "dms.claim.manage"
    CLAIM_ASSESS = "dms.claim.assess"
    CONTRACT_APPRAISAL_VIEW = "dms.contract.appraisal.view"
    CONTRACT_APPRAISAL_GENERATE = "dms.contract.appraisal.generate"
    CONTRACT_APPRAISAL_EDIT = "dms.contract.appraisal.edit"
    CONTRACT_APPRAISAL_APPROVE = "dms.contract.appraisal.approve"
    CONTRACT_APPRAISAL_REJECT = "dms.contract.appraisal.reject"
    CONTRACT_APPRAISAL_EXPORT = "dms.contract.appraisal.export"
    CONTRACT_APPRAISAL_CREATE_REGISTERS = "dms.contract.appraisal.create_registers"
    # Contract clause chunking agent (clause-wise processing pipeline)
    CONTRACT_READ = "dms.contract.read"
    CONTRACT_UPDATE = "dms.contract.update"
    CONTRACT_CLAUSE_CREATE = "dms.contract.clause.create"
    CONTRACT_CLAUSE_READ = "dms.contract.clause.read"
    AI_CONTRACT_PROCESSING_RUN = "dms.ai.contract_processing.run"
    TASK_VIEW = "dms.task.view"
    TASK_CREATE = "dms.task.create"
    TASK_EDIT = "dms.task.edit"
    TASK_DELETE = "dms.task.delete"
    TASK_MANAGE = "dms.task.manage"
    KEYDATE_VIEW = "dms.keydate.view"
    KEYDATE_CREATE = "dms.keydate.create"
    KEYDATE_EDIT = "dms.keydate.edit"
    KEYDATE_DELETE = "dms.keydate.delete"
    KEYDATE_EOT_SUBMIT = "dms.keydate.eot_submit"
    KEYDATE_EOT_APPROVE = "dms.keydate.eot_approve"
    KEYDATE_ACHIEVEMENT = "dms.keydate.achievement"
    KEYDATE_EXPORT = "dms.keydate.export"
    KEYDATE_MANAGE = "dms.keydate.manage"
    VARIATION_VIEW = "dms.variation.view"
    VARIATION_CREATE = "dms.variation.create"
    VARIATION_EDIT = "dms.variation.edit"
    VARIATION_DELETE = "dms.variation.delete"
    VARIATION_APPROVE = "dms.variation.approve"
    VARIATION_EXPORT = "dms.variation.export"
    BG_VIEW = "dms.bankguarantee.view"
    BG_CREATE = "dms.bankguarantee.create"
    BG_EDIT = "dms.bankguarantee.edit"
    BG_DELETE = "dms.bankguarantee.delete"
    BG_EXTEND = "dms.bankguarantee.extend"
    BG_RELEASE = "dms.bankguarantee.release"
    BG_EXPORT = "dms.bankguarantee.export"
    INSURANCE_VIEW = "dms.insurance.view"
    INSURANCE_CREATE = "dms.insurance.create"
    INSURANCE_EDIT = "dms.insurance.edit"
    INSURANCE_DELETE = "dms.insurance.delete"
    INSURANCE_EXPORT = "dms.insurance.export"
    INSURANCE_MANAGE_TYPES = "dms.insurance.manage_types"
    CONTRACT_MASTER_VIEW = "dms.contract.master.view"
    CONTRACT_MASTER_MANAGE = "dms.contract.master.manage"
    IPC_VIEW = "dms.ipc.view"
    IPC_CREATE = "dms.ipc.create"
    IPC_EDIT = "dms.ipc.edit"
    IPC_DELETE = "dms.ipc.delete"
    IPC_APPROVE = "dms.ipc.approve"
    IPC_EXPORT = "dms.ipc.export"
    EVIDENCE_GRAPH_VIEW = "dms.evidence_graph.view"
    EVIDENCE_GRAPH_VERIFY = "dms.evidence_graph.verify"
    EVIDENCE_GRAPH_MANAGE = "dms.evidence_graph.manage"
    CONTRACT_TIMELINE_VIEW = "dms.contract.timeline.view"
    CHRONOLOGY_VIEW = "dms.chronology.view"
    CHRONOLOGY_CREATE = "dms.chronology.create"
    CHRONOLOGY_EDIT = "dms.chronology.edit"
    CHRONOLOGY_VERIFY = "dms.chronology.verify"
    CHRONOLOGY_EXPORT = "dms.chronology.export"
    CHRONOLOGY_ADMIN = "dms.chronology.admin"
    ARBITRATION_VIEW = "dms.arbitration.view"
    ARBITRATION_CREATE = "dms.arbitration.create"
    ARBITRATION_EDIT = "dms.arbitration.edit"
    ARBITRATION_GENERATE = "dms.arbitration.generate"
    ARBITRATION_EXPORT = "dms.arbitration.export"
    ARBITRATION_APPROVE = "dms.arbitration.approve"
    ARBITRATION_AUDIT = "dms.arbitration.audit"
    ARBITRATION_ADMIN = "dms.arbitration.admin"
    DMS_ADMIN = "dms.admin"

    DRAFTING_REQUEST_CREATE = "drafting.request.create"
    DRAFTING_REQUEST_VIEW = "drafting.request.view"
    DRAFTING_REQUEST_ACCEPT = "drafting.request.accept"
    DRAFTING_REQUEST_ASSIGN = "drafting.request.assign"
    DRAFTING_DRAFT_CREATE = "drafting.draft.create"
    DRAFTING_DRAFT_EDIT = "drafting.draft.edit"
    DRAFTING_DRAFT_SUBMIT_FOR_REVIEW = "drafting.draft.submit_for_review"
    DRAFTING_REVIEW_PERFORM = "drafting.review.perform"
    DRAFTING_REVIEW_APPROVE = "drafting.review.approve"
    DRAFTING_REVIEW_RETURN_FOR_REVISION = "drafting.review.return_for_revision"
    DRAFTING_FINAL_VIEW = "drafting.final.view"
    DRAFTING_AUDIT_VIEW = "drafting.audit.view"
    DRAFTING_WORKFLOW_STATE = "drafting.workflow.state"
    DRAFTING_WORKFLOW_RESUME = "drafting.workflow.resume"
    DRAFTING_WORKFLOW_CANCEL = "drafting.workflow.cancel"
    DRAFTING_WORKFLOW_CHECKPOINTS = "drafting.workflow.checkpoints"
    DRAFTING_WORKFLOW_FORCE_V2 = "drafting.workflow.force_v2"
    DRAFTING_ADMIN = "drafting.admin"

    BILLING_PLAN_VIEW = "billing.plan.view"
    BILLING_PLAN_MANAGE = "billing.plan.manage"
    BILLING_INVOICE_VIEW = "billing.invoice.view"
    BILLING_INVOICE_DOWNLOAD = "billing.invoice.download"
    SUBSCRIPTION_ENTITLEMENT_MANAGE = "subscription.entitlement.manage"
    SUBSCRIPTION_UPGRADE = "subscription.upgrade"
    SUBSCRIPTION_DOWNGRADE = "subscription.downgrade"
    SUBSCRIPTION_CANCEL = "subscription.cancel"
    SUBSCRIPTION_TRIAL_MANAGE = "subscription.trial.manage"
    SUBSCRIPTION_ADDON_MANAGE = "subscription.addon.manage"
    SUBSCRIPTION_HISTORY_VIEW = "subscription.history.view"
    SUBSCRIPTION_USAGE_VIEW = "subscription.usage.view"
    SUBSCRIPTION_ARCHIVE_ACCESS = "subscription.archive_access"
    SUBSCRIPTION_OFFBOARDING_EXPORT = "subscription.offboarding_export"

    PLATFORM_ADMIN = "platform.admin"
    PLATFORM_ROLE_MANAGE = "platform.role.manage"
    PLATFORM_PERMISSION_MANAGE = "platform.permission.manage"

PERMISSION_DOMAINS: Dict[str, str] = {
    **{permission: "client_dms" for permission in CLIENT_DMS_PERMISSIONS},
    **{permission: "drafting" for permission in DRAFTING_PERMISSIONS},
    **{permission: "billing" for permission in BILLING_PERMISSIONS},
    **{permission: "role_management" for permission in ROLE_MANAGEMENT_PERMISSIONS},
}

LEGACY_PERMISSION_ALIASES: Dict[str, List[str]] = {
    "dms.dashboard.view": ["projects:read"],
    "dms.report.view": ["reports:view"],
    "dms.user.manage": ["users:create", "users:update", "users:delete"],
    "dms.project.manage": ["projects:create", "projects:update", "projects:delete", "projects:assign"],
    "dms.audit.view": ["audit:read"],
    "dms.claim.manage": ["projects:update"],
    "dms.contract.appraisal.approve": ["projects:update"],
    "dms.contract.appraisal.reject": ["projects:update"],
    "dms.task.manage": ["projects:update"],
    "dms.keydate.eot_approve": ["projects:update"],
    "dms.keydate.manage": ["projects:update"],
    "dms.variation.approve": ["projects:update"],
    "dms.bankguarantee.release": ["projects:update"],
    "dms.contract.master.view": ["projects:read"],
    "dms.contract.master.manage": ["projects:update"],
    "dms.ipc.approve": ["projects:update"],
    "dms.evidence_graph.manage": ["projects:update"],
    "dms.chronology.admin": ["projects:update"],
    "dms.arbitration.approve": ["projects:update"],
    "dms.arbitration.admin": ["projects:update"],
    "dms.admin": ["system:admin"],
    "billing.plan.view": ["organizations:read"],
    "billing.plan.manage": ["system:admin"],
    "billing.invoice.view": ["organizations:read"],
    "billing.invoice.download": ["organizations:read"],
    "subscription.entitlement.manage": ["system:admin"],
    "subscription.upgrade": ["system:admin"],
    "subscription.downgrade": ["system:admin"],
    "subscription.cancel": ["system:admin"],
    "subscription.trial.manage": ["system:admin"],
    "subscription.addon.manage": ["system:admin"],
    "subscription.history.view": ["organizations:read"],
    "subscription.usage.view": ["reports:view"],
    "subscription.archive_access": [],
    "subscription.offboarding_export": [],
}

ALIAS_TO_CANONICAL: Dict[str, str] = {
    alias: canonical
    for canonical, aliases in LEGACY_PERMISSION_ALIASES.items()
    for alias in aliases
}


def equivalent_permissions(permission: str) -> set[str]:
    """Return canonical and legacy spellings that should satisfy the same check."""
    key = (permission or "").strip()
    if not key:
        return set()

    canonical = ALIAS_TO_CANONICAL.get(key, key)
    equivalents = {key, canonical}
    equivalents.update(LEGACY_PERMISSION_ALIASES.get(canonical, []))

    if key in LEGACY_PERMISSION_ALIASES:
        equivalents.update(LEGACY_PERMISSION_ALIASES[key])
    return equivalents


def permission_domain(permission: str) -> str:
    canonical = ALIAS_TO_CANONICAL.get(permission, permission)
    return PERMISSION_DOMAINS.get(canonical, "system")
