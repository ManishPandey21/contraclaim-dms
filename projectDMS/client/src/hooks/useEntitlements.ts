import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PERMISSION_CONTRACT_VERSION } from "@/config/rolePermissions";
import {
  getCurrentEntitlements,
  type CurrentEntitlements,
} from "@/services/plan-settings-api";


export type EntitlementState = {
  entitlements: CurrentEntitlements | null;
  loading: boolean;
  error: string | null;
  hasFeature: (feature: string) => boolean;
  refresh: () => Promise<void>;
};

export function useEntitlements(enabled = true): EntitlementState {
  const [entitlements, setEntitlements] = useState<CurrentEntitlements | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasResolvedRef = useRef(false);

  const refresh = useCallback(async () => {
    // Only the initial entitlement lookup blocks the protected application
    // shell. Tenant/auth events are background refreshes: making those set
    // `loading` to true causes ProtectedRoute to unmount MainLayout, which
    // recreates TenantProvider and emits another tenant-context-changed event.
    // That feedback loop repeatedly remounts every protected page.
    if (!hasResolvedRef.current) setLoading(true);
    setError(null);
    try {
      const next = await getCurrentEntitlements();
      if (next.contract_version !== PERMISSION_CONTRACT_VERSION) {
        setEntitlements(null);
        setError("permission_contract_version_mismatch");
        return;
      }
      setEntitlements(next);
    } catch (requestError: any) {
      setEntitlements(null);
      const detail = requestError?.response?.data?.detail;
      setError(
        typeof detail === "string"
          ? detail
          : typeof detail?.message === "string"
            ? detail.message
            : requestError?.message || "entitlements_unavailable",
      );
    } finally {
      hasResolvedRef.current = true;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      hasResolvedRef.current = false;
      setEntitlements(null);
      setError(null);
      setLoading(false);
      return;
    }
    void refresh();
    const listener = () => void refresh();
    window.addEventListener("tenant-context-changed", listener);
    window.addEventListener("auth-state-changed", listener);
    return () => {
      window.removeEventListener("tenant-context-changed", listener);
      window.removeEventListener("auth-state-changed", listener);
    };
  }, [enabled, refresh]);

  const hasFeature = useMemo(
    () => (feature: string) => {
      if (!entitlements || error) return false;
      if (entitlements.features?.["*"] === true) return true;
      return entitlements.features?.[feature] === true;
    },
    [entitlements, error],
  );

  return { entitlements, loading, error, hasFeature, refresh };
}

export default useEntitlements;
