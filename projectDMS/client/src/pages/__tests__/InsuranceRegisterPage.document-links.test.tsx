import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InsuranceRegisterPage from "@/pages/InsuranceRegisterPage";

const insuranceApi = vi.hoisted(() => ({
  getInsurance: vi.fn(), getInsuranceSummary: vi.fn(), getInsuranceAlerts: vi.fn(),
  getInsuranceTypes: vi.fn(), createInsurance: vi.fn(), updateInsurance: vi.fn(),
  deleteInsurance: vi.fn(), exportInsurance: vi.fn(),
  uploadInsuranceDocument: vi.fn(),
}));
const relationshipApi = vi.hoisted(() => ({
  listEntityDocumentLinks: vi.fn(), searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(), removeDocumentLink: vi.fn(),
  INSURANCE_DOCUMENT_RELATIONSHIP_ROLES: [
    { value: "policy", label: "Policy" },
    { value: "certificate", label: "Certificate" },
    { value: "correspondence", label: "Correspondence" },
    { value: "supporting_document", label: "Supporting document" },
  ],
}));
const permission = vi.hoisted(() => ({ can: vi.fn(() => true) }));

vi.mock("@/services/insurance-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/insurance-api")>()),
  ...insuranceApi,
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
  listContractMaster: vi.fn().mockResolvedValue([]),
}));

const policy = {
  id: "insurance-1", policy_number: "POL-17", insurance_type: "Marine Cargo Insurance",
  currency: "INR", project_id: "project-1", organization_id: "org-1",
  date_of_issue: "2026-08-01T00:00:00Z", date_of_expiry: "2027-08-01T00:00:00Z",
  linked_document_ids: [],
};

describe("Insurance canonical Document workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permission.can.mockReturnValue(true);
    insuranceApi.getInsurance.mockResolvedValue([policy]);
    insuranceApi.getInsuranceSummary.mockResolvedValue({
      total: 1, active: 1, expiring_soon: 0, expired: 0, missing: 0, total_sum_insured: 0,
    });
    insuranceApi.getInsuranceAlerts.mockResolvedValue([]);
    insuranceApi.getInsuranceTypes.mockResolvedValue([
      { id: "type-1", name: "Marine Cargo Insurance", is_active: true, is_default: true },
    ]);
    insuranceApi.updateInsurance.mockResolvedValue(policy);
    insuranceApi.uploadInsuranceDocument.mockResolvedValue({
      document: { _id: "doc-new", filename: "Policy.pdf" },
      link: { _id: "link-new", document_id: "doc-new", relationship_role: "policy", _revision: 1 },
    });
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      { _id: "link-1", document_id: "doc-1", relationship_role: "policy", _revision: 1,
        document: { _id: "doc-1", filename: "Policy.pdf" } },
    ]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([]);
  });

  it("renders shared canonical links for the Insurance parent", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByTitle("Edit"));

    expect(await screen.findByText("Policy.pdf")).toBeInTheDocument();
    expect(relationshipApi.listEntityDocumentLinks).toHaveBeenCalledWith(
      "insurance", "insurance-1",
    );
    expect(screen.getByLabelText("Relationship role")).toHaveValue("policy");
  });

  it("uses the canonical linked Document for the register file action", async () => {
    insuranceApi.getInsurance.mockResolvedValueOnce([
      { ...policy, linked_document_ids: ["doc-1"] },
    ]);
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    const link = await screen.findByRole("link", { name: "Documents (1)" });
    expect(link).toHaveAttribute("href", "/documentviewer/doc-1");
    expect(screen.queryByText("Legacy review")).not.toBeInTheDocument();
  });

  it("uploads a selected file through the policy-scoped canonical endpoint on save", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);
    await user.click(await screen.findByTitle("Edit"));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    const file = new File(["%PDF-canonical"], "Renewed Policy.pdf", { type: "application/pdf" });

    await user.selectOptions(screen.getByLabelText("Upload relationship role"), "certificate");
    fireEvent.change(input!, { target: { files: [file] } });
    expect(insuranceApi.uploadInsuranceDocument).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(insuranceApi.uploadInsuranceDocument).toHaveBeenCalledWith(
      "insurance-1", file, "certificate",
    ));
    const payload = insuranceApi.updateInsurance.mock.calls[0][1];
    expect(payload).not.toHaveProperty("document_id");
    expect(payload).not.toHaveProperty("linked_document_ids");
  });

  it("keeps canonical links readable but removes Insurance mutation controls for view-only users", async () => {
    permission.can.mockImplementation((name: string) => name !== "dms.insurance.edit");
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByTitle("View"));

    expect(await screen.findByText("Policy.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });
});
