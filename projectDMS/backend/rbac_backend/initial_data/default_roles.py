from datetime import datetime

from ..core.permissions import CLIENT_DMS_PERMISSIONS

DOCUMENT_EDITOR_PERMISSIONS = [
    "dms.document.view",
    "dms.document.upload",
    "dms.document.edit_metadata",
    "dms.document.delete",
    "dms.document.download",
    "dms.document.share",
    "dms.status.update",
    "dms.comment.add",
]

DEFAULT_ROLES = [
    {
        "_id": "superadmin",
        "name": "Super Admin",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "drafting.request.create",
            "dms.document.upload",
            "dms.document.download",
            "dms.document.bulk_download",
            "tags:read",
            "tags:create",
            "tags:update",
            "tags:delete",
            "projects:read",
            "users:read",
            "users:create",
            "users:update",
            "users:delete",
            "roles:read",
            "roles:create",
            "roles:update",
            "roles:delete",
            "permissions:read",
            "organizations:read",
            "parties:read",
            "parties:create",
            "parties:update",
            "parties:delete",
            "representatives:create",
            "representatives:update",
            "representatives:delete",
            "concerns:read",
            "concerns:create",
            "concerns:update",
            "concerns:delete",
            "settings.storage.view",
            "settings.storage.manage",
            "settings.smtp.view",
            "settings.smtp.manage",
            "settings.notification.view",
            "settings.notification.manage",
            "settings.legal.view",
            "settings.legal.manage",
            "settings.prompt.view",
            "settings.prompt.manage",
        ],
    },
    {
        "_id": "orgadmin",
        "name": "Organization Admin",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "dms.document.bulk_download",
            "tags:read",
            "tags:create",
            "tags:update",
            "tags:delete",
            "projects:read",
            "users:read",
            "users:create",
            "users:update",
            "users:delete",
            "roles:read",
            "roles:create",
            "roles:update",
            "roles:delete",
            "permissions:read",
            "organizations:read",
            "organizations:create",
            "organizations:update",
            "organizations:delete",
            "parties:read",
            "parties:create",
            "parties:update",
            "parties:delete",
            "representatives:create",
            "representatives:update",
            "representatives:delete",
            "concerns:read",
            "settings.storage.view",
            "settings.storage.manage",
            "settings.smtp.view",
            "settings.smtp.manage",
            "concerns:create",
            "concerns:update",
            "concerns:delete",
        ],
    },
    {
        "_id": "orguser",
        "name": "Organization - User",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "tags:read",
            "projects:read",
            "users:read",
            "permissions:read",
            "organizations:read",
            "parties:read",
            "parties:update",
            "representatives:update",
            "concerns:read",
            "concerns:update",
        ],
    },
    {
        "_id": "contractmgr_org",
        "name": "Contract Manager - Organization",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "tags:read",
            "projects:read",
            "users:read",
            "permissions:read",
            "organizations:read",
            "parties:read",
            "parties:update",
            "representatives:update",
            "concerns:read",
            "concerns:update",
        ],
    },
    {
        "_id": "projectadmin",
        "name": "Project Admin",
        "is_system": True,
        "scope": "project",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "dms.document.bulk_download",
            "tags:read",
            "tags:create",
            "tags:update",
            "tags:delete",
            "projects:read",
            "users:read",
            "users:create",
            "users:update",
            "roles:read",
            "roles:create",
            "roles:update",
            "roles:assign",
            "permissions:read",
            "organizations:read",
            "parties:read",
            "concerns:read",
            "settings.storage.view",
            "settings.storage.manage",
        ],
    },
    {
        "_id": "projectuser",
        "name": "Project - User",
        "is_system": True,
        "scope": "project",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "tags:read",
            "projects:read",
            "users:read",
            "permissions:read",
            "organizations:read",
            "parties:read",
            "parties:update",
            "representatives:update",
            "concerns:read",
            "concerns:update",
        ],
    },
    {
        "_id": "doccontroller",
        "name": "Document Controller",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "dms.document.upload",
            "dms.document.download",
            "tags:read",
            "projects:read",
            "permissions:read",
        ],
    },
    {
        "_id": "reporter",
        "name": "Reporter",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "reports:view",
            "reports:create",
            "tags:read"
        ]
    },
    {
        "_id": "settings_manager",
        "name": "Settings Manager",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "settings.storage.view",
            "settings.storage.manage",
            "settings.smtp.view",
            "settings.smtp.manage",
            "settings.notification.view",
            "settings.notification.manage",
            "settings.legal.view",
            "settings.legal.manage",
            "tags:read"
        ]
    },
    {
        "_id": "limited_user",
        "name": "Limited User",
        "is_system": True,
        "scope": "organization",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "dms.document.view",
            "dms.dashboard.view",
            "tags:read",
            "projects:read"
        ]
    },
    {
        "_id": "contraclaim_drafting_manager",
        "name": "ContraClaim Drafting Manager",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
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
            "drafting.final.approve",
            "drafting.audit.view",
            "drafting.admin",
        ],
    },
    {
        "_id": "contraclaim_expert_drafter",
        "name": "ContraClaim Contract Expert - Drafter",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "drafting.request.view",
            "drafting.request.accept",
            "drafting.draft.create",
            "drafting.draft.edit",
            "drafting.draft.submit_for_review",
            "drafting.final.view",
            "drafting.audit.view",
        ],
    },
    {
        "_id": "contraclaim_expert_reviewer",
        "name": "ContraClaim Contract Expert - Reviewer",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "drafting.request.view",
            "drafting.review.perform",
            "drafting.review.approve",
            "drafting.review.return_for_revision",
            "drafting.final.view",
            "drafting.audit.view",
        ],
    },
    {
        "_id": "contraclaim_billing_admin",
        "name": "ContraClaim Billing Admin",
        "is_system": True,
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": [
            "billing.plan.view",
            "billing.plan.manage",
            "subscription.entitlement.manage",
            "subscription.usage.view",
            "subscription.archive_access",
            "subscription.offboarding_export",
        ],
    }

]


def _merge_role_permissions(role_ids: set[str], permissions: list[str]) -> None:
    for role in DEFAULT_ROLES:
        if role.get("_id") not in role_ids:
            continue
        existing = list(role.get("permissions") or [])
        seen = set(existing)
        for permission in permissions:
            if permission not in seen:
                existing.append(permission)
                seen.add(permission)
        role["permissions"] = existing


_merge_role_permissions(
    {"superadmin", "orgadmin", "projectadmin", "contractmgr_org"},
    CLIENT_DMS_PERMISSIONS,
)

_merge_role_permissions(
    {"contraclaim_drafting_manager"},
    [
        "drafting.workflow.state",
        "drafting.workflow.resume",
        "drafting.workflow.cancel",
        "drafting.workflow.checkpoints",
        "drafting.workflow.force_v2",
    ],
)

_merge_role_permissions(
    {"contraclaim_expert_drafter"},
    [
        "drafting.workflow.state",
        "drafting.workflow.resume",
        "drafting.workflow.cancel",
    ],
)

_merge_role_permissions(
    {"orguser", "projectuser"},
    DOCUMENT_EDITOR_PERMISSIONS,
)

_merge_role_permissions(
    {"doccontroller"},
    [
        "dms.document.view",
        "dms.document.upload",
        "dms.document.download",
        "dms.dashboard.view",
    ],
)


def _derive_superuser_role() -> dict:
    """Build the Super User role from the fully-merged Organization Admin set.

    ``superuser`` is a *global-tier* role in ``core.effective_scope``: its reach
    spans the organisations assigned to the account, and that reach is enforced
    by ``EffectiveScope``, not by holding extra permissions. So the capability
    set is Organization Admin's -- least privilege -- and only the tenancy tier
    differs. Deriving it rather than copying keeps the two in step: a permission
    added to orgadmin later is inherited instead of silently missed.

    The role was absent from this catalogue entirely, so every ``superuser``
    account resolved to zero permissions and was denied everywhere, while the
    tier stayed implemented in ``effective_scope``, ``security`` and
    ``policy_service``. An account that authenticates and can do nothing is a
    worse failure than one that cannot sign in at all.

    Derived after the ``_merge_role_permissions`` calls above so it picks up the
    merged CLIENT_DMS_PERMISSIONS, not the pre-merge literal.
    """
    org_admin = next(role for role in DEFAULT_ROLES if role.get("_id") == "orgadmin")
    return {
        "_id": "superuser",
        "name": "Super User",
        "is_system": True,
        # Global tier: bounded by assigned organisations, not by a home org.
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": list(org_admin.get("permissions") or []),
    }


if not any(role.get("_id") == "superuser" for role in DEFAULT_ROLES):
    DEFAULT_ROLES.append(_derive_superuser_role())
