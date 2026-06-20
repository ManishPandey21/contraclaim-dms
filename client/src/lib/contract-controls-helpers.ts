import type { VariationStatus } from "@/services/variations-api";
import type { BGStatus, BGDTO } from "@/services/bank-guarantees-api";

// Pure helpers for the Variation + Bank Guarantee registers.

export const VARIATION_STATUS_COLOR: Record<VariationStatus, string> = {
  draft: "bg-gray-500",
  submitted: "bg-blue-500",
  under_review: "bg-amber-500",
  recommended: "bg-indigo-500",
  approved: "bg-green-600",
  rejected: "bg-red-600",
  superseded: "bg-gray-700",
};

export const BG_STATUS_COLOR: Record<BGStatus, string> = {
  draft: "bg-gray-500",
  submitted: "bg-blue-500",
  valid: "bg-green-600",
  extension_required: "bg-orange-600",
  extended: "bg-purple-600",
  expired: "bg-red-600",
  released: "bg-gray-700",
  encashment_under_process: "bg-amber-600",
  encashed: "bg-red-700",
};

const titleCase = (s?: string | null) =>
  String(s || "—").split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

export const variationStatusColor = (s?: string | null) =>
  VARIATION_STATUS_COLOR[(s as VariationStatus)] || "bg-gray-500";
export const variationStatusLabel = (s?: string | null) => titleCase(s);

export const bgStatusColor = (s?: string | null) => BG_STATUS_COLOR[(s as BGStatus)] || "bg-gray-500";
export const bgStatusLabel = (s?: string | null) => titleCase(s);
export const bgTypeLabel = (s?: string | null) => titleCase(s);

/** Alert text for the BG list "Alerts" column. */
export function bgAlertText(bg: Pick<BGDTO, "days_to_expiry" | "extension_required" | "bg_status">): string {
  if (bg.bg_status === "released") return "—";
  const d = bg.days_to_expiry;
  if (d == null) return "—";
  if (d < 0) return `Expired ${Math.abs(d)}d`;
  if (!bg.extension_required) return "OK";
  if (d <= 30) return `${d}d (extend)`;
  if (d <= 45) return `${d}d (extend)`;
  return `${d}d`;
}

/** Compact amount with thousands separators. */
export function fmtAmount(n?: number | null, currency = "INR"): string {
  if (n == null) return "—";
  return `${currency} ${Number(n).toLocaleString()}`;
}
