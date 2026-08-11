import { describe, it, expect } from "vitest";
import {
  statusColor,
  statusLabel,
  alertText,
  achievementText,
  maxRevisionCount,
  revisionAt,
  milestoneRevisionLineage,
} from "../key-date-helpers";

describe("key-date-helpers", () => {
  it("colour-codes per the spec scheme", () => {
    expect(statusColor("achieved")).toContain("green");
    expect(statusColor("overdue")).toContain("red");
    expect(statusColor("upcoming")).toContain("amber");
    expect(statusColor("due_soon")).toContain("orange");
    expect(statusColor("eot_under_review")).toContain("blue");
    expect(statusColor("extension_approved")).toContain("purple");
    expect(statusColor("not_started")).toContain("gray");
    expect(statusColor(undefined)).toContain("gray");
  });

  it("labels statuses", () => {
    expect(statusLabel("eot_submitted")).toBe("EOT Submitted");
    expect(statusLabel("achieved")).toBe("Achieved");
  });

  it("renders alert text from days remaining", () => {
    expect(alertText({ status: "overdue", days_remaining: -3, actual_achievement_date: null })).toBe("3d overdue");
    expect(alertText({ status: "due_today", days_remaining: 0, actual_achievement_date: null })).toBe("Due today");
    expect(alertText({ status: "upcoming", days_remaining: 12, actual_achievement_date: null })).toBe("12d left");
    expect(alertText({ status: "achieved", days_remaining: 5, actual_achievement_date: "2026-01-01" })).toBe("—");
  });

  it("summarises achievement", () => {
    expect(achievementText({ actual_achievement_date: null })).toBe("Pending");
    expect(achievementText({ actual_achievement_date: "2026-01-01", delay_days: 4 })).toBe("Late +4d");
    expect(achievementText({ actual_achievement_date: "2026-01-01", early_completion_days: 2 })).toBe("Early -2d");
    expect(achievementText({ actual_achievement_date: "2026-01-01" })).toBe("On time");
  });

  it("derives one EOT column per approved revision (CM-4b)", () => {
    const rows = [
      { revisions: [] },
      { revisions: [{ revision_number: 1, status: "approved" }] },
      { revisions: [{ revision_number: 1, status: "approved" }, { revision_number: 2, status: "approved" }] },
    ];
    expect(maxRevisionCount(rows)).toBe(2);
    expect(maxRevisionCount([{ revisions: undefined }])).toBe(0);
    expect(revisionAt(rows[2], 1)?.revision_number).toBe(1);
    expect(revisionAt(rows[2], 2)?.revision_number).toBe(2);
    expect(revisionAt(rows[2], 3)).toBeUndefined();
    expect(revisionAt(rows[0], 1)).toBeUndefined();
  });

  it("preserves EOT-1 and EOT-2 independently while EOT-1 is pending and later granted", () => {
    const baseWorkflow: any = {
      submissions: [
        {
          id: "e1", revision_label: "EOT-1",
          items: [{ key_date_id: "m1", milestone_ref: "KD-01", eot_submitted_date: "2026-03-01", contractual_date_at_submission: "2026-01-01" }],
        },
        {
          id: "e2", revision_label: "EOT-2",
          items: [{ key_date_id: "m1", milestone_ref: "KD-01", eot_submitted_date: "2026-05-01", contractual_date_at_submission: "2026-01-01" }],
        },
      ],
      determinations: [],
    };
    const pending = milestoneRevisionLineage(baseWorkflow, { id: "m1", milestone_ref: "KD-01" });
    expect(pending.map((entry) => entry.submission.revision_label)).toEqual(["EOT-1", "EOT-2"]);
    expect(pending.every((entry) => entry.item.contractual_date_at_submission === "2026-01-01")).toBe(true);
    expect(pending.every((entry) => entry.determinations.length === 0)).toBe(true);

    const later = milestoneRevisionLineage({
      ...baseWorkflow,
      determinations: [{
        id: "d1", eot_submission_ids: ["e1"],
        items: [{ key_date_id: "m1", milestone_ref: "KD-01", determination_result: "partially_granted", eot_granted_date: "2026-02-15" }],
      }],
    } as any, { id: "m1", milestone_ref: "KD-01" });
    expect(later[0].determinations[0].item.eot_granted_date).toBe("2026-02-15");
    expect(later[1].item.eot_submitted_date).toBe("2026-05-01");
    expect(later[1].determinations).toEqual([]);
  });
});
