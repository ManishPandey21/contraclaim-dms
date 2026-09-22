import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LinkedRecordsPanel from "@/components/document-viewer/LinkedRecordsPanel";

const relationshipApi = vi.hoisted(() => ({
  listDocumentEntityLinks: vi.fn(), listDocumentLinkTargets: vi.fn(), batchLinkDocuments: vi.fn(),
  listDocumentLinkTargetTypes: vi.fn(),
}));
const toastApi = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("@/services/document-relationships-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/document-relationships-api")>()),
  ...relationshipApi,
}));
vi.mock("sonner", () => ({ toast: toastApi }));

const DOC = "65f0c0ffee0000000000aa01";

/** DocumentRelationshipView rows exactly as `/documents/{id}/entity-links` returns them. */
function reverseLink(target_type: string, target_id: string, target_label: string, target_route: string, relationship_role = "correspondence") {
  return {
    _id: `link-${target_type}-${target_id}`, organization_id: "org-1", project_id: "project-1",
    target_type, target_id, document_id: DOC, relationship_role, source: "user", _revision: 1,
    removed_at: null, frozen_at: null, document: { _id: DOC, filename: "ENG-VO-014.pdf" },
    target_label, target_route,
  };
}

/** Rows exactly as `/documents/{id}/link-targets` returns them. */
const variationTargets = [
  { target_type: "variation", target_id: "var-1", label: "VO-001", route: "/variations?variation_id=var-1",
    allowed_roles: ["correspondence", "supporting_document", "variation_approval", "variation_submission"],
    frozen: false, parent_type: null, parent_id: null },
  { target_type: "variation", target_id: "var-2", label: "VO-002", route: "/variations?variation_id=var-2",
    allowed_roles: ["correspondence", "supporting_document", "variation_approval", "variation_submission"],
    frozen: false, parent_type: null, parent_id: null },
];
const claimTargets = [
  { target_type: "claim", target_id: "claim-1", label: "CLM-001", route: "/claims/claim-1",
    allowed_roles: ["claim_submission", "correspondence", "determination", "notice"],
    frozen: false, parent_type: null, parent_id: null },
];

const hindranceTargets = [
  { target_type: "delay_event", target_id: "hin-1", label: "HIN-0001", route: "/hindrances/hin-1",
    allowed_roles: ["correspondence", "instruction", "notice", "photograph", "programme_record", "site_record", "supporting_document"],
    frozen: false, parent_type: null, parent_id: null },
];

/** Every type the server's `link_to_record` adapters offer (`/link-target-types` for an admin). */
const ALL_TYPES = [
  "claim", "ipc_bill", "insurance", "bank_guarantee_event", "key_date_achievement", "eot_submission",
  "eot_determination", "variation", "delay_event", "programme_milestone", "chronology_event",
];

const programmeTargets = [
  { target_type: "programme_milestone", target_id: "pm-1", label: "PM-110 · Pier P4 piling", route: "/programme-milestones/pm-1",
    allowed_roles: ["correspondence", "programme_record", "progress_evidence", "supporting_document"],
    frozen: false, parent_type: null, parent_id: null },
];
const chronologyTargets = [
  { target_type: "chronology_event", target_id: "ev-1", label: "2026-02-10 · Engineer instruction",
    route: "/chronology/chr-1?event_id=ev-1", allowed_roles: ["correspondence", "supporting_document"],
    frozen: false, parent_type: "matter_chronology", parent_id: "chr-1" },
];

const renderPanel = () => render(<MemoryRouter><LinkedRecordsPanel documentId={DOC} /></MemoryRouter>);

describe("LinkedRecordsPanel reverse lookup and Link to Record", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    relationshipApi.listDocumentEntityLinks.mockResolvedValue([]);
    relationshipApi.listDocumentLinkTargetTypes.mockResolvedValue(ALL_TYPES);
    relationshipApi.listDocumentLinkTargets.mockImplementation(async (_doc: string, params: { target_type: string }) =>
      params.target_type === "claim" ? claimTargets : params.target_type === "variation" ? variationTargets : []);
    relationshipApi.batchLinkDocuments.mockResolvedValue([reverseLink("variation", "var-1", "VO-001", "/variations?variation_id=var-1")]);
  });

  it("navigates every verified target through a deep link its page consumes", async () => {
    relationshipApi.listDocumentEntityLinks.mockResolvedValue([
      reverseLink("variation", "var-1", "VO-001", "/variations?variation_id=var-1"),
      reverseLink("insurance", "ins-1", "POL-7", "/insurance?insurance_id=ins-1", "policy"),
      reverseLink("key_date_achievement", "kd-1:ach", "KD-01 · Achievement", "/key-dates/kd-1?achievement_id=kd-1:ach"),
      reverseLink("eot_submission", "sub-1", "Rev A · Contractor Submission", "/key-dates?project_id=project-1&submission_id=sub-1"),
      reverseLink("eot_determination", "det-1", "DET-1 · Determination", "/key-dates?project_id=project-1&determination_id=det-1"),
      reverseLink("contract_document", "cd-1", "particular_conditions", `/contracts/viewer/${DOC}`, "supporting_document"),
      reverseLink("programme_milestone", "pm-1", "PM-110 · Pier P4 piling", "/programme-milestones/pm-1", "progress_evidence"),
      reverseLink("chronology_event", "ev-1", "2026-02-10 · Engineer instruction", "/chronology/chr-1?event_id=ev-1", "source_document"),
    ]);
    renderPanel();
    const rows = await screen.findAllByTestId("linked-record");
    expect(rows.map((row) => row.getAttribute("href"))).toEqual([
      "/variations?variation_id=var-1",
      "/insurance?insurance_id=ins-1",
      "/key-dates/kd-1?achievement_id=kd-1:ach",
      "/key-dates?project_id=project-1&submission_id=sub-1",
      "/key-dates?project_id=project-1&determination_id=det-1",
      `/contracts/viewer/${DOC}`,
      "/programme-milestones/pm-1",
      "/chronology/chr-1?event_id=ev-1",
    ]);
    expect(rows[0]).toHaveTextContent("Variation · Correspondence");
    expect(rows[6]).toHaveTextContent("Programme milestone · Progress evidence");
    expect(rows[7]).toHaveTextContent("Chronology event · Source document");
    expect(rows[1]).toHaveTextContent("Insurance · Policy");
  });

  it("links one letter to several records from the Document side", async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText("No linked records.");
    await user.click(screen.getByRole("button", { name: /Link to Record/ }));

    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("radio", { name: "VO-001" }));
    expect(within(dialog).getByLabelText("Relationship role")).toHaveValue("correspondence");
    await user.click(within(dialog).getByRole("button", { name: "Link" }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "variation", "var-1", [{ document_id: DOC, relationship_role: "correspondence" }],
    ));
    expect(relationshipApi.listDocumentLinkTargets).toHaveBeenCalledWith(DOC, { target_type: "variation" });

    // second record, different register and role, same letter
    await user.selectOptions(within(dialog).getByLabelText("Register"), "claim");
    await user.click(await within(dialog).findByRole("radio", { name: "CLM-001" }));
    await user.selectOptions(within(dialog).getByLabelText("Relationship role"), "notice");
    await user.click(within(dialog).getByRole("button", { name: "Link" }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenLastCalledWith(
      "claim", "claim-1", [{ document_id: DOC, relationship_role: "notice" }],
    ));
    // the reverse list reloads after each link
    await waitFor(() => expect(relationshipApi.listDocumentEntityLinks).toHaveBeenCalledTimes(3));
  });

  it("offers only the verified register types", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const options = within(await screen.findByRole("dialog")).getAllByRole("option").map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual([
      "variation", "delay_event", "programme_milestone", "chronology_event", "claim", "ipc_bill", "insurance",
      "bank_guarantee_event", "key_date_achievement", "eot_submission", "eot_determination",
    ]);
  });

  it("offers only the register types the caller may link to", async () => {
    relationshipApi.listDocumentLinkTargetTypes.mockResolvedValue(["claim", "chronology_event"]);
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const options = within(await screen.findByRole("dialog")).getAllByRole("option").map((option) => (option as HTMLOptionElement).value);
    expect(options).toEqual(["chronology_event", "claim"]);
    // The first offered type is the one queried; nothing is asked for the others.
    await waitFor(() => expect(relationshipApi.listDocumentLinkTargets).toHaveBeenCalledWith(DOC, { target_type: "chronology_event" }));
    expect(relationshipApi.listDocumentLinkTargets).not.toHaveBeenCalledWith(DOC, expect.objectContaining({ target_type: "variation" }));
  });

  it("hides Link to Record from a viewer who can link nothing, without reading any record", async () => {
    relationshipApi.listDocumentLinkTargetTypes.mockResolvedValue([]);
    renderPanel();
    await screen.findByText("No linked records.");
    await waitFor(() => expect(relationshipApi.listDocumentLinkTargetTypes).toHaveBeenCalledWith(DOC));
    expect(screen.queryByRole("button", { name: /Link to Record/ })).not.toBeInTheDocument();
    expect(relationshipApi.listDocumentLinkTargets).not.toHaveBeenCalled();
  });

  it("fails closed when the linkable types cannot be determined", async () => {
    relationshipApi.listDocumentLinkTargetTypes.mockRejectedValue(new Error("offline"));
    renderPanel();
    await screen.findByText("No linked records.");
    await waitFor(() => expect(relationshipApi.listDocumentLinkTargetTypes).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /Link to Record/ })).not.toBeInTheDocument();
  });

  it("links one letter to a Programme milestone and a Chronology event (CL-3B)", async () => {
    relationshipApi.listDocumentLinkTargets.mockImplementation(async (_doc: string, params: { target_type: string }) =>
      params.target_type === "programme_milestone" ? programmeTargets
        : params.target_type === "chronology_event" ? chronologyTargets : []);
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const dialog = await screen.findByRole("dialog");

    await user.selectOptions(within(dialog).getByLabelText("Register"), "programme_milestone");
    await user.click(await within(dialog).findByRole("radio", { name: "PM-110 · Pier P4 piling" }));
    const programmeRoles = Array.from((within(dialog).getByLabelText("Relationship role") as HTMLSelectElement).options).map((o) => o.value);
    expect(programmeRoles).toEqual(["correspondence", "programme_record", "progress_evidence", "supporting_document"]);
    await user.click(within(dialog).getByRole("button", { name: /^Link$/ }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "programme_milestone", "pm-1", [{ document_id: DOC, relationship_role: "correspondence" }],
    ));

    await user.selectOptions(within(dialog).getByLabelText("Register"), "chronology_event");
    await user.click(await within(dialog).findByRole("radio", { name: "2026-02-10 · Engineer instruction" }));
    // The source role is provenance: never offered as a linkable role.
    const roleOptions = Array.from((within(dialog).getByLabelText("Relationship role") as HTMLSelectElement).options).map((o) => o.value);
    expect(roleOptions).toEqual(["correspondence", "supporting_document"]);
    await user.click(within(dialog).getByRole("button", { name: /^Link$/ }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenLastCalledWith(
      "chronology_event", "ev-1", [{ document_id: DOC, relationship_role: "correspondence" }],
    ));
  });

  it("marks an existing link and refuses to relink it", async () => {
    relationshipApi.listDocumentEntityLinks.mockResolvedValue([
      reverseLink("variation", "var-1", "VO-001", "/variations?variation_id=var-1"),
    ]);
    const user = userEvent.setup();
    renderPanel();
    await screen.findByTestId("linked-record");
    await user.click(screen.getByRole("button", { name: /Link to Record/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("radio", { name: "VO-001" }));
    expect(within(dialog).getByText("Already linked")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Link" })).toBeDisabled();
  });

  it("shows the server refusal and an empty state", async () => {
    relationshipApi.batchLinkDocuments.mockRejectedValueOnce({
      response: { status: 422, data: { detail: "Only incoming or outgoing correspondence can be linked as correspondence; use supporting_document for other Documents" } },
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(await within(dialog).findByRole("radio", { name: "VO-001" }));
    await user.click(within(dialog).getByRole("button", { name: "Link" }));
    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith(expect.stringMatching(/use supporting_document/)));

    await user.selectOptions(within(dialog).getByLabelText("Register"), "insurance");
    expect(await within(dialog).findByText("No records you can link in this project.")).toBeInTheDocument();
  });

  it("links a letter to a Hindrance / Constraint Register entry (CL-3A)", async () => {
    relationshipApi.listDocumentLinkTargets.mockImplementation(async (_doc: string, params: { target_type: string }) =>
      params.target_type === "delay_event" ? hindranceTargets : []);
    relationshipApi.batchLinkDocuments.mockResolvedValue([
      reverseLink("delay_event", "hin-1", "HIN-0001", "/hindrances/hin-1"),
    ]);
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const dialog = await screen.findByRole("dialog");
    await user.selectOptions(within(dialog).getByLabelText("Register"), "delay_event");
    await user.click(await within(dialog).findByRole("radio", { name: "HIN-0001" }));
    expect(within(dialog).getByLabelText("Relationship role")).toHaveValue("correspondence");
    await user.click(within(dialog).getByRole("button", { name: /^Link$/ }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "delay_event", "hin-1", [{ document_id: DOC, relationship_role: "correspondence" }],
    ));
    expect(relationshipApi.listDocumentLinkTargets).toHaveBeenCalledWith(DOC, expect.objectContaining({ target_type: "delay_event" }));
  });

  it("explains a Link-to-Record refusal by the selected project", async () => {
    relationshipApi.listDocumentLinkTargets.mockRejectedValue({
      response: { status: 400, data: { detail: { code: "selection_required", message: "Select a project" } } },
    });
    const user = userEvent.setup();
    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Link to Record/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Select a project in the navbar to link this register.");
  });
});
