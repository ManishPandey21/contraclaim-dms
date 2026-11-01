import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IPCBillRegisterPage from "@/pages/IPCBillRegisterPage";

const relationshipApi = vi.hoisted(() => ({
  IPC_DOCUMENT_RELATIONSHIP_ROLES: [
    { value: "ipc_submission", label: "IPC submission" },
    { value: "certified_ipc", label: "Certified IPC" },
    { value: "invoice", label: "Invoice" },
    { value: "payment_certificate", label: "Payment certificate" },
    { value: "supporting_document", label: "Supporting document" },
    { value: "payment_correspondence", label: "Payment correspondence" },
  ],
  listEntityDocumentLinks: vi.fn(),
  searchLinkableDocuments: vi.fn(),
  batchLinkDocuments: vi.fn(),
  removeDocumentLink: vi.fn(),
}));

const ipcApi = vi.hoisted(() => ({
  getIPCBills: vi.fn(), getIPCBill: vi.fn(), getIPCSummary: vi.fn(),
  createIPCBill: vi.fn(), updateIPCBill: vi.fn(), deleteIPCBill: vi.fn(),
  exportIPCBills: vi.fn(),
}));
const rbacApi = vi.hoisted(() => ({ can: vi.fn() }));

vi.mock("@/services/document-relationships-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/document-relationships-api")>()),
  ...relationshipApi,
}));
vi.mock("@/hooks/useRBAC", () => ({ default: () => rbacApi }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ _id: "project-1", name: "Metro" }]) },
}));
vi.mock("@/services/contract-master-api", () => ({
  getContractMasterForProject: vi.fn().mockResolvedValue(null),
}));
vi.mock("@/services/ipc-categories-api", () => ({
  getIPCCategories: vi.fn().mockResolvedValue([]),
  getIPCCategoriesManage: vi.fn().mockResolvedValue([]),
  createIPCCategory: vi.fn(), updateIPCCategory: vi.fn(), deleteIPCCategory: vi.fn(),
}));
vi.mock("@/services/ipc-bills-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/ipc-bills-api")>()),
  ...ipcApi,
}));

const ipc = {
  id: "ipc-1", ipc_number: "IPC-001", status: "submitted", base_currency: "INR",
  payment_structure: "full", project_id: "project-1", organization_id: "org-1",
  line_items: [], payments: [], letter_references: [], linked_document_ids: ["doc-legacy"],
  deductions: { recovery_of_advances: [], withheld: [], penalties_ld: [], deductions: [], gst: [] },
  revisions: [], current_revision: 0,
};

describe("IPC document relationship workflow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    rbacApi.can.mockReturnValue(true);
    ipcApi.getIPCBills.mockResolvedValue([ipc]);
    ipcApi.getIPCBill.mockResolvedValue(ipc);
    ipcApi.getIPCSummary.mockResolvedValue({
      total_ipcs: 1, base_currency: "INR", total_claimed_base: 0,
      total_approved_base: 0, total_net_payable_base: 0, total_paid_base: 0,
      total_balance_payable_base: 0, cumulative_ipc_value_base: 0,
      percent_of_contract_billed: 0, percent_of_contract_approved: 0,
      pending_count: 1, approved_count: 0, paid_count: 0,
    });
    ipcApi.updateIPCBill.mockResolvedValue(ipc);
    relationshipApi.listEntityDocumentLinks.mockResolvedValue([
      { _id: "link-1", document_id: "doc-1", relationship_role: "invoice", _revision: 1,
        document: { _id: "doc-1", filename: "Invoice.pdf" } },
      { _id: "legacy:ipc_bill:ipc-1:doc-legacy", document_id: "doc-legacy",
        relationship_role: "manual_review", source: "legacy_read_through", _revision: 1,
        document: { _id: "doc-legacy", filename: "Legacy.pdf" } },
    ]);
    relationshipApi.searchLinkableDocuments.mockResolvedValue([
      { _id: "doc-2", filename: "Certificate.pdf" },
    ]);
    relationshipApi.batchLinkDocuments.mockResolvedValue([
      { _id: "link-2", document_id: "doc-2", relationship_role: "payment_certificate", _revision: 1,
        document: { _id: "doc-2", filename: "Certificate.pdf" } },
    ]);
  });

  it("opens a reverse-link target and uses the common role-aware workflow", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/ipc-bills?ipc_id=ipc-1"]}>
        <Routes><Route path="/ipc-bills" element={<IPCBillRegisterPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/Edit IPC.*IPC-001/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Docs & history" }));
    expect(await screen.findByText("Invoice.pdf")).toBeInTheDocument();
    expect(screen.getByText("Legacy.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Unlink Legacy.pdf" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Search Documents"), "certificate");
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    await user.selectOptions(screen.getByLabelText("Relationship role"), "payment_certificate");
    await user.click(await screen.findByRole("button", { name: "Link Certificate.pdf" }));

    await waitFor(() => expect(relationshipApi.batchLinkDocuments).toHaveBeenCalledWith(
      "ipc_bill", "ipc-1", [{ document_id: "doc-2", relationship_role: "payment_certificate" }],
    ));
    expect(relationshipApi.searchLinkableDocuments).toHaveBeenCalledWith({
      q: "certificate", organization_id: "org-1", project_id: "project-1", limit: 25, skip: 0,
    });

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(ipcApi.updateIPCBill).toHaveBeenCalled());
    expect(ipcApi.updateIPCBill.mock.calls[0][1]).not.toHaveProperty("linked_document_ids");
  });

  it("requires an IPC identity before document relationships can be created", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><IPCBillRegisterPage /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Add IPC" }));
    await user.click(screen.getByRole("tab", { name: "Docs & history" }));

    expect(screen.getByText("Create the IPC before linking Documents.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Search Documents")).not.toBeInTheDocument();
  });

  it("opens a deep-linked IPC read-only when the actor lacks IPC edit", async () => {
    rbacApi.can.mockImplementation((permission: string) => permission !== "dms.ipc.edit");
    render(
      <MemoryRouter initialEntries={["/ipc-bills?ipc_id=ipc-1"]}>
        <Routes><Route path="/ipc-bills" element={<IPCBillRegisterPage />} /></Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/View IPC.*IPC-001/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("IPC-001")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Docs & history" }));
    expect(await screen.findByText(/view-only access to document links/i)).toBeInTheDocument();
  });
});
