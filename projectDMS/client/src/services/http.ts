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
  return fetch(input, {
    ...init,
    credentials: init.credentials || "include",
    headers,
  });
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
