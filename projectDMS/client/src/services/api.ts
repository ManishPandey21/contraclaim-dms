import { createHttpClient } from "./http";
import { ensureValidToken, refreshToken, logoutAndRedirect } from "./auth";

type ApiRequestConfig = {
  _retry?: boolean;
};

export const api = createHttpClient();

// Proactively ensure token validity before every request
api.interceptors.request.use(
  async (config) => {
    await ensureValidToken(120); // silently refresh when <= 2 minutes remain
    const token = window.localStorage.getItem("accessToken");

    // Work on a mutable, loosely-typed headers object to satisfy TS
    const headers: any = config.headers || {};

    // Always attach Authorization if available; keep dev headers for compatibility.
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    config.headers = headers as any;
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
        await refreshToken();
        const newToken = window.localStorage.getItem("accessToken");
        if (newToken) {
          originalRequest.headers = originalRequest.headers || {};
          originalRequest.headers["Authorization"] = `Bearer ${newToken}`;
        }
        return api(originalRequest);
      } catch {
        logoutAndRedirect("/");
      }
    }
    return Promise.reject(error);
  }
);
