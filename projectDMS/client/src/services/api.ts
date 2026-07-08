import { toast } from "sonner";

import { createHttpClient } from "./http";
import { redirectToLoginAfterSessionExpiry } from "./auth";

type ApiRequestConfig = {
  _retry?: boolean;
};

export const api = createHttpClient();

// Global, deduplicated messaging for statuses every page handles the same way.
// Pages keep their own contextual error handling; this guarantees a rate-limit
// or permission problem is never silently swallowed or shown as a fake outage.
const GLOBAL_STATUS_TOASTS: Record<number, { title: string; description: string }> = {
  429: {
    title: "Too many requests",
    description: "Please wait a moment and try again.",
  },
  403: {
    title: "Permission denied",
    description: "You don't have access for this action. Contact your administrator if you think you should.",
  },
};
const TOAST_DEDUPE_MS = 5000;
const lastToastAt: Record<number, number> = {};

function showGlobalStatusToast(status: number | undefined): void {
  if (!status) return;
  const entry = GLOBAL_STATUS_TOASTS[status];
  if (!entry) return;
  const now = Date.now();
  if (now - (lastToastAt[status] ?? 0) < TOAST_DEDUPE_MS) return;
  lastToastAt[status] = now;
  toast.error(entry.title, { description: entry.description });
}

api.interceptors.request.use(
  (config) => {
    // HttpOnly auth cookies are sent via withCredentials. Do not mirror access
    // tokens from localStorage into headers; stale browser storage must not be
    // treated as an authentication source.
    return config;
  },
  (error) => Promise.reject(error)
);

// On 401, try one refresh then retry; otherwise redirect to login
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error?.response?.status;
    const originalRequest = (error?.config || {}) as ApiRequestConfig & any;
    if (status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;
      try {
        const { refreshToken } = await import("./auth");
        await refreshToken();
        return api(originalRequest);
      } catch {
        redirectToLoginAfterSessionExpiry();
      }
    }
    showGlobalStatusToast(status);
    return Promise.reject(error);
  }
);
