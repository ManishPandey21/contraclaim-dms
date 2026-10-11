import axios from "axios";
import { API_BASE_URL } from "../config/api";
import { activeScopeHeaders } from "./active-scope";

const SAFE_METHODS = ["GET", "HEAD", "OPTIONS", "TRACE"];

const isApiRequest = (input: RequestInfo | URL): boolean => {
  const value =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : input.url;
  if (!value) return false;
  if (value.startsWith("/")) return value.startsWith("/api") || !value.startsWith("//");
  try {
    const apiUrl = new URL(API_BASE_URL, window.location.origin);
    const requestUrl = new URL(value, window.location.origin);
    return requestUrl.origin === apiUrl.origin && requestUrl.pathname.startsWith(apiUrl.pathname);
  } catch {
    return false;
  }
};

/**
 * Add the navbar selection (see active-scope.ts) to a fetch request bound for
 * the API. A header the caller set explicitly wins. Only API requests carry it:
 * a presigned storage URL or third-party origin never sees the selection.
 */
const withActiveScopeHeaders = (input: RequestInfo | URL, headers: Headers): void => {
  if (!isApiRequest(input)) return;
  for (const [name, value] of Object.entries(activeScopeHeaders())) {
    if (!headers.has(name)) headers.set(name, value);
  }
};

export const readCookie = (name: string): string | null => {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
};

export const installCsrfFetchInterceptor = () => {
  if (typeof window === "undefined" || window.__contraclaimCsrfFetchInstalled) {
    return;
  }
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const method = String(
      init?.method ||
        (input instanceof Request ? input.method : "GET")
    ).toUpperCase();
    const headers = new Headers(
      init?.headers || (input instanceof Request ? input.headers : undefined)
    );
    const authorization = headers.get("Authorization");
    if (
      authorization &&
      (isApiRequest(input) ||
        /^Bearer\s*(null|undefined|none)?$/i.test(authorization.trim()))
    ) {
      headers.delete("Authorization");
    }
    if (!SAFE_METHODS.includes(method)) {
      const csrfToken = readCookie("cc_csrf_token");
      if (csrfToken) {
        if (!headers.has("X-CSRF-Token")) {
          headers.set("X-CSRF-Token", csrfToken);
        }
      }
    }
    withActiveScopeHeaders(input, headers);
    init = { ...init, headers };
    return originalFetch(input, init);
  };
  window.__contraclaimCsrfFetchInstalled = true;
};

export const authenticatedFetch = (
  input: RequestInfo | URL,
  init: RequestInit = {}
) => {
  const headers = new Headers(init.headers);
  headers.delete("Authorization");
  withActiveScopeHeaders(input, headers);
  return fetch(input, {
    ...init,
    credentials: init.credentials || "include",
    headers,
  });
};

/**
 * Download an API file route (`/documents/{id}/download`, `/contracts/{id}/download`)
 * as a Blob. The route is asked for the presigned storage URL as JSON
 * (`redirect=false`) instead of a 307: a browser follows a redirect with the
 * request's own headers, so the navbar selection would ride along to storage and
 * turn the presigned GET into a CORS preflight. The storage URL is then fetched
 * with no API headers and no credentials. A locally stored file streams as before.
 */
export const fetchApiFileBlob = async (url: string): Promise<Blob> => {
  const separator = url.includes("?") ? "&" : "?";
  const response = await authenticatedFetch(`${url}${separator}redirect=false`);
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    const error = new Error(detail || `Download failed (${response.status})`) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  if ((response.headers.get("content-type") || "").includes("application/json")) {
    const { url: storageUrl } = (await response.json()) as { url?: string };
    if (!storageUrl) throw new Error("The download URL is missing");
    const file = await fetch(storageUrl, { credentials: "omit" });
    if (!file.ok) throw new Error(`Download failed (${file.status})`);
    return file.blob();
  }
  return response.blob();
};

export const ensureCsrfToken = async () => {
  if (typeof window === "undefined" || readCookie("cc_csrf_token")) {
    return;
  }
  try {
    await window.fetch(`${API_BASE_URL}/csrf-token`, {
      credentials: "include",
    });
  } catch {
    // The first authenticated unsafe request will surface the API error.
  }
};

declare global {
  interface Window {
    __contraclaimCsrfFetchInstalled?: boolean;
  }
}

export const createHttpClient = () => {
  const client = axios.create({
    baseURL: API_BASE_URL,
    timeout: 20000,
    withCredentials: true,
  });
  client.interceptors.request.use((config) => {
    const method = String(config.method || "get").toUpperCase();
    const headers = config.headers as any;
    if (headers) {
      if (typeof headers.delete === "function") {
        headers.delete("Authorization");
      } else {
        delete headers.Authorization;
        delete headers.authorization;
      }
    }
    if (!SAFE_METHODS.includes(method)) {
      const csrfToken = readCookie("cc_csrf_token");
      if (csrfToken) {
        config.headers = config.headers || {};
        config.headers["X-CSRF-Token"] = csrfToken;
      }
    }
    // The navbar selection travels with every API request (see active-scope.ts).
    // A header the caller set explicitly wins, so a request can target a scope
    // deliberately; the backend validates it either way.
    if (headers) {
      for (const [name, value] of Object.entries(activeScopeHeaders())) {
        const existing = typeof headers.get === "function" ? headers.get(name) : headers[name];
        if (!existing) {
          if (typeof headers.set === "function") headers.set(name, value);
          else headers[name] = value;
        }
      }
    }
    return config;
  });
  return client;
};

export const publicApi = createHttpClient();
