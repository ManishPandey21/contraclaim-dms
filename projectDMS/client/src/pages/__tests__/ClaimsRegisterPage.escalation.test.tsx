import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** 4B-40: several claims of one project are escalated together from the register. */
const api = vi.hoisted(() => ({
  getClaims: vi.fn(),
  updateClaim: vi.fn(),
  createClaim: vi.fn(),
  deleteClaim: vi.fn(),
  downloadEvidenceBundle: vi.fn(),
  setClaimStatus: vi.fn(),
}));
const dialog = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));
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
vi.mock("@/components/claim-bench/EscalateDialog", () => ({
  default: (props: Record<string, unknown>) => {
    dialog.props = props;
    return props.open ? <p>escalate dialog open</p> : null;
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import ClaimsRegisterPage from "../ClaimsRegisterPage";

const claim = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  claim_ref: id.toUpperCase(),
  type: "eot",
  title: `Claim ${id}`,
  status: "submitted",
  contract_clauses: [],
  linked_document_ids: [],
  linked_letter_ids: [],
  allowed_status_transitions: [],
  project_id: "proj-A1",
  ...over,
});

describe("ClaimsRegisterPage: escalating several claims", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    dialog.props = null;
  });

  it("offers a checkbox for each claim that can be escalated, and escalates the chosen ones together", async () => {
    api.getClaims.mockResolvedValue([
      claim("clm-1"),
      claim("clm-2"),
      claim("clm-3", { status: "closed" }),
      claim("clm-4", { evidence_frozen_at: "2026-11-01T00:00:00" }),
    ]);
    render(<MemoryRouter><ClaimsRegisterPage /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select CLM-1 for escalation" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select CLM-2 for escalation" }));
    expect(screen.queryByRole("checkbox", { name: "Select CLM-3 for escalation" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select CLM-4 for escalation" })).not.toBeInTheDocument();
    const bar = screen.getByRole("region", { name: "Selected claims" });
    expect(within(bar).getByText("2 claims selected")).toBeInTheDocument();
    await userEvent.click(within(bar).getByRole("button", { name: "Escalate selected to arbitration" }));
    expect(screen.getByText("escalate dialog open")).toBeInTheDocument();
    expect(dialog.props?.claimIds).toEqual(["clm-1", "clm-2"]);
    expect(dialog.props?.projectId).toBe("proj-A1");
  });

  it("will not escalate claims of different projects together", async () => {
    api.getClaims.mockResolvedValue([claim("clm-1"), claim("clm-2", { project_id: "proj-A2" })]);
    render(<MemoryRouter><ClaimsRegisterPage /></MemoryRouter>);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select CLM-1 for escalation" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select CLM-2 for escalation" }));
    expect(screen.getByRole("button", { name: "Escalate selected to arbitration" })).toBeDisabled();
    expect(screen.getByText("Claims escalated together must belong to one project.")).toBeInTheDocument();
  });
});
