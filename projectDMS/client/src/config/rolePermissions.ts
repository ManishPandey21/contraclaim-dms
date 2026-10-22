/**
 * Central RBAC configuration for routes and sidebar.
 * Frontend checks are UX only; backend policy remains authoritative.
 */

import {
  PERMISSION_ALIASES as GENERATED_PERMISSION_ALIASES,
  PERMISSION_CONTRACT_VERSION,
  ROUTE_ACCESS_RULES,
} from "./generatedPermissionContract";

export { PERMISSION_CONTRACT_VERSION };

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
export const PERMISSION_ALIASES: Record<string, readonly string[]> =
  GENERATED_PERMISSION_ALIASES;

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

export const ROUTE_PERMISSIONS: Record<string, readonly string[]> =
  Object.fromEntries(
    Object.entries(ROUTE_ACCESS_RULES).map(([path, rule]) => [
      path,
      rule.required_any_permissions,
    ]),
  );

export const OPEN_AUTHENTICATED_ROUTES = Object.entries(ROUTE_ACCESS_RULES)
  .filter(([, rule]) => rule.open_authenticated)
  .map(([path]) => path);

export type RouteAccessDescriptor = {
  normalizedPath: string;
  matchedPath: string | null;
  requiredAnyPermissions: string[];
  requiredAllPermissions: string[];
  requiredAllFeatures: string[];
  isOpen: boolean;
  isMapped: boolean;
};

export function normalizeRoutePath(path: string): string {
  return path.split("?")[0].replace(/\/+$/, "") || "/overview";
}

export function getRouteAccessDescriptor(path: string): RouteAccessDescriptor {
  const normalizedPath = normalizeRoutePath(path);
  const rules = ROUTE_ACCESS_RULES as unknown as Record<
    string,
    {
      required_any_permissions: readonly string[];
      required_all_permissions: readonly string[];
      required_all_features: readonly string[];
      open_authenticated: boolean;
    }
  >;
  const exact = rules[normalizedPath];
  if (exact) {
    return {
      normalizedPath,
      matchedPath: normalizedPath,
      requiredAnyPermissions: [...exact.required_any_permissions],
      requiredAllPermissions: [...exact.required_all_permissions],
      requiredAllFeatures: [...exact.required_all_features],
      isOpen: exact.open_authenticated,
      isMapped: true,
    };
  }

  const matchedPath = Object.keys(rules)
    .sort((a, b) => b.length - a.length)
    .find((base) => normalizedPath === base || normalizedPath.startsWith(base + "/"));

  if (!matchedPath) {
    return {
      normalizedPath,
      matchedPath: null,
      requiredAnyPermissions: [],
      requiredAllPermissions: [],
      requiredAllFeatures: [],
      isOpen: false,
      isMapped: false,
    };
  }

  const rule = rules[matchedPath];
  return {
    normalizedPath,
    matchedPath,
    requiredAnyPermissions: [...rule.required_any_permissions],
    requiredAllPermissions: [...rule.required_all_permissions],
    requiredAllFeatures: [...rule.required_all_features],
    isOpen: rule.open_authenticated,
    isMapped: true,
  };
}

export function isRouteAllowedByEntitlement(
  hasFeature: (feature: string) => boolean,
  path: string,
): boolean {
  const descriptor = getRouteAccessDescriptor(path);
  if (!descriptor.isMapped) return false;
  return descriptor.requiredAllFeatures.every((feature) => hasFeature(feature));
}

export function isRouteAllowedByPermission(
  can: (perm: string) => boolean,
  path: string
): boolean {
  const descriptor = getRouteAccessDescriptor(path);
  if (!descriptor.isMapped) return false;
  if (descriptor.isOpen) return true;
  if (!descriptor.requiredAllPermissions.every((p) => can(p))) return false;
  return (
    descriptor.requiredAnyPermissions.length === 0 ||
    descriptor.requiredAnyPermissions.some((p) => can(p))
  );
}
