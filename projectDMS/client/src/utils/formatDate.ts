/**
 * Contractual dates render day-first (DD-MM-YYYY) everywhere.
 *
 * `toLocaleDateString()` follows the viewer's browser locale, so the same
 * contractual date read as 15/04/2026 in London and 4/15/2026 in New York.
 * On a register where a single digit decides whether a milestone is overdue,
 * that ambiguity is not acceptable — the format is fixed, not localised.
 *
 * Mirrors DATE_DISPLAY_FORMAT in backend/rbac_backend/services/key_date_service.py.
 */
export const DATE_PLACEHOLDER = "—";

export function formatDate(value?: string | Date | null, fallback = DATE_PLACEHOLDER): string {
  if (!value) return fallback;
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return fallback;
  const day = String(parsed.getDate()).padStart(2, "0");
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  return `${day}-${month}-${parsed.getFullYear()}`;
}

/** Day-first date plus 24h time, for audit-style timestamps. */
export function formatDateTime(value?: string | Date | null, fallback = DATE_PLACEHOLDER): string {
  if (!value) return fallback;
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return fallback;
  const hours = String(parsed.getHours()).padStart(2, "0");
  const minutes = String(parsed.getMinutes()).padStart(2, "0");
  return `${formatDate(parsed, fallback)} ${hours}:${minutes}`;
}
