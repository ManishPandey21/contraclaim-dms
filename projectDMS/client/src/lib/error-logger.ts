import axios from "axios";
import { joinApiUrl } from "@/config/api";

type ErrorLogContext = {
  scope: string;
  action?: string;
  metadata?: Record<string, unknown>;
};

const truncate = (value: unknown, max: number): string | undefined => {
  if (value == null) return undefined;
  const text = String(value);
  return text.length > max ? text.slice(0, max) : text;
};

/**
 * Fire-and-forget beacon so caught errors are visible server-side, not just in
 * the user's console. Deliberately uses bare fetch (not the shared axios
 * client) to avoid its auth/refresh/toast interceptors, and swallows every
 * failure: you cannot report a failure to report, and a telemetry error must
 * never surface to the user or recurse back into logError.
 */
const reportError = (
  error: unknown,
  { scope, action, metadata }: ErrorLogContext
): void => {
  try {
    const body = JSON.stringify({
      message: truncate(extractErrorMessage(error), 2000),
      scope: truncate(scope, 120),
      action: truncate(action ?? "unknown", 120),
      stack: truncate(error instanceof Error ? error.stack : undefined, 8000),
      component_stack: truncate(metadata?.componentStack, 8000),
      url: typeof window !== "undefined" ? truncate(window.location.href, 2000) : undefined,
      user_agent:
        typeof navigator !== "undefined" ? truncate(navigator.userAgent, 500) : undefined,
    });
    void fetch(joinApiUrl("/client-errors"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {
      /* best-effort telemetry — never surface a reporting failure */
    });
  } catch {
    /* never let error reporting throw */
  }
};

const extractMessageFromPayload = (payload: unknown): string | null => {
  if (!payload) return null;

  if (typeof payload === "string") {
    return payload;
  }

  if (Array.isArray(payload)) {
    const parts = payload
      .map((item) => extractMessageFromPayload(item))
      .filter((value): value is string => Boolean(value));
    return parts.length > 0 ? parts.join("; ") : null;
  }

  if (typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    return (
      extractMessageFromPayload(record.detail) ||
      extractMessageFromPayload(record.message) ||
      extractMessageFromPayload(record.error) ||
      extractMessageFromPayload(record.msg) ||
      null
    );
  }

  try {
    return String(payload);
  } catch {
    return null;
  }
};

export const extractErrorMessage = (
  error: unknown,
  fallback: string = "Something went wrong."
): string => {
  if (axios.isAxiosError(error)) {
    return (
      extractMessageFromPayload(error.response?.data) ||
      error.message ||
      fallback
    );
  }

  if (error instanceof Error) {
    return error.message || fallback;
  }

  return extractMessageFromPayload(error) || fallback;
};

export const logError = (
  error: unknown,
  { scope, action = "unknown", metadata }: ErrorLogContext
) => {
  console.error(`[${scope}] ${action}`, {
    message: extractErrorMessage(error),
    error,
    metadata,
  });
  reportError(error, { scope, action, metadata });
};
