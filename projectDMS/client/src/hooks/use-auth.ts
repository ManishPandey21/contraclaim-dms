import { useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { isUnauthorizedError, isForbiddenError } from "../utils/error-handler";

export const useAuth = () => {
  const navigate = useNavigate();

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

  // Check for token on mount
  useEffect(() => {
    const token = localStorage.getItem("accessToken");
    if (!token) {
      navigate("/login");
    }
  }, [navigate]);

  const getAuthHeaders = useCallback(() => {
    const token = localStorage.getItem("accessToken");
    return token ? { Authorization: `Bearer ${token}` } : {};
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("accessToken");
    navigate("/login");
    toast.success("Logged out successfully");
  }, [navigate]);

  return {
    handleAuthError,
    getAuthHeaders,
    logout,
    isAuthenticated: !!localStorage.getItem("accessToken"),
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
