/**
 * Auth helpers for JWT expiry checks, refresh, and redirect-on-expire.
 */

import { navigateTo } from "../lib/router";
import { extractErrorMessage, logError } from "../lib/error-logger";
import { publicApi } from "./http";
import { clearSessionProfileCache } from "./session-api";

let refreshInFlight: Promise<string> | null = null;

export function decodeJwt<T = any>(token: string): T | null {
  try {
    const payload = token.split(".")[1];
    const json = atob(payload.replace(/-/g, "+").replace(/_/g, "/"));
    return JSON.parse(decodeURIComponent(escape(json)));
  } catch {
    return null;
  }
}

export function secondsUntilExpiry(token: string): number {
  const payload = decodeJwt<{ exp?: number }>(token);
  if (!payload?.exp) return Number.MAX_SAFE_INTEGER;
  const now = Math.floor(Date.now() / 1000);
  return payload.exp - now;
}

export async function refreshToken(): Promise<string> {
  if (refreshInFlight) {
    return refreshInFlight;
  }

  refreshInFlight = (async () => {
    const { data } = await publicApi.post<{ access_token?: string }>(
      "/refresh",
      undefined,
      { withCredentials: true }
    );

    const newToken = data?.access_token;
    if (!newToken) throw new Error("No access_token in refresh response");

    // The backend refreshes the HttpOnly auth cookie. Keep the returned token
    // out of localStorage to avoid turning XSS into session theft. Refreshing
    // the same session must not dispatch auth-state-changed, otherwise every
    // authenticated API call can fan out into /me refresh loops.
    return newToken;
  })();

  try {
    return await refreshInFlight;
  } catch (error) {
    throw new Error(extractErrorMessage(error, "Unable to refresh session."));
  } finally {
    refreshInFlight = null;
  }
}

export function clearSession() {
  try {
    clearSessionProfileCache();
    window.localStorage.removeItem("accessToken");
    // Also clear demo headers if they were set
    window.localStorage.removeItem("user_id");
    window.localStorage.removeItem("user_roles");
    window.localStorage.removeItem("org_id");
    window.localStorage.removeItem("proj_id");
    window.localStorage.removeItem("profile_cache");
  } catch {
    // no-op
  }
}

export function logoutAndRedirect(redirectPath: string = "/") {
  try {
    clearSession();
  } finally {
    // Prefer SPA navigation (fallback handled inside navigateTo)
    navigateTo(redirectPath, { replace: true });
  }
}

export function redirectToLoginAfterSessionExpiry() {
  logoutAndRedirect("/login");
}

export async function logout(): Promise<void> {
  try {
    await publicApi.post("/logout", undefined, { withCredentials: true });
  } catch (error) {
    logError(error, {
      scope: "auth",
      action: "logout",
    });
  } finally {
    logoutAndRedirect("/");
  }
}

/**
 * Ensure token is valid. If it's expiring within thresholdSec, refresh silently.
 * On refresh failure, redirect to login.
 */
export async function ensureValidToken(
  thresholdSec: number = 120
): Promise<void> {
  void thresholdSec;
  // With HttpOnly cookie auth the client cannot safely inspect token expiry.
  // Authenticated API wrappers handle expiry by refreshing once after a 401.
  // Proactive refresh here would run before every API call and can create
  // /refresh -> auth-state -> /me request storms.
}
