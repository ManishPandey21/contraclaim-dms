import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  path: "/claims",
  entitlementError: null as string | null,
  entitlementLoading: false,
  hasFeature: vi.fn<(feature: string) => boolean>(),
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ isAuthenticated: true, isAuthLoading: false }),
}));

vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({
    roles: ["orguser"],
    can: () => true,
    loading: false,
    error: null,
  }),
}));

vi.mock("@/hooks/useEntitlements", () => ({
  default: () => ({
    entitlements: {
      features: {},
      unavailable_reason: null,
    },
    loading: state.entitlementLoading,
    error: state.entitlementError,
    hasFeature: state.hasFeature,
    refresh: vi.fn(),
  }),
}));

vi.mock("@/services/security-terms-api", () => ({
  getSecurityTermsStatus: vi.fn().mockResolvedValue({ requires_acceptance: false }),
}));

import ProtectedRoute from "../ProtectedRoute";


function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ProtectedRoute>
        <div>protected page content</div>
      </ProtectedRoute>
    </MemoryRouter>,
  );
}


describe("ProtectedRoute subscription UX", () => {
  beforeEach(() => {
    state.entitlementError = null;
    state.entitlementLoading = false;
    state.hasFeature.mockReset();
    state.hasFeature.mockReturnValue(false);
  });

  it("shows the plan-unavailable state for a permitted commercial route", async () => {
    renderRoute("/claims");

    expect(
      await screen.findByRole("heading", { name: "Not included in the current plan" }),
    ).toBeVisible();
    expect(screen.queryByText("protected page content")).not.toBeInTheDocument();
    expect(screen.getByText(/feature\.dms\.claims/)).toBeVisible();
  });

  it("fails closed when current plan status cannot be verified", async () => {
    state.entitlementError = "entitlements_unavailable";
    renderRoute("/claims");

    expect(
      await screen.findByRole("heading", { name: "Plan status unavailable" }),
    ).toBeVisible();
    expect(screen.queryByText("protected page content")).not.toBeInTheDocument();
  });

  it("does not block non-commercial account pages on entitlement service failure", async () => {
    state.entitlementError = "entitlements_unavailable";
    renderRoute("/profile");

    await waitFor(() => expect(screen.getByText("protected page content")).toBeVisible());
    expect(screen.queryByText("Plan status unavailable")).not.toBeInTheDocument();
  });
});
