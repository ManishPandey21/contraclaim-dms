import type {
  HindranceCategory,
  HindranceClaimStatus,
  HindranceEventType,
  HindranceLinkTargetType,
  HindranceResponsibility,
  HindranceStatus,
  StoredHindranceStatus,
} from "@/services/hindrance-api";

type Option<T extends string> = { value: T; label: string };

export const HINDRANCE_TYPE_OPTIONS: ReadonlyArray<Option<HindranceEventType>> = [
  { value: "hindrance", label: "Hindrance" },
  { value: "constraint", label: "Constraint" },
  { value: "delay_event", label: "Delay event" },
];

export const HINDRANCE_STATUS_OPTIONS: ReadonlyArray<Option<HindranceStatus>> = [
  { value: "open", label: "Open" },
  { value: "under_review", label: "Under review" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

export const HINDRANCE_CLAIM_STATUS_OPTIONS: ReadonlyArray<Option<HindranceClaimStatus>> = [
  { value: "not_assessed", label: "Not assessed" },
  { value: "notified", label: "Notified" },
  { value: "claimed", label: "Claimed" },
  { value: "assessed", label: "Assessed" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "not_applicable", label: "Not applicable" },
];

export const HINDRANCE_RESPONSIBILITY_OPTIONS: ReadonlyArray<Option<HindranceResponsibility>> = [
  { value: "employer", label: "Employer" },
  { value: "contractor", label: "Contractor" },
  { value: "concurrent", label: "Concurrent" },
  { value: "neutral", label: "Neutral" },
  { value: "under_review", label: "Under review" },
];

export const HINDRANCE_CATEGORY_OPTIONS: ReadonlyArray<Option<HindranceCategory>> = [
  { value: "site_access", label: "Site access" },
  { value: "land_handover", label: "Land handover" },
  { value: "design_information", label: "Design information" },
  { value: "drawing_approval", label: "Drawing approval" },
  { value: "utility_diversion", label: "Utility diversion" },
  { value: "traffic_diversion", label: "Traffic diversion" },
  { value: "tree_cutting", label: "Tree cutting" },
  { value: "statutory_approval", label: "Statutory approval" },
  { value: "local_restrictions", label: "Local restrictions" },
  { value: "adverse_weather", label: "Adverse weather" },
  { value: "force_majeure", label: "Force majeure" },
  { value: "variation_instruction", label: "Variation / instruction" },
  { value: "payment", label: "Payment" },
  { value: "employer_supplied_items", label: "Employer-supplied items" },
  { value: "suspension", label: "Suspension" },
  { value: "interface", label: "Interface" },
  { value: "other", label: "Other" },
];

export const HINDRANCE_LINK_TARGET_LABELS: Record<HindranceLinkTargetType, string> = {
  programme_milestone: "Programme activity",
  key_date: "Key date",
  eot_submission: "EOT submission",
};

const LEGACY_STATUS_LABELS: Record<string, string> = {
  claimed: "Claimed (legacy)",
  assessed: "Assessed (legacy)",
  rejected: "Rejected (legacy)",
};

function labelFrom<T extends string>(options: ReadonlyArray<Option<T>>, value?: string | null): string {
  if (!value) return "—";
  return options.find((option) => option.value === value)?.label ?? value;
}

export const hindranceTypeLabel = (value?: string | null) => labelFrom(HINDRANCE_TYPE_OPTIONS, value);
export const hindranceCategoryLabel = (value?: string | null) =>
  value ? labelFrom(HINDRANCE_CATEGORY_OPTIONS, value) : "Uncategorised";
export const hindranceResponsibilityLabel = (value?: string | null) =>
  labelFrom(HINDRANCE_RESPONSIBILITY_OPTIONS, value);
export const hindranceClaimStatusLabel = (value?: string | null) =>
  labelFrom(HINDRANCE_CLAIM_STATUS_OPTIONS, value);
export function hindranceStatusLabel(value?: StoredHindranceStatus | string | null): string {
  if (value && LEGACY_STATUS_LABELS[value]) return LEGACY_STATUS_LABELS[value];
  return labelFrom(HINDRANCE_STATUS_OPTIONS, value);
}

export function hindranceStatusClass(value?: string | null): string {
  switch (value) {
    case "open":
      return "bg-amber-100 text-amber-800";
    case "under_review":
      return "bg-blue-100 text-blue-800";
    case "resolved":
      return "bg-green-100 text-green-800";
    case "closed":
      return "bg-gray-200 text-gray-700";
    default:
      return "bg-purple-100 text-purple-800";
  }
}

/** A register entry's display reference: the generated one, else the legacy one. */
export function hindranceReference(item: { hindrance_ref?: string | null; delay_ref?: string | null }): string {
  return item.hindrance_ref || item.delay_ref || "—";
}

/** The API serialises naive UTC timestamps; read them as UTC, never local time. */
function parseUtc(value: string): Date {
  return new Date(/[zZ]$|[+-]\d\d:?\d\d$/.test(value) ? value : `${value}Z`);
}

export function formatRegisterDate(value?: string | null): string {
  if (!value) return "—";
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeZone: "UTC" }).format(date);
}

/** `yyyy-mm-dd` for an <input type="date">, read in UTC like the server stores it. */
export function toDateInput(value?: string | null): string {
  if (!value) return "";
  const date = parseUtc(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

/** The naive-UTC midnight timestamp the API stores for a picked date. */
export function fromDateInput(value: string): string | null {
  return value ? `${value}T00:00:00` : null;
}
