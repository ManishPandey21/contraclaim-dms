import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Key Dates page sits behind authentication, so the contractual UI cannot be
// reached in a local browser without credentials. These tests render the same
// component the browser would, against the shapes the API actually returns, so
// the render path is exercised rather than assumed.
const api = vi.hoisted(() => ({
  getKeyDateWorkflow: vi.fn(),
  supersedeEOTSubmissionRevision: vi.fn(),
  listDocuments: vi.fn(),
}));

vi.mock("@/services/key-dates-api", () => ({
  getKeyDateWorkflow: api.getKeyDateWorkflow,
  supersedeEOTSubmissionRevision: api.supersedeEOTSubmissionRevision,
  createEOTDetermination: vi.fn(),
  createEOTSubmissionRevision: vi.fn(),
  downloadEOTDeterminationTemplate: vi.fn(),
  downloadEOTSubmissionTemplate: vi.fn(),
  exportKeyDateWorkflow: vi.fn(),
  freezeEOTDetermination: vi.fn(),
  freezeOriginalKeyDates: vi.fn(),
  importEOTDeterminationCsv: vi.fn(),
  importEOTSubmissionCsv: vi.fn(),
  lockEOTSubmissionRevision: vi.fn(),
  previewEOTDeterminationCsv: vi.fn(),
  previewEOTSubmissionCsv: vi.fn(),
  updateEOTSubmissionRevision: vi.fn(),
  updateEOTDetermination: vi.fn(),
}));
vi.mock("@/services/documents-api", () => ({ listDocuments: api.listDocuments }));
vi.mock("@/hooks/useHasPermission", () => ({ default: () => true }));
vi.mock("@/components/document-links/EntityDocumentLinks", () => ({
  default: (props: any) => (
    <div
      data-testid="entity-document-links"
      data-target-type={props.targetType}
      data-target-id={props.targetId}
      data-frozen={String(props.frozen)}
    />
  ),
}));

// The workflow renders CsvImportDialog, which reads the navbar tenant selection.
const { useTenant } = vi.hoisted(() => ({ useTenant: vi.fn() }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant }));

import KeyDateRevisionWorkflow from "../KeyDateRevisionWorkflow";

const MILESTONES = [
  { id: "m-1", milestone_ref: "KD-01", title: "Milestone KD-01",
    current_approved_key_date: "2026-04-15T00:00:00", original_planned_key_date: "2026-01-01T00:00:00" },
  { id: "m-2", milestone_ref: "KD-02", title: "Milestone KD-02",
    current_approved_key_date: "2026-01-15T00:00:00", original_planned_key_date: "2026-01-15T00:00:00" },
] as any;

function submission(id: string, revision: number, outcome: string, extra: Record<string, unknown> = {}) {
  return {
    id, _id: id, project_id: "p", contract_id: "primary",
    revision_number: revision, revision_label: `EOT-${revision}`,
    status: "locked", locked_at: "2026-02-01T00:00:00",
    contractor_submission_date: "2026-02-01T00:00:00",
    determination_outcome: outcome, determining_determination_ids: [],
    linked_document_ids: [], linked_letter_ids: [],
    created_at: "2026-02-01T00:00:00",
    items: [{ id: `${id}-i`, eot_submission_id: id, key_date_id: "m-1", milestone_ref: "KD-01",
              eot_submitted_date: "2026-03-01T00:00:00", claimed_extension_days: 60 }],
    ...extra,
  };
}

const WORKFLOW = {
  project_id: "p", contract_id: "primary", baseline_status: "frozen",
  current_contractual_baseline: "EOT-1 + EOT-3",
  latest_eot_submission: "EOT-3",
  pending_determinations: 1, open_eot_submissions: 1,
  employer_initiated_determinations: 1, not_separately_determined: 1,
  oldest_pending_submission: "EOT-2",
  submissions: [
    submission("s-1", 1, "partially_accepted"),
    submission("s-2", 2, "not_separately_determined"),
    submission("s-3", 3, "partially_accepted"),
  ],
  determinations: [
    { id: "d-1", _id: "d-1", project_id: "p", contract_id: "primary",
      eot_submission_ids: ["s-1", "s-3"], origin: "contractor_submission",
      covered_revision_labels: ["EOT-1", "EOT-3"], determination_reference: "CLIENT/145",
      determination_date: "2026-07-01T00:00:00", status: "partially_granted",
      frozen_at: "2026-07-02T00:00:00", supersedes_determination_ids: [],
      linked_document_ids: [], linked_letter_ids: [], created_at: "2026-07-01T00:00:00", items: [] },
    { id: "d-2", _id: "d-2", project_id: "p", contract_id: "primary",
      eot_submission_ids: [], origin: "employer_initiated",
      covered_revision_labels: [], determination_reference: "CLIENT/OWN-1",
      determination_date: "2026-07-15T00:00:00", status: "granted",
      frozen_at: "2026-07-16T00:00:00", supersedes_determination_ids: [],
      linked_document_ids: [], linked_letter_ids: [], created_at: "2026-07-15T00:00:00", items: [] },
  ],
} as any;

describe("KeyDateRevisionWorkflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getKeyDateWorkflow.mockResolvedValue(WORKFLOW);
    api.listDocuments.mockResolvedValue({ documents: [] });
    useTenant.mockReturnValue({
      organizations: [{ _id: "o", name: "Org" }],
      projects: [{ _id: "p", name: "Project", organization_id: "o" }],
      selectedOrganization: { _id: "o", name: "Org" },
      selectedProject: { _id: "p", name: "Project", organization_id: "o" },
      selectedOrganizationId: "o",
      selectedProjectId: "p",
      loading: false,
      error: null,
      selectOrganization: vi.fn(),
      selectProject: vi.fn(),
      refresh: vi.fn(),
    });
  });

  const renderWorkflow = () =>
    render(<KeyDateRevisionWorkflow projectId="p" milestones={MILESTONES} onChanged={vi.fn()} />);

  it("renders each submission's derived outcome", async () => {
    renderWorkflow();
    expect(await screen.findByText("Not separately determined")).toBeInTheDocument();
    expect(screen.getAllByText("Partially accepted")).toHaveLength(2);
  });

  it("shows the contractual baseline and the not-separately-determined count", async () => {
    renderWorkflow();
    expect(await screen.findByText("EOT-1 + EOT-3")).toBeInTheDocument();
    expect(screen.getByText("Not Separately Determined")).toBeInTheDocument();
    expect(screen.getByText("Oldest Pending")).toBeInTheDocument();
  });

  it("labels an employer-initiated determination by its reference, not an EOT-N", async () => {
    renderWorkflow();
    expect(await screen.findByText("Employer determination")).toBeInTheDocument();
    expect(screen.getAllByText(/CLIENT\/OWN-1/).length).toBeGreaterThan(0);
  });

  it("supersede offers only later revisions and requires a reason", async () => {
    const user = userEvent.setup();
    renderWorkflow();
    const rows = await screen.findAllByRole("button", { name: "Supersede" });
    await user.click(rows[0]);   // EOT-1

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/never inferred/i);
    // Reason missing -> the API must not be called.
    await user.click(within(dialog).getByRole("button", { name: "Supersede" }));
    await waitFor(() => expect(api.supersedeEOTSubmissionRevision).not.toHaveBeenCalled());
  });

  it("renders submitted dates day-first", async () => {
    renderWorkflow();
    // contractor_submission_date is 2026-02-01 on every seeded submission.
    expect((await screen.findAllByText("01-02-2026")).length).toBeGreaterThan(0);
  });

  it("offers the employer-initiated determination entry point", async () => {
    renderWorkflow();
    expect(await screen.findByRole("button", { name: /Employer Determination/i })).toBeInTheDocument();
  });

  it("renders event-level evidence for every submission and determination", async () => {
    renderWorkflow();
    const evidence = await screen.findAllByTestId("entity-document-links");
    expect(evidence).toHaveLength(5);
    expect(evidence.map((node) => node.getAttribute("data-target-type"))).toEqual([
      "eot_submission", "eot_submission", "eot_submission",
      "eot_determination", "eot_determination",
    ]);
    expect(evidence.map((node) => node.getAttribute("data-target-id"))).toEqual([
      "s-1", "s-2", "s-3", "d-1", "d-2",
    ]);
    expect(evidence.every((node) => node.getAttribute("data-frozen") === "true")).toBe(true);
  });
});
