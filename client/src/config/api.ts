/**
 * Centralized API base URL configuration for the frontend.
 * Priority:
 * 1) Vite env var: import.meta.env.VITE_API_BASE_URL
 * 2) Runtime override: window.__API_BASE_URL__
 * 3) Fallback: "/api"
 *
 * Ensures no trailing slash to avoid double slashes when concatenating with paths.
 */

declare global {
  interface Window {
    __API_BASE_URL__?: string;
  }
}

function getEnvBaseUrl(): string | undefined {
  try {
    // Vite exposes env via import.meta.env.* at build time.
    // Using any to avoid TS complaints when import.meta is not available in some contexts.
    const viteEnv: any =
      typeof import.meta !== "undefined" ? (import.meta as any).env : undefined;
    const url = viteEnv?.VITE_API_BASE_URL as string | undefined;
    return url && typeof url === "string" && url.length > 0 ? url : undefined;
  } catch {
    return undefined;
  }
}

function getRuntimeBaseUrl(): string | undefined {
  try {
    const url =
      typeof window !== "undefined" ? window.__API_BASE_URL__ : undefined;
    return url && typeof url === "string" && url.length > 0 ? url : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Derive a sensible default based on the hosting domain when env/runtime override are absent.
 * This guarantees production builds on web.contraclaim.com talk to the API host.
 */
function getHostDerivedBaseUrl(): string | undefined {
  try {
    if (typeof window === "undefined") return undefined;
    const host = window.location.hostname;
    if (host === "web.contraclaim.com") {
      return "https://api.contraclaim.com/api";
    }
    return undefined;
  } catch {
    return undefined;
  }
}

function normalizeBaseUrl(url: string): string {
  if (!url) return "/api";
  // Trim whitespace
  let base = url.trim();
  // Remove trailing slash (keep root "/")
  if (base.length > 1 && base.endsWith("/")) {
    base = base.slice(0, -1);
  }
  return base;
}

function preferLocalProxy(base: string): string {
  try {
    if (typeof window === "undefined") return base;
    if (!/^https?:\/\//i.test(base)) return base;

    const pageHost = window.location.hostname;
    const apiUrl = new URL(base);
    const localHosts = new Set(["localhost", "127.0.0.1", "::1"]);
    if (
      localHosts.has(pageHost) &&
      localHosts.has(apiUrl.hostname) &&
      apiUrl.origin !== window.location.origin
    ) {
      return apiUrl.pathname && apiUrl.pathname !== "/" ? apiUrl.pathname : "/api";
    }
  } catch {
    return base;
  }
  return base;
}

/**
 * Resolve the API base URL using the priority order described above.
 */
export function resolveApiBaseUrl(): string {
  const fromEnv = getEnvBaseUrl();
  const fromRuntime = getRuntimeBaseUrl();
  const fromHost = getHostDerivedBaseUrl();
  const base = fromEnv || fromRuntime || fromHost || "/api";
  return preferLocalProxy(normalizeBaseUrl(base));
}

/**
 * The resolved API base URL constant.
 */
export const API_BASE_URL = resolveApiBaseUrl();

/**
 * Helper to safely join an API path to the base URL.
 * Ensures exactly one slash between base and path.
 */
export function joinApiUrl(path: string): string {
  const base = API_BASE_URL;
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}
