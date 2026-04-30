import axios from "axios";

type ErrorLogContext = {
  scope: string;
  action?: string;
  metadata?: Record<string, unknown>;
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
};
