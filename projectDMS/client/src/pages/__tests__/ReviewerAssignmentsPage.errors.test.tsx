import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Gate 3 bullet 8: the reviewer roles page when its API fails. The list failing
 * shows a plain message (no blank page, no list); a failed grant is reported and
 * nothing is shown as granted.
 */
const api = vi.hoisted(() => ({
  fetchReviewerAssignments: vi.fn(),
  grantReviewerAssignment: vi.fn(),
  revokeReviewerAssignment: vi.fn(),
  fetchLegacyReviewerRoles: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("@/services/reviewer-assignments-api", () => api);
vi.mock("@/services/session-api", () => ({ getCurrentUserProfile: vi.fn().mockResolvedValue({ id: "u-admin", roles: ["orgadmin"] }) }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant: () => ({ selectedOrganizationId: "org-a", selectedProjectId: "" }) }));
vi.mock("@/services/enhanced-api", () => ({
  enhancedApi: { getUsers: vi.fn().mockResolvedValue([{ id: "u-asha", first_name: "Asha", last_name: "Rao" }]) },
}));
vi.mock("sonner", () => ({ toast }));

import ReviewerAssignmentsPage from "../ReviewerAssignmentsPage";

const failure = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail } },
  });

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ReviewerAssignmentsPage />
    </QueryClientProvider>
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.fetchLegacyReviewerRoles.mockResolvedValue({ holders: [], counts_by_role: {} });
  api.fetchReviewerAssignments.mockResolvedValue([]);
});

describe("ReviewerAssignmentsPage error states", () => {
  it("says the list could not be loaded when the server fails, and shows no list", async () => {
    api.fetchReviewerAssignments.mockRejectedValue(failure(500, "Internal Server Error"));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent?.trim()).not.toBe("");
    expect(screen.queryByText("Nobody holds a reviewer role at this scope.")).not.toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("shows a refusal from the server as a message, not a blank page", async () => {
    api.fetchReviewerAssignments.mockRejectedValue(failure(403, "Not authorized: missing_permission"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who holds a reviewer role here" })).toBeInTheDocument();
  });

  it("still renders when the legacy-role reading fails", async () => {
    api.fetchLegacyReviewerRoles.mockRejectedValue(failure(500, "Internal Server Error"));
    renderPage();
    expect(await screen.findByText("Nobody holds a reviewer role at this scope.")).toBeInTheDocument();
  });

  it("reports a refused grant and adds nobody", async () => {
    api.grantReviewerAssignment.mockRejectedValue(failure(409, "This person already holds this role here"));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Nobody holds a reviewer role at this scope.");
    await user.selectOptions(await screen.findByLabelText("Person"), "u-asha");
    await user.selectOptions(screen.getByLabelText("Reviewer role"), "legal_reviewer");
    await user.type(screen.getByLabelText("Reason"), "Legal lead for the metro cases");
    await user.click(screen.getByRole("button", { name: "Grant role" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByText("Nobody holds a reviewer role at this scope.")).toBeInTheDocument();
  });
});
