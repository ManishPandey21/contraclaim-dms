import { describe, it, expect } from "vitest";
import {
  apiErrorDetail,
  buildSlaStateMap,
  describeNotice,
  followUpTaskTitle,
  parseClauseList,
} from "../claims-helpers";
import type { NoticeAssessment } from "@/services/claims-api";
import type { SlaItem } from "@/services/sla-api";

const item = (over: Partial<SlaItem>): SlaItem => ({
  claim_id: "c1",
  kind: "response",
  days_remaining: 5,
  state: "approaching",
  ...over,
});

describe("buildSlaStateMap (Phase 2)", () => {
  it("keeps the most urgent item per claim (breached outranks approaching)", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "c1", state: "approaching", days_remaining: 3 }),
      item({ claim_id: "c1", state: "breached", days_remaining: -2, kind: "time_bar" }),
    ]);
    expect(map.c1.state).toBe("breached");
    expect(map.c1.days_remaining).toBe(-2);
  });

  it("within the same state, the nearer deadline wins", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "c2", state: "approaching", days_remaining: 9 }),
      item({ claim_id: "c2", state: "approaching", days_remaining: 2 }),
    ]);
    expect(map.c2.days_remaining).toBe(2);
  });

  it("maps each claim independently", () => {
    const map = buildSlaStateMap([
      item({ claim_id: "a", state: "breached", days_remaining: -1 }),
      item({ claim_id: "b", state: "approaching", days_remaining: 4 }),
    ]);
    expect(map.a.state).toBe("breached");
    expect(map.b.state).toBe("approaching");
  });
});

describe("followUpTaskTitle", () => {
  it("prefers the claim ref, falls back to title", () => {
    expect(followUpTaskTitle({ claim_ref: "EOT-001", title: "Monsoon" })).toBe(
      "Follow up: EOT-001",
    );
    expect(followUpTaskTitle({ claim_ref: null, title: "Monsoon delay" })).toBe(
      "Follow up: Monsoon delay",
    );
  });
});

const notice = (over: Partial<NoticeAssessment>): NoticeAssessment => ({
  basis: "event",
  basis_date: "2026-03-02T00:00:00",
  period_days: 28,
  period_source: "assumed_default",
  period_verified: false,
  due_date: "2026-03-30T00:00:00",
  state: "open",
  ...over,
});

describe("describeNotice", () => {
  it("words a late notice against an assumed period, never against the contract", () => {
    const summary = describeNotice(
      notice({ state: "served_late", served_on: "2026-04-11T00:00:00", served_late_by_days: 12 }),
    )!;
    expect(summary.label).toBe("Served 12 days late");
    expect(summary.sentence).toBe(
      "Notice served 12 days after an assumed 28-day period, counted from the event date. " +
        "Check the notice clause in the contract before relying on this.",
    );
    expect(summary.tone).toBe("danger");
    expect(summary.sentence).not.toMatch(/under the contract|time-barred|out of time/i);
  });

  it("names an organisation rule as the organisation's, and the awareness date as the start", () => {
    const summary = describeNotice(
      notice({
        basis: "awareness",
        period_days: 14,
        period_source: "organisation_rule",
        state: "served_in_time",
        served_on: "2026-03-10T00:00:00",
      }),
    )!;
    expect(summary.sentence).toContain("the 14-day period set in your organisation's rules");
    expect(summary.sentence).toContain("counted from the awareness date");
    expect(summary.tone).toBe("ok");
  });

  it("counts down an outstanding notice and flags an overdue one", () => {
    expect(describeNotice(notice({ state: "open", days_remaining: 20 }))!.label).toBe("Notice due in 20 days");
    expect(describeNotice(notice({ state: "due_soon", days_remaining: 1 }))!.label).toBe("Notice due in 1 day");
    expect(describeNotice(notice({ state: "due_soon", days_remaining: 0 }))!.label).toBe("Notice due today");
    const overdue = describeNotice(notice({ state: "overdue", days_remaining: -3 }))!;
    expect(overdue.label).toBe("Notice 3 days overdue");
    expect(overdue.tone).toBe("danger");
  });

  it("drops the contract caveat only once the period is verified", () => {
    const verified = describeNotice(notice({ state: "open", days_remaining: 5, period_verified: true }))!;
    expect(verified.sentence).not.toContain("Check the notice clause");
  });

  it("has nothing to say when there is no assessment", () => {
    expect(describeNotice(null)).toBeNull();
    expect(describeNotice(undefined)).toBeNull();
  });
});

describe("apiErrorDetail", () => {
  it("returns the server's message and nothing else", () => {
    expect(apiErrorDetail({ response: { data: { detail: "A claim that is closed cannot be moved to draft." } } })).toBe(
      "A claim that is closed cannot be moved to draft.",
    );
    expect(apiErrorDetail({ response: { data: { detail: [{ msg: "x" }] } } })).toBeNull();
    expect(apiErrorDetail(new Error("network"))).toBeNull();
    expect(apiErrorDetail(null)).toBeNull();
  });
});

describe("parseClauseList", () => {
  it("splits, trims, strips the word clause and removes duplicates", () => {
    expect(parseClauseList("8.4, Sub-Clause 20.1; clause 2.1\n8.4 ,")).toEqual(["8.4", "20.1", "2.1"]);
    expect(parseClauseList("  ")).toEqual([]);
  });
});
