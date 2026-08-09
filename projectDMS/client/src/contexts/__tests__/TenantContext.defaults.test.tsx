import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TenantProvider, useTenant } from "../TenantContext";

/**
 * The default-selection matrix and the "All Organisations" rule.
 *
 * The navbar is the working context; RBAC is the maximum boundary. These cover
 * the two halves the existing suite does not: the login default each role
 * lands on, and the rule that a specific project can never be selected while
 * All Organisations is active -- the frontend half of the server's 403.
 *
 * The frontend is not a security boundary. Every expectation here is a
 * usability and consistency guarantee; the backend re-derives scope on every
 * request regardless of what this component believes.
 */

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

// org-1 deliberately holds two projects so "All Projects" is a real state
// rather than an artefact of there being only one option.
const projects = [
  { _id: "project-1", name: "North Corridor", organization_id: "org-1" },
  { _id: "project-2", name: "South Corridor", organization_id: "org-1" },
  { _id: "project-3", name: "Coastal Link", organization_id: "org-2" },
];

function Probe() {
  const tenant = useTenant();
  return (
    <div>
      <span data-testid="org">{tenant.selectedOrganization?.name ?? "ALL_ORGANISATIONS"}</span>
      <span data-testid="project">{tenant.selectedProject?.name ?? "ALL_PROJECTS"}</span>
      <span data-testid="can-switch-org">{String(tenant.canSwitchOrganization)}</span>
      <span data-testid="can-switch-project">{String(tenant.canSwitchProject)}</span>
      <button type="button" onClick={() => tenant.selectProject("project-1")}>
        Pick project
      </button>
      <button type="button" onClick={() => tenant.selectOrganization("")}>
        All organisations
      </button>
      <button type="button" onClick={() => tenant.selectOrganization("org-1")}>
        Pick org-1
      </button>
    </div>
  );
}

function renderTenant() {
  render(
    <TenantProvider>
      <Probe />
    </TenantProvider>,
  );
}

async function settled() {
  await waitFor(() => expect(screen.getByTestId("org")).toBeInTheDocument());
}

describe("navbar default selection matrix", () => {
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

  it("lands a superadmin on All Organisations and All Projects", async () => {
    getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], projects: [] });
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("ALL_ORGANISATIONS");
    });
    expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
    expect(screen.getByTestId("can-switch-org")).toHaveTextContent("true");
  });

  it("lands a superuser on All Accessible Organisations", async () => {
    getCurrentUserProfile.mockResolvedValue({ roles: ["superuser"], projects: [] });
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("ALL_ORGANISATIONS");
    });
    expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
  });

  it("lands an organisation admin on their organisation with All Projects", async () => {
    getCurrentUserProfile.mockResolvedValue({
      roles: ["orgadmin"],
      organization_id: "org-1",
      projects: [],
    });
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure");
    });
    // org-1 has two projects, so the default is genuinely "All Projects".
    expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
    expect(screen.getByTestId("can-switch-org")).toHaveTextContent("false");
  });

  it("lands a multi-project project user on All Accessible Projects", async () => {
    getCurrentUserProfile.mockResolvedValue({
      roles: ["projectuser"],
      organization_id: "org-1",
      projects: ["project-1", "project-2"],
    });
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("Acme Infrastructure");
    });
    expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
    expect(screen.getByTestId("can-switch-project")).toHaveTextContent("true");
  });

  it("pins a single-project project user to that project", async () => {
    getCurrentUserProfile.mockResolvedValue({
      roles: ["projectuser"],
      organization_id: "org-1",
      projects: ["project-1"],
    });
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("project")).toHaveTextContent("North Corridor");
    });
    expect(screen.getByTestId("can-switch-project")).toHaveTextContent("false");
    expect(screen.getByTestId("can-switch-org")).toHaveTextContent("false");
  });
});

describe("All Organisations forbids a specific project", () => {
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
    getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], projects: [] });
  });

  it("resets a chosen project when the user returns to All Organisations", async () => {
    renderTenant();
    await settled();

    await act(async () => {
      screen.getByText("Pick org-1").click();
    });
    await act(async () => {
      screen.getByText("Pick project").click();
    });
    await waitFor(() => {
      expect(screen.getByTestId("project")).toHaveTextContent("North Corridor");
    });

    await act(async () => {
      screen.getByText("All organisations").click();
    });

    // A project is only meaningful under a known parent organisation, so
    // widening back to All Organisations must drop it rather than leave a
    // project from the organisation just left.
    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("ALL_ORGANISATIONS");
    });
    expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
  });

  it("offers no project to choose while All Organisations is active", async () => {
    renderTenant();
    await settled();

    await waitFor(() => {
      expect(screen.getByTestId("org")).toHaveTextContent("ALL_ORGANISATIONS");
    });
    // Nothing selectable means the selector cannot produce the invalid
    // All-Organisations + specific-project pair the server rejects with a 403.
    expect(screen.getByTestId("can-switch-project")).toHaveTextContent("false");
  });

  it("ignores an attempt to set a project with no organisation selected", async () => {
    renderTenant();
    await settled();

    await act(async () => {
      screen.getByText("Pick project").click();
    });

    await waitFor(() => {
      expect(screen.getByTestId("project")).toHaveTextContent("ALL_PROJECTS");
    });
  });
});
