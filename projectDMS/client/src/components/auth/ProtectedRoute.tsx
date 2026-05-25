import { lazy, Suspense } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import useRBAC from "@/hooks/useRBAC";
import { isRouteAllowedByPermission } from "@/config/rolePermissions";
import RouteSkeleton from "@/components/layout/RouteSkeleton";

const LoginPage = lazy(() => import("@/pages/LoginPage"));

const InlineLogin = () => (
  <Suspense fallback={<RouteSkeleton />}>
    <LoginPage />
  </Suspense>
);

// Protected Route component
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated, isAuthLoading } = useAuth();
  const location = useLocation();
  const { roles, can, loading, error } = useRBAC();

  if (isAuthLoading) {
    return <RouteSkeleton />;
  }

  if (!isAuthenticated) {
    // Render LoginPage in-place to keep URL at "/" per requirement
    return <InlineLogin />;
  }

  if (loading) {
    return <RouteSkeleton />;
  }

  if (error && roles.length === 0) {
    return <InlineLogin />;
  }

  if (!isRouteAllowedByPermission(can, location.pathname)) {
    if (location.pathname === "/overview") {
      return <InlineLogin />;
    }
    return <Navigate to="/overview" replace />;
  }

  return children ? children : <Outlet />;
};

export default ProtectedRoute;
