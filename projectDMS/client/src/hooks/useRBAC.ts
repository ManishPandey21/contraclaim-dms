import { useEffect, useMemo, useState } from "react";
import { enhancedApi as api, Role } from "@/services/enhanced-api";
import { joinApiUrl } from "@/config/api";

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

function parseLocalRoles(): string[] {
  const raw = window.localStorage.getItem("user_roles") || "";
  if (!raw) return [];
  try {
    const asJson = JSON.parse(raw);
    if (Array.isArray(asJson))
      return asJson.map((x) => normalizeRoleId(String(x)));
  } catch {
    // not JSON, fall through
  }
  return raw
    .split(",")
    .map((x) => normalizeRoleId(x))
    .filter(Boolean);
}

let cachedPermissions: Set<string> | null = null;
let cachedAt = 0;

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
  // Keep roles in state so we can populate from /me after JWT login if localStorage is empty
  const [roles, setRoles] = useState<string[]>(() => parseLocalRoles());

  // If roles are empty but we have a JWT, fetch /me to synchronize roles and update localStorage
  useEffect(() => {
    (async () => {
      try {
        if (roles.length > 0) return;
        const token = window.localStorage.getItem("accessToken");
        if (!token) return;
        const res = await fetch(joinApiUrl("/me"), {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) return;
        const me = await res.json();
        const fetched = Array.isArray(me?.roles)
          ? (me.roles as string[]).map((r) => normalizeRoleId(String(r)))
          : [];
        if (fetched.length > 0) {
          setRoles(fetched);
          try {
            window.localStorage.setItem("user_roles", JSON.stringify(fetched));
          } catch {
            window.localStorage.setItem("user_roles", fetched.join(","));
          }
        }
      } catch {
        // Non-fatal; fall back to existing local roles
      }
    })();
  }, []);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        setLoading(true);
        setError(null);

        // If roles are not yet resolved but we have a JWT, wait for the /me sync effect
        // to populate roles. This prevents premature "access denied" UI.
        if (
          roles.length === 0 &&
          typeof window !== "undefined" &&
          window.localStorage.getItem("accessToken")
        ) {
          return; // keep loading=true; effect above will set roles then re-run
        }

        // Superadmin short-circuit
        if (roles.includes("superadmin")) {
          const superSet = new Set<string>(["*"]);
          if (mounted) {
            setPerms(superSet);
            cachedPermissions = superSet;
            cachedAt = Date.now();
            setLoading(false);
          }
          return;
        }

        // Use simple in-memory cache (5 minutes)
        if (cachedPermissions && Date.now() - cachedAt < 5 * 60 * 1000) {
          if (mounted) {
            setPerms(new Set(cachedPermissions));
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
            (role.permissions || []).forEach((p) =>
              collected.add(normalizePermissionId(p))
            );
          }
        }

        if (mounted) {
          setPerms(collected);
          cachedPermissions = new Set(collected);
          cachedAt = Date.now();
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
  }, [roles.join(",")]);

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
