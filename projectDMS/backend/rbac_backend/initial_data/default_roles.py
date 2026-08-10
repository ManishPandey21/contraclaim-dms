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
    """Build the Super User role with the same capabilities as Super Admin.

    ``superuser`` is a *global-tier* role in ``core.effective_scope``, but the
    two axes are independent: this sets the **capability** set equal to Super
    Admin's, while the **tenancy** reach stays bounded to the organisations
    assigned to the account and is enforced by ``EffectiveScope``. A Super User
    can therefore do everything a Super Admin can, inside its own organisations
    only.

    Nothing usable is lost by taking Super Admin's set rather than Organization
    Admin's: the three permissions unique to orgadmin
    (``organizations:create/update/delete``) are gated in
    ``authorization_service.check_organization_access`` on the *role names*
    ``superadmin``/``orgadmin``, which a Super User does not hold, so they were
    unreachable for this role either way.

    Deriving rather than copying keeps the two in step: a permission added to
    superadmin later is inherited instead of silently missed.

    The role was absent from this catalogue entirely, so every ``superuser``
    account resolved to zero permissions and was denied everywhere, while the
    tier stayed implemented in ``effective_scope``, ``security`` and
    ``policy_service``. An account that authenticates and can do nothing is a
    worse failure than one that cannot sign in at all.

    Derived after the ``_merge_role_permissions`` calls above so it picks up the
    merged CLIENT_DMS_PERMISSIONS, not the pre-merge literal.
    """
    super_admin = next(role for role in DEFAULT_ROLES if role.get("_id") == "superadmin")
    return {
        "_id": "superuser",
        "name": "Super User",
        "is_system": True,
        # Global tier: bounded by assigned organisations, not by a home org.
        "scope": "system",
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "permissions": list(super_admin.get("permissions") or []),
    }


if not any(role.get("_id") == "superuser" for role in DEFAULT_ROLES):
    DEFAULT_ROLES.append(_derive_superuser_role())
