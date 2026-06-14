/**
 * Date/time utilities to standardize parsing and formatting.
 * - Treats API datetimes as UTC by default and renders in Asia/Kolkata (IST)
 *   unless a different timeZone is passed.
 */

export type ISOish = string | Date | undefined | null;

const hasTZ = (s: string) => /(?:Z|[+-]\d{2}:\d{2})$/.test(s);

/**
 * Ensure an ISO string has a timezone suffix. If missing, assume UTC (Z).
 */
export function ensureZ(iso: string): string {
  const trimmed = iso.trim();
  if (hasTZ(trimmed)) return trimmed;
  // If string only contains date part (YYYY-MM-DD), leave as is (interpreted as UTC midnight)
  // but still append Z to avoid local interpretation.
  return `${trimmed}Z`;
}

/**
 * Parse an API datetime into a Date object.
 * - If string lacks timezone, assume UTC by appending Z.
 */
export function parseApiDate(iso: ISOish): Date | undefined {
  if (!iso) return undefined;
  if (iso instanceof Date) return iso;
  try {
    const s = ensureZ(String(iso));
    const d = new Date(s);
    if (isNaN(d.getTime())) return undefined;
    return d;
  } catch {
    return undefined;
  }
}

/**
 * Resolve preferred user time zone from localStorage or browser, with fallback.
 */
export function getUserTimeZone(): string {
  try {
    const saved =
      typeof window !== "undefined"
        ? window.localStorage.getItem("user_timezone")
        : null;
    if (saved && saved.trim()) return saved.trim();
  } catch {
    // ignore localStorage access errors
  }
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz) return tz;
  } catch {
    // ignore
  }
  return "Asia/Kolkata";
}

/**
 * Format a datetime string or Date in a specific IANA time zone (default IST).
 */
export function formatInTimeZone(
  iso: ISOish,
  options: Intl.DateTimeFormatOptions = {
    year: "numeric",
    month: "short",
    day: "2-digit",
  },
  timeZone: string = getUserTimeZone()
): string {
  const d = parseApiDate(iso);
  if (!d) return "—";
  try {
    return new Intl.DateTimeFormat("en-US", { timeZone, ...options }).format(d);
  } catch {
    return d.toISOString();
  }
}

/**
 * Common helpers
 */
export function formatDate(
  iso: ISOish,
  timeZone: string = getUserTimeZone()
): string {
  return formatInTimeZone(
    iso,
    { year: "numeric", month: "short", day: "2-digit" },
    timeZone
  );
}

export function formatDateTime(
  iso: ISOish,
  timeZone: string = getUserTimeZone()
): string {
  return formatInTimeZone(
    iso,
    {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    },
    timeZone
  );
}

export function formatTime(
  iso: ISOish,
  timeZone: string = getUserTimeZone()
): string {
  return formatInTimeZone(
    iso,
    {
      hour: "2-digit",
      minute: "2-digit",
      second: undefined,
    },
    timeZone
  );
}

/**
 * Humanized "time ago" using UTC-based parsing.
 */
export function timeAgo(iso: ISOish): string {
  const d = parseApiDate(iso);
  if (!d) return "—";
  const diffSeconds = Math.floor((Date.now() - d.getTime()) / 1000);
  if (diffSeconds < 60) return "Just now";
  const minutes = Math.floor(diffSeconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes !== 1 ? "s" : ""} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours !== 1 ? "s" : ""} ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return formatDate(iso);
}
