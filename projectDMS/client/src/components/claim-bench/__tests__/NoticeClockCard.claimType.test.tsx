import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Owner decision 2026-11-18: a claim started from three questions has a provisional
 * type. The notice clock says so, and the period counts as the contract's only once
 * a person confirms (or changes) the type.
 */
const api = vi.hoisted(() => ({ confirmClaimType: vi.fn() }));
vi.mock("@/services/claims-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/claims-api")>()),
  ...api,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import NoticeClockCard from "../NoticeClockCard";
import { describeNotice, noticeCaveat } from "@/lib/claims-helpers";
import type { NoticeAssessment } from "@/services/claims-api";

const ASSESSMENT: NoticeAssessment = {
  basis: "awareness",
  basis_date: "2026-03-20T00:00:00",
  period_days: 28,
  period_source: "contract_unverified",
  period_verified: false,
  claim_type_confirmed: false,
  clause_no: "20.1",
  due_date: "2026-04-17T00:00:00",
  days_remaining: 10,
  state: "open",
};

function renderCard(props: Partial<React.ComponentProps<typeof NoticeClockCard>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NoticeClockCard claimId="c-1" claimType="other" projectId="p-1" assessment={ASSESSMENT} readOnly={false} {...props} />
    </QueryClientProvider>,
  );
}

describe("a provisional claim type on the notice clock", () => {
  beforeEach(() => api.confirmClaimType.mockReset().mockResolvedValue({ id: "c-1", type: "other", type_confirmed: true }));

  it("says the type is provisional and the period is not yet the contract's", () => {
    renderCard();
    expect(screen.getByText(/only once a person has chosen the type/)).toBeInTheDocument();
    expect(noticeCaveat(ASSESSMENT)).toMatch(/claim type is provisional/);
    expect(describeNotice(ASSESSMENT)?.sentence).toMatch(/claim type is provisional/);
  });

  it("confirms the type a person keeps", async () => {
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: "Confirm claim type" }));
    await waitFor(() => expect(api.confirmClaimType).toHaveBeenCalledWith("c-1", "other"));
  });

  it("changes and confirms another type, warning that notice decisions are withdrawn", async () => {
    renderCard();
    await userEvent.selectOptions(screen.getByLabelText("Claim type"), "eot");
    expect(screen.getByText(/withdraws the notice decisions/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change and confirm" }));
    await waitFor(() => expect(api.confirmClaimType).toHaveBeenCalledWith("c-1", "eot"));
  });

  it("offers no control on a read-only board, and says nothing once the type is confirmed", () => {
    const { unmount } = renderCard({ readOnly: true });
    expect(screen.getByText(/only once a person has chosen the type/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm claim type" })).not.toBeInTheDocument();
    unmount();
    renderCard({ assessment: { ...ASSESSMENT, claim_type_confirmed: true } });
    expect(screen.queryByText(/only once a person has chosen the type/)).not.toBeInTheDocument();
  });
});
