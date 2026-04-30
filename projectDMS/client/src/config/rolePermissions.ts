/**
 * Central RBAC configuration for routes and sidebar.
 * Extend Roles and ROUTE_RULES to evolve permissions easily.
 */

export const Roles = {
  SuperAdmin: "superadmin",
  OrgAdmin: "orgadmin",
  OrgUser: "orguser",
  ProjectAdmin: "projectadmin",
  ProjectUser: "projectuser",
} as const;

export type Role = (typeof Roles)[keyof typeof Roles];

/**
 * Route-level rules.
 * - If a path is listed here, access is limited to the "allow" roles.
 * - If a path is not listed, it is accessible to all authenticated roles by default.
 */
export const ROUTE_RULES: Record<
  string,
  {
    allow: Role[];
  }
> = {
  "/register": { allow: [Roles.SuperAdmin] },
  // Letter drafting should be available to all authenticated roles by default.
  "/letters": {
    allow: [
      Roles.SuperAdmin,
      Roles.OrgAdmin,
      Roles.OrgUser,
      Roles.ProjectAdmin,
      Roles.ProjectUser,
    ],
  },
  "/health": { allow: [Roles.SuperAdmin] },
};

/**
 * Returns true if any of userRoles is in allowed set or the user is super-admin/user.
 */
export function hasAnyRole(userRoles: string[], allowed: Role[]): boolean {
  const normalized = userRoles.map((r) => String(r).toLowerCase());
  if (normalized.includes(Roles.SuperAdmin)) {
    return true; // supers can access everything
  }
  const allowSet = new Set(allowed);
  return normalized.some((r) => allowSet.has(r as Role));
}

/**
 * Check if the given roles are allowed to access a path.
 * Default allow when no rule exists.
 */
export function isRouteAllowed(userRoles: string[], path: string): boolean {
  const normalized = userRoles.map((r) => String(r).toLowerCase());

  // Health dashboard is restricted to superadmin only
  if (path === "/health" || path.startsWith("/health")) {
    return normalized.includes(Roles.SuperAdmin);
  }

  // Exact match first
  const exact = ROUTE_RULES[path];
  if (exact) return hasAnyRole(normalized, exact.allow);

  // Prefix match for nested routes (e.g., "/letters/123")
  const matchedKey = Object.keys(ROUTE_RULES).find(
    (base) => path === base || path.startsWith(base + "/")
  );
  if (!matchedKey) return true; // unrestricted when no rules match
  return hasAnyRole(normalized, ROUTE_RULES[matchedKey].allow);
}
