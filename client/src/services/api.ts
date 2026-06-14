import { createHttpClient } from "./http";
import { redirectToLoginAfterSessionExpiry } from "./auth";

type ApiRequestConfig = {
  _retry?: boolean;
};

export const api = createHttpClient();

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
    return Promise.reject(error);
  }
);
