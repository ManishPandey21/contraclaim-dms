import type { SlaItem, SlaState } from "@/services/sla-api";
import type { ClaimDTO, ClaimStatus, ClaimType, NoticeAssessment } from "@/services/claims-api";

// Pure helpers shared by the claims register and claim detail views (Phase 2).

export const CLAIM_TYPES: { value: ClaimType; label: string }[] = [
  { value: "eot", label: "Extension of Time" },
  { value: "variation", label: "Variation" },
  { value: "payment_ipc", label: "Payment / IPC" },
  { value: "loss_expense", label: "Loss & Expense" },
  { value: "acceleration", label: "Acceleration" },
  { value: "defect", label: "Defect" },
  { value: "other", label: "Other" },
];

export const claimTypeLabel = (t: string | null | undefined): string =>
  CLAIM_TYPES.find((x) => x.value === t)?.label || t || "Other";

export interface ClaimSla {
  state: SlaState;
  days_remaining: number;
  kind: string;
}

const SEVERITY: Record<SlaState, number> = { breached: 2, approaching: 1, ok: 0 };

/**
 * Collapse SLA items to the single most-urgent entry per claim id. "breached"
 * outranks "approaching"; within the same state the nearer deadline wins.
 */
export function buildSlaStateMap(items: SlaItem[]): Record<string, ClaimSla> {
  const map: Record<string, ClaimSla> = {};
  for (const item of items || []) {
    const current = map[item.claim_id];
    const incoming: ClaimSla = {
      state: item.state,
      days_remaining: item.days_remaining,
      kind: item.kind,
    };
    if (
      !current ||
      SEVERITY[incoming.state] > SEVERITY[current.state] ||
      (SEVERITY[incoming.state] === SEVERITY[current.state] &&
        incoming.days_remaining < current.days_remaining)
    ) {
      map[item.claim_id] = incoming;
    }
  }
  return map;
}

/** Default title for a follow-up task raised from a claim. */
export function followUpTaskTitle(claim: Pick<ClaimDTO, "title" | "claim_ref">): string {
  const label = claim.claim_ref || claim.title || "claim";
  return `Follow up: ${label}`;
}

export const CLAIM_STATUS_LABEL: Record<ClaimStatus, string> = {
  potential: "Potential",
  draft: "Draft",
  notified: "Notified",
  submitted: "Submitted",
  under_review: "Under Review",
  agreed: "Agreed",
  rejected: "Rejected",
  disputed: "Disputed",
  closed: "Closed",
};

// White text on a dark fill; every pair is at least 4.5:1.
export const CLAIM_STATUS_BADGE: Record<ClaimStatus, string> = {
  potential: "bg-violet-700 text-white",
  draft: "bg-slate-600 text-white",
  notified: "bg-blue-700 text-white",
  submitted: "bg-indigo-700 text-white",
  under_review: "bg-amber-700 text-white",
  agreed: "bg-green-700 text-white",
  rejected: "bg-red-700 text-white",
  disputed: "bg-rose-900 text-white",
  closed: "bg-slate-800 text-white",
};

export type NoticeTone = "neutral" | "ok" | "warn" | "danger";

/** The shared Badge variant that carries each tone. */
export const NOTICE_BADGE_VARIANT: Record<NoticeTone, "outline" | "success" | "warning" | "danger"> = {
  neutral: "outline",
  ok: "success",
  warn: "warning",
  danger: "danger",
};

export interface NoticeSummary {
  /** Short text for a table cell. */
  label: string;
  /** One sentence for the claim page. Always says where the period came from. */
  sentence: string;
  tone: NoticeTone;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** How the notice period was arrived at, in words a reader cannot mistake for more than it is. */
export function noticePeriodBasis(assessment: NoticeAssessment): string {
  const period = `${assessment.period_days}-day period`;
  const clause = assessment.clause_no ? `clause ${assessment.clause_no}` : "the contract";
  switch (assessment.period_source) {
    case "contract_verified":
      return `the ${period} in ${clause}`;
    case "contract_unverified":
      return `the ${period} read from ${clause}, not yet verified`;
    case "organisation_rule":
      return `the ${period} set in your organisation's rules`;
    default:
      return `an assumed ${period}`;
  }
}

/** What a reader should do before relying on the assessment. Empty once the period is verified. */
export function noticeCaveat(assessment: NoticeAssessment): string {
  if (assessment.period_verified) return "";
  // A provisional claim type: the period recorded for that type is not this claim's yet.
  if (assessment.claim_type_confirmed === false)
    return " The claim type is provisional: confirm it before relying on the contract's notice period.";
  return assessment.period_source === "contract_unverified"
    ? " Verify the period against the clause before relying on this."
    : " Check the notice clause in the contract before relying on this.";
}

/**
 * Describe a notice assessment. A late or overdue result is always worded
 * against the period's actual source: an assumed or organisation-set period is
 * not the contract, so the text never says "out of time under the contract".
 */
export function describeNotice(assessment?: NoticeAssessment | null): NoticeSummary | null {
  if (!assessment) return null;
  const from = assessment.basis === "awareness" ? "the awareness date" : "the event date";
  const basis = noticePeriodBasis(assessment);
  const check = noticeCaveat(assessment);
  switch (assessment.state) {
    case "served_late": {
      const days = assessment.served_late_by_days ?? 0;
      return {
        label: `Served ${plural(days, "day")} late`,
        sentence: `Notice served ${plural(days, "day")} after ${basis}, counted from ${from}.${check}`,
        tone: "danger",
      };
    }
    case "served_in_time":
      return {
        label: "Notice served",
        sentence: `Notice served within ${basis}, counted from ${from}.${check}`,
        tone: "ok",
      };
    case "overdue": {
      const days = Math.abs(assessment.days_remaining ?? 0);
      return {
        label: `Notice ${plural(days, "day")} overdue`,
        sentence: `No notice recorded. It was due ${plural(days, "day")} ago under ${basis}, counted from ${from}.${check}`,
        tone: "danger",
      };
    }
    case "due_soon": {
      const days = assessment.days_remaining ?? 0;
      return {
        label: days === 0 ? "Notice due today" : `Notice due in ${plural(days, "day")}`,
        sentence: `No notice recorded. ${days === 0 ? "It is due today" : `It is due in ${plural(days, "day")}`} under ${basis}, counted from ${from}.${check}`,
        tone: "warn",
      };
    }
    default: {
      const days = assessment.days_remaining ?? 0;
      return {
        label: `Notice due in ${plural(days, "day")}`,
        sentence: `No notice recorded. It is due in ${plural(days, "day")} under ${basis}, counted from ${from}.${check}`,
        tone: "neutral",
      };
    }
  }
}

/** The server's own explanation of a refused request, when it gave one. */
export function apiErrorDetail(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

/** Comma- or newline-separated clause numbers -> a clean list without duplicates. */
export function parseClauseList(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[,;\n]/)) {
    const clause = part.trim().replace(/^(sub-)?clause\s+/i, "");
    if (clause) seen.add(clause);
  }
  return [...seen];
}
