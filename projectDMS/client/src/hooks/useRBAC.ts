import { useEffect, useMemo, useState } from "react";
import { enhancedApi as api, Role } from "@/services/enhanced-api";
import { getCurrentUserProfile } from "@/services/session-api";
import {
  expandPermissionAliases,
  expandPermissionSet,
  normalizeRoleId,
} from "@/config/rolePermissions";

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
              expandPermissionAliases(p).forEach((candidate) =>
                collected.add(candidate)
              );
            });
          }
        }

        const expanded = expandPermissionSet(collected);

        if (mounted) {
          setPerms(expanded);
          permissionCache.set(roleKey, {
            permissions: new Set(expanded),
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
    return expandPermissionAliases(permId).some((candidate) =>
      perms.has(candidate)
    );
    // Wildcard not used here; superadmin covers full access.
  };

  return { roles, permissions: perms, can, loading, error };
}

export default useRBAC;
