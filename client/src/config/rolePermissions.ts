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

export const ROUTE_PERMISSIONS: Record<string, string[]> = {
  "/overview": [], // Open to all authenticated users
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
  // C3: backend permission is `documents:share` (routers/email_share.py), not `dms.document.share`.
  "/share": ["documents:share"],
  // C3: canonical backend permission is `drafting.request.view` (default_roles.py,
  // letter_drafting.py), not `draft.request.view`.
  "/letters": ["drafting.request.view"],
  "/letter-quality": ["drafting.request.view"],
  "/letter-templates": ["letter_templates:read"],
  "/contracts": ["dms.document.view"],
  // C3: no `dms.folder.view` permission exists on the backend; folders organize
  // documents, so gate on `dms.document.view` (matches /documents and /contracts).
  "/folders": ["dms.document.view"],
  "/reports": ["reports:view"],
  "/health": ["system:admin"],
  "/users": ["users:read"],
  "/permissions": ["roles:read"],
  "/plan-settings": ["subscription.entitlement.manage"],
  "/subscription-management": ["subscription.entitlement.manage", "subscription.upgrade"],
  "/settings": ["settings:view"],
  "/notifications": [],
  "/profile": ["profile:read"],
  "/tags": ["tags:read"],
  "/tasks": ["tasks:read"],
};

export function isRouteAllowedByPermission(
  can: (perm: string) => boolean,
  path: string
): boolean {
  const normalizedPath = path.split("?")[0].replace(/\/+$/, "") || "/overview";
  
  if (normalizedPath === "/overview" || normalizedPath === "/profile" || normalizedPath === "/notifications") {
    return true; // Universally allowed authenticated routes
  }

  const exact = ROUTE_PERMISSIONS[normalizedPath];
  if (exact) {
    if (exact.length === 0) return true;
    return exact.some((p) => can(p));
  }

  const matchedKey = Object.keys(ROUTE_PERMISSIONS)
    .sort((a, b) => b.length - a.length)
    .find((base) => normalizedPath === base || normalizedPath.startsWith(base + "/"));

  if (!matchedKey) return false;
  
  const required = ROUTE_PERMISSIONS[matchedKey];
  if (required.length === 0) return true;
  return required.some((p) => can(p));
}
