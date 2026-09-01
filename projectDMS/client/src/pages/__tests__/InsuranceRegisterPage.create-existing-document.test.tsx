import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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

// Radix Select needs pointer-capture APIs jsdom does not implement, and real
// constructable observers: the shared setup stubs those with vi.fn(), which
// throws when Radix calls them with `new`. Overridden locally so the shared
// harness other suites depend on stays untouched.
beforeAll(() => {
  Object.assign(Element.prototype, {
    hasPointerCapture: () => false,
    setPointerCapture: () => undefined,
    releasePointerCapture: () => undefined,
    scrollIntoView: () => undefined,
  });
  class Observer {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  }
  window.ResizeObserver = Observer as unknown as typeof ResizeObserver;
  window.IntersectionObserver = Observer as unknown as typeof IntersectionObserver;
});

const existingDocument = {
  _id: "doc-1",
  filename: "Existing Policy.pdf",
  status: "approved",
  organization_id: "org-1",
  project_id: "project-1",
};

async function selectOption(user: ReturnType<typeof userEvent.setup>, name: string) {
  const trigger = screen.getByRole("combobox", { name });
  trigger.focus();
  await user.keyboard("{Enter}");
  await user.keyboard("{ArrowDown}{Enter}");
}

async function openCreateDialogAndFillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Add Insurance/i }));
  await selectOption(user, "Project");
  await selectOption(user, "Insurance type");
  await user.type(screen.getByLabelText("Policy number"), "POL-NEW-1");
  await user.type(screen.getByLabelText("Date of issue"), "2026-08-01");
  await user.type(screen.getByLabelText("Date of expiry"), "2027-08-01");
}

describe("Insurance create from an existing canonical Document", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    permission.can.mockReturnValue(true);
    insuranceApi.getInsurance.mockResolvedValue([]);
    insuranceApi.getInsuranceSummary.mockResolvedValue({
      total: 0, active: 0, expiring_soon: 0, expired: 0, missing: 0, total_sum_insured: 0,
    });
    insuranceApi.getInsuranceAlerts.mockResolvedValue([]);
    insuranceApi.getInsuranceTypes.mockResolvedValue([
      { id: "type-1", name: "Marine Cargo Insurance", is_active: true, is_default: true },
    ]);
    insuranceApi.createInsurance.mockResolvedValue({ ...existingDocument, id: "insurance-new" });
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([existingDocument]);
  });

  it("offers linking an existing Document instead of uploading a new one", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: /Add Insurance/i }));

    expect(
      screen.getByRole("button", { name: /Link existing Document/i }),
    ).toBeInTheDocument();
  });

  it("creates the policy with the selected existing Document and uploads nothing", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await openCreateDialogAndFillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: /Link existing Document/i }));
    await user.type(screen.getByLabelText("Search existing Documents"), "policy");

    // Server-side canonical search, not a client-side filter over a preloaded list.
    await waitFor(() =>
      expect(relationshipApi.searchLinkableDocuments).toHaveBeenCalled(),
    );
    await user.click(await screen.findByRole("button", { name: /Select Existing Policy.pdf/i }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(insuranceApi.createInsurance).toHaveBeenCalledWith(
        expect.objectContaining({
          existing_document_links: [
            { document_id: "doc-1", relationship_role: "policy" },
          ],
        }),
      ),
    );
    expect(insuranceApi.uploadInsuranceDocument).not.toHaveBeenCalled();
  });

  it("still refuses a create with neither new nor existing evidence", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await openCreateDialogAndFillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(insuranceApi.createInsurance).not.toHaveBeenCalled());
  });

  it("offers a direct view action for the selected existing Document", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><InsuranceRegisterPage /></MemoryRouter>);

    await openCreateDialogAndFillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: /Link existing Document/i }));
    await user.type(screen.getByLabelText("Search existing Documents"), "policy");
    await user.click(await screen.findByRole("button", { name: /Select Existing Policy.pdf/i }));

    expect(screen.getByRole("link", { name: /View Existing Policy.pdf/i })).toHaveAttribute(
      "href",
      "/documentviewer/doc-1",
    );
  });
});
