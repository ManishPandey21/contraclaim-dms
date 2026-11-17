// Indian-convention formatting for money and dates, shared by the registers.
// Pure functions: no locale lookups that differ between browsers and CI.

const DASH = "—";

/** 184500000 -> "18,45,00,000" (last three digits, then pairs). */
export function groupIndian(value: number): string {
  if (!Number.isFinite(value)) return DASH;
  const negative = value < 0;
  const [whole, fraction] = Math.abs(value).toFixed(2).split(".");
  const head = whole.length > 3 ? whole.slice(0, -3) : "";
  const tail = whole.slice(-3);
  const grouped = head ? `${head.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${tail}` : tail;
  const withFraction = fraction && fraction !== "00" ? `${grouped}.${fraction}` : grouped;
  return negative ? `-${withFraction}` : withFraction;
}

const SYMBOLS: Record<string, string> = { INR: "₹", USD: "$", EUR: "€", GBP: "£" };

/** Full amount with Indian grouping: formatINR(184500000) -> "₹18,45,00,000". */
export function formatINR(amount?: number | null, currency: string | null = "INR"): string {
  if (amount == null || !Number.isFinite(amount)) return DASH;
  const code = (currency || "INR").toUpperCase();
  const symbol = SYMBOLS[code];
  return symbol ? `${symbol}${groupIndian(amount)}` : `${code} ${groupIndian(amount)}`;
}

/**
 * Integer paise as rupees, grouped the Indian way, with no floating point on the way:
 * formatPaise(12500050) -> "₹1,25,000.50". Exact for any whole number of paise a JSON
 * number carries exactly (up to 2^53, about ₹90 lakh crore).
 */
export function formatPaise(paise?: number | null, currency: string | null = "INR"): string {
  if (paise == null || !Number.isSafeInteger(paise)) return DASH;
  const negative = paise < 0;
  const digits = String(Math.abs(paise)).padStart(3, "0");
  const whole = digits.slice(0, -2);
  const fraction = digits.slice(-2);
  const head = whole.length > 3 ? whole.slice(0, -3) : "";
  const tail = whole.slice(-3);
  const grouped = head ? `${head.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${tail}` : tail;
  const text = `${negative ? "-" : ""}${fraction !== "00" ? `${grouped}.${fraction}` : grouped}`;
  const code = (currency || "INR").toUpperCase();
  const symbol = SYMBOLS[code];
  return symbol ? `${symbol}${text}` : `${code} ${text}`;
}

/** Short form in lakh / crore: formatCrore(184500000) -> "₹18.45 Cr". */
export function formatCrore(amount?: number | null, currency: string | null = "INR"): string {
  if (amount == null || !Number.isFinite(amount)) return DASH;
  const code = (currency || "INR").toUpperCase();
  if (code !== "INR") return formatINR(amount, code);
  const absolute = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  const trim = (n: number) => n.toFixed(2).replace(/\.?0+$/, "");
  if (absolute >= 1e7) return `${sign}₹${trim(absolute / 1e7)} Cr`;
  if (absolute >= 1e5) return `${sign}₹${trim(absolute / 1e5)} L`;
  return `${sign}₹${groupIndian(absolute)}`;
}

/**
 * Calendar date as DD-MM-YYYY. The date part of an ISO string is used as
 * written, so a date never shifts by a day with the viewer's time zone.
 */
export function formatDateIN(value?: string | null): string {
  if (!value) return DASH;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return DASH;
  return `${match[3]}-${match[2]}-${match[1]}`;
}

/** ISO date-time -> value for <input type="date">. */
export function toDateInput(value?: string | null): string {
  const match = value ? /^(\d{4}-\d{2}-\d{2})/.exec(value) : null;
  return match ? match[1] : "";
}

/** <input type="date"> value -> ISO date-time at UTC midnight (or undefined when empty). */
export function fromDateInput(value: string): string | undefined {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : undefined;
}
