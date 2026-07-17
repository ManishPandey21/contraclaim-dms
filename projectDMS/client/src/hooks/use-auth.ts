import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { isUnauthorizedError, isForbiddenError } from "../utils/error-handler";
import { getCurrentUserProfile } from "../services/session-api";
import { publicApi } from "../services/http";
import { logError } from "../lib/error-logger";

export const useAuth = () => {
  const navigate = useNavigate();
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isAuthLoading, setIsAuthLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const refreshAuthState = async () => {
      try {
        await getCurrentUserProfile();
        if (mounted) setIsAuthenticated(true);
      } catch {
        if (mounted) setIsAuthenticated(false);
      } finally {
        if (mounted) setIsAuthLoading(false);
      }
    };

    void refreshAuthState();
    const listener = () => void refreshAuthState();
    window.addEventListener("auth-state-changed", listener);
    window.addEventListener("focus", listener);
    return () => {
      mounted = false;
      window.removeEventListener("auth-state-changed", listener);
      window.removeEventListener("focus", listener);
    };
  }, []);

  const handleAuthError = useCallback(
    (error: unknown) => {
      if (isUnauthorizedError(error)) {
        localStorage.removeItem("accessToken");
        navigate("/login");
        toast.error("Session expired. Please login again.");
        return true;
      }

      if (isForbiddenError(error)) {
        toast.error("You do not have permission to perform this action");
        return true;
      }

      return false;
    },
    [navigate]
  );

  const getAuthHeaders = useCallback(() => {
    return {};
  }, []);

  const logout = useCallback(() => {
    void publicApi
      .post("/logout", undefined, { withCredentials: true })
      .catch((error) => {
        logError(error, {
          scope: "auth",
          action: "logout",
        });
      })
      .finally(() => {
        localStorage.removeItem("accessToken");
        localStorage.removeItem("user_id");
        localStorage.removeItem("user_roles");
        localStorage.removeItem("org_id");
        localStorage.removeItem("proj_id");
        localStorage.removeItem("profile_cache");
        setIsAuthenticated(false);
        window.dispatchEvent(new Event("auth-state-changed"));
        navigate("/login");
        toast.success("Logged out successfully");
      });
  }, [navigate]);

  return {
    handleAuthError,
    getAuthHeaders,
    logout,
    isAuthenticated,
    isAuthLoading,
  };
};

// Custom hook for handling API requests with auth
export const useAuthenticatedApi = () => {
  const { handleAuthError } = useAuth();

  const executeRequest = useCallback(
    async <T>(
      apiCall: () => Promise<T>,
      errorMessage = "An error occurred"
    ): Promise<T | null> => {
      try {
        return await apiCall();
      } catch (error) {
        if (!handleAuthError(error)) {
          toast.error(errorMessage);
        }
        return null;
      }
    },
    [handleAuthError]
  );

  return { executeRequest };
};
