import type {
  KeyDateWorkflowSummaryDTO,
  MilestoneStatus,
  MilestoneDTO,
  MilestoneRevision,
} from "@/services/key-dates-api";

// Pure helpers for the Key Date Tracker (status colour-coding + labels).
// Colour scheme per spec §10: green achieved, amber ≤30d, orange ≤15d,
// red overdue, blue EOT under review, purple extension approved, grey not started.

export const STATUS_LABEL: Record<MilestoneStatus, string> = {
  not_started: "Not Started",
  upcoming: "Upcoming",
  due_soon: "Due Soon",
  due_today: "Due Today",
  overdue: "Overdue",
  achieved: "Achieved",
  eot_submitted: "EOT Submitted",
  eot_under_review: "EOT Under Review",
  extension_approved: "Extension Approved",
  extension_rejected: "Extension Rejected",
};

export const STATUS_COLOR: Record<MilestoneStatus, string> = {
  achieved: "bg-green-600",
  upcoming: "bg-amber-500",
  due_soon: "bg-orange-500",
  due_today: "bg-orange-600",
  overdue: "bg-red-600",
  eot_submitted: "bg-blue-500",
  eot_under_review: "bg-blue-600",
  extension_approved: "bg-purple-600",
  extension_rejected: "bg-red-500",
  not_started: "bg-gray-500",
};

export function statusColor(status?: string | null): string {
  return STATUS_COLOR[(status as MilestoneStatus)] || "bg-gray-500";
}

export function statusLabel(status?: string | null): string {
  return STATUS_LABEL[(status as MilestoneStatus)] || String(status || "—");
}

/** Short alert text for the list "Alert status" column. */
export function alertText(m: Pick<MilestoneDTO, "status" | "days_remaining" | "actual_achievement_date">): string {
  if (m.actual_achievement_date) return "—";
  const d = m.days_remaining;
  if (d == null) return "—";
  if (d < 0) return `${Math.abs(d)}d overdue`;
  if (d === 0) return "Due today";
  return `${d}d left`;
}

/** Achievement summary for the list "Achievement status" column. */
export function achievementText(
  m: Pick<MilestoneDTO, "actual_achievement_date" | "delay_days" | "early_completion_days">,
): string {
  if (!m.actual_achievement_date) return "Pending";
  if (m.delay_days) return `Late +${m.delay_days}d`;
  if (m.early_completion_days) return `Early -${m.early_completion_days}d`;
  return "On time";
}

// --- CM-4b: one register column per EOT -----------------------------------

/** Max approved-EOT count across the rows → how many EOT columns to render. */
export function maxRevisionCount(items: Pick<MilestoneDTO, "revisions">[]): number {
  return items.reduce((max, m) => Math.max(max, m.revisions?.length ?? 0), 0);
}

/** The k-th approved EOT revision for a milestone (1-based), or undefined. */
export function revisionAt(
  m: Pick<MilestoneDTO, "revisions">,
  k: number,
): MilestoneRevision | undefined {
  const revs = m.revisions ?? [];
  return revs[k - 1];
}

/** Immutable project-revision lineage for one milestone. */
export function milestoneRevisionLineage(
  workflow: Pick<KeyDateWorkflowSummaryDTO, "submissions" | "determinations">,
  milestone: Pick<MilestoneDTO, "id" | "milestone_ref">,
) {
  return workflow.submissions.flatMap((submission) => {
    const item = submission.items.find(
      (entry) => entry.key_date_id === milestone.id || entry.milestone_ref === milestone.milestone_ref,
    );
    if (!item) return [];
    const determinations = workflow.determinations.flatMap((determination) => {
      if (!determination.eot_submission_ids.includes(submission.id)) return [];
      const determined = determination.items.find(
        (entry) => entry.key_date_id === milestone.id || entry.milestone_ref === milestone.milestone_ref,
      );
      return determined ? [{ determination, item: determined }] : [];
    });
    return [{ submission, item, determinations }];
  });
}
