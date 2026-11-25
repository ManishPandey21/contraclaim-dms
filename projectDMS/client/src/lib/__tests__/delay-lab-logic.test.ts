import { describe, expect, it } from "vitest";
import { buildTimeline, eventNeedsDecision, netEotExplanation, netEotText } from "../delay-lab-logic";
import type { DelayEventView, DelayView, OverlapView } from "@/types/claimBench";

const event = (over: Partial<DelayEventView> = {}): DelayEventView => ({
  event_id: "E1",
  link_id: "l1",
  label: "Hindrance HR-E1",
  title: "Site not handed over",
  start_date: "2026-03-01T00:00:00",
  end_date: "2026-03-31T00:00:00",
  open_ended: false,
  revision_current: true,
  counted: true,
  days: 31,
  decision: { risk_owner: "employer", criticality: "critical" },
  ...over,
});

const overlap = (over: Partial<OverlapView> = {}): OverlapView => ({
  overlap_id: "2026-03-20_2026-03-31",
  start_date: "2026-03-20T00:00:00",
  end_date: "2026-03-31T00:00:00",
  days: 12,
  employer_event_ids: ["E1"],
  contractor_event_ids: ["C1"],
  ...over,
});

const net = (over: Partial<DelayView["net"]> = {}): DelayView["net"] => ({
  low: 19,
  high: 31,
  days: null,
  settled: false,
  previously_granted: 0,
  employer_only_days: 19,
  overlap_days_to_eot: 0,
  neutral_days_included: 0,
  ...over,
});

describe("buildTimeline", () => {
  const contractor = event({
    event_id: "C1",
    label: "Hindrance HR-C1",
    start_date: "2026-03-20T00:00:00",
    end_date: "2026-04-10T00:00:00",
    decision: { risk_owner: "contractor", criticality: "critical" },
  });

  it("places events and overlaps on one inclusive day axis", () => {
    const timeline = buildTimeline({ events: [event(), contractor], overlaps: [overlap()], as_of_date: null })!;
    expect(timeline.start).toBe("2026-03-01");
    expect(timeline.end).toBe("2026-04-10");
    expect(timeline.totalDays).toBe(41);
    const [employerBar, contractorBar] = timeline.bars;
    expect(employerBar.left).toBe(0);
    expect(employerBar.width).toBeCloseTo((31 / 41) * 100);
    expect(contractorBar.left).toBeCloseTo((19 / 41) * 100);
    expect(contractorBar.left + contractorBar.width).toBeCloseTo(100);
    expect(timeline.overlaps[0].left).toBeCloseTo((19 / 41) * 100);
    expect(timeline.overlaps[0].width).toBeCloseTo((12 / 41) * 100);
    expect(timeline.overlaps[0].decided).toBe(false);
  });

  it("keeps an event out of a party's lane until a person has decided its owner", () => {
    const proposed = event({ decision: { proposed_risk_owner: "employer", proposed_criticality: "critical" }, counted: false });
    const timeline = buildTimeline({ events: [proposed], overlaps: [], as_of_date: null })!;
    expect(timeline.bars[0].lane).toBe("undecided");
    expect(timeline.bars[0].counted).toBe(false);
  });

  it("runs an open-ended event to the analysis date and leaves it out without one", () => {
    const open = event({ end_date: null, open_ended: true });
    expect(buildTimeline({ events: [open], overlaps: [], as_of_date: null })).toBeNull();
    const dated = buildTimeline({ events: [open], overlaps: [], as_of_date: "2026-03-10T00:00:00" })!;
    expect(dated.totalDays).toBe(10);
    expect(dated.bars[0].openEnded).toBe(true);
  });

  it("draws a one-day event with a visible width and returns null for nothing", () => {
    const single = event({ end_date: "2026-03-01T00:00:00" });
    expect(buildTimeline({ events: [single], overlaps: [], as_of_date: null })!.bars[0].width).toBe(100);
    expect(buildTimeline({ events: [], overlaps: [], as_of_date: null })).toBeNull();
  });
});

describe("net extension wording", () => {
  it("states a range as a range until every overlap is decided", () => {
    expect(netEotText(net())).toBe("19 to 31 days");
    expect(netEotText(net({ settled: true, days: 31, low: 31, high: 31 }))).toBe("31 days");
    expect(netEotText(net({ settled: true, days: 1, low: 1, high: 1 }))).toBe("1 day");
    expect(netEotText(net({ settled: true, days: 0, low: 0, high: 0 }))).toBe("0 days");
  });

  it("explains where the figure comes from", () => {
    const view = { net: net({ previously_granted: 10 }), overlaps: [overlap()] } as DelayView;
    expect(netEotExplanation(view)).toBe(
      "19 Employer critical days with no Contractor delay; 0 from decided overlaps, 1 overlap still to decide; less 10 already granted.",
    );
    const decided = {
      net: net({ settled: true, days: 31, overlap_days_to_eot: 12, neutral_days_included: 5 }),
      overlaps: [overlap({ finding: { treatment: "eot_granted_no_cost", basis: "Sub-Clause 8.4" } })],
    } as DelayView;
    expect(netEotExplanation(decided)).toBe(
      "19 Employer critical days with no Contractor delay; 12 from overlaps as decided; 5 neutral days.",
    );
  });
});

describe("eventNeedsDecision", () => {
  it("needs both a risk owner and a call on criticality", () => {
    expect(eventNeedsDecision(event())).toBe(false);
    expect(eventNeedsDecision(event({ decision: { risk_owner: "employer" } }))).toBe(true);
    expect(eventNeedsDecision(event({ decision: { proposed_risk_owner: "employer", proposed_criticality: "critical" } }))).toBe(true);
  });
});
