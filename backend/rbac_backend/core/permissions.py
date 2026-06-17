"""Canonical RBAC permissions and legacy compatibility aliases."""

from __future__ import annotations

from typing import Dict, List


CLIENT_DMS_PERMISSIONS: List[str] = [
    "dms.document.view",
    "dms.document.upload",
    "dms.document.edit_metadata",
    "dms.document.delete",
    "dms.document.download",
    "dms.document.bulk_download",
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

CANONICAL_PERMISSIONS: List[str] = [
    *CLIENT_DMS_PERMISSIONS,
    *DRAFTING_PERMISSIONS,
    *BILLING_PERMISSIONS,
]


class Permissions:
    """Central permission constants used by routers and services."""

    DOCUMENT_VIEW = "dms.document.view"
    DOCUMENT_UPLOAD = "dms.document.upload"
    DOCUMENT_EDIT_METADATA = "dms.document.edit_metadata"
    DOCUMENT_DELETE = "dms.document.delete"
    DOCUMENT_DOWNLOAD = "dms.document.download"
    DOCUMENT_BULK_DOWNLOAD = "dms.document.bulk_download"
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
}

LEGACY_PERMISSION_ALIASES: Dict[str, List[str]] = {
    "dms.document.view": [
        "documents:read",
        "docs:view",
        "letters:view",
        "email_groups:read",
        "representatives:read",
    ],
    "dms.document.upload": ["documents:create", "documents:upload", "docs:create", "docs:upload", "letters:create"],
    "dms.document.edit_metadata": [
        "documents:update",
        "docs:edit",
        "letters:edit",
        "email_groups:create",
        "email_groups:update",
        "email_groups:delete",
        "representatives:create",
        "representatives:update",
        "representatives:delete",
    ],
    "dms.document.delete": ["documents:delete", "docs:delete"],
    "dms.document.download": ["documents:download", "docs:download"],
    "dms.document.bulk_download": ["documents:download_all", "docs:download_all", "docs:download-all"],
    "dms.document.link_reference": ["documents:update", "documents:link", "docs:link"],
    "dms.status.update": ["documents:approve", "documents:update"],
    "dms.comment.add": ["documents:comment", "docs:comment", "letters:comment"],
    "dms.dashboard.view": ["documents:read", "projects:read"],
    "dms.report.view": ["reports:view"],
    "dms.user.manage": ["users:create", "users:update", "users:delete"],
    "dms.project.manage": ["projects:create", "projects:update", "projects:delete", "projects:assign"],
    "dms.audit.view": ["audit:read", "documents:read"],
    "dms.claim.view": ["documents:read"],
    "dms.claim.create": ["documents:create"],
    "dms.claim.edit": ["documents:update"],
    "dms.claim.delete": ["documents:delete"],
    "dms.claim.manage": ["documents:update", "projects:update"],
    "dms.claim.assess": ["documents:read"],
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
    "subscription.archive_access": ["documents:read"],
    "subscription.offboarding_export": ["documents:download_all"],
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
