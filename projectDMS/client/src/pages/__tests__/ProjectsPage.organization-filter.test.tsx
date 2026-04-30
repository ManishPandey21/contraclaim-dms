import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectsPage from "../ProjectsPage";

const getProjectsMock = vi.fn();
const getOrganizationsMock = vi.fn();

vi.mock("@/services/enhanced-api", () => {
  return {
    __esModule: true,
    enhancedApi: {
      getProjects: (...args: any[]) => getProjectsMock(...args),
      getOrganizations: (...args: any[]) => getOrganizationsMock(...args),
      deactivateProject: vi.fn(),
    },
  };
});

vi.mock("@/hooks/useRBAC", () => {
  return {
    __esModule: true,
    default: () => ({ roles: ["superadmin"] }),
  };
});

vi.mock("@/hooks/useHasPermission", () => {
  return {
    __esModule: true,
    default: () => false,
  };
});

vi.mock("@/services/auth", () => {
  return {
    __esModule: true,
    logoutAndRedirect: vi.fn(),
  };
});

describe("ProjectsPage organization-scoped navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getProjectsMock.mockResolvedValue([
      {
        _id: "project-1",
        name: "Alpha Project",
        organization_id: "org-1",
        status: "Active",
      },
      {
        _id: "project-2",
        name: "Beta Project",
        organization_id: "org-2",
        status: "Active",
      },
      {
        _id: "project-3",
        name: "Gamma Project",
        organization_id: "org-1",
        status: "Completed",
      },
    ]);
    getOrganizationsMock.mockResolvedValue([
      { _id: "org-1", name: "Org One" },
      { _id: "org-2", name: "Org Two" },
    ]);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows only the selected organization's projects when opened from organizations", async () => {
    render(
      <MemoryRouter
        initialEntries={[
          "/projects?org=org-1&orgName=Org%20One&source=organization",
        ]}
      >
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(
      await screen.findByRole("heading", { name: "Projects - Org One" })
    ).toBeInTheDocument();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    expect(screen.getByText("Gamma Project")).toBeInTheDocument();
    expect(screen.queryByText("Beta Project")).not.toBeInTheDocument();
  });

  it("shows all projects when opened directly", async () => {
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(
      await screen.findByRole("heading", { name: "Projects" })
    ).toBeInTheDocument();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    expect(screen.getByText("Beta Project")).toBeInTheDocument();
    expect(screen.getByText("Gamma Project")).toBeInTheDocument();
  });

  it("does not apply organization filtering without the organization navigation source", async () => {
    render(
      <MemoryRouter initialEntries={["/projects?org=org-1&orgName=Org%20One"]}>
        <Routes>
          <Route path="/projects" element={<ProjectsPage />} />
        </Routes>
      </MemoryRouter>
    );

    expect(
      await screen.findByRole("heading", { name: "Projects" })
    ).toBeInTheDocument();
    expect(await screen.findByText("Alpha Project")).toBeInTheDocument();
    expect(screen.getByText("Beta Project")).toBeInTheDocument();
    expect(screen.getByText("Gamma Project")).toBeInTheDocument();
  });
});
