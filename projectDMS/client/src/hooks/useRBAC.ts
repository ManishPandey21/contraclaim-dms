import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentUserProfile } from "@/services/session-api";
import {
  EXPLICIT_GRANT_PERMISSIONS,
  RBAC_ACCOUNT_EVENTS,
  RBAC_REVALIDATE_EVENTS,
  normalizeRoleId,
} from "@/config/rolePermissions";

/**
 * The signed-in user's permissions, as the server's effective list (`/me`) states them.
 *
 * T-2 (owner decision 2026-11-19). The server list is authoritative and complete: every
 * name in it passes both kinds of backend gate, and it already includes each check a held
 * name passes. So:
 *
 * - **No role-catalogue fallback.** An empty list returned by the server means "no
 *   permissions"; the hook never downloads the role catalogue and expands roles itself
 *   (that ignored scope, and ordinary users cannot read the catalogue anyway).
 * - **No client-side alias expansion.** `can()` matches the server list exactly; the
 *   client's two-hop alias table could grant what the server refuses.
 * - **Three states.** `loading` (nothing is known: deny), `error` (the profile could not
 *   be read: deny, and say so), `ready` (the server's answer, possibly empty).
 * - **Fail closed on account change.** Signing in or out clears what was known and denies
 *   until the server answers. A profile or scope change re-reads the server (`force`) in
 *   the background, keeping the current answer meanwhile. An answer to an older request
 *   never overwrites a newer one.
 * - **The server list decides alone.** `["*"]` (Super Admin) passes every check except the
 *   explicit-grant permissions (`EXPLICIT_GRANT_PERMISSIONS`), which need the name itself
 *   in the list; profile role names are not consulted. The backend enforces every privilege
 *   regardless; this only decides what the UI offers.
 */

export type RBACStatus = "loading" | "error" | "ready";

export interface UseRBACResult {
  roles: string[];
  permissions: Set<string>;
  can: (permId: string) => boolean;
  loading: boolean;
  error: string | null;
  status: RBACStatus;
}

interface RBACState {
  status: RBACStatus;
  roles: string[];
  permissions: Set<string>;
  error: string | null;
}

const LOADING: RBACState = { status: "loading", roles: [], permissions: new Set(), error: null };

function listFrom(value: unknown, normalise: (item: string) => string): string[] {
  const items = Array.isArray(value)
    ? value.map((item) => String(item))
    : typeof value === "string"
      ? value.split(",")
      : [];
  return items.map((item) => normalise(item.trim())).filter(Boolean);
}

export function decide(state: RBACState, permId: string): boolean {
  const key = String(permId || "").trim().toLowerCase();
  if (!key || state.status !== "ready") return false;
  if (EXPLICIT_GRANT_PERMISSIONS.has(key)) {
    // Never through "*" or the Super Admin role: only a grant of the name itself.
    return state.permissions.has(key);
  }
  // Decided from the server's list only, never from the role names in the profile: the
  // server drops revoked or deactivated role references (a stored "superadmin" whose role
  // is deactivated is not "*"), and already lists users:read where its role rule grants it.
  if (state.permissions.has("*")) return true;
  return state.permissions.has(key);
}

export function useRBAC(): UseRBACResult {
  const [state, setState] = useState<RBACState>(LOADING);
  const request = useRef(0);

  const refresh = useCallback(async (force: boolean, clear: boolean) => {
    const id = ++request.current;
    // A different account (sign-in, sign-out): forget everything and deny until the
    // server answers. A profile or scope change on the same account re-reads in the
    // background instead: clearing there unmounts the layout (ProtectedRoute shows its
    // skeleton while loading), and the layout's TenantProvider announcing its scope on
    // mount then looped forever (independent review, 2026-11-19, Critical).
    if (clear) setState(LOADING);
    try {
      const me = await getCurrentUserProfile({ force });
      if (id !== request.current) return; // a newer request owns the state
      setState({
        status: "ready",
        roles: listFrom(me?.roles, normalizeRoleId),
        permissions: new Set(listFrom(me?.permissions, (item) => item.toLowerCase())),
        error: null,
      });
    } catch (e: any) {
      if (id !== request.current) return;
      setState({
        status: "error",
        roles: [],
        permissions: new Set(),
        error: e?.message || "Your permissions could not be loaded.",
      });
    }
  }, []);

  useEffect(() => {
    void refresh(false, true);
    const onAccount = () => void refresh(true, true);
    const onSameAccount = () => void refresh(true, false);
    for (const event of RBAC_ACCOUNT_EVENTS) window.addEventListener(event, onAccount);
    for (const event of RBAC_REVALIDATE_EVENTS) window.addEventListener(event, onSameAccount);
    return () => {
      request.current += 1; // ignore any answer that arrives after unmount
      for (const event of RBAC_ACCOUNT_EVENTS) window.removeEventListener(event, onAccount);
      for (const event of RBAC_REVALIDATE_EVENTS) window.removeEventListener(event, onSameAccount);
    };
  }, [refresh]);

  const can = useCallback((permId: string) => decide(state, permId), [state]);

  return {
    roles: state.roles,
    permissions: state.permissions,
    can,
    loading: state.status === "loading",
    error: state.error,
    status: state.status,
  };
}

export default useRBAC;
