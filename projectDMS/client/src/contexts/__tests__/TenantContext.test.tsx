import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TenantProvider, resolveRoleTier, useTenant } from "../TenantContext";

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

const organizations = [
  { _id: "org-1", name: "Acme Infrastructure" },
  { _id: "org-2", name: "Borealis Rail" },
];
const projects = [
  { _id: "project-1", name: "North Corridor", organization_id: "org-1" },
  { _id: "project-2", name: "South Corridor", organization_id: "org-1" },
  { _id: "project-3", name: "Coastal Link", organization_id: "org-2" },
];

function TenantProbe() {
  const tenant = useTenant();
  return (
    <div>
      <span data-testid="org">{tenant.selectedOrganization?.name ?? "-"}</span>
      <span data-testid="project">{tenant.selectedProject?.name ?? "-"}</span>
      <span data-testid="can-switch-org">{String(tenant.canSwitchOrganization)}</span>
      <span data-testid="can-switch-project">{String(tenant.canSwitchProject)}</span>
      <span data-testid="org-locked">{String(tenant.organizationLocked)}</span>
      <span data-testid="requires-selection">{String(tenant.requiresSelection)}</span>
      <span data-testid="context-ready">{String(tenant.contextReady)}</span>
      <span data-testid="tier">{tenant.roleTier}</span>
      <span data-testid="project-count">{String(tenant.projects.length)}</span>
      <button type="button" onClick={() => tenant.selectProject("project-2")}>
        Switch project
      </button>
      <button type="button" onClick={() => tenant.selectOrganization("org-2")}>
        Switch organisation
      </button>
    </div>
  );
}

function renderTenant() {
  render(
    <TenantProvider>
      <TenantProbe />
    </TenantProvider>,
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

  describe("role tier", () => {
    it.each([
      [["superadmin"], "global"],
      [["superuser"], "global"],
      [["orgadmin"], "org"],
      [["orguser"], "org"],
      [["projectadmin"], "project"],
      [["projectuser"], "project"],
      [["reporter"], "restricted"],
    ])("classifies %s as %s", (roles, expected) => {
      expect(resolveRoleTier(roles as string[])).toBe(expected);
    });
  });

  describe("organisation-level user", () => {
    it("locks the organisation and requires a project choice when several exist", async () => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure"),
      );
      // Two projects in org-1: the user must choose rather than be given one.
      expect(screen.getByTestId("project")).toHaveTextContent("-");
      expect(screen.getByTestId("requires-selection")).toHaveTextContent("true");
      expect(screen.getByTestId("context-ready")).toHaveTextContent("false");
      expect(screen.getByTestId("can-switch-org")).toHaveTextContent("false");
      expect(screen.getByTestId("org-locked")).toHaveTextContent("true");
      expect(screen.getByTestId("can-switch-project")).toHaveTextContent("true");
    });

    it("auto-selects when the organisation has exactly one project", async () => {
      listProjects.mockResolvedValue([projects[0]]);
      getCurrentUserProfile.mockResolvedValue({ roles: ["orguser"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("North Corridor"),
      );
      expect(screen.getByTestId("context-ready")).toHaveTextContent("true");
      expect(screen.getByTestId("can-switch-project")).toHaveTextContent("false");
    });

    it("never lists projects from another organisation", async () => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("project-count")).toHaveTextContent("2"));
      expect(screen.queryByText("Coastal Link")).not.toBeInTheDocument();
    });

    it("cannot switch organisation even when others exist", async () => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure"),
      );
      act(() => screen.getByRole("button", { name: "Switch organisation" }).click());
      expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure");
    });

    it("persists a chosen project", async () => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("tier")).toHaveTextContent("org"));
      act(() => screen.getByRole("button", { name: "Switch project" }).click());

      await waitFor(() => {
        expect(screen.getByTestId("project")).toHaveTextContent("South Corridor");
        expect(window.localStorage.getItem("proj_id")).toBe("project-2");
      });
    });

    it("restores a valid persisted project selection", async () => {
      window.localStorage.setItem("org_id", "org-1");
      window.localStorage.setItem("proj_id", "project-2");
      getCurrentUserProfile.mockResolvedValue({ roles: ["orguser"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("South Corridor"),
      );
    });

    it("discards a persisted project that belongs to another organisation", async () => {
      window.localStorage.setItem("org_id", "org-1");
      window.localStorage.setItem("proj_id", "project-3"); // org-2
      getCurrentUserProfile.mockResolvedValue({ roles: ["orguser"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("tier")).toHaveTextContent("org"));
      expect(screen.getByTestId("project")).toHaveTextContent("-");
      expect(window.localStorage.getItem("proj_id")).toBeNull();
    });
  });

  describe("project-level user", () => {
    it("locks both values to the single assignment", async () => {
      getCurrentUserProfile.mockResolvedValue({
        roles: ["projectuser"],
        organization_id: "org-1",
        projects: ["project-1"],
      });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("North Corridor"),
      );
      expect(screen.getByTestId("can-switch-org")).toHaveTextContent("false");
      expect(screen.getByTestId("can-switch-project")).toHaveTextContent("false");
      expect(screen.getByTestId("context-ready")).toHaveTextContent("true");
    });

    it("only lists assigned projects, not every project in the organisation", async () => {
      getCurrentUserProfile.mockResolvedValue({
        roles: ["projectuser"],
        organization_id: "org-1",
        projects: ["project-1"],
      });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("project-count")).toHaveTextContent("1"));
      expect(screen.queryByText("South Corridor")).not.toBeInTheDocument();
    });

    it("cannot select an unassigned project in its own organisation", async () => {
      getCurrentUserProfile.mockResolvedValue({
        roles: ["projectuser"],
        organization_id: "org-1",
        projects: ["project-1"],
      });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("North Corridor"),
      );
      act(() => screen.getByRole("button", { name: "Switch project" }).click());
      expect(screen.getByTestId("project")).toHaveTextContent("North Corridor");
    });

    it("may choose between its own assignments when it has several", async () => {
      getCurrentUserProfile.mockResolvedValue({
        roles: ["projectuser"],
        organization_id: "org-1",
        projects: ["project-1", "project-2"],
      });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("project-count")).toHaveTextContent("2"));
      expect(screen.getByTestId("can-switch-project")).toHaveTextContent("true");
      act(() => screen.getByRole("button", { name: "Switch project" }).click());
      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("South Corridor"),
      );
    });
  });

  describe("global roles", () => {
    it("requires an explicit organisation choice and blocks until complete", async () => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], organization_id: null });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("tier")).toHaveTextContent("global"));
      expect(screen.getByTestId("org")).toHaveTextContent("-");
      expect(screen.getByTestId("project")).toHaveTextContent("-");
      expect(screen.getByTestId("requires-selection")).toHaveTextContent("true");
      expect(screen.getByTestId("can-switch-org")).toHaveTextContent("true");
    });

    it("clears the selected project when the organisation changes", async () => {
      window.localStorage.setItem("org_id", "org-1");
      window.localStorage.setItem("proj_id", "project-2");
      getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], organization_id: null });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("South Corridor"),
      );

      act(() => screen.getByRole("button", { name: "Switch organisation" }).click());

      await waitFor(() => {
        expect(screen.getByTestId("org")).toHaveTextContent("Borealis Rail");
        // Project cleared in state *and* in storage, so legacy pages reading
        // proj_id directly cannot paint the previous project's data.
        expect(screen.getByTestId("project")).toHaveTextContent("-");
        expect(window.localStorage.getItem("proj_id")).toBeNull();
        expect(screen.getByTestId("context-ready")).toHaveTextContent("false");
      });
    });

    it("auto-selects when only one organisation is reachable", async () => {
      listOrganizations.mockResolvedValue([organizations[0]]);
      listProjects.mockResolvedValue([projects[0]]);
      getCurrentUserProfile.mockResolvedValue({ roles: ["superuser"], organization_id: null });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure"),
      );
      expect(screen.getByTestId("project")).toHaveTextContent("North Corridor");
      expect(screen.getByTestId("context-ready")).toHaveTextContent("true");
    });

    it("discards a persisted organisation the user can no longer reach", async () => {
      window.localStorage.setItem("org_id", "org-9");
      getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], organization_id: null });
      renderTenant();

      await waitFor(() => expect(screen.getByTestId("tier")).toHaveTextContent("global"));
      expect(screen.getByTestId("org")).toHaveTextContent("-");
    });
  });

  describe("deactivated records", () => {
    it("excludes deactivated projects from the selectable list", async () => {
      listProjects.mockResolvedValue([
        projects[0],
        { ...projects[1], is_active: false },
      ]);
      getCurrentUserProfile.mockResolvedValue({ roles: ["orgadmin"], organization_id: "org-1" });
      renderTenant();

      // Only one project survives, so it is auto-selected.
      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("North Corridor"),
      );
      expect(screen.getByTestId("project-count")).toHaveTextContent("1");
    });

    it("drops a persisted project that has since been deactivated", async () => {
      window.localStorage.setItem("org_id", "org-1");
      window.localStorage.setItem("proj_id", "project-2");
      listProjects.mockResolvedValue([
        projects[0],
        { ...projects[1], is_active: false },
      ]);
      getCurrentUserProfile.mockResolvedValue({ roles: ["orguser"], organization_id: "org-1" });
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("project")).toHaveTextContent("North Corridor"),
      );
    });
  });

  describe("failure states", () => {
    it("surfaces an error when scope cannot be loaded", async () => {
      getCurrentUserProfile.mockRejectedValue(new Error("nope"));
      renderTenant();

      await waitFor(() =>
        expect(screen.getByTestId("context-ready")).toHaveTextContent("false"),
      );
      expect(screen.getByTestId("requires-selection")).toHaveTextContent("false");
    });
  });
});
