import { fromDateInput, toDateInput } from "@/lib/format-in";
import type {
  DeterminationStatus,
  FindingResult,
  HeadFigures,
  HeadFindingInput,
  InformationRequest,
} from "@/types/claimFindings";

export const RESULT_LABEL: Record<FindingResult, string> = {
  full: "Made out in full",
  partial: "Made out in part",
  none: "Not made out",
  insufficient_information: "Cannot yet be determined",
};

export const STATUS_LABEL: Record<DeterminationStatus, string> = {
  draft: "Draft",
  in_review: "In review",
  returned: "Returned for changes",
  approved: "Approved",
  recorded: "Recorded",
};

export const PARTY_LABEL = { contractor: "Contractor", engineer: "Engineer", employer: "Employer" } as const;

/**
 * Why a finding cannot be saved yet, or null. The server checks the same
 * things; this only saves a round trip and tells the person what is missing.
 */
export function findingProblem(input: HeadFindingInput): string | null {
  if (input.result === null) return null;
  const figure = Boolean(input.days) || Boolean(input.amount);
  if ((input.result === "none" || input.result === "insufficient_information") && figure) {
    return "A head that is not made out, or cannot yet be determined, cannot carry days or an amount.";
  }
  if (input.result === "insufficient_information" && input.information_required.length === 0) {
    return "Say what information is required before this head can be determined.";
  }
  if (!input.findings.trim()) return "Give the reasons for the finding.";
  return null;
}

/** The days the record holds for a head, as one line for display beside the finding. */
export function daysOnRecord(figures: HeadFigures | undefined): string {
  if (!figures) return "No days on record";
  const parts: string[] = [];
  if (figures.days_claimed != null) parts.push(`${figures.days_claimed} claimed`);
  if (figures.days_assessed != null) parts.push(`${figures.days_assessed} on the delay assessment`);
  else if (figures.days_assessed_low != null && figures.days_assessed_high != null) {
    parts.push(`${figures.days_assessed_low} to ${figures.days_assessed_high} on the delay assessment, overlaps undecided`);
  }
  return parts.length ? parts.join(" · ") : "No days on record";
}

/** One entry per non-empty line. */
export function linesOf(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** One row of the "information required" editor: what, by when, and what follows if not supplied. */
export interface InformationRow {
  item: string;
  deadline: string; // yyyy-mm-dd, or "" when no date is set
  consequence: string;
}

/** Rows as the server takes them: empty rows dropped, dates as ISO, blank consequences as null. */
export function informationInput(rows: InformationRow[]): InformationRequest[] {
  return rows
    .filter((row) => row.item.trim())
    .map((row) => ({
      item: row.item.trim(),
      deadline: fromDateInput(row.deadline) ?? null,
      consequence: row.consequence.trim() || null,
    }));
}

/** "overdue", "due" (within `soonDays`), or null, for an information item's deadline. */
export function deadlineState(deadline: string | null | undefined, today: string, soonDays = 7): "overdue" | "due" | null {
  const day = toDateInput(deadline);
  if (!day) return null;
  const diff = (Date.parse(`${day}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000;
  if (diff < 0) return "overdue";
  return diff <= soonDays ? "due" : null;
}

/**
 * Which letters a determination can start (4B-31). The server enforces the same rules;
 * this only decides which buttons to offer.
 */
export function letterOptions(
  determination: { _id: string; origin: string; status: DeterminationStatus; heads: { information_required: unknown[] }[] },
  all: { supersedes_id?: string | null }[]
): { determination: boolean; informationRequest: boolean } {
  const latest = !all.some((other) => other.supersedes_id === determination._id);
  const authored = determination.origin === "authored" && latest;
  const asks = determination.heads.some((head) => head.information_required.length > 0);
  return {
    determination: authored && determination.status === "approved",
    informationRequest: authored && asks && ["draft", "returned", "in_review", "approved"].includes(determination.status),
  };
}
