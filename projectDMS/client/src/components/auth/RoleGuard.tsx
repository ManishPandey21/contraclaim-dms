import React, { PropsWithChildren } from "react";
import useRBAC from "@/hooks/useRBAC";
import { isRouteAllowedByPermission } from "@/config/rolePermissions";
import AccessDenied from "@/components/auth/AccessDenied";

/**
 * Guards a given route element by checking user roles against ROUTE_RULES.
 * If unauthorized, renders a clear fallback page with the required permission.
 */
type RoleGuardProps = {
  path: string; // absolute app path, e.g. "/register"
  fallback?: string; // default "/overview"
};

const RoleGuard: React.FC<PropsWithChildren<RoleGuardProps>> = ({
  path,
  fallback = "/overview",
  children,
}) => {
  const { can, loading } = useRBAC();

  if (loading) {
    // Avoid flicker/false-deny while roles are loading
    return null;
  }

  const allowed = isRouteAllowedByPermission(can, path);
  if (!allowed) {
    return <AccessDenied path={path} fallback={fallback} />;
  }

  return <>{children}</>;
};

export default RoleGuard;
