import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClaimBenchPage from "@/pages/ClaimBenchPage";
import { ELEMENT_KEYS, type ClaimBenchResponse, type ClaimHead } from "@/types/claimBench";

const api = vi.hoisted(() => ({
  fetchClaimBench: vi.fn(),
  fetchClaimBenchAvailability: vi.fn(),
  fetchClaimSignals: vi.fn(),
  openClaimBench: vi.fn(),
  addClaimHead: vi.fn(),
  patchClaimHead: vi.fn(),
  removeClaimHead: vi.fn(),
  decideElement: vi.fn(),
  testClaimHead: vi.fn(),
  lockClaimBench: vi.fn(),
  unlockClaimBench: vi.fn(),
  setProjectPerspective: vi.fn(),
  fetchNoticeCandidates: vi.fn(),
  setNoticeRequirement: vi.fn(),
  verifyNoticeRequirement: vi.fn(),
}));
vi.mock("@/services/claim-bench-api", () => api);
const permission = vi.hoisted(() => ({ granted: false }));
vi.mock("@/hooks/useHasPermission", () => ({ default: () => permission.granted }));
vi.mock("@/components/claim-bench/DeterminationsPanel", () => ({ default: () => <p>determinations panel</p> }));
vi.mock("@/components/claim-bench/SubmissionPanel", () => ({ default: () => <p>submission panel</p> }));
vi.mock("@/components/claim-bench/InternalAnalysisPanel", () => ({ default: () => <p>internal panel</p> }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const head = (over: Partial<ClaimHead> = {}): ClaimHead => ({
  head_id: "H1",
  title: "Extension of time",
  kind: "time_and_cost",
  event_summary: "Site handed over late.",
  clause_refs: ["8.4"],
  amount_claimed: 184500000,
  eot_days_claimed: 146,
  elements: Object.fromEntries(ELEMENT_KEYS.map((key) => [key, { source_ids: [] }])) as ClaimHead["elements"],
  residual_risks: [],
  missing_information: [],
  test_stale: false,
  ...over,
});

const response = (over: Partial<ClaimBenchResponse> = {}): ClaimBenchResponse => ({
  claim_id: "claim-1",
  claim_ref: "CLM-014",
  title: "Delayed handover of Site",
  claim_type: "eot",
  status: "submitted",
  organization_id: "org-A",
  project_id: "proj-A",
  exists: true,
  bench: { board_version: 3, stage: "heads", heads: [head()] },
  gate: { undecided_elements: 5, unclassified_heads: 1, stale_tests: 0, blockers: ["5 elements still need your call"], can_lock: false },
  steps: [],
  notice_assessment: {
    basis: "event",
    basis_date: "2026-03-02T00:00:00",
    period_days: 28,
    period_source: "assumed_default",
    period_verified: false,
    due_date: "2026-03-30T00:00:00",
    served_on: "2026-04-11T00:00:00",
    served_late_by_days: 12,
    state: "served_late",
  },
  notice_requirement: null,
  perspective: "contractor",
  skill: { skill_key: "indian-infra-claims", available: false, reason: "Awaiting legal approval" },
  read_only: false,
  rollout_mode: "primary",
  ...over,
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/claims/claim-1"]}>
        <Routes>
          <Route path="/claims/:id" element={<ClaimBenchPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Claim Bench page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permission.granted = false;
    api.fetchClaimBench.mockResolvedValue(response());
  });

  it("opens a Contractor's workspace on the board, with the submission and no internal tab without access", async () => {
    renderPage();
    const tabs = await screen.findByRole("tablist", { name: "Claim workspace" });
    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Board",
      "Determinations received",
      "Submission",
    ]);
    expect(within(tabs).getByRole("tab", { name: "Board" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(within(tabs).getByRole("tab", { name: "Submission" }));
    expect(screen.getByText("submission panel")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Heads of claim" })).not.toBeInTheDocument();
  });

  it("opens an Engineer's workspace on the determination, with no submission tab", async () => {
    permission.granted = true;
    api.fetchClaimBench.mockResolvedValue(response({ perspective: "engineer" }));
    renderPage();
    const tabs = await screen.findByRole("tablist", { name: "Claim workspace" });
    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Board",
      "Determination",
      "Internal analysis",
    ]);
    expect(screen.getByText("determinations panel")).toBeInTheDocument();
    await userEvent.click(within(tabs).getByRole("tab", { name: "Internal analysis" }));
    expect(screen.getByText("internal panel")).toBeInTheDocument();
  });

  it("shows an error state when the claim cannot be read", async () => {
    api.fetchClaimBench.mockRejectedValue({ isAxiosError: true, response: { status: 403, data: { detail: "Not in your project" } } });
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("This claim could not be opened");
    expect(screen.getByRole("button", { name: "Back to the register" })).toBeInTheDocument();
  });

  it("offers to open the board for a claim that has none", async () => {
    api.fetchClaimBench.mockResolvedValue(response({ exists: false, bench: { board_version: 0, stage: "heads", heads: [] } }));
    api.openClaimBench.mockResolvedValue(response());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Open the board" }));
    expect(api.openClaimBench).toHaveBeenCalledWith("claim-1");
    expect(await screen.findByRole("heading", { name: /The five elements/ })).toBeInTheDocument();
  });

  it("says plainly that the claims skill is not in use and what a test then does", async () => {
    renderPage();
    const skill = (await screen.findByRole("heading", { name: "Claims skill" })).closest("section")!;
    expect(within(skill).getByText("Not in use")).toBeInTheDocument();
    expect(within(skill).getByText("Awaiting legal approval")).toBeInTheDocument();
    expect(within(skill).getByText(/never proposes that an element is supported/)).toBeInTheDocument();
  });

  it("words a late notice against an assumed period and names no contract source", async () => {
    renderPage();
    const clock = (await screen.findByRole("heading", { name: /Notice clock/ })).closest("section")!;
    expect(within(clock).getByText(/after an assumed 28-day period/)).toBeInTheDocument();
    expect(within(clock).getByText(/no notice period has been recorded from this project's contract/)).toBeInTheDocument();
    expect(within(clock).getByRole("button", { name: "Set from contract" })).toBeInTheDocument();
  });

  it("asks which party the project acts for and will not test a head until told", async () => {
    api.fetchClaimBench.mockResolvedValue(response({ perspective: null }));
    api.setProjectPerspective.mockResolvedValue(undefined);
    renderPage();
    expect(await screen.findByRole("heading", { name: "Which party does this project act for?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Test this head" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Engineer" }));
    expect(api.setProjectPerspective).toHaveBeenCalledWith("proj-A", "engineer");
  });

  it("keeps a proposal apart from the decision, and sends the person's call with the board version", async () => {
    const proposed = head({
      step_id: "s1",
      elements: {
        ...head().elements,
        notice: { source_ids: [], proposed_state: "weak", proposed_by: "record_check", proposed_reason: "Notice was served 12 day(s) after a 28-day period." },
      },
    });
    api.fetchClaimBench.mockResolvedValue(response({ bench: { board_version: 3, stage: "heads", heads: [proposed] } }));
    api.decideElement.mockResolvedValue(response());
    renderPage();
    const group = await screen.findByRole("group", { name: "Your call on Notice" });
    // The proposal is text beside the buttons; no button is pre-selected by it.
    within(group).getAllByRole("button").forEach((button) => expect(button).toHaveAttribute("aria-pressed", "false"));
    expect(screen.getByText("From the records only:")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Notice: proposed weak, not yet decided" })).toBeInTheDocument();
    await userEvent.click(within(group).getByRole("button", { name: "Supported" }));
    await waitFor(() =>
      expect(api.decideElement).toHaveBeenCalledWith("claim-1", "H1", "notice", 3, { state: "supported" }),
    );
  });

  it("lists what stands between the board and locking it, and keeps Lock disabled", async () => {
    renderPage();
    expect(await screen.findByText("5 elements still need your call")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lock the board" })).toBeDisabled();
  });

  it("is read-only once locked, with a way to reopen", async () => {
    api.fetchClaimBench.mockResolvedValue(
      response({
        bench: { board_version: 9, stage: "locked", heads: [head({ classification: "excusable" })] },
        gate: { undecided_elements: 0, unclassified_heads: 0, stale_tests: 0, blockers: [], can_lock: false },
      }),
    );
    api.unlockClaimBench.mockResolvedValue(response());
    renderPage();
    expect(await screen.findByRole("heading", { name: "Board locked" })).toBeInTheDocument();
    expect(screen.getByLabelText("Head of claim")).toBeDisabled();
    // Without claim-manage rights the button is shown but disabled (the server refuses too).
    expect(screen.getByRole("button", { name: "Reopen the board" })).toBeDisabled();
  });

  it("asks a claim manager for a reason before reopening", async () => {
    permission.granted = true;
    api.fetchClaimBench.mockResolvedValue(
      response({
        bench: { board_version: 9, stage: "locked", heads: [head({ classification: "excusable" })] },
        gate: { undecided_elements: 0, unclassified_heads: 0, stale_tests: 0, blockers: [], can_lock: false },
      }),
    );
    api.unlockClaimBench.mockResolvedValue(response());
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Reopen the board" }));
    const confirm = screen.getByRole("button", { name: "Reopen" });
    expect(confirm).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Why is the board being reopened?"), "Engineer changed the event date");
    await userEvent.click(confirm);
    expect(api.unlockClaimBench).toHaveBeenCalledWith("claim-1", 9, "Engineer changed the event date");
    permission.granted = false;
  });
});
