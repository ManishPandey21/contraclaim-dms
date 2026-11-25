import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 4B-08: the reviewer assignment admin screen. Platform scope is offered to a
 * Super Admin only; an organisation admin works within the selected organisation.
 */
const api = vi.hoisted(() => ({
  fetchReviewerAssignments: vi.fn(),
  grantReviewerAssignment: vi.fn(),
  revokeReviewerAssignment: vi.fn(),
  fetchLegacyReviewerRoles: vi.fn(),
}));
const session = vi.hoisted(() => ({ getCurrentUserProfile: vi.fn() }));
const tenant = vi.hoisted(() => ({ value: { selectedOrganizationId: "org-a", selectedProjectId: "" } }));
vi.mock("@/services/reviewer-assignments-api", () => api);
vi.mock("@/services/session-api", () => session);
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => tenant.value }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: {
    getUsers: vi.fn().mockResolvedValue([
      { id: "u-asha", first_name: "Asha", last_name: "Rao" },
      { id: "u-vikram", first_name: "Vikram", last_name: "Shah" },
    ]),
  },
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import ReviewerAssignmentsPage from "../ReviewerAssignmentsPage";

const held = {
  id: "a1",
  user_id: "u-vikram",
  user_name: "Vikram Shah",
  role_key: "quantum_reviewer",
  role_label: "Quantum reviewer",
  scope_type: "organization",
  organization_id: "org-a",
  granted_by: "u-admin",
  reason: "Quantum lead for the metro cases",
  granted_at: "2026-11-01T00:00:00Z",
  expires_at: "2027-10-31T23:59:59Z",
  state: "active",
};

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ReviewerAssignmentsPage />
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  tenant.value = { selectedOrganizationId: "org-a", selectedProjectId: "" };
  session.getCurrentUserProfile.mockResolvedValue({ id: "u-admin", roles: ["orgadmin"] });
  api.fetchReviewerAssignments.mockResolvedValue([held]);
  api.fetchLegacyReviewerRoles.mockResolvedValue({
    holders: [
      { user_id: "u-asha", user_name: "Asha Rao", role_key: "legal_reviewer", has_assignment: false },
      { user_id: "u-vikram", user_name: "Vikram Shah", role_key: "quantum_reviewer", has_assignment: true },
    ],
    counts_by_role: { legal_reviewer: 1, quantum_reviewer: 1 },
  });
  api.grantReviewerAssignment.mockResolvedValue({ ...held, id: "a2", user_name: "Asha Rao", role_label: "Legal reviewer" });
});

describe("ReviewerAssignmentsPage", () => {
  it("offers an organisation admin their organisation and not the platform", async () => {
    renderPage();
    expect(await screen.findByText("Vikram Shah · Quantum reviewer")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "This organisation" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Platform" })).not.toBeInTheDocument();
    expect(api.fetchReviewerAssignments).toHaveBeenCalledWith({
      scope_type: "organization",
      organization_id: "org-a",
      project_id: undefined,
      include_inactive: false,
    });
    // People and roles are shown by name; no record id is on the page.
    expect(document.body.textContent).not.toMatch(/u-vikram|u-admin|\ba1\b|quantum_reviewer/);
  });

  it("offers a Super Admin the platform, with skill reviewer roles only and no required end date", async () => {
    session.getCurrentUserProfile.mockResolvedValue({ id: "u-root", roles: ["Super Admin"] });
    renderPage();
    const platform = await screen.findByRole("tab", { name: "Platform" });
    await waitFor(() => expect(platform).toHaveAttribute("aria-selected", "true"));
    const roles = within(screen.getByLabelText("Reviewer role")).getAllByRole("option").map((option) => option.textContent);
    expect(roles).toEqual([
      "Choose a role",
      "Chief contract expert",
      "Delay and planning expert",
      "Arbitration case preparation expert",
      "Senior legal approver",
    ]);
    expect(screen.getByLabelText("Ends on (optional)")).toBeInTheDocument();
  });

  it("grants a role only once a person, a role and a reason are given, and sends the scope the server will check", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Vikram Shah · Quantum reviewer");
    const grant = screen.getByRole("button", { name: "Grant role" });
    expect(grant).toBeDisabled();
    expect(screen.getByText("Choose a person and a role, give a reason and an end date.")).toBeInTheDocument();

    await user.selectOptions(await screen.findByLabelText("Person"), "u-asha");
    await user.selectOptions(screen.getByLabelText("Reviewer role"), "legal_reviewer");
    await user.type(screen.getByLabelText("Reason"), "ok");
    expect(grant).toBeDisabled();
    await user.type(screen.getByLabelText("Reason"), " - legal lead for the metro cases");
    expect(grant).toBeEnabled();
    await user.click(grant);

    await waitFor(() => expect(api.grantReviewerAssignment).toHaveBeenCalledTimes(1));
    const sent = api.grantReviewerAssignment.mock.calls[0][0];
    expect(sent).toMatchObject({
      scope_type: "organization",
      organization_id: "org-a",
      user_id: "u-asha",
      role_key: "legal_reviewer",
      reason: "ok - legal lead for the metro cases",
    });
    expect(sent.expires_at).toMatch(/^\d{4}-\d{2}-\d{2}T23:59:59Z$/);
  });

  it("withdraws with a reason and refuses a blank one", async () => {
    const user = userEvent.setup();
    const prompt = vi.spyOn(window, "prompt");
    api.revokeReviewerAssignment.mockResolvedValue({ ...held, state: "revoked" });
    renderPage();
    const withdraw = await screen.findByRole("button", { name: "Withdraw Quantum reviewer from Vikram Shah" });

    prompt.mockReturnValueOnce("  ");
    await user.click(withdraw);
    expect(api.revokeReviewerAssignment).not.toHaveBeenCalled();

    prompt.mockReturnValueOnce("Left the panel");
    await user.click(withdraw);
    await waitFor(() => expect(api.revokeReviewerAssignment).toHaveBeenCalledWith("a1", "Left the panel"));
    prompt.mockRestore();
  });

  it("lists people who hold a reviewer role without a grant, and only those", async () => {
    renderPage();
    const section = (await screen.findByRole("heading", { name: "Held without a grant" })).closest("section")!;
    expect(within(section).getByText("Asha Rao: Legal reviewer")).toBeInTheDocument();
    expect(within(section).queryByText(/Vikram Shah/)).not.toBeInTheDocument();
  });

  it("asks for an organisation when none is selected and the user is not a Super Admin", async () => {
    tenant.value = { selectedOrganizationId: "", selectedProjectId: "" };
    renderPage();
    expect(await screen.findByText("Select an organisation to see and grant its reviewer roles.")).toBeInTheDocument();
    expect(api.fetchReviewerAssignments).not.toHaveBeenCalled();
  });
});
