import { ApiError } from "../types/api";
import { toast } from "sonner";

export const handleApiError = (
  error: unknown,
  fallbackMessage = "An unexpected error occurred"
) => {
  console.error("API Error:", error);

  let errorMessage = fallbackMessage;

  if (error instanceof Error) {
    try {
      const parsed = JSON.parse(error.message) as ApiError;
      errorMessage = parsed.detail || parsed.message || error.message;
    } catch {
      errorMessage = error.message;
    }
  }

  toast.error(errorMessage);
  return errorMessage;
};

export const isUnauthorizedError = (error: unknown): boolean => {
  if (error instanceof Error) {
    try {
      const parsed = JSON.parse(error.message) as ApiError;
      return parsed.status === 401;
    } catch {
      return (
        error.message.includes("401") || error.message.includes("unauthorized")
      );
    }
  }
  return false;
};

export const isForbiddenError = (error: unknown): boolean => {
  if (error instanceof Error) {
    try {
      const parsed = JSON.parse(error.message) as ApiError;
      return parsed.status === 403;
    } catch {
      return (
        error.message.includes("403") || error.message.includes("forbidden")
      );
    }
  }
  return false;
};
