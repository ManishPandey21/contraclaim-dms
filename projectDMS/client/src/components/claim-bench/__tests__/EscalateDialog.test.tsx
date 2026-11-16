import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type { DraftCitations } from "@/services/arbitration-drafting-api";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DraftCitationPanel from "@/components/arbitration/DraftCitationPanel";
import EscalateDialog from "@/components/claim-bench/EscalateDialog";
import type { EscalationPreview } from "@/services/claim-escalation-api";

const api = vi.hoisted(() => ({ previewEscalation: vi.fn(), runEscalation: vi.fn() }));
const cases = vi.hoisted(() => ({ listArbitrationCases: vi.fn() }));
const drafts = vi.hoisted(() => ({ getArbitrationDraftCitations: vi.fn() }));
vi.mock("@/services/claim-escalation-api", () => api);
vi.mock("@/services/arbitration-cases-api", () => cases);
vi.mock("@/services/arbitration-drafting-api", () => drafts);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const preview = (over: Partial<EscalationPreview> = {}): EscalationPreview => ({
  project_id: "proj-A",
  claims: [
    {
      claim_id: "c1",
      claim_ref: "CLM-014",
      title: "Delayed handover",
      status: "submitted",
      board_locked: false,
      has_board: true,
      rows: { "claim-matrix": 2 },
      weak: ["The board is not locked: some elements or heads are not decided."],
      not_carried: ["H2: 2 cost lines (INR 12,20,00,000.00) whose proof is not linked"],
      status_after: "disputed",
    },
  ],
  totals: { "claim-matrix": 2, "document-index": 3 },
  blockers: [],
  can_escalate: true,
  ...over,
});

const mount = (onEscalated = vi.fn()) =>
  render(
    <MemoryRouter>
      <EscalateDialog claimIds={["c1"]} claimTitle="Delayed handover" projectId="proj-A" open onOpenChange={vi.fn()} onEscalated={onEscalated} />
    </MemoryRouter>
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.previewEscalation.mockResolvedValue(preview());
  cases.listArbitrationCases.mockResolvedValue([
    { _id: "case-1", project_id: "proj-A", title: "ABC v. Authority" },
    { _id: "case-9", project_id: "proj-B", title: "Other project" },
  ]);
});

describe("Escalate to arbitration", () => {
  it("shows what is carried, what is weak and what is left behind before anything changes", async () => {
    mount();
    const carried = (await screen.findByRole("heading", { name: "What will be carried over" })).closest("section")!;
    expect(within(carried).getByText("Claim matrix (heads of claim)")).toBeInTheDocument();
    expect(within(carried).getByText("3 rows")).toBeInTheDocument();
    expect(screen.getByText(/The board is not locked/)).toBeInTheDocument();
    expect(screen.getByText(/whose proof is not linked/)).toBeInTheDocument();
    expect(screen.getByText(/Internal analysis .* is never carried into a case/)).toBeInTheDocument();
    expect(screen.getByText(/status becomes “disputed”/)).toBeInTheDocument();
    expect(api.runEscalation).not.toHaveBeenCalled();
    // Only cases of the claim's own project are offered.
    expect(screen.getByRole("option", { name: "Add to: ABC v. Authority" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Add to: Other project" })).not.toBeInTheDocument();
  });

  it("creates a new case by title, or adds to an existing one, and reports the result", async () => {
    const onEscalated = vi.fn();
    api.runEscalation.mockResolvedValue({
      case_id: "case-1", case_title: "ABC v. Authority", created_case: false,
      rows_created: { "claim-matrix": 2 }, rows_already_present: 3, claims: [],
    });
    mount(onEscalated);
    const escalate = await screen.findByRole("button", { name: "Escalate" });
    await userEvent.clear(screen.getByLabelText("Case title"));
    expect(escalate).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Arbitration case"), "case-1");
    expect(screen.queryByLabelText("Case title")).not.toBeInTheDocument();
    // The board is not locked and the record is weak: Escalate waits for the acknowledgement (D-18).
    await waitFor(() => expect(escalate).toBeDisabled());
    await userEvent.click(await screen.findByRole("checkbox", { name: /board of CLM-014 is not locked/ }));
    await waitFor(() => expect(escalate).toBeEnabled());
    await userEvent.click(escalate);
    await waitFor(() =>
      expect(api.runEscalation).toHaveBeenCalledWith(["c1"], { case_id: "case-1", acknowledged_weak: ["c1"] })
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Case extended: ABC v. Authority. 2 rows added, all awaiting review. 3 were already there");
    expect(screen.getByRole("link", { name: "Open the case" })).toHaveAttribute("href", "/arbitration/cases/case-1");
    expect(onEscalated).toHaveBeenCalled();
  });

  it("works the preview out for the case chosen (C-10)", async () => {
    mount();
    await screen.findByRole("button", { name: "Escalate" });
    expect(api.previewEscalation).toHaveBeenLastCalledWith(["c1"], undefined);
    await userEvent.selectOptions(screen.getByLabelText("Arbitration case"), "case-1");
    await waitFor(() => expect(api.previewEscalation).toHaveBeenLastCalledWith(["c1"], "case-1"));
  });

  it("sends a new case with one idempotency key, the same on a repeat (D-18)", async () => {
    api.runEscalation.mockRejectedValueOnce(new Error("network")).mockResolvedValue({
      case_id: "case-2", case_title: "Delayed handover", created_case: true, rows_created: {}, rows_already_present: 0, claims: [],
    });
    mount();
    const escalate = await screen.findByRole("button", { name: "Escalate" });
    await userEvent.click(await screen.findByRole("checkbox", { name: /board of CLM-014 is not locked/ }));
    await userEvent.click(escalate);
    await waitFor(() => expect(api.runEscalation).toHaveBeenCalledTimes(1));
    await userEvent.click(await screen.findByRole("button", { name: "Escalate" }));
    await waitFor(() => expect(api.runEscalation).toHaveBeenCalledTimes(2));
    const [first, second] = api.runEscalation.mock.calls.map((call) => call[1]);
    expect(first.new_case.title).toBe("Delayed handover");
    expect(first.idempotency_key).toMatch(/^esc-[A-Za-z0-9-]{8,}$/);
    expect(second.idempotency_key).toBe(first.idempotency_key);
  });

  it("escalates several claims at once, each with its own section and acknowledgement (4B-40)", async () => {
    const two = preview({
      claims: [
        preview().claims[0],
        { ...preview().claims[0], claim_id: "c2", claim_ref: "CLM-015", title: "Idle plant", board_locked: true },
      ],
    });
    api.previewEscalation.mockResolvedValue(two);
    api.runEscalation.mockResolvedValue({
      case_id: "case-1", case_title: "ABC v. Authority", created_case: false, rows_created: {}, rows_already_present: 0, claims: [],
    });
    render(
      <MemoryRouter>
        <EscalateDialog claimIds={["c1", "c2"]} claimTitle="Two claims" projectId="proj-A" open onOpenChange={vi.fn()} onEscalated={vi.fn()} />
      </MemoryRouter>
    );
    expect(await screen.findByRole("region", { name: "Claim CLM-014" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Claim CLM-015" })).toBeInTheDocument();
    expect(api.previewEscalation).toHaveBeenCalledWith(["c1", "c2"], undefined);
    // Only the unlocked, weak claim needs acknowledging.
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    await userEvent.selectOptions(screen.getByLabelText("Arbitration case"), "case-1");
    await userEvent.click(await screen.findByRole("checkbox", { name: /board of CLM-014 is not locked/ }));
    await userEvent.click(screen.getByRole("button", { name: "Escalate" }));
    await waitFor(() =>
      expect(api.runEscalation).toHaveBeenCalledWith(["c1", "c2"], { case_id: "case-1", acknowledged_weak: ["c1"] })
    );
  });

  it("will not escalate while the server lists a blocker", async () => {
    api.previewEscalation.mockResolvedValue(
      preview({ can_escalate: false, blockers: ["CLM-014 is already in another arbitration case."] })
    );
    mount();
    expect(await screen.findByText("CLM-014 is already in another arbitration case.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeDisabled();
  });
});

describe("Draft citation panel", () => {
  const citations = (over = {}) => ({
    draft_id: "d1",
    version: 3,
    scratch: false,
    counts: { verified: 1, unverified: 1, unresolved: 1, cited: 3, ledger: 4 },
    items: [
      { source_key: "S1", state: "verified", uses: 2 },
      { source_key: "S2", state: "unverified", uses: 1, reason: "Source status is needs_review." },
      { source_key: "S9", state: "unresolved", uses: 1, reason: "Not in this draft's source ledger." },
    ],
    approval_blockers: ["Draft cites sources that are not yet verified: S2."],
    pleaded_head_gaps: [],
    missing_evidence: ["Baseline programme"],
    can_be_approved: false,
    cannot_be_approved_reason: "The blockers listed must be resolved in a new version before approval.",
    ...over,
  });

  it("shows the three counts, each citation's state in words, and what blocks approval", async () => {
    drafts.getArbitrationDraftCitations.mockResolvedValue(citations());
    render(<DraftCitationPanel draftId="d1" version={3} />);
    const panel = await screen.findByRole("region", { name: "Citation states" });
    for (const label of ["Verified", "Unverified", "Unresolved"]) {
      expect(within(panel).getByText(label, { selector: "dt" }).nextElementSibling).toHaveTextContent("1");
    }
    const list = within(panel).getByRole("list", { name: "Citations in this version" });
    expect(within(list).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "✓ S1: Verified", "? S2: Unverified", "✕ S9: Unresolved",
    ]);
    const blocked = within(panel).getByRole("status");
    expect(blocked).toHaveTextContent("This version cannot be approved or exported for filing");
    expect(blocked).toHaveTextContent("Draft cites sources that are not yet verified: S2.");
    expect(blocked).toHaveTextContent("Evidence required: Baseline programme");
  });

  it("shows an exhibit chip with its number, title and state, and why an unresolved one is not found (4B-39)", async () => {
    drafts.getArbitrationDraftCitations.mockResolvedValue(
      citations({
        counts: { verified: 1, unverified: 0, unresolved: 1, cited: 2, ledger: 1 },
        items: [
          { kind: "exhibit", source_key: "Ex. C-1", exhibit_id: "C-1", state: "verified", uses: 1, label: "Letter of 3 May 2026" },
          { kind: "exhibit", source_key: "Ex. C-12", exhibit_id: "C-12", state: "unresolved", uses: 1,
            reason: "Exhibit C-12 is not in this case's exhibit list for this draft." },
        ],
      })
    );
    render(<DraftCitationPanel draftId="d1" version={3} />);
    const list = await screen.findByRole("list", { name: "Citations in this version" });
    const chips = within(list).getAllByRole("listitem").map((item) => item.textContent);
    expect(chips[0]).toBe("✓ Ex. C-1: Letter of 3 May 2026: Verified");
    expect(chips[1]).toContain("Ex. C-12");
    expect(chips[1]).toContain("not in this case's exhibit list");
  });

  it("uses the page's copy of the citations instead of fetching again", async () => {
    render(<DraftCitationPanel draftId="d1" version={3} data={citations() as unknown as DraftCitations} />);
    expect(await screen.findByRole("region", { name: "Citation states" })).toBeInTheDocument();
    expect(drafts.getArbitrationDraftCitations).not.toHaveBeenCalled();
  });

  it("says so when nothing in the evidence blocks approval, without claiming the workflow gates are passed", async () => {
    drafts.getArbitrationDraftCitations.mockResolvedValue(
      citations({ counts: { verified: 1, unverified: 0, unresolved: 0, cited: 1, ledger: 1 }, items: [{ source_key: "S1", state: "verified", uses: 1 }], approval_blockers: [], missing_evidence: [], can_be_approved: true, cannot_be_approved_reason: null })
    );
    render(<DraftCitationPanel draftId="d1" version={3} />);
    expect(await screen.findByText(/Nothing in the evidence blocks approval of version 3\. The workflow gates still apply\./)).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
