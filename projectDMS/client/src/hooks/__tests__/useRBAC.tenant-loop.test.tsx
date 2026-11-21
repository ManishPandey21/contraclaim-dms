import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

/**
 * Regression for the independent review of 2026-11-19 (Critical): a permission guard that
 * shows a skeleton while useRBAC is loading, around a layout that mounts TenantProvider,
 * remounted forever - the provider announced its scope on mount, the hook cleared to
 * loading, the guard unmounted the layout, and so on (over a million /me calls in 20 ms).
 * Real useRBAC and real TenantProvider; only the network is mocked.
 */

const mocks = vi.hoisted(() => ({
  getCurrentUserProfile: vi.fn(),
  listOrganizations: vi.fn(),
  listProjects: vi.fn(),
  setActiveScope: vi.fn(),
}));

vi.mock("@/services/session-api", () => ({ __esModule: true, getCurrentUserProfile: mocks.getCurrentUserProfile }));
vi.mock("@/services/projects-api", () => ({ __esModule: true, listProjects: mocks.listProjects }));
vi.mock("@/services/active-scope", () => ({ __esModule: true, setActiveScope: mocks.setActiveScope }));
vi.mock("@/services/organizations-api", () => ({ __esModule: true, listOrganizations: mocks.listOrganizations }));

import { TenantProvider, useTenant } from "@/contexts/TenantContext";
import { useRBAC } from "../useRBAC";

let layoutMounts = 0;

function Layout() {
  React.useEffect(() => {
    layoutMounts += 1;
  }, []);
  const tenant = useTenant();
  return <div data-testid="scope">{tenant.selectedOrganizationId || "none"}</div>;
}

function Guard() {
  const { loading } = useRBAC();
  if (loading) return <div>skeleton</div>;
  return (
    <TenantProvider>
      <Layout />
    </TenantProvider>
  );
}

describe("useRBAC inside a guard around TenantProvider", () => {
  it("settles: one layout mount and a bounded number of profile reads", async () => {
    mocks.getCurrentUserProfile.mockResolvedValue({
      roles: ["orgadmin"],
      organization_id: "org-1",
      permissions: ["dms.document.view"],
      projects: [],
    });
    mocks.listOrganizations.mockResolvedValue([{ _id: "org-1", name: "Acme" }]);
    mocks.listProjects.mockResolvedValue([{ _id: "project-1", name: "North", organization_id: "org-1" }]);

    render(<Guard />);
    await waitFor(() => expect(screen.getByTestId("scope")).toHaveTextContent("org-1"));
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(layoutMounts).toBe(1);
    expect(mocks.getCurrentUserProfile.mock.calls.length).toBeLessThanOrEqual(3);
  });
});
