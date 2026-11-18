import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DeterminationsPanel from "@/components/claim-bench/DeterminationsPanel";
import InternalAnalysisPanel from "@/components/claim-bench/InternalAnalysisPanel";
import SubmissionPanel from "@/components/claim-bench/SubmissionPanel";
import { DeterminationContext } from "@/components/letter-bench/DeterminationContext";
import {
  daysOnRecord,
  deadlineState,
  findingProblem,
  informationInput,
  letterOptions,
  linesOf,
} from "@/lib/claim-findings-logic";
import type {
  DeterminationView,
  DeterminationsResponse,
  SimulationsResponse,
  StrategyNoteResponse,
  SubmissionView,
} from "@/types/claimFindings";

const api = vi.hoisted(() => ({
  fetchDeterminations: vi.fn(),
  createDetermination: vi.fn(),
  setHeadFinding: vi.fn(),
  actOnDetermination: vi.fn(),
  submitDetermination: vi.fn(),
  reassignDetermination: vi.fn(),
  reviseDetermination: vi.fn(),
  fetchSimulations: vi.fn(),
  simulateResponse: vi.fn(),
  fetchStrategyNote: vi.fn(),
  saveStrategyNote: vi.fn(),
  generateStrategyNote: vi.fn(),
  fetchSubmission: vi.fn(),
  composeSubmission: vi.fn(),
  editSubmissionSection: vi.fn(),
  downloadSubmission: vi.fn(),
}));
vi.mock("@/services/claim-findings-api", () => api);
vi.mock("@/services/document-relationships-api", () => ({
  listEntityDocumentLinks: vi.fn().mockResolvedValue([
    { _id: "l1", document_id: "doc-1", document: { _id: "doc-1", subject: "Engineer's determination letter" } },
  ]),
}));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: {
    getUsers: vi.fn().mockResolvedValue([
      { id: "user-1", first_name: "Asha", last_name: "Rao" },
      { id: "user-2", first_name: "Vikram", last_name: "Nair" },
    ]),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// The approver comes from the server's picker: only project members holding determination authority.
const pickers = vi.hoisted(() => ({
  listPickerUsers: vi.fn().mockResolvedValue({
    items: [
      { id: "user-1", name: "Asha Rao" },
      { id: "user-2", name: "Vikram Nair" },
    ],
    truncated: false,
  }),
}));
vi.mock("@/services/pickers-api", () => pickers);

const INTERNAL = "Internal analysis — not a determination";
const NOT_FOR_ISSUE = "Internal — not for issue";

const determination = (over: Partial<DeterminationView["determination"]> = {}, view: Partial<DeterminationView> = {}): DeterminationView => ({
  determination: {
    _id: "det-1",
    claim_id: "c1",
    origin: "authored",
    revision: 1,
    issued_by: "engineer",
    status: "draft",
    heads: [
      {
        head_id: "H1",
        head_title: "Delayed handover",
        result: null,
        findings: "",
        residual_risks: [],
        information_required: [],
        proposal: {
          result: "insufficient_information",
          findings: "No finding is proposed.",
          residual_risks: [],
          information_required: ["Notice: no notice is recorded as served"],
          proposed_by: "record_check",
        },
      },
    ],
    created_by: "user-1",
    created_at: "2026-11-16T00:00:00",
    history: [],
    version: 2,
    ...over,
  },
  figures: [{ head_id: "H1", days_claimed: 146, days_assessed_low: 19, days_assessed_high: 31, amount_claimed: 4500000, amount_supported: 500000, amount_locked: 4000000 }],
  board_changed: false,
  editable: true,
  blockers: ["No finding yet on: H1"],
  ...view,
});

const determinations = (over: Partial<DeterminationsResponse> = {}): DeterminationsResponse => ({
  claim_id: "c1",
  project_id: "p1",
  perspective: "engineer",
  can_author: true,
  items: [determination()],
  ...over,
});

const submission = (over: Partial<SubmissionView> = {}): SubmissionView => ({
  claim_id: "c1",
  exists: true,
  sections: [
    {
      stale: true,
      section: {
        key: "executive_summary",
        title: "Executive summary",
        paragraphs: [
          { text: "Under head H1 the Contractor claims payment of INR 5,00,000.00.", sources: [{ kind: "quantum", ref: "H1", label: "Quantum build-up of H1" }] },
        ],
        internal_notes: ["H1: 1 cost line (INR 40,00,000.00) left out: proof not linked."],
        produced_by: "record_composition",
        degraded: true,
        degraded_reason: "Skill not used: No published version",
        composed_at: "2026-11-16T00:00:00",
      },
    },
  ],
  evidence: [{ number: "E-1", document_id: "doc-1", title: "Plant log March 2026", reference: "PL/03", date: "2026-03-31T00:00:00" }],
  composed_at: "2026-11-16T00:00:00",
  can_compose: true,
  stale_sections: 1,
  approved_for_issue: false,
  can_export: false,
  cannot_export_reason: "1 section no longer match the record. Re-compose before exporting.",
  ...over,
});

function mount(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("findings logic", () => {
  const input = { result: "partial" as const, days: 10, amount: null, findings: "Reasons.", residual_risks: [], information_required: [] };
  it("refuses figures on a head that is not made out, and a bare 'cannot be determined'", () => {
    expect(findingProblem(input)).toBeNull();
    expect(findingProblem({ ...input, result: "none" })).toMatch(/cannot carry days/);
    expect(findingProblem({ ...input, result: "insufficient_information", days: null })).toMatch(/what information/);
    expect(findingProblem({ ...input, findings: " " })).toMatch(/reasons/);
    expect(findingProblem({ ...input, result: null, findings: "" })).toBeNull();
  });
  it("describes the days on record, as a range while overlaps are undecided", () => {
    expect(daysOnRecord({ head_id: "H1", days_claimed: 146, days_assessed_low: 19, days_assessed_high: 31 })).toBe(
      "146 claimed · 19 to 31 on the delay assessment, overlaps undecided"
    );
    expect(daysOnRecord({ head_id: "H1", days_assessed: 25 })).toBe("25 on the delay assessment");
    expect(daysOnRecord(undefined)).toBe("No days on record");
    expect(linesOf(" a \n\n b ")).toEqual(["a", "b"]);
  });
});

describe("Determinations", () => {
  it("tells a Contractor's workspace why it cannot determine, and still lets it record one received", async () => {
    api.fetchDeterminations.mockResolvedValue(
      determinations({ perspective: "contractor", can_author: false, cannot_author_reason: "This project acts as Contractor.", items: [] })
    );
    api.createDetermination.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText("This project acts as Contractor.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start a determination" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Record one received" }));
    const start = screen.getByRole("button", { name: "Start the transcription" });
    expect(start).toBeDisabled();
    await userEvent.selectOptions(await screen.findByLabelText("The document it arrived in"), "doc-1");
    await userEvent.type(screen.getByLabelText("Its reference"), "ENG/DET/7");
    await userEvent.click(start);
    await waitFor(() =>
      expect(api.createDetermination).toHaveBeenCalledWith("c1", {
        origin: "received",
        issued_by: "engineer",
        source_document_id: "doc-1",
        reference: "ENG/DET/7",
        determination_date: null,
      })
    );
  });

  it("in an Engineer's workspace, a received determination defaults to the Employer, never to itself (4B-26)", async () => {
    api.fetchDeterminations.mockResolvedValue(determinations({ perspective: "engineer", items: [] }));
    api.createDetermination.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    await userEvent.click(await screen.findByRole("button", { name: "Record one received" }));
    expect(screen.getByLabelText("Issued by")).toHaveValue("employer");
    await userEvent.selectOptions(await screen.findByLabelText("The document it arrived in"), "doc-1");
    await userEvent.click(screen.getByRole("button", { name: "Start the transcription" }));
    await waitFor(() =>
      expect(api.createDetermination).toHaveBeenCalledWith("c1", expect.objectContaining({ origin: "received", issued_by: "employer" }))
    );
  });

  it("shows a proposal as a proposal and records a finding only when the person saves it", async () => {
    api.fetchDeterminations.mockResolvedValue(determinations());
    api.setHeadFinding.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText(/Proposed from the records only/)).toBeInTheDocument();
    expect(screen.getByText("No finding yet")).toBeInTheDocument();
    expect(screen.getByText(/19 to 31 on the delay assessment, overlaps undecided/)).toBeInTheDocument();
    expect(screen.getByText(/₹40,00,000 without proof/)).toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save finding" });
    await userEvent.click(screen.getByRole("button", { name: "Start from this proposal" }));
    expect(api.setHeadFinding).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Finding")).toHaveValue("insufficient_information");
    await userEvent.type(screen.getByLabelText("Days"), "10");
    expect(save).toBeDisabled();
    expect(screen.getByText(/cannot carry days/)).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Days"));
    await userEvent.click(save);
    await waitFor(() =>
      expect(api.setHeadFinding).toHaveBeenCalledWith("c1", "det-1", "H1", 2, {
        result: "insufficient_information",
        days: null,
        amount: null,
        findings: "No finding is proposed.",
        residual_risks: [],
        information_required: [{ item: "Notice: no notice is recorded as served", deadline: null, consequence: null }],
      })
    );
  });

  it("will not submit with findings outstanding or without an approver who is someone else", async () => {
    api.fetchDeterminations.mockResolvedValue(
      determinations({
        items: [
          determination(
            { heads: [{ head_id: "H1", head_title: "Delayed handover", result: "partial", days: 90, findings: "Reasons.", residual_risks: [], information_required: [] }] },
            { blockers: [] }
          ),
        ],
      })
    );
    api.submitDetermination.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    const submit = await screen.findByRole("button", { name: /Submit for approval/ });
    expect(submit).toBeDisabled();
    const approver = screen.getByLabelText("Approver");
    await waitFor(() => expect(within(approver).getByRole("option", { name: "Vikram Nair" })).toBeInTheDocument());
    // The person who made the findings is not offered as their own approver.
    expect(within(approver).queryByRole("option", { name: "Asha Rao" })).not.toBeInTheDocument();
    await userEvent.selectOptions(approver, "user-2");
    await userEvent.click(submit);
    await waitFor(() => expect(api.submitDetermination).toHaveBeenCalledWith("c1", "det-1", 2, "user-2"));
    expect(pickers.listPickerUsers).toHaveBeenCalledWith({ projectId: "p1", permission: "dms.claim.determine", q: "" });
  });

  it("offers Approve and Return only to the named reviewer (4B-26)", async () => {
    const inReview = (mayDecide: boolean) =>
      determinations({
        items: [
          determination(
            { status: "in_review", reviewer_id: "user-2" },
            { editable: false, blockers: [], may_decide: mayDecide }
          ),
        ],
      });
    api.fetchDeterminations.mockResolvedValue(inReview(false));
    const first = mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText(/Only they can approve it or send it back/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Return for changes/ })).not.toBeInTheDocument();
    first.unmount();

    api.fetchDeterminations.mockResolvedValue(inReview(true));
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByRole("button", { name: /Approve/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Return for changes/ })).toBeInTheDocument();
  });

  it("when the reviewer loses the authority, nobody is offered Approve and the sender may send it on (4B-26)", async () => {
    const lost = (mayReassign: boolean) =>
      determinations({
        items: [
          determination(
            { status: "in_review", reviewer_id: "user-2", created_by: "user-1", submitted_by: "user-1" },
            { editable: false, blockers: [], may_decide: false, reviewer_eligible: false, may_reassign: mayReassign }
          ),
        ],
      });
    api.fetchDeterminations.mockResolvedValue(lost(false));
    const first = mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText(/no longer holds determination authority/)).toBeInTheDocument();
    expect(screen.getByText(/The person who sent it can send it to another approver/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send to another approver/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/Only they can approve it or send it back/)).not.toBeInTheDocument();
    first.unmount();

    api.fetchDeterminations.mockResolvedValue(lost(true));
    api.reassignDetermination.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    const send = await screen.findByRole("button", { name: /Send to another approver/ });
    expect(send).toBeDisabled();
    const approver = screen.getByLabelText("New approver");
    await waitFor(() => expect(within(approver).getByRole("option", { name: "Vikram Nair" })).toBeInTheDocument());
    expect(within(approver).queryByRole("option", { name: "Asha Rao" })).not.toBeInTheDocument();
    await userEvent.selectOptions(approver, "user-2");
    await userEvent.click(send);
    await waitFor(() => expect(api.reassignDetermination).toHaveBeenCalledWith("c1", "det-1", 2, "user-2"));
  });

  it("shows an approved determination read-only and offers a revision, not an edit", async () => {
    api.fetchDeterminations.mockResolvedValue(
      determinations({
        items: [
          determination(
            {
              status: "approved",
              decided_by: "user-2",
              decided_at: "2026-11-16T00:00:00",
              heads: [{ head_id: "H1", head_title: "Delayed handover", result: "partial", days: 90, findings: "Employer delay for part.", residual_risks: [], information_required: [] }],
            },
            { editable: false, blockers: [] }
          ),
        ],
      })
    );
    api.reviseDetermination.mockResolvedValue(determination());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText("Employer delay for part.")).toBeInTheDocument();
    expect(screen.getByText("90 days")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save finding" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Start a new revision" }));
    await waitFor(() => expect(api.reviseDetermination).toHaveBeenCalledWith("c1", "det-1"));
  });
});

describe("Determination and RFI letters (4B-31)", () => {
  const asked = [
    { item: "Daily site records", deadline: "2026-12-20T00:00:00Z", consequence: "Decided on the records supplied" },
  ];
  const withItems = (over: Partial<DeterminationView["determination"]> = {}, view: Partial<DeterminationView> = {}) =>
    determination(
      {
        heads: [
          { head_id: "H1", head_title: "Delayed handover", result: "partial", days: 10, findings: "Ten days.", residual_risks: [], information_required: asked },
        ],
        ...over,
      },
      { blockers: [], ...view }
    );

  it("works out which letters a determination can start", () => {
    const base = { _id: "d1", origin: "authored", status: "approved" as const, heads: [{ information_required: [1] }] };
    expect(letterOptions(base, [])).toEqual({ determination: true, informationRequest: true });
    expect(letterOptions({ ...base, status: "draft" }, [])).toEqual({ determination: false, informationRequest: true });
    expect(letterOptions({ ...base, heads: [{ information_required: [] }] }, [])).toEqual({ determination: true, informationRequest: false });
    expect(letterOptions(base, [{ supersedes_id: "d1" }])).toEqual({ determination: false, informationRequest: false });
    expect(letterOptions({ ...base, origin: "received", status: "recorded" }, [])).toEqual({ determination: false, informationRequest: false });
    expect(deadlineState("2026-12-01T00:00:00Z", "2026-12-02")).toBe("overdue");
    expect(deadlineState("2026-12-05", "2026-12-01")).toBe("due");
    expect(deadlineState("2026-12-30", "2026-12-01")).toBeNull();
    expect(deadlineState(null, "2026-12-01")).toBeNull();
    expect(
      informationInput([
        { item: " Records ", deadline: "2026-12-20", consequence: " " },
        { item: "  ", deadline: "2026-12-21", consequence: "dropped" },
      ])
    ).toEqual([{ item: "Records", deadline: "2026-12-20T00:00:00Z", consequence: null }]);
  });

  it("edits each item's deadline and saves it, and offers a pre-filled request for information", async () => {
    api.fetchDeterminations.mockResolvedValue(determinations({ items: [withItems()] }));
    api.setHeadFinding.mockResolvedValue(withItems());
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    const deadline = await screen.findByLabelText("Deadline");
    expect(deadline).toHaveValue("2026-12-20");
    await userEvent.clear(deadline);
    await userEvent.type(deadline, "2026-12-15");
    await userEvent.click(screen.getByRole("button", { name: "Add an information item" }));
    await userEvent.type(screen.getByLabelText("Item 2"), "Revised programme");
    await userEvent.click(screen.getByRole("button", { name: "Save finding" }));
    await waitFor(() =>
      expect(api.setHeadFinding).toHaveBeenCalledWith("c1", "det-1", "H1", 2, expect.objectContaining({
        information_required: [
          { item: "Daily site records", deadline: "2026-12-15T00:00:00Z", consequence: "Decided on the records supplied" },
          { item: "Revised programme", deadline: null, consequence: null },
        ],
      }))
    );
    expect(screen.getByRole("link", { name: /Request information/ })).toHaveAttribute(
      "href",
      "/letters/bench/new?determinationId=det-1&purpose=information_request"
    );
    expect(screen.queryByRole("link", { name: /Issue determination letter/ })).not.toBeInTheDocument();
  });

  it("offers the determination letter once approved, shows deadlines, and nothing from a superseded revision", async () => {
    const approved = withItems({ status: "approved" }, { editable: false });
    api.fetchDeterminations.mockResolvedValue(determinations({ items: [approved] }));
    const { unmount } = mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByRole("link", { name: /Issue determination letter/ })).toHaveAttribute(
      "href",
      "/letters/bench/new?determinationId=det-1&purpose=determination"
    );
    expect(screen.getByText(/by 20-12-2026/)).toBeInTheDocument();
    unmount();

    const v2 = withItems({ _id: "det-2", revision: 2, supersedes_id: "det-1" });
    api.fetchDeterminations.mockResolvedValue(determinations({ items: [v2, approved] }));
    mount(<DeterminationsPanel claimId="c1" readOnly={false} />);
    const old = await screen.findByRole("article", { name: "Determination revision 1" });
    expect(within(old).queryByRole("link", { name: /Issue determination letter|Request information/ })).not.toBeInTheDocument();
  });

  it("shows on the Bench the findings and each item's deadline the letter was started from", () => {
    render(
      <DeterminationContext
        today="2026-12-10"
        trigger={{
          type: "information_request",
          id: "det-1",
          label: "Information requested on claim CLM-1 (revision 1)",
          information_items: [
            { head_id: "H1", head_title: "Delayed handover", item: "Handover letters", deadline: "2026-12-05", consequence: null },
            { head_id: "H1", head_title: "Delayed handover", item: "Site records", deadline: "2026-12-12", consequence: "Decided without them" },
          ],
        }}
      />
    );
    expect(screen.getByRole("region", { name: "From the determination" })).toBeInTheDocument();
    expect(screen.getByText("(passed)")).toBeInTheDocument();
    expect(screen.getByText("(within a week)")).toBeInTheDocument();
    expect(screen.getByText("Decided without them")).toBeInTheDocument();
    const { container } = render(<DeterminationContext trigger={{ type: "claim", id: "c", label: "Claim" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Internal analysis", () => {
  const simulations = (): SimulationsResponse => ({
    label: INTERNAL,
    privileged: true,
    claim_id: "c1",
    prepared_for: "contractor",
    can_simulate: ["employer", "engineer"],
    current_board_version: 5,
    items: [
      {
        _id: "s1",
        label: INTERNAL,
        privileged: true,
        simulated_party: "engineer",
        prepared_for: "contractor",
        board_version: 4,
        degraded: true,
        degraded_reason: "Skill not used: No published version",
        created_at: "2026-11-16T00:00:00",
        heads: [
          {
            head_id: "H1",
            head_title: "Delayed handover",
            likely_result: "insufficient_information",
            reasoning: "No response is predicted.",
            likely_challenges: ["Notice is held as weak on the board"],
            information_likely_requested: [],
            produced_by: "record_check",
          },
        ],
      },
    ],
  });
  const strategy = (): StrategyNoteResponse => ({
    label: NOT_FOR_ISSUE,
    privileged: true,
    claim_id: "c1",
    note: {
      _id: "n1",
      label: NOT_FOR_ISSUE,
      strengths: ["H1 event: decided supported"],
      weaknesses: [],
      residual_risks: [],
      notes: "",
      produced_by: "record_check",
      degraded: true,
      degraded_reason: "Skill not used: No published version",
      version: 3,
      updated_at: "2026-11-16T00:00:00",
    },
  });

  it("labels every panel, never calls a records-only simulation a prediction, and saves the note by lines", async () => {
    api.fetchSimulations.mockResolvedValue(simulations());
    api.fetchStrategyNote.mockResolvedValue(strategy());
    api.saveStrategyNote.mockResolvedValue(strategy());
    api.simulateResponse.mockResolvedValue(simulations().items[0]);
    mount(<InternalAnalysisPanel claimId="c1" readOnly={false} />);
    const simulation = (await screen.findByRole("heading", { name: "How the Engineer is likely to respond" })).closest("section")!;
    expect(within(simulation).getByText(INTERNAL)).toBeInTheDocument();
    expect(within(simulation).getByText("No response predicted")).toBeInTheDocument();
    expect(within(simulation).getByText(/The board has changed since/)).toBeInTheDocument();
    const note = screen.getByRole("heading", { name: "Strategy note" }).closest("section")!;
    expect(within(note).getByText(NOT_FOR_ISSUE)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Weaknesses, one per line"), "Notice late{enter}Programme not agreed");
    await userEvent.click(screen.getByRole("button", { name: "Save note" }));
    await waitFor(() =>
      expect(api.saveStrategyNote).toHaveBeenCalledWith("c1", 3, {
        strengths: ["H1 event: decided supported"],
        weaknesses: ["Notice late", "Programme not agreed"],
        residual_risks: [],
        notes: "",
      })
    );
    await userEvent.click(screen.getByRole("button", { name: "Simulate the Employer's response" }));
    await waitFor(() => expect(api.simulateResponse).toHaveBeenCalledWith("c1", "employer"));
  });

  it("shows the server's refusal instead of any content", async () => {
    api.fetchSimulations.mockRejectedValue(new Error("You do not have access to privileged claim analysis."));
    api.fetchStrategyNote.mockRejectedValue(new Error("You do not have access to privileged claim analysis."));
    mount(<InternalAnalysisPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have access");
    expect(screen.queryByRole("heading", { name: "Strategy note" })).not.toBeInTheDocument();
  });
});

describe("Submission", () => {
  it("edits a section by paragraph, marks it edited, and asks before re-composing over an edit (4B-32)", async () => {
    const base = submission({ version: 3 });
    api.fetchSubmission.mockResolvedValue(base);
    api.editSubmissionSection.mockResolvedValue(base);
    api.composeSubmission.mockResolvedValue(base);
    const first = mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const paragraph = screen.getByLabelText("Paragraph 1 of Executive summary");
    await userEvent.clear(paragraph);
    await userEvent.type(paragraph, "In short, the Contractor claims payment of INR 5,00,000.00.");
    await userEvent.click(screen.getByRole("button", { name: "Save section" }));
    await waitFor(() =>
      expect(api.editSubmissionSection).toHaveBeenCalledWith("c1", "executive_summary", 3, [
        "In short, the Contractor claims payment of INR 5,00,000.00.",
      ])
    );
    first.unmount();

    const edited = submission({ version: 4 });
    edited.sections[0].section = { ...edited.sections[0].section, edited_by: "u1", edited_at: "2026-11-17T00:00:00",
      paragraphs: [{ ...edited.sections[0].section.paragraphs[0], edited: true }] };
    api.fetchSubmission.mockResolvedValue(edited);
    api.composeSubmission.mockClear();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText("Edited by hand")).toBeInTheDocument();
    expect(screen.getByText("(edited)")).toBeInTheDocument();
    const recompose = screen.getByRole("button", { name: "Re-compose Executive summary" });
    await userEvent.click(recompose);
    expect(api.composeSubmission).not.toHaveBeenCalled(); // declined: nothing replaced
    await userEvent.click(recompose);
    await waitFor(() =>
      expect(api.composeSubmission).toHaveBeenCalledWith("c1", ["executive_summary"], ["executive_summary"])
    );
    confirm.mockRestore();
  });

  it("offers no editing to a viewer for whom evidence is restricted (4B-32, D-25)", async () => {
    api.fetchSubmission.mockResolvedValue(submission({ restricted_items: 1 }));
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    await screen.findByRole("heading", { name: /Executive summary/ });
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("says how many items are restricted, never which, and that changed evidence needs re-composing (4B-27)", async () => {
    api.fetchSubmission.mockResolvedValue(
      submission({
        restricted_items: 2,
        evidence_changed: 1,
        evidence: [{ number: "E-1", document_id: "", title: "Restricted item" }],
      })
    );
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText(/2 items of this claim's evidence are restricted for you/)).toBeInTheDocument();
    expect(screen.getByText(/1 evidence document has changed since the submission was composed/)).toBeInTheDocument();
  });

  it("lists evidence whose contract is not confirmed beside the submission (4B-23 review)", async () => {
    const note = "Document L-12: does not say which contract it belongs to. Check it belongs to this claim's contract before relying on it.";
    api.fetchSubmission.mockResolvedValue(submission({ evidence_notes: [note] }));
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    const section = (await screen.findByRole("heading", { name: "Evidence whose contract is not confirmed" })).closest("section")!;
    expect(within(section).getByText(note)).toBeInTheDocument();
  });

  it("keeps notes to the reader apart from the document, shows provenance, and blocks export while stale", async () => {
    api.fetchSubmission.mockResolvedValue(submission());
    api.composeSubmission.mockResolvedValue(submission({ stale_sections: 0, can_export: true, cannot_export_reason: null }));
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    const document = await screen.findByRole("article", { name: "Claim submission" });
    expect(within(document).getByText(/claims payment of INR 5,00,000.00/)).toBeInTheDocument();
    expect(within(document).getByText("Record has changed")).toBeInTheDocument();
    expect(within(document).getByText("Quantum build-up: Quantum build-up of H1")).toBeInTheDocument();
    const aside = within(document).getByText("For you, not in the document").closest("aside")!;
    expect(within(aside).getByText(/left out: proof not linked/)).toBeInTheDocument();
    expect(within(document).getByRole("row", { name: /E-1 31-03-2026 PL\/03 Plant log March 2026/ })).toBeInTheDocument();
    expect(screen.getByText(/no longer match the record/)).toBeInTheDocument();
    expect(screen.getByText(/marked “Draft. Not approved for issue.”/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Word" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "PDF" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Re-compose Executive summary" }));
    await waitFor(() => expect(api.composeSubmission).toHaveBeenCalledWith("c1", ["executive_summary"], []));
    await waitFor(() => expect(screen.getByRole("button", { name: "Word" })).toBeEnabled());
  });

  it("says why nothing can be composed yet", async () => {
    api.fetchSubmission.mockResolvedValue(
      submission({ exists: false, sections: [], evidence: [], can_compose: false, cannot_compose_reason: "Lock the board first.", cannot_export_reason: "Compose the submission first." })
    );
    mount(<SubmissionPanel claimId="c1" readOnly={false} />);
    expect(await screen.findByText("Lock the board first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compose the submission" })).toBeDisabled();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
});
