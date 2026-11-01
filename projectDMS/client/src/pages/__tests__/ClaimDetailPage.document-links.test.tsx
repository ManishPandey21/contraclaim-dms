import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClaimDetailPage from "@/pages/ClaimDetailPage";

const relationshipApi = vi.hoisted(() => ({
  CLAIM_DOCUMENT_RELATIONSHIP_ROLES: [
    { value: "notice", label: "Notice" },
    { value: "claim_submission", label: "Claim submission" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "engineer_response", label: "Engineer response" },
    { value: "employer_response", label: "Employer response" },
    { value: "determination", label: "Determination" },
    { value: "correspondence", label: "Correspondence" },
  ],
  listEntityDocumentLinks: vi.fn(),
  searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(),
  removeDocumentLink: vi.fn(),
}));

vi.mock("@/services/document-relationships-api", () => relationshipApi);
vi.mock("@/hooks/useHasPermission", () => ({ default: () => true }));
vi.mock("@/services/claims-api", () => ({
  getClaim: vi.fn().mockResolvedValue({
    id: "claim-1",
    claim_ref: "CLM-001",
    title: "Delay claim",
    type: "eot",
    status: "draft",
    currency: "INR",
    contract_clauses: [],
    linked_document_ids: ["doc-1"],
    linked_letter_ids: [],
    organization_id: "org-1",
    project_id: "project-1",
  }),
  downloadEvidenceBundle: vi.fn(),
}));
vi.mock("@/services/tasks-api", () => ({ getTasks: vi.fn().mockResolvedValue([]) }));
vi.mock("@/components/claims/ClaimApprovalDialog", () => ({ default: () => null }));
vi.mock("@/components/claims/ClaimAssessmentDialog", () => ({ default: () => null }));
vi.mock("@/components/claims/ClaimTaskDialog", () => ({ default: () => null }));

describe("Claim document relationship workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      {
        _id: "link-1",
        document_id: "doc-1",
        relationship_role: "notice",
        _revision: 1,
        document: { _id: "doc-1", filename: "Notice.pdf", subject: "Notice" },
      },
    ]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([
      { _id: "doc-2", filename: "Programme.pdf", subject: "Baseline programme" },
    ]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([
      {
        _id: "link-2",
        document_id: "doc-2",
        relationship_role: "supporting_document",
        _revision: 1,
        document: { _id: "doc-2", filename: "Programme.pdf" },
      },
    ]);
  });

  it("renders names and links a searched Document with an explicit Claim role", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/claims/claim-1"]}>
        <Routes>
          <Route path="/claims/:id" element={<ClaimDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Notice.pdf")).toBeInTheDocument();
    expect(screen.queryByText("Document doc-1")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Search Documents"), "programme");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Programme.pdf")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Relationship role"), "supporting_document");
    await user.click(screen.getByRole("button", { name: "Link Programme.pdf" }));

    await waitFor(() =>
      expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
        "claim",
        "claim-1",
        [{ document_id: "doc-2", relationship_role: "supporting_document" }],
      ),
    );
    expect(relationshipApi.searchLinkableDocuments).toHaveBeenCalledWith({
      q: "programme",
      organization_id: "org-1",
      project_id: "project-1",
      limit: 25,
      skip: 0,
    });

    await user.click(screen.getByRole("button", { name: "Unlink Notice.pdf" }));
    await waitFor(() =>
      expect(relationshipApi.removeDocumentLink).toHaveBeenCalledWith(
        "link-1",
        1,
        "Removed from Claim",
      ),
    );
    expect(screen.queryByText("Notice.pdf")).not.toBeInTheDocument();
  });
});
