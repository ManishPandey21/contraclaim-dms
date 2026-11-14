import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const evidenceGraphApi = vi.hoisted(() => ({
  getContractTimeline: vi.fn(),
  getEventLinkHistory: vi.fn(),
  verifyEventLink: vi.fn(),
  rejectEventLink: vi.fn(),
  approveEventLink: vi.fn(),
}));

vi.mock("@/services/evidence-graph-api", () => evidenceGraphApi);
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import ContractTimelinePage from "../ContractTimelinePage";

const SUMMARY = {
  total_events: 2, graph_links: 0, ai_suggested: 0, user_verified: 0, approved: 0, rejected: 0, awaiting_review: 0,
};

const event = (id: string, overrides: Record<string, unknown>) => ({
  id, event_type: "letter", title: `Event ${id}`, status: "open", links: [], metadata: {}, ...overrides,
});

describe("ContractTimelinePage undated events (A0b / D1)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evidenceGraphApi.getContractTimeline.mockResolvedValue({
      events: [
        event("dated", { event_date: "2026-03-10T00:00:00", created_at: "2026-09-30T11:45:00" }),
        // Recorded on 30 Sep: that is when the row was written, not when it happened.
        event("undated", { event_date: null, created_at: "2026-09-30T11:45:00", metadata: { date_flags: ["source_missing"] } }),
      ],
      summary: SUMMARY, skip: 0, limit: 50, has_more: false,
    });
    render(<ContractTimelinePage />);
  });

  it("labels an undated event honestly instead of showing a date", async () => {
    const badges = await screen.findAllByTestId("timeline-event-date");
    const undated = badges.find((badge) => badge.getAttribute("data-event-id") === "undated")!;

    expect(undated).toHaveTextContent("Undated");
    expect(undated).toHaveAttribute("title", "Date not established");
    // created_at (30 Sep 2026) is never presented as the event's date.
    expect(within(undated).queryByText("30")).toBeNull();
    expect(within(undated).queryByText("2026")).toBeNull();
    // The card's own date line says the same, not a placeholder that reads like a value.
    expect(screen.getByText("Date not established")).toBeInTheDocument();
  });

  it("still shows a real event date", async () => {
    const badges = await screen.findAllByTestId("timeline-event-date");
    const dated = badges.find((badge) => badge.getAttribute("data-event-id") === "dated")!;

    expect(dated).toHaveTextContent("10");
    expect(dated).toHaveTextContent("2026");
    expect(dated).not.toHaveTextContent("Undated");
  });
});
