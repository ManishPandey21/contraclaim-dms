import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import VariationRegisterPage from "@/pages/VariationRegisterPage";

const relationshipApi = vi.hoisted(() => ({
  listEntityDocumentLinks: vi.fn(), searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(), removeDocumentLink: vi.fn(),
}));
const variationApi = vi.hoisted(() => ({
  getVariations: vi.fn(), getVariation: vi.fn(), getVariationSummary: vi.fn(),
  createVariation: vi.fn(), updateVariation: vi.fn(), deleteVariation: vi.fn(), exportVariations: vi.fn(),
}));
const rbacApi = vi.hoisted(() => ({ can: vi.fn() }));

vi.mock("@/services/document-relationships-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/document-relationships-api")>()),
  ...relationshipApi,
}));
vi.mock("@/services/variations-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/variations-api")>()),
  ...variationApi,
}));
vi.mock("@/hooks/useRBAC", () => ({ default: () => rbacApi }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ _id: "project-1", name: "Metro" }]) },
}));
vi.mock("@/services/contract-master-api", () => ({
  getContractMasterForProject: vi.fn().mockResolvedValue(null),
}));

/** Shaped like `Variation` from routers/variations.py after variations-api `norm`. */
const variation = {
  id: "var-1", _id: "var-1", variation_number: "VO-001", variation_type: "positive",
  description: "Added works", status: "submitted", organization_id: "org-1", project_id: "project-1",
  contract_id: "primary", currency_amounts: [], linked_document_ids: ["65f0c0ffee0000000000aa01"],
};
const other = { ...variation, id: "var-9", _id: "var-9", variation_number: "VO-009" };
const letter = {
  _id: "65f0c0ffee0000000000aa01", filename: "ENG-VO-014.pdf", subject: "Instruction",
  letterNo: "ENG/VO/014", uploadType: "incoming", organization_id: "org-1", project_id: "project-1",
};
const link = {
  _id: "link-1", organization_id: "org-1", project_id: "project-1", target_type: "variation",
  target_id: "var-1", document_id: letter._id, relationship_role: "correspondence", source: "user",
  _revision: 1, removed_at: null, frozen_at: null, document: letter, target_label: "VO-001",
  target_route: "/variations?variation_id=var-1",
};

const renderAt = (url = "/variations") => render(
  <MemoryRouter initialEntries={[url]}>
    <Routes><Route path="/variations" element={<VariationRegisterPage />} /></Routes>
  </MemoryRouter>,
);

describe("Variation Register correspondence links", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rbacApi.can.mockReturnValue(true);
    variationApi.getVariations.mockResolvedValue([variation]);
    variationApi.getVariation.mockResolvedValue(other);
    variationApi.getVariationSummary.mockResolvedValue({
      original_contract_value: 0, total_submitted_amount: 0, total_approved_amount: 0,
      cumulative_approved_variation: 0, revised_contract_value: 0, percentage_variation: 0,
      pending_variation_count: 1, approved_variation_count: 0, rejected_variation_count: 0,
    });
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([letter]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([link]);
  });

  it("links existing correspondence to a Variation through the shared component", async () => {
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Correspondence for VO-001" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Correspondence — VO-001/)).toBeInTheDocument();
    expect(await within(dialog).findByText("No linked Documents yet.")).toBeInTheDocument();
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledWith("variation", "var-1");

    await user.type(within(dialog).getByLabelText("Search Documents"), "ENG");
    fireEvent.click(within(dialog).getByRole("button", { name: "Search" }));
    await waitFor(() => expect(relationshipApi.searchLinkableDocuments).toHaveBeenCalledWith({
      q: "ENG", organization_id: "org-1", project_id: "project-1", uploadType: "correspondence", limit: 25, skip: 0,
    }));
    await user.click(await within(dialog).findByRole("button", { name: "Link ENG-VO-014.pdf" }));
    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "variation", "var-1", [{ document_id: letter._id, relationship_role: "correspondence" }],
    ));
    expect(await within(dialog).findByTestId("linked-document")).toHaveTextContent("ENG-VO-014.pdf");
    // No raw linked_document_ids write ever leaves the page.
    expect(variationApi.updateVariation).not.toHaveBeenCalled();
  });

  it("opens the deep-linked Variation, fetching it when it is not in the current list", async () => {
    renderAt("/variations?variation_id=var-9");
    expect(await screen.findByText(/Correspondence — VO-009/)).toBeInTheDocument();
    expect(variationApi.getVariation).toHaveBeenCalledWith("var-9");
    await waitFor(() => expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledWith("variation", "var-9"));
  });

  it("opens a deep-linked Variation from the loaded list without a second fetch", async () => {
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([link]);
    renderAt("/variations?variation_id=var-1");
    expect(await screen.findByText(/Correspondence — VO-001/)).toBeInTheDocument();
    expect(await screen.findByTestId("linked-document")).toHaveTextContent("ENG/VO/014");
    expect(variationApi.getVariation).not.toHaveBeenCalled();
  });

  it("is view-only without dms.variation.edit", async () => {
    rbacApi.can.mockImplementation((permission: string) => permission !== "dms.variation.edit");
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([link]);
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Correspondence for VO-001" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/view-only access/i)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: /Unlink/ })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Search Documents")).not.toBeInTheDocument();
  });

  it("shows a retryable error when the links cannot be loaded", async () => {
    relationshipApi.listEntityDocumentLinks.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([]);
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Correspondence for VO-001" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Linked Documents could not be loaded.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Retry linked Documents" }));
    expect(await within(dialog).findByText("No linked Documents yet.")).toBeInTheDocument();
  });

  it("never sends linked_document_ids when a Variation is edited", async () => {
    variationApi.updateVariation.mockResolvedValue(variation);
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByTitle("Edit"));
    await user.click(await screen.findByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(variationApi.updateVariation).toHaveBeenCalled());
    expect(variationApi.updateVariation.mock.calls[0][1]).not.toHaveProperty("linked_document_ids");
  });
});
