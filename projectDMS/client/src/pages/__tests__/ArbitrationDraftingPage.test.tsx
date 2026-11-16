import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ArbitrationDraftingPage from "../ArbitrationDraftingPage";

const getDraftMock = vi.fn();
const listVersionsMock = vi.fn();
const getVersionMock = vi.fn();
const approveMock = vi.fn();
const returnMock = vi.fn();
const saveVersionMock = vi.fn();
const searchEvidenceMock = vi.fn();
const addReferencesMock = vi.fn();
const removeReferenceMock = vi.fn();
const generateMock = vi.fn();
const updateDraftMock = vi.fn();
const citationsMock = vi.fn();

const gate = (allowed: boolean, reasons: { code: string; message: string; anchor?: string | null }[] = []) => ({
  allowed,
  reasons,
  checked_on_action: ["Case readiness approval", "Plan approval", "Legal review approval"],
});
const CITATIONS = {
  draft_id: "draft-1",
  version: 2,
  scratch: true,
  counts: { verified: 0, unverified: 0, unresolved: 0, cited: 0, ledger: 0 },
  items: [],
  approval_blockers: [],
  pleaded_head_gaps: [],
  missing_evidence: [],
  can_be_approved: false,
  cannot_be_approved_reason: "This is a scratch draft with no case workspace.",
  action_gates: {
    approve: gate(false, [
      { code: "scratch_draft", message: "A scratch draft has no case and cannot be approved. Attach it to a case.", anchor: "draft-case" },
    ]),
    filing_export: gate(false, [{ code: "not_approved", message: "The draft must be approved and locked before it is exported for filing.", anchor: "draft-approval" }]),
  },
};

vi.mock("@/services/arbitration-drafting-api", () => ({
  listArbitrationDrafts: vi.fn().mockResolvedValue([]),
  createArbitrationDraft: vi.fn(),
  getArbitrationDraft: (...args: unknown[]) => getDraftMock(...args),
  getArbitrationDraftCitations: (...args: unknown[]) => citationsMock(...args),
  generateArbitrationDraft: (...args: unknown[]) => generateMock(...args),
  prepareArbitrationDraftFromCase: vi.fn(),
  regenerateArbitrationSection: vi.fn(),
  importDefenceParagraphs: vi.fn(),
  importSocParagraphs: vi.fn(),
  exportArbitrationDraft: vi.fn(),
  approveArbitrationDraft: (...args: unknown[]) => approveMock(...args),
  returnArbitrationDraftForRevision: (...args: unknown[]) => returnMock(...args),
  listArbitrationDraftVersions: (...args: unknown[]) => listVersionsMock(...args),
  getArbitrationDraftVersion: (...args: unknown[]) => getVersionMock(...args),
  saveArbitrationDraftVersion: (...args: unknown[]) => saveVersionMock(...args),
  searchArbitrationEvidence: (...args: unknown[]) => searchEvidenceMock(...args),
  addArbitrationDraftReferences: (...args: unknown[]) => addReferencesMock(...args),
  removeArbitrationDraftReference: (...args: unknown[]) => removeReferenceMock(...args),
  updateArbitrationDraft: (...args: unknown[]) => updateDraftMock(...args),
}));

vi.mock("@/services/arbitration-cases-api", () => ({
  listArbitrationCases: vi.fn().mockResolvedValue([]),
}));

vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([]) },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const VERSION = {
  _id: "version-2",
  draft_id: "draft-1",
  version: 2,
  full_markdown: "# EOT Statement of Claim\n\nRelies on [S1: Delay notice].",
  sections: [{ key: "introduction", heading: "Introduction", body: "..." }],
  structured_output: { validation_warnings: [], approval_blockers: [] },
  source_ledger: [
    { source_key: "S1", source_id: "doc-1", citation: "Delay notice" },
    { source_key: "S2", source_id: "claim-1", citation: "CL-001", source_origin: "claim_register" },
  ],
  missing_evidence: [],
  warnings: [],
  validation_status: "passed",
};

const DRAFT = {
  _id: "draft-1",
  project_id: "project-1",
  draft_type: "statement_of_claim",
  party_role: "claimant",
  dispute_type: "eot_delay",
  title: "EOT Statement of Claim",
  status: "draft",
  is_locked: false,
  current_version: 2,
  latest_version: VERSION,
  selected_references: [
    { _id: "ref-1", source_type: "document", source_id: "doc-1", label: "Delay notice", citation: "LTR-001" },
  ],
};

const renderDraftView = () =>
  render(
    <MemoryRouter initialEntries={["/arbitration/drafts/draft-1"]}>
      <Routes>
        <Route path="/arbitration/drafts/:draftId" element={<ArbitrationDraftingPage />} />
      </Routes>
    </MemoryRouter>,
  );

describe("ArbitrationDraftingPage (draft view)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getDraftMock.mockResolvedValue(DRAFT);
    listVersionsMock.mockResolvedValue([
      VERSION,
      { ...VERSION, _id: "version-1", version: 1, validation_status: "needs_review" },
    ]);
    approveMock.mockResolvedValue({ ...DRAFT, status: "approved", is_locked: true });
    saveVersionMock.mockResolvedValue({ ...VERSION, version: 3 });
    searchEvidenceMock.mockResolvedValue([
      { source_type: "document", source_id: "doc-9", label: "Hindrance register", citation: "REG-9" },
    ]);
    addReferencesMock.mockResolvedValue(DRAFT);
    citationsMock.mockResolvedValue(CITATIONS);
  });

  it("shows approve control and version history; approving calls the API", async () => {
    getDraftMock.mockResolvedValue({ ...DRAFT, case_id: "case-1" });
    citationsMock.mockResolvedValue({
      ...CITATIONS, scratch: false, can_be_approved: true,
      action_gates: { ...CITATIONS.action_gates, approve: gate(true) },
    });
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalledWith("draft-1"));

    expect(listVersionsMock).toHaveBeenCalledWith("draft-1");
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();

    const approve = screen.getByRole("button", { name: /Approve/ });
    await waitFor(() => expect(approve).toBeEnabled());
    // What is still checked on approval is said, not hidden.
    expect(screen.getByText(/Approve is available\. Checked when you approve: Case readiness approval/)).toBeInTheDocument();
    await userEvent.click(approve);
    await waitFor(() => expect(approveMock).toHaveBeenCalledWith("draft-1"));
  });

  it("disables Approve with the server's reasons, each linked to where it is resolved (B4)", async () => {
    getDraftMock.mockResolvedValue({ ...DRAFT, case_id: "case-1" });
    citationsMock.mockResolvedValue({
      ...CITATIONS,
      scratch: false,
      action_gates: {
        ...CITATIONS.action_gates,
        approve: gate(false, [
          { code: "approval_blockers", message: "1 drafting blocker(s) must be resolved in a new version.", anchor: "draft-citations" },
          { code: "self_approval", message: "You wrote or last changed this draft, so someone else must approve it.", anchor: null },
        ]),
      },
    });
    renderDraftView();
    const status = await screen.findByText("Approve is not available yet:");
    const approve = screen.getByRole("button", { name: /Approve/ });
    expect(approve).toBeDisabled();
    expect(approve).toHaveAttribute("aria-describedby", "draft-action-gate");
    const link = screen.getByRole("link", { name: "1 drafting blocker(s) must be resolved in a new version." });
    expect(link).toHaveAttribute("href", "#draft-citations");
    expect(document.getElementById("draft-citations")).not.toBeNull();
    expect(status.closest("[role=status]")).toHaveTextContent("someone else must approve it");
    await userEvent.click(approve);
    expect(approveMock).not.toHaveBeenCalled();
  });

  it("keeps Approve disabled until the gate is known, and disables it for a scratch draft", async () => {
    citationsMock.mockReturnValue(new Promise(() => undefined));
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: /Approve/ })).toBeDisabled();
  });

  it("disables filing export with its reasons and enables it when the gate allows", async () => {
    const locked = { ...DRAFT, case_id: "case-1", status: "approved", is_locked: true };
    getDraftMock.mockResolvedValue(locked);
    citationsMock.mockResolvedValue({
      ...CITATIONS,
      scratch: false,
      action_gates: {
        approve: gate(true),
        filing_export: gate(false, [
          { code: "self_approval", message: "You wrote or last changed this draft, so someone else must export it.", anchor: null },
        ]),
      },
    });
    const { unmount } = renderDraftView();
    expect(await screen.findByText("Export for filing is not available yet:")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Filing DOCX/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Filing PDF/ })).toBeDisabled();
    unmount();

    citationsMock.mockResolvedValue({
      ...CITATIONS, scratch: false, action_gates: { approve: gate(true), filing_export: gate(true) },
    });
    renderDraftView();
    await waitFor(() => expect(screen.getByRole("button", { name: /Filing DOCX/ })).toBeEnabled());
    expect(screen.getByRole("button", { name: /Filing PDF/ })).toBeEnabled();
  });

  it("shows the scratch-draft banner for drafts without a case link and excludes register sources", async () => {
    updateDraftMock.mockResolvedValue({ ...DRAFT, excluded_register_ids: ["claim-1"] });
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());

    // DRAFT has no case_id -> it is named as a scratch draft, with what that means.
    expect(screen.getByText(/Scratch draft\./)).toBeInTheDocument();
    expect(screen.getAllByText(/cannot be\s+approved or exported for filing/).length).toBeGreaterThan(0);

    // The claim-register ledger row offers per-row exclusion.
    await userEvent.click(screen.getByRole("button", { name: "Exclude register source CL-001" }));
    await waitFor(() =>
      expect(updateDraftMock).toHaveBeenCalledWith("draft-1", { excluded_register_ids: ["claim-1"] }),
    );
    expect(await screen.findByText(/1 register source\(s\) excluded/)).toBeInTheDocument();
  });

  it("locked draft disables generation and offers return-for-revision", async () => {
    getDraftMock.mockResolvedValue({ ...DRAFT, status: "approved", is_locked: true });
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());

    expect(screen.getByText(/Locked \(approved\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Return for Revision/ })).toBeInTheDocument();
  });

  it("edit mode saves the edited markdown as a new manual version", async () => {
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: /Edit/ }));
    const editor = screen.getByRole("textbox", { name: /Draft markdown editor/ });
    await userEvent.clear(editor);
    await userEvent.type(editor, "Edited pleading body.");
    await userEvent.click(screen.getByRole("button", { name: /Save as New Version/ }));

    await waitFor(() => expect(saveVersionMock).toHaveBeenCalledWith("draft-1", "Edited pleading body."));
  });

  it("exposes a draft-mode selector and threads the mode into generation", async () => {
    generateMock.mockResolvedValue(DRAFT);
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());

    // The AI-prose vs source-grounded selector is present (Radix portal options
    // don't open under jsdom, so we assert wiring via the default generate call).
    expect(screen.getByRole("combobox", { name: /Draft mode/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Generate/ }));
    await waitFor(() => expect(generateMock).toHaveBeenCalledWith("draft-1", { draft_mode: "deterministic" }));
  });

  it("evidence search links a found source to the draft", async () => {
    renderDraftView();
    await waitFor(() => expect(getDraftMock).toHaveBeenCalled());

    await userEvent.type(screen.getByPlaceholderText(/site access/), "hindrance");
    await userEvent.click(screen.getByRole("button", { name: "Search evidence" }));
    await waitFor(() => expect(searchEvidenceMock).toHaveBeenCalledWith("draft-1", "hindrance"));

    expect(screen.getByText("Hindrance register")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add Hindrance register" }));
    await waitFor(() =>
      expect(addReferencesMock).toHaveBeenCalledWith("draft-1", [
        expect.objectContaining({ source_id: "doc-9", label: "Hindrance register" }),
      ]),
    );
  });
});
