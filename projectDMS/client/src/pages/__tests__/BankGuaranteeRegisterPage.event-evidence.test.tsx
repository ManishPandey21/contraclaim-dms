import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BankGuaranteeRegisterPage from "@/pages/BankGuaranteeRegisterPage";

const bgApi = vi.hoisted(() => ({
  getBGs: vi.fn(), getBGSummary: vi.fn(), getBGAlerts: vi.fn(),
  getBGEvents: vi.fn(), createBG: vi.fn(), updateBG: vi.fn(),
  extendBG: vi.fn(), releaseBG: vi.fn(), transitionBGStatus: vi.fn(), exportBGs: vi.fn(),
  downloadBGImportTemplate: vi.fn(), previewBGsCsv: vi.fn(), importBGsCsv: vi.fn(),
}));
const relationshipApi = vi.hoisted(() => ({
  listEntityDocumentLinks: vi.fn(), searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(), removeDocumentLink: vi.fn(),
}));
const permission = vi.hoisted(() => ({ can: vi.fn(() => true) }));

vi.mock("@/services/bank-guarantees-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/bank-guarantees-api")>()),
  ...bgApi,
}));
vi.mock("@/services/document-relationships-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/document-relationships-api")>()),
  ...relationshipApi,
}));
vi.mock("@/hooks/useHasPermission", () => ({ default: (name: string) => permission.can(name) }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ _id: "project-1", name: "Metro" }]) },
}));
vi.mock("@/services/contract-master-api", () => ({
  getContractMasterForProject: vi.fn().mockResolvedValue(null),
}));
vi.mock("@/components/registers/CsvImportDialog", () => ({ default: () => null }));

const bg = {
  id: "bg-1", bg_type: "performance", bg_number: "BG-001", currency: "INR",
  bg_status: "valid", project_id: "project-1", organization_id: "org-1",
  linked_document_ids: [], current_revision: 1,
};

describe("Bank Guarantee event evidence workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permission.can.mockReturnValue(true);
    bgApi.getBGs.mockResolvedValue([bg]);
    bgApi.getBGSummary.mockResolvedValue({
      total: 1, total_bg_amount: 0, valid: 1, extension_required: 0,
      expiring_45: 0, expiring_30: 0, expired: 0, released: 0,
    });
    bgApi.getBGAlerts.mockResolvedValue([]);
    bgApi.getBGEvents.mockResolvedValue([
      {
        id: "event-extension", bank_guarantee_id: "bg-1", event_type: "extension",
        sequence: 2, revision_number: 1, event_date: "2026-08-20T00:00:00Z", reference: "EXT/02",
        organization_id: "org-1", project_id: "project-1",
      },
    ]);
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      { _id: "link-1", document_id: "doc-1", relationship_role: "extension", _revision: 1,
        document: { _id: "doc-1", filename: "Extension.pdf" } },
    ]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([
      { _id: "doc-2", filename: "Support.pdf" },
    ]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([
      { _id: "link-2", document_id: "doc-2", relationship_role: "supporting_document", _revision: 1,
        document: { _id: "doc-2", filename: "Support.pdf" } },
    ]);
    bgApi.releaseBG.mockResolvedValue({ ...bg, bg_status: "released" });
    bgApi.transitionBGStatus.mockResolvedValue({ ...bg, bg_status: "submitted" });
  });

  it("renders evidence under the actual event and reuses the common link workflow", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Evidence for BG-001" }));
    expect(await screen.findByText("Extension 1")).toBeInTheDocument();
    expect(screen.getByText(/EXT\/02/)).toBeInTheDocument();
    expect(await screen.findByText("Extension.pdf")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Search Documents"), "support");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.selectOptions(screen.getByLabelText("Relationship role"), "supporting_document");
    await user.click(await screen.findByRole("button", { name: "Link Support.pdf" }));

    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "bank_guarantee_event", "event-extension",
      [{ document_id: "doc-2", relationship_role: "supporting_document" }],
    ));
  });

  it("opens the event evidence workflow from a reverse-link deep link", async () => {
    render(
      <MemoryRouter initialEntries={["/bank-guarantees?bg_id=bg-1&event_id=event-extension"]}>
        <BankGuaranteeRegisterPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Extension 1")).toBeInTheDocument();
    expect(bgApi.getBGEvents).toHaveBeenCalledWith("bg-1");
    expect(await screen.findByText("Extension.pdf")).toBeInTheDocument();
  });

  it("does not render a stale event response under a newly selected BG", async () => {
    const secondBg = { ...bg, id: "bg-2", bg_number: "BG-002" };
    bgApi.getBGs.mockResolvedValue([bg, secondBg]);
    let resolveFirst!: (events: any[]) => void;
    let resolveSecond!: (events: any[]) => void;
    bgApi.getBGEvents
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSecond = resolve; }));
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Evidence for BG-001" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Evidence for BG-002" }));
    resolveSecond([
      {
        id: "event-bg-2", bank_guarantee_id: "bg-2", event_type: "release",
        sequence: 2, organization_id: "org-1", project_id: "project-1",
      },
    ]);
    expect(await screen.findByText("Release 2")).toBeInTheDocument();

    await act(async () => {
      resolveFirst([
        {
          id: "event-bg-1", bank_guarantee_id: "bg-1", event_type: "extension",
          sequence: 9, organization_id: "org-1", project_id: "project-1",
        },
      ]);
    });
    expect(screen.queryByText("Extension 9")).not.toBeInTheDocument();
    expect(screen.getByText("Evidence for BG-002")).toBeInTheDocument();
  });

  it("offers an event-history retry without leaving the selected BG", async () => {
    bgApi.getBGEvents
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce([
        {
          id: "event-extension", bank_guarantee_id: "bg-1", event_type: "extension",
          sequence: 2, revision_number: 1, organization_id: "org-1", project_id: "project-1",
        },
      ]);
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Evidence for BG-001" }));
    await user.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Extension 1")).toBeInTheDocument();
    expect(bgApi.getBGEvents).toHaveBeenCalledTimes(2);
  });

  it("collects and sends release date, reference, and remarks", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByTitle("Release"));
    await user.type(screen.getByLabelText("Release date"), "2026-08-21");
    await user.type(screen.getByLabelText("Release letter reference"), "REL/09");
    await user.type(screen.getByLabelText("Release remarks"), "Returned to bank");
    await user.click(screen.getByRole("button", { name: "Release BG" }));

    await waitFor(() => expect(bgApi.releaseBG).toHaveBeenCalledWith("bg-1", {
      release_date: expect.stringContaining("2026-08-21"),
      release_letter_reference: "REL/09",
      remarks: "Returned to bank",
    }));
  });

  it("uses the explicit lifecycle command for a draft submission", async () => {
    bgApi.getBGs.mockResolvedValue([{ ...bg, bg_status: "draft" }]);
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Submit BG" }));

    await waitFor(() => expect(bgApi.transitionBGStatus).toHaveBeenCalledWith(
      "bg-1", { target_status: "submitted" },
    ));
    expect(bgApi.updateBG).not.toHaveBeenCalled();
  });

  it("creates only draft records so later states require explicit lifecycle commands", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Add BG" }));
    const statusLabel = screen.getAllByText("Status").find((node) => node.tagName === "LABEL");
    expect(statusLabel).toBeDefined();
    const statusTrigger = statusLabel.parentElement?.querySelector<HTMLElement>("[role=combobox]");
    expect(statusTrigger).not.toBeNull();
    expect(statusTrigger).toHaveTextContent("Draft");
    expect(statusTrigger).toBeDisabled();
  });

  it("keeps event evidence view-only and hides lifecycle controls without permission", async () => {
    permission.can.mockReturnValue(false);
    const user = userEvent.setup();
    render(<MemoryRouter><BankGuaranteeRegisterPage /></MemoryRouter>);

    const evidence = await screen.findByRole("button", { name: "Evidence for BG-001" });
    expect(screen.queryByTitle("Edit")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Extend")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Release")).not.toBeInTheDocument();
    await user.click(evidence);
    expect(await screen.findByText(/view-only access to document links/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Search Documents")).not.toBeInTheDocument();
  });
});
