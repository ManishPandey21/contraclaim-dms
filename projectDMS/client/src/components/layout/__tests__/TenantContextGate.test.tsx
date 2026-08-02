import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TenantContextGate from "../TenantContextGate";
import { isContextFreeRoute } from "../contextFreeRoutes";

const { useTenant } = vi.hoisted(() => ({ useTenant: vi.fn() }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant }));

function tenant(overrides: Record<string, unknown> = {}) {
  return {
    contextReady: false,
    requiresSelection: true,
    loading: false,
    error: null,
    roleTier: "global",
    selectedOrganizationId: "",
    projects: [],
    ...overrides,
  };
}

describe("TenantContextGate", () => {
  it("blocks project-scoped children until a context exists", () => {
    useTenant.mockReturnValue(tenant());
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );

    expect(
      screen.getByText("Please select an Organisation and Project to continue."),
    ).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
  });

  it("offers a direct action to complete the selection", () => {
    useTenant.mockReturnValue(tenant());
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByRole("button", { name: "Select Organisation" })).toBeInTheDocument();
  });

  it("asks for a project once the organisation is chosen", () => {
    useTenant.mockReturnValue(
      tenant({ selectedOrganizationId: "org-1", projects: [{ _id: "p1" }, { _id: "p2" }] }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByRole("button", { name: "Select Project" })).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
  });

  it("renders children once the context is complete", () => {
    useTenant.mockReturnValue(
      tenant({ contextReady: true, requiresSelection: false, selectedOrganizationId: "org-1" }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByText("project data")).toBeInTheDocument();
  });

  it("shows a loading state rather than an empty page", () => {
    useTenant.mockReturnValue(tenant({ loading: true }));
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByRole("status", { name: "Loading organisation and project" })).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
  });

  it("surfaces an unauthorised/load failure instead of children", () => {
    useTenant.mockReturnValue(tenant({ error: "Organisation and project details could not be loaded." }));
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
  });

  it("explains a project-role account with no assigned project", () => {
    useTenant.mockReturnValue(
      tenant({ roleTier: "project", selectedOrganizationId: "org-1", projects: [] }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByText("No project available")).toBeInTheDocument();
    expect(screen.getByText(/No active project is assigned/)).toBeInTheDocument();
  });

  it("explains an organisation with no accessible projects", () => {
    useTenant.mockReturnValue(
      tenant({ roleTier: "org", selectedOrganizationId: "org-1", projects: [] }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByText(/no active projects you can access/)).toBeInTheDocument();
  });
});

describe("isContextFreeRoute", () => {
  it.each(["/", "/overview", "/organizations", "/projects", "/profile", "/settings", "/admin/legal-words"])(
    "treats %s as context-free",
    (path) => expect(isContextFreeRoute(path)).toBe(true),
  );

  it.each(["/documents", "/letters", "/claims", "/reports", "/letters/abc/draft", "/key-dates"])(
    "treats %s as project-scoped",
    (path) => expect(isContextFreeRoute(path)).toBe(false),
  );

  it("gates unknown new routes by default", () => {
    expect(isContextFreeRoute("/some-future-page")).toBe(false);
  });
});
