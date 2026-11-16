import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-17 (D-14, C-07): once a claim has a Claim Bench board, its amount and EOT days come
 * from the heads. The register shows them read-only and does not send them; the server
 * refuses a change (409 claim_amount_from_board).
 */
const api = vi.hoisted(() => ({
  getClaims: vi.fn(),
  updateClaim: vi.fn(),
  createClaim: vi.fn(),
  deleteClaim: vi.fn(),
  downloadEvidenceBundle: vi.fn(),
  setClaimStatus: vi.fn(),
}));
vi.mock("@/services/claims-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/claims-api")>()),
  ...api,
}));
vi.mock("@/services/sla-api", () => ({ getUpcoming: vi.fn().mockResolvedValue([]) }));
vi.mock("@/services/enhanced-api", () => ({ enhancedApi: { getProjects: vi.fn().mockResolvedValue([]) } }));
vi.mock("@/hooks/useRegisterProjectScope", () => ({
  useRegisterProjectScope: () => ({ selectedProjectId: "proj-A1", tenantLoading: false }),
}));
vi.mock("@/hooks/useClaimBenchOn", () => ({ useClaimBenchOn: () => true }));
vi.mock("@/components/claim-bench/ClaimSignalsStrip", () => ({ default: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// The row menu renders its items directly: the test is about the form, not the menu.
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

const claim = (overrides: Record<string, unknown> = {}) => ({
  id: "claim-1",
  claim_ref: "CLM-001",
  type: "eot",
  title: "Delayed handover of Site",
  status: "draft",
  amount_claimed: 18_450_000,
  currency: "INR",
  eot_days_claimed: 146,
  contract_clauses: ["8.4"],
  linked_document_ids: [],
  linked_letter_ids: [],
  allowed_status_transitions: [],
  project_id: "proj-A1",
  ...overrides,
});

async function editAndSave() {
  render(
    <MemoryRouter>
      <ClaimsRegisterPage />
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("button", { name: /Edit details/ }));
  return {
    amount: screen.getByLabelText("Amount claimed") as HTMLInputElement,
    days: screen.getByLabelText("EOT days claimed") as HTMLInputElement,
  };
}

describe("ClaimsRegisterPage: the amount of a claim with a board", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.updateClaim.mockResolvedValue(claim());
  });

  it("is read-only and not sent once the claim has a board", async () => {
    api.getClaims.mockResolvedValue([claim({ bench_stage: "heads", head_count: 2 })]);
    const { amount, days } = await editAndSave();
    expect(amount).toBeDisabled();
    expect(days).toBeDisabled();
    expect(screen.getByText("Comes from the heads on the Claim Bench. Change it there.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^(Save|Update)/ }));
    await waitFor(() => expect(api.updateClaim).toHaveBeenCalled());
    const payload = api.updateClaim.mock.calls[0][1];
    expect(payload.amount_claimed).toBeUndefined();
    expect(payload.eot_days_claimed).toBeUndefined();
  });

  it("is editable and sent while the claim has no board", async () => {
    api.getClaims.mockResolvedValue([claim({ bench_stage: null })]);
    const { amount, days } = await editAndSave();
    expect(amount).not.toBeDisabled();
    expect(days).not.toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /^(Save|Update)/ }));
    await waitFor(() => expect(api.updateClaim).toHaveBeenCalled());
    const payload = api.updateClaim.mock.calls[0][1];
    expect(payload.amount_claimed).toBe(18_450_000);
    expect(payload.eot_days_claimed).toBe(146);
  });
});
