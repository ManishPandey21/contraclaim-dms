/**
 * Display helpers for blog metadata.
 */

/**
 * Format an ISO date (YYYY-MM-DD) for display.
 *
 * Parsed and formatted in UTC on purpose: `new Date("2026-08-06")` is UTC
 * midnight, so formatting it in a timezone behind UTC would render the
 * previous day.
 */
export function formatBlogDate(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate);
  if (!match) return isoDate;

  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (Number.isNaN(date.getTime())) return isoDate;

  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function formatReadingTime(minutes: number): string {
  return `${minutes} min read`;
}
