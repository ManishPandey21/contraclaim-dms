/**
 * Central RBAC configuration for routes and sidebar.
 * Frontend checks are UX only; backend policy remains authoritative.
 */

export const Roles = {
  SuperAdmin: "superadmin",
  OrgAdmin: "orgadmin",
  OrgUser: "orguser",
  ProjectAdmin: "projectadmin",
  ProjectUser: "projectuser",
  DocumentController: "doccontroller",
  Reporter: "reporter",
  SettingsManager: "settings_manager",
  LimitedUser: "limited_user",
  ContractManagerOrg: "contractmgr_org",
  DraftingManager: "contraclaim_drafting_manager",
  ExpertDrafter: "contraclaim_expert_drafter",
  ExpertReviewer: "contraclaim_expert_reviewer",
  BillingAdmin: "contraclaim_billing_admin",
} as const;

export type Role = (typeof Roles)[keyof typeof Roles];

export const ROLE_ALIASES: Record<string, string> = {
  "organization-user": Roles.OrgUser,
  "org-user": Roles.OrgUser,
  "organization user": Roles.OrgUser,
  organizationuser: Roles.OrgUser,
  orguser: Roles.OrgUser,
  "organization-admin": Roles.OrgAdmin,
  "org-admin": Roles.OrgAdmin,
  "organization admin": Roles.OrgAdmin,
  organizationadmin: Roles.OrgAdmin,
  orgadmin: Roles.OrgAdmin,
  "project-user": Roles.ProjectUser,
  "project user": Roles.ProjectUser,
  projectuser: Roles.ProjectUser,
  "proj-user": Roles.ProjectUser,
  "proj user": Roles.ProjectUser,
  projuser: Roles.ProjectUser,
  "project-admin": Roles.ProjectAdmin,
  "project admin": Roles.ProjectAdmin,
  projectadmin: Roles.ProjectAdmin,
  "proj-admin": Roles.ProjectAdmin,
  "proj admin": Roles.ProjectAdmin,
  projadmin: Roles.ProjectAdmin,
  "super-admin": Roles.SuperAdmin,
  "super admin": Roles.SuperAdmin,
  superadministrator: Roles.SuperAdmin,
  "super-user": "superuser",
  "super user": "superuser",
  superuser: "superuser",
  superadmin: Roles.SuperAdmin,
  admin: Roles.SuperAdmin,
  administrator: Roles.SuperAdmin,
  "document-controller": Roles.DocumentController,
  "document controller": Roles.DocumentController,
  documentcontroller: Roles.DocumentController,
  doccontroller: Roles.DocumentController,
  reporter: Roles.Reporter,
  auditor: Roles.Reporter,
  "settings-manager": Roles.SettingsManager,
  "settings manager": Roles.SettingsManager,
  settingsmanager: Roles.SettingsManager,
  settings_manager: Roles.SettingsManager,
  "limited-user": Roles.LimitedUser,
  "limited user": Roles.LimitedUser,
  limiteduser: Roles.LimitedUser,
  limited_user: Roles.LimitedUser,
  "contract-manager-organization": Roles.ContractManagerOrg,
  "contract manager organization": Roles.ContractManagerOrg,
  "contract manager - organization": Roles.ContractManagerOrg,
  contractmgr_org: Roles.ContractManagerOrg,
  "contraclaim drafting manager": Roles.DraftingManager,
  contraclaim_drafting_manager: Roles.DraftingManager,
  "contraclaim expert drafter": Roles.ExpertDrafter,
  "contraclaim contract expert - drafter": Roles.ExpertDrafter,
  contraclaim_expert_drafter: Roles.ExpertDrafter,
  "contraclaim expert reviewer": Roles.ExpertReviewer,
  "contraclaim contract expert - reviewer": Roles.ExpertReviewer,
  contraclaim_expert_reviewer: Roles.ExpertReviewer,
  "contraclaim billing admin": Roles.BillingAdmin,
  contraclaim_billing_admin: Roles.BillingAdmin,
};

export function normalizeRoleId(role: string): string {
  const raw = String(role || "").trim().toLowerCase();
  if (!raw) return "";
  const compact = raw.replace(/[^a-z0-9]/g, "");
  return ROLE_ALIASES[raw] || ROLE_ALIASES[compact] || raw;
}

export const ROLE_LABELS: Record<string, string> = {
  [Roles.SuperAdmin]: "Super Admin",
  [Roles.OrgAdmin]: "Organization Admin",
  [Roles.OrgUser]: "Organization User",
  [Roles.ProjectAdmin]: "Project Admin",
  [Roles.ProjectUser]: "Project User",
  [Roles.DocumentController]: "Document Controller",
  [Roles.Reporter]: "Reporter / Auditor",
  [Roles.SettingsManager]: "Settings Manager",
  [Roles.LimitedUser]: "Limited User",
  [Roles.ContractManagerOrg]: "Contract Manager",
  [Roles.DraftingManager]: "Drafting Manager",
  [Roles.ExpertDrafter]: "Expert Drafter",
  [Roles.ExpertReviewer]: "Expert Reviewer",
  [Roles.BillingAdmin]: "Billing Admin",
};

export function labelForRole(role: string): string {
  const normalized = normalizeRoleId(role);
  return (
    ROLE_LABELS[normalized] ||
    normalized
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (char) => char.toUpperCase()) ||
    "User"
  );
}

const ALL_APP_ROLES: Role[] = Object.values(Roles);
const ORG_PROJECT_ROLES: Role[] = [
  Roles.OrgAdmin,
  Roles.OrgUser,
  Roles.ProjectAdmin,
  Roles.ProjectUser,
  Roles.DocumentController,
  Roles.Reporter,
  Roles.SettingsManager,
  Roles.LimitedUser,
  Roles.ContractManagerOrg,
];
const DMS_ROLES: Role[] = [
  Roles.SuperAdmin,
  Roles.OrgAdmin,
  Roles.OrgUser,
  Roles.ProjectAdmin,
  Roles.ProjectUser,
  Roles.DocumentController,
  Roles.LimitedUser,
  Roles.ContractManagerOrg,
];
const DRAFTING_ROLES: Role[] = [
  Roles.SuperAdmin,
  Roles.OrgAdmin,
  Roles.OrgUser,
  Roles.ProjectAdmin,
  Roles.ProjectUser,
  Roles.DocumentController,
  Roles.ContractManagerOrg,
  Roles.DraftingManager,
  Roles.ExpertDrafter,
  Roles.ExpertReviewer,
];

// Keep frontend aliases aligned with backend core.permissions.LEGACY_PERMISSION_ALIASES
// plus the non-document org/project aliases retained by PermissionService.
// Document access is intentionally canonical-only, matching backend policy.
export const PERMISSION_ALIASES: Record<string, string[]> = {
  "dms.dashboard.view": ["projects:read"],
  "dms.report.view": ["reports:view"],
  "dms.user.manage": ["users:create", "users:update", "users:delete"],
  "dms.project.manage": [
    "projects:create",
    "projects:update",
    "projects:delete",
    "projects:assign",
  ],
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
  "organizations:read": ["orgs:view"],
  "organizations:create": ["orgs:create"],
  "organizations:update": ["orgs:edit"],
  "organizations:delete": ["orgs:delete"],
  "projects:read": ["projects:view"],
  "projects:update": ["projects:edit"],
};

const ALIAS_TO_CANONICAL = Object.entries(PERMISSION_ALIASES).reduce(
  (acc, [canonical, aliases]) => {
    for (const alias of aliases) {
      acc[alias] = [...(acc[alias] || []), canonical];
    }
    return acc;
  },
  {} as Record<string, string[]>,
);

export function normalizePermissionId(permission: string): string {
  const key = String(permission || "").trim().toLowerCase();
  if (!key) return "";
  return ALIAS_TO_CANONICAL[key]?.[0] || key;
}

export function expandPermissionAliases(permission: string): string[] {
  const key = String(permission || "").trim().toLowerCase();
  if (!key) return [];
  const canonicalPermissions = ALIAS_TO_CANONICAL[key] || [key];
  return Array.from(
    new Set([
      key,
      ...canonicalPermissions,
      ...canonicalPermissions.flatMap((canonical) => PERMISSION_ALIASES[canonical] || []),
      ...(PERMISSION_ALIASES[key] || []),
    ].filter(Boolean)),
  );
}

export function expandPermissionSet(permissions: Iterable<string>): Set<string> {
  const expanded = new Set<string>();
  for (const permission of permissions) {
    for (const candidate of expandPermissionAliases(permission)) {
      expanded.add(candidate);
    }
  }
  return expanded;
}

export const OPEN_AUTHENTICATED_ROUTES = [
  "/overview",
  "/security-terms",
  "/profile",
  "/notifications",
] as const;

export const ROUTE_PERMISSIONS: Record<string, string[]> = {
  "/overview": [], // Open to all authenticated users
  "/security-terms": [],
  "/dashboard": ["dms.dashboard.view"],
  // C3: align with backend permission names. `admin.user.create` does not exist
  // on the backend; user creation requires `users:create` (POST /api/users).
  "/register": ["users:create"],
  "/organizations": ["organizations:read"],
  "/projects": ["projects:read"],
  "/parties": ["parties:read"],
  "/representatives": ["representatives:read"],
  "/email-groups": ["email_groups:read"],
  "/upload": ["dms.document.upload"],
  "/documents": ["dms.document.view"],
  "/documentsearch": ["dms.document.view"],
  "/documentviewer": ["dms.document.view"],
  "/reference": ["dms.document.view"],
  "/share": ["dms.document.share"],
  // C3: canonical backend permission is `drafting.request.view` (default_roles.py,
  // letter_drafting.py), not `draft.request.view`.
  "/letters": ["drafting.request.view"],
  "/letter-quality": ["drafting.request.view"],
  "/letter-templates": ["letter_templates:read"],
  "/contracts": ["dms.document.view"],
  "/contracts/viewer": ["dms.document.view"],
  "/contracts/clauses": ["dms.document.view"],
  // Must be listed explicitly so it wins over the `/contracts` prefix match.
  "/contracts/appraisal": ["dms.contract.appraisal.view", "dms.document.view"],
  "/contracts/timeline": ["dms.contract.timeline.view", "dms.evidence_graph.view", "dms.document.view"],
  "/chronology": ["dms.chronology.view", "dms.document.view"],
  "/chronology/new": ["dms.chronology.create", "dms.chronology.view", "dms.document.view"],
  "/arbitration": ["dms.arbitration.view", "dms.document.view"],
  "/arbitration/cases": ["dms.arbitration.view", "dms.document.view"],
  "/arbitration/cases/new": ["dms.arbitration.create", "dms.arbitration.view", "dms.document.view"],
  "/arbitration/claim": ["dms.arbitration.create", "dms.arbitration.view", "dms.document.view"],
  "/arbitration/defence": ["dms.arbitration.create", "dms.arbitration.view", "dms.document.view"],
  "/arbitration/rejoinder": ["dms.arbitration.create", "dms.arbitration.view", "dms.document.view"],
  "/arbitration/counterclaim": ["dms.arbitration.create", "dms.arbitration.view", "dms.document.view"],
  "/arbitration/drafts": ["dms.arbitration.view", "dms.document.view"],
  "/claims": ["dms.claim.view", "dms.document.view"],
  "/sla": ["dms.claim.view", "dms.document.view"],
  "/key-dates": ["dms.keydate.view", "dms.document.view"],
  "/variations": ["dms.variation.view", "dms.document.view"],
  "/bank-guarantees": ["dms.bankguarantee.view", "dms.document.view"],
  "/insurance": ["dms.insurance.view", "dms.document.view"],
  "/ipc-bills": ["dms.ipc.view", "dms.document.view"],
  "/concerns": ["concerns:read"],
  "/admin/billing-catalog": ["billing.plan.manage", "system:admin"],
  "/retrieval-console": ["dms.document.view"],
  "/observability": ["reports:view", "system:admin"],
  // Contract Master (backend dms.contract.master.view aliased to projects:read).
  // Explicit so it wins over the /contracts prefix mapping.
  "/contracts/master": ["dms.contract.master.view", "dms.document.view"],
  // C3: no `dms.folder.view` permission exists on the backend; folders organize
  // documents, so gate on `dms.document.view` (matches /documents and /contracts).
  "/folders": ["dms.document.view"],
  "/reports": ["reports:view"],
  "/health": ["system:admin"],
  "/users": ["users:read"],
  "/permissions": ["roles:read", "permissions:read"],
  "/plan-settings": ["billing.plan.view", "subscription.entitlement.manage"],
  "/subscription-management": ["billing.plan.view", "subscription.entitlement.manage", "subscription.upgrade"],
  // Razorpay hosted-checkout return landing (Phase 4). Same billing audience.
  "/billing/return": ["billing.plan.view", "subscription.entitlement.manage", "subscription.upgrade"],
  "/settings": ["settings:view"],
  "/notifications": [],
  "/legal-words": [],
  "/admin/legal-words": ["system:admin"],
  "/profile": ["profile:read"],
  "/tags": ["tags:read"],
  // Tasks: backend currently gates by ownership/scope, not a task permission
  // (a `dms.task.*` family is planned for Phase 2). Accept `tasks:read` or any
  // document viewer so the (functional) Tasks module is reachable in the meantime.
  "/tasks": ["tasks:read", "dms.document.view"],
};

export type RouteAccessDescriptor = {
  normalizedPath: string;
  matchedPath: string | null;
  requiredAnyPermissions: string[];
  isOpen: boolean;
  isMapped: boolean;
};

export function normalizeRoutePath(path: string): string {
  return path.split("?")[0].replace(/\/+$/, "") || "/overview";
}

export function getRouteAccessDescriptor(path: string): RouteAccessDescriptor {
  const normalizedPath = normalizeRoutePath(path);
  if (OPEN_AUTHENTICATED_ROUTES.includes(normalizedPath as any)) {
    return {
      normalizedPath,
      matchedPath: normalizedPath,
      requiredAnyPermissions: [],
      isOpen: true,
      isMapped: true,
    };
  }

  const exact = ROUTE_PERMISSIONS[normalizedPath];
  if (exact) {
    return {
      normalizedPath,
      matchedPath: normalizedPath,
      requiredAnyPermissions: exact,
      isOpen: exact.length === 0,
      isMapped: true,
    };
  }

  const matchedPath = Object.keys(ROUTE_PERMISSIONS)
    .sort((a, b) => b.length - a.length)
    .find((base) => normalizedPath === base || normalizedPath.startsWith(base + "/"));

  if (!matchedPath) {
    return {
      normalizedPath,
      matchedPath: null,
      requiredAnyPermissions: [],
      isOpen: false,
      isMapped: false,
    };
  }

  const requiredAnyPermissions = ROUTE_PERMISSIONS[matchedPath];
  return {
    normalizedPath,
    matchedPath,
    requiredAnyPermissions,
    isOpen: requiredAnyPermissions.length === 0,
    isMapped: true,
  };
}

export function isRouteAllowedByPermission(
  can: (perm: string) => boolean,
  path: string
): boolean {
  const descriptor = getRouteAccessDescriptor(path);
  if (!descriptor.isMapped) return false;
  if (descriptor.isOpen) return true;
  return descriptor.requiredAnyPermissions.some((p) => can(p));
}
