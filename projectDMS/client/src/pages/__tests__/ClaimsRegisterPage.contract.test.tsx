import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-18 (D-23, AQ-1): a claim says which contract it is made under. One contract on
 * the project is shown, not asked for. Several: the person chooses and nothing is
 * guessed. None: "No contract identified". A contract already on a claim is fixed.
 */
const api = vi.hoisted(() => ({
  getClaims: vi.fn(),
  updateClaim: vi.fn(),
  createClaim: vi.fn(),
  deleteClaim: vi.fn(),
  downloadEvidenceBundle: vi.fn(),
  setClaimStatus: vi.fn(),
  fetchClaimContractOptions: vi.fn(),
}));
vi.mock("@/services/claims-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/claims-api")>()),
  ...api,
}));
vi.mock("@/services/sla-api", () => ({ getUpcoming: vi.fn().mockResolvedValue([]) }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ id: "proj-A1", name: "Metro Line 3" }]) },
}));
vi.mock("@/hooks/useRegisterProjectScope", () => ({
  useRegisterProjectScope: () => ({ selectedProjectId: "proj-A1", tenantLoading: false }),
}));
vi.mock("@/hooks/useClaimBenchOn", () => ({ useClaimBenchOn: () => true }));
vi.mock("@/components/claim-bench/ClaimSignalsStrip", () => ({ default: () => null }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, onClick }: { children: React.ReactNode; onClick?: () => void }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
}));

import ClaimsRegisterPage from "../ClaimsRegisterPage";

const MAIN = { contract_id: "primary", label: "Main works: viaduct and stations" };
const DEPOT = { contract_id: "pkg-depot", label: "Depot package" };

const claim = (overrides: Record<string, unknown> = {}) => ({
  id: "claim-1",
  claim_ref: "CLM-001",
  type: "eot",
  title: "Delayed handover of Site",
  status: "draft",
  currency: "INR",
  contract_clauses: ["8.4"],
  linked_document_ids: [],
  linked_letter_ids: [],
  allowed_status_transitions: [],
  project_id: "proj-A1",
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <ClaimsRegisterPage />
    </MemoryRouter>,
  );

async function openNew() {
  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: /New Claim/ }));
  await userEvent.type(screen.getByPlaceholderText("e.g. EOT — monsoon delay"), "Late access to the depot");
}

beforeEach(() => {
  vi.clearAllMocks();
  api.getClaims.mockResolvedValue([]);
  api.createClaim.mockResolvedValue(claim());
  api.updateClaim.mockResolvedValue(claim());
});

describe("ClaimsRegisterPage: the claim's contract", () => {
  it("shows the project's only contract and does not ask for it", async () => {
    api.fetchClaimContractOptions.mockResolvedValue({ options: [MAIN], selected: "primary", must_choose: false });
    await openNew();
    expect(await screen.findByText("Main works: viaduct and stations")).toBeInTheDocument();
    expect(screen.getByText("The project's only contract.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Contract")).not.toBeInTheDocument();
    expect(api.fetchClaimContractOptions).toHaveBeenCalledWith("proj-A1");

    await userEvent.click(screen.getByRole("button", { name: "Create claim" }));
    await waitFor(() => expect(api.createClaim).toHaveBeenCalledTimes(1));
    // The server selects the only contract; the form does not pretend to have chosen.
    expect(api.createClaim.mock.calls[0][0].contract_id).toBeUndefined();
  });

  it("makes the person choose when the project has several, and sends the choice", async () => {
    api.fetchClaimContractOptions.mockResolvedValue({ options: [DEPOT, MAIN], selected: null, must_choose: true });
    await openNew();
    const select = await screen.findByLabelText("Contract");
    expect(select).toHaveValue("");

    await userEvent.click(screen.getByRole("button", { name: "Create claim" }));
    expect(toast.error).toHaveBeenCalledWith("Choose the contract this claim is made under");
    expect(api.createClaim).not.toHaveBeenCalled();

    await userEvent.selectOptions(select, "pkg-depot");
    await userEvent.click(screen.getByRole("button", { name: "Create claim" }));
    await waitFor(() => expect(api.createClaim).toHaveBeenCalledTimes(1));
    expect(api.createClaim.mock.calls[0][0].contract_id).toBe("pkg-depot");
    // The key is the option's value; what the person reads is the contract's name.
    expect(select.textContent).not.toContain("pkg-depot");
  });

  it("says no contract is identified when the project has none the person can read", async () => {
    api.fetchClaimContractOptions.mockResolvedValue({ options: [], selected: null, must_choose: false });
    await openNew();
    expect(await screen.findByText("No contract identified")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create claim" }));
    await waitFor(() => expect(api.createClaim).toHaveBeenCalledTimes(1));
    expect(api.createClaim.mock.calls[0][0].contract_id).toBeUndefined();
  });

  it("shows a contract already on a claim as fixed, and never sends it again", async () => {
    api.getClaims.mockResolvedValue([claim({ contract_id: "primary", contract_label: "Main works: viaduct and stations" })]);
    renderPage();
    // The register names the contract under the claim's title.
    expect(await screen.findByText("Main works: viaduct and stations")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Edit details/ }));
    expect(screen.getByText(/Fixed for this claim/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Contract")).not.toBeInTheDocument();
    expect(api.fetchClaimContractOptions).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(api.updateClaim).toHaveBeenCalledTimes(1));
    expect(api.updateClaim.mock.calls[0][1].contract_id).toBeUndefined();
  });

  it("lets a claim that has no contract be given one when it is edited", async () => {
    api.getClaims.mockResolvedValue([claim()]);
    api.fetchClaimContractOptions.mockResolvedValue({ options: [DEPOT, MAIN], selected: null, must_choose: true });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Edit details/ }));
    // Editing does not force a choice: the claim may stay without a contract.
    await userEvent.selectOptions(await screen.findByLabelText("Contract"), "primary");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(api.updateClaim).toHaveBeenCalledTimes(1));
    expect(api.updateClaim.mock.calls[0][1].contract_id).toBe("primary");
  });

  it("says so when the contracts cannot be loaded, and still does not guess one", async () => {
    api.fetchClaimContractOptions.mockRejectedValue(new Error("network"));
    await openNew();
    expect(await screen.findByText(/The project's contracts could not be loaded/)).toBeInTheDocument();
  });
});
