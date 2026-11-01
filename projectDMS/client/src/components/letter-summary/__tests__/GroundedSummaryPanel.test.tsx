import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import GroundedSummaryPanel from "../GroundedSummaryPanel";

const { mockToast } = vi.hoisted(() => ({
  mockToast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("sonner", () => ({ toast: mockToast }));

const writeText = vi.fn().mockResolvedValue(undefined);
Object.assign(navigator, { clipboard: { writeText } });

const item = (text: string, pages: number[], status = "source_stated", role = "party_position") => ({
  text,
  role,
  fact_ids: ["f-1"],
  page_numbers: pages,
  verification_status: status,
});

const currentView = () => ({
  document_id: "doc-1",
  summary_status: "current",
  short_summary: "Letter from Gulermak JV to the Engineer regarding the revised rework claim.",
  generated_from: { canonical_revision: 3 },
  current_evidence: { canonical_revision: 3, canonical_sha256: "abc" },
  detailed_summary: {
    schema_version: "1",
    executive_overview: "Letter GLM/4299 dated 22 June 2024 from Gulermak JV.",
    background: [],
    material_events: [],
    issues_positions: [item("Gulermak JV states (as written): “the soil collapse caused additional cost”", [1])],
    contractual_basis: [item("The letter cites Clause 17.1 of GCC", [1], "source_stated", "source_fact")],
    requests_relief: [],
    claims_reservations: [],
    dates_periods: [],
    amounts_quantities: [item("INR 34,011,265 (written “Rs. 3,40,11,265/-”)", [3], "verified", "source_fact")],
    references: [],
    uncertainties: [
      item("Page 5 failed automated quality checks (numeric_integrity) and was approved as-is by a reviewer.", [5], "human_approved", "uncertainty"),
    ],
    facts: [],
    markdown: "**Executive overview**\n\nLetter GLM/4299",
    generated_from: { canonical_revision: 3, canonical_sha256: "abc", review_resolution: { approved_pages: [5] } },
    generator: { mode: "deterministic", prompt_version: "grounded_letter_summary.v1", generated_at: "2026-10-16", degraded: false },
  },
});

let view: any;
let generated: any;

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url.endsWith("/grounded-summary/generate") && init?.method === "POST") {
    return jsonResponse(generated);
  }
  if (url.endsWith("/grounded-summary")) return jsonResponse(view);
  return jsonResponse({}, 404);
}) as any;

function renderPanel(canGenerate = true) {
  return render(
    <MemoryRouter>
      <GroundedSummaryPanel documentId="doc-1" canGenerate={canGenerate} />
    </MemoryRouter>
  );
}

beforeEach(() => {
  view = currentView();
  generated = currentView();
  writeText.mockClear();
  mockToast.success.mockClear();
  mockToast.error.mockClear();
});

describe("GroundedSummaryPanel", () => {
  it("shows the short summary and only the sections that have items", async () => {
    renderPanel();

    expect(await screen.findByText(view.short_summary)).toBeInTheDocument();
    expect(screen.getByText("Detailed Summary")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Contractual Basis Cited" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Uncertainty / Review" })).toBeInTheDocument();
    // Empty sections are not rendered as noise.
    expect(screen.queryByRole("heading", { name: "Background" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Referenced Correspondence" })).not.toBeInTheDocument();
  });

  it("links each cited page to that page of the source document", async () => {
    renderPanel();

    const link = await screen.findByRole("link", { name: "Open page 3 of the source document" });
    expect(link).toHaveAttribute("href", "/documentviewer/doc-1?page=3");
  });

  it("labels verification status, including human approval of a machine-failed page", async () => {
    renderPanel();

    const uncertainty = await screen.findByRole("heading", { name: "Uncertainty / Review" });
    const list = uncertainty.parentElement as HTMLElement;
    expect(within(list).getByText(/Page 5 failed automated quality checks/)).toBeInTheDocument();
    expect(within(list).getByText("Human-approved")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText(/released by human review/)).toBeInTheDocument();
  });

  it("copies the short and detailed summary", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole("button", { name: /copy summary/i }));

    // user-event installs its own clipboard; read back what the page wrote.
    const copied = await navigator.clipboard.readText();
    expect(copied).toContain(view.short_summary);
    expect(copied).toContain("Executive overview");
    expect(mockToast.success).toHaveBeenCalledWith("Summary copied");
  });

  it("never renders a stale summary and offers regeneration", async () => {
    const user = userEvent.setup();
    view = { ...currentView(), summary_status: "stale", short_summary: null, detailed_summary: null };
    renderPanel();

    expect(await screen.findByText(/changed since the summary was generated/)).toBeInTheDocument();
    expect(screen.queryByText("Detailed Summary")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /copy summary/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /regenerate/i }));
    expect(await screen.findByText("Detailed Summary")).toBeInTheDocument();
  });

  it("explains a review hold and offers nothing to generate", async () => {
    view = { ...currentView(), summary_status: "blocked_for_review", short_summary: null, detailed_summary: null };
    renderPanel();

    expect(await screen.findByText(/awaiting extraction review/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });

  it("hides generation from users who cannot edit metadata", async () => {
    view = { ...currentView(), summary_status: "not_generated", short_summary: null, detailed_summary: null };
    renderPanel(false);

    expect(await screen.findByText(/No grounded summary has been generated/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });
});
