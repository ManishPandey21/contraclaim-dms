import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  path: "/claims",
  entitlementError: null as string | null,
  entitlementLoading: false,
  hasFeature: vi.fn<(feature: string) => boolean>(),
  //: null means "grant every permission" (the default for the plan-state
  //: tests); a Set restricts the caller to exactly those permissions.
  permissions: null as Set<string> | null,
}));

vi.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({ isAuthenticated: true, isAuthLoading: false }),
}));

vi.mock("@/hooks/useRBAC", () => ({
  default: () => ({
    roles: ["orguser"],
    can: (permission: string) =>
      state.permissions === null ? true : state.permissions.has(permission),
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
    state.permissions = null;
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

  // M-05: document-view must not be a master key for paid modules.
  describe("document-view permission does not unlock commercial modules", () => {
    const COMMERCIAL_ROUTES = [
      "/claims",
      "/contracts/appraisal",
      "/arbitration/cases",
      "/letters",
    ];

    for (const path of COMMERCIAL_ROUTES) {
      it(`denies ${path} to a document-view-only user even when the plan grants every feature`, async () => {
        state.permissions = new Set(["dms.document.view"]);
        // Entitlement is deliberately wide open: if the page still renders, the
        // permission check -- not the plan -- is what failed.
        state.hasFeature.mockReturnValue(true);

        renderRoute(path);

        // Permission denial must present as access denied, never as an upgrade
        // prompt, so the user is not told to buy something they already have.
        expect(await screen.findByText("Access unavailable")).toBeVisible();
        expect(screen.queryByText("protected page content")).not.toBeInTheDocument();
        expect(
          screen.queryByRole("heading", { name: "Not included in the current plan" }),
        ).not.toBeInTheDocument();
      });
    }

    it("still allows the document module the permission actually grants", async () => {
      state.permissions = new Set(["dms.document.view"]);
      state.hasFeature.mockReturnValue(true);

      renderRoute("/documents");

      await waitFor(() => expect(screen.getByText("protected page content")).toBeVisible());
    });

    it("separates plan denial from permission denial on the same route", async () => {
      // Correct permission, but the plan does not include the feature.
      state.permissions = new Set(["dms.claim.view", "dms.document.view"]);
      state.hasFeature.mockReturnValue(false);

      renderRoute("/claims");

      expect(
        await screen.findByRole("heading", { name: "Not included in the current plan" }),
      ).toBeVisible();
    });
  });
});
