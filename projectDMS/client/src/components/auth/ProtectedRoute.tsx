import { lazy, Suspense } from "react";
import { useAuth } from "@/hooks/use-auth";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import useRBAC from "@/hooks/useRBAC";
import { isRouteAllowedByPermission } from "@/config/rolePermissions";
import RouteSkeleton from "@/components/layout/RouteSkeleton";
import AccessDenied from "@/components/auth/AccessDenied";
import { getSecurityTermsStatus } from "@/services/security-terms-api";
import { useEffect, useState } from "react";

const LoginPage = lazy(() => import("@/pages/LoginPage"));

const InlineLogin = () => (
  <Suspense fallback={<RouteSkeleton />}>
    <LoginPage />
  </Suspense>
);

const SECURITY_TERMS_PATH = "/security-terms";

// Protected Route component
const ProtectedRoute = ({ children }) => {
  const { isAuthenticated, isAuthLoading } = useAuth();
  const location = useLocation();
  const { roles, can, loading, error } = useRBAC();
  const [termsState, setTermsState] = useState<{
    loading: boolean;
    requiresAcceptance: boolean;
    error: boolean;
  }>({ loading: true, requiresAcceptance: false, error: false });

  useEffect(() => {
    const accepted = () => {
      setTermsState({ loading: false, requiresAcceptance: false, error: false });
    };
    window.addEventListener("security-terms-accepted", accepted);
    return () => window.removeEventListener("security-terms-accepted", accepted);
  }, []);

  useEffect(() => {
    let active = true;
    if (!isAuthenticated) {
      setTermsState({ loading: false, requiresAcceptance: false, error: false });
      return () => {
        active = false;
      };
    }
    setTermsState((prev) => ({ ...prev, loading: true, error: false }));
    getSecurityTermsStatus()
      .then((status) => {
        if (active) {
          setTermsState({
            loading: false,
            requiresAcceptance: Boolean(status.requires_acceptance),
            error: false,
          });
        }
      })
      .catch(() => {
        if (active) {
          setTermsState({ loading: false, requiresAcceptance: true, error: true });
        }
      });
    return () => {
      active = false;
    };
  }, [isAuthenticated, location.pathname]);

  if (isAuthLoading) {
    return <RouteSkeleton />;
  }

  if (!isAuthenticated) {
    // Render LoginPage in-place to keep URL at "/" per requirement
    return <InlineLogin />;
  }

  if (termsState.loading) {
    return <RouteSkeleton />;
  }

  if ((termsState.requiresAcceptance || termsState.error) && location.pathname !== SECURITY_TERMS_PATH) {
    return <Navigate to={SECURITY_TERMS_PATH} replace state={{ from: location.pathname }} />;
  }

  if (location.pathname === SECURITY_TERMS_PATH) {
    return children ? children : <Outlet />;
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
    return <AccessDenied path={location.pathname} fallback="/overview" />;
  }

  return children ? children : <Outlet />;
};

export default ProtectedRoute;
