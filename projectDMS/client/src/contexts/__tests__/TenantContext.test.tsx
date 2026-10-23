import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SELECTION_STORAGE_KEY, TenantProvider, useTenant } from "../TenantContext";
import { serializeSelection } from "@/services/active-scope";

const { getCurrentUserProfile, listOrganizations, listProjects } = vi.hoisted(
  () => ({
    getCurrentUserProfile: vi.fn(),
    listOrganizations: vi.fn(),
    listProjects: vi.fn(),
  }),
);

vi.mock("@/services/session-api", () => ({ getCurrentUserProfile }));
vi.mock("@/services/organizations-api", () => ({ listOrganizations }));
vi.mock("@/services/projects-api", () => ({ listProjects }));

const organizations = [{ _id: "org-1", name: "Acme Infrastructure" }];
const projects = [
  { _id: "project-1", name: "North Corridor", organization_id: "org-1" },
  { _id: "project-2", name: "South Corridor", organization_id: "org-1" },
];

function TenantProbe() {
  const tenant = useTenant();
  return (
    <div>
      <span>{tenant.selectedOrganization?.name}</span>
      <span>{tenant.selectedProject?.name}</span>
      <span data-testid="can-switch">{String(tenant.canSwitchProject)}</span>
      <button type="button" onClick={() => tenant.selectProject("project-2")}>
        Switch project
      </button>
    </div>
  );
}

describe("TenantProvider", () => {
  beforeEach(() => {
    const storage = new Map<string, string>();
    vi.mocked(window.localStorage.getItem).mockImplementation(
      (key) => storage.get(key) ?? null,
    );
    vi.mocked(window.localStorage.setItem).mockImplementation((key, value) => {
      storage.set(key, String(value));
    });
    vi.mocked(window.localStorage.removeItem).mockImplementation((key) => {
      storage.delete(key);
    });
    vi.mocked(window.localStorage.clear).mockImplementation(() => storage.clear());
    window.localStorage.clear();
    vi.clearAllMocks();
    listOrganizations.mockResolvedValue(organizations);
    listProjects.mockResolvedValue(projects);
  });

  it("shows names and lets an organisation-level user switch projects", async () => {
    getCurrentUserProfile.mockResolvedValue({
      roles: ["orgadmin"],
      organization_id: "org-1",
    });

    render(
      <TenantProvider>
        <TenantProbe />
      </TenantProvider>,
    );

    expect(await screen.findByText("Acme Infrastructure")).toBeInTheDocument();
    // Organisation-tier default is All Projects (owner policy 2026-10-06).
    await waitFor(() => expect(screen.getByTestId("can-switch")).toHaveTextContent("true"));
    expect(screen.queryByText("North Corridor")).not.toBeInTheDocument();

    act(() => screen.getByRole("button", { name: "Switch project" }).click());

    await waitFor(() => {
      expect(screen.getByText("South Corridor")).toBeInTheDocument();
      expect(window.localStorage.getItem("org_id")).toBe("org-1");
      expect(window.localStorage.getItem("proj_id")).toBe("project-2");
    });
  });

  it("does not expose project switching to a project-level user", async () => {
    getCurrentUserProfile.mockResolvedValue({
      roles: ["projectuser"],
      organization_id: "org-1",
      projects: ["project-1", "project-2"],
    });

    render(
      <TenantProvider>
        <TenantProbe />
      </TenantProvider>,
    );

    expect(await screen.findByText("North Corridor")).toBeInTheDocument();
    expect(screen.getByTestId("can-switch")).toHaveTextContent("false");

    act(() => screen.getByRole("button", { name: "Switch project" }).click());
    expect(screen.queryByText("South Corridor")).not.toBeInTheDocument();
  });

  it("restores a valid persisted project selection", async () => {
    window.localStorage.setItem(
      SELECTION_STORAGE_KEY,
      serializeSelection({ organizationId: "org-1", projectId: "project-2" }),
    );
    getCurrentUserProfile.mockResolvedValue({
      roles: ["orguser"],
      organization_id: "org-1",
    });

    render(
      <TenantProvider>
        <TenantProbe />
      </TenantProvider>,
    );

    expect(await screen.findByText("South Corridor")).toBeInTheDocument();
  });
});
