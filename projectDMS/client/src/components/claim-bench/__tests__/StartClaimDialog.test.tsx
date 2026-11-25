import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-20 (D-24, JG-1): a claim starts from three questions - what happened, when you
 * became aware, which contract - and opens on its board. "More details" is the full form.
 */
const api = vi.hoisted(() => ({
  startClaim: vi.fn(),
  fetchClaimContractOptions: vi.fn(),
  getClaims: vi.fn(),
  createClaim: vi.fn(),
}));
vi.mock("@/services/claims-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/claims-api")>()),
  ...api,
}));
const bench = vi.hoisted(() => ({ on: true }));
vi.mock("@/hooks/useClaimBenchOn", () => ({ useClaimBenchOn: () => bench.on }));
vi.mock("@/services/sla-api", () => ({ getUpcoming: vi.fn().mockResolvedValue([]) }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getProjects: vi.fn().mockResolvedValue([{ id: "proj-A1", name: "Metro Line 3" }]) },
}));
vi.mock("@/hooks/useRegisterProjectScope", () => ({
  useRegisterProjectScope: () => ({ selectedProjectId: "proj-A1", tenantLoading: false }),
}));
vi.mock("@/components/claim-bench/ClaimSignalsStrip", () => ({ default: () => null }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import StartClaimDialog from "../StartClaimDialog";
import ClaimsRegisterPage from "@/pages/ClaimsRegisterPage";

const HAPPENED = "Access to the depot site was handed over on 20 March, eighteen days late.";
const ONE = { options: [{ contract_id: "primary", label: "Main works" }], selected: "primary", must_choose: false };
const TWO = {
  options: [
    { contract_id: "primary", label: "Main works" },
    { contract_id: "pkg-depot", label: "Depot package" },
  ],
  selected: null,
  must_choose: true,
};

function renderDialog(props: Partial<React.ComponentProps<typeof StartClaimDialog>> = {}) {
  const handlers = { onOpenChange: vi.fn(), onStarted: vi.fn(), onMoreDetails: vi.fn() };
  render(<StartClaimDialog open projectId="proj-A1" {...handlers} {...props} />);
  return handlers;
}

async function answer(date = "2026-03-20") {
  await userEvent.type(screen.getByLabelText("What happened?"), HAPPENED);
  const aware = screen.getByLabelText("When did you become aware of it?");
  await userEvent.clear(aware);
  await userEvent.type(aware, date);
}

beforeEach(() => {
  vi.clearAllMocks();
  bench.on = true;
  api.fetchClaimContractOptions.mockResolvedValue(ONE);
  api.getClaims.mockResolvedValue([]);
});

describe("StartClaimDialog (4B-20)", () => {
  it("asks three questions and sends exactly those answers", async () => {
    api.startClaim.mockResolvedValue({ claim: { id: "claim-9" }, head_id: "H1", board_version: 0 });
    const handlers = renderDialog();
    const start = screen.getByRole("button", { name: "Start claim" });
    expect(start).toBeDisabled();
    await answer();
    await screen.findByText("Main works");
    expect(start).toBeEnabled();
    await userEvent.click(start);
    await waitFor(() => expect(handlers.onStarted).toHaveBeenCalled());
    expect(api.startClaim).toHaveBeenCalledWith({
      project_id: "proj-A1",
      what_happened: HAPPENED,
      aware_on: "2026-03-20",
      contract_id: undefined,
    });
  });

  it("with several contracts, one must be chosen before starting", async () => {
    api.fetchClaimContractOptions.mockResolvedValue(TWO);
    renderDialog();
    await answer();
    expect(await screen.findByText(/choose the contract/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start claim" })).toBeDisabled();
    expect(api.startClaim).not.toHaveBeenCalled();
  });

  it("refuses an awareness date in the future", async () => {
    renderDialog();
    await answer("2999-01-01");
    expect(await screen.findByText(/cannot be in the future/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start claim" })).toBeDisabled();
  });

  it("shows a refusal as the server's sentence", async () => {
    api.startClaim.mockRejectedValue({
      response: { status: 403, data: { detail: { code: "x", user_message: "You cannot start claims on this project." } } },
    });
    renderDialog();
    await answer();
    await screen.findByText("Main works");
    await userEvent.click(screen.getByRole("button", { name: "Start claim" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You cannot start claims on this project.");
  });

  it("More details hands what was answered to the full form", async () => {
    const handlers = renderDialog();
    await answer();
    await userEvent.click(screen.getByRole("button", { name: "More details" }));
    expect(handlers.onMoreDetails).toHaveBeenCalledWith({ whatHappened: HAPPENED, awareOn: "2026-03-20", contractId: "" });
    expect(api.startClaim).not.toHaveBeenCalled();
  });
});

describe("the claim register's New Claim (4B-20)", () => {
  const renderRegister = () =>
    render(
      <MemoryRouter initialEntries={["/claims"]}>
        <Routes>
          <Route path="/claims" element={<ClaimsRegisterPage />} />
          <Route path="/claims/:id" element={<p>Board of the new claim</p>} />
        </Routes>
      </MemoryRouter>,
    );

  it("opens the three questions and then the new claim's board", async () => {
    api.startClaim.mockResolvedValue({ claim: { id: "claim-9" }, head_id: "H1", board_version: 0 });
    renderRegister();
    await userEvent.click(await screen.findByRole("button", { name: /New Claim/ }));
    expect(await screen.findByRole("dialog", { name: "Start a claim" })).toBeInTheDocument();
    await answer();
    await screen.findByText("Main works");
    await userEvent.click(screen.getByRole("button", { name: "Start claim" }));
    expect(await screen.findByText("Board of the new claim")).toBeInTheDocument();
  });

  it("More details opens the full form with the answers filled in", async () => {
    renderRegister();
    await userEvent.click(await screen.findByRole("button", { name: /New Claim/ }));
    await answer();
    await userEvent.click(screen.getByRole("button", { name: "More details" }));
    const form = await screen.findByRole("dialog", { name: "New Claim" });
    expect(form).toBeInTheDocument();
    expect(screen.getByLabelText("Became aware on")).toHaveValue("2026-03-20");
    expect(screen.getByDisplayValue(HAPPENED)).toBeInTheDocument();
  });

  it("where the Claim Bench is off, New Claim is the full form", async () => {
    bench.on = false;
    renderRegister();
    await userEvent.click(await screen.findByRole("button", { name: /New Claim/ }));
    expect(await screen.findByRole("dialog", { name: "New Claim" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Start a claim" })).toBeNull();
  });
});
