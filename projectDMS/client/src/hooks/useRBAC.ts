import { useEffect, useMemo, useState } from "react";
import { enhancedApi as api, Role } from "@/services/enhanced-api";
import { getCurrentUserProfile } from "@/services/session-api";

// Normalize role id strings similar to backend ROLE_ALIASES in core/security.py
const ROLE_ALIASES: Record<string, string> = {
  "organization-user": "orguser",
  "org-user": "orguser",
  "organization user": "orguser",
  organizationuser: "orguser",
  orguser: "orguser",
  admin: "superadmin",
  administrator: "superadmin",
  "organization-admin": "orgadmin",
  "org-admin": "orgadmin",
  "organization admin": "orgadmin",
  organizationadmin: "orgadmin",
  orgadmin: "orgadmin",
  "project-user": "projectuser",
  "project user": "projectuser",
  projectuser: "projectuser",
  "project-admin": "projectadmin",
  "project admin": "projectadmin",
  projectadmin: "projectadmin",
  "super-admin": "superadmin",
  "super admin": "superadmin",
  superadministrator: "superadmin",
  "super-user": "superuser",
  "super user": "superuser",
  superuser: "superuser",
  superadmin: "superadmin",
  "document-controller": "doccontroller",
  "document controller": "doccontroller",
  documentcontroller: "doccontroller",
  doccontroller: "doccontroller",
  reporter: "reporter",
  auditor: "reporter",
  "settings-manager": "settings_manager",
  "settings manager": "settings_manager",
  settingsmanager: "settings_manager",
  settings_manager: "settings_manager",
  "limited-user": "limited_user",
  "limited user": "limited_user",
  limiteduser: "limited_user",
  limited_user: "limited_user",
  "contract-manager-organization": "contractmgr_org",
  "contract manager organization": "contractmgr_org",
  "contract manager - organization": "contractmgr_org",
  contractmgr_org: "contractmgr_org",
  "contraclaim drafting manager": "contraclaim_drafting_manager",
  contraclaim_drafting_manager: "contraclaim_drafting_manager",
  "contraclaim expert drafter": "contraclaim_expert_drafter",
  "contraclaim contract expert - drafter": "contraclaim_expert_drafter",
  contraclaim_expert_drafter: "contraclaim_expert_drafter",
  "contraclaim expert reviewer": "contraclaim_expert_reviewer",
  "contraclaim contract expert - reviewer": "contraclaim_expert_reviewer",
  contraclaim_expert_reviewer: "contraclaim_expert_reviewer",
  "contraclaim billing admin": "contraclaim_billing_admin",
  contraclaim_billing_admin: "contraclaim_billing_admin",
};

function normalizeRoleId(r: string): string {
  const s = String(r || "")
    .trim()
    .toLowerCase();
  return ROLE_ALIASES[s] || s;
}

const PERMISSION_ALIASES: Record<string, string> = {
  "docs:view": "documents:read",
  "docs:create": "documents:create",
  "docs:edit": "documents:update",
  "docs:update": "documents:update",
  "docs:delete": "documents:delete",
  "docs:approve": "documents:approve",
  "docs:share": "documents:share",
  "docs:upload": "documents:upload",
  "docs:comment": "documents:comment",
  "docs:download_all": "documents:download_all",
  "docs:download-all": "documents:download_all",
  "docs:download": "dms.document.download",
  "documents:download": "dms.document.download",
  "dms.document.view": "dms.document.view",
  "dms.document.upload": "dms.document.upload",
  "dms.document.edit_metadata": "dms.document.edit_metadata",
  "dms.document.delete": "dms.document.delete",
  "dms.document.download": "dms.document.download",
  "dms.document.bulk_download": "dms.document.bulk_download",
  "drafting.request.create": "drafting.request.create",
  "letters:view": "documents:read",
  "letters:create": "documents:create",
  "letters:edit": "documents:update",
  "letters:delete": "documents:delete",
  "letters:upload": "documents:upload",
  "letters:comment": "documents:comment",
  "projects:view": "projects:read",
  "projects:edit": "projects:update",
  "orgs:view": "organizations:read",
  "orgs:create": "organizations:create",
  "orgs:edit": "organizations:update",
  "orgs:delete": "organizations:delete",
};

function normalizePermissionId(p: string): string {
  const key = String(p || "")
    .trim()
    .toLowerCase();
  return PERMISSION_ALIASES[key] || key;
}

function expandPermissionAliases(permission: string): string[] {
  const legacyToCanonical: Record<string, string[]> = {
    "documents:read": ["dms.document.view"],
    "documents:create": ["dms.document.upload"],
    "documents:upload": ["dms.document.upload"],
    "documents:update": ["dms.document.edit_metadata", "dms.status.update"],
    "documents:delete": ["dms.document.delete"],
    "documents:download_all": ["dms.document.bulk_download"],
    "documents:comment": ["dms.comment.add"],
    "reports:view": ["dms.report.view"],
    "dms.document.view": ["documents:read"],
    "dms.document.upload": ["documents:create", "documents:upload"],
    "dms.document.edit_metadata": ["documents:update"],
    "dms.document.delete": ["documents:delete"],
    "dms.document.bulk_download": ["documents:download_all"],
    "dms.comment.add": ["documents:comment"],
    "dms.report.view": ["reports:view"],
  };
  return [permission, ...(legacyToCanonical[permission] || [])];
}

const permissionCache = new Map<
  string,
  { permissions: Set<string>; cachedAt: number }
>();

export interface UseRBACResult {
  roles: string[];
  permissions: Set<string>;
  can: (permId: string) => boolean;
  loading: boolean;
  error: string | null;
}

export function useRBAC(): UseRBACResult {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [perms, setPerms] = useState<Set<string>>(new Set());
  const [roles, setRoles] = useState<string[]>([]);
  // Whether the first role fetch has completed. Until it has, an empty `roles`
  // means "still loading", not "no access" — otherwise a freshly-mounted
  // RoleGuard would deny and bounce deep-links to /overview before roles arrive.
  const [rolesResolved, setRolesResolved] = useState(false);

  useEffect(() => {
    let mounted = true;
    const refreshRoles = async () => {
      try {
        const me = await getCurrentUserProfile();
        const fetched = Array.isArray(me?.roles)
          ? (me.roles as string[]).map((r) => normalizeRoleId(String(r)))
          : typeof me?.roles === "string"
            ? me.roles
                .split(",")
                .map((r) => normalizeRoleId(r))
                .filter(Boolean)
            : [];
        if (mounted) setRoles(fetched);
      } catch {
        if (mounted) setRoles([]);
      } finally {
        if (mounted) setRolesResolved(true);
      }
    };

    void refreshRoles();
    const listener = () => void refreshRoles();
    window.addEventListener("auth-state-changed", listener);
    return () => {
      mounted = false;
      window.removeEventListener("auth-state-changed", listener);
    };
  }, []);

  const roleKey = useMemo(() => roles.join(","), [roles]);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        // If roles are not yet resolved but we have a JWT, wait for the /me sync effect
        // to populate roles. This prevents premature "access denied" UI: stay in the
        // loading state until the first role fetch has actually completed.
        if (roles.length === 0) {
          if (mounted) setLoading(!rolesResolved);
          return;
        }

        // Superadmin short-circuit
        if (roles.includes("superadmin")) {
          const superSet = new Set<string>(["*"]);
          if (mounted) {
            setPerms(superSet);
            permissionCache.set(roleKey, {
              permissions: superSet,
              cachedAt: Date.now(),
            });
            setLoading(false);
          }
          return;
        }

        // Use simple in-memory cache (5 minutes)
        const cached = permissionCache.get(roleKey);
        if (cached && Date.now() - cached.cachedAt < 5 * 60 * 1000) {
          if (mounted) {
            setPerms(new Set(cached.permissions));
            setLoading(false);
          }
          return;
        }

        const allRoles: Role[] = await api.getRoles();
        const wanted = new Set(roles);
        const collected = new Set<string>();

        for (const role of allRoles) {
          const rid = normalizeRoleId(role._id);
          if (wanted.has(rid)) {
            (role.permissions || []).forEach((p) => {
              const normalized = normalizePermissionId(p);
              expandPermissionAliases(normalized).forEach((candidate) =>
                collected.add(candidate)
              );
            });
          }
        }

        if (mounted) {
          setPerms(collected);
          permissionCache.set(roleKey, {
            permissions: new Set(collected),
            cachedAt: Date.now(),
          });
          setLoading(false);
        }
      } catch (e: any) {
        if (mounted) {
          setError(e?.message || "Failed to load RBAC permissions");
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      mounted = false;
    };
  }, [roleKey, roles, rolesResolved]);

  const can = (permId: string) => {
    if (!permId) return false;
    if (roles.includes("superadmin")) return true;
    if (
      permId === "users:read" &&
      roles.some((role) =>
        ["orgadmin", "orguser", "projectadmin", "projectuser"].includes(role)
      )
    ) {
      return true;
    }
    return perms.has(permId);
    // Wildcard not used here; superadmin covers full access.
  };

  return { roles, permissions: perms, can, loading, error };
}

export default useRBAC;
