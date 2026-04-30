import { useEffect, useState } from "react";
import { enhancedApi as api } from "@/services/enhanced-api";

/**
 * Lightweight permission check hook that queries the backend for the current user.
 * Returns a boolean indicating whether the user has the requested permission.
 */
const useHasPermission = (permissionName: string) => {
  const [hasPermission, setHasPermission] = useState(false);

  useEffect(() => {
    let mounted = true;
    if (!permissionName) {
      setHasPermission(false);
      return undefined;
    }

    api
      .checkUserPermission(permissionName)
      .then((result) => {
        if (!mounted) return;
        const granted =
          typeof result === "object" && result !== null && "granted" in result
            ? Boolean((result as any).granted)
            : Boolean(result);
        setHasPermission(granted);
      })
      .catch(() => {
        if (mounted) setHasPermission(false);
      });

    return () => {
      mounted = false;
    };
  }, [permissionName]);

  return hasPermission;
};

export default useHasPermission;
