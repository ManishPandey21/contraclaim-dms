import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TenantContextGate from "../TenantContextGate";
import { isContextFreeRoute } from "../contextFreeRoutes";

const { useTenant } = vi.hoisted(() => ({ useTenant: vi.fn() }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant }));

function tenant(overrides: Record<string, unknown> = {}) {
  return {
    contextReady: false,
    hasNoAccessibleScope: false,
    loading: false,
    error: null,
    roleTier: "global",
    selectedOrganizationId: "",
    projects: [],
    ...overrides,
  };
}

describe("TenantContextGate", () => {
  it("renders children when nothing is selected (consolidated scope)", () => {
    useTenant.mockReturnValue(tenant({ contextReady: true }));
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    // No selection means the widest permitted scope, not a blocked page.
    expect(screen.getByText("project data")).toBeInTheDocument();
  });

  it("renders children when only an organisation is selected", () => {
    useTenant.mockReturnValue(tenant({ contextReady: true, selectedOrganizationId: "org-1" }));
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

  it("blocks a project-role account with no assigned project", () => {
    useTenant.mockReturnValue(
      tenant({ roleTier: "project", hasNoAccessibleScope: true, contextReady: false }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByText("No accessible data")).toBeInTheDocument();
    expect(screen.getByText(/No active project is assigned/)).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
  });

  it("blocks a non-global account with no organisation", () => {
    useTenant.mockReturnValue(
      tenant({ roleTier: "org", hasNoAccessibleScope: true, contextReady: false }),
    );
    render(
      <TenantContextGate>
        <p>project data</p>
      </TenantContextGate>,
    );
    expect(screen.getByText(/No active organisation is assigned/)).toBeInTheDocument();
    expect(screen.queryByText("project data")).not.toBeInTheDocument();
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
