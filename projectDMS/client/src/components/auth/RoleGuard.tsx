import React, { PropsWithChildren } from "react";
import { Navigate } from "react-router-dom";
import useRBAC from "@/hooks/useRBAC";
import { isRouteAllowedByPermission } from "@/config/rolePermissions";

/**
 * Guards a given route element by checking user roles against ROUTE_RULES.
 * If unauthorized, redirects to a safe fallback (Overview).
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
    return <Navigate to={fallback} replace />;
  }

  return <>{children}</>;
};

export default RoleGuard;
