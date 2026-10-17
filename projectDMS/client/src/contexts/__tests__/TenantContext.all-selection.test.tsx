import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SELECTION_STORAGE_KEY, TenantProvider, useTenant } from "../TenantContext";
import {
  ALL_SELECTION,
  activeScopeHeaders,
  parseSelection,
  serializeSelection,
  setActiveScope,
} from "@/services/active-scope";

/*
 * Owner policy (2026-10-05) for the navbar selection:
 *   Super Admin defaults to All Organisations + All Projects (global);
 *   Organisation A + All Projects is Organisation A; A + Project A1 is A1;
 *   changing organisation resets the project to All Projects.
 * Every other role keeps a concrete selection and never gets ALL.
 */

const { getCurrentUserProfile, listOrganizations, listProjects } = vi.hoisted(() => ({
  getCurrentUserProfile: vi.fn(),
  listOrganizations: vi.fn(),
  listProjects: vi.fn(),
}));

vi.mock("@/services/session-api", () => ({ getCurrentUserProfile }));
vi.mock("@/services/organizations-api", () => ({ listOrganizations }));
vi.mock("@/services/projects-api", () => ({ listProjects }));

const organizations = [
  { _id: "org-A", name: "Acme Infrastructure" },
  { _id: "org-B", name: "Bravo Rail" },
];
const projects = [
  { _id: "proj-A1", name: "North Corridor", organization_id: "org-A" },
  { _id: "proj-A2", name: "South Corridor", organization_id: "org-A" },
  { _id: "proj-B1", name: "Bravo Depot", organization_id: "org-B" },
];

let storage: Map<string, string>;

function Probe() {
  const tenant = useTenant();
  return (
    <div>
      <span data-testid="org">{tenant.selectedOrganizationId}</span>
      <span data-testid="project">{tenant.selectedProjectId}</span>
      <span data-testid="all-orgs">{String(tenant.allOrganizations)}</span>
      <span data-testid="all-projects">{String(tenant.allProjects)}</span>
      <button type="button" onClick={() => tenant.selectOrganization("org-A")}>org A</button>
      <button type="button" onClick={() => tenant.selectOrganization("org-B")}>org B</button>
      <button type="button" onClick={() => tenant.selectOrganization(ALL_SELECTION)}>all orgs</button>
      <button type="button" onClick={() => tenant.selectProject("proj-A1")}>project A1</button>
      <button type="button" onClick={() => tenant.selectProject("proj-B1")}>project B1</button>
      <button type="button" onClick={() => tenant.selectProject(ALL_SELECTION)}>all projects</button>
    </div>
  );
}

function renderProbe() {
  render(
    <TenantProvider>
      <Probe />
    </TenantProvider>,
  );
}

const text = (id: string) => screen.getByTestId(id).textContent;
const click = (name: string) => act(() => screen.getByRole("button", { name }).click());


describe("explicit ALL selection", () => {
  beforeEach(() => {
    storage = new Map<string, string>();
    vi.mocked(window.localStorage.getItem).mockImplementation((key) => storage.get(key) ?? null);
    vi.mocked(window.localStorage.setItem).mockImplementation((key, value) => {
      storage.set(key, String(value));
    });
    vi.mocked(window.localStorage.removeItem).mockImplementation((key) => {
      storage.delete(key);
    });
    vi.clearAllMocks();
    setActiveScope({ organizationId: "", projectId: "" });
    listOrganizations.mockResolvedValue(organizations);
    listProjects.mockResolvedValue(projects);
  });

  describe("Super Admin", () => {
    beforeEach(() => {
      getCurrentUserProfile.mockResolvedValue({ roles: ["superadmin"], organization_id: null });
    });

    it("defaults to All Organisations + All Projects and says so explicitly on the wire", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      expect(text("all-projects")).toBe("true");
      // Pages that read the ids see no organisation/project, never the sentinel.
      expect(text("org")).toBe("");
      expect(text("project")).toBe("");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": ALL_SELECTION, "X-Proj-Id": ALL_SELECTION });
    });

    it("Organisation A + All Projects, then Organisation A + Project A1", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));

      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));
      expect(text("all-projects")).toBe("true");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });

      click("project A1");
      await waitFor(() => expect(text("project")).toBe("proj-A1"));
      expect(text("all-projects")).toBe("false");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": "proj-A1" });
    });

    it("ALL/ALL -> A/ALL -> A/A1 -> B/ALL: A1 never survives the organisation change", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      const seen: Record<string, string>[] = [{ ...activeScopeHeaders() }];
      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));
      seen.push({ ...activeScopeHeaders() });
      click("project A1");
      await waitFor(() => expect(text("project")).toBe("proj-A1"));
      seen.push({ ...activeScopeHeaders() });
      click("org B");
      // The very next request after the switch, before any re-render settles.
      seen.push({ ...activeScopeHeaders() });
      await waitFor(() => expect(text("org")).toBe("org-B"));
      expect(seen).toEqual([
        { "X-Org-Id": ALL_SELECTION, "X-Proj-Id": ALL_SELECTION },
        { "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION },
        { "X-Org-Id": "org-A", "X-Proj-Id": "proj-A1" },
        { "X-Org-Id": "org-B", "X-Proj-Id": ALL_SELECTION },
      ]);
      expect(text("project")).toBe("");
    });

    it("changing organisation resets the project to All Projects", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));
      click("project A1");
      await waitFor(() => expect(text("project")).toBe("proj-A1"));

      click("org B");
      await waitFor(() => expect(text("org")).toBe("org-B"));
      expect(text("project")).toBe("");
      expect(text("all-projects")).toBe("true");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-B", "X-Proj-Id": ALL_SELECTION });
    });

    it("cannot pick a project from another organisation", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));
      click("project B1");
      expect(text("project")).toBe("");
      expect(activeScopeHeaders()["X-Proj-Id"]).toBe(ALL_SELECTION);
    });

    it("returning to All Organisations clears the project too", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));
      click("project A1");
      await waitFor(() => expect(text("project")).toBe("proj-A1"));
      click("all orgs");
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      expect(text("all-projects")).toBe("true");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": ALL_SELECTION, "X-Proj-Id": ALL_SELECTION });
    });

    it("a stale persisted project from the previous organisation does not stay active", async () => {
      storage.set(
        SELECTION_STORAGE_KEY,
        serializeSelection({ organizationId: "org-A", projectId: "proj-B1" }),
      );
      renderProbe();
      await waitFor(() => expect(text("org")).toBe("org-A"));
      expect(text("project")).toBe("");
      expect(text("all-projects")).toBe("true");
      expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });
    });

    it("a persisted organisation that no longer exists falls back to ALL/ALL", async () => {
      storage.set(
        SELECTION_STORAGE_KEY,
        serializeSelection({ organizationId: "org-gone", projectId: "proj-A1" }),
      );
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      expect(text("all-projects")).toBe("true");
    });

    it("persists ALL explicitly and restores it, never writing the sentinel to the legacy keys", async () => {
      renderProbe();
      await waitFor(() => expect(text("all-orgs")).toBe("true"));
      click("org A");
      await waitFor(() => expect(text("org")).toBe("org-A"));

      expect(parseSelection(storage.get(SELECTION_STORAGE_KEY))).toEqual({
        organizationId: "org-A",
        projectId: ALL_SELECTION,
      });
      expect(storage.get("org_id")).toBe("org-A");
      expect(storage.has("proj_id")).toBe(false);
      for (const value of storage.values()) {
        if (value !== storage.get(SELECTION_STORAGE_KEY)) expect(value).not.toContain(ALL_SELECTION);
      }
    });
  });

  describe("organisation user / admin of Organisation A", () => {
    // Owner policy 2026-10-06: default Organisation A + All Projects, which is
    // every project of Organisation A - never All Organisations, never another
    // organisation. The backend enforces the same boundary independently.
    describe.each(["orguser", "orgadmin"])("%s", (role) => {
      beforeEach(() => {
        getCurrentUserProfile.mockResolvedValue({ roles: [role], organization_id: "org-A" });
      });

      it("A: defaults to Organisation A + All Projects", async () => {
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        expect(text("all-projects")).toBe("true");
        expect(text("all-orgs")).toBe("false");
        expect(text("project")).toBe("");
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });
      });

      it("C/D: a project narrows, and All Projects restores the organisation", async () => {
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        click("project A1");
        await waitFor(() => expect(text("project")).toBe("proj-A1"));
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": "proj-A1" });
        click("all projects");
        await waitFor(() => expect(text("all-projects")).toBe("true"));
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });
      });

      it("E/B: cannot select All Organisations, another organisation or its project", async () => {
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        click("all orgs");
        click("org B");
        click("project B1");
        expect(text("org")).toBe("org-A");
        expect(text("all-orgs")).toBe("false");
        expect(text("project")).toBe("");
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });
      });

      it("G: a persisted project of another organisation is reset, never sent", async () => {
        storage.set(
          SELECTION_STORAGE_KEY,
          serializeSelection({ organizationId: "org-B", projectId: "proj-B1" }),
        );
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        expect(text("all-projects")).toBe("true");
        expect(Object.values(activeScopeHeaders())).not.toContain("proj-B1");
      });

      it("G: a valid persisted project of Organisation A survives a refresh", async () => {
        storage.set(
          SELECTION_STORAGE_KEY,
          serializeSelection({ organizationId: "org-A", projectId: "proj-A2" }),
        );
        renderProbe();
        await waitFor(() => expect(text("project")).toBe("proj-A2"));
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": "proj-A2" });
      });

      it("H: a legacy empty or legacy-key selection becomes All Projects of Organisation A", async () => {
        storage.set("org_id", "org-B");
        storage.set("proj_id", "proj-B1");
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        expect(text("all-projects")).toBe("true");
        expect(activeScopeHeaders()).toEqual({ "X-Org-Id": "org-A", "X-Proj-Id": ALL_SELECTION });
      });

      it("a Super Admin ALL/ALL left in this browser is not inherited", async () => {
        storage.set(
          SELECTION_STORAGE_KEY,
          serializeSelection({ organizationId: ALL_SELECTION, projectId: ALL_SELECTION }),
        );
        renderProbe();
        await waitFor(() => expect(text("org")).toBe("org-A"));
        expect(text("all-orgs")).toBe("false");
        expect(activeScopeHeaders()["X-Org-Id"]).toBe("org-A");
      });
    });
  });

  describe("project-tier roles", () => {
    it.each(["projectuser", "projectadmin"])(
      "%s keeps its concrete assigned project and cannot select ALL",
      async (role) => {
        getCurrentUserProfile.mockResolvedValue({
          roles: [role],
          organization_id: "org-A",
          projects: ["proj-A2"],
        });
        renderProbe();
        await waitFor(() => expect(text("project")).toBe("proj-A2"));
        expect(text("all-orgs")).toBe("false");
        expect(text("all-projects")).toBe("false");
        click("all orgs");
        click("all projects");
        expect(text("project")).toBe("proj-A2");
        expect(Object.values(activeScopeHeaders())).not.toContain(ALL_SELECTION);
      },
    );
  });
});

describe("selection serialization", () => {
  it.each([
    [{ organizationId: ALL_SELECTION, projectId: ALL_SELECTION }],
    [{ organizationId: "org-A", projectId: ALL_SELECTION }],
    [{ organizationId: "org-A", projectId: "proj-A1" }],
  ])("round-trips %o", (selection) => {
    expect(parseSelection(serializeSelection(selection))).toEqual(selection);
  });

  it("All Organisations always means All Projects", () => {
    expect(parseSelection(serializeSelection({ organizationId: ALL_SELECTION, projectId: "proj-A1" }))).toEqual({
      organizationId: ALL_SELECTION,
      projectId: ALL_SELECTION,
    });
    setActiveScope({ organizationId: ALL_SELECTION, projectId: "proj-A1" });
    expect(activeScopeHeaders()).toEqual({ "X-Org-Id": ALL_SELECTION, "X-Proj-Id": ALL_SELECTION });
  });

  it.each([null, "", "org-A", "{}", '{"v":2,"organizationId":"a","projectId":"b"}', "not json"])(
    "rejects what it did not write: %s",
    (raw) => {
      expect(parseSelection(raw)).toBeNull();
    },
  );
});
