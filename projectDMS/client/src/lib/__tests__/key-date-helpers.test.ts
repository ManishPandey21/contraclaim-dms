import { describe, it, expect } from "vitest";
import {
  statusColor,
  statusLabel,
  alertText,
  achievementText,
  maxRevisionCount,
  revisionAt,
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
});
